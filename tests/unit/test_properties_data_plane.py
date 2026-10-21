"""Differential properties for the data plane (#258).

#248/#249 gave the two *structural* query DSLs oracle-backed differential
properties. The builder that turns SDMX query parameters into
``SELECT … WHERE col IN (?)`` over an observation table had none —
despite being *more* differential-testable than they are, since "which
observations match this filter" is a one-line Polars expression over the same
frame the fixture built.

`rest.py` was at **100% line coverage** while the empty-``IN ()`` bug (#256)
sat in it untouched. That is the `code_query` lesson again, on a module nobody
had pointed a generative test at: line coverage records that a line ran, not
that anyone checked what it did.

## The fixture is a cartesian product, on purpose

Two reasons, both load-bearing. The expected result of any filter combination
is trivially computable, and **no accidental correlation between dimensions can
mask a bug** — on a realistic sparse frame, a filter that silently ignored one
dimension could return the right rows by luck.

It carries a **nullable attribute**, because three-valued logic is where SQL
builders most reliably diverge from the naive reading, and a fixture without
NULLs can never show it. Measured: an attribute filter does *not* match rows
whose value is NULL, since ``NULL IN (…)`` is NULL rather than false. The
library had not written that down anywhere; `tests/support/data_oracle` now
does.

## What is not tested here

No live DuckLake catalog — registered frames cover the semantics and catalog
integration is a separate, smaller suite. And no mocked connection: in-memory
DuckDB is sub-millisecond, and a mock would only test the builder against a
fictional dialect.
"""

import itertools
from datetime import UTC, datetime

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sdmxlib.local import LocalRegistry
from sdmxlib.local.data_store import DuckDBDataStore
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.ref import Ref
from sdmxlib.query.accessors import q
from sdmxlib.query.observations import Observations
from sdmxlib.rest import ComponentFilter
from sdmxlib.sql import SqlRelation
from tests.support import data_oracle
from tests.support.narrow import present

pytestmark = pytest.mark.unit

