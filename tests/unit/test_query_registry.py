"""`reg.query()` against a real registry, resolving to real artefacts.

The other query tests drive the compiler with a hand-built schema. These go
through the front door — `Registry.add` writes, `reg.query` reads — so they
catch the wiring the layered tests cannot: a resolver that returns the wrong
objects, a discriminator that does not match what the writer stored, an
accessor naming a column the writer never fills.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.annotations import Annotation
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.query.accessors import q
from sdmxlib.query.query import SliceError
from sdmxlib.storage.lazy import LazyCodelist

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


def _concept_ref(item_id: str) -> Ref[Concept]:
    """A Ref to a concept in the fixture's scheme."""
    return Ref[Concept].unresolved(
        SdmxUrn.make(
            Concept,
            package="conceptscheme",
            artefact_class="Concept",
            agency="ESTAT",
            id="CROSS",
            version="1.0",
            item_id=item_id,
        )
    )


def _codelist(cid: str, agency: str, codes: list[str]) -> Codelist:
    """The first code carries an annotation, so a resolver that dropped them
    would answer this fixture wrong rather than merely thinly."""
    return Codelist(
        id=cid,
        maintainer=Agency(id=agency),
        name={"en": cid},
        codes=[
            Code(
                id=c,
                name={"en": c},
                annotations=[Annotation(id=f"a{i}", type="NOTE", title=f"about {c}")] if i == 0 else [],
            )
            for i, c in enumerate(codes)
        ],
    )


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    with sl.LocalRegistry(":memory:") as registry:
        area = _codelist("CL_AREA", "ESTAT", ["CA", "US"])
        freq = _codelist("CL_FREQ", "OECD", ["A", "Q"])
        registry.add(area)
        registry.add(freq)
        scheme = ConceptScheme(
            id="CROSS",
            maintainer=Agency(id="ESTAT"),
            name={"en": "CROSS"},
            concepts=[
                Concept(id="REF_AREA", name={"en": "Reference area"}),
                Concept(id="FREQ", name={"en": "Frequency"}),
            ],
        )
        registry.add(scheme)
        # `concept=` takes a Ref, unlike `representation=` which also accepts
        # a bare Codelist — an asymmetry in the domain model, not in the query
        # layer. A Concept is a child item, so its URN carries an item_id.
        ref_area = _concept_ref("REF_AREA")
        freq_concept = _concept_ref("FREQ")
        registry.add(
            DataStructure(
                id="CPI",
                maintainer=Agency(id="ESTAT"),
                name={"en": "CPI"},
                dimensions=[
                    Dimension(id="REF_AREA", concept=ref_area, representation=area),
                    Dimension(id="FREQ", concept=freq_concept, representation=freq),
                ],
            )
        )
        registry.add(
            DataStructure(
                id="TRADE",
                maintainer=Agency(id="OECD"),
                name={"en": "TRADE"},
                dimensions=[Dimension(id="FREQ", concept=freq_concept, representation=freq)],
            )
        )
        yield registry


# ── the discriminator matches what the writer stored ──────────────────────


def test_each_root_sees_only_its_own_type(reg: sl.LocalRegistry) -> None:
    """The `artefact_type` constants must agree with `storage.writers`.

    A mismatch here fails silently — an empty result is not an error — so this
    is checked against written rows rather than against the constant.
    """
    assert reg.query(q.codelist).ids() == ["CL_AREA", "CL_FREQ"]
    assert reg.query(q.dsd).ids() == ["CPI", "TRADE"]
    assert reg.query(q.concept_scheme).ids() == ["CROSS"]


def test_the_polymorphic_root_sees_everything(reg: sl.LocalRegistry) -> None:
    ids = reg.query(q.artefact).ids()
    for expected in ("CL_AREA", "CL_FREQ", "CROSS", "CPI", "TRADE"):
        assert expected in ids


# ── paths against written data ────────────────────────────────────────────


def test_a_hoisted_column_needs_no_hop(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.dsd).filter(q.dsd.agency == "ESTAT").ids() == ["CPI"]


def test_a_fan_out_finds_a_child(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.codelist).filter(q.codelist.codes.id == "CA").ids() == ["CL_AREA"]


def test_a_facet_narrows_to_dimensions(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.dsd).filter(q.dsd.dimensions.id == "REF_AREA").ids() == ["CPI"]


def test_a_path_through_a_fan_out(reg: sl.LocalRegistry) -> None:
    """`dsd -> dimensions -> concept -> id`, two hops past the fan-out."""
    assert reg.query(q.dsd).filter(q.dsd.dimensions.concept.id == "REF_AREA").ids() == ["CPI"]


def test_component_rooted_queries_reach_their_parent(reg: sl.LocalRegistry) -> None:
    ids = reg.query(q.component).filter(q.component.dsd.agency == "OECD").ids()
    assert ids == ["FREQ"]


def test_the_same_child_binder_against_real_rows(reg: sl.LocalRegistry) -> None:
    """`CPI` has a dimension that is both REF_AREA and CL_AREA-enumerated;
    no single dimension of `TRADE` is."""
    same = reg.query(q.dsd).filter(
        q.dsd.dimensions.any(
            q.component.id == "REF_AREA",
            q.component.codelist.id == "CL_AREA",
        )
    )
    assert same.ids() == ["CPI"]


