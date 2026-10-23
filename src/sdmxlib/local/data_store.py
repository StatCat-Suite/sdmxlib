"""DuckDBDataStore — DuckDB-backed implementation of `DataStore`.

Path-style: ``DuckDBDataStore(uri)`` is a cheap value object; the DuckDB
connection opens on ``open()`` / ``__enter__`` and closes on exit.

For the common case where a `LocalRegistry` and the
data store share a single DuckDB session (so SQL can join SDMX codelists
to observation rows without cross-connection plumbing), use the
`from_registry` classmethod.

External catalogs (DuckLake, raw Parquet, S3 via httpfs) can be
``ATTACH``-ed at open time so cross-database joins work without
file-handle juggling.
"""

# `local.registry` imports this module at runtime, so `LocalRegistry` can only
# be imported under TYPE_CHECKING. String annotations keep the public
# signatures that name it (`registry`, `from_registry`) evaluable;
# `test_runtime_annotations.py` fails without this line. No class here takes a
# PEP 695 parameter, which strings would hide from `get_type_hints`.
from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self, override

import duckdb
import polars as pl

from sdmxlib.data_store import WriteSource
from sdmxlib.local.sql import SchemaInspector
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from sdmxlib.query.observations import Observations, ObsQuery, Sidecar
from sdmxlib.query.query import build
from sdmxlib.session import SessionFactory, completed

if TYPE_CHECKING:
    # `local.registry` imports this module at runtime: a cycle.
    from sdmxlib.local.registry import LocalRegistry


class DuckLakeVersionError(Exception):  # mirrors RegistryUnavailable etc.
    """A DuckLake catalog's format is newer than the installed extension.

    Raised when ATTACH-ing a bundle whose DuckLake catalog format exceeds
    what the loaded ``ducklake`` extension supports. The fix is to align
    versions (upgrade ``duckdb`` >= 1.5.3 and/or the ``ducklake``
    extension), not to migrate the bundle in place.
    """


def _is_ducklake_version_mismatch(exc: Exception) -> bool:
    """True if ``exc`` is a DuckDB error about an unsupported DuckLake format.

    Matches the two shapes DuckDB raises depending on the skew:

    - ``DuckLake catalog version mismatch: catalog version is X, but the
      extension requires version Y``
    - ``Only DuckLake versions ... are supported``
    """
    msg = str(exc).lower()
    return "ducklake" in msg and ("catalog version mismatch" in msg or "are supported" in msg)


def store_path(uri: object, *, owner: str, wrap: str) -> Path:
    """`uri` as the path of a DuckDB file, refusing anything else (#505).

    The constructors took ``str | Path`` and passed anything that was not a
    `str` through untouched, so a `DuckDBPyConnection` reached `duckdb.connect`
    as its own `repr` — a new database file named
    ``<_duckdb.DuckDBPyConnection object at 0x…>`` in the working directory,
    holding everything the caller meant to put in theirs, and no error.

    Args:
        uri: What the caller passed.
        owner: The class name, for the message.
        wrap: How to use a connection the caller already has, for the message.

    Returns:
        The path.

    Raises:
        TypeError: If `uri` is not a `str` or a `Path`.
    """
    if isinstance(uri, Path):
        return uri
    if isinstance(uri, str):
        return Path(uri)
    if isinstance(uri, duckdb.DuckDBPyConnection):
        msg = f"{owner}() takes a path, not a DuckDB connection. To use a connection you already have: {wrap}."
        raise TypeError(msg)
    msg = f"{owner}() takes a path (str or Path), got {type(uri).__name__}."
    raise TypeError(msg)


