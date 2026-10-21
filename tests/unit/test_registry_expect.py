"""`get(..., expect=Class)` answers that class or None, on every registry (#406).

A URN parsed from a string carries no type a checker can use, and what a
checker infers for `get(parse(...))` is not settled — #399 measured one tool
taking the first overload and another answering `Any`. `expect=` sidesteps the
question rather than fighting it: `T` comes from the argument, so the
ambiguous-overload case never arises.

**The runtime half is the point, not a formality.** The consumer's two call
sites read

    [ms for u in urns if isinstance(ms := registry.get(sl.SdmxUrn.parse(u)), sl.MetadataSet)]

because a stored row may legitimately name something the call site cannot use —
and before that filter existed, such a row reached the writer and failed there.
So the `isinstance` and the narrowing are one operation, which is the argument
for it living in the library. These tests assert both halves: the match, and
the mismatch answering None rather than a mistyped object.

The static half is `tests/typing/probe_registry_expect.py`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import CodelistLike
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.storage.lazy import LazyCodelist

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def codelist() -> sl.Codelist:
    return sl.Codelist(
        id="CL",
        maintainer="AG",
        version="1.0",
        name={"en": "cl"},
        codes=[sl.Code(id="A", name={"en": "a"})],
    )


@pytest.fixture
def dsd() -> sl.DataStructure:
    return sl.DataStructure(id="DSD", maintainer="AG", version="1.0", name={"en": "dsd"})


@pytest.fixture
def memory(codelist: sl.Codelist, dsd: sl.DataStructure) -> InMemoryRegistry:
    registry = InMemoryRegistry()
    registry.add(codelist)
    registry.add(dsd)
    return registry


@pytest.fixture
def local(codelist: sl.Codelist, dsd: sl.DataStructure) -> Iterator[sl.LocalRegistry]:
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(codelist)
        registry.add(dsd)
        yield registry


def _parsed(artefact: sl.Codelist | sl.DataStructure) -> SdmxUrn[object]:
    """The URN as a caller holds it after reading a string out of a store."""
    return SdmxUrn.parse(str(artefact.urn))


# ── InMemoryRegistry ─────────────────────────────────────────────────────


def test_memory_returns_the_artefact_when_the_class_matches(memory: InMemoryRegistry, codelist: sl.Codelist) -> None:
    assert memory.get(_parsed(codelist), expect=sl.Codelist) is codelist


def test_memory_returns_none_when_the_class_does_not(memory: InMemoryRegistry, dsd: sl.DataStructure) -> None:
    """The discrimination check: the URN resolves, the class is wrong, and the
    answer is None rather than a `DataStructure` in a `Codelist`-shaped hole."""
    assert memory.get(_parsed(dsd), expect=sl.Codelist) is None


def test_memory_returns_none_when_the_urn_is_absent(memory: InMemoryRegistry) -> None:
    missing = SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:GONE(1.0)")
    assert memory.get(missing, expect=sl.Codelist) is None


def test_memory_without_expect_is_unchanged(memory: InMemoryRegistry, codelist: sl.Codelist) -> None:
    """`expect=` is additive: the existing call keeps its behaviour, which is
    what stops this being a change to `get` rather than an addition to it."""
    assert memory.get(codelist.urn) is codelist


def test_memory_expect_reaches_an_item(memory: InMemoryRegistry) -> None:
    """Composes with the item resolution added in #405 — the lookup runs first
    and `expect` checks whatever it produced."""
    code_urn = SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Code=AG:CL(1.0).A")
    assert isinstance(memory.get(code_urn, expect=sl.Code), sl.Code)
    assert memory.get(code_urn, expect=sl.Codelist) is None


# ── LocalRegistry, where the lazy roots make `expect` a real question ────


def test_local_expects_what_actually_comes_back(local: sl.LocalRegistry, codelist: sl.Codelist) -> None:
    """A stored codelist comes back as a `LazyCodelist`, which is not a
    `Codelist` — so ``expect=Codelist`` answers None, and that is correct
    rather than surprising once said out loud. The docstring says it; this
    pins it, because a caller who did not read it will meet it here."""
    urn = _parsed(codelist)
    assert isinstance(local.get(urn, expect=LazyCodelist), LazyCodelist)
    assert local.get(urn, expect=sl.Codelist) is None


def test_local_expect_composes_with_eager(local: sl.LocalRegistry, codelist: sl.Codelist) -> None:
    """The way to ask for the real class: materialise, then check."""
    found = local.get(_parsed(codelist), expect=sl.Codelist, eager=True)
    assert isinstance(found, sl.Codelist)
    assert found.codes["A"].id == "A"


def test_local_expect_accepts_the_shared_protocol(local: sl.LocalRegistry, codelist: sl.Codelist) -> None:
    """`CodelistLike` is what the eager and lazy classes share (#398), so it is
    the expectation that holds either way without forcing a materialisation."""
    assert isinstance(local.get(_parsed(codelist), expect=CodelistLike), LazyCodelist)


def test_local_returns_none_when_the_class_does_not_match(local: sl.LocalRegistry, dsd: sl.DataStructure) -> None:
    assert local.get(_parsed(dsd), expect=sl.Codelist) is None


def test_local_without_expect_is_unchanged(local: sl.LocalRegistry, codelist: sl.Codelist) -> None:
    assert isinstance(local.get(codelist.urn), LazyCodelist)
    assert isinstance(local.get(codelist.urn, eager=True), sl.Codelist)


def test_expect_is_the_isinstance_the_caller_would_write(local: sl.LocalRegistry, dsd: sl.DataStructure) -> None:
    """The claim the feature rests on, asserted as an equivalence.

    If these two ever disagree, `expect=` has stopped being a shorthand for the
    filter it replaces and has become its own semantics.
    """
    urn = _parsed(dsd)
    by_hand = local.get(urn)
    expected = local.get(urn, expect=sl.DataStructure)
    assert (expected is not None) is isinstance(by_hand, sl.DataStructure)
    # Equality, not identity: this registry reconstructs from DuckDB per call,
    # so two reads of one artefact are equal and not the same object.
    assert expected is None or expected == by_hand
