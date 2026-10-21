"""SDMX-ML 2.1's own DSD constructs, held (#422).

Four things a 2.1 DSD can say that the reader dropped, each a `LOSSES` row:

- an attribute related to dimensions may also name the groups it attaches to
  (``AttachmentGroup``) — `Attribute.attachment_groups`;
- a group may be defined by an attachment constraint instead of its
  dimensions — `Group.attachment_constraint`;
- the measure dimension's concept roles — `MeasureDimension.concept_role`, as
  the other components have;
- the reporting year's start day, an attribute whose id 2.1 fixes and
  reserves — held as an `Attribute` by that id, `REPORTING_YEAR_START_DAY`.

SDMX 3.x has no form for the first two, so writing them there is refused. It
has no ``ReportingYearStartDay`` element either, and needs none: there the
attribute is an ordinary one by the same id.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

import attrs
import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats import parse, serialize
from sdmxlib.formats.gaps import unrepresentable
from sdmxlib.model.datastructure import REPORTING_YEAR_START_DAY, AttachmentLevel, Attribute, DataStructure
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import ValidationError
from tests.support import shapes
from tests.support.factories import concept_ref
from tests.support.field_census import differences
from tests.support.narrow import present
from tests.support.roundtrip import as_structure

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.unit

_STR_21 = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"


def _dsd() -> DataStructure:
    (dsd,) = shapes.message_21().data_structures
    return dsd


def _alone(dsd: DataStructure) -> StructureMessage:
    """The DSD with what it references, and nothing else to refuse."""
    full = shapes.message_21()
    return StructureMessage(concept_schemes=full.concept_schemes, data_structures=[dsd])


# ── SDMX-ML 2.1 ───────────────────────────────────────────────────────────


def test_sdmx_ml_2_1_round_trips_each() -> None:
    dsd = _dsd()
    written = serialize(_alone(dsd), version="2.1")
    (back,) = as_structure(parse(written)).data_structures
    # Less what `gaps.py` says 2.1 cannot carry: the shapes' 3.x facets.
    declared = {gap.field for gap in unrepresentable(dsd, "2.1")}
    assert not [d for d in differences(dsd, back) if re.sub(r"\[\d+\]", "[]", d.path.removeprefix(".")) not in declared]
    assert back.attributes["ATTR_SERIES"].attachment_groups == ("G_DIMS",)
    assert present(present(back.groups)["G_CONSTRAINT"].attachment_constraint).urn.id == "AC_SHAPES"
    assert back.attributes[REPORTING_YEAR_START_DAY].representation == sl.Facet(type="MonthDay")


def test_the_reserved_id_is_written_as_reporting_year_start_day() -> None:
    doc = etree.fromstring(serialize(_alone(_dsd()), version="2.1"))
    (rysd,) = doc.iterfind(f".//{{{_STR_21}}}ReportingYearStartDay")
    assert rysd.get("id") == REPORTING_YEAR_START_DAY
    attribute_ids = [el.get("id") for el in doc.iterfind(f".//{{{_STR_21}}}AttributeList/{{{_STR_21}}}Attribute")]
    assert REPORTING_YEAR_START_DAY not in attribute_ids


def test_the_id_is_optional_on_the_wire_and_fixed() -> None:
    """``ReportingYearStartDayType`` makes ``@id`` optional, with a fixed value."""
    doc = etree.fromstring(serialize(_alone(_dsd()), version="2.1"))
    (rysd,) = doc.iterfind(f".//{{{_STR_21}}}ReportingYearStartDay")
    del rysd.attrib["id"]
    (back,) = as_structure(parse(etree.tostring(doc))).data_structures
    assert REPORTING_YEAR_START_DAY in back.attributes.ids()


@pytest.mark.parametrize(
    ("change", "path"),
    [
        pytest.param(
            lambda a: attrs.evolve(a, representation=sl.Facet(type="String")),
            "representation",
            id="a-representation-other-than-monthday",
        ),
        pytest.param(lambda a: attrs.evolve(a, concept_role=("ROLE",)), "concept_role", id="a-concept-role"),
    ],
)
def test_sdmx_ml_2_1_refuses_a_reporting_year_start_day_it_cannot_write(
    change: Callable[[Attribute], Attribute], path: str
) -> None:
    dsd = _dsd()
    rysd = change(dsd.attributes[REPORTING_YEAR_START_DAY])
    others = [a for a in dsd.attributes if a.id != REPORTING_YEAR_START_DAY]
    with pytest.raises(ValidationError) as caught:
        serialize(_alone(attrs.evolve(dsd, attributes=[*others, rysd])), version="2.1")
    assert [i.path for i in caught.value.issues] == [
        f"DataStructure:DSD_SHAPES.attributes[{REPORTING_YEAR_START_DAY}].{path}"
    ]


# ── SDMX 3.x and SDMX-JSON ────────────────────────────────────────────────


@pytest.mark.parametrize("version", ["3.0", "3.1", "json"])
def test_3x_refuses_constraint_groups(version: str) -> None:
    """Attachment groups were refused here too until #421; they are a declared gap now."""
    with pytest.raises(ValidationError) as caught:
        serialize(_alone(_dsd()), version=version)
    # A set: the reference-class check refuses the constraint reference too.
    assert sorted({i.path for i in caught.value.issues}) == [
        "DataStructure:DSD_SHAPES.groups[G_CONSTRAINT].attachment_constraint",
    ]


