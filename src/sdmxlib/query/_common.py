"""Laws and identity shared by every `Query[R]` clause (#267, #306).

Package-internal: plain names in a private module, following the precedent
`sdmxlib.model._label_match` set in #255. Deliberately not `sdmxlib.sql`, which
was the public cross-library seam — this is compiler detail.

What was here is most of what #306 deleted. `_query_common` began as the shared
half of four hand-written DSLs — a generic `Predicate[R]` hierarchy, a frame
compiler, a ref hierarchy, ~2400 lines — and the path language replaced all of
it with declared edges and one compiler. What survives is the part that was
never about *emitting SQL*: the composition laws, and query identity.

## The refinement principle (#293)

**Every row-set composition refines the query. Nothing a later caller adds can
undo what an earlier call established.**

This is the rule the individual clause decisions are instances of, and it is
written here because it was not: each new clause had been a fresh argument, and
#264 shipped a first implementation that broke it.

| clause | law | why |
| --- | --- | --- |
| `filter(p)` | AND | narrows rows |
| `qualify(p)` | AND | narrows rows, after windows |
| `limit(n)` | `min` | lowers the ceiling |
| `offset(n)` | raises `start`, shrinks the length | raises the floor |
| `select` | not a row refinement — returns a `Select`, whose `filter` raises |
| `group_by` | not a refinement at all, changes shape — raises after `order_by`/`limit` |

Three laws follow, generated in `tests/unit/test_properties_query_laws.py`:
**monotonicity** (the rows of a refined query are a subset), **idempotence**
(stating a constraint twice is stating it once), and — in its strongest usable
form — **every ordering either commutes or is refused**. There is no third
category where order silently changes the answer.

Two things the principle does *not* cover, both deliberate:

- **Ordering is not a row-set property.** `order_by` permutes rather than
  narrows, and it *replaces* rather than appends (#294), which is the one
  place a clause deliberately does not refine — appending cannot be undone, so
  a helper that sorted would silently outrank its caller.
- **`default_order` is not an ordering.** It is what the rows mean when nobody
  has said — `codelist.codes` in declaration order — so it does not block
  `group_by` and the first `order_by` replaces it.
"""

from collections.abc import Sequence
from enum import Enum
from typing import Protocol, runtime_checkable

import attrs

from sdmxlib.sql import SqlRelation


@attrs.frozen
class Descending[K]:
    """What ``ref.desc()`` returns — a sort key with its direction attached.

    Direction is per *key*, not per call, because one ``order_by`` can carry
    several keys and they routinely disagree: "newest version first, then id
    ascending" is the common shape.

    A marker on the key rather than a ``descending=[True, False]`` list
    parallel to the keys. polars takes the list; SQLAlchemy, ibis and Django
    all mark the key (``col.desc()``, ``t.a.desc()``, ``"-a"``). Parallel
    arrays put the direction at a distance from the thing it describes and go
    wrong silently when the two lengths drift, which is a failure mode this
    codebase already carries an error message for.

    Ascending is the bare ref, so there is no ``asc()``: an unmarked key is
    ascending, as in every one of those libraries.

    Generic over the *key* rather than the row kind, so unwrapping gives the
    ref type back: a row query gets a `ColumnRef`, a `FrameQuery` gets whatever
    it accepts. Parameterising on the row instead would erase that and force a
    cast at every use.
    """

    key: K


@runtime_checkable
class SupportsCanonicalKey(Protocol):
    """An object that describes its own identity for `canonical_key`.

    For expression types that are not `attrs` — `AnyCol` and its subclasses
    carry slots and a descriptor protocol instead — where the structural walk
    has nothing to walk and the `repr` fallback is lossy.
    """

    def __canonical__(self) -> tuple[object, ...]:
        """A deterministic, hashable, comparable key. See `canonical_key`."""
        ...


