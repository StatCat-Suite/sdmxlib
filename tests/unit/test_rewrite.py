"""`sl.rewrite`: change artefacts' identities, and every reference follows (GH-406).

The migration in GH-399 re-versions most of a registry (`X.Y` → `X.Y.0`) and
renames a few artefacts; each reference has to follow, including the item
URNs that point into a scheme (`Concept=A:CS(1.0).FREQ`).
"""

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.model.link import Link
from sdmxlib.model.ref import SupportsUrn
from sdmxlib.model.validation import _collect_refs
from tests.support import repo
from tests.support.narrow import present
from tests.support.roundtrip import structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_COMPLETE = repo.ROOT / "tests" / "fixtures" / "sdmx_ml30" / "complete.xml"


def _message() -> sl.StructureMessage:
    msg = sl.read(_COMPLETE)
    assert isinstance(msg, sl.StructureMessage)
    return msg


def _refs(msg: sl.StructureMessage) -> list[tuple[str, sl.Ref[object]]]:
    return [pair for artefact in msg.all_artefacts() for pair in _collect_refs(artefact, artefact.id)]


def _urn(artefact: SupportsUrn) -> sl.SdmxUrn[object]:
    return sl.SdmxUrn.parse(str(sl.artefact_urn(artefact)))


def _semver(msg: sl.StructureMessage) -> dict[sl.SdmxUrn[object], sl.SdmxUrn[object]]:
    """Every artefact `1.0` → `1.0.0`, the migration's commonest change.

    Less the organisation schemes, whose version every format fixes at 1.0.
    """
    return {_urn(a): attrs.evolve(_urn(a), version="1.0.0") for a in msg.all_artefacts() if not _fixed(a)}


def _fixed(artefact: object) -> bool:
    return isinstance(artefact, (sl.AgencyScheme, sl.DataProviderScheme, sl.DataConsumerScheme))


def test_every_artefact_and_every_reference_moves_together():
    msg = _message()
    migrated = sl.rewrite(msg, _semver(msg))

    assert {a.version for a in migrated.all_artefacts() if not _fixed(a)} == {"1.0.0"}
    refs = _refs(migrated)
    assert len(refs) == 23
    # The provision agreement and a constraint name a provider, in a scheme fixed at 1.0.
    stayed = [str(ref.urn) for _, ref in refs if ref.urn.version != "1.0.0"]
    assert stayed == ["urn:sdmx:org.sdmx.infomodel.base.DataProvider=TEST:DATA_PROVIDERS(1.0).PROV_A"] * 2
    sl.validate_references(migrated)


def test_the_reference_check_can_fail():
    """Discrimination: the source's references all name 1.0, so the check above sees a change."""
    refs = _refs(_message())
    assert len(refs) == 23
    assert {ref.urn.version for _, ref in refs} == {"1.0"}


def test_references_resolve_to_the_rewritten_artefacts():
    msg = _message()
    migrated = sl.rewrite(msg, _semver(msg))
    (dataflow,) = migrated.dataflows
    (dsd,) = migrated.data_structures
    assert present(dataflow.structure)() is dsd


def test_an_item_reference_follows_its_scheme():
    msg = _message()
    scheme = _urn(msg.concept_schemes[0])
    migrated = sl.rewrite(msg, {scheme: attrs.evolve(scheme, version="2.0.0")})
    concepts = {ref.urn for path, ref in _refs(migrated) if ref.urn.artefact_class == "Concept"}
    assert concepts
    assert {(urn.id, urn.version) for urn in concepts} == {("CROSS_DOMAIN_CONCEPTS", "2.0.0")}


def test_a_rename_moves_the_artefact_and_its_references():
    msg = _message()
    old = sl.Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    new = sl.Codelist.urn_for(agency="SDMX", id="CL_FREQUENCY", version="1.0.0")
    migrated = sl.rewrite(msg, {old: new})
    assert "SDMX:CL_FREQ" not in {f"{c.agency_id}:{c.id}" for c in migrated.codelists}
    assert migrated.codelists["SDMX:CL_FREQUENCY(1.0.0)"].codes.ids() == msg.codelists["SDMX:CL_FREQ"].codes.ids()
    named = [ref.urn for _, ref in _refs(migrated) if ref.urn.id in {"CL_FREQ", "CL_FREQUENCY"}]
    assert named
    assert {str(urn) for urn in named} == {str(new)}


