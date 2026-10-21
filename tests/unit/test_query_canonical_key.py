"""`Query.canonical_key()` — #270's contract, for the new language.

A cache downstream keys on what we hand it. Until #270 the only thing we could
hand it was order-dependent: `filter(a).filter(b)` and `filter(b).filter(a)`
mean the same thing, produce different SQL text, and compare unequal as values,
so a consumer keying on either lost hits it should have had.

Three properties, each load-bearing and each asserted here:

* **Deterministic across processes** — no `id()`, no `hash()`, no set
  iteration. Python's string hash is salted per process, so a key built from
  one cannot be persisted or compared between workers.
* **Hashable**, so it can be a dict key.
* **Comparable**, so it doubles as the sort key that makes the form canonical.

The interesting tests are the ones asserting two queries *differ*. A key that
collides is worse than no key at all — it serves the wrong rows — and every
collision below was a real one before the expression types learned to describe
themselves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.organisation import Agency
from sdmxlib.query import _expr, _plan
from sdmxlib.query._common import _COMMUTATIVE_FIELDS
from sdmxlib.query._plan import Plan
from sdmxlib.query.accessors import CodeRow, q
from sdmxlib.query.query import Query

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            Codelist(
                id="CL",
                maintainer=Agency(id="ESTAT"),
                name={"en": "Codes", "fr": "Codes"},
                codes=[Code(id="A", name={"en": "Alpha", "fr": "Alpha"})],
            )
        )
        yield registry


# ── the three properties ──────────────────────────────────────────────────


def test_the_key_is_hashable_and_comparable(reg: sl.LocalRegistry) -> None:
    key = reg.query(q.codelist).filter(q.codelist.id == "CL").canonical_key()
    assert isinstance(hash(key), int)
    assert {key: 1}[key] == 1
    assert sorted([key, reg.query(q.dsd).canonical_key()])


def test_the_key_holds_no_process_local_state(reg: sl.LocalRegistry) -> None:
    """Flattened, it must be primitives only. An `id()` or a `hash()` would
    make the key unusable across workers — which is most of what a cache is
    for."""

    def flatten(value: object) -> Iterator[object]:
        if isinstance(value, tuple):
            for item in value:
                yield from flatten(item)
        else:
            yield value

    key = reg.query(q.code).filter(q.code.name["en"] == "Alpha").limit(3).canonical_key()
    for leaf in flatten(key):
        assert leaf is None or isinstance(leaf, str | int | float | bool), f"{leaf!r} is not a primitive"


def test_filter_order_does_not_change_the_key(reg: sl.LocalRegistry) -> None:
    """The whole of #270."""
    base = reg.query(q.codelist)
    first = base.filter(q.codelist.id == "CL").filter(q.codelist.agency == "ESTAT")
    second = base.filter(q.codelist.agency == "ESTAT").filter(q.codelist.id == "CL")
    assert first.canonical_key() == second.canonical_key()
    assert first.to_sql().sql != second.to_sql().sql, "the SQL text still differs, which is why the key exists"


def test_one_call_and_two_calls_agree(reg: sl.LocalRegistry) -> None:
    base = reg.query(q.codelist)
    chained = base.filter(q.codelist.id == "CL").filter(q.codelist.agency == "ESTAT")
    together = base.filter(q.codelist.id == "CL", q.codelist.agency == "ESTAT")
    assert chained.canonical_key() == together.canonical_key()


def test_the_key_is_stable_across_identical_queries(reg: sl.LocalRegistry) -> None:
    """Two separately built queries, not one object asked twice — the leaves
    hash by identity, so a key that leaked that would pass the weaker test."""
    left = reg.query(q.code).filter(q.code.id == "A")
    right = reg.query(q.code).filter(q.code.id == "A")
    assert left is not right
    assert left.canonical_key() == right.canonical_key()


def test_the_connection_and_resolver_are_excluded(reg: sl.LocalRegistry) -> None:
    """They are wiring, not meaning. Excluded by their own `eq=False` rather
    than by a list here, so a field added to `Query` is in the key by default
    and has to opt *out*."""
    with sl.LocalRegistry(":memory:") as other:
        other.add(Codelist(id="CL", maintainer=Agency(id="ESTAT"), name={"en": "Codes"}))
        assert reg.query(q.codelist).canonical_key() == other.query(q.codelist).canonical_key()


