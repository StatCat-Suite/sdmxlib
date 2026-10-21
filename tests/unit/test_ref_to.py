"""`ref_to` on every item-bearing scheme — #404's constructive half (#407).

#404 and #405 made the library *read* item references: `validate_references`
resolves them and the readers index every item kind. Producing one had no
equivalent. `ConceptScheme.ref_to` was the only builder in `src/`, so anyone
authoring a 3.0 `Hierarchy` — where `HierarchicalCode.code` **must** name a
`Code` and `HierarchyAssociation.linked_object` **must** name a component —
assembled the URN from six keyword arguments, three of which restate the parent.

The failure that invites is specific: pass the wrong parent's agency or version
and you get a URN naming an artefact that does not exist. Since #404 that is
correctly reported as a broken reference, which is a better failure than the
silence before it and still a failure a builder makes unreachable. So the last
test here is the one that matters most: every URN `ref_to` builds resolves under
`validate_references`.
"""

from __future__ import annotations

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.model.category import Categorisation, Category, CategoryScheme
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.datastructure import AttachmentLevel, Attribute, Dimension, Measure, TimeDimension
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, HierarchyAssociation
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.organisation import Agency, AgencyScheme, DataProvider, DataProviderScheme
from sdmxlib.model.validation import validate_references
from sdmxlib.storage.lazy import LazyCodelist
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit

A, V = "AGENCY", "1.0.0"


@pytest.fixture
def codelist() -> Codelist:
    return Codelist(
        id="CL_AREA",
        maintainer=A,
        version=V,
        name={"en": "Area"},
        codes=[Code(id="CA", name={"en": "Canada"}), Code(id="ON", name={"en": "Ontario"})],
    )


@pytest.fixture
def concepts() -> sl.ConceptScheme:
    return sl.ConceptScheme(
        id="CS_COMMON",
        maintainer=A,
        version=V,
        name={"en": "Concepts"},
        concepts=[sl.Concept(id=i, name={"en": i}) for i in ("REF_AREA", "TIME_PERIOD", "OBS_STATUS", "OBS_VALUE")],
    )


@pytest.fixture
def dsd(concepts: sl.ConceptScheme) -> sl.DataStructure:
    """Built with `ref_to` itself — every component carries a concept identity,
    which `validate` requires and which is the same item reference this feature
    is about."""
    return sl.DataStructure(
        id="DSD",
        maintainer=A,
        version=V,
        name={"en": "DSD"},
        dimensions=[Dimension(id="REF_AREA", concept=concepts.ref_to("REF_AREA"))],
        time_dimension=TimeDimension(concept=concepts.ref_to("TIME_PERIOD")),
        attributes=[
            Attribute(id="OBS_STATUS", concept=concepts.ref_to("OBS_STATUS"), attachment=AttachmentLevel.OBSERVATION)
        ],
        measures=[Measure(id="OBS_VALUE", concept=concepts.ref_to("OBS_VALUE"))],
    )


# ── the URN is the scheme's own coordinates ──────────────────────────────


def test_a_code_ref_names_its_codelist(codelist: Codelist) -> None:
    ref = codelist.ref_to("CA")
    assert str(ref.urn) == "urn:sdmx:org.sdmx.infomodel.codelist.Code=AGENCY:CL_AREA(1.0.0).CA"
    assert isinstance(ref(), Code), "and it comes back resolved, not dangling"
    assert ref().id == "CA"


def test_the_urn_cannot_disagree_with_the_scheme(codelist: Codelist) -> None:
    """The defect `ref_to` exists to make unreachable.

    A hand-built URN takes agency, id and version as three separate arguments,
    so nothing stops them naming a different codelist than the one holding the
    code. Here they are read off `self`.
    """
    urn = codelist.ref_to("ON").urn
    assert (urn.agency, urn.id, urn.version) == (codelist.agency_id, codelist.id, codelist.version)


@pytest.mark.parametrize("missing", ["ZZ", "ca", ""])
def test_an_absent_id_raises_naming_the_scheme(codelist: Codelist, missing: str) -> None:
    """Discrimination: `ref_to` must not invent a URN for an item that is not
    there. Case matters — `ca` is not `CA` — because SDMX ids are
    case-sensitive and a silently-accepted lowercase id would produce exactly
    the dangling reference this feature removes."""
    with pytest.raises(KeyError, match="Codelist AGENCY:CL_AREA"):
        codelist.ref_to(missing)


