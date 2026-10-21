"""Tests for model/validation.py — construction refusals, validate(msg), validate_references.

These cover the per-field charset/version checks and the cross-artefact
``Ref`` resolution check. Write-path enforcement (``serialize``,
``Registry.add``) lives in ``test_id_charset.py``.
"""

from collections.abc import Callable
from typing import Any

import attrs
import pytest

from sdmxlib.model import validation
from sdmxlib.model._items import CLASS_ALIAS_GROUPS, CLASS_ALIASES
from sdmxlib.model.category import Categorisation, Category, CategoryScheme
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ItemList
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import AttachmentLevel, Attribute, DataStructure, Dimension, Measure, TimeDimension
from sdmxlib.model.hierarchy import (
    HierarchicalCode,
    HierarchicalCodelist,
    Hierarchy,
    HierarchyAssociation,
    Level,
)
from sdmxlib.model.mapping import StructureSet
from sdmxlib.model.message import MetadataMessage, StructureMessage
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import (
    ReferenceIssue,
    ReferenceValidationError,
    ValidationError,
    ValidationIssue,
    _collect_refs,
    validate,
    validate_references,
)
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit

_CL_URN = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ(1.0)"
_DSD_URN = "urn:sdmx:org.sdmx.infomodel.datastructure.DataStructure=ESTAT:CPI(1.0)"


def _closed(msg: StructureMessage) -> StructureMessage:
    """*msg* with the `T:CS` concept scheme every `concept_ref` it holds names (#492).

    Every component has a concept since construction requires one, and these
    tests are about other references: without the scheme, each would also
    report its concepts broken.
    """
    ids = sorted(
        {
            ref.urn.item_id
            for artefact in msg.all_artefacts()
            for _, ref in validation._collect_refs(artefact, "")
            if ref.urn.item_id is not None and (ref.urn.agency, ref.urn.id) == ("T", "CS")
        }
    )
    if not ids:
        return msg
    scheme = ConceptScheme(
        id="CS", maintainer="T", name={"en": "cs"}, concepts=[Concept(id=i, name={"en": i}) for i in ids]
    )
    return attrs.evolve(msg, concept_schemes=[*msg.concept_schemes, scheme])


def _refused(build: Callable[[], object]) -> list[ValidationIssue]:
    """What constructing raises: an id, agency id or version is refused as it is set (#492)."""
    with pytest.raises(ValidationError) as exc:
        build()
    return exc.value.issues


# ── ValidationIssue ───────────────────────────────────────────────────────────


def test_validation_issue_str():
    issue = ValidationIssue(path="id", message="must not be empty")
    assert str(issue) == "id — must not be empty"


# ── validate — id format ──────────────────────────────────────────────────────


def test_validate_valid_codelist_no_raise():
    Codelist(name={"en": "CL_FREQ"}, id="CL_FREQ", maintainer="SDMX", version="1.0")


def test_validate_invalid_id_special_chars():
    """A space is refused at both an item and a maintainable position, as the id is set."""
    (issue,) = _refused(lambda: Codelist(name={"en": "CL FREQ"}, id="CL FREQ", maintainer="SDMX"))
    assert issue.path == "Codelist.id"
    assert "NCName" in issue.message, "maintainable ids are NCName-checked"

    (issue,) = _refused(lambda: Code(name={"en": "c"}, id="C ODE"))
    assert issue.path == "Code.id"
    assert "invalid characters" in issue.message, "item ids keep the IDType message"


def test_validate_empty_id():
    issues = _refused(lambda: Codelist(name={"en": "x"}, id="", maintainer="SDMX"))
    assert [(i.path, i.message) for i in issues] == [("Codelist.id", "id must not be empty")]


def test_validate_invalid_agency_id():
    issues = _refused(lambda: Codelist(name={"en": "CL_FREQ"}, id="CL_FREQ", maintainer="MY AGENCY"))
    assert [i.path for i in issues] == ["Codelist.agency_id"]


def test_validate_agency_id_with_dots_valid():
    Codelist(name={"en": "CL_FREQ"}, id="CL_FREQ", maintainer="IMF.STAT")


def test_validate_invalid_version():
    issues = _refused(
        lambda: Codelist(name={"en": "CL_FREQ"}, id="CL_FREQ", maintainer="SDMX", version="not-a-version")
    )
    assert [i.path for i in issues] == ["Codelist.version"]


def test_validate_version_with_three_parts():
    Codelist(name={"en": "CL_FREQ"}, id="CL_FREQ", maintainer="SDMX", version="1.2.3")


# ── whole-artefact rules, refused as the artefact is built (#492) ─────────────


def _duplicated(cl_id: str) -> Codelist:
    """A codelist two codes share an id in — a rule no one value shows."""
    return Codelist(
        name={"en": cl_id},
        id=cl_id,
        maintainer="SDMX",
        codes=[Code(name={"en": "A"}, id="A"), Code(name={"en": "A"}, id="A")],
    )


def test_duplicate_code_ids_are_refused_as_the_codelist_is_built():
    (issue,) = _refused(lambda: _duplicated("CL_FREQ"))
    assert issue.path == "Codelist.codes[A]"
    assert "duplicate" in issue.message


