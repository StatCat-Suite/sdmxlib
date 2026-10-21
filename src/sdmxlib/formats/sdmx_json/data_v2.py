"""SDMX-JSON 2.0.0 data-message writer (streaming).

Consumes an iterator of Arrow record batches (typically from
`ObsQuery.stream`) and emits SDMX-JSON 2.0.0 bytes one
observation-row at a time. The envelope skeleton, dim position
tables, and structures block are buffered in memory; the
``observations`` map body is streamed.

SDMX-JSON 2.0.0 envelope (flat layout)::

    {
      "meta":   {schema, id, prepared, test, contentLanguages, sender},
      "data": {
        "dataSets": [
          {
            "structure": 0,                # index into data.structures[]
            "action": "Information",
            "observations": {
              "p0:p1:...:pn": [measure, attr0, attr1, ...]
            }
          }
        ],
        "structures": [
          {
            "dimensions": {"observation": [<dim>...]},
            "attributes": {"observation": [<attr>...]}
          }
        ]
      }
    }

where each ``<dim>`` carries ``id`` and ``values: [{id, name}, ...]``.
Observation keys are colon-joined integer positions into each
dimension's ``values`` array, in declaration order.

Layout: only ``"flat"`` is implemented at this version — all dimensions
sit under ``dimensions.observation``. Series layout (split between
``series`` / ``observation``) is a TODO; it requires the caller to sort
on the series key so series boundaries can be detected mid-stream.

Streaming strategy: dimension/attribute position tables are built
lazily as observation rows arrive. The ``observations`` map is emitted
as soon as each batch is processed, and the ``structures`` block is
emitted *after* the body so its ``values: [...]`` arrays reflect every
code seen during the scan. The JSON object key order
(``observations`` before ``structures`` within the same dataSet) is
irrelevant to conforming consumers, who key by name.
"""

from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Literal

import msgspec
import polars as pl
import pyarrow as pa

from sdmxlib.formats.sdmx_json._data_common import (
    AnnotationPool,
    ComponentTable,
    DataSetBlockJson,
    StructureBlockJson,
    dataflow_annotations,
    dataflow_link,
    dataflow_urn,
    decode_observations,
    display_names,
    encode_batch,
    frequency_dimension_id,
    resolve_columns,
    rows_to_lazyframe,
)
from sdmxlib.formats.sdmx_json._dto import ENCODER, _camel_rename, prepared_text
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.ref import Ref

#: The SDMX-JSON 2.0.0 data schema, where SDMX publishes it and FMR points, as
#: the structure writer's is (`writer.STRUCTURE_SCHEMA_URL`). The GitHub URL
#: that is the schema's `$id` no longer resolves.
_SCHEMA_URL = "https://json.sdmx.org/2.0.0/sdmx-json-data-schema.json"

ActionLiteral = Literal["Information", "Append", "Replace", "Delete"]


