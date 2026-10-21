"""Misuse tests for the query DSLs — wrong calls must fail, loudly (#266).

Every test here asserts that something *invalid* raises. That is a category the
suite did not have, and its absence let a real defect ship.

## Why the existing tests could not have caught it

`code.id.is_in("CA")` silently meant `is_in(["C", "A"])` — a filter that
type-checks, runs, and returns the wrong rows. Three layers all declined to
catch it, each for a good reason:

- **The strategies only generate well-formed input.** `st.lists(...)` produces
  lists. The properties explore the *value* space; a bare string is a wrong
  *argument shape*, which no value-space generator reaches.
- **The type checkers were right to allow it.** `str` genuinely satisfies
  `Iterable[object]`.
- **The oracle shared the misconception**, so the differential property agreed
  with the SQL and passed. Measured at the time: `oracle.in_set("CA")` returned
  `['C', 'A']`, identical to what the compiler produced.

That third one is a **structural limit of differential testing**, not a defect
in the oracle: the technique catches divergence between two implementations and
is blind to a mistake both make. CLAUDE.md's rule 1 — an oracle shares no code
with the module it tests — exists to reduce exactly this, but sharing *Python's
iteration semantics* is not sharing code, so the rule did not bite.

What found it was a third implementation outside the shared assumptions: polars
rejects `is_in("CA")`. Hence this file, and hence `test_no_unreviewed_collection_parameter`
below, which guards the class rather than the instance.

## What each test asserts

Not merely that an exception is raised — that the message **names the fix**. A
`TypeError` reading `'_IdRef' object has no attribute 'to_sql'` is technically a
failure and practically useless; it names a private attribute and leaves the
caller to guess.
"""

import pathlib
from collections.abc import Callable

import pytest

import sdmxlib as sl
from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, Level
from sdmxlib.query.accessors import q
from tests.support.factories import code_urn, concept_ref
from tests.support.narrow import present

pytestmark = pytest.mark.unit


@pytest.fixture
def codes():
    with LocalRegistry(":memory:") as reg:
        reg.add(
            Codelist(
                id="CL",
                maintainer="AG",
                version="1.0",
                name={"en": "n"},
                codes=[Code(id="A", name={"en": "a"}), Code(id="B", name={"en": "b"})],
            )
        )
        yield present(reg.get(Codelist.urn_for(agency="AG", id="CL", version="1.0"))).codes


@pytest.fixture
def nodes():
    with LocalRegistry(":memory:") as reg:
        lvl = Level(id="L", name={"en": "L"})
        reg.add(
            Hierarchy(
                id="H",
                maintainer="AG",
                version="1.0",
                name={"en": "n"},
                has_formal_levels=True,
                levels=[lvl],
                tree=[HierarchicalCode(id="N1", code=code_urn("CL", "N1"), level=lvl)],
            )
        )
        yield present(reg.get(Hierarchy.urn_for(agency="AG", id="H", version="1.0"))).nodes


# ── The original defect, and its siblings ────────────────────────────────────


def test_is_in_rejects_a_bare_string(codes) -> None:
    """The #261 defect. `is_in("CA")` must not mean `is_in(["C", "A"])`."""
    with pytest.raises(TypeError, match=r"not \'str\'"):
        codes.filter(q.code.id.is_in("AB"))


def test_is_in_rejects_bytes(codes) -> None:
    with pytest.raises(TypeError, match=r"not \'bytes\'"):
        codes.filter(q.code.id.is_in(b"AB"))


def test_is_in_rejects_a_mapping(codes) -> None:
    """A dict is iterable and yields its *keys* — the same shape, still live.

    It survived the fix for the bare string because that fix named ``str``
    rather than the class of mistake. Both now go through one guard.
    """
    with pytest.raises(TypeError, match=r"iterating it would use its keys"):
        codes.filter(q.code.id.is_in({"A": 1}))


def test_is_in_still_accepts_the_legitimate_shapes(codes) -> None:
    """The guards must not have made the ordinary call harder.

    A generator is a perfectly good argument and is deliberately *not*
    rejected — the rule is "iterable but means something else", not
    "not a list".
    """
    assert codes.filter(q.code.id.is_in(["A"])).ids() == ["A"]
    assert codes.filter(q.code.id.is_in(("A",))).ids() == ["A"]
    assert codes.filter(q.code.id.is_in({"A"})).ids() == ["A"]
    assert codes.filter(q.code.id.is_in(x for x in ["A"])).ids() == ["A"]
    assert codes.filter(q.code.id.is_in([])).ids() == []


