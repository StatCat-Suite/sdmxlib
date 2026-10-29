"""What `resolve=` means on the wire (docs/design/evidence/registry-landing.md §6 step 4).

`fetch()`, `resolve()`, `resolve(type)` and the `GetQuery` that carried them
were four ways to say one thing: how far to follow references. They are now one
parameter, and what each setting *means* is a pair of SDMX-REST query
parameters — so the tests assert the request rather than the return value,
which is what a caller is choosing between.

`detail` and `references` are checked together because they move together:
asking for references and asking for them trimmed are different questions, and
a test that pinned only one would pass while the other drifted.
"""

from __future__ import annotations

import pytest

import sdmxlib as sl
from sdmxlib.model.message import StructureMessage

pytestmark = pytest.mark.unit


@pytest.fixture
def reg(monkeypatch: pytest.MonkeyPatch):
    """A registry that records what it would have asked for, and answers empty."""
    calls: list[dict[str, str]] = []
    with sl.RestRegistry("https://example.test/s", id="X") as registry:

        def _get_artefact(resource, agency, id_, version, *, detail, references, conditional=True):
            calls.append({"resource": resource, "detail": detail, "references": references})
            return StructureMessage()

        # `get_artefact_if_changed` is the seam the `get` path uses: it may
        # answer NOT_MODIFIED, which the unconditional `get_artefact` cannot
        # (#339). Stubbing the other one leaves the real client reaching for
        # the network.
        monkeypatch.setattr(registry._client, "get_artefact_if_changed", _get_artefact)
        yield registry, calls


_URN = sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1", version="1.0")


def test_the_default_is_a_skeleton(reg) -> None:
    """What `fetch()` meant. Remote resolution is another round trip, so the
    default here is the cheap one — the opposite of `Registry.get`, which
    resolves because a join is not a round trip."""
    registry, calls = reg
    registry.get(_URN)
    assert calls[-1] == {"resource": "DataStructure", "detail": "full", "references": "none"}


def test_resolve_true_follows_every_reference(reg) -> None:
    """What `resolve()` meant."""
    registry, calls = reg
    registry.get(_URN, resolve=True)
    assert calls[-1]["references"] == "descendants"
    assert calls[-1]["detail"] == "referencepartial", "trimmed by constraints unless asked otherwise"


def test_partial_false_asks_for_the_untrimmed_scheme(reg) -> None:
    """What `resolve(partial=False)` meant — the full-audit case."""
    registry, calls = reg
    registry.get(_URN, resolve=True, partial=False)
    assert calls[-1] == {"resource": "DataStructure", "detail": "full", "references": "descendants"}


def test_a_type_list_follows_only_those(reg) -> None:
    """What `resolve(sl.Codelist)` meant, now plural because a list can be."""
    registry, calls = reg
    registry.get(_URN, resolve=[sl.Codelist])
    assert calls[-1]["references"] == "codelist"


def test_several_types_are_sorted_for_a_stable_url(reg) -> None:
    """The same question must not produce two URLs — and so two cache
    entries — depending on the order a caller happened to write."""
    registry, calls = reg
    registry.get(_URN, resolve=[sl.ConceptScheme, sl.Codelist])
    assert calls[-1]["references"] == "codelist,conceptscheme"


def test_partial_is_ignored_without_resolution(reg) -> None:
    """There is nothing to trim when no reference is followed, so `detail`
    stays `full` rather than becoming a meaningless `referencepartial`."""
    registry, calls = reg
    registry.get(_URN, partial=False)
    assert calls[-1]["detail"] == "full"
    assert calls[-1]["references"] == "none"


def test_a_missing_artefact_is_none_not_an_exception(reg) -> None:
    """`get` reports absence; it does not raise it. The stub answers with an
    empty message, which is what a registry that has never heard of an
    artefact returns."""
    registry, _calls = reg
    assert registry.get(_URN) is None


def test_a_ref_is_accepted_as_well_as_a_urn(reg) -> None:
    """What `resolve(target)` accepted, kept: graph traversal hands back
    `Ref`s, and making a caller unwrap one would be ceremony."""
    registry, calls = reg
    registry.get(sl.Ref[sl.DataStructure].unresolved(_URN), resolve=True)
    assert calls[-1]["references"] == "descendants"
