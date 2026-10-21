"""Storing an instant in a column that has no timezone.

DuckDB's `TIMESTAMP` carries no zone, so handing it a tz-aware value converts
to the *session's* local time and returns something naive. On a UTC-5 machine
`2021-02-03T04:05:06Z` came back as `2021-02-02 23:05:06` — a different
instant, and a different one per reader's machine, which is worse than a
constant offset because it is not reproducible (#321).

**The convention is UTC-naive**, and it is not new: `dataflow_binding` has
stored `materialized_at` / `bound_at` this way since it was written, for the
reason recorded in its DDL — DuckDB's `TIMESTAMPTZ` Python path wants `pytz`,
and adding a dependency to store a timestamp is a poor trade. This module is
that decision extracted so every column shares one implementation rather than
a fifth copy of two lines.

**A naive input is read as UTC, not as local time.** Local would reintroduce
exactly the machine-dependence being removed: the same value written on two
machines would mean two instants. UTC is the only reading that makes a naive
value mean the same thing everywhere, and it is what the wire formats mean by
an unqualified `xs:dateTime` in practice.

So the round-trip is:

| in | out | |
| --- | --- | --- |
| `04:05:06+00:00` | `04:05:06+00:00` | identity |
| `04:05:06+05:00` | `23:05:06+00:00` (prev day) | same instant, normalised |
| `04:05:06` naive | `04:05:06+00:00` | same wall time, now labelled |

Only the first is an identity. The second and third preserve the instant,
which is the property that matters and the one `TIMESTAMP` was silently
breaking.
"""

from datetime import UTC, datetime
from typing import overload


# Overloaded so a value known to be present stays `datetime` on the way out —
# a query literal (#415) is never None, and widening it to `datetime | None`
# would push a pointless check onto every caller.
@overload
def to_storage(value: datetime) -> datetime: ...
@overload
def to_storage(value: None) -> None: ...
def to_storage(value: datetime | None) -> datetime | None:
    """A datetime as this schema stores it: UTC, with `tzinfo` stripped.

    Args:
        value: Any datetime, aware or naive. `None` passes through.

    Returns:
        The same instant expressed in UTC, without a zone — which is what a
        `TIMESTAMP` column can hold without reinterpreting it.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        # Already the convention. Converting would apply the local offset and
        # move the instant, which is the bug this module exists to remove.
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


@overload
def from_storage(value: datetime) -> datetime: ...
@overload
def from_storage(value: None) -> None: ...
def from_storage(value: datetime | None) -> datetime | None:
    """A stored datetime as the model sees it: the same instant, tagged UTC.

    Args:
        value: What the column returned. `None` passes through.

    Returns:
        A tz-aware datetime in UTC. Already-aware input is returned unchanged
        rather than re-tagged, so a driver that starts returning aware values
        does not silently double-apply an offset.
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


@overload
def as_utc(value: datetime) -> datetime: ...
@overload
def as_utc(value: None) -> None: ...
def as_utc(value: datetime | None) -> datetime | None:
    """A datetime as the *model* holds it: the instant, tagged UTC (#440).

    The attrs converter on every model `datetime`. The store already returned
    this form (`from_storage`), but the model held whatever a reader handed
    it — a naive value for an unqualified `xs:dateTime` — so an artefact read
    from a document was not equal to the same artefact read back from the
    store. Normalising at the model boundary makes the two the same object,
    by the reading this module already chose: a naive value is UTC.

    Args:
        value: Any datetime, aware or naive. `None` passes through.

    Returns:
        The same instant, tz-aware in UTC: a naive value is labelled, an aware
        one converted.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
