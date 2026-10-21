"""Every `Ref`-typed field takes a `Ref` or `SdmxUrn`, and refuses URN text (#418).

#418 reported one field, `CodelistExtension.codelist`, storing a URN string
or a bare `SdmxUrn` that the SDMX-ML 3.0 writer then failed on with an
`AttributeError` naming neither the artefact nor the field. A sweep found the
class, not the instance: 33 of the 34 `Ref`-typed fields the maximal fixture
reaches stored a `str` as given, and 20 stored a bare `SdmxUrn`.

The rule now, one mechanism for all of them (`model/ref.py`):

- an `SdmxUrn` is wrapped as an unresolved `Ref` (`to_ref`, `to_optional_ref`,
  `to_refs`, or the field's own converter, which already did this);
- a `str` is refused with a `TypeError` naming the field and the parse that
  fixes it (`refuse_urn_text`), on construction and on `attrs.evolve`. Converting
  text was declined, as `get()` declined it in #370: the typed URN is the API.

Two kinds of check, because each misses what the other sees. The static one
covers every field of every model class, including the ones no fixture builds;
the behavioural one proves the mechanism does what the static one assumes.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections.abc import Callable
from typing import TypeIs

import attrs
import pytest

import sdmxlib.model
from sdmxlib.model.codelist import Codelist, CodelistExtension
from sdmxlib.model.collections import ArtefactList, ItemList
from sdmxlib.model.ref import Ref, refuse_urn_text
from sdmxlib.model.urn import SdmxUrn
from tests.support import maximal

pytestmark = pytest.mark.unit

_URN = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:CL_A(1.0)"


def _is_attrs(obj: object) -> TypeIs[attrs.AttrsInstance]:
    """`attrs.has` narrows the class it is given, not the instance; this narrows the instance."""
    return attrs.has(type(obj))


def _ref_fields() -> list[tuple[type, attrs.Attribute[object]]]:
    """Every init field of every attrs class in `sdmxlib.model` whose type names a `Ref`."""
    found: dict[type, None] = {}
    for info in pkgutil.walk_packages(sdmxlib.model.__path__, "sdmxlib.model."):
        module = importlib.import_module(info.name)
        for obj in vars(module).values():
            if inspect.isclass(obj) and obj.__module__ == module.__name__ and attrs.has(obj) and obj is not Ref:
                found[obj] = None
    # Through a `type` alias too: `Representation` names a `Ref` only in its
    # value, and a field annotated with it was invisible here — which is how
    # `Concept.core_representation` went without the converter every other
    # `Ref` field has (#422).
    return [
        (cls, f)
        for cls in found
        for f in attrs.fields(cls)
        if f.init and "Ref[" in str(getattr(f.type, "__value__", f.type))
    ]


def test_every_ref_field_is_counted() -> None:
    """Exact, so a new `Ref` field is noticed here and given the same treatment."""
    assert len(_ref_fields()) == 51, "a Ref-typed field was added or removed — check it below, then update the count"


@pytest.mark.parametrize(
    ("cls", "attribute"),
    [pytest.param(cls, f, id=f"{cls.__name__}.{f.name}") for cls, f in _ref_fields()],
)
def test_every_ref_field_refuses_urn_text_and_converts_an_urn(cls: type, attribute: attrs.Attribute[object]) -> None:
    assert attribute.validator is refuse_urn_text, f"{cls.__name__}.{attribute.name} does not refuse URN text"
    assert attribute.converter is not None, f"{cls.__name__}.{attribute.name} stores a bare SdmxUrn as given"


def _maximal_instances() -> list[tuple[attrs.AttrsInstance, str, object]]:
    """(instance, field, sample value) for each Ref field the maximal fixture populates."""
    seen: set[int] = set()
    out: list[tuple[attrs.AttrsInstance, str, object]] = []
    covered: set[tuple[type, str]] = set()

    def walk(obj: object) -> None:
        if id(obj) in seen:
            return
        seen.add(id(obj))
        if _is_attrs(obj) and not isinstance(obj, Ref):
            for f in attrs.fields(type(obj)):
                value = getattr(obj, f.name)
                if f.init and "Ref[" in str(f.type) and (type(obj), f.name) not in covered and value:
                    covered.add((type(obj), f.name))
                    out.append((obj, f.name, value))
                walk(value)
        elif isinstance(obj, dict):
            for x in obj.values():
                walk(x)
        elif isinstance(obj, (list, tuple, ItemList, ArtefactList)):
            for x in obj:
                walk(x)

    walk(maximal.message())
    return out


#: `attrs.evolve` over a field named at runtime, with a value of the wrong type
#: on purpose — what the checker refuses at a direct call, left to the runtime
#: (the spelling `model.version_rewrite` uses for its own runtime field map).
_evolve: Callable[..., object] = attrs.evolve


def _first(value: object) -> object:
    return value[0] if isinstance(value, (list, tuple)) and value else value


@pytest.mark.parametrize(
    ("instance", "name", "value"),
    [pytest.param(*row, id=f"{type(row[0]).__name__}.{row[1]}") for row in _maximal_instances()],
)
def test_text_is_refused_and_an_urn_is_wrapped(instance: attrs.AttrsInstance, name: str, value: object) -> None:
    """Through `attrs.evolve`, which runs the same converter and validator as `__init__` (frozen since #492)."""
    sample = _first(value)
    if not isinstance(sample, Ref):
        pytest.skip(f"{type(instance).__name__}.{name} holds a {type(sample).__name__} here, not a Ref")
    as_list = isinstance(value, (list, tuple))
    text: object = [str(sample.urn)] if as_list else str(sample.urn)
    urn: object = [sample.urn] if as_list else sample.urn

    keyword = attrs.fields_dict(type(instance))[name].alias or name
    with pytest.raises(TypeError, match=rf"{type(instance).__name__}\.{name} got a URN string"):
        _evolve(instance, **{keyword: text})
    stored = _first(getattr(_evolve(instance, **{keyword: urn}), name))
    assert isinstance(stored, Ref)
    assert stored.urn == sample.urn


def test_the_behavioural_sweep_reaches_most_fields() -> None:
    """A discrimination floor, not an exact count: the fixture decides how many it builds.

    35 at the time of writing; the static test above covers the remainder.
    """
    assert len(_maximal_instances()) >= 30


def test_the_reported_case() -> None:
    """#418's two spellings, both accepted until now and both fatal to the 3.0 writer.

    Through `attrs.evolve` with the field held in a variable, because the
    checker already refuses both at a direct call — which is the point, and
    leaves only the runtime to test.
    """
    ext = CodelistExtension(codelist=Codelist.parse_urn(_URN))
    field_name = "codelist"
    with pytest.raises(TypeError, match=r"Codelist\.parse_urn\(text\)"):
        _evolve(ext, **{field_name: _URN})
    rebuilt = _evolve(ext, **{field_name: SdmxUrn.parse(_URN)})
    assert isinstance(rebuilt, CodelistExtension)
    assert isinstance(rebuilt.codelist, Ref)