#: Fields whose *order* is not part of their meaning, keyed by
#: `(class name, field name)`. A conjunction does not care about the order its
#: operands arrived in, so `canonical_key` sorts these rather than preserving
#: them — which is what makes `filter(a).filter(b)` and `filter(b).filter(a)`
#: agree.
#:
#: Keyed on names rather than on the classes themselves so `_common` stays
#: ignorant of `sdmxlib.query`, which imports it. The cost is that a rename or
#: a deletion leaves an entry pointing at nothing, with no error and no symptom
#: beyond a cache miss where #270 promised a hit — so
#: `test_the_commutative_fields_name_a_class_that_exists` checks the names
#: resolve. It found seven entries #306 had already orphaned when it deleted
#: the four older DSLs (`CodeQuery`, `HierarchyNodeQuery`, `ArtefactQuery`,
#: `FrameQuery`) and their `And` / `Or` / `_Nary` expression nodes.
_COMMUTATIVE_FIELDS = frozenset(
    {
        # `Plan` rather than `Query` since pushdown §3 split the question from
        # the binding: `canonical_key` walks the nested attrs value and sees
        # the owning class's name, not the outer one's.
        ("Plan", "predicates"),
        ("Plan", "qualifiers"),
        ("BoolOp", "parts"),
        ("Quantified", "parts"),
        ("InSet", "values"),
    }
)


def _as_members(value: object) -> Sequence[object] | None:
    """*value* as a sequence of members, or ``None`` if it is not one.

    A narrowing helper rather than an inline ``isinstance(value, tuple | list)``:
    pyrefly rejects the union form on generic classes, and separate calls in
    one condition trip ruff's SIM101. Extracting it satisfies both and is the
    only place the question is asked.
    """
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return value
    return None


def _field_key(cls_name: str, field_name: str, value: object) -> tuple[object, ...]:
    """`canonical_key` for one field, sorting it when its order carries nothing.

    Sorting *here* rather than at construction is a **measured** decision.
    Canonicalising inside `filter` and `is_in` makes the IR compare equal
    directly, which is tidier, and it cost:

        is_in, 5000 members      115 us  ->  13.3 ms   (116x)
        is_in, 100 members       7.6 us  ->  279 us    (37x)
        chain of predicates     11.5 us  ->  59.7 us   (5x)
        single predicate         2.7 us  ->  6.7 us    (2.4x)

    because every construction paid for a full recursive key build, on the path
    #271 already flags as hot. Doing it in the key moves the whole cost to the
    caller who wants one, and only when they ask for it.

    The consequence, stated rather than buried: ``filter(a).filter(b)`` and
    ``filter(b).filter(a)`` still differ as *values* and still emit their
    clauses in different orders. They agree on `canonical_key`, which is what
    #270 asked for and what a cache uses.
    """
    if (cls_name, field_name) not in _COMMUTATIVE_FIELDS:
        return canonical_key(value)
    members = _as_members(value)
    if members is None:
        return canonical_key(value)
    return ("set", tuple(sorted({canonical_key(v) for v in members})))


