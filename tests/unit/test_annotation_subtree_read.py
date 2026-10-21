"""An artefact's annotations are read by index, not by scanning the table (#426).

`get()` of a codelist, concept scheme or DSD reads every annotation the
artefact owns: its own, its items', its parts'. The read matched
``owner_urn = ? OR owner_urn LIKE '%=<suffix>.%' OR starts_with(...)``, which
no index can serve. In a store whose annotation table is mostly someone
else's, a census geography's 580,000 codes say, that scanned every row: 21 ms
to find a DSD's 2 annotations, two thirds of `LocalRegistry.obs()`.

The guard counts rows scanned, which DuckDB's detailed profile reports per
operator: the subject itself, not a clock or the statement's text.
"""

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.storage import readers

pytestmark = pytest.mark.unit

#: Someone else's annotations: the bulk of the table, as in a real bundle.
_OTHERS = sl.Codelist(
    id="CL_BIG",
    maintainer="AG",
    version="1.0",
    name={"en": "Big"},
    codes=[sl.Code(id=f"C{i}", name={"en": "c"}, annotations=Annotations([Annotation(type="T")])) for i in range(500)],
)
#: The artefact read: one annotation of its own and one on an item.
_MINE = sl.Codelist(
    id="CL_SMALL",
    maintainer="AG",
    version="1.0",
    name={"en": "Small"},
    annotations=Annotations([Annotation(type="OWN")]),
    codes=[sl.Code(id="A", name={"en": "a"}, annotations=Annotations([Annotation(type="ITEM")]))],
)


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all([_OTHERS, _MINE])
        yield reg.connection


def _annotation_rows_scanned(conn: duckdb.DuckDBPyConnection, tmp_path: Path, read: Callable[[], object]) -> int:
    """Rows the table scans of ``sdmx.annotation`` read while `read` runs."""
    profile = tmp_path / "profile.json"
    conn.execute("SET enable_profiling = 'json'")
    conn.execute("SET profiling_mode = 'detailed'")
    conn.execute(f"SET profiling_output = '{profile}'")
    try:
        read()
    finally:
        conn.execute("SET enable_profiling = 'no_output'")
    scanned = 0
    pending = list(json.loads(profile.read_text())["operator"])
    while pending:
        node = pending.pop()
        # Qualified by catalog and schema: ``memory.sdmx.annotation``.
        if str(node.get("extra_info", {}).get("Table", "")).endswith(".sdmx.annotation"):
            scanned += int(node.get("rows_scanned", 0))
        pending.extend(node.get("children", []))
    return scanned


def test_an_artefacts_annotations_are_read_without_scanning_the_table(
    conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    def read() -> dict[str, Annotations]:
        return readers._read_annotations_subtree(conn, str(_MINE.urn))

    found = read()
    assert [annotation.type for annotation in found.pop(str(_MINE.urn))] == ["OWN"]
    ((item_urn, item_annotations),) = found.items()
    assert item_urn.endswith("Code=AG:CL_SMALL(1.0).A")
    assert [annotation.type for annotation in item_annotations] == ["ITEM"]
    assert _annotation_rows_scanned(conn, tmp_path, read) == 2, "the read scanned beyond the artefact's own rows"


def test_the_old_predicate_scans_the_table(conn: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """The discrimination check: the predicate this replaced reads every row."""
    urn = str(_MINE.urn)

    def read() -> dict[str, Annotations]:
        return readers._read_annotations_for_owners_predicate(
            conn,
            "owner_urn = ? OR owner_urn LIKE ? OR starts_with(owner_urn, ?)",
            [urn, f"%={urn.partition('=')[2]}.%", f"{urn}#"],
        )

    assert _annotation_rows_scanned(conn, tmp_path, read) == 502
