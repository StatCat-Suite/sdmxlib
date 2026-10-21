"""Tests for the SDMX REST component-filter parser."""

from __future__ import annotations

import pytest

from sdmxlib.model.datastructure import DataStructure, Dimension, TimeDimension
from sdmxlib.rest import ComponentFilter, parse_components, widen_key
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


KEY_DIMS = ["GEO", "FREQ", "TIME_PERIOD"]


# ── parse_components: dotted-key only ───────────────────────────────────


def test_parse_full_key():
    cf = parse_components("CA.M.2021", {}, KEY_DIMS)
    assert cf.dimension_ids == KEY_DIMS
    assert cf.dimensions == [["CA"], ["M"], ["2021"]]
    assert cf.attributes == {}


def test_parse_or_within_segment():
    cf = parse_components("CA+US+MX.M.2021", {}, KEY_DIMS)
    assert cf.dimensions == [["CA", "US", "MX"], ["M"], ["2021"]]


def test_parse_empty_segment_is_wildcard():
    cf = parse_components("..2021", {}, KEY_DIMS)
    assert cf.dimensions == [None, None, ["2021"]]


def test_parse_trailing_empty_segment():
    cf = parse_components("CA.M.", {}, KEY_DIMS)
    assert cf.dimensions == [["CA"], ["M"], None]


def test_parse_all_wildcard():
    cf = parse_components("all", {}, KEY_DIMS)
    assert cf.dimensions == [None, None, None]
    assert cf.is_wildcard


def test_parse_empty_string_wildcard():
    cf = parse_components("", {}, KEY_DIMS)
    assert cf.is_wildcard
    assert cf.dimension_ids == KEY_DIMS


def test_parse_too_few_segments_raises():
    with pytest.raises(ValueError, match="2 segments but DSD has 3"):
        parse_components("CA.M", {}, KEY_DIMS)


def test_parse_too_many_segments_raises():
    with pytest.raises(ValueError, match="4 segments but DSD has 3"):
        parse_components("CA.M.2021.extra", {}, KEY_DIMS)


def test_parse_error_message_lists_dim_ids():
    with pytest.raises(ValueError, match="GEO, FREQ, TIME_PERIOD"):
        parse_components("CA.M", {}, KEY_DIMS)


def test_is_wildcard_false_when_constrained():
    cf = parse_components("CA..", {}, KEY_DIMS)
    assert not cf.is_wildcard


def test_parse_single_dimension():
    cf = parse_components("M", {}, ["FREQ"])
    assert cf.dimensions == [["M"]]


def test_component_filter_parallel_array_mismatch_raises():
    with pytest.raises(ValueError, match="parallel arrays"):
        ComponentFilter(dimension_ids=["A", "B"], dimensions=[["x"]])


# ── parse_components: c[...]=... attribute filters ──────────────────────


def test_parse_attribute_filter_lands_on_attributes():
    cf = parse_components("all", {"GEO_LEVEL": ["FED"]}, KEY_DIMS)
    assert cf.dimensions == [None, None, None]
    assert cf.attributes == {"GEO_LEVEL": ["FED"]}
    assert not cf.is_wildcard  # attribute constraint counts


def test_parse_attribute_filter_with_dotted_key():
    cf = parse_components(
        "CA..2021",
        {"GEO_LEVEL": ["FED", "PROV"], "OBS_STATUS": ["A"]},
        KEY_DIMS,
    )
    assert cf.dimensions == [["CA"], None, ["2021"]]
    assert cf.attributes == {"GEO_LEVEL": ["FED", "PROV"], "OBS_STATUS": ["A"]}


def test_parse_component_filter_for_key_dim_overlays_dimensions():
    """c[GEO]=CA  should land on dimensions[0], not attributes."""
    cf = parse_components("all", {"GEO": ["CA", "US"]}, KEY_DIMS)
    assert cf.dimensions[0] == ["CA", "US"]
    assert cf.attributes == {}


