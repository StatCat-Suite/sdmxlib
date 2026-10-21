"""Tests closing the real gaps `collections.py`'s surviving mutants found (#253).

The #213 pilot reported 27 survivors here. Re-running after all the test work
since: **still exactly 27** of 179 mutants — 145 killed, 3 timeout, 3 segfault,
1 no-tests. Nothing that landed in between touched them, which is the
calibration #253 wanted on how much four properties actually pin.

Triage of all 27 is recorded in `docs/design/mutation-testing.md`. This file
holds the ones that were **real gaps**: a mutant changes behaviour a caller can
observe, and nothing noticed. Twelve died; the surviving fifteen are recorded
there as equivalent or won't-fix, each with its reason.

They cluster, and the cluster is informative. `ArtefactList`'s *lookup* paths —
partial `SdmxRef` matching, misses on a bare id, the descending sort — had no
direct coverage at all, while the four existing properties concentrate on
`ItemList` and on aggregate shape (subset, cover, ids). Aggregate properties
pin aggregate claims; they say nothing about a lookup returning the wrong one
of two artefacts.
"""

import pytest

from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ArtefactList, ItemList
from sdmxlib.model.message import MetadataMessage, StructureMessage
from sdmxlib.model.ref import SdmxRef

pytestmark = pytest.mark.unit


def _cl(agency: str, cl_id: str, version: str) -> Codelist:
    return Codelist(
        id=cl_id,
        maintainer=agency,
        version=version,
        name={"en": cl_id},
        codes=[Code(id="A", name={"en": "a"})],
    )


@pytest.fixture
def artefacts() -> ArtefactList[Codelist]:
    """Two agencies, two versions, one shared id — so lookups can be wrong."""
    return ArtefactList(
        [
            _cl("AG", "CL_A", "1.0"),
            _cl("AG", "CL_A", "2.0"),
            _cl("ESTAT", "CL_A", "1.0"),
            _cl("AG", "CL_B", "1.0"),
        ]
    )


# ── SdmxRef lookup: the wildcard and the comparison ──────────────────────────


def test_a_ref_field_of_none_is_a_wildcard(artefacts) -> None:
    """``ref.id is None`` means "any id", and the sense of that test matters.

    Killed mutant: ``is None`` → ``is not None``, which inverts every wildcard
    at once — a ref naming an id would match everything and a ref naming none
    would match nothing. No test looked.
    """
    only_agency = artefacts[SdmxRef(agency="ESTAT")]
    assert only_agency is not None
    assert (only_agency.agency_id, only_agency.id, only_agency.version) == ("ESTAT", "CL_A", "1.0")

    only_id = artefacts[SdmxRef(id="CL_B")]
    assert only_id is not None
    assert only_id.id == "CL_B"


def test_a_ref_version_must_match_not_differ(artefacts) -> None:
    """Killed mutant: ``item.version == ref.version`` → ``!=``.

    With the comparison flipped, asking for 2.0 returns the 1.0 artefacts —
    the most quietly wrong answer this module can give, since the caller gets
    a real Codelist of the right id and agency.
    """
    got = artefacts[SdmxRef(agency="AG", id="CL_A", version="2.0")]
    assert got is not None
    assert got.version == "2.0"


def test_an_ambiguous_ref_names_the_candidates(artefacts) -> None:
    """The message content is pinned; its punctuation is not.

    Killed mutant: the separator in ``', '.join(qualified)``. Asserting the
    exact string would encode formatting, so this asserts the thing a caller
    actually needs — which artefacts collided.
    """
    with pytest.raises(KeyError) as exc:
        artefacts[SdmxRef(id="CL_A")]
    message = str(exc.value)
    assert "AG:CL_A(1.0)" in message
    assert "AG:CL_A(2.0)" in message
    assert "ESTAT:CL_A(1.0)" in message


# ── String lookup: the miss, and the malformed key ───────────────────────────


def test_a_miss_on_a_bare_id_returns_none_rather_than_raising(artefacts) -> None:
    """Killed mutants: ``_by_id.get(key, [])`` → ``.get(key, None)`` / ``.get(key)``.

    The default is load-bearing. Without it a miss makes ``len(matches)`` a
    ``TypeError`` on ``None`` — so looking up an id that is simply absent
    crashes instead of returning ``None``, and only for the *bare id* path.
    """
    assert artefacts.get("NOPE") is None


def test_a_miss_on_a_qualified_key_returns_none(artefacts) -> None:
    """The agency-qualified path has the same default and the same hazard."""
    assert artefacts.get("AG:NOPE") is None
    assert artefacts.get("NOPE:CL_A") is None


def test_an_ambiguous_bare_id_names_the_candidates(artefacts) -> None:
    """As above, for the string path's own ambiguity message."""
    with pytest.raises(KeyError) as exc:
        _ = artefacts["CL_A"]
    message = str(exc.value)
    assert "AG:CL_A(1.0)" in message
    assert "ESTAT:CL_A(1.0)" in message


def test_a_versioned_key_needs_both_delimiters(artefacts) -> None:
    """Killed mutant: ``"(" in key and key.endswith(")")`` → ``or``.

    With ``or``, ``"AG:CL_A("`` takes the versioned branch and splits into a
    version that is not one. Both delimiters have to be present for the key to
    be read as versioned; anything else is an ordinary agency-qualified key or
    a miss.
    """
    assert artefacts["AG:CL_A(1.0)"].version == "1.0"
    assert artefacts.get("AG:CL_A(") is None
    assert artefacts.get("AG:CL_A)") is None


