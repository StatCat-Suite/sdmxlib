"""Opening a store whose scans lose rows refuses, rather than reading it (#400).

A `metadata.duckdb` in the wild answered `WHERE artefact_type = 'Codelist'` with
**1 of its 19 codelists**, and `WHERE scheme_urn = …` with **0 of 349
concepts**. The rows were all there — `SELECT *` returned them, and so did the
same queries with `SET disabled_optimizers='filter_pushdown'`. What was wrong
was the metadata the scan prunes with. Every DuckDB from 1.5.0 to 1.6.0-dev
read it identically, so it is a property of the file, not of the reader.

The reason it needs a guard rather than a fix is that it is **silent**. Nothing
errors: an eager `ConceptScheme` read just comes back with no concepts, and a
tool that re-persists what it read writes the loss down as fact. That bundle
had been answering wrongly since it was built, and was only found because a
rebuilt copy came out short.

`check_scan_integrity` runs one query against the spine on open:

    SELECT count(*) FROM artefact WHERE artefact_type >= (SELECT min(...))

`artefact_type` is `NOT NULL`, so that predicate is a tautology and the count
*must* equal the total. It carries no literal, so it cannot go stale as
artefact kinds are added, and it costs ~1 ms because the spine is small — the
same probe on `code` costs 30 ms, which is why the check does not sweep every
table and why its docstring calls it a sentinel rather than a proof.

The tests below cannot manufacture a lying store — that is the whole
difficulty of the defect — so they check the two halves that are reachable:
the probe passes on every store we write, and the refusal fires and says what
to do when the count disagrees.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.storage.schema import ScanIntegrityError, check_scan_integrity

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _artefacts() -> list[sl.Codelist | sl.ConceptScheme | sl.Dataflow]:
    return [
        sl.Codelist(
            id="CL",
            maintainer="AG",
            version="1.0",
            name={"en": "c"},
            codes=[sl.Code(id=f"C{i}", name={"en": f"c{i}"}) for i in range(20)],
        ),
        sl.ConceptScheme(
            id="CS",
            maintainer="AG",
            version="1.0",
            name={"en": "s"},
            concepts=[sl.Concept(id=f"X{i}", name={"en": f"x{i}"}) for i in range(20)],
        ),
        sl.Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "f"}),
    ]


def test_a_store_we_wrote_passes_the_probe(tmp_path: Path) -> None:
    """The other half of the guard: it must not fire on a healthy store.

    A check that refuses a good bundle is worse than no check, because the
    first thing anyone does with it is turn it off.
    """
    path = str(tmp_path / "s.duckdb")
    with sl.LocalRegistry(path) as reg:
        reg.add_all(_artefacts())

    with sl.LocalRegistry(path) as reg:  # the reopen is where the probe runs
        assert reg.query(sl.q.codelist).ids() == ["CL"]


def test_reopening_runs_the_probe_against_a_populated_store(tmp_path: Path) -> None:
    """Placed on the already-populated branch of `_initialise_schema`, not on
    every open: a schema we just created or migrated cannot have stale scan
    statistics, and skipping it there keeps the cost off the several thousand
    `:memory:` registries in this suite."""
    path = str(tmp_path / "s.duckdb")
    with sl.LocalRegistry(path) as reg:
        reg.add_all(_artefacts())

    calls = {"n": 0}
    real = duckdb.DuckDBPyConnection.execute

    def counting(
        self: duckdb.DuckDBPyConnection, query: duckdb.Statement | str, parameters: object = None
    ) -> duckdb.DuckDBPyConnection:
        if isinstance(query, str) and "min(artefact_type)" in query:
            calls["n"] += 1
        return real(self, query, parameters)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(duckdb.DuckDBPyConnection, "execute", counting)
        with sl.LocalRegistry(path):
            pass
        with sl.LocalRegistry(":memory:"):
            pass

    assert calls["n"] == 1, (
        f"the probe ran {calls['n']} times over one reopen and one fresh "
        f":memory: store — it belongs on the populated branch only"
    )


def test_the_refusal_names_the_repair() -> None:
    """A shortfall must raise, and the message must say what to do about it.

    Driven through the check directly against a hand-built table, because a
    store whose statistics lie cannot be constructed on demand — if it could,
    #400 would have a minimal repro and be an upstream bug report instead.
    """
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE artefact (urn TEXT, artefact_type TEXT NOT NULL)")
    conn.execute("INSERT INTO artefact VALUES ('a', 'Codelist'), ('b', 'Dataflow')")
    check_scan_integrity(conn)  # intact: the probe agrees

    # Stand in for the lying scan by making the probe's own subquery wrong,
    # which is the one thing about the real failure that is reproducible: the
    # filtered count comes back short while the table is full.
    conn.execute("CREATE OR REPLACE TABLE artefact AS SELECT * FROM artefact WHERE false")
    conn.execute("INSERT INTO artefact VALUES ('a', 'Codelist'), ('b', NULL)")
    with pytest.raises(ScanIntegrityError) as excinfo:
        check_scan_integrity(conn)

    message = str(excinfo.value)
    assert "filter_pushdown" in message, "the message must name the setting that reads it correctly"
    assert "#400" in message
    # Both counts, so the size of the loss is visible rather than just its fact.
    assert "holds 2 artefacts" in message
    assert "only 1 of them" in message


def test_an_empty_store_is_not_accused() -> None:
    """`min()` over no rows is NULL, so the probe would count zero and the
    check would refuse every new store. It returns early instead."""
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE artefact (urn TEXT, artefact_type TEXT NOT NULL)")
    check_scan_integrity(conn)
