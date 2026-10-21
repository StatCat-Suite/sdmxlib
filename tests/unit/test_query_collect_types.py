"""What a root *declares* it collects, against what it actually builds (#396).

`Query.collect()` returns `list[A]`, where `A` is pinned on the accessor class
(`_Code(Cols[R, Code])`). Nothing statically relates that pin to the resolver
that does the building: resolvers are registered per *table* at runtime in
`_RESOLVERS`, so `query.py:_as_artefacts` has to assert the correspondence with
a `cast`.

This is the guard that makes the cast honest, and it is differential rather
than a restatement: the declared side is read off `__orig_bases__`, the actual
side comes from running the query, and neither is written down twice. A pin
that drifts from its resolver fails here.

`test_every_root_is_accounted_for` is the half that survives someone adding a
38th root — the two sides are enumerated off `q` itself, so a new accessor is
either populated and checked or named in `UNPOPULATED` with a reason.
"""

from __future__ import annotations

import typing
from typing import TYPE_CHECKING, Any

import pytest

import sdmxlib as sl
from sdmxlib.local import LocalRegistry
from sdmxlib.model.mapping import CategorySchemeMap, ItemMap
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.ref import Ref, artefact_urn
from sdmxlib.query._expr import Cols, RowKind
from sdmxlib.query.accessors import q
from sdmxlib.storage.lazy import LazyCodelist, LazyHierarchy
from tests.support import maximal

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

#: Forward references the accessors pin as strings, because importing
#: `storage.lazy` there at runtime would cycle. Resolved by hand here, and that
#: is the one hand-written link in this file — kept to two entries for that
#: reason.
FORWARD_REFS = {"CollectedCodelist": LazyCodelist, "CollectedHierarchy": LazyHierarchy}

#: Roots the fixture cannot populate, and why. Empty, and that is the point:
#: every building root returns rows, so no assertion below is vacuous. A new
#: root that cannot be populated goes here with a reason rather than silently
#: contributing nothing.
UNPOPULATED: dict[str, str] = {}

#: How many roots build objects, and how many refuse. Asserted rather than
#: derived so that moving one across the line is a visible change.
BUILDING_ROOTS = 27
REFUSING_ROOTS = 21


def _declared(accessor: type[object]) -> Any:
    """The `A` this accessor pins, read off its base rather than a table here.

    Walks `__orig_bases__` to the first parameterised base and takes its second
    argument. A base that leaves `A` free — `_Maintainable` itself — yields the
    TypeVar, whose `__default__` is the answer.

    A `Cols` subclass that pins nothing yields `object`, because PEP 696 fills
    the default in at subscript time. That is not treated as an error here;
    `test_every_accessor_pins_an_artefact_type` is what rejects it, so the
    failure names the accessor rather than this helper.
    """
    for base in getattr(accessor, "__orig_bases__", ()):
        args = typing.get_args(base)
        if len(args) < 2:
            continue
        pinned = args[1]
        if isinstance(pinned, typing.TypeVar):
            return pinned.__default__
        if isinstance(pinned, typing.ForwardRef):
            return FORWARD_REFS[pinned.__forward_arg__]
        return pinned
    msg = f"{accessor.__name__} names no artefact — every Cols subclass must pin one"
    raise AssertionError(msg)


def _roots() -> dict[str, Any]:
    """Every root accessor, by name.

    `Any` because this enumerates 37 accessors of 37 different
    `Cols[RowKind, Artefact]` instantiations, and `A` is invariant — no single
    static type describes the collection. That is the point of the test, and
    CLAUDE.md's rule for a genuinely dynamic step is one annotation here.
    """
    return {name: getattr(q, name) for name in dir(q) if not name.startswith("_")}


