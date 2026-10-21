"""What a write costs as a message grows, guarded by counts rather than clocks (%22).

A cost that is linear in the artefact count has a constant step: writing
30 codelists costs as much more than 20 as 20 costs more than 10. A
quadratic one does not. So the guards here count calls at three sizes and
compare the steps exactly, which no machine's speed can make pass or fail.
"""

from collections.abc import Callable

import pytest

from sdmxlib import formats
from sdmxlib.model import codelist as codelist_module
from sdmxlib.model import validation
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.link import Link
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.urn import SdmxUrn

pytestmark = pytest.mark.unit


def _message(codelists: int) -> StructureMessage:
    return StructureMessage(
        codelists=[
            Codelist(
                id=f"CL{i}", maintainer="AG", version="1.0", name={"en": "C"}, codes=[Code(id="A", name={"en": "A"})]
            )
            for i in range(codelists)
        ]
    )


def _steps(monkeypatch: pytest.MonkeyPatch, target: object, name: str, *, validate: bool) -> tuple[int, int]:
    """How many more calls of `target.name` each ten more codelists cost, twice over."""
    original: Callable[..., object] = getattr(target, name)
    calls = 0

    def counting(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    counts: list[int] = []
    with monkeypatch.context() as patch:
        patch.setattr(target, name, counting)
        for size in (10, 20, 30):
            calls = 0
            formats.serialize(_message(size), version="json", validate=validate)
            counts.append(calls)
    return counts[1] - counts[0], counts[2] - counts[1]


@pytest.mark.parametrize("validate", [False, True])
def test_a_json_write_looks_up_codelists_linearly(monkeypatch: pytest.MonkeyPatch, validate: bool) -> None:
    """Indexing the message's codelists by URN happens per wildcard, not per codelist (#462).

    `wildcard_misfits` built the index of every codelist for every codelist,
    so writing BIS's 105 codelists built 11,000 URNs before writing one.
    Counted as URNs formatted, which every index entry costs.
    """
    first, second = _steps(monkeypatch, SdmxUrn, "__str__", validate=validate)
    assert first == second, f"URN lookups grow faster than the codelist count: +{first} then +{second}"


def test_the_linearity_guard_can_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """The discrimination check: an index built per codelist is seen as a growing step."""
    original = codelist_module.wildcard_misfits

    def indexing_every_time(codelist: Codelist, available: list[Codelist]) -> list[str]:
        _ = {str(cl.urn): cl for cl in available}
        return original(codelist, available)

    monkeypatch.setattr("sdmxlib.formats.sdmx_json.writer.wildcard_misfits", indexing_every_time)
    first, second = _steps(monkeypatch, SdmxUrn, "__str__", validate=False)
    assert second > first


def _linked_message(codes: int, *, linked: bool) -> StructureMessage:
    """One codelist of `codes` codes, each with one link when `linked`."""
    links = (Link(rel="describedby", url="https://example.org/code"),) if linked else ()
    return StructureMessage(
        codelists=[
            Codelist(
                id="CL",
                maintainer="AG",
                version="1.0",
                name={"en": "C"},
                codes=[
                    Code(id=f"C{i}", name={"en": "A"}, uri="https://example.org/uri", links=links) for i in range(codes)
                ],
            )
        ]
    )


def _paths_built(monkeypatch: pytest.MonkeyPatch, message: StructureMessage) -> int:
    """How many item paths the SDMX-JSON link check builds while `message` is written."""
    original = validation._at
    calls = 0

    def counting(path: str, item: object, index: int) -> str:
        nonlocal calls
        calls += 1
        return original(path, item, index)

    with monkeypatch.context() as patch:
        patch.setattr(validation, "_at", counting)
        formats.serialize(message, version="json")
    return calls


def test_the_json_link_check_passes_items_without_links(monkeypatch: pytest.MonkeyPatch) -> None:
    """A code with no link gets no path built, so the check costs nothing per such code (#462).

    Descending into each one cost 16 ms of the 87 ms UNSD CL_COMMODITY write.
    """
    one = _paths_built(monkeypatch, _linked_message(1, linked=False))
    many = _paths_built(monkeypatch, _linked_message(200, linked=False))
    assert many == one, f"paths built grow with link-less codes: {one} for 1, {many} for 200"


def test_the_json_link_check_still_visits_items_with_links(monkeypatch: pytest.MonkeyPatch) -> None:
    """The discrimination check: a code that has a link is still walked."""
    one = _paths_built(monkeypatch, _linked_message(1, linked=True))
    many = _paths_built(monkeypatch, _linked_message(200, linked=True))
    assert many == one + 199
