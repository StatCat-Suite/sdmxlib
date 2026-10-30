"""SDMX-JSON 2.0 structure wire Structs (msgspec) + attrs→Struct converters.

Part of the msgspec migration (milestone: SDMX-JSON dict+orjson → msgspec).
Builds on the shared `_dto` conventions. This module is filled in family by
family; increment 1 covers the item schemes — Codelist, ConceptScheme,
CategoryScheme — plus the shared maintainable base and representation Structs
that later families (DSD components) reuse.
"""

from datetime import date

import msgspec
from typing_extensions import TypedDict

from sdmxlib.formats._tags import tag
from sdmxlib.formats.sdmx_json._dto import (
    Annotation,
    Link,
    WireStruct,
    _camel_rename,
    emit_annotations,
    emit_istr,
    one_of,
)
from sdmxlib.model.category import Categorisation, Category, CategoryScheme
from sdmxlib.model.codelist import (
    CascadeSelection,
    Code,
    Codelist,
    CodelistExtension,
    CodeSelection,
    GeoFeatureSetCode,
    GeographicCodelist,
    GeoGridCode,
    GeoGridCodelist,
    InclusionMode,
    MemberValue,
)
from sdmxlib.model.collections import Maintainable
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.constraint import (
    ComponentValueSet,
    ConstraintAttachment,
    CubeRegion,
    DataConstraint,
    DataKeySet,
    QueryableDataSource,
    RegionValue,
    TimeBound,
    TimeRange,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import (
    AttachmentLevel,
    Attribute,
    AttributeUsage,
    DataStructure,
    Dimension,
    Group,
    Measure,
    MetadataAttributeUsage,
    TimeDimension,
)
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, HierarchyAssociation, Level
from sdmxlib.model.link import Link as ModelLink
from sdmxlib.model.mapping import (
    CategorySchemeMap,
    ConceptSchemeMap,
    DatePatternMap,
    EpochMap,
    ItemMap,
    MapDataType,
    MapEndpoint,
    OrganisationSchemeMap,
    RepresentationMap,
    RepresentationMapping,
    StructureMap,
)
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.organisation import (
    Agency,
    AgencyScheme,
    Contact,
    DataConsumer,
    DataConsumerScheme,
    DataProvider,
    DataProviderScheme,
    MetadataProvider,
    MetadataProviderScheme,
    OrganisationUnit,
    OrganisationUnitScheme,
)
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref, SupportsUrn, artefact_urn
from sdmxlib.model.representation import Facet, SentinelValue
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.valuelist import ValueList

# Struct fields are snake_case; `rename=_camel_rename` (shared, from `_dto`)
# maps them to the SDMX-JSON camelCase wire names. MaintainableJson subclasses
# inherit the renamer.
#
# Every maintainable shares id/agencyID/version + name/names, description/
# descriptions, annotations, validity. id/agencyID/version are REQUIRED
# (no default) so omit_defaults can never drop them — see _dto conventions.


class MaintainableJson(WireStruct, rename=_camel_rename):
    id: str
    agency_id: str
    #: Optional in SDMX-JSON 2.0, which reads its absence as un-versioned;
    #: ingest holds it as 1.0 and the read records the assumption (#474, #478).
    version: str | None = None
    name: str | None = None
    names: dict[str, str] | None = None
    description: str | None = None
    descriptions: dict[str, str] | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    annotations: list[Annotation] | None = None
    #: `None` when false, so the default is never emitted (#422). SDMX-JSON
    #: has no ``serviceURL`` or ``structureURL`` to go with it.
    is_external_reference: bool | None = None
    #: SDMX-JSON 2.1's `isPartialLanguage` (#422); `None` when false.
    is_partial_language: bool | None = None
    #: Artefact-level links. Carries the artefact's own URN under
    #: ``rel="self"`` (#406), its ``@uri`` under ``rel="describedby"``
    #: (#238), and the SDMX 3.0 ``rel="metadata"`` back-links to
    #: reference-metadata reports, which the writer appends (#191). ``None``
    #: (omitted) when there are none of the three.
    links: list[Link] | None = None


class ItemSchemeJson(MaintainableJson):
    """`MaintainableJson` plus ``isPartial``, which the schema gives every item scheme (#422)."""

    #: `None` when false, so the default is never emitted.
    is_partial: bool | None = None


class MaintFields(TypedDict, closed=True):
    """The `MaintainableJson` field values `_maint_fields` produces.

    A TypedDict rather than ``dict[str, object]`` so the ``**`` splat into each
    concrete ``…Json`` Struct type-checks: with the loose form every splat
    failed all eleven field assignments at once, which is what the eighteen
    ``arg-type`` suppressions removed here used to hide.

    Keys mirror `MaintainableJson`'s fields — snake_case; the shared
    ``rename=_camel_rename`` maps them to wire names. ``links`` carries the
    artefact's ``@uri`` (#238); the writer *appends* its ``rel="metadata"``
    back-links to whatever is here rather than replacing them.
    """

    id: str
    agency_id: str
    version: str
    name: str | None
    names: dict[str, str] | None
    description: str | None
    descriptions: dict[str, str] | None
    valid_from: str | None
    valid_to: str | None
    annotations: list[Annotation] | None
    is_external_reference: bool | None
    is_partial_language: bool | None
    links: list[Link] | None


#: The ``rel`` SDMX-JSON carries an artefact's **own URN** under.
#:
#: The schema says so itself, on the ``rel`` property of a link item: "Use
#: 'self' to indicate the urn to the parent object." A link item permits
#: ``urn`` and requires no ``href``, so the identity rides alone in one entry.
#:
#: This is the form that matters in practice as well as in the spec: the .Stat
#: Suite client reads ``links[rel=self].urn`` and does not fall back to a
#: top-level ``urn`` property, so a writer emitting only the property would
#: still leave its references unresolvable there (#406).
#:
#: **And it is the only conformant form.** #406 asked for a top-level ``urn``
#: property as well, on the reading that SDMX-JSON accepts either. Neither
#: vendored schema defines one: 2.0 and 2.1 both put the self-URN in ``links``
#: and describe it in those words on `IdentifiableType`. 2.0 tolerates the
#: extra property because its objects are open; 2.1 closes every object with
#: ``unevaluatedProperties`` and rejects it outright — which
#: `test_wire_conformance_gate` reported as 72 invalid documents when the
#: property was emitted. One form, because there is one.
SELF_REL = "self"

#: The ``rel`` SDMX-JSON carries an artefact's ``@uri`` under.
#:
#: SDMX-JSON has no ``uri`` field. The spec routes external pointers through
#: ``links``, whose ``rel`` is an RFC 5988 relation, and reserves ``self`` for
#: the artefact's own URN — which is the identity, not the pointer. ``@uri``
#: is documented as "a link to a resource with additional information about
#: the object", which is exactly RFC 5988's ``describedby``; the SDMX-JSON
#: schema lists it among the permitted relations (#238).
URI_REL = "describedby"


def uri_links(obj: object) -> list[Link] | None:
    """The ``links`` entry carrying an artefact's ``@uri``, or None.

    Args:
        obj: Any identifiable artefact. Probed rather than typed to
            `SupportsUri` so the DSD-component and item call sites, which pass
            concrete types, need no protocol conformance declaration.

    Returns:
        A one-element list when the artefact carries a uri, else None so
        ``omit_defaults`` drops the key entirely.
    """
    uri: str | None = getattr(obj, "uri", None)
    links: tuple[ModelLink, ...] = getattr(obj, "links", ())
    return link_to(uri, links)


def link_to(uri: str | None, links: tuple[ModelLink, ...] = ()) -> list[Link] | None:
    """`uri_links` for a bare uri and links — a component list's, which no model object carries (#422).

    The uri is a ``describedby`` link, and `read_uri` takes the first one. So
    when the first held ``describedby`` link *is* the uri, carrying more than
    its ``href``, it is written where it stands and stands for the uri too —
    which is how `read_links` read it. Otherwise a bare uri link goes first.
    """
    if uri is None and not links:
        # Nearly every item: nothing to link, so no list built per item (#462).
        return None
    out = [] if uri is None or _carries_uri(uri, links) else [Link(rel=URI_REL, href=uri)]
    out.extend(
        Link(rel=link.rel, href=link.url, urn=link.urn, type=link.type, hreflang=link.hreflang) for link in links
    )
    return out or None


def _carries_uri(uri: str, links: tuple[ModelLink, ...]) -> bool:
    """Whether the first ``describedby`` link is `uri`'s, with something besides the ``href`` (#422)."""
    first = next((link for link in links if link.rel == URI_REL), None)
    return (
        first is not None
        and first.url == uri
        and (first.urn is not None or first.type is not None or first.hreflang is not None)
    )


def self_urn(obj: object) -> str | None:
    """The artefact's own URN, or None when one cannot be built.

    Probed rather than typed, for the same reason `uri_links` is: this runs
    for every maintainable the writer emits, and `_maint_fields` takes the
    looser `Maintainable` protocol — id / agency_id / version — which is not
    enough to build a URN. `SupportsUrn` is, and it is runtime-checkable, so
    the probe is the narrowing.

    An object that cannot answer gets None and loses only its self-link, which
    is what a writer should do with an artefact whose identity it cannot
    state.
    """
    if not isinstance(obj, SupportsUrn):
        return None
    try:
        return str(artefact_urn(obj))
    except ValueError:
        return None


def read_uri(links: list[Link] | None) -> str | None:
    """The ``@uri`` carried by a ``links`` array, or None.

    The inverse of `uri_links`. Other relations — the ``rel="metadata"``
    back-links, a producer's ``self`` — are ignored rather than mistaken for
    a uri.
    """
    uri_link = _uri_link(links)
    return None if uri_link is None else uri_link.href


def _uri_link(links: list[Link] | None) -> Link | None:
    """The first ``describedby`` link with an ``href``: the one `read_uri` and `read_links` both look for.

    A plain loop: these run once per item, and a generator expression's setup
    cost more than the search on the one-link lists FMR writes (#466).
    """
    for link in links or ():
        if link.rel == URI_REL and link.href:
            return link
    return None


def read_links(links: list[Link] | None) -> tuple[ModelLink, ...]:
    """The model's links from a ``links`` array: all but what other fields hold (#422).

    The inverse of `uri_links`. A ``self`` link is the artefact's identity and
    the first ``describedby`` link with an ``href`` is its `@uri`, so neither
    is a `Link`; every other link is, the ``rel="metadata"`` back-links
    included. The uri's link is a `Link` as well when it carries more than its
    ``href`` — a ``urn``, a ``type`` or a language — which `@uri` cannot hold.
    """
    if not links:
        return ()
    uri_link = _uri_link(links)
    if uri_link is not None and (uri_link.urn or uri_link.type or uri_link.hreflang):
        uri_link = None
    return tuple(
        [
            ModelLink(
                rel=link.rel,
                url=link.href,
                urn=link.urn,
                type=link.type,
                hreflang=None if link.hreflang is None else tag(link.hreflang, "hreflang"),
            )
            for link in links
            if link.rel != SELF_REL and link is not uri_link
        ]
    )


def _maint_fields(m: Maintainable) -> MaintFields:
    """The common maintainable field values, ready to splat into a subclass.

    ``m`` is typed to the `Maintainable` Protocol — id / agency_id / version —
    which is the whole contract this needs for the three required fields. The
    remaining fields stay ``getattr``-probed with defaults: not every artefact
    that reaches here carries validity or annotations, and the probe is what
    makes that safe.
    """
    name, names = emit_istr(getattr(m, "name", None))
    desc, descs = emit_istr(getattr(m, "description", None))
    # Identity first, then the pointer at `@uri`; the writer appends its
    # `rel="metadata"` back-links after both.
    urn = self_urn(m)
    links: list[Link] = [Link(rel=SELF_REL, urn=urn)] if urn else []
    links.extend(uri_links(m) or [])
    return {
        "id": m.id,
        "agency_id": m.agency_id,
        "version": m.version,
        "name": name,
        "names": names,
        "description": desc,
        "descriptions": descs,
        "valid_from": vf.isoformat() if (vf := getattr(m, "valid_from", None)) is not None else None,
        "valid_to": vt.isoformat() if (vt := getattr(m, "valid_to", None)) is not None else None,
        "annotations": emit_annotations(getattr(m, "annotations", None)),
        "is_external_reference": bool(getattr(m, "is_external_reference", False)) or None,
        "is_partial_language": bool(getattr(m, "is_partial_language", False)) or None,
        "links": links or None,
    }


# ── Representation (concept core_representation; reused by DSD components) ─────


class Format(WireStruct, rename=_camel_rename):
    data_type: str | None = None
    max_length: int | None = None
    min_length: int | None = None
    max_value: float | None = None
    min_value: float | None = None
    decimals: int | None = None
    pattern: str | None = None
    start_value: float | None = None
    end_value: float | None = None
    is_sequence: bool | None = None
    is_multilingual: bool | None = None
    #: A sequence's step and a time sequence's step and bounds (#422).
    interval: float | None = None
    time_interval: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    #: Values with a special meaning (#422) — a text format's alone.
    sentinel_values: list[SentinelValueJson] | None = None
    #: Read tolerance: SDMX-JSON 1.0's name for `data_type` (GH-30), moved
    #: there as it is read so nothing downstream sees two spellings.
    text_type: str | None = None

    def __post_init__(self) -> None:
        """Read 1.0's `text_type` as `data_type`."""
        if self.text_type is not None and self.data_type is None:
            self.data_type, self.text_type = self.text_type, None


class SentinelValueJson(WireStruct, rename=_camel_rename):
    """`sentinelValueType`: a value with a special meaning, and its name."""

    #: A string or a number on the wire; `int` before `float`, so ``-1`` reads
    #: back as the text ``"-1"`` rather than ``"-1.0"``.
    value: str | int | float | None = None
    name: str | None = None
    names: dict[str, str] | None = None
    description: str | None = None
    descriptions: dict[str, str] | None = None

    def __post_init__(self) -> None:
        """A sentinel is its value; one without is refused (#474).

        Raises:
            ValueError: If the value is missing.
        """
        if self.value is None:
            msg = "a sentinel value needs its value"
            raise ValueError(msg)


class Representation(WireStruct, rename=_camel_rename):
    enumeration: str | None = None
    format: Format | None = None
    #: Facets constraining the *codes* of an enumerated representation. The
    #: schema declares it on five representation types, and it is a sibling of
    #: `enumeration` rather than an alternative to it — a component may carry
    #: a codelist and a format together (#243).
    enumeration_format: Format | None = None
    #: A DSD attribute's or measure's occurrence bounds (#422), beside the
    #: format rather than inside it. `maxOccurs` is `OccurenceType`: a count,
    #: or ``"unbounded"``.
    min_occurs: int | None = None
    max_occurs: int | str | None = None
    #: Read tolerance: SDMX-JSON 1.0's name for `format` (GH-30), moved there
    #: as it is read, before the ``oneOf`` below counts it.
    text_format: Format | None = None

    def __post_init__(self) -> None:
        """Every representation type's ``oneOf``: an enumeration or a format (#474).

        Raises:
            ValueError: If it has neither, or both.
        """
        if self.text_format is not None and self.format is None:
            self.format, self.text_format = self.text_format, None
        # Tested inline before `one_of` builds its arguments: this runs for
        # every component and concept, and the call alone was 2-5% of a read.
        if (self.enumeration is None) == (self.format is None):
            one_of("a representation", enumeration=self.enumeration, format=self.format)


def _to_format(f: Facet, *, coded: bool = False, coding: bool = False) -> Format:
    """A `Facet` as its wire struct.

    `coded=True` for an `enumerationFormat`, which constrains the *codes* of an
    enumerated representation rather than free text. The schema types that
    branch without `decimals` or `isMultiLingual` — codes are strings — and
    closes it, so emitting either produced a document the validator rejects:
    *"Additional properties are not allowed ('decimals', 'isMultiLingual' were
    unexpected)"*. Exactly the restriction `CodedTextFormatType` puts on the
    XML side, which `_common._PROHIBITED_ON` handles there (#364).

    `coding=True` for a level's `codingFormat`, which is `coded` and also has
    no time facets — `CodingTextFormatType` declares none, and `gaps.py`
    reports them (#422).
    """
    coded = coded or coding
    decimals = None if coded else f.decimals
    # SDMX-JSON declares no default for it, and an absent one reads as False:
    # only True is stated (#503).
    is_multilingual = True if f.is_multilingual and not coded else None
    return Format(
        data_type=f.type if isinstance(f.type, str) else (f.type.value if f.type is not None else None),
        max_length=f.max_length,
        min_length=f.min_length,
        max_value=f.max_value,
        min_value=f.min_value,
        decimals=decimals,
        pattern=f.pattern,
        start_value=f.start_value,
        end_value=f.end_value,
        is_sequence=f.is_sequence,
        is_multilingual=is_multilingual,
        interval=f.interval,
        time_interval=None if coding else f.time_interval,
        start_time=None if coding else f.start_time,
        end_time=None if coding else f.end_time,
        sentinel_values=None if coded else _to_sentinels(f.sentinel_values),
    )


def _to_sentinels(sentinels: tuple[SentinelValue, ...]) -> list[SentinelValueJson] | None:
    out: list[SentinelValueJson] = []
    for sentinel in sentinels:
        name, names = emit_istr(sentinel.name)
        desc, descs = emit_istr(sentinel.description)
        out.append(
            SentinelValueJson(value=sentinel.value, name=name, names=names, description=desc, descriptions=descs)
        )
    return out or None


def to_representation(
    rep: object, enumeration_format: Facet | None = None, occurs: tuple[int, int | None] = (1, 1)
) -> Representation | None:
    """Model representation (Ref → enumeration URN, Facet → format) → Struct.

    *enumeration_format* is emitted alongside an enumeration rather than
    instead of it — see `Representation.enumeration_format`. *occurs* is a DSD
    attribute's or measure's ``(min_occurs, max_occurs)``, written where it is
    not the default 1 (#422).
    """
    encoded = _to_format(enumeration_format, coded=True) if enumeration_format is not None else None
    min_occurs, max_occurs = occurs
    bounds = {
        "min_occurs": None if min_occurs == 1 else min_occurs,
        "max_occurs": None if max_occurs == 1 else ("unbounded" if max_occurs is None else max_occurs),
    }
    if isinstance(rep, Ref):
        return Representation(enumeration=str(rep.urn), enumeration_format=encoded, **bounds)
    if isinstance(rep, Facet):
        return Representation(format=_to_format(rep), enumeration_format=encoded, **bounds)
    if encoded is not None:
        return Representation(enumeration_format=encoded)
    return None


# ── Codelist ─────────────────────────────────────────────────────────────────


class CodeJson(WireStruct, rename=_camel_rename):
    id: str
    name: str | None = None
    names: dict[str, str] | None = None
    description: str | None = None
    descriptions: dict[str, str] | None = None
    parent: str | None = None
    annotations: list[Annotation] | None = None
    #: Carries this artefact's ``@uri`` under ``rel="describedby"`` (#238).
    links: list[Link] | None = None


class MemberValueJson(WireStruct, rename=_camel_rename):
    #: MemberValueType anyOf — this object variant; the array element type is
    #: ``str | MemberValueJson`` so a bare id string decodes to ``str`` and this
    #: object to the Struct (msgspec disambiguates by JSON type, no dec_hook).
    value: str
    #: CascadeSelectionType: ``xs:boolean`` OR the literal ``"excluderoot"`` →
    #: genuinely ``bool | str``. Ingested to the `CascadeSelection` enum.
    cascade_values: bool | str | None = None


class CodeSelectionJson(WireStruct, rename=_camel_rename):
    member_values: list[str | MemberValueJson] | None = None

    def __post_init__(self) -> None:
        """A selection is its member values (#474).

        The schema's alternative, ``wildcardedMemberValues``, is not read, so a
        selection holding only those is refused rather than read as selecting
        nothing.

        Raises:
            ValueError: If there are no member values.
        """
        if self.member_values is None:
            msg = "a code selection needs memberValues (sdmxlib does not read wildcardedMemberValues)"
            raise ValueError(msg)


class CodelistExtensionJson(WireStruct, rename=_camel_rename):
    #: The extended codelist's URN.
    codelist: str
    prefix: str | None = None
    #: ``CodelistExtensionType`` xs:choice — at most one is set. The model folds
    #: the choice into `CodeSelection.mode`; ingest picks whichever is present.
    inclusive_code_selection: CodeSelectionJson | None = None
    exclusive_code_selection: CodeSelectionJson | None = None


class CodelistJson(ItemSchemeJson):
    codes: list[CodeJson] | None = None
    codelist_extensions: list[CodelistExtensionJson] | None = None


class GeoFeatureSetCodeJson(CodeJson, kw_only=True):
    #: Required by the schema; keyword-only so it can follow `CodeJson`'s defaults.
    value: str


class GeoGridCodeJson(CodeJson, kw_only=True):
    #: Required by the schema; keyword-only as `GeoFeatureSetCodeJson.value` is.
    geo_cell: str


class GeographicCodelistJson(ItemSchemeJson):
    """``GeographicCodelistType``: a codelist whose codes sit under their own key."""

    #: The schema's ``const``; absent only from a stub.
    geo_type: str | None = None
    geo_feature_set_codes: list[GeoFeatureSetCodeJson] | None = None
    codelist_extensions: list[CodelistExtensionJson] | None = None


class GeoGridCodelistJson(ItemSchemeJson):
    """``GeoGridCodelistType``: `GeographicCodelistJson` with a grid definition."""

    geo_type: str | None = None
    grid_definition: str | None = None
    geo_grid_codes: list[GeoGridCodeJson] | None = None
    codelist_extensions: list[CodelistExtensionJson] | None = None


def to_code(c: Code) -> CodeJson:
    name, names = emit_istr(c.name)
    desc, descs = emit_istr(c.description)
    return CodeJson(
        id=c.id, name=name, names=names, description=desc, descriptions=descs, parent=c.parent_id,
        annotations=emit_annotations(c.annotations),
        links=uri_links(c),
    )  # fmt: skip


def to_geo_feature_set_code(c: GeoFeatureSetCode) -> GeoFeatureSetCodeJson:
    name, names = emit_istr(c.name)
    desc, descs = emit_istr(c.description)
    return GeoFeatureSetCodeJson(
        id=c.id, name=name, names=names, description=desc, descriptions=descs, parent=c.parent_id,
        annotations=emit_annotations(c.annotations),
        links=uri_links(c),
        value=c.value,
    )  # fmt: skip


def to_geo_grid_code(c: GeoGridCode) -> GeoGridCodeJson:
    name, names = emit_istr(c.name)
    desc, descs = emit_istr(c.description)
    return GeoGridCodeJson(
        id=c.id, name=name, names=names, description=desc, descriptions=descs, parent=c.parent_id,
        annotations=emit_annotations(c.annotations),
        links=uri_links(c),
        geo_cell=c.geo_cell,
    )  # fmt: skip


def to_member_value(mv: MemberValue) -> str | MemberValueJson:
    """MemberValue → bare id string (no cascade) OR ``{value, cascadeValues}``."""
    if mv.cascade is None:
        return mv.value
    # CascadeSelection.value is "true"/"false"/"excluderoot"; the first two are
    # JSON booleans on the wire, the last a string literal.
    cascade: bool | str = mv.cascade.value == "true" if mv.cascade.value != "excluderoot" else "excluderoot"
    return MemberValueJson(value=mv.value, cascade_values=cascade)


def to_code_selection(sel: CodeSelection) -> CodeSelectionJson:
    return CodeSelectionJson(member_values=[to_member_value(mv) for mv in sel.members])


def to_codelist_extension(ext: CodelistExtension) -> CodelistExtensionJson:
    incl = excl = None
    if ext.selection is not None:
        cs = to_code_selection(ext.selection)
        if ext.selection.mode is InclusionMode.INCLUDE:
            incl = cs
        else:
            excl = cs
    return CodelistExtensionJson(
        codelist=str(ext.codelist.urn),
        prefix=ext.prefix,
        inclusive_code_selection=incl,
        exclusive_code_selection=excl,
    )


def to_codelist(cl: Codelist) -> CodelistJson:
    codes = [to_code(c) for c in cl.codes]
    extensions = [to_codelist_extension(e) for e in cl.extensions] if cl.extensions else None
    return CodelistJson(
        **_maint_fields(cl),
        is_partial=cl.is_partial or None,
        # Schema requires non-empty when present; omit for empty codelists.
        codes=codes or None,
        codelist_extensions=extensions,
    )


def to_geographic_codelist(cl: GeographicCodelist) -> GeographicCodelistJson:
    codes = [to_geo_feature_set_code(c) for c in cl.codes if isinstance(c, GeoFeatureSetCode)]
    extensions = [to_codelist_extension(e) for e in cl.extensions] if cl.extensions else None
    return GeographicCodelistJson(
        **_maint_fields(cl),
        is_partial=cl.is_partial or None,
        geo_type="GeographicCodelist",
        geo_feature_set_codes=codes or None,
        codelist_extensions=extensions,
    )


def to_geo_grid_codelist(cl: GeoGridCodelist) -> GeoGridCodelistJson:
    codes = [to_geo_grid_code(c) for c in cl.codes if isinstance(c, GeoGridCode)]
    extensions = [to_codelist_extension(e) for e in cl.extensions] if cl.extensions else None
    return GeoGridCodelistJson(
        **_maint_fields(cl),
        is_partial=cl.is_partial or None,
        geo_type="GeoGridCodelist",
        grid_definition=cl.grid_definition,
        geo_grid_codes=codes or None,
        codelist_extensions=extensions,
    )


# ── ValueList ─────────────────────────────────────────────────────────────────


class ValueItemJson(WireStruct, rename=_camel_rename):
    id: str
    name: str | None = None
    names: dict[str, str] | None = None
    description: str | None = None
    descriptions: dict[str, str] | None = None
    annotations: list[Annotation] | None = None


class ValueListJson(MaintainableJson):
    value_items: list[ValueItemJson] | None = None


def to_value_list(vl: ValueList) -> ValueListJson:
    fields = _maint_fields(vl)
    # No `rel="self"` link: SDMX-JSON 2.0's generic `urn` pattern misspells the
    # class `Valuelist`, so a value list's own URN fails 2.0 wherever that
    # pattern applies. The link is optional; the id, agency and version name it.
    fields["links"] = [link for link in fields["links"] or [] if link.rel != SELF_REL] or None
    items = []
    for item in vl.items:
        name, names = emit_istr(item.name)
        desc, descs = emit_istr(item.description)
        items.append(
            ValueItemJson(
                id=item.id,
                name=name,
                names=names,
                description=desc,
                descriptions=descs,
                annotations=emit_annotations(item.annotations),
            )
        )
    return ValueListJson(**fields, value_items=items or None)


# ── ConceptScheme ────────────────────────────────────────────────────────────


class ISOConceptReferenceJson(WireStruct, rename=_camel_rename):
    """A concept's ``isoConceptReference`` (#422); all three are required."""

    concept_agency: str
    #: ``conceptSchemeID``: the trailing ``ID`` is upper case, as camel renaming
    #: would not spell it.
    concept_scheme_id: str = msgspec.field(name="conceptSchemeID")
    concept_id: str = msgspec.field(name="conceptID")


class ConceptJson(WireStruct, rename=_camel_rename):
    id: str
    name: str | None = None
    names: dict[str, str] | None = None
    description: str | None = None
    descriptions: dict[str, str] | None = None
    #: SDMX-JSON spells the parent reference `parent`, as it does for a code.
    parent: str | None = None
    core_representation: Representation | None = None
    iso_concept_reference: ISOConceptReferenceJson | None = None
    annotations: list[Annotation] | None = None
    #: Carries this artefact's ``@uri`` under ``rel="describedby"`` (#238).
    links: list[Link] | None = None


class ConceptSchemeJson(ItemSchemeJson):
    concepts: list[ConceptJson] | None = None


def to_concept(c: Concept) -> ConceptJson:
    name, names = emit_istr(c.name)
    desc, descs = emit_istr(c.description)
    return ConceptJson(
        id=c.id, name=name, names=names, description=desc, descriptions=descs,
        parent=c.parent_id,
        # Beside an enumeration only, as `ConceptRepresentation` allows (#422).
        core_representation=to_representation(
            c.core_representation,
            enumeration_format=c.enumeration_format if isinstance(c.core_representation, Ref) else None,
            occurs=(c.min_occurs, c.max_occurs),
        ),
        iso_concept_reference=(
            None
            if (iso := c.iso_concept_reference) is None
            else ISOConceptReferenceJson(
                concept_agency=iso.agency, concept_scheme_id=iso.concept_scheme_id, concept_id=iso.concept_id
            )
        ),
        annotations=emit_annotations(c.annotations),
        links=uri_links(c),
    )  # fmt: skip


def to_concept_scheme(cs: ConceptScheme) -> ConceptSchemeJson:
    return ConceptSchemeJson(
        **_maint_fields(cs), is_partial=cs.is_partial or None, concepts=[to_concept(c) for c in cs.concepts] or None
    )


# ── CategoryScheme ───────────────────────────────────────────────────────────


class CategoryJson(WireStruct, rename=_camel_rename):
    id: str
    name: str | None = None
    names: dict[str, str] | None = None
    description: str | None = None
    descriptions: dict[str, str] | None = None
    categories: list[CategoryJson] | None = None  # recursive nesting
    annotations: list[Annotation] | None = None
    #: Carries this artefact's ``@uri`` under ``rel="describedby"`` (#238).
    links: list[Link] | None = None


class CategorySchemeJson(ItemSchemeJson):
    categories: list[CategoryJson] | None = None


def to_category(cat: Category) -> CategoryJson:
    name, names = emit_istr(cat.name)
    desc, descs = emit_istr(cat.description)
    sub = [to_category(c) for c in cat.categories]
    return CategoryJson(
        id=cat.id, name=name, names=names, description=desc, descriptions=descs,
        categories=sub or None, annotations=emit_annotations(cat.annotations),
        # A category's `uri`, at every nesting depth. `CategoryJson` carries a
        # `links` field and this builder never filled it — the same omission as
        # `LevelJson`, and the recursion meant it was missing on sub-categories
        # too (#355).
        links=uri_links(cat),
    )  # fmt: skip


def to_category_scheme(cs: CategoryScheme) -> CategorySchemeJson:
    return CategorySchemeJson(
        **_maint_fields(cs),
        is_partial=cs.is_partial or None,
        categories=[to_category(c) for c in cs.categories] or None,
    )


# ── DataStructure components + Dataflow ──────────────────────────────────────


def _ref_urn(ref: object) -> str | None:
    """A component's concept/structure ref → URN string (None when not a Ref)."""
    return str(ref.urn) if isinstance(ref, Ref) else None


def _concept_role_urns(roles: tuple[str, ...]) -> list[str] | None:
    """Bare role ids (FREQUENCY, MEASURE, …) → cross-domain concept URN strings.

    SDMX-JSON carries roles as concept URNs into the SDMX cross-domain concept
    scheme — the canonical home assumed on re-serialisation (mirrors SDMX-ML).
    """
    if not roles:
        return None
    return [
        str(
            SdmxUrn.make(
                object,
                package="conceptscheme",
                artefact_class="Concept",
                agency="SDMX",
                id="CROSS_DOMAIN_CONCEPTS",
                version="1.0",
                item_id=role,
            )
        )
        for role in roles
    ]


class EmptyObject(WireStruct):
    """An SDMX-JSON presence marker — an empty ``{}`` whose presence is the signal.

    Carries no fields. Used for the ``observation`` / ``dataflow``
    attribute-relationship variants, which the wire spells as ``{}``.
    """


class AttributeRelationship(WireStruct, rename=_camel_rename):
    #: oneOf: {observation:{}} | {dimensions:[ids]} | {group:id} | {dataflow:{}}.
    #: Exactly one field is set; the empty-object variants carry ``{}``.
    observation: EmptyObject | None = None
    dimensions: list[str] | None = None
    #: Parallel to `dimensions`: which of them a value may be reported
    #: without (#422).
    are_dimensions_optional: list[bool] | None = None
    group: str | None = None
    dataflow: EmptyObject | None = None
    #: Read tolerance, SDMX-JSON 1.0's two spellings (GH-30): ``none`` is
    #: `dataflow` (2.1's ``<str:None/>``, a dataset-level attribute), and
    #: ``primaryMeasure`` is `observation`, naming the measure 2.1 relates it to.
    none: EmptyObject | None = None
    primary_measure: str | None = None

    def __post_init__(self) -> None:
        """The schema's ``oneOf`` (#474).

        Raises:
            ValueError: If it names no attachment, or several.
        """
        one_of(
            "an attributeRelationship",
            dataflow=self.dataflow,
            dimensions=self.dimensions,
            group=self.group,
            observation=self.observation,
            none=self.none,
            primaryMeasure=self.primary_measure,
        )


def to_attribute_relationship(attr: Attribute | MetadataAttributeUsage) -> AttributeRelationship:
    match attr.attachment:
        case AttachmentLevel.OBSERVATION:
            return AttributeRelationship(observation=EmptyObject())
        case AttachmentLevel.SERIES:
            dims = list(attr.related_dimensions or [])
            optional = [d in attr.optional_dimensions for d in dims] if attr.optional_dimensions else None
            return AttributeRelationship(dimensions=dims, are_dimensions_optional=optional)
        case AttachmentLevel.GROUP:
            return AttributeRelationship(group=attr.related_group or "")
        case _:
            return AttributeRelationship(dataflow=EmptyObject())


class DimensionJson(WireStruct, rename=_camel_rename):
    #: Optional, as a component's id is: ingest takes the concept's (#477).
    id: str | None = None
    #: Optional in the schema: the model holds order, not position (#474).
    position: int | None = None
    concept_identity: str | None = None
    local_representation: Representation | None = None
    concept_roles: list[str] | None = None
    annotations: list[Annotation] | None = None
    #: Carries this artefact's ``@uri`` under ``rel="describedby"`` (#238).
    links: list[Link] | None = None


class TimeDimensionJson(WireStruct, rename=_camel_rename):
    #: `id` is `str | None` for read tolerance (default "TIME_PERIOD" on ingest);
    #: the write side always sets it, so it is always emitted.
    id: str | None = None
    #: Both required by the schema; ingest refuses a time dimension without
    #: them. Optional here because the model's are, and the writer builds this.
    concept_identity: str | None = None
    local_representation: Representation | None = None
    annotations: list[Annotation] | None = None
    #: Carries this artefact's ``@uri`` under ``rel="describedby"`` (#238).
    links: list[Link] | None = None


class AttributeJson(WireStruct, rename=_camel_rename):
    #: Optional, as a component's id is: ingest takes the concept's (#477).
    id: str | None = None
    usage: str | None = None
    #: Read tolerance: SDMX-JSON 2.0 producers used `assignment_status` for `usage`.
    assignment_status: str | None = None
    concept_identity: str | None = None
    local_representation: Representation | None = None
    attribute_relationship: AttributeRelationship | None = None
    #: The measures the attribute applies to; absent means all (#422).
    measure_relationship: list[str] | None = None
    concept_roles: list[str] | None = None
    annotations: list[Annotation] | None = None
    #: Carries this artefact's ``@uri`` under ``rel="describedby"`` (#238).
    links: list[Link] | None = None


class MetadataAttributeUsageJson(WireStruct, rename=_camel_rename):
    """`MetadataAttributeUsageType` (#422): no id of its own, as in SDMX-ML."""

    metadata_attribute_reference: str
    attribute_relationship: AttributeRelationship | None = None
    annotations: list[Annotation] | None = None
    #: Carries the usage's ``@uri`` under ``rel="describedby"`` (#422).
    links: list[Link] | None = None


class MeasureJson(WireStruct, rename=_camel_rename):
    #: `id` is `str | None` for read tolerance (default "OBS_VALUE" on ingest);
    #: always emitted by the write side.
    id: str | None = None
    concept_identity: str | None = None
    local_representation: Representation | None = None
    #: SDMX 3.x ``usage`` (#422).
    usage: str | None = None
    concept_roles: list[str] | None = None
    annotations: list[Annotation] | None = None
    #: Carries this artefact's ``@uri`` under ``rel="describedby"`` (#238).
    links: list[Link] | None = None


class GroupJson(WireStruct, rename=_camel_rename):
    id: str
    group_dimensions: list[str] = msgspec.field(default_factory=list)
    annotations: list[Annotation] | None = None
    #: Carries this artefact's ``@uri`` under ``rel="describedby"`` (#238).
    links: list[Link] | None = None


def to_dimension(dim: Dimension, position: int) -> DimensionJson:
    return DimensionJson(
        id=dim.id, position=position,
        concept_identity=_ref_urn(dim.concept),
        local_representation=to_representation(dim.representation, dim.enumeration_format),
        concept_roles=_concept_role_urns(dim.concept_role),
        annotations=emit_annotations(dim.annotations),
        links=uri_links(dim),
    )  # fmt: skip


def to_time_dimension(td: TimeDimension) -> TimeDimensionJson:
    return TimeDimensionJson(
        id=td.id, concept_identity=_ref_urn(td.concept),
        local_representation=to_representation(td.representation),
        annotations=emit_annotations(td.annotations),
        links=uri_links(td),
    )  # fmt: skip


def to_attribute(attr: Attribute) -> AttributeJson:
    return AttributeJson(
        id=attr.id,
        # Optional is `AttributeType`'s default, and left out (#503).
        usage=None if attr.usage is AttributeUsage.OPTIONAL else attr.usage.value,
        concept_identity=_ref_urn(attr.concept),
        local_representation=to_representation(
            attr.representation, attr.enumeration_format, (attr.min_occurs, attr.max_occurs)
        ),
        attribute_relationship=to_attribute_relationship(attr),
        measure_relationship=list(attr.related_measures) if attr.related_measures is not None else None,
        concept_roles=_concept_role_urns(attr.concept_role),
        annotations=emit_annotations(attr.annotations),
        links=uri_links(attr),
    )


def to_measure(meas: Measure) -> MeasureJson:
    return MeasureJson(
        id=meas.id, concept_identity=_ref_urn(meas.concept),
        local_representation=to_representation(
            meas.representation, meas.enumeration_format, (meas.min_occurs, meas.max_occurs)
        ),
        usage=None if meas.usage is AttributeUsage.OPTIONAL else meas.usage.value,
        concept_roles=_concept_role_urns(meas.concept_role),
        annotations=emit_annotations(meas.annotations),
        links=uri_links(meas),
    )  # fmt: skip


def to_metadata_attribute_usage(usage: MetadataAttributeUsage) -> MetadataAttributeUsageJson:
    return MetadataAttributeUsageJson(
        metadata_attribute_reference=usage.id,
        attribute_relationship=to_attribute_relationship(usage),
        annotations=emit_annotations(usage.annotations),
        links=uri_links(usage),
    )


def to_group(grp: Group) -> GroupJson:
    # `links` for the group's `uri`, the third builder with a links field its
    # constructor never filled — after `LevelJson` and `CategoryJson` (#355).
    return GroupJson(
        id=grp.id,
        group_dimensions=list(grp.dimension_ids),
        annotations=emit_annotations(grp.annotations),
        links=uri_links(grp),
    )


class DimensionListJson(WireStruct, rename=_camel_rename):
    dimensions: list[DimensionJson] = msgspec.field(default_factory=list)
    time_dimension: TimeDimensionJson | None = None
    annotations: list[Annotation] | None = None
    #: Carries the list's ``@uri`` under ``rel="describedby"`` (#422).
    links: list[Link] | None = None


class AttributeListJson(WireStruct, rename=_camel_rename):
    attributes: list[AttributeJson] = msgspec.field(default_factory=list)
    metadata_attribute_usages: list[MetadataAttributeUsageJson] | None = None
    annotations: list[Annotation] | None = None
    links: list[Link] | None = None


class MeasureListJson(WireStruct, rename=_camel_rename):
    measures: list[MeasureJson] = msgspec.field(default_factory=list)
    #: Read tolerance: SDMX-JSON 2.1 producers use a single `primary_measure`.
    primary_measure: MeasureJson | None = None
    annotations: list[Annotation] | None = None
    links: list[Link] | None = None


class DataStructureComponentsJson(WireStruct, rename=_camel_rename):
    #: Required by the schema, so non-defaulted (#474).
    dimension_list: DimensionListJson
    attribute_list: AttributeListJson | None = None
    measure_list: MeasureListJson | None = None
    groups: list[GroupJson] | None = None


class DataStructureJson(MaintainableJson):
    data_structure_components: DataStructureComponentsJson | None = None
    #: The MSD whose attributes the DSD uses, as a URN (#422).
    metadata: str | None = None
    #: SDMX-JSON 2.1's `evolvingStructure` (#422); `None` when false.
    evolving_structure: bool | None = None


def to_data_structure(dsd: DataStructure) -> DataStructureJson:
    dim_list = DimensionListJson(
        # SDMX-JSON counts positions from 0 — "starting at 0", the schema says.
        dimensions=[to_dimension(d, i) for i, d in enumerate(dsd.dimensions)],
        time_dimension=to_time_dimension(dsd.time_dimension) if dsd.time_dimension is not None else None,
        annotations=emit_annotations(dsd.dimension_descriptor_annotations),
        links=link_to(dsd.dimension_descriptor_uri, dsd.dimension_descriptor_links),
    )
    attrs = [to_attribute(a) for a in dsd.attributes]
    usages = [to_metadata_attribute_usage(u) for u in dsd.metadata_attributes]
    measures = [to_measure(m) for m in dsd.measures]
    components = DataStructureComponentsJson(
        dimension_list=dim_list,
        attribute_list=(
            AttributeListJson(
                attributes=attrs,
                metadata_attribute_usages=usages or None,
                annotations=emit_annotations(dsd.attribute_descriptor_annotations),
                links=link_to(dsd.attribute_descriptor_uri, dsd.attribute_descriptor_links),
            )
            if attrs or usages
            else None
        ),
        measure_list=(
            MeasureListJson(
                measures=measures,
                annotations=emit_annotations(dsd.measure_descriptor_annotations),
                links=link_to(dsd.measure_descriptor_uri, dsd.measure_descriptor_links),
            )
            if measures
            else None
        ),
        groups=[to_group(g) for g in dsd.groups] if dsd.groups else None,
    )
    return DataStructureJson(
        **_maint_fields(dsd),
        data_structure_components=components,
        metadata=_ref_urn(dsd.metadata),
        evolving_structure=dsd.evolving_structure or None,
    )


class DataflowJson(MaintainableJson):
    structure: str | None = None


def to_dataflow(df: Dataflow) -> DataflowJson:
    return DataflowJson(**_maint_fields(df), structure=_ref_urn(df.structure))


# ── Organisation schemes, Categorisation, ProvisionAgreement ─────────────────


class ContactJson(WireStruct, rename=_camel_rename):
    name: str | None = None
    names: dict[str, str] | None = None
    department: str | None = None
    departments: dict[str, str] | None = None
    role: str | None = None
    roles: dict[str, str] | None = None
    telephones: list[str] | None = None
    faxes: list[str] | None = None
    x400s: list[str] | None = None
    uris: list[str] | None = None
    emails: list[str] | None = None
    id: str | None = None


def to_contact(c: Contact) -> ContactJson:
    name, names = emit_istr(c.name)
    dept, depts = emit_istr(c.department)
    role, roles = emit_istr(c.role)
    return ContactJson(
        name=name, names=names, department=dept, departments=depts, role=role, roles=roles,
        telephones=list(c.phones) or None, faxes=list(c.faxes) or None,
        x400s=list(c.x400s) or None, uris=list(c.uris) or None, emails=list(c.emails) or None,
        id=c.id,
    )  # fmt: skip


class OrganisationJson(WireStruct, rename=_camel_rename):
    """Shared Agency / DataProvider item shape (id + name/description + contacts)."""

    id: str
    name: str | None = None
    names: dict[str, str] | None = None
    description: str | None = None
    descriptions: dict[str, str] | None = None
    contacts: list[ContactJson] | None = None
    annotations: list[Annotation] | None = None
    #: Carries this artefact's ``@uri`` under ``rel="describedby"`` (#238).
    links: list[Link] | None = None


def to_organisation(org: Agency | DataProvider | MetadataProvider | DataConsumer) -> OrganisationJson:
    name, names = emit_istr(org.name)
    desc, descs = emit_istr(org.description)
    return OrganisationJson(
        id=org.id, name=name, names=names, description=desc, descriptions=descs,
        contacts=[to_contact(c) for c in org.contacts] or None,
        annotations=emit_annotations(org.annotations),
        links=uri_links(org),
    )  # fmt: skip


class AgencySchemeJson(ItemSchemeJson):
    agencies: list[OrganisationJson] | None = None


def to_agency_scheme(scheme: AgencyScheme) -> AgencySchemeJson:
    # `agencies` is always emitted (even empty), matching the legacy writer.
    return AgencySchemeJson(
        **_maint_fields(scheme),
        is_partial=scheme.is_partial or None,
        agencies=[to_organisation(a) for a in scheme.agencies] or None,
    )


class DataProviderSchemeJson(ItemSchemeJson):
    data_providers: list[OrganisationJson] | None = None


def to_data_provider_scheme(scheme: DataProviderScheme) -> DataProviderSchemeJson:
    # `dataProviders` is optional but, when present, may not be empty — the
    # schema sets minItems on it. An empty scheme is a normal model state, so
    # the key is omitted rather than emitted as `[]`, which is a document the
    # schema rejects (#229). The XML writers already omit theirs.
    providers = [to_organisation(p) for p in scheme.providers]
    return DataProviderSchemeJson(
        **_maint_fields(scheme), is_partial=scheme.is_partial or None, data_providers=providers or None
    )


class MetadataProviderSchemeJson(ItemSchemeJson):
    metadata_providers: list[OrganisationJson] | None = None


def to_metadata_provider_scheme(scheme: MetadataProviderScheme) -> MetadataProviderSchemeJson:
    """The metadata twin of `to_data_provider_scheme` (#315).

    Same empty-list treatment: the key is omitted rather than emitted as `[]`,
    which the schema's `minItems` rejects (#229).
    """
    providers = [to_organisation(p) for p in scheme.providers]
    return MetadataProviderSchemeJson(
        **_maint_fields(scheme), is_partial=scheme.is_partial or None, metadata_providers=providers or None
    )


class DataConsumerSchemeJson(ItemSchemeJson):
    data_consumers: list[OrganisationJson] | None = None


def to_data_consumer_scheme(scheme: DataConsumerScheme) -> DataConsumerSchemeJson:
    """A data consumer scheme (#469). An empty one omits `dataConsumers`, which the schema's `minItems` forbids."""
    consumers = [to_organisation(c) for c in scheme.consumers]
    return DataConsumerSchemeJson(
        **_maint_fields(scheme), is_partial=scheme.is_partial or None, data_consumers=consumers or None
    )


class OrganisationUnitJson(OrganisationJson):
    """An organisation unit: an organisation with a `parent`, the id of another unit of its scheme (#469)."""

    parent: str | None = None


def to_organisation_unit(unit: OrganisationUnit) -> OrganisationUnitJson:
    name, names = emit_istr(unit.name)
    desc, descs = emit_istr(unit.description)
    return OrganisationUnitJson(
        id=unit.id, name=name, names=names, description=desc, descriptions=descs,
        contacts=[to_contact(c) for c in unit.contacts] or None,
        annotations=emit_annotations(unit.annotations),
        links=uri_links(unit),
        parent=unit.parent_id,
    )  # fmt: skip


class OrganisationUnitSchemeJson(ItemSchemeJson):
    organisation_units: list[OrganisationUnitJson] | None = None


def to_organisation_unit_scheme(scheme: OrganisationUnitScheme) -> OrganisationUnitSchemeJson:
    """An organisation unit scheme (#469), with the same empty-list treatment."""
    units = [to_organisation_unit(u) for u in scheme.organisation_units]
    return OrganisationUnitSchemeJson(
        **_maint_fields(scheme), is_partial=scheme.is_partial or None, organisation_units=units or None
    )


class CategorisationJson(MaintainableJson):
    source: str | None = None
    target: str | None = None


def to_categorisation(cat: Categorisation) -> CategorisationJson:
    return CategorisationJson(**_maint_fields(cat), source=_ref_urn(cat.source), target=_ref_urn(cat.target))


class ProvisionAgreementJson(MaintainableJson):
    # SDMX-JSON 2.1 `dataflow` (#129); legacy sdmxlib used the 2.0 `structureUsage`.
    dataflow: str | None = None
    data_provider: str | None = None


def to_provision_agreement(pa: ProvisionAgreement) -> ProvisionAgreementJson:
    return ProvisionAgreementJson(
        **_maint_fields(pa),
        dataflow=_ref_urn(pa.dataflow),
        data_provider=_ref_urn(pa.data_provider),
    )


# ── DataConstraint ───────────────────────────────────────────────────────────


class QueryableDataSourceJson(WireStruct, rename=_camel_rename):
    """`QueryableDataSourceType` — its acronyms are the spec's, not camel case."""

    data_url: str = msgspec.field(name="dataURL")
    is_rest_datasource: bool = msgspec.field(name="isRESTDatasource")
    is_web_service_datasource: bool = msgspec.field(name="isWebServiceDatasource")
    wsdl_url: str | None = msgspec.field(default=None, name="wsdlURL")
    wadl_url: str | None = msgspec.field(default=None, name="wadlURL")


class ConstraintAttachmentJson(WireStruct, rename=_camel_rename):
    #: `DataConstraintAttachmentType` is a oneOf: a provider, or one
    #: structure kind — and in 2.0 simple data sources, or queryable ones
    #: beside a kind. Both are read; neither is written, since the output
    #: validates against 2.1 too, which removed them — `check_serialisable`
    #: refuses them, and SDMX-JSON's want of data sets (#422).
    data_provider: str | None = None
    simple_data_sources: list[str] | None = None
    data_structures: list[str] | None = None
    dataflows: list[str] | None = None
    provision_agreements: list[str] | None = None
    queryable_data_sources: list[QueryableDataSourceJson] | None = None

    def __post_init__(self) -> None:
        """The schema's ``oneOf``: what the constraint attaches to (#474).

        Raises:
            ValueError: If it attaches to nothing, or to several kinds.
        """
        one_of(
            "a constraintAttachment",
            dataProvider=self.data_provider,
            simpleDataSources=self.simple_data_sources,
            dataStructures=self.data_structures,
            dataflows=self.dataflows,
            provisionAgreements=self.provision_agreements,
        )


def to_constraint_attachment(attach: ConstraintAttachment) -> ConstraintAttachmentJson | None:
    if attach.is_empty:
        return None

    def urns(refs: tuple[Ref[object], ...]) -> list[str] | None:
        return [str(ref.urn) for ref in refs] or None

    return ConstraintAttachmentJson(
        data_provider=_ref_urn(attach.data_provider),
        simple_data_sources=list(attach.simple_data_sources) or None,
        data_structures=urns(attach.data_structures),
        dataflows=urns(attach.dataflows),
        provision_agreements=urns(attach.provision_agreements),
        queryable_data_sources=[to_queryable_data_source(source) for source in attach.queryable_data_sources] or None,
    )


def to_queryable_data_source(source: QueryableDataSource) -> QueryableDataSourceJson:
    return QueryableDataSourceJson(
        data_url=source.data_url,
        is_rest_datasource=source.is_rest_datasource,
        is_web_service_datasource=source.is_web_service_datasource,
        wsdl_url=source.wsdl_url,
        wadl_url=source.wadl_url,
    )


class TimePeriodRangeJson(WireStruct, rename=_camel_rename):
    """One end of a time range: `{"period": ..., "isInclusive": ...}`."""

    period: str
    is_inclusive: bool = True


class TimeRangeJson(WireStruct, rename=_camel_rename):
    """`TimeRangeValueType`: `afterPeriod` | `beforePeriod` | `startPeriod` + `endPeriod`."""

    after_period: TimePeriodRangeJson | None = None
    before_period: TimePeriodRangeJson | None = None
    start_period: TimePeriodRangeJson | None = None
    end_period: TimePeriodRangeJson | None = None
    #: The range's own validity (#422).
    valid_from: str | None = None
    valid_to: str | None = None

    def __post_init__(self) -> None:
        """The schema's ``oneOf``: after a period, before one, or between two (#474).

        Raises:
            ValueError: If the range is none of those, or more than one.
        """
        between = None
        if self.start_period is not None or self.end_period is not None:
            if self.start_period is None or self.end_period is None:
                msg = "a timeRange between two periods needs both startPeriod and endPeriod"
                raise ValueError(msg)
            between = (self.start_period, self.end_period)
        one_of("a timeRange", afterPeriod=self.after_period, beforePeriod=self.before_period, startPeriod=between)


class ValueJson(WireStruct, rename=_camel_rename):
    """`SimpleComponentValueType`'s object form: a value plus what qualifies it.

    The schema allows a bare string or this, and the reader typed the values
    `list[str]`, so a conformant document using the object form failed to
    decode at all. It is the wire form of a qualified `RegionValue` (#422);
    an unqualified one is written bare.
    """

    value: str
    cascade_values: bool | str | None = None
    lang: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None


#: A region value as the wire spells it: bare, or an object (`ValueJson`).
type WireRegionValue = str | ValueJson


def to_wire_value(value: RegionValue) -> WireRegionValue:
    """A region value, bare unless something qualifies it."""
    alone = value.cascade is CascadeSelection.SELF_ONLY
    if alone and value.lang is None and value.valid_from is None and value.valid_to is None:
        return value.value
    return ValueJson(
        value=value.value,
        # `CascadeSelectionType` is a boolean or "excluderoot"; the code alone
        # is its default, and left out (#503).
        cascade_values=None if alone else _CASCADE_WIRE[value.cascade],
        lang=value.lang,
        valid_from=value.valid_from,
        valid_to=value.valid_to,
    )


_CASCADE_WIRE: dict[CascadeSelection, bool | str] = {
    CascadeSelection.INCLUDE_DESCENDANTS: True,
    CascadeSelection.SELF_ONLY: False,
    CascadeSelection.EXCLUDE_ROOT: "excluderoot",
}


def _wire_values(values: frozenset[RegionValue]) -> list[WireRegionValue]:
    return [to_wire_value(v) for v in sorted(values, key=lambda v: (v.value, v.lang or ""))]


class KeyValueJson(WireStruct, rename=_camel_rename):
    id: str
    values: list[WireRegionValue] = msgspec.field(default_factory=list)
    time_range: TimeRangeJson | None = None
    #: False excludes the values (#422); absent is true, and only false is written.
    include: bool | None = None
    remove_prefix: bool | None = None
    valid_from: str | None = None
    valid_to: str | None = None


class ComponentValueSetJson(WireStruct, rename=_camel_rename):
    """A cube region's `components` entry: a non-dimension component's values or time range."""

    id: str
    values: list[WireRegionValue] = msgspec.field(default_factory=list)
    time_range: TimeRangeJson | None = None
    #: As `KeyValueJson.include`.
    include: bool | None = None
    remove_prefix: bool | None = None


class CubeRegionJson(WireStruct, rename=_camel_rename):
    #: `bool | None` (default None): the write side always sets it, so it is
    #: always emitted; a plain `bool` default of True would let omit_defaults
    #: drop it on inclusive regions, and read tolerance wants it optional
    #: (ingest defaults absent → True).
    include: bool | None = None
    key_values: list[KeyValueJson] = msgspec.field(default_factory=list)
    components: list[ComponentValueSetJson] = msgspec.field(default_factory=list)
    annotations: list[Annotation] | None = None


def _bound(bound: TimeBound) -> TimePeriodRangeJson:
    return TimePeriodRangeJson(period=bound.period, is_inclusive=bound.inclusive)


def to_time_range(time_range: TimeRange | None) -> TimeRangeJson | None:
    """The wire choice follows from which ends the range has, as in SDMX-ML."""
    if time_range is None:
        return None
    start, end = time_range.start, time_range.end
    both = start is not None and end is not None
    return TimeRangeJson(
        start_period=_bound(start) if both and start is not None else None,
        end_period=_bound(end) if both and end is not None else None,
        after_period=_bound(start) if not both and start is not None else None,
        before_period=_bound(end) if not both and end is not None else None,
        valid_from=time_range.valid_from,
        valid_to=time_range.valid_to,
    )


def to_cube_region(region: CubeRegion) -> CubeRegionJson:
    return CubeRegionJson(
        include=None if region.include else False,
        annotations=emit_annotations(region.annotations),
        key_values=[
            KeyValueJson(
                id=kv.dimension_id,
                values=_wire_values(kv.values),
                time_range=to_time_range(kv.time_range),
                include=None if kv.include else False,
                remove_prefix=kv.remove_prefix or None,
                valid_from=kv.valid_from,
                valid_to=kv.valid_to,
            )
            for kv in region.keys
        ],
        components=[to_component_value_set(c) for c in region.components],
    )


def to_component_value_set(c: ComponentValueSet) -> ComponentValueSetJson:
    """A component's value set — a cube region's or a data key's (#422)."""
    return ComponentValueSetJson(
        id=c.component_id,
        values=_wire_values(c.values),
        time_range=to_time_range(c.time_range),
        include=None if c.include else False,
        remove_prefix=c.remove_prefix or None,
    )


class DataKeyValueJson(WireStruct, rename=_camel_rename):
    """One dimension's value within an explicit data key."""

    id: str
    value: str


class DataKeyJson(WireStruct, rename=_camel_rename):
    key_values: list[DataKeyValueJson] = msgspec.field(default_factory=list)
    #: The attribute or measure values the key's data has (#422).
    components: list[ComponentValueSetJson] | None = None
    annotations: list[Annotation] | None = None
    valid_from: str | None = None
    valid_to: str | None = None


class DataKeySetJson(WireStruct, rename=_camel_rename):
    """``DataKeySetType`` — whole observation keys, as opposed to cube regions.

    ``isIncluded`` is declared *required* by the schema, so it is a
    non-defaulted field: `WireStruct` omits defaults on encode, and a default
    here would drop the key the schema insists on.
    """

    is_included: bool
    keys: list[DataKeyJson]


class DataConstraintJson(MaintainableJson):
    constraint_attachment: ConstraintAttachmentJson | None = None
    #: `DataConstraintType` allows selection by whole key as well as by
    #: region. sdmxlib modelled only the region, so a constraint carrying a
    #: key set lost it (#241).
    data_key_sets: list[DataKeySetJson] | None = None
    cube_regions: list[CubeRegionJson] | None = None


def to_data_constraint(cc: DataConstraint) -> DataConstraintJson:
    return DataConstraintJson(
        **_maint_fields(cc),
        constraint_attachment=to_constraint_attachment(cc.attachment),
        data_key_sets=[to_data_key_set(key_set) for key_set in cc.data_key_sets] or None,
        cube_regions=[to_cube_region(r) for r in cc.regions] if cc.regions else None,
    )


def to_data_key_set(key_set: DataKeySet) -> DataKeySetJson:
    """One key set, with its ``isIncluded`` (#479)."""
    return DataKeySetJson(
        is_included=key_set.is_included,
        keys=[
            DataKeyJson(
                key_values=[DataKeyValueJson(id=d, value=v) for d, v in key.values],
                annotations=emit_annotations(key.annotations),
                valid_from=key.valid_from,
                valid_to=key.valid_to,
                components=[to_component_value_set(c) for c in key.components] or None,
            )
            for key in key_set.keys
        ],
    )


# ── Hierarchy + HierarchyAssociation ─────────────────────────────────────────


class HierarchicalCodeJson(WireStruct, rename=_camel_rename):
    id: str
    code: str | None = None  # URN of the underlying Code
    level: str | None = None  # id reference to a formal Level
    valid_from: str | None = None
    valid_to: str | None = None
    hierarchical_codes: list[HierarchicalCodeJson] | None = None
    annotations: list[Annotation] | None = None
    #: Model name/description have no JSON counterpart — intentionally omitted.
    #: Carries this artefact's ``@uri`` under ``rel="describedby"`` (#238).
    links: list[Link] | None = None


def to_hierarchical_code(hc: HierarchicalCode) -> HierarchicalCodeJson:
    code = None
    if hc.code is not None:
        code = str(hc.code.urn if isinstance(hc.code, Ref) else hc.code)
    return HierarchicalCodeJson(
        id=hc.id,
        code=code,
        level=hc.level.id if hc.level is not None else None,
        valid_from=hc.valid_from.isoformat() if hc.valid_from is not None else None,
        valid_to=hc.valid_to.isoformat() if hc.valid_to is not None else None,
        hierarchical_codes=[to_hierarchical_code(c) for c in hc.children] if hc.children else None,
        annotations=emit_annotations(hc.annotations),
        links=uri_links(hc),
    )


class LevelJson(WireStruct, rename=_camel_rename):
    id: str
    name: str | None = None
    names: dict[str, str] | None = None
    description: str | None = None
    descriptions: dict[str, str] | None = None
    coding_format: Format | None = None
    level: LevelJson | None = None  # recursive next tier
    annotations: list[Annotation] | None = None
    #: Carries this artefact's ``@uri`` under ``rel="describedby"`` (#238).
    links: list[Link] | None = None


def _levels_head(levels: list[Level]) -> LevelJson | None:
    """The model's flat levels list → the SDMX-JSON recursive head Level node.

    HierarchyType.level is a single node whose ``level`` field nests the next
    tier. Build the chain tail→head so head.level == levels[1], etc.
    """
    node: LevelJson | None = None
    for lv in reversed(levels):
        name, names = emit_istr(lv.name)
        desc, descs = emit_istr(lv.description)
        node = LevelJson(
            id=lv.id, name=name, names=names, description=desc, descriptions=descs,
            # `coded=True`: a Level's coding format constrains *codes*, the
            # same as an `enumerationFormat`, and every target types it
            # without `decimals` or `isMultiLingual` — SDMX-ML calls it
            # `CodingTextFormatType` and `gaps.py` has declared the loss since
            # #159. Only the XML writers honoured it; this one emitted both,
            # which SDMX-JSON 2.0 tolerates and 2.1 rejects (#364).
            coding_format=_to_format(lv.coding_format, coding=True) if lv.coding_format is not None else None,
            level=node, annotations=emit_annotations(lv.annotations),
            # `LevelJson` has carried a `links` field all along and nothing
            # filled it, so a Level's `uri` was dropped — the one artefact in
            # the JSON writer whose builder forgot the call every other one
            # makes. `IdentifiableType` permits links in both 2.0 and 2.1
            # (#355).
            links=uri_links(lv),
        )  # fmt: skip
    return node


class HierarchyJson(MaintainableJson):
    #: Always emitted: typed bool|None so a real False (not the None default) is
    #: never dropped by omit_defaults (see _dto conventions).
    has_formal_levels: bool | None = None
    level: LevelJson | None = None
    hierarchical_codes: list[HierarchicalCodeJson] | None = None


def to_hierarchy(h: Hierarchy) -> HierarchyJson:
    return HierarchyJson(
        **_maint_fields(h),
        has_formal_levels=h.has_formal_levels,
        level=_levels_head(list(h.levels)),
        hierarchical_codes=[to_hierarchical_code(hc) for hc in h.tree] if h.tree else None,
    )


class HierarchyAssociationJson(MaintainableJson):
    linked_hierarchy: str | None = None
    linked_object: str | None = None
    context_object: str | None = None


def to_hierarchy_association(ha: HierarchyAssociation) -> HierarchyAssociationJson:
    return HierarchyAssociationJson(
        **_maint_fields(ha),
        linked_hierarchy=_ref_urn(ha.linked_hierarchy),
        linked_object=_ref_urn(ha.linked_object),
        context_object=_ref_urn(ha.context_object),
    )


# ── MetadataStructure (MSD) + Metadataflow ───────────────────────────────────


class MetadataAttributeJson(WireStruct, rename=_camel_rename):
    #: Optional, as a component's id is: ingest takes the concept's (#477).
    id: str | None = None
    concept_identity: str | None = None
    local_representation: Representation | None = None
    min_occurs: int | None = None
    max_occurs: int | str | None = None
    is_presentational: bool | None = None
    metadata_attributes: list[MetadataAttributeJson] | None = None
    annotations: list[Annotation] | None = None
    #: Carries this artefact's ``@uri`` under ``rel="describedby"`` (#238).
    links: list[Link] | None = None


def to_metadata_attribute(attr: MetadataAttribute) -> MetadataAttributeJson:
    max_occ: int | str | None = None
    if attr.max_occurs != 1:
        # OccurenceType: positive int OR "unbounded" (model uses None for it).
        max_occ = "unbounded" if attr.max_occurs is None else attr.max_occurs
    return MetadataAttributeJson(
        id=attr.id,
        concept_identity=_ref_urn(attr.concept),
        local_representation=to_representation(attr.representation, attr.enumeration_format),
        # 1 is `MetadataAttributeType`'s default, as SDMX-ML's: left out, and
        # an absent one read as 1. It was 0 both ways, which another reader
        # takes to mean 1 (#503).
        min_occurs=None if attr.min_occurs == 1 else attr.min_occurs,
        max_occurs=max_occ,
        is_presentational=True if attr.is_presentational else None,
        metadata_attributes=[to_metadata_attribute(c) for c in attr.children] if attr.children else None,
        annotations=emit_annotations(attr.annotations),
        links=uri_links(attr),
    )


class MetadataAttributeListJson(WireStruct, rename=_camel_rename, kw_only=True):
    #: Optional in the schema, and fixed: ``MetadataAttributeDescriptor``, as
    #: SDMX-ML 3.x fixes it. Never read (#474).
    id: str | None = None
    #: Required by the schema, so non-defaulted (#474).
    metadata_attributes: list[MetadataAttributeJson]
    annotations: list[Annotation] | None = None
    links: list[Link] | None = None


class MetadataStructureComponentsJson(WireStruct, rename=_camel_rename):
    #: Optional in the schema: an MSD with no attributes may omit it (#474).
    metadata_attribute_list: MetadataAttributeListJson | None = None


class MetadataStructureJson(MaintainableJson):
    metadata_structure_components: MetadataStructureComponentsJson | None = None


def to_metadata_structure(msd: MetadataStructure) -> MetadataStructureJson:
    # `metadataAttributes` carries minItems, and neither
    # `metadataStructureComponents` nor `metadataAttributeList` is required —
    # so an MSD with no attributes omits the whole components object instead of
    # emitting an empty attribute list inside it (#229).
    if not msd.attributes:
        return MetadataStructureJson(**_maint_fields(msd))
    components = MetadataStructureComponentsJson(
        metadata_attribute_list=MetadataAttributeListJson(
            id="MetadataAttributeDescriptor",
            metadata_attributes=[to_metadata_attribute(a) for a in msd.attributes],
            annotations=emit_annotations(msd.attribute_list_annotations),
            links=link_to(msd.attribute_list_uri, msd.attribute_list_links),
        )
    )
    return MetadataStructureJson(**_maint_fields(msd), metadata_structure_components=components)


class MetadataflowJson(MaintainableJson):
    structure: str | None = None
    #: Wildcard URNs, as for `MetadataProvisionAgreementJson.targets`. Neither
    #: written nor read before #421, so a flow's targets were lost silently.
    targets: list[str] | None = None


def to_metadataflow(mdf: Metadataflow) -> MetadataflowJson:
    return MetadataflowJson(**_maint_fields(mdf), structure=_ref_urn(mdf.structure), targets=list(mdf.targets) or None)


class MetadataProvisionAgreementJson(MaintainableJson):
    metadataflow: str | None = None
    metadata_provider: str | None = None
    #: Wildcard URNs — plain strings, since a wildcard names a class of
    #: artefacts rather than one resolvable artefact.
    targets: list[str] | None = None


def to_metadata_provision_agreement(mpa: MetadataProvisionAgreement) -> MetadataProvisionAgreementJson:
    return MetadataProvisionAgreementJson(
        **_maint_fields(mpa),
        metadataflow=_ref_urn(mpa.metadataflow),
        metadata_provider=_ref_urn(mpa.metadata_provider),
        targets=list(mpa.targets) or None,
    )


# ── Structure maps (the SDMX-JSON 2.1 /data map arrays) ──────────────────────


class ItemMapJson(WireStruct, rename=_camel_rename):
    """One entry of a concept- or category-scheme map.

    SDMX-JSON 2.1 types these as ``SingleValueMappingType``, whose fields are
    ``sourceValue`` / ``targetValue`` — *not* ``sourceId`` / ``targetId``,
    which the schema rejects outright (#226). ``sourceValue`` is a
    ``MappedValueType`` object rather than a bare string, because it can carry
    regex and substring-index qualifiers we do not model.
    """

    source_value: MappedValueJson
    target_value: str
    annotations: list[Annotation] | None = None
    valid_from: str | None = None
    valid_to: str | None = None


class ComponentMapJson(WireStruct, rename=_camel_rename):
    """One entry of a structure map.

    ``ComponentMapType`` requires ``source`` and ``target`` as *arrays* of
    (nested) ids with ``minItems: 1``, supporting many-to-many component maps.
    We model a single pair, so each array holds exactly one id.
    """

    source: list[str]
    target: list[str]
    annotations: list[Annotation] | None = None
    #: A representation, epoch or date-pattern map's URN (#422).
    representation_map: str | None = None


class MappedComponentJson(WireStruct, rename=_camel_rename):
    """One (source, target) component pair of a date map (#422)."""

    source: str
    target: str


class DateMapJson(WireStruct, rename=_camel_rename):
    """``DateMapType``'s content, which an epoch and a date-pattern map share (#422)."""

    id: str
    #: Required by the schema, so non-defaulted (#474).
    mapped_components: list[MappedComponentJson]
    resolve_period: str | None = None
    frequency_dimension: str | None = None
    mapped_frequencies: list[str] | None = None
    #: ``targetFrequencyID``: the trailing ``ID`` is upper case.
    target_frequency_id: str | None = msgspec.field(default=None, name="targetFrequencyID")
    annotations: list[Annotation] | None = None
    links: list[Link] | None = None

    def __post_init__(self) -> None:
        """The schema's ``oneOf``: the frequency to map from (#474).

        Raises:
            ValueError: If it names neither a frequency dimension nor a target frequency, or both.
        """
        one_of("a date map", frequencyDimension=self.frequency_dimension, targetFrequencyID=self.target_frequency_id)


class EpochMapJson(DateMapJson, kw_only=True):
    #: Required by the schema, so non-defaulted: `WireStruct` omits a default.
    base_period: str
    #: Required by the schema, with no default; ingest assumes ``day`` and the
    #: read records it, as SDMX-ML's does (#474).
    epoch_period: str | None = None


class DatePatternMapJson(DateMapJson, kw_only=True):
    source_pattern: str
    locale: str


class FrequencyFormatMappingJson(WireStruct, rename=_camel_rename):
    id: str
    frequency_id: str
    date_pattern: str
    annotations: list[Annotation] | None = None
    links: list[Link] | None = None


class FixedValueMapJson(WireStruct, rename=_camel_rename):
    """A source *or* a target component, as the schema's ``oneOf`` says, and its values."""

    source: str | None = None
    target: str | None = None
    values: list[str] | None = None
    annotations: list[Annotation] | None = None

    def __post_init__(self) -> None:
        """The schema's ``oneOf`` (#474).

        Raises:
            ValueError: If it names neither a source nor a target, or both.
        """
        one_of("a fixedValueMap", source=self.source, target=self.target)


class _DateMapJsonFields(TypedDict, closed=True):
    """The `DateMapJson` keywords, which each subclass adds its own beside (#422)."""

    id: str
    mapped_components: list[MappedComponentJson]
    resolve_period: str | None
    frequency_dimension: str | None
    mapped_frequencies: list[str] | None
    target_frequency_id: str | None
    annotations: list[Annotation] | None
    links: list[Link] | None


def _date_map_fields(m: EpochMap | DatePatternMap) -> _DateMapJsonFields:
    """What an epoch and a date-pattern map share on the wire (#422)."""
    return {
        "id": m.id,
        "mapped_components": [MappedComponentJson(source=s, target=t) for s, t in m.component_maps],
        "resolve_period": m.resolve_period.value if m.resolve_period is not None else None,
        "frequency_dimension": m.frequency_dimension,
        "mapped_frequencies": list(m.mapped_frequencies) or None,
        "target_frequency_id": m.target_frequency_id,
        "annotations": emit_annotations(m.annotations),
        "links": uri_links(m),
    }


def map_validity(day: date | None) -> str | None:
    """A map's validity date as SDMX-JSON's ``date-time``: midnight UTC (#496).

    SDMX-ML 3.0 types a value map's ``validFrom`` / ``validTo`` ``xs:date``;
    both SDMX-JSON schemas type them ``date-time``, which requires a time and
    a zone. The model holds the date, so the instant written is its start in
    UTC, and the reader refuses any other.
    """
    return f"{day.isoformat()}T00:00:00Z" if day is not None else None


def _item_map(m: ItemMap) -> ItemMapJson:
    """Model item map to its SDMX-JSON ``SingleValueMappingType`` form."""
    return ItemMapJson(
        source_value=MappedValueJson(value=m.source_id),
        target_value=m.target_id,
        annotations=emit_annotations(m.annotations),
        valid_from=map_validity(m.valid_from),
        valid_to=map_validity(m.valid_to),
    )


class ConceptSchemeMapJson(MaintainableJson):
    source: str | None = None
    target: str | None = None
    item_maps: list[ItemMapJson] | None = None


def to_concept_scheme_map(csm: ConceptSchemeMap) -> ConceptSchemeMapJson:
    return ConceptSchemeMapJson(
        **_maint_fields(csm),
        source=_ref_urn(csm.source),
        target=_ref_urn(csm.target),
        item_maps=[_item_map(m) for m in csm.maps] or None,
    )


class CategorySchemeMapJson(MaintainableJson):
    source: str | None = None
    target: str | None = None
    item_maps: list[ItemMapJson] | None = None


def to_category_scheme_map(catm: CategorySchemeMap) -> CategorySchemeMapJson:
    return CategorySchemeMapJson(
        **_maint_fields(catm),
        source=_ref_urn(catm.source),
        target=_ref_urn(catm.target),
        item_maps=[_item_map(m) for m in catm.maps] or None,
    )


class OrganisationSchemeMapJson(MaintainableJson):
    source: str | None = None
    target: str | None = None
    item_maps: list[ItemMapJson] | None = None


def to_organisation_scheme_map(catm: OrganisationSchemeMap) -> OrganisationSchemeMapJson:
    return OrganisationSchemeMapJson(
        **_maint_fields(catm),
        source=_ref_urn(catm.source),
        target=_ref_urn(catm.target),
        item_maps=[_item_map(m) for m in catm.maps] or None,
    )


class StructureMapJson(MaintainableJson):
    source: str | None = None
    target: str | None = None
    component_maps: list[ComponentMapJson] | None = None
    epoch_maps: list[EpochMapJson] | None = None
    date_pattern_maps: list[DatePatternMapJson] | None = None
    frequency_format_mappings: list[FrequencyFormatMappingJson] | None = None
    fixed_value_maps: list[FixedValueMapJson] | None = None


def to_structure_map(sm: StructureMap) -> StructureMapJson:
    return StructureMapJson(
        **_maint_fields(sm),
        source=_ref_urn(sm.source),
        target=_ref_urn(sm.target),
        component_maps=[
            ComponentMapJson(
                source=list(m.sources),
                target=list(m.targets),
                annotations=emit_annotations(m.annotations),
                representation_map=_ref_urn(m.representation_map),
            )
            for m in sm.maps
        ]
        or None,
        epoch_maps=[
            EpochMapJson(**_date_map_fields(m), base_period=m.base_period, epoch_period=m.epoch_period.value)
            for m in sm.epoch_maps
        ]
        or None,
        date_pattern_maps=[
            DatePatternMapJson(**_date_map_fields(m), source_pattern=m.source_pattern, locale=m.locale)
            for m in sm.date_pattern_maps
        ]
        or None,
        frequency_format_mappings=[
            FrequencyFormatMappingJson(
                id=m.id,
                frequency_id=m.frequency_id,
                date_pattern=m.date_pattern,
                annotations=emit_annotations(m.annotations),
                links=uri_links(m),
            )
            for m in sm.frequency_format_mappings
        ]
        or None,
        fixed_value_maps=[
            FixedValueMapJson(
                source=m.source_id,
                target=m.target_id,
                values=list(m.values) or None,
                annotations=emit_annotations(m.annotations),
            )
            for m in sm.fixed_value_maps
        ]
        or None,
    )


class MapEndpointJson(WireStruct, rename=_camel_rename):
    #: A RepresentationMap source/target entry: a codelist URN OR a data_type.
    codelist: str | None = None
    data_type: str | None = None

    def __post_init__(self) -> None:
        """The schema's ``oneOf`` (#474).

        Raises:
            ValueError: If it names neither a codelist nor a data type, or both.
        """
        one_of("a representation map's source or target", codelist=self.codelist, dataType=self.data_type)


def _map_endpoint(ep: MapEndpoint) -> MapEndpointJson:
    match ep:
        case MapDataType(data_type=data_type):
            return MapEndpointJson(data_type=data_type)
        case Ref(urn=urn):
            return MapEndpointJson(codelist=str(urn))


class MappedValueJson(WireStruct, rename=_camel_rename):
    #: A MappedValueType `source_values` entry — value plus the operation
    #: discriminator (is_reg_ex, or start_index/end_index substring slice).
    value: str
    is_reg_ex: bool | None = None
    start_index: int | None = None
    end_index: int | None = None


class ValueMappingJson(WireStruct, rename=_camel_rename):
    #: source_values carry the discriminated source; target_values are bare strings.
    #: Required by the schema, so non-defaulted (#474).
    source_values: list[MappedValueJson]
    target_values: list[str] = msgspec.field(default_factory=list)
    valid_from: str | None = None
    valid_to: str | None = None
    annotations: list[Annotation] | None = None


def to_value_mapping(m: RepresentationMapping) -> ValueMappingJson:
    return ValueMappingJson(
        source_values=[
            MappedValueJson(
                value=sv.value, is_reg_ex=sv.is_regex or None, start_index=sv.start_index, end_index=sv.end_index
            )
            for sv in m.sources
        ],
        target_values=list(m.targets),
        valid_from=map_validity(m.valid_from),
        valid_to=map_validity(m.valid_to),
        annotations=emit_annotations(m.annotations),
    )


class RepresentationMapJson(MaintainableJson):
    source: list[MapEndpointJson] | None = None
    target: list[MapEndpointJson] | None = None
    representation_mappings: list[ValueMappingJson] | None = None


def to_representation_map(rm: RepresentationMap) -> RepresentationMapJson:
    return RepresentationMapJson(
        **_maint_fields(rm),
        source=[_map_endpoint(ep) for ep in rm.sources] or None,
        target=[_map_endpoint(ep) for ep in rm.targets] or None,
        representation_mappings=[to_value_mapping(m) for m in rm.mappings] or None,
    )


# ── Message envelope ─────────────────────────────────────────────────────────


class StructureDataJson(WireStruct, rename=_camel_rename):
    """The ``data`` object of an SDMX-JSON 2.0 Structure message.

    One field per top-level artefact section, so the whole message decodes in
    a single typed pass instead of eighteen ``msgspec.convert`` calls against
    a ``dict[str, Any]``. msgspec ignores unknown keys, so a producer emitting
    a section sdmxlib does not model is tolerated rather than fatal.

    Every field defaults to ``None`` rather than ``[]``: SDMX-JSON omits absent
    sections, and ``None`` keeps "the producer said nothing" distinct from
    "the producer sent an empty list".
    """

    agency_schemes: list[AgencySchemeJson] | None = None
    data_provider_schemes: list[DataProviderSchemeJson] | None = None
    metadata_provider_schemes: list[MetadataProviderSchemeJson] | None = None
    data_consumer_schemes: list[DataConsumerSchemeJson] | None = None
    organisation_unit_schemes: list[OrganisationUnitSchemeJson] | None = None
    codelists: list[CodelistJson] | None = None
    geographic_codelists: list[GeographicCodelistJson] | None = None
    geo_grid_codelists: list[GeoGridCodelistJson] | None = None
    value_lists: list[ValueListJson] | None = None
    concept_schemes: list[ConceptSchemeJson] | None = None
    category_schemes: list[CategorySchemeJson] | None = None
    data_structures: list[DataStructureJson] | None = None
    dataflows: list[DataflowJson] | None = None
    categorisations: list[CategorisationJson] | None = None
    provision_agreements: list[ProvisionAgreementJson] | None = None
    data_constraints: list[DataConstraintJson] | None = None
    hierarchies: list[HierarchyJson] | None = None
    hierarchy_associations: list[HierarchyAssociationJson] | None = None
    metadata_structures: list[MetadataStructureJson] | None = None
    metadataflows: list[MetadataflowJson] | None = None
    metadata_provision_agreements: list[MetadataProvisionAgreementJson] | None = None
    representation_maps: list[RepresentationMapJson] | None = None
    structure_maps: list[StructureMapJson] | None = None
    concept_scheme_maps: list[ConceptSchemeMapJson] | None = None
    category_scheme_maps: list[CategorySchemeMapJson] | None = None
    organisation_scheme_maps: list[OrganisationSchemeMapJson] | None = None


class StructureMessageJson(WireStruct):
    """The outer envelope of an SDMX-JSON 2.0 Structure message.

    ``data`` is optional so that a message with no ``data`` key — or an
    explicit ``"data": null`` — decodes to an empty message rather than
    raising, matching the reader's previous ``doc.get("data") or {}``.
    """

    #: Read as untyped here so this module need not import the header's
    #: DTOs, which import it; `reader` decodes it typed (#480).
    #: Empty when the document has none: msgspec takes no `Raw | None`.
    meta: msgspec.Raw = msgspec.field(default_factory=msgspec.Raw)
    data: StructureDataJson | None = None
