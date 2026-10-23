"""Convert domain objects to/from plain Python dicts.

The dict format mirrors the domain model field names. It is suitable for
JSON serialization, config files, and round-tripping through databases.

Example:
    import sdmxlib as sl

    d = sl.to_dict(dsd)
    dsd2 = sl.from_dict(sl.DataStructure, d)

    # Round-trip through JSON
    import json
    json_str = json.dumps(sl.to_dict(cl), indent=2)
    cl2 = sl.from_dict(sl.Codelist, json.loads(json_str))
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Required, TypedDict, TypeIs

import attrs

from sdmxlib.model.annotations import Annotation, Annotations, AnnotationURL
from sdmxlib.model.category import Categorisation, Category, CategoryScheme
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import (
    AttachmentLevel,
    Attribute,
    DataStructure,
    Dimension,
    Group,
    Measure,
    TimeDimension,
)
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.organisation import (
    Agency,
    AgencyScheme,
    DataConsumer,
    DataConsumerScheme,
    DataProvider,
    DataProviderScheme,
    MetadataProviderScheme,
    OrganisationUnit,
    OrganisationUnitScheme,
)
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from sdmxlib.model.urn import SdmxUrn

# ── Field name registries ─────────────────────────────────────────────────────

# Fields that hold a Ref[T] and serialize as a URN string
_REF_FIELDS: frozenset[str] = frozenset(
    {"code", "concept", "structure", "dataflow", "data_provider", "source", "target"}
)

# List fields → element type for recursive from_dict
_LIST_ELEMENT_TYPES: dict[str, type] = {
    "codes": Code,
    "concepts": Concept,
    "dimensions": Dimension,
    "attributes": Attribute,
    "measures": Measure,
    "groups": Group,
    "categories": Category,
    "codelists": Codelist,
    "concept_schemes": ConceptScheme,
    "data_structures": DataStructure,
    "dataflows": Dataflow,
    "category_schemes": CategoryScheme,
    "categorisations": Categorisation,
    "provision_agreements": ProvisionAgreement,
    "agencies": Agency,
    "providers": DataProvider,
    "agency_schemes": AgencyScheme,
    "data_provider_schemes": DataProviderScheme,
    "metadata_provider_schemes": MetadataProviderScheme,
    "consumers": DataConsumer,
    "data_consumer_schemes": DataConsumerScheme,
    "organisation_units": OrganisationUnit,
    "organisation_unit_schemes": OrganisationUnitScheme,
    "tree": HierarchicalCode,
    "children": HierarchicalCode,
    "hierarchies": Hierarchy,
}

# ── to_dict ───────────────────────────────────────────────────────────────────


class AnnotationURLDict(TypedDict, total=False):
    """One `AnnotationURL` as a plain dict: its URL, and its language, URN and type when it has them."""

    url: Required[str]
    lang: str
    urn: str
    type: str


class AnnotationDict(TypedDict, total=False):
    """The serialised form of an `Annotation` — five optional keys.

    Unlike the representation payload below, this shape is *written out* by
    hand in both directions, so a `TypedDict` binds the two halves together:
    a key renamed in `_annotation_to_dict` and not in `_parse_annotation` is
    now a type error rather than a field that silently stops round-tripping
    (#285).
    """

    id: str
    title: str
    type: str
    urls: list[AnnotationURLDict]
    text: dict[str, str]


def _representation_to_dict(rep: Ref[object] | Facet | None) -> dict[str, Any] | None:
    """Serialise a representation. Deliberately `Any`, not a `TypedDict`.

    The Facet branch walks ``attrs.fields(Facet)`` and `_parse_representation`
    mirrors it with ``Facet(**kwargs)`` — both are generic over whatever fields
    the model declares. A `TypedDict` would have to restate all twelve and
    would go stale the moment `Facet` gains a thirteenth, trading a real
    drift risk for type information neither side actually consumes (#285).
    """
    if rep is None:
        return None
    if isinstance(rep, Ref):
        return {"kind": "Enumeration", "urn": str(rep.urn)}
    # Facet
    d: dict[str, Any] = {"kind": "Facet"}
    for f in attrs.fields(type(rep)):
        val = getattr(rep, f.name)
        if val is not None:
            d[f.name] = val
    return d if len(d) > 1 else {"kind": "Facet"}


def _annotation_to_dict(ann: Annotation) -> AnnotationDict:
    d: AnnotationDict = {}
    if ann.id is not None:
        d["id"] = ann.id
    if ann.title is not None:
        d["title"] = ann.title
    if ann.type is not None:
        d["type"] = ann.type
    if ann.urls:
        d["urls"] = [_annotation_url_to_dict(u) for u in ann.urls]
    if ann.text:
        d["text"] = {lang: ann.text[lang] for lang in ann.text}
    return d


def _annotation_url_to_dict(url: AnnotationURL) -> AnnotationURLDict:
    d: AnnotationURLDict = {"url": url.url}
    if url.lang is not None:
        d["lang"] = url.lang
    if url.urn is not None:
        d["urn"] = url.urn
    if url.type is not None:
        d["type"] = url.type
    return d


def _is_attrs_instance(val: object) -> TypeIs[attrs.AttrsInstance]:
    """Runtime attrs-instance test that also narrows for the type checker.

    ``attrs.has`` takes a *class*, so ``attrs.has(type(val))`` narrows the
    discarded ``type(val)`` expression and leaves ``val`` untouched — a dead
    TypeGuard of exactly the kind the pyrefly 1.2 upgrade turned up. Wrapping
    it in a `TypeIs` puts the narrowing back on the value.
    """
    return not isinstance(val, type) and attrs.has(type(val))


def _value_to_dict(field_name: str, val: object) -> object:
    """Convert a single field value to a serializable form."""
    if val is None:
        return None
    if isinstance(val, InternationalString):
        d = {lang: val[lang] for lang in val}
        return d or None
    if isinstance(val, Annotations):
        items = [_annotation_to_dict(a) for a in val]
        return items or None
    if isinstance(val, Ref):
        # A Ref in a `representation` slot is an enumeration, not an edge.
        return _representation_to_dict(val) if field_name == "representation" else str(val.urn)
    if isinstance(val, Facet):
        return _representation_to_dict(val)
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, AttachmentLevel):
        return val.value
    if _is_attrs_instance(val):
        return to_dict(val)
    if isinstance(val, Iterable) and not isinstance(val, str):
        return [to_dict(item) if _is_attrs_instance(item) else item for item in val] or None
    return val


def to_dict(artefact: attrs.AttrsInstance) -> dict[str, object]:
    """Convert a domain object to a nested plain dict.

    - `InternationalString` → ``dict[str, str]``
    - `Ref[T]` → URN string (or ``{"kind": "Enumeration", "urn": "..."}`` for representation fields)
    - `Facet` → ``{"kind": "Facet", ...}``
    - `ItemList`/`ArtefactList` → ``list[dict]``
    - `Annotations` → ``list[dict]`` (omitted when empty)
    - `Agency` maintainer → ``"agency_id": str`` (serialized as plain ID string)
    - Enums → string value
    - ``None`` fields are omitted

    Example:
        d = to_dict(dsd)
        json.dumps(d, indent=2)
    """
    result: dict[str, object] = {}
    for f in attrs.fields(type(artefact)):
        if not f.init:
            continue
        val = getattr(artefact, f.name)
        if f.name == "maintainer" and isinstance(val, Agency):
            result["agency_id"] = val.id
            continue
        converted = _value_to_dict(f.name, val)
        if converted is not None:
            result[f.name] = converted
    return result


# ── from_dict ─────────────────────────────────────────────────────────────────


def _parse_representation(data: dict[str, Any]) -> Ref[object] | Facet:
    """Rebuild a representation. `Any` for the same reason its twin is.

    ``Facet(**kwargs)`` is generic over whatever fields the model declares;
    see `_representation_to_dict` (#285).
    """
    kind = data.get("kind", "Facet")
    if kind == "Enumeration":
        return Ref.unresolved(SdmxUrn.parse(data["urn"]))
    # Facet — pass all keys except "kind"
    kwargs = {k: v for k, v in data.items() if k != "kind"}
    return Facet(**kwargs)


def _parse_annotation(data: AnnotationDict) -> Annotation:
    text_data = data.get("text")
    text = InternationalString.of(text_data) if text_data else InternationalString()
    return Annotation(
        id=data.get("id"),
        title=data.get("title"),
        type=data.get("type"),
        urls=[AnnotationURL(u["url"], u.get("lang"), u.get("urn"), u.get("type")) for u in data.get("urls", [])],
        text=text,
    )


def _coerce_field(field_name: str, val: object) -> object:
    """Coerce a raw dict/string value to the domain type for a given field name."""
    if val is None:
        return None

    # URN → unresolved Ref
    if field_name in _REF_FIELDS and isinstance(val, str):
        return Ref.unresolved(SdmxUrn.parse(val))

    # Representation dict
    if field_name == "representation" and isinstance(val, dict):
        return _parse_representation(val)

    # datetime strings (valid_from, valid_to)
    if field_name in {"valid_from", "valid_to"} and isinstance(val, str):
        return datetime.fromisoformat(val)

    # Attachment enum
    if field_name == "attachment" and isinstance(val, str):
        return AttachmentLevel(val)

    # Annotations list
    if field_name == "annotations" and isinstance(val, list):
        return [_parse_annotation(item) for item in val]

    # Known list → recurse into element type
    if field_name in _LIST_ELEMENT_TYPES and isinstance(val, list):
        elem_cls = _LIST_ELEMENT_TYPES[field_name]
        return [from_dict(elem_cls, item) for item in val]

    # TimeDimension dict
    if field_name == "time_dimension" and isinstance(val, dict):
        return from_dict(TimeDimension, val)

    return val


def from_dict[T](cls: type[T], data: dict[str, Any]) -> T:
    """Construct a domain object from a nested plain dict.

    Handles:
    - ``dict`` name/description fields → coercion via ``__post_init__``
    - URN strings for Ref fields → ``Ref.unresolved``
    - Representation dicts → ``Ref[Codelist]`` or ``Facet``
    - List of child dicts → recursive ``from_dict`` per element type
    - Attachment strings → ``AttachmentLevel``
    - Annotation dicts → ``Annotation`` instances

    The resulting Refs are unresolved (URN only). To resolve them, use
    a Registry or pass the object through a StructureMessage parser.

    Example:
        cl = from_dict(Codelist, {"id": "CL_FREQ", "agency_id": "SDMX",
                                   "name": {"en": "Frequency"},
                                   "codes": [{"id": "A"}]})
        cl.codes["A"]  # Code(id="A")

    ``data`` is `dict[str, Any]` rather than `dict[str, object]` because the
    payload's shape depends on *cls* — every value flows into a differently
    typed constructor parameter, so there is no single narrowing that would
    serve. This is the generic deserialiser; the typed entry points are the
    per-format readers built over it.
    """
    # Probe through a separate handle: `attrs.has` is a TypeGuard onto
    # `type[AttrsInstance]`, and narrowing `cls` itself would erase `T` from
    # the constructor call at the bottom of this function.
    attrs_cls: type = cls
    if not attrs.has(attrs_cls):
        msg = f"{cls} is not an attrs class"
        raise TypeError(msg)

    # to_dict() emits maintainer.id as "agency_id" for ergonomics; reverse
    # that mapping here so the dict round-trips into the attrs constructor.
    if "agency_id" in data and "maintainer" not in data:
        data = {**data, "maintainer": data["agency_id"]}

    fields = {f.name for f in attrs.fields(attrs_cls) if f.init}
    kwargs: dict[str, Any] = {}
    for key, val in data.items():
        if key not in fields:
            continue
        kwargs[key] = _coerce_field(key, val)

    return cls(**kwargs)
