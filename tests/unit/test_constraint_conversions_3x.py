"""What a constraint loses on its way to 3.x, said rather than refused (#421).

3.1 removed ``<ReleaseCalendar>`` from DataConstraint: leaving it out asserts
nothing false, so it is a declared gap, as it always was for SDMX-JSON. The
role is different — dropping it turns Actual into Allowed — and stays refused.
An AttachmentConstraint, which 3.0 removed, is written as the Allowed
DataConstraint it equals, and the change of kind is declared.
"""

import pytest

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.gaps import unrepresentable
from sdmxlib.model.constraint import (
    AttachmentConstraint,
    ConstraintAttachment,
    ConstraintType,
    DataConstraint,
    DataKey,
    DataKeySet,
    ReleaseCalendar,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.validation import ValidationError
from tests.support.roundtrip import as_structure, structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_FLOW = ConstraintAttachment(
    dataflows=(Ref[object].unresolved(Dataflow.urn_for(agency="AG", id="DF", version="1.0")),),
)


def _constraint(constraint_type: ConstraintType = ConstraintType.ALLOWED) -> DataConstraint:
    return DataConstraint(
        id="DC",
        maintainer="AG",
        name={"en": "dc"},
        constraint_type=constraint_type,
        attachment=_FLOW,
        release_calendar=ReleaseCalendar(periodicity="P1M", offset="P15D", tolerance="P2D"),
    )


def test_3_1_leaves_a_release_calendar_out_and_says_so() -> None:
    written = serialize(StructureMessage(constraints=[_constraint()]), version="3.1")
    assert_valid_sdmx_ml(written, structure_schema("3.1"))
    (back,) = as_structure(parse(written)).constraints
    assert back.release_calendar is None
    assert "release_calendar" in [gap.field for gap in unrepresentable(_constraint(), "3.1")]
    assert unrepresentable(_constraint(), "3.0") == ()


def test_3_1_still_refuses_an_actual_constraint() -> None:
    """The role is not a gap: written without it, Actual reads back as Allowed."""
    with pytest.raises(ValidationError, match="removed DataConstraint/@role"):
        serialize(StructureMessage(constraints=[_constraint(ConstraintType.ACTUAL)]), version="3.1")


@pytest.mark.parametrize("version", ["3.0", "3.1", "json"])
def test_an_attachment_constraint_is_declared_as_written_as_a_data_constraint(version: str) -> None:
    attachment = AttachmentConstraint(
        id="AC",
        maintainer="AG",
        name={"en": "ac"},
        attachment=_FLOW,
        data_key_sets=(DataKeySet(is_included=False, keys=(DataKey(values=(("FREQ", "A"),)),)),),
    )
    (gap,) = [gap for gap in unrepresentable(attachment, version) if gap.field == "sdmx_class"]
    assert "written as the Allowed DataConstraint" in gap.reason
    back = as_structure(parse(serialize(StructureMessage(attachment_constraints=[attachment]), version=version)))
    assert (len(back.constraints), list(back.attachment_constraints)) == (1, [])
    assert back.constraints[0].data_key_sets == attachment.data_key_sets
