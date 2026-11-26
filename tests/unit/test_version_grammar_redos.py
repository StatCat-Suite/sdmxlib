"""SDMX-ML 3.0's version grammar: the XSD's language, matched in linear time.

The pattern copied from the XSD backtracked exponentially on a prerelease of
repeated ``---.`` (CodeQL ``py/redos``), and differed from the XSD after a
dot. `model.version._GRAMMAR_30` now writes the same language unambiguously;
here it is held to the vendored XSD's own patterns, as the oracle.
"""

import itertools
import re
import time

import pytest
from hypothesis import given
from hypothesis import strategies as st
from lxml import etree

from sdmxlib.model.version import _GRAMMAR_30
from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

_XSD = "{http://www.w3.org/2001/XMLSchema}"


def _xsd_pattern(type_name: str) -> re.Pattern[str]:
    tree = etree.parse(str(ROOT / "tests" / "schemas" / "sdmx_ml30" / "SDMXCommonReferences.xsd"))
    (simple,) = [t for t in tree.iter(f"{_XSD}simpleType") if t.get("name") == type_name]
    (pattern,) = [p.get("value") for p in simple.iter(f"{_XSD}pattern")]
    assert pattern is not None
    return re.compile(pattern)


_LEGACY = _xsd_pattern("LegacyVersionNumberType")
_SEMANTIC = _xsd_pattern("SemanticVersionNumberType")


def _xsd_accepts(version: str) -> bool:
    """The XSD's `VersionType` union: an XSD pattern matches the whole value."""
    return bool(_LEGACY.fullmatch(version) or _SEMANTIC.fullmatch(version))


def _ours(version: str) -> bool:
    return bool(_GRAMMAR_30.match(version))


def test_every_short_prerelease_agrees_with_the_xsd():
    """Exhaustive: every prerelease up to 6 characters over letters, digits, hyphens and dots."""
    alphabet = "a01-."
    disagreements = [
        version
        for length in range(1, 7)
        for chars in itertools.product(alphabet, repeat=length)
        if _ours(version := "1.0.0-" + "".join(chars)) != _xsd_accepts(version)
    ]
    assert disagreements == []


def test_every_short_version_agrees_with_the_xsd():
    alphabet = "019.-a"
    disagreements = [
        version
        for length in range(1, 7)
        for chars in itertools.product(alphabet, repeat=length)
        if _ours(version := "".join(chars)) != _xsd_accepts(version)
    ]
    assert disagreements == []


@given(st.text(alphabet="aZ09-.+_", max_size=14))
def test_a_random_prerelease_agrees_with_the_xsd(tail: str) -> None:
    version = "2.10.0-" + tail
    assert _ours(version) == _xsd_accepts(version)


def test_the_agreement_check_can_tell_the_old_pattern_apart():
    """Discrimination: the copy before this change refused ``1.0.0-x.a1``, which the XSD accepts."""
    assert _xsd_accepts("1.0.0-x.a1")
    assert _ours("1.0.0-x.a1")


def test_an_adversarial_prerelease_is_matched_in_linear_time():
    """100,000 repeats of the pattern that took 0.9 s at 14."""
    version = "1.0.0-" + "---." * 100_000 + "!"
    start = time.perf_counter()
    assert not _ours(version)
    assert time.perf_counter() - start < 1.0
