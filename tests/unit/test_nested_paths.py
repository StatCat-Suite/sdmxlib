"""Nested items are identified by path, not by leaf id (#439).

A `Category`, a `HierarchicalCode` and a `MetadataAttribute` nest, and SDMX
names a nested item by its dotted path — every XSD identity constraint on them
is sibling-scoped, so a leaf id may repeat in another branch. Real registries
repeat it: Eurostat files ``hsw_ac`` under four parents of one category scheme,
OECD puts ``MR`` at a hierarchy's root and under ``TYPO_METRO``, BIS numbers
its nodes per level.

Every test here builds that shape and asks one layer what it means. Each pairs
the repeat with something only path identity gets right — a different name or
annotation on the two same-id items — because two *identical* items would
survive a leaf-keyed implementation that silently kept one of them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import attrs
import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats import parse, serialize
from sdmxlib.formats.sdmx_ml._common import hierarchy_containers
from sdmxlib.formats.sdmx_ml21.reader import _helpers as reader_21
from sdmxlib.formats.sdmx_ml21.writer import _helpers as writer_21
from sdmxlib.model._items import item_ref
from sdmxlib.model.annotations import Annotation
from sdmxlib.model.category import Categorisation, Category, CategoryScheme
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.hierarchy import HierarchicalCode, HierarchicalCodelist, Hierarchy
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import SdmxConformanceError, ValidationError, check_serialisable
from sdmxlib.query.accessors import q
from tests.support.factories import code_ref, concept_ref
from tests.support.narrow import present
from tests.support.xsd import SchemaVersion, assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit


# ── the three shapes, each repeating a leaf in another branch ─────────────


def _categories() -> CategoryScheme:
    """``HSW`` under both ``HEALTH`` and ``LABOUR``, named differently."""
    return CategoryScheme(
        id="CS",
        maintainer="AG",
        name={"en": "Themes"},
        categories=[
            Category(
                id="HEALTH",
                name={"en": "Health"},
                categories=[Category(id="HSW", name={"en": "Health and safety at work"})],
            ),
            Category(
                id="LABOUR",
                name={"en": "Labour"},
                categories=[
                    Category(
                        id="HSW",
                        name={"en": "Health and safety, labour view"},
                        annotations=[Annotation(id="NOTE", type="NOTE", title="labour")],
                    )
                ],
            ),
        ],
    )


def _code_ref(code_id: str) -> Ref[Code]:
    urn = SdmxUrn.make(
        Code, package="codelist", artefact_class="Code", agency="AG", id="CL_GEO", version="1.0", item_id=code_id
    )
    return Ref[Code].unresolved(urn)


def _hierarchy() -> Hierarchy:
    """OECD's shape: ``MR`` at a root and again under ``TYPO_METRO``."""
    return Hierarchy(
        id="H_GEO",
        maintainer="AG",
        version="1.0",
        name={"en": "Geography"},
        tree=[
            HierarchicalCode(id="MR", code=_code_ref("MR")),
            HierarchicalCode(
                id="TYPO_METRO",
                code=_code_ref("TYPO_METRO"),
                children=[
                    HierarchicalCode(
                        id="MR",
                        code=_code_ref("MR"),
                        annotations=[Annotation(id="WHERE", type="WHERE", title="typology")],
                    )
                ],
            ),
        ],
    )


def _msd() -> MetadataStructure:
    """BIS's shape: ``URL`` under both ``IMAGE`` and ``LINK``, bounded differently."""
    return MetadataStructure(
        id="MSD",
        maintainer="AG",
        name={"en": "News"},
        attributes=[
            MetadataAttribute(
                id="IMAGE",
                concept=concept_ref("IMAGE"),
                children=[MetadataAttribute(id="URL", concept=concept_ref("URL"), max_occurs=1)],
            ),
            MetadataAttribute(
                id="LINK",
                concept=concept_ref("LINK"),
                children=[MetadataAttribute(id="URL", concept=concept_ref("URL"), max_occurs=None)],
            ),
        ],
    )


