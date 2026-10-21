"""Boolean combinators on the query DSLs (#262).

Until now the DSLs had conjunction only, and only implicitly: ``filter()`` ANDs
its arguments and chained ``filter()`` calls AND together. There was no route to
a disjunction at all — "FREQ or TIME_PERIOD" could not be written, and callers
dropped to raw SQL for it. `&`, `|` and `~` on `Predicate` close that.

The nodes are shared. #267 unified the four DSLs onto one `Predicate` hierarchy
and one `CompileContext`, so `And` / `Or` / `Not` are defined once in
`_query_common` and each compiler registers three emitters that recurse through
*its own* dispatch table. That is what the cross-DSL test below is checking: not
that boolean logic works, but that all four are actually wired, because the two
that declare `_EMITTERS` after `_emit_predicate` and the two that declare it
before need the wiring in different places and a missed one would only fail at
runtime.

What is asserted here is what the differential properties in
`test_properties_code_query` and `test_properties_hierarchy_query` cannot be:

- **The NULL divergence.** The oracles are two-valued, so they can model ``~``
  only over fields that cannot be NULL. The case where SQL and a set-complement
  reading disagree is pinned here as an example instead, because modelling it
  would mean maintaining a second three-valued oracle for one operator.
- **The precedence footgun**, which is a `TypeError` rather than a row set.
- **`ItemPredicate`**, which is Python callables and has no SQL to compare to.
"""

import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.expr import item
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, Level
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.ref import artefact_urn
from sdmxlib.query._expr import BoolOp, Pred
from sdmxlib.query.accessors import CodeRow, q
from tests.support.factories import code_urn, concept_ref
from tests.support.narrow import present

pytestmark = pytest.mark.unit


def _parts(pred: Pred[CodeRow]) -> int:
    """How many operands a boolean tree flattened to.

    `parts` is on `BoolOp`, not on the `Pred` base every operator returns, so
    the narrowing lives here rather than at each assertion.
    """
    assert isinstance(pred, BoolOp)
    return len(pred.parts)


#: Named because the spelling is long enough that inlining it three times
#: pushes past the line limit and buries what each assertion is contrasting.
_note_in_french = q.code.annotations.any(q.annotation.type == "note", q.annotation.text["fr"].is_not_null())
_note_in_english = q.code.annotations.any(q.annotation.type == "note", q.annotation.text["en"].is_not_null())
_note_titled_x = q.code.annotations.any(q.annotation.type == "note", q.annotation.title == "x")

_LEVEL = Level(id="Region", name={"en": "Region"})

#: Gives one dimension an enumerated representation and leaves the other free
#: text, so `is_enumerated` discriminates.
_FREQ_CODES = Codelist(
    id="CL_FREQ", maintainer="AG", version="1.0", name={"en": "Freq"}, codes=[Code(id="A", name={"en": "Annual"})]
)


def _codelist() -> Codelist:
    """Three codes, two of them children — so ``parent_id`` has a NULL and a value."""
    return Codelist(
        id="CL",
        maintainer="AG",
        version="1.0",
        name={"en": "Codes"},
        codes=[
            Code(id="A", name={"en": "Alpha"}, annotations=Annotations([Annotation(type="note", title="x")])),
            Code(id="B", name={"en": "Beta"}, parent_id="A"),
            Code(id="C", name={"en": "Gamma"}, parent_id="A"),
        ],
    )


@pytest.fixture
def codes():
    with LocalRegistry(":memory:") as reg:
        reg.add(_codelist())
        yield present(reg.get(Codelist.urn_for(agency="AG", id="CL", version="1.0"))).codes


def _ids(query, predicate) -> list[str]:
    return sorted(query.filter(predicate).ids())


# ── The operation that had no route at all ───────────────────────────────────


def test_or_selects_the_union(codes) -> None:
    """The motivating case: two values of one field, which ``filter`` cannot AND."""
    assert _ids(codes, (q.code.id == "A") | (q.code.id == "C")) == ["A", "C"]


def test_or_composes_across_different_fields(codes) -> None:
    """Disjunction is not restricted to one field — that would be `is_in`."""
    assert _ids(codes, (q.code.id == "A") | q.code.name["en"].str.contains("Gam")) == ["A", "C"]


def test_or_composes_with_an_annotation_predicate(codes) -> None:
    """Only true because #254 made annotations a correlated ``EXISTS``.

    While they compiled to an inner JOIN, a code with no annotation was gone
    before the ``WHERE`` clause ran, so it could not satisfy the *other* branch
    either and the disjunction returned too few rows. ``C`` has no annotation
    and must still come back on the strength of the right-hand side.
    """
    assert _ids(codes, (_note_titled_x) | (q.code.id == "C")) == ["A", "C"]


