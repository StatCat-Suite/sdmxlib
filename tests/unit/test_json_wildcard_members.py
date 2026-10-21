"""A codelist extension's `%` member value, written to SDMX-JSON (#451).

SDMX-ML 3.x types a member value as `WildcardedMemberValueType`, which may hold
`%`; SDMX-JSON's `MemberValueType` is a plain id. The JSON writer wrote the
pattern anyway, and the document failed the schema. It now writes the codes
the pattern matches, each with the member's cascade, and refuses when the
extended codelist cannot be found or the pattern matches nothing.
"""

import pytest

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.gaps import unrepresentable
from sdmxlib.model.codelist import (
    CascadeSelection,
    Code,
    Codelist,
    CodelistExtension,
    CodeSelection,
    InclusionMode,
    MemberValue,
    wildcard_misfits,
)
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.validation import ValidationError
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema
from tests.support.roundtrip import as_structure

pytestmark = pytest.mark.unit

_SOURCE = Codelist(
    id="CL_SOURCE",
    maintainer="AG",
    name={"en": "source"},
    codes=[Code(id=code_id, name={"en": code_id}) for code_id in ("AA", "AB", "B")],
)


def _extending(*members: MemberValue) -> Codelist:
    return Codelist(
        id="CL_EXT",
        maintainer="AG",
        name={"en": "ext"},
        codes=[Code(id="OWN", name={"en": "own"})],
        extensions=(
            CodelistExtension(
                codelist=Ref[Codelist].unresolved(_SOURCE.urn),
                selection=CodeSelection(mode=InclusionMode.INCLUDE, members=members),
            ),
        ),
    )


_WILD = _extending(MemberValue("A%", CascadeSelection.INCLUDE_DESCENDANTS), MemberValue("B"))


def _members(codelist: Codelist) -> list[tuple[str, CascadeSelection | None]]:
    (extension,) = codelist.extensions
    assert extension.selection is not None
    return [(m.value, m.cascade) for m in extension.selection.members]


def test_a_wildcard_is_written_to_json_as_the_codes_it_matches() -> None:
    written = serialize(StructureMessage(codelists=[_SOURCE, _WILD]), version="json")
    for schema in ("2.0", "2.1"):
        assert_valid_sdmx_json(written, compile_json_schema("structure", schema))
    back = {cl.id: cl for cl in as_structure(parse(written)).codelists}
    assert _members(back["CL_EXT"]) == [
        ("AA", CascadeSelection.INCLUDE_DESCENDANTS),
        ("AB", CascadeSelection.INCLUDE_DESCENDANTS),
        ("B", None),
    ]
    assert [gap.field for gap in unrepresentable(_WILD, "json")] == ["extensions[].selection.members[].value"]


def test_the_expanded_codelist_inherits_the_same_codes() -> None:
    expanded = _WILD.expand_wildcards([_SOURCE])
    assert expanded.apply_extensions([_SOURCE]).codes.ids() == _WILD.apply_extensions([_SOURCE]).codes.ids()


def test_sdmx_ml_3x_keeps_the_wildcard() -> None:
    (back,) = [
        cl
        for cl in as_structure(parse(serialize(StructureMessage(codelists=[_SOURCE, _WILD]), version="3.0"))).codelists
        if cl.id == "CL_EXT"
    ]
    assert _members(back)[0] == ("A%", CascadeSelection.INCLUDE_DESCENDANTS)
    assert unrepresentable(_WILD, "3.0") == ()


def test_a_plain_selection_is_left_alone() -> None:
    plain = _extending(MemberValue("B"))
    assert plain.expand_wildcards() is plain
    assert unrepresentable(plain, "json") == ()


@pytest.mark.parametrize(
    ("codelists", "message"),
    [
        pytest.param([_WILD], "neither resolved nor among the codelists given", id="extended-missing"),
        pytest.param([_SOURCE, _extending(MemberValue("Z%"))], "member 'Z%' matches no code", id="matches-nothing"),
    ],
)
def test_json_refuses_a_wildcard_it_cannot_spell_out(codelists: list[Codelist], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        serialize(StructureMessage(codelists=codelists), version="json")
    assert wildcard_misfits(codelists[-1], codelists)
