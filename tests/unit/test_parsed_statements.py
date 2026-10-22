"""The parse cache for fixed store reads (GH-39).

Guarded by counts, not clocks: what it promises is that a text is parsed once,
not that a read is some number of microseconds faster.
"""

import ast
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

import sdmxlib
from sdmxlib.local import LocalRegistry
from sdmxlib.model.annotations import Annotation
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.session import VALUES_RELATION_PREFIX
from sdmxlib.storage import _parsed
from sdmxlib.storage._parsed import ParsedStatements
from sdmxlib.storage.resolve import annotation_rows

pytestmark = pytest.mark.unit


@pytest.fixture
def cache(monkeypatch: pytest.MonkeyPatch) -> ParsedStatements:
    """A fresh cache in place of the process's, so counts start at zero."""
    fresh = ParsedStatements()
    for module in ("sdmxlib.storage.readers", "sdmxlib.storage.resolve"):
        monkeypatch.setattr(f"{module}.parsed", fresh)
    return fresh


def test_reading_many_artefacts_parses_each_text_once(
    cache: ParsedStatements, make_codelist: Callable[..., Codelist]
) -> None:
    """Twenty `get`s of different codelists run the same few texts: the URN is a
    bound parameter, so it never changes what has to be parsed. A code carries
    an annotation so the annotation URL read runs too."""
    annotated = Code(id="A", name={"en": "a"}, annotations=[Annotation(id="n", type="note", title="t")])
    lists = [make_codelist(id=f"CL_{i}", codes=[annotated]) for i in range(20)]
    with LocalRegistry(":memory:") as reg:
        reg.add_all(lists)
        for codelist in lists:
            assert reg.get(codelist.urn, eager=True) is not None
    assert cache.parses == len(cache)
    assert len(cache) == 4, "the spine, name/description and both annotation reads"


def test_reading_many_dsds_parses_their_own_reads_once(
    cache: ParsedStatements, make_dsd: Callable[..., DataStructure]
) -> None:
    """The two DSD reads (components; metadata usage and descriptor URIs) join
    the shared ones: ten DSDs, each read once, parse every text once."""
    structures = [make_dsd(id=f"DSD_{i}") for i in range(10)]
    with LocalRegistry(":memory:") as reg:
        reg.add_all(structures)
        for dsd in structures:
            assert reg.get(dsd.urn) is not None
    assert cache.parses == len(cache)
    texts = list(cache._statements)
    assert sum("FROM dsd_component" in t for t in texts) == 1
    assert sum("FROM dsd_metadata_attribute" in t for t in texts) == 1


def test_a_text_is_parsed_once_however_many_threads_race_for_it() -> None:
    """A slow parser holds the race open: without single-flight, every thread
    that arrives during the first parse would parse again."""
    cache = ParsedStatements()
    conn = duckdb.connect()

    class SlowParser:
        def extract_statements(self, query: str) -> list[duckdb.Statement]:
            time.sleep(0.05)
            return conn.cursor().extract_statements(query)

    start = threading.Barrier(8)
    found: list[object] = []

    def race() -> None:
        start.wait()
        found.append(cache(SlowParser(), "SELECT 1 WHERE ? > 0"))

    threads = [threading.Thread(target=race) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert cache.parses == 1
    assert len({id(statement) for statement in found}) == 1, "every thread got the one parse"


def test_a_cached_parse_runs_with_new_values_and_on_another_connection() -> None:
    cache = ParsedStatements()
    first, second = duckdb.connect(), duckdb.connect()
    for conn, scale in ((first, 1), (second, 10)):
        conn.execute(f"CREATE TABLE t AS SELECT range AS k, range * {scale} AS v FROM range(5)")
    statement = cache(first, "SELECT v FROM t WHERE k = ?")
    assert first.execute(statement, [3]).fetchall() == [(3,)]
    assert second.execute(cache(second, "SELECT v FROM t WHERE k = ?"), [4]).fetchall() == [(40,)]
    assert cache.parses == 1


def test_a_text_naming_a_per_call_relation_is_never_kept(cache: ParsedStatements) -> None:
    """`value_match` names a large value set randomly, so the annotation read
    built around it never repeats — keeping it would only hold a slot."""
    conn = duckdb.connect()
    conn.execute("CREATE SCHEMA sdmx")
    conn.execute(
        "CREATE TABLE sdmx.annotation (id BIGINT, owner_urn TEXT, annotation_id TEXT, title TEXT, "
        "annotation_type TEXT, value TEXT, ordinal INTEGER)"
    )
    conn.execute("CREATE TABLE sdmx.annotation_text (annotation_id BIGINT, lang TEXT, text TEXT)")
    name = f"{VALUES_RELATION_PREFIX}test"
    conn.execute(f"CREATE TEMP VIEW {name} AS SELECT 'x' AS v")
    assert annotation_rows(conn, f"a.owner_urn IN (SELECT v FROM {name})", []) == {}
    assert len(cache) == 0
    assert cache.parses == 0


def test_past_its_bound_a_text_is_parsed_but_not_kept() -> None:
    cache = ParsedStatements(maxsize=2)
    conn = duckdb.connect()
    for i in range(3):
        cache(conn, f"SELECT {i}")
    assert len(cache) == 2
    assert isinstance(cache(conn, "SELECT 2"), str), "the third text runs as text, parsed by DuckDB as before"


def test_the_opted_in_reads_are_exactly_these() -> None:
    """An inventory: a new call site is a decision someone should see.

    Exact on purpose. The call sites were chosen by measurement (the texts
    that were 78% of every character parsed on a bundle browse); one added
    without measuring, or one that silently went away, both fail here.
    """
    root = Path(sdmxlib.__file__).parent
    sites: Counter[str] = Counter()
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "parsed":
                sites[path.relative_to(root).as_posix()] += 1
    assert sites == Counter({"storage/readers.py": 4, "storage/resolve.py": 2})


def test_the_module_cache_is_one_shared_instance() -> None:
    from sdmxlib.storage import readers, resolve

    assert readers.parsed is resolve.parsed is _parsed.parsed
