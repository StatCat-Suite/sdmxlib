"""The relation accessors, exported as `sl.q`.

Lowercase singletons rather than uppercase classes, matching the existing
`sl.code` / `sl.artefact` and their separate `CodeRow` / `ArtefactRow` phantom
types. The accessor does four jobs — namespace for column names, target of
`edge()`, root anchor for `reg.query()`, and row-kind witness — and only the
last needs a *type*, which the library already keeps separate.

That spelling also carries the law separating the two traversals of this graph.
`Ref.__getattr__` resolves eagerly and returns domain objects; these compile to
SQL. The discriminator is the namespace, present at every call site:

    dim.codelist.codes              resolves now, returns Code objects
    sl.q.component.codelist.codes   compiles to SQL

> **`sl.q.` compiles. Everything else resolves.**

The accessor classes are private and only the singletons are exported, so
"never instantiated by a caller" needs no metaclass guard — it is what
`_CodeAccessor` already does.

**The maintainable surface is hoisted, not pathed.** `sdmx.dataflow` is
`urn PRIMARY KEY` against `sdmx.artefact`'s own `urn` — PK-to-PK and mandatory,
so the split is physical layout rather than a choice, and there is no `spine`
hop to write. `tests/unit/test_query_edges.py` fails loudly if that stops being
true, because hoisted columns would then silently multiply or drop rows.

The consequence is that **every accessor roots at the table holding its
columns**, which for a maintainable is always `sdmx.artefact`. An accessor
whose own *edge* lives on the extension declares that edge as a compound hop
across the split rather than moving its root — `_Dataflow.dsd` is
`("dataflow", "dsd")`, and `_Dsd.dataflows` is `("dataflows", "spine")` back.
Rooting on the extension instead is the reading that looks right and is not:
`q.dataflow.agency` would then bind `agency_id` against a two-column table.
See `tests/unit/test_query_dataflow.py`.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import TYPE_CHECKING, Never

from sdmxlib.model.category import CategoryScheme
from sdmxlib.model.codelist import Code
from sdmxlib.model.collections import Maintainable
from sdmxlib.model.concept import ConceptScheme
from sdmxlib.model.constraint import DataConstraint
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.hierarchy import HierarchicalCode, HierarchyAssociation
from sdmxlib.model.mapping import (
    CategorySchemeMap,
    ConceptSchemeMap,
    OrganisationSchemeMap,
    RepresentationMap,
    StructureMap,
)
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSection, MetadataSet
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.organisation import (
    AgencyScheme,
    DataConsumerScheme,
    DataProviderScheme,
    MetadataProviderScheme,
    OrganisationUnitScheme,
)
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.query._expr import (
    AnyCol,
    AnyCols,
    Arr,
    Bool,
    Cols,
    FanOut,
    Localised,
    Num,
    RowKind,
    SideLocalised,
    Str,
    Temporal,
    Version,
    Vocabulary,
    acol,
    bcol,
    col,
    edge,
    extcol,
    label,
    many,
    ncol,
    tcol,
    text,
    vcol,
    vocab,
)

if TYPE_CHECKING:
    # `storage.lazy` imports this package at runtime, so these two cannot be
    # ordinary imports — `q.codelist` and `q.hierarchy` pin them as *string*
    # forward references, which no linter counts as a reference. The aliases
    # below are that reference, and they are what `_Codelist` / `_Hierarchy`
    # actually name (#396).
    from sdmxlib.storage.lazy import LazyCodelist, LazyHierarchy

    type CollectedCodelist = LazyCodelist
    type CollectedHierarchy = LazyHierarchy

# ── row kinds ─────────────────────────────────────────────────────────────


class ArtefactRow(RowKind):
    """Any maintainable artefact, queried polymorphically."""

    __slots__ = ()


class DataflowRow(RowKind):
    """A published data product."""

    __slots__ = ()


class DsdRow(RowKind):
    """A data structure definition."""

    __slots__ = ()


class CodelistRow(RowKind):
    """A codelist."""

    __slots__ = ()


class ConceptSchemeRow(RowKind):
    """A concept scheme."""

    __slots__ = ()


class ComponentRow(RowKind):
    """One dimension, attribute or measure of a DSD."""

    __slots__ = ()


class CodeRow(RowKind):
    """One code within a codelist."""

    __slots__ = ()


class ConceptRow(RowKind):
    """One concept within a scheme."""

    __slots__ = ()


class CategoryRow(RowKind):
    """One category within a category scheme."""

    __slots__ = ()


class CategorisationRow(RowKind):
    """One binding of an artefact to a category."""

    __slots__ = ()


class HierarchyRow(RowKind):
    """A hierarchy — a standalone tree of codes."""

    __slots__ = ()


class HCodeRow(RowKind):
    """One placement of a code within a hierarchy."""

    __slots__ = ()


class MetadataSetRow(RowKind):
    """A populated reference-metadata payload."""

    __slots__ = ()


class ReportedAttributeRow(RowKind):
    """One reported attribute value within a metadata set."""

    __slots__ = ()


class MetadataTargetRow(RowKind):
    """One artefact a metadata set describes."""

    __slots__ = ()


class MsdRow(RowKind):
    """A metadata structure definition."""

    __slots__ = ()


class MetadataflowRow(RowKind):
    """A published reference-metadata product."""

    __slots__ = ()


class MetadataProvisionAgreementRow(RowKind):
    """One binding of a metadata provider to a metadataflow."""

    __slots__ = ()


class MetadataAttributeRow(RowKind):
    """One declared slot in an MSD's attribute tree."""

    __slots__ = ()


class AgencyRow(RowKind):
    """One agency within an agency scheme."""

    __slots__ = ()


class ContactRow(RowKind):
    """One contact point of an agency."""

    __slots__ = ()


class DataProviderRow(RowKind):
    """One data provider within a data-provider scheme."""

    __slots__ = ()


class MetadataProviderRow(RowKind):
    """One metadata provider within a metadata-provider scheme (#315)."""

    __slots__ = ()


class DataConsumerRow(RowKind):
    """One data consumer within a data-consumer scheme (#469)."""

    __slots__ = ()


class OrganisationUnitRow(RowKind):
    """One organisation unit within an organisation-unit scheme (#469)."""

    __slots__ = ()


class ProvisionAgreementRow(RowKind):
    """One binding of a data provider to a dataflow."""

    __slots__ = ()


class GroupRow(RowKind):
    """One group descriptor of a DSD."""

    __slots__ = ()


class RepresentationMapRow(RowKind):
    """A code-to-code translation between two codelists."""

    __slots__ = ()


class DataConstraintRow(RowKind):
    """A constraint on what a dataflow, DSD or provider may contain."""

    __slots__ = ()


class CubeRegionRow(RowKind):
    """One included or excluded slice of a constrained cube."""

    __slots__ = ()


class KeyValueRow(RowKind):
    """One permitted value of one component within a cube region."""

    __slots__ = ()


class HierarchyAssociationRow(RowKind):
    """One binding of a hierarchy to the object it organises."""

    __slots__ = ()


class ConceptSchemeMapRow(RowKind):
    """A concept-to-concept map between two schemes."""

    __slots__ = ()


class CategorySchemeMapRow(RowKind):
    """A category-to-category map between two schemes."""

    __slots__ = ()


class OrganisationSchemeMapRow(RowKind):
    """An organisation-to-organisation map between two schemes."""

    __slots__ = ()


class StructureMapRow(RowKind):
    """A component-to-component map between two structures."""

    __slots__ = ()


class ItemMapRow(RowKind):
    """One source-item to target-item pair, in a scheme map."""

    __slots__ = ()


class ComponentMapRow(RowKind):
    """One mapping of source components to target components, in a structure map."""

    __slots__ = ()


class MapEndpointRow(RowKind):
    """One source or target of a representation map — a codelist or a data type."""

    __slots__ = ()


class RepresentationMappingRow(RowKind):
    """One mapping of source values to target values, within a representation map."""

    __slots__ = ()


class AnnotationRow(RowKind):
    """One annotation."""

    __slots__ = ()


class AnnotationURLRow(RowKind):
    """One URL an annotation points at."""

    __slots__ = ()


# ── representation ────────────────────────────────────────────────────────


class _Late[T: AnyCols]:
    """An edge declared in its owner's body whose target class comes later.

    `code.ancestors` is a `_ManyCode`, and `_ManyCode` is a `_Code`, so the
    target does not exist while `_Code`'s body runs; `dataflow.constraints`
    reaches `_DataConstraint`, which reaches `_Dataflow`. The declaration is
    built on first read instead, then binds onto the reading accessor exactly
    as one written in the body would.

    It replaced an annotation in the body plus a `setattr` once the target
    existed. An annotation binds nothing, so a forgotten `setattr` type-checked
    and raised `AttributeError` (#307), and pyrefly 1.4's
    `uninitialized-instance-variable` refuses the shape. Here the declaration
    is the binding, and its type is the class it builds.
    """

    __slots__ = ("_declaration", "_make")

    def __init__(self, make: Callable[[], T]) -> None:
        self._make = make
        self._declaration: T | None = None

    def __get__(self, obj: object, owner: type | None = None) -> T:
        # Built once; two threads racing here build equal declarations.
        declaration = self._declaration
        if declaration is None:
            declaration = self._declaration = self._make()
        return declaration.__get__(obj, owner)


class _FacetColumn[T: AnyCol]:
    """One facet column: declared once on `_Facet`, built for each owner's table.

    Three owners carry a facet — `concept`, `dsd_component`,
    `metadata_attribute` — and each column must read *its* table. Declared
    per owner, the group drifted twice; built from a table in
    `__init_subclass__`, the columns were attributes nothing in the class
    body set. This declares each once and builds it on first read, for the
    owner it is read through.
    """

    __slots__ = ("_by_owner", "_declare", "_name")

    def __init__(self, declare: Callable[[str, str], T]) -> None:
        self._declare = declare
        self._name = ""
        self._by_owner: dict[type, T] = {}

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    def __get__(self, obj: object, owner: type | None = None) -> T:
        holder = type(obj) if owner is None else owner
        declaration = self._by_owner.get(holder)
        if declaration is None:
            if not (issubclass(holder, AnyCols) and holder.__table__):
                msg = f"facet column {self._name!r} read through {holder.__name__}, which names no table"
                raise AttributeError(msg)
            declaration = self._by_owner[holder] = self._declare(holder.__table__, self._name)
        return declaration.__get__(obj, owner)


