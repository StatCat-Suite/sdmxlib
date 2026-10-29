"""SDMX-CSV 2.0.0 data writer (streaming).

Consumes an iterator of Arrow record batches (typically from
`ObsQuery.stream`) and emits SDMX-CSV bytes one
chunk at a time. The header is emitted once with the first chunk;
subsequent chunks contain data rows only.

SDMX-CSV 2.0.0 header layout::

    STRUCTURE,STRUCTURE_ID,ACTION,<dim cols>,<measure cols>,<attribute cols>

where:

- ``STRUCTURE`` is the artefact type ("dataflow" / "datastructure" /
  "provisionagreement"). Defaults to ``"dataflow"``.
- ``STRUCTURE_ID`` is ``AGENCY:ID(VERSION)``. The caller passes this
  in — typically derived from the bound dataflow's URN.
- ``ACTION`` is one of ``I`` / ``A`` / ``R`` / ``D`` (Information / Append /
  Replace / Delete). Defaults to ``"I"``.

Dim/measure/attribute column ordering is taken from the DSD's
column maps in their dict-insertion order, which matches the order in
which they were declared on the structure.

Performance: rows are serialised via Polars' Rust CSV writer
(``pl.from_arrow(batch).write_csv``) rather than Python's `csv`,
which makes the writer fast enough that bulk SDMX-CSV downloads are
network-bound rather than writer-bound (#61).

Example:
    from sdmxlib.formats.sdmx_csv import write_sdmx_csv

    chunks = write_sdmx_csv(
        reg.obs(flow_urn).filter_components(cf).stream(),
        reg.dsd(flow_urn),
        structure_id="ESTAT:STS_INPR_M(1.0)",
    )
    with open("out.csv", "wb") as f:
        for b in chunks:
            f.write(b)
"""

import io
from collections.abc import Iterable, Iterator
from typing import Literal, get_args

import polars as pl
import pyarrow as pa

from sdmxlib.formats._arrow import frame_of
from sdmxlib.formats.sdmx_csv.reader import STRUCTURE_KINDS
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import short_urn

#: SDMX-CSV 2.0's four actions: Information, Append, Replace, Delete
#: (`sdmx-csv-field-guide.md` at v2.0.0). 2.1 deprecates ``I`` and ``A`` and
#: adds ``M`` (Merge); this writer writes 2.0.
type CsvAction = Literal["I", "A", "R", "D"]

#: Artefact class -> the ``STRUCTURE`` value SDMX-CSV gives it, for the form of
#: `write_sdmx_csv` that takes the artefact instead of a pre-formatted string.
_KIND_OF: dict[type, str] = {
    Dataflow: "dataflow",
    DataStructure: "datastructure",
    ProvisionAgreement: "dataprovision",
}


def write_sdmx_csv(
    batches: Iterable[pa.RecordBatch],
    dsd: DataStructure,
    *,
    structure_id: str | Dataflow | DataStructure | ProvisionAgreement,
    structure: str = "dataflow",
    action: CsvAction = "I",
) -> Iterator[bytes]:
    """Stream SDMX-CSV 2.0.0 bytes for a sequence of Arrow record batches.

    Args:
        batches: Iterable of `RecordBatch` (e.g. the output
            of `ObsQuery.stream`). Each batch
            must contain every component the structure declares.
        dsd: The resolved `DataStructure`.
            Determines column order in the output: dimensions, then
            measures, then attributes, each in the order the structure
            declares them.
        structure_id: The ``AGENCY:ID(VERSION)`` identifier emitted in the
            ``STRUCTURE_ID`` column on every row — or the artefact itself, in
            which case both this and ``structure`` are read off it. Passing the
            artefact is the point of #409: every caller was formatting the short
            URN by hand while `parse_dataflow_id` owned reading it.
        structure: Value emitted in the ``STRUCTURE`` column. SDMX-CSV allows
            ``"dataflow"``, ``"datastructure"`` or ``"dataprovision"``, and the
            field guide's wording is exactly those three. This said
            ``"provisionagreement"`` until #409 — that is SDMX-JSON's RFC 5988
            link relation, a different vocabulary, and a caller who followed it
            wrote a file no conformant reader accepts. Ignored when
            ``structure_id`` is an artefact.
        action: Value emitted in the ``ACTION`` column, SDMX-CSV 2.0's
            action for every row: ``I`` (Information, the default), ``A``
            (Append), ``R`` (Replace) or ``D`` (Delete).

    Yields:
        UTF-8 bytes — header on the first chunk, data rows on every
        chunk including the first. Each yielded ``bytes`` is one record
        batch's worth of CSV.

    Raises:
        KeyError: If a batch is missing a component the structure declares.
        ValueError: If ``structure`` is not one of SDMX-CSV's three kinds,
            or ``action`` not one of its four actions.
    """
    if not isinstance(structure_id, str):
        structure = _KIND_OF.get(type(structure_id), "dataflow")
        structure_id = short_urn(structure_id)
    if structure not in STRUCTURE_KINDS:
        msg = f"STRUCTURE must be one of {', '.join(STRUCTURE_KINDS)}, not {structure!r}"
        raise ValueError(msg)
    if action not in get_args(CsvAction.__value__):
        msg = f"ACTION must be one of {', '.join(get_args(CsvAction.__value__))}, not {action!r}"
        raise ValueError(msg)
    # Component ids, in SDMX order, from the structure. This read the
    # *binding's* physical column names until an observation query began
    # projecting by component id — after which it raised a `KeyError` naming
    # columns the batch correctly did not have.
    dim_cols = [d.id for d in dsd.key_dimensions]
    measure_cols = [m.id for m in dsd.measures]
    attr_cols = [a.id for a in dsd.attributes]
    data_cols = [*dim_cols, *measure_cols, *attr_cols]
    out_cols = ["STRUCTURE", "STRUCTURE_ID", "ACTION", *data_cols]

    # Header is plain bytes — one line, never quoted by polars' writer
    # because none of the column names contain commas or newlines.
    header_bytes = (",".join(out_cols) + "\n").encode("utf-8")
    header_emitted = False

    for batch in batches:
        missing = [c for c in data_cols if c not in batch.schema.names]
        if missing:
            msg = (
                f"Record batch is missing column(s) {missing!r} declared by "
                f"the structure. Available columns: {batch.schema.names}"
            )
            raise KeyError(msg)

        # `frame_of`, not the `pl.DataFrame` constructor: 1.33x per batch (#427).
        df = (
            frame_of(batch)
            .select(data_cols)
            .with_columns(
                pl.lit(structure).alias("STRUCTURE"),
                pl.lit(structure_id).alias("STRUCTURE_ID"),
                pl.lit(action).alias("ACTION"),
            )
            .select(out_cols)
        )

        buf = io.BytesIO()
        df.write_csv(buf, include_header=False)
        body = buf.getvalue()

        if not header_emitted:
            yield header_bytes + body
            header_emitted = True
        else:
            yield body

    if not header_emitted:
        # Empty stream — emit just the header so consumers get a valid
        # (if empty) SDMX-CSV file.
        yield header_bytes