def test_and_narrows(codes) -> None:
    assert _ids(codes, (q.code.parent.id == "A") & q.code.name["en"].str.contains("Bet")) == ["B"]


def test_nesting_respects_the_parentheses(codes) -> None:
    """``(a | b) & c`` and ``a | (b & c)`` are different queries, not the same one."""
    left = (q.code.id == "A") | (q.code.id == "B")
    assert _ids(codes, (left) & (q.code.parent.id == "A")) == ["B"]
    assert _ids(codes, (q.code.id == "A") | ((q.code.id == "B") & (q.code.parent.id == "A"))) == ["A", "B"]


# ── NULL: the divergence the oracles cannot model ────────────────────────────


def test_negation_is_not_set_complement_over_a_nullable_field(codes) -> None:
    """``~p`` drops rows where the column is NULL, exactly as ``!=`` does.

    ``A`` is a root, so its ``parent_id`` is NULL and ``NOT (parent_id = 'A')``
    evaluates to NULL rather than true — the row is not selected. So ``p`` and
    ``~p`` between them return two of the three codes, not all three, and
    excluded middle fails.

    This is standard SQL and almost certainly the right choice: the alternative
    is an anti-join, which is expensive and would surprise anyone who reads the
    generated query. But it is the most likely source of a "where did my rows
    go" report, so it is pinned rather than left to be discovered, and
    `Predicate.__invert__` says so in the docstring a user actually reads.
    """
    assert _ids(codes, q.code.parent.id == "A") == ["B", "C"]
    assert _ids(codes, ~(q.code.parent.id == "A")) == []
    assert _ids(codes, (q.code.parent.id == "A") | ~(q.code.parent.id == "A")) == ["B", "C"]

    # `~` and `!=` agree, which is the coherence that matters most: a user who
    # reaches for one after the other must not get two different answers.
    assert _ids(codes, ~(q.code.parent.id == "A")) == _ids(codes, q.code.parent.id != "A")


def test_negation_of_an_annotation_predicate_does_complement(codes) -> None:
    """The contrast that makes the rule precise: it is NULL, not ``~``, that leaks.

    An annotation predicate compiles to a correlated ``EXISTS`` (#254), and
    ``NOT EXISTS`` is *total* — there is no third value for an existence test,
    so every row lands in exactly one of the two branches. So ``~`` does
    complement here while it does not over ``parent_id``, and the difference is
    entirely about whether the operand can be NULL.

    Worth pinning because the natural summary — "negation in this DSL is not
    complement" — is too strong, and a user who believes it will write an
    unnecessary second query.
    """
    with_note = _ids(codes, _note_titled_x)
    without = _ids(codes, ~(_note_titled_x))
    assert with_note == ["A"]
    assert without == ["B", "C"]
    assert sorted([*with_note, *without]) == sorted(codes.ids())


def test_excluded_middle_holds_over_a_non_nullable_field(codes) -> None:
    """The contrast case. ``id`` is always populated, so the law is recovered.

    Without this the test above reads as "negation is broken" rather than
    "negation is three-valued".
    """
    assert _ids(codes, (q.code.id == "A") | ~(q.code.id == "A")) == ["A", "B", "C"]
    assert _ids(codes, (q.code.id == "A") & ~(q.code.id == "A")) == []


def test_double_negation_folds_rather_than_nesting(codes) -> None:
    """``~~p`` is ``p`` — an involution even in three-valued logic.

    Folded at construction rather than left to the compiler, so the emitted SQL
    stays readable and the canonical key of a double negation is the key of
    what it negates. The identity is asserted on the tree *and* on the rows,
    because a fold that changed which rows came back would be a bug, not an
    optimisation.

    Asserted through `canonical_key` since #306: `==` on a predicate raises,
    because attrs' generated equality never worked on these nodes and
    `canonical_key` is what query identity means here.
    """
    from sdmxlib.query import _expr

    pred = q.code.parent.id == "A"
    assert not isinstance(~~pred, _expr.Not)
    assert codes.filter(~~pred).canonical_key() == codes.filter(pred).canonical_key()
    assert _ids(codes, ~~pred) == _ids(codes, pred)


# ── Shape of the tree ────────────────────────────────────────────────────────


def test_same_kind_operands_flatten(codes) -> None:
    """``a & b & c`` is one three-operand node, not two nested two-operand ones.

    Readability of a failing query is the reason — ``(a AND b AND c)`` rather
    than ``((a AND b) AND c)`` — and it makes two structurally equal trees
    compare equal, which several assertions here depend on.
    """
    a, b, c = q.code.id == "A", q.code.id == "B", q.code.id == "C"
    assert _parts(a & b & c) == 3
    assert _parts(a | b | c) == 3
    # A mixed tree does not flatten: `&` inside `|` has to stay a subtree or
    # the query means something else entirely.
    assert _parts((a & b) | c) == 2