def test_parse_component_filter_for_key_dim_extends_existing():
    """Both dotted-key and c[GEO]= specifying the same dim merge into one OR list."""
    cf = parse_components("CA.M.", {"GEO": ["US", "MX"]}, KEY_DIMS)
    assert cf.dimensions[0] == ["CA", "US", "MX"]


def test_parse_keeps_empty_attribute_value_lists():
    """An empty OR-set survives the parse layer (#256).

    It used to be dropped, which meant ``{"GEO_LEVEL": []}`` widened the query
    to every row — while constructing the same filter directly produced
    ``IN ()`` and crashed. Same input, two answers, one of them a parser error.
    """
    cf = parse_components("all", {"GEO_LEVEL": []}, KEY_DIMS)
    assert cf.attributes == {"GEO_LEVEL": []}
    assert not cf.is_wildcard, "an empty set is a constraint, not the absence of one"


# ── to_where_clauses ────────────────────────────────────────────────────


def test_to_where_full_filter():
    cf = parse_components("CA+US.M.2021", {}, KEY_DIMS)
    column_map = {"GEO": "GEO", "FREQ": "FREQ", "TIME_PERIOD": "TIME_PERIOD"}
    fragments, params = cf.to_where_clauses(column_map)
    assert fragments == [
        "GEO IN (?, ?)",
        "FREQ IN (?)",
        "TIME_PERIOD IN (?)",
    ]
    assert params == ["CA", "US", "M", "2021"]


def test_to_where_skips_wildcards():
    cf = parse_components("..2021", {}, KEY_DIMS)
    column_map = {"GEO": "GEO", "FREQ": "FREQ", "TIME_PERIOD": "TIME_PERIOD"}
    fragments, params = cf.to_where_clauses(column_map)
    assert fragments == ["TIME_PERIOD IN (?)"]
    assert params == ["2021"]


def test_to_where_uses_renamed_columns():
    cf = parse_components("CA..", {}, KEY_DIMS)
    column_map = {"GEO": "ref_area_code", "FREQ": "freq_col", "TIME_PERIOD": "t"}
    fragments, params = cf.to_where_clauses(column_map)
    assert fragments == ["ref_area_code IN (?)"]
    assert params == ["CA"]


def test_to_where_wildcard_filter_is_empty():
    cf = parse_components("all", {}, KEY_DIMS)
    column_map = {"GEO": "GEO", "FREQ": "FREQ", "TIME_PERIOD": "TIME_PERIOD"}
    fragments, params = cf.to_where_clauses(column_map)
    assert fragments == []
    assert params == []


def test_to_where_missing_dim_in_map_raises():
    cf = parse_components("CA..", {}, KEY_DIMS)
    with pytest.raises(KeyError, match="GEO"):
        cf.to_where_clauses({"FREQ": "FREQ", "TIME_PERIOD": "TIME_PERIOD"})


def test_to_where_attribute_emits_in_clause():
    cf = parse_components("all", {"GEO_LEVEL": ["FED", "PROV"]}, KEY_DIMS)
    dims = {"GEO": "GEO", "FREQ": "FREQ", "TIME_PERIOD": "TIME_PERIOD"}
    attrs = {"GEO_LEVEL": "GEO_LEVEL", "OBS_STATUS": "OBS_STATUS"}
    fragments, params = cf.to_where_clauses(dims, attrs)
    assert fragments == ["GEO_LEVEL IN (?, ?)"]
    assert params == ["FED", "PROV"]


def test_to_where_attribute_uses_renamed_column():
    cf = parse_components("all", {"GEO_LEVEL": ["FED"]}, KEY_DIMS)
    dims = {"GEO": "GEO", "FREQ": "FREQ", "TIME_PERIOD": "TIME_PERIOD"}
    attrs = {"GEO_LEVEL": "geo_level_col"}
    fragments, params = cf.to_where_clauses(dims, attrs)
    assert fragments == ["geo_level_col IN (?)"]
    assert params == ["FED"]