_EXAMPLES = max(5, present(settings.default).max_examples // 5)

_GEO = ["CA", "US", "MX"]
_FREQ = ["A", "Q"]
_TIME = ["2020", "2021"]
#: Includes ``None`` so the fixture has rows a `GEO_LEVEL` filter cannot match.
_LEVELS = ["FED", "PROV", None]

_DIM_IDS = ["GEO", "FREQ", "TIME_PERIOD"]
_DIM_MAP = {d: d for d in _DIM_IDS}
_ATTR_MAP = {"GEO_LEVEL": "GEO_LEVEL"}
_VALUES = {"GEO": _GEO, "FREQ": _FREQ, "TIME_PERIOD": _TIME}


def _frame() -> pl.DataFrame:
    """The full cartesian product, with a rotating nullable attribute."""
    return pl.DataFrame(
        [
            {
                "GEO": g,
                "FREQ": f,
                "TIME_PERIOD": t,
                "GEO_LEVEL": _LEVELS[i % len(_LEVELS)],
                "OBS_VALUE": float(i),
            }
            for i, (g, f, t) in enumerate(itertools.product(_GEO, _FREQ, _TIME))
        ]
    )


def _binding() -> DataflowBinding:
    return DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(Dataflow.urn_for(agency="T", id="DF", version="1.0")),
        database_role="memory",
        table_location="data.obs",
        dimension_column_map=_DIM_MAP,
        measure_column_map={"OBS_VALUE": "OBS_VALUE"},
        attribute_column_map=_ATTR_MAP,
        structural_version="T:DSD(1.0)",
        source_registry="local",
        materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
        bound_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture(scope="module")
def plane():
    """LocalRegistry + observation table + store + a codelist for relation filters.

    Module-scoped deliberately. Hypothesis rejects a *function*-scoped fixture
    under `@given` because it is not reset between generated inputs — which is
    a real hazard when a test mutates it. Nothing here does: the table is built
    once and every property only reads. Widening the scope is the honest fix;
    suppressing the health check would assert the same thing without making it
    true.
    """
    frame = _frame()
    with LocalRegistry(":memory:") as reg:
        conn = reg.connection
        conn.register("obs_frame", frame)
        conn.execute("CREATE SCHEMA IF NOT EXISTS data")
        conn.execute("CREATE OR REPLACE TABLE data.obs AS SELECT * FROM obs_frame")
        # A codelist whose ids are the GEO values, so a CodeQuery can stand in
        # as a relation-valued filter over a real dimension.
        reg.add(
            Codelist(
                id="CL_GEO",
                maintainer="AG",
                version="1.0",
                name={"en": "geo"},
                codes=[Code(id=g, name={"en": g}) for g in _GEO],
            )
        )
        yield reg, frame, DuckDBDataStore.from_registry(reg), _binding()


def _rows(df: pl.DataFrame) -> list[float]:
    return sorted(df["OBS_VALUE"].to_list())


# ── Strategies ───────────────────────────────────────────────────────────────


@st.composite
def component_filters(draw: st.DrawFn, *, allow_relations: bool = False) -> ComponentFilter:
    """A filter over the fixture's dimensions, spanning every value shape.

    Covers wildcard (`None`), the empty list, single values, full value sets
    and — when asked — relation-valued components, which are the ones that
    splice *their* binds into the middle of the outer parameter list.
    """
    dims: list[list[str] | SqlRelation | None] = []
    for dim in _DIM_IDS:
        shape = draw(
            st.sampled_from(
                ["wildcard", "empty", "some", "all", "relation"]
                if allow_relations and dim == "GEO"
                else ["wildcard", "empty", "some", "all"]
            ),
            label=f"{dim}-shape",
        )
        if shape == "wildcard":
            dims.append(None)
        elif shape == "empty":
            dims.append([])
        elif shape == "all":
            dims.append(list(_VALUES[dim]))
        elif shape == "relation":
            dims.append(_RELATION_SENTINEL)
        else:
            dims.append(draw(st.lists(st.sampled_from(_VALUES[dim]), min_size=1, max_size=3, unique=True)))

    attrs: dict[str, list[str] | SqlRelation] = {}
    if draw(st.booleans(), label="constrain-attr"):
        attrs["GEO_LEVEL"] = draw(
            st.lists(st.sampled_from(["FED", "PROV", "MUNI"]), min_size=0, max_size=3, unique=True),
            label="attr-values",
        )
    return ComponentFilter(list(_DIM_IDS), dims, attrs)


#: Placeholder swapped for a live `CodeQuery` once the fixture exists — a
#: strategy cannot hold a connection.
_RELATION_SENTINEL = ["__RELATION__"]


def _bind_relations(cf: ComponentFilter, reg: LocalRegistry) -> ComponentFilter:
    """Replace the sentinel with a real relation over the GEO codelist."""
    lazy = reg.get(Codelist.urn_for(agency="AG", id="CL_GEO", version="1.0"))
    relation = present(lazy).codes.filter(q.code.id.is_in(["CA", "US"])).select(q.code.id)
    dims = [relation if d is _RELATION_SENTINEL else d for d in cf.dimensions]
    return ComponentFilter(cf.dimension_ids, dims, cf.attributes)


# ── The differential property ────────────────────────────────────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cf=component_filters())
def test_sql_selects_the_rows_polars_selects(cf: ComponentFilter, plane) -> None:
    """The core property: DuckDB and a direct Polars evaluation agree."""
    reg, frame, store, binding = plane
    got = store.obs(binding).filter_components(cf).frame()
    want = data_oracle.matching_rows(
        frame, cf, dimension_column_map=_DIM_MAP, attribute_column_map=_ATTR_MAP, conn=reg.connection
    )
    assert _rows(got) == _rows(want)


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cf=component_filters(allow_relations=True))
def test_relation_valued_filters_agree_too(cf: ComponentFilter, plane) -> None:
    """A `SqlRelation` component splices its binds into the middle of the list.

    That interleaving is the point: the structural DSLs never mix eager and
    lazy values in one parameter list, and this layer does.
    """
    reg, frame, store, binding = plane
    bound = _bind_relations(cf, reg)
    got = store.obs(binding).filter_components(bound).frame()
    want = data_oracle.matching_rows(
        frame, bound, dimension_column_map=_DIM_MAP, attribute_column_map=_ATTR_MAP, conn=reg.connection
    )
    assert _rows(got) == _rows(want)


