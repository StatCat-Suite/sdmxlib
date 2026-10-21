"""pysdmx as an independent SDMX-ML 3.1 oracle: what it makes of our documents, replayed (#457).

FMR 12.4.2 reads no SDMX-ML 3.1 (`test_fmr_oracle.py` holds it to that), so
`bench/pysdmx_oracle` had pysdmx read our 3.1 maximal fixture and shape, and
write back what it read, as 3.1. Everything here replays those recordings,
offline:

- **What was sent is what we send now**, or the recordings are re-made.
- **Every adjustment is matched and needed** (`bench.pysdmx_oracle.ledger`):
  each removes something, and pysdmx fails on the document without it.
- **What pysdmx wrote is classified** (`tests.support.pysdmx_differences`):
  every difference our reader sees, beyond what `formats.gaps` declares for
  3.1, matches exactly one rule, and every rule matches a difference. A rule
  calling pysdmx's output invalid rests on that output failing the schema.
"""

import pytest
from lxml import etree

from tests.support import pysdmx_oracle
from tests.support.interop import Classification, Rule, classify, load_json
from tests.support.pysdmx_differences import RULES, Kind
from tests.support.pysdmx_oracle import CASES, FORMAT, PYSDMX_VERSION, case_dir, every_difference
from tests.support.xsd import compile_structure_schema

pytestmark = pytest.mark.integration

# `bench/` is outside what mutmut copies into `mutants/`; imported as
# `test_sweep.py` does, so a mutation run skips this file rather than aborting.
documents = pytest.importorskip("bench.pysdmx_oracle.documents")
_ledger = pytest.importorskip("bench.pysdmx_oracle.ledger")

_DOCUMENTS = [(f"3.1-{document}", document) for document in documents.DOCUMENTS]


def _necessity(case: str) -> list[dict[str, object]]:
    loaded = load_json(case_dir(case) / "necessity.json")
    assert isinstance(loaded, list)
    return loaded


# What was recorded.


def test_the_recordings_name_the_pysdmx_that_made_them() -> None:
    info = load_json(pysdmx_oracle.RECORDINGS / "pysdmx.json")
    assert isinstance(info, dict)
    assert info["version"] == PYSDMX_VERSION


@pytest.mark.parametrize(("case", "document"), _DOCUMENTS)
def test_what_was_sent_is_what_the_fixtures_and_ledger_make_now(case: str, document: str) -> None:
    recorded = (case_dir(case) / "sent.xml").read_bytes()
    assert documents.adjusted(document) == recorded, f"{case} changed: re-record with `just pysdmx-record`"


def test_pysdmx_read_what_it_models() -> None:
    # Discrimination for the differences below: pysdmx read most of each
    # document, so a difference is a field it lost, not a document it refused.
    read = {case: load_json(case_dir(case) / "read.json") for case in CASES}
    assert read == {
        "3.1-maximal": {
            "AgencyScheme": 1,
            "Categorisation": 1,
            "CategoryScheme": 1,
            "Codelist": 3,
            "ConceptScheme": 2,
            "DataConstraint": 5,
            "DataConsumerScheme": 1,
            "DataProviderScheme": 1,
            "DataStructureDefinition": 1,
            "Dataflow": 1,
            "Hierarchy": 1,
            "HierarchyAssociation": 1,
            "MetadataProviderScheme": 1,
            "MetadataProvisionAgreement": 1,
            "MetadataStructure": 1,
            "Metadataflow": 1,
            "ProvisionAgreement": 1,
            "RepresentationMap": 2,
            "StructureMap": 1,
        },
        "3.1-shape": {
            "AvailabilityConstraint": 1,
            "CategoryScheme": 2,
            "Codelist": 2,
            "ConceptScheme": 1,
            "DataConstraint": 1,
            "DataStructureDefinition": 1,
            "Dataflow": 2,
            "MetadataStructure": 1,
            "RepresentationMap": 3,
            "StructureMap": 1,
        },
    }


# The adjustments.


@pytest.mark.parametrize(("case", "document"), _DOCUMENTS)
def test_every_adjustment_removes_something(case: str, document: str) -> None:
    for adjustment in documents.adjustments(document):
        root = etree.fromstring(documents.written(document))
        assert documents.apply(root, adjustment.xpath), f"{case}: {adjustment.xpath} matches nothing"


@pytest.mark.parametrize(("case", "document"), _DOCUMENTS)
def test_every_adjustment_is_needed(case: str, document: str) -> None:
    recorded = _necessity(case)
    expected = [a.xpath for a in documents.adjustments(document)]
    assert [r["xpath"] for r in recorded] == expected, f"{case}: the ledger changed since recording; re-record"
    unneeded = [r["xpath"] for r in recorded if r["failure"] is None]
    assert not unneeded, f"{case}: pysdmx took the document without {unneeded}"


def test_every_adjustment_kind_is_described() -> None:
    assert {a.kind for a in _ledger.ADJUSTMENTS} <= set(_ledger.KINDS)
    assert len(_ledger.ADJUSTMENTS) == sum(len(_necessity(case)) for case in CASES) == 22


# What pysdmx wrote.


def test_pysdmx_output_is_invalid_exactly_where_declared() -> None:
    schema = compile_structure_schema("3.1")
    validity = {case: schema.validate(etree.parse(str(case_dir(case) / "back.xml"))) for case in CASES}
    assert validity == {"3.1-maximal": False, "3.1-shape": True}


def _classify(rules: tuple[Rule[Kind], ...]) -> Classification:
    return classify(rules, every_difference())


def test_every_difference_is_classified_once() -> None:
    found = _classify(RULES)
    assert not found.unclassified, "\n".join(f"{case}  {entry}" for case, _, entry in found.unclassified)
    assert not found.ambiguous, "\n".join(f"{case}  {entry}" for case, _, entry in found.ambiguous)


def test_every_rule_classifies_a_difference() -> None:
    covered = _classify(RULES).covered
    dead = [RULES[i].entry.pattern for i, where in covered.items() if not where]
    assert not dead, f"rules no difference needs: {dead}"


def test_a_rule_left_out_leaves_its_differences_unclassified() -> None:
    include = next(i for i, rule in enumerate(RULES) if "region excludes" in rule.reason)
    unclassified = _classify(RULES[:include] + RULES[include + 1 :]).unclassified
    assert [entry for _, _, entry in unclassified] == ["DataConstraint.regions[].keys[].include"]


def test_every_invalid_classification_rests_on_an_invalid_document() -> None:
    covered = _classify(RULES).covered
    for index, where in covered.items():
        if RULES[index].kind == "pysdmx-invalid":
            assert where == {f"3.1-maximal:{FORMAT}"}, f"{RULES[index].entry.pattern} calls a valid document invalid"


def test_the_classification_reaches_every_case() -> None:
    assert {case for case, _, _ in every_difference()} == set(CASES)