def canonical_key(value: object) -> tuple[object, ...]:
    """A deterministic, hashable, *comparable* key for any part of a query (#270).

    Three properties, each load-bearing:

    - **Deterministic across processes.** No ``id()``, no set iteration, no
      ``hash()`` — Python's string hash is salted per process, so a key built
      from it cannot be persisted or compared between workers.
    - **Hashable**, so a consumer can key a cache on it. A downstream SDMX REST service keys
      cached results on what we hand it, and until now the only thing we handed
      it was order-dependent.
    - **Comparable**, so it doubles as the sort key that *makes* the IR
      canonical. Every leaf is tagged with its type name first, which is what
      lets an ``int`` and a ``str`` sit in one sorted list without
      ``TypeError`` — the tags compare before the values ever do.

    Recursive over the shapes a predicate tree contains: `attrs` values,
    tuples, `sdmxlib.sql.SqlRelation` (keyed on its compiled SQL and binds,
    which is what a cache would use anyway), enums, and primitives.

    Args:
        value: Any part of a query — a predicate, a field expression, a
            literal, or a whole query object.

    Returns:
        A tuple of tuples of primitives. Opaque; compare and hash it, do not
        parse it.
    """
    # `attrs` **before** `SqlRelation`. The relation check is a
    # `runtime_checkable` Protocol, so it matches on method presence alone —
    # and a query object has both `to_sql` and `columns`, so it matched first
    # and keyed itself on its own compiled SQL. That SQL is emitted with
    # `omit_order=True`, so two queries differing only in `order_by` produced
    # the *same key*: a cache would have served one page order for another.
    # Precisely the failure #270 exists to prevent, reintroduced by the key
    # meant to fix it, and caught only because the test asserted the
    # order-sensitive case rather than assuming it.
    #
    # Structural walking is better for our own objects anyway: it needs no
    # compile, and it sees fields that emission deliberately omits.
    cls = type(value)
    if isinstance(value, SupportsCanonicalKey):
        # An expression type that describes itself. Checked **first**, and
        # before the `opaque` fallback that would otherwise key it on `repr`:
        # `AnyCol.__repr__` names the path, table and column but not the
        # *language*, so `name["en"]` and `name["fr"]` produced the same key —
        # a cache serving one language's rows for another, which is precisely
        # what #270 exists to prevent.
        #
        # An extension point rather than a branch per type, so `_query_common`
        # stays ignorant of `sdmxlib.query` while its expressions stay
        # keyable.
        return value.__canonical__()
    if attrs.has(cls):
        return (
            "attrs",
            cls.__name__,
            tuple(
                (f.name, _field_key(cls.__name__, f.name, getattr(value, f.name))) for f in attrs.fields(cls) if f.eq
            ),
        )
    if isinstance(value, SqlRelation):
        # A foreign relation — statcatlib's, or anything else implementing the
        # protocol without being attrs. Its compiled SQL and binds are the only
        # stable description available, and are what a cache would key on.
        sql, params = value.to_sql()
        return ("relation", sql, tuple(canonical_key(p) for p in params))
    if isinstance(value, Enum):
        # The member's name, not its value: as unique within the class, and a
        # `str` where `Enum.value` is `Any`.
        return ("enum", cls.__name__, value.name)
    # `cls` is bound above rather than inline: `attrs.has` is a TypeGuard, and
    # applied to a discarded `type(value)` call expression it narrows nothing —
    # the case CLAUDE.md records under "Typed core, untyped shell".
    members = _as_members(value)
    if members is not None:
        return ("seq", tuple(canonical_key(v) for v in members))
    if isinstance(value, frozenset):
        return ("set", tuple(sorted(canonical_key(v) for v in value)))
    if isinstance(value, set):
        return ("set", tuple(sorted(canonical_key(v) for v in value)))
    return _scalar_key(value)


def _scalar_key(value: object) -> tuple[object, ...]:
    """`canonical_key` for the leaves.

    Split out to keep the dispatch under ruff's complexity ceiling once
    `SupportsCanonicalKey` was added.
    """
    if value is None:
        return ("none",)
    if isinstance(value, bool):
        # Before `int`: bool is a subclass, and tagging it separately keeps
        # `True` and `1` distinct in a key the way #272 keeps them distinct in
        # a comparison.
        return ("bool", value)
    if isinstance(value, str | int | float):
        return (type(value).__name__, value)
    # Anything else — a ref object a downstream library put in a predicate.
    # `repr` is a weak key, so say so rather than pretending otherwise.
    return ("opaque", type(value).__name__, repr(value))


def unwrap_order_key[K](key: K | Descending[K]) -> tuple[K, bool]:
    """Split a sort key into the ref and its direction.

    One place, because all five ``order_by`` implementations need it and a
    direction read differently in one of them is the kind of drift #267 was
    about.

    Args:
        key: A field ref, an aggregate ref, or either wrapped in `Descending`.

    Returns:
        ``(ref, ascending)``.
    """
    if isinstance(key, Descending):
        return key.key, False
    return key, True