def test_the_source_message_is_left_as_it_was():
    msg = _message()
    before = [str(ref.urn) for _, ref in _refs(msg)]
    sl.rewrite(msg, _semver(msg))
    assert [str(ref.urn) for _, ref in _refs(msg)] == before
    assert {a.version for a in msg.all_artefacts()} == {"1.0"}


def test_an_artefact_the_message_lacks_still_redirects_references_to_it():
    msg = _message()
    held = attrs.evolve(msg, codelists=[c for c in msg.codelists if c.id != "CL_FREQ"])
    old = sl.Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    migrated = sl.rewrite(held, {old: attrs.evolve(old, version="1.0.0")})
    versions = {ref.urn.version for _, ref in _refs(migrated) if ref.urn.id == "CL_FREQ"}
    assert versions == {"1.0.0"}


def test_a_mapping_that_moves_nothing_returns_the_message():
    msg = _message()
    urn = _urn(msg.dataflows[0])
    assert sl.rewrite(msg, {}) is msg
    assert sl.rewrite(msg, {urn: urn}) is msg


def test_a_link_naming_a_rewritten_artefact_follows():
    old = sl.Codelist.urn_for(agency="A", id="CL", version="1.0")
    new = attrs.evolve(old, version="1.0.0")
    linked = sl.Codelist(
        id="CL_OTHER",
        maintainer="A",
        name={"en": "other"},
        links=(Link(rel="codelist", url="https://example.org/cl", urn=str(old)),),
    )
    msg = sl.StructureMessage(codelists=[linked])
    ((link,),) = [c.links for c in sl.rewrite(msg, {old: new}).codelists]
    assert link.urn == str(new)
    assert link.url == "https://example.org/cl"


# ── Refusals ───────────────────────────────────────────────────────────────


def _refused(msg: sl.StructureMessage, mapping: sl.UrnMap) -> list[str]:
    with pytest.raises(sl.ValidationError) as caught:
        sl.rewrite(msg, mapping)
    return [issue.message for issue in caught.value.issues]


def test_an_item_urn_key_is_refused():
    item = sl.SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Code=SDMX:CL_FREQ(1.0).A")
    (message,) = _refused(_message(), {item: attrs.evolve(item, version="1.0.0")})
    assert message.endswith(": an item URN; only a maintainable's identity changes")
    assert message.startswith("key ")
    assert " and value " in message


def test_a_class_change_is_refused():
    old = sl.Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    new = sl.ValueList.urn_for(agency="SDMX", id="CL_FREQ", version="1.0.0")
    (message,) = _refused(_message(), {old: new})
    assert "is a ValueList, and" in message


def test_two_artefacts_mapped_to_one_urn_are_refused():
    one = sl.Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    two = sl.Codelist.urn_for(agency="TEST", id="CL_AREA", version="1.0")
    target = sl.Codelist.urn_for(agency="SDMX", id="CL_X", version="1.0.0")
    (message,) = _refused(_message(), {one: target, two: target})
    assert "are both mapped to" in message


def test_a_value_the_message_already_holds_is_refused():
    one = sl.Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    held = sl.Codelist.urn_for(agency="TEST", id="CL_AREA", version="1.0")
    (message,) = _refused(_message(), {one: held})
    assert "is held by the message and not moved by the mapping" in message


def test_a_value_the_mapping_moves_out_of_the_way_is_allowed():
    """A swap: each target is held, and each is moved, so no two artefacts share one."""
    one = sl.Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    two = sl.Codelist.urn_for(agency="TEST", id="CL_AREA", version="1.0")
    swapped = sl.rewrite(_message(), {one: two, two: one})
    assert len({str(_urn(c)) for c in swapped.codelists}) == len(swapped.codelists)


def test_a_key_with_no_one_version_is_refused():
    latest = sl.Codelist.urn_for(agency="SDMX", id="CL_FREQ")
    (message,) = _refused(_message(), {latest: attrs.evolve(latest, version="1.0.0")})
    assert message.endswith(": names no one version")


def _with_latest_structure(msg: sl.StructureMessage) -> sl.StructureMessage:
    (dataflow,) = msg.dataflows
    latest = sl.DataStructure.urn_for(agency="TEST", id="DSD_TEST")
    return attrs.evolve(msg, dataflows=[attrs.evolve(dataflow, structure=sl.Ref[sl.DataStructure].unresolved(latest))])


