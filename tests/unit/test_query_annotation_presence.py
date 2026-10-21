"""Annotation presence, and the question each annotation predicate answers (#292).

Every annotation predicate compiles to ``EXISTS(… annotation_type = ? AND
<condition>)``, so all of them mean "an annotation of this type exists **and**
its value …". That is deliberate and stays. What was missing is the predicate
that carries no condition — and without it, "has no annotation of this type"
was unreachable by any spelling.

The name that misleads is `is_null`. On `annotations["note"]` it asks whether a
note exists whose *title* is missing, which is a legal SDMX annotation: the
type may carry the meaning and the title be absent. A reader will take it for
"has no note", and get a different set.

So the fixture below has one code for each case, and that separation is the
whole point of the file — a fixture where every annotated code also has a title
cannot tell the two readings apart:

| code | annotation |
| ---- | ---------- |
| `A`  | note, title `"x"` |
| `B`  | note, no title |
| `C`  | no annotation at all |
| `D`  | note, no title, French text |

`C` is the row every pre-#292 spelling missed.
"""

import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.model.istring import InternationalString
from sdmxlib.query.accessors import q
from tests.support.factories import code_urn, concept_ref
from tests.support.narrow import present

pytestmark = pytest.mark.unit

#: Named because the spelling is long enough that inlining it three times
#: pushes past the line limit and buries what each assertion is contrasting.
_note_in_french = q.code.annotations.any(q.annotation.type == "note", q.annotation.text["fr"].is_not_null())
_note_in_english = q.code.annotations.any(q.annotation.type == "note", q.annotation.text["en"].is_not_null())
_note_titled_x = q.code.annotations.any(q.annotation.type == "note", q.annotation.title == "x")


@pytest.fixture
def codes():
    cl = Codelist(
        id="CL",
        maintainer="AG",
        version="1.0",
        name={"en": "Codes"},
        codes=[
            Code(id="A", name={"en": "a"}, annotations=Annotations([Annotation(type="note", title="x")])),
            Code(id="B", name={"en": "b"}, annotations=Annotations([Annotation(type="note")])),
            Code(id="C", name={"en": "c"}),
            Code(
                id="D",
                name={"en": "d"},
                annotations=Annotations([Annotation(type="note", text=InternationalString(fr="bonjour"))]),
            ),
        ],
    )
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        yield present(reg.get(Codelist.urn_for(agency="AG", id="CL", version="1.0"))).codes


def _ids(query, predicate) -> list[str]:
    return sorted(query.filter(predicate).ids())


# ── The question each predicate answers ──────────────────────────────────────


def test_the_four_readings_are_four_different_sets(codes) -> None:
    """Laid out together, because the distinction only shows in the contrast.

    Read as a table, this is the documentation: three of the four are scoped to
    an annotation that exists *and* has some property of its title, and only
    `exists` stops at the type.
    """
    assert _ids(codes, (q.code.annotations.type == "note")) == ["A", "B", "D"]
    assert _ids(codes, q.code.annotations.any(q.annotation.type == "note", q.annotation.title.is_not_null())) == ["A"]
    assert _ids(codes, q.code.annotations.any(q.annotation.type == "note", q.annotation.title.is_null())) == ["B", "D"]
    assert _ids(codes, _note_titled_x) == ["A"]


def test_is_null_is_a_missing_title_not_a_missing_annotation(codes) -> None:
    """The trap, stated as an assertion rather than left in prose.

    `C` has no annotation at all and is in *neither* `is_null` nor
    `is_not_null` — which is the tell that both are existence-scoped. A reader
    who took `is_null` for "has no note" would expect `C` and get `B`, `D`.
    """
    assert "C" not in _ids(codes, q.code.annotations.any(q.annotation.type == "note", q.annotation.title.is_null()))
    assert "C" not in _ids(codes, q.code.annotations.any(q.annotation.type == "note", q.annotation.title.is_not_null()))
    assert _ids(codes, ~(q.code.annotations.type == "note")) == ["C"]


def test_exists_and_its_negation_partition_the_rows(codes) -> None:
    """`NOT EXISTS` is total, so this is a real complement.

    Worth its own test because it is the exception in this DSL. `~` over a
    nullable *column* leaves the NULL row in neither branch (#262) — the
    reason "negation is not set complement" is documented on `__invert__`.
    An existence test has no third value, so the law is recovered, and that is
    what makes `~exists()` the supported spelling of absence rather than a
    workaround.
    """
    present = _ids(codes, (q.code.annotations.type == "note"))
    absent = _ids(codes, ~(q.code.annotations.type == "note"))
    assert sorted(present + absent) == sorted(codes.ids())
    assert not set(present) & set(absent)


# ── The localised-text variant ───────────────────────────────────────────────