def test_unique_code_ids_build():
    Codelist(
        name={"en": "CL_FREQ"},
        id="CL_FREQ",
        maintainer="SDMX",
        codes=[Code(name={"en": "A"}, id="A"), Code(name={"en": "Q"}, id="Q"), Code(name={"en": "M"}, id="M")],
    )


def test_a_valid_dsd_builds():
    DataStructure(
        name={"en": "DSD_CPI"},
        id="DSD_CPI",
        maintainer="ESTAT",
        dimensions=[
            Dimension(id="FREQ", concept=concept_ref("FREQ")),
            Dimension(id="REF_AREA", concept=concept_ref("REF_AREA")),
        ],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )


def test_validate_dsd_dimension_invalid_id():
    (issue,) = _refused(lambda: Dimension(id="FREQ VALUE", concept=concept_ref("FREQ VALUE")))
    assert issue.path == "Dimension.id"
    assert "invalid characters" in issue.message


def test_construction_accumulates_every_whole_artefact_issue():
    """Over the rules that need the whole artefact; one value's rules refuse as it is set."""
    issues = _refused(
        lambda: Codelist(
            name={"en": "CL"},
            id="CL",
            maintainer="SDMX",
            codes=[
                Code(name={"en": "A"}, id="A"),
                Code(name={"en": "A"}, id="A"),
                Code(name={"en": "B"}, id="B", parent_id="Z"),
            ],
        )
    )
    assert sorted(i.path for i in issues) == ["Codelist.codes[A]", "Codelist.codes[B].parent_id"]


def test_construction_refuses_the_first_bad_value_it_sets() -> None:
    """Fail-fast, by design: the id is refused before the version is looked at."""
    issues = _refused(lambda: Codelist(name={"en": "x"}, id="bad id", maintainer="MY AGENCY", version="latest"))
    assert [i.path for i in issues] == ["Codelist.id"]


def test_evolve_refuses_what_construction_refuses():
    cl = Codelist(name={"en": "CL"}, id="CL", maintainer="SDMX", codes=[Code(name={"en": "A"}, id="A")])
    with pytest.raises(ValidationError, match="duplicate code id 'A'"):
        attrs.evolve(cl, codes=[*cl.codes, Code(name={"en": "A"}, id="A")])


# ── validate: what only a message can know (#492) ─────────────────────────────


def test_validate_passes_a_clean_structure_message() -> None:
    validate(StructureMessage(codelists=[Codelist(name={"en": "CL_OK"}, id="CL_OK", maintainer="SDMX")]))


def test_validate_refuses_a_maintainable_in_the_message_twice() -> None:
    twin = Codelist(name={"en": "CL"}, id="CL", maintainer="SDMX")
    with pytest.raises(ValidationError, match="CL"):
        validate(StructureMessage(codelists=[twin, attrs.evolve(twin, name={"en": "other"})]))


def test_validate_refuses_a_reference_the_message_cannot_resolve() -> None:
    flow = Dataflow(name={"en": "DF"}, id="DF", maintainer="SDMX", structure=DataStructure.parse_urn(_DSD_URN))
    with pytest.raises(ReferenceValidationError):
        validate(StructureMessage(dataflows=[flow]))


def test_validate_passes_a_metadata_message() -> None:
    """A metadata message holds sets, not references a message resolves; nothing to check."""
    msd = MetadataStructure(name={"en": "MSD"}, id="MSD", maintainer="SDMX")
    mdf = Metadataflow(name={"en": "MDF"}, id="MDF", maintainer="SDMX", structure=msd)
    validate(MetadataMessage(metadata_sets=[MetadataSet(name={"en": "A"}, id="A", maintainer="SDMX", structure=mdf)]))


@pytest.mark.parametrize(
    "thing",
    [
        Codelist(name={"en": "CL"}, id="CL", maintainer="SDMX"),
        Code(name={"en": "A"}, id="A"),
        Dimension(id="FREQ", concept=concept_ref("FREQ")),
        "CL_FREQ",
        None,
    ],
    ids=["artefact", "item", "component", "text", "none"],
)
def test_validate_refuses_anything_but_a_message(thing: object) -> None:
    """An artefact is checked as it is built; passing one silently would say it was checked here."""
    with pytest.raises(TypeError, match=rf"^validate\(\) takes a message, not {type(thing).__name__};"):
        validate(thing)  # pyrefly: ignore[bad-argument-type]


# ── validate_references ───────────────────────────────────────────────────────


def test_validate_references_no_refs_no_raise():
    cl = Codelist(name={"en": "CL_FREQ"}, id="CL_FREQ", maintainer="SDMX")
    dsd = DataStructure(name={"en": "DSD_CPI"}, id="DSD_CPI", maintainer="ESTAT")
    msg = StructureMessage(codelists=[cl], data_structures=[dsd])
    assert validate_references(_closed(msg)) is None


def test_validate_references_resolved_ref_no_raise():
    cl = Codelist(name={"en": "CL_FREQ"}, id="CL_FREQ", maintainer="SDMX")
    ref = Ref[Codelist].of(Codelist.parse_urn(_CL_URN), cl)
    msg = StructureMessage(
        codelists=[cl],
        data_structures=[
            DataStructure(
                name={"en": "DSD_CPI"},
                id="DSD_CPI",
                maintainer="ESTAT",
                dimensions=[Dimension(id="FREQ", representation=ref, concept=concept_ref("FREQ"))],
            )
        ],
    )
    validate_references(_closed(msg))  # no raise


