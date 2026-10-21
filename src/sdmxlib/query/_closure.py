"""What a fetch has to contain for a query to be answerable locally.

§13 of `docs/design/query-pushdown.md`. A backend that computes nothing
answers by fetching artefacts and letting DuckDB evaluate the original query,
so the fetch must **close over every relation the query's paths reach**. Fetch
one relation short and the query does not fail — it evaluates against an empty
table and returns nothing, which is the design's central failure mode arriving
through the back door.

Measured on the recorded ECB responses: `/dataflow/ECB?references=datastructure`
returns 102 dataflows and 81 DSDs and resolves **zero** concept ids, because
the concept schemes those DSDs reference were never fetched. The same query
against a `references=descendants` fetch resolves 31.

**Containment is not a crossing, and telling them apart is the whole job.**
`dsd.dimensions` stays inside the DSD — components arrive with it, and no
extra fetch is needed. `component.concept` leaves for a different artefact,
the concept scheme, and does need one. Counting hops would conflate the two
and ask for `descendants` everywhere; counting *crossings* asks for the
cheapest fetch that closes.
"""

from collections.abc import Iterable

from sdmxlib.query._edges import Edge
from sdmxlib.query._expr import (
    AggCompare,
    AnyCol,
    AnyPred,
    ArrayHas,
    BoolOp,
    Compare,
    InRelation,
    InSet,
    IsNull,
    Match,
    Not,
    Pred,
    RowKind,
    WinCompare,
    WinPred,
)
from sdmxlib.query._joins import resolve_path
from sdmxlib.query._plan import Plan
from sdmxlib.query._pushdown import shapes_of


def _is_crossing(edge: Edge, *, forward: bool) -> bool:
    """Whether walking `edge` leaves the artefact currently held.

    A **reverse** hop is containment: the far rows are keyed by this
    artefact's urn and arrive inside it — `dsd.dimensions`, `codelist.codes`.

    A **forward** hop crosses unless it is the artefact-to-extension identity:
    `artefact.urn → dataflow.urn` is the same artefact wearing a second table,
    and costs no fetch. Every other forward hop reads a *reference* —
    `dataflow.dsd_urn → artefact.urn`, `dsd_component.concept_urn →
    concept.concept_urn` — and the row it reaches belongs to an artefact we do
    not hold.

    Note the second example: the target table is `concept`, not `artefact`, so
    a rule keyed on "does this land on the spine" would call it containment
    and fetch one relation short. What decides it is that the key being
    followed is the *target's* identity rather than the source artefact's.
    """
    if not forward:
        return False
    return tuple(edge.columns) != (("urn", "urn"),)


def crossings(root_table: str, path: tuple[str, ...]) -> int:
    """How many artefacts a path leaves the root to reach.

    Zero for a column on the root or on its own extension row; one for a
    to-one reference like `dataflow.dsd`; two for `dataflow.dsd.…concept`,
    which reaches the concept scheme through the DSD.

    Args:
        root_table: Relation the path starts from.
        path: Declared hop names, outermost first.

    Returns:
        The count. It is what a `references=` depth has to cover.
    """
    return sum(1 for hop in resolve_path(root_table, path) if _is_crossing(hop.edge, forward=hop.forward))


def reached_paths[R: RowKind](predicate: Pred[R]) -> frozenset[tuple[str, ...]]:
    """Every path a predicate touches, absolute from the scan root.

    A `Quantified`'s parts are pathed from the fan-out rather than from the
    root, so they are concatenated onto the quantifier's own path here — which
    is what `PredicateShape.inner` keeps them separate for. Flattened, a
    predicate over `dataflow.dsd.dimensions.any(component.concept.id == …)`
    would report `("concept",)` as a path from the dataflow and the closure
    would be computed for the wrong relation entirely.
    """
    paths: set[tuple[str, ...]] = set()
    for shape in shapes_of(predicate):
        paths.add(shape.path)
        for nested in shape.inner:
            paths.add(shape.path + nested.path)
    return frozenset(paths)


def _window_paths[R: RowKind](qualifier: WinPred[R]) -> frozenset[tuple[str, ...]]:
    """Paths a windowed predicate reaches — its column, source and partition.

    `WinPred` is a separate hierarchy from `Pred`, deliberately: a windowed
    comparison is not valid in a `WHERE`, and the type is what keeps the two
    clauses apart. So `shapes_of` does not accept one, and the partition is
    the part that matters here — `rank().over(q.dataflow.dsd.agency)` reaches
    across an edge without any filter saying so.
    """
    if not isinstance(qualifier, WinCompare):
        return frozenset()
    window = qualifier.column
    columns: tuple[AnyCol, ...] = (window, *window.source, *window.partition)
    return frozenset(tuple(column.path) for column in columns)


def closure[R: RowKind](plan: Plan[R]) -> frozenset[tuple[str, ...]]:
    """Every path this plan reaches — filters, sort keys and window alike.

    Not only the filters: a sort key crossing an edge needs that edge's rows
    present just as much, and so does anything a `Select` projects (§11). The
    caller adds projected paths; this covers the plan itself.
    """
    paths: set[tuple[str, ...]] = set()
    for predicate in plan.predicates:
        paths |= reached_paths(predicate)
    for qualifier in plan.qualifiers:
        paths |= _window_paths(qualifier)
    for key in plan.order:
        paths.add(tuple(key.column.path))
    # `cursor` needs no branch: it carries values and directions, not columns,
    # and the columns it positions against are the order keys already covered.
    return frozenset(paths)