@pytest.mark.parametrize("version", ["3.0", "3.1", "json"])
def test_3x_writes_the_reporting_year_start_day_as_an_attribute(version: str) -> None:
    dsd = _dsd()
    plain = attrs.evolve(
        dsd,
        groups=[g for g in present(dsd.groups) if g.attachment_constraint is None],
        attributes=[attrs.evolve(a, attachment_groups=()) for a in dsd.attributes],
    )
    (back,) = as_structure(parse(serialize(_alone(plain), version=version))).data_structures
    assert back.attributes[REPORTING_YEAR_START_DAY].representation == sl.Facet(type="MonthDay")


# ── well-formedness ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("build", "path", "message"),
    [
        pytest.param(
            lambda dsd: attrs.evolve(
                dsd,
                groups=[
                    attrs.evolve(g, dimension_ids=["DIM"]) if g.id == "G_CONSTRAINT" else g for g in present(dsd.groups)
                ],
            ),
            "DataStructure.groups[G_CONSTRAINT]",
            "not both",
            id="a-group-of-dimensions-and-a-constraint",
        ),
        pytest.param(
            lambda dsd: attrs.evolve(
                dsd,
                attributes=[
                    attrs.evolve(a, attachment=AttachmentLevel.DATASET, related_dimensions=None)
                    if a.id == "ATTR_SERIES"
                    else a
                    for a in dsd.attributes
                ],
            ),
            "DataStructure.attributes[ATTR_SERIES].attachment_groups",
            "beside related dimensions",
            id="attachment-groups-with-no-dimensions",
        ),
        pytest.param(
            lambda dsd: attrs.evolve(
                dsd,
                attributes=[
                    attrs.evolve(a, attachment_groups=("NOPE",)) if a.id == "ATTR_SERIES" else a for a in dsd.attributes
                ],
            ),
            "DataStructure.attributes[ATTR_SERIES].attachment_groups",
            "'NOPE'",
            id="an-undeclared-group",
        ),
    ],
)
def test_validate_refuses_a_grouping_2_1_cannot_mean(build: object, path: str, message: str) -> None:
    assert callable(build)
    with pytest.raises(ValidationError) as caught:
        build(_dsd())
    (issue,) = caught.value.issues
    assert issue.path == path
    assert message in issue.message


def test_a_bare_string_is_not_a_tuple_of_group_ids() -> None:
    untyped = cast("object", "G_DIMS")
    with pytest.raises(TypeError, match="tuple of ids"):
        sl.Attribute(
            attachment=sl.AttachmentLevel.DATASET,
            id="A",
            attachment_groups=cast("tuple[str, ...]", untyped),
            concept=concept_ref("A"),
        )


# ── storage ───────────────────────────────────────────────────────────────


def test_each_survives_the_store() -> None:
    dsd = _dsd()
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(dsd)
        back = reg.get(DataStructure.parse_urn(str(dsd.urn)))
        assert isinstance(back, DataStructure)
        assert not list(differences(dsd, back))
        assert back.attributes["ATTR_SERIES"].attachment_groups == ("G_DIMS",)
        assert present(present(back.groups)["G_CONSTRAINT"].attachment_constraint).urn.id == "AC_SHAPES"