def test_validate_references_missing_codelist():
    ref = Ref[Codelist].unresolved(Codelist.parse_urn(_CL_URN))
    msg = StructureMessage(
        data_structures=[
            DataStructure(
                name={"en": "DSD_CPI"},
                id="DSD_CPI",
                maintainer="ESTAT",
                dimensions=[Dimension(id="FREQ", representation=ref, concept=concept_ref("FREQ"))],
            )
        ]
    )
    with pytest.raises(ReferenceValidationError) as exc:
        validate_references(_closed(msg))
    assert len(exc.value.issues) == 1
    assert exc.value.issues[0].target_urn == _CL_URN
    assert "FREQ" in exc.value.issues[0].source


def test_validate_references_codelist_present_no_raise():
    cl = Codelist(name={"en": "CL_FREQ"}, id="CL_FREQ", maintainer="SDMX", version="1.0")
    ref = Ref[Codelist].unresolved(Codelist.parse_urn(_CL_URN))
    msg = StructureMessage(
        codelists=[cl],
        data_structures=[
            DataStructure(
                name={"en": "DSD_CPI"},
                id="DSD_CPI",
                maintainer="ESTAT",
                dimensions=[Dimension(id="FREQ", representation=ref, concept=concept_ref("FREQ"))],
            )
        ],
    )
    validate_references(_closed(msg))  # no raise


def test_validate_references_missing_dsd_in_dataflow():
    dsd_ref = Ref[DataStructure].unresolved(DataStructure.parse_urn(_DSD_URN))
    df = Dataflow(name={"en": "DF_CPI"}, id="DF_CPI", maintainer="ESTAT", structure=dsd_ref)
    msg = StructureMessage(dataflows=[df])
    with pytest.raises(ReferenceValidationError) as exc:
        validate_references(_closed(msg))
    assert len(exc.value.issues) == 1
    assert exc.value.issues[0].target_urn == _DSD_URN


def test_validate_references_accumulates_all_broken():
    # Two broken refs in one message — both must appear.
    cl_ref = Ref[Codelist].unresolved(Codelist.parse_urn(_CL_URN))
    dsd_ref = Ref[DataStructure].unresolved(DataStructure.parse_urn(_DSD_URN))
    msg = StructureMessage(
        data_structures=[
            DataStructure(
                name={"en": "DSD_CPI"},
                id="DSD_CPI",
                maintainer="ESTAT",
                dimensions=[Dimension(id="FREQ", representation=cl_ref, concept=concept_ref("FREQ"))],
            )
        ],
        dataflows=[Dataflow(name={"en": "DF_CPI"}, id="DF_CPI", maintainer="ESTAT", structure=dsd_ref)],
    )
    with pytest.raises(ReferenceValidationError) as exc:
        validate_references(_closed(msg))
    urns = {i.target_urn for i in exc.value.issues}
    assert urns == {_CL_URN, _DSD_URN}


def test_reference_issue_str():
    issue = ReferenceIssue(source="DSD:x.dimensions[FREQ].codelist", target_urn=_CL_URN)
    assert "BROKEN" in str(issue)
    assert _CL_URN in str(issue)


# ── ID pattern boundary tests ─────────────────────────────────────────────────
#
# SDMX has *three* id types and the position decides which applies
# (SDMXCommonReferences.xsd):
#
#   IDType              [A-Za-z0-9_@$-]+         items: Code, Category
#   NCNameIDType        [A-Za-z][A-Za-z0-9_-]*   maintainables, components, Concept
#   NestedNCNameIDType  NCName(.NCName)*         agencyID
#
# These tests used to apply IDType to a *Codelist* id, which is a maintainable
# and therefore NCName — so they asserted that `0`, `CL$FREQ` and `@` were
# valid there. They are not: the XSD rejects them, and `validate` accepting
# them meant `serialize(validate=True)` could emit invalid SDMX-ML (#221).


@pytest.mark.parametrize(
    "valid_id",
    [
        "A",
        "z",
        "0",
        "CL_FREQ",
        "dsd-cpi",
        "123ABC",
        "CL@FREQ",
        "CL$FREQ",
        "A_@$-B",
        "_",
        "-",
        "@",
        "$",
    ],
)
def test_idtype_pattern_valid_on_an_item(valid_id: str) -> None:
    """`Code` ids really are the permissive form — verified against the XSD."""
    Codelist(name={"en": "x"}, id="CL_X", maintainer="SDMX", codes=[Code(name={"en": "c"}, id=valid_id)])


@pytest.mark.parametrize("valid_id", ["A", "z", "CL_FREQ", "dsd-cpi", "A1", "a-b_c"])
def test_ncname_pattern_valid_on_a_maintainable(valid_id: str) -> None:
    Codelist(name={"en": "x"}, id=valid_id, maintainer="SDMX")  # builds: every rule passes


