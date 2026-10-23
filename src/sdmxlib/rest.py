"""SDMX REST utilities — URL/key parsing shared by REST facades.

The SDMX REST API identifies a slice of a dataflow with two filter
mechanisms applied uniformly to *components*:

- A *dotted key* over the DSD's key dimensions
  (``CA+US.M.2021``, ``..2021``, ``all``). Each dimension's allowed
  values are separated by ``.``, ``+`` joins alternatives within a
  single dimension, an empty segment is "any value", and the shorthand
  ``all`` (or the empty string) means "every observation".
- The 2.0 ``c[<id>]=<vals>`` query parameters which constrain *any*
  component — most importantly attributes (e.g. ``c[GEO_LEVEL]=FED``).
  Without this, attribute-side filtering is impossible at the REST
  layer for any DSD where the relevant filter axis isn't a key
  dimension (the census profile's ``GEO_LEVEL`` is the canonical example).

This module turns both inputs into `ComponentFilter`, a
structured filter that data stores translate to their query language.
Keeping the parser here means every facade gets the same wildcard /
mismatch / OR semantics.

Filter values may be eager ``list[str]`` of allowed codes *or* a
`SqlRelation` (typically a one-column projection —
``LazyCodelist.narrower_transitive(...).select(sl.q.code.id)``).
Relations let a 50k-code
hierarchy traversal compose into one ``IN (SELECT ...)`` clause rather
than 50k bind parameters — see #62.

Example:
    >>> cf = parse_components(
    ...     "CA+US.M.2021",
    ...     {"GEO_LEVEL": ["FED"]},
    ...     ["GEO", "FREQ", "TIME_PERIOD"],
    ... )
    >>> cf.dimensions
    [['CA', 'US'], ['M'], ['2021']]
    >>> cf.attributes
    {'GEO_LEVEL': ['FED']}
"""

from attrs import field, frozen

from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.rest_api import ApiVersion
from sdmxlib.sql import SqlRelation


@frozen
class ComponentFilter:
    """Structured form of an SDMX REST component filter.

    Self-describing: carries the dimension ids it was built against
    (parallel to ``dimensions``) so downstream translators don't have
    to second-guess key order. Attribute filters are a flat ``id →
    values`` dict — order is irrelevant for attributes.

    Each filter value may be either an eager ``list[str]`` (OR set of
    allowed codes) or a `SqlRelation`
    (lazy single-column subquery). The SQL builder dispatches on the
    type — lists become ``IN (?, ?, ...)``; relations become
    ``IN (<subquery>)`` with the relation's params spliced into the
    outer bind list.

    **``None`` and ``[]`` are different, deliberately**. ``None`` is
    "this component is unconstrained"; ``[]`` is "the set of permitted values
    is empty", which no observation can satisfy, so the query returns nothing.
    Reading ``[]`` as "no constraint" would make a dynamically-computed code
    list that came back empty silently return the entire dataset, and it would
    give the library two spellings for one meaning while the literal reading of
    one of them is the opposite. polars (``is_in([])`` → empty) and SQLAlchemy
    (``in_([])`` → a ``1 != 1`` contradiction) both resolve it this way, as does
    the relational definition: ``x IN S`` is ``∃y∈S: x = y``, false for empty
    ``S``.

    Attributes:
        dimension_ids: Dimension ids in DSD-key order.
        dimensions: Parallel list — ``dimensions[i]`` constrains
            ``dimension_ids[i]``. ``None`` means no constraint; ``[]`` means no
            permitted value; a non-empty list is an OR set of allowed values;
            a `SqlRelation` is a lazy single-column subquery.
        attributes: Per-attribute allowed values. Each value is either
            a list (OR semantics, ``[]`` matching nothing) or a `SqlRelation`.
            AND semantics across attributes. Empty dict = no attribute
            constraints.
    """

    dimension_ids: list[str]
    dimensions: list[list[str] | SqlRelation | None]
    attributes: dict[str, list[str] | SqlRelation] = field(factory=dict)

    def __attrs_post_init__(self) -> None:
        if len(self.dimension_ids) != len(self.dimensions):
            msg = (
                f"ComponentFilter has {len(self.dimension_ids)} dimension_ids but "
                f"{len(self.dimensions)} dimensions — must be parallel arrays"
            )
            raise ValueError(msg)

    @property
    def is_wildcard(self) -> bool:
        """True if no dimension or attribute is constrained.

        An empty *list* is a constraint — the one that admits nothing — so a
        filter carrying one is not a wildcard even though it selects no rows.
        """
        return all(s is None for s in self.dimensions) and not self.attributes

    def to_where_clauses(
        self,
        dimension_column_map: dict[str, str],
        attribute_column_map: dict[str, str] | None = None,
    ) -> tuple[list[str], list[object]]:
        """Render this filter as ``(where_fragments, params)``.

        Each constrained dimension or attribute becomes one
        ``"col IN (...)"`` fragment with values appended to ``params``
        in order. Wildcard dimensions are skipped. Eager-list values
        emit ``IN (?, ?, ...)``; `SqlRelation` values emit
        ``IN (<subquery>)`` with the relation's bind params spliced
        in at the right position.

        Args:
            dimension_column_map: SDMX dimension id → physical column
                name. Usually ``binding.dimension_column_map``.
            attribute_column_map: SDMX attribute id → physical column
                name. Usually ``binding.attribute_column_map``. May be
                omitted (or empty) if the filter has no attribute
                constraints.

        Returns:
            A pair of ``(fragments, params)`` ready to pass through to
            `query` as ``where=`` and ``params=``.

        Raises:
            KeyError: If a constrained dimension is missing from
                ``dimension_column_map``, or a constrained attribute
                is missing from ``attribute_column_map``.
        """
        fragments: list[str] = []
        params: list[object] = []
        for dim_id, val in zip(self.dimension_ids, self.dimensions, strict=True):
            if val is None:
                continue
            if dim_id not in dimension_column_map:
                msg = (
                    f"ComponentFilter constrains dimension {dim_id!r} but it is "
                    f"not in dimension_column_map (have: {sorted(dimension_column_map)})"
                )
                raise KeyError(msg)
            col = dimension_column_map[dim_id]
            frag, frag_params = _emit_in_clause(col, val)
            fragments.append(frag)
            params.extend(frag_params)

        attr_map = attribute_column_map or {}
        for attr_id, val in self.attributes.items():
            if attr_id not in attr_map:
                msg = (
                    f"ComponentFilter constrains attribute {attr_id!r} but it is "
                    f"not in attribute_column_map (have: {sorted(attr_map)})"
                )
                raise KeyError(msg)
            col = attr_map[attr_id]
            frag, frag_params = _emit_in_clause(col, val)
            fragments.append(frag)
            params.extend(frag_params)

        return fragments, params


