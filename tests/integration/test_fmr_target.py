"""`Target.fmr`: the plan predicts what FMR 12.5.0 does, checked against what it did (GH-399).

The registry-rules recording (`bench/fmr/registry_rules.py`) sent FMR small
messages and kept its answers. Here the same messages are planned against
`Target.fmr("12.5.0")`, and each prediction is held against the recording:
a refusal the plan foresees is one FMR made, with the version FMR named, and
a change the plan reports is what FMR served.
"""

import json

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.migration import Plan, Target, _fmr
from sdmxlib.migration._plan import QUESTIONS
from tests.support.fmr_oracle import recordings

pytestmark = pytest.mark.integration

_CASE = recordings("12.5.0") / "registry-rules"
_TARGET = Target.fmr("12.5.0")


def _read(name: str) -> sl.StructureMessage:
    msg = sl.parse((_CASE / name).read_bytes())
    assert isinstance(msg, sl.StructureMessage)
    return msg


def _refusal(step: str) -> str | None:
    outcomes = json.loads((_CASE / "outcomes.json").read_text())
    refusal = outcomes[step]["refusal"]
    assert refusal is None or isinstance(refusal, str)
    return refusal


def _outcome(step: str) -> dict[str, object]:
    outcomes = json.loads((_CASE / "outcomes.json").read_text())
    found = outcomes[step]
    assert isinstance(found, dict)
    return found


def _merged(*names: str) -> sl.StructureMessage:
    """The recorded documents as one message, as a migration would hold them."""
    msgs = [_read(name) for name in names]
    merged = sl.StructureMessage(
        concept_schemes=[a for m in msgs for a in m.concept_schemes],
        codelists=[a for m in msgs for a in m.codelists],
        data_structures=[a for m in msgs for a in m.data_structures],
        dataflows=[a for m in msgs for a in m.dataflows],
        metadata_structures=[a for m in msgs for a in m.metadata_structures],
        constraints=[a for m in msgs for a in m.constraints],
    )
    # The six kinds the recordings hold; anything else would be lost here.
    assert len(merged.all_artefacts()) == sum(len(m.all_artefacts()) for m in msgs)
    return merged


def _plan(*names: str) -> Plan:
    return sl.migration.plan(_merged(*names), target=_TARGET)


def test_the_fmr_target_is_3_0_with_the_parent_deviation():
    assert (_TARGET.version, _TARGET.allow, _TARGET.registry) == ("3.0", frozenset({"non-ncname-parent"}), "fmr-12.5.0")


def test_an_unrecorded_release_is_refused():
    with pytest.raises(ValueError, match=r"no recorded FMR rules for '12.4.2'; the releases recorded are \['12.5.0'\]"):
        Target.fmr("12.4.2")


def test_the_plan_asks_about_the_actual_constraint_fmr_refused():
    plan = _plan("base.xml", "actual.xml")
    (question,) = [q for q in plan.questions if q.rule == "fmr.actual-constraint"]
    assert question.urn in str(_refusal("actual"))
    assert question.recommended == "omit"


def test_the_plan_foresees_the_major_fmr_expected():
    plan = _plan("codelist-1.0.0.xml", "codelist-1.1.0-removes.xml")
    (question,) = [q for q in plan.questions if q.rule == "fmr.next-major"]
    assert question.urn in str(_refusal("codelist-1.1.0-removes"))
    (move,) = question.option("major").edits
    assert isinstance(move, sl.migration.Move)
    assert str(_refusal("codelist-1.1.0-removes")).endswith(f"expected version of {move.new.version}")


def test_a_version_that_only_adds_codes_asks_nothing_and_fmr_took_it():
    plan = _plan("codelist-1.0.0.xml", "codelist-1.1.0-adds.xml")
    assert [q for q in plan.questions if q.rule == "fmr.next-major"] == []
    assert _refusal("codelist-1.1.0-adds") is None


def test_the_major_answer_overrides_the_version_rule_move():
    """1.1 is final in 2.1, so the version rule moves it to 1.1.0; FMR wants 2.0.0, and the answer wins."""
    first = sl.Codelist(
        id="CL",
        maintainer="AG",
        name={"en": "c"},
        version="1.0",
        legacy_is_final=True,
        codes=[sl.Code(id=c, name={"en": c}) for c in "AB"],
    )
    later = attrs.evolve(first, version="1.1", codes=[sl.Code(id="A", name={"en": "A"})])
    plan = sl.migration.plan(sl.StructureMessage(codelists=[first, later]), target=_TARGET)
    assert [c.after for c in plan.changes if c.rule == "version.semantic"] == ["1.0.0", "1.1.0"]
    migrated = plan.apply({q.key: "major" for q in plan.questions})
    assert sorted(c.version for c in migrated.codelists) == ["1.0.0", "2.0.0"]


