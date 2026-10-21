"""Structural invariants over any SQL the library emits (#257).

Three properties should hold of every statement, whatever query produced it.
None of them was asserted before this.

## Why parse rather than string-match

Not as a validity check. The query DSLs already **execute** every generated
query against real DuckDB (#248/#249), and executing subsumes parsing — a
"does it parse" assertion would be strictly weaker than what exists. The value
here is the three structural questions below, which no amount of executing
answers: a statement can run perfectly and still have a user value welded into
its text.

## The measurement that motivates `assert_single_statement`

DuckDB's `execute()` accepts multiple statements and runs them all:

    >>> c.execute("CREATE TABLE obs(geo VARCHAR)")
    >>> c.execute("SELECT 1; DROP TABLE obs")
    >>> [r[0] for r in c.execute("SHOW TABLES").fetchall()]
    []

So "one query in, one statement out" is a real invariant rather than ceremony,
and `local/data_store._build_query` interpolates three caller-supplied strings
verbatim (`columns`, `where`, `order_by`).

Test-only; not packaged with sdmxlib. `sqlglot` is a dev dependency and
`test_sql_ast_invariants.test_sqlglot_is_test_only` keeps it one.
"""

from __future__ import annotations

import sqlglot
from sqlglot import expressions as exp

# `Expression` is not re-exported from `sqlglot.expressions`; basedpyright names
# `sqlglot.expressions.core` as the import to use, and it is right (#347).
from sqlglot.expressions.core import Expr

#: Every relation the library is allowed to name. Anything else in a FROM or
#: JOIN means an identifier was interpolated from somewhere it should not have
#: been, or a schema remap (`CompileContext.schema`) went wrong.
KNOWN_TABLES = frozenset(
    {
        "sdmx.artefact",
        "sdmx.annotation",
        "sdmx.annotation_text",
        "sdmx.code",
        "sdmx.concept",
        "sdmx.categorisation",
        "sdmx.dataflow",
        "sdmx.dsd_component",
        "sdmx.hierarchy_node",
        "sdmx.localized_text",
        "sdmx.metadata_target",
        "sdmx.metadata_value",
        # Unqualified spellings: the schema is on the search path, and the
        # storage readers use the short form.
        "artefact",
        "annotation",
        "annotation_text",
        "code",
        "hierarchy_node",
        "localized_text",
        "metadata_value",
    }
)


def parse(sql: str) -> list[Expr]:
    """Parse *sql* as DuckDB, returning one expression per statement.

    `Expr`, not `Expression`, because that is what `sqlglot.parse` promises:
    `Expression` is a *subclass* of `Expr`, so the old annotation claimed more
    than the library guarantees. At runtime a statement is a `Select` or
    similar, which satisfies both — the over-claim was invisible until this
    directory was type-checked.
    """
    return [s for s in sqlglot.parse(sql, dialect="duckdb") if s is not None]


def assert_single_statement(sql: str) -> None:
    """The statement-injection invariant: one query in, one statement out.

    The one that pays for the dependency. DuckDB runs every statement in a
    multi-statement string, so a builder that can be made to emit two has a
    statement-injection hole rather than merely a value-injection one.
    """
    statements = parse(sql)
    assert len(statements) == 1, f"expected one statement, got {len(statements)}: {sql!r}"


def assert_no_literals_in_predicates(sql: str) -> None:
    """No user value may appear as a literal inside a WHERE or HAVING.

    The injection suite in its strongest form. The assertion is not "the value
    was escaped" but "the value never reached the SQL text at all" — which is
    what a bind parameter guarantees and escaping only approximates.

    String literals the *compiler* writes are permitted by name below, because
    they are not user values: they are part of the emitted expression.
    """
    for statement in parse(sql):
        for clause in [*statement.find_all(exp.Where), *statement.find_all(exp.Having)]:
            for literal in clause.find_all(exp.Literal):
                if not literal.is_string:
                    # Numeric literals here are ordinals and structural
                    # constants the compiler emits (`GROUP BY 1`), never
                    # user values — those are bound.
                    continue
                assert literal.this in _COMPILER_OWNED_STRINGS, (
                    f"user value may have reached the SQL text as a literal: {literal.this!r} in {sql!r}"
                )


#: String literals the compilers legitimately write into a predicate. Each is
#: part of an expression, not a value a caller supplied.
_COMPILER_OWNED_STRINGS = frozenset(
    {
        "",  # the folded empty-needle guard: `norm(?) <> ''` (#255)
        "%",  # the folded LIKE wrapper: `'%' || norm(?) || '%'`
        " ",  # the punctuation-fold replacement
        "\\",  # the ESCAPE character
        "[\\p{P}\\p{Z}\\s]+",  # the punctuation-fold pattern
        "g",  # regexp_replace's flags argument
        "name",  # localized_text.field discriminators
        "description",
    }
)


def assert_only_known_tables(sql: str, *, allowed: frozenset[str] = KNOWN_TABLES) -> None:
    """Every relation named must be one we recognise.

    Catches identifier interpolation and schema-remapping mistakes. Subquery
    aliases are not tables and are skipped; a CTE name is registered as allowed
    for the statement that defines it.
    """
    for statement in parse(sql):
        cte_names = {cte.alias_or_name for cte in statement.find_all(exp.CTE)}
        for table in statement.find_all(exp.Table):
            name = table.sql(dialect="duckdb", identify=False).split(" ")[0]
            if name in cte_names:
                continue
            assert name in allowed, f"unknown relation {name!r} in {sql!r}"


def assert_all_invariants(sql: str) -> None:
    """All three, for a corpus sweep."""
    assert_single_statement(sql)
    assert_no_literals_in_predicates(sql)
    assert_only_known_tables(sql)
