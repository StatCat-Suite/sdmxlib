"""Hierarchy, HierarchicalCode, and Level — cross-codelist hierarchical views.

SDMX 3.0 artefact that replaces HierarchicalCodelist from 2.1.
SDMX 3.1 adds Level support for explicit level structures.
The 2.1 reader maps the older structure into this model.
"""

from datetime import datetime
from typing import ClassVar, override

from attrs import field, frozen

from sdmxlib._time import as_utc
from sdmxlib.model._invariants import enforce
from sdmxlib.model._refshape import derived_view
from sdmxlib.model._rules import id_type, maintained_by, named, version_type
from sdmxlib.model._tree import PathIndex, PathNavigation
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.codelist import Code
from sdmxlib.model.collections import ItemList
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import Dimension
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link, to_links
from sdmxlib.model.organisation import Agency, _to_agency, _to_annotations, _to_istring
from sdmxlib.model.ref import Ref, artefact_urn, refuse_urn_text, to_optional_ref, to_refs
from sdmxlib.model.registry import SupportsGetAll
from sdmxlib.model.representation import Facet
from sdmxlib.model.urn import ParsesUrn, SdmxUrn


class AmbiguousHierarchyError(ValueError):
    """Raised when `unique_hierarchy` finds more than one applicable Hierarchy.

    Carries the list of conflicting `HierarchyAssociation` URNs. The
    caller asserted "at most one Hierarchy applies" (via the
    `unique_hierarchy` accessor); the IM permits many, so this signals
    that the caller's assumption doesn't hold for this registry.

    Use `applicable_hierarchies` instead when the IM-canonical many-case
    is the intended question.
    """

    def __init__(self, dim_urn: str, dataflow_urn: str, ha_urns: list[str]) -> None:
        self.dim_urn = dim_urn
        self.dataflow_urn = dataflow_urn
        self.ha_urns = ha_urns
        joined = ", ".join(ha_urns)
        super().__init__(
            f"Multiple HierarchyAssociations bind {dim_urn} for dataflow "
            f"{dataflow_urn}: {joined}. Use applicable_hierarchies() to "
            "enumerate them.",
        )


@frozen(unsafe_hash=False)
class Level(ParsesUrn):
    """A named level in a Hierarchy (SDMX 3.1).

    Hierarchies that declare levels allow each ``HierarchicalCode`` node
    to explicitly state which structural level it belongs to — rather
    than inferring level from tree depth or code ID patterns.

    The optional ``coding_format`` records the character/length
    constraints that codes at this level must follow (for example,
    "ISO-3166-2 subdivision codes are 1 to 3 alphanumeric chars"). It
    reuses the existing ``Facet`` value type so callers have one
    constraint vocabulary across the model.

    Example:
        hierarchy.levels["SECTOR"]        # Level lookup by id
        node.level                         # Level | None
    """

    #: See `Code.sdmx_package` — the item's own URN pair, not its container's.
    #: In the ``codelist`` package, as for `HierarchicalCode`.
    sdmx_package: ClassVar[str] = "codelist"
    sdmx_class: ClassVar[str] = "Level"

    id: str
    name: InternationalString = field(factory=InternationalString, converter=_to_istring)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    coding_format: Facet | None = None
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)


def _to_levels(v: ItemList[Level] | list[Level]) -> ItemList[Level]:
    return v if isinstance(v, ItemList) else ItemList(v)


def _to_hcode_ref(v: Ref[Code] | SdmxUrn[Code] | None) -> Ref[Code] | None:
    if isinstance(v, SdmxUrn):
        return Ref[Code].unresolved(v)
    return v


# A lazily evaluated alias rather than the union written out: attrs evaluates a
# converter's annotations when the class is built (attrs#1470, open), and
# `HierarchicalCode` is the class still being built.
type _HCodesLike = ItemList[HierarchicalCode] | list[HierarchicalCode]
type _HCodes = ItemList[HierarchicalCode]


