"""Parse a fixed store read once, and execute the parse after that (GH-39).

DuckDB 2.0's parser costs about ten times 1.5's: 339 µs to parse a 961-character
statement against 30 µs, which is most of what a point read lost in the upgrade
(1.19 ms against 0.85 ms). A store read's SQL text never changes — the URN it
looks up is a bound parameter — so the parse can be done once per process.
`con.extract_statements(sql)` returns the parse as a `duckdb.Statement`, and
`execute(statement, params)` runs it without parsing again: the pattern
DuckDB's own suite tests (`test_pystatement`), and the one `sqlite3`'s
statement cache and `re.compile` follow.

**Only the parse is kept.** Binding, planning and execution still happen on
every call, so a cached parse cannot go stale when the catalog or the data
changes. Nothing here needs invalidating.

**Opt-in, at fixed texts only.** A call site passes its SQL through `parsed`;
nothing is intercepted. The texts are the hottest reads measured on a full
browse of the 2021 Census Profile edition — the spine read and the annotation
reads were 65% of every character parsed. A text naming a per-call value
relation is never admitted: its name is random, so it would never be read
back, and it would hold a slot until evicted.

**A parse depends on the parser's settings** (`enable_legacy_parser`,
`lambda_syntax`). sdmxlib never changes them; a caller who does should not
expect a parse cached under the old setting to follow.
"""

from typing import Protocol

import duckdb

from sdmxlib._singleflight import SingleFlight
from sdmxlib.session import VALUES_RELATION_PREFIX

#: Distinct texts to keep. The store reads that opt in are a fixed set — a
#: handful — so this is a bound against a dynamic text passed by mistake, not a
#: working-set size. A parse weighs about 33 KB, so the cap is ~8 MB at most.
MAX_TEXTS = 256


class Parses(Protocol):
    """What the cache needs of a connection: its parser, and nothing else."""

    def extract_statements(self, query: str) -> list[duckdb.Statement]:
        """Parse ``query`` into its statements."""
        ...


class ParsedStatements:
    """Bounded `sql -> Statement` with lock-free hits and one parse per text.

    A hit is a plain dict read. A miss parses through `SingleFlight`, so threads
    racing on one text parse it once. Past `MAX_TEXTS` a text is parsed but not
    kept: DuckDB parses it on every call, as it does for any text that never
    opted in.
    """

    def __init__(self, maxsize: int = MAX_TEXTS) -> None:
        self._statements: dict[str, duckdb.Statement] = {}
        self._flight = SingleFlight(maxsize=maxsize)
        self.maxsize = maxsize
        #: How many times a text was parsed here. Best-effort under contention,
        #: like `_FIFOCache`'s counters; exact in a single thread.
        self.parses = 0

    def __call__(self, handle: Parses, sql: str) -> duckdb.Statement | str:
        """The parse of ``sql``, or ``sql`` itself when it may not be kept.

        Args:
            handle: The connection about to execute it. Only its parser is used;
                a `Statement` runs on any connection.
            sql: One statement's text, with its values as bound parameters.

        Returns:
            What to hand `execute()`: the cached `Statement`, or the text when
            it names a per-call relation or the cache is full.
        """
        statement = self._statements.get(sql)
        if statement is not None:
            return statement
        if VALUES_RELATION_PREFIX in sql:
            return sql

        def parse() -> None:
            (parsed,) = handle.extract_statements(sql)
            self.parses += 1
            if len(self._statements) < self.maxsize:
                self._statements[sql] = parsed

        self._flight.run(sql, parse)
        return self._statements.get(sql, sql)

    def __len__(self) -> int:
        """How many texts are kept."""
        return len(self._statements)

    def __contains__(self, sql: object) -> bool:
        """Whether ``sql``'s parse is kept."""
        return sql in self._statements


#: The process's one cache: a `Statement` belongs to no connection, so every
#: store and thread shares it.
parsed = ParsedStatements()
