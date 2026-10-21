"""DataStore — Protocol for the data plane behind a federated catalog.

A `DataStore` materialises observation rows for a `DataflowBinding`.
Implementations wrap whatever underlying storage they like — DuckDB +
DuckLake, raw Parquet on S3, Iceberg, Spark — and present the same
interface to a `Registry`.

**Write-side only, since #329 took the read half away.** The Protocol used to
carry `query` and `query_stream`, which took SQL text for `where` / `columns` /
`order_by`. Reading is now `obs()`, which compiles a path expression —
and a compiler is not something a Protocol can require of an arbitrary backend.
So `Registry.obs` narrows to the DuckDB store rather than widening
this, and every other implementation is spared a method it could only refuse.
When a second backend arrives, what it declares is a *capability*
(`query/_pushdown.py`), not a method returning a frame.

The store is **data-only** — it knows nothing about codelists, labels,
or DSDs. Result annotation (joining labels onto code columns) is the
 `Registry`'s job, since only it has the structural side.
"""

from typing import Literal, Protocol, runtime_checkable

import duckdb
import polars as pl
import pyarrow as pa

type WriteSource = pl.DataFrame | pl.LazyFrame | pa.Table | pa.RecordBatchReader | duckdb.DuckDBPyRelation
"""What `write_observations` accepts as a source of rows.

Public and defined once, because it appears in the signature of a `Protocol`
method: anyone implementing `DataStore` outside this package has to name this
type, and until #299 it existed only under `TYPE_CHECKING` in two modules —
spelled identically in both, importable from neither.

Runtime rather than type-checking-only: `duckdb`, `polars` and `pyarrow` are
all baseline runtime dependencies, so there is nothing to defer. A comment in
`local/data_store.py` claimed pyarrow was optional; it has not been since the
dependency baseline was set.
"""


@runtime_checkable
class DataStore(Protocol):
    """Write-side contract for the data plane.

    Implementations must accept a `DataflowBinding` and materialise rows into
    the storage they manage. Reading goes through
    `DuckDBDataStore.obs` — see the module docstring for why that is
    not stated here.
    """

    def write_observations(
        self,
        *,
        schema: str,
        table: str,
        source: WriteSource,
        partition_by: tuple[str, ...] = (),
        mode: Literal["overwrite", "append"] = "overwrite",
        lake_alias: str = "lake",
    ) -> None:
        """Materialise observation rows into a backend-managed table.

        Implementations route the write to whatever physical storage they
        manage (DuckLake, Iceberg, Postgres, raw Parquet). The caller
        side is uniform: schema + table + a source that can be turned
        into rows.

        Args:
            schema: Target schema.
            table: Target table name.
            source: Rows to write. Accepted shapes vary by implementation
                but Polars frames and Arrow tables are the portable
                lowest common denominator.
            partition_by: SDMX dimension ids the table is partitioned on.
                Recorded on the matching `DataflowBinding`.
            mode: ``"overwrite"`` replaces atomically; ``"append"``
                requires the table to already exist.
            lake_alias: For ATTACH-based backends, the alias of the
                target catalog. Implementations that don't use ATTACH
                may ignore this argument.
        """
        ...
