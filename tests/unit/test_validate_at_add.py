"""`Registry.add` refuses what no SDMX format can write (#419).

Each case is a rule SDMX-ML 2.1, 3.0, 3.1 and SDMX-JSON agree on, as a pair:
the artefact that breaks it, refused by both registries, and the same artefact
with that one thing fixed, accepted. The accepted half is what makes the
refusal mean something — a check that refused every DSD would pass the first
half alone.
"""

from collections.abc import Callable

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.model.ref import SupportsUrn
from sdmxlib.model.validation import ValidationError
from tests.support.factories import code_urn, concept_ref

pytestmark = pytest.mark.unit

_CODELIST = sl.Ref[sl.Codelist].unresolved(sl.Codelist.urn_for(agency="AG", id="CL", version="1.0"))
_CODES = sl.Facet(type="AlphaNumeric", max_length=3)


def _dsd(
    *, concept: bool = True, enumeration_format: sl.Facet | None = None, enumerated: bool = True
) -> sl.DataStructure:
    return sl.DataStructure(
        id="DSD",
        maintainer="AG",
        name={"en": "d"},
        dimensions=[
            sl.Dimension(
                id="FREQ",
                concept=concept_ref("FREQ") if concept else None,
                representation=_CODELIST if enumerated else sl.Facet(type="String"),
                enumeration_format=enumeration_format,
            )
        ],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )


def _msd(*, child_concept: bool = True, enumeration_format: sl.Facet | None = None) -> sl.MetadataStructure:
    child = sl.MetadataAttribute(
        id="COVERAGE",
        concept=concept_ref("COVERAGE") if child_concept else None,
        representation=_CODELIST,
        enumeration_format=enumeration_format,
    )
    parent = sl.MetadataAttribute(id="QUALITY", concept=concept_ref("QUALITY"), children=[child])
    return sl.MetadataStructure(id="MSD", maintainer="AG", name={"en": "m"}, attributes=[parent])


def _concepts(enumeration_format: sl.Facet) -> sl.ConceptScheme:
    concept = sl.Concept(
        id="CODED", name={"en": "c"}, core_representation=_CODELIST, enumeration_format=enumeration_format
    )
    return sl.ConceptScheme(id="CS", maintainer="AG", name={"en": "cs"}, concepts=[concept])


def _hierarchy(*, code: bool = True, coding_format: sl.Facet | None = None) -> sl.Hierarchy:
    level = sl.Level(id="L1", name={"en": "l"}, coding_format=coding_format)
    node = sl.HierarchicalCode(id="A", code=code_urn("CL", "A") if code else None, level=level)
    return sl.Hierarchy(id="H", maintainer="AG", name={"en": "h"}, levels=[level], tree=[node])


#: (refused, accepted, the path the refusal names). Each accepted artefact
#: differs from its refused one in exactly the field the rule is about.
_CASES: dict[str, tuple[Callable[[], SupportsUrn], Callable[[], SupportsUrn], str]] = {
    "component without a concept": (
        lambda: _dsd(concept=False),
        _dsd,
        "dimensions[FREQ].concept",
    ),
    "nested metadata attribute without a concept": (
        lambda: _msd(child_concept=False),
        _msd,
        "attributes[QUALITY].children[COVERAGE].concept",
    ),
    "hierarchical code without a code": (
        lambda: _hierarchy(code=False),
        _hierarchy,
        "codes[A].code",
    ),
    "enumeration format beside a text format": (
        lambda: _dsd(enumerated=False, enumeration_format=_CODES),
        lambda: _dsd(enumeration_format=_CODES),
        "dimensions[FREQ].enumeration_format",
    ),
    "enumeration format with decimals": (
        lambda: _dsd(enumeration_format=attrs.evolve(_CODES, decimals=2)),
        lambda: _dsd(enumeration_format=_CODES),
        "enumeration_format.decimals",
    ),
    "enumeration format with a decimal bound": (
        lambda: _dsd(enumeration_format=attrs.evolve(_CODES, max_value=9.5)),
        lambda: _dsd(enumeration_format=attrs.evolve(_CODES, max_value=9.0)),
        "enumeration_format.max_value",
    ),
    "metadata attribute's enumeration format, multilingual": (
        lambda: _msd(enumeration_format=attrs.evolve(_CODES, is_multilingual=True)),
        lambda: _msd(enumeration_format=_CODES),
        "children[COVERAGE].enumeration_format.is_multilingual",
    ),
    "concept's enumeration format with decimals": (
        lambda: _concepts(attrs.evolve(_CODES, decimals=2)),
        lambda: _concepts(_CODES),
        "concepts[CODED].enumeration_format.decimals",
    ),
    "coding format of a value type": (
        lambda: _hierarchy(coding_format=sl.Facet(type="Decimal")),
        lambda: _hierarchy(coding_format=sl.Facet(type="Numeric")),
        "levels[L1].coding_format.type",
    ),
    "coding format with a time facet": (
        lambda: _hierarchy(coding_format=attrs.evolve(_CODES, time_interval="P1D")),
        lambda: _hierarchy(coding_format=_CODES),
        "levels[L1].coding_format.time_interval",
    ),
}


@pytest.mark.parametrize("case", list(_CASES))
def test_a_local_registry_refuses_it(case: str) -> None:
    refused, accepted, path = _CASES[case]
    with sl.LocalRegistry(":memory:") as registry:
        with pytest.raises(ValidationError) as caught:
            registry.add(refused())
        _assert_names(caught.value, path)
        registry.add(accepted())


@pytest.mark.parametrize("case", list(_CASES))
def test_an_in_memory_registry_refuses_it(case: str) -> None:
    refused, accepted, path = _CASES[case]
    registry = sl.InMemoryRegistry()
    with pytest.raises(ValidationError) as caught:
        registry.add_all([refused()])
    _assert_names(caught.value, path)
    registry.add(accepted())


def _assert_names(error: ValidationError, path: str) -> None:
    """One issue, at the field the rule is about — not merely some refusal."""
    assert len(error.issues) == 1, error
    assert error.issues[0].path.endswith(f".{path}"), error.issues[0].path


def test_each_is_refused_as_it_is_built_naming_its_class() -> None:
    """Refused where it is built (#492), not later in a batch, so the class names it."""
    with pytest.raises(ValidationError) as dsd:
        _dsd(concept=False)
    with pytest.raises(ValidationError) as hierarchy:
        _hierarchy(code=False)
    assert [i.path for i in dsd.value.issues] == ["DataStructure.dimensions[FREQ].concept"]
    assert [i.path for i in hierarchy.value.issues] == ["Hierarchy.codes[A].code"]


def test_a_refused_batch_adds_nothing() -> None:
    """One bad artefact refuses the batch, so the store is not left half-written."""
    with sl.LocalRegistry(":memory:") as registry:
        with pytest.raises(ValidationError):
            registry.add_all([_msd(), _dsd(concept=False)])
        assert registry.query(sl.q.msd).count() == 0