def test_to_where_dimension_and_attribute_combined():
    cf = parse_components("CA..", {"GEO_LEVEL": ["FED"]}, KEY_DIMS)
    dims = {"GEO": "GEO", "FREQ": "FREQ", "TIME_PERIOD": "TIME_PERIOD"}
    attrs = {"GEO_LEVEL": "GEO_LEVEL"}
    fragments, params = cf.to_where_clauses(dims, attrs)
    assert fragments == ["GEO IN (?)", "GEO_LEVEL IN (?)"]
    assert params == ["CA", "FED"]


def test_to_where_unknown_attribute_raises():
    cf = parse_components("all", {"BOGUS": ["x"]}, KEY_DIMS)
    dims = {"GEO": "GEO", "FREQ": "FREQ", "TIME_PERIOD": "TIME_PERIOD"}
    with pytest.raises(KeyError, match="BOGUS"):
        cf.to_where_clauses(dims, {})


def test_to_where_attribute_without_attribute_map_raises():
    """Attribute filter present but no attribute_column_map provided → KeyError."""
    cf = parse_components("all", {"GEO_LEVEL": ["FED"]}, KEY_DIMS)
    dims = {"GEO": "GEO", "FREQ": "FREQ", "TIME_PERIOD": "TIME_PERIOD"}
    with pytest.raises(KeyError, match="GEO_LEVEL"):
        cf.to_where_clauses(dims)


# ── SqlRelation dispatch ────────────────────────────────────────────────


class FakeRelation:
    """Minimal SqlRelation stub for unit-testing the dispatch path.

    `columns` is part of the protocol — declared there so arity is checkable
    rather than promised in prose — and this stub did not have it, which
    nothing caught while `tests/` was unchecked (#347). One column, which is
    what `IN (<subquery>)` needs and what these tests exercise.
    """

    def __init__(self, sql: str, params: list[object]) -> None:
        self._sql = sql
        self._params = params

    @property
    def columns(self) -> tuple[str, ...]:
        return ("v",)

    def to_sql(self) -> tuple[str, list[object]]:
        return self._sql, self._params


def test_to_where_dimension_relation_emits_subquery():
    rel = FakeRelation("SELECT id FROM cl WHERE urn = ?", ["URN_GEO"])
    cf = parse_components("all", {"GEO": rel}, KEY_DIMS)
    dims = {"GEO": "REF_AREA", "FREQ": "FREQ", "TIME_PERIOD": "T"}
    fragments, params = cf.to_where_clauses(dims)
    assert fragments == ["REF_AREA IN (SELECT id FROM cl WHERE urn = ?)"]
    assert params == ["URN_GEO"]


def test_to_where_attribute_relation_emits_subquery():
    rel = FakeRelation("SELECT v FROM allowed", [])
    cf = parse_components("all", {"GEO_LEVEL": rel}, KEY_DIMS)
    dims = {"GEO": "GEO", "FREQ": "FREQ", "TIME_PERIOD": "TIME_PERIOD"}
    attrs = {"GEO_LEVEL": "GEO_LEVEL"}
    fragments, params = cf.to_where_clauses(dims, attrs)
    assert fragments == ["GEO_LEVEL IN (SELECT v FROM allowed)"]
    assert params == []


def test_to_where_mixed_eager_and_relation():
    """Eager list on one dim, relation on another, attribute eager — all compose."""
    rel = FakeRelation("SELECT id FROM cl WHERE urn = ?", ["URN_GEO"])
    cf = parse_components("M.", {"GEO": rel}, ["FREQ", "GEO"])
    cf = ComponentFilter(
        dimension_ids=cf.dimension_ids,
        dimensions=cf.dimensions,
        attributes={"OBS_STATUS": ["A"]},
    )
    dims = {"FREQ": "FREQ", "GEO": "REF_AREA"}
    attrs = {"OBS_STATUS": "OBS_STATUS"}
    fragments, params = cf.to_where_clauses(dims, attrs)
    assert fragments == [
        "FREQ IN (?)",
        "REF_AREA IN (SELECT id FROM cl WHERE urn = ?)",
        "OBS_STATUS IN (?)",
    ]
    # Param ordering matches fragment ordering — DuckDB binds positionally.
    assert params == ["M", "URN_GEO", "A"]