# `test_aggregate_is_in_has_the_same_guards` went with the aggregate `is_in`
# (#306). `count().is_in([...])` had no counterpart in the path language and no
# caller: an aggregate is compared (`count() == 0`), and set membership over a
# scalar aggregate was a spelling nothing used. `Agg` exposes no `is_in` at
# all now, so there is no guard to have — `test_query_expression_surface.py`
# holds `Agg` to the operators it does expose.


# ── A field where a predicate belongs ────────────────────────────────────────


def test_filter_rejects_a_bare_field(codes) -> None:
    """`filter(code.id)` used to raise ``'_IdRef' has no attribute 'to_sql'``."""
    with pytest.raises(TypeError, match=r"takes a predicate, got Str"):
        codes.filter(q.code.id)


def test_filter_error_shows_how_to_fix_it(codes) -> None:
    """The message must carry a worked example, not just a complaint."""
    with pytest.raises(TypeError) as exc:
        codes.filter(q.code.id)
    assert 'q.code.id == "X"' in str(exc.value)


def test_filter_rejects_the_accessor_itself(codes) -> None:
    with pytest.raises(TypeError, match=r"takes a predicate"):
        codes.filter(q.code)


def test_filter_names_the_right_accessor_per_dsl(nodes) -> None:
    """The message is one message now.

    `CodeQuery` and `HierarchyNodeQuery` each had their own, so each had to
    name its own accessor. The path language has one `filter()` for every
    root, so the example in it is generic — there is no wrong-accessor to
    send a reader to.
    """
    with pytest.raises(TypeError) as exc:
        nodes.filter(q.hcode.id)
    assert "predicate" in str(exc.value)


# ── A predicate where a field belongs ────────────────────────────────────────


def test_sort_by_rejects_a_predicate(codes) -> None:
    """`sort_by(code.id == "A")` used to raise ``'Eq' has no attribute '_field'``."""
    with pytest.raises(TypeError, match=r"takes a column, got Compare"):
        codes.order_by(q.code.id == "A")


def test_sort_by_predicate_error_explains_the_confusion(codes) -> None:
    with pytest.raises(TypeError) as exc:
        codes.order_by(q.code.id == "A")
    assert "which rows" in str(exc.value)


def test_order_by_rejects_a_string_field_name(codes) -> None:
    """This DSL is not string-keyed; ``order_by("id")`` is a pandas habit.

    Also the one place the two `sort_by`/`order_by` families can be confused:
    `ItemList.sort_by` and `Query[T].sort_by` *do* take a string or a callable,
    because they sort in Python. A string reaching a SQL query means the caller
    has the wrong object, so the message says which kind of thing is wanted.
    """
    with pytest.raises(TypeError, match=r"takes a column, got str"):
        codes.order_by("id")


def test_select_and_group_by_reject_non_fields(codes) -> None:
    with pytest.raises(TypeError, match=r"takes? (a column|fields), got str"):
        codes.select("id")
    with pytest.raises(TypeError, match=r"takes? (a column|fields), got Compare"):
        codes.group_by(q.code.id == "A")


# ── limit ────────────────────────────────────────────────────────────────────


def test_limit_rejects_a_string(codes) -> None:
    """`limit("5")` reached DuckDB and *worked*, which is worse than failing."""
    with pytest.raises(TypeError, match=r"takes an int, got str"):
        codes.limit("5")


def test_limit_rejects_a_bool(codes) -> None:
    """`bool` is an `int` subclass; `limit(True)` is never intended."""
    with pytest.raises(TypeError, match=r"takes an int, got bool"):
        codes.limit(True)


def test_limit_rejects_a_negative(codes) -> None:
    """Was a DuckDB `BinderException` from deep inside execution."""
    with pytest.raises(ValueError, match=r"non-negative"):
        codes.limit(-1)


def test_limit_zero_is_allowed(codes) -> None:
    """Zero is a legitimate cap, and rejecting it would be over-reach."""
    assert codes.limit(0).ids() == []


# ── Mistyped literals (#272) ─────────────────────────────────────────────────