# ── Invariants of the emitted query ──────────────────────────────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cf=component_filters(allow_relations=True))
def test_placeholders_and_params_stay_aligned(cf: ComponentFilter, plane) -> None:
    """``?`` count equals param count — the classic bug for this layer.

    `_build_query` concatenates params from three sources (`where`,
    `component_filter`, `limit`) and a relation splices *its* binds into the
    middle. A drift here produces either a bind error or, worse, a silently
    shifted parameter.
    """
    reg, _, store, binding = plane
    bound = _bind_relations(cf, reg)
    compiled = store.obs(binding).filter_components(bound).limit(7).to_sql()
    sql, params = compiled.sql, list(compiled.params)
    assert sql.count("?") == len(params), f"{sql.count('?')} placeholders vs {len(params)} params: {sql!r}"


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cf=component_filters(allow_relations=True))
def test_every_generated_filter_executes(cf: ComponentFilter, plane) -> None:
    """No filter shape may raise. The general form of #256's empty-``IN ()``."""
    reg, _, store, binding = plane
    store.obs(binding).filter_components(_bind_relations(cf, reg)).frame()


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cf=component_filters())
def test_emitted_sql_satisfies_the_structural_invariants(cf: ComponentFilter, plane) -> None:
    """#257's invariants, applied to the data plane it named but did not cover."""
    from tests.support import sql_ast

    _reg, _, store, binding = plane
    sql = store.obs(binding).filter_components(cf).to_sql().sql
    sql_ast.assert_single_statement(sql)
    sql_ast.assert_no_literals_in_predicates(sql)


# ── Algebraic laws ───────────────────────────────────────────────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(data=st.data())
def test_widening_a_component_never_shrinks_the_result(data: st.DataObject, plane) -> None:
    """OR within a component is monotone."""
    _, _, store, binding = plane
    narrow = data.draw(st.lists(st.sampled_from(_GEO), min_size=1, max_size=2, unique=True), label="narrow")
    extra = data.draw(st.lists(st.sampled_from(_GEO), min_size=0, max_size=3, unique=True), label="extra")
    wide = sorted(set(narrow) | set(extra))

    def run(values: list[str]) -> set[float]:
        cf = ComponentFilter(list(_DIM_IDS), [values, None, None])
        return set(store.obs(binding).filter_components(cf).frame()["OBS_VALUE"].to_list())

    assert run(narrow) <= run(wide)


@settings(max_examples=_EXAMPLES, deadline=None)
@given(data=st.data())
def test_adding_a_component_never_grows_the_result(data: st.DataObject, plane) -> None:
    """AND across components is antitone."""
    _, _, store, binding = plane
    geo = data.draw(st.lists(st.sampled_from(_GEO), min_size=1, max_size=3, unique=True), label="geo")
    freq = data.draw(st.lists(st.sampled_from(_FREQ), min_size=1, max_size=2, unique=True), label="freq")

    one = ComponentFilter(list(_DIM_IDS), [geo, None, None])
    two = ComponentFilter(list(_DIM_IDS), [geo, freq, None])
    a = set(store.obs(binding).filter_components(one).frame()["OBS_VALUE"].to_list())
    b = set(store.obs(binding).filter_components(two).frame()["OBS_VALUE"].to_list())
    assert b <= a