def test_to_where_relation_has_no_parameter_explosion():
    """A relation with N values still emits 1 fragment + the relation's small param list."""
    rel = FakeRelation("SELECT id FROM cl WHERE urn = ?", ["URN_GEO"])
    cf = parse_components("all", {"GEO": rel}, KEY_DIMS)
    dims = {"GEO": "REF_AREA", "FREQ": "FREQ", "TIME_PERIOD": "T"}
    fragments, params = cf.to_where_clauses(dims)
    assert len(fragments) == 1
    # Just the relation's bind, not 50k codes.
    assert len(params) == 1


def test_parse_relation_for_key_dim_with_dotted_key_raises():
    """Mixing a dotted-key segment with a relation on the same dim is ambiguous."""
    rel = FakeRelation("SELECT 1", [])
    with pytest.raises(ValueError, match="don't merge"):
        parse_components("CA.M", {"GEO": rel}, ["GEO", "FREQ"])


def test_parse_relation_replaces_wildcard_dim():
    """A relation on a dim with a wildcard dotted-key segment slots in cleanly."""
    rel = FakeRelation("SELECT 1", [])
    cf = parse_components(".M", {"GEO": rel}, ["GEO", "FREQ"])
    assert cf.dimensions[0] is rel
    assert cf.dimensions[1] == ["M"]


def test_relation_makes_filter_non_wildcard():
    rel = FakeRelation("SELECT 1", [])
    cf = parse_components("all", {"GEO": rel}, KEY_DIMS)
    assert not cf.is_wildcard


# ── The empty OR-set (#256) ─────────────────────────────────────────────


def test_empty_dimension_list_compiles_to_a_contradiction():
    """``[]`` admits no value, so the predicate is FALSE — not ``IN ()``."""
    cf = ComponentFilter(dimension_ids=["GEO"], dimensions=[[]])
    fragments, params = cf.to_where_clauses({"GEO": "geo"})
    assert fragments == ["FALSE"]
    assert params == []


def test_empty_attribute_list_compiles_to_a_contradiction():
    cf = ComponentFilter(dimension_ids=["GEO"], dimensions=[None], attributes={"OBS_STATUS": []})
    fragments, params = cf.to_where_clauses({"GEO": "geo"}, {"OBS_STATUS": "obs_status"})
    assert fragments == ["FALSE"]
    assert params == []


def test_none_and_empty_list_mean_opposite_things():
    """The distinction the whole issue turns on.

    ``None`` is "unconstrained" and is what `build_component_availability`
    writes to lift a component's restriction. ``[]`` is "no permitted value".
    Collapsing them would leave the library with two spellings for one meaning
    while the literal reading of one is the opposite.
    """
    unconstrained = ComponentFilter(dimension_ids=["GEO"], dimensions=[None])
    admits_nothing = ComponentFilter(dimension_ids=["GEO"], dimensions=[[]])

    assert unconstrained.to_where_clauses({"GEO": "geo"}) == ([], [])
    assert admits_nothing.to_where_clauses({"GEO": "geo"}) == (["FALSE"], [])
    assert unconstrained.is_wildcard
    assert not admits_nothing.is_wildcard


def test_empty_list_merged_onto_a_dotted_key_changes_nothing():
    """Merging is a set union, so adding no alternatives leaves the key alone.

    ``CA.M.2021`` says GEO ∈ {CA}; ``c[GEO]=`` adds no further alternatives.
    The union is still {CA} — the empty set neither widens the query to
    everything nor narrows it to nothing.
    """
    cf = parse_components("CA.M.2021", {"GEO": []}, KEY_DIMS)
    assert cf.dimensions[0] == ["CA"]


