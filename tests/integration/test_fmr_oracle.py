"""FMR as an independent oracle: what it makes of our documents, replayed (#457).

`bench/fmr` sent the maximal fixture and the version shapes to a local Fusion
Metadata Registry and recorded what it served back. Everything here replays
those recordings, offline:

- **What was sent is what we send now.** A fixture or writer change that alters
  the document fails here, so the recordings are re-made rather than left
  describing a document nobody writes any more.
- **Every adjustment is matched and needed** (`bench.fmr.ledger`): each removes
  something from its document, and the recording shows FMR refusing the
  document without it -- or accepting fewer artefacts, which is how FMR's
  silent truncation shows.
- **What FMR served is classified** (`tests.support.fmr_differences`): every
  difference our reader sees, beyond the gaps `formats.gaps` declares, matches
  a rule, and every rule matches a difference. A rule calling FMR's output
  invalid rests on that output failing its schema, which is checked.
- **FMR still reads and serves no SDMX-ML 3.1**, as recorded; an FMR that does
  fails here, and 3.1 joins the documents.
"""

import json
from pathlib import Path

import pytest
from lxml import etree

from tests.support import fmr_oracle
from tests.support.fmr_differences import INVALID, RULES, Kind
from tests.support.fmr_oracle import CASES, FMR_VERSION, FORMATS, case_dir, every_difference
from tests.support.interop import Classification, Rule, classify, load_json
from tests.support.json_schema import compile_json_schema
from tests.support.xsd import compile_structure_schema

pytestmark = pytest.mark.integration

# `bench/` is outside what mutmut copies into `mutants/`; imported as
# `test_sweep.py` does, so a mutation run skips this file rather than aborting.
documents = pytest.importorskip("bench.fmr.documents")
_ledger = pytest.importorskip("bench.fmr.ledger")
ADJUSTMENTS = _ledger.ADJUSTMENTS
KINDS = _ledger.KINDS

_DOCUMENTS = [
    (f"{version}-{document}", version, document) for version in documents.VERSIONS for document in documents.DOCUMENTS
]


def _necessity(case: str) -> list[dict[str, object]]:
    loaded = load_json(case_dir(case) / "necessity.json")
    assert isinstance(loaded, list)
    return loaded


def _accepted(case: str) -> list[str]:
    loaded = load_json(case_dir(case) / "accepted.json")
    assert isinstance(loaded, dict)
    accepted = loaded["accepted"]
    assert isinstance(accepted, list)
    return accepted


def _needed(record: dict[str, object], full: int) -> bool:
    """Whether FMR refused the document without the adjustment, or accepted fewer artefacts."""
    accepted = record["accepted"]
    assert isinstance(accepted, list)
    return record["refusal"] is not None or len(accepted) < full


# What was recorded.


def test_the_recordings_name_the_fmr_that_made_them() -> None:
    info = load_json(fmr_oracle.RECORDINGS / "fmr.json")
    assert isinstance(info, dict)
    assert info["version"] == FMR_VERSION


@pytest.mark.parametrize(("case", "version", "document"), _DOCUMENTS)
def test_what_was_sent_is_what_the_fixtures_and_ledger_make_now(case: str, version: str, document: str) -> None:
    recorded = (case_dir(case) / "sent.xml").read_bytes()
    assert documents.adjusted(version, document) == recorded, f"{case} changed: re-record with `just fmr-record`"


def test_fmr_reads_and_serves_no_sdmx_ml_31() -> None:
    info = load_json(fmr_oracle.RECORDINGS / "fmr.json")
    assert isinstance(info, dict)
    ml31 = info["ml31"]
    assert isinstance(ml31, dict)
    submit = ml31["submit"]
    assert isinstance(submit, dict)
    assert "Unrecognised file format" in str(submit["refusal"])
    assert ml31["fetch_status"] == 406


# The adjustments.


@pytest.mark.parametrize(("case", "version", "document"), _DOCUMENTS)
def test_every_adjustment_removes_something(case: str, version: str, document: str) -> None:
    for adjustment in documents.adjustments(version, document):
        root = etree.fromstring(documents.written(version, document))
        assert documents.apply(root, adjustment.xpath), f"{case}: {adjustment.xpath} matches nothing"


