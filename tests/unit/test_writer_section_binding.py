"""The writer section tables must bind their accessor and converter to one type.

This is a *type-level* invariant. At runtime both `SectionEntry` and `_Section`
store `Callable[..., Any]`, so a mismatched pair — say ``codelists`` paired with
``to_dataflow`` — cannot be detected by calling anything; it fails deep inside
``write()`` with an `AttributeError` on the wrong artefact.

What makes the mismatch a *type* error is that the factories are generic: the
single type parameter appears in both callable parameters, so the checker has
to unify them. That is what these tests assert, via PEP 695's runtime
``__type_params__``. Dropping the type variable — the one edit that silently
restores the old unchecked behaviour — makes them fail.

They deliberately do not re-derive what the checker concludes. The real tables
in the four writers are checked by ``just check``; this guards the mechanism
those tables depend on.

See #210.
"""

import annotationlib
import re

import pytest

from sdmxlib.formats.sdmx_json.writer import _Section, _section
from sdmxlib.formats.sdmx_ml._common import SectionEntry, section_entry

pytestmark = pytest.mark.unit


def _mentions(annotation: object, type_var: str) -> bool:
    """Whether *annotation* refers to *type_var*.

    String-matches, on each annotation as written: the SDMX-ML factory's
    `StructureMessage` import is ``TYPE_CHECKING``-only, so its annotations
    cannot be evaluated at runtime.
    """
    return re.search(rf"\b{type_var}\b", str(annotation)) is not None


@pytest.mark.parametrize(
    ("factory", "bound_params"),
    [
        pytest.param(section_entry, ("get", "write"), id="sdmx_ml"),
        pytest.param(_section, ("get", "convert"), id="sdmx_json"),
    ],
)
def test_section_factory_binds_both_sides_to_one_type_var(factory, bound_params):
    """One type parameter, referenced by *both* callables — that is the binding."""
    type_params = factory.__type_params__
    assert len(type_params) == 1, f"{factory.__name__} should take exactly one type parameter"
    name = type_params[0].__name__

    for param in bound_params:
        annotation = annotationlib.get_annotations(factory, format=annotationlib.Format.STRING)[param]
        assert _mentions(annotation, name), (
            f"{factory.__name__}'s {param!r} must be typed in terms of {name} — "
            f"otherwise the accessor and converter are no longer forced to agree. Got: {annotation}"
        )


@pytest.mark.parametrize(
    ("entry_type", "bound_params"),
    [
        pytest.param(SectionEntry, ("get", "write"), id="sdmx_ml"),
        pytest.param(_Section, ("get", "convert"), id="sdmx_json"),
    ],
)
def test_section_entry_storage_is_erased(entry_type, bound_params):
    """The stored fields are `Any` on purpose — that is why the factory matters.

    If storage ever recovered the artefact type, the factories would be
    redundant. It cannot: `Callable` is contravariant in its parameters, so a
    common supertype of ``Callable[[Codelist], …]`` and ``Callable[[Dataflow],
    …]`` would need a parameter type that is a subtype of both — ``Never``,
    which is uncallable. The erasure is forced; the check has to happen at
    construction instead.
    """
    for param in bound_params:
        assert "Any" in annotationlib.get_annotations(entry_type, format=annotationlib.Format.STRING)[param]
