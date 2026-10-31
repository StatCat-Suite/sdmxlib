"""A metadata set conforms to its metadataflow and MSD, checked as a registry adds it (GH-29).

SDMX 3.1 Section 2, §7.4.2.1: "the MetadataSet must conform to the
MetadataStructureDefinition associated to the Metadataflow or
MetadataProvisionAgreement" (p. 101), and its targets "must be part of those
specified in the relevant Metadataflow or MetadataProvisionAgreement" (p. 102).
A set and its structures arrive in different messages, so a registry, which
holds both, checks a set as it is added, and warns (#481). A set added before
its metadataflow is not checked.
"""

import warnings
from collections.abc import Callable

import pytest

import sdmxlib as sl
from sdmxlib.model.errors import ImRuleWarning
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import DataType, Facet
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit

_FLOW_URN = "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=AG:DF(1.0)"
_MSD = MetadataStructure(
    id="MSD",
    maintainer="AG",
    name={"en": "msd"},
    attributes=[
        MetadataAttribute(
            id="SECTION",
            concept=concept_ref("SECTION"),
            min_occurs=1,
            is_presentational=True,
            children=[MetadataAttribute(id="TEXT", concept=concept_ref("TEXT"), max_occurs=2)],
        ),
        MetadataAttribute(id="NOTE", concept=concept_ref("NOTE")),
        MetadataAttribute(
            id="SHARE",
            concept=concept_ref("SHARE"),
            representation=Facet(type=DataType.Decimal, max_value=100, decimals=1),
        ),
        MetadataAttribute(
            id="LABEL", concept=concept_ref("LABEL"), representation=Facet(max_length=4, is_multilingual=True)
        ),
        MetadataAttribute(id="BODY", concept=concept_ref("BODY"), representation=Facet(type=DataType.XHTML)),
    ],
)
_MDF = Metadataflow(
    id="MDF",
    maintainer="AG",
    name={"en": "mdf"},
    structure=Ref.to(_MSD),
    targets=("urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=AG:*(*)",),
)
_MPA = MetadataProvisionAgreement(id="MPA", maintainer="AG", name={"en": "mpa"}, metadataflow=Ref.to(_MDF))


def _set(*attributes: ReportedAttribute, target: str = _FLOW_URN, through_agreement: bool = False) -> MetadataSet:
    structure: Ref[Metadataflow] | Ref[MetadataProvisionAgreement] = Ref.to(_MPA) if through_agreement else Ref.to(_MDF)
    return MetadataSet(
        id="MDS",
        maintainer="AG",
        name={"en": "mds"},
        structure=structure,
        targets=[Ref[object].unresolved(sl.SdmxUrn.parse(target))],
        attributes=list(attributes),
    )


_CONFORMING = (
    ReportedAttribute(id="SECTION", children=[ReportedAttribute(id="TEXT", value="a")]),
    ReportedAttribute(id="SHARE", value="99.5"),
    ReportedAttribute(id="LABEL", value={"en": "Area", "fr": "Aire"}),
    ReportedAttribute(id="BODY", value={"en": "<p>longer than four characters</p>"}),
)


def _in_memory(artefacts: list[object]) -> None:
    reg = sl.InMemoryRegistry()
    reg.add_all([a for a in artefacts if isinstance(a, sl.MaintainableArtefact)])


def _local(artefacts: list[object]) -> None:
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all([a for a in artefacts if isinstance(a, sl.MaintainableArtefact)])


def _one_by_one(artefacts: list[object]) -> None:
    reg = sl.InMemoryRegistry()
    for artefact in artefacts:
        if isinstance(artefact, sl.MaintainableArtefact):
            reg.add(artefact)


_ADDS: list[Callable[[list[object]], None]] = [_in_memory, _local, _one_by_one]


