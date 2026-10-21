"""The wire gate over populated artefacts: valid, or refused — never quietly invalid.

`test_wire_conformance_gate.py` asks the gate's question of *bare* artefacts,
and its own docstring says what that leaves out: constraints that only bite
on a populated artefact. Milestone 11 closed with that table empty, and #416
and #417 then shipped anyway — a non-default organisation-scheme version, a
2.1-only version written to JSON — because both are *values*, and a bare
artefact carries none (#423).

This module asks the same question of artefacts carrying the values the
model accepts and some format cannot. Each case is one state, applied to an
artefact that is otherwise valid in every format, and named for the row of
#423 or the registry sweep (`bench/sweep/`) it comes from:

- **valid** — the document validates against the official schema;
- **refused** — `serialize` raises `ValidationError` before writing;
- **known invalid** — neither, recorded in `KNOWN_INVALID` against its
  issue, and asserted *still* invalid so a fix fails here until the entry
  goes.

Two checks keep the cases honest. Every base is valid in every format
(`test_every_base_is_valid_everywhere`), so a case that is refused or
invalid is so because of the state it sets and nothing else. And a case may
not reproduce its base (`test_every_case_changes_its_base`), or it would
test the base twice.
"""

from collections.abc import Callable
from typing import Any

import attrs
import pytest
from attrs import evolve, frozen

from sdmxlib.formats import serialize
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept
from sdmxlib.model.constraint import ComponentValueSet, CubeRegion, DataConstraint, KeyValue, TimeBound, TimeRange
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import Attribute, DataStructure, Measure
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.organisation import AgencyScheme, DataProviderScheme
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import ValidationError
from tests.support import maximal
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema
from tests.support.roundtrip import structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_WIRE_VERSIONS = ("2.1", "3.0", "3.1", "json")


def _maximal(kind: type[object], index: int = 0) -> object:
    """The maximal fixture's `index`-th artefact of type `kind`."""
    found = [a for a in maximal.message().all_artefacts() if type(a) is kind]
    return found[index]


def _data_structure() -> DataStructure:
    """The maximal DSD without the two parts every non-2.1 format refuses.

    `enumeration_format.decimals` is refused everywhere and `measure_dimension`
    outside 2.1; both have cases of their own in the bare and coverage tests.
    Left in, every DSD case here would be refused for them, whatever it set.
    """
    dsd = _maximal(DataStructure)
    assert isinstance(dsd, DataStructure)
    return evolve(
        dsd,
        measure_dimension=None,
        dimensions=[evolve(d, enumeration_format=None) for d in dsd.dimensions],
        attributes=[evolve(a, enumeration_format=None) for a in dsd.attributes],
        measures=[evolve(m, enumeration_format=None) for m in dsd.measures],
    )


def _codelist() -> Codelist:
    codelist = _maximal(Codelist)
    assert isinstance(codelist, Codelist)
    return codelist


def _constraint() -> DataConstraint:
    """A constraint anchored on a DSD — the maximal one carries a reference
    period that 3.1 refuses."""
    (constraint,) = (
        a for a in maximal.message().constraints if isinstance(a, DataConstraint) and a.id == "CC_data_structures"
    )
    return constraint


def _dataflow() -> Dataflow:
    dataflow = _maximal(Dataflow)
    assert isinstance(dataflow, Dataflow)
    return dataflow


def _scheme[T](kind: type[T]) -> T:
    scheme = _maximal(kind)
    assert isinstance(scheme, kind)
    return scheme


#: One base per kind a case changes, each valid in every format.
_BASES: dict[str, Callable[[], object]] = {
    "agency_scheme": lambda: _scheme(AgencyScheme),
    "codelist": _codelist,
    "constraint": _constraint,
    "data_provider_scheme": lambda: _scheme(DataProviderScheme),
    "data_structure": _data_structure,
    "dataflow": _dataflow,
}


@frozen
class Case:
    """One populated state.

    Attributes:
        base: Key into `_BASES`.
        build: The base, with the state applied.
        source: The #423 row, issue, or sweep finding the state comes from.
    """

    base: str
    build: Callable[[], object]
    source: str


def _relating(attribute: Attribute, measures: set[str]) -> Attribute:
    """*attribute*, related to those of its measures still in *measures*, or to none.

    None rather than an empty tuple when none is left: an empty one writes an
    empty `MeasureRelationship`, which SDMX-ML 3.x rejects.
    """
    kept = [m for m in attribute.related_measures or () if m in measures]
    return evolve(attribute, related_measures=kept or None)