@pytest.mark.parametrize(("case", "version", "document"), _DOCUMENTS)
def test_every_adjustment_is_needed(case: str, version: str, document: str) -> None:
    recorded = _necessity(case)
    expected = [a.xpath for a in documents.adjustments(version, document)]
    assert [r["xpath"] for r in recorded] == expected, f"{case}: the ledger changed since recording; re-record"
    full = len(_accepted(case))
    unneeded = [r["xpath"] for r in recorded if not _needed(r, full)]
    assert not unneeded, f"{case}: FMR took the document without {unneeded}"


def test_a_silent_truncation_counts_as_needed() -> None:
    # FMR 12.4.2 reported success on the 3.0 maximal without the ValueItem
    # adjustment, having accepted 8 of its 27 artefacts.
    full = len(_accepted("3.0-maximal"))
    record = next(r for r in _necessity("3.0-maximal") if "ValueItem" in str(r["xpath"]))
    assert record["refusal"] is None
    assert _needed(record, full)
    assert not _needed({"refusal": None, "accepted": ["x"] * full}, full)


def test_every_adjustment_kind_is_described() -> None:
    assert {a.kind for a in ADJUSTMENTS} <= set(KINDS)
    assert len(ADJUSTMENTS) == sum(len(_necessity(case)) for case in CASES) == 37


# What FMR served.


def _validity() -> dict[str, bool]:
    xsd = {"ml21": compile_structure_schema("2.1"), "ml30": compile_structure_schema("3.0")}
    json_schema = {"json10": compile_json_schema("structure", "1.0"), "json20": compile_json_schema("structure", "2.0")}
    out: dict[str, bool] = {}
    for case in CASES:
        statuses = load_json(case_dir(case) / "back" / "status.json")
        assert isinstance(statuses, dict)
        for name, status in statuses.items():
            if status != 200:
                continue
            fmt = name.rsplit("-", 1)[1]
            path: Path = case_dir(case) / "back" / f"{name}.{FORMATS[fmt]}"
            if fmt in xsd:
                out[f"{case}/{name}"] = xsd[fmt].validate(etree.parse(str(path)))
            else:
                out[f"{case}/{name}"] = json_schema[fmt].is_valid(json.loads(path.read_bytes()))
    return out


def test_fmr_output_is_invalid_exactly_where_declared() -> None:
    validity = _validity()
    assert {name for name, valid in validity.items() if not valid} == set(INVALID)
    assert sum(validity.values()) == 7, "the valid responses are counted, so a vanished recording shows"


def _classify(rules: tuple[Rule[Kind], ...]) -> Classification:
    return classify(rules, every_difference())


def test_every_difference_is_classified_once() -> None:
    found = _classify(RULES)
    unclassified, ambiguous = found.unclassified, found.ambiguous
    assert not unclassified, "\n".join(f"{case}:{fmt}  {entry}" for case, fmt, entry in unclassified)
    assert not ambiguous, "\n".join(f"{case}:{fmt}  {entry}" for case, fmt, entry in ambiguous)


def test_every_rule_classifies_a_difference() -> None:
    covered = _classify(RULES).covered
    dead = [RULES[i].entry.pattern for i, where in covered.items() if not where]
    assert not dead, f"rules no difference needs: {dead}"


def test_a_rule_left_out_leaves_its_differences_unclassified() -> None:
    month = next(i for i, rule in enumerate(RULES) if "month endTime" in rule.reason)
    unclassified = _classify(RULES[:month] + RULES[month + 1 :]).unclassified
    assert unclassified
    assert all(entry.endswith("representation.end_time") for _, _, entry in unclassified)


def test_every_invalid_classification_rests_on_an_invalid_document() -> None:
    covered = _classify(RULES).covered
    invalid_pairs = {name.split("/")[0] + ":" + name.rsplit("-", 1)[1] for name in INVALID}
    for index, where in covered.items():
        if RULES[index].kind == "fmr-invalid":
            assert where <= invalid_pairs, (
                f"{RULES[index].entry.pattern} calls valid documents invalid: {where - invalid_pairs}"
            )


def test_the_classification_reaches_every_case_and_format() -> None:
    # Discrimination: the differences are read from every recording, so an
    # empty or unreadable one would show as a case or format with none.
    seen = {f"{case}:{fmt}" for case, fmt, _ in every_difference()}
    assert seen == {f"{case}:{fmt}" for case in CASES for fmt in FORMATS}