# ── widen_key — accepting the other generation's key shape (#337) ─────────


def _dsd_with_time() -> DataStructure:
    return DataStructure(
        id="D",
        maintainer="AG",
        version="1.0",
        name={"en": "d"},
        dimensions=[
            Dimension(id="FREQ", concept=concept_ref("FREQ")),
            Dimension(id="REF_AREA", concept=concept_ref("REF_AREA")),
        ],
        time_dimension=TimeDimension(id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD")),
    )


def test_a_short_key_is_widened_for_a_3_0_endpoint() -> None:
    """The case the bridge exists for.

    Fusion Registry, pandasdmx and SDMX-RI all send 1.5-shaped keys with the
    time dimension omitted. A 2.2.2 endpoint that refuses them is refusing
    conformant requests from most of the ecosystem.
    """
    assert widen_key("M.CA", _dsd_with_time(), "3.0") == "M.CA."


def test_a_key_already_the_right_length_is_untouched() -> None:
    assert widen_key("M.CA.", _dsd_with_time(), "3.0") == "M.CA."


def test_a_long_key_is_never_narrowed() -> None:
    """One direction only, and this is why.

    Widening adds a *wildcard*, which cannot change which rows match. Narrowing
    would drop a trailing segment, silently discarding a filter the caller
    wrote — so a 2.2.2-shaped key handed to a 2.1 endpoint is left alone and
    refused downstream, where the caller learns about it.
    """
    assert widen_key("M.CA.2020", _dsd_with_time(), "2.1") == "M.CA.2020"


def test_a_key_of_no_recognisable_length_is_left_for_the_parser() -> None:
    """Two segments short is not a version difference, it is a bad key.

    Guessing at it would turn a clear error into a wrong answer, so this
    returns it unchanged and `parse_components` refuses with a message naming
    both counts.
    """
    dsd = _dsd_with_time()
    assert widen_key("M", dsd, "3.0") == "M"
    with pytest.raises(ValueError, match="segments"):
        parse_components(widen_key("M", dsd, "3.0"), {}, dsd.key_slot_ids("3.0"))


@pytest.mark.parametrize("key", ["all", ""])
def test_the_wildcards_are_untouched(key: str) -> None:
    assert widen_key(key, _dsd_with_time(), "3.0") == key


def test_a_structure_without_a_time_dimension_is_untouched() -> None:
    """Nothing to widen *into*.

    The two generations agree about a structure with no time dimension, so a
    short key there is simply short — and widening it would append a segment
    for a component that does not exist.
    """
    dsd = DataStructure(
        id="D",
        maintainer="AG",
        version="1.0",
        name={"en": "d"},
        dimensions=[
            Dimension(id="FREQ", concept=concept_ref("FREQ")),
            Dimension(id="REF_AREA", concept=concept_ref("REF_AREA")),
        ],
    )
    assert widen_key("M", dsd, "3.0") == "M"


def test_widening_then_parsing_leaves_time_unconstrained() -> None:
    """The widened segment must be a wildcard, not an empty *value*.

    If it parsed as a constraint admitting nothing, a 1.5 client's query would
    return zero rows against a 2.2.2 endpoint — the silent-wrong-answer failure
    this whole issue is about, reintroduced by the fix for it.
    """
    dsd = _dsd_with_time()
    parsed = parse_components(widen_key("M.CA", dsd, "3.0"), {}, dsd.key_slot_ids("3.0"))
    by_id = dict(zip(parsed.dimension_ids, parsed.dimensions, strict=True))
    assert by_id["TIME_PERIOD"] is None, "unconstrained, not an empty set"
    assert by_id["FREQ"] == ["M"]