def _measure_variants() -> dict[str, Case]:
    def dsd_with(measures: Callable[[DataStructure], list[Measure]]) -> Callable[[], object]:
        def build() -> object:
            dsd = _data_structure()
            kept = measures(dsd)
            ids = {m.id for m in kept}
            # An attribute may relate only to the DSD's own measures (#492), so
            # it keeps those still there: the case changes the measures alone.
            attributes = [a if a.related_measures is None else _relating(a, ids) for a in dsd.attributes]
            return evolve(dsd, measures=kept, attributes=attributes)

        return build

    return {
        "measures_two": Case(
            "data_structure", dsd_with(lambda d: [*d.measures, evolve(d.measures[0], id="OBS_2")]), "#421 measures"
        ),
        "measures_not_obs_value": Case(
            "data_structure", dsd_with(lambda d: [evolve(d.measures[0], id="VALUE")]), "#421 measures"
        ),
        "measures_none": Case("data_structure", dsd_with(lambda _d: []), "#421 measures"),
    }


def _version(version: str) -> Callable[[], object]:
    return lambda: evolve(_codelist(), version=version)


def _structure_ref(version: str) -> Callable[[], object]:
    """A dataflow whose DSD reference carries `version` — the reference, not
    the artefact, which nothing checks against the target grammar."""
    return lambda: evolve(
        _dataflow(),
        structure=Ref[DataStructure].unresolved(DataStructure.urn_for(agency="AG", id="DSD", version=version)),
    )


def _latest_concept_ref() -> object:
    """A DSD whose dimension's concept is an *item* reference with no version.

    Refused in every format (#233). What the 2.1 writer emits for a caller
    writing directly is `test_ref_versions.py`'s to check.
    """
    dsd = _data_structure()
    dimension = dsd.dimensions[0]
    urn = SdmxUrn.make(
        Concept,
        package="conceptscheme",
        artefact_class="Concept",
        agency="AG",
        id="CS",
        version="latest",
        item_id="PARENT",
    )
    latest = Ref[Concept].unresolved(urn)
    return evolve(dsd, dimensions=[evolve(dimension, concept=latest)])


def _time_key(*, start: str | None = None, end: str | None = None) -> KeyValue:
    return KeyValue(
        dimension_id="TIME_PERIOD",
        time_range=TimeRange(
            start=TimeBound(start) if start is not None else None,
            end=TimeBound(end, inclusive=False) if end is not None else None,
        ),
    )


def _region(*keys: KeyValue, regions: int = 1) -> Callable[[], object]:
    return lambda: evolve(_constraint(), regions=[CubeRegion(include=True, keys=keys) for _ in range(regions)])


CASES: dict[str, Case] = {
    # ── B1: format-independent ────────────────────────────────────────────
    # An organisation scheme off version 1.0 cannot be built (#416, #492), so
    # it has no case here — see test_serialisation_conformance.
    # A version in neither grammar cannot be built: `version` refuses it as it
    # is set (#492), so it has no case here — see test_validation's version tests.
    # ── B2: versions, one grammar at a time ───────────────────────────────
    "version_2_1_only_four_part": Case("codelist", _version("1.2.3.4"), "#420"),
    "version_2_1_only_leading_zero": Case("codelist", _version("1.03"), "#420"),
    "version_semver_draft": Case("codelist", _version("1.0.0-draft"), "#420"),
    "version_semver_final": Case("codelist", _version("1.0.0"), "#420"),
    # ── B2: references ────────────────────────────────────────────────────
    "ref_latest": Case("dataflow", _structure_ref("latest"), "#233"),
    "ref_semver_draft": Case("dataflow", _structure_ref("1.0.0-draft"), "#429"),
    "ref_2_1_only": Case("dataflow", _structure_ref("1.2.3.4"), "#429"),
    "ref_wildcard_minor": Case("dataflow", _structure_ref("1.0+.0"), "#429; sweep: IMF"),
    "ref_wildcard_major_and_patch": Case("dataflow", _structure_ref("1+.0.0+"), "#429: 3.0 only"),
    "ref_version_in_no_grammar": Case("dataflow", _structure_ref("*"), "#429"),
    "ref_item_latest": Case("data_structure", _latest_concept_ref, "#429"),
    # ── B2: shapes ────────────────────────────────────────────────────────
    **_measure_variants(),
    "constraint_two_include_regions": Case(
        "constraint", _region(KeyValue(dimension_id="DIM", values={"A"}), regions=2), "#423 B2"
    ),
    "annotation_value": Case(
        "codelist", lambda: evolve(_codelist(), annotations=Annotations([Annotation(id="A", value="v")])), "#423 B2"
    ),
    # ── found by the registry sweep ───────────────────────────────────────
    # A time dimension typed String cannot be built (#431, #492), so it has no
    # case here — see test_time_text_type.
    "code_parent_leading_underscore": Case(
        "codelist",
        lambda: evolve(
            _codelist(), codes=[Code(id="_T", name={"en": "t"}), Code(id="A", name={"en": "a"}, parent_id="_T")]
        ),
        "#432; sweep: OECD, FMR, IMF, BIS",
    ),
    "constraint_key_value_without_values": Case(
        "constraint", _region(KeyValue(dimension_id="DIM")), "#433; sweep: OECD"
    ),
    "constraint_time_range_between": Case(
        "constraint", _region(_time_key(start="2000-01-01", end="2010-12-31")), "#433; sweep: OECD"
    ),
    "constraint_time_range_after": Case("constraint", _region(_time_key(start="2000-01-01")), "#433"),
    "constraint_time_range_before": Case("constraint", _region(_time_key(end="2010-12-31")), "#433"),
    # A time range with neither bound cannot be built: `TimeRange` refuses it
    # (#492), so it has no case here — see test_cube_region_time_range.
    # A selection with values and a time range cannot be built (#433, #492),
    # so it has no case here — see test_cube_region_time_range.
    "constraint_component_values": Case(
        "constraint",
        lambda: evolve(
            _constraint(),
            regions=[
                CubeRegion(
                    keys=(KeyValue(dimension_id="DIM", values={"A"}),),
                    components=(ComponentValueSet(component_id="ATTR", values={"0", "6"}),),
                )
            ],
        ),
        "#433; sweep: OECD",
    ),
}