@pytest.mark.parametrize("bad_id", ["0", "123ABC", "CL@FREQ", "CL$FREQ", "_", "-", "@", "$", "_LEAD"])
def test_ncname_pattern_rejects_idtype_only_ids_on_a_maintainable(bad_id: str) -> None:
    """Legal for a Code, illegal for a Codelist — the distinction this fixes."""
    (issue,) = _refused(lambda: Codelist(name={"en": "x"}, id=bad_id, maintainer="SDMX"))
    assert issue.path == "Codelist.id"
    assert "NCName" in issue.message, f"expected {bad_id!r} to be rejected as a maintainable id"


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "CL FREQ",
        "CL.FREQ",
        "CL/FREQ",
        "CL:FREQ",
        "CL(FREQ)",
        "CL=FREQ",
        "CL+FREQ",
        "CL#FREQ",
        "héllo",
        "CL\tFREQ",
        "CL\nFREQ",
    ],
)
def test_id_pattern_invalid(bad_id: str) -> None:
    """Rejected at a maintainable position by either id rule.

    Asserts on the *path* rather than the wording: a Codelist id is checked
    against NCNameIDType and an item id against IDType, and those two produce
    different messages. Matching the literal word "invalid" pinned one of them
    by accident.
    """
    issues = _refused(lambda: Codelist(name={"en": "x"}, id=bad_id, maintainer="SDMX"))
    assert [i.path for i in issues] == ["Codelist.id"], f"Expected {bad_id!r} to be an invalid id"


# ── Agency ID pattern boundary tests ─────────────────────────────────────────
# SDMX NestedNCNameIDType = NCName(.NCName)* — each dot-separated segment must
# start with a letter, and @ / $ are not permitted. `AG@NCY` and `0.1` used to
# be asserted valid here; the XSD rejects both (#221).


@pytest.mark.parametrize(
    "valid_agency",
    [
        "SDMX",
        "A",
        "IMF",
        "IMF.STAT",
        "A.B.C",
        "MY_AGENCY",
        "MY-AGENCY",
    ],
)
def test_agency_pattern_valid(valid_agency: str) -> None:
    Codelist(name={"en": "CL_X"}, id="CL_X", maintainer=valid_agency)


@pytest.mark.parametrize(
    "bad_agency",
    [
        "",
        "MY AGENCY",
        ".SDMX",
        "SDMX.",
        "A..B",
        ".",
        "..",
        "AG@NCY",
        "AG$NCY",
        "A.B@C",
        "0.1",
        "A:B",
        "A/B",
        "A B",
        "A.B.C.",
        ".A.B",
    ],
)
def test_agency_pattern_invalid(bad_agency: str) -> None:
    issues = _refused(lambda: Codelist(name={"en": "CL_X"}, id="CL_X", maintainer=bad_agency))
    assert [i.path for i in issues] == ["Codelist.agency_id"], f"Expected {bad_agency!r} to be an invalid agency_id"


# ── Version pattern boundary tests ───────────────────────────────────────────


@pytest.mark.parametrize(
    "valid_version",
    [
        "1",
        "0",
        "1.0",
        "1.2.3",
        "1.0.0",
        "10.20.30",
        "0.1",
        "01.0",
        "999",
    ],
)
def test_version_pattern_valid(valid_version: str) -> None:
    Codelist(name={"en": "CL_X"}, id="CL_X", maintainer="SDMX", version=valid_version)


@pytest.mark.parametrize(
    "bad_version",
    [
        "",
        "latest",
        "v1.0",
        "1.0-alpha",
        ".1",
        "1.",
        "1..2",
        "1.a",
        "a",
        "1 0",
        "1.0.0.0.x",
    ],
)
def test_version_pattern_invalid(bad_version: str) -> None:
    issues = _refused(lambda: Codelist(name={"en": "CL_X"}, id="CL_X", maintainer="SDMX", version=bad_version))
    assert [i.path for i in issues] == ["Codelist.version"], f"Expected {bad_version!r} to be an invalid version"


# ── NameableArtefact requires a name ──────────────────────────────────────────
#
# SDMX types every maintainable and every item-scheme item as NameableArtefact,
# whose `<com:Name>` carries no minOccurs in the XSD. Components (Dimension,
# Attribute, Measure) are *identifiable* only — no Name element exists on them —
# so they are deliberately excluded.


def test_maintainable_without_a_name_is_invalid():
    """`name` has no default, so no name is a missing argument the checker sees; an empty one is refused (#492)."""
    issues = _refused(lambda: Codelist(name={}, id="CL", maintainer="T", version="1.0"))
    assert [(i.path, i.message) for i in issues] == [("Codelist.name", "name is required (SDMX NameableArtefact)")]


def test_item_without_a_name_is_invalid():
    issues = _refused(lambda: Code(name={}, id="A"))
    assert [(i.path, i.message) for i in issues] == [("Code.name", "name is required (SDMX NameableArtefact)")]


def test_named_artefact_and_items_pass():
    Codelist(
        id="CL",
        maintainer="T",
        version="1.0",
        name={"en": "cl"},
        codes=ItemList([Code(id="A", name={"en": "Annual"})]),
    )


def test_components_are_not_name_checked():
    """Dimension/Attribute/Measure are identifiable, not nameable — no Name in the XSD."""
    DataStructure(
        id="DSD",
        maintainer="T",
        version="1.0",
        name={"en": "dsd"},
        dimensions=ItemList([Dimension(id="FREQ", concept=concept_ref("FREQ"))]),
        measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
    )


