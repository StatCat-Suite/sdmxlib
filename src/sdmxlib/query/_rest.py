"""A `Push` as an SDMX REST request.

§13's first step, and the least interesting one by design: the predicates a
`Push` carries are equality on agency, id and version *by construction* — the
capability declares nothing else — so this is assembly rather than
compilation. If it ever stops being assembly, the declaration has drifted from
what the URL can express and that is the thing to fix.

The `references=` value is the part that carries risk, and it comes from
`_closure`: enough to answer the query locally, and no more. Fetch short and
the query returns nothing with no error; fetch `all` and one codelist is
29.8 MB.
"""

from collections.abc import Iterable, Mapping, Sequence

import attrs

from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.datastructure import ComponentRole, DataStructure
from sdmxlib.model.rest_api import ApiVersion
from sdmxlib.query._closure import closure, columns_in, detail_for, reached_columns, reached_paths, references_for
from sdmxlib.query._expr import AnyPred, Compare, InSet, RowKind
from sdmxlib.query._plan import Plan
from sdmxlib.query._pushdown import Program, Push, root_key

#: What SDMX REST calls a path segment that was not constrained. `all` and
#: `latest` are the spec's wildcards, and they differ: `all` means every
#: agency or every id, `latest` means the newest version rather than every
#: version. Using `all` for the version would change the result set.
ANY = "all"
LATEST = "latest"


#: Resources SDMX-REST serves from `/metadata` rather than `/structure`.
#:
#: One entry, deliberately named rather than inlined: the neighbouring
#: *definitions* — `metadatastructure`, `metadataflow`,
#: `metadataprovisionagreement` — are structural and already routed correctly,
#: so "metadata" in a resource name says nothing about its plane. What sits
#: here is the populated payload, checked against pysdmx's
#: `RefMetaByMetadatasetQuery`, which renders
#: ``/metadata/metadataset/{provider}/{id}/{version}``.
_METADATA_PLANE = frozenset({"metadataset"})


def _plane_of(resource: str) -> str:
    """Which SDMX-REST plane serves `resource`."""
    return "metadata" if resource in _METADATA_PLANE else "structure"


@attrs.frozen
class Request:
    """One structure request, as path segments and query parameters.

    Not a URL string: the segments are what a test can assert on and what a
    client joins. Keeping them apart also keeps the percent-encoding question
    in one place — the client's — rather than in whatever built the string.

    Attributes:
        resource: The structure type — `dataflow`, `datastructure`.
        agency: Maintainer, or `all`.
        resource_id: Artefact id, or `all`.
        version: Version, or `latest`.
        references: What to return alongside, from `_closure`.
        detail: `allstubs` when a **projection** is served by one, else
            `full`. Derived from the columns read, the way `references` is
            derived from the relations reached (#318). A stub is the artefact
            without its items and without its description, both measured
            against recorded responses rather than read off the spec.

            A row terminal always gets `full`: `collect()` resolves rows to
            artefacts out of the store, and a stub-populated store returns a
            codelist with zero codes and no error.
    """

    resource: str
    agency: str = ANY
    resource_id: str = ANY
    version: str = LATEST
    references: str = "none"
    detail: str = "full"

    @property
    def path(self) -> str:
        """The path portion, without the service root.

        SDMX-REST routes three ways — `/structure`, `/data`, `/metadata` — and
        the plane follows from the resource rather than being a field: a
        `metadataset` is served from `/metadata`, and everything else this
        builds is structural. `/data` is not reachable from here at all; that
        is `DataRequest`, whose path has a different shape.

        This said `structure/` for every resource, which was right for eleven
        of the twelve and wrong for `metadataset`. Since #338 a 404 comes back
        as "not here", so the wrong path did not raise — it returned an empty
        result, which is the shape of failure that survives longest.
        """
        return f"{_plane_of(self.resource)}/{self.resource}/{self.agency}/{self.resource_id}/{self.version}"

    @property
    def params(self) -> dict[str, str]:
        """Query parameters, omitting `references=none` as the wire default."""
        params = {"detail": self.detail}
        if self.references != "none":
            params["references"] = self.references
        return params

    def is_bounded(self) -> bool:
        """Whether this names a set a caller could reasonably have asked for.

        The §5 rule as it appears on the wire, and a second check rather than
        a substitute: the planner has already refused an unbounded scan, so a
        `False` here means the two disagree, which is worth a test rather than
        a silent fetch.
        """
        return self.agency != ANY


