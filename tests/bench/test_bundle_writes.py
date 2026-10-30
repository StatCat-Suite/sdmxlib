"""Write-path assertions against a real SDMX bundle (#140).

The bundle tier had no write test. Every assertion in `test_bundle_reads.py`
and `test_bundle_observations.py` reads; nothing watched the path that *puts*
a 580k-code codelist into DuckDB — which is the path #140 was filed about,
after a 523-artefact persist ran for five hours and was killed.

That is fixed now, and by a sequence of changes none of which had a guard on
this tier: Arrow columnar inserts replaced per-row `INSERT` (#66), the last
`executemany` went the same way (#317), the owner URN stopped being re-parsed
per item (#139), and the delete cascade stopped issuing 40 statements per
artefact (#144). Re-measured on the census bundle, 698 artefacts and ~3 M rows:

| | #140 as filed (0.34 / 0.35) | today |
| --- | --- | --- |
| whole-bundle persist | 5 h+, killed | **30.4 s** |
| per-artefact floor | ~1 s | **2.4 ms** median over 682 |
| `INSERT` for 579,951 codes | one per row | **one** |

Measured on `main` the day this landed, and stated so a later reader can tell
whether a bound is tight or slack:

| | statements | one-item control | INSERTs |
| --- | --- | --- | --- |
| `CL_GEOGRAPHY`, 579,951 codes | **7** | 5 | 4, one per table |
| `H_CP_GEO_FED2023`, 499,143 nodes | **11** | 6 | 8, one per table |
| 100 MetadataSets | **8.0** each | — | — |

## Statements, not seconds

The regression these guard against is *shape*: a per-row round trip coming
back. That is a count, and a count reads the same on a busy laptop as on an
idle one — CLAUDE.md's rule, and the reason #139 shipped as a count after
three attempts at a clock disagreed with each other.

So each assertion compares a large artefact against a one-item control of the
same kind. The control is what makes the bound self-calibrating: adding a
table to the writer moves both sides and the test stays honest, while a bound
written as a bare number would have to be edited every time — and an edited
bound is a bound nobody trusts.

Each was watched to fail before being trusted, per this tier's standing rule.
Chunking `code` / `hierarchy_node` into 20 bulk inserts fails both large-side
assertions naming the table (`{'code': 20}`), and 15 extra statements per
artefact in `add_all` takes the floor from 8.0 to 23.0. The honest pre-#66
shape — one `INSERT` per row — could not be used as the sabotage: a single
codelist did not finish in ten minutes, which is the five hours in the table
above, reproduced.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import attrs
import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.model.ref import SupportsUrn
from tests.support.factories import code_urn

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from sdmxlib.model.urn import SdmxUrn

    from ._bundle import Shapes

pytestmark = pytest.mark.bundle

#: How many more statements a large artefact may cost than a one-item one of
#: the same kind.
#:
#: Not zero: a real codelist carries annotations and a real hierarchy carries
#: levels and outgoing refs, so it legitimately writes tables the control does
#: not. It is small because the *number* of tables is a property of the class,
#: not of the row count — the failure this catches is a count that tracks rows,
#: which lands three or four orders of magnitude away from any figure here.
STATEMENT_HEADROOM = 12


@pytest.fixture(scope="module")
def bundle_registry(bundle_conn: duckdb.DuckDBPyConnection) -> sl.LocalRegistry:
    """A registry over the read-only bundle, used only to read the input.

    `resolve_cache_items=0` for the reason `test_bundle_reads` gives: a shared
    cache would make one test's read serve the next one's. Here the read is not
    what is measured, but the artefacts must still be the bundle's own rather
    than a cached graph shared across parametrisations.
    """
    return sl.LocalRegistry.from_connection(bundle_conn, resolve_cache_items=0)


@contextlib.contextmanager
def counting_sql() -> Generator[list[str]]:
    """Every SQL statement issued on any DuckDB connection in the block.

    Patched on the class rather than an instance: `LocalRegistry` opens its own
    connection, and the point of the count is to catch statements the writer
    issues where nobody is looking.
    """
    seen: list[str] = []
    real = duckdb.DuckDBPyConnection.execute

    def counting(
        self: duckdb.DuckDBPyConnection, query: duckdb.Statement | str, parameters: object = None
    ) -> duckdb.DuckDBPyConnection:
        seen.append(str(query))
        return real(self, query, parameters)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(duckdb.DuckDBPyConnection, "execute", counting)
        yield seen


def _write(tmp_path: Path, name: str, artefact: SupportsUrn) -> list[str]:
    """Statements issued writing one artefact into a fresh store.

    Fresh per call so the write is an insert rather than a re-ingest — the
    cascade's cost is #144's subject and would otherwise be counted here too.
    """
    with sl.LocalRegistry(str(tmp_path / f"{name}.duckdb")) as reg, counting_sql() as seen:
        reg.add(artefact)
    return seen


def _read(reg: sl.LocalRegistry, urn: SdmxUrn[object]) -> SupportsUrn:
    """The artefact, fully materialised — a lazy one writes none of its items."""
    artefact = reg.get(urn, eager=True)
    assert artefact is not None, f"the bundle has no {urn}"
    assert isinstance(artefact, SupportsUrn), f"{urn} came back as {type(artefact).__name__}"
    return artefact


def _tiny_codelist() -> Codelist:
    return Codelist(
        id="CL_CONTROL", maintainer="BENCH", version="1.0", name={"en": "one"}, codes=[Code(id="A", name={"en": "a"})]
    )


def _tiny_hierarchy() -> Hierarchy:
    return Hierarchy(
        id="H_CONTROL",
        maintainer="BENCH",
        version="1.0",
        name={"en": "one"},
        tree=[HierarchicalCode(id="N", code=code_urn("CL", "N"))],
    )


#: The two large artefacts these assertions run against, paired with the
#: one-item control of the same kind and with the `Shapes` field naming their
#: size. Both tests below read this list rather than repeating it.
LARGE = [
    ("codelist", "codelist_codes", _tiny_codelist),
    ("big_hierarchy", "big_hierarchy_nodes", _tiny_hierarchy),
]


@pytest.fixture(scope="module")
def large_writes(
    bundle_registry: sl.LocalRegistry, shapes: Shapes, tmp_path_factory: pytest.TempPathFactory
) -> dict[str, tuple[int, list[str]]]:
    """Each large artefact written once, with its one-item control.

    Module-scoped because reading and writing 580k codes twice costs ~20 s and
    buys nothing: the two assertions below are different questions about the
    *same* statement list, not two measurements.
    """
    root = tmp_path_factory.mktemp("bundle_writes")
    out: dict[str, tuple[int, list[str]]] = {}
    for shape_attr, _, control in LARGE:
        urn: SdmxUrn[object] = getattr(shapes, shape_attr)
        control_n = len(_write(root, f"{shape_attr}_control", control()))
        out[shape_attr] = (control_n, _write(root, shape_attr, _conformant(_read(bundle_registry, urn))))
    return out


def _conformant(artefact: SupportsUrn) -> SupportsUrn:
    """*artefact* repaired where the bundle's data breaks a rule `add` enforces (#452, #453).

    The bundle's `CL_GEOGRAPHY` is StatCan's data, which a strict registry
    refuses: 498,786 codes named ``{"en": ""}``, and 18,207 whose parent is no
    code in the list. `add` refuses both now. What this file measures is how
    the write batches, not what the codes say, so here a blank name becomes
    the code's id and a dangling parent is dropped, rather than the rules
    bent for the bundle.
    """
    if not isinstance(artefact, Codelist):
        return artefact
    ids = {code.id for code in artefact.codes}

    def repaired(code: Code) -> Code:
        if code.parent_id is not None and code.parent_id not in ids:
            code = attrs.evolve(code, parent_id=None)
        texts = code.name.as_dict()
        if all(text.strip() for text in texts.values()):
            return code
        return attrs.evolve(code, name={lang: text if text.strip() else code.id for lang, text in texts.items()})

    return attrs.evolve(artefact, codes=[repaired(code) for code in artefact.codes])


@pytest.mark.parametrize(("shape_attr", "size_attr"), [(a, b) for a, b, _ in LARGE], ids=["codelist", "hierarchy"])
def test_a_large_artefact_costs_no_more_statements_than_a_one_item_one(
    large_writes: dict[str, tuple[int, list[str]]], shapes: Shapes, shape_attr: str, size_attr: str
) -> None:
    """The regression #140 was filed on, stated so it cannot come back quietly.

    Persisting `CL_GEOGRAPHY` used to issue one `INSERT` per code. Nothing in
    the suite could see that: the synthetic ingest benchmarks cap at 10k items
    and report a duration, so the difference between batched and not read as
    "the machine was busy". Here it is the difference between ~20 statements
    and ~580,000.
    """
    items: int = getattr(shapes, size_attr)
    small_n, statements = large_writes[shape_attr]
    large_n = len(statements)

    assert large_n <= small_n + STATEMENT_HEADROOM, (
        f"{shape_attr}: {large_n} statements for {items:,} items against {small_n} for one — "
        f"{large_n / max(small_n, 1):.1f}x the one-item cost, so the write is scaling with rows "
        f"rather than with tables. That is the per-row round trip #66 removed."
    )


@pytest.mark.parametrize(("shape_attr", "size_attr"), [(a, b) for a, b, _ in LARGE], ids=["codelist", "hierarchy"])
def test_the_rows_go_in_through_one_insert_per_table(
    large_writes: dict[str, tuple[int, list[str]]], shapes: Shapes, shape_attr: str, size_attr: str
) -> None:
    """The same property from the other side, and the one that names the table.

    The test above bounds the total; this says no single table is written more
    than once, which is what "batched" means and what a failure needs to point
    at. A writer that batches per parent instead of per artefact passes the
    bound above by a hair and fails here saying which table it split.
    """
    items: int = getattr(shapes, size_attr)
    _, statements = large_writes[shape_attr]

    inserts: dict[str, int] = {}
    for sql in statements:
        head = sql.split()
        if len(head) > 2 and head[0].upper() == "INSERT":
            inserts[head[2]] = inserts.get(head[2], 0) + 1

    assert inserts, f"{shape_attr} wrote nothing — the read or the write is broken, not the batching"
    repeated = {table: n for table, n in inserts.items() if n > 1}
    assert not repeated, (
        f"{shape_attr}: {repeated} written more than once for {items:,} items. "
        f"One INSERT per table is what makes a bulk write bulk; a count that "
        f"tracks the item count is the #66 regression."
    )


def test_the_per_artefact_floor_is_a_handful_of_statements(
    bundle_conn: duckdb.DuckDBPyConnection, bundle_registry: sl.LocalRegistry, tmp_path: Path
) -> None:
    """#140's observation 2: 28 small artefacts took 29.1 s, ~1 s each.

    That floor mattered more than the row volume for a bundle of 523 mostly
    small artefacts, and it is the half most likely to creep back — a lookup
    added to `add` costs nothing on one artefact and everything on a thousand.

    Counted rather than timed, and expressed per artefact so the number does
    not move when the bundle grows.
    """
    # `sl.schema` rather than a bare table name: the bundle connection has no
    # search_path, and a qualified name reflected from the DDL fails loudly if
    # the column is renamed instead of silently reading nothing.
    rows = bundle_conn.execute(
        f"SELECT {sl.schema.artefact.urn} FROM {sl.schema.artefact} "
        f"WHERE {sl.schema.artefact.artefact_type} = ? ORDER BY 1 LIMIT 100",
        ["MetadataSet"],
    ).fetchall()
    if len(rows) < 20:
        pytest.skip(f"bundle has {len(rows)} MetadataSets — a per-artefact figure over a handful is noise")

    small = [_read(bundle_registry, sl.SdmxUrn.parse(str(row[0]))) for row in rows]

    with sl.LocalRegistry(str(tmp_path / "many.duckdb")) as reg, counting_sql() as seen:
        reg.add_all(small)

    per_artefact = len(seen) / len(small)
    assert per_artefact <= 20, (
        f"{per_artefact:.1f} statements per small artefact over {len(small)} of them "
        f"({len(seen)} total) — the per-artefact fixed cost #140 measured at ~1 s "
        f"is growing again"
    )


def test_the_bundle_has_the_shape_these_assertions_need(shapes: Shapes) -> None:
    """A guard that runs on an empty bundle asserts nothing.

    Same role as `test_bundle_observations.test_the_binding_has_the_shape…`:
    the counts above are only meaningful if the large side really is large,
    and a bundle rebuilt smaller should say so here rather than passing
    everything by being trivial.
    """
    assert shapes.codelist_codes > 10_000, f"largest codelist has {shapes.codelist_codes} codes"
    assert shapes.big_hierarchy_nodes > 10_000, f"largest hierarchy has {shapes.big_hierarchy_nodes} nodes"