class DuckDBDataStore(SessionFactory):
    """A `DataStore` backed by DuckDB.

    Two construction paths:

    1. ``DuckDBDataStore(uri)`` — Path-style. Opens its own DuckDB
       connection at ``open()`` / ``__enter__`` time. Use when the data
       store stands alone (no registry sharing).
    2. ``DuckDBDataStore.from_registry(reg, attach=, extensions=)`` —
       reuses a `LocalRegistry`'s connection so the SDMX structural
       side and the observation side share a single DuckDB session.
       Lazy codelists keep working across queries, and queries can join
       across the two without cross-connection plumbing.

    The ``attach`` / ``extensions`` / ``azure_credentials`` knobs
    configure the DuckDB session at open time. ``attach`` maps
    ``alias → ATTACH spec`` (each executed as
    ``ATTACH <spec> AS <alias>``); ``extensions`` INSTALL+LOAD the
    listed extensions before any ATTACH. ``azure_credentials`` is a
    dict that drives a DuckDB ``CREATE SECRET`` for ADLSv2 access — see
    below.

    Azure credential dicts accept one of three shapes:

    * ``{"connection_string": "..."}`` → ``PROVIDER 'config'`` with
      ``CONNECTION_STRING``.
    * ``{"account_name": ..., "tenant_id": ..., "client_id": ...,
      "client_secret": ...}`` → ``PROVIDER 'service_principal'``.
    * ``{"chain": "cli"}`` (or ``"env"``, ``"managed_identity"``) →
      ``PROVIDER 'credential_chain'`` with the named chain.

    When ``azure_credentials`` is set, or any ATTACH spec contains
    ``abfss://``, the ``azure`` extension is auto-INSTALL+LOADed so
    DATA_PATH URIs like ``abfss://acct.dfs.core.windows.net/c/p/`` work
    out of the box.

    Example:
        with sl.LocalRegistry("sdmx.duckdb") as reg:
            store = sl.DuckDBDataStore.from_registry(
                reg,
                attach={"lake": "ducklake:'catalog.duckdb' (READ_ONLY)"},
                extensions=["ducklake"],
            )
            obs = store.obs(binding)
            df = obs.filter(obs.col("REF_AREA") == "CA").frame()
    """

    def __init__(
        self,
        uri: str | Path,
        *,
        attach: dict[str, str | tuple[str, str]] | None = None,
        extensions: list[str] | None = None,
        azure_credentials: dict[str, str] | None = None,
    ) -> None:
        self._uri = store_path(
            uri,
            owner="DuckDBDataStore",
            wrap="DuckDBDataStore.from_registry(sl.LocalRegistry.from_connection(conn))",
        )
        self._attach = attach
        self._extensions = extensions
        self._azure_credentials = azure_credentials
        self._registry: LocalRegistry | None = None
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._owns_connection = True
        # Per-thread read cursor (#80); see LocalRegistry._read_cursor.
        self._thread_local: threading.local = threading.local()
        # Lazily-bound schema inspector, shared by the internal query path
        # and consumers' custom SQL (#163). Reset on close(). The lock
        # single-flights its lazy construction under concurrent reads.
        self._inspector: SchemaInspector | None = None
        self._inspector_lock: threading.Lock = threading.Lock()

    @property
    def uri(self) -> Path:
        """The URI this data store references. Available before opening."""
        return self._uri

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """The store's base DuckDB connection — stable, and safe to hold.

        For setup, DDL, and as the object another component derives its own
        handles from. **Not the handle to run queries on**: it is shared by
        every thread and `DuckDBPyConnection` is not thread-safe, which is why
        the library executes on per-thread handles. Use `session` or
        `execute` for custom SQL — they resolve that handle, share its session
        state with the DSL, and cannot be captured by accident.

        Raises:
            RuntimeError: If the data store hasn't been opened.
        """
        if self._conn is None:
            msg = (
                f"DuckDBDataStore({self._uri!r}) is not open. Use it as a context "
                "manager (``with DuckDBDataStore(uri) as store: ...``) or call "
                "``.open()`` first."
            )
            raise RuntimeError(msg)
        return self._conn

    @property
    def registry(self) -> LocalRegistry | None:
        """The `LocalRegistry` whose connection is shared, or ``None``.

        Set when the data store was constructed via `from_registry`;
        ``None`` for standalone Path-style construction.
        """
        return self._registry

    def open(self) -> Self:
        """Open the DuckDB connection and apply ``extensions`` + ``attach``.

        Idempotent within a single open/close cycle.
        """
        if self._conn is not None:
            return self
        self._conn = duckdb.connect(str(self._uri))
        self._owns_connection = True
        self._configure_session()
        return self

    @classmethod
    def from_registry(
        cls,
        registry: LocalRegistry,
        *,
        attach: dict[str, str | tuple[str, str]] | None = None,
        extensions: list[str] | None = None,
        azure_credentials: dict[str, str] | None = None,
    ) -> Self:
        """Wrap a `LocalRegistry`'s open connection.

        The data store does NOT take ownership of the connection — closing
        the data store leaves the registry's connection open. The registry
        must already be open (i.e. inside its ``with`` block).

        Args:
            registry: An open `LocalRegistry`. Its connection is reused.
            attach: ``alias → ATTACH spec`` mapping. Executed at open time.
            extensions: DuckDB extensions to INSTALL+LOAD before any ATTACH.
            azure_credentials: Optional Azure secret config. See
                `__init__` for accepted keys.
        """
        # `Self`-annotated because `cls.__new__(cls)` is otherwise untyped: the
        # local would be `Self@__new__` with no field information. (It also
        # silenced SLF001 before that rule was retired — see CLAUDE.md "The
        # public / private barrier".)
        instance: Self = cls.__new__(cls)
        instance._uri = registry.uri
        instance._attach = attach
        instance._extensions = extensions
        instance._azure_credentials = azure_credentials
        instance._registry = registry
        instance._conn = registry.connection
        instance._owns_connection = False
        instance._thread_local = threading.local()
        instance._inspector = None
        instance._inspector_lock = threading.Lock()
        instance._configure_session()
        return instance

    def _configure_session(self) -> None:
        """Apply the configured extensions + ATTACH specs to the live connection."""
        if self._conn is None:
            return
        extensions = list(self._extensions or [])
        # Auto-load the azure extension if we need to talk to abfss:// or
        # have credentials configured. Callers can override by listing
        # "azure" in extensions themselves — INSTALL/LOAD are idempotent.
        if self._needs_azure() and "azure" not in extensions:
            extensions.append("azure")
        if extensions:
            for ext in extensions:
                self._conn.execute(f"INSTALL {_sql_identifier(ext)}")
                self._conn.execute(f"LOAD {_sql_identifier(ext)}")
            # Loading extensions can change the search_path; reset to
            # ``sdmx`` so any colocated LocalRegistry's internal queries stay clean.
            self._conn.execute("SET search_path = 'sdmx'")
        if self._needs_azure():
            # Force the named curl transport adapter. The Azure SDK's
            # default transport on Linux uses a statically-linked HTTP
            # stack that doesn't honour the standard ``SSL_CERT_FILE`` /
            # ``CURL_CA_BUNDLE`` environment variables, so it fails on
            # any image whose CA store is at a non-default path. The
            # ``curl`` adapter delegates to libcurl, which reads those
            # env vars at handshake time.
            #
            # ``SET GLOBAL`` (not bare ``SET``) is required because the
            # data-plane uses per-thread cursors (Phase 5 of #75). A
            # bare ``SET`` is session-scoped and bypassed by every cursor
            # opened later, so the curl transport wouldn't take effect on
            # the codepath where abfss reads actually happen — surfacing
            # as "Problem with the SSL CA cert" on every data query. See
            # issue #81.
            self._conn.execute("SET GLOBAL azure_transport_option_type = 'curl'")
        if self._azure_credentials:
            sql = _azure_create_secret_sql(self._azure_credentials)
            self._conn.execute(sql)
        if self._attach:
            self._run_attaches()

    def _run_attaches(self) -> None:
        """ATTACH each configured catalog, translating DuckLake skew errors.

        A DuckLake catalog whose format is newer than the installed
        ``ducklake`` extension surfaces as an opaque DuckDB exception; we
        re-raise it as `DuckLakeVersionError` with the fix. Any other
        ATTACH failure propagates unchanged. See issue #156.
        """
        conn = self._conn
        if conn is None:  # guarded by caller; narrows the type here too
            return
        for alias, spec in (self._attach or {}).items():
            url, options = _split_attach_spec(spec)
            if _already_attached(conn, alias, url):
                continue
            sql = f"ATTACH {url} AS {_sql_identifier(alias)}"
            if options:
                sql += f" {options}"
            try:
                conn.execute(sql)
            except duckdb.Error as exc:
                if _is_ducklake_version_mismatch(exc):
                    msg = (
                        f"cannot attach DuckLake catalog {alias!r}: its catalog format is "
                        "newer than the installed ducklake extension supports. Upgrade "
                        "duckdb (>=1.5.3) and/or the ducklake extension, then retry. "
                        f"(underlying: {exc})"
                    )
                    raise DuckLakeVersionError(msg) from exc
                raise
        conn.execute("SET search_path = 'sdmx'")

    def _needs_azure(self) -> bool:
        """True if Azure secret/extension setup is required for this session.

        Matches against ATTACH specs that either (a) name an ``abfss://``
        path directly or (b) ATTACH a DuckLake catalog (``ducklake:``)
        whose ``data_path`` is stored inside the lake metadata file —
        invisible to the ATTACH SQL but routed through the Azure backend
        on first read. Catching ``ducklake:`` here is over-broad for
        purely local lakes, but the cost of a spurious curl-transport SET
        is zero (DuckDB ignores it until something talks to Azure); the
        cost of a false negative is a TLS handshake failure on every data
        query. See issue #81.
        """
        if self._azure_credentials:
            return True
        if self._attach:
            for spec in self._attach.values():
                spec_text = spec if isinstance(spec, str) else " ".join(s for s in spec if s)
                if "abfss://" in spec_text or "ducklake:" in spec_text:
                    return True
        return False

    def __enter__(self) -> Self:
        if self._conn is None:
            self.open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the database connection (only if we own it)."""
        if self._conn is not None and self._owns_connection:
            self._conn.close()
        self._conn = None
        self._thread_local = threading.local()
        self._inspector = None

    @override
    def _session_base(self) -> duckdb.DuckDBPyConnection:
        return self.connection

    def inspect(self) -> SchemaInspector:
        """Return this store's `SchemaInspector` — the custom-SQL seam.

        The inspector reflects the live DuckDB session (cached ``DESCRIBE``)
        and turns a `DataflowBinding` into a `QueryPlan`, so consumers who
        build their own SQL against `connection` reuse the store's table-name
        convention and sidecar join-key derivation. The returned inspector is
        the same instance the internal ``query`` path uses, so its column
        cache is shared.

        Raises:
            RuntimeError: If the data store hasn't been opened.
        """
        conn = self.connection  # raises if not open
        inspector = self._inspector
        if inspector is not None:
            return inspector
        with self._inspector_lock:
            # Double-checked: another thread may have built it while we waited.
            if self._inspector is None:
                self._inspector = SchemaInspector(conn)
            return self._inspector

    def _read_cursor(self) -> duckdb.DuckDBPyConnection:
        """Return this thread's read cursor for reads.

        Mirror of `_read_cursor` (#80). Lazily created on
        first call per thread, reused for the thread's lifetime.
        """
        # `getattr` on threading.local is untyped; annotate at the boundary so
        # the Any stops here instead of riding out through the return.
        cur: duckdb.DuckDBPyConnection | None = getattr(self._thread_local, "cur", None)
        if cur is not None:
            return cur
        conn = self.connection
        cur = conn.cursor()
        cur.execute("SET search_path = 'sdmx'")
        self._thread_local.cur = cur
        return cur

    # ── DataStore protocol ───────────────────────────────────────────────

    def _require_attached(self, binding: DataflowBinding) -> None:
        """Refuse a binding whose *catalog* this store has not attached.

        A binding names its table as ``<database_role>.<table_location>``, and
        the role is a DuckDB catalog. A registry opened with
        `LocalRegistry.bundle` attaches the bundle's DuckLake as ``lake``; one
        opened as a plain file attaches nothing, so a bundle's binding read
        through it reached DuckDB as
        ``Binder Error: Catalog "lake" does not exist!`` — naming neither the
        binding nor the fix.

        This is **not** the absence rule of §2. `obs` is not a method
        that needs a lake: it answers perfectly well on a plain registry whose
        binding points at a local table, and design §5.17 was wrong to frame it
        as a method-set question. What needs the lake is a *binding*, which is
        data, and data cannot be checked by a type.

        **The catalog only, never the table.** Probing the whole name with a
        `LIMIT 0` was the second attempt and was wrong for the case that
        matters: `RestRegistry.obs` synthesises a binding for a table
        its fetch has not created yet, so the probe refused every remote
        observation query. A missing catalog is always an error; a missing
        table may simply be one fetch away.

        **And it declines to answer rather than guess.** An empty catalog list
        means the question could not be answered here, not that nothing is
        attached — the first version treated the two as the same and refused
        every binding with "attached: none" about one run in three under the
        concurrency suite. A diagnostic check must never turn a working query
        into a failure.

        Raises:
            KeyError: Naming the dataflow, the table and the constructor that
                attaches the catalog.
        """
        role = binding.database_role
        rows = self._read_cursor().execute("SELECT database_name FROM duckdb_databases()").fetchall()
        attached = {str(row[0]) for row in rows}
        if not attached or role in attached:
            return
        msg = (
            f"{binding.dataflow.urn} is bound to {role}.{binding.table_location}, but no "
            f"catalog named {role!r} is attached here (attached: {', '.join(sorted(attached))}). "
            f"A bundle's observations live in its DuckLake, which `LocalRegistry.bundle(path)` "
            f"attaches and `LocalRegistry(path)` does not — open it as a bundle, or rebind the "
            f"dataflow to a table this registry can see."
        )
        raise KeyError(msg)

    def obs(self, binding: DataflowBinding, *, dsd: DataStructure | None = None) -> ObsQuery:
        """A lazy query over this dataflow's observations.

        The same path language as `LocalRegistry.query`, rooted at a dataflow
        instead of at a structural relation — so a filter is a predicate over
        named components rather than a SQL string:

            store.obs(binding).filter(
                obs["REF_AREA"] == "CA", obs["TIME_PERIOD"] >= "2020"
            ).collect()

        where `obs` is `Observations(binding)`. Component ids come from the
        binding's column maps, and an unknown one raises with the valid ones
        listed — the check a downstream SDMX REST service hand-writes as a 400.

        `where=` / `columns=` / `order_by=` on `query` remain, as the escape
        hatch for what this cannot express (value formatting, PIVOT). They are
        SQL text; this is not.

        Args:
            binding: The dataflow's binding.
            dsd: The dataflow's structure, for labelling. A store cannot
                resolve one — that needs a structural registry — so this is
                filled in by `LocalRegistry.obs` and left empty
                here.

        Returns:
            A `Query` over its observations, on this store's read cursor.

        Raises:
            ValueError: If the binding has a series-attributes sidecar, which
                this root does not join yet.
            KeyError: If the binding's `database_role` names a catalog this
                store has not attached — see `_require_attached`.
        """
        self._require_attached(binding)
        root = Observations(
            binding, dsd, self._sidecar_of(binding), physical=lambda: self._physical_types(binding, dsd)
        )
        # No resolver: an observation is a row, not an artefact, so the
        # object-returning terminals have nothing to build. See #297 §6.2 —
        # they should be *absent* rather than refusing, which needs the
        # Query/KeyedQuery split.
        query = build(root, self._read_cursor(), None)
        # Rebuilt as the observation-aware subclass so a caller can name
        # columns off the query itself, without also holding the accessor.
        #
        # `source` is the **base** connection, not the cursor the query runs
        # on: a data-plane query and a structural query from the same registry
        # are one database, and comparing cursors would refuse exactly the
        # composition `test_a_relation_valued_filter_crosses_the_planes` pins.
        return ObsQuery(
            plan=query.plan,
            conn=self._read_cursor(),
            resolve=None,
            source=self.connection,
            source_label=self._source_label(),
        )

    def _physical_types(self, binding: DataflowBinding, dsd: DataStructure | None) -> Mapping[str, str]:
        """``{physical column: DuckDB type}`` for the binding's table — what a column's kind falls back to (#511).

        From the table when it exists (`SchemaInspector.column_types`, one
        cached ``DESCRIBE``). A remote registry's table does not exist until
        its first fetch, and that fetch creates it with `column_types` — so
        those are its types, known before it exists. Asked only for a
        component the DSD leaves untyped.
        """
        try:
            return self.inspect().column_types(f"{binding.database_role}.{binding.table_location}")
        except duckdb.CatalogException:
            return column_types(binding, dsd) if dsd is not None else {}

    def _source_label(self) -> str:
        """How a refusal names this store's database (§5.9).

        Deferred to the registry when there is one, so both planes of a shared
        connection name it the same way.
        """
        registry = self.registry
        if registry is not None:
            return registry._source_label()
        uri = str(self._uri)
        return f"{uri!r} <0x{id(self):x}>" if uri == ":memory:" else repr(uri)

    def _sidecar_of(self, binding: DataflowBinding) -> Sidecar | None:
        """Where this binding keeps its series-level attributes, if not inline.

        `SchemaInspector.plan` already derives the sidecar and the columns the
        two tables share — the series key — from the tables themselves rather
        than from a declaration, behind a cached `DESCRIBE`. This adds the
        sidecar's full column list, which is what decides *which side* a
        component lives on.

        The store answers this rather than the root because the inspector lives
        here, and because it is a question about physical layout: the same
        component in another binding may sit on the fact table.
        """
        plan = self.inspect().plan(binding)
        if plan.sidecar is None:
            return None
        return Sidecar(plan.sidecar, plan.join_keys, frozenset(self.inspect().columns(plan.sidecar)))

    def ingest_csv(
        self,
        *,
        schema: str,
        table: str,
        path: Path,
        types: Mapping[str, str],
        lake_alias: str = "lake",
    ) -> None:
        """Load a CSV file straight into a table, without materialising it here.

        DuckDB reads the file itself, so the rows never pass through a Python
        object. Measured against the two alternatives on 2M rows — reading the
        bytes into polars, and scanning to Arrow IPC and back — this is both
        the fastest and the lightest, and by enough that there is no axis to
        trade off:

            bytes -> polars -> duckdb    0.30 s   660 MB
            file  -> ipc    -> duckdb    0.27 s   608 MB
            duckdb reads the file        0.21 s   307 MB

        **`types` is a partial override, and that is deliberate.** Only the
        columns a caller names are forced; the rest are inferred. An SDMX-CSV
        envelope differs between 1.0 (`DATAFLOW`, `KEY`) and 2.0 (`STRUCTURE`,
        `STRUCTURE_ID`, `ACTION`), so a complete map would have to know which
        one arrived — while the *components* are known from the structure
        either way. Forcing those is what matters: a code column of `007` or
        `0100` inferred as an integer is silent corruption.

        Args:
            schema: Target schema, created if absent.
            table: Target table, replaced if it exists.
            path: The CSV file to read.
            types: Column name to DuckDB type, for the columns worth forcing.
            lake_alias: Catalog the table lives in.

        Raises:
            ValueError: If any identifier is not a plain SQL identifier.
        """
        for name, value in (("schema", schema), ("table", table), ("lake alias", lake_alias)):
            if not _is_valid_identifier(value):
                msg = f"Invalid {name} identifier: {value!r}"
                raise ValueError(msg)
        conn = self.connection
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {lake_alias}.{schema}")
        conn.execute(
            f"CREATE OR REPLACE TABLE {lake_alias}.{schema}.{table} AS SELECT * FROM read_csv(?, types=?, header=true)",
            [str(path), dict(types)],
        )
        # A column `types` leaves out is inferred, so a replacement may change
        # its type (#518) — and the inspector's cached `DESCRIBE` with it.
        self.inspect().invalidate(f"{lake_alias}.{schema}.{table}")

    def write_observations(
        self,
        *,
        schema: str,
        table: str,
        source: WriteSource,
        partition_by: tuple[str, ...] = (),
        sort_by: tuple[str, ...] = (),
        mode: Literal["overwrite", "append"] = "overwrite",
        lake_alias: str = "lake",
    ) -> None:
        """Materialise observation rows into a DuckLake-managed table.

        Routes through the ATTACHed lake (``lake_alias``, default ``lake``).
        Schema is created if missing. ``mode="overwrite"`` uses
        ``CREATE OR REPLACE TABLE``; ``mode="append"`` uses ``INSERT INTO``
        and requires the target table to already exist with a compatible
        schema. ``partition_by`` is currently informational — DuckLake
        infers partition columns from table definition; we record the
        layout on the matching `DataflowBinding`.

        Args:
            schema: Target schema inside the lake (created if absent).
            table: Target table name. Together with ``schema`` and
                ``lake_alias`` this resolves to
                ``<lake_alias>.<schema>.<table>``.
            source: Rows to write. Anything DuckDB can register as a
                virtual relation — Polars frames, Arrow tables, or an
                existing DuckDB relation.
            partition_by: SDMX dimension ids the table is partitioned on.
            sort_by: Columns of ``source`` to store the rows sorted by,
                outermost first. Empty writes them in the order they arrive.

                **Opt-in, and the reason is the cost.** Measured on 4M rows of
                three columns, sorting on the way in cost 82 ms against 33 ms
                and 342 MB of peak RSS against 132 MB — roughly 2.5x both. It
                buys a query-side ``ORDER BY`` at 102 MB instead of 251 MB,
                slightly cheaper scans, and better pruning. Paying once at
                write for every read is usually right, and it is not right when
                ingestion is the memory-constrained step, so the caller decides.

                Physical column names, not SDMX ids: this method holds a source
                and a target, never a binding, so an id would have nothing to
                resolve against. Record the SDMX ids on the binding's
                ``sort_order`` alongside.
            mode: ``"overwrite"`` replaces the table atomically;
                ``"append"`` inserts into an existing table.
            lake_alias: ATTACH alias of the target lake. Defaults to
                ``"lake"`` to match the conventional bundle layout.
        """
        if not _is_valid_identifier(schema):
            msg = f"Invalid schema identifier: {schema!r}"
            raise ValueError(msg)
        if not _is_valid_identifier(table):
            msg = f"Invalid table identifier: {table!r}"
            raise ValueError(msg)
        if not _is_valid_identifier(lake_alias):
            msg = f"Invalid lake alias: {lake_alias!r}"
            raise ValueError(msg)

        conn = self.connection
        # Registered for this session, not created as a view: a view is
        # catalog state, and DuckDB refuses one over a Python object.
        view_name = f"__sdmxlib_write_obs_{id(source):x}"
        completed(conn).register(view_name, _as_relation(conn, source))
        try:
            target = f"{lake_alias}.{schema}.{table}"
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {lake_alias}.{schema}")
            order = _sort_clause(conn, view_name, sort_by)
            if mode == "overwrite":
                conn.execute(f"CREATE OR REPLACE TABLE {target} AS SELECT * FROM {view_name}{order}")
            elif mode == "append":
                # An append sorts only what it inserts. The table as a whole is
                # then sorted in runs rather than globally, which is what every
                # append-structured store gives you and why they all compact —
                # DuckLake's own rewrite is the compaction here.
                conn.execute(f"INSERT INTO {target} SELECT * FROM {view_name}{order}")
            else:
                msg = f"Unknown mode: {mode!r} (expected 'overwrite' or 'append')"
                raise ValueError(msg)
        finally:
            completed(conn).unregister(view_name)
        # A rewrite can change the target table's schema; drop cached column
        # lists so the inspector (internal query path + custom SQL) re-reflects.
        # The target may be cached under a differently-qualified name than the
        # one written here (2- vs 3-part, alias skew), so clear the whole
        # (tiny) cache rather than guess the exact key. Writes are rare.
        if self._inspector is not None:
            self._inspector.invalidate_all()
        _ = partition_by  # silence unused — see docstring


# ── SQL helpers ─────────────────────────────────────────────────────────


def _sort_clause(conn: duckdb.DuckDBPyConnection, view_name: str, sort_by: tuple[str, ...]) -> str:
    """`ORDER BY` for a write, with every column checked against the source.

    Identifier interpolation, so the names cannot come from anywhere but the
    source relation itself: a name that is not a column of `view_name` is
    refused here rather than reaching the statement. The check also catches the
    likelier mistake, which is passing an SDMX component id where a physical
    column was wanted — `REF_AREA` when the column is `GEO`.
    """
    if not sort_by:
        return ""
    available = {str(row[0]) for row in conn.execute(f"DESCRIBE {view_name}").fetchall()}
    unknown = [name for name in sort_by if name not in available]
    if unknown:
        msg = (
            f"cannot sort the write by {', '.join(unknown)}: not "
            f"{'a column' if len(unknown) == 1 else 'columns'} of the source, which has "
            f"{', '.join(sorted(available))}. `sort_by` takes physical column names, not SDMX ids."
        )
        raise ValueError(msg)
    return " ORDER BY " + ", ".join(_sql_identifier(name) for name in sort_by)


def _sql_identifier(name: str) -> str:
    """Escape a SQL identifier (extension name, ATTACH alias).

    DuckDB identifiers are case-insensitive and may contain alphanumerics
    and underscores. Reject anything else to avoid SQL injection through
    extension/alias names.
    """
    if not _is_valid_identifier(name):
        msg = f"Invalid SQL identifier: {name!r}"
        raise ValueError(msg)
    return name


def _is_valid_identifier(name: str) -> bool:
    """True if ``name`` is a safe SQL identifier (alphanumeric + underscore)."""
    return bool(name) and all(c.isalnum() or c == "_" for c in name)


def _already_attached(conn: duckdb.DuckDBPyConnection, alias: str, url: str) -> bool:
    """Whether `alias` is attached already, to the catalog `url` names.

    DuckDB shares one database instance between connections to the same file
    in a process, so a second registry over a bundle sees the first's ``lake``:
    a bundle checked or profiled while a server holds it open, and the bundle
    tier's own fixtures. Attaching again fails ("database with name ... already
    exists"); attached to the same catalog, there is nothing to do.

    Raises:
        ValueError: If `alias` is attached to a different catalog, which the
            registry would read in place of its own.
    """
    row = conn.execute("SELECT path FROM duckdb_databases() WHERE database_name = ?", [alias]).fetchall()
    if not row:
        return False
    wanted = url.strip("'").removeprefix("ducklake:")
    held = row[0][0] or ""
    if Path(held).resolve() == Path(wanted).resolve():
        return True
    msg = f"{alias!r} is attached to {held} in this process; this registry wants {wanted}"
    raise ValueError(msg)


def _split_attach_spec(spec: object) -> tuple[str, str]:
    """Normalise an ATTACH spec into ``(url, options)``.

    Two shapes accepted:

    - ``"'ducklake:/path'"`` — connection URL only; no options.
    - ``("'ducklake:/path'", "(DATA_PATH '/p/')")`` — URL + trailing
      options clause that goes after ``AS alias``.

    Returns ``(url, options)`` strings; ``options`` is empty when none.
    """
    if isinstance(spec, str):
        return spec, ""
    if isinstance(spec, tuple) and len(spec) == 2:
        url, options = spec
        return str(url), str(options or "")
    msg = f"Unsupported ATTACH spec shape: {type(spec).__name__}"
    raise TypeError(msg)


def _azure_create_secret_sql(creds: dict[str, str]) -> str:
    """Render a DuckDB ``CREATE SECRET`` statement for an Azure credential dict.

    Three accepted shapes:

    * ``{"connection_string": "..."}`` → ``PROVIDER 'config'`` with
      ``CONNECTION_STRING``.
    * ``{"account_name": ..., "tenant_id": ..., "client_id": ...,
      "client_secret": ...}`` → ``PROVIDER 'service_principal'``.
    * ``{"chain": "cli"|"env"|"managed_identity"}`` →
      ``PROVIDER 'credential_chain'``.
    """
    name = "sdmxlib_azure"
    if "connection_string" in creds:
        cs = _sql_string_literal(creds["connection_string"])
        return f"CREATE OR REPLACE SECRET {name} (TYPE AZURE, PROVIDER 'config', CONNECTION_STRING {cs})"
    if "chain" in creds:
        chain = _sql_string_literal(creds["chain"])
        clauses = ["TYPE AZURE", "PROVIDER 'credential_chain'", f"CHAIN {chain}"]
        if "account_name" in creds:
            clauses.append(f"ACCOUNT_NAME {_sql_string_literal(creds['account_name'])}")
        return f"CREATE OR REPLACE SECRET {name} ({', '.join(clauses)})"
    required = {"account_name", "tenant_id", "client_id", "client_secret"}
    if required.issubset(creds):
        return (
            f"CREATE OR REPLACE SECRET {name} ("
            f"TYPE AZURE, PROVIDER 'service_principal', "
            f"TENANT_ID {_sql_string_literal(creds['tenant_id'])}, "
            f"CLIENT_ID {_sql_string_literal(creds['client_id'])}, "
            f"CLIENT_SECRET {_sql_string_literal(creds['client_secret'])}, "
            f"ACCOUNT_NAME {_sql_string_literal(creds['account_name'])}"
            f")"
        )
    msg = (
        "azure_credentials must be one of: "
        "{'connection_string': ...}, "
        "{'account_name': ..., 'tenant_id': ..., 'client_id': ..., 'client_secret': ...}, "
        "or {'chain': 'cli'|'env'|'managed_identity'}. "
        f"Got keys: {sorted(creds.keys())}"
    )
    raise ValueError(msg)


def _sql_string_literal(value: str) -> str:
    """Quote a value as a SQL string literal — single-quotes, doubled internals."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _as_relation(
    conn: duckdb.DuckDBPyConnection,
    source: WriteSource,
) -> duckdb.DuckDBPyRelation:
    """Materialise ``source`` as a DuckDB relation the connection can scan.

    Handles Polars frames (eager + lazy), Arrow tables and readers, and
    existing DuckDB relations uniformly.

    **`LazyFrame` is collected; a `RecordBatchReader` is not.** That is the
    difference between a bounded write and one whose peak is the whole frame,
    so a caller with a large source hands over a reader — which is what
    `RestRegistry` does after streaming a fetch to disk.
    """
    if isinstance(source, duckdb.DuckDBPyRelation):
        return source
    if isinstance(source, pl.LazyFrame):
        source = source.collect()
    if isinstance(source, pl.DataFrame):
        return conn.from_arrow(source.to_arrow())
    # Last branch: pyarrow.Table. Imported lazily to keep pyarrow optional.
    return conn.from_arrow(source)


def column_types(binding: DataflowBinding, dsd: DataStructure) -> dict[str, str]:
    """DuckDB types for the columns a structure names — the ones worth forcing.

    **Every dimension and attribute is `VARCHAR`, without exception.** SDMX
    codes are strings, and a code list of `007`, `0100`, `2021` inferred as an
    integer loses its leading zeros — a silent corruption that survives every
    downstream join, because the values still *look* like codes.

    A measure takes its type from the DSD: `DOUBLE` for a numeric facet,
    `VARCHAR` for a declared text type or an enumerated one. **A measure the
    DSD leaves untyped is not forced at all** (#518): DuckDB infers it from
    the values, so a fetch of numbers is `DOUBLE` and compares as one. Forcing
    it to `VARCHAR`, as this did, made every numeric filter on the common
    untyped ``OBS_VALUE`` a refusal whose only fix — declaring a `Facet` — is
    the provider's to make. Inference is safe for a measure and not for a
    code: a wrong guess is a type a comparison refuses by name, not a code
    silently losing its leading zeros.

    Partial by design: the SDMX-CSV envelope columns are left to DuckDB, since
    which ones arrive depends on whether the provider sent 1.0 or 2.0.
    """
    types = dict.fromkeys(binding.dimension_column_map.values(), "VARCHAR")
    types.update(dict.fromkeys(binding.attribute_column_map.values(), "VARCHAR"))
    for component_id, column in binding.measure_column_map.items():
        representation = getattr(dsd.find_component(component_id), "representation", None)
        if isinstance(representation, Ref):
            types[column] = "VARCHAR"
        elif isinstance(representation, Facet) and representation.type is not None:
            numeric = str(representation.type).lower() in NUMERIC_FACETS
            types[column] = "DOUBLE" if numeric else "VARCHAR"
    return types


#: Facet types that mean "a number" for a measure column. Lowercased on the way
#: in, so `Double`, `Integer` and `Numeric` all land here whatever the DSD
#: spells them.
NUMERIC_FACETS = frozenset(
    {"double", "float", "decimal", "integer", "long", "short", "numeric", "bigdecimal", "biginteger"}
)