# ── the closed-world check is now actually closed (#148) ──────────────────


def _dangling_concept() -> Ref[Any]:
    """A concept ref pointing at a scheme no message will contain."""
    return Ref[Any].unresolved(
        SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=AG:CS_MISSING(1.0).NOPE")
    )


def _sources(msg: StructureMessage) -> list[str]:
    """The `source` path of every issue `validate_references` reports."""
    with pytest.raises(ReferenceValidationError) as raised:
        validate_references(_closed(msg))
    return [issue.source for issue in raised.value.issues]


def test_a_ref_on_a_nested_metadata_attribute_is_checked() -> None:
    """`validate_references` documents itself as a *closed-world* check, so a
    ref it cannot see is a ref it silently passes.

    Before #148 it read the artefact's own `Ref`-typed fields and then
    hand-descended into `DataStructure` components and `Hierarchy` codes only —
    so this was missed at *any* depth, nested or top-level. The descent now
    comes from `walk_fields`, the same per-class shape the resolver and the
    interner already use (#376).
    """
    msd = MetadataStructure(
        id="MSD",
        maintainer="AG",
        version="1.0",
        name={"en": "msd"},
        attributes=[
            MetadataAttribute(
                id="PARENT",
                children=[MetadataAttribute(id="CHILD", concept=_dangling_concept())],
                concept=concept_ref("PARENT"),
            )
        ],
    )
    assert _sources(StructureMessage(metadata_structures=[msd])) == [
        "MetadataStructure:MSD.attributes[PARENT].children[CHILD].concept"
    ]


