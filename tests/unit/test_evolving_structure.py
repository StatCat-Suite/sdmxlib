"""A dataflow late-bound to an evolving DSD names its dimensions (GH-27).

SDMX 3.1 Section 2, §5.3.2.1 p. 75: a DimensionConstraint is "a mandatory
requirement if the DataStructureDefinition sets its' evolvingStructure property
to 'true' and is sematically referenced by the Dataflow", which §5.3.2.2 and
its footnote spell as a reference "on the latest minor version", such as
`2.0+.0`. The 3.1 XSD's `DataflowType` says the same. No wire schema can
enforce a rule between two artefacts, so `validate` warns (#481).

`validate_references` used to report such a reference broken although its
DSD was in the message, so `validate` raised before it could ask:
`version.late_binds` now says which versions a late-bound reference takes.
"""

import warnings

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.model.errors import ImRuleWarning
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import ReferenceValidationError, validate
from sdmxlib.model.version import late_binds
from tests.support import maximal

pytestmark = pytest.mark.unit


def _message(version: str, *, constraint: tuple[str, ...] = (), evolving: bool = True) -> StructureMessage:
    """The maximal message, its flow bound to `AG:DSD(version)` and its DSD (1.0) evolving or not."""
    msg = maximal.message()
    assert [dsd.version for dsd in msg.data_structures] == ["1.0"]
    flow, *flows = msg.dataflows
    urn = sl.DataStructure.urn_for(agency="AG", id="DSD", version=version)
    flow = attrs.evolve(flow, structure=sl.Ref[sl.DataStructure].unresolved(urn), dimension_constraint=constraint)
    dsd = attrs.evolve(msg.data_structures[0], evolving_structure=evolving)
    return attrs.evolve(msg, dataflows=[flow, *flows], data_structures=[dsd])


def _quiet(msg: StructureMessage) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", ImRuleWarning)
        validate(msg)


@pytest.mark.parametrize("version", ["1.0+.0", "1+.0.0"], ids=["minor", "major"])
def test_a_late_bound_flow_of_an_evolving_dsd_warns(version: str) -> None:
    with pytest.warns(ImRuleWarning) as caught:
        validate(_message(version))
    (warning,) = caught
    assert isinstance(warning.message, ImRuleWarning)
    (issue,) = warning.message.issues
    assert issue.path == "Dataflow:DF.dimension_constraint"
    assert "is an evolving structure, so the IM requires a dimension constraint" in issue.message


@pytest.mark.parametrize(
    ("version", "constraint", "evolving"),
    [
        ("1.0+.0", ("DIM",), True),
        ("1.0", (), True),
        ("1.0.0+", (), True),
        ("1.0+.0", (), False),
    ],
    ids=["constrained", "pinned", "later-patches-only", "not-evolving"],
)
def test_a_flow_the_rule_does_not_reach_validates_quietly(
    version: str, constraint: tuple[str, ...], evolving: bool
) -> None:
    _quiet(_message(version, constraint=constraint, evolving=evolving))


def test_a_late_bound_reference_resolves_to_a_version_it_takes() -> None:
    """It used to be reported broken: `1.0+.0` matched no spelling of DSD 1.0."""
    _quiet(_message("1.0+.0", constraint=("DIM",)))


def test_a_late_bound_reference_no_version_satisfies_is_broken() -> None:
    with pytest.raises(ReferenceValidationError, match=r"DataStructure=AG:DSD\(2\.0\+\.0\)"):
        validate(_message("2.0+.0", constraint=("DIM",)))


@pytest.mark.parametrize(
    ("reference", "version", "binds"),
    [
        ("2.0+.0", "2.3.1", True),
        ("2.0+.0", "2.0.0", True),
        ("2.0+.0", "3.0.0", False),
        ("2.1+.0", "2.0.5", False),
        ("1.2.3+", "1.2.9", True),
        ("1.2.3+", "1.3.0", False),
        ("1+.0.0", "4.2.0", True),
        ("1.0+.0", "1.0", True),
        ("1.0+.0", "1.1.0-draft", False),
        ("1.0", "1.0", False),
        ("1+.0.0+", "2.0.0", True),
    ],
)
def test_late_binds_takes_the_versions_from_its_plus(reference: str, version: str, binds: bool) -> None:
    """IM §5.3.2.2's footnote: "2.0+.0 will resolve to the highest version 2.x.y"."""
    assert late_binds(reference, version) is binds
