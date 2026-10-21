"""The delete cascade finds annotations by an indexed column, not a LIKE (#145).

`annotation.owner_urn` is usually an *item* URN — a code, a concept, a DSD
component — and an item URN is not a prefix of its artefact's: the class
differs (`Code=` against `Codelist=`), so only the `agency:id(version)` tail is
shared. Clearing one artefact's annotations therefore matched
`LIKE '%=SUFFIX.%'`, whose leading wildcard no index can serve. On a real
bundle that scanned all 585,675 rows, 698 times: 29.2 s of a 148.4 s
whole-bundle re-ingest, 20 % of it, in two statements.

Schema 2.19 carries the owning maintainable as its own column, so the cascade
is an equality on `idx_ann_artefact`. Measured interleaved against `main`:

| | re-ingest | those statements |
| --- | --- | --- |
| `LIKE` (main) | 148.4 s | 29.2 s |
| indexed (here) | 121.1 s | 1.15 s — **25x** |

Fresh writes are the control and do not move (29.6 s / 30.0 s), the cascade
being skipped entirely for an artefact that is not stored yet (#143).

`localized_text` needed no column. It holds only *maintainables'* labels — an
item's live inline in a `MAP` column, which `SideLabel` documents on the read
side — so its owner already **is** the artefact URN, and the `OR owner_urn
LIKE` half matched nothing while costing a full scan on every delete. That is
asserted here rather than assumed, because it is what licenses removing it.

## Why the guards are shaped this way

Denormalisation trades a scan for an invariant, and this one has no constraint
behind it: `annotation` is keyed by a sequence id, so a row the cascade fails
to match does not collide — it silently accumulates. `test_cascade_dispatch`
cannot see that either, since it excludes the universal tables from its
growth comparison by construction. So the re-ingest count is checked here
directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.model.ref import SupportsUrn, artefact_urn
from tests.support.factories import code_urn

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.unit


def _annotation(text: str) -> sl.Annotation:
    return sl.Annotation(id=f"A_{text}", type="note", title=text, text={"en": text})


def _annotated_codelist() -> sl.Codelist:
    """A codelist whose *codes* carry annotations — the case the LIKE existed for."""
    return sl.Codelist(
        id="CL",
        maintainer="AG",
        version="1.0",
        name={"en": "codes"},
        annotations=[_annotation("on-the-codelist")],
        codes=[sl.Code(id=f"C{i}", name={"en": f"c{i}"}, annotations=[_annotation(f"on-code-{i}")]) for i in range(5)],
    )


def _annotated_hierarchy() -> Hierarchy:
    return Hierarchy(
        id="H",
        maintainer="AG",
        version="1.0",
        name={"en": "tree"},
        annotations=[_annotation("on-the-hierarchy")],
        tree=[
            HierarchicalCode(id=f"N{i}", code=code_urn("CL", f"N{i}"), annotations=[_annotation(f"on-node-{i}")])
            for i in range(4)
        ],
    )


def _annotated_scheme() -> sl.ConceptScheme:
    return sl.ConceptScheme(
        id="CS",
        maintainer="AG",
        version="1.0",
        name={"en": "concepts"},
        concepts=[
            sl.Concept(id=f"X{i}", name={"en": f"x{i}"}, annotations=[_annotation(f"on-concept-{i}")]) for i in range(3)
        ],
    )


#: One artefact per kind that annotates *items*, not just itself.
CASES: list[tuple[str, Callable[[], SupportsUrn]]] = [
    ("Codelist", _annotated_codelist),
    ("Hierarchy", _annotated_hierarchy),
    ("ConceptScheme", _annotated_scheme),
]


@pytest.mark.parametrize(("kind", "build"), CASES, ids=[k for k, _ in CASES])
def test_every_annotation_names_the_artefact_it_belongs_to(kind: str, build: Callable[[], SupportsUrn]) -> None:
    """The invariant the cascade now depends on, checked against the writers.

    An item's annotation must carry the *maintainable's* URN here even though
    its `owner_urn` is the item's. A writer that queued rows on a batch built
    for a different artefact — or on one built with an item URN — leaves rows
    the cascade will never match, and nothing else would notice.
    """
    artefact = build()
    urn = str(artefact_urn(artefact))
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(artefact)
        rows = reg.connection.execute("SELECT owner_urn, owner_artefact_urn FROM annotation ORDER BY 1").fetchall()

    assert rows, f"{kind} wrote no annotations — the fixture cannot test anything"
    assert any(owner != urn for owner, _ in rows), (
        f"{kind}: every annotation is owned by the artefact itself, so this "
        f"fixture never exercises the item case the column exists for"
    )
    wrong = {(owner, artefact_owner) for owner, artefact_owner in rows if artefact_owner != urn}
    assert not wrong, f"{kind}: {sorted(wrong)} do not name {urn}"


@pytest.mark.parametrize(("kind", "build"), CASES, ids=[k for k, _ in CASES])
def test_a_re_ingest_clears_the_items_annotations(kind: str, build: Callable[[], SupportsUrn]) -> None:
    """The failure the denormalisation risks, stated as a count.

    `annotation` is keyed by a sequence id, so a row the cascade misses does
    not collide with its replacement — it accumulates, silently, forever.
    `test_cascade_dispatch` excludes the universal tables from its growth
    comparison, so this is the only place that would see it.
    """
    artefact = build()
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(artefact)
        first = reg.connection.execute("SELECT count(*) FROM annotation").fetchone()
        reg.add(artefact)
        second = reg.connection.execute("SELECT count(*) FROM annotation").fetchone()
        texts = reg.connection.execute("SELECT count(*) FROM annotation_text").fetchone()
        after_text = texts[0] if texts else 0

    assert first is not None
    assert second is not None
    assert second[0] == first[0], (
        f"{kind}: re-ingest took annotation from {first[0]} to {second[0]} rows — "
        f"the cascade is not finding the items' annotations by owner_artefact_urn"
    )
    assert after_text, "and annotation_text must still hold the replacements, not have been emptied"


def test_localized_text_only_ever_holds_maintainables() -> None:
    """What licenses the cascade dropping the `OR owner_urn LIKE` half there.

    Item labels live inline in a `MAP` column (`code.name`), so an item URN
    never reaches this table — the LIKE matched nothing while forcing a full
    scan on every delete. If a writer ever starts storing item text here, the
    cascade would leave it behind, and this is what says so.
    """
    with sl.LocalRegistry(":memory:") as reg:
        for _kind, build in CASES:
            reg.add(build())
        orphans = reg.connection.execute("""
            SELECT lt.owner_urn
            FROM localized_text lt
            LEFT JOIN artefact a ON a.urn = lt.owner_urn
            WHERE a.urn IS NULL
        """).fetchall()

    assert not orphans, (
        f"localized_text holds non-maintainable owners {[r[0] for r in orphans]} — "
        f"the cascade deletes by `owner_urn = ?` and would leave these behind (#145)"
    )


def test_the_cascade_issues_no_like() -> None:
    """The property as a shape, not a duration.

    A LIKE with a leading wildcard cannot use an index whatever the row count,
    so its cost is the table's size — which is exactly the growth-rate
    slowdown #140 observed and #145 was filed to remove. Asserting the
    statements rather than the clock keeps that true on a machine of any speed
    and at a table of any size.
    """
    conn_cls = duckdb.DuckDBPyConnection
    real = conn_cls.execute
    seen: list[str] = []

    def counting(self: duckdb.DuckDBPyConnection, sql: str, *args: object, **kwargs: object) -> object:
        seen.append(sql)
        return real(self, sql, *args, **kwargs)

    with sl.LocalRegistry(":memory:") as reg:
        for _kind, build in CASES:
            reg.add(build())
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(conn_cls, "execute", counting)
            for _kind, build in CASES:
                reg.add(build())  # the re-ingest, where the cascade runs

    liked = [sql for sql in seen if " LIKE " in sql.upper()]
    assert not liked, f"the write path still issues {len(liked)} LIKE statements: {liked[:2]}"
    assert any("owner_artefact_urn" in sql for sql in seen), "and the replacement must actually be in use"