def test_an_int_against_a_varchar_column_is_rejected(codes) -> None:
    """`code.id == 1` built, compiled, and then behaved differently per dataset.

    Measured in DuckDB: comparing a ``VARCHAR`` column to an integer casts the
    *column* to ``INT32``, so on an all-numeric codelist the query matches, and
    on one containing a single non-numeric code it raises a
    ``ConversionException`` naming a cast the caller never wrote. The issue
    predicted a silent empty result; the real behaviour is worse, because it
    depends on the data rather than the query.

    No checker can catch it — `__eq__` overrides `object.__eq__` and cannot
    narrow its parameter — which is why it is a runtime guard.
    """
    with pytest.raises(TypeError, match="holds str, got int"):
        codes.filter(q.code.id == 1)
    with pytest.raises(TypeError, match="holds str, got int"):
        codes.filter(q.code.id != 1)


def test_the_message_names_the_field_and_suggests_the_literal(codes) -> None:
    """Naming the field is the point; "str, got int" alone barely improves on DuckDB."""
    with pytest.raises(TypeError) as exc:
        _ = q.code.id == 1
    text = str(exc.value)
    assert "code_id" in text
    assert '"1"' in text, "should suggest the string the caller probably meant"


def test_a_string_against_an_integer_column_is_rejected(codes) -> None:
    """The other direction. DuckDB *coerces* this one, so it works — until it does not.

    ``depth = '2'`` succeeds; ``depth = 'zz'`` raises. Accepting the first
    teaches a habit that breaks on the second, so both are refused.
    """
    with pytest.raises(TypeError, match="holds int, got str"):
        codes.filter(q.code.depth == "2")


def test_bool_is_not_an_int_here(codes) -> None:
    """``depth == True`` is never intended, and ``bool`` is an ``int`` subclass.

    Same reasoning as `check_limit`, which rejects ``limit(True)``.
    """
    with pytest.raises(TypeError, match="holds int, got bool"):
        codes.filter(q.code.depth == True)  # noqa: E712


# `test_a_bool_column_rejects_an_int` went with `ArtefactQuery` (#306).
# `artefact.is_final` was the only bool column any of these DSLs had, so the
# converse half of the int/bool exclusion is no longer reachable — the check
# itself lived in the deleted module. The path language declares `is_final` as
# text and does not type-check bools at all, which is a gap of its own rather
# than this one.


def test_comparing_to_none_points_at_is_null(codes) -> None:
    """``parent_id == None`` returned nothing, silently.

    ``= NULL`` is never true in SQL. A second silent-wrong-answer of the same
    family, caught by the same guard, and the message names the spelling that
    works.
    """
    with pytest.raises(TypeError, match="is_null"):
        codes.filter(q.code.parent.id == None)  # noqa: E711


def test_is_in_checks_the_first_member_only(codes) -> None:
    """O(1) by design, and the limit is stated rather than hidden.

    Checking every member costs ~110 us on a 5000-element set — measured —
    which roughly doubles the compile on the path #271 flags as hot. What it
    misses is a *mixed* set; every realistic version of this mistake is
    homogeneous (an int literal list, a ``range``, a dataframe column that came
    back int64).
    """
    with pytest.raises(TypeError, match="holds str, got int"):
        codes.filter(q.code.id.is_in([1, 2, 3]))
    with pytest.raises(TypeError, match="holds str, got int"):
        codes.filter(q.code.id.is_in(range(3)))

    # The documented gap: a mixed set passes the guard and reaches DuckDB.
    # Asserted so the limit is a known quantity rather than a surprise.
    #
    # #270 briefly narrowed this by sorting members at construction — a wrong
    # `int` among `str` members sorted to the front and was caught — but that
    # canonicalisation moved into `canonical_key` when it measured 116x on a
    # 5000-element set. Members are stored as written again, so the gap is
    # exactly as wide as it was.
    assert codes.filter(q.code.id.is_in(["A", 2])) is not None