def test_a_non_predicate_operand_names_the_precedence_trap(codes) -> None:
    """Python binds ``&`` and ``|`` tighter than ``==``, so the natural spelling
    misparses. The error says which parentheses are missing.

    ``q.code.id == "A" | q.code.id == "B"`` reads as ``q.code.id == ("A" | q.code.id)``,
    which without this check surfaces as an opaque failure inside `str.__or__`.
    polars and pandas both have the same hazard and both document it; this
    reports it at the point of the mistake instead.
    """
    # The spelling people actually write. Note where it lands: the misparse is
    # ``q.code.id == ("A" | q.code.id)``, so Python evaluates ``str.__or__(ref)``,
    # gets `NotImplemented`, and falls through to ``__ror__`` *on the field
    # ref* — `Predicate.__or__` is never reached. Guarding only the predicate
    # side left this case reporting "unsupported operand type(s) for |: 'str'
    # and '_IdRef'", which names two private types and no fix.
    with pytest.raises(TypeError, match="parenthesise each comparison"):
        _ = q.code.id == "A" | q.code.id == "B"
    with pytest.raises(TypeError, match="parenthesise each comparison"):
        _ = q.code.depth == 1 & q.code.depth == 2

    # And the predicate-side arm, for an operand that is genuinely not one.
    # Suppressed because the refusal is the subject: the operand has to be
    # written wrong for the runtime message to be asserted at all.
    with pytest.raises(TypeError, match="parenthesise each comparison"):
        _ = (q.code.id == "A") | "B"  # pyrefly: ignore[unsupported-operation]
    with pytest.raises(TypeError, match="parenthesise each comparison"):
        _ = (q.code.id == "A") & "B"  # pyrefly: ignore[unsupported-operation]


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: q.hcode.level.id == "PR" | q.hcode.id == "N1", id="hierarchy"),
    ],
)
def test_the_precedence_guard_covers_every_dsl(build) -> None:
    """The reflected arm lives on the shared `FieldRef`, so both inherit it.

    Parametrised rather than asserted once because the guard is only useful if
    it is everywhere — a DSL whose refs missed it would report the raw Python
    message, which is the failure mode this exists to remove.
    """
    with pytest.raises(TypeError, match="parenthesise each comparison"):
        build()


# `test_a_foreign_predicate_composes` and
# `test_a_foreign_predicate_asking_for_a_join_fails_loudly` went with the
# `Predicate` protocol (#306). Both were about a *duck-typed* operand — an
# object exposing `to_sql(ctx)` that the DSL had never seen — and the path
# language has no such seam: `filter()` takes `Pred[R]`, checked statically by
# row kind and at runtime by `isinstance`, so a foreign predicate is a checker
# error rather than a runtime refusal. The `ctx.join_sqls` hazard the second
# test pinned cannot arise either, because a foreign object never reaches a
# compiler to request a join.
#
# `test_aggregate_restrictions_are_deliberately_not_combinable` went with
# `AggPredicate`, and the answer it pinned reversed. An aggregate comparison is
# an ordinary `Pred` now — `AggCompare` — because the compiler emits it as a
# correlated subquery in the `WHERE` rather than as `HAVING` over a group, so
# `(children.count() > 1) | (ancestors.count() > 2)` is valid SQL and composes
# like any other predicate. The test below covers it.


def test_aggregate_comparisons_compose_like_any_other_predicate(codes) -> None:
    """The reversal of what `AggPredicate` pinned, asserted rather than assumed.

    Under `CodeQuery` an aggregate restriction compiled to `HAVING`, so it
    could not be OR-ed with a row predicate and was given its own
    non-combinable type to say so. The path language compiles it to a
    correlated subquery, which is an ordinary scalar in the `WHERE` — so the
    restriction is gone, and this states that it is gone deliberately.
    """
    from sdmxlib.query import _expr

    childless = q.code.children.count() == 0
    assert isinstance(childless, _expr.AnyPred)
    assert isinstance(childless | (q.code.id == "A"), _expr.AnyPred)
    assert _ids(codes, childless) == ["B", "C"]
    assert _ids(codes, childless & (q.code.id == "B")) == ["B"]


def test_the_combinators_nest_inside_an_any_subquery(codes) -> None:
    """``.any(a | b)`` — broken on `main` when #262 shipped, fixed in #292.

    `.any(...)` compiled its sub-predicate through the module's `_EMITTERS`
    table rather than through ``pred.to_sql(ctx)``. That is the same seam
    mistake #262 fixed in `_where` and did not look for one level in, so the
    combinators worked in a top-level ``filter()`` and raised
    `NotImplementedError` inside a subquery — as did any predicate from a
    downstream library, which had never worked there.

    Nothing caught it because every existing `.any(...)` property draws a
    single leaf predicate. Found by hand, trying to nest `exists()`.
    """
    ancestors = q.code.ancestors
    assert _ids(codes, ancestors.any((q.code.id == "A") | (q.code.id == "ZZ"))) == ["B", "C"]
    assert _ids(codes, ancestors.any(~(q.code.id == "ZZ"))) == ["B", "C"]
    assert _ids(codes, ancestors.any((q.code.id == "A") & q.code.name["en"].str.contains("Alph"))) == ["B", "C"]