def test_a_latest_reference_to_a_renamed_artefact_is_refused():
    msg = _with_latest_structure(_message())
    old = sl.DataStructure.urn_for(agency="TEST", id="DSD_TEST", version="1.0")
    (message,) = _refused(msg, {old: attrs.evolve(old, id="DSD_RENAMED")})
    assert "with no exact version, and the mapping moves that artefact" in message


def test_a_latest_reference_survives_a_version_change():
    """`latest` still answers when only the version moves."""
    msg = _with_latest_structure(_message())
    old = sl.DataStructure.urn_for(agency="TEST", id="DSD_TEST", version="1.0")
    migrated = sl.rewrite(msg, {old: attrs.evolve(old, version="1.0.0")})
    assert present(migrated.dataflows[0].structure).urn.is_latest


def test_every_problem_is_reported_at_once():
    item = sl.SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Code=SDMX:CL_FREQ(1.0).A")
    old = sl.Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    messages = _refused(
        _message(),
        {item: attrs.evolve(item, version="1.0.0"), old: sl.ValueList.urn_for(agency="SDMX", id="V", version="1.0.0")},
    )
    assert len(messages) == 2


def test_the_rewritten_message_writes_as_3_0_and_reads_back_the_same():
    msg = _message()
    migrated = sl.rewrite(msg, _semver(msg))
    written = sl.to_xml(migrated, version="3.0")
    assert_valid_sdmx_ml(written, structure_schema("3.0"))
    read_back = sl.parse(written)
    assert isinstance(read_back, sl.StructureMessage)
    assert sorted(str(_urn(a)) for a in read_back.all_artefacts()) == sorted(
        str(_urn(a)) for a in migrated.all_artefacts()
    )
    assert sorted(str(ref.urn) for _, ref in _refs(read_back)) == sorted(str(ref.urn) for _, ref in _refs(migrated))


# ── Annotation references ──────────────────────────────────────────────────


def _annotated(msg: sl.StructureMessage, title: str, *, kind: str = "METADATA") -> sl.StructureMessage:
    """The dataflow carries one annotation, and its first codelist's first code another, both titled *title*."""
    note = sl.Annotation(type=kind, title=title)
    (dataflow,) = msg.dataflows
    first, *rest = msg.codelists
    code, *codes = first.codes
    codelist = attrs.evolve(first, codes=[attrs.evolve(code, annotations=[note]), *codes])
    return attrs.evolve(msg, dataflows=[attrs.evolve(dataflow, annotations=[note])], codelists=[codelist, *rest])


def _titles(msg: sl.StructureMessage) -> list[str | None]:
    return [
        *(a.title for a in msg.dataflows[0].annotations),
        *(a.title for a in msg.codelists[0].codes[0].annotations),
    ]


def test_an_annotation_of_a_named_type_follows_on_artefacts_and_items():
    msg = _message()
    old = _urn(msg.data_structures[0])
    new = attrs.evolve(old, version="1.0.0")
    migrated = sl.rewrite(_annotated(msg, str(old)), {old: new}, annotation_refs={"METADATA"})
    assert _titles(migrated) == [str(new), str(new)]


def test_an_annotation_is_opaque_unless_its_type_is_named():
    """Discrimination: the same message and mapping, with the type not named or named differently."""
    msg = _message()
    old = _urn(msg.data_structures[0])
    new = attrs.evolve(old, version="1.0.0")
    assert _titles(sl.rewrite(_annotated(msg, str(old)), {old: new})) == [str(old), str(old)]
    named_other = sl.rewrite(_annotated(msg, str(old), kind="NOTE"), {old: new}, annotation_refs={"METADATA"})
    assert _titles(named_other) == [str(old), str(old)]


def test_an_annotation_title_that_is_not_a_urn_is_left_alone():
    msg = _message()
    old = _urn(msg.data_structures[0])
    migrated = sl.rewrite(
        _annotated(msg, "see the DSD"), {old: attrs.evolve(old, version="1.0.0")}, annotation_refs={"METADATA"}
    )
    assert _titles(migrated) == ["see the DSD", "see the DSD"]
