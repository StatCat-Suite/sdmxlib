"""The IM's abstract bases declare the fields every subclass supplies (GH-403).

`AnnotableArtefact`, `IdentifiableArtefact`, `NameableArtefact` and
`MaintainableArtefact` state their fields as properties whose body raises
`NotImplementedError`; an attrs subclass replaces each with a slotted field.
A field a base declares and a subclass forgets would raise on access, so each
base's declared properties are checked against every attrs class beneath it.
"""

import importlib
import pkgutil
from collections.abc import Iterable

import attrs
import pytest

import sdmxlib as sl
import sdmxlib.model
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import AnnotableArtefact, IdentifiableArtefact, MaintainableArtefact, NameableArtefact
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.istring import InternationalString

pytestmark = pytest.mark.unit


def _model_classes() -> list[type]:
    found: list[type] = []
    for info in pkgutil.walk_packages(sdmxlib.model.__path__, "sdmxlib.model."):
        module = importlib.import_module(info.name)
        found.extend(
            value
            for value in vars(module).values()
            if isinstance(value, type) and attrs.has(value) and value.__module__ == info.name
        )
    return found


def _declared(cls: type) -> list[str]:
    """The properties *cls* itself declares by a body that only raises NotImplementedError.

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


_BASES = (AnnotableArtefact, IdentifiableArtefact, NameableArtefact, MaintainableArtefact)


def test_each_base_declares_its_im_fields():
    assert {base.__name__: sorted(_declared(base)) for base in _BASES} == {
        "AnnotableArtefact": ["annotations"],
        "IdentifiableArtefact": ["id", "uri"],
        "NameableArtefact": ["description", "name"],
        "MaintainableArtefact": [
            "is_external_reference",
            "is_partial_language",
            "maintainer",
            "service_url",
            "structure_url",
            "version",
        ],
    }


def _missing(classes: Iterable[type], base: type) -> list[str]:
    """Each `Class.field` *base* declares, through its MRO, that *classes* do not supply as an attrs field."""
    declared = [name for ancestor in base.__mro__ for name in _declared(ancestor)]
    return [f"{cls.__name__}.{name}" for cls in classes for name in declared if name not in attrs.fields_dict(cls)]


#: How many attrs model classes sit beneath each base: exact, so a class that
#: gains or loses a base is a reviewed change.
_COUNTS = {"AnnotableArtefact": 65, "IdentifiableArtefact": 54, "NameableArtefact": 41, "MaintainableArtefact": 30}


@pytest.mark.parametrize("base", _BASES, ids=lambda base: base.__name__)
def test_every_class_beneath_a_base_supplies_its_fields(base: type):
    beneath = [cls for cls in _model_classes() if issubclass(cls, base)]
    assert len(beneath) == _COUNTS[base.__name__]
    assert _missing(beneath, base) == []


def test_the_field_check_can_fail():
    forgetful = attrs.make_class("Forgetful", ["id"])
    assert sorted(_missing([forgetful], NameableArtefact)) == [
        "Forgetful.annotations",
        "Forgetful.description",
        "Forgetful.name",
        "Forgetful.uri",
    ]


def test_a_derived_property_is_not_a_declared_field():
    assert "is_final" in vars(MaintainableArtefact)
    assert "is_final" not in _declared(MaintainableArtefact)


def test_the_bases_follow_the_im():
    assert issubclass(sl.Code, NameableArtefact)
    assert issubclass(sl.Dimension, IdentifiableArtefact)
    assert not issubclass(sl.Dimension, NameableArtefact)
    assert issubclass(sl.CubeRegion, AnnotableArtefact)
    assert not issubclass(sl.CubeRegion, IdentifiableArtefact)
    assert issubclass(MaintainableArtefact, NameableArtefact)


def test_no_model_instance_carries_a_dict():
    """Every base declares empty slots, so no item, component or artefact holds a `__dict__` (GH-403)."""
    assert [cls.__name__ for cls in _model_classes() if cls.__dictoffset__] == []


def test_the_dict_check_can_fail():
    class Unslotted:
        pass

    @attrs.frozen
    class Leaky(Unslotted):
        x: int

    assert Leaky.__dictoffset__


def test_generic_code_reads_through_the_bases():
    """Typed through a base alone: no narrowing to a concrete class."""

    def texts(item: NameableArtefact) -> tuple[str, InternationalString, Annotations]:
        return item.id, item.description, item.annotations

    codelist = Codelist(id="CL", maintainer="A", name={"en": "x"}, description={"en": "about"})
    code = sl.Code(id="A", name={"en": "a"})
    assert texts(codelist)[1]["en"].text == "about"
    assert texts(code)[0] == "A"


def test_the_organisation_schemes_share_a_base():
    """The five schemes 3.x writes with no version, named by one type."""
    schemes = sorted(cls.__name__ for cls in _model_classes() if issubclass(cls, sl.OrganisationScheme))
    assert schemes == [
        "AgencyScheme",
        "DataConsumerScheme",
        "DataProviderScheme",
        "MetadataProviderScheme",
        "OrganisationUnitScheme",
    ]