def _to_hcodes(v: _HCodesLike) -> _HCodes:
    return v if isinstance(v, ItemList) else ItemList(v)


def _to_tree(v: ItemList[HierarchicalCode] | list[HierarchicalCode]) -> ItemList[HierarchicalCode]:
    return v if isinstance(v, ItemList) else ItemList(v)


@frozen(unsafe_hash=False)
class HierarchicalCode(ParsesUrn):
    """A node in a Hierarchy tree, referencing a code from a Codelist.

    Provides tree navigation via ``children``; the parent `Hierarchy` looks a
    node up by path, or every node sharing a leaf id with ``by_id``.

    Example:
        node = hierarchy.tree["CANADA"]
        node.children["ON"]           # tree navigation
        hierarchy["CANADA.ON"]         # by path
        hierarchy.by_id("ON")          # every node whose id is ON
        node.code()                    # Ref[Code] unwrap → Code
    """

    #: See `Code.sdmx_package` — the item's own URN pair, not its container's.
    #: SDMX puts the whole hierarchy group in the ``codelist`` package, not a
    #: ``hierarchy`` one.
    sdmx_package: ClassVar[str] = "codelist"
    sdmx_class: ClassVar[str] = "HierarchicalCode"

    id: str = field(validator=id_type)
    code: Ref[Code] | None = field(default=None, converter=_to_hcode_ref, validator=refuse_urn_text)
    children: ItemList[HierarchicalCode] = field(factory=ItemList, converter=_to_hcodes)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    # Per-node temporal validity (independent of the underlying Code's
    # own validity). Supported by 2.1 ``HierarchicalCodeType`` and 3.1
    # alike.
    valid_from: datetime | None = field(default=None, converter=as_utc)
    valid_to: datetime | None = field(default=None, converter=as_utc)
    #: SDMX-ML 2.1's ``@version`` on a hierarchical code, which the schema
    #: calls informational only (#422). 3.x has no such attribute.
    version: str | None = None
    level: Level | None = None
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)


def _node_code_id(node: HierarchicalCode) -> str | None:
    """Underlying Code id for a node — its ``code`` URN's item id, or its own id if no ref."""
    if node.code is None:
        return node.id
    return node.code.urn.item_id