def test_the_convenient_spellings_still_work(codes) -> None:
    """A guard that over-fires is worse than the mistakes it catches.

    The old DSL coerced artefacts at the comparison — `node.level` accepted a
    `Level`, `node.code` a ``Ref[Code]`` — so this test existed to prove
    coercion ran *before* the type check. The path language has no coercion to
    order: an artefact-valued edge is a hop (`q.hcode.level.id`), not a
    comparison against an object. What survives is the claim underneath, that
    the literal check admits everything it should.
    """
    from sdmxlib.query import _expr

    for built in (
        q.hcode.level.id == "PR",
        q.code.id == "A",
        q.code.depth == 2,
        q.code.id.is_in([]),
        q.code.id.is_in(["A", "B"]),
        q.code.name["en"] == "a",
    ):
        assert isinstance(built, _expr.AnyPred)


# ── The guard against the class, not the instances ───────────────────────────


def _collection_params() -> set[str]:
    """Public parameters in the query DSLs annotated as a bare-str-satisfying collection.

    A *static* inventory rather than a reflective call sweep. The first version
    called each candidate with ``"AB"`` and crashed descending into builtins —
    and even working, it could only test parameters whose receiver it knew how
    to construct. Reading the annotations needs neither.
    """
    import ast
    import re

    modules = [
        "src/sdmxlib/query/_expr.py",
        "src/sdmxlib/query/query.py",
        "src/sdmxlib/query/raw.py",
    ]
    hits: set[str] = set()
    for path in modules:
        tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
                continue
            for arg in [*node.args.args, *node.args.kwonlyargs]:
                if arg.annotation is None:
                    continue
                text = ast.unparse(arg.annotation)
                # Only flag element types a bare `str` actually satisfies.
                # `Sequence[Projection[R] | Aggregate[R]]` does not — a str is
                # a `Sequence[str]`, and the checkers reject it. Flagging those
                # would make the inventory noise, and a noisy guard gets
                # allowlisted wholesale.
                for m in re.finditer(r"(?:Iterable|Sequence|Collection)\[([^\[\]]+)\]", text):
                    if m.group(1).strip() in {"object", "Any", "str"}:
                        hits.add(f"{node.name}({arg.arg})")
    return hits


#: Collection-typed public parameters that have been reviewed for the bare-str
#: hazard. Each one is covered by a case above. Adding a parameter here without
#: a test is the mistake this list exists to make visible.
_REVIEWED_COLLECTION_PARAMS = {
    # The original defect. Guarded in `Col.is_in` and `Str.is_in`; the cases
    # above cover the bare `str`, the `bytes`, the `Mapping` and the `RawSql`.
    "is_in(values)",
    # `where=` on every aggregate (#373) is `AnyPred | None` / `Pred[C] | None`
    # — not collection-typed, so it does not appear in `found` — but the
    # *runtime* normaliser used to accept a sequence, which is the same defect
    # one layer down. Refused now, and pinned by the two tests at the end.
    # `collect(labels)` / `frame(labels)` are `str | Sequence[str]`, and the
    # bare-str case is the *intended* spelling — `labels="en"` is one language,
    # not two characters. Guarded by an explicit `isinstance(lang, str)` in
    # `sdmxlib.polars.label`, and pinned by
    # `test_a_bare_language_is_one_language_not_two_characters` below.
    "collect(labels)",
    "frame(labels)",
    # `build(resolve)` used to be listed here: it was matched on its *return*
    # annotation, `Callable[[Sequence[tuple[object, ...]]], Sequence[object]]`,
    # never on a parameter a caller passes a collection to. #381 replaced that
    # inline `Callable` with the `Resolver` Protocol — which it needed anyway,
    # for the `annotations` keyword — and the false positive went with it. The
    # stale half of `test_no_unreviewed_collection_parameter` is what noticed;
    # left as a comment because "why is this not in the list" is the question a
    # reader of the entry above will now have.
}


def test_no_unreviewed_collection_parameter():
    """A new `Iterable[...]` parameter must be reviewed for the bare-str hazard.

    `is_in` was the only one when #266 was written, and it had just shipped a
    silent-wrong-answer bug because of it. This is the part that guards the
    *class* rather than the instance: the next such parameter fails here on the
    day it lands, and the fix is to add a guard, a misuse test, and this name.

    A static inventory, so it needs no instance of the receiver and cannot be
    defeated by a parameter on a class the test does not know how to build.
    """
    found = _collection_params()
    unreviewed = found - _REVIEWED_COLLECTION_PARAMS
    assert not unreviewed, (
        f"unreviewed collection parameters: {sorted(unreviewed)}. "
        "A bare `str` satisfies these annotations — check the value is not "
        "silently iterated into characters, add a misuse test, then list it in "
        "_REVIEWED_COLLECTION_PARAMS."
    )
    stale = _REVIEWED_COLLECTION_PARAMS - found
    assert not stale, f"reviewed parameters that no longer exist: {sorted(stale)}"


