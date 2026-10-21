"""What `just bench-compete` says about sdmxlib's output, and what each library keeps (#500).

The report writes with validation off so the writer alone is timed. Before
#500 it then called a file sdmxlib's default `serialize` refuses `INVALID`,
as though sdmxlib wrote it: BIS's `_T` parents in SDMX-ML 3.0 read as a bug
that #432 had already handled. The label must say "refused by default", and
`INVALID` must stay reachable for a real writer bug.

The fidelity counts are measured against the document's own text, not any
library's read, so a loss shows as a loss rather than as a disagreement.
"""

import re

import pytest

from sdmxlib.formats import parse, serialize
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.message import StructureMessage

pytestmark = pytest.mark.unit

# `bench/` is outside the trees mutmut copies into `mutants/`, so a plain
# `from bench.compete import ...` would abort the whole mutation run at
# collection (`test_no_test_imports_a_repo_tree_mutmut_does_not_copy`). As in
# `test_sweep.py`, it is imported this way and the file is skipped where it is
# absent. The benchmark harness is not what a mutation run measures.
_main = pytest.importorskip("bench.compete.__main__")
_libs = pytest.importorskip("bench.compete.libs")
probe = _main.probe
Fidelity, dropped, fidelity, label = _libs.Fidelity, _libs.dropped, _libs.fidelity, _libs.label
refused_by_default, source_fidelity = _libs.refused_by_default, _libs.source_fidelity


def _with_total() -> StructureMessage:
    """BIS's shape: `_T`, SDMX's total, as a parent. A valid IDType that 3.0.0 cannot name as one (#432)."""
    codes = [Code(id="_T", name={"en": "Total"}), Code(id="A", name={"en": "A"}, parent_id="_T")]
    return StructureMessage(codelists=[Codelist(id="CL", maintainer="AG", name={"en": "c"}, codes=codes)])


def test_a_write_the_default_refuses_is_labelled_refused_not_invalid():
    message = _with_total()
    refusal = refused_by_default(message, "3.0")
    assert refusal is not None
    assert "parent SDMX-ML 3.0 cannot name" in refusal
    unchecked = serialize(message, version="3.0", validate=False)
    assert label("3.0", unchecked, refusal).startswith("refused by default: ")


def test_the_unchecked_write_really_is_schema_invalid():
    """The discrimination check for the case above: without the refusal, the label would say INVALID."""
    unchecked = serialize(_with_total(), version="3.0", validate=False)
    assert label("3.0", unchecked, None).startswith("INVALID")


def test_invalid_stays_reachable_for_a_writer_bug():
    """Sabotage: output the default *would* write, broken after the fact, must still read INVALID."""
    message = probe()
    assert refused_by_default(message, "3.0") is None
    written = serialize(message, version="3.0")
    assert label("3.0", written, None) == "valid"
    broken, n = re.subn(rb'<str:Code id="A"', b'<str:Code id="A B"', written)
    assert n == 1, "the sabotage lost its subject"
    assert label("3.0", broken, None).startswith("INVALID")


@pytest.mark.parametrize("fmt", ["2.1", "3.0", "json"])
def test_the_source_count_agrees_with_sdmxlib_on_its_own_output(fmt: str):
    """Two independent counts of one document. sdmxlib keeps all of the probe, so they must agree."""
    data = serialize(probe(), version=fmt)
    read = parse(data)
    assert isinstance(read, StructureMessage)
    source = source_fidelity(fmt, data)
    assert source == Fidelity(codes=3, languages=frozenset({"en", "fr"}), parents=2, annotations=1, descriptions=1), (
        "the probe lost what it exists to show"
    )
    assert fidelity(read) == source
    assert dropped(source, fidelity(read)) == []


def test_dropped_names_each_loss():
    """The discrimination check for the agreement above: a read that keeps less is reported, item by item."""
    source = Fidelity(codes=3, languages=frozenset({"en", "fr"}), parents=2, annotations=1, descriptions=1)
    assert dropped(source, Fidelity(3, frozenset({"en"}), 0, 1, 1)) == ["languages ['fr']", "2 of 2 parents"]
    assert dropped(source, Fidelity(3, None, 0, 0, 1)) == [
        "all but one of 2 languages, unlabelled",
        "2 of 2 parents",
        "1 of 1 annotations",
    ]
