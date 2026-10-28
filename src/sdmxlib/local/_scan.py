"""A data file read where it lies, as an `ObsQuery` (#516).

`reg.scan_csv(path)` answers with the same query a store's own observations
do, so a file gets `pivot`, `validate`, the sinks and labels through the one
surface a store's rows have — not a second, as `Dataset` was. The rows have to be somewhere DuckDB
can reach, and that is a catalog of its own:

**`sdmx_scans`, an attached `:memory:` database.** A view there is visible to
every cursor and thread of the store — a `TEMP` view is not, since a cursor is
a connection of its own — and it is never written into a file-backed store,
so scanning a file does not change the database the caller opened.

**A path is a view; bytes are a table.** A view over `read_csv` reads the file
on each query, as `polars.scan_csv` would: nothing is copied, and the answer
follows the file. Bytes have nowhere to be read from later, so they are
written to a table once, the way a REST fetch is (`DuckDBDataStore.ingest_csv`).

**One relation per scan.** Two files of one dataflow are two questions; a
name per dataflow would let the second scan change what the first query reads.

**The one literal in a DDL.** A view cannot hold a bound parameter — DuckDB
refuses `CREATE VIEW … read_csv(?)` as a statement that "can't be prepared" —
so the path and the column types are written into it as quoted literals, as
`CREATE SECRET`'s values are (`sql_literal`).
"""

import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path

import polars as pl

from sdmxlib._read_errors import SCANS, reading_csv, type_mismatch
from sdmxlib.local.data_store import DuckDBDataStore, column_types
from sdmxlib.local.object_store import sql_literal
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.errors import SdmxParseError
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.query.observations import ObsQuery
from sdmxlib.session import completed


def scan_csv_into(
    plane: DuckDBDataStore, source: str | Path | bytes, *, dsd: DataStructure, dataflow: Ref[Dataflow]
) -> ObsQuery:
    """`source`'s observations, as a query over a relation in `plane`'s database.

    Args:
        plane: The data store the relation is made in.
        source: An SDMX-CSV file, or its bytes.
        dsd: The structure the file is read with.
        dataflow: The dataflow its rows are observations of.

    Returns:
        A query over the file's rows, typed from `dsd` as any is.
    """
    binding, relation = _new_relation(plane, dsd, dataflow)
    types = column_types(binding, dsd)
    if isinstance(source, bytes):
        with tempfile.TemporaryDirectory(prefix="sdmxlib-scan-") as tmp:
            path = Path(tmp) / "data.csv"
            path.write_bytes(source)
            with reading_csv(structure=dsd.id):
                plane.connection.execute(
                    f"CREATE TABLE {relation} AS SELECT * FROM read_csv(?, types=?, header=true)", [str(path), types]
                )
    else:
        literal_types = ", ".join(f"{sql_literal(c)}: {sql_literal(t)}" for c, t in types.items())
        path_literal = sql_literal(str(Path(source).resolve()))
        # DuckDB reads the header when the view is made, and the rows when a
        # query runs; the query terminals convert what those raise.
        with reading_csv(structure=dsd.id):
            read = f"read_csv({path_literal}, types={{{literal_types}}}, header=true)"
            plane.connection.execute(f"CREATE VIEW {relation} AS SELECT * FROM {read}")
    _require_columns(plane, relation, binding)
    return plane.obs(binding, dsd=dsd)


def _require_columns(plane: DuckDBDataStore, relation: str, binding: DataflowBinding) -> None:
    """Refuse a file without a column the binding reads, before a query finds out.

    A forced column that is absent already fails the read. A measure the DSD
    leaves untyped is not forced (`column_types`), so without this a file
    lacking it would scan and then fail every query on a binder error.

    Raises:
        SdmxParseError: If a dimension, measure or attribute column is absent.
    """
    present = {row[0] for row in plane.connection.execute(f"DESCRIBE {relation}").fetchall()}
    wanted = [
        *binding.dimension_column_map.values(),
        *binding.measure_column_map.values(),
        *binding.attribute_column_map.values(),
    ]
    missing = [column for column in wanted if column not in present]
    if missing:
        msg = f"the SDMX-CSV data has no {', '.join(missing)} column, which its structure declares"
        raise SdmxParseError(msg)


def scan_frame_into(
    plane: DuckDBDataStore, frame: pl.DataFrame, *, dsd: DataStructure, dataflow: Ref[Dataflow]
) -> ObsQuery:
    """A decoded message's observations, as a query over a table in `plane`'s database.

    For a format DuckDB cannot read itself — SDMX-JSON — so the rows are
    decoded first and loaded once, typed as a CSV's columns are.

    Args:
        plane: The data store the table is made in.
        frame: The observations, one row each.
        dsd: The structure they are read with.
        dataflow: The dataflow they are observations of.

    Returns:
        A query over the rows, typed from `dsd` as any is.
    """
    binding, relation = _new_relation(plane, dsd, dataflow)
    try:
        typed = frame.with_columns(
            pl.col(column).cast(_POLARS_TYPE[duckdb_type])
            for column, duckdb_type in column_types(binding, dsd).items()
            if column in frame.columns
        )
    except pl.exceptions.PolarsError as exc:
        raise type_mismatch(dsd.id, exc) from exc
    name = relation.rpartition(".")[2]
    completed(plane.connection).register(name, typed.to_arrow())
    try:
        plane.connection.execute(f"CREATE TABLE {relation} AS SELECT * FROM {name}")
    finally:
        completed(plane.connection).unregister(name)
    return plane.obs(binding, dsd=dsd)


def resolve_flow(
    urn: str | None, fetch: Callable[[SdmxUrn[Dataflow]], Dataflow | None]
) -> tuple[DataStructure, Ref[Dataflow]]:
    """The structure and dataflow a message names by URN, through `fetch`.

    Args:
        urn: The message's ``rel="dataflow"`` link, or `None` if it had none.
        fetch: How the registry finds a dataflow — with its structure resolved.

    Returns:
        The dataflow's structure, and a reference to the dataflow.

    Raises:
        ValueError: If the message names no dataflow, the registry has none by
            that URN, or the dataflow has no structure.
    """
    if urn is None:
        msg = 'this message names no dataflow: no dataSet or structure link has rel="dataflow"'
        raise ValueError(msg)
    flow = fetch(Dataflow.parse_urn(urn))
    if flow is None:
        msg = f"dataflow {urn} is not in this registry — add it, with its structure"
        raise ValueError(msg)
    if flow.structure is None:
        msg = f"dataflow {urn} has no structure — it cannot say how to read this message"
        raise ValueError(msg)
    return flow.structure(), Ref.to(flow)


def _new_relation(plane: DuckDBDataStore, dsd: DataStructure, dataflow: Ref[Dataflow]) -> tuple[DataflowBinding, str]:
    """A fresh relation name in `SCANS`, and the binding that reads it."""
    plane.connection.execute(f"ATTACH IF NOT EXISTS ':memory:' AS {SCANS}")
    table = f"scan_{uuid.uuid4().hex}"
    binding = DataflowBinding.from_dsd(dsd, dataflow=dataflow, schema="main", table=table, database_role=SCANS)
    return binding, f"{SCANS}.main.{table}"


#: `column_types`' DuckDB types, as the polars types a decoded frame is cast to.
_POLARS_TYPE: dict[str, type[pl.DataType]] = {"VARCHAR": pl.String, "DOUBLE": pl.Float64}