# ── the model ─────────────────────────────────────────────────────────────


def test_a_leaf_repeated_in_another_branch_is_valid() -> None:
    """Each builds, and every rule runs as it is built (#492)."""
    _categories()
    _hierarchy()
    _msd()


def test_each_repeat_is_its_own_item_by_path() -> None:
    cs = _categories()
    assert cs["HEALTH.HSW"].name["en"].text == "Health and safety at work"
    assert cs["LABOUR.HSW"].name["en"].text == "Health and safety, labour view"
    assert [cs.path_of(c) for c in cs.by_id("HSW")] == ["HEALTH.HSW", "LABOUR.HSW"]
    assert _msd()["LINK.URL"].max_occurs is None
    assert _hierarchy().depths() == {"MR": 0, "TYPO_METRO": 0, "TYPO_METRO.MR": 1}


@pytest.mark.parametrize(
    "build",
    [
        lambda: CategoryScheme(
            id="CS",
            maintainer="AG",
            name={"en": "cs"},
            categories=[Category(id="A", name={"en": "a"}), Category(id="A", name={"en": "again"})],
        ),
        lambda: Hierarchy(
            id="H",
            maintainer="AG",
            name={"en": "h"},
            tree=[HierarchicalCode(id="A", code=code_ref("A")), HierarchicalCode(id="A", code=code_ref("A"))],
        ),
        lambda: MetadataStructure(
            id="MSD",
            maintainer="AG",
            name={"en": "m"},
            attributes=[
                MetadataAttribute(
                    id="P",
                    concept=concept_ref("P"),
                    children=[
                        MetadataAttribute(id="A", concept=concept_ref("A")),
                        MetadataAttribute(id="A", concept=concept_ref("A")),
                    ],
                )
            ],
        ),
    ],
    ids=["category", "hierarchy", "metadata-attribute"],
)
def test_a_repeat_among_siblings_is_refused(build: Callable[[], object]) -> None:
    """Discrimination: the one repeat no SDMX version allows, because a path
    cannot tell the two apart. Refused as it is built (#492)."""
    with pytest.raises(ValidationError, match="two siblings share the id 'A'"):
        build()


# ── the formats ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("version", ["3.0", "3.1"])
def test_3x_refuses_a_hierarchy_whose_node_id_repeats(version: str) -> None:
    """`Hierarchiy_UniqueHierarchicalCode` is descendant-wide in 3.0 and 3.1."""
    with pytest.raises(ValidationError, match=re.escape("MR at MR, TYPO_METRO.MR")):
        check_serialisable(StructureMessage(hierarchies=[_hierarchy()]), version=version)


def test_3x_writes_a_hierarchy_whose_ids_do_not_repeat() -> None:
    """Discrimination for the refusal: the same tree with the repeat renamed."""
    h = Hierarchy(
        id="H_GEO",
        maintainer="AG",
        name={"en": "Geography"},
        tree=[
            HierarchicalCode(id="MR", code=_code_ref("MR")),
            HierarchicalCode(
                id="TYPO_METRO",
                code=_code_ref("TYPO_METRO"),
                children=[HierarchicalCode(id="MR2", code=_code_ref("MR"))],
            ),
        ],
    )
    check_serialisable(StructureMessage(hierarchies=[h]), version="3.0")


def test_2_1_writes_and_reads_back_a_repeated_node() -> None:
    """2.1's constraints are sibling-scoped, so 2.1 is where this hierarchy lives."""
    written = serialize(StructureMessage(hierarchies=[_hierarchy()]), version="2.1")
    assert_valid_sdmx_ml(written, compile_structure_schema("2.1"))
    back = parse(written, version="2.1")
    assert isinstance(back, StructureMessage)
    h = back.hierarchies[0]
    # By annotation: 2.1's `HierarchicalCodeType` is identifiable, not nameable.
    assert [a.title for a in h["TYPO_METRO.MR"].annotations] == ["typology"]
    assert list(h["MR"].annotations) == []
    assert h.broader_map() == _hierarchy().broader_map()


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1"])
def test_a_category_scheme_with_a_repeat_round_trips(version: SchemaVersion) -> None:
    written = serialize(StructureMessage(category_schemes=[_categories()]), version=version)
    assert_valid_sdmx_ml(written, compile_structure_schema(version))
    back = parse(written, version=version)
    assert isinstance(back, StructureMessage)
    assert back.category_schemes[0]["LABOUR.HSW"].name["en"].text == "Health and safety, labour view"


