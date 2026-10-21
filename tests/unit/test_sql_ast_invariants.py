"""Structural invariants over every SQL statement the library emits (#257).

Three properties should hold of any statement, whatever query produced it:

1. **one statement** — DuckDB runs every statement in a multi-statement
   string, so this is a real invariant rather than ceremony;
2. **no user value as a literal in a predicate** — every value arrives as a
   bind;
3. **no unknown relation** — nothing was interpolated into a table position.

## Not a parse check

The query DSLs already **execute** every generated query against real DuckDB
(#248/#249), and executing subsumes parsing — "does it parse" would be strictly
weaker than what exists. What these add is structural: a statement can run
perfectly and still have a user value welded into its text, or name a relation
nobody intended.

## Relationship to #250

#250 asserted the same structural property the narrow way: build a query with a
benign value and again with a hostile one, and require the SQL to be
byte-identical. That works and needs no dependency, but proves only that *the
value under test* did not leak. This generalises it to "no literal anywhere in
any WHERE we can generate", so the byte-identity assertion was **folded in
here** rather than kept in both places — #257 asked for that explicitly.

What stays in #250 is its semantic half, which this says nothing about: a
hostile value must still *match the right rows*.
"""

import subprocess
import sys
import tomllib

import pytest
from hypothesis import assume, given, settings

from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.hierarchy import Hierarchy
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.query.accessors import q
from tests.support import sql_ast
from tests.support.narrow import present
from tests.support.repo import ROOT
from tests.support.strategies import (
    annotated_forest_codelists,
    forest_codelists,
    forest_hierarchies,
    hostile_strings,
)

pytestmark = pytest.mark.unit

_ROOT = ROOT
_EXAMPLES = max(5, present(settings.default).max_examples // 5)


def _code_queries(lazy):
    """A spread of code queries wide enough to reach every emitter."""
    c = q.code
    #: Two aggregates, because the two positions accept different paths. A
    #: filter aggregate must cross a fan-out — there is nothing to aggregate
    #: over otherwise — while a `GROUP BY` aggregate must *not*, since the
    #: group is what it aggregates over.
    fanned = c.children.id.count()
    grouped = c.id.count()
    return [
        lazy.codes,
        lazy.codes.filter(c.id == "A"),
        lazy.codes.filter(c.id.str.contains("A")).filter(c.depth <= 2),
        lazy.codes.filter(c.name["en"].str.folded.contains("a")),
        lazy.codes.filter(c.annotations.any(q.annotation.type == "note", q.annotation.title == "x")),
        lazy.codes.filter(c.annotations.any(q.annotation.type == "note", q.annotation.text["en"] == "y")),
        lazy.codes.filter(c.ancestors.id == "A"),
        lazy.codes.filter(c.descendants.any(q.code.depth <= 2)),
        lazy.codes.filter(c.parent.id == "A"),
        lazy.codes.filter(c.id.is_in(["A", "B"])),
        lazy.codes.filter(c.id.is_in(lazy.codes.filter(c.depth == 0).select(c.id))),
        lazy.codes.order_by(c.name["en"]).limit(5),
        lazy.codes.select(c.id, c.name["en"]),
        lazy.codes.filter(fanned > 1).select(c.depth, n=fanned),
        lazy.codes.group_by(c.depth).agg(count=grouped).order_by(grouped.desc()),
        lazy.codes.qualify(c.id.rank().over(c.depth) == 1),
    ]


def _node_queries(lazy):
    n = q.hcode
    return [
        lazy.nodes,
        lazy.nodes.filter(n.level.id == "L"),
        lazy.nodes.filter(n.code.name["en"].str.unaccented.contains("a")),
        lazy.nodes.filter(n.ancestors.id == "N1"),
        lazy.nodes.group_by(n.level.id).agg(count=n.id.count()),
    ]


# ── The invariants, over generated artefacts ─────────────────────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=annotated_forest_codelists())
def test_code_queries_satisfy_every_invariant(cl: Codelist):
    """Every code query, over generated codelists, on all three invariants."""
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        lazy = reg.get(Codelist.urn_for(agency=cl.agency_id, id=cl.id, version=cl.version))
        for query in _code_queries(lazy):
            sql, _ = query.to_sql()
            sql_ast.assert_all_invariants(sql)


@settings(max_examples=_EXAMPLES, deadline=None)
@given(h=forest_hierarchies())
def test_node_queries_satisfy_every_invariant(h: Hierarchy):
    with LocalRegistry(":memory:") as reg:
        reg.add(h)
        lazy = reg.get(
            SdmxUrn.make(
                Hierarchy,
                package=h.sdmx_package,
                artefact_class=h.sdmx_class,
                agency=h.agency_id,
                id=h.id,
                version=h.version,
            )
        )
        for query in _node_queries(lazy):
            sql, _ = query.to_sql()
            sql_ast.assert_all_invariants(sql)


@settings(max_examples=_EXAMPLES, deadline=None)
@given(cl=forest_codelists(), needle=hostile_strings())
def test_a_hostile_value_never_reaches_the_sql_text(cl: Codelist, needle: str):
    """#250's structural assertion, generalised (#257).

    The narrow form compared a hostile query's SQL against a benign one's and
    required byte-identity. This asks the stronger question directly: is there
    a user value *anywhere* in this predicate's text? A leak that happened to
    produce the same byte count would satisfy the narrow form and fail this.
    """
    # `assume`, not `skip`: a skip inside a Hypothesis test abandons the whole
    # property on the first NUL draw, silently reducing it to nothing.
    assume("\x00" not in needle)  # rejected by match_sql; see #250's named regression
    c = q.code
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        lazy = reg.get(Codelist.urn_for(agency=cl.agency_id, id=cl.id, version=cl.version))
        for build in (
            lambda v: present(lazy).codes.filter(c.id == v),
            lambda v: present(lazy).codes.filter(c.id.str.contains(v)),
            lambda v: present(lazy).codes.filter(c.name["en"].str.folded.ends_with(v)),
            lambda v: present(lazy).codes.filter(
                c.annotations.any(q.annotation.type == "note", q.annotation.title == v)
            ),
            lambda v: present(lazy).codes.filter(c.id.is_in([v])),
            lambda v: present(lazy).codes.filter(c.name[v] == "x"),
            lambda v: present(lazy).codes.filter(c.annotations.any(q.annotation.type == v, q.annotation.title == "x")),
        ):
            sql, params = build(needle).to_sql()
            sql_ast.assert_all_invariants(sql)
            # Deliberately *not* asserting the value appears verbatim in the
            # binds. It legitimately does not: `50%` is bound as `50\%`, since
            # `ilike_pattern` escapes the LIKE metacharacters before binding.
            # A "value must survive intact" check would be asserting the
            # absence of escaping, which is the opposite of what we want.
            assert params, "a filtered query bound nothing at all"


# ── The invariants discriminate ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("sql", "checker", "why"),
    [
        ("SELECT a FROM code WHERE b = ?; DROP TABLE code", sql_ast.assert_single_statement, "two statements"),
        ("SELECT a FROM code WHERE b = 'CA'", sql_ast.assert_no_literals_in_predicates, "spliced value"),
        ("SELECT a FROM code WHERE b ILIKE '%CA%'", sql_ast.assert_no_literals_in_predicates, "spliced pattern"),
        ("SELECT a FROM secrets", sql_ast.assert_only_known_tables, "unknown relation"),
        ("SELECT a FROM evil.code", sql_ast.assert_only_known_tables, "remapped schema"),
    ],
)
def test_each_invariant_catches_its_defect(sql: str, checker, why: str) -> None:
    """A green invariant proves nothing until it has been shown to fail.

    Same discipline as CLAUDE.md's rule 4 for the oracles: measure that the
    check discriminates before trusting it.
    """
    with pytest.raises(AssertionError):
        checker(sql)


