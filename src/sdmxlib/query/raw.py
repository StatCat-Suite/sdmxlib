"""The raw-SQL escape hatch — `reg.sql(...)`.

Removing raw SQL was never the intent. `query-composition.md` bans SQL *text in
the structural DSL*, because its safety property is that a caller supplies field
references and literals and nothing else. That is an argument against merging
the two representations, not against a separate, clearly-marked escape.

An escape hatch is also what tells us what to build next. Consumers wrote 113
hand-written SQL sites because the DSL could not express what they needed, and
that survey is the reason this milestone exists — closing the hatch would remove
the *evidence* of the need rather than the need, and send people around the
library instead of through it.

So it is a **different method on a different object**: `reg.sql` is visibly not
`filter` at the call site, which is the property the ban was protecting.

    reg.sql("SELECT agency_id, count(*) FROM sdmx.artefact GROUP BY 1").collect()

And it is a **terminal**. It briefly did double duty as a `SqlRelation` that
`is_in` would splice into a structural query, which put the two contracts back
in one expression — the thing the ban on SQL text was there to stop, arriving
through an object rather than a string. `RawSql` no longer satisfies that
protocol; see its docstring for the four reasons and for the two supported ways
back across.

The protocol itself is untouched, because it is not the raw-SQL seam: `Query`
satisfies it, which is how one structural query filters another, and external
producers satisfy it, which is how statcatlib composes. Both are compiled
relations the planner can still reason about. Raw text is not.
"""

import re
from typing import override

import attrs
import duckdb
import polars as pl

from sdmxlib.session import completed, frame_of

#: A `$name` placeholder. Matched only outside strings, comments and
#: identifiers — see `_rewrite`.
_PLACEHOLDER = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


class RawSqlError(ValueError):
    """The escape hatch was handed SQL it will not run.

    Distinct from a database error: these are the library's own rules — named
    parameters only, every one supplied, and no construct the rewriter cannot
    read.
    """


def _spans_to_skip(text: str, entry: str = "reg.sql()") -> list[tuple[int, int]]:
    """Half-open ranges of `text` that are string, identifier or comment.

    A `$` inside any of them is data, not a placeholder. Written as a scanner
    rather than a regex because these constructs nest badly and a regex that
    looked right would be wrong on the first `'it''s'`.

    Raises:
        RawSqlError: On dollar-quoting (`$tag$...$tag$`), which makes `$name`
            ambiguous. Rare enough to refuse rather than guess at.
    """
    spans: list[tuple[int, int]] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in {"'", '"'}:
            start = index
            index += 1
            while index < length:
                if text[index] == char:
                    # A doubled quote is an escaped one, not the end.
                    #
                    # Not falsifiable by test, and said so rather than left to
                    # look covered: quotes in a well-formed literal come in
                    # pairs, so pairing them naively shifts the span boundaries
                    # but still covers everything between the first quote and
                    # the last. Measured over `'it''s $x'`, `'a''$x''b'` and
                    # friends, the two agree on every placeholder. It is here
                    # so the scanner is *right* rather than accidentally right,
                    # and so malformed input degrades predictably.
                    if index + 1 < length and text[index + 1] == char:
                        index += 2
                        continue
                    break
                index += 1
            spans.append((start, min(index + 1, length)))
            index += 1
        elif text.startswith("--", index):
            end = text.find("\n", index)
            end = length if end == -1 else end
            spans.append((index, end))
            index = end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = length if end == -1 else end + 2
            spans.append((index, end))
            index = end
        elif char == "$" and (match := re.compile(r"\$[A-Za-z_0-9]*\$").match(text, index)):
            msg = (
                f"dollar-quoted strings ({match.group()}) are not supported by "
                f"{entry}: they make $name placeholders ambiguous. Use a "
                f"single-quoted literal, or a $parameter for the value."
            )
            raise RawSqlError(msg)
        else:
            index += 1
    return spans