def _emit_in_clause(col: str, value: list[str] | SqlRelation) -> tuple[str, list[object]]:
    """Build one ``col IN (...)`` fragment for either filter-value form.

    An empty list compiles to ``FALSE`` rather than ``IN ()``, which is not
    valid SQL in DuckDB or anywhere else — the empty OR-set admits no value, so
    the predicate is a contradiction (#256). SQLAlchemy emits the same thing
    spelled ``IN (NULL) AND (1 != 1)``; the extra terms buy cross-dialect
    portability we do not need.
    """
    if isinstance(value, list):
        if not value:
            return "FALSE", []
        placeholders = ", ".join(["?"] * len(value))
        return f"{col} IN ({placeholders})", list(value)
    # SqlRelation — splice the subquery inline. The relation's params
    # bind to the ?-placeholders inside the subquery; they sit before
    # any outer-clause params that come after this fragment.
    sub_sql, sub_params = value.to_sql()
    return f"{col} IN ({sub_sql})", list(sub_params)


def widen_key(key: str, dsd: DataStructure, api: ApiVersion) -> str:
    """Accept a key written for the *other* REST generation, where that is safe.

    Real clients disagree about the time dimension. Fusion Registry, pandasdmx
    and SDMX-RI all send 1.5-shaped keys — time omitted — and a 2.2.2 endpoint
    expects one segment more. A facade that refuses them is refusing
    conformant requests from most of the ecosystem, so this widens a short key
    by appending the empty (wildcard) time segment.

    **Only that one direction, and only when it is unambiguous.** A 2.2.2 key
    given to a 2.1 endpoint is *not* narrowed: dropping a trailing segment
    would silently discard a filter the caller wrote. And a key of any other
    length is returned untouched, so `parse_components` still refuses it with
    a message naming both counts rather than this guessing.

    Deliberately **not** applied inside `parse_components`. Leniency is a
    policy a facade chooses; a parser that quietly reinterprets its input has
    no way to tell a caller they sent the wrong thing. Call this first when you
    want it:

        key = sl.widen_key(raw_key, dsd, "3.0")
        cf = sl.parse_components(key, params, dsd.key_slot_ids("3.0"))

    Args:
        key: The dotted key as received.
        dsd: The structure the key is positional over.
        api: The generation *this endpoint serves*.

    Returns:
        The key, widened when it was one short of this version's shape and the
        missing slot is the time dimension. Otherwise unchanged.
    """
    if api != "3.0" or key in {"", "all"}:
        return key
    slots = dsd.key_slot_ids(api)
    if not slots or slots[-1] != (dsd.time_dimension.id if dsd.time_dimension else None):
        return key
    if len(key.split(".")) != len(slots) - 1:
        return key
    return key + "."


