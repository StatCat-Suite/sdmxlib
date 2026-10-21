"""Rules the IM states and no wire schema enforces warn, and build anyway (#481).

`sl.ImRuleWarning` carries every such rule an artefact breaks, one warning per
artefact, pointed at the caller's line. The rules: a facet's sequence
attributes go together (IM §3.6.3.3, #482), and a data constraint holds cube
regions or key sets, not both (§12.3.3.1, #491). The census cites each per
rule.
"""

import warnings

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.formats import parse, serialize
from sdmxlib.model.constraint import (
    ConstraintAttachment,
    ConstraintType,
    CubeRegion,
    DataConstraint,
    DataKey,
    DataKeySet,
    KeyValue,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure, TimeDimension
from sdmxlib.model.errors import ImRuleWarning, SdmxWarning, ValidationError
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from tests.support.factories import concept_ref
from tests.support.roundtrip import as_structure

pytestmark = pytest.mark.unit


def _dsd(facet: Facet, *, dimension_id: str = "D") -> DataStructure:
    return DataStructure(
        id="DSD",
        maintainer="AG",
        name={"en": "d"},
        dimensions=[Dimension(id=dimension_id, concept=concept_ref("D"), representation=facet)],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )


def _issues(build: object) -> list[str]:
    """The messages of the one `ImRuleWarning` that building warns with."""
    assert callable(build)
    with pytest.warns(ImRuleWarning) as caught:
        build()
    (warning,) = caught
    assert isinstance(warning.message, ImRuleWarning)
    return [issue.message for issue in warning.message.issues]


# ── IM §3.6.3.3: the sequence facets (#482), one case per census row ───────────

_SEQUENCE_RULES = [
    pytest.param(Facet(is_sequence=True), "is_sequence=True with no interval or time_interval", id="3.6.3.3-1"),
    pytest.param(Facet(interval=1.0), "interval without is_sequence=True", id="3.6.3.3-2"),
    pytest.param(Facet(is_sequence=True, interval=1.0), "a sequence with interval needs start_value", id="3.6.3.3-3"),
    pytest.param(
        Facet(is_sequence=True, time_interval="P1D", start_time="2020", end_value=9.0),
        "end_value without interval",
        id="3.6.3.3-4",
    ),
    pytest.param(Facet(time_interval="P1D"), "time_interval without is_sequence=True", id="3.6.3.3-5"),
    pytest.param(
        Facet(is_sequence=True, time_interval="P1D"), "a sequence with time_interval needs start_time", id="3.6.3.3-6"
    ),
    pytest.param(
        Facet(is_sequence=True, interval=1.0, start_value=0.0, end_time="2030"),
        "end_time without time_interval",
        id="3.6.3.3-7",
    ),
]


@pytest.mark.parametrize(("facet", "message"), _SEQUENCE_RULES)
def test_a_facet_sequence_rule_warns(facet: Facet, message: str) -> None:
    assert message in _issues(lambda: _dsd(facet))


def test_a_whole_sequence_does_not_warn() -> None:
    """The discrimination check: a numeric and a time sequence, each complete, build silently."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", ImRuleWarning)
        _dsd(Facet(is_sequence=True, interval=2.0, start_value=1.0, end_value=99.0))
        _dsd(Facet(is_sequence=True, time_interval="P1D", start_time="2020-01-01", end_time="2030-12-31"))
        _dsd(Facet(is_sequence=False, max_length=5))


def test_a_time_dimensions_bounds_do_not_warn() -> None:
    """`TimeTextFormatType` allows `startTime`/`endTime` and prohibits `isSequence`: there they are bounds."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", ImRuleWarning)
        DataStructure(
            id="DSD",
            maintainer="AG",
            name={"en": "d"},
            dimensions=[Dimension(id="D", concept=concept_ref("D"))],
            time_dimension=TimeDimension(
                id="TIME_PERIOD",
                concept=concept_ref("TIME_PERIOD"),
                representation=Facet(type="ObservationalTimePeriod", start_time="2020", end_time="2030"),
            ),
            measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
        )


# ── IM §12.3.3.1: cube regions or key sets (#491) ──────────────────────────────

