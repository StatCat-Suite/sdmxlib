"""A caller's own migration rules, and `omit_agencies` (GH-427)."""

from collections.abc import Callable, Iterator

import pytest

import sdmxlib as sl
from sdmxlib.migration import Change, Option, Question, Rule, Target, omit_agencies

pytestmark = pytest.mark.unit


def _codelist(maintainer: str, id_: str = "CL", version: str = "1.0.0") -> sl.Codelist:
    return sl.Codelist(
        id=id_, maintainer=maintainer, version=version, name={"en": "c"}, codes=[sl.Code(id="A", name={"en": "a"})]
    )


def _concepts() -> sl.ConceptScheme:
    return sl.ConceptScheme(
        id="CS", maintainer="OK", version="1.0.0", name={"en": "cs"}, concepts=[sl.Concept(id="D", name={"en": "d"})]
    )


def _dsd(id_: str, codelist: sl.Codelist | sl.Ref[sl.Codelist]) -> sl.DataStructure:
    representation = codelist if isinstance(codelist, sl.Ref) else sl.Ref.to(codelist)
    return sl.DataStructure(
        id=id_,
        maintainer="OK",
        version="1.0.0",
        name={"en": id_},
        dimensions=[sl.Dimension(id="D", concept=_concepts().ref_to("D"), representation=representation)],
    )


def _omitted(plan: sl.migration.Plan) -> dict[str, str]:
    return {change.urn: change.reason for change in plan.changes if change.rule == "agency.omitted"}


def test_omit_agencies_leaves_out_the_agency_and_what_references_it_transitively():
    test = _codelist("TEST")
    dsd = _dsd("DSD", test)
    flow = sl.Dataflow(id="DF", maintainer="OK", version="1.0.0", name={"en": "df"}, structure=sl.Ref.to(dsd))
    kept = _codelist("OK")
    msg = sl.StructureMessage(
        codelists=[test, kept], concept_schemes=[_concepts()], data_structures=[dsd], dataflows=[flow]
    )
    plan = sl.migration.plan(msg, target=Target(version="3.0", rules=[omit_agencies("TEST")]))
    omitted = _omitted(plan)
    assert omitted == {
        str(test.urn): "maintained by TEST, which the caller leaves out",
        str(dsd.urn): f"it references {test.urn}, which is left out",
        str(flow.urn): f"it references {dsd.urn}, which is left out",
    }
    held = {str(artefact.urn) for artefact in plan.apply().all_artefacts()}
    assert held == {str(kept.urn), str(_concepts().urn)}


def test_omit_agencies_follows_a_wildcard_reference():
    test = _codelist("TEST")
    wildcard = sl.Ref[sl.Codelist].unresolved(sl.Codelist.urn_for(agency="TEST", id="CL", version="1+.0.0"))
    dsd = _dsd("DSD", wildcard)
    msg = sl.StructureMessage(codelists=[test], concept_schemes=[_concepts()], data_structures=[dsd])
    plan = sl.migration.plan(msg, target=Target(version="3.0", rules=[omit_agencies("TEST")]))
    assert set(_omitted(plan)) == {str(test.urn), str(dsd.urn)}


def test_omit_agencies_leaves_a_sub_agency_alone_unless_named():
    msg = sl.StructureMessage(codelists=[_codelist("TEST.SUB")])
    assert _omitted(sl.migration.plan(msg, target=Target(version="3.0", rules=[omit_agencies("TEST")]))) == {}
    named = sl.migration.plan(msg, target=Target(version="3.0", rules=[omit_agencies("TEST.SUB")]))
    assert len(_omitted(named)) == 1


def _asking(rule_id: str, *option_ids: str) -> Callable[[sl.StructureMessage], Iterator[Question]]:
    def find(msg: sl.StructureMessage) -> Iterator[Question]:
        for codelist in msg.codelists:
            urn = sl.SdmxUrn.parse(str(codelist.urn))
            yield Question(
                key=f"{rule_id}@{urn}",
                rule=rule_id,
                urn=str(urn),
                question="keep it?",
                options=[
                    Option(id=option_id, consequence=option_id, edits=(sl.migration.Omit(urn=urn),))
                    if option_id == "omit"
                    else Option(id=option_id, consequence=option_id, edits=())
                    for option_id in option_ids
                ],
            )

    return find


def test_a_callers_question_is_answered_by_policy_like_a_built_in_one():
    rule = Rule(find=_asking("local.keep", "keep", "omit"), questions={"local.keep": ("keep", "omit")})
    target = Target.fmr("12.5.0", rules=[rule])
    assert target.rules == (rule,)
    msg = sl.StructureMessage(codelists=[_codelist("OK")])
    plan = sl.migration.plan(msg, target=target, policy={"local.keep": "omit"})
    (question,) = [q for q in plan.questions if q.rule == "local.keep"]
    assert question.answer == "omit"
    assert list(plan.apply().all_artefacts()) == []


def test_a_policy_for_an_undeclared_option_of_a_callers_rule_is_refused():
    rule = Rule(find=_asking("local.keep", "keep", "omit"), questions={"local.keep": ("keep", "omit")})
    msg = sl.StructureMessage(codelists=[_codelist("OK")])
    with pytest.raises(ValueError, match="no option 'drop'"):
        sl.migration.plan(msg, target=Target(version="3.0", rules=[rule]), policy={"local.keep": "drop"})


def test_a_callers_question_it_did_not_declare_is_refused():
    msg = sl.StructureMessage(codelists=[_codelist("OK")])
    undeclared = Rule(find=_asking("local.keep", "keep", "omit"))
    with pytest.raises(ValueError, match="declares no such rule"):
        sl.migration.plan(msg, target=Target(version="3.0", rules=[undeclared]))
    other_options = Rule(find=_asking("local.keep", "keep", "omit"), questions={"local.keep": ("keep",)})
    with pytest.raises(ValueError, match=r"declares \['keep'\]"):
        sl.migration.plan(msg, target=Target(version="3.0", rules=[other_options]))


def test_a_callers_rule_cannot_claim_a_built_in_rule_id():
    rule = Rule(find=lambda _msg: iter(()), questions={"version.non-final": ("draft",)})
    with pytest.raises(ValueError, match="built-in rule"):
        sl.migration.plan(sl.StructureMessage(), target=Target(version="3.0", rules=[rule]))


def test_a_callers_change_is_applied():
    def find(msg: sl.StructureMessage) -> Iterator[Change]:
        for codelist in msg.codelists:
            urn = sl.SdmxUrn.parse(str(codelist.urn))
            yield Change(
                rule="local.rename",
                urn=str(urn),
                path="id",
                before=urn.id,
                after="CL2",
                reason="local naming",
                edits=(
                    sl.migration.Move(
                        old=urn, new=sl.SdmxUrn[object](urn.package, urn.artefact_class, urn.agency, "CL2", urn.version)
                    ),
                ),
            )

    msg = sl.StructureMessage(codelists=[_codelist("OK")])
    migrated = sl.migration.plan(msg, target=Target(version="3.0", rules=[Rule(find=find)])).apply()
    assert migrated.codelists.ids() == ["CL2"]