#: Columns SDMX REST is *measured* to return under `detail=allstubs`, keyed by
#: the table they sit on. Deliberately an allowlist of the proven, not a
#: denylist of the known-absent: getting this wrong means fetching short, and a
#: short fetch answers nothing without erroring. A column nobody has checked
#: therefore costs a `full` fetch rather than a silent empty result.
#:
#: Measured against `codelist/ECB?detail=allstubs` (244 codelists) compared
#: field-by-field with a full response for the 109 artefacts in both:
#:
#: * `codes` absent — the point of a stub
#: * `description` absent — present at full on the one artefact that has one
#: * `name`, `id`, `agency`, `version` all present
#:
#: `maintainer` also differed, and is *not* listed as a stub omission: the full
#: response was `references=all`, which pulled the AgencyScheme and resolved
#: the agency's name. That difference belongs to `references=`, not `detail=`,
#: and attributing it here would have made every query needing an agency name
#: ask for `full` on the strength of a confounded comparison.
_STUB_COLUMNS: dict[str, frozenset[str]] = {
    "artefact": frozenset({"resource_id", "agency_id", "version", "urn", "artefact_type"}),
}

#: Localised fields a stub carries. Matched on the field rather than the
#: column, because a `SideLabel`'s column is language-suffixed — `name_en`,
#: `description_fr` — and the language is not what decides this.
_STUB_LABEL_FIELDS = frozenset({"name"})


def columns_in(predicate: AnyPred) -> tuple[AnyCol, ...]:
    """Every column a predicate reads, recursing into composites.

    Takes `AnyPred` rather than `Pred[R]` for the reason that base exists: it
    matches on node *kinds* and never touches the row kind, and `Query.filter`
    holds its predicates non-generically once they are checked.

    Separate from `reached_paths`, which works off `PredicateShape` — a shape
    carries the column *name* but not the table it sits on, and `detail=`
    needs both. Adding a table to the shape was the alternative and would have
    changed what a `Capability` declaration matches, which is a much larger
    blast radius than one walker.
    """
    match predicate:
        case Compare(column=column) | Match(column=column) | ArrayHas(column=column) | InRelation(column=column):
            return (column,)
        case IsNull(column=column) | InSet(column=column):
            return (column,)
        case AggCompare(agg=agg):
            return tuple(agg.columns)
        case BoolOp(parts=parts):
            return tuple(col for part in parts for col in columns_in(part))
        case Not(part=part):
            return columns_in(part)
        case _:
            # `Quantified` reads its parts against the *child*, and `RowIn`
            # carries compiled SQL. Neither contributes a column of the root,
            # and both already force `full` through their non-empty path.
            return ()


def reached_columns[R: RowKind](plan: Plan[R]) -> frozenset[tuple[str, str]]:
    """Every `(table, column)` this plan reads.

    `closure` answers "which relations", which is what `references=` needs.
    `detail=` is finer: a query naming only `id` is servable by a stub, one
    naming `description` is not, and both have the same empty path.
    """
    found: set[tuple[str, str]] = set()
    for predicate in plan.predicates:
        for column in columns_in(predicate):
            found.add((column.table, column.column))
    for key in plan.order:
        found.add((key.column.table, key.column.column))
    return frozenset(found)


def detail_for(columns: Iterable[tuple[str, str]], paths: Iterable[tuple[str, ...]]) -> str:
    """`allstubs` when a stub answers the query, else `full`.

    A stub is the artefact without its items, and without its description
    (§—measured, see `_STUB_COLUMNS`). So a listing that names ids and labels
    is served by one, and anything reaching an item or a crossed relation is
    not.

    The default is `full` for anything unrecognised. A wrong `allstubs` does
    not fail — the query evaluates against an artefact whose items were never
    fetched and answers nothing — which is the same silence `references=`
    exists to prevent, arriving through the other parameter.

    Args:
        columns: `(table, column)` pairs the query reads, from
            `reached_columns`.
        paths: Paths the query reaches, from `closure`. Any non-empty path
            forces `full`: a stub carries no items and no references.

    Returns:
        A value for the `detail` query parameter.
    """
    if any(path for path in paths):
        return "full"
    for table, column in columns:
        if table == "localized_text":
            if column.rsplit("_", 1)[0] not in _STUB_LABEL_FIELDS:
                return "full"
        elif column not in _STUB_COLUMNS.get(table, frozenset()):
            return "full"
    return "allstubs"


def references_for(root_table: str, paths: Iterable[tuple[str, ...]]) -> str:
    """The SDMX REST `references=` value that closes over `paths`.

    The cheapest value that covers the deepest crossing, and never `all` —
    §11 measured 29.8 MB for one codelist under `references=all`, and a
    planner that reaches for it has stopped choosing.

    | crossings | value | why |
    | --- | --- | --- |
    | 0 | `none` | everything needed is on the artefact or its extension |
    | 1 | `children` | one reference out, and its contents come with it |
    | 2+ | `descendants` | the reference's own references are read |

    A one-crossing fetch could name the target *type* instead of `children` —
    `references=datastructure` — which is narrower still. That is left for the
    executor, which knows the concrete relation each path lands on; here the
    depth is what the paths determine.

    Args:
        root_table: Relation the scan is rooted at.
        paths: Paths the query reaches, from `closure`.

    Returns:
        A value for the `references` query parameter.
    """
    deepest = max((crossings(root_table, path) for path in paths), default=0)
    if deepest == 0:
        return "none"
    return "children" if deepest == 1 else "descendants"