@functools.cache
def _artefact_types() -> frozenset[str]:
    """Legal `artefact.artefact_type` values, from the writer's own registry.

    Deferred: `storage` imports this module, so naming it at class-definition
    time is a cycle. Cached because a predicate built in a loop should not pay
    for the lookup each time.
    """
    from sdmxlib.storage._kinds import ARTEFACT_TYPES  # noqa: PLC0415  (cycle: storage imports accessors)

    return ARTEFACT_TYPES


@functools.cache
def _component_types() -> frozenset[str]:
    """Legal `dsd_component.component_type` values. Deferred, as above."""
    from sdmxlib.storage._kinds import COMPONENT_TYPES  # noqa: PLC0415  (cycle: storage imports accessors)

    return COMPONENT_TYPES


class _Facet[R: RowKind](Cols[R, Never]):
    """The non-enumerated arm of `Representation`, as a namespace.

    `Representation = Ref[Codelist] | Facet | None` is a sum type, and the two
    arms read symmetrically here: `component.codelist.id` for the enumerated
    one, `component.facet.max_length` for this one. Named after the IM class,
    per the naming rule.

    **A zero-hop accessor, not an edge.** The facet columns live on the owner's
    own row, so this binds the empty path and its columns resolve exactly as a
    flat `component.max_length` would — no join, nothing for the planner to do.
    It exists to *group* the columns that only mean something together, and
    to keep them from crowding a surface where `position` and `role` live.

    The columns are declared once here, as `_FacetColumn`s each owner reads
    against its own table, rather than restated per owner. Three owners carry a facet — `concept`,
    `dsd_component`, `metadata_attribute` — and when the group was written out
    three times it drifted twice.
    """

    __slots__ = ()

    text_type = _FacetColumn(lambda table, column: Str[R]((), table, column))
    min_length = _FacetColumn(lambda table, column: Num[R]((), table, column))
    max_length = _FacetColumn(lambda table, column: Num[R]((), table, column))
    min_value = _FacetColumn(lambda table, column: Num[R]((), table, column))
    max_value = _FacetColumn(lambda table, column: Num[R]((), table, column))
    decimals = _FacetColumn(lambda table, column: Num[R]((), table, column))
    pattern = _FacetColumn(lambda table, column: Str[R]((), table, column))
    is_sequence = _FacetColumn(lambda table, column: Bool[R]((), table, column))
    start_value = _FacetColumn(lambda table, column: Num[R]((), table, column))
    end_value = _FacetColumn(lambda table, column: Num[R]((), table, column))
    is_multilingual = _FacetColumn(lambda table, column: Bool[R]((), table, column))
    interval = _FacetColumn(lambda table, column: Num[R]((), table, column))
    time_interval = _FacetColumn(lambda table, column: Str[R]((), table, column))
    start_time = _FacetColumn(lambda table, column: Str[R]((), table, column))
    end_time = _FacetColumn(lambda table, column: Str[R]((), table, column))


class _ConceptFacet[R: RowKind](_Facet[R]):
    """`concept.facet` — a concept's core representation."""

    __slots__ = ()
    __table__ = "concept"


class _ComponentFacet[R: RowKind](_Facet[R]):
    """`component.facet` — a dimension's, attribute's or measure's own."""

    __slots__ = ()
    __table__ = "dsd_component"


class _AttributeFacet[R: RowKind](_Facet[R]):
    """`metadata_attribute.facet` — an MSD slot's representation."""

    __slots__ = ()
    __table__ = "metadata_attribute"


# ── accessors ─────────────────────────────────────────────────────────────


class _AnnotationURL[R: RowKind](Cols[R, Never]):
    """One `annotation_url` row — a URL an annotation points at (#422).

    Keyed by the annotation and the URL's position rather than its language:
    two URLs may share a language, or have none.
    """

    __slots__ = ()
    __table__ = "annotation_url"
    __id__ = "url"
    __key__ = ("annotation_id", "position")

    url: Str[R] = col("annotation_url", "url")
    #: The language of the resource, or null when it is not localised.
    lang: Str[R] = col("annotation_url", "lang")
    #: The SDMX URN of what the URL points at — SDMX-JSON's, null otherwise.
    #: Not `urn`, which names a row's own identity on every other accessor.
    link_urn: Str[R] = col("annotation_url", "link_urn")
    #: The kind of resource, such as ``"PDF"`` — SDMX-JSON's, null otherwise.
    type: Str[R] = col("annotation_url", "link_type")
    position: Num[R] = ncol("annotation_url", "position")
    annotation: _Annotation[R] = edge("annotation", "_Annotation")


class _ManyAnnotationURL[P: RowKind](_AnnotationURL[P], FanOut[P, AnnotationURLRow]):
    """`annotation.urls`."""

    __slots__ = ()


class _Annotation[R: RowKind](Cols[R, Never]):
    """One annotation attached to an artefact, code or concept."""

    __slots__ = ()
    __table__ = "annotation"
    __id__ = "annotation_id"
    __key__ = ("id",)

    id: Str[R] = col("annotation", "annotation_id")
    type: Str[R] = col("annotation", "annotation_type")
    title: Str[R] = col("annotation", "title")
    #: Held in `sdmx.annotation_text`, keyed by `(annotation_id, lang)` — the
    #: second side-table shape, and the reason `text()` takes the table and
    #: key rather than assuming `localized_text`.
    #:
    #: `owner="id"` and not `"annotation_id"`: on `sdmx.annotation` those are
    #: two different columns. `id` is the surrogate the text table references;
    #: `annotation_id` is SDMX's own optional identifier, which is what the
    #: `id` *accessor* above exposes.
    text: SideLocalised[R] = text("", owner="id", table="annotation_text", key="annotation_id")
    #: Every URL, each with the language of its resource (#422) — a fan-out,
    #: since SDMX 3.x allows several: `.urls.url.contains(...)` is *some* URL.
    urls: _ManyAnnotationURL[R] = many("urls", _ManyAnnotationURL)
    position: Num[R] = ncol("annotation", "ordinal")


class _ManyAnnotation[P: RowKind](_Annotation[P], FanOut[P, AnnotationRow]):
    """`<anything>.annotations`.

    A to-many side table, so it behaves exactly like any other fan-out: dotting
    through means *some* annotation, `.any()` groups predicates onto one, and
    `.list()` collects. No special case — which is the point of having declared
    it as an edge rather than as a bespoke predicate.
    """

    __slots__ = ()


class _Maintainable[R: RowKind, A = Maintainable](Cols[R, A]):
    """The surface every maintainable artefact shares, hoisted onto each.

    Generic over the row kind rather than concrete, which is what keeps
    `_Dataflow(_Maintainable[DataflowRow])` and
    `_Codelist(_Maintainable[CodelistRow])` distinct. A concrete base would
    collapse them, and every cross-root predicate would then type-check and
    compile to the wrong relation.
    """

    __slots__ = ()
    __table__ = "artefact"

    urn: Str[R] = col("artefact", "urn")
    #: A maintainable's labels live in `sdmx.localized_text` keyed by
    #: (owner_urn, field, lang), not in an inline `MAP` like `code.name`, so
    #: these compile to a correlated scalar subquery rather than a subscript.
    #: Both spell the same at a call site: `q.dsd.name["en"]`.
    name: SideLocalised[R] = text("name")
    description: SideLocalised[R] = text("description")
    annotations: _ManyAnnotation[R] = many("annotations", _ManyAnnotation)
    agency: Str[R] = col("artefact", "agency_id")
    #: The SDMX class name — `Codelist`, `Dataflow`. The discriminator the
    #: named roots apply for you, exposed for a query over `q.artefact` that
    #: needs to narrow by type at runtime.
    type: Vocabulary[R] = vocab("artefact", "artefact_type", _artefact_types, "q.artefact.type")
    id: Str[R] = col("artefact", "resource_id")
    #: SDMX ``@uri``: an external link describing it, not an identifier (#444).
    uri: Str[R] = col("artefact", "uri")
    #: `Version`, not `Str`: it compares as text and *orders* by the sort key
    #: baked beside it, because `1.9` sorts after `1.10` otherwise (#311).
    version: Version[R] = vcol("artefact", "version")
    is_final: Bool[R] = bcol("artefact", "is_final")
    #: The validity window SDMX gives every maintainable. Stored since the
    #: schema's first version and named by `design/query-language.md` as part
    #: of the hoisted maintainable surface — but never actually exposed, so
    #: `q.artefact.valid_from` was an `AttributeError` and as-of queries had no
    #: spelling at all.
    #:
    #: `Temporal`, not `Num`: both compare and order correctly, but only one
    #: can tell a projected frame that the value is an instant, so it comes
    #: back labelled UTC like `reg.get(...).valid_from` does. See `Temporal`.
    valid_from: Temporal[R] = tcol("artefact", "valid_from")
    valid_to: Temporal[R] = tcol("artefact", "valid_to")
    #: Built lazily (`_Late`): both fan-outs reach classes that are themselves
    #: maintainables, so neither can be named while this class is being built.
    #:
    #: Every artefact may be filed under categories and may have reference
    #: metadata reported about it, so both belong here rather than on one
    #: subclass — `in_category` and `has_metadata` on the retired
    #: `ArtefactQuery` were exactly these two reads.
    categorisations = _Late(lambda: _ManyCategorisation[R](("categorisations",)))
    described_by = _Late(lambda: _ManyMetadataTarget[R](("described_by",)))


class _Concept[R: RowKind](Cols[R, Never]):
    """One concept within a scheme."""

    __slots__ = ()
    __table__ = "concept"
    __id__ = "concept_id"
    __key__ = ("concept_urn",)

    urn: Str[R] = col("concept", "concept_urn")
    name: Localised[R] = label("concept", "name")
    annotations: _ManyAnnotation[R] = many("annotations", _ManyAnnotation)
    id: Str[R] = col("concept", "concept_id")
    uri: Str[R] = col("concept", "uri")
    position: Num[R] = ncol("concept", "position")
    #: Depth in the parent chain, root = 0 — baked by the writer, like the
    #: other three trees.
    depth: Num[R] = ncol("concept", "depth")
    scheme: _ConceptScheme[R] = edge("scheme", "_ConceptScheme")
    #: The concept's *default* representation — distinct from a component's
    #: own `codelist`, and the pair is why `dsd -> codelist` has two routes
    #: with different answers whenever a structure overrides.
    codelist: _Codelist[R] = edge("codelist", "_Codelist")
    #: The other arm of the same representation — see `_Facet`.
    facet: _ConceptFacet[R] = _ConceptFacet(())
    #: Concepts nest within a scheme — SDMX-ML's `<str:Parent>`, which the XSD
    #: calls "the semantic relationships between concepts which occur within a
    #: single concept scheme". Built lazily (`_Late`) once `_ManyConcept`
    #: exists, exactly as for codes and categories.
    #:
    #: This was a *hole* until #307, and an instructive one: the storage column
    #: existed from the start, the writer put a constant `NULL` in it, and the
    #: model had no parent field at all — so the wire element was parsed by
    #: nobody and a nested scheme came back flat, on read and on round-trip
    #: write. The class briefly carried these three annotations copied from
    #: `_Code`; annotations bind nothing, so they type-checked and raised
    #: `AttributeError`. An un-populated column is an absence, and absences do
    #: not fail tests.
    ancestors = _Late(lambda: _ManyConcept[R](("ancestors",)))
    descendants = _Late(lambda: _ManyConcept[R](("descendants",)))
    children = _Late(lambda: _ManyConcept[R](("children",)))
    parent = _Late(lambda: _Concept[R](("parent",)))