def test_a_2_1_hierarchical_code_reference_names_the_hierarchy_by_container() -> None:
    """2.1 nests a Hierarchy in an HCL and its node reference names both; the
    model's Hierarchy is the container, so it is the URN's id, as the reader
    names it: ``<hierarchy>@<HCL>`` (#421), or bare in an HCL of its own id."""
    ref = b'<Ref agencyID="AG" maintainableParentID="%s" maintainableParentVersion="1.0" '
    ref += b'containerID="H_GEO" id="TYPO_METRO.MR" class="HierarchicalCode" package="codelist"/>'
    urn = present(reader_21.ref_el_to_urn(etree.fromstring(ref % b"HCL")))
    assert (urn.id, urn.item_id, urn.version) == ("H_GEO@HCL", "TYPO_METRO.MR", "1.0")
    assert present(reader_21.ref_el_to_urn(etree.fromstring(ref % b"H_GEO"))).id == "H_GEO"


def _node_urn(hierarchy_id: str, item_id: str, version: str = "1.0") -> SdmxUrn[HierarchicalCode]:
    return SdmxUrn.make(
        HierarchicalCode,
        package="codelist",
        artefact_class="HierarchicalCode",
        agency="AG",
        id=hierarchy_id,
        version=version,
        item_id=item_id,
    )


def test_a_2_1_hierarchical_code_reference_is_written_with_its_container() -> None:
    """The reference names the HCL, and the hierarchy by its id inside it (#421)."""
    urn = _node_urn("H_GEO@HCL", "TYPO_METRO.MR")
    hierarchy_urn = str(attrs.evolve(_hierarchy(), id="H_GEO@HCL").urn)
    with hierarchy_containers({hierarchy_urn: "HCL"}):
        declared = writer_21.urn_to_ref_el(urn)
    synthesised = writer_21.urn_to_ref_el(urn)
    assert (declared.get("maintainableParentID"), declared.get("containerID")) == ("HCL", "H_GEO")
    # No declared container: the 2.1 writer wraps the hierarchy in one carrying
    # its own id, so that is what the reference names.
    assert (synthesised.get("maintainableParentID"), synthesised.get("containerID")) == ("H_GEO@HCL", "H_GEO@HCL")
    assert declared.get("id") == "TYPO_METRO.MR"
    # And what the reader makes of each is the hierarchy's id again.
    assert present(reader_21.ref_el_to_urn(declared)).id == "H_GEO@HCL"
    assert present(reader_21.ref_el_to_urn(synthesised)).id == "H_GEO@HCL"


def test_the_2_1_writer_scopes_the_container_map_to_the_write() -> None:
    """A declared HCL claims its hierarchy for the duration of one write only."""
    hierarchy = attrs.evolve(_hierarchy(), id="H_GEO@HCL")
    hcl = HierarchicalCodelist(
        id="HCL", maintainer="AG", name={"en": "hcl"}, hierarchies=(Ref[object].unresolved(hierarchy.urn),)
    )
    serialize(StructureMessage(hierarchies=[hierarchy], hierarchical_codelists=[hcl]), version="2.1")
    assert writer_21.urn_to_ref_el(_node_urn("H_GEO@HCL", "MR")).get("maintainableParentID") == "H_GEO@HCL"


