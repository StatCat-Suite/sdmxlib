"""The registry sweep's own logic (`bench/sweep/`).

The sweep runs against live registries and is never in `just ci`, so these
are what keep its instruments honest. Each one is a measuring device whose
failure looks exactly like a clean registry — a census that counts nothing
reports nothing lost, a diff that compares nothing reports nothing different —
so every test here pairs the quiet case with a case it must catch.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import attrs
import pytest

from sdmxlib.formats import parse, serialize
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from tests.support.repo import ROOT

# `bench/` is outside the trees mutmut copies into `mutants/`, so a plain
# `from bench.sweep import ...` would abort the whole mutation run at
# collection (`test_no_test_imports_a_repo_tree_mutmut_does_not_copy`). The
# sweep is a package whose modules import each other, so it cannot be loaded
# by path like the `tools/` scripts; it is imported normally and the file is
# skipped where it is absent. The sweep is not what a mutation run measures.
_analyse = pytest.importorskip("bench.sweep.analyse")
_capacity = pytest.importorskip("bench.sweep.capacity")
_census = pytest.importorskip("bench.sweep.census")
_modeldiff = pytest.importorskip("bench.sweep.modeldiff")
_split = pytest.importorskip("bench.sweep.split")
DocumentReport, Tally, Totals = _analyse.DocumentReport, _analyse.Tally, _analyse.Totals
analyse_chunk, issue_key = _analyse.analyse_chunk, _analyse.issue_key
rule_key, xsd_key = _analyse.rule_key, _analyse.xsd_key
estimate, measure = _capacity.estimate, _capacity.measure
census, gained, lost = _census.census, _census.gained, _census.lost
diff = _modeldiff.diff
chunks, ensure_chunks = _split.chunks, _split.ensure_chunks

pytestmark = pytest.mark.unit

_DOC = b"""<m:Structure xmlns:m="M" xmlns:s="S" xmlns:c="C">
<m:Header><m:ID>IREF1</m:ID><m:Sender id="ZZZ"/></m:Header>
<m:Structures><s:Codelists>
<s:Codelist id="CL_A" agencyID="X" version="1.0" isFinal="true" urn="urn:x">
<c:Name xml:lang="en">A</c:Name><c:Name xml:lang="fr">A</c:Name>
<s:Code id="P"><s:Code id="Q"/></s:Code><s:Code id="R"><c:Link rel="x" url="y"/></s:Code>
</s:Codelist>
<s:Codelist id="CL_B" agencyID="X" version="1.0" isFinal="false"/>
</s:Codelists></m:Structures></m:Structure>"""


def test_census_counts_below_structures_and_skips_the_header():
    counts = census(_DOC)
    assert "Header" not in " ".join(counts)
    assert "ID" not in counts
    assert counts["Codelists/Codelist"] == 2


def test_census_collapses_an_element_nested_in_itself():
    # A code tree is one path however deep it goes, or depth would masquerade as content.
    assert census(_DOC)["Codelists/Codelist/Code"] == 3
    assert not any(path.endswith("Code/Code") for path in census(_DOC))


def test_census_keys_a_boolean_by_its_value_and_lang_by_name():
    counts = census(_DOC)
    assert counts["Codelists/Codelist/@isFinal=true"] == 1
    assert counts["Codelists/Codelist/@isFinal=false"] == 1
    assert counts["Codelists/Codelist/Name/@lang"] == 2


def test_lost_reports_a_removed_element_and_separates_the_derivable():
    after = census(_DOC.replace(b'<c:Link rel="x" url="y"/>', b"").replace(b' urn="urn:x"', b""))
    real, derivable = lost(census(_DOC), after)
    assert real == {
        "Codelists/Codelist/Code/Link": 1,
        "Codelists/Codelist/Code/Link/@rel": 1,
        "Codelists/Codelist/Code/Link/@url": 1,
    }
    assert derivable == {"Codelists/Codelist/@urn": 1}


def test_lost_and_gained_are_empty_for_the_same_document():
    assert lost(census(_DOC), census(_DOC)) == ({}, {})
    assert gained(census(_DOC), census(_DOC)) == ({}, {})


def test_census_of_a_real_round_trip_is_capable_of_seeing_a_loss():
    """The census over our own writer's output — the sweep's real use."""
    codelist = Codelist(id="CL_A", maintainer="X", name={"en": "A"}, codes=[Code(id="P", name={"en": "p"})])
    written = serialize(StructureMessage(codelists=[codelist]), version="3.0")
    assert census(written)["Codelists/Codelist/Code"] == 1
    stripped = attrs.evolve(codelist, codes=[])
    real, _ = lost(census(written), census(serialize(StructureMessage(codelists=[stripped]), version="3.0")))
    assert real["Codelists/Codelist/Code"] == 1


def _codelist(
    *,
    codes: list[Code] | None = None,
    name: InternationalString | None = None,
    description: dict[str, str] | None = None,
) -> Codelist:
    return Codelist(
        id="CL_A",
        maintainer="X",
        name=name or InternationalString(en="A"),
        description=description or {},
        codes=codes if codes is not None else [Code(id="P", name={"en": "p"}), Code(id="Q", name={"en": "q"})],
    )


def test_diff_of_equal_artefacts_is_empty():
    assert diff(_codelist(), _codelist()) == {}


def test_diff_names_a_changed_item_field_without_its_id():
    other = _codelist(codes=[Code(id="P", name={"en": "changed"}), Code(id="Q", name={"en": "q"})])
    assert list(diff(_codelist(), other)) == ["Codelist.codes[].name (text)"]


def test_diff_reports_items_present_on_one_side_only():
    other = _codelist(codes=[Code(id="P", name={"en": "p"})])
    assert list(diff(_codelist(), other)) == ["Codelist.codes[] (only left)"]


def test_diff_reports_a_language_set_change_apart_from_a_text_change():
    other = _codelist(name=InternationalString(en="A", fr="A"))
    assert list(diff(_codelist(), other)) == ["Codelist.name (languages)"]


def test_diff_compares_refs_by_urn_not_by_resolution():
    urn = SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=X:CL_A(1.0)")
    other_urn = SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=X:CL_B(1.0)")
    assert diff(Ref[object].unresolved(urn), Ref[object].of(urn, _codelist())) == {}
    assert list(diff(Ref[object].unresolved(urn), Ref[object].unresolved(other_urn))) == ["Ref"]


def test_diff_survives_a_parse_round_trip_of_its_own_writer():
    """Discrimination: equal after a lossless round trip, unequal after a lossy edit."""
    message = StructureMessage(codelists=[_codelist()])
    back = parse(serialize(message, version="3.0"))
    assert isinstance(back, StructureMessage)
    assert diff(_codelist(), back.codelists[0]) == {}
    assert diff(_codelist(description={"en": "new"}), back.codelists[0]) != {}


def test_rule_key_removes_values_but_keeps_the_rule():
    a = rule_key("Codelist 'CL_A' version 1.2.3.4 is not valid in urn:sdmx:x=Y:Z(1.0)")
    b = rule_key("Codelist 'CL_B' version 7.0 is not valid in urn:sdmx:x=Q:W(2.0)")
    assert a == b == "Codelist <q> version <n> is not valid in <urn>"


def test_rule_key_drops_a_counted_findings_examples():
    # How many examples a message lists varies per artefact; the rule does not (#450).
    rule = "SDMX-ML 3.0 requires a node id to be unique"
    one = rule_key(f"1 node id(s) repeat in another branch: BYINS at FA.BYINS, L.BYINS. {rule}")
    four = rule_key(
        f"4 node id(s) repeat in another branch: A at X.A, Y.A; B at X.B, Y.B; C at X.C, Y.C and 1 more. {rule}"
    )
    assert (
        one
        == four
        == "<n> node id(s) repeat in another branch: <examples>. SDMX-ML <n> requires a node id to be unique"
    )
    parent = rule_key("2 code(s) have a parent SDMX-ML 3.0 cannot name: _Z (parent '1'), AA (parent '2'). 3.0.0 types")
    assert parent == "<n> code(s) have a parent SDMX-ML <n> cannot name: <examples>. <n> types"


def test_issue_key_drops_the_artefact_and_item_ids_from_the_path():
    assert issue_key("DataConstraint:CPI_IDX.constraint_type", "x") == "DataConstraint.constraint_type: x"
    assert issue_key("Hierarchy.codes[AG]", "duplicate node id 'AG'") == "Hierarchy.codes[]: duplicate node id <q>"
    # As `Registry.add` labels it (#419), with a nested agency.
    added = issue_key("DataStructure:IMF.STA.DS:DSD_X.time_dimension.type", "x")
    assert added == "DataStructure.time_dimension.type: x"


def test_refusal_key_drops_the_item_ids_from_each_path():
    reason = "DataStructure.dimensions[FREQ].concept — required; DataStructure.measures[OBS_VALUE].concept — required"
    assert _analyse.refusal_key(reason) == (
        "DataStructure.dimensions[].concept — required; DataStructure.measures[].concept — required"
    )


def test_xsd_key_keeps_the_element_and_facet_and_drops_the_value():
    one = xsd_key("Element '{ns}Parent': [facet 'pattern'] The value '_T' is not accepted by the pattern 'P'.")
    two = xsd_key("Element '{ns}Parent': [facet 'pattern'] The value '_Z' is not accepted by the pattern 'P'.")
    assert one == two == "Element 'Parent': [facet 'pattern'] The value <v> is not accepted by the pattern 'P'."
    other = xsd_key("Element '{ns}Source': [facet 'pattern'] The value 'x' is not accepted by the pattern 'P'.")
    assert other != one


def test_split_cuts_between_artefacts_and_loses_none(tmp_path: Path):
    codelists = [_codelist(name=InternationalString(en=n)) for n in ("A", "B", "C")]
    codelists = [attrs.evolve(c, id=f"CL_{i}") for i, c in enumerate(codelists)]
    source = tmp_path / "doc.xml"
    source.write_bytes(serialize(StructureMessage(codelists=codelists), version="3.0"))

    pieces = list(chunks(source, budget=1))  # one artefact always exceeds the budget

    assert len(pieces) == len(codelists)
    ids = []
    for piece in pieces:
        message = parse(piece)
        assert isinstance(message, StructureMessage)
        ids.extend(c.id for c in message.codelists)
    assert ids == ["CL_0", "CL_1", "CL_2"]
    whole = census(source)
    summed = sum((census(piece) for piece in pieces), Counter[str]())
    assert summed["Codelists/Codelist/Code"] == whole["Codelists/Codelist/Code"] == 6


def test_split_with_a_large_budget_is_one_piece(tmp_path: Path):
    source = tmp_path / "doc.xml"
    source.write_bytes(serialize(StructureMessage(codelists=[_codelist()]), version="3.0"))
    assert len(list(chunks(source))) == 1


def test_capacity_honours_an_explicit_job_count():
    assert measure(jobs=3).workers == 3
    assert measure(jobs=0).workers == 1


def test_capacity_never_plans_zero_workers_or_every_core():
    capacity = measure()
    assert capacity.workers >= 1
    assert capacity.workers == 1 or capacity.workers < capacity.cores


def test_estimate_grows_with_what_is_parsed_at_once():
    assert estimate(0) < estimate(10_000_000) < estimate(100_000_000)


def test_census_does_not_count_containers():
    assert "Codelists" not in census(_DOC)


def _three_codelists(tmp_path: Path) -> Path:
    codelists = [attrs.evolve(_codelist(), id=f"CL_{i}") for i in range(3)]
    source = tmp_path / "doc.xml"
    source.write_bytes(serialize(StructureMessage(codelists=codelists), version="3.0"))
    return source


def test_merged_chunk_totals_equal_the_whole_document(tmp_path: Path):
    """The parallel path's guarantee: chunk counts merged in order are the whole's counts."""
    source = _three_codelists(tmp_path)
    whole = Totals("3.0")
    whole.take(source.read_bytes(), 0)
    merged = Totals("3.0")
    for index, path in enumerate(ensure_chunks(source, tmp_path / "chunks", budget=1)):
        merged.merge(analyse_chunk("3.0", path, index))
    assert merged.pieces == 3
    a, b = DocumentReport(), DocumentReport()
    whole.fill(a)
    merged.fill(b)
    assert a == b
    assert a.get("artefacts") == {"Codelist": 3}


def test_tally_merge_keeps_the_first_example_and_sums_counts():
    first, second = Tally(), Tally()
    first.add("k", "from first")
    second.add("k", "from second", 2)
    second.add("j", "only second")
    first.merge(second)
    assert first.as_report() == {"k": {"n": 3, "example": "from first"}, "j": {"n": 1, "example": "only second"}}


def test_chunks_are_reused_until_the_source_changes(tmp_path: Path):
    source = _three_codelists(tmp_path)
    out = tmp_path / "chunks"
    first = ensure_chunks(source, out, budget=1)
    stamp = first[0].stat().st_mtime_ns
    assert ensure_chunks(source, out, budget=1) == first
    assert first[0].stat().st_mtime_ns == stamp  # not rewritten
    assert len(ensure_chunks(source, out, budget=10**9)) == 1  # a new budget re-splits


def test_a_defaulted_attribute_is_lost_only_when_it_was_not_the_default():
    # `maxOccurs` defaults to 1 in both XSDs: omitting `1` loses nothing, omitting `25` loses the bound.
    doc = _DOC.replace(b'<s:Code id="R">', b'<s:Code id="R" maxOccurs="1"/><s:Code id="S" maxOccurs="25">')
    real, derivable = lost(census(doc), census(doc.replace(b' maxOccurs="1"', b"").replace(b' maxOccurs="25"', b"")))
    assert derivable["Codelists/Codelist/Code/@maxOccurs=1"] == 1
    assert real == {"Codelists/Codelist/Code/@maxOccurs=25": 1}


def test_an_empty_element_is_derivable_but_one_with_an_id_is_not():
    doc = _DOC.replace(
        b'<s:Code id="R">', b'<s:Code id="R"><c:AnnotationURL/><c:Text xml:lang="en"/><c:Annotation id="k"/>'
    )
    stripped = doc.replace(b'<c:AnnotationURL/><c:Text xml:lang="en"/><c:Annotation id="k"/>', b"")
    real, derivable = lost(census(doc), census(stripped))
    assert derivable == {
        "Codelists/Codelist/Code/AnnotationURL(empty)": 1,
        "Codelists/Codelist/Code/Text(empty)": 1,
        "Codelists/Codelist/Code/Text(empty)/@lang": 1,
    }
    assert real == {"Codelists/Codelist/Code/Annotation": 1, "Codelists/Codelist/Code/Annotation/@id": 1}


def _underscore_parent(*, parent: str) -> bytes:
    codes = [Code(id=parent, name={"en": "p"}), Code(id="C0", name={"en": "c"}, parent_id=parent)]
    codelist = Codelist(id="CL", maintainer="AG", name={"en": "cl"}, codes=codes)
    return serialize(StructureMessage(codelists=[codelist]), version="3.0", validate=False)


def test_a_refused_write_is_not_counted_as_a_read_loss():
    """The census measures the reader: a codelist 3.0 refuses (#432) was still read whole."""
    totals = Totals("3.0")
    totals.take(_underscore_parent(parent="_T"), 0)
    report = DocumentReport()
    totals.fill(report)
    assert report["targets"]["3.0"]["refused"]
    assert report["targets"]["3.0"]["written"] == 0
    assert report["read_lost"] == {}


def test_a_written_codelist_is_censused_once():
    """Discrimination: the unchecked census write adds nothing for an accepted artefact."""
    refused, accepted = Totals("3.0"), Totals("3.0")
    refused.take(_underscore_parent(parent="_T"), 0)
    accepted.take(_underscore_parent(parent="T"), 0)
    assert accepted.targets["3.0"].written == 1
    assert sum(refused.written.values()) == sum(accepted.written.values())


# ── schema defaults and the 2.1 alias form (GH-36) ───────────────────────

_FLAGS = b"""<m:Structure xmlns:m="M" xmlns:s="S"><m:Structures><s:MetadataStructures>
<s:MetadataStructure id="MSD" agencyID="X" version="1.0">
<s:MetadataAttribute id="A" isPresentational="false"/><s:MetadataAttribute id="B" isPresentational="true"/>
</s:MetadataStructure></s:MetadataStructures><s:DataConstraints>
<s:DataConstraint id="DC" agencyID="X" version="1.0"><s:CubeRegion>
<s:KeyValue id="FREQ" include="true" removePrefix="false"/><s:KeyValue id="AREA" removePrefix="true"/>
</s:CubeRegion></s:DataConstraint></s:DataConstraints></m:Structures></m:Structure>"""


def test_a_default_is_derivable_and_its_opposite_is_lost():
    """Omitting `isPresentational="false"`, `include="true"` or `removePrefix="false"` says
    the same thing; omitting a `true` the model failed to hold loses it. The census has
    to stay able to fail."""
    stripped = (
        _FLAGS.replace(b' isPresentational="false"', b"")
        .replace(b' isPresentational="true"', b"")
        .replace(b' include="true"', b"")
        .replace(b' removePrefix="false"', b"")
        .replace(b' removePrefix="true"', b"")
    )
    real, derivable = lost(census(_FLAGS), census(stripped))
    msd = "MetadataStructures/MetadataStructure/MetadataAttribute"
    region = "DataConstraints/DataConstraint/CubeRegion/KeyValue"
    assert derivable == {
        f"{msd}/@isPresentational=false": 1,
        f"{region}/@include=true": 1,
        f"{region}/@removePrefix=false": 1,
    }
    assert real == {f"{msd}/@isPresentational=true": 1, f"{region}/@removePrefix=true": 1}


def test_a_default_the_writer_spells_out_is_a_derivable_gain():
    """GitLab #503's mirror: a writer adding `usage="optional"` the source omitted
    gains nothing, and adding a `true` it did not say gains something."""
    written = _FLAGS.replace(b'<s:MetadataAttribute id="A"', b'<s:MetadataAttribute id="A" usage="optional"').replace(
        b'<s:KeyValue id="AREA"', b'<s:KeyValue id="AREA" include="false"'
    )
    real, derivable = gained(census(_FLAGS), census(written))
    assert derivable == {"MetadataStructures/MetadataStructure/MetadataAttribute/@usage=optional": 1}
    assert real == {"DataConstraints/DataConstraint/CubeRegion/KeyValue/@include=false": 1}


def test_the_21_alias_form_of_a_code_reference_is_derivable():
    """OECD names a hierarchical code's code through an alias. The reader resolves it, the
    writer writes the full `Code` reference, and the alias form is set aside with its reason;
    the full reference it became shows as gained, which is what the writer did."""
    full = (ROOT / "tests" / "fixtures" / "sdmx_ml21" / "with_hierarchy.xml").read_bytes()
    reference = (
        b"<str:Code>\n"
        b'              <Ref agencyID="TEST" maintainableParentID="CL_AREA" maintainableParentVersion="1.0" '
        b'id="CA" package="codelist" class="Code"/>\n'
        b"            </str:Code>"
    )
    included = (
        b'<str:IncludedCodelist alias="AREA"><Ref agencyID="TEST" id="CL_AREA" version="1.0" '
        b'package="codelist" class="Codelist"/></str:IncludedCodelist>\n        <str:Hierarchy '
    )
    alias = full.replace(b"<str:Hierarchy ", included, 1).replace(
        reference, b'<str:CodeID><Ref id="CA"/></str:CodeID><str:CodelistAliasRef>AREA</str:CodelistAliasRef>'
    )
    assert alias.count(b"<str:CodelistAliasRef>") == 1, "the fixture changed under this test"
    written = serialize(parse(alias, strict=False), version="2.1")
    real, derivable = lost(census(alias), census(written))
    node = "HierarchicalCodelists/HierarchicalCodelist/Hierarchy/HierarchicalCode"
    assert real == {}
    assert derivable == {
        f"{node}/CodeID": 1,
        f"{node}/CodeID/Ref": 1,
        f"{node}/CodeID/Ref/@id": 1,
        f"{node}/CodelistAliasRef": 1,
        "HierarchicalCodelists/HierarchicalCodelist/IncludedCodelist/@alias": 1,
    }
    assert gained(census(alias), census(written))[0][f"{node}/Code/Ref/@id"] == 1