@frozen(unsafe_hash=False)
class Hierarchy(MaintainableArtefact, PathNavigation[HierarchicalCode]):
    """A standalone hierarchy of codes from one or more codelists.

    Nodes are identified by path, as SDMX identifies them: a node id may repeat
    in another branch, never among siblings (#439).

    - ``tree``: top-level entry points (roots) with ``children`` navigation
    - ``hierarchy["CANADA.ON.OTTAWA"]``: one node by path; a bare id is a root
    - ``hierarchy.by_id("OTTAWA")``: every node with that leaf id
    - ``codes``: every node, in pre-order

    Example:
        hierarchy.tree["CANADA"].children["ON"]   # tree navigation
        hierarchy["CANADA.ON.OTTAWA"].code()       # Ref[Code] unwrap → Code
    """

    sdmx_package: ClassVar[str] = "codelist"
    sdmx_class: ClassVar[str] = "Hierarchy"
    msg_field: ClassVar[str] = "hierarchies"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    valid_from: datetime | None = field(default=None, converter=as_utc)
    valid_to: datetime | None = field(default=None, converter=as_utc)
    #: Whether the hierarchy has a *formal* level structure.
    #:
    #: SDMX 3.1 ``HierarchyType`` carries a required ``@hasFormalLevels``
    #: distinguishing two semantically different shapes:
    #:
    #:   - **Leveled** (``True``): every node lives in exactly one named
    #:     ``Level``. The level chain in ``levels`` is load-bearing.
    #:   - **Value-based** (``False``): the tree has no formal level
    #:     concept. ``levels`` may still be populated for descriptive
    #:     metadata but doesn't constrain node placement.
    #:
    #: SDMX-ML 2.1 expresses the same flag as ``@leveled`` on the
    #: ``<str:Hierarchy>`` element (default ``false``).
    has_formal_levels: bool = False
    levels: ItemList[Level] = field(factory=ItemList, converter=_to_levels)
    tree: ItemList[HierarchicalCode] = field(factory=ItemList, converter=_to_tree)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)
    #: SDMX ``@isExternalReference``, ``@serviceURL``, ``@structureURL`` — see
    #: `MaintainableArtefact` (#422).
    is_external_reference: bool = False
    service_url: str | None = None
    structure_url: str | None = None
    #: SDMX 3.1 ``@isPartialLanguage``: some text is missing a language the
    #: rest carries (#422).
    is_partial_language: bool = False
    # Set by `__attrs_post_init__`, so no default: the index is a function of
    # the tree field and has no meaning before it.
    _tree: PathIndex[HierarchicalCode] = field(init=False, repr=False, eq=False, metadata=derived_view())

    def __attrs_post_init__(self) -> None:
        # Construction, not mutation: the one write a frozen class makes to itself (#492).
        object.__setattr__(self, "_tree", PathIndex(self.tree, lambda n: n.children))
        # Its whole-artefact rules, once the index they read is built (#492).
        enforce(self)

    @property
    @override
    def _paths(self) -> PathIndex[HierarchicalCode]:
        return self._tree

    @property
    def codes(self) -> tuple[HierarchicalCode, ...]:
        """Every node in the hierarchy, in pre-order.

        A tuple, not an id-keyed list: a node id may repeat in another branch
        (#439), so there is no single node for a leaf id. Look a node up by
        path with ``hierarchy["CANADA.ON"]``, or by leaf with
        ``hierarchy.by_id("ON")``.
        """
        return self._tree.nodes()

    # ── SKOS/XKOS hierarchy navigation ────────────────────────────────────
    # Path-keyed, from `PathNavigation`; `roots` is this hierarchy's own field.

    def roots(self) -> ItemList[HierarchicalCode]:
        """Top-level nodes (no parent). Equivalent to ``self.tree``."""
        return self.tree

    def descendants(self, root_code_id: str) -> list[str]:
        """Code ids of all descendants of ``root_code_id`` in this hierarchy.

        Pure-Python counterpart to a stored hierarchy's
        ``narrower_transitive(path)`` — use this when you want the descendant
        set without a SQL round-trip (e.g. to feed a small id list into a
        `ComponentFilter`'s ``dimensions``). Keyed by *code* id, not by path:
        it finds every placement of the code.

        Matches nodes by underlying ``code`` URN's item id (or by
        ``node.id`` if a node has no ``code`` ref). If multiple nodes
        reference the same root code, walks every subtree and dedupes
        while preserving first-seen order. Returns ``[]`` if no node in
        this hierarchy references ``root_code_id``.
        """
        roots = [n for n in self._tree.nodes() if _node_code_id(n) == root_code_id]
        if not roots:
            return []
        seen: list[str] = []
        seen_set: set[str] = set()
        for root in roots:
            for desc in self.narrower_transitive(self._tree.path_of(root)):
                cid = _node_code_id(desc)
                if cid is not None and cid not in seen_set:
                    seen.append(cid)
                    seen_set.add(cid)
        return seen


