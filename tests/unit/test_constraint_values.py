"""A constraint's values are items of the enumeration its DSD gives the component (GH-29).

SDMX 3.1 Section 2, §12.3.3.1 p. 127: "the value in ComponentValue.value and
MemberValue.value must be consistent with the Representation declared for the
Component". The representation is another artefact's, so no wire schema can
check the value, and `validate` warns (#481). The maximal fixture's
constraints reach their DSD through a dataflow (`CC`), the DSD itself
(`CC_data_structures`) and a provision agreement (`CC_provision_agreements`).
"""

import warnings

import attrs
import pytest

from sdmxlib.model.constraint import DataConstraint
from sdmxlib.model.errors import ImRuleWarning
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import validate
from tests.support import maximal

pytestmark = pytest.mark.unit


def _with(constraint_id: str, change: object) -> StructureMessage:
    """The maximal message with constraint `constraint_id` passed through `change`."""
    assert callable(change)
    msg = maximal.message()
    constraints = [change(dc) if dc.id == constraint_id else dc for dc in msg.constraints]
    assert any(dc.id == constraint_id for dc in msg.constraints), constraint_id
    return attrs.evolve(msg, constraints=constraints)


def _key_values(
    dc: DataConstraint, dimension_id: str, values: set[str], *, remove_prefix: bool = False
) -> DataConstraint:
    region, *regions = dc.regions
    keys = tuple(
        attrs.evolve(kv, values=frozenset(values), remove_prefix=remove_prefix)
        if kv.dimension_id == dimension_id
        else kv
        for kv in region.keys
    )
    return attrs.evolve(dc, regions=[attrs.evolve(region, keys=keys), *regions])


def _warned(msg: StructureMessage) -> list[tuple[str, str]]:
    with pytest.warns(ImRuleWarning) as caught:
        validate(msg)
    (warning,) = caught
    assert isinstance(warning.message, ImRuleWarning)
    return [(issue.path, issue.message) for issue in warning.message.issues]


def test_the_maximal_constraints_validate_quietly() -> None:
    """The discrimination check: every value below is refused only once changed."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", ImRuleWarning)
        validate(maximal.message())


@pytest.mark.parametrize("constraint_id", ["CC_data_structures", "CC_provision_agreements"])
def test_a_key_value_its_codelist_lacks_warns(constraint_id: str) -> None:
    msg = _with(constraint_id, lambda dc: _key_values(dc, "DIM", {"PARENT", "NOPE"}))
    ((path, message),) = _warned(msg)
    assert path == f"DataConstraint:{constraint_id}.regions[0].keys[DIM]"
    assert message.startswith("selects 'NOPE', which urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:CL(1.0) does not")


def test_a_component_value_its_codelist_lacks_warns() -> None:
    def change(dc: DataConstraint) -> DataConstraint:
        regions = [
            attrs.evolve(
                region,
                components=tuple(
                    attrs.evolve(c, values=frozenset({"NOPE"}), remove_prefix=False) if c.component_id == "ATTR" else c
                    for c in region.components
                ),
            )
            for region in dc.regions
        ]
        return attrs.evolve(dc, regions=regions)

    paths = [path for path, _ in _warned(_with("CC", change))]
    assert paths == [path for path in paths if path.endswith(".components[ATTR]")]
    assert len(paths) == sum(
        1
        for dc in maximal.message().constraints
        if dc.id == "CC"
        for r in dc.regions
        for c in r.components
        if c.component_id == "ATTR"
    )


def test_a_value_set_with_its_prefix_removed_is_not_compared() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", ImRuleWarning)
        validate(_with("CC", lambda dc: _key_values(dc, "DIM", {"ENT"}, remove_prefix=True)))
