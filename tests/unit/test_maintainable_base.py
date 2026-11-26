"""`MaintainableArtefact` declares every maintainable's fields, and each artefact class supplies them (GH-403).

The base states each field as a property whose body raises
`NotImplementedError`; an attrs subclass replaces it with a slotted field.
A field the base declares and a subclass forgets would raise on access, so
each declared property is checked against every artefact class.
"""

from collections.abc import Iterable

import attrs
import pytest

from sdmxlib.model._items import _maintainable_classes
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.istring import InternationalString

pytestmark = pytest.mark.unit


def _declared(cls: type) -> list[str]:
    """The properties on *cls* whose body only raises NotImplementedError: the fields it declares.

    Read from the getter's code: a derived property (`is_final`, `agency_id`)
    also raises NotImplementedError on the base, through the field it reads, so
    calling the getter cannot tell the two apart.
    """
    return [
        name
        for name, value in vars(cls).items()
        if isinstance(value, property)
        and value.fget is not None
        and value.fget.__code__.co_names == ("NotImplementedError",)
    ]


def test_the_base_declares_the_fields_every_maintainable_has():
    assert sorted(_declared(MaintainableArtefact)) == [
        "annotations",
        "description",
        "id",
        "is_external_reference",
        "is_partial_language",
        "maintainer",
        "name",
        "service_url",
        "structure_url",
        "uri",
        "version",
    ]


def _missing(classes: Iterable[type]) -> list[str]:
    """Each `Class.field` the base declares and *classes* do not supply as an attrs field."""
    declared = _declared(MaintainableArtefact)
    return [f"{cls.__name__}.{name}" for cls in classes for name in declared if name not in attrs.fields_dict(cls)]


def test_every_artefact_class_supplies_every_declared_field():
    classes = _maintainable_classes()
    assert len(classes) == 30
    assert _missing(classes) == []


def test_the_field_check_can_fail():
    forgetful = attrs.make_class("Forgetful", ["id", "name"])
    assert "Forgetful.annotations" in _missing([forgetful])
    assert "Forgetful.description" in _missing([forgetful])


def test_a_derived_property_is_not_a_declared_field():
    assert "is_final" in vars(MaintainableArtefact)
    assert "is_final" not in _declared(MaintainableArtefact)


def test_generic_code_reads_annotations_and_description():
    """Typed through the base alone: no narrowing to a concrete class."""

    def texts(artefact: MaintainableArtefact) -> tuple[InternationalString, Annotations]:
        return artefact.description, artefact.annotations

    codelist = Codelist(id="CL", maintainer="A", name={"en": "x"}, description={"en": "about"})
    description, annotations = texts(codelist)
    assert description["en"].text == "about"
    assert list(annotations) == []
