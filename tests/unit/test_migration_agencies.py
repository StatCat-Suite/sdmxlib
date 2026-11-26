"""The agency rule: every sub-agency a message's maintainers name is declared by its parent's scheme (GH-399)."""

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.migration import Target

pytestmark = pytest.mark.unit

_TARGET = Target(version="3.0")


def _codelist(maintainer: str) -> sl.Codelist:
    return sl.Codelist(
        id="CL", maintainer=maintainer, version="1.0.0", name={"en": "c"}, codes=[sl.Code(id="A", name={"en": "a"})]
    )


def _scheme(maintainer: str, *agencies: str) -> sl.AgencyScheme:
    return sl.AgencyScheme(
        id="AGENCIES",
        maintainer=maintainer,
        name={"en": "agencies"},
        agencies=[sl.Agency(id=a, name={"en": a}) for a in agencies],
    )


def _rules(plan: sl.migration.Plan) -> set[str]:
    return {f.rule for f in (*plan.changes, *plan.blocked) if f.rule.startswith("agency.")}


def test_a_sub_agency_without_names_is_blocked_naming_what_is_missing():
    plan = sl.migration.plan(sl.StructureMessage(codelists=[_codelist("CA1.QOL")]), target=_TARGET)
    (blocked,) = [b for b in plan.blocked if b.rule == "agency.unnamed"]
    assert blocked.reason.endswith("agency_names= has none for CA1.QOL, CA1:AGENCIES")


def test_with_names_the_scheme_is_added_and_declares_the_agency():
    names = {"CA1.QOL": {"en": "Quality of Life"}, "CA1:AGENCIES": {"en": "CA1 agencies"}}
    plan = sl.migration.plan(sl.StructureMessage(codelists=[_codelist("CA1.QOL")]), target=_TARGET, agency_names=names)
    (change,) = [c for c in plan.changes if c.rule == "agency.scheme"]
    assert (change.before, change.after) == (None, "QOL")
    migrated = plan.apply()
    (scheme,) = migrated.agency_schemes
    assert (scheme.agency_id, scheme.id) == ("CA1", "AGENCIES")
    assert scheme.agencies["QOL"].name["en"].text == "Quality of Life"
    assert scheme.name["en"].text == "CA1 agencies"
    assert sl.to_xml(migrated, version="3.0")


def test_a_held_scheme_is_extended_and_needs_no_scheme_name():
    msg = sl.StructureMessage(codelists=[_codelist("CA1.QOL")], agency_schemes=[_scheme("CA1", "OTHER")])
    plan = sl.migration.plan(msg, target=_TARGET, agency_names={"CA1.QOL": {"en": "Quality of Life"}})
    (change,) = [c for c in plan.changes if c.rule == "agency.scheme"]
    assert (change.before, change.after) == ("OTHER", "OTHER, QOL")
    (scheme,) = plan.apply().agency_schemes
    assert scheme.agencies.ids() == ["OTHER", "QOL"]


def test_a_declared_sub_agency_and_a_top_level_agency_ask_nothing():
    declared = sl.StructureMessage(codelists=[_codelist("CA1.QOL")], agency_schemes=[_scheme("CA1", "QOL")])
    assert _rules(sl.migration.plan(declared, target=_TARGET)) == set()
    assert _rules(sl.migration.plan(sl.StructureMessage(codelists=[_codelist("CA1")]), target=_TARGET)) == set()


def test_a_deeper_sub_agency_needs_its_own_parent_scheme():
    plan = sl.migration.plan(sl.StructureMessage(codelists=[_codelist("CA1.QOL.X")]), target=_TARGET)
    (blocked,) = [b for b in plan.blocked if b.rule == "agency.unnamed"]
    assert blocked.urn.endswith("AgencyScheme=CA1.QOL:AGENCIES(1.0)")


def test_every_sub_agency_of_one_parent_is_declared_in_one_change():
    msg = sl.StructureMessage(codelists=[_codelist("CA1.QOL"), attrs.evolve(_codelist("CA1.ENV"), id="CL2")])
    names = {"CA1.QOL": {"en": "q"}, "CA1.ENV": {"en": "e"}, "CA1:AGENCIES": {"en": "a"}}
    plan = sl.migration.plan(msg, target=_TARGET, agency_names=names)
    (change,) = [c for c in plan.changes if c.rule == "agency.scheme"]
    assert change.after == "ENV, QOL"
