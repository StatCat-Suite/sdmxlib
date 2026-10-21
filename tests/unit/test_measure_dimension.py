"""A MeasureDimension is 2.1-only, and must not vanish quietly (#305).

#305 was filed during the #231 audit claiming a DSD holding a MeasureDimension
"cannot be serialised at all — AttributeError, every version". **That report
was wrong**, and the way it was wrong is the useful part: the reproduction put
the component in `dimensions`, which is typed `ItemList[Dimension]` and is not
where it goes. There is a dedicated `measure_dimension` slot, and used properly
the component wrote valid 2.1 and round-tripped the whole time.

Two real defects were behind the bad report, and this file pins both.

## 1. The misuse crashed instead of explaining

`MeasureDimension` is deliberately not a `Component` subclass — its
representation is a ConceptScheme rather than a Codelist — so it carries no
`enumeration_format`. Every walker over DSD components assumed one, and
misplacing the component surfaced as

    AttributeError: 'MeasureDimension' object has no attribute 'enumeration_format'

from deep inside validation, naming a field the user never mentioned. Now it is
a `ValidationIssue` naming the slot to use.

## 2. The 3.x and JSON writers dropped it silently

The larger defect, and invisible from the crash. SDMX 3.0 removed the
cross-sectional measure dimension — the 3.0 and 3.1 schemas contain no
`MeasureDimension` element at all — and the writers responded by emitting
nothing:

    serialize(msg, version="3.0")   ->  valid document, component gone
    as_structure(parse(that))                     ->  measure_dimension is None

Valid output, silently missing a component, no error. That is the exact failure
class %11 existed to remove, found after it closed. It now refuses, the way
other downgrade hazards do.

## Why the field is not simply added to the class

2.1's `MeasureDimensionRepresentationType` is a restriction permitting only
`<Enumeration>` — no `EnumerationFormat`, no `TextFormat`. So the field would
be meaningless on this component, and the walkers tolerate its absence instead.
"""

import pytest

from sdmxlib.formats import parse, serialize
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure, MeasureDimension
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.validation import ValidationError
from tests.support.roundtrip import as_structure
from tests.support.xsd import assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

_CS = "urn:sdmx:org.sdmx.infomodel.conceptscheme.ConceptScheme=AG:CS(1.0)"


def _concept(concept_id: str) -> Ref[Concept]:
    return Ref[Concept].unresolved(
        Concept.parse_urn(f"urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=AG:CS(1.0).{concept_id}")
    )


@pytest.fixture
def scheme() -> ConceptScheme:
    return ConceptScheme(
        id="CS",
        maintainer="AG",
        version="1.0",
        name={"en": "Concepts"},
        concepts=[Concept(id=i, name={"en": i}) for i in ("FREQ", "M1", "OBS_VALUE")],
    )


def _measure_dimension() -> MeasureDimension:
    return MeasureDimension(
        id="M1",
        concept=_concept("M1"),
        representation=Ref[ConceptScheme].unresolved(ConceptScheme.parse_urn(_CS)),
    )


def _dsd(*, misplaced: bool) -> DataStructure:
    """A DSD carrying the measure dimension in the right slot, or the wrong one."""
    dims: list[Dimension] = [Dimension(id="FREQ", concept=_concept("FREQ"))]
    if misplaced:
        # A `MeasureDimension` is not a `Dimension` — putting one here is the
        # misplacement this fixture exists to build.
        dims.append(_measure_dimension())  # pyrefly: ignore[bad-argument-type]
    return DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name={"en": "Structure"},
        dimensions=dims,
        measure_dimension=None if misplaced else _measure_dimension(),
        measures=[Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"))],
    )


def _message(scheme: ConceptScheme, dsd: DataStructure) -> StructureMessage:
    return StructureMessage(concept_schemes=[scheme], data_structures=[dsd])


# ── It works, which is what #305 got wrong ───────────────────────────────────


def test_a_measure_dimension_writes_valid_21_and_round_trips(scheme: ConceptScheme) -> None:
    """The correction to the issue, asserted first so nobody re-derives it.

    Used as designed this was never broken. Pinning it also means a future
    tightening of the refusal below cannot quietly take 2.1 with it.
    """
    written = serialize(_message(scheme, _dsd(misplaced=False)), version="2.1")
    assert_valid_sdmx_ml(written, compile_structure_schema("2.1"))
    back = as_structure(parse(written)).data_structures[0]
    assert back.measure_dimension is not None
    assert back.measure_dimension.id == "M1"


# ── Defect 1: misuse must explain itself ─────────────────────────────────────


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1", "json"])
def test_a_misplaced_measure_dimension_is_refused_with_a_useful_message(scheme: ConceptScheme, version: str) -> None:
    """Every version, because misplacement is not a wire-format question.

    The check sits *before* the 2.1 early return for exactly this reason. Below
    it, a 2.1 write skipped validation and crashed in the writer instead —
    which is the AttributeError the issue reported.
    """
    with pytest.raises(ValidationError, match="cannot live in `dimensions`") as excinfo:
        serialize(_message(scheme, _dsd(misplaced=True)), version=version)
    assert "measure_dimension=" in str(excinfo.value), "the message must name the slot to use"


def test_the_misuse_no_longer_raises_attributeerror(scheme: ConceptScheme) -> None:
    """Stated as its own test because the exception *type* is the regression.

    A `ValidationError` is inspectable and names a path; an `AttributeError`
    mentioning `enumeration_format` names a field the caller never touched.
    """
    with pytest.raises(ValidationError):
        serialize(_message(scheme, _dsd(misplaced=True)), version="2.1")


# ── Defect 2: no silent drop on downgrade ────────────────────────────────────


@pytest.mark.parametrize("version", ["3.0", "3.1", "json"])
def test_no_3x_form_is_refused_rather_than_dropped(scheme: ConceptScheme, version: str) -> None:
    """The serious one. Before #305 these produced a valid document minus a component."""
    with pytest.raises(ValidationError, match=r"no SDMX-(?:ML 3|JSON)"):
        serialize(_message(scheme, _dsd(misplaced=False)), version=version)


def test_the_refusal_explains_why_and_what_to_do(scheme: ConceptScheme) -> None:
    with pytest.raises(ValidationError) as excinfo:
        serialize(_message(scheme, _dsd(misplaced=False)), version="3.0")
    text = str(excinfo.value)
    assert "DataStructure:DSD.measure_dimension" in text
    assert "measure list" in text, "should say what 3.0 replaced it with"
    assert "2.1" in text, "should say which version can carry it"


def test_the_drop_this_replaces_really_did_happen(scheme: ConceptScheme) -> None:
    """Bypassing validation reproduces the old behaviour, so the fix is anchored.

    Without this the refusal above is asserted against nothing: it would pass
    just as well if the writers had always emitted the component correctly and
    the rule were pure superstition.
    """
    written = serialize(_message(scheme, _dsd(misplaced=False)), version="3.0", validate=False)
    assert_valid_sdmx_ml(written, compile_structure_schema("3.0")), "the emitted document is valid…"
    assert as_structure(parse(written)).data_structures[0].measure_dimension is None, (
        "…and the component is simply gone"
    )


def test_a_dsd_without_one_is_unaffected(scheme: ConceptScheme) -> None:
    """The rule must not cost anything to the overwhelmingly common case."""
    plain = DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name={"en": "Structure"},
        dimensions=[Dimension(id="FREQ", concept=_concept("FREQ"))],
        measures=[Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"))],
    )
    for version in ("2.1", "3.0", "3.1", "json"):
        assert serialize(_message(scheme, plain), version=version)