@pytest.mark.parametrize("version", ["1.0", "2.0"])
def test_a_2_1_write_names_a_node_by_the_container_it_wrote(version: str) -> None:
    """End to end: the HCL `write` declares is the one its node references name, at its version.

    At ``1.0`` an omitted ``maintainableParentVersion`` reads back the same,
    since that is the schema's default (#476); ``2.0`` tells them apart.
    """
    hierarchy = attrs.evolve(_hierarchy(), id="H_GEO@HCL", version=version)
    hcl = HierarchicalCodelist(
        id="HCL",
        maintainer="AG",
        version=version,
        name={"en": "hcl"},
        hierarchies=(Ref[object].unresolved(hierarchy.urn),),
    )
    node = _categorisation("CAT_NODE", _node_urn("H_GEO@HCL", "MR", version))
    msg = StructureMessage(hierarchies=[hierarchy], hierarchical_codelists=[hcl], categorisations=[node])
    written = serialize(msg, version="2.1")
    (target,) = etree.fromstring(written).iter("{*}Target")
    ref = present(target.find("Ref"))
    assert (ref.get("maintainableParentID"), ref.get("containerID"), ref.get("maintainableParentVersion")) == (
        "HCL",
        "H_GEO",
        version,
    )
    back = parse(written, version="2.1")
    assert isinstance(back, StructureMessage)
    assert str(present(back.categorisations[0].target).urn) == str(present(node.target).urn)


# ── 2.1 report structures (merge identical, refuse different) ─────────────


def _msd_21(second: bytes) -> bytes:
    """The fixture MSD with *second* added as another root of ``REPORT_B``."""
    doc = (Path(__file__).parents[1] / "fixtures/sdmx_ml21/with_metadata_structure.xml").read_bytes()
    marker = b'<str:ReportStructure id="REPORT_B">'
    return doc.replace(marker, marker + second)


_CONTACT = (
    b'<str:MetadataAttribute id="CONTACT"><str:ConceptIdentity>'
    b'<Ref agencyID="AG" maintainableParentID="CS" maintainableParentVersion="1.0" id="CONTACT" '
    b'package="conceptscheme" class="Concept"/></str:ConceptIdentity>'
    b'<str:MetadataAttribute id="EMAIL"%s><str:ConceptIdentity>'
    b'<Ref agencyID="AG" maintainableParentID="CS" maintainableParentVersion="1.0" id="EMAIL" '
    b'package="conceptscheme" class="Concept"/></str:ConceptIdentity></str:MetadataAttribute>'
    b"</str:MetadataAttribute>"
)


def test_two_reports_declaring_one_attribute_identically_share_it() -> None:
    msg = parse(_msd_21(_CONTACT % b""), version="2.1")
    assert isinstance(msg, StructureMessage)
    msd = msg.metadata_structures[0]
    assert [a.id for a in msd.attributes] == ["CONTACT", "QUALITY"]
    assert [r.attribute_ids for r in msd.report_structures] == [("CONTACT",), ("CONTACT", "QUALITY")]


def test_two_reports_declaring_one_attribute_differently_are_refused() -> None:
    """The single 3.0-shaped list cannot hold two `CONTACT`s; keeping the last
    — what concatenating did — would answer for both reports with one."""
    different = _msd_21(_CONTACT % b' maxOccurs="unbounded"')
    with pytest.raises(SdmxConformanceError) as refused:
        parse(different, version="2.1")
    # Whole, not a fragment: the element and the reason are the message (#458).
    assert str(refused.value).endswith(
        "<MetadataAttribute CONTACT>: report structures 'REPORT_A' and 'REPORT_B' each declare a root "
        "attribute 'CONTACT', differently. Valid SDMX-ML 2.1, but sdmxlib holds an MSD's attributes in "
        "SDMX 3.0's single list, where one id is one attribute"
    )
    lenient = parse(different, version="2.1", strict=False)
    assert isinstance(lenient, StructureMessage)
    assert [r.artefact_class for r in lenient.refused] == ["MetadataStructure"]


# ── the store and the query layer ─────────────────────────────────────────


