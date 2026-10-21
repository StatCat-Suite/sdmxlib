"""An instant survives a column that has no timezone (#321).

DuckDB's `TIMESTAMP` carries no zone, so handing it a tz-aware value converted
to the *session's* local time and returned something naive: on a UTC-5 machine
`2021-02-03T04:05:06Z` came back as `2021-02-02 23:05:06`. Not merely a lost
`tzinfo` — a different instant, and a different one per reader's machine, which
is worse than a constant offset because it is not reproducible.

The convention is UTC-naive in the column, re-tagged on read, and it is not
new: `dataflow_binding` has stored `materialized_at` this way since it was
written, for the reason recorded in its DDL — DuckDB's `TIMESTAMPTZ` Python
path wants `pytz`, and adding a dependency to store a timestamp is a poor
trade. The fix extracted that decision so every column shares one
implementation rather than a fifth copy of two lines.

The derived guard (#316) covers the UTC-aware case only, because its sentinel
is UTC. The offset and naive cases are here, and they are the ones where
"preserves the instant" and "returns what you passed" differ.
"""

from __future__ import annotations

import datetime as dt

import pytest

import sdmxlib as sl
from sdmxlib._time import from_storage, to_storage
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.hierarchy import Hierarchy
from sdmxlib.model.organisation import Agency

pytestmark = pytest.mark.unit

AG = Agency(id="AG")
PLUS_FIVE = dt.timezone(dt.timedelta(hours=5))


def _roundtrip(value: dt.datetime) -> dt.datetime | None:
    artefact = Codelist(id="X", maintainer=AG, name={"en": "x"}, valid_from=value)
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(artefact)
        stored = registry.get(artefact.urn)
        assert stored is not None
        return stored.valid_from


def test_a_utc_value_round_trips_unchanged() -> None:
    value = dt.datetime(2021, 2, 3, 4, 5, 6, tzinfo=dt.UTC)
    assert _roundtrip(value) == value


def test_an_offset_value_keeps_its_instant() -> None:
    """Normalised to UTC rather than returned verbatim — the same moment,
    written the one way the column can hold."""
    value = dt.datetime(2021, 2, 3, 4, 5, 6, tzinfo=PLUS_FIVE)
    got = _roundtrip(value)
    assert got == value, "the instant must survive"
    assert got is not None
    assert got.utcoffset() == dt.timedelta(0), "and it comes back expressed in UTC"


def test_a_naive_value_is_read_as_utc_not_as_local_time() -> None:
    """The decision that removes the machine-dependence.

    Reading a naive value as *local* would mean the same stored value denotes
    two instants on two machines, which is the bug this fix exists to remove —
    so naive means UTC, everywhere.
    """
    value = dt.datetime(2021, 2, 3, 4, 5, 6)
    got = _roundtrip(value)
    assert got == value.replace(tzinfo=dt.UTC)


def test_the_stored_form_carries_no_zone() -> None:
    """What the column actually holds, asserted rather than assumed.

    A `TIMESTAMP` that received an aware value is where the shifting happened,
    so the property worth pinning is that one never reaches it.
    """
    value = dt.datetime(2021, 2, 3, 4, 5, 6, tzinfo=PLUS_FIVE)
    artefact = Codelist(id="X", maintainer=AG, name={"en": "x"}, valid_from=value)
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(artefact)
        row = registry._c.execute("SELECT valid_from FROM sdmx.artefact").fetchone()
    assert row is not None
    assert row[0].tzinfo is None
    assert row[0] == dt.datetime(2021, 2, 2, 23, 5, 6), "normalised to UTC before storing"


def test_a_hierarchy_node_gets_the_same_treatment() -> None:
    """The second table with these columns. One convention, not one per site."""
    value = dt.datetime(2021, 2, 3, 4, 5, 6, tzinfo=PLUS_FIVE)
    hierarchy = Hierarchy(id="H", maintainer=AG, name={"en": "h"}, valid_from=value)
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(hierarchy)
        stored = registry.get(hierarchy.urn)
        assert stored is not None
        assert stored.valid_from == value


# ── the helpers, directly ─────────────────────────────────────────────────


def test_none_passes_through_both_ways() -> None:
    assert to_storage(None) is None
    assert from_storage(None) is None


def test_a_naive_value_is_not_converted_on_write() -> None:
    """`astimezone` on a naive datetime applies the *local* offset, which would
    move the instant — the exact bug, reintroduced by the fix."""
    naive = dt.datetime(2021, 2, 3, 4, 5, 6)
    assert to_storage(naive) == naive


def test_an_already_aware_value_is_not_re_tagged_on_read() -> None:
    """Defensive: if the driver ever starts returning aware values, re-tagging
    would double-apply an offset and move the instant again."""
    aware = dt.datetime(2021, 2, 3, 4, 5, 6, tzinfo=PLUS_FIVE)
    assert from_storage(aware) is aware


def test_the_pair_is_instant_preserving_for_any_input() -> None:
    """The property that matters, over the three shapes a caller can pass."""
    for value in (
        dt.datetime(2021, 2, 3, 4, 5, 6, tzinfo=dt.UTC),
        dt.datetime(2021, 2, 3, 4, 5, 6, tzinfo=PLUS_FIVE),
        dt.datetime(2021, 2, 3, 4, 5, 6),
    ):
        got = from_storage(to_storage(value))
        expected = value if value.tzinfo else value.replace(tzinfo=dt.UTC)
        assert got == expected, value