def _equalities[R: RowKind](plan: Plan[R]) -> dict[str, str]:
    """Root-level `column = literal` pairs, which is all a URL can carry."""
    found: dict[str, str] = {}
    for predicate in plan.predicates:
        if isinstance(predicate, Compare) and predicate.op == "=" and not predicate.column.path:
            found[predicate.column.column] = str(predicate.value)
    return found


def request_for[R: RowKind](
    push: Push[R],
    residual_paths: Iterable[tuple[str, ...]] = (),
    residual_columns: Iterable[tuple[str, str]] = (),
    projection: Iterable[tuple[str, str]] | None = None,
) -> Request:
    """The request one `Push` becomes.

    `residual_paths` is not optional in spirit, only in signature. A scan's
    `references=` has to cover **everything evaluated against its rows**, and
    what a push pushes is only part of that: the predicates the backend could
    not take come back as `Residual` steps over the same rows, and they reach
    the deepest paths precisely because they are the ones it could not answer.

    Computing this from `push.plan` alone was the first cut and was wrong in
    the case that matters. The §9.1 sub-scan pushes `dsd.agency` and keeps the
    concept predicate local, so its plan mentions no crossing at all, and the
    request came out `references=none` — the fetch that resolves zero concept
    ids and answers nothing. `requests_for` pairs each push with its residuals
    for this reason; calling this directly with an empty second argument is
    only right when there are none.
    """
    plan = push.plan
    equalities = _equalities(plan)
    table = plan.root.__table__
    # Both parameters come from the same closure and fail the same way: ask
    # for too little and the query evaluates against something never fetched,
    # answering nothing without erroring. `references` covers the relations
    # reached, `detail` the columns read.
    paths = closure(plan) | frozenset(residual_paths)
    columns = reached_columns(plan) | frozenset(residual_columns)
    # `None` means the caller wants *artefacts*, not columns — `collect()`,
    # `one()`, `first()` all resolve rows to domain objects out of the store.
    # A stub has no items, so those come back hollow and silent: a codelist
    # with zero codes and no error. Only a projection knows it needs nothing
    # more than the columns it names, so only a projection can ask for less.
    detail = "full" if projection is None else detail_for(columns | frozenset(projection), paths)
    return Request(
        # `root_key` returns the artefact type, which is the resource with a
        # different capitalisation — `DataStructure` / `datastructure`.
        resource=root_key(plan.root).lower(),
        agency=equalities.get("agency_id", ANY),
        resource_id=equalities.get("resource_id", ANY),
        version=equalities.get("version", LATEST),
        detail=detail,
        references=references_for(table, paths),
    )


def requests_for_derived[R: RowKind](push: Push[R]) -> tuple[Request, ...]:
    """The requests a *derived* root becomes — one per source relation.

    A view has no endpoint. `hierarchy_applicability` is one row of a dataflow
    joined to one of its components and one association, so answering a scan
    of it means fetching all three and letting the view do the join locally,
    which is what §13 already does for every other query: fetch, materialise,
    then ask the store.

    `detail="full"` and `references="none"` for each: the view reads plain
    columns off all three relations, and a stub carries identification only —
    a `dsd_urn` that is null joins to nothing, and an empty answer is not an
    error. No prefetch, because the relations are being fetched outright
    rather than reached through one another.

    Args:
        push: The scan. Its root must declare `__derived_from__`.

    Returns:
        One `Request` per source relation, in the order the root names them.
    """
    sources = push.plan.root.__derived_from__
    if sources is None:  # pragma: no cover — guarded by the caller
        msg = f"{type(push.plan.root).__name__} is not a derived root"
        raise TypeError(msg)
    equalities = _equalities(push.plan)
    agency = equalities.get("agency_id", ANY)
    return tuple(
        Request(
            resource=resource,
            agency=agency,
            resource_id=ANY,
            version=LATEST,
            detail="full",
            references="none",
        )
        for resource in sources
    )


