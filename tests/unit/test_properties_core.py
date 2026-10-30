"""Property-based tests for the core value types (#215).

A deliberately small first set. The point of this file is to prove the setup —
strategies, profiles, shrinking — on types where the properties are obvious and
the failures would be unambiguous. The valuable targets (serializer round-trips,
query-DSL differential testing) are #216 and #217 and depend on this landing.

Where a property overlaps a module the mutation pilot measured (#213), the
docstring says what the pilot found, so the follow-up re-run has something to
compare against.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sdmxlib.model.collections import ArtefactList, ItemList
from sdmxlib.model.istring import InternationalString, LocalisedString
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.version import sort_key
from tests.support.strategies import (
    agency_ids,
    codelists,
    international_strings,
    names,
    sdmx_ids,
    versions,
)

pytestmark = pytest.mark.unit


# ── URN ───────────────────────────────────────────────────────────────────────


@given(agency=agency_ids(), artefact_id=sdmx_ids(), version=versions())
def test_urn_parse_is_inverse_of_str(agency: str, artefact_id: str, version: str):
    """``parse(str(urn)) == urn`` for every generated URN.

    `model/urn.py` is at 100% line coverage *and* produced zero surviving
    mutants in the #213 pilot, so this is not expected to find a bug — it is
    expected to keep being true as the URN grammar grows.
    """
    from sdmxlib.model.codelist import Codelist

    urn = Codelist.urn_for(agency=agency, id=artefact_id, version=version)
    assert SdmxUrn.parse(str(urn)) == urn


@given(agency=agency_ids(), artefact_id=sdmx_ids(), version=versions(), item_id=sdmx_ids())
def test_item_urn_round_trips(agency: str, artefact_id: str, version: str, item_id: str):
    """Item-level URNs carry the item id through a parse/format cycle."""
    from sdmxlib.model.codelist import Code

    urn = SdmxUrn.make(
        Code,
        package="codelist",
        artefact_class="Code",
        agency=agency,
        id=artefact_id,
        version=version,
        item_id=item_id,
    )
    parsed = SdmxUrn.parse(str(urn))
    assert parsed == urn
    assert parsed.item_id == item_id


# ── InternationalString ───────────────────────────────────────────────────────


@given(istr=international_strings())
def test_istring__emit_pair_round_trips(istr: InternationalString):
    """``_emit_pair`` splits into (primary, map); the map reconstructs the value.

    This is the split every SDMX-JSON writer relies on. The #213 pilot left
    three survivors in `istring.py`, all in `__repr__` / `__hash__` /
    `as_dict` — none in this path.
    """
    primary, full = istr._emit_pair()
    if not istr:
        assert (primary, full) == (None, None)
        return
    assert full is not None
    assert InternationalString(**full) == istr
    assert primary in full.values()


@given(istr=international_strings(allow_empty=False))
def test_istring_get_agrees_with_mapping(istr: InternationalString):
    """``get(lang)`` agrees with the backing map for every language present."""
    for lang, text in istr.as_dict().items():
        assert istr[lang] == LocalisedString(text, lang)


# ── Version ordering ──────────────────────────────────────────────────────────


@given(a=versions(), b=versions())
def test_version_key_is_antisymmetric(a: str, b: str):
    """A total order: at most one of a<b, b<a holds, and equal keys tie.

    These two properties used to run against `api.session._version_key`, a
    second, cruder version parser that existed only to pick a winner for the
    `latest` cache index. #338 removed that index, and with it the parser —
    so they now run against `model.version.sort_key` (#311), which is the one
    the storage layer bakes and the query language orders by. Same properties,
    pointed at the implementation that is actually load-bearing.
    """
    ka, kb = sort_key(a), sort_key(b)
    assert not (ka < kb < ka)
    if ka == kb:
        assert not ka < kb
        assert not kb < ka


@given(a=versions(), b=versions(), c=versions())
def test_version_key_is_transitive(a: str, b: str, c: str):
    """a < b and b < c implies a < c."""
    ka, kb, kc = sort_key(a), sort_key(b), sort_key(c)
    if ka < kb < kc:
        assert ka < kc


# ── Collections ───────────────────────────────────────────────────────────────


@given(cl=codelists())
def test_itemlist_indexing_agrees_with_iteration(cl):
    """Every code reachable by id is the same object iteration yields.

    `collections.py` carried 27 surviving mutants in the #213 pilot, 11 of them
    in `ArtefactList._lookup_str` — the id-lookup path this exercises.
    """
    for code in cl.codes:
        assert cl.codes[code.id] is code
    assert list(cl.codes.ids()) == [c.id for c in cl.codes]


@given(cl=codelists())
def test_itemlist_filter_is_a_subset_that_satisfies_the_predicate(cl):
    """`filter` yields a subset, and every element satisfies the predicate."""
    kept = cl.codes.filter(lambda c: c.id.startswith("A"))
    originals = list(cl.codes)
    assert all(c in originals for c in kept)
    assert all(c.id.startswith("A") for c in kept)


@given(cl=codelists())
def test_itemlist_partition_is_a_disjoint_cover(cl):
    """`partition` splits into two disjoint halves whose union is the input."""
    yes, no = cl.codes.partition(lambda c: c.id.startswith("A"))
    assert len(yes) + len(no) == len(cl.codes)
    assert {id(c) for c in yes}.isdisjoint({id(c) for c in no})
    assert {id(c) for c in [*yes, *no]} == {id(c) for c in cl.codes}


@given(cls=st.lists(codelists(), max_size=4))
def test_artefactlist_ids_match_members(cls):
    """`ArtefactList.ids()` reports exactly the ids of what it holds."""
    al = ArtefactList(cls)
    assert list(al.ids()) == [c.id for c in cls]


# ── Validation ────────────────────────────────────────────────────────────────


@given(cl=codelists())
def test_generated_codelists_validate(cl):
    """Every artefact these strategies build must pass `validate`.

    This is the strategy/spec agreement check, and it is only meaningful
    because the alphabets in `tests.support.strategies` are transcribed from
    the SDMX-IM rather than from `validation.py`'s regexes. A failure here is a
    genuine question about which of the two is wrong, not a tautology.
    """


@given(name=names(), items=st.lists(sdmx_ids(), max_size=4, unique=True))
def test_itemlist_length_is_stable(name: InternationalString, items: list[str]):
    """Building an ItemList preserves cardinality when ids are unique."""
    from sdmxlib.model.codelist import Code

    il = ItemList([Code(id=i, name=name) for i in items])
    assert len(il) == len(items)