def test_the_store_keeps_both_repeats_and_their_own_annotations() -> None:
    with sl.LocalRegistry(":memory:") as reg:
        for scheme in (_categories(), _hierarchy(), _msd()):
            reg.add(scheme)
        cs = reg.get(_categories().urn, eager=True)
        assert isinstance(cs, CategoryScheme)
        assert cs == _categories()
        assert [a.title for a in cs["LABOUR.HSW"].annotations] == ["labour"]
        assert list(cs["HEALTH.HSW"].annotations) == []
        assert present(reg.get(_msd().urn, expect=MetadataStructure, eager=True)) == _msd()
        assert present(reg.get(_hierarchy().urn, expect=Hierarchy, eager=True)) == _hierarchy()


def test_the_lazy_hierarchy_answers_by_path() -> None:
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(_hierarchy())
        lazy = present(reg.get(_hierarchy().urn))
        assert [a.title for a in lazy["TYPO_METRO.MR"].annotations] == ["typology"]
        assert "TYPO_METRO.MR" in lazy
        assert "MR" in lazy, "a root, by its bare id"
        assert sorted([a.title for a in n.annotations] for n in lazy.by_id("MR").collect()) == [[], ["typology"]]
        assert lazy.materialize() == _hierarchy(), "including declaration order"


def test_the_query_language_tells_the_repeats_apart() -> None:
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(_hierarchy())
        found = reg.query(q.hcode).filter(q.hcode.parent.nested_id == "TYPO_METRO")
        assert found.ids() == ["MR"]
        assert reg.query(q.hcode).filter(q.hcode.id == "MR").count() == 2
        assert reg.query(q.hcode).filter(q.hcode.ancestors.id == "TYPO_METRO").count() == 1


def _categorisation(cat_id: str, target: SdmxUrn[Any]) -> Categorisation:
    return Categorisation(
        id=cat_id,
        maintainer="AG",
        name={"en": cat_id},
        source=Ref[object].unresolved(_codelist().urn),
        target=Ref[object].unresolved(target),
    )


def _codelist() -> Codelist:
    return Codelist(id="CL_GEO", maintainer="AG", name={"en": "geo"}, codes=[Code(id="MR", name={"en": "MR"})])


def _target_of(reg: sl.LocalRegistry, categorisation_id: str) -> object:
    """What a stored categorisation's target resolves to on `get`."""
    stored = reg.get(Categorisation.urn_for(agency="AG", id=categorisation_id, version="1.0"))
    assert isinstance(stored, Categorisation)
    return present(stored.target)()


def test_a_categorisation_joins_and_resolves_to_the_category_its_path_names() -> None:
    """The join is exact on the category URN, which carries the path — so the
    leaf spelling joins nothing, and each path joins its own category."""
    cs = _categories()
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(_codelist())
        reg.add(cs)
        reg.add(_categorisation("BY_LABOUR", cs.ref_to("LABOUR.HSW").urn))
        under = reg.query(q.categorisation).select(path=q.categorisation.category.nested_id).collect()
        assert under["path"].to_list() == ["LABOUR.HSW"]
        target = _target_of(reg, "BY_LABOUR")
        assert isinstance(target, Category)
        assert target.name["en"].text == "Health and safety, labour view"


def test_the_store_resolves_hierarchy_node_and_metadata_attribute_refs() -> None:
    """It resolved only `Concept` and `Code` item refs, so a reference to any
    other item stayed unresolved in a `LocalRegistry` while an
    `InMemoryRegistry` resolved it."""
    h, msd = _hierarchy(), _msd()
    with sl.LocalRegistry(":memory:") as reg:
        for artefact in (_codelist(), h, msd):
            reg.add(artefact)
        reg.add(_categorisation("ON_NODE", item_ref(h, "TYPO_METRO.MR", HierarchicalCode).urn))
        reg.add(_categorisation("ON_SLOT", msd.ref_to("LINK.URL").urn))
        node = _target_of(reg, "ON_NODE")
        slot = _target_of(reg, "ON_SLOT")
        assert isinstance(node, HierarchicalCode)
        assert [a.title for a in node.annotations] == ["typology"], "the nested MR, not the root"
        assert isinstance(slot, MetadataAttribute)
        assert slot.max_occurs is None