# ── every scheme kind ────────────────────────────────────────────────────


def test_a_category_ref_resolves_at_any_depth() -> None:
    """Categories nest, and a reference to a nested one is ordinary — a
    `Categorisation` names a nested category as readily as a root, by path."""
    scheme = CategoryScheme(
        id="CS",
        maintainer=A,
        version=V,
        name={"en": "cs"},
        categories=[Category(id="ECON", name={"en": "e"}, categories=[Category(id="GDP", name={"en": "g"})])],
    )
    assert isinstance(scheme.ref_to("ECON")(), Category)
    nested = scheme.ref_to("ECON.GDP")
    assert nested().id == "GDP"
    assert str(nested.urn) == "urn:sdmx:org.sdmx.infomodel.categoryscheme.Category=AGENCY:CS(1.0.0).ECON.GDP"
    with pytest.raises(KeyError):
        scheme.ref_to("GDP")  # a leaf is not a path (#439)


def test_an_organisation_scheme_ref() -> None:
    # Version 1.0: every format fixes an organisation scheme's (#416, #492).
    scheme = AgencyScheme(
        id="AGENCIES", maintainer=A, version="1.0", name={"en": "a"}, agencies=[Agency(id="ESTAT", name={"en": "e"})]
    )
    ref = scheme.ref_to("ESTAT")
    assert str(ref.urn) == "urn:sdmx:org.sdmx.infomodel.base.Agency=AGENCY:AGENCIES(1.0).ESTAT"
    assert isinstance(ref(), Agency)

    providers = DataProviderScheme(
        id="DATA_PROVIDERS",
        maintainer=A,
        version="1.0",
        name={"en": "p"},
        providers=[DataProvider(name={"en": "ECB"}, id="ECB")],
    )
    assert isinstance(providers.ref_to("ECB")(), DataProvider)


def test_a_metadata_attribute_ref_reaches_a_nested_one() -> None:
    msd = MetadataStructure(
        id="MSD",
        maintainer=A,
        version=V,
        name={"en": "msd"},
        attributes=[
            MetadataAttribute(
                id="CONTACT",
                children=[MetadataAttribute(id="EMAIL", concept=concept_ref("EMAIL"))],
                concept=concept_ref("CONTACT"),
            )
        ],
    )
    assert isinstance(msd.ref_to("CONTACT")(), MetadataAttribute)
    nested = msd.ref_to("CONTACT.EMAIL")
    assert nested().id == "EMAIL"
    assert str(nested.urn).endswith("MSD(1.0.0).CONTACT.EMAIL")


def test_a_concept_ref_still_works() -> None:
    """The one builder that already existed, now on the shared path. Its URN
    must not have moved."""
    scheme = sl.ConceptScheme(
        id="CS", maintainer=A, version=V, name={"en": "cs"}, concepts=[sl.Concept(id="FREQ", name={"en": "f"})]
    )
    assert str(scheme.ref_to("FREQ").urn) == ("urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=AGENCY:CS(1.0.0).FREQ")


# ── DataStructure dispatches across six lists ────────────────────────────


@pytest.mark.parametrize(
    ("component_id", "expected", "urn_class"),
    [
        ("REF_AREA", Dimension, "Dimension"),
        ("TIME_PERIOD", TimeDimension, "TimeDimension"),
        ("OBS_STATUS", Attribute, "Attribute"),
        ("OBS_VALUE", Measure, "Measure"),
    ],
)
def test_the_dsd_finds_a_component_in_any_list(
    dsd: sl.DataStructure, component_id: str, expected: type, urn_class: str
) -> None:
    """One call site, four lists — including `time_dimension`, which is a
    *single* component rather than a list and which an accessor table could not
    have named (#404's lesson, applied here)."""
    ref = dsd.ref_to(component_id)
    assert isinstance(ref(), expected)
    assert str(ref.urn) == f"urn:sdmx:org.sdmx.infomodel.datastructure.{urn_class}=AGENCY:DSD(1.0.0).{component_id}"


