"""When a data query is refused before it is sent, and when the server refuses it.

Two different refusals, and keeping them apart is the point of this file.

**Ours** (`PushdownError`) fires before any request: a plan that pushes
*nothing* would fetch a whole dataflow and filter it locally. **Theirs**
(`ResponseTooLargeError`, SDMX-REST 413/414) fires after: the service will not
serve a request that large.

The standard's opinion is the second one — `/data/{flow}/all` is a legal
request, and whether it is served is the provider's call, with defined codes to
say no. So the client-side rule is deliberately the narrowest thing that still
catches the catastrophe, rather than a guess at limits only the provider knows.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import httpx
import pytest

from sdmxlib.api.client import ResponseTooLargeError, _raise_for_status
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.ref import Ref
from sdmxlib.query._expr import Pred
from sdmxlib.query._plan import Plan
from sdmxlib.query._pushdown import (
    DATA_ROOT,
    SDMX_REST,
    SDMX_REST_DATA_21,
    Capability,
    PredicateShape,
    Push,
    is_bounded,
)
from sdmxlib.query.observations import Observations, ObsRow

pytestmark = pytest.mark.unit


def _root() -> Observations:
    """A real observation root; `is_bounded` reads only its `__resource__`."""
    binding = DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(Dataflow.urn_for(agency="AG", id="DF", version="1.0")),
        database_role="memory",
        table_location="data.obs",
        structural_version="AG:DSD(1.0)",
        source_registry="local",
        materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
        bound_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return Observations(binding)


def _bounded(capability: Capability, predicates: Sequence[Pred[ObsRow]]) -> bool:
    return is_bounded(Push(Plan(root=_root(), predicates=tuple(predicates))), capability)


class _Pred(Pred[ObsRow]):
    """A predicate stand-in whose shapes are stated rather than compiled."""

    __slots__ = ("_shapes",)

    def __init__(self, *shapes: PredicateShape) -> None:
        self._shapes = frozenset(shapes)


@pytest.fixture(autouse=True)
def _shapes_of(monkeypatch):
    """`shapes_of` over the stand-in, so this file tests the *rule* rather than
    the compiler that feeds it."""
    import sdmxlib.query._pushdown as pushdown

    real = pushdown.shapes_of
    monkeypatch.setattr(pushdown, "shapes_of", lambda p: p._shapes if isinstance(p, _Pred) else real(p))


_KEY = PredicateShape(kind="Compare", column="@key_dimension", op="=")


# ── the three states of `bounding` ────────────────────────────────────────


def test_a_declared_empty_bound_admits_any_pushed_constraint() -> None:
    """The `/data` case, and the state that did not exist before.

    `bounding.get(root, frozenset())` collapsed "not declared" into "requires
    nothing", so a backend could say the first and never the second.
    """
    assert _bounded(SDMX_REST_DATA_21, [_Pred(_KEY)])


def test_a_declared_empty_bound_refuses_a_plan_that_pushed_nothing() -> None:
    """The catastrophe the client-side rule exists for: no constraint reaches
    the URL, so the whole flow is fetched to be filtered here."""
    assert not _bounded(SDMX_REST_DATA_21, [])


def test_an_undeclared_root_is_bounded_by_nothing() -> None:
    """Unchanged, and the more dangerous default of the two — an undeclared
    backend must not inherit unlimited residuals from a vacuous `all()`."""
    undeclared = Capability(name="silent", pushes={(): frozenset({_KEY})})
    assert not _bounded(undeclared, [_Pred(_KEY)])


def test_a_non_empty_bound_still_requires_all_of_it() -> None:
    """A backend needing both an agency and an id states both, and neither
    alone opens the door."""
    agency = PredicateShape(kind="Compare", column="agency_id", op="=")
    resource = PredicateShape(kind="Compare", column="resource_id", op="=")
    strict = Capability(
        name="strict",
        pushes={(): frozenset({agency, resource})},
        scans=frozenset({DATA_ROOT}),
        bounding={DATA_ROOT: frozenset({agency, resource})},
    )
    assert not _bounded(strict, [_Pred(agency)])
    assert _bounded(strict, [_Pred(agency, resource)])


def test_the_structural_capability_is_unchanged() -> None:
    """Its bound is a named shape, so the new middle state must not reach it."""
    assert SDMX_REST.bounding, "still declares one"
    assert all(shapes for shapes in SDMX_REST.bounding.values()), "and none is the empty bound"


# ── the server's refusal ──────────────────────────────────────────────────


@pytest.mark.parametrize(("status", "phrase"), [(413, "larger than"), (414, "longer than")])
def test_a_size_refusal_is_named_and_says_what_to_do(status: int, phrase: str) -> None:
    """The standard's own answer to "is this too big", which we used to discard.

    `raise_for_status()` gave a bare `HTTPStatusError` — no mention that the
    fix is narrowing the query, and no way for a caller to catch *this* rather
    than every HTTP failure.
    """
    response = httpx.Response(status, request=httpx.Request("GET", "https://x/data/all"))
    with pytest.raises(ResponseTooLargeError) as caught:
        _raise_for_status(response, url="https://x/data/all")
    assert phrase in str(caught.value)
    assert "Narrow the query" in str(caught.value)
    assert "https://x/data/all" in str(caught.value), "which request, since a plan issues several"


def test_other_failures_keep_httpx_s_own_error() -> None:
    """There is nothing this layer can add to a 500 that the message does not
    already say, and swallowing it into a library type would lose the status."""
    response = httpx.Response(500, request=httpx.Request("GET", "https://x/"))
    with pytest.raises(httpx.HTTPStatusError):
        _raise_for_status(response, url="https://x/")


def test_success_passes_through() -> None:
    response = httpx.Response(200, request=httpx.Request("GET", "https://x/"))
    _raise_for_status(response, url="https://x/")