def test_exists_on_a_language_asks_about_that_language(codes) -> None:
    """``annotations[type][lang].exists()`` — carries text in this language.

    A distinct question from `annotations[type].exists()`, and one no
    comparison can express: asking whether a translation is present at all
    means not naming a value to compare it to. `D` is the only code with French
    text; `A` and `B` have notes but no text in any language.
    """
    assert _ids(codes, _note_in_french) == ["D"]
    assert _ids(codes, _note_in_english) == []
    assert _ids(codes, ~_note_in_french) == [
        "A",
        "B",
        "C",
    ]


def test_missing_translations_of_an_annotation_that_exists(codes) -> None:
    """The data-quality question the two combine to answer.

    "Has a note but no French text on it" needs both halves and a `&`, which
    is why #292 waited on #262 — before the combinators there was no way to
    write the conjunction even with both predicates in hand.
    """
    query = (q.code.annotations.type == "note") & ~q.code.annotations.any(
        q.annotation.type == "note", q.annotation.text["fr"].is_not_null()
    )
    assert _ids(codes, query) == ["A", "B"]


# ── Same node, every relation ────────────────────────────────────────────────


def test_the_predicate_works_on_the_hierarchy_and_artefact_relations() -> None:
    """One shared node, compiled from `RowKind.urn_column`, so no per-DSL wiring.

    Same construction as `And` / `Or` / `Not`: the only relation-specific fact
    is which column holds the row's URN, and `CompileContext` already carries
    the `RowKind` that knows it. A relation added downstream gets this for free
    provided it has a URN.
    """
    h = Hierarchy(
        id="H",
        maintainer="AG",
        version="1.0",
        name={"en": "H"},
        tree=[
            HierarchicalCode(id="N1", code=code_urn("CL", "N1"), annotations=Annotations([Annotation(type="tier")])),
            HierarchicalCode(id="N2", code=code_urn("CL", "N2")),
        ],
    )
    dsd = DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name={"en": "S"},
        dimensions=[Dimension(id="FREQ", concept=concept_ref("FREQ"))],
        annotations=Annotations([Annotation(type="tier", title="core")]),
    )
    plain = Codelist(
        id="CL_PLAIN", maintainer="AG", version="1.0", name={"en": "p"}, codes=[Code(id="A", name={"en": "a"})]
    )

    with LocalRegistry(":memory:") as reg:
        reg.add(h)
        reg.add(dsd)
        reg.add(plain)

        nodes = present(reg.get(Hierarchy.urn_for(agency="AG", id="H", version="1.0"))).nodes
        assert _ids(nodes, (q.hcode.annotations.type == "tier")) == ["N1"]
        assert _ids(nodes, ~(q.hcode.annotations.type == "tier")) == ["N2"]

        # `H` is on the spine too — a hierarchy is a maintainable artefact, so
        # a cross-type sweep sees it alongside the DSD and the codelist. Since
        # #306 retired `ArtefactQuery` that sweep is the path language, where
        # presence is a fan-out reach rather than a bespoke predicate.
        spine = reg.query(q.artefact)
        assert spine.filter(q.artefact.annotations.type == "tier").ids() == ["DSD"]
        assert sorted(spine.filter(~(q.artefact.annotations.type == "tier")).ids()) == ["CL_PLAIN", "H"]


# ── The SQL, because the shape is the claim ──────────────────────────────────


def test_presence_emits_no_condition_on_the_value(codes) -> None:
    """No trailing ``AND`` — the subquery tests the type and stops.

    Not cosmetic. A condition that happens to be always-true (``AND TRUE``, or
    ``AND title IS NOT DISTINCT FROM title``) would select the same rows and
    read as though a value were being examined. The emitted SQL is the clearest
    statement of which question this predicate asks, and someone reading a slow
    query is who most needs it.
    """
    sql, params = codes.filter(q.code.annotations.type == "note").to_sql()
    exists_clause = sql[sql.index("EXISTS") : sql.index("ORDER BY")]
    assert exists_clause.rstrip().rstrip(")").endswith("annotation_type = ?"), exists_clause
    assert "title" not in exists_clause
    assert "note" in params


def test_the_language_variant_reaches_the_text_table_and_stops_there(codes) -> None:
    """The text lookup carries the language and nothing else.

    Under `CodeQuery` this was a ``JOIN sdmx.annotation_text``, and the join
    *was* the language test — an annotation with no row for the language had
    nothing to join to. The path language reaches side-table text the same way
    everywhere, as a correlated scalar subquery, so presence is
    `text[lang].is_not_null()` and the shape is shared with `q.dsd.name["en"]`
    rather than being annotation-specific.

    What is asserted is that no `field` condition appears. `annotation_text`
    holds one field and has no such column, so an emitted ``AND lt.field = ?``
    would not merely be redundant — it would not run.
    """
    predicate = _note_in_french
    sql, params = codes.filter(predicate).to_sql()
    exists_clause = sql[sql.index("EXISTS") : sql.index("ORDER BY")]
    assert "sdmx.annotation_text" in exists_clause
    assert "lt.field" not in exists_clause
    assert "fr" in params
