"""The 3.0 and 3.1 fixture sets must exercise the same element vocabulary.

`sdmx_ml31` was forked from `sdmx_ml30` "at parity", and its *fixtures* were
forked too — then trimmed. `<str:TargetDataType>` was dropped from the 3.1
`with_representation_map_variants.xml`, and the 3.1 reader and writer both
stopped handling it. The round-trip guard passed the whole time, because both
sides had lost the same thing (#345).

That is the failure mode a round-trip test cannot see by construction: it
compares a document against itself, so anything absent from the document is
absent from the comparison. What catches it is comparing the two *corpora*.

Not a claim that the generations are identical — 3.1 really did drop
`Categorisation/@version`, and 2.1 has a different vocabulary entirely. It is
a claim that a difference is a *decision*, listed below with a reason, rather
than something that happened when a file was copied.
"""

from __future__ import annotations

import collections
import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

_FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"

#: Elements the 3.0 corpus exercises and the 3.1 one deliberately does not.
#: An entry is a decision, and needs a reason the XSD supports — not "nobody
#: got round to it", which is what produced #345.
_ONLY_IN_30: dict[str, str] = {
    # Declared three times in the 3.0 XSD and not at all in 3.1: the element
    # was removed, not moved. `formats/gaps.py` carries the matching entry, so
    # a caller can ask before writing rather than discover after.
    "ReleaseCalendar": "3.1 removed ReleaseCalendar from DataConstraintType",
    "Periodicity": "child of ReleaseCalendar",
    "Offset": "child of ReleaseCalendar",
    "Tolerance": "child of ReleaseCalendar",
}

#: The reverse. Also empty, and also a place for a reason if it stops being.
_ONLY_IN_31: dict[str, str] = {}


def _vocabulary(generation: str) -> collections.Counter[str]:
    """Every `<str:…>` element name the generation's fixtures contain."""
    counts: collections.Counter[str] = collections.Counter()
    for path in sorted((_FIXTURES / generation).glob("*.xml")):
        counts.update(re.findall(r"<str:(\w+)", path.read_text(encoding="utf-8")))
    return counts


def test_the_corpora_are_both_non_trivial() -> None:
    """Discriminating power: two empty sets are equal, and prove nothing."""
    for generation in ("sdmx_ml30", "sdmx_ml31"):
        assert len(_vocabulary(generation)) > 30, f"{generation} corpus looks too small to compare"


def test_no_element_is_covered_only_by_the_3_0_fixtures() -> None:
    """The direction that hid #345.

    A 3.1 reader can only be *tested* on what its fixtures contain, so an
    element the 3.0 corpus exercises and the 3.1 corpus does not is an
    untested code path — and, as it turned out, an absent one.
    """
    missing = sorted(set(_vocabulary("sdmx_ml30")) - set(_vocabulary("sdmx_ml31")) - set(_ONLY_IN_30))
    assert not missing, (
        f"the 3.0 fixtures exercise these and the 3.1 fixtures do not: {missing}. "
        "Add them to the 3.1 corpus, or to _ONLY_IN_30 with the reason 3.1 cannot carry them."
    )


def test_no_element_is_covered_only_by_the_3_1_fixtures() -> None:
    """The reverse, which would mean the 3.0 corpus had drifted instead."""
    missing = sorted(set(_vocabulary("sdmx_ml31")) - set(_vocabulary("sdmx_ml30")) - set(_ONLY_IN_31))
    assert not missing, f"the 3.1 fixtures exercise these and the 3.0 fixtures do not: {missing}."


@pytest.mark.parametrize("exempt", sorted(_ONLY_IN_30) + sorted(_ONLY_IN_31))
def test_every_exemption_is_still_needed(exempt: str) -> None:
    """An exemption for an element now covered on both sides is a licence
    nobody is using, and the next real gap would hide behind it."""
    both = set(_vocabulary("sdmx_ml30")) & set(_vocabulary("sdmx_ml31"))
    assert exempt not in both, f"{exempt!r} is exercised by both corpora now — drop the exemption"