def test_the_reported_related_dimension_order_is_what_fmr_served():
    plan = _plan("base.xml")
    (change,) = [c for c in plan.changes if c.rule == "fmr.related-dimensions"]
    served = _read("back/AG-ml30.xml").data_structures["AG:DSD"].attributes["A1"].related_dimensions
    assert change.after == ", ".join(served or [])
    assert change.before != change.after


def test_the_reported_monolingual_change_is_what_fmr_served():
    plan = _plan("base.xml")
    (change,) = [c for c in plan.changes if c.rule == "fmr.monolingual"]
    assert change.path == "attributes[MA].representation.is_multilingual"
    assert _read("back/AG-ml30.xml").metadata_structures["AG:MSD"].attributes["MA"].representation is None


def test_non_ncname_parents_apply_and_fmr_took_them():
    plan = _plan("codelist-non-ncname-parents.xml")
    migrated = plan.apply()
    assert sl.to_xml(migrated, version="3.0", allow=_TARGET.allow)
    assert _refusal("codelist-non-ncname-parents") is None
    with pytest.raises(sl.ValidationError, match="cannot name"):
        sl.migration.plan(_read("codelist-non-ncname-parents.xml"), target=Target(version="3.0")).apply()


def test_a_plain_3_0_target_asks_none_of_the_fmr_questions():
    """Discrimination: the FMR rules are the target's, not 3.0's."""
    plan = sl.migration.plan(
        _merged(
            "base.xml",
            "actual.xml",
            "codelist-1.0.0.xml",
            "codelist-1.1.0-removes.xml",
            "codelist-lower-case-1.0.xml",
            "codelist-case-clash-1.0.xml",
        ),
        target=Target(version="3.0"),
    )
    assert {f.rule for f in (*plan.changes, *plan.questions, *plan.blocked) if f.rule.startswith("fmr.")} == set()


def test_the_plan_asks_to_rename_the_lower_case_id_fmr_upper_cased():
    plan = _plan("codelist-lower-case-1.0.0.xml")
    (question,) = [q for q in plan.questions if q.rule == "fmr.id-case"]
    assert question.recommended == "rename"
    (move,) = question.option("rename").edits
    assert isinstance(move, sl.migration.Move)
    assert _outcome("codelist-lower-case-1.0.0")["accepted"] == [str(move.new)]


def test_the_rename_composes_with_the_version_move():
    codelist = sl.Codelist(
        id="CL_low",
        maintainer="AG",
        version="1.0",
        legacy_is_final=True,
        name={"en": "c"},
        codes=[sl.Code(id="A", name={"en": "A"})],
    )
    flow_dsd = sl.StructureMessage(codelists=[codelist])
    plan = sl.migration.plan(flow_dsd, target=_TARGET, policy={"fmr.id-case": "rename"})
    (migrated,) = plan.apply().codelists
    assert (migrated.id, migrated.version) == ("CL_LOW", "1.0.0")


def test_the_plan_blocks_the_case_clash_fmr_lost_an_artefact_to():
    plan = _plan("codelist-lower-case-1.0.xml", "codelist-case-clash-1.0.xml")
    (blocked,) = [b for b in plan.blocked if b.rule == "fmr.id-case-clash"]
    assert blocked.urn.endswith("Codelist=AG:CL_low(1.0)")
    assert [q for q in plan.questions if q.rule == "fmr.id-case"] == []
    assert _outcome("codelist-case-clash-1.0")["accepted"] == [
        "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:CL_LOW(1.0)"
    ]
    semantic = _plan("codelist-lower-case-1.0.0.xml", "codelist-case-clash-1.0.0.xml")
    assert [b.rule for b in semantic.blocked if b.rule == "fmr.id-case-clash"] == ["fmr.id-case-clash"]
    assert "can not be modified" in str(_refusal("codelist-case-clash-1.0.0"))


def test_every_fmr_rule_fires_on_the_recordings():
    plan = _plan(
        "base.xml",
        "actual.xml",
        "codelist-1.0.0.xml",
        "codelist-1.1.0-removes.xml",
        "codelist-lower-case-1.0.xml",
        "codelist-case-clash-1.0.xml",
        "codelist-lower-case-1.0.0.xml",
    )
    fired = {f.rule for f in (*plan.changes, *plan.questions, *plan.blocked) if f.rule.startswith("fmr.")}
    assert fired == {
        "fmr.actual-constraint",
        "fmr.next-major",
        "fmr.related-dimensions",
        "fmr.monolingual",
        "fmr.id-case",
        "fmr.id-case-clash",
    }
    assert set(_fmr.QUESTIONS) == {"fmr.actual-constraint", "fmr.next-major", "fmr.id-case"}
    assert set(_fmr.QUESTIONS) <= set(QUESTIONS)
