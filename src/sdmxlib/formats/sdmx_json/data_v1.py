"""SDMX-JSON 1.0 data-message writer (streaming).

Targets the SDMX-JSON 1.0 data message paired with SDMX 2.1. The
envelope is flatter than 2.0: ``{header, structure, dataSets}`` at the
top level (no ``meta``/``data`` wrapper), and a single shared
``structure`` rather than ``data.structures[]`` with indices. The
observation-map and value-array shapes are the same as 2.0, so the
batch encoder and lazy code-position tables are reused from
``_data_common``.

SDMX-JSON 1.0 envelope (flat layout)::

    {
      "header":    {id, prepared, sender: {id}},
      "structure": {
        "dimensions": {"observation": [<dim>...]},
        "attributes": {"observation": [<attr>...]}
      },
      "dataSets": [
        {
          "action": "Information",
          "links":  [<dataflow ref>],
          "observations": {
            "p0:p1:...:pn": [measure, attr0, attr1, ...]
          }
        }
      ]
    }

Streaming strategy mirrors the 2.0 writer: the ``observations`` map
is streamed per batch, and the shared ``structure`` block is emitted
at the tail so the lazy dimension/attribute value tables include every
code seen during the scan.
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
    display_names,
    encode_batch,
    frequency_dimension_id,
    read_data_sets,
    resolve_columns,
)
from sdmxlib.formats.sdmx_json._dto import ENCODER, _camel_rename, prepared_text
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.errors import SdmxParseError
from sdmxlib.model.istring import normalize_tag
from sdmxlib.model.ref import Ref

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
) -> Iterator[bytes]:
    """Stream SDMX-JSON 1.0 data-message bytes for a batch iterator.

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
        message_id: Value emitted as ``header.id``. SDMX requires a
            unique id per message; callers typically pass a UUID.
        prepared: When the message was prepared, emitted as
            ``header.prepared``. Timezone-aware: the schema requires a zone,
            so a naive one is refused with `ValueError`.
        sender_id: Value emitted as ``header.sender.id``.
        action: dataSet ``action``. One of ``Information`` (default),
            ``Append``, ``Replace``, ``Delete``.
        lang: Language tag used to pick localized names for dimensions,
            attributes, and codes. Falls back to the id when no label
            is set in *lang*.

    Yields:
        UTF-8 bytes. The first chunk carries the envelope opening up to
        the dataSet's ``"observations": {``. Each subsequent chunk
        carries one record batch's worth of observations. The final
        chunk closes the ``observations`` map, the dataSet, the
        ``dataSets`` array, then emits the shared ``structure`` block
        and closes the envelope.

    Raises:
        KeyError: If a batch is missing a component the structure declares.
        ValueError: If the DSD does not declare a single OBS_VALUE
            measure (multi-measure DSDs are deferred to a follow-up).
    """
    lang = normalize_tag(lang)
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
    header = {
        "id": message_id,
        "prepared": prepared_text(prepared),
        "sender": {"id": sender_id},
        **names,
    }
    head = ENCODER.encode({"header": header})
    dataset_head: dict[str, object] = {"action": action, **names}
    if flow_annotations:
        dataset_head["annotations"] = flow_annotations
    dataset_head_bytes = ENCODER.encode(dataset_head)[:-1] + b',"links":[' + dataflow_link(dataflow) + b"],"
    # Strip trailing `}` and splice in the dataSets opener.
    yield head[:-1] + (b',"dataSets":[' + dataset_head_bytes + b'"observations":{')

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

    structure_block: dict[str, object] = {
        **names,
        "dimensions": {"observation": dim_lookup.to_json()},
        "attributes": {"observation": attr_lookup.to_json()},
    }
    # After both `to_json()` calls: those are what populate the pool, and a
    # dict literal evaluating them in the right order is too implicit to rely
    # on. Omitted entirely when nothing registered — an empty `annotations: []`
    # is noise in every message that has none.
    if annotations:
        structure_block["annotations"] = annotations.to_json()
    # Close observations + dataSet + dataSets array, then emit the
    # shared structure block and the envelope's closing brace.
    yield b"}}]," + b'"structure":' + ENCODER.encode(structure_block) + b"}"


def read_observations(source: bytes | str | Path) -> tuple[pl.DataFrame, str | None]:
    """A data message's observations, and the URN of the dataflow it names.

    Every data set is read; they must share columns and name at most one
    dataflow, and none may be a ``Delete`` (`read_data_sets`).

    Args:
        source: The message's bytes, or a file path.

    Returns:
        One row per observation, ``[*dim_ids, OBS_VALUE, *attr_ids]``; and the
        ``rel="dataflow"`` link's URN, or `None` when the message has none.

    Raises:
        SdmxParseError: If the document is not a recognisable data message of this
            version, or its data sets do not read as one frame.
    """
    payload = _load(source)
    if not payload.data_sets or payload.structure is None:
        msg = "Source does not look like a SDMX-JSON 1.0 data message — missing top-level dataSets / structure"
        raise SdmxParseError(msg)
    structure = payload.structure
    return read_data_sets([(data_set, structure) for data_set in payload.data_sets])


class _DataMessageJson(msgspec.Struct, rename=_camel_rename):
    """The envelope of an SDMX-JSON 1.0 data message.

    1.0 keeps ``structure`` as a single shared block at the top level, where
    2.0 nests an array under ``data``.
    """

    structure: StructureBlockJson | None = None
    data_sets: list[DataSetBlockJson] = msgspec.field(default_factory=list)


def _load(source: bytes | str | Path) -> _DataMessageJson:
    # `type=` makes msgspec validate the top level and hand back a typed
    # value; the bare decode returns Any and leaks it through the signature.
    if isinstance(source, (bytes, bytearray)):
        return msgspec.json.decode(source, type=_DataMessageJson)
    return msgspec.json.decode(Path(source).read_bytes(), type=_DataMessageJson)