# ── All four DSLs, because the wiring is per-module ──────────────────────────


def test_the_hierarchy_dsl_is_wired() -> None:
    h = Hierarchy(
        id="H",
        maintainer="AG",
        version="1.0",
        name={"en": "H"},
        has_formal_levels=True,
        levels=[_LEVEL],
        tree=[
            HierarchicalCode(
                id="N1",
                code=code_urn("CL", "N1"),
                level=_LEVEL,
                children=[HierarchicalCode(id="N2", code=code_urn("CL", "N2"))],
            )
        ],
    )
    with LocalRegistry(":memory:") as reg:
        reg.add(h)
        nodes = present(reg.get(Hierarchy.urn_for(agency="AG", id="H", version="1.0"))).nodes
        assert _ids(nodes, (q.hcode.id == "N1") | (q.hcode.id == "N2")) == ["N1", "N2"]
        assert _ids(nodes, ~(q.hcode.id == "N1")) == ["N2"]


def test_the_metadata_dsl_is_wired() -> None:
    described = Codelist(
        id="CL_D", maintainer="AG", version="1.0", name={"en": "d"}, codes=[Code(id="A", name={"en": "a"})]
    )
    msd = MetadataStructure(id="MSD", maintainer="AG", name={"en": "MSD"})
    mdf = Metadataflow(id="MDF", maintainer="AG", name={"en": "MDF"}, structure=msd)
    mds = MetadataSet(
        id="MDS",
        maintainer="AG",
        name={"en": "Set"},
        structure=mdf,
        targets=[artefact_urn(described)],
        attributes=[ReportedAttribute(id="COVERAGE", value="national"), ReportedAttribute(id="TIER", value="core")],
    )
    with LocalRegistry(":memory:") as reg:
        for a in (described, msd, mdf, mds):
            reg.add(a)
        # `reg.reported_attributes()` is the generic query since #306, so `|` and
        # `~` here are the new language's — same laws, different implementation,
        # which is exactly what this file exists to check across DSLs.
        path = q.reported_attribute.attribute_path
        values = reg.reported_attributes()
        assert sorted(values.filter((path == "COVERAGE") | (path == "TIER")).ids()) == ["COVERAGE", "TIER"]
        assert values.filter(~(path == "COVERAGE")).ids() == ["TIER"]


# ── The in-memory sibling ────────────────────────────────────────────────────


def _dimensions():
    """Two dimensions differing in *two* ways, so a conjunction can discriminate.

    ``FREQ`` is enumerated and ``TIME_PERIOD`` is not, which gives the boolean
    tests a second field to work with — a fixture where the only thing that
    varies is the id cannot tell ``a & b`` from ``a``.
    """
    return DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name={"en": "S"},
        dimensions=[
            Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=_FREQ_CODES),
            Dimension(id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD")),
        ],
    ).dimensions


def test_item_predicates_take_the_same_three_operators() -> None:
    """`ItemExpr` filters in-memory `ItemList`s and now spells composition the same.

    Two APIs for "and these two conditions" that differ only in punctuation is
    the kind of seam a user trips over once and remembers as arbitrary, so the
    operators are the same three even though nothing is shared: this side
    composes Python callables and there is no SQL and no NULL.
    """
    dims = _dimensions()

    assert dims.filter((item("id") == "FREQ") | (item("id") == "TIME_PERIOD")).ids() == ["FREQ", "TIME_PERIOD"]
    assert dims.filter((item("id") == "FREQ") & item("is_enumerated")).ids() == ["FREQ"]
    assert dims.filter((item("id") == "TIME_PERIOD") & item("is_enumerated")).ids() == []
    assert dims.filter(~(item("id") == "FREQ")).ids() == ["TIME_PERIOD"]


def test_item_predicates_obey_excluded_middle() -> None:
    """The law the SQL side loses to NULL, which this side keeps.

    Worth an assertion precisely because the two surfaces are spelled the same:
    `ItemPredicate` evaluates a Python callable that returns a `bool`, so there
    is no third value and ``p | ~p`` is every item. A user who learns the rule
    on one side and applies it to the other is right here and wrong there —
    which is the whole reason the SQL divergence is documented on `__invert__`.
    """
    dims = _dimensions()
    p = item("id") == "FREQ"
    assert dims.filter(p | ~p).ids() == dims.ids()
    assert dims.filter(p & ~p).ids() == []