def test_a_bare_language_is_one_language_not_two_characters():
    """`labels="en"` means English, not `["e", "n"]`.

    The #261 shape exactly: a `str` satisfies `Sequence[str]`, so iterating it
    is silently *valid* and produces per-character nonsense. `is_in("CA")`
    shipped that way. Here the bare string is the spelling a caller will reach
    for first, so it is handled rather than refused — and pinned, because the
    handling is one `isinstance` that a refactor could drop without any type
    checker noticing.
    """
    import polars as pl

    from sdmxlib.model.codelist import Code, Codelist
    from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
    from sdmxlib.model.organisation import Agency
    from sdmxlib.model.ref import Ref
    from sdmxlib.polars import label

    agency = Agency(id="AG")
    codelist = Codelist(id="CL", maintainer=agency, name={"en": "c"}, codes=[Code(id="A", name={"en": "Alpha"})])
    dsd = DataStructure(
        id="D",
        maintainer=agency,
        name={"en": "d"},
        dimensions=[Dimension(id="X", concept=concept_ref("X"), representation=Ref.to(codelist))],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    frame = pl.DataFrame({"X": ["A"]}).lazy()
    columns = label(frame, dsd, lang="en").collect().columns
    assert columns == ["X", "X_label_en"], "one column, not one per character"
    assert "X_label_e" not in columns


# ── where= on an aggregate takes one predicate (#373) ─────────────────────


def test_where_rejects_a_collection_of_predicates(codes) -> None:
    """`.any(p, q)` is variadic; a restriction is not, and says so.

    The two differ for a reason. `.any()` has to tell "one child satisfies
    both" from "some child each" apart, so it groups. A restriction is
    evaluated per candidate child — there is no second child for a condition
    to land on — so a variadic form would be a second spelling of `&`, which
    is exactly the kind of thing that gets written once and then means
    something subtly different from the operator.

    It compiled before this check: the internal normaliser took a sequence
    while the signature said one predicate, so a tuple worked at runtime and
    failed the checker.
    """
    dimension = q.component.role == "Dimension"
    named = q.component.id == "GEO"
    with pytest.raises(TypeError, match=r"where= takes one predicate"):
        q.dsd.components.list(q.dsd.components.id, where=(dimension, named))  # pyrefly: ignore[bad-argument-type]


def test_the_where_error_names_both_operators(codes) -> None:
    """A message that says "not a collection" and stops has not helped."""
    with pytest.raises(TypeError) as caught:
        q.dsd.components.count(where=[q.component.role == "Dimension"])  # pyrefly: ignore[bad-argument-type]
    message = str(caught.value)
    assert "&" in message
    assert "|" in message


# ── Misuse that reached DuckDB as a raw error (#511) ─────────────────────────
#
# Each of these failed inside the database — a BinderException naming an
# alias the caller never wrote, or "cannot compile Compare" — where the rest
# of the language says what to write instead.


@pytest.fixture
def observations():
    """One dataflow over a two-row table, bound — an `ObsQuery` to misuse."""
    concepts = sl.ConceptScheme(
        id="CS",
        maintainer="AG",
        name={"en": "c"},
        concepts=[sl.Concept(id=c, name={"en": c}) for c in ("GEO", "OBS_VALUE")],
    )
    dsd = sl.DataStructure(
        id="DSD",
        maintainer="AG",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="GEO", concept=concepts.ref_to("GEO"))],
        measures=[sl.Measure(id="OBS_VALUE", concept=concepts.ref_to("OBS_VALUE"))],
    )
    flow = sl.Dataflow(id="F", maintainer="AG", name={"en": "f"}, structure=sl.Ref.to(dsd))
    with LocalRegistry(":memory:") as reg:
        reg.add_all([concepts, dsd, flow])
        reg.connection.execute("CREATE SCHEMA data")
        reg.connection.execute("CREATE TABLE data.obs AS SELECT 'CA' AS GEO, 1.0 AS OBS_VALUE")
        reg.add(
            sl.DataflowBinding.from_dsd(
                dsd, dataflow=sl.Ref.to(flow), schema="data", table="obs", database_role="memory"
            )
        )
        yield reg.obs(flow)