def requests_for[R: RowKind](
    program: Program[R],
    projection: Iterable[tuple[str, str]] | None = None,
    projected_paths: Iterable[tuple[str, ...]] = (),
) -> tuple[Request, ...]:
    """Every request a program issues, in order.

    Each `Residual` is attributed to the most recent `Push`, which is the
    execution order the type already states — "work left to us, over the rows
    the previous step returned". So a push's `references=` covers its own
    predicates *and* the local work that will run against what it returns.

    `projected_paths` are the paths a `Select` reads (`projection_paths`), and
    they join the last push: `plan_program` emits any sub-scans first and the
    root scan last, and the projection is over the root scan's rows (GH-271).

    The length of the result is the round-trip cost, and it is a property of
    the plan rather than of the data (§5). A program whose length grew with
    the number of rows returned would satisfy every other rule here and still
    be the N+1 problem.
    """
    requests: list[Request] = []
    pending: Push[R] | None = None
    paths: set[tuple[str, ...]] = set()
    columns: set[tuple[str, str]] = set()
    for step in program.steps:
        if isinstance(step, Push):
            if pending is not None:
                requests.extend(_requests_for_push(pending, paths, columns, projection))
            pending, paths, columns = step, set(), set()
            continue
        for predicate in step.predicates:
            paths |= reached_paths(predicate)
            columns |= {(column.table, column.column) for column in columns_in(predicate)}
        if step.member_of is not None:
            # The §9.1 membership reads a *reference* off this scan's rows —
            # `dataflow.dsd_urn` — and a stub carries identification only, so
            # the reference is not in one. Missed, the outer scan came back as
            # stubs, every `dsd_urn` was null, and the membership matched
            # nothing: the rewrite's own answer, silently empty.
            #
            # It is not in `step.predicates`, which is exactly why: a
            # `Membership` is something the *planner* produced, so a check
            # that walks only what the caller wrote cannot see it.
            reference = step.member_of.column
            paths.add(tuple(reference.path))
            columns.add((reference.table, reference.column))
    if pending is not None:
        paths |= set(projected_paths)
        requests.extend(_requests_for_push(pending, paths, columns, projection))
    return tuple(requests)


def _requests_for_push[R: RowKind](
    push: Push[R],
    paths: Iterable[tuple[str, ...]],
    columns: Iterable[tuple[str, str]],
    projection: Iterable[tuple[str, str]] | None,
) -> tuple[Request, ...]:
    """One push's requests: several when its root is derived, else one."""
    if push.plan.root.__derived_from__ is not None:
        return requests_for_derived(push)
    return (request_for(push, paths, columns, projection),)


@attrs.frozen
class DataRequest:
    """One `/data` request, as parts rather than a URL.

    Sibling to `Request` and kept apart from it deliberately: the two endpoints
    take different shapes, and merging them would mean a type where half the
    fields are always empty. Parts rather than a string for the same reason
    `Request` gives — percent-encoding belongs in the client, and a test can
    assert on a field.

    Attributes:
        agency: Maintainer of the dataflow.
        resource_id: Dataflow id.
        version: Dataflow version.
        api: Which generation this was built for. Carried rather than inferred
            because the `key` below was built to *this* version's slot count,
            so a request handed to the other endpoint is wrong in a way nothing
            downstream could detect (#337).
        key: The dotted positional key, `+`-joined per slot, empty for a
            wildcard slot.
        start_period: `startPeriod`, an **inclusive** lower bound on time.
        end_period: `endPeriod`, inclusive.
        components: `c[<id>]=<v1>+<v2>` constraints, for components outside the
            key. 3.0 only — 2.1 has no such parameter, and the lowering leaves
            those predicates residual there.
    """

    agency: str
    resource_id: str
    version: str
    api: ApiVersion
    key: str = ANY
    start_period: str | None = None
    end_period: str | None = None
    components: Mapping[str, tuple[str, ...]] = attrs.field(factory=dict)

    @property
    def path(self) -> str:
        """The path portion, without the service root."""
        if self.api == "3.0":
            version = "~" if self.version == LATEST else self.version
            return f"data/dataflow/{self.agency}/{self.resource_id}/{version}/{self.key}"
        return f"data/{self.agency},{self.resource_id},{self.version}/{self.key}"

    @property
    def narrows(self) -> bool:
        """Whether anything was pushed — a key slot, a period, or a component.

        `False` is `/data/{flow}/all` with no parameters: the whole dataflow.
        """
        return self.key != ANY or self.start_period is not None or self.end_period is not None or bool(self.components)

    @property
    def params(self) -> dict[str, str]:
        """Query parameters, omitting anything unconstrained."""
        params: dict[str, str] = {}
        if self.start_period is not None:
            params["startPeriod"] = self.start_period
        if self.end_period is not None:
            params["endPeriod"] = self.end_period
        for component_id, values in self.components.items():
            params[f"c[{component_id}]"] = "+".join(values)
        return params


