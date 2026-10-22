"""A dataflow's observations as a query root (#297 step 4).

The path language compiles predicates, projections, aggregates and windows
against a *relation*. Every relation it has served so far is structural and
declared: `q.code` names `sdmx.code`, whose columns are known at import.

A dataflow's observations are neither. The table comes from a
`DataflowBinding` — `<database_role>.<table_location>` — and the columns are
whatever that dataflow's DSD says, discovered when the binding is read. This
module is the root that closes that gap, so the same language reaches both.

**Why the same language rather than a second one.** `docs/design/data-plane-query.md`
reads both consumers' hand-written SQL and finds that most of what they build
— comparisons, the LIKE family, set membership, projection, GROUP BY, per-group
top-N via `QUALIFY row_number() OVER (…)`, sorting, paging — is already
compiled here. A second vocabulary would be a second set of null rules and a
second set of bugs.

## What this slice does and does not do

Does: a root with the binding's table, its dimensions as the key, and
`obs["GEO"]` resolving a component id to its physical column — including one
that lives on a series-attributes sidecar, which #332 added as a root join
(`Sidecar` below).

Does not, deliberately, and each has its own slice in the design doc:

* **Typed columns.** `obs["GEO"]` returns the permissive `Col`, on which
  comparisons, ordering and every aggregate work. The DSD knows whether a
  component is enumerated or carries a `DataType` facet (§6.3), and
  `obs.num(...)` / `obs.text(...)` will use it — but nothing here needs to be
  unpicked when they land.
* **`ids()` / `urns()`.** They have nothing to return for an observation and
  should be *absent* rather than raising (§6.2), which needs the
  `Query`/`KeyedQuery` split. Until then they are present and will fail at the
  database, which is the one lie this slice tells and the reason the split is
  next.
"""

from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal, Self, override

import attrs
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from sdmxlib.formats._arrow import frame_of
from sdmxlib.formats.sdmx_json import data_v1, data_v2
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.datastructure import ComponentRole, DataStructure, MeasureDimension
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from sdmxlib.polars import _TEXT_TYPE_MAP, validation_report
from sdmxlib.query._expr import Cols, ColumnKind, ComponentNum, ComponentStr, Num, RowKind, Str, Untyped
from sdmxlib.query._joins import RootJoin
from sdmxlib.query.query import Page, Query, Resolver, _typed
from sdmxlib.rest import ComponentFilter


@attrs.frozen
class Sidecar:
    """Where a dataflow keeps its series-level attributes, and how to reach them.

    SDMX attributes attach at a level. One attached at *series* level is
    constant across every observation of that series, so a producer may store
    it once per series rather than once per observation — twelve observations
    of three series carry three `GEO_NAME` values, not twelve.

    **Physical, not semantic.** The attachment level is declared by the DSD and
    is the same wherever the value is stored; whether there is a sidecar at all
    is a layout choice, and `series_attributes_location` is optional. So this
    changes the *plan* and never the spelling: `obs["GEO_NAME"]` means the same
    thing whether the column sits on the fact table or across a join.

    **One row per series, enforced.** "Once per series" means unique on the
    join keys, and a sidecar that is not would multiply every observation it
    matches — silently, in every count and aggregate over its columns.
    `SchemaInspector.plan` refuses one with `SidecarNotUniqueError` (#412), so
    a `Sidecar` that exists has been checked.

    Attributes:
        table: Fully-qualified sidecar table.
        join_keys: Columns common to both sides — derived by
            `SchemaInspector.plan` from the tables themselves, not declared.
            Which is why uniqueness is checked against *these*: a sidecar unique
            on its own key can still fan out if the two tables share fewer
            columns than that key.
        columns: Every column the sidecar has, for deciding which side a
            component lives on.
    """

    table: str
    join_keys: tuple[str, ...]
    columns: frozenset[str]


