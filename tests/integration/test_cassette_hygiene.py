"""Every cassette is either replayed by a test or declared as evidence.

Four cassettes had outlived the tests that recorded them. Nothing noticed,
because a cassette nobody replays costs nothing at runtime. It costs something
at *reading* time: two of them held the only recorded ``detail=allstubs``
requests in the suite, which read as evidence that the parameter was reachable
while design §5.14 said it was not. It had been, under an API since removed.

**The first version of this test was wrong, and usefully so.** It flagged
anything with no same-named test, and deleting what it flagged broke
`test_query_closure.py`: one of those files is not a replay at all but a
*fixture* — a recorded ECB response that
`test_the_depth_rule_matches_what_the_wire_returns` reads to pin the
`references=` rule against what a real provider actually returns, rather than
against a reading of the spec. It is found by URL pattern, not by filename, so
no grep for its name would have shown that.

That dependency was invisible: the only reason it surfaced was a test failing.
So the allowlist below is the point of this file rather than an escape from it
— it makes "this recording is load-bearing for something else" a declared fact
instead of one discoverable by deleting the file and seeing what breaks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).parent
_TEST_NAME = re.compile(r"^def (test_\w+)", re.MULTILINE)

#: Cassettes kept as *evidence* rather than replayed, and what reads each.
#: A recorded provider response is the strongest thing a rule can be pinned
#: to, so these are deliberate — but they must be named here, because nothing
#: else connects the file to its reader.
_EVIDENCE = {
    "test_query_resolve_follows_refs.yaml": (
        "tests/unit/test_query_closure.py::test_the_depth_rule_matches_what_the_wire_returns "
        "— reads it by URL pattern to pin `references=` against a real ECB response"
    ),
}


def _declared_tests() -> set[str]:
    names: set[str] = set()
    for module in _ROOT.glob("test_*.py"):
        names.update(_TEST_NAME.findall(module.read_text(encoding="utf-8")))
    return names


def test_no_cassette_outlives_its_test() -> None:
    """A cassette is named for the test that recorded it, so replay coverage is
    a name lookup — pytest-recording's own convention is what makes it one.
    Parametrised tests record as ``test_name[param]``; the stem before the
    bracket is what must exist.
    """
    declared = _declared_tests()
    orphans = sorted(
        f"{path.parent.name}/{path.name}"
        for path in _ROOT.glob("cassettes/*/*.yaml")
        if path.stem.split("[")[0] not in declared and path.name not in _EVIDENCE
    )
    assert not orphans, (
        f"{len(orphans)} cassette(s) neither replayed nor declared: {orphans}. "
        "Delete them, restore the test that recorded them, or add them to "
        "`_EVIDENCE` naming what reads them — a recording nobody replays is a "
        "claim about a provider that nobody checks."
    )


def test_every_declared_evidence_cassette_exists() -> None:
    """The allowlist decays in the other direction too: a name left here after
    its file is gone silently excuses nothing, and reads as a dependency that
    still exists."""
    missing = sorted(name for name in _EVIDENCE if not list(_ROOT.glob(f"cassettes/*/{name}")))
    assert not missing, f"_EVIDENCE names cassettes that are gone: {missing}"