def _warnings(add: Callable[[list[object]], None], metadata_set: MetadataSet) -> list[str]:
    with pytest.warns(ImRuleWarning) as caught:
        add([_MSD, _MDF, _MPA, metadata_set])
    return [
        issue.message
        for warning in caught
        if isinstance(warning.message, ImRuleWarning)
        for issue in warning.message.issues
    ]


@pytest.mark.parametrize("add", _ADDS, ids=["in-memory", "local", "add"])
@pytest.mark.parametrize("through_agreement", [False, True], ids=["flow", "agreement"])
def test_a_conforming_set_adds_quietly(add: Callable[[list[object]], None], through_agreement: bool) -> None:
    """The discrimination check: each warning below comes from one change to this set."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", ImRuleWarning)
        add([_MSD, _MDF, _MPA, _set(*_CONFORMING, through_agreement=through_agreement)])


@pytest.mark.parametrize("add", _ADDS, ids=["in-memory", "local", "add"])
def test_a_target_outside_the_flows_warns(add: Callable[[list[object]], None]) -> None:
    other = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:CL(1.0)"
    (message,) = _warnings(add, _set(*_CONFORMING, target=other))
    assert message.startswith(f"targets {other}, which none of")


def test_an_attribute_the_msd_lacks_warns() -> None:
    (message,) = _warnings(_in_memory, _set(*_CONFORMING, ReportedAttribute(id="EXTRA", value="x")))
    assert message.startswith("reports EXTRA, which")


@pytest.mark.parametrize(
    ("attributes", "fragment"),
    [
        ((), "reports SECTION 0 times"),
        (
            (ReportedAttribute(id="SECTION", children=[ReportedAttribute(id="TEXT", value=v) for v in "abc"]),),
            "reports TEXT 3 times",
        ),
        ((*_CONFORMING, ReportedAttribute(id="NOTE"), ReportedAttribute(id="NOTE")), "reports NOTE 2 times"),
    ],
    ids=["mandatory-missing", "nested-too-many", "too-many"],
)
def test_an_occurrence_the_msd_does_not_allow_warns(attributes: tuple[ReportedAttribute, ...], fragment: str) -> None:
    (message,) = _warnings(_in_memory, _set(*attributes))
    assert message.startswith(fragment)


def test_a_value_on_a_presentational_attribute_warns() -> None:
    attribute = ReportedAttribute(id="SECTION", value="x", children=[ReportedAttribute(id="TEXT", value="a")])
    (message,) = _warnings(_in_memory, _set(attribute))
    assert message.startswith("gives SECTION a value")


def test_a_set_added_before_its_flow_is_not_checked() -> None:
    reg = sl.InMemoryRegistry()
    with warnings.catch_warnings():
        warnings.simplefilter("error", ImRuleWarning)
        reg.add(_set(ReportedAttribute(id="EXTRA", value="x")))


def test_a_value_its_text_format_does_not_allow_warns() -> None:
    attributes = [a for a in _CONFORMING if a.id != "SHARE"]
    (message,) = _warnings(_in_memory, _set(*attributes, ReportedAttribute(id="SHARE", value="100.5")))
    assert message == (
        "reports '100.5', which is above maxValue 100 in the text format "
        "urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataStructure=AG:MSD(1.0) gives SHARE"
    )


def test_each_language_of_a_multilingual_value_meets_its_text_format() -> None:
    attributes = [a for a in _CONFORMING if a.id != "LABEL"]
    label = ReportedAttribute(id="LABEL", value={"en": "Area", "fr": "Superficie"})
    (message,) = _warnings(_in_memory, _set(*attributes, label))
    assert message.startswith("reports 'Superficie', which is 10 characters long, longer than maxLength 4")


def test_a_number_reported_as_a_number_is_read_as_its_text() -> None:
    attributes = [a for a in _CONFORMING if a.id != "SHARE"]
    (message,) = _warnings(_in_memory, _set(*attributes, ReportedAttribute(id="SHARE", value=12.25)))
    assert message.startswith("reports '12.25', which has 2 digits after the decimal separator, more than decimals 1")