class ObsRow(RowKind):
    """One observation of one dataflow.

    **One kind for every dataflow, not one per dataflow.** It catches the error
    that matters — an observation predicate handed to a structural query, or
    the reverse — and permits the rarer one, predicates from two different
    dataflows, which the binder catches anyway because it resolves every column
    against a binding. The alternative, `ObsRow[Flow]` keyed on a marker type,
    buys static separation between flows at the cost of an annotation nobody
    writing a script will write.
    """

    __slots__ = ()


class Observations(Cols[ObsRow]):
    """The observations of one dataflow, as a query root.

    Unlike every other accessor this is built per binding rather than being a
    module singleton, which is why the compiler reads a root's identity from
    the *instance* (!338): two dataflows are two tables behind one class.

    Columns are named by SDMX component id, not by physical column:

        obs["REF_AREA"]        # -> the binding's column for that dimension
        obs["OBS_VALUE"]       # -> its measure column

    A caller who has the physical name already can still use it, since a
    binding maps some ids to themselves — but the id is what a DSD talks about,
    and it is what an error message lists.
    """

    #: What a backend calls this relation — the SDMX REST `/data` resource.
    #: Class-level and shared by every dataflow, unlike `__table__`, because it
    #: is what a `Capability` is keyed by and a capability cannot enumerate
    #: tables.
    __resource__ = "data"

    # No `__slots__`, deliberately. The four identity attributes below are
    # declared on `Cols` as *class* attributes because every other accessor is
    # a singleton whose relation is fixed by its type. Here they are per
    # instance, and a property would override a settable attribute with a
    # read-only one — which pyrefly rejects as an inconsistent override, and is
    # right to. An instance `__dict__` costs one dict per query root.

    def __init__(
        self,
        binding: DataflowBinding,
        dsd: DataStructure | None = None,
        sidecar: Sidecar | None = None,
        *,
        physical: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        """Root the language at `binding`'s table.

        Args:
            binding: The dataflow's binding. Its column maps supply every
                column name, and its dimensions supply the key.
            dsd: The dataflow's structure, when it is available. Only
                labelling needs it — a code's label lives in the codelist a
                component is enumerated by — and only a *catalog* can resolve
                one, so a root built straight from a store has none and says
                so when labels are asked for.

            sidecar: Where the series-level attributes live, when they are not
                on the fact table. Supplied by the store, which holds the
                inspector that derives it. **Independent of `dsd`**: which side
                a column lives on is answered by which table has it, so a
                store-built root reads a sidecar perfectly well without a DSD.
            physical: The table's ``{column: DuckDB type}``, asked for only
                when a component's DSD gives it no type (#511). A callable,
                because most components never need it and a remote table does
                not exist until its first fetch.
        """
        super().__init__()
        self._binding = binding
        self._dsd = dsd
        self._sidecar = sidecar
        self._physical = physical
        self._physical_types: Mapping[str, str] | None = None
        # SDMX id -> physical column, dimensions first so the key can be read
        # off in DSD order without a second pass.
        self._columns = {
            **binding.dimension_column_map,
            **binding.measure_column_map,
            **binding.attribute_column_map,
        }

        #: Qualified observation table — already two-part, hence `__schema__`.
        self.__table__ = f"{binding.database_role}.{binding.table_location}"
        #: `None`: the table name above qualifies itself.
        self.__schema__ = None
        #: The dimension columns, in DSD order.
        #:
        #: An observation has no surrogate key and is not unkeyed: the
        #: information model identifies it *by its dimension values*. So paging
        #: and the `ORDER BY` tiebreak work here for the same reason they work
        #: over a structural relation, rather than by special case.
        self.__key__ = tuple(binding.dimension_column_map.values())
        #: The binding's partition columns, mapped from SDMX ids to physical
        #: names. A partition id the binding does not map is dropped rather
        #: than guessed: a wrong partition column would plan a stream that
        #: silently returned a subset.
        self.__partitions__ = tuple(
            column for column in (self._columns.get(part) for part in binding.partition_layout) if column is not None
        )
        #: `None`. A dataflow's table holds that dataflow's rows and no others,
        #: while the structural constraint exists because many artefact types
        #: share `sdmx.artefact`.
        self.__constraint__ = None
        #: Component ids, not physical columns, for an unprojected result.
        #: Every predicate, projection and label in this language names a
        #: component — so a frame whose columns are one producer's physical
        #: names is the single place that leaks. It is also what
        #: `sdmxlib.polars.label` matches on, which is why labelling a renamed
        #: binding used to silently do nothing.
        self.__projection__ = tuple(
            (self._table_of(column), column, component_id) for component_id, column in self._columns.items()
        )
        #: The sidecar join, which every query over this dataflow needs and no
        #: path reaches — see `_expr.AnyCols.__joins__`.
        self.__joins__ = () if sidecar is None else (RootJoin(sidecar.table, sidecar.join_keys),)

    @property
    def sidecar(self) -> Sidecar | None:
        """Where this dataflow's series-level attributes live, if not inline."""
        return self._sidecar

    @property
    def dsd(self) -> DataStructure | None:
        """The dataflow's structure, when the root was built with one."""
        return self._dsd

    @property
    def binding(self) -> DataflowBinding:
        """The binding this root reads. Public because callers need the DSD."""
        return self._binding

    @property
    def component_ids(self) -> tuple[str, ...]:
        """Every component id this root can name — dimensions, then measures, then attributes."""
        return tuple(self._columns)

    def __getitem__(self, component_id: str) -> Untyped[ObsRow]:
        """The column for one SDMX component.

        Returns `Untyped`, which is what it has always constructed — the
        declaration said `Col` and the docstring claimed comparisons and
        ordering were "on the base". They are not: `Col` carries `__eq__`
        alone, and `>=` on an observation column type-checked nowhere while
        working everywhere (#347). `Untyped` is the class that offers exactly
        the operations meaning one thing without knowing the column's type,
        which is the property this method is trading on.

        Still no *typed* column: the physical column is whatever its producer
        wrote, so nothing here claims a type the binding cannot confirm. §6.3
        of the design doc has the typed forms, which read the DSD.

        Raises:
            KeyError: Naming the valid ids. A component id is exactly the kind
                of thing a caller mistypes or half-remembers, and the binding
                already holds the answer.
        """
        column = self._column(component_id)
        return Untyped((), self._table_of(column), column, component_id, self.kind_of(component_id))

    def text(self, component_id: str) -> Str[ObsRow]:
        """The column for one component, as text — exact `Str`, checked against what it holds (#511).

        Raises:
            KeyError: If *component_id* is not a component, naming those that are.
            TypeError: If the DSD or the store says it holds numbers.
        """
        column = self._column(component_id)
        kind = self.kind_of(component_id)
        if kind is not None and kind.holds != "str":
            msg = (
                f"{component_id} {kind.because}, so its values are numbers, not text — "
                f"obs.num({component_id!r}) reads it as one."
            )
            raise TypeError(msg)
        return ComponentStr((), self._table_of(column), column, component_id)

    def num(self, component_id: str) -> Num[ObsRow]:
        """The column for one component, as a number — exact `Num`, checked against what it holds (#511).

        Raises:
            KeyError: If *component_id* is not a component, naming those that are.
            TypeError: If the DSD or the store says it holds text — a code, a
                time period, a string.
        """
        column = self._column(component_id)
        kind = self.kind_of(component_id)
        if kind is not None and kind.holds != "num":
            msg = (
                f"{component_id} {kind.because}, so its values are text, not numbers — "
                f"obs.text({component_id!r}) reads it as text."
            )
            raise TypeError(msg)
        return ComponentNum((), self._table_of(column), column, component_id)

    def kind_of(self, component_id: str) -> ColumnKind | None:
        """What a component's column holds, or `None` when nothing says.

        **The DSD first** (design §6.3): an enumerated component holds codes —
        text, whether or not its codelist is resolved, and a measure
        dimension's are concept ids — and a `Facet` its declared type. Read
        from the representation rather than through `sdmxlib.polars.schema`,
        which would list every code of every codelist to type one column.

        **The store second**, for a component the DSD leaves untyped: its
        physical column's type. A type neither text nor a number — a date, a
        boolean — checks nothing, as an unknown one does.
        """
        dsd = self._dsd
        component = dsd.find_component(component_id) if dsd is not None else None
        if isinstance(component, MeasureDimension):
            return ColumnKind("str", "is the measure dimension, whose values are concept ids")
        representation = getattr(component, "representation", None)
        if isinstance(representation, Ref):
            return ColumnKind("str", f"is enumerated by {representation.urn.id}")
        if isinstance(representation, Facet) and representation.type is not None:
            return _kind_of_dtype(_TEXT_TYPE_MAP.get(str(representation.type)), f"is declared {representation.type}")
        stored = self._stored_types().get(self._columns.get(component_id, ""))
        if stored is None:
            return None
        return _kind_of_duckdb(stored, f"has no type in its DSD and is stored as {stored}")

    def _stored_types(self) -> Mapping[str, str]:
        """The table's column types, read once and only when a component needs them."""
        if self._physical_types is None:
            self._physical_types = self._physical() if self._physical is not None else {}
        return self._physical_types

    def _column(self, component_id: str) -> str:
        """The physical column for *component_id*.

        Raises:
            KeyError: Naming the valid ids.
        """
        column = self._columns.get(component_id)
        if column is None:
            known = ", ".join(sorted(self._columns)) or "(none — this binding maps no components)"
            msg = f"{component_id!r} is not a component of this dataflow. It has: {known}"
            raise KeyError(msg)
        return column

    def role_of(self, column: str) -> ComponentRole | None:
        """What part of the shape a *physical column* plays, or `None`.

        The question a backend capability is declared over — see
        `ComponentRole`. Takes a physical column name rather than a component
        id because that is what a compiled predicate carries.

        **`None` when this root has no DSD, and that is a refusal rather than a
        gap.** A binding alone can say a column is *a dimension*; only a DSD can
        say whether it is in the key, where in the key, or whether it is the
        time dimension. Guessing any of those builds a positional SDMX key with
        a wrong slot, which returns a different slice of data with no error —
        the failure `docs/design/data-plane-pushdown.md` §6 names as one of the
        three that would falsify the design. So a root built without a DSD
        pushes nothing and every predicate stays local, which is slower and
        correct.

        Args:
            column: A physical column name, as it appears on the table.

        Returns:
            The role, or `None` when there is no DSD, when the column is not
            one this binding maps, or when the DSD does not define the
            component the binding maps it to.
        """
        if self._dsd is None:
            return None
        component_id = self._binding.component_of(column)
        if component_id is None:
            return None
        return self._dsd.role_of(component_id)

    def _table_of(self, column: str) -> str:
        """Which side of the join a column lives on.

        A *join key* lives on both, and `USING` merges them — so reading one
        through the root alias is correct. Everything else the sidecar carries
        must be read through the sidecar's alias, because `USING` merges only
        the keys and `s0.GEO_NAME` would be a binder error.
        """
        if self._sidecar is None or column in self._sidecar.join_keys:
            return self.__table__
        return self._sidecar.table if column in self._sidecar.columns else self.__table__


class ObsQuery(Query[ObsRow]):
    """A query over observations, which also names their columns.

    Every structural query needs two objects — the accessor (`sl.q.code`, a
    module singleton) and the query — and that is fine there, because the
    accessor is importable and shared. An observation accessor is neither: it
    is built per binding, so making a caller hold both means making them find
    the binding first.

        obs = store.obs(binding)
        obs.filter(obs.col("REF_AREA") == "ON").select(v=obs.col("OBS_VALUE")).collect()

    So the query carries the accessor and exposes its columns. Refinements
    return `Self` and `attrs.evolve` keeps the concrete class, so column access
    survives the whole chain rather than being lost at the first `filter`.

    **`col()` rather than `[]`**, which was the first cut: `Query.__getitem__`
    already means *look up a row by id* for a string, and slicing for a slice.
    Overriding it here would give one spelling two meanings depending on the
    root — the LSP violation basedpyright rejected, and a genuine trap for
    anyone reading a query without knowing which root it has.

    This is also where §6.2's `Query`/`KeyedQuery` split will land: `ids()` and
    `urns()` have nothing to return for an observation and should be absent
    here rather than raising.
    """

    def col(self, component_id: str) -> Untyped[ObsRow]:
        """The column for one SDMX component. See `Observations.__getitem__`.

        Declared `Untyped`, which is what it always returned (#413). It said
        `Col` — the lie #347 corrected on `__getitem__` and left here — so the
        operators `Untyped` really offers were invisible to every checker and
        every reader: the comparisons, and `.str`'s `contains`, `starts_with`,
        `ends_with` and `caseless` rung. Those four are SDMX-REST's `co`, `nc`
        (negated), `sw` and `ew`, with the `LIKE` escaping done by
        `_label_match` rather than by each consumer.
        """
        root = self.plan.root
        if not isinstance(root, Observations):  # pragma: no cover - built one way
            msg = f"this query is not rooted at observations, but at {type(root).__name__}"
            raise TypeError(msg)
        return root[component_id]

    def text(self, component_id: str) -> Str[ObsRow]:
        """The column for one component as text — exact `Str[ObsRow]` where `col` gives `Untyped` (#511).

        `col` has one signature for every id, so it cannot promise a type; this
        is where a caller states the expectation and the model checks it:
        ``obs.text("REF_AREA").str.contains("C")``. See `Observations.text`.

        Raises:
            KeyError: If *component_id* is not a component.
            TypeError: If the DSD or the store says it holds numbers.
        """
        return self._root().text(component_id)

    def num(self, component_id: str) -> Num[ObsRow]:
        """The column for one component as a number — exact `Num[ObsRow]` (#511).

        ``obs.num("OBS_VALUE") > 1.5``, with `Num`'s aggregates and arithmetic
        visible to a checker. See `Observations.num`.

        Raises:
            KeyError: If *component_id* is not a component.
            TypeError: If the DSD or the store says it holds text — a code, a
                time period, a string.
        """
        return self._root().num(component_id)

    def _root(self) -> Observations:
        root = self.plan.root
        if not isinstance(root, Observations):  # pragma: no cover - built one way
            msg = f"this query is not rooted at observations, but at {type(root).__name__}"
            raise TypeError(msg)
        return root

    #: What an observation query answers with instead of `ids()`, `collect()`
    #: and the rest: an observation is a row keyed by its dimensions, not an
    #: artefact with an id, so those reached the database and failed there on
    #: a column it does not have — and the refusal the others gave named
    #: `.ids()` as the fix (#511).
    _ROWS_NOT_ARTEFACTS: ClassVar[str] = (
        "an observation is a row of its dataflow, keyed by its dimensions — it has no id and builds "
        "no artefact. Use .frame() or .select(obs.col(...)) for its columns, or .stream() for batches."
    )

    @override
    def ids(self) -> list[str]:
        msg = f"ids(): {self._ROWS_NOT_ARTEFACTS}"
        raise TypeError(msg)

    @override
    def urns(self) -> list[str]:
        msg = f"urns(): {self._ROWS_NOT_ARTEFACTS}"
        raise TypeError(msg)

    @override
    def page(self, n: int) -> Page[ObsRow, object]:
        msg = f"page(): {self._ROWS_NOT_ARTEFACTS}"
        raise TypeError(msg)

    @override
    def _resolver(self, terminal: str) -> Resolver:
        msg = f"{terminal}(): {self._ROWS_NOT_ARTEFACTS}"
        raise TypeError(msg)

    def filter_components(self, component_filter: ComponentFilter) -> Self:
        """Narrow by an SDMX REST component filter — a key, structurally (#329).

        `ComponentFilter` is what a dotted key plus `c[DIM]=…` parameters parse
        into, and it was the one *typed* thing the SQL-text API took. It is
        translated here so that API can go: each constrained component becomes
        `is_in`, which is what its own SQL rendering did.

            obs.filter_components(parse_components(key, params, dsd))

        `None` means unconstrained and contributes nothing. `[]` means the
        permitted set is empty and matches no row — `is_in([])` compiles to
        `1 = 0`, the reading polars and SQLAlchemy both take and the one #256
        argued from first principles. A `SqlRelation` value stays lazy, as a
        subquery.

        Args:
            component_filter: Dimension and attribute constraints, by SDMX id.

        Returns:
            The query, narrowed. Unchanged when the filter is a wildcard.

        Raises:
            KeyError: If the filter names a component this dataflow has not
                got, listing the ones it has. The filter is parsed against a
                DSD and applied against a binding, so a mismatch between them
                surfaces here rather than as a missing column at the database.
        """
        predicates = [
            self.col(component_id).is_in(allowed)
            for component_id, allowed in (
                *zip(component_filter.dimension_ids, component_filter.dimensions, strict=True),
                *component_filter.attributes.items(),
            )
            if allowed is not None
        ]
        return self.filter(*predicates)

    @property
    def components(self) -> tuple[str, ...]:
        """Every component id this query can name."""
        root = self.plan.root
        return root.component_ids if isinstance(root, Observations) else ()

    @property
    def structure(self) -> DataStructure | None:
        """The dataflow's `DataStructure`, or `None` for a query a store built without one.

        What the retired `Dataset` exposed piecemeal — `dimensions`,
        `measures`, `wide_dimension` — is this structure's to answer (#516):
        ``obs.structure.dimensions.ids()``, ``obs.structure.measure_dimension``.
        A registry's `obs(flow)` always resolves it; only
        `DuckDBDataStore.obs(binding)` without `dsd=` leaves it out.
        """
        root = self.plan.root
        return root.dsd if isinstance(root, Observations) else None

    def sink_parquet(self, path: str | Path, *, chunk_rows: int = 10_000) -> None:
        """Write the observations to a Parquet file, a batch at a time.

        Typed as `frame()` is — each batch of `stream()` cast from the DSD —
        so the file holds what a frame would, without the frame. `stream()`
        is the raw rows, for a store whose values do not all fit.

        Args:
            path: Destination file.
            chunk_rows: Rows per batch, and so per row group.
        """
        with self.stream(chunk_rows) as reader:
            schema = self._typed_batch(pa.RecordBatch.from_pylist([], schema=reader.schema)).to_arrow().schema
            with pq.ParquetWriter(path, schema) as writer:
                for batch in reader:
                    writer.write_table(self._typed_batch(batch).to_arrow())

    def sink_csv(self, path: str | Path, *, chunk_rows: int = 10_000) -> None:
        """Write the observations to a CSV file, a batch at a time.

        Plain CSV, a column per component, as `frame().write_csv` would write;
        `sdmxlib.formats.sdmx_csv.write_sdmx_csv` is the SDMX-CSV message.

        Args:
            path: Destination file.
            chunk_rows: Rows per batch.
        """
        with self.stream(chunk_rows) as reader, Path(path).open("wb") as out:
            self._typed_batch(pa.RecordBatch.from_pylist([], schema=reader.schema)).write_csv(out)
            for batch in reader:
                self._typed_batch(batch).write_csv(out, include_header=False)

    def sink_json(
        self,
        path: str | Path,
        *,
        lang: str,
        version: Literal["1.0", "2.0"] = "2.0",
        message_id: str | None = None,
        prepared: datetime | None = None,
        sender_id: str = "sdmxlib",
        chunk_rows: int = 10_000,
    ) -> None:
        """Write the observations as an SDMX-JSON data message, a batch at a time.

        Args:
            path: Destination file.
            lang: The language of the names the message carries — each
                dimension's, value's and the dataflow's — and its
                ``contentLanguages``. Required, as every multilingual call is.
            version: ``"2.0"`` (SDMX 3.0's ``{meta, data}``) or ``"1.0"``
                (SDMX 2.1's ``{header, structure, dataSets}``).
            message_id: ``meta.id`` / ``header.id``; defaults to one derived
                from the dataflow's id.
            prepared: When the message was prepared; timezone-aware, since the
                schemas require a zone. Defaults to UTC now.
            sender_id: The sender's id.
            chunk_rows: Rows per batch.

        Raises:
            ValueError: If the query has no `structure` — a message is
                written from one.
        """
        root = self.plan.root
        dsd = root.dsd if isinstance(root, Observations) else None
        if not isinstance(root, Observations) or dsd is None:
            msg = (
                "sink_json writes the message from the dataflow's DataStructure, and this query has none. "
                "Pass dsd= to DuckDBDataStore.obs, or use a registry's obs(flow)."
            )
            raise ValueError(msg)
        dataflow = root.binding.dataflow
        write = data_v2.write_sdmx_json_data if version == "2.0" else data_v1.write_sdmx_json_data
        with self.stream(chunk_rows) as reader, Path(path).open("wb") as out:
            out.writelines(
                write(
                    self._typed_batches(reader),
                    dsd,
                    dataflow,
                    message_id=message_id or f"sdmxlib-{dataflow.urn.id}",
                    prepared=prepared or datetime.now(UTC).replace(microsecond=0),
                    sender_id=sender_id,
                    lang=lang,
                )
            )

    def validate(self, *, chunk_rows: int = 100_000) -> pl.DataFrame:
        """Every value its component's codelist does not list, and how often.

        The report `frame()` cannot give: a typed read stops at the first bad
        value, and this one names them all —

            ┌──────────┬───────────────┬───────┐
            │ column   │ invalid_value │ count │
            └──────────┴───────────────┴───────┘

        empty when every value fits. It reads the raw rows (`stream()`) and
        reports a batch at a time (`sdmxlib.polars.validation_report`), so a
        dataflow is checked without being held.

        Args:
            chunk_rows: Rows per batch.

        Returns:
            The violations, components in DSD order, values sorted within one.

        Raises:
            ValueError: If the query has no `structure` to validate against.
        """
        dsd = self.structure
        if dsd is None:
            msg = (
                "validate() checks values against the dataflow's DataStructure, and this query has none. "
                "Pass dsd= to DuckDBDataStore.obs, or use a registry's obs(flow)."
            )
            raise ValueError(msg)
        with self.stream(chunk_rows) as reader:
            reports = [validation_report(dsd, frame_of(batch)) for batch in reader]
        found = pl.concat([validation_report(dsd, pl.DataFrame()), *reports])
        measure_dimension = [dsd.measure_dimension] if dsd.measure_dimension is not None else []
        components = [*dsd.dimensions, *measure_dimension, *dsd.attributes, *dsd.measures]
        order = {component.id: i for i, component in enumerate(components)}
        return (
            found.group_by("column", "invalid_value")
            .agg(pl.col("count").sum())
            .sort(pl.col("column").replace_strict(order, default=len(order)), "invalid_value")
        )

    def pivot(self, on: str | None = None) -> pl.DataFrame:
        """The observations wide: one column per value of a dimension.

        The axis is `on`, or the DSD's `MeasureDimension` — SDMX 2.1's formal
        signal that a dataflow arrives long, one ``OBS_VALUE`` per measure per
        row. Its values become columns, in the order its concept scheme or
        codelist lists them (in sorted order, read from the data, when that is
        not resolved), and every other dimension, with the time dimension,
        forms the index. One measure fills the cells; several give a column
        per measure per value. Attributes are not carried.

        A `pl.DataFrame`, not a `LazyFrame` (#516), as every other terminal's
        result is: the query has run by the time a pivot can start, so
        laziness after it would push nothing back into the fetch, and when
        the axis's enumeration is unresolved its columns can only be read
        from the rows. ``.lazy()`` on the result costs nothing.

        Args:
            on: The dimension to spread into columns. Needed when the DSD
                declares no measure dimension.

        Returns:
            The wide frame, rows in the order their series first appear in
            `frame()` — key order, unless the query sorts otherwise.

        Raises:
            ValueError: If there is no axis — `on` is not a dimension, or is
                not given and the DSD declares no measure dimension — naming
                the enumerated dimensions that could be one; or if the query
                has no `structure`.
        """
        dsd = self.structure
        if dsd is None:
            msg = (
                "pivot() reads the axis from the dataflow's DataStructure, and this query has none. "
                "Pass dsd= to DuckDBDataStore.obs, or use a registry's obs(flow)."
            )
            raise ValueError(msg)
        axis, listed = _pivot_axis(dsd, on)
        frame = self.frame()
        if listed is None:
            listed = frame.get_column(axis).cast(pl.String).unique().drop_nulls().sort().to_list()
        md = [dsd.measure_dimension] if dsd.measure_dimension is not None else []
        index = [d.id for d in (*dsd.dimensions, *md) if d.id != axis]
        if dsd.time_dimension is not None:
            index.append(dsd.time_dimension.id)
        measures = [m.id for m in dsd.measures]
        # `maintain_order`: rows in the order their series first appear. The
        # lazy pivot defaults to no order, which differs run to run.
        return (
            frame.lazy()
            .pivot(
                on=axis,
                on_columns=pl.Series(listed),
                index=index,
                values=measures[0] if len(measures) == 1 else measures,
                maintain_order=True,
            )
            .collect()
        )

    def _typed_batches(self, reader: pa.RecordBatchReader) -> Iterator[pa.RecordBatch]:
        for batch in reader:
            yield from self._typed_batch(batch).to_arrow().to_batches()

    def _typed_batch(self, batch: pa.RecordBatch) -> pl.DataFrame:
        """One batch of `stream()`, typed from the DSD as `frame()` is (`_typed`)."""
        return _typed(frame_of(batch), self.plan.root)


def _pivot_axis(dsd: DataStructure, on: str | None) -> tuple[str, list[str] | None]:
    """`ObsQuery.pivot`'s axis, and its values as the structure lists them — `None` when unresolved.

    Raises:
        ValueError: If there is no axis, naming the dimensions that could be one.
    """
    md = dsd.measure_dimension
    if md is not None and on in {None, md.id}:
        concepts = md.concepts
        return md.id, [c.id for c in concepts] if concepts is not None else None
    dimension = next((d for d in dsd.dimensions if d.id == on), None) if on is not None else None
    if dimension is not None:
        enumeration = dimension.resolved_enumeration
        return dimension.id, enumeration.code_ids() if enumeration is not None else None
    enumerated = ", ".join(d.id for d in dsd.dimensions if d.is_enumerated) or "(none)"
    reason = f"{on!r} is not a dimension of {dsd.id}" if on is not None else f"{dsd.id} declares no measure dimension"
    msg = f"pivot() needs an axis: {reason}. Pass on= one of its enumerated dimensions: {enumerated}."
    raise ValueError(msg)


#: DuckDB's numeric type names, as `DESCRIBE` spells them; ``DECIMAL(p,s)`` by prefix.
_DUCKDB_NUMERIC = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
        "FLOAT",
        "REAL",
        "DOUBLE",
    }
)


def _kind_of_duckdb(duckdb_type: str, because: str) -> ColumnKind | None:
    """A stored column's kind: VARCHAR is text, a numeric type a number, anything else unknown."""
    if duckdb_type == "VARCHAR":
        return ColumnKind("str", because)
    if duckdb_type in _DUCKDB_NUMERIC or duckdb_type.startswith("DECIMAL"):
        return ColumnKind("num", because)
    return None


def _kind_of_dtype(dtype: pl.DataType | None, because: str) -> ColumnKind | None:
    """A declared facet's kind, by the polars type a frame gives it: text, a number, or unknown."""
    if dtype is None:
        return None
    if isinstance(dtype, pl.String):
        return ColumnKind("str", because)
    if dtype.is_numeric():
        return ColumnKind("num", because)
    return None