def test_an_absent_component_names_the_lists_searched(dsd: sl.DataStructure) -> None:
    with pytest.raises(KeyError, match="searched dimensions"):
        dsd.ref_to("NOPE")


def test_duplicate_component_ids_are_a_validation_issue() -> None:
    """What makes the dispatch unambiguous, enforced on the model.

    `storage.schema` has always keyed `dsd_component` on
    ``(dsd_urn, component_id)``, so a DSD with a dimension and a measure sharing
    an id could never round-trip through DuckDB — but nothing checked the
    artefact, so the collision surfaced as a constraint violation on write.
    """
    with pytest.raises(sl.ValidationError) as raised:
        sl.DataStructure(
            id="D",
            maintainer=A,
            version=V,
            name={"en": "d"},
            dimensions=[Dimension(id="X", concept=concept_ref("X"))],
            measures=[Measure(id="X", concept=concept_ref("X"))],
        )
    assert any("unique across the whole structure" in issue.message for issue in raised.value.issues)


def test_a_valid_dsd_still_validates(dsd: sl.DataStructure) -> None:
    """The discrimination check for the test above: the new rule must not fire
    on a structure whose ids differ."""
    attrs.evolve(dsd)  # rebuilt: every rule runs again, and none fires


def test_a_stored_codelist_builds_the_same_ref_without_materialising() -> None:
    """`LazyCodelist` keeps pace with `Codelist` (the parity guard in
    `test_lazy_return_types` requires it), and does so through its own
    subscript — one targeted query rather than reading every code to check one
    exists. The URN must be identical to the eager class's."""
    codelist = Codelist(
        id="CL_AREA", maintainer=A, version=V, name={"en": "Area"}, codes=[Code(id="CA", name={"en": "Canada"})]
    )
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(codelist)
        lazy = registry.get(codelist.urn)
        assert isinstance(lazy, LazyCodelist)
        assert str(lazy.ref_to("CA").urn) == str(codelist.ref_to("CA").urn)
        assert lazy.ref_to("CA")().id == "CA"
        with pytest.raises(KeyError, match="LazyCodelist AGENCY:CL_AREA"):
            lazy.ref_to("ZZ")


# ── the property that ties this to #404 ──────────────────────────────────


def test_every_ref_to_urn_resolves_under_validate_references(
    codelist: Codelist, dsd: sl.DataStructure, concepts: sl.ConceptScheme
) -> None:
    """The whole point, stated as the check #404 added.

    A hand-built URN that names the wrong parent is reported broken. Every URN
    `ref_to` builds must survive the closed-world check — which is what says the
    producing and consuming halves agree rather than merely coexist.
    """
    hierarchy = Hierarchy(
        id="HR",
        maintainer=A,
        version=V,
        name={"en": "h"},
        tree=[
            HierarchicalCode(
                id="CA",
                code=codelist.ref_to("CA"),
                children=[HierarchicalCode(id="ON", code=codelist.ref_to("ON"))],
            )
        ],
    )
    association = HierarchyAssociation(
        id="HA",
        maintainer=A,
        version=V,
        name={"en": "ha"},
        linked_hierarchy=sl.Ref.to(hierarchy),
        linked_object=dsd.ref_to("REF_AREA"),
    )
    scheme = CategoryScheme(
        id="CS", maintainer=A, version=V, name={"en": "cs"}, categories=[Category(id="ECON", name={"en": "e"})]
    )
    flow = sl.Dataflow(id="DF", maintainer=A, version=V, name={"en": "df"}, structure=sl.Ref.to(dsd))
    categorisation = Categorisation(
        id="CAT",
        maintainer=A,
        version=V,
        name={"en": "cat"},
        source=sl.Ref.to(flow),
        target=scheme.ref_to("ECON"),
    )
    msg = sl.StructureMessage(
        codelists=[codelist],
        concept_schemes=[concepts],
        data_structures=[dsd],
        dataflows=[flow],
        category_schemes=[scheme],
        hierarchies=[hierarchy],
        hierarchy_associations=[association],
        categorisations=[categorisation],
    )
    assert validate_references(msg) is None
    assert sl.to_xml(msg, version="3.0"), "and the message it builds serialises"
