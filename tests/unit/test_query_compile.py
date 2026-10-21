"""Compiled predicates run, and return the rows the design says they should.

Structural assertions about emitted SQL are cheap and weak. What matters is
that the statement executes and answers the question, so every test here loads
a fixture and compares row sets. The fixtures are the ones the design documents
argue from, so a regression here is visible as a disagreement with the prose.
"""

from __future__ import annotations

import duckdb
import pytest

from sdmxlib.query._compile import compile_select
from sdmxlib.query._expr import AnyCols, AnyPred, Quantified
from sdmxlib.query._joins import PathError
from sdmxlib.query.accessors import q
from sdmxlib.storage.schema import create_schema

pytestmark = pytest.mark.unit


def _artefact(urn: str, kind: str, agency: str, rid: str) -> tuple[str, ...]:
    return (urn, kind, agency, rid, "1.0")


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    """Two DSDs whose components differ in exactly the ways the design turns on.

    `CPI` has one dimension carrying *both* REF_AREA and CL_GEO. `TRADE` has
    two dimensions carrying one each. That is the fixture the conjunction
    finding used, and it is what makes "same child" and "independent children"
    give different answers.
    """
    conn = duckdb.connect()
    create_schema(conn)
    conn.executemany(
        "INSERT INTO sdmx.artefact (urn, artefact_type, agency_id, resource_id, version) VALUES (?, ?, ?, ?, ?)",
        [
            _artefact("u:dsd:CPI", "DataStructure", "ESTAT", "CPI"),
            _artefact("u:dsd:TRADE", "DataStructure", "ESTAT", "TRADE"),
            _artefact("u:dsd:EMPTY", "DataStructure", "OECD", "EMPTY"),
            _artefact("u:cl:GEO", "Codelist", "ESTAT", "CL_GEO"),
            _artefact("u:cl:OTHER", "Codelist", "ESTAT", "CL_OTHER"),
        ],
    )
    conn.executemany(
        "INSERT INTO sdmx.concept (concept_urn, scheme_urn, concept_id) VALUES (?, ?, ?)",
        [("u:con:REF_AREA", "u:cs:X", "REF_AREA"), ("u:con:OTHER", "u:cs:X", "OTHER")],
    )
    conn.executemany(
        "INSERT INTO sdmx.dsd_component"
        " (dsd_urn, component_id, component_type, concept_urn, codelist_urn)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            # CPI: ONE dimension with both
            ("u:dsd:CPI", "REF_AREA", "Dimension", "u:con:REF_AREA", "u:cl:GEO"),
            # TRADE: two dimensions, one each
            ("u:dsd:TRADE", "REF_AREA", "Dimension", "u:con:REF_AREA", "u:cl:OTHER"),
            ("u:dsd:TRADE", "X", "Dimension", "u:con:OTHER", "u:cl:GEO"),
            # an attribute, so facets can be told apart from the raw edge
            ("u:dsd:CPI", "OBS_STATUS", "Attribute", "u:con:OTHER", "u:cl:OTHER"),
        ],
    )
    return conn


def _run(conn: duckdb.DuckDBPyConnection, root: AnyCols, preds: list[AnyPred]) -> list[str]:
    compiled = compile_select(root, preds, columns="s0.resource_id")
    rows = conn.execute(compiled.sql, list(compiled.params)).fetchall()
    return sorted({str(r[0]) for r in rows})


# ── flat paths ────────────────────────────────────────────────────────────