# ── Sorting and projection ───────────────────────────────────────────────────


def test_artefactlist_sort_by_honours_descending(artefacts) -> None:
    """Killed mutants: ``reverse=descending`` → ``reverse=None`` / omitted.

    Both make ``descending=True`` a silent no-op. The existing properties check
    that sorting preserves membership, which every one of those mutants also
    does.
    """
    ascending = [a.version for a in artefacts.sort_by(lambda a: (a.id, a.version))]
    descending = [a.version for a in artefacts.sort_by(lambda a: (a.id, a.version), descending=True)]
    assert descending == list(reversed(ascending))
    assert descending != ascending, "fixture no longer discriminates order"


def test_artefactlist_to_dict_uses_the_key_function_per_item(artefacts) -> None:
    """Killed mutant: ``key(item)`` → ``key(None)``.

    Every key becomes identical, so the dict collapses to one entry. A test
    checking only "is a dict" or "values are artefacts" passes.
    """
    by_urn = artefacts.to_dict(lambda a: f"{a.agency_id}:{a.id}({a.version})")
    assert len(by_urn) == len(artefacts)
    assert by_urn["AG:CL_A(2.0)"].version == "2.0"


# ── Hashing ──────────────────────────────────────────────────────────────────


def test_the_collections_are_honestly_unhashable() -> None:
    """`__hash__` is `None`, so the *declaration* is the refusal (#290).

    This test used to pin the bug rather than the fix. `__hash__` was
    `hash(self._items)`, which worked for the empty list and raised
    `TypeError: unhashable type: 'Code'` for every populated one — every domain
    item is an attrs `@define` class, so mutable, so unhashable. The class
    advertised a capability it could not deliver for any type it is actually
    parameterised with, and the surviving mutant `hash(self._items) ->
    hash(None)` was arguably an improvement, since a constant at least returns.

    Both directions matter, which is why the empty case is asserted too:
    "empty hashes, populated raises" was the *inconsistency*, and a fix that
    left the empty list hashable would have kept it.

    Hashing the ids was the alternative. It satisfies the `__eq__`/`__hash__`
    contract on paper — equal lists have equal ids — and fails in practice,
    because the items are mutable: a list used as a dict key would keep its
    hash while `__eq__` changed underneath it. Python makes `list` unhashable
    for the same reason.
    """
    for collection in (ItemList[Code]([]), ItemList([Code(id="A", name={"en": "a"})])):
        with pytest.raises(TypeError, match="unhashable type: 'ItemList'"):
            hash(collection)


def test_the_error_names_the_collection_not_its_contents() -> None:
    """The message a caller actually sees.

    `unhashable type: 'Code'` sent whoever hit it looking at the wrong class —
    `Code` is unhashable by design, and nothing about that message says the
    collection was the thing refusing.
    """
    with pytest.raises(TypeError) as caught:
        hash(ItemList([Code(id="A", name={"en": "a"})]))
    assert "Code" not in str(caught.value), "the error still blames the item type"


def test_a_message_refuses_in_its_own_name() -> None:
    """The same claim, one level up (#290).

    `StructureMessage` and `MetadataMessage` are `@frozen`, so attrs generated
    a `__hash__` for them — which worked only while the message was *empty*,
    because every collection field holds mutable items. Fixing the collections
    alone would have left a caller hashing a message and being told about an
    `ArtefactList` they never mentioned.

    `@frozen` is for immutability, which still holds. Hashability was its side
    effect, and it was never deliverable.
    """
    for message in (StructureMessage(), MetadataMessage()):
        with pytest.raises(TypeError, match=f"unhashable type: '{type(message).__name__}'"):
            hash(message)


# ── Messages that carry information, without pinning their format ────────────


def test_a_malformed_versioned_key_names_the_key(artefacts) -> None:
    """Killed mutants: ``msg = None`` and ``raise KeyError(None)``.

    Both keep the exception *type*, so "it raises KeyError" passes either way.
    What they destroy is the only thing that makes the error actionable — which
    key was malformed. A `KeyError(None)` tells the caller nothing at all.
    """
    with pytest.raises(KeyError) as exc:
        _ = artefacts["CL_A(1.0)"]  # parens but no agency
    assert "CL_A(1.0)" in str(exc.value)


def test_repr_names_the_members(artefacts) -> None:
    """Killed mutants: the `__repr__` family that drops or blanks the ids.

    `repr` is debugging output, so its *format* is deliberately not pinned —
    asserting the exact string would freeze punctuation nobody has decided on.
    Its *content* is another matter: a repr that no longer says which artefacts
    are in the list has lost the only reason to call it, and nine mutants lived
    in these two methods because nothing looked at either.
    """
    items = ItemList([Code(id="AAA", name={"en": "a"}), Code(id="BBB", name={"en": "b"})])
    text = repr(items)
    assert "ItemList" in text
    assert "AAA" in text
    assert "BBB" in text

    text = repr(artefacts)
    assert "ArtefactList" in text
    assert "AG:CL_A(1.0)" in text
    assert "ESTAT:CL_A(1.0)" in text
