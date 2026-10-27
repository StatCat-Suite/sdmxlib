"""An observation query's natural order follows each dimension's codelist (GH-213).

The root-key tiebreak sorts a dimension enumerated by a codelist or a value
list by its code's position, then by the code id. `TIME_PERIOD`, and a
dimension the DSD leaves unenumerated, sort as stored. The codes here are
positioned `2`, `10`, `1`, so position order and text order disagree.

An explicit `order_by(obs.col(id))` sorts by the stored value, as a predicate
compares it and as the frame's `pl.Enum`, whose categories are the code ids in
sorted order, sorts it.
"""

import re
from datetime import UTC, datetime

import pytest

import sdmxlib as sl
from sdmxlib.query.observations import ObsRow

pytestmark = pytest.mark.unit

_POSITIONED = ("2", "10", "1")
_CHARACTERISTICS = sl.Codelist(
    id="CL_CHAR",
    maintainer="AG",
    name={"en": "Characteristics"},
    codes=[sl.Code(id=c, name={"en": c}) for c in _POSITIONED],
)
_SEXES = sl.ValueList(
    id="VL_SEX",
    maintainer="AG",
    name={"en": "Sexes"},
    items=[sl.ValueItem(id=v, name={"en": v}) for v in ("T", "M", "F")],
)
_CONCEPTS = sl.ConceptScheme(
    id="CS",
    maintainer="AG",
    name={"en": "c"},
    concepts=[sl.Concept(id=c, name={"en": c}) for c in ("CHAR", "SEX", "GEO", "TIME_PERIOD", "OBS_VALUE")],
)
_DSD = sl.DataStructure(
    id="DSD",
    maintainer="AG",
    name={"en": "d"},
    dimensions=[
        sl.Dimension(id="CHAR", concept=_CONCEPTS.ref_to("CHAR"), representation=sl.Ref.to(_CHARACTERISTICS)),
        sl.Dimension(id="SEX", concept=_CONCEPTS.ref_to("SEX"), representation=sl.Ref.to(_SEXES)),
        # Free text: sorts as stored.
        sl.Dimension(id="GEO", concept=_CONCEPTS.ref_to("GEO")),
    ],
    time_dimension=sl.TimeDimension(concept=_CONCEPTS.ref_to("TIME_PERIOD")),
    measures=[sl.Measure(id="OBS_VALUE", concept=_CONCEPTS.ref_to("OBS_VALUE"))],
)
_FLOW = sl.Dataflow(id="F", maintainer="AG", name={"en": "f"}, structure=sl.Ref.to(_DSD))
_STAMP = datetime(2026, 1, 1, tzinfo=UTC)
_KEY = ("CHAR", "SEX", "GEO", "TIME_PERIOD")
# Every characteristic and sex at two geographies: 18 rows, inserted in text order.
_ROWS = [
    (char, sex, geo, "2021", 1.0)
    for char in sorted(_POSITIONED)
    for sex in sorted(("T", "M", "F"))
    for geo in ("a", "b")
]


def _binding(table: str) -> sl.DataflowBinding:
    return sl.DataflowBinding(
        dataflow=sl.Ref.to(_FLOW),
        database_role="memory",
        table_location=f"data.{table}",
        dimension_column_map={c: c for c in _KEY},
        measure_column_map={"OBS_VALUE": "OBS_VALUE"},
        attribute_column_map={},
        structural_version="AG:DSD(1.0)",
        source_registry="local",
        materialized_at=_STAMP,
        bound_at=_STAMP,
    )


def _store(registry: sl.LocalRegistry, table: str, rows: list[tuple[str, str, str, str, float]]) -> None:
    registry.connection.execute(
        f"CREATE TABLE data.{table} (CHAR TEXT, SEX TEXT, GEO TEXT, TIME_PERIOD TEXT, OBS_VALUE DOUBLE)"
    )
    registry.connection.executemany(f"INSERT INTO data.{table} VALUES (?, ?, ?, ?, ?)", rows)
    registry.add(_binding(table))


@pytest.fixture
def registry():
    with sl.LocalRegistry(":memory:") as registry:
        registry.add_all([_CHARACTERISTICS, _SEXES, _CONCEPTS, _DSD, _FLOW])
        registry.connection.execute("CREATE SCHEMA data")
        _store(registry, "obs", _ROWS)
        yield registry


@pytest.fixture
def obs(registry: sl.LocalRegistry) -> sl.ObsQuery:
    return registry.obs(_FLOW)


def _keys(query: sl.ObsQuery) -> list[tuple[str, ...]]:
    frame = query.frame()
    return [tuple(str(v) for v in row) for row in frame.select(_KEY).iter_rows()]


def _expected() -> list[tuple[str, ...]]:
    """The 18 rows in codelist order: characteristic, then sex, by position; geography and time as text."""
    return [(char, sex, geo, "2021") for char in _POSITIONED for sex in ("T", "M", "F") for geo in ("a", "b")]


# ── the order ────────────────────────────────────────────────────────────────


def test_rows_come_in_codelist_and_value_list_order(obs: sl.ObsQuery) -> None:
    assert _keys(obs) == _expected()


def test_position_order_differs_from_text_order() -> None:
    """The discrimination check: a text sort of the same rows gives another order."""
    assert sorted(_expected()) != _expected()
    assert sorted(_POSITIONED) == ["1", "10", "2"]