class _Code[R: RowKind](Cols[R, Code]):
    """One code within a codelist."""

    __slots__ = ()
    __table__ = "code"
    __id__ = "code_id"
    __key__ = ("code_urn",)

    urn: Str[R] = col("code", "code_urn")
    name: Localised[R] = label("code", "name")
    annotations: _ManyAnnotation[R] = many("annotations", _ManyAnnotation)
    id: Str[R] = col("code", "code_id")
    uri: Str[R] = col("code", "uri")
    position: Num[R] = ncol("code", "position")
    depth: Num[R] = ncol("code", "depth")
    codelist: _Codelist[R] = edge("codelist", "_Codelist")

    #: Every ancestor, every descendant, and the direct children — one
    #: relationship each, built lazily (`_Late`) once `_ManyCode` exists.
    ancestors = _Late(lambda: _ManyCode[R](("ancestors",)))
    descendants = _Late(lambda: _ManyCode[R](("descendants",)))
    children = _Late(lambda: _ManyCode[R](("children",)))
    parent = _Late(lambda: _Code[R](("parent",)))


class _Level[R: RowKind](Cols[R, Never]):
    """One `hierarchy_level` row — a named rung of a hierarchy.

    Reached as `hcode.level`. Keyed `(hierarchy_urn, level_id)`, so the edge
    that reaches it is composite for the same reason the tree edges are: a
    level id is unique within one hierarchy, not across them.

    `index` is the rung's position from the root, and it is the level's own
    ordering rather than a placement's — `hcode.depth` counts a placement's
    own ancestors, which need not agree when a hierarchy skips levels.
    """

    __slots__ = ()
    __table__ = "hierarchy_level"
    __id__ = "level_id"
    __key__ = ("hierarchy_urn", "level_id")

    id: Str[R] = col("hierarchy_level", "level_id")
    uri: Str[R] = col("hierarchy_level", "uri")
    name: Localised[R] = label("hierarchy_level", "name")
    description: Localised[R] = label("hierarchy_level", "description")
    index: Num[R] = ncol("hierarchy_level", "level_idx")


class _HCode[R: RowKind](Cols[R, HierarchicalCode]):
    """One `HierarchicalCode` — a *placement* of a code within a hierarchy.

    Named `hcode`, exposed as `q.hcode` and `hierarchy.hcodes`. The SDMX class
    is `HierarchicalCode`, which both sdmx1 and pysdmx spell in full and which
    is what our own model class is called; `hcode` shortens only the
    *qualifier*, leaving the head noun intact so the pair stays legible:

        q.codelist.codes        Code
        q.hierarchy.hcodes      HierarchicalCode
        q.hcode.code.id         the Code this HierarchicalCode places

    That last line is the IM relationship written out, and it reads only
    because both names share the `code` morpheme. An earlier spelling, `node`,
    did not: `code` and `node` share nothing, so nothing in the names said the
    two were related, and `q.node.code` needed prose to explain itself. It was
    also a general graph word doing duty for one specific class. The full
    `hierarchical_code` was measured and rejected — 35 lines past the 120-char
    limit, and `hierarchical_code.code` reads as a nesting error.

    **A placement is not a code**, which is the reason `RowKind` exists at all.
    The spec is explicit that *"a single code may have multiple parents within
    the hierarchy"* — so one `Code` may be placed several times in one
    hierarchy, and each placement needs its own identity. `sdmx.code` is keyed
    `(codelist_urn, code_id)`; a placement is keyed `(hierarchy_urn, path)` —
    its id may repeat in another branch (#439) — carries its own parent, level
    and validity period, and *may*
    reference a code in some codelist. The same tree shape over two relations,
    which is why a predicate built for one must not compile against the other.

    The storage table keeps its physical name, `hierarchy_node` — layout is not
    API, and renaming it would touch the writers and readers for no gain.

    `code` is optional here though SDMX 3.0 requires it: #276's survey found
    real hierarchies with bare placements, so the leniency is deliberate.

    No `name` or `description`: a hierarchical code is identifiable, not
    nameable, in every SDMX format, and the name it shows is its code's —
    ``hcode.code.name[lang]`` (#419).
    """

    __slots__ = ()
    __table__ = "hierarchy_node"
    __id__ = "node_id"
    __key__ = ("node_urn",)

    urn: Str[R] = col("hierarchy_node", "node_urn")
    annotations: _ManyAnnotation[R] = many("annotations", _ManyAnnotation)
    id: Str[R] = col("hierarchy_node", "node_id")
    uri: Str[R] = col("hierarchy_node", "uri")
    #: The node's dotted path from its root — its identity in the hierarchy,
    #: where `id` may repeat across branches (#439). SDMX's name for it: a 2.1
    #: item reference's ``@id`` is a ``NestedIDType``. Not ``path``, which every
    #: accessor already uses for its own hop tuple.
    nested_id: Str[R] = col("hierarchy_node", "path")
    position: Num[R] = ncol("hierarchy_node", "position")
    #: The level this node sits at. There is no `level_id` column beside it:
    #: trimming makes `level.id` compile to exactly that, so the column would
    #: be a second spelling with nothing of its own to say.
    level: _Level[R] = edge("level", "_Level")
    depth: Num[R] = ncol("hierarchy_node", "depth")
    hierarchy: _Hierarchy[R] = edge("hierarchy", "_Hierarchy")
    #: The code this node places, when it has one.
    code: _Code[R] = edge("code", "_Code")
    #: Every ancestor, every descendant, and the direct children — one
    #: relationship each, built lazily (`_Late`) once `_ManyHCode` exists.
    ancestors = _Late(lambda: _ManyHCode[R](("ancestors",)))
    descendants = _Late(lambda: _ManyHCode[R](("descendants",)))
    children = _Late(lambda: _ManyHCode[R](("children",)))

    parent = _Late(lambda: _HCode[R](("parent",)))


class _ManyHCode[P: RowKind](_HCode[P], FanOut[P, HCodeRow]):
    """`hierarchy.hcodes`, and a placement's own tree relationships."""

    __slots__ = ()


class _Component[R: RowKind](Cols[R, Never]):
    """A `dsd_component` row — a dimension, attribute or measure.

    Not maintainable: it has no identity of its own, so it carries no `agency`
    or `version`. Reaching those means saying *whose*, which is what
    `component.dsd.agency` does.
    """

    __slots__ = ()
    __table__ = "dsd_component"
    __id__ = "component_id"
    __key__ = ("dsd_urn", "component_id")

    id: Str[R] = col("dsd_component", "component_id")
    uri: Str[R] = col("dsd_component", "uri")
    role: Vocabulary[R] = vocab("dsd_component", "component_type", _component_types, "q.component.role")
    position: Num[R] = ncol("dsd_component", "position")
    dsd: _Dsd[R] = edge("dsd", "_Dsd")
    concept: _Concept[R] = edge("concept", "_Concept")
    #: Every `HierarchyAssociation` binding a hierarchy to this component.
    #: Bound late — `_ManyHierarchyAssociation` reaches back here.
    hierarchy_associations = _Late(lambda: _ManyHierarchyAssociation[R](("hierarchy_associations", "spine")))
    #: The two arms of `Representation`. `codelist` is the enumerated one and
    #: is a real edge; `facet` is the free-text one and is a zero-hop
    #: namespace, because its columns live on this row.
    codelist: _Codelist[R] = edge("codelist", "_Codelist")
    facet: _ComponentFacet[R] = _ComponentFacet(())
    #: Attribute-only: whether the attribute must be present, and what it
    #: hangs off. `attachment_level` is `Dataset` / `Series` / `Observation` /
    #: `Group`; `attachment_group` names the group when it is `Group`.
    usage_status: Str[R] = col("dsd_component", "usage_status")
    attachment_level: Str[R] = col("dsd_component", "attachment_level")
    attachment_group: Str[R] = col("dsd_component", "attachment_group")
    #: Attribute-only, and the reason `related_dimensions` is a list: a
    #: dimension-attached attribute names the subset of dimensions it varies
    #: with, which is a set rather than one reference.
    related_dimensions: Arr[R] = acol("dsd_component", "related_dimensions")
    #: Measure-only.
    measure_usage: Str[R] = col("dsd_component", "measure_usage")
    #: The concept roles a component plays — again a set, so `.has()` rather
    #: than `==`.
    concept_roles: Arr[R] = acol("dsd_component", "concept_roles")


# ── fan-out accessors ─────────────────────────────────────────────────────
#
# A `Many` is the child accessor at the *parent's* row kind, plus `FanOut`.
# Dotting through it therefore works unchanged and yields the parent's row
# kind; the mixin adds `.any()` / `.every()`, whose predicates are child-rooted.
# One class per child relation, because the child accessor is what a caller
# dots into.


class _ManyComponent[P: RowKind](_Component[P], FanOut[P, ComponentRow]):
    """`dsd.components` — the dimensions, attributes and measures of a DSD."""

    __slots__ = ()


class _ManyCode[P: RowKind](_Code[P], FanOut[P, CodeRow]):
    """`codelist.codes`, and a code's own `ancestors` / `descendants` / `children`."""

    __slots__ = ()


class _ManyConcept[P: RowKind](_Concept[P], FanOut[P, ConceptRow]):
    """`scheme.concepts`."""

    __slots__ = ()


class _ManyDataflow[P: RowKind](_Maintainable[P], FanOut[P, DataflowRow]):
    """`dsd.dataflows`.

    A DSD may back many dataflows, which is the edge that caught seven design
    examples out by looking to-one — see
    `docs/design/query-language-examples.md` §5.

    Reached by `("dataflows", "spine")`: the inverse FK lands on the extension
    row, and one more hop crosses back to the spine so the hoisted columns
    resolve. Fan-out first, to-one after — the same shape as
    `q.dsd.dimensions.concept.id`, so the planner needs nothing new.

    **Based on `_Maintainable`, not `_Dataflow`** — the one place a `Many` does
    not subclass the accessor its caller is dotting into. That is what breaks
    the cycle letting `_Dataflow.dsd` reach `_Dsd`: `_Dsd.dataflows` would
    otherwise need `_Dataflow`, which needs `_Dsd`. The only thing given up is
    `q.dsd.dataflows.dsd` — the DSD of a flow reached *from* that DSD, the
    DSD you started at. Everything else a dataflow has is on `_Maintainable`.
    """

    __slots__ = ()