# ── what must not collide ─────────────────────────────────────────────────


def test_the_language_is_in_the_key(reg: sl.LocalRegistry) -> None:
    """The collision that motivated `__canonical__`.

    `AnyCol.__repr__` names the path, table and column but not the language,
    and the generic key falls back to `repr` for a non-`attrs` object — so
    these two keyed identically and a cache would have served English rows for
    a French query.
    """
    english = reg.query(q.code).filter(q.code.name["en"] == "Alpha")
    french = reg.query(q.code).filter(q.code.name["fr"] == "Alpha")
    assert english.canonical_key() != french.canonical_key()


def test_arithmetic_is_in_the_key(reg: sl.LocalRegistry) -> None:
    """A computed column reports its first operand's path, table and column,
    so `position + 1` and `position - 1` are identical to the base key."""
    plus = reg.query(q.code).filter(q.code.position + 1 == 2)
    minus = reg.query(q.code).filter(q.code.position - 1 == 2)
    assert plus.canonical_key() != minus.canonical_key()


def test_the_window_frame_is_in_the_key(reg: sl.LocalRegistry) -> None:
    ascending = reg.query(q.code).qualify(q.code.id.rank().over(q.code.parent.id) == 1)
    descending = reg.query(q.code).qualify(q.code.id.rank(descending=True).over(q.code.parent.id) == 1)
    unpartitioned = reg.query(q.code).qualify(q.code.id.rank().over() == 1)
    keys = {ascending.canonical_key(), descending.canonical_key(), unpartitioned.canonical_key()}
    assert len(keys) == 3


def test_the_root_is_in_the_key(reg: sl.LocalRegistry) -> None:
    """`q.dsd` and `q.codelist` are the same relation and differ only by their
    discriminator, so keying on the table alone would make them equal."""
    assert reg.query(q.dsd).canonical_key() != reg.query(q.codelist).canonical_key()


def test_a_column_and_its_type_are_both_in_the_key(reg: sl.LocalRegistry) -> None:
    """`id` and `parent_id` are both `Str` over `sdmx.code`; the column tells
    them apart. A `Str` and a `Num` over one column would need the class."""
    assert (
        reg.query(q.code).filter(q.code.id == "A").canonical_key()
        != reg.query(q.code).filter(q.code.parent.id == "A").canonical_key()
    )


def test_the_folding_rung_is_in_the_key(reg: sl.LocalRegistry) -> None:
    """`==` and `.str.caseless ==` return different rows, so they must key
    differently — the rung lives in `Compare.fold`."""
    exact = reg.query(q.code).filter(q.code.id == "a")
    caseless = reg.query(q.code).filter(q.code.id.str.caseless == "a")
    assert exact.canonical_key() != caseless.canonical_key()


@pytest.mark.parametrize(
    "refine",
    [
        pytest.param(lambda base: base.limit(5), id="limit"),
        pytest.param(lambda base: base.offset(2), id="offset"),
        pytest.param(lambda base: base.order_by(q.code.id), id="order_by"),
        pytest.param(lambda base: base.order_by(q.code.id.desc()), id="order_by_desc"),
        pytest.param(lambda base: base.filter(q.code.id == "A"), id="filter"),
        pytest.param(lambda base: base.qualify(q.code.id.rank().over() == 1), id="qualify"),
    ],
)
def test_every_clause_changes_the_key(
    reg: sl.LocalRegistry, refine: Callable[[Query[CodeRow, Code]], Query[CodeRow, Code]]
) -> None:
    """A clause absent from the key is a cache serving the wrong rows. Swept
    rather than spot-checked, so a new clause has to be added here to pass."""
    base = reg.query(q.code)
    assert refine(base).canonical_key() != base.canonical_key()


def test_sort_direction_changes_the_key(reg: sl.LocalRegistry) -> None:
    base = reg.query(q.code)
    assert base.order_by(q.code.id).canonical_key() != base.order_by(q.code.id.desc()).canonical_key()


def test_a_cursor_changes_the_key(reg: sl.LocalRegistry) -> None:
    """Two pages of one query are different results, so they cannot share a
    cache entry."""
    ordered = reg.query(q.code).order_by(q.code.id)
    page = ordered.page(1)
    assert page.cursor is not None
    assert ordered.after(page.cursor).canonical_key() != ordered.canonical_key()


