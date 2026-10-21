"""The raw-SQL escape hatch — `reg.sql()` and `sl.schema`.

The hatch is not a concession. Consumers wrote 113 hand-written SQL sites
because the DSL could not express what they needed, and that survey is why this
milestone exists — closing the hatch removes the *evidence* of the need, not
the need. So it stays, as a **different method on a different object**: the
structural DSL still never accepts text.

It is also a **terminal**. A raw statement used to splice into `is_in` as a
`SqlRelation`, and no longer does: the two carry different contracts, and the
mixture reintroduced through an object what the ban on SQL text was protecting.
The section below tests that boundary in both directions — raw text refused,
compiled relations still composing — because the useful half of the capability
is easy to lose along with the harmful half.

Most of what is left is the rewrite rather than the SQL. `$name` placeholders
have to become `?` because that is what the connection binds positionally, and
a rewriter that mistakes a `$` inside a string literal for a placeholder would
corrupt the statement silently. It is checked against DuckDB's own parser
rather than against a second copy of the same reasoning.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.organisation import Agency
from sdmxlib.query.accessors import q
from sdmxlib.query.raw import RawSqlError, _check_rewrite

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    with sl.LocalRegistry(":memory:") as registry:
        for cid, agency in [("CL_A", "ESTAT"), ("CL_B", "OECD"), ("CL_C", "ESTAT")]:
            registry.add(
                Codelist(
                    id=cid,
                    maintainer=Agency(id=agency),
                    name={"en": cid},
                    codes=[Code(id="X", name={"en": "x"})],
                )
            )
        yield registry


# ── the terminal ──────────────────────────────────────────────────────────


def test_raw_sql_runs_against_the_layout(reg: sl.LocalRegistry) -> None:
    frame = reg.sql("SELECT agency_id, count(*) AS n FROM sdmx.artefact GROUP BY 1 ORDER BY 1").collect()
    assert frame.to_dicts() == [{"agency_id": "ESTAT", "n": 2}, {"agency_id": "OECD", "n": 1}]


def test_values_are_parameters(reg: sl.LocalRegistry) -> None:
    found = reg.sql("SELECT resource_id FROM sdmx.artefact WHERE agency_id = $a ORDER BY 1", a="ESTAT")
    assert found.collect()["resource_id"].to_list() == ["CL_A", "CL_C"]
    assert "ESTAT" not in found.sql
    assert found.params == ("ESTAT",)


def test_a_placeholder_used_twice_binds_twice(reg: sl.LocalRegistry) -> None:
    """Positional binds are consumed in order, so one name appearing twice has
    to emit two — a dict-shaped rewrite would emit one and misalign the rest."""
    found = reg.sql(
        "SELECT resource_id FROM sdmx.artefact WHERE agency_id = $a OR agency_id = $a ORDER BY 1",
        a="OECD",
    )
    assert found.params == ("OECD", "OECD")
    assert found.collect()["resource_id"].to_list() == ["CL_B"]


def test_it_is_lazy(reg: sl.LocalRegistry) -> None:
    prepared = reg.sql("SELECT 1 AS x")
    assert "lazy" in repr(prepared)
    assert "binds" in prepared.explain()


def test_columns_does_not_run_the_query(reg: sl.LocalRegistry) -> None:
    """`DESCRIBE` plans without fetching, so asking a relation's arity is cheap
    — which matters because `is_in` asks it on every splice."""
    assert reg.sql("SELECT urn, resource_id FROM sdmx.artefact").columns == ("urn", "resource_id")


# ── the boundary: a terminal, not a step ──────────────────────────────────


def test_raw_sql_does_not_splice_into_the_structural_dsl(reg: sl.LocalRegistry) -> None:
    """The hatch is a terminal. It composed until the boundary was drawn, and
    the reasons it should not are contract-level rather than stylistic: no row
    kind to check the column against, and nothing a non-DuckDB backend could
    push down."""
    urns = reg.sql("SELECT urn FROM sdmx.artefact WHERE resource_id LIKE $p", p="CL_A%")
    with pytest.raises(TypeError, match=r"will not splice reg\.sql"):
        # The refusal is the subject: `reg.sql` is exactly what `is_in` will
        # not splice, so the call has to be written to be refused.
        q.codelist.urn.is_in(urns)  # pyrefly: ignore[bad-argument-type]


def test_the_refusal_names_both_ways_across(reg: sl.LocalRegistry) -> None:
    """A refusal that does not say what to do instead sends the caller around
    the library rather than through it — which is what the hatch exists to
    prevent."""
    urns = reg.sql("SELECT urn FROM sdmx.artefact")
    with pytest.raises(TypeError) as excinfo:
        # The refusal is the subject: `reg.sql` is exactly what `is_in` will
        # not splice, so the call has to be written to be refused.
        q.codelist.urn.is_in(urns)  # pyrefly: ignore[bad-argument-type]
    assert ".collect()" in str(excinfo.value)
    assert "reg.query(...)" in str(excinfo.value)


def test_the_refusal_is_structural_not_a_special_case(reg: sl.LocalRegistry) -> None:
    """`RawSql` carries no `to_sql`, so it misses the `SqlRelation` probe
    everywhere at once — `is_in`, the older DSLs, `rest.py`. The message in
    `is_in` is a courtesy on top of a refusal that does not depend on it."""
    from sdmxlib.sql import SqlRelation

    raw = reg.sql("SELECT urn FROM sdmx.artefact")
    assert not isinstance(raw, SqlRelation)
    assert not hasattr(raw, "to_sql")


def test_materialising_is_the_way_across(reg: sl.LocalRegistry) -> None:
    """The cost of the rule, paid in the open: N binds where the subquery was
    one statement. That visibility is the point — a hatch that composes freely
    never reports the gap it exists to reveal."""
    urns = reg.sql("SELECT urn FROM sdmx.artefact WHERE resource_id LIKE $p", p="CL_A%")
    found = reg.query(q.codelist).filter(q.codelist.urn.is_in(urns.collect()["urn"]))
    assert found.ids() == ["CL_A"]


def test_a_compiled_query_still_composes_lazily(reg: sl.LocalRegistry) -> None:
    """The boundary is around raw text, not around `SqlRelation`. A `Query`
    satisfies it and still splices to one `IN (SELECT ...)` — losing that
    would have been the needlessly restrictive version of this rule."""
    from sdmxlib.sql import SqlRelation

    estat = reg.query(q.codelist).filter(q.codelist.agency == "ESTAT").select(urn=q.codelist.urn)
    assert isinstance(estat, SqlRelation)
    found = reg.query(q.codelist).filter(q.codelist.urn.is_in(estat))
    assert "IN (SELECT" in found.to_sql().sql
    assert sorted(found.ids()) == ["CL_A", "CL_C"]


def test_a_multi_column_relation_is_refused(reg: sl.LocalRegistry) -> None:
    """`IN` needs exactly one column. A column-count error at the database
    names neither the call site nor the fix (#274). Still reachable through a
    `Query`, which is now the only thing that splices."""
    two = reg.query(q.codelist).select(urn=q.codelist.urn, agency=q.codelist.agency)
    with pytest.raises(TypeError, match="one-column relation, got 2"):
        q.codelist.urn.is_in(two)


def test_a_list_still_works(reg: sl.LocalRegistry) -> None:
    """The relation form is an addition, not a replacement."""
    assert reg.query(q.codelist).filter(q.codelist.id.is_in(["CL_A", "CL_B"])).count() == 2


def test_a_bare_string_is_still_refused(reg: sl.LocalRegistry) -> None:
    """#261. Adding a second accepted shape must not reopen it."""
    with pytest.raises(TypeError, match="iterable of values"):
        q.codelist.id.is_in("CL_A")


# ── the rewrite, checked against DuckDB's parser ──────────────────────────


def test_a_dollar_inside_a_string_is_not_a_placeholder(reg: sl.LocalRegistry) -> None:
    """The rewriter's whole risk. DuckDB reads no parameter here, so a scanner
    that found one would disagree with it and be caught."""
    found = reg.sql("SELECT 'cost: $amount' AS label")
    assert found.params == ()
    assert found.collect()["label"].to_list() == ["cost: $amount"]


def test_a_doubled_quote_does_not_end_the_string(reg: sl.LocalRegistry) -> None:
    found = reg.sql("SELECT 'it''s $x' AS label")
    assert found.params == ()
    assert found.collect()["label"].to_list() == ["it's $x"]


def test_a_dollar_in_a_comment_is_not_a_placeholder(reg: sl.LocalRegistry) -> None:
    found = reg.sql("SELECT 1 AS x -- $nope\n")
    assert found.params == ()
    found = reg.sql("SELECT /* $nope */ 1 AS x")
    assert found.params == ()


def test_a_dollar_in_a_quoted_identifier_is_not_a_placeholder(reg: sl.LocalRegistry) -> None:
    found = reg.sql('SELECT 1 AS "a $b"')
    assert found.params == ()
    assert found.collect().columns == ["a $b"]


def test_a_placeholder_beside_a_string_is_still_found(reg: sl.LocalRegistry) -> None:
    """The contrast: skipping spans must not swallow the real one after them."""
    found = reg.sql("SELECT 'a$b' AS lit, $v AS value", v=7)
    assert found.params == (7,)
    assert found.collect().to_dicts() == [{"lit": "a$b", "value": 7}]


def test_the_oracle_catches_a_rewriter_that_disagrees_with_duckdb(
    reg: sl.LocalRegistry,
) -> None:
    """The guard itself, driven directly.

    Nothing else can reach it: while the scanner is right the two never
    disagree, so removing the check breaks no test. Handing it a wrong answer
    on purpose is what shows it would catch a future scanner bug — the kind
    that would otherwise corrupt a statement silently.
    """
    conn = reg._require_open()
    with pytest.raises(RawSqlError, match="disagree about this statement"):
        _check_rewrite(conn, "SELECT $a", "SELECT ?", set(), 1)
    with pytest.raises(RawSqlError, match="placeholders, expected"):
        _check_rewrite(conn, "SELECT $a", "SELECT ?, ?", {"a"}, 1)


def test_the_scanner_and_duckdb_agree_on_every_shape(reg: sl.LocalRegistry) -> None:
    """Cross-checked over the constructs the scanner reasons about, rather than
    one at a time — this is what the oracle is protecting."""
    for text in (
        "SELECT 'a$b' AS x, $v AS y",
        'SELECT "a $b" FROM (SELECT $v AS "a $b")',
        "SELECT 1 /* $no */, $v -- $no\n",
        "SELECT 'it''s $no' AS x, $v AS y",
    ):
        prepared = reg.sql(text, v=1)
        assert prepared.params == (1,)


def test_dollar_quoting_is_refused(reg: sl.LocalRegistry) -> None:
    """It makes `$name` ambiguous, and is rare enough to refuse rather than
    guess at."""
    with pytest.raises(RawSqlError, match="dollar-quoted"):
        reg.sql("SELECT $tag$ hello $tag$ AS x")


# ── refusals ──────────────────────────────────────────────────────────────


def test_positional_placeholders_are_refused(reg: sl.LocalRegistry) -> None:
    """The structural compiler emits `?`, and DuckDB will not mix the styles —
    so the hatch takes named only."""
    with pytest.raises(RawSqlError, match="named placeholders only"):
        reg.sql("SELECT * FROM sdmx.artefact WHERE agency_id = ?")
    with pytest.raises(RawSqlError, match="named placeholders only"):
        reg.sql("SELECT * FROM sdmx.artefact WHERE agency_id = $1")


def test_a_placeholder_without_a_value_is_refused(reg: sl.LocalRegistry) -> None:
    with pytest.raises(RawSqlError, match=r"\$a placeholder but no a="):
        reg.sql("SELECT $a")


def test_a_value_without_a_placeholder_is_refused(reg: sl.LocalRegistry) -> None:
    """A typo'd keyword would otherwise be silently ignored."""
    with pytest.raises(RawSqlError, match="no matching"):
        reg.sql("SELECT 1", agency="ESTAT")


def test_several_statements_are_refused(reg: sl.LocalRegistry) -> None:
    with pytest.raises(RawSqlError, match="one statement, got 2"):
        reg.sql("SELECT 1; SELECT 2")


def test_unparseable_sql_is_refused_before_running(reg: sl.LocalRegistry) -> None:
    with pytest.raises(RawSqlError, match="could not parse"):
        reg.sql("SELEKT 1")


def test_an_unknown_table_fails_at_collect_not_at_prepare(reg: sl.LocalRegistry) -> None:
    """Name resolution happens when the statement binds, so this is the
    database's error and is left as such — `sl.schema` is what turns a wrong
    table name into an early failure."""
    prepared = reg.sql("SELECT * FROM sdmx.nope")
    with pytest.raises(Exception, match="nope"):
        prepared.collect()


def test_a_closed_registry_refuses(reg: sl.LocalRegistry) -> None:
    reg.close()
    with pytest.raises(RuntimeError, match="is not open"):
        reg.sql("SELECT 1")


# ── schema constants ──────────────────────────────────────────────────────


def test_a_table_stringifies_to_its_qualified_name() -> None:
    assert str(sl.schema.artefact) == "sdmx.artefact"
    assert sl.schema.artefact.name == "sdmx.artefact"


def test_a_column_is_fully_qualified() -> None:
    assert sl.schema.dsd_component.codelist_urn == "sdmx.dsd_component.codelist_urn"
    assert sl.schema.code.code_id == "sdmx.code.code_id"


def test_an_unknown_column_fails_at_use(reg: sl.LocalRegistry) -> None:
    """The point of the constants. A hardcoded string rots silently; this
    names what is actually there."""
    with pytest.raises(AttributeError, match=r"sdmx\.artefact has no column 'agency'"):
        _ = sl.schema.artefact.agency


def test_an_unknown_table_fails_at_use() -> None:
    with pytest.raises(AttributeError, match=r"there is no sdmx\.dataflows"):
        _ = sl.schema.dataflows


def test_the_constants_come_from_the_ddl_not_a_copy() -> None:
    """Reflected from `create_schema`, so the two cannot drift. Checked by
    naming a column that only exists because the DDL says so."""
    assert "artefact_type" in sl.schema.artefact.columns
    assert set(dir(sl.schema.code)) == set(sl.schema.code.columns)
    assert "artefact" in dir(sl.schema)


def test_the_constants_build_a_working_query(reg: sl.LocalRegistry) -> None:
    """End to end: the hatch's two halves used together, which is the shape the
    design proposes. It ends at `collect()` — the hatch is a terminal."""
    found = reg.sql(
        f"SELECT {sl.schema.artefact.urn} AS urn FROM {sl.schema.artefact} WHERE {sl.schema.artefact.agency_id} = $a",
        a="OECD",
    )
    assert found.collect()["urn"].to_list() == ["urn:sdmx:org.sdmx.infomodel.codelist.Codelist=OECD:CL_B(1.0)"]
