"""A time column compares instants, whatever zone the literal or the session is in (#415).

The store keeps every `TIMESTAMP` UTC-naive (#321, `sdmxlib/_time.py`): DuckDB's
`TIMESTAMP` has no zone, and handing it a tz-aware value converts it to the
*session's* local time first. Writers and readers honoured that; query
literals did not. `q.codelist.valid_from.is_in([aware])` matched nothing, and
`< aware` compared against an instant shifted by the session's offset, so the
same query gave different answers on different machines. `==` refused a
`datetime` outright, because `Temporal` inherited `Num`'s integer check.

**The session zone is the point of this file.** On a UTC session every
assertion here passes with or without the fix: an aware UTC value and its naive
twin are the same wall time. That is how the bug survived — CI and most
containers run in UTC. So the store's session is pinned to `Pacific/Kiritimati`
(UTC+14), far enough that a shifted literal lands on the wrong side of every
boundary below, and `test_the_session_really_is_far_from_utc` asserts the
premise rather than trusting it.

Each operator is checked against the answer computed in Python over the stored
instants, never against a hand-written expected list — the literal is read the
way the store reads it (naive means UTC) and compared as an instant.
"""

from __future__ import annotations

import datetime as dt
import operator
from typing import TYPE_CHECKING

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.query.accessors import CodelistRow, q

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sdmxlib.query._expr import Pred

pytestmark = pytest.mark.unit

FAR_ZONE = "Pacific/Kiritimati"

UTC = dt.UTC
PLUS_FIVE = dt.timezone(dt.timedelta(hours=5))

#: Stored instants, chosen so a 14-hour shift moves a literal across each one.
STORED: dict[str, dt.datetime] = {
    "MIDNIGHT": dt.datetime(2021, 1, 1, tzinfo=UTC),
    "NOON": dt.datetime(2021, 1, 1, 12, tzinfo=UTC),
    "JUNE": dt.datetime(2021, 6, 1, tzinfo=UTC),
}

#: One instant — the NOON row — three spellings, all of which must select the
#: same rows. NOON rather than an end, so every operator selects something and
#: none selects everything: shifted 14 h either way, `<` and `>` each gain or
#: lose a row, and `==` loses its only one.
SPELLINGS: dict[str, dt.datetime] = {
    "aware-utc": dt.datetime(2021, 1, 1, 12, tzinfo=UTC),
    "aware-plus-five": dt.datetime(2021, 1, 1, 17, tzinfo=PLUS_FIVE),
    "naive": dt.datetime(2021, 1, 1, 12),
}

#: The `operator` functions rather than the operators themselves, so one table
#: drives both the query and the Python answer it is checked against.
OPERATORS = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


def _as_instant(value: dt.datetime) -> dt.datetime:
    """How the store reads a literal: naive means UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@pytest.fixture(scope="module")
def reg() -> Iterator[sl.LocalRegistry]:
    conn = duckdb.connect()
    # GLOBAL, so every handle the registry opens off this connection inherits it.
    conn.execute(f"SET GLOBAL TimeZone = '{FAR_ZONE}'")
    registry = sl.LocalRegistry.from_connection(conn)
    registry.add_all(
        [
            sl.Codelist(id=cid, maintainer="AG", version="1.0", name={"en": cid}, valid_from=when)
            for cid, when in STORED.items()
        ]
    )
    yield registry
    conn.close()


def _ids(reg: sl.LocalRegistry, pred: Pred[CodelistRow]) -> set[str]:
    return set(reg.query(q.codelist).filter(pred).ids())


def test_the_session_really_is_far_from_utc(reg: sl.LocalRegistry) -> None:
    """The premise. On a UTC session nothing below can fail."""
    with reg.session() as active:
        zone = active.execute("SELECT current_setting('TimeZone')").fetchone()
    assert zone == (FAR_ZONE,)


@pytest.mark.parametrize("spelling", SPELLINGS)
@pytest.mark.parametrize("op", OPERATORS)
def test_every_operator_compares_instants(reg: sl.LocalRegistry, op: str, spelling: str) -> None:
    literal = SPELLINGS[spelling]
    want = {cid for cid, when in STORED.items() if OPERATORS[op](when, _as_instant(literal))}
    assert want, "every case selects something, or it cannot tell a shift from a miss"
    assert want != set(STORED), "and leaves something out, for the same reason"
    assert _ids(reg, OPERATORS[op](q.codelist.valid_from, literal)) == want


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_is_in_compares_instants(reg: sl.LocalRegistry, spelling: str) -> None:
    literal = SPELLINGS[spelling]
    assert _ids(reg, q.codelist.valid_from.is_in([literal, STORED["JUNE"]])) == {"NOON", "JUNE"}


def test_is_in_negated_compares_instants(reg: sl.LocalRegistry) -> None:
    assert _ids(reg, ~q.codelist.valid_from.is_in([SPELLINGS["aware-plus-five"]])) == {"MIDNIGHT", "JUNE"}


def test_a_date_is_midnight_utc(reg: sl.LocalRegistry) -> None:
    """A `date` is read as the start of that day in UTC, like a naive datetime."""
    assert _ids(reg, q.codelist.valid_from == dt.date(2021, 1, 1)) == {"MIDNIGHT"}
    assert _ids(reg, q.codelist.valid_from < dt.date(2021, 6, 1)) == {"MIDNIGHT", "NOON"}


@pytest.mark.parametrize("bad", [2021, 2021.0, "2021-01-01", True])
def test_a_non_temporal_literal_is_refused_naming_the_type(bad: float | str) -> None:
    for build in (operator.eq, operator.ne, operator.lt, operator.ge):
        with pytest.raises(TypeError, match=r"valid_from holds datetime, got"):
            build(q.codelist.valid_from, bad)
    with pytest.raises(TypeError, match=r"valid_from holds datetime, got"):
        q.codelist.valid_from.is_in([bad])