def _rewrite(
    text: str, values: dict[str, object], entry: str = "reg.sql()"
) -> tuple[str, tuple[object, ...], set[str]]:
    """Turn `$name` placeholders into `?`, with the binds in emitted order.

    Named parameters are what CLAUDE.md's SQL rules ask for, and positional is
    what the structural compiler emits — DuckDB refuses to mix the two in one
    statement, so a raw relation spliced into a query has to be converted.

    Raises:
        RawSqlError: If a placeholder has no value, or a value no placeholder.

    Returns:
        The rewritten SQL, the binds in emitted order, and the placeholder
        names consumed — which `_check_rewrite` compares against DuckDB's.
    """
    skip = _spans_to_skip(text, entry)
    params: list[object] = []
    used: set[str] = set()
    out: list[str] = []
    cursor = 0
    for match in _PLACEHOLDER.finditer(text):
        if any(start <= match.start() < end for start, end in skip):
            continue
        name = match.group(1)
        if name not in values:
            msg = f"{entry} has a ${name} placeholder but no {name}= argument."
            raise RawSqlError(msg)
        out.append(text[cursor : match.start()])
        out.append("?")
        params.append(values[name])
        used.add(name)
        cursor = match.end()
    out.append(text[cursor:])

    if unused := set(values) - used:
        names = ", ".join(sorted(unused))
        msg = f"{entry} was given {names}= but the SQL has no matching $placeholder."
        raise RawSqlError(msg)
    return "".join(out), tuple(params), used


def _check_rewrite(
    conn: duckdb.DuckDBPyConnection,
    original: str,
    rewritten: str,
    found: set[str],
    emitted: int,
    *,
    entry: str = "reg.sql()",
) -> None:
    """Verify the rewrite against DuckDB's own parse.

    The scanner above decides what is a placeholder and what is text inside a
    literal. Rather than trust it, ask the parser — `extract_statements` reports
    exactly the placeholder identifiers it read, and correctly reports none for
    `'a$b'`. A `$` the scanner misread inside a string shows up immediately as a
    set that does not agree.

    That is the "third implementation outside the shared assumptions" the
    testing notes in CLAUDE.md argue for: a different parser, not a second copy
    of the same reasoning. It has already earned its place — DuckDB numbers
    positional `?` as parameters `"1"`, `"2"`, … which is how the numeric-name
    case below came to be handled at all.

    Raises:
        RawSqlError: If the statement does not parse, is more than one, uses
            positional placeholders, or disagrees with the scanner.
    """
    try:
        original_statements = conn.extract_statements(original)
        statements = conn.extract_statements(rewritten)
    except Exception as exc:
        msg = f"{entry} could not parse the statement: {exc}"
        raise RawSqlError(msg) from exc
    if len(statements) != 1:
        msg = f"{entry} takes one statement, got {len(statements)}."
        raise RawSqlError(msg)

    declared = original_statements[0].named_parameters
    if numbered := {name for name in declared if name.isdigit()}:
        # DuckDB reports `?` and `$1` alike as numeric names. Both are
        # positional, and mixing them with `$name` is what it refuses outright.
        msg = (
            f"{entry} takes named placeholders only, but the SQL has "
            f"positional ones ({', '.join('$' + n for n in sorted(numbered))} "
            f"or ?). Use $name with a matching keyword argument."
        )
        raise RawSqlError(msg)
    if declared != found:
        missed = ", ".join(sorted(declared - found)) or "none"
        msg = (
            f"{entry} and DuckDB disagree about this statement's "
            f"placeholders: DuckDB reads {sorted(declared)}, the rewriter "
            f"found {sorted(found)} (missed: {missed}). This is a bug in "
            f"sdmxlib; the SQL was: {original}"
        )
        raise RawSqlError(msg)

    # `emitted`, not `len(found)`: one name used twice emits two placeholders,
    # because positional binds are consumed in order and a caller writing $a
    # twice means the value twice.
    expected = {str(index) for index in range(1, emitted + 1)}
    if statements[0].named_parameters != expected:
        msg = (
            f"{entry} produced {sorted(statements[0].named_parameters)} "
            f"placeholders, expected {sorted(expected)}. This is a bug in "
            f"sdmxlib; the SQL was: {original}"
        )
        raise RawSqlError(msg)