# ── maintainables carrying a fan-out ──────────────────────────────────────
#
# The inverses attach here rather than to `_Maintainable`. The storage edge
# points at `artefact` because that is where DSDs and codelists are stored, but
# a codelist has no components: the accessor is deliberately more precise than
# the physical edge.


class _Group[R: RowKind](Cols[R, Never]):
    """One `Group` of a DSD — a partial key that attributes may attach to.

    Not maintainable and not urn-keyed: a group is identified by
    `(dsd_urn, group_id)`, so every edge touching it is composite.

    `dimensions` is a **compound** path, `("dimensions", "dimension")`. SDMX
    models the membership as `GroupDimension`, a class holding only a reference
    to a dimension, so the storage has a junction row between the group and the
    component. The accessor spells the pair as one hop, and a caller writes
    `q.dsd.groups.dimensions.id` without ever naming the junction.

    That the word `dimensions` also names a facet on `q.dsd` is deliberate
    rather than tolerated: it means the same thing in both places, and
    resolution is per relation so neither shadows the other.
    """

    __slots__ = ()
    __table__ = "dsd_group"
    __id__ = "group_id"
    __key__ = ("dsd_urn", "group_id")

    id: Str[R] = col("dsd_group", "group_id")
    uri: Str[R] = col("dsd_group", "uri")
    dsd: _Dsd[R] = edge("dsd", "_Dsd")
    #: The dimensions forming this group's partial key.
    dimensions: _ManyComponent[R] = many(("dimensions", "dimension"), _ManyComponent)


class _ManyGroup[P: RowKind](_Group[P], FanOut[P, GroupRow]):
    """`dsd.groups`."""

    __slots__ = ()


class _Dsd[R: RowKind](_Maintainable[R, DataStructure]):
    """A data structure definition."""

    __slots__ = ()
    __constraint__ = ("artefact_type", ("DataStructure",))

    #: Every `dsd_component` row, unfiltered.
    #:
    #: **Note this is wider than `DataStructure.components`**, which omits the
    #: time dimension. This one is the raw relation, so a time dimension is a
    #: row like any other. Reach the domain's named collections through the
    #: facets below instead — they match it exactly.
    components: _ManyComponent[R] = many("components", _ManyComponent)
    #: `component_type = 'Dimension'`, matching `DataStructure.dimensions` —
    #: which likewise excludes the time and measure dimensions, those being
    #: separate scalar fields on the domain object.
    dimensions: _ManyComponent[R] = many("dimensions", _ManyComponent)
    attributes: _ManyComponent[R] = many("attributes", _ManyComponent)
    measures: _ManyComponent[R] = many("measures", _ManyComponent)
    dataflows: _ManyDataflow[R] = many(("dataflows", "spine"), _ManyDataflow)
    #: Group descriptors — partial keys that attributes attach to.
    groups: _ManyGroup[R] = many("groups", _ManyGroup)


class _Dataflow[R: RowKind](_Maintainable[R, Dataflow]):
    """A published data product.

    Rooted on `artefact` like every other maintainable, because that is where
    its columns and its discriminator live. Its one outgoing edge is the
    exception: `dsd_urn` sits on the extension table, so `dsd` is declared as
    the compound hop `("dataflow", "dsd")` — out to the extension, then along
    the FK. The identity split is physical layout, so no caller writes it.

    Rooting this at `"dataflow"` instead is the obvious-looking alternative and
    is wrong: every hoisted column would then bind against a table holding only
    `(urn, dsd_urn)`, and `q.dataflow.agency == "ESTAT"` fails in the binder.

    `dsd` targets `_Dsd` rather than the generic maintainable, so
    `q.dataflow.dsd.dimensions` works — `flow -> dsd -> dimensions` being the
    most common traversal in SDMX. That costs the definition order below: this
    class comes after `_Dsd`, and `_ManyDataflow` is based on `_Maintainable`
    so `_Dsd.dataflows` does not point back here.
    """

    __slots__ = ()
    __constraint__ = ("artefact_type", ("Dataflow",))

    dsd: _Dsd[R] = edge(("dataflow", "dsd"), "_Dsd")
    #: What this flow is declared to contain — built lazily (`_Late`), since
    #: `_ManyDataConstraint` reaches `_Dataflow` in turn.
    constraints = _Late(lambda: _ManyDataConstraint[R](("constraints", "constraint", "spine")))
    #: The agreements covering this flow — built lazily (`_Late`) once
    #: `_ManyProvisionAgreement` exists, which needs `_Dataflow` in turn.
    provision_agreements = _Late(lambda: _ManyProvisionAgreement[R](("provision_agreements", "spine")))


class _MetadataTarget[R: RowKind](Cols[R, Never]):
    """One `metadata_target` row — an artefact a metadata set describes.

    A join row with two outgoing edges that both point at `sdmx.artefact` and
    mean different things: `metadata_set` is the payload this target belongs to,
    `artefact` is what the payload is *about*. Naming them apart is what stops
    the two collapsing into one hop.
    """

    __slots__ = ()
    __table__ = "metadata_target"
    __id__ = "target_urn"
    __key__ = ("set_urn", "ordinal")

    position: Num[R] = ncol("metadata_target", "ordinal")
    metadata_set: _MetadataSet[R] = edge("metadata_set", "_MetadataSet")
    #: The artefact this target names — any identifiable one, which is the
    #: point of reference metadata.
    artefact: _Maintainable[R] = edge("artefact", "_Maintainable")


class _ManyMetadataTarget[P: RowKind](_MetadataTarget[P], FanOut[P, MetadataTargetRow]):
    """`metadata_set.targets`."""

    __slots__ = ()


class _MetadataSet[R: RowKind](_Maintainable[R, MetadataSet]):
    """A populated reference-metadata payload.

    Maintainable, with an extension holding the structure it was reported
    against — so `structure` crosses the identity split as a compound hop, the
    same shape as `_Dataflow.dsd`.

    SDMX 3.0 dropped the v2.1 `MetadataReport` layer, so one set carries a
    single attribute tree that applies to *all* its targets. That is why
    `targets` is a fan-out here rather than a property of each value.
    """

    __slots__ = ()
    __constraint__ = ("artefact_type", ("MetadataSet",))

    #: The metadataflow or provision agreement this set reports against.
    structure: _Maintainable[R] = edge(("metadata_set", "structure"), "_Maintainable")
    targets: _ManyMetadataTarget[R] = many("targets", _ManyMetadataTarget)


class _ReportedAttribute[R: RowKind](Cols[R, MetadataSection]):
    """One reported value — `sdmx.metadata_value`.

    Has no URN of its own: a reported value is identified by its position in a
    set's attribute tree, which is what `path` and `position` are. So the
    resolving terminals refuse this root, and `ids()` returns the paths.

    `value` is the localised text and `scalar` the plain one; `kind` says which
    a row carries. Reading the wrong one yields `NULL` rather than an error,
    which is why `kind` is exposed rather than inferred.
    """

    __slots__ = ()
    __table__ = "metadata_value"
    __id__ = "attribute_path"
    __key__ = ("set_urn", "attribute_path", "sibling_index")

    #: Named `attribute_path` rather than `path`, which `AnyCols` already uses
    #: for the declared edge route. A column shadowing it makes `__get__` call
    #: itself — `tests/unit/test_query_accessors.py` guards the whole reserved
    #: set, because the failure is an infinite recursion at first use.
    attribute_path: Str[R] = col("metadata_value", "attribute_path")
    #: Which sibling this is, where an attribute repeats. SDMX models
    #: repetition as sibling rows sharing an id, never as a values list.
    position: Num[R] = ncol("metadata_value", "sibling_index")
    kind: Str[R] = col("metadata_value", "value_kind")
    scalar: Str[R] = col("metadata_value", "scalar")
    value: Localised[R] = label("metadata_value", "i18n")
    metadata_set: _MetadataSet[R] = edge("metadata_set", "_MetadataSet")


class _ManyReportedAttribute[P: RowKind](_ReportedAttribute[P], FanOut[P, ReportedAttributeRow]):
    """A metadata set's reported values.

    Declared for completeness of the fan-out surface, but **not exposed on
    `_MetadataSet`**: `reported_attributes` there would need this class,
    which needs
    `_ReportedAttribute`, which needs `_MetadataSet` for its `metadata_set`
    edge. The
    same true cycle `q.component.dsd` has, and resolved the same way — the
    richer direction wins, so a reported attribute can reach its set's targets
    and a set cannot fan out to them. Root at `q.reported_attribute` instead.
    """

    __slots__ = ()


class _Category[R: RowKind](Cols[R, Never]):
    """One `category` row — an item of a category scheme.

    A tree like `_Code` and shaped the same way, down to the baked `depth`. It
    is a *third* relation with `id` / `parent_id` / `depth` meaning something
    of its own, which is the row kind earning its keep again.
    """

    __slots__ = ()
    __table__ = "category"
    __id__ = "category_id"
    __key__ = ("category_urn",)

    urn: Str[R] = col("category", "category_urn")
    name: Localised[R] = label("category", "name")
    description: Localised[R] = label("category", "description")
    id: Str[R] = col("category", "category_id")
    uri: Str[R] = col("category", "uri")
    #: The category's dotted path — its identity in the scheme, as `hcode.nested_id`.
    nested_id: Str[R] = col("category", "path")
    depth: Num[R] = ncol("category", "depth")
    position: Num[R] = ncol("category", "position")
    scheme: _CategoryScheme[R] = edge("scheme", "_CategoryScheme")
    #: Every ancestor, every descendant, and the direct children — one
    #: relationship each, built lazily (`_Late`) once `_ManyCategory` exists.
    ancestors = _Late(lambda: _ManyCategory[R](("ancestors",)))
    descendants = _Late(lambda: _ManyCategory[R](("descendants",)))
    children = _Late(lambda: _ManyCategory[R](("children",)))

    parent = _Late(lambda: _Category[R](("parent",)))


class _ManyCategory[P: RowKind](_Category[P], FanOut[P, CategoryRow]):
    """`scheme.categories`, and a category's own tree relationships."""

    __slots__ = ()


