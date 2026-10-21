"""Per-item SDMX-JSON 2.0 value serialisers for Codelists and ConceptSchemes.

The full structure-message writer in sdmxlib.formats.sdmx_json.writer
builds entire envelopes. These helpers expose just the per-Code and
per-Concept dict shape so external producers (cache pre-bakers) and
consumers (response hydrators) can share one source of truth for the
codelists[*].codes[*] and conceptSchemes[*].concepts[*] entry bodies.

What this module owns:

- serialize_code / serialize_codes — Code → SDMX-JSON value entry
- serialize_concept / serialize_concepts — Concept → entry

What it does NOT own:

- The structure-envelope (data block, header, links) — see writer.write.
- Codelist filtering (detail=referencepartial) — consumer policy.
- Codelist/ConceptScheme hydration into a response — consumer policy.
"""

from typing import NotRequired, Required, TypedDict

from sdmxlib.formats.sdmx_json._dto import emit_istr
from sdmxlib.model.annotations import Annotation, AnnotationURL
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet

# ── Wire shapes ──────────────────────────────────────────────────────────────
#
# These are TypedDicts rather than msgspec Structs because the module's whole
# point is to hand *plain dicts* to external producers and consumers. A
# TypedDict is a dict at runtime, so the contract is unchanged — it is now
# merely checkable, which is what "one source of truth for the entry bodies"
# was always claiming to be.


class LinkJson(TypedDict):
    """An ``annotations[*].links[*]`` entry."""

    rel: str
    href: str
    hreflang: NotRequired[str]
    urn: NotRequired[str]
    type: NotRequired[str]


class AnnotationJson(TypedDict, total=False):
    """An ``annotations[*]`` entry. Every field is optional."""

    id: str
    title: str
    type: str
    text: str
    texts: dict[str, str]
    value: str
    links: list[LinkJson]


class EnumerationRepresentationJson(TypedDict):
    """``coreRepresentation`` for an enumerated concept — a codelist URN."""

    enumeration: str


class FormatRepresentationJson(TypedDict):
    """``coreRepresentation`` for a free-text concept — its facet bounds."""

    format: dict[str, str | int | float | bool]


class CodeJson(TypedDict, total=False):
    """A ``codelists[*].codes[*]`` entry."""

    id: Required[str]
    name: str
    names: dict[str, str]
    description: str
    descriptions: dict[str, str]
    parent: str
    annotations: list[AnnotationJson]


class ConceptJson(TypedDict, total=False):
    """A ``conceptSchemes[*].concepts[*]`` entry."""

    id: Required[str]
    name: str
    names: dict[str, str]
    description: str
    descriptions: dict[str, str]
    coreRepresentation: EnumerationRepresentationJson | FormatRepresentationJson
    annotations: list[AnnotationJson]


def _annotations(artefact: Code | Concept) -> list[AnnotationJson] | None:
    """Render ``artefact.annotations``, or None when it carries none.

    Returning rather than mutating the caller's dict: a TypedDict cannot be
    written through a computed key, and the value form is what every caller
    wanted anyway.
    """
    if not artefact.annotations:
        return None
    return [annotation_dict(a) for a in artefact.annotations]


def annotation_dict(ann: Annotation) -> AnnotationJson:
    """Render one ``Annotation`` as a SDMX-JSON 2.0 ``annotations[*]`` entry.

    Plainly named rather than underscored because the *data* writers render
    the same object into their own annotation pool (#366) — a package-internal
    API in the sense of the `_name` rules, not a published one: it is
    deliberately absent from this package's ``__all__``.

    Follows the structure-schema shape: ``id`` / ``title`` / ``type``
    scalars (omitted when None), ``text`` + ``texts`` for the localised
    body, and the annotation ``url`` as a ``links`` entry with
    ``rel: "self"`` (per the schema's Annotation.links contract).
    """
    out: AnnotationJson = {}
    if ann.id is not None:
        out["id"] = ann.id
    if ann.title is not None:
        out["title"] = ann.title
    if ann.type is not None:
        out["type"] = ann.type
    text, texts = emit_istr(ann.text)
    if text is not None and texts is not None:
        out["text"] = text
        out["texts"] = texts
    if ann.value is not None:
        # The non-localised body (#368) — a sibling of ``text``/``texts``, not a
        # replacement: an annotation may carry both.
        out["value"] = ann.value
    if ann.urls:
        out["links"] = [_annotation_link(u) for u in ann.urls]
    return out


