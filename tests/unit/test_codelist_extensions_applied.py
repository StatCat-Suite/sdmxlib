"""A codelist's extensions, applied — and written to SDMX-ML 2.1 that way (#421).

SDMX 3.x lets a codelist inherit another's codes: all or a selection (by id,
`%` wildcard and cascade), each behind an optional prefix. SDMX-ML 2.1 has no
extension, so the 2.1 writer inlines what is inherited, and refuses when an
extended codelist cannot be found rather than writing only the codelist's own
codes.
"""

import pytest
from lxml import etree

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.gaps import unrepresentable
from sdmxlib.model.codelist import (
    CascadeSelection,
    Code,
    Codelist,
    CodelistExtension,
    CodeSelection,
    GeoFeatureSetCode,
    GeographicCodelist,
    InclusionMode,
    MemberValue,
    unresolved_extensions,
)
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.validation import ValidationError
from tests.support.roundtrip import as_structure
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit


def _codelist(cl_id: str, *codes: tuple[str, str | None], extensions: tuple[CodelistExtension, ...] = ()) -> Codelist:
    return Codelist(
        id=cl_id,
        maintainer="AG",
        name={"en": cl_id},
        codes=[Code(id=code_id, name={"en": f"{cl_id} {code_id}"}, parent_id=parent) for code_id, parent in codes],
        extensions=extensions,
    )


def _extending(
    source: Codelist, selection: CodeSelection | None = None, prefix: str | None = None
) -> CodelistExtension:
    """An extension by URN only, so it must be found among the codelists given."""
    return CodelistExtension(codelist=Ref[Codelist].unresolved(source.urn), selection=selection, prefix=prefix)


def _select(mode: InclusionMode, *members: tuple[str, CascadeSelection | None]) -> CodeSelection:
    return CodeSelection(mode=mode, members=tuple(MemberValue(value, cascade) for value, cascade in members))


#: A, with B and C under it, and D under C; E a second root.
_TREE = _codelist("CL_TREE", ("A", None), ("B", "A"), ("C", "A"), ("D", "C"), ("E", None))


def _ids(codelist: Codelist) -> list[tuple[str, str | None]]:
    return [(code.id, code.parent_id) for code in codelist.codes]


def test_whole_codelist_is_inherited_after_the_codelists_own() -> None:
    applied = _codelist("CL_X", ("Z", None), extensions=(_extending(_TREE),)).apply_extensions([_TREE])
    assert _ids(applied) == [("Z", None), *_ids(_TREE)]
    assert applied.extensions == ()


def test_own_codes_win_and_a_later_extension_wins() -> None:
    """IM §4.3.3.2 and FMR agree (#486): the later extension's B, in the place B first took."""
    first = _codelist("CL_FIRST", ("A", None), ("B", None))
    second = _codelist("CL_SECOND", ("A", None), ("B", None), ("C", None))
    own = _codelist("CL_OWN", ("A", None), extensions=(_extending(first), _extending(second)))
    applied = own.apply_extensions([first, second])
    assert [(code.id, code.name["en"]) for code in applied.codes] == [
        ("A", "CL_OWN A"),
        ("B", "CL_SECOND B"),
        ("C", "CL_SECOND C"),
    ]


def test_a_prefix_applies_to_the_id_and_the_parent() -> None:
    applied = _codelist("CL_X", extensions=(_extending(_TREE, prefix="T_"),)).apply_extensions([_TREE])
    assert _ids(applied) == [("T_A", None), ("T_B", "T_A"), ("T_C", "T_A"), ("T_D", "T_C"), ("T_E", None)]


@pytest.mark.parametrize(
    ("members", "want"),
    [
        pytest.param([("C", None)], [("C", None)], id="self-only-and-an-orphan-becomes-a-root"),
        pytest.param([("C", CascadeSelection.INCLUDE_DESCENDANTS)], [("C", None), ("D", "C")], id="cascade"),
        pytest.param([("A", CascadeSelection.EXCLUDE_ROOT)], [("B", None), ("C", None), ("D", "C")], id="excluderoot"),
        pytest.param([("%", None)], [("A", None), ("B", "A"), ("C", "A"), ("D", "C"), ("E", None)], id="wildcard-all"),
        pytest.param([("%E", None), ("B%", None)], [("B", None), ("E", None)], id="wildcard-affix"),
    ],
)
def test_an_inclusive_selection(
    members: list[tuple[str, CascadeSelection | None]], want: list[tuple[str, str | None]]
) -> None:
    extension = _extending(_TREE, selection=_select(InclusionMode.INCLUDE, *members))
    assert _ids(_codelist("CL_X", extensions=(extension,)).apply_extensions([_TREE])) == want