def parse_components(
    key: str,
    component_filters: dict[str, list[str] | SqlRelation],
    key_dimension_ids: list[str],
) -> ComponentFilter:
    """Parse SDMX REST component filters into a `ComponentFilter`.

    Combines the two filter mechanisms of SDMX REST 2.0:

    1. The dotted-key path segment that constrains key dimensions.
    2. The ``c[<id>]=<vals>`` query params that constrain any other
       component — typically attributes that don't appear in the key.

    The HTTP layer is expected to have already split the URL slice into
    these two parts before calling this function. ``component_filters``
    carries the parsed ``c[...]=...`` dict; the web framework handles
    URL-decoding.

    Filter values may be eager ``list[str]`` (the URL form, after
    splitting on ``+``) or a `SqlRelation` (lazy one-column subquery —
    typically ``LazyCodelist.narrower_transitive(...).select(sl.q.code.id)``
    for hierarchy slices).

    Args:
        key: The dotted-key fragment from the URL. ``"all"`` and ``""``
            both mean "every observation". Empty segments are wildcards.
            ``+`` joins alternatives within a single dimension.
        component_filters: ``{component_id: list[str] | SqlRelation}``
            parsed from ``c[...]=...`` query params (or constructed
            programmatically). Component ids that match a key-dimension
            id are placed onto ``dimensions``; the rest land on
            ``attributes``. An empty list is kept, and means "no permitted
            value" — see `ComponentFilter`. It used to be dropped,
            which silently widened the caller's query to everything and
            disagreed with what direct construction did with the same input.
        key_dimension_ids: Dimension ids in DSD key order. Used both to
            size the result and to validate the segment count.

    Returns:
        A `ComponentFilter` ready to pass as ``component_filter=``
        to `query` or
        `query`.

    Raises:
        ValueError: If the key has a different number of segments than
            ``key_dimension_ids`` (and is not the ``all`` wildcard), or
            if a relation value is given for a key dimension that the
            dotted key already constrains (relations don't merge with
            eager lists).
    """
    n = len(key_dimension_ids)
    if key in {"", "all"}:
        dimensions: list[list[str] | SqlRelation | None] = [None] * n
    else:
        raw_segments = key.split(".")
        if len(raw_segments) != n:
            msg = (
                f"SDMX key has {len(raw_segments)} segments but DSD has {n} "
                f"key dimensions ({', '.join(key_dimension_ids)}): {key!r}"
            )
            raise ValueError(msg)
        dimensions = [None if raw == "" else raw.split("+") for raw in raw_segments]

    # c[<id>]=<vals> may target either a key dimension (overlay onto
    # ``dimensions``) or any other component (lands on ``attributes``).
    # An empty list is a constraint that admits nothing, not the absence of
    # one, and is carried through unchanged (#256).
    #
    # Note the merge below is a set *union*, so an empty list merged onto a
    # dotted-key segment widens nothing and narrows nothing: the key already
    # said which values are permitted, and adding no further alternatives
    # leaves that set alone.
    attributes: dict[str, list[str] | SqlRelation] = {}
    dim_index = {d: i for i, d in enumerate(key_dimension_ids)}
    for comp_id, value in component_filters.items():
        if comp_id in dim_index:
            i = dim_index[comp_id]
            existing = dimensions[i]
            if isinstance(value, list) and isinstance(existing, list):
                # Eager + eager: merge OR sets.
                dimensions[i] = [*existing, *value]
            elif existing is None:
                dimensions[i] = list(value) if isinstance(value, list) else value
            else:
                # Either side is a relation — can't merge.
                msg = (
                    f"ComponentFilter for key dimension {comp_id!r} mixes a "
                    "dotted-key segment with a SqlRelation; relations don't "
                    "merge with eager lists. Pass the constraint via only one path."
                )
                raise ValueError(msg)
        else:
            attributes[comp_id] = list(value) if isinstance(value, list) else value

    return ComponentFilter(
        dimension_ids=list(key_dimension_ids),
        dimensions=dimensions,
        attributes=attributes,
    )
