"""The Python calls one `InternationalString.get` makes (GH-309, GH-432).

`get` builds the `LocalisedString` it returns, so it does more than the
`dict.get` it replaced. Its cost is guarded by counting the Python-level calls
it makes rather than by timing it: a call count is the same on every machine
and interpreter release, where a timed ratio against the old lookup moved with
both (GH-432 measured it at its 1.5x budget on CPython 3.14.10).

The calls are recorded with `sys.setprofile`, whose ``call`` event fires for
each Python frame and for no C function.
"""

import sys
from collections.abc import Callable
from types import FrameType
from typing import cast

import pytest

from sdmxlib.model import istring
from sdmxlib.model.istring import InternationalString

pytestmark = pytest.mark.unit

_HELD = InternationalString._from_normalized({"en": "Annual", "fr": "Annuel"})


def _calls[**P](fn: Callable[P, object], *args: P.args, **kwargs: P.kwargs) -> list[str]:
    """The qualified name of each Python function `fn` runs, itself first."""
    names: list[str] = []

    def record(frame: FrameType, event: str, _arg: object) -> None:
        if event == "call":
            names.append(frame.f_code.co_qualname)

    sys.setprofile(record)
    try:
        fn(*args, **kwargs)
    finally:
        sys.setprofile(None)
    return names


@pytest.fixture
def seen_fr() -> None:
    """`fr` checked once, as every lookup after a process's first finds it."""
    istring._SEEN_TAGS.clear()
    istring.normalize_tag("fr")


@pytest.mark.usefixtures("seen_fr")
def test_a_held_tag_already_seen_costs_two_calls() -> None:
    assert _calls(_HELD.get, "fr") == ["InternationalString.get", "LocalisedString.__post_init__"]


@pytest.mark.usefixtures("seen_fr")
def test_a_missing_tag_already_seen_builds_nothing() -> None:
    assert _calls(InternationalString.EMPTY.get, "fr") == ["InternationalString.get"]


def test_a_tag_not_yet_seen_is_checked_once() -> None:
    """Discrimination: the recorder sees nested calls, so the fast path is what the counts above measure."""
    istring._SEEN_TAGS.clear()
    assert _calls(_HELD.get, "fr") == [
        "InternationalString.get",
        "normalize_tag",
        "_well_formed",
        "LocalisedString.__post_init__",
    ]
    assert _calls(_HELD.get, "fr") == ["InternationalString.get", "LocalisedString.__post_init__"]


@pytest.mark.usefixtures("seen_fr")
def test_every_lookup_skips_normalize_tag_for_a_seen_tag() -> None:
    lookups: list[tuple[str, Callable[[], object]]] = [
        ("InternationalString.__getitem__", lambda: _HELD["fr"]),
        ("InternationalString.pick", lambda: _HELD.pick("de", "fr")),
        ("InternationalString.__contains__", lambda: "fr" in _HELD),
    ]
    istring.normalize_tag("de")
    for name, lookup in lookups:
        called = _calls(lookup)
        assert name in called
        assert "normalize_tag" not in called, name


def test_a_tag_that_is_not_a_str_still_names_its_type() -> None:
    untyped = cast("Callable[[object], object]", _HELD.get)
    with pytest.raises(TypeError, match="a language tag is a str, not list"):
        untyped(["fr"])


def test_a_tag_in_another_case_is_checked_every_time() -> None:
    """Only a tag in its normalised form skips the check; ``FR`` is never in the set."""
    istring._SEEN_TAGS.clear()
    for _ in range(2):
        assert "_well_formed" in _calls(_HELD.get, "FR")
    assert "FR" not in istring._SEEN_TAGS
