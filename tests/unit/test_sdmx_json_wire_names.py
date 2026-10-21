"""SDMX-JSON wire names, and the overrides that cannot be derived.

`_camel_rename` turns a snake_case Struct field into its SDMX-JSON name, and
for almost every field the wire name *is* the camel-cased Python name. Two are
not, and both exist because the spec spells something differently from
idiomatic Python — in opposite directions:

| Python | wire | why |
| --- | --- | --- |
| `agency_id` | `agencyID` | the SDMX-IM keeps the acronym |
| `is_multilingual` | `isMultiLingual` | the spec splits a word English writes as one |

**Why this file exists.** The second override was found the hard way. The
field used to be `is_multi_lingual`, whose camel-cased form is `isMultiLingual`
by construction — so the wire name was correct *by accident of the Python
spelling*. Renaming it to `is_multilingual`, which is how English spells the
word, silently re-derived the wire name as `isMultilingual` and broke both
reading and writing SDMX-JSON. Nothing failed loudly: msgspec calls the
renamer once per field at class definition, `omit_defaults` drops the field
when unset, and every round-trip test that did not set it kept passing.

An override keyed on a field name is silent in the other direction too: rename
or delete the field and the entry stops matching, leaving a mapping that
covers nothing. So this guard checks both halves — the override applies, and
every override still corresponds to a real field.
"""

import msgspec
import pytest

from sdmxlib.formats.sdmx_json import _dto, _structure_dto
from sdmxlib.formats.sdmx_json._dto import _WIRE_NAMES, _camel_rename

pytestmark = pytest.mark.unit


def test_the_overrides_are_applied() -> None:
    """The mapping itself, at its two entries."""
    assert _camel_rename("agency_id") == "agencyID"
    assert _camel_rename("is_multilingual") == "isMultiLingual"


def test_ordinary_fields_still_camel_case() -> None:
    """A guard that over-fires would rename every field. These are the shapes
    around the overrides: one word, two words, and a trailing acronym-free tail.
    """
    assert _camel_rename("id") == "id"
    assert _camel_rename("max_length") == "maxLength"
    assert _camel_rename("is_sequence") == "isSequence"
    assert _camel_rename("is_external_reference") == "isExternalReference"


def _wire_names_of(struct: type[msgspec.Struct]) -> set[str]:
    return {f.encode_name for f in msgspec.structs.fields(struct)}


def test_the_multilingual_field_reaches_the_wire_under_the_spec_name() -> None:
    """End to end on the real DTO, not just on the renamer.

    `_camel_rename` could be correct while the Struct failed to use it — the
    renamer is opt-in per class (`rename=_camel_rename`), so a DTO declared
    without it would camel-case nothing and this is what notices.
    """
    names = _wire_names_of(_structure_dto.Format)

    assert "isMultiLingual" in names
    assert "isMultilingual" not in names, "the derived-by-accident spelling"
    assert "is_multilingual" not in names, "the raw Python name"


def test_every_override_still_matches_a_real_field() -> None:
    """The half an override cannot check itself.

    A mapping keyed on a field name goes stale silently: rename the field and
    the entry simply stops matching, leaving the wire name to be derived —
    which is the exact break this file was written for, one step removed.

    Derived by sweeping the DTO modules for Struct fields rather than
    hand-listing, so a field moving between modules does not fake a pass.
    """
    declared: set[str] = set()
    for module in (_dto, _structure_dto):
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, msgspec.Struct):
                declared |= {f.name for f in msgspec.structs.fields(value)}

    stale = set(_WIRE_NAMES) - declared
    assert not stale, (
        f"wire-name overrides matching no Struct field: {sorted(stale)}. "
        "The field was renamed or removed and its wire name is now being "
        "derived — which is how `isMultiLingual` broke."
    )