def _annotation_link(url: AnnotationURL) -> LinkJson:
    link: LinkJson = {"rel": "self", "href": url.url}
    if url.lang is not None:
        link["hreflang"] = url.lang
    if url.urn is not None:
        link["urn"] = url.urn
    if url.type is not None:
        link["type"] = url.type
    return link


def serialize_code(code: Code) -> CodeJson:
    """Render one ``Code`` as a SDMX-JSON ``codelists[*].codes[*]`` entry.

    Fields emitted (each optional except ``id``):

    - ``id``
    - ``name`` / ``names`` (primary + full multilingual map)
    - ``description`` / ``descriptions``
    - ``parent`` (parent code id)

    Args:
        code: The Code to render.

    Returns:
        The wire entry body.
    """
    obj: CodeJson = {"id": code.id}
    name, names = emit_istr(code.name)
    if name is not None and names is not None:
        obj["name"] = name
        obj["names"] = names
    desc, descs = emit_istr(code.description)
    if desc is not None and descs is not None:
        obj["description"] = desc
        obj["descriptions"] = descs
    if code.parent_id is not None:
        obj["parent"] = code.parent_id
    anns = _annotations(code)
    if anns is not None:
        obj["annotations"] = anns
    return obj


def serialize_codes(codelist: Codelist) -> list[CodeJson]:
    """Render every ``Code`` in ``codelist`` to its SDMX-JSON entry."""
    return [serialize_code(c) for c in codelist.codes]


def _core_representation(
    rep: object,
) -> EnumerationRepresentationJson | FormatRepresentationJson | None:
    """Render a concept's core representation, or None when it has none."""
    if isinstance(rep, Ref):
        return {"enumeration": str(rep.urn)}
    if not isinstance(rep, Facet):
        return None
    pairs: list[tuple[str, str | int | float | bool | None]] = [
        ("dataType", rep.type),
        ("maxLength", rep.max_length),
        ("minLength", rep.min_length),
        ("maxValue", rep.max_value),
        ("minValue", rep.min_value),
        ("decimals", rep.decimals),
        ("pattern", rep.pattern),
        ("startValue", rep.start_value),
        ("endValue", rep.end_value),
        ("isSequence", rep.is_sequence),
        ("isMultiLingual", rep.is_multilingual or None),
    ]
    tf = {k: v for k, v in pairs if v is not None}
    return {"format": tf} if tf else None


def serialize_concept(concept: Concept) -> ConceptJson:
    """Render one ``Concept`` as a SDMX-JSON ``conceptSchemes[*].concepts[*]`` entry.

    Fields emitted (each optional except ``id``):

    - ``id``
    - ``name`` / ``names``
    - ``description`` / ``descriptions``
    - ``coreRepresentation`` (enumeration URN or text ``format``)

    Args:
        concept: The Concept to render.

    Returns:
        The wire entry body.
    """
    obj: ConceptJson = {"id": concept.id}
    name, names = emit_istr(concept.name)
    if name is not None and names is not None:
        obj["name"] = name
        obj["names"] = names
    desc, descs = emit_istr(concept.description)
    if desc is not None and descs is not None:
        obj["description"] = desc
        obj["descriptions"] = descs
    rep = _core_representation(concept.core_representation)
    if rep is not None:
        obj["coreRepresentation"] = rep
    anns = _annotations(concept)
    if anns is not None:
        obj["annotations"] = anns
    return obj


def serialize_concepts(scheme: ConceptScheme) -> list[ConceptJson]:
    """Render every ``Concept`` in ``scheme`` to its SDMX-JSON entry."""
    return [serialize_concept(c) for c in scheme.concepts]
