"""Every kind of DSD component needs a concept, refused as it is built (#506).

The rule was applied to `dimensions`, `attributes` and `measures` — the three
lists — and so missed the two single-valued components. A `TimeDimension` or
`MeasureDimension` with no concept was built, stored, and written as SDMX-ML
its XSD rejects. The check below is over every component *class*, so the
next one cannot be missed the same way.
"""

import inspect
from collections.abc import Callable

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.model import datastructure
from sdmxlib.model.datastructure import ConceptIdentityMixin
from sdmxlib.model.errors import ValidationError

pytestmark = pytest.mark.unit

_CONCEPTS = sl.ConceptScheme(
    id="CS",
    maintainer="AG",
    name={"en": "Concepts"},
    concepts=[sl.Concept(id=c, name={"en": c}) for c in ("GEO", "OBS_VALUE", "X")],
)

_BASE = sl.DataStructure(
    id="DSD",
    maintainer="AG",
    name={"en": "d"},
    dimensions=[sl.Dimension(id="GEO", concept=_CONCEPTS.ref_to("GEO"))],
    measures=[sl.Measure(id="OBS_VALUE", concept=_CONCEPTS.ref_to("OBS_VALUE"))],
)

type _Concept = sl.Ref[sl.Concept] | None

#: `_BASE` with one component of each class added, with or without a concept.
#: `evolve`, because it runs the construction rules as the constructor does (#492).
_SLOTS: dict[type, Callable[[_Concept], sl.DataStructure]] = {
    sl.Dimension: lambda c: attrs.evolve(_BASE, dimensions=[*_BASE.dimensions, sl.Dimension(id="X", concept=c)]),
    sl.TimeDimension: lambda c: attrs.evolve(_BASE, time_dimension=sl.TimeDimension(concept=c)),
    sl.MeasureDimension: lambda c: attrs.evolve(_BASE, measure_dimension=sl.MeasureDimension(id="X", concept=c)),
    sl.Attribute: lambda c: attrs.evolve(
        _BASE, attributes=[sl.Attribute(attachment=sl.AttachmentLevel.DATASET, id="X", concept=c)]
    ),
    sl.Measure: lambda c: attrs.evolve(_BASE, measures=[*_BASE.measures, sl.Measure(id="X", concept=c)]),
}


def test_every_component_class_has_a_slot_here() -> None:
    """Exact, so a component class added later fails here until it is covered."""
    classes = {
        cls
        for cls in vars(datastructure).values()
        if inspect.isclass(cls) and issubclass(cls, ConceptIdentityMixin) and cls is not ConceptIdentityMixin
    }
    assert classes == set(_SLOTS)


@pytest.mark.parametrize("kind", list(_SLOTS), ids=lambda k: k.__name__)
def test_a_component_without_a_concept_is_refused(kind: type) -> None:
    with pytest.raises(ValidationError, match="ConceptIdentity is required"):
        _SLOTS[kind](None)


@pytest.mark.parametrize("kind", list(_SLOTS), ids=lambda k: k.__name__)
def test_the_same_component_with_one_builds(kind: type) -> None:
    """The discrimination check: the refusal is the missing concept, not the slot."""
    assert _SLOTS[kind](_CONCEPTS.ref_to("X")).id == "DSD"