def test_independent_children_match_more(reg: sl.LocalRegistry) -> None:
    both = reg.query(q.dsd).filter(
        q.dsd.dimensions.id == "FREQ",
        q.dsd.dimensions.codelist.id == "CL_AREA",
    )
    assert both.ids() == ["CPI"]


# ── terminals resolve real objects ────────────────────────────────────────


def test_collect_returns_artefacts(reg: sl.LocalRegistry) -> None:
    found = reg.query(q.codelist).filter(q.codelist.agency == "ESTAT").collect()
    assert len(found) == 1
    assert isinstance(found[0], LazyCodelist)
    assert found[0].id == "CL_AREA"


def test_one_returns_a_single_artefact(reg: sl.LocalRegistry) -> None:
    found = reg.query(q.dsd).filter(q.dsd.id == "CPI").one()
    assert isinstance(found, DataStructure)
    assert found.id == "CPI"
    assert found.dimensions.ids() == ["REF_AREA", "FREQ"]


def test_urns_do_not_resolve(reg: sl.LocalRegistry) -> None:
    urns = reg.query(q.dsd).urns()
    assert len(urns) == 2
    assert all(u.startswith("urn:") for u in urns)


def test_frame_returns_columns(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.dsd).frame()
    assert frame.height == 2
    assert "resource_id" in frame.columns


def test_count_and_exists(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.codelist).count() == 2
    assert reg.query(q.codelist).filter(q.codelist.agency == "ESTAT").exists() is True
    assert reg.query(q.codelist).filter(q.codelist.agency == "NOBODY").exists() is False


# ── laziness and laws survive the wiring ──────────────────────────────────


def test_the_query_is_still_lazy(reg: sl.LocalRegistry) -> None:
    query = reg.query(q.dsd).filter(q.dsd.agency == "ESTAT")
    assert "lazy" in repr(query)
    with pytest.raises(TypeError, match="not iterable"):
        iter(query)
    with pytest.raises(TypeError, match=r"no len\(\)"):
        len(query)


def test_filter_after_slice_is_still_refused(reg: sl.LocalRegistry) -> None:
    with pytest.raises(SliceError):
        reg.query(q.dsd).limit(1).filter(q.dsd.agency == "ESTAT")


def test_a_terminal_leaves_the_query_reusable(reg: sl.LocalRegistry) -> None:
    base = reg.query(q.dsd)
    assert base.count() == 2
    assert base.filter(q.dsd.agency == "ESTAT").count() == 1
    assert base.count() == 2


def test_a_closed_registry_refuses(reg: sl.LocalRegistry) -> None:
    """`query()` takes the connection through `_require_open`, like its peers."""
    reg.close()
    with pytest.raises(RuntimeError, match="is not open"):
        reg.query(q.dsd)


# ── terminals that resolve need a root that can be resolved ───────────────


def test_urns_reads_the_roots_own_urn_column(reg: sl.LocalRegistry) -> None:
    """It is `code_urn` on `sdmx.code`, not `urn`. Assuming the name was a
    binder error on every root but a maintainable."""
    urns = reg.query(q.code).urns()
    assert len(urns) == 4
    assert all(u.startswith("urn:") for u in urns)
    assert reg.query(q.concept).urns()


def test_codes_resolve_to_code_objects(reg: sl.LocalRegistry) -> None:
    """`q.code` has its own resolver, so `collect()` builds whole `Code`s —
    labels, parent and annotations — rather than refusing.

    Until #306 this returned `[]`: the resolver read the artefact spine, where
    a code URN finds nothing.
    """
    found = reg.query(q.code).filter(q.code.codelist.id == "CL_AREA").order_by(q.code.id).collect()
    assert [type(c).__name__ for c in found] == ["Code", "Code"]
    assert [c.id for c in found] == ["CA", "US"]
    assert found[0].name.get("en") == "CA"
    assert [a.title for a in found[0].annotations] == ["about CA"]
    assert list(found[1].annotations) == [], "only the first code carries one"


def test_a_resolver_preserves_the_querys_order(reg: sl.LocalRegistry) -> None:
    """The lookup is one batched `IN (...)`, which does not preserve order —
    so the resolver re-orders to the keys it was given."""
    ordered = reg.query(q.code).filter(q.code.codelist.id == "CL_AREA").order_by(q.code.id.desc())
    assert [c.id for c in ordered.collect()] == ["US", "CA"]


def test_collecting_a_root_with_no_resolver_is_refused(reg: sl.LocalRegistry) -> None:
    """A `dsd_component` row has no object to become, so the refusal names the
    terminals that do not need one."""
    with pytest.raises(TypeError, match=r"nothing knows how to build one.*\.ids\(\)"):
        reg.query(q.component).collect()
    for terminal in ("first", "one", "one_or_none"):
        with pytest.raises(TypeError, match="nothing knows how to build one"):
            getattr(reg.query(q.component), terminal)()


def test_a_component_has_no_urn_at_all(reg: sl.LocalRegistry) -> None:
    """`dsd_component` carries no identity of its own, so there is not even an
    identifier to hand back."""
    with pytest.raises(TypeError, match="have no URN"):
        reg.query(q.component).urns()
    assert sorted(reg.query(q.component).ids()) == ["FREQ", "FREQ", "REF_AREA"]