@pytest.mark.parametrize("terminal", ["ids", "urns", "collect", "first", "page"])
def test_an_observation_query_refuses_what_only_artefacts_answer(observations, terminal: str) -> None:
    """It reached DuckDB on `resource_id`, and `collect()`'s refusal named `.ids()` as the fix."""
    call = getattr(observations, terminal)
    with pytest.raises(TypeError, match=r"no id and builds no artefact\. Use \.frame\(\)") as caught:
        call(1) if terminal == "page" else call()
    assert ".ids()" not in str(caught.value)


def test_an_observation_query_still_answers_with_rows(observations) -> None:
    """The discrimination check: the refusal is the artefact terminals only."""
    assert observations.frame().rows() == [("CA", 1.0)]


def test_qualify_refuses_an_ordinary_predicate_and_names_filter(codes) -> None:
    """It was accepted, then failed to compile with `cannot compile Compare`."""
    with pytest.raises(TypeError, match=r"ordinary predicate belongs in filter\(\)"):
        codes.qualify(q.code.id == "A")


def test_filter_refuses_a_window_and_names_qualify(codes) -> None:
    with pytest.raises(TypeError, match=r"Pass it to qualify\(\)"):
        codes.filter(q.code.id.rank().over(q.code.depth) == 1)


def test_a_parent_predicate_inside_any_is_refused_as_it_is_built() -> None:
    """`Referenced table "s1" not found` — the parent's column inside the child's quantifier."""
    # Called as an untyped caller would: the row kind refuses it statically.
    untyped_any: Callable[..., object] = q.codelist.codes.any
    untyped_every: Callable[..., object] = q.codelist.codes.every
    with pytest.raises(TypeError, match=r"\.any\(\) got a predicate built from q\.codelist"):
        untyped_any(q.codelist.id == "CL")
    with pytest.raises(TypeError, match=r"\.every\(\) got a predicate built from q\.codelist"):
        untyped_every(q.codelist.id == "CL")


def test_a_where_from_another_relation_is_refused_as_it_is_built() -> None:
    untyped_count: Callable[..., object] = q.codelist.codes.count
    with pytest.raises(TypeError, match=r"count\(where=…\) got a predicate built from q\.dsd"):
        untyped_count(where=q.dsd.id == "X")


def test_the_child_s_own_predicates_still_build() -> None:
    """The discrimination check: a child-rooted predicate, through a hop and nested."""
    q.codelist.codes.any(q.code.id == "A", q.code.parent.id == "B")
    q.codelist.codes.count(where=q.code.annotations.any(q.annotation.type == "t"))


def test_a_window_is_refused_inside_group_by(codes) -> None:
    with pytest.raises(TypeError, match=r"computed per row, not per group"):
        codes.group_by(q.code.depth).agg(t=q.code.position.sum().over())


def test_an_aggregate_compared_with_an_aggregate_is_refused() -> None:
    """`Unable to transform python value of type Agg` — the right side was bound as a value."""
    codes = q.codelist.codes
    with pytest.raises(TypeError, match=r"not with another aggregate"):
        _ = codes.count() > codes.id.n_unique()
    with pytest.raises(TypeError, match=r"not with a column"):
        _ = codes.count() == q.codelist.id


def test_a_column_compared_with_a_column_says_it_is_not_supported() -> None:
    """The literal check said "Did you mean <Str parent_id>?", which read as if it were."""
    with pytest.raises(TypeError, match=r"compared with a column, which is not supported yet") as caught:
        _ = q.code.id == q.code.parent.id
    assert "Did you mean" not in str(caught.value)


def test_in_on_a_query_is_refused_naming_exists(codes) -> None:
    with pytest.raises(TypeError, match=r"a query has no `in`.*exists\(\)"):
        _ = "A" in codes


def test_a_to_many_path_is_named_as_the_caller_wrote_it() -> None:
    """Not `'dsd.dataflows.spine'` — the identity-split hop is the library's, not the caller's."""
    with LocalRegistry(":memory:") as reg:
        with pytest.raises(Exception, match=r"'dsd\.dataflows'") as caught:
            reg.query(q.component).select(q.component.dsd.dataflows.id)
        assert "spine" not in str(caught.value)