def data_request_for(
    predicates: Sequence[AnyPred],
    *,
    binding: DataflowBinding,
    dsd: DataStructure,
    api: ApiVersion,
) -> tuple[DataRequest, list[AnyPred]]:
    """Lower what SDMX REST can express into a request; return the rest.

    The data half of `request_for`. Each predicate either becomes part of the
    URL or stays behind to be evaluated locally after the fetch — there is no
    third outcome, and nothing is dropped.

    **Pushing a superset is always safe, and that is what makes the conflict
    rule simple.** When two predicates constrain one slot, the first is pushed
    and the second stays residual: the server then returns a superset and the
    local filter narrows it. The reverse — pushing the *intersection* — would
    be an optimisation whose correctness depends on both predicates meaning
    what we think, and there is no case yet that needs it.

    What lowers, by role (see `ComponentRole`):

    * key dimension, `==` or `is_in` → a key slot, `+`-joined
    * time, `>=` / `<=` → `startPeriod` / `endPeriod`; `==` → both, since the
      bounds are inclusive
    * time, `is_in` → a key slot on 3.0, where time has one; residual on 2.1
    * attribute, `==` or `is_in` → `c[<id>]=` on 3.0; residual on 2.1
    * everything else, including every measure predicate → residual

    Args:
        predicates: The query's predicates, in any order.
        binding: Maps physical columns back to component ids.
        dsd: Supplies the slot order and each component's role.
        api: The generation being addressed. Never inferred — it decides both
            the key's length and whether `c[…]` exists.

    Returns:
        The request, and the predicates it could not express.
    """
    slots = dsd.key_slot_ids(api)
    keyed: dict[str, tuple[str, ...]] = {}
    components: dict[str, tuple[str, ...]] = {}
    start: str | None = None
    end: str | None = None
    residual: list[AnyPred] = []

    for predicate in predicates:
        values = _lowerable_values(predicate)
        component = _component_for(predicate, binding)
        role = dsd.role_of(component) if component is not None else None
        if values is None or component is None or role is None:
            residual.append(predicate)
            continue

        op, literals = values
        if role is ComponentRole.TIME_DIMENSION and op in {"=", ">=", "<="} and len(literals) == 1:
            # `==` sets both bounds: `startPeriod` and `endPeriod` are
            # inclusive, so a single period is the range [v, v].
            if op in {"=", ">="} and start is None:
                start = literals[0]
            if op in {"=", "<="} and end is None:
                end = literals[0]
            continue

        target = keyed if component in slots else components
        if target is components and (api != "3.0" or role is not ComponentRole.ATTRIBUTE):
            residual.append(predicate)
            continue
        if op not in {"=", ""}:
            residual.append(predicate)
            continue
        if component in target:
            residual.append(predicate)
            continue
        target[component] = literals

    return (
        DataRequest(
            agency=binding.dataflow.urn.agency,
            resource_id=binding.dataflow.urn.id,
            version=binding.dataflow.urn.version,
            api=api,
            key=_dotted_key(slots, keyed),
            start_period=start,
            end_period=end,
            components=components,
        ),
        residual,
    )


def _lowerable_values(predicate: AnyPred) -> tuple[str, tuple[str, ...]] | None:
    """`(op, literals)` for a predicate the URL could carry, else `None`.

    `InSet` reports `""` as its operator because a key segment's `+` is not an
    operator the caller wrote — it is what membership *is* on the wire.
    """
    match predicate:
        case Compare(op=op, value=value) if op in {"=", ">=", "<="}:
            return op, (str(value),)
        case InSet(values=values):
            return "", tuple(str(v) for v in values)
        case _:
            return None


def _component_for(predicate: AnyPred, binding: DataflowBinding) -> str | None:
    """The component id a predicate is about, or `None` if it is not about one."""
    match predicate:
        case Compare(column=column) | InSet(column=column):
            return binding.component_of(column.column)
        case _:
            return None


def _dotted_key(slots: Sequence[str], keyed: Mapping[str, tuple[str, ...]]) -> str:
    """Positional key, `+`-joined per slot, empty where unconstrained.

    `all` rather than a row of dots when nothing is constrained — both are
    accepted, and `all` says so.
    """
    if not keyed:
        return ANY
    return ".".join("+".join(sorted(keyed.get(slot, ()))) for slot in slots)