def write_sdmx_json_data(
    batches: Iterable[pa.RecordBatch],
    dsd: DataStructure,
    dataflow: Ref[Dataflow],
    *,
    message_id: str,
    prepared: datetime,
    sender_id: str,
    lang: str,
    action: ActionLiteral = "Information",
    test: bool = False,
) -> Iterator[bytes]:
    """Stream SDMX-JSON 2.0.0 data-message bytes for a batch iterator.

    Args:
        batches: Iterable of `pa.RecordBatch` — the natural output of
            `ObsQuery.stream`. Each batch must contain every
            component the structure declares.
        dsd: Resolved `DataStructure`. Supplies the column order —
            dimensions, then measures, then attributes — and supplies
            ``id`` / ``name`` for dimensions and attributes, plus the
            seed code list for enumerated representations.
        dataflow: `Ref` to the dataflow this message wraps. Supplies the
            `links` urn and, when resolved, the display name — SDMX-JSON
            data messages wrap a *flow*, so a consumer titles the dataset
            from it and falls back to the DSD only when it is unresolved.
        message_id: Value emitted as ``meta.id``. SDMX requires a unique
            id per message; callers typically pass a UUID.
        prepared: When the message was prepared, emitted as
            ``meta.prepared``. Timezone-aware: the schema requires a zone,
            so a naive one is refused with `ValueError`.
        sender_id: Value emitted as ``meta.sender.id``.
        action: dataSet ``action``. One of ``Information`` (default),
            ``Append``, ``Replace``, ``Delete``.
        test: Value emitted as ``meta.test``.
        lang: Language tag used to pick localized names for dimensions,
            attributes, and codes. Falls back to the first available
            language if *lang* is not present.

    Yields:
        UTF-8 bytes. The first chunk carries the envelope opening up to
        ``"observations": {``. Each subsequent chunk carries one record
        batch's worth of observations. The final chunk closes the
        ``observations`` map, emits the ``structures`` block (with
        ``values: [...]`` reflecting every code seen), and closes the
        envelope.

    Raises:
        KeyError: If a batch is missing a component the structure declares.
        ValueError: If the DSD does not declare a single OBS_VALUE
            measure (multi-measure DSDs are deferred to a follow-up).
    """
    dim_cols, dim_phys, attr_cols, attr_phys, _measure_id, measure_col = resolve_columns(dsd)

    time_dim_id = dsd.time_dimension.id if dsd.time_dimension is not None else "TIME_PERIOD"
    freq_dim_id = frequency_dimension_id(dsd)
    # One pool for the whole structure block — the two tables emit integer
    # indices into it, and an index is meaningless without knowing which array
    # it indexes (#366).
    annotations = AnnotationPool()
    # Registered first, so its indices are fixed before the dataSet head is written.
    flow_annotations = dataflow_annotations(annotations, dataflow)
    dim_lookup = ComponentTable(
        dim_cols,
        dsd,
        kind="dimension",
        lang=lang,
        annotations=annotations,
        time_dim_id=time_dim_id,
        freq_dim_id=freq_dim_id,
    )
    attr_lookup = ComponentTable(attr_cols, dsd, kind="attribute", lang=lang, annotations=annotations)

    names = display_names(dataflow, dsd, lang)
    meta = {
        "schema": _SCHEMA_URL,
        "id": message_id,
        "prepared": prepared_text(prepared),
        "test": test,
        "contentLanguages": [lang],
        "sender": {"id": sender_id},
        **names,
    }
    head = ENCODER.encode({"meta": meta})
    dataset_head: dict[str, object] = {"action": action, "structure": 0, **names}
    if flow_annotations:
        dataset_head["annotations"] = flow_annotations
    # ``"action":...,"structure":0,[name:..,names:..,]"links":[...]"observations":{``
    dataset_head_bytes = ENCODER.encode(dataset_head)[:-1] + b',"links":[' + dataflow_link(dataflow) + b"],"
    # Strip trailing `}` and splice in the data envelope opener.
    yield head[:-1] + (b',"data":{"dataSets":[' + dataset_head_bytes + b'"observations":{')

    first_obs = True
    for batch in batches:
        chunk = encode_batch(
            batch,
            dim_cols=dim_cols,
            dim_phys=dim_phys,
            attr_cols=attr_cols,
            attr_phys=attr_phys,
            measure_col=measure_col,
            dim_lookup=dim_lookup,
            attr_lookup=attr_lookup,
            first_obs=first_obs,
        )
        first_obs = False
        yield chunk

    structures_block: dict[str, object] = {
        **names,
        "dimensions": {"observation": dim_lookup.to_json()},
        "attributes": {"observation": attr_lookup.to_json()},
    }
    # After both `to_json()` calls: those are what populate the pool, and a
    # dict literal evaluating them in the right order is too implicit to rely
    # on. Omitted entirely when nothing registered — an empty `annotations: []`
    # is noise in every message that has none.
    if annotations:
        structures_block["annotations"] = annotations.to_json()
    tail = b"}}],"  # close observations, close dataSet
    tail += b'"structures":[' + ENCODER.encode(structures_block) + b"]}}"
    yield tail


def read_observations(source: bytes | str | Path) -> tuple[pl.DataFrame, str | None]:
    """A data message's observations, and the URN of the dataflow it names.

    Args:
        source: The message's bytes, or a file path.

    Returns:
        One row per observation, ``[*dim_ids, OBS_VALUE, *attr_ids]``; and the
        ``rel="dataflow"`` link's URN, or `None` when the message has none.

    Raises:
        ValueError: If the document is not a recognisable data message of this version.
    """
    payload = _load(source).data
    if payload is None or not payload.data_sets or not payload.structures:
        msg = "Source does not look like a SDMX-JSON 2.0 data message — missing data.dataSets / data.structures"
        raise ValueError(msg)
    dataset_block = payload.data_sets[0]
    structure_block = payload.structures[0]

    dim_ids, attr_ids, rows = decode_observations(structure_block, dataset_block)
    return rows_to_lazyframe(dim_ids, attr_ids, rows).collect(), dataflow_urn(dataset_block, structure_block)


class _DataPayloadJson(msgspec.Struct, rename=_camel_rename):
    """The ``data`` object of an SDMX-JSON 2.0 data message."""

    structures: list[StructureBlockJson] = msgspec.field(default_factory=list)
    data_sets: list[DataSetBlockJson] = msgspec.field(default_factory=list)


class _DataMessageJson(msgspec.Struct):
    """The outer envelope of an SDMX-JSON 2.0 data message."""

    data: _DataPayloadJson | None = None


def _load(source: bytes | str | Path) -> _DataMessageJson:
    """Decode *source* into the typed envelope.

    A shape violation surfaces as ``msgspec.ValidationError``, which is a
    ``ValueError`` — so the documented contract of `read_observations`
    still holds for a malformed document.
    """
    if isinstance(source, (bytes, bytearray)):
        return msgspec.json.decode(source, type=_DataMessageJson)
    # Treat str / Path as a filesystem path. Bytes paths would be ambiguous,
    # so we don't support them — pass `Path(...)` or pre-load bytes.
    return msgspec.json.decode(Path(source).read_bytes(), type=_DataMessageJson)
