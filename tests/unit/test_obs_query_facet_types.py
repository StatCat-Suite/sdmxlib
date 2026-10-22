"""A temporal or boolean `Facet` types an observation column from its stored text.

Every observation column is stored as text, and a cast is not a parse: polars
2.0 casts no text to a date, a datetime or a time (1.x did dates only), and no
version casts text to a boolean. Each of these components failed every typed
result with "does not fit its component's type" — on values that fit — and no
test had a component of any of these types.
"""

import datetime as dt

import pytest

import sdmxlib as sl

pytestmark = pytest.mark.unit

#: Component id -> facet type, one measure each.
_TYPES = {"D": "Date", "GD": "GregorianDay", "DT": "DateTime", "T": "Time", "B": "Boolean"}

_AREAS = sl.Codelist(
    id="CL_AREA", maintainer="AG", name={"en": "Areas"}, codes=[sl.Code(id="CA", name={"en": "Canada"})]
)
_CONCEPTS = sl.ConceptScheme(
    id="CS",
    maintainer="AG",
    name={"en": "c"},
    concepts=[sl.Concept(id=c, name={"en": c}) for c in ("REF_AREA", *_TYPES)],
)
_DSD = sl.DataStructure(
    id="DSD",
    maintainer="AG",
    name={"en": "d"},
    dimensions=[sl.Dimension(id="REF_AREA", concept=_CONCEPTS.ref_to("REF_AREA"), representation=sl.Ref.to(_AREAS))],
    measures=[
        sl.Measure(id=c, concept=_CONCEPTS.ref_to(c), representation=sl.Facet(type=t)) for c, t in _TYPES.items()
    ],
)
_FLOW = sl.Dataflow(id="F", maintainer="AG", name={"en": "f"}, structure=sl.Ref.to(_DSD))


def _frame(rows: str) -> list[dict[str, object]]:
    """`rows` stored as text, read back as a typed frame's rows."""
    with sl.LocalRegistry(":memory:") as registry:
        registry.add_all([_AREAS, _CONCEPTS, _DSD, _FLOW])
        registry.connection.execute("CREATE SCHEMA data")
        registry.connection.execute(
            f"CREATE TABLE data.obs AS SELECT * FROM (VALUES {rows}) t(REF_AREA, {', '.join(_TYPES)})"
        )
        registry.add(
            sl.DataflowBinding.from_dsd(
                _DSD, dataflow=sl.Ref.to(_FLOW), schema="data", table="obs", database_role="memory"
            )
        )
        return registry.obs(_FLOW).frame().to_dicts()


def test_each_type_is_parsed_from_its_text() -> None:
    (row,) = _frame("('CA', '2021-01-31', '2021-02-01', '2021-01-31T12:30:00', '12:30:00', 'true')")
    assert row == {
        "REF_AREA": "CA",
        "D": dt.date(2021, 1, 31),
        "GD": dt.date(2021, 2, 1),
        "DT": dt.datetime(2021, 1, 31, 12, 30),
        "T": dt.time(12, 30),
        "B": True,
    }


def test_a_datetime_with_an_offset_lands_in_utc() -> None:
    (row,) = _frame("('CA', NULL, NULL, '2021-01-31T08:30:00-04:00', NULL, NULL)")
    assert row["DT"] == dt.datetime(2021, 1, 31, 12, 30)


@pytest.mark.parametrize(("text", "expected"), [("true", True), ("false", False), ("1", True), ("0", False)])
def test_a_boolean_reads_every_xs_boolean_form(text: str, expected: bool) -> None:
    (row,) = _frame(f"('CA', NULL, NULL, NULL, NULL, '{text}')")
    assert row["B"] is expected


def test_nulls_stay_null() -> None:
    (row,) = _frame("('CA', NULL, NULL, NULL, NULL, NULL)")
    assert all(row[c] is None for c in _TYPES)


@pytest.mark.parametrize(
    ("column", "text"), [("D", "2021-02-30"), ("DT", "yesterday"), ("T", "25:00:00"), ("B", "yes")]
)
def test_a_value_that_does_not_fit_still_fails(column: str, text: str) -> None:
    values = dict.fromkeys(_TYPES, "NULL") | {column: f"'{text}'"}
    with pytest.raises(ValueError, match="does not fit its component's type"):
        _frame(f"('CA', {', '.join(values.values())})")