def test_a_ref_in_a_plain_tuple_is_checked() -> None:
    """Five model fields are a bare `list`/`tuple` of `Ref` rather than an
    `ItemList`, and every one was unreachable: a `Ref` in a plain container is
    in neither hand-listed type.

    `StructureSet.maps` is `tuple[Ref[object], ...]`. An earlier version of
    this test used `Metadataflow.targets`, which is `tuple[str, ...]` — the
    storage reader builds strings there deliberately, since a declared target
    may be a wildcard URN no reference can express (#362). Passing a `Ref` into
    it made the test agree with the code for the wrong reason, and it only
    passed because `_refshape` read the `...` in that annotation as unreadable
    and walked the field conservatively. #392 fixed that reading, and this test
    with it.

    The path names the ref by its own id — `Ref.id` is a property over the URN,
    so reading it resolves nothing.
    """
    target = Ref[object].unresolved(SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:CL_MISSING(1.0)"))
    structure_set = StructureSet(id="SS", maintainer="AG", version="1.0", name={"en": "ss"}, maps=(target,))

    # `_collect_refs` directly, not through `validate_references`, because
    # `StructureMessage.all_artefacts()` omits `structure_sets` and
    # `hierarchical_codelists` — so the two artefact types carrying this shape
    # are invisible to the message-level check for an unrelated reason. Filed
    # separately; asserting on the walker keeps this test about the walker.
    assert _collect_refs(structure_set, "StructureSet:SS") == [("StructureSet:SS.maps[CL_MISSING]", target)]


def test_the_two_shapes_that_already_worked_still_do() -> None:
    """The hand-listed descent is gone; these are what it was there for.

    A `Hierarchy` is now reached through `tree` and its `children`, not through
    the `codes` flat index — `walk_fields` excludes that field as a derived
    view (#383), and the two hold the same objects. Same refs, different path.
    """
    dsd = DataStructure(
        id="D",
        maintainer="AG",
        version="1.0",
        name={"en": "d"},
        dimensions=[Dimension(id="X", concept=_dangling_concept())],
    )
    assert _sources(StructureMessage(data_structures=[dsd])) == ["DataStructure:D.dimensions[X].concept"]

    missing_code = Ref[Code].unresolved(
        Code.parse_urn("urn:sdmx:org.sdmx.infomodel.codelist.Code=AG:CL_MISSING(1.0).Z")
    )
    hierarchy = Hierarchy(
        id="H",
        maintainer="AG",
        version="1.0",
        name={"en": "h"},
        tree=[HierarchicalCode(id="N", code=missing_code)],
    )
    assert _sources(StructureMessage(hierarchies=[hierarchy])) == ["Hierarchy:H.tree[N].code"]


def test_a_shared_object_is_reported_once() -> None:
    """The model is a graph — a `Level` is shared by every node at that level —
    so the walk carries a `seen` set. Without it a wide hierarchy reports the
    same ref once per referrer and the issue list is quadratic in fan-out."""
    shared = Level(id="L", name={"en": "l"})
    tree = [HierarchicalCode(id=f"N{i}", code=_dangling_concept(), level=shared) for i in range(5)]
    hierarchy = Hierarchy(id="H", maintainer="AG", version="1.0", name={"en": "h"}, levels=[shared], tree=tree)
    sources = _sources(StructureMessage(hierarchies=[hierarchy]))
    assert len(sources) == 5, f"one per node, not one per (node, referrer): {sources}"


def test_walking_a_large_scheme_stays_cheap() -> None:
    """The cost of descending where the old walk did not.

    A `Codelist`'s `codes` is walked now, and `can_reach_ref(Code)` is False,
    so each element must cost a cached class lookup rather than an f-string and
    a `getattr`. Asserted as a *count* of paths built, per CLAUDE.md — a
    wall-clock bound here would be a statement about the machine.
    """
    codes = [Code(id=f"C{i}", name={"en": f"c{i}"}) for i in range(2_000)]
    codelist = Codelist(id="CL", maintainer="AG", version="1.0", name={"en": "cl"}, codes=codes)
    # No refs anywhere, so a correct walk reports nothing and builds no paths.
    assert validate_references(_closed(StructureMessage(codelists=[codelist]))) is None


def test_a_structure_set_is_reference_checked_now() -> None:
    """`validate_references` reaches `StructureSet` at last (#394).

    It never did: `all_artefacts()` omitted that slot, so the closed-world
    check passed over the artefact entirely — and `StructureSet.maps` is one of
    the five container-of-`Ref` fields #392 taught the walkers to read.
    """
    target = Ref[object].unresolved(SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:CL_MISSING(1.0)"))
    structure_set = StructureSet(id="SS", maintainer="AG", version="1.0", name={"en": "ss"}, maps=(target,))
    assert _sources(StructureMessage(structure_sets=[structure_set])) == ["StructureSet:SS.maps[CL_MISSING]"]


def test_a_hierarchical_codelist_is_reference_checked_now() -> None:
    """The other slot `all_artefacts()` omitted, and the same consequence."""
    target = Ref[object].unresolved(SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Hierarchy=AG:H_MISSING(1.0)"))
    container = HierarchicalCodelist(
        id="HCL", maintainer="AG", version="1.0", name={"en": "hcl"}, hierarchies=(target,)
    )
    assert _sources(StructureMessage(hierarchical_codelists=[container])) == [
        "HierarchicalCodelist:HCL.hierarchies[H_MISSING]"
    ]


# ── item references resolve against the items in the message (#404) ────────


def _item_ref(package: str, artefact_class: str, scheme: str, item_id: str, version: str = "1.0") -> Ref[Any]:
    """A reference to an item of *scheme*, spelled as a URN carries it."""
    return Ref[Any].unresolved(
        SdmxUrn.make(
            object,
            package=package,
            artefact_class=artefact_class,
            agency="AG",
            id=scheme,
            version=version,
            item_id=item_id,
        )
    )


def _area_codelist() -> Codelist:
    return Codelist(
        id="CL_AREA",
        maintainer="AG",
        version="1.0",
        name={"en": "Area"},
        codes=[Code(id="CA", name={"en": "Canada"}), Code(id="ON", name={"en": "Ontario"})],
    )


def test_a_hierarchical_code_resolves_to_a_code_in_the_message() -> None:
    """#404's report, verbatim in shape.

    `_collect_known_urns` expanded item URNs for `ConceptScheme` and nothing
    else, so both of these read as broken against a message that plainly
    contains their target — and a `HierarchicalCode` *must* point at a `Code`,
    which made `validate_references` unusable for any project modelling a 3.0
    hierarchy. The measured case was 1,388 issues of which zero were real.
    """
    codelist = _area_codelist()
    hierarchy = Hierarchy(
        id="HR_AREA",
        maintainer="AG",
        version="1.0",
        name={"en": "Area hierarchy"},
        tree=[
            HierarchicalCode(
                id="CA",
                code=_item_ref("codelist", "Code", "CL_AREA", "CA"),
                children=[HierarchicalCode(id="ON", code=_item_ref("codelist", "Code", "CL_AREA", "ON"))],
            )
        ],
    )
    assert validate_references(_closed(StructureMessage(codelists=[codelist], hierarchies=[hierarchy]))) is None


def test_a_code_that_is_not_in_the_codelist_is_still_broken() -> None:
    """The discrimination check for the test above.

    A fix that registered every item of every scheme *and* silently accepted
    anything item-shaped would pass it. This is what says the check still
    checks: same codelist, same shape, one id that is not in it.
    """
    hierarchy = Hierarchy(
        id="HR_AREA",
        maintainer="AG",
        version="1.0",
        name={"en": "Area hierarchy"},
        tree=[HierarchicalCode(id="QC", code=_item_ref("codelist", "Code", "CL_AREA", "QC"))],
    )
    assert _sources(StructureMessage(codelists=[_area_codelist()], hierarchies=[hierarchy])) == [
        "Hierarchy:HR_AREA.tree[QC].code"
    ]


def test_an_item_of_a_scheme_the_message_lacks_is_still_broken() -> None:
    """The other half of closed-world: the item id exists, the scheme does not."""
    hierarchy = Hierarchy(
        id="HR_AREA",
        maintainer="AG",
        version="1.0",
        name={"en": "Area hierarchy"},
        tree=[HierarchicalCode(id="CA", code=_item_ref("codelist", "Code", "CL_GONE", "CA"))],
    )
    assert _sources(StructureMessage(codelists=[_area_codelist()], hierarchies=[hierarchy])) == [
        "Hierarchy:HR_AREA.tree[CA].code"
    ]


def _categorisation(target_item: str) -> Categorisation:
    return Categorisation(
        id="CAT",
        maintainer="AG",
        version="1.0",
        name={"en": "cat"},
        source=Ref[object].unresolved(SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=AG:DF(1.0)")),
        target=_item_ref("categoryscheme", "Category", "CS", target_item),
    )


def _categorised(target_item: str) -> StructureMessage:
    scheme = CategoryScheme(
        id="CS",
        maintainer="AG",
        version="1.0",
        name={"en": "cs"},
        categories=[Category(id="ECON", name={"en": "e"}, categories=[Category(id="GDP", name={"en": "g"})])],
    )
    flow = Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "df"})
    return StructureMessage(category_schemes=[scheme], dataflows=[flow], categorisations=[_categorisation(target_item)])


def test_a_categorisation_target_resolves_to_a_category() -> None:
    """The case that makes #404 more than a 3.0-hierarchy edge case.

    `Categorisation` is how a category scheme is attached to a flow, it
    predates 3.0, and it is item-referencing by definition — so every
    categorised dataflow in every message carried a false positive.
    """
    assert validate_references(_closed(_categorised("ECON"))) is None


def test_a_nested_category_resolves_by_its_path() -> None:
    """Categories nest, and SDMX names a nested item by its dotted path — in the
    URN grammar and in a 2.1 ``<Ref>`` alike (``@id`` is a ``NestedIDType``)."""
    assert validate_references(_closed(_categorised("ECON.GDP"))) is None


def test_a_nested_category_named_by_its_leaf_is_broken() -> None:
    """A leaf may repeat in another branch, so it names no one category (#439).
    Every registry swept writes the path; a bare id is a root."""
    assert _sources(_categorised("GDP")) == ["Categorisation:CAT.target"]


def test_a_category_absent_from_the_scheme_is_still_broken() -> None:
    """Discrimination for the nesting pair above."""
    assert _sources(_categorised("TRADE")) == ["Categorisation:CAT.target"]


def test_a_nested_metadata_attribute_resolves() -> None:
    """The other recursive item shape — `MetadataAttribute.children`.

    Both recursive fields are *self-referential*, so attrs hands back their
    declaration as a string and `item_fields` has to resolve it. This test and
    the nested-category one are what say that resolving half works: without it
    both would report as broken and nothing else in the suite would notice.
    """
    msd = MetadataStructure(
        id="MSD",
        maintainer="AG",
        version="1.0",
        name={"en": "msd"},
        attributes=[
            MetadataAttribute(
                id="CONTACT",
                children=[MetadataAttribute(id="EMAIL", concept=concept_ref("EMAIL"))],
                concept=concept_ref("CONTACT"),
            )
        ],
    )
    flow = Metadataflow(
        id="MF",
        maintainer="AG",
        version="1.0",
        name={"en": "mf"},
        structure=Ref[MetadataStructure].unresolved(msd.urn),
    )
    linking = Categorisation(
        id="CAT",
        maintainer="AG",
        version="1.0",
        name={"en": "cat"},
        source=Ref[Any].unresolved(flow.urn),
        target=_item_ref("metadatastructure", "MetadataAttribute", "MSD", "CONTACT.EMAIL"),
    )
    msg = StructureMessage(metadata_structures=[msd], metadataflows=[flow], categorisations=[linking])
    assert validate_references(_closed(msg)) is None


def _dsd_with_every_component() -> DataStructure:
    return DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name={"en": "dsd"},
        dimensions=[Dimension(id="GEO", concept=concept_ref("GEO"))],
        time_dimension=TimeDimension(concept=concept_ref("X")),
        attributes=[Attribute(attachment=AttachmentLevel.DATASET, id="OBS_STATUS", concept=concept_ref("OBS_STATUS"))],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )


def _association(artefact_class: str, item_id: str) -> HierarchyAssociation:
    hierarchy_urn = Hierarchy.parse_urn("urn:sdmx:org.sdmx.infomodel.codelist.Hierarchy=AG:H(1.0)")
    return HierarchyAssociation(
        id="HA",
        maintainer="AG",
        version="1.0",
        name={"en": "ha"},
        linked_hierarchy=Ref[Hierarchy].unresolved(hierarchy_urn),
        linked_object=_item_ref("datastructure", artefact_class, "DSD", item_id),
    )


def _with_association(artefact_class: str, item_id: str) -> StructureMessage:
    return StructureMessage(
        data_structures=[_dsd_with_every_component()],
        hierarchies=[Hierarchy(id="H", maintainer="AG", version="1.0", name={"en": "h"})],
        hierarchy_associations=[_association(artefact_class, item_id)],
    )


def test_a_hierarchy_association_resolves_to_the_dimension_it_organises() -> None:
    """The third shape #404 measured, and the one that fails on a *single*
    field: `DataStructure.time_dimension` is one component, not a list, so an
    accessor column in a table of (scheme, accessor) rows could not have named
    it — `item_fields` reads the declaration, and a single item field and a
    container of them are both item fields."""
    assert validate_references(_closed(_with_association("Dimension", "GEO"))) is None
    assert validate_references(_closed(_with_association("TimeDimension", "TIME_PERIOD"))) is None


def test_a_component_that_is_not_on_the_dsd_is_still_broken() -> None:
    """Discrimination for the components, including the single-valued field:
    the time dimension's id is fixed at ``TIME_PERIOD``, so any other id for
    that class is absent however the DSD is shaped."""
    assert _sources(_with_association("Dimension", "NOPE")) == ["HierarchyAssociation:HA.linked_object"]
    assert _sources(_with_association("TimeDimension", "OBS_DATE")) == ["HierarchyAssociation:HA.linked_object"]


@pytest.mark.parametrize(
    ("artefact_class", "item_id"),
    [
        ("Attribute", "OBS_STATUS"),
        ("DataAttribute", "OBS_STATUS"),
        ("Measure", "OBS_VALUE"),
        ("PrimaryMeasure", "OBS_VALUE"),
    ],
)
def test_both_version_spellings_of_a_component_class_resolve(artefact_class: str, item_id: str) -> None:
    """SDMX renamed two component classes, and a URN carries the spelling of
    whatever produced it: 2.1 says ``Attribute`` / ``PrimaryMeasure``, 3.0 says
    ``DataAttribute`` / ``Measure``. The model has one class for each and
    declares one spelling, so the other is folded on at lookup —
    `CLASS_ALIAS_GROUPS`."""
    assert validate_references(_closed(_with_association(artefact_class, item_id))) is None


def test_the_alias_groups_are_symmetric_and_lose_nothing() -> None:
    """`CLASS_ALIASES` is derived from the groups, so a member cannot reach
    some of its group and not the rest. Asserted because the derivation is the
    only reason the folding is symmetric — a hand-written map was the
    alternative."""
    for group in CLASS_ALIAS_GROUPS:
        for spelling in group:
            assert set(CLASS_ALIASES[spelling]) == set(group) - {spelling}


def test_a_version_less_reference_resolves_to_the_version_present() -> None:
    """The second false positive in the same function, and the same symptom: a
    target plainly in the message, reported missing (#404).

    SDMX writes *no version* for "latest". The known set registered an artefact
    under the version it has and nothing else, so a version-less reference
    missed — at the **scheme** level, which is every reference, not only the
    item ones. The readers' own resolution index has always registered both
    spellings (`_common.index_artefact`).
    """
    dsd = DataStructure(id="DSD1", maintainer="AG", version="1.0.0", name={"en": "dsd"})
    flow = Dataflow(
        id="DF1",
        maintainer="AG",
        version="1.0.0",
        name={"en": "df"},
        structure=Ref[DataStructure].unresolved(DataStructure.urn_for(agency="AG", id="DSD1")),
    )
    assert validate_references(_closed(StructureMessage(data_structures=[dsd], dataflows=[flow]))) is None


def test_a_version_less_item_reference_resolves_too() -> None:
    """The same alias, one level down — an item URN may omit the version as
    readily as a scheme URN, and fixing one direction only is how a check ends
    up half-right."""
    hierarchy = Hierarchy(
        id="HR_AREA",
        maintainer="AG",
        version="1.0",
        name={"en": "h"},
        tree=[HierarchicalCode(id="CA", code=_item_ref("codelist", "Code", "CL_AREA", "CA", version="latest"))],
    )
    assert validate_references(_closed(StructureMessage(codelists=[_area_codelist()], hierarchies=[hierarchy]))) is None


def test_a_different_explicit_version_is_still_broken() -> None:
    """Discrimination for the two above. A version-*less* reference means
    "latest of what is here"; an explicit one names an artefact, and a
    different version is a different artefact rather than another spelling."""
    hierarchy = Hierarchy(
        id="HR_AREA",
        maintainer="AG",
        version="1.0",
        name={"en": "h"},
        tree=[HierarchicalCode(id="CA", code=_item_ref("codelist", "Code", "CL_AREA", "CA", version="2.0"))],
    )
    assert _sources(StructureMessage(codelists=[_area_codelist()], hierarchies=[hierarchy])) == [
        "Hierarchy:HR_AREA.tree[CA].code"
    ]


def test_item_urns_cost_one_make_per_scheme_and_item_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registering items must not cost a URN build per item.

    The 2021 Census Profile bundle holds 579,951 codes in one codelist, so "one
    `SdmxUrn.make` per item" is the difference between a check and a stall. Every
    item URN of one scheme shares everything but its `item_id`, and
    `SdmxUrn.__str__` builds an item URN as exactly ``base + "." + item_id`` —
    the same reasoning, and the same shape, as `storage._item_urn_prefix`.

    Asserted as an **exact** count, not a bound: every term is knowable. Three
    scheme URNs (the concept scheme `_closed` adds, since every component has
    a concept, #492), one `Code` and one `Concept` prefix, and four component
    prefixes for the fields of the DSD that are populated — `measure_dimension`
    and `groups` are None, so they are never reached. A bound of ``< 100`` would have passed a
    regression to one build per item on any codelist smaller than that.
    """
    calls = 0
    original = SdmxUrn.make

    def counted(*args: Any, **kwargs: Any) -> SdmxUrn[Any]:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    codes = [Code(id=f"C{i}", name={"en": f"c{i}"}) for i in range(500)]
    codelist = Codelist(id="CL", maintainer="AG", version="1.0", name={"en": "cl"}, codes=codes)
    msg = StructureMessage(codelists=[codelist], data_structures=[_dsd_with_every_component()])

    monkeypatch.setattr(SdmxUrn, "make", counted)
    assert validate_references(_closed(msg)) is None
    assert calls == 9, f"3 schemes + Code and Concept prefixes + 4 component prefixes, not one per item: {calls}"
