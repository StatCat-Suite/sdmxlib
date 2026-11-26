"""`Target.fmr`'s refused constructs, held to the FMR oracle's ledger (GH-399).

`sdmxlib.migration._fmr_refusals` names each construct by the XPath the
ledger removed before FMR 12.5.0 would take a document, and the oracle's
recordings show FMR refusing each document without its removal
(`test_fmr_oracle.test_every_adjustment_is_needed`). These tests hold the
product's table to that ledger: the same XPaths, matched in the same
documents, and gone once the plan strips them.
"""

import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.migration._fmr_refusals import DUPLICATE_MAPS, REFUSALS, _duplicate_maps, _written
from sdmxlib.model.mapping import StructureMap
from tests.support import maximal, shapes

pytestmark = pytest.mark.integration

_ledger = pytest.importorskip("bench.fmr.ledger")

_TARGET = sl.migration.Target.fmr("12.5.0")
_DOCUMENTS = {"maximal": maximal.message, "shape": shapes.message}


def _ledger_xpaths(document: str | None = None) -> set[str]:
    return {
        a.xpath
        for a in _ledger.ADJUSTMENTS
        if a.version == "3.0" and a.holds("12.5.0") and (document is None or a.document == document)
    }


def test_the_table_is_the_ledgers_3_0_adjustments_for_12_5_0():
    table = {xpath for refusal in (*REFUSALS, DUPLICATE_MAPS) for xpath in refusal.xpaths}
    assert table == _ledger_xpaths()


def _matched(msg: sl.StructureMessage) -> set[str]:
    """The table's XPaths that match some artefact of *msg* written as 3.0, the duplicate rule on the model."""
    found: set[str] = set()
    for artefact in msg.all_artefacts():
        tree = _written(artefact)
        if tree is not None:
            found |= {xpath for refusal in REFUSALS for xpath in refusal.xpaths if tree.xpath(xpath)}
        if isinstance(artefact, StructureMap) and _duplicate_maps(artefact):
            found |= set(DUPLICATE_MAPS.xpaths)
    return found


@pytest.mark.parametrize("document", sorted(_DOCUMENTS))
def test_each_document_matches_exactly_its_ledger_adjustments(document: str):
    assert _matched(_DOCUMENTS[document]()) == _ledger_xpaths(document)


@pytest.mark.parametrize("document", sorted(_DOCUMENTS))
def test_the_plan_asks_about_every_construct_the_ledger_removed(document: str):
    plan = sl.migration.plan(_DOCUMENTS[document](), target=_TARGET)
    asked = {q.rule for q in plan.questions}
    expected = {
        refusal.rule for refusal in (*REFUSALS, DUPLICATE_MAPS) if set(refusal.xpaths) & _ledger_xpaths(document)
    }
    assert expected <= asked
    assert {rule for rule in asked if rule in {r.rule for r in (*REFUSALS, DUPLICATE_MAPS)}} == expected


def _strip_everything(plan: sl.migration.Plan) -> dict[str, str]:
    """Strip where the plan offers it, omit where it does not, and the recommended answer elsewhere."""
    refused = {r.rule for r in (*REFUSALS, DUPLICATE_MAPS)}
    answers: dict[str, str] = {}
    for question in plan.open:
        ids = [o.id for o in question.options]
        if question.rule in refused:
            answers[question.key] = "strip" if "strip" in ids else "omit"
        else:
            answers[question.key] = question.recommended or ids[0]
    return answers


@pytest.mark.parametrize("document", sorted(_DOCUMENTS))
def test_stripped_and_omitted_nothing_the_ledger_removed_is_left(document: str):
    plan = sl.migration.plan(_DOCUMENTS[document](), target=_TARGET)
    migrated = plan.apply(_strip_everything(plan))
    written = etree.fromstring(sl.to_xml(migrated, version="3.0", allow=_TARGET.allow))
    left = {xpath for xpath in _ledger_xpaths(document) if written.xpath(xpath)}
    # The duplicate-maps XPath matches any second component map; stripping keeps distinct pairs.
    assert left - set(DUPLICATE_MAPS.xpaths) == set()
    assert not any(_duplicate_maps(m) for m in migrated.structure_maps)


def test_a_strip_keeps_the_artefact():
    """Discrimination: strip rebuilds the artefact, omit removes it."""
    plan = sl.migration.plan(maximal.message(), target=_TARGET)
    question = next(q for q in plan.questions if q.rule == "fmr.concept-role")
    answers = _strip_everything(plan)
    stripped = plan.apply({**answers, question.key: "strip"})
    omitted = plan.apply({**answers, question.key: "omit"})
    urn = question.urn.rsplit("(", 1)[0]
    held = {str(sl.artefact_urn(a)).rsplit("(", 1)[0] for a in stripped.all_artefacts()}
    gone = {str(sl.artefact_urn(a)).rsplit("(", 1)[0] for a in omitted.all_artefacts()}
    assert urn in held
    assert urn not in gone
    dsd = next(
        d for d in stripped.data_structures if question.urn.startswith(str(sl.artefact_urn(d)).rsplit("(", 1)[0])
    )
    assert all(not getattr(c, "concept_role", ()) for c in (*dsd.dimensions, *dsd.attributes, *dsd.measures))
