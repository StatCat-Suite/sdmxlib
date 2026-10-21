"""SDMX 3.1's AvailabilityConstraint, and the Actual role converted to it (#421).

3.1 removed the role from DataConstraint and gave Actual content a type of its
own that has no identity — one attachment, one included cube region, optional
counts. sdmxlib keeps the role on `DataConstraint`, models the 3.1 type as a
value in `StructureMessage.availability_constraints`, writes an Actual
constraint that fits as one, and leaves one out of every other target.
"""

import attrs
import pytest
from lxml import etree

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.gaps import availability_label, dropped, unrepresentable
from sdmxlib.model.constraint import (
    AvailabilityConstraint,
    ConstraintAttachment,
    ConstraintType,
    CubeRegion,
    DataConstraint,
    DataKey,
    DataKeySet,
    KeyValue,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.errors import ImRuleWarning
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.validation import ValidationError
from tests.support.roundtrip import as_structure, structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit


def _flow(flow_id: str = "DF") -> Ref[object]:
    return Ref[object].unresolved(Dataflow.urn_for(agency="AG", id=flow_id, version="1.0"))


_REGION = CubeRegion(keys=(KeyValue(dimension_id="FREQ", values=frozenset({"A", "M"})),))
_ACTUAL = DataConstraint(
    id="DC_ACTUAL",
    maintainer="AG",
    name={"en": "what exists"},
    constraint_type=ConstraintType.ACTUAL,
    attachment=ConstraintAttachment(dataflows=(_flow(),)),
    regions=(_REGION,),
)


def _with_key_set() -> DataConstraint:
    """`_ACTUAL` with a key set beside its region, which the IM warns of (#491) and 3.1 cannot convert."""
    with pytest.warns(ImRuleWarning, match="both cube regions and data key sets"):
        return attrs.evolve(_ACTUAL, data_key_sets=(DataKeySet(keys=(DataKey(values=(("FREQ", "A"),)),)),))


def _back(msg: StructureMessage, version: str) -> StructureMessage:
    written = serialize(msg, version=version)
    if version != "json":
        assert_valid_sdmx_ml(written, structure_schema(version))
    return as_structure(parse(written, lossless=True))


# ── An Actual constraint, written to 3.1 ─────────────────────────────────────


def test_3_1_writes_an_actual_constraint_that_fits_as_an_availability_constraint() -> None:
    written = serialize(StructureMessage(constraints=[_ACTUAL]), version="3.1")
    assert b"<str:AvailabilityConstraint>" in written
    assert b"<str:DataConstraint " not in written
    back = _back(StructureMessage(constraints=[_ACTUAL]), "3.1")
    assert (list(back.constraints), list(back.availability_constraints)) == ([], [_ACTUAL.to_availability_constraint()])
    assert [gap.field for gap in unrepresentable(_ACTUAL, "3.1")] == ["sdmx_class"]
    assert unrepresentable(_ACTUAL, "3.0") == ()


def test_3_1_still_writes_an_allowed_constraint_as_one() -> None:
    allowed = attrs.evolve(_ACTUAL, constraint_type=ConstraintType.ALLOWED)
    back = _back(StructureMessage(constraints=[allowed]), "3.1")
    assert (list(back.constraints), list(back.availability_constraints)) == ([allowed], [])


@pytest.mark.parametrize(
    ("constraint", "reason"),
    [
        pytest.param(attrs.evolve(_ACTUAL, regions=(_REGION, _REGION)), "2 cube regions", id="two-regions"),
        pytest.param(
            attrs.evolve(_ACTUAL, regions=(attrs.evolve(_REGION, include=False),)), "not one included", id="excluded"
        ),
        pytest.param(attrs.evolve(_ACTUAL, regions=()), "0 cube regions", id="no-region"),
        pytest.param(_with_key_set(), "1 data key sets", id="keys"),
        pytest.param(
            attrs.evolve(_ACTUAL, attachment=ConstraintAttachment(dataflows=(_flow(), _flow("DF2")))),
            "exactly one",
            id="two-flows",
        ),
    ],
)
def test_3_1_refuses_an_actual_constraint_that_does_not_fit(constraint: DataConstraint, reason: str) -> None:
    assert any(reason in m for m in constraint.availability_misfits())
    with pytest.raises(ValueError, match=reason):
        constraint.to_availability_constraint()
    with pytest.raises(ValidationError, match=reason):
        serialize(StructureMessage(constraints=[constraint]), version="3.1")


# ── An AvailabilityConstraint ────────────────────────────────────────────────

_AVAILABILITY = AvailabilityConstraint(
    attachment=ConstraintAttachment(dataflows=(_flow(),)), region=_REGION, series_count=3, obs_count=120
)


def test_an_availability_constraint_round_trips_through_3_1() -> None:
    back = _back(StructureMessage(availability_constraints=[_AVAILABILITY]), "3.1")
    assert list(back.availability_constraints) == [_AVAILABILITY]


@pytest.mark.parametrize("version", ["2.1", "3.0", "json"])
def test_every_other_target_leaves_it_out_and_names_it(version: str) -> None:
    msg = StructureMessage(availability_constraints=[_AVAILABILITY], constraints=[attrs.evolve(_ACTUAL, id="KEPT")])
    (drop,) = dropped(msg, version)
    assert drop.urn == availability_label(_AVAILABILITY) == f"AvailabilityConstraint({_flow().urn})"
    assert [gap.field for gap in unrepresentable(_AVAILABILITY, version)] == ["sdmx_class"]
    back = _back(msg, version)
    assert ([c.id for c in back.constraints], list(back.availability_constraints)) == (["KEPT"], [])


def test_the_store_does_not_hold_one_and_says_so() -> None:
    assert [drop.urn for drop in dropped(StructureMessage(availability_constraints=[_AVAILABILITY]), "storage")] == [
        availability_label(_AVAILABILITY)
    ]
    assert [gap.field for gap in unrepresentable(_AVAILABILITY, "storage")] == ["sdmx_class"]


@pytest.mark.parametrize(
    ("availability", "path"),
    [
        pytest.param(
            attrs.evolve(_AVAILABILITY, attachment=ConstraintAttachment(dataflows=(_flow(), _flow("DF2")))),
            "AvailabilityConstraint[1].attachment",
            id="two-flows",
        ),
        pytest.param(
            attrs.evolve(_AVAILABILITY, region=attrs.evolve(_REGION, include=False)),
            "AvailabilityConstraint[1].region",
            id="excluded",
        ),
    ],
)
def test_3_1_refuses_one_its_schema_cannot_hold(availability: AvailabilityConstraint, path: str) -> None:
    with pytest.raises(ValidationError) as caught:
        serialize(StructureMessage(availability_constraints=[availability]), version="3.1")
    assert [issue.path for issue in caught.value.issues] == [path]


def test_one_without_a_region_is_refused_alone() -> None:
    """Named by position: it has no URN. The rest of the document is read."""
    written = serialize(StructureMessage(availability_constraints=[_AVAILABILITY, _AVAILABILITY]), version="3.1")
    doc = etree.fromstring(written)
    first = doc.find(".//{*}AvailabilityConstraint")
    assert first is not None
    region = first.find("{*}CubeRegion")
    assert region is not None
    first.remove(region)
    back = as_structure(parse(etree.tostring(doc), strict=False))
    assert [(r.artefact_class, r.urn) for r in back.refused] == [
        ("AvailabilityConstraint", "AvailabilityConstraint[1]")
    ]
    assert list(back.availability_constraints) == [_AVAILABILITY]