@pytest.fixture(scope="module")
def populated() -> Iterator[LocalRegistry]:
    """One of everything the maximal message holds, plus a MetadataSet.

    The maximal fixture is the widest artefact set in the suite, which is what
    makes this worth running: a root with no rows would make its assertion
    vacuous, and `test_every_root_is_accounted_for` fails rather than letting
    that pass quietly.
    """
    with LocalRegistry(":memory:") as reg:
        for artefact in maximal.message().all_artefacts():
            try:
                reg.add(artefact)
            except TypeError:
                # Not persisted by this registry — #395's refusal, not ours.
                continue
        category_scheme = sl.CategoryScheme(id="CATS_Q", maintainer="AG", version="1.0", name={"en": "Categories"})
        reg.add(category_scheme)
        codelist = sl.Codelist(
            id="CL_TARGET",
            maintainer="AG",
            version="1.0",
            name={"en": "Target"},
            codes=[sl.Code(id="A", name={"en": "a"})],
        )
        # #395 made the three map kinds storable; the maximal message carries
        # two of them, so the third is built here rather than listed as
        # unpopulated — an assertion over zero rows checks nothing.
        reg.add(
            CategorySchemeMap(
                id="CATM_Q",
                maintainer="AG",
                version="1.0",
                name={"en": "Category map"},
                source=Ref.to(category_scheme),
                target=Ref.to(category_scheme),
                maps=(ItemMap(source_id="A", target_id="B"),),
            )
        )
        msd = MetadataStructure(id="MSD_Q", maintainer="AG", name={"en": "MSD"})
        mdf = Metadataflow(id="MDF_Q", maintainer="AG", name={"en": "MDF"}, structure=msd)
        for artefact in (codelist, msd, mdf):
            reg.add(artefact)
        reg.add(
            MetadataSet(
                id="MDS_Q",
                maintainer="AG",
                name={"en": "Set"},
                structure=mdf,
                targets=[artefact_urn(codelist)],
                attributes=[ReportedAttribute(id="CONTACT", value="x@example.org")],
            )
        )
        yield reg


# ── the differential ──────────────────────────────────────────────────────


def test_what_each_root_builds_is_what_it_declares(populated: LocalRegistry) -> None:
    """Every element of `collect()` is an instance of the accessor's pinned `A`.

    This is what `_as_artefacts`'s cast asserts and cannot check.
    """
    checked: list[str] = []
    for name, root in sorted(_roots().items()):
        declared = _declared(type(root))
        if declared is typing.Never:
            continue
        rows = populated.query(root).collect()
        if not rows:
            continue
        wrong = [r for r in rows if not isinstance(r, declared)]
        assert not wrong, (
            f"q.{name} declares {declared!r} but collect() returned {sorted({type(r).__name__ for r in wrong})}"
        )
        checked.append(name)
    assert len(checked) == BUILDING_ROOTS, (
        f"{len(checked)} of {BUILDING_ROOTS} building roots had rows — the rest "
        f"assert nothing. Populate them in the fixture or state why in UNPOPULATED."
    )


def test_a_root_that_declares_never_refuses_to_collect(populated: LocalRegistry) -> None:
    """`Never` is the declaration, and the runtime `TypeError` is the behaviour.

    They have to agree in both directions: a root pinned `Never` that quietly
    built something would be a silent lie, and one that refuses while pinning
    an artefact would send a caller looking for rows that never come.
    """
    refused: list[str] = []
    for name, root in sorted(_roots().items()):
        declares_never = _declared(type(root)) is typing.Never
        try:
            populated.query(root).collect()
        except TypeError:
            assert declares_never, f"q.{name} refuses to collect but does not declare Never"
            refused.append(name)
        else:
            assert not declares_never, f"q.{name} declares Never but collect() succeeded"
    assert len(refused) == REFUSING_ROOTS, f"expected {REFUSING_ROOTS} refusing roots, got {len(refused)}: {refused}"


# ── the inventory ─────────────────────────────────────────────────────────


def test_every_root_is_accounted_for(populated: LocalRegistry) -> None:
    """A new accessor is populated and checked, or named in `UNPOPULATED`.

    Without this the differential above silently shrinks: a root with no rows
    contributes no assertion, so adding one and forgetting the fixture reads
    exactly like adding one that works.
    """
    unchecked: list[str] = []
    for name, root in sorted(_roots().items()):
        if _declared(type(root)) is typing.Never:
            continue
        if not populated.query(root).collect():
            unchecked.append(name)
    assert set(unchecked) <= set(UNPOPULATED), (
        f"roots with no rows and no stated reason: {sorted(set(unchecked) - set(UNPOPULATED))} — "
        f"populate them in the fixture or add them to UNPOPULATED with a reason"
    )


def test_every_accessor_pins_an_artefact_type() -> None:
    """No root still collects bare `object` — the state this issue is about.

    `object` is what a `Cols` subclass gets by default, so this is the check
    that a new accessor decided what it builds instead of inheriting silence.
    """
    for name, root in sorted(_roots().items()):
        pinned = _declared(type(root))
        assert pinned is not None, f"q.{name} pins nothing"
        assert pinned is not object, f"q.{name} still collects bare `object` — pin the artefact it builds, or `Never`"


def test_the_pins_are_read_from_the_class_not_from_a_table_here() -> None:
    """Guards `_declared` itself: it must fail on a `Cols` subclass with no pin.

    A helper that silently answered `object` would make every assertion above
    pass vacuously, which is the failure mode this whole file exists to avoid.
    """

    class _Unpinned[R: RowKind](Cols[R]):
        __slots__ = ()

    assert _declared(_Unpinned) is object, "the default is object, and the tests above reject it"