class _Categorisation[R: RowKind](Cols[R, Never]):
    """One `categorisation` row — an artefact placed in a category.

    Rooted on the *extension* rather than the spine, unlike `_Dataflow`, and
    deliberately: a categorisation's own maintainable surface is almost never
    the question. What a caller wants is the pair of ends, and rooting here
    reaches both without crossing the identity split. Nothing hoisted means
    nothing to bind against the wrong table.
    """

    __slots__ = ()
    __table__ = "categorisation"
    __id__ = "urn"
    __key__ = ("urn",)

    urn: Str[R] = col("categorisation", "urn")
    #: What was categorised.
    artefact: _Maintainable[R] = edge("artefact", "_Maintainable")
    #: What it was categorised under.
    category: _Category[R] = edge("category", "_Category")


class _ManyCategorisation[P: RowKind](_Categorisation[P], FanOut[P, CategorisationRow]):
    """`artefact.categorisations`."""

    __slots__ = ()


class _Hierarchy[R: RowKind](_Maintainable[R, "CollectedHierarchy"]):
    """A hierarchy.

    Its nodes are a fan-out like any other child relation. **Recursion is not**
    — `narrower_transitive` and friends stay named domain verbs on the artefact
    rather than becoming path syntax, which is the corollary that keeps every
    path in this language finite and the compiler free of recursive CTEs. See
    `docs/design/query-language.md`, "Hierarchy fan-out".
    """

    __slots__ = ()
    __constraint__ = ("artefact_type", ("Hierarchy",))

    hcodes: _ManyHCode[R] = many("hcodes", _ManyHCode)
    #: Where this hierarchy is applied — built lazily (`_Late`), since
    #: `_ManyHierarchyAssociation` reaches `_Hierarchy` in turn.
    associations = _Late(lambda: _ManyHierarchyAssociation[R](("associations", "spine")))


class _Codelist[R: RowKind](_Maintainable[R, "CollectedCodelist"]):
    """A codelist."""

    __slots__ = ()
    __constraint__ = ("artefact_type", ("Codelist",))

    codes: _ManyCode[R] = many("codes", _ManyCode)


class _CategoryScheme[R: RowKind](_Maintainable[R, CategoryScheme]):
    """A category scheme — the sibling `q.concept_scheme` was renamed for.

    `q.scheme` used to mean ConceptScheme specifically while this had no root
    at all, which is why the general word was occupied by one claimant. Both
    are now named.
    """

    __slots__ = ()
    __constraint__ = ("artefact_type", ("CategoryScheme",))

    categories: _ManyCategory[R] = many("categories", _ManyCategory)


class _ConceptScheme[R: RowKind](_Maintainable[R, ConceptScheme]):
    """A concept scheme."""

    __slots__ = ()
    __constraint__ = ("artefact_type", ("ConceptScheme",))

    concepts: _ManyConcept[R] = many("concepts", _ManyConcept)


# ── the exported namespace ────────────────────────────────────────────────


class _MetadataAttribute[R: RowKind](Cols[R, Never]):
    """One `MetadataAttribute` — a *declared* slot in an MSD's attribute tree.

    The declared half of reference metadata. `_ReportedAttribute` is the
    populated half, and the two names are the spec's own: an MSD declares
    `MetadataAttribute` slots, a `MetadataSet` reports values against them. A
    query can hold both at once, so the names have to say which is which
    without a lookup.

    **No labels of its own.** The relation carries no `name` or `description`
    columns, because SDMX takes an attribute's wording from its concept. Reach
    it through the hop: `metadata_attribute.concept.name["en"]`.

    The fourth tree in this schema after `code`, `category` and
    `hierarchy_node`, and the bake is already there — `parent_path`, `depth`
    and the two ancestor lists are written at persist time, so ancestry is a
    containment test rather than recursion, exactly as for the other three.
    """

    __slots__ = ()
    __table__ = "metadata_attribute"
    __id__ = "attribute_id"
    __key__ = ("attribute_urn",)

    urn: Str[R] = col("metadata_attribute", "attribute_urn")
    id: Str[R] = col("metadata_attribute", "attribute_id")
    uri: Str[R] = col("metadata_attribute", "uri")
    #: The attribute's dotted path — its identity in the MSD, as `hcode.nested_id`.
    nested_id: Str[R] = col("metadata_attribute", "path")
    position: Num[R] = ncol("metadata_attribute", "position")
    depth: Num[R] = ncol("metadata_attribute", "depth")
    #: Occurrence bounds as declared by the MSD. `max_occurs` is NULL for
    #: unbounded, which is why `is_not_null()` is the way to ask "repeatable".
    min_occurs: Num[R] = ncol("metadata_attribute", "min_occurs")
    max_occurs: Num[R] = ncol("metadata_attribute", "max_occurs")
    is_presentational: Bool[R] = bcol("metadata_attribute", "is_presentational")
    msd: _Msd[R] = edge("msd", "_Msd")
    #: Where this slot's wording lives.
    concept: _Concept[R] = edge("concept", "_Concept")
    #: The slot's own enumeration, when it has one.
    codelist: _Codelist[R] = edge("codelist", "_Codelist")
    #: The other arm of the same representation — see `_Facet`.
    facet: _AttributeFacet[R] = _AttributeFacet(())
    #: Every ancestor, every descendant, and the direct children — one
    #: relationship each, built lazily (`_Late`) once the fan-out class exists.
    ancestors = _Late(lambda: _ManyMetadataAttribute[R](("ancestors",)))
    descendants = _Late(lambda: _ManyMetadataAttribute[R](("descendants",)))
    children = _Late(lambda: _ManyMetadataAttribute[R](("children",)))

    parent = _Late(lambda: _MetadataAttribute[R](("parent",)))


class _ManyMetadataAttribute[P: RowKind](_MetadataAttribute[P], FanOut[P, MetadataAttributeRow]):
    """`msd.metadata_attributes`, and a slot's own tree relationships.

    Named `metadata_attributes` rather than `attributes` because
    `artefact.attributes` is already the DSD facet — one accessor cannot carry
    two meanings for one name, and `test_query_edges.py` refuses it.
    """

    __slots__ = ()


class _Msd[R: RowKind](_Maintainable[R, MetadataStructure]):
    """A metadata structure definition — the shape of a reference-metadata payload."""

    __slots__ = ()
    __constraint__ = ("artefact_type", ("MetadataStructure",))

    metadata_attributes: _ManyMetadataAttribute[R] = many("metadata_attributes", _ManyMetadataAttribute)
    #: Sets structured by this MSD. Bound late — `_ManyMetadataSet` is a
    #: maintainable and cannot be named here.
    metadata_sets = _Late(lambda: _ManyMetadataSet[R](("metadata_sets", "spine")))


class _Metadataflow[R: RowKind](_Maintainable[R, Metadataflow]):
    """A published reference-metadata product, backed by an MSD.

    The metadata-side mirror of `_Dataflow`, and it carries the same compound
    hop for the same reason: `msd_urn` sits on the `metadataflow` extension, so
    `msd` is `("metadataflow", "msd")` — out across the identity split, then
    along the FK.
    """

    __slots__ = ()
    __constraint__ = ("artefact_type", ("Metadataflow",))

    msd: _Msd[R] = edge(("metadataflow", "msd"), "_Msd")
    #: Sets reported against this flow, and the agreements permitting them.
    metadata_sets = _Late(lambda: _ManyMetadataSet[R](("metadata_sets", "spine")))
    metadata_provision_agreements = _Late(
        lambda: _ManyMetadataProvisionAgreement[R](("metadata_provision_agreements", "spine"))
    )


class _Contact[R: RowKind](Cols[R, Never]):
    """One `Contact` of an agency — who to ask about its artefacts.

    The multi-valued fields are stored **list** columns, so they are asked with
    `has()` rather than `==`: `q.contact.emails.has("stats@example.org")`. That
    is the same operator `ancestors` uses, and the same reason — a containment
    test against a column, not a scalar comparison.

    `id` is a storage surrogate (a sequence), not an SDMX identifier: a contact
    has no id in the information model. It is the key because nothing else
    distinguishes two contacts of one agency, and it makes `ids()` on this root
    close to meaningless — `agency.contacts` is the way in.

    **Either organisation's contacts are here.** `Agency` and `DataProvider`
    are both `Organisation` in the IM and carry the identical `contacts` field,
    so one relation serves both and `owner_urn` says which. Reach them through
    the owner — `agency.contacts` or `data_provider.contacts` — since the row
    itself does not say whose it is.

    A provider's contacts were silently dropped on write until #309: there was
    no table to put them in, so the field existed in the model, in every format
    reader and writer, and nowhere else.
    """

    __slots__ = ()
    __table__ = "organisation_contact"
    __id__ = "id"
    __key__ = ("id",)

    #: Position within its owning organisation, as declared.
    position: Num[R] = ncol("organisation_contact", "ordinal")
    name: Localised[R] = label("organisation_contact", "contact_name")
    department: Localised[R] = label("organisation_contact", "department")
    role: Localised[R] = label("organisation_contact", "role")
    emails: Arr[R] = acol("organisation_contact", "emails")
    phones: Arr[R] = acol("organisation_contact", "phones")
    faxes: Arr[R] = acol("organisation_contact", "faxes")
    uris: Arr[R] = acol("organisation_contact", "uris")
    x400s: Arr[R] = acol("organisation_contact", "x400s")
    #: Built lazily (`_Late`) once the owner classes exist, which need
    #: `_ManyContact` in turn — the same cycle as
    #: `dataflow.provision_agreements`.
    agency = _Late(lambda: _Agency[R](("agency",)))
    data_provider = _Late(lambda: _DataProvider[R](("data_provider",)))
    data_consumer = _Late(lambda: _DataConsumer[R](("data_consumer",)))
    organisation_unit = _Late(lambda: _OrganisationUnit[R](("organisation_unit",)))


class _ManyContact[P: RowKind](_Contact[P], FanOut[P, ContactRow]):
    """`agency.contacts` and `data_provider.contacts`."""

    __slots__ = ()


class _Agency[R: RowKind](Cols[R, Never]):
    """One `Agency` — an item of an `AgencyScheme`.

    Not to be confused with `artefact.agency`, the *maintainer id* every
    artefact carries. That is a bare string with no row to reach (see
    `NOT_EDGES` in `tests/unit/test_query_edges.py`); this is the organisation
    the scheme actually declares, with a name, a description and contacts.

    Not a tree: ``AgencyType`` restricts ``Parent`` away in every SDMX format,
    so an agency scheme is flat and the model has no `Agency.parent` (#419).
    """

    __slots__ = ()
    __table__ = "agency"
    __id__ = "agency_id"
    __key__ = ("agency_urn",)

    urn: Str[R] = col("agency", "agency_urn")
    id: Str[R] = col("agency", "agency_id")
    uri: Str[R] = col("agency", "uri")
    name: Localised[R] = label("agency", "name")
    description: Localised[R] = label("agency", "description")
    scheme: _AgencyScheme[R] = edge("scheme", "_AgencyScheme")

    contacts: _ManyContact[R] = many("contacts", _ManyContact)


