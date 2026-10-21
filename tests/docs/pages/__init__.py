"""Per-page setups for the doc-block runner.

One module per documentation page that needs names bound before its blocks
run. `SETUPS` maps the page's path to a callable returning the namespace those
blocks execute in.

A page with no entry runs against `BASE` alone — `sl`, `q`, `pl` — which is
enough for a page whose examples build everything they use.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

import polars as pl

import sdmxlib as sl


#: The names every page may assume, matching how the prose refers to them.
def base() -> dict[str, Any]:
    return {"sl": sl, "pl": pl, "q": sl.q}


#: page path -> a context manager yielding its namespace. A context manager
#: rather than a dict because most setups own a registry that must be closed.
SETUPS: dict[str, Callable[[], Any]] = {}

#: Pages whose fenced blocks are executed from the page itself. Adding one
#: means writing its setup here and fixing whatever the run turns up — which
#: is the point, and is how the stale examples in this suite were found.
EXECUTED: set[str] = set()


def setup_for(page: str) -> Callable[[], Any]:
    """The setup for `page`, or a base-only one."""

    @contextmanager
    def _base() -> Generator[dict[str, Any]]:
        yield base()

    return SETUPS.get(page, _base)


from tests.docs.pages import _registrations  # populates SETUPS
