"""`sl.migration`'s version rules: plan, decide, apply a 2.1 → 3.x move (GH-399)."""

import collections

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.migration import Plan, Question, Target
from sdmxlib.model.validation import _collect_refs
from tests.support import repo
from tests.support.roundtrip import structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_FIXTURES = repo.ROOT / "tests" / "fixtures" / "sdmx_ml21"
_TARGET = Target(version="3.0")


def _read(name: str) -> sl.StructureMessage:
    msg = sl.read(_FIXTURES / name)
    assert isinstance(msg, sl.StructureMessage)
    return msg


def _rules(plan: Plan) -> collections.Counter[str]:
    return collections.Counter(f.rule for f in (*plan.changes, *plan.questions, *plan.blocked))


def _refs(msg: sl.StructureMessage) -> list[sl.Ref[object]]:
    return [ref for artefact in msg.all_artefacts() for _, ref in _collect_refs(artefact, artefact.id)]


def _codelist(version: str, *, legacy_is_final: bool | None = None, agency: str = "A") -> sl.Codelist:
    return sl.Codelist(
        id="CL",
        maintainer=agency,
        name={"en": "cl"},
        version=version,
        legacy_is_final=legacy_is_final,
        codes=[sl.Code(id="X", name={"en": "x"})],
    )


# ── The plan ───────────────────────────────────────────────────────────────


def test_the_plan_of_a_2_1_message():
    """`complete.xml` holds 2 final artefacts and 9 that are not; its 2 agency schemes stay unversioned."""
    plan = sl.migration.plan(_read("complete.xml"), target=_TARGET)
    assert _rules(plan) == {"version.semantic": 2, "version.is-final-dropped": 2, "version.non-final": 9}
    assert {q.recommended for q in plan.questions} == {"draft"}
    assert plan.open == plan.questions


def test_planning_changes_nothing():
    msg = _read("complete.xml")
    before = [str(sl.artefact_urn(a)) for a in msg.all_artefacts()]
    sl.migration.plan(msg, target=_TARGET).apply({}, partial=True)
    assert [str(sl.artefact_urn(a)) for a in msg.all_artefacts()] == before


def test_question_keys_are_stable_across_plans():
    msg = _read("complete.xml")
    keys = [q.key for q in sl.migration.plan(msg, target=_TARGET).questions]
    assert keys == [q.key for q in sl.migration.plan(_read("complete.xml"), target=_TARGET).questions]
    assert keys[0].startswith("version.non-final@urn:sdmx:")


# ── Applying it ────────────────────────────────────────────────────────────


def test_applied_with_drafts_the_result_is_3_0_and_its_references_follow():
    plan = sl.migration.plan(_read("complete.xml"), target=_TARGET, policy={"version.non-final": "draft"})
    assert plan.open == ()
    migrated = plan.apply()

    versions = collections.Counter(a.version for a in migrated.all_artefacts())
    assert versions == {"1.0.0": 2, "1.0.0-draft": 9, "1.0": 2}
    assert {getattr(a, "legacy_is_final", None) for a in migrated.all_artefacts()} == {None}
    held = {str(sl.artefact_urn(a)) for a in migrated.all_artefacts()}
    assert [str(r.urn) for r in _refs(migrated) if r.urn.version == "1.0" and str(r.urn.owner()) not in held] == []
    sl.validate_references(migrated)
    assert_valid_sdmx_ml(sl.to_xml(migrated, version="3.0"), structure_schema("3.0"))


def test_an_answer_overrides_the_policy_for_its_question():
    plan = sl.migration.plan(_read("complete.xml"), target=_TARGET, policy={"version.non-final": "draft"})
    first = plan.questions[0]
    migrated = plan.apply({first.key: "final"})
    by_urn = {str(sl.artefact_urn(a)).rsplit("(", 1)[0]: a.version for a in migrated.all_artefacts()}
    assert by_urn[first.urn.rsplit("(", 1)[0]] == "1.0.0"
    assert collections.Counter(by_urn.values())["1.0.0-draft"] == 8