class _ManyAgency[P: RowKind](_Agency[P], FanOut[P, AgencyRow]):
    """`agency_scheme.agencies`."""

    __slots__ = ()


class _DataProvider[R: RowKind](Cols[R, Never]):
    """One `DataProvider` — an item of a `DataProviderScheme`.

    The far end of `provision_agreement.data_provider`, and the reason that
    edge had to be corrected: a provider's urn lives here, never in
    `sdmx.artefact`.
    """

    __slots__ = ()
    __table__ = "data_provider"
    __id__ = "provider_id"
    __key__ = ("provider_urn",)

    urn: Str[R] = col("data_provider", "provider_urn")
    id: Str[R] = col("data_provider", "provider_id")
    uri: Str[R] = col("data_provider", "uri")
    name: Localised[R] = label("data_provider", "name")
    description: Localised[R] = label("data_provider", "description")
    scheme: _DataProviderScheme[R] = edge("scheme", "_DataProviderScheme")
    #: Built lazily (`_Late`) once `_ManyContact` exists — the same cycle the
    #: agency side has.
    contacts = _Late(lambda: _ManyContact[R](("contacts",)))


class _ManyDataProvider[P: RowKind](_DataProvider[P], FanOut[P, DataProviderRow]):
    """`data_provider_scheme.data_providers`."""

    __slots__ = ()


class _MetadataProvider[R: RowKind](Cols[R, Never]):
    """One `MetadataProvider` — an item of a `MetadataProviderScheme` (#315).

    The far end of `metadata_provision_agreement.metadata_provider`, which
    until this existed pointed at a relation that was not there.
    """

    __slots__ = ()
    __table__ = "metadata_provider"
    __id__ = "provider_id"
    __key__ = ("provider_urn",)

    urn: Str[R] = col("metadata_provider", "provider_urn")
    id: Str[R] = col("metadata_provider", "provider_id")
    uri: Str[R] = col("metadata_provider", "uri")
    name: Localised[R] = label("metadata_provider", "name")
    description: Localised[R] = label("metadata_provider", "description")
    scheme: _MetadataProviderScheme[R] = edge("scheme", "_MetadataProviderScheme")


class _ManyMetadataProvider[P: RowKind](_MetadataProvider[P], FanOut[P, MetadataProviderRow]):
    """`metadata_provider_scheme.metadata_providers`."""

    __slots__ = ()


class _DataConsumer[R: RowKind](Cols[R, Never]):
    """One `DataConsumer` — an item of a `DataConsumerScheme` (#469)."""

    __slots__ = ()
    __table__ = "data_consumer"
    __id__ = "consumer_id"
    __key__ = ("consumer_urn",)

    urn: Str[R] = col("data_consumer", "consumer_urn")
    id: Str[R] = col("data_consumer", "consumer_id")
    uri: Str[R] = col("data_consumer", "uri")
    name: Localised[R] = label("data_consumer", "name")
    description: Localised[R] = label("data_consumer", "description")
    scheme: _DataConsumerScheme[R] = edge("scheme", "_DataConsumerScheme")
    contacts = _Late(lambda: _ManyContact[R](("contacts",)))


class _ManyDataConsumer[P: RowKind](_DataConsumer[P], FanOut[P, DataConsumerRow]):
    """`data_consumer_scheme.data_consumers`."""

    __slots__ = ()


class _OrganisationUnit[R: RowKind](Cols[R, Never]):
    """One `OrganisationUnit` — an item of an `OrganisationUnitScheme`, and a tree within it (#469).

    `parent` and `children` walk `parent_id` within the scheme, as a code's do
    within its codelist. No `ancestors`: a code's are baked at write time
    (`ancestor_ids`), and nothing asks this tree for them yet.
    """

    __slots__ = ()
    __table__ = "organisation_unit"
    __id__ = "unit_id"
    __key__ = ("unit_urn",)

    urn: Str[R] = col("organisation_unit", "unit_urn")
    id: Str[R] = col("organisation_unit", "unit_id")
    uri: Str[R] = col("organisation_unit", "uri")
    name: Localised[R] = label("organisation_unit", "name")
    description: Localised[R] = label("organisation_unit", "description")
    scheme: _OrganisationUnitScheme[R] = edge("scheme", "_OrganisationUnitScheme")
    contacts = _Late(lambda: _ManyContact[R](("contacts",)))
    parent = _Late(lambda: _OrganisationUnit[R](("parent",)))
    children = _Late(lambda: _ManyOrganisationUnit[R](("children",)))


class _ManyOrganisationUnit[P: RowKind](_OrganisationUnit[P], FanOut[P, OrganisationUnitRow]):
    """`organisation_unit_scheme.organisation_units`."""

    __slots__ = ()


class _AgencyScheme[R: RowKind](_Maintainable[R, AgencyScheme]):
    """An agency scheme."""

    __slots__ = ()
    __constraint__ = ("artefact_type", ("AgencyScheme",))

    agencies: _ManyAgency[R] = many("agencies", _ManyAgency)


class _DataProviderScheme[R: RowKind](_Maintainable[R, DataProviderScheme]):
    """A data-provider scheme."""

    __slots__ = ()
    __constraint__ = ("artefact_type", ("DataProviderScheme",))

    data_providers: _ManyDataProvider[R] = many("data_providers", _ManyDataProvider)


class _MetadataProviderScheme[R: RowKind](_Maintainable[R, MetadataProviderScheme]):
    """A metadata-provider scheme (#315)."""

    __slots__ = ()
    __constraint__ = ("artefact_type", ("MetadataProviderScheme",))

    metadata_providers: _ManyMetadataProvider[R] = many("metadata_providers", _ManyMetadataProvider)


class _DataConsumerScheme[R: RowKind](_Maintainable[R, DataConsumerScheme]):
    """A data-consumer scheme (#469)."""

    __slots__ = ()
    __constraint__ = ("artefact_type", ("DataConsumerScheme",))

    data_consumers: _ManyDataConsumer[R] = many("data_consumers", _ManyDataConsumer)


class _OrganisationUnitScheme[R: RowKind](_Maintainable[R, OrganisationUnitScheme]):
    """An organisation-unit scheme (#469)."""

    __slots__ = ()
    __constraint__ = ("artefact_type", ("OrganisationUnitScheme",))

    organisation_units: _ManyOrganisationUnit[R] = many("organisation_units", _ManyOrganisationUnit)


class _ProvisionAgreement[R: RowKind](_Maintainable[R, ProvisionAgreement]):
    """A `ProvisionAgreement` — which provider supplies which dataflow.

    Two outgoing edges, and they land on relations of different *kinds*:
    `dataflow` reaches a maintainable on the spine, `data_provider` reaches an
    item of a scheme. Both foreign keys sit on the extension table, so both are
    compound hops out across the identity split.
    """

    __slots__ = ()
    __constraint__ = ("artefact_type", ("ProvisionAgreement",))

    dataflow: _Dataflow[R] = edge(("provision_agreement", "dataflow"), "_Dataflow")
    data_provider: _DataProvider[R] = edge(("provision_agreement", "data_provider"), "_DataProvider")


class _ManyProvisionAgreement[P: RowKind](_ProvisionAgreement[P], FanOut[P, ProvisionAgreementRow]):
    """`dataflow.provision_agreements` — who is cleared to supply this flow."""

    __slots__ = ()


# `("provision_agreements", "spine")` for the reason `_Dsd.dataflows` is
# `("dataflows", "spine")`: the inverse FK lands on the *extension* table, and
# the accessor's own columns and hops are measured from the spine, so the hop
# back out is left over.


class _RepresentationMapping[R: RowKind](Cols[R, Never]):
    """One `RepresentationMapping` — source values to target values, N:M (#422).

    `sources` and `targets` are stored lists, one value per endpoint of the
    map, so a test on one value is `.has()`:
    `q.representation_map.mappings.sources.has("A")`. A source value's regex
    and substring flags are held but not queryable here; read the artefact for
    them.
    """

    __slots__ = ()
    __table__ = "representation_mapping"
    __id__ = "ordinal"
    __key__ = ("representation_map_urn", "ordinal")

    sources: Arr[R] = acol("representation_mapping", "sources")
    targets: Arr[R] = acol("representation_mapping", "targets")
    position: Num[R] = ncol("representation_mapping", "ordinal")
    representation_map: _RepresentationMap[R] = edge(("representation_map", "spine"), "_RepresentationMap")


class _ManyRepresentationMapping[P: RowKind](_RepresentationMapping[P], FanOut[P, RepresentationMappingRow]):
    """`representation_map.mappings`."""

    __slots__ = ()


class _MapSource[R: RowKind](Cols[R, Never]):
    """One source of a `RepresentationMap`: a codelist (or value list), or a data type (#422).

    `codelist` is null for a data-type endpoint, and `data_type` for a
    codelist one.
    """

    __slots__ = ()
    __table__ = "representation_map_source"
    __id__ = "ordinal"
    __key__ = ("representation_map_urn", "ordinal")

    data_type: Str[R] = col("representation_map_source", "data_type")
    position: Num[R] = ncol("representation_map_source", "ordinal")
    codelist: _Codelist[R] = edge("codelist", "_Codelist")
    representation_map: _RepresentationMap[R] = edge(("representation_map", "spine"), "_RepresentationMap")


class _ManyMapSource[P: RowKind](_MapSource[P], FanOut[P, MapEndpointRow]):
    """`representation_map.sources`."""

    __slots__ = ()


class _MapTarget[R: RowKind](Cols[R, Never]):
    """One target of a `RepresentationMap` — `_MapSource`'s twin (#422)."""

    __slots__ = ()
    __table__ = "representation_map_target"
    __id__ = "ordinal"
    __key__ = ("representation_map_urn", "ordinal")

    data_type: Str[R] = col("representation_map_target", "data_type")
    position: Num[R] = ncol("representation_map_target", "ordinal")
    codelist: _Codelist[R] = edge("codelist", "_Codelist")
    representation_map: _RepresentationMap[R] = edge(("representation_map", "spine"), "_RepresentationMap")


class _ManyMapTarget[P: RowKind](_MapTarget[P], FanOut[P, MapEndpointRow]):
    """`representation_map.targets`."""

    __slots__ = ()