@frozen(unsafe_hash=False)
class HierarchyAssociation(MaintainableArtefact):
    """Binds a Hierarchy to an identifiable artefact within an optional context.

    SDMX 3.0 / 3.1 ``HierarchyAssociationType``. The Hierarchy itself is
    structure-agnostic — it just defines a code tree. The association
    says *which dimension* (or other identifiable) the hierarchy applies
    to, optionally within a particular context (e.g. a Dataflow).

    Example:
        sl.HierarchyAssociation(
            id="HA_GEO_REF_AREA", maintainer="ESTAT",
            linked_hierarchy=hierarchy_ref,
            linked_object=dim_ref,
            context_object=dataflow_ref,
        )

    SDMX-ML 2.1 has none, and binds a hierarchy to nothing, so a 2.1 write
    leaves it out (`formats.gaps.dropped`, #421).
    """

    sdmx_package: ClassVar[str] = "codelist"
    sdmx_class: ClassVar[str] = "HierarchyAssociation"
    msg_field: ClassVar[str] = "hierarchy_associations"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    valid_from: datetime | None = field(default=None, converter=as_utc)
    valid_to: datetime | None = field(default=None, converter=as_utc)
    #: The Hierarchy this association is wiring up. Required per the XSD.
    linked_hierarchy: Ref[Hierarchy] | None = field(converter=to_optional_ref, default=None, validator=refuse_urn_text)
    #: The identifiable artefact the hierarchy is bound to (e.g. a
    #: Dimension in a DataStructure). Required per the XSD; modelled as
    #: ``Ref[object]`` because the target can be any kind of identifiable.
    linked_object: Ref[object] | None = field(converter=to_optional_ref, default=None, validator=refuse_urn_text)
    #: Optional context for the binding (e.g. a Dataflow). Distinguishes
    #: "this dimension uses this hierarchy in the context of *that*
    #: dataflow" from a global binding.
    context_object: Ref[object] | None = field(converter=to_optional_ref, default=None, validator=refuse_urn_text)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)
    #: SDMX ``@isExternalReference``, ``@serviceURL``, ``@structureURL`` — see
    #: `MaintainableArtefact` (#422).
    is_external_reference: bool = False
    service_url: str | None = None
    structure_url: str | None = None
    #: SDMX 3.1 ``@isPartialLanguage``: some text is missing a language the
    #: rest carries (#422).
    is_partial_language: bool = False


def _partition_associations(
    registry: SupportsGetAll,
    dataflow: Dataflow,
    dim_id: str,
) -> tuple[str, str, list[HierarchyAssociation], list[HierarchyAssociation]]:
    """Internal helper: enumerate HAs binding (dim_id, dataflow), split by scope.

    Returns (dim_urn_str, dataflow_urn_str, scoped_HAs, unscoped_HAs) where
    ``scoped`` are HAs with ``context_object`` equal to ``dataflow``'s URN
    and ``unscoped`` have ``context_object=None``. HAs naming a different
    dataflow context are omitted.

    ``registry`` only needs ``get_all(HierarchyAssociation) -> list``, so it is
    typed to the `SupportsGetAll` Protocol — backend-agnostic, and
    ``InMemoryRegistry`` and ``local.Registry`` both satisfy it structurally.
    """
    if dataflow.structure is None:
        return ("", "", [], [])
    dsd_urn = dataflow.structure.urn
    dim_urn = SdmxUrn.make(
        Dimension,
        package="datastructure",
        artefact_class="Dimension",
        agency=dsd_urn.agency,
        id=dsd_urn.id,
        version=dsd_urn.version,
        item_id=dim_id,
    )
    dim_urn_str = str(dim_urn)
    df_urn_str = str(artefact_urn(dataflow))

    scoped: list[HierarchyAssociation] = []
    unscoped: list[HierarchyAssociation] = []
    for ha in registry.get_all(HierarchyAssociation):
        if ha.linked_object is None or str(ha.linked_object.urn) != dim_urn_str:
            continue
        if ha.context_object is None:
            unscoped.append(ha)
        elif str(ha.context_object.urn) == df_urn_str:
            scoped.append(ha)
    return (dim_urn_str, df_urn_str, scoped, unscoped)