def test_omit_leaves_the_artefact_out():
    plan = sl.migration.plan(_read("complete.xml"), target=_TARGET, policy={"version.non-final": "draft"})
    dropped = next(q for q in plan.questions if "Codelist=" in q.urn)
    migrated = plan.apply({dropped.key: "omit"})
    assert len(list(migrated.all_artefacts())) == 12
    assert dropped.urn.rsplit("(", 1)[0] not in {
        str(sl.artefact_urn(a)).rsplit("(", 1)[0] for a in migrated.all_artefacts()
    }


def test_partial_leaves_open_questions_at_their_2_1_version():
    migrated = sl.migration.plan(_read("complete.xml"), target=_TARGET).apply(partial=True)
    assert collections.Counter(a.version for a in migrated.all_artefacts()) == {"1.0.0": 2, "1.0": 11}


# ── Refusals ───────────────────────────────────────────────────────────────


def test_an_open_question_is_refused_unless_partial():
    plan = sl.migration.plan(_read("complete.xml"), target=_TARGET)
    with pytest.raises(
        ValueError, match=r"^9 question\(s\) open: .* and 6 more; answer them, or apply with partial=True$"
    ):
        plan.apply()


def test_an_answer_to_no_question_is_refused():
    plan = sl.migration.plan(_read("complete.xml"), target=_TARGET)
    with pytest.raises(ValueError, match="answers to questions the plan has not got"):
        plan.apply({"version.non-final@urn:nothing": "draft"}, partial=True)


def test_an_option_the_question_lacks_is_refused():
    plan = sl.migration.plan(_read("complete.xml"), target=_TARGET)
    with pytest.raises(ValueError, match=r"no option 'maybe'; it takes \['draft', 'final', 'omit'\]"):
        plan.apply({plan.questions[0].key: "maybe"}, partial=True)


def test_a_policy_for_a_rule_that_asked_nothing_is_refused():
    with pytest.raises(ValueError, match=r"policy for rules that asked nothing: \['version.external'\]"):
        sl.migration.plan(_read("complete.xml"), target=_TARGET, policy={"version.external": "final"})


def test_a_policy_option_the_rule_lacks_is_refused():
    with pytest.raises(ValueError, match="no option 'leave'"):
        sl.migration.plan(_read("complete.xml"), target=_TARGET, policy={"version.non-final": "leave"})


def test_a_2_1_target_is_refused():
    with pytest.raises(ValueError, match=r"a migration targets one of \['3.0', '3.1', 'json'\], not '2.1'"):
        Target(version="2.1")


# ── External references ────────────────────────────────────────────────────


def test_an_artefact_referenced_and_not_held_is_asked_about_with_no_recommendation():
    plan = sl.migration.plan(_read("with_metadata_structure.xml"), target=_TARGET)
    (external,) = [q for q in plan.questions if q.rule == "version.external"]
    assert external.recommended is None
    assert [o.id for o in external.options] == ["final", "draft", "leave"]


@pytest.mark.parametrize(("choice", "version"), [("final", "1.0.0"), ("draft", "1.0.0-draft"), ("leave", "1.0")])
def test_the_external_answer_decides_what_references_name(choice: str, version: str):
    plan = sl.migration.plan(
        _read("with_metadata_structure.xml"), target=_TARGET, policy={"version.non-final": "draft"}
    )
    (external,) = [q for q in plan.questions if q.rule == "version.external"]
    migrated = plan.apply({external.key: choice})
    named = {
        r.urn.owner().version
        for r in _refs(migrated)
        if str(r.urn.owner()).rsplit("(", 1)[0] == external.urn.rsplit("(", 1)[0]
    }
    assert named == {version}


# ── Each rule ──────────────────────────────────────────────────────────────


