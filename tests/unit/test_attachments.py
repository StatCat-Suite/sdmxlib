"""A series attachment names the DSD's dimensions; a group attachment one of its groups (#454).

`AttributeRelationshipType` is a required choice, so an attribute attached to
a series with no dimensions was written as an empty
``<str:AttributeRelationship/>`` no schema accepts, and FMR refused the DSD.
One naming a dimension the DSD lacks says nothing a reader can use.

Eurostat's 3.0 DSDs name ``freq`` as ``FREQ``: the readers match a reference
naming exactly one dimension ignoring case, and record it on `msg.assumed`.
"""

from collections.abc import Callable

import pytest

from sdmxlib.formats import parse, serialize
from sdmxlib.model.concept import Concept
from sdmxlib.model.datastructure import (
    AttachmentLevel,
    Attribute,
    DataStructure,
    Dimension,
    Group,
    Measure,
    MetadataAttributeUsage,
    TimeDimension,
)
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import ValidationError
from tests.support.unchecked import refusal

pytestmark = pytest.mark.unit


def _concept(item_id: str) -> Ref[Concept]:
    urn = SdmxUrn.make(
        Concept, package="conceptscheme", artefact_class="Concept", agency="AG", id="CS", version="1.0", item_id=item_id
    )
    return Ref[Concept].unresolved(urn)


def _dsd(
    *attributes: Attribute, usages: tuple[MetadataAttributeUsage, ...] = (), dimension_ids: tuple[str, ...] = ("FREQ",)
) -> DataStructure:
    return DataStructure(
        id="DSD",
        maintainer="AG",
        name={"en": "d"},
        dimensions=[Dimension(id=d, concept=_concept(d)) for d in dimension_ids],
        time_dimension=TimeDimension(id="TIME_PERIOD", concept=_concept("TIME_PERIOD")),
        measures=[Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"))],
        attributes=list(attributes),
        metadata_attributes=list(usages),
        groups=[Group(id="G", dimension_ids=["FREQ"])] if "FREQ" in dimension_ids else [],
    )


def _series(*dimensions: str) -> Attribute:
    return Attribute(
        id="ATTR", concept=_concept("ATTR"), attachment=AttachmentLevel.SERIES, related_dimensions=list(dimensions)
    )


def _issues(build: Callable[[], object]) -> list[tuple[str, str]]:
    return [(i.path.split(".", 1)[1], i.message) for i in refusal(build)]


def test_a_series_attachment_naming_its_dimensions_is_valid() -> None:
    assert _issues(lambda: _dsd(_series("FREQ"), _series_named("TIME", "TIME_PERIOD"))) == []


def _series_named(attribute_id: str, *dimensions: str) -> Attribute:
    return Attribute(
        id=attribute_id,
        concept=_concept(attribute_id),
        attachment=AttachmentLevel.SERIES,
        related_dimensions=list(dimensions),
    )


@pytest.mark.parametrize("related", [None, []], ids=["none", "empty"])
def test_a_series_attachment_naming_no_dimension_is_refused(related: list[str] | None) -> None:
    attribute = Attribute(
        id="ATTR", concept=_concept("ATTR"), attachment=AttachmentLevel.SERIES, related_dimensions=related
    )
    (issue,) = _issues(lambda: _dsd(attribute))
    assert issue[0] == "attributes[ATTR].related_dimensions"
    assert "names no dimension" in issue[1]


def test_a_series_attachment_naming_an_unknown_dimension_is_refused() -> None:
    assert _issues(lambda: _dsd(_series("FREQ", "GEO"))) == [
        ("attributes[ATTR].related_dimensions", "names GEO, which this DataStructure has no dimension for")
    ]


def test_a_group_attachment_names_a_declared_group() -> None:
    missing = Attribute(id="ATTR", concept=_concept("ATTR"), attachment=AttachmentLevel.GROUP)
    unknown = Attribute(id="ATTR", concept=_concept("ATTR"), attachment=AttachmentLevel.GROUP, related_group="H")
    known = Attribute(id="ATTR", concept=_concept("ATTR"), attachment=AttachmentLevel.GROUP, related_group="G")
    assert _issues(lambda: _dsd(missing)) == [("attributes[ATTR].related_group", "attached to a group, it names none")]
    assert _issues(lambda: _dsd(unknown)) == [
        ("attributes[ATTR].related_group", "names group 'H', which this DataStructure does not declare")
    ]
    assert _issues(lambda: _dsd(known)) == []


def test_a_metadata_attribute_usage_is_checked_too() -> None:
    usage = MetadataAttributeUsage(id="MA", attachment=AttachmentLevel.SERIES, related_dimensions=["GEO"])
    (issue,) = _issues(lambda: _dsd(usages=(usage,)))
    assert issue[0] == "metadata_attributes[MA].related_dimensions"


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1", "json"])
def test_an_empty_series_relationship_is_refused_on_write(version: str) -> None:
    """The issue's symptom: written as ``<str:AttributeRelationship/>``, which the XSD refuses."""
    with pytest.raises(ValidationError, match="names no dimension"):
        serialize(StructureMessage(data_structures=[_dsd(_series())]), version=version)


# ── Eurostat's case slip ──────────────────────────────────────────────────────


def _written_with_case_slip(version: str, written_as: str) -> bytes:
    """A DSD whose dimension is ``freq`` and whose attribute names it *written_as*."""
    dsd = _dsd(_series("freq"), dimension_ids=("freq",))
    text = serialize(StructureMessage(data_structures=[dsd]), version=version).decode()
    for old, new in ((">freq<", f">{written_as}<"), ('Ref id="freq"', f'Ref id="{written_as}"')):
        text = text.replace(old, new, 1) if old in text and "AttributeRelationship" in text else text
    return text.encode()


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1"])
def test_a_relationship_naming_a_dimension_in_another_case_reads_as_that_dimension(version: str) -> None:
    msg = parse(_written_with_case_slip(version, "FREQ"))
    assert isinstance(msg, StructureMessage)
    (dsd,) = msg.data_structures
    assert dsd.attributes["ATTR"].related_dimensions == ["freq"]
    (assumed,) = msg.assumed
    assert (assumed.construct, assumed.count) == ("str:AttributeRelationship/str:Dimension", 1)


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1"])
def test_a_relationship_naming_no_dimension_in_any_case_is_refused(version: str) -> None:
    msg = parse(_written_with_case_slip(version, "GEO"), strict=False)
    assert isinstance(msg, StructureMessage)
    (refusal,) = msg.refused
    assert "names GEO" in refusal.reason
    assert msg.assumed == ()