class _RepresentationMap[R: RowKind](_Maintainable[R, RepresentationMap]):
    """A `RepresentationMap` — how values of some representations translate to others.

    N:M (#422): several endpoints a side, reached as fan-outs, and mappings
    holding one value per endpoint. The endpoints are children of the
    extension row rather than the spine — see `_edges.py` — hence the
    two-step paths.
    """

    __slots__ = ()
    __constraint__ = ("artefact_type", ("RepresentationMap",))

    sources: _ManyMapSource[R] = many(("representation_map", "sources"), _ManyMapSource)
    targets: _ManyMapTarget[R] = many(("representation_map", "targets"), _ManyMapTarget)
    mappings: _ManyRepresentationMapping[R] = many(("representation_map", "mappings"), _ManyRepresentationMapping)


class _ItemMap[R: RowKind](Cols[R, Never]):
    """One `ItemMap` — a source-item id and a target-item id.

    Ids, not urns: an `ItemMap` names items *within* the schemes its parent
    map points at, which is all SDMX carries. Reaching the items themselves
    goes through the endpoint — `q.concept_scheme_map.source` — rather than
    from here.

    One table serves every scheme-map kind. The hop back is `scheme_map` and
    lands on the shared maintainable rather than on any one class: all live in
    `artefact`, so an edge each would all be `artefact.item_maps` and the
    reverse hop would be ambiguous. Rooting at `q.concept_scheme_map`,
    `q.category_scheme_map` or `q.organisation_scheme_map` is what picks a
    kind (#395).
    """

    __slots__ = ()
    __table__ = "item_map"
    __id__ = "source_id"
    __key__ = ("map_urn", "ordinal")

    source: Str[R] = col("item_map", "source_id")
    target: Str[R] = col("item_map", "target_id")
    position: Num[R] = ncol("item_map", "ordinal")
    scheme_map: _Maintainable[R] = edge("scheme_map", "_Maintainable")


class _ManyItemMap[P: RowKind](_ItemMap[P], FanOut[P, ItemMapRow]):
    """`item_maps` of a concept, category or organisation scheme map."""

    __slots__ = ()


class _ComponentMap[R: RowKind](Cols[R, Never]):
    """One `ComponentMap` — source-component ids to target-component ids.

    Separate from `_ItemMap` because `ComponentMap` is a separate class in the
    information model: it maps *components* of a structure, not items of a
    scheme. SDMX 3.x makes it N:M (#422), so `sources` and `targets` are
    stored lists: `q.structure_map.component_maps.sources.has("FREQ")`.
    """

    __slots__ = ()
    __table__ = "component_map"
    __id__ = "ordinal"
    __key__ = ("structure_map_urn", "ordinal")

    sources: Arr[R] = acol("component_map", "sources")
    targets: Arr[R] = acol("component_map", "targets")
    position: Num[R] = ncol("component_map", "ordinal")
    structure_map: _StructureMap[R] = edge("structure_map", "_StructureMap")


class _ManyComponentMap[P: RowKind](_ComponentMap[P], FanOut[P, ComponentMapRow]):
    """`structure_map.component_maps`."""

    __slots__ = ()


class _ConceptSchemeMap[R: RowKind](_Maintainable[R, ConceptSchemeMap]):
    """A `ConceptSchemeMap` — how one concept scheme's concepts map to another's.

    `source` / `target` rather than role-plus-class: unlike
    `_RepresentationMap`, whose two endpoints are both Codelist and needed
    telling apart, these name one class each.
    """

    __slots__ = ()
    __constraint__ = ("artefact_type", ("ConceptSchemeMap",))

    source: _ConceptScheme[R] = edge(("concept_scheme_map", "source"), "_ConceptScheme")
    target: _ConceptScheme[R] = edge(("concept_scheme_map", "target"), "_ConceptScheme")
    item_maps: _ManyItemMap[R] = many("item_maps", _ManyItemMap)


class _CategorySchemeMap[R: RowKind](_Maintainable[R, CategorySchemeMap]):
    """A `CategorySchemeMap` — how one category scheme's categories map to another's."""

    __slots__ = ()
    __constraint__ = ("artefact_type", ("CategorySchemeMap",))

    source: _CategoryScheme[R] = edge(("category_scheme_map", "source"), "_CategoryScheme")
    target: _CategoryScheme[R] = edge(("category_scheme_map", "target"), "_CategoryScheme")
    item_maps: _ManyItemMap[R] = many("item_maps", _ManyItemMap)


class _OrganisationSchemeMap[R: RowKind](_Maintainable[R, OrganisationSchemeMap]):
    """An `OrganisationSchemeMap` — how one organisation scheme's organisations map to another's.

    Its ends are any organisation scheme — agencies, data or metadata
    providers, or a data consumer or organisation unit scheme sdmxlib does
    not model — so `source` / `target` reach the maintainable, not one scheme
    class (#448): `q.organisation_scheme_map.source.id == "AGENCIES"`.
    """

    __slots__ = ()
    __constraint__ = ("artefact_type", ("OrganisationSchemeMap",))

    source: _Maintainable[R] = edge(("organisation_scheme_map", "source"), "_Maintainable")
    target: _Maintainable[R] = edge(("organisation_scheme_map", "target"), "_Maintainable")
    item_maps: _ManyItemMap[R] = many("item_maps", _ManyItemMap)


class _StructureMap[R: RowKind](_Maintainable[R, StructureMap]):
    """A `StructureMap` — how one structure's components map to another's.

    The 3.0 successor to 2.1's `DataStructureMap` and `DataflowMap`, which is
    why its endpoints are `_Dsd` rather than either.
    """

    __slots__ = ()
    __constraint__ = ("artefact_type", ("StructureMap",))

    source: _Dsd[R] = edge(("structure_map", "source"), "_Dsd")
    target: _Dsd[R] = edge(("structure_map", "target"), "_Dsd")
    component_maps: _ManyComponentMap[R] = many("component_maps", _ManyComponentMap)


class _MetadataProvisionAgreement[R: RowKind](_Maintainable[R, MetadataProvisionAgreement]):
    """A `MetadataProvisionAgreement` — the metadata mirror of its data twin.

    **Half of it is unreachable.** `metadata_provider_urn` names a
    `MetadataProvider`, and nothing persists one: SDMX 3.0 has the class and a
    `MetadataProviderScheme` to hold it, and this schema has neither a table
    nor a writer. So `metadataflow` is a hop and the provider is not — an
    absence recorded in `NOT_EDGES` rather than an edge pointing at a table
    that is not there.
    """

    __slots__ = ()
    __constraint__ = ("artefact_type", ("MetadataProvisionAgreement",))

    metadataflow: _Metadataflow[R] = edge(("metadata_provision_agreement", "metadataflow"), "_Metadataflow")
    metadata_provider: _MetadataProvider[R] = edge(
        ("metadata_provision_agreement", "metadata_provider"), "_MetadataProvider"
    )


class _ManyMetadataProvisionAgreement[P: RowKind](
    _MetadataProvisionAgreement[P], FanOut[P, MetadataProvisionAgreementRow]
):
    """`metadataflow.metadata_provision_agreements`."""

    __slots__ = ()


class _ManyMetadataSet[P: RowKind](_MetadataSet[P], FanOut[P, MetadataSetRow]):
    """`msd.metadata_sets` and `metadataflow.metadata_sets`.

    A set names *one* structure, which may be either, so the same fan-out hangs
    off both roots and only one of them answers for any given set.
    """

    __slots__ = ()


class _KeyValue[R: RowKind](Cols[R, Never]):
    """One `KeyValue` row — a component and one value it may take.

    Storage flattens SDMX's `KeyValue(dimension_id, values[])` to one row per
    value, so a component with three permitted codes is three rows here. That
    is what makes `region.key_values.value == "A"` a plain predicate instead of
    a containment test, and it is the shape the availability builders already
    read.

    `component` is a bare id, not an edge: a region names a component of
    whatever the constraint is attached to, and the constraint may attach to a
    dataflow, a DSD, a provision agreement or a provider — so which
    `dsd_component` row it means is not on this row to say.
    """

    __slots__ = ()
    __table__ = "region_key_value"
    __id__ = "value"
    __key__ = ("constraint_urn", "region_idx", "component_id", "value")

    component: Str[R] = col("region_key_value", "component_id")
    value: Str[R] = col("region_key_value", "value")
    #: False when the value set excludes this value (#422). Read with the
    #: region's own `is_included`: an included region can still exclude
    #: some of one component's values.
    is_included: Bool[R] = bcol("region_key_value", "is_included")
    #: Whether the code's descendants are selected too: `"true"`,
    #: `"excluderoot"`, `"false"`, or null for the default — the code alone
    #: (#422). A query for the codes a region names has to read it.
    cascade: Str[R] = col("region_key_value", "cascade")
    region = _Late(lambda: _CubeRegion[R](("region",)))


class _ManyKeyValue[P: RowKind](_KeyValue[P], FanOut[P, KeyValueRow]):
    """`cube_region.key_values`."""

    __slots__ = ()


class _CubeRegion[R: RowKind](Cols[R, Never]):
    """One `CubeRegion` — an included or excluded slice of the cube.

    `is_included` is the whole of the semantics: the same region shape means
    "only these" or "not these" depending on it, so a query that ignores it
    inverts its own answer. There is no natural id, so the region is keyed
    `(constraint_urn, region_idx)` and `ids()` returns the index.
    """

    __slots__ = ()
    __table__ = "cube_region"
    __id__ = "region_idx"
    __key__ = ("constraint_urn", "region_idx")

    index: Num[R] = ncol("cube_region", "region_idx")
    is_included: Bool[R] = bcol("cube_region", "is_included")
    constraint: _DataConstraint[R] = edge("constraint", "_DataConstraint")
    key_values: _ManyKeyValue[R] = many("key_values", _ManyKeyValue)


class _ManyCubeRegion[P: RowKind](_CubeRegion[P], FanOut[P, CubeRegionRow]):
    """`data_constraint.cube_regions`."""

    __slots__ = ()


class _ManyAttachedDataflow[P: RowKind](_Dataflow[P], FanOut[P, DataflowRow]):
    """`data_constraint.dataflows` — the flows a constraint is attached to (#422)."""

    __slots__ = ()


class _ManyAttachedDsd[P: RowKind](_Dsd[P], FanOut[P, DsdRow]):
    """`data_constraint.dsds` — the data structures a constraint is attached to (#422)."""

    __slots__ = ()