def test_is_in_ignores_member_order_but_not_membership(reg: sl.LocalRegistry) -> None:
    """`is_in` is a set, so its order carries nothing — and its *contents*
    carry everything."""
    base = reg.query(q.code)
    assert (
        base.filter(q.code.id.is_in(["A", "B"])).canonical_key()
        == base.filter(q.code.id.is_in(["B", "A"])).canonical_key()
    )
    assert (
        base.filter(q.code.id.is_in(["A", "B"])).canonical_key()
        != base.filter(q.code.id.is_in(["A", "C"])).canonical_key()
    )


def test_boolean_operand_order_does_not_change_the_key(reg: sl.LocalRegistry) -> None:
    left, right = q.code.id == "A", q.code.parent.id == "B"
    base = reg.query(q.code)
    assert base.filter(left & right).canonical_key() == base.filter(right & left).canonical_key()
    assert base.filter(left | right).canonical_key() == base.filter(right | left).canonical_key()
    assert base.filter(left & right).canonical_key() != base.filter(left | right).canonical_key()


def test_the_binder_and_two_filters_key_differently(reg: sl.LocalRegistry) -> None:
    """They return different rows — one annotation satisfying both, versus two
    each satisfying one — so a shared key would be a wrong answer."""
    base = reg.query(q.code)
    bound = base.filter(q.code.annotations.any(q.annotation.type == "N", q.annotation.title == "t"))
    apart = base.filter(q.code.annotations.type == "N", q.code.annotations.title == "t")
    assert bound.canonical_key() != apart.canonical_key()


def test_the_plan_holds_the_question_and_the_query_holds_the_binding() -> None:
    """The split pushdown §3 asks for, asserted rather than described.

    Without this, a later clause needing one more piece of state adds a field
    to `Query` — it is the class with the methods — and the plan quietly stops
    being the whole question. A second backend would then be handed something
    that does not describe the query, which is the failure this step exists to
    prevent and which nothing else here would notice.

    Comparing to a literal set rather than counting: a *renamed* field must
    fail too, since `_common._COMMUTATIVE_FIELDS` and `canonical_key` key on
    field names.
    """
    assert {f.name for f in attrs.fields(Plan)} == {
        "root",
        "predicates",
        "qualifiers",
        "order_keys",
        "default_order",
        "unordered",
        "cursor",
        "limit_n",
        "offset_n",
    }
    # `eq` is the discriminator, not privacy: `_conn` and `_resolve` are
    # excluded from equality *because* they are wiring, so the same predicate
    # picks them out here.
    query_fields = attrs.fields(Query)
    assert {f.name for f in query_fields if f.eq} == {"plan"}
    # `_prepare` joined them in #312: a REST-backed query must fetch before it
    # can answer, and that is wiring by the same test — two queries meaning the
    # same thing must key identically whether or not one has to fetch first.
    #
    # `_source` / `_source_label` joined in §5.9, and belong on the same side
    # for the same reason: they say *which database* answers, which is wiring,
    # not question. Two queries asking the same thing of two registries must
    # still key identically — the cross-source refusal is what keeps them from
    # being composed, not the cache key.
    assert {f.name for f in query_fields if not f.eq} == {
        "_conn",
        "_resolve",
        "_prepare",
        "_source",
        "_source_label",
    }


def test_the_commutative_fields_name_a_class_that_exists() -> None:
    """`canonical_key` keys commutativity on `(class name, field name)`.

    Both are plain strings, so moving `predicates` off `Query` and onto `Plan`
    left the entries pointing at nothing — and the symptom is not an error but
    `filter(a).filter(b)` and `filter(b).filter(a)` silently keying
    differently, i.e. a cache miss where #270 promised a hit. The property
    below covers the `Plan` pair; this covers every entry, including the four
    older DSLs', against the same drift.
    """
    known = {
        cls.__name__: cls
        for module in (_expr, _plan)
        for cls in vars(module).values()
        if attrs.has(cls)
        if isinstance(cls, type)
    }
    for cls_name, field_name in _COMMUTATIVE_FIELDS:
        cls = known.get(cls_name)
        assert cls is not None, f"_COMMUTATIVE_FIELDS names {cls_name}, which no longer exists"
        assert field_name in {f.name for f in attrs.fields(cls)}, (
            f"_COMMUTATIVE_FIELDS names {cls_name}.{field_name}, which is no longer a field"
        )