#: (case, wire) → the schema's complaint and the issue that fixes it. The
#: burn-down: each entry is asserted still invalid, so a fix fails
#: `test_populated_artefact_is_valid_or_refused` until its entry is deleted.
KNOWN_INVALID: dict[tuple[str, str], str] = {}


def _message(base: str, artefact: object) -> StructureMessage:
    """A message holding one artefact, in the slot its kind belongs to.

    The slot is found at runtime, so no static type describes the splat — the
    one annotated local CLAUDE.md allows for a genuinely dynamic step, as in
    the bare gate's `_message_with`.
    """
    slot = next(f.name for f in attrs.fields(StructureMessage) if f.name in _SLOT_OF and _SLOT_OF[f.name] == base)
    by_slot: dict[str, Any] = {slot: [artefact]}
    return StructureMessage(**by_slot)


_SLOT_OF = {
    "agency_schemes": "agency_scheme",
    "codelists": "codelist",
    "constraints": "constraint",
    "data_provider_schemes": "data_provider_scheme",
    "data_structures": "data_structure",
    "dataflows": "dataflow",
}


def _outcome(message: StructureMessage, wire: str) -> str:
    """`valid`, `refused`, or `invalid: <the schema's first complaint>`."""
    try:
        written = serialize(message, version=wire)
    except ValidationError:
        return "refused"
    try:
        if wire == "json":
            assert_valid_sdmx_json(written, compile_json_schema("structure"))
        else:
            assert_valid_sdmx_ml(written, structure_schema(wire))
    except AssertionError as exc:
        return f"invalid: {exc}"
    return "valid"


@pytest.mark.parametrize("wire", _WIRE_VERSIONS)
@pytest.mark.parametrize("base", sorted(_BASES))
def test_every_base_is_valid_everywhere(base: str, wire: str):
    """The discrimination check: a case's outcome is caused by its state."""
    outcome = _outcome(_message(base, _BASES[base]()), wire)
    assert outcome == "valid", f"base {base!r} is {outcome} in {wire}, so cases over it say nothing there"


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_case_changes_its_base(name: str):
    case = CASES[name]
    assert case.build() != _BASES[case.base](), f"{name} builds its base unchanged"


@pytest.mark.parametrize("wire", _WIRE_VERSIONS)
@pytest.mark.parametrize("name", sorted(CASES))
def test_populated_artefact_is_valid_or_refused(name: str, wire: str):
    """Valid, or refused. A quietly-invalid document is a defect, and an
    expected one is in `KNOWN_INVALID` with the issue that removes it."""
    case = CASES[name]
    outcome = _outcome(_message(case.base, case.build()), wire)
    known = KNOWN_INVALID.get((name, wire))
    if known is not None:
        assert outcome.startswith("invalid"), (
            f"({name}, {wire}) is now {outcome} — delete its KNOWN_INVALID entry ({known})"
        )
        return
    assert not outcome.startswith("invalid"), (
        f"{name} ({case.source}) serialises to a document SDMX {wire} rejects, with no refusal: {outcome}. "
        "Fix it, or record it in KNOWN_INVALID against its issue"
    )


def test_known_invalid_names_real_cases():
    """A typo'd key would exempt nothing, silently."""
    bad = sorted(key for key in KNOWN_INVALID if key[0] not in CASES or key[1] not in _WIRE_VERSIONS)
    assert not bad, f"KNOWN_INVALID names {bad}, which are not (case, wire) pairs"


def test_every_base_has_a_slot():
    assert set(_SLOT_OF.values()) == set(_BASES)