class _DataConstraint[R: RowKind](_Maintainable[R, DataConstraint]):
    """A `DataConstraint` — what a dataflow, DSD or provider may contain.

    What it is attached to: a provider, a to-one edge, or several data
    structures, dataflows or provision agreements, each a fan-out through a
    table of its own (#422). The provider is a column on the extension table;
    modelled only as role-filtered `artefact_ref` rows it would have needed an
    edge mechanism this language does not have, and whose cardinality could
    not be derived. See #308 and the schema comment on `data_constraint`.
    """

    __slots__ = ()
    __constraint__ = ("artefact_type", ("DataConstraint",))

    #: `Actual` or `Allowed` — whether this describes what *is* present or what
    #: *may* be. Availability queries want `Actual`.
    constraint_type: Str[R] = extcol("data_constraint", "constraint_type")
    dataflows: _ManyAttachedDataflow[R] = many(("data_constraint", "dataflows", "dataflow"), _ManyAttachedDataflow)
    dsds: _ManyAttachedDsd[R] = many(("data_constraint", "dsds", "dsd"), _ManyAttachedDsd)
    provision_agreements: _ManyProvisionAgreement[R] = many(
        ("data_constraint", "provision_agreements", "provision_agreement"), _ManyProvisionAgreement
    )
    data_provider: _DataProvider[R] = edge(("data_constraint", "data_provider"), "_DataProvider")
    cube_regions: _ManyCubeRegion[R] = many("cube_regions", _ManyCubeRegion)


class _ManyDataConstraint[P: RowKind](_DataConstraint[P], FanOut[P, DataConstraintRow]):
    """`dataflow.constraints` — what this flow is declared to contain."""

    __slots__ = ()


# `constraints` lands on a `data_constraint_dataflow` row, `constraint` on the
# extension table, and `spine` crosses back out, for the reason `_Dsd.dataflows`
# carries `spine` (#422).


class _HierarchyAssociation[R: RowKind](_Maintainable[R, HierarchyAssociation]):
    """A `HierarchyAssociation` — this hierarchy organises that object, here.

    Three slots and all three matter: the hierarchy, the object it is applied
    to, and the context that application holds in.
    """

    __slots__ = ()
    __constraint__ = ("artefact_type", ("HierarchyAssociation",))

    hierarchy: _Hierarchy[R] = edge(("hierarchy_association", "hierarchy"), "_Hierarchy")
    #: The linked object *as a maintainable*. Reads its URN, and nothing else:
    #: an association usually links a Dimension, whose item URN is not in
    #: `sdmx.artefact`, so every other column comes back NULL. Reach for
    #: `component` instead when the link is to a DSD component (#372).
    linked_object: _Maintainable[R] = edge(("hierarchy_association", "linked_object"), "_Maintainable")
    #: The DSD component this association binds a hierarchy to — the ordinary
    #: case, and the one `linked_object` cannot answer.
    component: _Component[R] = edge(("hierarchy_association", "component"), "_Component")
    context_object: _Maintainable[R] = edge(("hierarchy_association", "context_object"), "_Maintainable")


class _ManyHierarchyAssociation[P: RowKind](_HierarchyAssociation[P], FanOut[P, HierarchyAssociationRow]):
    """`hierarchy.associations` — where this hierarchy is applied."""

    __slots__ = ()


# `("hierarchy_associations", "spine")` for the same reason
# `_Dataflow.provision_agreements` is: the inverse FK lands on the
# `hierarchy_association` extension table, and a HierarchyAssociation accessor
# is measured from the spine, so the hop back out is left over.


class ApplicabilityRow(RowKind):
    """One (dataflow, component, hierarchy) applicability, ranked."""

    __slots__ = ()


class _Applicability[R: RowKind](Cols[R, Never]):
    """Which hierarchy applies to a component, in one dataflow's context.

    Derived, not stored: `sdmx.hierarchy_applicability` is a view over
    dataflow x dsd_component x hierarchy_association, and the scoped-beats-
    unscoped rule lives in its DDL. `rank == 1` is the winner; the losers are
    present too, so a component with two equally applicable hierarchies can be
    *seen* rather than silently resolved — which is the disagreement #372
    found between this library and a downstream one.

    Over REST the root fetches its three source relations rather than one
    endpoint (`__derived_from__`), so it needs an agency the way a
    maintainable root does.
    """

    __slots__ = ()
    __table__ = "hierarchy_applicability"
    __id__ = "component_id"
    __key__ = ("dataflow_urn", "dsd_urn", "component_id", "association_urn")
    #: The REST resources a scan of this relation must fetch. One endpoint
    #: cannot answer it, and a root that names none is refused outright.
    __derived_from__ = ("hierarchyassociation", "dataflow", "datastructure")

    id: Str[R] = col("hierarchy_applicability", "component_id")
    agency: Str[R] = col("hierarchy_applicability", "agency_id")
    rank: Num[R] = ncol("hierarchy_applicability", "rank")
    #: Whether the association naming this applicability was scoped to this
    #: dataflow. `False` means it is the default for any flow.
    scoped: Bool[R] = bcol("hierarchy_applicability", "scoped")
    dataflow: _Dataflow[R] = edge("dataflow", "_Dataflow")
    hierarchy: _Hierarchy[R] = edge("hierarchy", "_Hierarchy")
    component: _Component[R] = edge("component", "_Component")
    association: _HierarchyAssociation[R] = edge("association", "_HierarchyAssociation")


class _Q:
    """`sl.q` — one singleton per queryable relation.

    Attribute access here is the only entry to the query language, which is
    what makes the `sl.q.` prefix a reliable signal at a call site.
    """

    __slots__ = ()

    artefact: _Maintainable[ArtefactRow] = _Maintainable[ArtefactRow]()
    dataflow: _Dataflow[DataflowRow] = _Dataflow[DataflowRow]()
    dsd: _Dsd[DsdRow] = _Dsd[DsdRow]()
    codelist: _Codelist[CodelistRow] = _Codelist[CodelistRow]()
    concept_scheme: _ConceptScheme[ConceptSchemeRow] = _ConceptScheme[ConceptSchemeRow]()
    category_scheme: _CategoryScheme[ArtefactRow] = _CategoryScheme[ArtefactRow]()
    component: _Component[ComponentRow] = _Component[ComponentRow]()
    code: _Code[CodeRow] = _Code[CodeRow]()
    concept: _Concept[ConceptRow] = _Concept[ConceptRow]()
    hierarchy: _Hierarchy[HierarchyRow] = _Hierarchy[HierarchyRow]()
    hcode: _HCode[HCodeRow] = _HCode[HCodeRow]()
    category: _Category[CategoryRow] = _Category[CategoryRow]()
    categorisation: _Categorisation[CategorisationRow] = _Categorisation[CategorisationRow]()
    msd: _Msd[MsdRow] = _Msd[MsdRow]()
    metadataflow: _Metadataflow[MetadataflowRow] = _Metadataflow[MetadataflowRow]()
    metadata_provision_agreement: _MetadataProvisionAgreement[MetadataProvisionAgreementRow] = (
        _MetadataProvisionAgreement[MetadataProvisionAgreementRow]()
    )
    metadata_attribute: _MetadataAttribute[MetadataAttributeRow] = _MetadataAttribute[MetadataAttributeRow]()
    group: _Group[GroupRow] = _Group[GroupRow]()
    representation_map: _RepresentationMap[RepresentationMapRow] = _RepresentationMap[RepresentationMapRow]()
    data_constraint: _DataConstraint[DataConstraintRow] = _DataConstraint[DataConstraintRow]()
    cube_region: _CubeRegion[CubeRegionRow] = _CubeRegion[CubeRegionRow]()
    key_value: _KeyValue[KeyValueRow] = _KeyValue[KeyValueRow]()
    hierarchy_association: _HierarchyAssociation[HierarchyAssociationRow] = _HierarchyAssociation[
        HierarchyAssociationRow
    ]()
    representation_mapping: _RepresentationMapping[RepresentationMappingRow] = _RepresentationMapping[
        RepresentationMappingRow
    ]()
    concept_scheme_map: _ConceptSchemeMap[ConceptSchemeMapRow] = _ConceptSchemeMap[ConceptSchemeMapRow]()
    category_scheme_map: _CategorySchemeMap[CategorySchemeMapRow] = _CategorySchemeMap[CategorySchemeMapRow]()
    organisation_scheme_map: _OrganisationSchemeMap[OrganisationSchemeMapRow] = _OrganisationSchemeMap[
        OrganisationSchemeMapRow
    ]()
    structure_map: _StructureMap[StructureMapRow] = _StructureMap[StructureMapRow]()
    item_map: _ItemMap[ItemMapRow] = _ItemMap[ItemMapRow]()
    component_map: _ComponentMap[ComponentMapRow] = _ComponentMap[ComponentMapRow]()
    agency_scheme: _AgencyScheme[ArtefactRow] = _AgencyScheme[ArtefactRow]()
    agency: _Agency[AgencyRow] = _Agency[AgencyRow]()
    contact: _Contact[ContactRow] = _Contact[ContactRow]()
    data_provider_scheme: _DataProviderScheme[ArtefactRow] = _DataProviderScheme[ArtefactRow]()
    data_provider: _DataProvider[DataProviderRow] = _DataProvider[DataProviderRow]()
    metadata_provider_scheme: _MetadataProviderScheme[ArtefactRow] = _MetadataProviderScheme[ArtefactRow]()
    metadata_provider: _MetadataProvider[MetadataProviderRow] = _MetadataProvider[MetadataProviderRow]()
    data_consumer_scheme: _DataConsumerScheme[ArtefactRow] = _DataConsumerScheme[ArtefactRow]()
    data_consumer: _DataConsumer[DataConsumerRow] = _DataConsumer[DataConsumerRow]()
    organisation_unit_scheme: _OrganisationUnitScheme[ArtefactRow] = _OrganisationUnitScheme[ArtefactRow]()
    organisation_unit: _OrganisationUnit[OrganisationUnitRow] = _OrganisationUnit[OrganisationUnitRow]()
    provision_agreement: _ProvisionAgreement[ProvisionAgreementRow] = _ProvisionAgreement[ProvisionAgreementRow]()
    metadata_set: _MetadataSet[MetadataSetRow] = _MetadataSet[MetadataSetRow]()
    reported_attribute: _ReportedAttribute[ReportedAttributeRow] = _ReportedAttribute[ReportedAttributeRow]()
    metadata_target: _MetadataTarget[MetadataTargetRow] = _MetadataTarget[MetadataTargetRow]()
    annotation: _Annotation[AnnotationRow] = _Annotation[AnnotationRow]()
    annotation_url: _AnnotationURL[AnnotationURLRow] = _AnnotationURL[AnnotationURLRow]()
    hierarchy_applicability: _Applicability[ApplicabilityRow] = _Applicability[ApplicabilityRow]()


q = _Q()
"""The query accessor namespace. Exported as ``sl.q``."""