_FLOW = Ref[object].unresolved(Dataflow.urn_for(agency="AG", id="DF", version="1.0"))
_REGION = CubeRegion(keys=(KeyValue(dimension_id="D", values=frozenset({"A"})),))
_KEYS = DataKeySet(keys=(DataKey(values=(("D", "A"),)),))


def _constraint(*, regions: tuple[CubeRegion, ...] = (), key_sets: tuple[DataKeySet, ...] = ()) -> DataConstraint:
    return DataConstraint(
        id="DC",
        maintainer="AG",
        name={"en": "c"},
        constraint_type=ConstraintType.ALLOWED,
        attachment=ConstraintAttachment(dataflows=(_FLOW,)),
        regions=regions,
        data_key_sets=key_sets,
    )


def test_a_constraint_with_regions_and_key_sets_warns() -> None:
    (message,) = _issues(lambda: _constraint(regions=(_REGION,), key_sets=(_KEYS,)))
    assert message.startswith("holds both cube regions and data key sets")


def test_a_constraint_with_one_kind_does_not_warn() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", ImRuleWarning)
        _constraint(regions=(_REGION,))
        _constraint(key_sets=(_KEYS,))


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1", "json"])
def test_a_warned_constraint_still_round_trips(version: str) -> None:
    """Warned, not refused: every format carries both, so the artefact must survive a round trip."""
    with pytest.warns(ImRuleWarning):
        constraint = _constraint(regions=(_REGION,), key_sets=(_KEYS,))
    with pytest.warns(ImRuleWarning):
        back = as_structure(parse(serialize(StructureMessage(constraints=[constraint]), version=version)))
    assert back.constraints[0].data_key_sets == constraint.data_key_sets
    assert back.constraints[0].regions == constraint.regions


# ── The mechanism ───────────────────────────────────────────────────────────────


def test_the_artefact_is_built() -> None:
    with pytest.warns(ImRuleWarning):
        dsd = _dsd(Facet(interval=1.0))
    representation = dsd.dimensions["D"].representation
    assert isinstance(representation, Facet)
    assert representation.interval == 1.0


def test_one_warning_per_artefact_carrying_every_rule() -> None:
    issues = _issues(lambda: _dsd(Facet(is_sequence=True, start_value=1.0, end_time="2030")))
    assert issues == [
        "is_sequence=True with no interval or time_interval",
        "start_value without interval",
        "end_time without time_interval",
    ]


def test_the_warning_points_at_the_callers_line() -> None:
    """Not at sdmxlib, and not at the `__init__` attrs generates."""
    with pytest.warns(ImRuleWarning) as caught:
        _dsd(Facet(interval=1.0))
    assert caught[0].filename == __file__


def test_the_path_names_the_component() -> None:
    with pytest.warns(ImRuleWarning) as caught:
        _dsd(Facet(interval=1.0), dimension_id="REF_AREA")
    message = caught[0].message
    assert isinstance(message, ImRuleWarning)
    assert [issue.path for issue in message.issues] == ["DataStructure:DSD.dimensions[REF_AREA].representation"]


def test_a_refused_artefact_does_not_also_warn() -> None:
    """Rules first: an artefact that is refused is never built, so nothing warns of it."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", ImRuleWarning)
        with pytest.raises(ValidationError):
            DataStructure(
                id="DSD",
                maintainer="AG",
                name={"en": "d"},
                dimensions=[
                    Dimension(id="D", concept=concept_ref("D"), representation=Facet(interval=1.0)),
                    Dimension(id="D", concept=concept_ref("D")),
                ],
            )


def test_disabled_validators_disable_the_warnings_too() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", ImRuleWarning)
        with attrs.validators.disabled():
            _dsd(Facet(interval=1.0))


def test_the_warnings_are_filterable_as_errors_and_exported() -> None:
    assert issubclass(sl.ImRuleWarning, sl.SdmxWarning)
    assert issubclass(SdmxWarning, UserWarning)
    with warnings.catch_warnings():
        warnings.simplefilter("error", sl.SdmxWarning)
        with pytest.raises(ImRuleWarning):
            _dsd(Facet(interval=1.0))