@pytest.mark.parametrize(
    ("sql", "checker"),
    [
        ("SELECT a FROM code WHERE b = ?", sql_ast.assert_single_statement),
        ("SELECT a FROM code WHERE b = ?", sql_ast.assert_no_literals_in_predicates),
        ("SELECT a FROM sdmx.code", sql_ast.assert_only_known_tables),
        ("WITH t AS (SELECT 1) SELECT * FROM t", sql_ast.assert_only_known_tables),
    ],
)
def test_each_invariant_accepts_the_benign_case(sql: str, checker) -> None:
    """And it must not fire on ordinary SQL — including a CTE self-reference."""
    checker(sql)


# ── DuckDB really does run multi-statement input ─────────────────────────────


def test_duckdb_runs_every_statement_in_a_multi_statement_string() -> None:
    """The measurement `assert_single_statement` exists for (#257).

    Pinned as a test because the whole invariant rests on it: if a future
    DuckDB rejected multi-statement `execute`, the single-statement assertion
    would become ceremony, and someone should be told rather than left
    guessing.
    """
    import duckdb

    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE obs(geo VARCHAR)")
    conn.execute("SELECT 1; DROP TABLE obs")
    assert [r[0] for r in conn.execute("SHOW TABLES").fetchall()] == []


# ── The dependency stays where it belongs ────────────────────────────────────


def test_sqlglot_is_test_only() -> None:
    """`sqlglot` must not reach the runtime dependency set (#257).

    A SQL parser is a large dependency to impose on every consumer for a check
    that belongs in the suite, and the `order_by` decision in
    `DataStore.query` cites this boundary as the reason validation happens in
    tests rather than at runtime. If it moves, that reasoning silently stops
    holding.
    """
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = pyproject["project"]["dependencies"]
    assert not [d for d in runtime if "sqlglot" in d], f"sqlglot leaked into runtime deps: {runtime}"
    dev = pyproject["dependency-groups"]["dev"]
    assert [d for d in dev if "sqlglot" in d], "sqlglot should be in the dev group"


def test_sqlglot_is_not_imported_by_the_package() -> None:
    """Belt and braces: importing sdmxlib must not pull the parser in.

    A declaration in `pyproject.toml` is a promise; this is the observation.
    An accidental `import sqlglot` in `src/` would satisfy the former and
    break the latter.
    """
    code = "import sys, sdmxlib; print('sqlglot' in sys.modules)"
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=_ROOT
    )
    assert result.stdout.strip() == "False", "importing sdmxlib pulled in sqlglot"
