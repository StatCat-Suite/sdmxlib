"""What a backend can evaluate, and the program a plan becomes for it.

Step 2 of `docs/design/query-pushdown.md` §7. A `Plan` says what is being
asked; a `Capability` says what one backend can answer itself; `plan_program`
puts the two together and produces the ordered work. DuckDB's declaration is
total, so its program is a single `Push` and the emitted SQL does not move —
`tests/support/sql_corpus.py` diffing to nothing is the evidence, as it was for
step 1.

Two shapes here are deliberate rather than incidental, and both are §9's doing.

**The result is a sequence, not a `(pushed, residual)` pair.** For DuckDB every
program has length one, so the pair would look sufficient through steps 1 to 3 and
fail at step 4: REST answers `dataflow.dsd.components.concept.id == "X"` by
fetching the DSDs, collecting their urns, and filtering dataflows by
membership — two calls with a local step between (§9.1). A pair cannot say
that.

**Capability is keyed by path prefix, not by whole predicate.** Asking "can you
scan `dsd` filtered by `components.concept.id`?" is a different question from
"can you scan `dataflow` filtered by `dsd.components.concept.id`?", and it is
the first question that finds the rewrite above. Keying on the whole predicate
bakes in an atomicity REST does not have, and changes what the planner iterates
over — which is why it is here in step 2 rather than deferred (§9.1).

**A prefix is the declared edge route, not the attribute spelling.**
`q.dataflow.dsd.id` reads as one hop and resolves to two — `("dataflow",
"dsd")` — because `dsd` is a composite edge crossing the dataflow extension
table and back out to the spine. A declaration keyed on `("dsd",)` would match
nothing, and would do it silently, since an unmatched prefix is simply a
capability the backend does not have. Read the routes off `_edges.py` rather
than off the accessor surface when writing one.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Never, Protocol, cast, runtime_checkable

import attrs

from sdmxlib.model._label_match import Fold
from sdmxlib.model.datastructure import ComponentRole
from sdmxlib.query._expr import (
    AggCompare,
    AnyCol,
    AnyCols,
    ArrayHas,
    BoolOp,
    Cols,
    Compare,
    InRelation,
    InSet,
    IsNull,
    Match,
    Not,
    OrderKey,
    Pred,
    Quantified,
    RowIn,
    RowKind,
)
from sdmxlib.query._plan import Plan
from sdmxlib.query._rebase import RebaseError, rebase


@attrs.frozen
class PredicateShape:
    """One predicate's structure, with the literal removed.

    A capability is declared over shapes rather than over predicates because a
    backend's answer does not depend on the value: a registry that can filter
    by agency can filter by *any* agency. Dropping the literal is what makes a
    declaration finite and a test able to assert one.

    **The fold is part of the shape**, not a detail beside it. `_label_match`'s
    ladder is what pins matching in this library — `==` is case-sensitive,
    `.str.caseless` is not, `unaccented` and `folded` are label-only (#222,
    #255) — and all of that is pinned by SQL we emit. A predicate handed to a
    server is evaluated by rules we do not control, so a backend accepting
    `Match` at `Fold.NONE` has said nothing about `Fold.CASELESS`. Conflating
    them would let a case-sensitive server answer a caseless question and
    return too few rows, with nothing to say so (§9.3).

    Attributes:
        kind: The predicate class name — `"Compare"`, `"Match"`, `"BoolOp"`.
        path: Declared edge names from the scan root to the column, outermost
            first. Empty for a column on the root itself.
        column: Physical column name, or `""` where the kind has no single
            column (a `BoolOp`, a `RowIn` over several key columns).
        op: The operator, or `""` where the kind has none.
        fold: The matching strength. `Fold.NONE` for everything that is not a
            string comparison.
        inner: Shapes of a sub-tree written in a **different** frame of
            reference — a `Quantified`'s parts, whose paths are relative to
            the fan-out rather than to the scan root. Nested rather than
            flattened into the outer set, because a flat set silently claims
            the two are comparable: prefix analysis over
            `dataflow.dsd.dimensions.any(component.concept.id == "X")` reads
            `("concept",)` as a path from the dataflow and concludes the
            predicate spans two relations, when it does not. Empty for
            everything else, including `BoolOp` and `Not` — their parts share
            the parent's frame, so flattening those is correct.
    """

    kind: str
    path: tuple[str, ...] = ()
    column: str = ""
    op: str = ""
    fold: Fold = Fold.NONE
    inner: frozenset[PredicateShape] = frozenset()


def _of_column(kind: str, column: AnyCol, op: str = "", fold: Fold = Fold.NONE) -> PredicateShape:
    return PredicateShape(kind=kind, path=tuple(column.path), column=column.column, op=op, fold=fold)


def root_key(root: AnyCols) -> str:
    """What a backend calls this relation, when deciding whether it can scan it.

    Three sources, in order. `__resource__` when a root names itself — an
    observation root does, because it is built per binding and its table is one
    dataflow's rather than a relation any capability could enumerate. Otherwise
    a maintainable's `__constraint__`, which carries its artefact type, and that
    type *is* the SDMX REST resource — `/structure/dataflow/…`. Otherwise the
    table name, for an item relation that has neither; a backend not listing it
    is saying "I have no endpoint for these", which is the truth REST could not
    previously express.

    **Read off the instance, not the type.** Every other consumer does
    (`_compile` uses `root.__table__`), and while every root was a module
    singleton the difference was invisible. `Observations` is the first root
    built per binding, and `type(root).__table__` gives it the class default —
    the empty string, shared by every dataflow.
    """
    if root.__resource__ is not None:
        return root.__resource__
    constraint = root.__constraint__
    if constraint is not None:
        _column, values = constraint
        if len(values) == 1:
            return values[0]
    return root.__table__


def _leaf_shapes[R: RowKind](predicate: Pred[R]) -> frozenset[PredicateShape] | None:
    """The shapes of a predicate that has no sub-predicates, or `None`.

    Split from `shapes_of` so neither half is a ten-branch match. The division
    is the real one: a leaf's shape is read off its own fields, a composite's
    is its own plus its children's.
    """
    match predicate:
        case Compare(column=column, op=op, fold=fold):
            return frozenset({_of_column("Compare", column, op, fold)})
        case Match(column=column, op=op, fold=fold):
            return frozenset({_of_column("Match", column, op, fold)})
        case IsNull(column=column, negate=negate):
            return frozenset({_of_column("IsNull", column, op="is not null" if negate else "is null")})
        case ArrayHas(column=column):
            return frozenset({_of_column("ArrayHas", column)})
        case InSet(column=column):
            return frozenset({_of_column("InSet", column)})
        case InRelation(column=column):
            return frozenset({_of_column("InRelation", column)})
        case AggCompare(agg=agg, op=op):
            # The aggregate kind rides in `op` — `"count ="` — because the
            # comparison is against the aggregate, not against a column, and
            # `column` is a physical column name or nothing. One shape per
            # distinct fan-out path, since that is what a backend would be
            # agreeing to cross.
            return frozenset(
                PredicateShape(kind="AggCompare", path=tuple(column.path), op=f"{agg.kind} {op}")
                for column in agg.columns
            )
        case RowIn(negate=negate):
            # Several key columns and an already-compiled subquery, so no
            # column names it and the SQL is opaque to a declaration. Which
            # key columns, and how many, is not in the shape because no
            # declaration has had a use for it — add it when one does rather
            # than guessing the form now.
            return frozenset({PredicateShape(kind="RowIn", op="not in" if negate else "in")})
        case _:
            return None


def shapes_of[R: RowKind](predicate: Pred[R]) -> frozenset[PredicateShape]:
    """Every shape a predicate is built from, including its own.

    Recursive, and the whole tree matters: a backend that can filter by agency
    but cannot evaluate `OR` must not be handed `agency == "A" | agency == "B"`
    on the strength of its leaves. So a composite contributes its own shape
    *and* its children's, and a predicate pushes only when the backend accepts
    all of them.

    `Quantified` contributes **one** shape, carrying its parts under `inner`.
    A backend declaring it can evaluate a quantifier is declaring it can
    evaluate the whole subtree — there is no way to push half a correlated
    existence test — and the parts' paths are in the child's frame, so putting
    them in the outer set would make every path comparison over that set
    wrong. An earlier cut did flatten them, and the survey in
    `docs/design/query-pushdown.md` §11 is what found it.

    Returns:
        The shapes, deduplicated. A frozenset because order is not meaning and
        a capability check is a subset test.
    """
    leaf = _leaf_shapes(predicate)
    if leaf is not None:
        return leaf
    match predicate:
        case BoolOp(op=op, parts=parts):
            return frozenset({PredicateShape(kind="BoolOp", op=op)}).union(*(shapes_of(p) for p in parts))
        case Not(part=part):
            return frozenset({PredicateShape(kind="Not")}) | shapes_of(part)
        case Quantified(quantifier=quantifier, path=path, parts=parts):
            inner = frozenset[PredicateShape]().union(*(shapes_of(part) for part in parts))
            return frozenset({PredicateShape(kind="Quantified", path=tuple(path), op=quantifier, inner=inner)})
        case _:
            # Not a fallback that guesses: a new `Pred` subclass reaching here
            # would otherwise get an empty shape set and *push everywhere*,
            # since a capability check is a subset test and the empty set is a
            # subset of everything. Failing loudly is the only safe default.
            msg = (
                f"{type(predicate).__name__} has no declared shape, so no backend can "
                f"say whether it can evaluate it. Add a case to `shapes_of`."
            )
            raise NotImplementedError(msg)


@attrs.frozen
class Capability:
    """What one backend can evaluate itself.

    Data rather than code, so `explain()` can print it and a test can assert
    one. `total` is the DuckDB case and is a separate field rather than a
    magic empty mapping: "declares nothing" and "declares no limits" are
    opposite claims, and encoding them the same way is how a REST backend with
    a missing declaration would quietly acquire DuckDB's powers.

    Attributes:
        name: Backend name, for `explain()` and for error messages.
        total: The backend evaluates anything. `pushes` must then be empty —
            a declaration beside `total` is dead text that will drift.
        pushes: Shapes the backend accepts, keyed by the path prefix of the
            scan they are asked about. `()` is the root scan.
        orders: Whether `ORDER BY` survives a push.
        slices: Whether `LIMIT` / `OFFSET` survive a push. Note §5: a slice is
            only *pushed* when nothing is left over, whatever this says.
        prefetch: Path prefixes the backend can return *alongside* the scanned
            artefacts in one request — SDMX REST's `references=<type>`. This
            is what makes projection over a crossed path affordable: §11
            measured `/dataflow/ECB?references=datastructure` returning 102
            dataflows and 81 DSDs in one response, against 82 requests to
            resolve them one at a time.

            Distinct from `pushes`, and the distinction is the useful one: a
            prefetched prefix yields *values*, but the backend still cannot
            *filter* on it. So a projection naming `dsd.id` is served by
            prefetch, while a filter on `dsd.id` is better served by the §9.1
            rewrite, whose inner scan the filter narrows.

            Never expanded to "everything reachable". `references=all` on one
            bounded artefact returned **29.8 MB** in §11's measurement — a
            single codelist with 143,146 codes — so a planner asks for the
            prefixes a projection names and no others.
        scans: Roots this backend can scan at all, by `root_key`. REST has no
            endpoint for "all codes" — items arrive inside their parent — and
            without this the planner read `q.code` as a scan that merely
            happened to be unbounded, refusing it with a message telling the
            caller to add an agency that would not have helped (§10).
        bounding: What must be pushed for a scan to count as bounded (§5),
            **keyed by root**. Per root rather than one flat set, because the
            bound is not always a filter on the root itself: a codelist is
            bounded by its own agency, while a *code* would be bounded by its
            codelist's — `GET /codelist/ESTAT/CL_FREQ` returns codes, and a
            single set could only ever say "agency on the root".

            **Three states, and the middle one is the point.**

            * **Absent** — nothing bounds this root, so every residual over it
              is refused. Not the vacuous "everything is bounded" that `all()`
              would otherwise hand a backend nobody has declared.
            * **Present and empty** — *any* pushed constraint bounds it. The
              `/data` case: SDMX-REST puts request size on the **server**
              (413 "Request entity too large", 414), so a client-side refusal
              is ours to justify, and what it justifiably catches is a plan
              that pushes *nothing* and fetches everything to filter locally.
              A plan that pushed something has a fetch the caller named, and
              the provider caps the rest.
            * **Present and non-empty** — all of these shapes must be pushed.
              All rather than any, so a backend needing both an agency and an
              id states both and neither alone opens the door.

            Absent and empty were one state until the data plane needed the
            middle one: `bounding.get(root, frozenset())` collapsed them,
            which made "requires nothing" unsayable.
            Ignored when `total`, which is bounded by definition:
            DuckDB's unbounded scan is a table scan, not a fetch of every
            artefact every agency has ever published.
    """

    name: str
    total: bool = False
    pushes: Mapping[tuple[str, ...], frozenset[PredicateShape]] = attrs.field(factory=dict)
    orders: bool = True
    slices: bool = True
    scans: frozenset[str] = frozenset()
    bounding: Mapping[str, frozenset[PredicateShape]] = attrs.field(factory=dict)
    prefetch: frozenset[tuple[str, ...]] = frozenset()

    def __attrs_post_init__(self) -> None:
        """Refuse a declaration beside `total`, where it would be dead text.

        `__attrs_post_init__` rather than an `@pushes.validator`: the
        decorator form is untyped from both checkers' point of view — the
        attribute is inferred as its `factory`'s type, so `pushes.validator`
        reads as an attribute on `dict`. Same check, statically visible.
        """
        if self.total and self.pushes:
            msg = f"{self.name} declares total capability and also lists shapes; the list would never be read."
            raise ValueError(msg)

    def accepts(self, prefix: tuple[str, ...], shapes: Iterable[PredicateShape]) -> bool:
        """Whether this backend can evaluate `shapes` on a scan rooted at `prefix`."""
        if self.total:
            return True
        declared = self.pushes.get(prefix, frozenset())
        return all(shape in declared for shape in shapes)

    def can_project(self, prefixes: Iterable[tuple[str, ...]]) -> bool:
        """Whether one request can supply values at all of `prefixes`.

        The question `select()` and `group_by()` ask. Projection never pushes
        — no registry computes `count(*)` for us — but that does not make it
        unavailable: artefacts become rows locally, and a crossed path's
        values arrive in the same response when the backend can prefetch that
        prefix. So this decides the *request*, not who does the arithmetic.
        """
        if self.total:
            return True
        return all(prefix == () or prefix in self.prefetch for prefix in prefixes)


#: Prefix marking a column name that has been replaced by a **role**.
#:
#: `@` cannot begin a bare SQL identifier, so a physical column can never
#: collide with a role by being named after one. Cheap insurance: a binding
#: chooses its own column names, and a table with a `key_dimension` column
#: would otherwise have that column silently accepted by a data capability.
ROLE_SIGIL = "@"

#: What a backend calls the observation relation — `Observations.__resource__`,
#: and the SDMX REST `/data` resource. A constant rather than an import so the
#: capability layer does not depend on the accessor layer.
DATA_ROOT = "data"


def _role_shape(kind: str, role: ComponentRole, op: str = "") -> PredicateShape:
    """A shape declared over a role rather than a column name."""
    return PredicateShape(kind=kind, column=f"{ROLE_SIGIL}{role.value}", op=op)


@runtime_checkable
class SupportsRoles(Protocol):
    """A root that can say what part of the shape one of its columns plays.

    Structural roots do not implement this and do not need to: their columns
    are fixed by the schema, so a capability names them directly. An
    observation root does, because its columns come from a DSD.
    """

    def role_of(self, column: str, /) -> ComponentRole | None: ...


def shapes_for[R: RowKind](predicate: Pred[R], root: AnyCols) -> frozenset[PredicateShape]:
    """`shapes_of`, with column names replaced by roles where the root has them.

    The one place the two capability styles meet. A structural root is returned
    unchanged — `agency_id` means the same thing on every maintainable, so the
    declaration names it. An observation root's columns come from a DSD, so
    each is rewritten to `@key_dimension`, `@time_dimension` and so on, and the
    data capability is declared over those.

    **A column with no role keeps its physical name, and that is the failure
    mode we want.** No data capability declares a bare column name, so an
    unmapped column — or any column at all when the root has no DSD — simply
    fails the subset test and becomes a residual. Nothing is pushed on a guess.
    """
    shapes = shapes_of(predicate)
    if not isinstance(root, SupportsRoles):
        return shapes
    return frozenset(_in_role_space(shape, root) for shape in shapes)


def _in_role_space(shape: PredicateShape, root: SupportsRoles) -> PredicateShape:
    """One shape with its column swapped for the role that column plays."""
    inner = frozenset(_in_role_space(part, root) for part in shape.inner) if shape.inner else shape.inner
    if not shape.column:
        return attrs.evolve(shape, inner=inner) if inner is not shape.inner else shape
    role = root.role_of(shape.column)
    column = f"{ROLE_SIGIL}{role.value}" if role is not None else shape.column
    return attrs.evolve(shape, column=column, inner=inner)


#: What the SDMX REST **data** endpoint can evaluate, read off the URL:
#: `/data/dataflow/{agency}/{id}/{version}/{key}?startPeriod=…&c[…]=…`.
#:
#: Richer than the structural declaration, which is worth stating because the
#: two planes are usually assumed to be the other way round. `/structure` has
#: no predicate slot at all beyond identity; `/data` has a positional key that
#: takes equality or membership on every key dimension, and period parameters
#: that take a **range** on time.
#:
#: Declared over roles rather than column names (`shapes_for`), because an
#: observation's columns come from a DSD.
#:
#: `>` and `<` are absent deliberately: `startPeriod` / `endPeriod` are
#: *inclusive* bounds, so a strict comparison cannot be expressed exactly and
#: pushing it would return one period too many. Under-declaring costs a round
#: trip; over-declaring returns the wrong rows.
SDMX_REST_DATA_KEY = frozenset(
    {
        _role_shape("Compare", ComponentRole.KEY_DIMENSION, "="),
        _role_shape("InSet", ComponentRole.KEY_DIMENSION),
        # Time equality is expressible on both versions, by different means —
        # a key segment on 2.2.2, `startPeriod == endPeriod` on 1.5 — so it
        # belongs in the shared set. Membership does not; see below.
        _role_shape("Compare", ComponentRole.TIME_DIMENSION, "="),
        _role_shape("Compare", ComponentRole.TIME_DIMENSION, ">="),
        _role_shape("Compare", ComponentRole.TIME_DIMENSION, "<="),
    }
)

#: SDMX-REST 1.5 / 2.1: the key and the period parameters, and nothing else.
#:
#: **No membership on time**, which is the version difference nobody expects
#: (#337). Here the time dimension sits *outside* the dotted key — sdmx1 skips
#: it when building a query string for the same reason, commenting
#: "TimeDimensions handled by query parameters" — so there is no `+`-joined
#: segment to put values in, and `startPeriod`/`endPeriod` is one *contiguous*
#: range. `is_in(["2019", "2021"])` would silently acquire 2020.
SDMX_REST_DATA_21 = Capability(
    name="sdmx-rest-data-2.1",
    pushes={(): SDMX_REST_DATA_KEY},
    orders=False,
    slices=False,
    scans=frozenset({DATA_ROOT}),
    # Declared and empty: *any* pushed constraint bounds a data scan. The
    # standard puts request size on the server — 413, 414 — so the only thing
    # a client-side refusal justifiably catches is a plan that pushes nothing
    # and fetches the whole flow to filter locally.
    bounding={DATA_ROOT: frozenset()},
)

#: SDMX-REST 3.0 adds `c[<component>]=<values>`, which constrains components
#: outside the key — `c[GEO_LEVEL]=FED` is the example `sdmxlib.rest` documents
#: from the parsing side.
#:
#: **Attributes only, not measures.** `c[…]` is defined over any component, but
#: a filter on a measure is a filter on a *value*, and provider support for
#: that is not something this declaration can vouch for. The same conservatism
#: as `>` above: a missing shape costs a local filter, a wrong one costs
#: correctness.
#:
#: It also regains **membership on time**, because 2.2.2 made the time
#: dimension a key dimension — so it has a segment, and a segment takes
#: `+`-joined values (#337).
SDMX_REST_DATA_30 = Capability(
    name="sdmx-rest-data-3.0",
    pushes={
        (): SDMX_REST_DATA_KEY
        | {
            _role_shape("InSet", ComponentRole.TIME_DIMENSION),
            _role_shape("Compare", ComponentRole.ATTRIBUTE, "="),
            _role_shape("InSet", ComponentRole.ATTRIBUTE),
        }
    },
    orders=False,
    slices=False,
    scans=frozenset({DATA_ROOT}),
    bounding={DATA_ROOT: frozenset()},
)


#: What the SDMX REST structure endpoint can evaluate, read off the URL:
#: `/structure/{resource}/{agency}/{id}/{version}`. The resource is the
#: accessor's own `__constraint__` rather than a predicate, which leaves
#: equality on three path segments — and that is the entire declaration.
#:
#: Six entries, against a machine of several hundred lines. That ratio is the
#: design working rather than a sign it is overbuilt: what the machinery
#: carries is the §9.1 rewrite and the §5 refusal, not the pushing. The §10
#: survey measured the consequence — 11 of 23 real question shapes are served
#: (`test_query_rest_survey.py` pins it), and each refusal names a fix.
#:
#: Prefixes are the *declared edge routes* to other maintainables, not the
#: attribute spellings: `q.dataflow.dsd` is `("dataflow", "dsd")`.
SDMX_REST_MAINTAINABLE = frozenset(
    {
        PredicateShape(kind="Compare", column="agency_id", op="="),
        PredicateShape(kind="Compare", column="resource_id", op="="),
        PredicateShape(kind="Compare", column="version", op="="),
    }
)

SDMX_REST_PREFIXES = (
    (),
    ("dataflow", "dsd"),
    ("codelist",),
    ("concept",),
    ("scheme",),
    ("dsd",),
    ("described_by",),
    ("structure",),
    ("msd",),
)


def _derived_roots() -> frozenset[str]:
    """Every root that is scannable but not one endpoint (`__derived_from__`).

    Derived from the accessors for the same reason `_maintainable_roots` is:
    a relation gains an endpoint by declaring where its rows come from, not by
    someone remembering to extend a list here.
    """
    from sdmxlib.query import accessors  # noqa: PLC0415  (cycle: accessors has no need of us)

    roots: set[str] = set()
    for name in dir(accessors.q):
        if name.startswith("_"):
            continue
        value: object = getattr(accessors.q, name, None)
        if isinstance(value, AnyCols) and value.__derived_from__ is not None:
            roots.add(root_key(value))
    return frozenset(roots)


def _maintainable_roots() -> frozenset[str]:
    """Every artefact type `sl.q` exposes as a root — REST's resource list.

    Derived from the accessors rather than typed out: `/structure/{resource}`
    accepts every maintainable, so a new artefact type gaining an accessor
    gains an endpoint, and a hand-written list would be one more thing to
    forget. The item relations fall out by having no artefact-type constraint.
    """
    from sdmxlib.query import accessors  # noqa: PLC0415  (cycle: accessors has no need of us)

    roots: set[str] = set()
    for name in dir(accessors.q):
        if name.startswith("_"):
            continue
        value: object = getattr(accessors.q, name, None)
        if isinstance(value, AnyCols) and value.__constraint__ is not None:
            roots.add(root_key(value))
    return frozenset(roots)


SDMX_REST = Capability(
    name="sdmx-rest",
    pushes=dict.fromkeys(SDMX_REST_PREFIXES, SDMX_REST_MAINTAINABLE),
    # No ORDER BY in the URL, and no LIMIT: the endpoint returns the set.
    orders=False,
    slices=False,
    # A derived root scans too: it has no endpoint of its own, and several
    # instead — `_rest.request_for` expands it into one request per source
    # relation. Refusing it here would be the wrong answer for the same reason
    # the old refusal message was: there *is* a fetch, it is just not one.
    scans=_maintainable_roots() | _derived_roots(),
    # Every maintainable is bounded the same way — by its agency, the second
    # path segment. Per root even so, because the shape of the answer is what
    # differs for an item relation, and a flat set could not say it.
    #
    # A derived root is bounded identically, and that is why the view carries
    # an `agency_id` column rather than making the caller reach through a hop:
    # every request the expansion issues takes the same agency segment, so one
    # equality bounds all three.
    bounding=dict.fromkeys(
        _maintainable_roots() | _derived_roots(),
        frozenset({PredicateShape(kind="Compare", column="agency_id", op="=")}),
    ),
    # `references=<type>` — the server-side join §9 was written without. Only
    # the prefixes that name another maintainable, and never `references=all`.
    prefetch=frozenset(SDMX_REST_PREFIXES) - {()},
)


#: DuckDB runs the whole language: it is the backend the language was compiled
#: against, so anything expressible is pushable. Declared rather than assumed,
#: which is what makes the planner's no-op visible instead of implicit.
DUCKDB = Capability(name="duckdb", total=True)


@attrs.frozen
class Membership:
    """Links one `Push` to an earlier one: keep rows whose key it returned.

    Named for what §9.1 calls it — "apply it as a set membership" — and not
    `Match`, which is already the string-comparison predicate one import away.

    The second half of the §9.1 rewrite. A property of the *program* rather
    than a predicate, deliberately: nobody wrote it, the planner produced it,
    and putting it in the predicate language would mean a `Pred` that only a
    planner can build and only one backend can read. `explain()` prints it as
    a step, which is what it is.

    **A local step, not a pushed one.** Step 2 put this on `Push`, reasoning
    that a membership is how the second call is *narrowed*. §1 and §6 of the
    design both draw it as a `LOCAL` step, and they are right: no SDMX REST
    URL accepts a set of urns, so the set can only be applied to rows already
    returned. Putting it on `Push` would also let it read as a bounding
    constraint, and it is not one — the outer scan still needs its own agency.
    A backend that genuinely *can* push a membership would declare that, and
    the field would move then rather than being assumed now.

    **The reference is a column expression, not a column name.** The obvious
    encoding — a physical column on the scan's root — is wrong here:
    `q.dataflow.dsd.urn` trims to `dataflow.dsd_urn`, on the dataflow
    *extension* table, one hop from the `artefact` row the scan is rooted at.
    That split is a storage decision, and a REST backend does not share it —
    the reference is simply a field of the artefact it returns. Carrying the
    column expression keeps both readings available and reuses the trimming
    the language already does, rather than re-deriving foreign keys here.

    Attributes:
        column: The reference on this scan's side, as the language addresses
            it — `q.dataflow.dsd.urn`.
        step: Index into `Program.steps` of the `Push` that supplies values.
        source: Physical column of that step's rows to match against, which is
            the target of the same edge — `urn`.
    """

    column: AnyCol
    step: int
    source: str


@attrs.frozen
class Push[R: RowKind]:
    """One backend call: a plan the backend accepts in full."""

    plan: Plan[R]


class PushdownError(ValueError):
    """A query cannot be run against this backend without fetching too much.

    Raised rather than answered, per §5 and the whole of %11: a valid result
    or an error naming the problem, never a plausible-looking wrong one. The
    problem here is invisible in the result — the rows come back correct, just
    at the cost of having fetched every artefact in the registry to filter
    three of them locally.
    """


@attrs.frozen
class Residual[R: RowKind]:
    """Work left to us, over the rows the previous step returned.

    Carries the window and the sort as well as the predicates, because a slice
    cannot be pushed past a filter the backend did not run: fetching 10 rows
    and *then* filtering yields fewer than 10, silently (§5). So when anything
    is residual, the slice comes local with it.

    Attributes:
        predicates: Evaluated locally, in the order they were written.
        member_of: Keep rows whose reference is among an earlier step's — the
            second half of the §9.1 rewrite.
        order: The sort, when the backend does not sort.
        limit_n: The window size, when the slice could not be pushed.
        offset_n: The window start, likewise.
    """

    predicates: tuple[Pred[R], ...] = ()
    member_of: Membership | None = None
    order: tuple[OrderKey, ...] = ()
    limit_n: int | None = None
    offset_n: int = 0

    def is_empty(self) -> bool:
        """Whether this step would do nothing — a program should not carry it."""
        return not (self.predicates or self.member_of or self.order) and self.limit_n is None and not self.offset_n


@attrs.frozen
class Program[R: RowKind]:
    """The ordered work a plan becomes for one backend.

    A sequence rather than a pair, for the reason in this module's docstring:
    the shape has to hold a rewrite that issues two calls with a local step
    between, and it must hold it *before* such a backend exists, or the pair
    survives to step 4 and everything built on it has to be rebuilt.

    Attributes:
        backend: The capability's name.
        steps: `Push` and `Residual` in execution order.
    """

    backend: str
    steps: tuple[Push[R] | Residual[R], ...]

    @property
    def pushes(self) -> tuple[Push[R], ...]:
        """Just the backend calls — how many round trips this program costs."""
        return tuple(step for step in self.steps if isinstance(step, Push))

    @property
    def residuals(self) -> tuple[Residual[R], ...]:
        """Just the local steps."""
        return tuple(step for step in self.steps if isinstance(step, Residual))


def is_bounded[R: RowKind](push: Push[R], capability: Capability) -> bool:
    """Whether one backend call is a request a user could reasonably issue.

    §5's rule, and the reason it is a function of a single `Push` rather than
    of the program: a rewrite that bounds the *outer* scan by a set computed
    from an unbounded *inner* one satisfies any whole-program check while
    having moved the catastrophe one step down, where it is harder to see. So
    every scan carries the bound on its own terms. Only one scan is produced
    today, which makes this trivially true and is exactly why it is worth
    writing now — retrofitting it after the §9.1 rewrite lands means auditing
    a rewrite rather than a scan.
    """
    if capability.total:
        return True
    required = capability.bounding.get(root_key(push.plan.root))
    if required is None:
        # Undeclared: nothing bounds this root, so no scan of it is ever
        # bounded and every residual is refused. The reading `all()` gives for
        # free is the opposite one — vacuously true, so an undeclared root
        # would grant unlimited residuals — and it is the more dangerous of
        # the two by exactly the margin this rule exists to protect. Same
        # trap as `total` versus an empty `pushes`, one field over.
        return False
    pushed = frozenset[PredicateShape]().union(
        frozenset[PredicateShape](), *(shapes_of(p) for p in push.plan.predicates)
    )
    if not required:
        # Declared, unspecific: any pushed constraint bounds the scan, and what
        # is refused is the plan that pushed *nothing*.
        return bool(pushed)
    return all(shape in pushed for shape in required)


def _refuse[R: RowKind](capability: Capability, plan: Plan[R], residual: Sequence[Pred[R]]) -> Never:
    """Raise, naming what could not be pushed and what would make it legal."""
    kinds = ", ".join(sorted({shape.kind for p in residual for shape in shapes_of(p)}))
    required = capability.bounding.get(root_key(plan.root))
    relation = type(plan.root).__name__.lstrip("_").lower()
    if required is not None and not required:
        # The unspecific bound: nothing in particular is wanted, so naming a
        # shape would be a lie. What is missing is *any* constraint at all.
        remedy = "constrain something the backend can evaluate"
    else:
        wanted = ", ".join(sorted(f"{s.column or s.kind} {s.op}".strip() for s in required or ()))
        remedy = f"add {wanted or 'a bounding filter'}"
    msg = (
        f"{capability.name} cannot evaluate {kinds} on {relation}, "
        f"so those rows would be filtered locally — over every row the scan returns. "
        f"The scan is unbounded: {remedy} so the backend "
        f"returns a set a caller could have asked for, then filter within it."
    )
    raise PushdownError(msg)


def _relations(root: AnyCols) -> dict[tuple[str, ...], AnyCols]:
    """Declared relations reachable from `root`, keyed by their edge route.

    Read off the accessor rather than the edge graph because the accessor
    supplies both halves at once: the route (`q.dataflow.dsd.path` is
    `("dataflow", "dsd")`, two hops for one attribute) *and* the object a
    sub-scan would be rooted at. Deriving the route from `_edges.py` and then
    hunting for the matching accessor would be the same information twice.
    """
    found: dict[tuple[str, ...], AnyCols] = {}
    for name in dir(type(root)):
        if name.startswith("_"):
            continue
        value: object = getattr(root, name, None)
        if isinstance(value, AnyCols) and value.path:
            found[tuple(value.path)] = value
    return found


def _prefix_of[R: RowKind](predicate: Pred[R], candidates: Mapping[tuple[str, ...], AnyCols]) -> tuple[str, ...] | None:
    """The single declared relation this predicate lives entirely under.

    `None` when it touches the root's own columns, or spans two relations —
    neither can move into one sub-scan. Longest prefix first, so a predicate
    under `dsd.dimensions` re-roots at the most specific relation that holds
    it rather than the first one that happens to match.

    `PredicateShape.inner` is what makes this answerable at all: a
    `Quantified`'s parts are pathed from the fan-out rather than from the
    root, and while they sat in the outer shape set this read
    `dataflow.dsd.dimensions.any(component.concept.id == "X")` as spanning
    two relations and declined every time (§10).
    """
    paths = {shape.path for shape in shapes_of(predicate)}
    if not paths or () in paths:
        return None
    for prefix in sorted(candidates, key=len, reverse=True):
        if all(path[: len(prefix)] == prefix for path in paths):
            return prefix
    return None


def _rewrite[R: RowKind, C: RowKind](
    root: AnyCols,
    prefix: tuple[str, ...],
    predicates: Sequence[Pred[R]],
    capability: Capability,
) -> tuple[tuple[Push[C] | Residual[C], ...], Membership] | None:
    """The §9.1 rewrite for one group: a sub-program, and the link back to it.

    `None` when the group cannot be re-rooted, or when the resulting scan
    would be one the backend must refuse — an unbounded inner scan is the
    catastrophe moved one step down, which §5's per-scan bound exists to catch.

    The whole group goes into **one** sub-scan. That is the difference between
    this working and not: `dsd.agency == "ESTAT"` is what bounds the scan that
    `dsd.dimensions.any(...)` filters, and neither predicate is plannable on
    its own.
    """
    relations = _relations(root)
    # The one dynamic boundary in the planner. `_relations` reads accessors
    # off `dir()`, so the checker sees `AnyCols` and the concrete row kind is
    # not recoverable — there is no runtime probe that could narrow it, since
    # the parameter is a phantom. Cast one line from the `isinstance` that
    # established it is an accessor at all, per the typed-core/untyped-shell
    # rule; `C` is then bound consistently across the rebase and the sub-plan.
    sub_root = cast("Cols[C]", relations[prefix])
    try:
        rebased: tuple[Pred[C], ...] = tuple(rebase(predicate, prefix) for predicate in predicates)
    except RebaseError:
        return None
    try:
        sub = plan_program(Plan(root=sub_root, predicates=rebased), capability)
    except PushdownError:
        return None

    # The key both sides agree on. A composite key has no single-column
    # membership to express, so decline rather than emit something that would
    # match on one column of several.
    keys = sub_root.__key__
    if len(keys) != 1:
        return None
    reference: object = getattr(sub_root, keys[0], None)
    if not isinstance(reference, AnyCol):
        return None
    # `step` is filled in by the caller, which knows where these land.
    return sub.steps, Membership(column=reference, step=-1, source=reference.column)


def _lift_residuals[R: RowKind](
    root: AnyCols,
    residual: list[Pred[R]],
    capability: Capability,
) -> tuple[list[Push[R] | Residual[R]], list[Membership]]:
    """Move whatever residuals can become sub-scans, in place.

    Predicates are grouped by the relation they live under and each group
    becomes **one** sub-scan, which is what makes the headline query work:
    `dsd.agency == "ESTAT"` bounds the scan that `dsd.dimensions.any(...)`
    filters, and neither is plannable alone. Anything that does not fit is
    left in `residual` for the caller to evaluate locally.

    Split out of `plan_program` to keep either half under a reader's working
    memory, not only ruff's complexity limit.
    """
    steps: list[Push[R] | Residual[R]] = []
    memberships: list[Membership] = []
    if not residual or capability.total:
        return steps, memberships

    relations = _relations(root)
    grouped: dict[tuple[str, ...], list[Pred[R]]] = {}
    for predicate in residual:
        prefix = _prefix_of(predicate, relations)
        if prefix is not None:
            grouped.setdefault(prefix, []).append(predicate)

    for prefix, group in grouped.items():
        rewritten = _rewrite(root, prefix, group, capability)
        if rewritten is None:
            continue
        sub_steps, membership = rewritten
        steps.extend(sub_steps)
        memberships.append(attrs.evolve(membership, step=len(steps) - 1))
        for predicate in group:
            residual.remove(predicate)
    return steps, memberships


def plan_program[R: RowKind](plan: Plan[R], capability: Capability) -> Program[R]:
    """Split `plan` against what `capability` declares.

    For a total backend this is one `Push` carrying the plan unchanged — the
    no-op that keeps the SQL corpus byte-identical.

    For a partial one: push the predicates it accepts, keep the rest local,
    and refuse outright when the resulting scan is not bounded. **The rule is
    about boundedness, not about residuals** (§5). Refusing every residual
    would reject `agency == "ESTAT"` plus a local name filter, a perfectly good
    query over a few hundred rows; allowing every residual is the
    800-resolutions problem arrived at silently.

    The slice and the sort come local whenever anything else does. A `LIMIT`
    pushed past a filter the backend did not run returns fewer rows than asked
    for and says nothing — the same reasoning `Query._refuse_after_slice`
    applies within one backend, reused rather than restated.

    Args:
        plan: The question.
        capability: What the backend running it declares.

    Returns:
        The program, its `steps` in execution order.

    Raises:
        PushdownError: If a residual would run over an unbounded scan.
    """
    if not capability.total and root_key(plan.root) not in capability.scans:
        # No endpoint exists for this relation, so there is no request to
        # make and no filter that would create one. Refused before anything
        # else, because every message below would otherwise name a fix — "add
        # an agency" — that cannot help.
        relation = type(plan.root).__name__.lstrip("_").lower()
        msg = (
            f"{capability.name} cannot scan {relation}: it has no endpoint for that relation. "
            f"Items are returned inside the artefact that owns them, so root the query at "
            f"the owner and project or filter from there."
        )
        raise PushdownError(msg)

    residual: list[Pred[R]] = []
    pushed: list[Pred[R]] = []
    for predicate in plan.predicates:
        target = pushed if capability.accepts((), shapes_of(predicate)) else residual
        target.append(predicate)

    # Move what is left into sub-scans where it fits. Only for a partial
    # backend: a total one accepted everything above, and rewriting for it
    # would emit two statements where its own planner does better with one.
    before, memberships = _lift_residuals(plan.root, residual, capability)

    # Order and slice are pushed only when the backend can take them *and*
    # nothing is left over. `local_window` is the difference between the two
    # questions: `capability.slices` says the wire format has a LIMIT, the
    # residual says whether pushing it would still mean the same thing.
    local_window = bool(residual) or bool(memberships) or not capability.slices
    local_order = bool(residual) or bool(memberships) or not capability.orders

    scan = attrs.evolve(
        plan,
        predicates=tuple(pushed),
        order_keys=() if local_order else plan.order_keys,
        default_order=() if local_order else plan.default_order,
        limit_n=None if local_window else plan.limit_n,
        offset_n=0 if local_window else plan.offset_n,
    )
    push = Push(scan)
    # A membership is local work too, so it puts the scan under the same bound
    # a residual does — and the case is easy to miss, because lifting every
    # residual into a sub-scan leaves `residual` empty. The §10 headline query
    # filters only on `dsd`, so after the rewrite the outer scan carries no
    # predicate at all: `GET /dataflow` for every agency, then a local
    # membership. That is precisely §5's "bounded per scan, not per program",
    # arriving from the direction the rule was not written for — the *outer*
    # scan unbounded rather than the inner one.
    if (residual or memberships) and not is_bounded(push, capability):
        _refuse(capability, plan, residual or list(plan.predicates))

    steps: list[Push[R] | Residual[R]] = [*before, push]
    steps.extend(Residual(member_of=membership) for membership in memberships)
    left_over = Residual(
        predicates=tuple(residual),
        order=plan.order if local_order else (),
        limit_n=plan.limit_n if local_window else None,
        offset_n=plan.offset_n if local_window else 0,
    )
    if not left_over.is_empty():
        steps.append(left_over)
    return Program(backend=capability.name, steps=tuple(steps))