def test_an_exclusive_selection() -> None:
    extension = _extending(_TREE, selection=_select(InclusionMode.EXCLUDE, ("C", CascadeSelection.INCLUDE_DESCENDANTS)))
    assert _ids(_codelist("CL_X", extensions=(extension,)).apply_extensions([_TREE])) == [
        ("A", None),
        ("B", "A"),
        ("E", None),
    ]


def test_an_extended_codelist_has_its_own_extensions_applied_first() -> None:
    middle = _codelist("CL_MIDDLE", ("M", None), extensions=(_extending(_TREE, prefix="T_"),))
    top = _codelist("CL_TOP", extensions=(_extending(middle),))
    assert [code.id for code in top.apply_extensions([_TREE, middle]).codes] == ["M", "T_A", "T_B", "T_C", "T_D", "T_E"]


def test_a_resolved_reference_needs_nothing_given() -> None:
    applied = _codelist("CL_X", extensions=(CodelistExtension(codelist=Ref.to(_TREE)),)).apply_extensions()
    assert _ids(applied) == _ids(_TREE)


def test_a_codelist_extending_nothing_is_itself() -> None:
    assert _TREE.apply_extensions() is _TREE


def test_an_extended_codelist_that_cannot_be_found_is_named() -> None:
    codelist = _codelist("CL_X", extensions=(_extending(_TREE),))
    assert unresolved_extensions(codelist) == [str(_TREE.urn)]
    assert unresolved_extensions(codelist, [_TREE]) == []
    with pytest.raises(LookupError, match="CL_TREE"):
        codelist.apply_extensions()


def test_a_cycle_is_refused() -> None:
    a_urn = Codelist.urn_for(agency="AG", id="CL_A", version="1.0")
    b = _codelist("CL_B", ("B", None), extensions=(CodelistExtension(codelist=Ref[Codelist].unresolved(a_urn)),))
    a = _codelist("CL_A", ("A", None), extensions=(_extending(b),))
    with pytest.raises(ValueError, match="extends itself"):
        a.apply_extensions([a, b])


# ── SDMX-ML 2.1 ───────────────────────────────────────────────────────────────


def test_21_writes_the_inherited_codes(sdmx_ml21_schema: etree.XMLSchema) -> None:
    extending = _codelist("CL_X", ("Z", None), extensions=(_extending(_TREE, prefix="T_"),))
    written = serialize(StructureMessage(codelists=[_TREE, extending]), version="2.1")
    assert_valid_sdmx_ml(written, sdmx_ml21_schema)
    back = as_structure(parse(written)).codelists.get("CL_X")
    assert back is not None
    assert _ids(back) == _ids(extending.apply_extensions([_TREE]))
    assert back.extensions == ()


def test_21_names_the_extension_and_the_inlined_codes() -> None:
    extending = _codelist("CL_X", extensions=(_extending(_TREE),))
    assert [gap.field for gap in unrepresentable(extending, "2.1")] == ["extensions", "codes"]
    assert unrepresentable(_TREE, "2.1") == ()


def test_21_refuses_an_extension_it_cannot_find() -> None:
    extending = _codelist("CL_X", ("Z", None), extensions=(_extending(_TREE),))
    with pytest.raises(ValidationError, match="neither resolved nor in the message"):
        serialize(StructureMessage(codelists=[extending]), version="2.1")
    assert serialize(StructureMessage(codelists=[extending]), version="3.0")


def _geographic_extending_tree() -> GeographicCodelist:
    return GeographicCodelist(
        id="CL_GEO",
        maintainer="AG",
        name={"en": "CL_GEO"},
        codes=[GeoFeatureSetCode(id="G", name={"en": "G"}, value="POINT(0 0)")],
        extensions=(_extending(_TREE),),
    )


def test_a_geographic_codelist_cannot_hold_inherited_plain_codes() -> None:
    with pytest.raises(TypeError, match="GeoFeatureSetCode"):
        _geographic_extending_tree().apply_extensions([_TREE])


def test_21_writes_a_geographic_extension_as_a_plain_codelist(sdmx_ml21_schema: etree.XMLSchema) -> None:
    written = serialize(StructureMessage(codelists=[_TREE, _geographic_extending_tree()]), version="2.1")
    assert_valid_sdmx_ml(written, sdmx_ml21_schema)
    back = as_structure(parse(written)).codelists.get("CL_GEO")
    assert back is not None
    assert [code.id for code in back.codes] == ["G", "A", "B", "C", "D", "E"]