def test_wildcard_returns_everything_and_is_wildcard_agrees(plane) -> None:
    """The identity, and that `is_wildcard` reports it.

    `is_wildcard` is a *syntactic* claim about the filter; returning every row
    is a semantic one. They should coincide here and deliberately do not for an
    empty list, which selects nothing while being a constraint.
    """
    _reg, frame, store, binding = plane
    cf = ComponentFilter(list(_DIM_IDS), [None, None, None])
    assert cf.is_wildcard
    assert store.obs(binding).filter_components(cf).frame().height == frame.height

    empty = ComponentFilter(list(_DIM_IDS), [[], None, None])
    assert not empty.is_wildcard, "an empty list is a constraint, not an absence of one (#256)"
    assert store.obs(binding).filter_components(empty).frame().height == 0


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cf=component_filters(), n=st.integers(min_value=0, max_value=15))
def test_limit_returns_a_prefix_of_the_ordered_result(cf: ComponentFilter, n: int, plane) -> None:
    """A prefix, not merely a length.

    Stated this way so a ``LIMIT`` applied *before* the ``ORDER BY`` fails: a
    length assertion passes for any n rows, and picking the wrong n is exactly
    the bug.
    """
    _reg, _, store, binding = plane
    obs = Observations(binding)
    ordered = store.obs(binding).filter_components(cf).order_by(obs["OBS_VALUE"])
    full = ordered.frame()
    capped = ordered.limit(n).frame()
    assert capped["OBS_VALUE"].to_list() == full["OBS_VALUE"].to_list()[:n]


# ── The NULL finding, pinned ─────────────────────────────────────────────────


def test_an_attribute_filter_does_not_match_null_rows(plane) -> None:
    """Three-valued logic, measured rather than assumed (#258).

    ``NULL IN ('FED','PROV')`` is NULL, not false, so a row with no
    ``GEO_LEVEL`` is not selected by *any* attribute filter — including one
    naming every non-null value there is. A reader would reasonably expect
    "GEO_LEVEL is one of everything" to mean "all rows".

    The library had not stated this anywhere. Pinned here so a future change to
    `to_where_clauses` — say, emitting ``col IS NULL OR col IN (...)`` — is a
    deliberate decision rather than a silent one.
    """
    _reg, frame, store, binding = plane
    every_value = ComponentFilter(list(_DIM_IDS), [None, None, None], {"GEO_LEVEL": ["FED", "PROV"]})
    got = store.obs(binding).filter_components(every_value).frame()
    nulls = frame.filter(pl.col("GEO_LEVEL").is_null()).height

    assert nulls > 0, "fixture no longer exercises the NULL case"
    assert got.height == frame.height - nulls


# ── Law 2: a one-element OR-set is an equality (#260) ────────────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(data=st.data())
def test_a_single_value_filter_equals_an_equality(data: st.DataObject, plane) -> None:
    """``In(d, [c])`` and ``Eq(d, c)`` must select the same rows.

    Builders routinely special-case the one-element path for an index-friendly
    ``=`` and let it drift from the general ``IN``. This layer does not today —
    `_emit_in_clause` emits ``IN (?)`` for a singleton — so the law also pins
    that *if* someone adds the optimisation, it has to stay equivalent.

    The oracle is the comparison: a Polars `is_in` with one element against the
    same frame.
    """
    reg, frame, store, binding = plane
    value = data.draw(st.sampled_from(_GEO), label="value")

    cf = ComponentFilter(list(_DIM_IDS), [[value], None, None])
    got = store.obs(binding).filter_components(cf).frame()
    want = frame.filter(pl.col("GEO") == value)
    assert _rows(got) == sorted(want["OBS_VALUE"].to_list())

    oracle_rows = data_oracle.matching_rows(
        frame, cf, dimension_column_map=_DIM_MAP, attribute_column_map=_ATTR_MAP, conn=reg.connection
    )
    assert _rows(got) == _rows(oracle_rows)


@settings(max_examples=_EXAMPLES, deadline=None)
@given(data=st.data())
def test_a_single_value_attribute_filter_equals_an_equality(data: st.DataObject, plane) -> None:
    """The same law on the attribute side, where NULLs make it less obvious.

    ``GEO_LEVEL IN ('FED')`` and ``GEO_LEVEL = 'FED'`` agree — and both exclude
    the NULL rows, for the same three-valued-logic reason. A special-cased
    equality that used ``IS NOT DISTINCT FROM`` would break this.
    """
    _reg, frame, store, binding = plane
    value = data.draw(st.sampled_from(["FED", "PROV"]), label="value")

    cf = ComponentFilter(list(_DIM_IDS), [None, None, None], {"GEO_LEVEL": [value]})
    got = store.obs(binding).filter_components(cf).frame()
    assert _rows(got) == sorted(frame.filter(pl.col("GEO_LEVEL") == value)["OBS_VALUE"].to_list())
