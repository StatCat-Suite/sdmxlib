"""SDMX-REST's string component filters, through the observation DSL (#413).

SDMX-REST 2.2.2 lets `c[GEO]=co:Census` constrain a component by substring,
alongside `nc:` (does not contain), `sw:` (starts with) and `ew:` (ends with).
Every service built on sdmxlib must answer them on `/data` and `/availability`,
and #413 was filed because the DSL appeared to have no form for them.

It had: `obs.col(...)` returns `Untyped`, whose `.str` namespace carries
`contains` / `starts_with` / `ends_with`, compiled by `_label_match` to
`LIKE ? ESCAPE '\\'` with the wildcards in the needle escaped. What was missing
was the declaration — `col()` said `Col`, which has no `.str` — so nothing
showed the operators existed. `tests/typing/probe_obs_columns.py` holds the
static half; this file is the runtime half.

Every expectation is computed in Python over the stored values, not written by
hand, and the needles are the ones a hand-rolled escaper gets wrong: `%`, `_`,
a backslash, and the empty needle.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.local.data_store import DuckDBDataStore
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.ref import Ref

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sdmxlib.query._expr import Pred
    from sdmxlib.query.observations import ObsQuery, ObsRow

pytestmark = pytest.mark.unit

#: Fact-table values, chosen so each escaping mistake selects a wrong row:
#: `Census_Div` vs `CensusXDiv` for `_`, `50%` vs `500` for `%`, and a literal
#: backslash. `None` pins what a negated match does with a missing value.
GEO = ["Census_Div", "CensusXDiv", "Province 50%", "Province 500", r"A\B", "census tract", None]

#: Sidecar values, one per series, so a match reaches across the join.
GEO_NAME = {g: (f"Name of {g}" if g else None) for g in GEO}

NEEDLES = ["Census", "Census_", "50%", "\\", "Div", "Province", "", "zzz"]

PY_OPS: dict[str, Callable[[str, str], bool]] = {
    "co": lambda value, needle: needle in value,
    "sw": lambda value, needle: value.startswith(needle),
    "ew": lambda value, needle: value.endswith(needle),
}


@pytest.fixture(scope="module")
def obs() -> Iterator[ObsQuery]:
    with LocalRegistry(":memory:") as registry:
        cur = registry.connection.cursor()
        cur.execute("CREATE SCHEMA data")
        cur.execute("CREATE TABLE data.obs (GEO TEXT, TIME_PERIOD TEXT, OBS_VALUE DOUBLE)")
        cur.executemany("INSERT INTO data.obs VALUES (?, '2020', 1.0)", [[g] for g in GEO])
        cur.execute("CREATE TABLE data.series (GEO TEXT, GEO_NAME TEXT)")
        cur.executemany("INSERT INTO data.series VALUES (?, ?)", [[g, n] for g, n in GEO_NAME.items() if g])
        binding = DataflowBinding(
            dataflow=Ref[Dataflow].unresolved(Dataflow.urn_for(agency="AG", id="DF", version="1.0")),
            database_role="memory",
            table_location="data.obs",
            series_attributes_location="data.series",
            dimension_column_map={"GEO": "GEO", "TIME_PERIOD": "TIME_PERIOD"},
            measure_column_map={"OBS_VALUE": "OBS_VALUE"},
            attribute_column_map={"GEO_NAME": "GEO_NAME"},
            structural_version="AG:DSD(1.0)",
            source_registry="local",
            materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
            bound_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        yield DuckDBDataStore.from_registry(registry).obs(binding)


def _geos(query: ObsQuery) -> list[str | None]:
    return sorted(query.select(g=query.col("GEO")).collect()["g"].to_list(), key=str)


def _predicate(query: ObsQuery, column: str, op: str, needle: str) -> Pred[ObsRow]:
    ns = query.col(column).str
    return {"co": ns.contains, "sw": ns.starts_with, "ew": ns.ends_with}[op](needle)


@pytest.mark.parametrize("needle", NEEDLES)
@pytest.mark.parametrize("op", list(PY_OPS))
def test_each_operator_matches_python_on_the_fact_table(obs: ObsQuery, op: str, needle: str) -> None:
    want = sorted((g for g in GEO if g is not None and PY_OPS[op](g, needle)), key=str)
    assert _geos(obs.filter(_predicate(obs, "GEO", op, needle))) == want


@pytest.mark.parametrize("needle", ["Census_", "50%", "\\"])
def test_nc_is_the_negation_and_leaves_missing_values_out(obs: ObsQuery, needle: str) -> None:
    """`nc:` is `~contains`. A NULL value neither contains nor fails to contain.

    SQL's three-valued reading, and the same one `!=` already has: a row with no
    value is excluded from both `co:` and `nc:`. A consumer wanting "anything
    not containing X, including missing" adds `| col.is_null()` itself.
    """
    want = sorted((g for g in GEO if g is not None and needle not in g), key=str)
    assert _geos(obs.filter(~obs.col("GEO").str.contains(needle))) == want


@pytest.mark.parametrize("needle", ["of Census_", "50%", "Name of A\\"])
def test_a_sidecar_column_matches_across_the_join(obs: ObsQuery, needle: str) -> None:
    want = sorted((g for g, n in GEO_NAME.items() if n is not None and needle in n), key=str)
    assert _geos(obs.filter(obs.col("GEO_NAME").str.contains(needle))) == want


def test_matching_is_case_sensitive_unless_asked(obs: ObsQuery) -> None:
    """`co:` is exact, like `LIKE`; `caseless` is there for a service that wants `ILIKE`."""
    assert _geos(obs.filter(obs.col("GEO").str.contains("census"))) == ["census tract"]
    assert _geos(obs.filter(obs.col("GEO").str.caseless.contains("census"))) == [
        "CensusXDiv",
        "Census_Div",
        "census tract",
    ]


def test_the_escaping_is_done_once_by_the_dsl(obs: ObsQuery) -> None:
    """A consumer passes the raw needle; the bound pattern is escaped, with `ESCAPE` stated."""
    sql, params = obs.filter(obs.col("GEO").str.contains("50%_\\")).to_sql()
    assert "LIKE ? ESCAPE '\\'" in sql
    assert params == ["%50\\%\\_\\\\%"]
