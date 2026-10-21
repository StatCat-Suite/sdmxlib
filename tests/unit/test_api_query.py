"""Unit tests for Query[T] lazy collection search."""

import annotationlib
import importlib.util
from dataclasses import dataclass

import pytest

pytestmark = pytest.mark.unit


@dataclass
class _Item:
    id: str
    value: int = 0


def _make_items(ids: list[str]) -> list[_Item]:
    return [_Item(id=i, value=v) for v, i in enumerate(ids)]


def test_the_retired_collection_query_is_gone() -> None:
    """`reg.query(T)` and its `Query[T]`, retired in #312.

    `query-language.md` argued this before it was executed: the class's only
    distinguishing feature was `filter(lambda)` evaluated in Python over
    everything fetched, and strict laziness bans exactly that — so once it is
    lazy and compiled it *is* the path language under another name. The
    project breaks cleanly pre-stability rather than carrying both.

    An earlier version of this test asserted the class had lost `filter` /
    `sort_by` / `limit`. That guarantee is now subsumed: there is no class.
    The module itself went with `reg.data()` and `DataQuery` (#516), which
    makes the absence total.
    """
    assert importlib.util.find_spec("sdmxlib.api.query") is None


def test_the_rest_registry_query_takes_an_accessor_not_a_type() -> None:
    """The replacement, checked at the signature rather than by running it.

    `reg.query(sl.Dataflow)` used to mean "every dataflow, fetched, then
    filtered in Python". It now means nothing — the parameter is a relation
    accessor, and a domain class is a type error rather than a slower answer.
    """
    import inspect

    from sdmxlib.api.registry import RestRegistry

    parameters = list(
        inspect.signature(RestRegistry.query, annotation_format=annotationlib.Format.FORWARDREF).parameters
    )
    assert parameters == ["self", "root"], parameters


# ── resolution depth, now a parameter of get() ────────────────────────────
#
# These pinned `GetQuery.fetch()` / `.resolve()` / `.resolve(type)`. The
# handle is gone and the axis is not: what each spelling means is asserted
# against the *wire* — which `detail` and `references` reach the request —
# in `test_get_resolution_depth.py`, because that is the thing a caller is
# actually choosing between.


# ── count ─────────────────────────────────────────────────────────────────────