def _every_rule() -> sl.StructureMessage:
    """One artefact or reference for each version rule."""
    flow = sl.Dataflow(
        id="DF",
        maintainer="A",
        name={"en": "df"},
        version="1.0",
        legacy_is_final=True,
        structure=sl.Ref[sl.DataStructure].unresolved(sl.DataStructure.urn_for(agency="EXT", id="DSD", version="2.1")),
    )
    return sl.StructureMessage(
        codelists=[
            _codelist("1.0", legacy_is_final=True, agency="FINAL"),
            _codelist("1.0", agency="DRAFT"),
            _codelist("1.0.0", legacy_is_final=False, agency="SEMVER"),
            _codelist("1.03", agency="OLD"),
        ],
        dataflows=[flow],
        organisation_unit_schemes=[
            sl.OrganisationUnitScheme(id="OU", maintainer="A", name={"en": "ou"}, version="2.0")
        ],
    )


def test_every_version_rule_fires():
    """Exact, so a rule added without a case here, or one that stops firing, fails."""
    assert _rules(sl.migration.plan(_every_rule(), target=_TARGET)) == {
        "version.semantic": 2,
        "version.is-final-dropped": 3,
        "version.non-final": 1,
        "version.semantic-non-final": 1,
        "version.2.1-only": 1,
        "version.unit-scheme": 1,
        "version.external": 1,
    }


def test_a_semantic_version_2_1_calls_non_final_becomes_a_draft():
    plan = sl.migration.plan(_every_rule(), target=_TARGET)
    (question,) = [q for q in plan.questions if q.rule == "version.semantic-non-final"]
    assert question.recommended == "draft"
    assert "3.x reads 1.0.0 as final" in question.question


def test_blocked_items_say_what_would_let_them_move():
    blocked = {b.rule: b.reason for b in sl.migration.plan(_every_rule(), target=_TARGET).blocked}
    assert "strip_leading_zeros" in blocked["version.2.1-only"]
    assert "move its units to a scheme at 1.0" in blocked["version.unit-scheme"]


def test_question_is_a_value():
    """Frozen and comparable, so a caller can diff two plans' questions."""
    one = sl.migration.plan(_read("complete.xml"), target=_TARGET).questions
    two = sl.migration.plan(_read("complete.xml"), target=_TARGET).questions
    assert one == two
    assert attrs.evolve(one[0], answer="draft") != one[0]
    assert isinstance(one[0], Question)


# ── Annotation references ──────────────────────────────────────────────────


def _linked_to(msg: sl.StructureMessage, title: str) -> sl.StructureMessage:
    """Each DSD carries .Stat's link to an MSD: a `METADATA` annotation titled with its URN."""
    note = sl.Annotation(type="METADATA", title=title)
    return attrs.evolve(msg, data_structures=[attrs.evolve(d, annotations=[note]) for d in msg.data_structures])


_MSD = sl.MetadataStructure.urn_for(agency="TEST", id="MSD_GONE", version="1.2")


def test_an_artefact_only_an_annotation_names_is_asked_about():
    msg = _linked_to(_read("complete.xml"), str(_MSD))
    plain = sl.migration.plan(msg, target=_TARGET)
    named = sl.migration.plan(msg, target=_TARGET, annotation_refs={"METADATA"})
    assert "version.external" not in _rules(plain)
    (question,) = [q for q in named.questions if q.rule == "version.external"]
    assert question.urn == str(_MSD)


def test_the_annotation_follows_the_answer():
    msg = _linked_to(_read("complete.xml"), str(_MSD))
    plan = sl.migration.plan(msg, target=_TARGET, policy={"version.non-final": "draft"}, annotation_refs={"METADATA"})
    migrated = plan.apply({f"version.external@{_MSD}": "final"})
    titles = {a.title for d in migrated.data_structures for a in d.annotations}
    assert titles == {str(attrs.evolve(_MSD, version="1.2.0"))}