def advance_window(start: int, length: int | None, n: int) -> tuple[int, int | None]:
    """Raise the window's floor to *n*, holding its ceiling fixed (#264).

    A query owns the half-open window ``[start, end)``. `offset` raises
    ``start``, `limit` lowers ``end``, and neither moves back — so every call
    narrows, which is the invariant `narrow_limit` was chosen to protect and
    the one #293 states in general.

    Holding the ceiling fixed is the whole content of this function, and it is
    the part that is easy to get wrong. Storing the window as
    ``(start, length)`` and raising ``start`` alone *slides* it:

        q.offset(5).limit(10)            rows 5..15
        q.offset(5).limit(10).offset(8)  rows 8..18   <- three rows it had excluded

    That is a caller undoing a helper's cap, which is exactly the failure
    `narrow_limit` exists to prevent, so the length shrinks by however far the
    floor moved.

    The cost, paid deliberately: `limit` and `offset` no longer commute.
    ``offset(5).limit(10)`` is "ten rows starting at five"; ``limit(10)
    .offset(5)`` is "of the first ten, skip five". Both read left to right and
    both are useful; the pair is the only order-sensitive composition in the
    DSL. It is not possible to have narrowing, idempotence and commutativity at
    once — SQL's ``LIMIT n OFFSET m`` buys commutativity by making ``n`` a
    length from ``m``, which is precisely what slides the window.

    Django reaches the same place from the other side: its slices compose
    relatively (``qs[5:15][2:4]`` is ``LIMIT 2 OFFSET 7``, a genuine subset)
    and it then *forbids* filtering or reordering a sliced query rather than
    living with the ambiguity.

    Args:
        start: The window's current floor.
        length: The current length, or ``None`` when unbounded.
        n: The requested floor.

    Returns:
        The new ``(start, length)``. ``length`` is clamped at zero, so an
        offset past the end yields an empty query rather than a negative
        ``LIMIT``.
    """
    new_start = max(start, n)
    if length is None:
        return new_start, None
    return new_start, max(0, length - (new_start - start))


def check_offset(n: object) -> None:
    """Reject an offset that is not a non-negative int (#264).

    Same guard as `check_limit`, for the same reason: ``limit("5")`` reached
    DuckDB and *worked*, which is worse than failing.

    Raises:
        TypeError: If *n* is not an ``int``.
        ValueError: If *n* is negative.
    """
    # bool is an int subclass, and `offset(True)` is never intended.
    if isinstance(n, bool) or not isinstance(n, int):
        msg = f"offset() takes an int, got {type(n).__name__}: {n!r}"
        raise TypeError(msg)
    if n < 0:
        msg = f"offset() takes a non-negative int, got {n}. To page backwards, lower the offset."
        raise ValueError(msg)


def narrow_limit(current: int | None, n: int) -> int:
    """Compose two limits: a cap never widens (#260).

    ``q.limit(5).limit(10)`` is five rows, not ten. Every composition method on
    these queries narrows — ``filter`` removes rows, ``order_by`` adds keys,
    ``limit`` lowers the cap — and that invariant is worth more than matching
    ``attrs.evolve``'s natural replace.

    Two concrete reasons beyond symmetry. polars, whose vocabulary this DSL
    borrows, composes the same way (``head(5).head(10)`` is five rows). And
    last-wins silently discards a *safety* cap: a helper returning
    ``q.limit(100)`` has that undone by a caller's ``.limit(10_000)``, which is
    the wrong direction for the mistake to run.

    SQLAlchemy replaces instead — its ``limit()`` is a generative setter. The
    two references genuinely disagree, so this is a decision rather than a
    lookup; it is recorded here and pinned by a test.
    """
    return n if current is None else min(current, n)


def check_limit(n: object) -> None:
    """Reject a limit that is not a non-negative int (#266).

    ``limit("5")`` reached DuckDB and *worked*, which is worse than failing.

    Raises:
        TypeError: If *n* is not an ``int``.
        ValueError: If *n* is negative.
    """
    # bool is an int subclass, and `limit(True)` is never intended.
    if not isinstance(n, int) or isinstance(n, bool):
        msg = f"limit() takes an int, got {type(n).__name__}."
        raise TypeError(msg)
    if n < 0:
        msg = f"limit() takes a non-negative int, got {n}."
        raise ValueError(msg)