def _applicable_hierarchies(
    registry: SupportsGetAll,
    dataflow: Dataflow,
    dim_id: str,
) -> list[Ref[Hierarchy]]:
    """Implementation of the `applicable_hierarchies` registry method.

    See `InMemoryRegistry.applicable_hierarchies` for semantics.
    """
    _dim, _df, scoped, unscoped = _partition_associations(registry, dataflow, dim_id)
    scoped.sort(key=lambda ha: str(artefact_urn(ha)))
    unscoped.sort(key=lambda ha: str(artefact_urn(ha)))
    return [ha.linked_hierarchy for ha in (*scoped, *unscoped) if ha.linked_hierarchy is not None]


def _unique_hierarchy(
    registry: SupportsGetAll,
    dataflow: Dataflow,
    dim_id: str,
) -> Ref[Hierarchy] | None:
    """Implementation of the `unique_hierarchy` registry method.

    See `InMemoryRegistry.unique_hierarchy` for semantics.
    """
    dim_urn_str, df_urn_str, scoped, unscoped = _partition_associations(
        registry,
        dataflow,
        dim_id,
    )
    chosen = scoped or unscoped
    if not chosen:
        return None
    if len(chosen) > 1:
        urns = [str(artefact_urn(ha)) for ha in chosen]
        urns.sort()
        raise AmbiguousHierarchyError(
            dim_urn=dim_urn_str,
            dataflow_urn=df_urn_str,
            ha_urns=urns,
        )
    return chosen[0].linked_hierarchy


@frozen(unsafe_hash=False)
class HierarchicalCodelist(MaintainableArtefact):
    """A 2.1 container grouping related hierarchies.

    SDMX-ML 2.1 nests every ``<str:Hierarchy>`` inside a
    ``<str:HierarchicalCodelist>``, which is a maintainable in its own right —
    id, agency, version, validity dates, name — and asserts that the
    hierarchies inside it belong together. SDMX 3.0 removed it and promoted
    `Hierarchy` to a top-level artefact.

    sdmxlib follows 3.0 for the hierarchies themselves and models the
    container as a peer that *references* them, for the same reason
    `StructureSet` is modelled that way: one copy of the grouping, rather than
    the container's identity denormalised onto each member.

    Before this existed, reading a 2.1 document kept the inner hierarchy's
    name and silently discarded the container's — so a codelist named
    "Geographic hierarchy" containing a hierarchy named
    "Canada → provinces → cities" came back with only the second, and
    re-emitting produced a different document (#239).

    Written only to SDMX-ML 2.1. The other targets leave it out and write its
    hierarchies (`formats.gaps.dropped`, #421); a 2.1 reader names each one
    for it (`model._container.member_id`).
    """

    sdmx_package: ClassVar[str] = "codelist"
    sdmx_class: ClassVar[str] = "HierarchicalCodelist"
    msg_field: ClassVar[str] = "hierarchical_codelists"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    #: SDMX 2.1's `isFinal`, kept only where it disagrees with the version
    #: (see `MaintainableArtefact.is_final`). Only the 2.1 reader sets it.
    legacy_is_final: bool | None = None
    #: References to the hierarchies this codelist groups, in document order.
    hierarchies: tuple[Ref[object], ...] = field(factory=tuple, converter=to_refs, validator=refuse_urn_text)
    #: ``<str:IncludedCodelist>`` — the codelists whose codes the hierarchies
    #: arrange. 2.1 states them explicitly; 3.0 leaves them implicit in each
    #: HierarchicalCode's own code reference.
    included_codelists: tuple[Ref[object], ...] = field(factory=tuple, converter=to_refs, validator=refuse_urn_text)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    valid_from: datetime | None = field(default=None, converter=as_utc)
    valid_to: datetime | None = field(default=None, converter=as_utc)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX ``@isExternalReference``, ``@serviceURL``, ``@structureURL`` — see
    #: `MaintainableArtefact` (#422).
    is_external_reference: bool = False
    service_url: str | None = None
    structure_url: str | None = None
    #: SDMX 3.1 ``@isPartialLanguage``: some text is missing a language the
    #: rest carries (#422).
    is_partial_language: bool = False