def test_a_to_one_path_filters_without_changing_the_row_count(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    compiled = compile_select(q.component, [q.component.dsd.agency == "ESTAT"], columns="s0.component_id")
    rows = conn.execute(compiled.sql, list(compiled.params)).fetchall()
    assert len(rows) == 4


def test_the_two_routes_to_a_codelist_answer_differently(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """A component's own representation versus its concept's default.

    The concepts here carry no default representation, so the second route
    matches nothing — which is the point: they are different questions.
    """
    own = compile_select(q.component, [q.component.codelist.id == "CL_GEO"], columns="s0.component_id")
    default = compile_select(
        q.component,
        [q.component.concept.codelist.id == "CL_GEO"],
        columns="s0.component_id",
    )
    assert conn.execute(own.sql, list(own.params)).fetchall()
    assert not conn.execute(default.sql, list(default.params)).fetchall()


# ── fan-out: the distinction the design turns on ──────────────────────────


def test_independent_children_and_same_child_differ(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """The hole the hostile review found, and the reason `.any()` exists.

    Two dot-through predicates ask "some dimension has the concept **and** some
    dimension has the codelist". One `.any()` asks "one dimension has both".
    """
    independent = _run(
        conn,
        q.dsd,
        [
            q.dsd.dimensions.concept.id == "REF_AREA",
            q.dsd.dimensions.codelist.id == "CL_GEO",
        ],
    )
    same_child = _run(
        conn,
        q.dsd,
        [
            q.dsd.dimensions.any(
                q.component.concept.id == "REF_AREA",
                q.component.codelist.id == "CL_GEO",
            )
        ],
    )
    assert independent == ["CPI", "TRADE"]
    assert same_child == ["CPI"]
    assert independent != same_child


def test_dot_through_preserves_the_outer_row_count(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """`EXISTS`, not a join — CPI has two components and appears once."""
    compiled = compile_select(q.dsd, [q.dsd.components.dsd.agency == "ESTAT"], columns="s0.resource_id")
    rows = conn.execute(compiled.sql, list(compiled.params)).fetchall()
    assert len(rows) == len({r[0] for r in rows})


def test_two_fan_outs_do_not_multiply_rows(conn: duckdb.DuckDBPyConnection) -> None:
    compiled = compile_select(
        q.dsd,
        [
            q.dsd.dimensions.concept.id == "REF_AREA",
            q.dsd.attributes.concept.id == "OTHER",
        ],
        columns="s0.resource_id",
    )
    rows = conn.execute(compiled.sql, list(compiled.params)).fetchall()
    assert [str(r[0]) for r in rows] == ["CPI"]


# ── facets ────────────────────────────────────────────────────────────────


def test_a_facet_filters_the_child(conn: duckdb.DuckDBPyConnection) -> None:
    """`attributes` must not match a dimension carrying the same concept."""
    assert _run(conn, q.dsd, [q.dsd.attributes.concept.id == "OTHER"]) == ["CPI"]
    assert _run(conn, q.dsd, [q.dsd.dimensions.concept.id == "OTHER"]) == ["TRADE"]


def test_the_unfiltered_edge_sees_every_component(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    assert _run(conn, q.dsd, [q.dsd.components.concept.id == "OTHER"]) == [
        "CPI",
        "TRADE",
    ]


# ── every() and three-valued logic ────────────────────────────────────────


def test_every_is_null_safe(conn: duckdb.DuckDBPyConnection) -> None:
    """A child whose column is NULL must count as a violation.

    Compiled with `NOT (...)` instead of `IS NOT TRUE`, `NOT(NULL)` is NULL,
    the row is not counted as a violation, and a non-enumerated dimension
    silently satisfies the universal. `TRADE` has a dimension whose codelist is
    `CL_OTHER`, so it must not qualify; adding one with no codelist at all must
    not qualify either.
    """
    conn.execute(
        "INSERT INTO sdmx.dsd_component (dsd_urn, component_id, component_type)"
        " VALUES ('u:dsd:CPI', 'NOCL', 'Dimension')"
    )
    assert _run(conn, q.dsd, [q.dsd.dimensions.every(q.component.codelist.id == "CL_GEO")]) == ["EMPTY"]


def test_every_is_vacuously_true_with_no_children(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Standard logic, standard surprise — `EMPTY` has no dimensions at all."""
    assert "EMPTY" in _run(conn, q.dsd, [q.dsd.dimensions.every(q.component.concept.id == "REF_AREA")])


def test_every_holds_when_all_children_match(conn: duckdb.DuckDBPyConnection) -> None:
    assert "CPI" in _run(conn, q.dsd, [q.dsd.dimensions.every(q.component.concept.id == "REF_AREA")])
    assert "TRADE" not in _run(conn, q.dsd, [q.dsd.dimensions.every(q.component.concept.id == "REF_AREA")])


# ── operators ─────────────────────────────────────────────────────────────


def test_composition_and_negation(conn: duckdb.DuckDBPyConnection) -> None:
    both = q.component.dsd.agency == "ESTAT"
    named = q.component.id == "REF_AREA"
    compiled = compile_select(q.component, [both & named], columns="s0.component_id")
    rows = conn.execute(compiled.sql, list(compiled.params)).fetchall()
    assert sorted({str(r[0]) for r in rows}) == ["REF_AREA"]

    compiled = compile_select(q.component, [~named], columns="s0.component_id")
    rows = conn.execute(compiled.sql, list(compiled.params)).fetchall()
    assert "REF_AREA" not in {str(r[0]) for r in rows}


def test_is_in_and_the_empty_set(conn: duckdb.DuckDBPyConnection) -> None:
    compiled = compile_select(q.component, [q.component.id.is_in(["REF_AREA", "X"])], columns="s0.component_id")
    rows = conn.execute(compiled.sql, list(compiled.params)).fetchall()
    assert sorted({str(r[0]) for r in rows}) == ["REF_AREA", "X"]

    empty = compile_select(q.component, [q.component.id.is_in([])], columns="s0.component_id")
    assert not conn.execute(empty.sql, list(empty.params)).fetchall()


def test_null_checks(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        "INSERT INTO sdmx.dsd_component (dsd_urn, component_id, component_type)"
        " VALUES ('u:dsd:CPI', 'NOCL', 'Dimension')"
    )
    compiled = compile_select(q.component, [q.component.codelist.id.is_null()], columns="s0.component_id")
    rows = conn.execute(compiled.sql, list(compiled.params)).fetchall()
    assert "NOCL" in {str(r[0]) for r in rows}


# ── values are bound, never interpolated ──────────────────────────────────


def test_values_are_parameterised(conn: duckdb.DuckDBPyConnection) -> None:
    hostile = "'; DROP TABLE sdmx.artefact; --"
    compiled = compile_select(q.component, [q.component.id == hostile], columns="1")
    assert hostile not in compiled.sql
    assert compiled.params == [hostile]
    conn.execute(compiled.sql, list(compiled.params)).fetchall()
    assert conn.execute("SELECT count(*) FROM sdmx.artefact").fetchone()


# ── refusals ──────────────────────────────────────────────────────────────


def test_quantifying_a_to_one_edge_is_refused() -> None:
    """Nothing to quantify over when the path cannot fan out.

    Unreachable through the accessors — `.any()` lives only on a `FanOut` — so
    the guard is exercised by building the node directly. It exists because the
    compiler will also be the entry point for an IR, where the invariant is not
    carried by the type.
    """
    bogus = Quantified("any", ("dsd",), (q.component.id == "FREQ",))
    with pytest.raises(PathError, match=r"nothing to quantify over"):
        compile_select(q.component, [bogus])


# ── the root discriminator ────────────────────────────────────────────────


def test_a_root_only_returns_its_own_artefact_type(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """`q.codelist` and `q.dsd` are the same relation and differ by this.

    Without the discriminator, a codelist query returns every artefact — and
    `every()` being vacuously true over them would make the answer look
    plausible.
    """
    assert _run(conn, q.codelist, []) == ["CL_GEO", "CL_OTHER"]
    assert _run(conn, q.dsd, []) == ["CPI", "EMPTY", "TRADE"]


def test_the_untyped_artefact_root_has_no_discriminator(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """`reg.query(q.artefact)` is the polymorphic entry, so it sees them all."""
    assert _run(conn, q.artefact, []) == ["CL_GEO", "CL_OTHER", "CPI", "EMPTY", "TRADE"]


def test_every_composes_across_nested_to_many_edges() -> None:
    """Two to-many hops inside `every` — which the design doc said was refused.

    `docs/design/query-language.md` once carried this as a live limitation:
    "`.every()` refuses on two or more to-many hops, so a class of universals
    stays unexpressible". It was written before the compiler existed, and the
    compiler that shipped solved it. The page now states that `every` composes
    across nested to-many edges, naming this test as the guard — without it the
    old line could quietly become true again.

    The fixture discriminates. `ALL` has the annotation on every code and `SOME`
    on one of two, so a universal that had degenerated into an existential —
    the likely regression, since `every` compiles as a negated `EXISTS` over
    violations — would return both.
    """
    from sdmxlib.local import LocalRegistry
    from sdmxlib.model.annotations import Annotation, Annotations
    from sdmxlib.model.codelist import Code, Codelist

    def annotated(code_id: str, kind: str) -> Code:
        return Code(
            id=code_id,
            name={"en": code_id},
            annotations=Annotations([Annotation(type=kind, title=kind)]),
        )

    with LocalRegistry(":memory:") as reg:
        reg.add(
            Codelist(
                id="ALL",
                maintainer="AG",
                version="1.0",
                name={"en": "all"},
                codes=[annotated("A", "note"), annotated("B", "note")],
            )
        )
        reg.add(
            Codelist(
                id="SOME",
                maintainer="AG",
                version="1.0",
                name={"en": "some"},
                codes=[annotated("A", "note"), annotated("B", "other")],
            )
        )

        universal = q.codelist.codes.every(q.code.annotations.type == "note")
        assert reg.query(q.codelist).filter(universal).ids() == ["ALL"]

        # And the shapes the doc also claimed were out of reach.
        for deeper in (
            q.codelist.codes.every(q.code.children.annotations.type == "x"),
            q.codelist.codes.every(q.code.children.every(q.code.id != "X")),
            q.codelist.codes.any(q.code.children.every(q.code.id != "X")),
        ):
            assert reg.query(q.codelist).filter(deeper).to_sql().sql