def test_a_page_by_offset_holds_the_codelist_order_rows(obs: sl.ObsQuery) -> None:
    """The explorer's first page: the top of the codelist, not a text-order sample."""
    assert _keys(obs.limit(6)) == _expected()[:6]
    assert {char for char, *_ in _keys(obs.limit(6))} == {"2"}


@pytest.mark.parametrize("size", [1, 4, 5, 18])
def test_pages_by_offset_partition_the_result(obs: sl.ObsQuery, size: int) -> None:
    pages = [_keys(obs.offset(start).limit(size)) for start in range(0, len(_ROWS), size)]
    walked = [row for page in pages for row in page]
    assert walked == _expected(), "no row repeats, none is skipped, and the order holds across pages"


@pytest.mark.parametrize("size", [1, 4, 5])
def test_pages_by_cursor_partition_the_result(obs: sl.ObsQuery, size: int) -> None:
    """A cursor carries the key values alone; `after()` looks up each value's rank."""
    walked: list[tuple[str, ...]] = []
    query = obs.limit(size)
    while page := _keys(query):
        walked.extend(page)
        cursor = sl.Cursor[ObsRow](page[-1], (True,) * len(_KEY))
        query = obs.after(cursor).limit(size)
    assert walked == _expected()


def test_a_code_the_codelist_lacks_sorts_after_every_listed_one(registry: sl.LocalRegistry) -> None:
    """Its rank stands in for a position after every listed code; a cursor on it continues from there.

    Read through the compiled statement: `frame()` refuses a code its
    codelist lacks, as it types the column.
    """
    registry.connection.execute("INSERT INTO data.obs VALUES ('X', 'T', 'a', '2021', 1.0)")
    obs = registry.obs(_FLOW)

    def chars(query: sl.ObsQuery) -> list[str]:
        compiled = query.to_sql()
        return [row[0] for row in registry.connection.execute(compiled.sql, compiled.params).fetchall()]

    assert chars(obs) == [char for char, *_ in _expected()] + ["X"]
    assert chars(obs.after(sl.Cursor[ObsRow](_expected()[-1], (True,) * 4))) == ["X"]
    assert chars(obs.after(sl.Cursor[ObsRow](("X", "T", "a", "2021"), (True,) * 4))) == []


# ── an explicit order ────────────────────────────────────────────────────────


def test_an_explicit_order_on_a_dimension_sorts_its_codes_as_text(obs: sl.ObsQuery) -> None:
    """The stored value, as `==` and `<` compare it; the natural order then breaks ties."""
    chars = [char for char, *_ in _keys(obs.order_by(obs.col("CHAR")))]
    assert chars == sorted(chars)
    assert chars[0] == "1"


def test_the_frames_enum_sorts_codes_as_the_explicit_order_does(obs: sl.ObsQuery) -> None:
    """Polars sorts an `Enum` by category, and the frame's categories are the ids in sorted order."""
    frame = obs.frame()
    assert frame.sort("CHAR")["CHAR"].cast(str).to_list() == [c for c, *_ in _keys(obs.order_by(obs.col("CHAR")))]


# ── what the order costs ─────────────────────────────────────────────────────


def _joins(sql: str) -> list[str]:
    return re.findall(r"LEFT JOIN sdmx\.(\w+) ", sql)


def test_the_order_joins_each_enumerated_dimension_once(obs: sl.ObsQuery) -> None:
    """One join per codelist or value list; `GEO` and `TIME_PERIOD` join nothing."""
    assert _joins(obs.to_sql().sql) == ["code", "value_item"]


def test_the_order_looks_nothing_up_per_row(obs: sl.ObsQuery) -> None:
    """The positions arrive by join: the `ORDER BY` holds no subquery."""
    order_by = obs.to_sql().sql.split("ORDER BY", 1)[1]
    assert "SELECT" not in order_by
    assert order_by.count("coalesce(") == 2


def test_a_cursor_adds_one_lookup_per_ranked_value(obs: sl.ObsQuery) -> None:
    """Each ranked key value in a cursor is ranked by one uncorrelated subquery per comparison it appears in."""
    sql = obs.after(sl.Cursor[ObsRow](("2", "T", "a", "2021"), (True,) * 4)).to_sql().sql
    assert _joins(sql) == ["code", "value_item"]
    # Two ranked keys, each compared twice (strictly after, and equal).
    assert sql.count("(SELECT position FROM sdmx.") == 4


def test_an_unsorted_read_joins_no_scheme(obs: sl.ObsQuery) -> None:
    """`count()` compiles unordered, as `order_by(None)` does, so neither joins a codelist."""
    assert _joins(obs.to_sql(columns="count(*)", ordered=False).sql) == []
    assert _joins(obs.order_by(None).to_sql().sql) == []


def test_a_root_without_a_dsd_sorts_as_stored(registry: sl.LocalRegistry) -> None:
    """The discrimination check for the join count: nothing says what enumerates a column."""
    obs = registry._data_plane().obs(_binding("obs"))
    assert _joins(obs.to_sql().sql) == []
    rows = registry.connection.execute(obs.to_sql().sql, obs.to_sql().params).fetchall()
    assert [row[:4] for row in rows] == sorted(row[:4] for row in _ROWS)