@attrs.frozen
class RawSql:
    """A prepared raw statement. A terminal, and deliberately *only* a terminal.

    Lazy like everything else here — `sql()` compiles and validates, and
    nothing runs until `collect()`.

    **Not a `sdmxlib.sql.SqlRelation`**, which it was until the boundary was
    drawn. It carried `to_sql()` for no reason but that protocol, so removing
    the method removes the capability everywhere at once rather than at each
    call site: `is_in`, the old DSLs, and `rest.py` all probe structurally.

    The two are different contracts, and mixing them costs more than it reads:

    - **The DSL's safety property is that a caller supplies field references
      and literals and nothing else.** Splicing arbitrary SQL back in returns
      what `query-composition.md` bans, via an object instead of a string.
    - **A raw statement has no row kind**, so nothing checks that the spliced
      relation yields urns, or codes, or anything related to the query it
      lands in. The static story the row-kind phantoms tell is bypassed
      silently — the failure is an empty result, not an error.
    - **It cannot be pushed down.** §4 compiles this DSL to REST backends and
      a federation planner. An opaque DuckDB statement is not analysable, so
      one splice pins the whole query to DuckDB for good.
    - **It hides the evidence.** This hatch exists to show what to build next
      — 113 hand-written SQL sites are why the milestone exists. Free
      splicing makes the gap painless and therefore unreported.

    Crossing back is one call, and the point is that it is *visible*:

        urns = reg.sql("SELECT urn FROM sdmx.artefact WHERE ...").collect()["urn"]
        reg.query(q.dataflow).filter(q.dataflow.urn.is_in(urns)).collect()

    That materialises where the subquery did not, which is the real cost of
    the rule: a large set becomes N binds. When that hurts, the answer is to
    make the DSL express the query — which is the hatch working as intended.

    Composition between *compiled* relations is untouched: `Query` satisfies
    `SqlRelation`, so `is_in(another_query)` still compiles to one
    `IN (SELECT ...)`, as does a producer from another library, for which the
    protocol has no alternative.
    """

    sql: str
    params: tuple[object, ...]
    _conn: duckdb.DuckDBPyConnection = attrs.field(eq=False, repr=False, alias="conn")

    @property
    def columns(self) -> tuple[str, ...]:
        """Column names this statement produces, in order.

        Read with `DESCRIBE`, which plans without running — so asking what a
        relation yields does not fetch it.
        """
        rows = self._conn.execute(f"DESCRIBE {self.sql}", list(self.params)).fetchall()
        return tuple(str(row[0]) for row in rows)

    def collect(self) -> pl.DataFrame:
        """Execute and return a `polars.DataFrame`. The only executing call."""
        return frame_of(completed(self._conn).sql(self.sql, params=list(self.params)))

    def explain(self) -> str:
        """The statement and its binds. Runs nothing."""
        binds = ", ".join(repr(p) for p in self.params) or "none"
        return f"{self.sql}\n-- binds: {binds}"

    @override
    def __repr__(self) -> str:
        first = self.sql.strip().split("\n", 1)[0]
        return f"<RawSql lazy {first!r}{'...' if '\n' in self.sql.strip() else ''}>"


def prepare(
    conn: duckdb.DuckDBPyConnection,
    text: str,
    values: dict[str, object],
    *,
    entry: str = "reg.sql()",
) -> RawSql:
    """Validate and convert a raw statement. The entry point `Registry.sql` calls.

    `entry` is the method a caller actually called, so a refusal names it, and
    a caller with its own spelling of the entry point is not pointed at
    `reg.sql()`.

    Raises:
        RawSqlError: If the SQL will not parse, is more than one statement, or
            its placeholders and arguments do not correspond.
    """
    rewritten, params, found = _rewrite(text, values, entry)
    _check_rewrite(conn, text, rewritten, found, len(params), entry=entry)
    return RawSql(sql=rewritten, params=params, conn=conn)
