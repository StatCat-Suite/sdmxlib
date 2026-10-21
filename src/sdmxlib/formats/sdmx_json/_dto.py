"""Shared SDMX-JSON 2.0 wire Structs and helpers (msgspec).

Foundation of the msgspec migration (milestone: SDMX-JSON dict+orjson →
typed msgspec Structs). Holds the cross-cutting wire types every artefact DTO
reuses — annotations, links, the message ``meta`` envelope — plus the
``InternationalString`` ↔ ``(name, names)`` convention, the annotation
convert helpers, and the shared encoder. The per-artefact DTOs (Codelist,
DataStructure, …) live in the writer/reader modules and build on these.

Conventions for wire Structs — read before adding one
-----------------------------------------------------

- **Inherit `WireStruct`.** It carries ``omit_defaults=True`` + ``gc=False``
  for every DTO (add ``rename=_camel_rename`` in the subclass when the wire
  wants camelCase). ``gc=False`` opts these transient, tree-shaped structs out
  of the cyclic collector — a large encode speedup — and is safe only because
  wire DTOs never form reference cycles (see `WireStruct`).

- **``omit_defaults=True``** (from `WireStruct`): a field whose value equals
  its default is skipped on encode, matching SDMX-JSON's "omit optional when
  absent".

- **A field that must ALWAYS appear on the wire is REQUIRED — no default.**
  This is the subtle one. With ``omit_defaults`` a field equal to its default
  is *dropped*; if you give an artefact's ``version`` a default of ``"1.0"`` it
  silently vanishes whenever the value is ``"1.0"``. So ``id`` / ``agencyID`` /
  ``version`` and any other always-present field must be declared without a
  default (required). ``test_dto.py`` locks this with a guard test.

- **Unknown fields are ignored on decode** (msgspec's default) — forward-
  compatible with producer extensions.

- **Wire names that differ from Python** use ``rename=`` (e.g. ``agencyID``).

- **An ``InternationalString`` splits into ``name: str`` (primary /
  default-language) + ``names: dict[str, str]`` (full map)** per the SDMX-JSON
  2.0 contract. Use `emit_istr` / `ingest_istr` — never hand-roll the split.
  Annotations use the same pattern under ``text`` / ``texts``.
"""

from datetime import datetime

import msgspec

from sdmxlib.model.annotations import Annotation as _ModelAnnotation
from sdmxlib.model.annotations import Annotations, AnnotationURL
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.validation import SdmxConformanceError, _first_xml_incompatible

# Sentinel language used when a producer sends a singular ``name`` with no
# ``names`` map — we can't know the real language tag, so we mirror the
# dict-reader's behaviour and key it under ``_default``.
_DEFAULT_LANG = "_default"


# ── Wire-name convention ─────────────────────────────────────────────────────
# Struct fields are snake_case; `rename=_camel_rename` maps them to the SDMX-JSON
# camelCase wire names (msgspec's equivalent of pydantic's alias_generator).


#: Fields whose wire name cannot be derived from the Python name by
#: camel-casing, and what the wire calls them.
#:
#: Both entries exist because the *spec* spells something differently from
#: idiomatic Python, in opposite directions: `agencyID` keeps the SDMX-IM
#: acronym, and `isMultiLingual` splits a word English writes as one. The
#: second was found the hard way — renaming `is_multi_lingual` to
#: `is_multilingual` silently re-derived the wire name as `isMultilingual`,
#: which is a wire break that reads as a Python tidy-up.
_WIRE_NAMES = {
    "agency_id": "agencyID",
    "is_multilingual": "isMultiLingual",
}


def _camel_rename(name: str) -> str:
    """snake_case field → SDMX-JSON wire name.

    camelCase, except the `_WIRE_NAMES` overrides. Matches msgspec's built-in
    ``"camel"`` for every other field. msgspec calls this once per field at
    class definition, so an override that stops matching a field name is
    silent — `tests/unit/test_sdmx_json_wire_names.py` is what makes it loud.
    """
    override = _WIRE_NAMES.get(name)
    if override is not None:
        return override
    head, *tail = name.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in tail)


# ── InternationalString ↔ (name, names) ─────────────────────────────────────


def emit_istr(v: InternationalString | None) -> tuple[str | None, dict[str, str] | None]:
    """Split an ``InternationalString`` into ``(primary, full map)`` for a Struct.

    Returns ``(None, None)`` for an empty/absent istring so ``omit_defaults``
    drops both the singular and plural fields. Delegates to
    `InternationalString.emit_pair`, which does the empty-check + best-match +
    backing-map fetch in one call (the map is shared, not copied: it feeds a
    transient DTO that msgspec encodes and we then discard, so it is never
    mutated).
    """
    return v.emit_pair() if v is not None else (None, None)


def ingest_istr(name: str | None, names: dict[str, str] | None) -> dict[str, str]:
    """Reconstruct a lang→text map from a wire ``(name, names)`` pair.

    Prefers the full ``names`` map. Falls back to ``{_DEFAULT_LANG: name}`` when
    only the singular ``name`` is present (a producer that omitted ``names``),
    mirroring the dict-reader. Empty when neither is present. The caller builds
    an ``InternationalString`` from the result.

    Rejects text XML cannot represent. JSON permits ``\u0000`` in a string and
    XML does not, so without this check the two readers disagreed about what a
    valid SDMX message is: SDMX-ML input was rejected by the parser while the
    same content arrived intact through SDMX-JSON, producing an artefact that
    could never be written back as SDMX-ML. Strict at the read boundary, as
    #190 established for non-conformant references.

    Args:
        name: The singular ``name`` field, if present.
        names: The full language map, if present.

    Returns:
        The lang→text map.

    Raises:
        SdmxConformanceError: If any value contains a character XML forbids.
    """
    out = names or ({_DEFAULT_LANG: name} if name else {})
    for lang, text in out.items():
        bad = _first_xml_incompatible(text)
        if bad is not None:
            where = f"name[{lang}]"
            detail = (
                f"contains U+{ord(bad):04X}, which XML cannot represent. SDMX string "
                "types are XSD types, so this value has no valid SDMX-ML form — JSON "
                "accepts it but the artefact could never be serialised back to XML."
            )
            raise SdmxConformanceError(where, detail)
    return out


# ── Wire Struct base ─────────────────────────────────────────────────────────


class WireStruct(msgspec.Struct, omit_defaults=True, gc=False):
    """Base for every SDMX-JSON wire DTO.

    Carries the two config knobs every DTO wants:

    - ``omit_defaults=True`` — a field equal to its default is skipped on encode
      (matches SDMX-JSON's "omit optional when absent"). Subclasses that also
      need camelCase wire names add ``rename=_camel_rename``; it composes with
      this base's config down the MRO.
    - ``gc=False`` — opt these structs out of CPython's *cyclic* collector.
      Reference counting still frees them promptly (they are transient: built
      per-serialise, encoded, discarded), so nothing leaks; skipping cyclic-GC
      tracking removes the dominant cost of building millions of them.

    Invariant that makes ``gc=False`` safe: **wire DTOs are trees** — parent →
    children only, built fresh from the (acyclic) domain model, never holding a
    back-reference to an ancestor or sibling. A reference *cycle* would leak
    under ``gc=False`` (refcounting alone can't break one). Do not introduce
    one.
    """


def one_of(where: str, **given: object) -> None:
    """Raise unless exactly one of `given` is set: the schema's ``oneOf`` over required keys (#474).

    Called from a struct's ``__post_init__``, where msgspec turns the
    `ValueError` into the `msgspec.ValidationError` the reader refuses an
    entry for, so a rule the schema states is refused as a missing key is.

    Raises:
        ValueError: If none, or more than one, of `given` is set.
    """
    present = [name for name, value in given.items() if value is not None]
    if len(present) != 1:
        msg = f"{where} needs exactly one of {', '.join(given)}; it has {', '.join(present) or 'none'}"
        raise ValueError(msg)


class Link(WireStruct):
    """A SDMX-JSON ``links[*]`` entry. Annotation URLs ride here under ``rel=self``."""

    href: str = ""
    rel: str = ""
    #: Present on artefact-level links that point at another SDMX artefact
    #: (e.g. the ``rel="metadata"`` back-link to a MetadataSet report).
    urn: str | None = None
    #: The language of the linked resource — an annotation URL's ``xml:lang``
    #: in SDMX-ML (#422).
    hreflang: str | None = None
    #: The kind of link — SDMX-ML's ``Link/@type`` (#422).
    type: str | None = None

    def __post_init__(self) -> None:
        """The schema's rule: a ``rel``, and an ``href`` or a ``urn`` (#474).

        Raises:
            ValueError: If the link breaks it.
        """
        if not self.rel:
            msg = "a link needs a rel"
            raise ValueError(msg)
        if not self.href and self.urn is None:
            msg = "a link needs an href or a urn"
            raise ValueError(msg)


class Annotation(WireStruct):
    """SDMX-JSON 2.0 ``annotations[*]``. ``text``/``texts`` carry the localised body."""

    id: str | None = None
    title: str | None = None
    type: str | None = None
    text: str | None = None
    texts: dict[str, str] | None = None
    #: The annotation URL lives in ``links`` under ``rel="self"``; a bare ``url``
    #: is tolerated on decode for non-conformant producers.
    url: str | None = None
    links: list[Link] | None = None
    #: SDMX ``AnnotationValue`` — the non-localised body, distinct from
    #: ``text``/``texts`` (#368). In the schema for 2.0 and 2.1, absent from 1.0.
    value: str | None = None


class Sender(WireStruct):
    """The ``meta.sender`` object."""

    id: str = ""


class Meta(WireStruct):
    """The SDMX-JSON ``meta`` envelope object.

    `schema` comes first because readers recognise a message by it, and some
    look only at the start of the document: pysdmx reads the first 350
    characters (#465).
    """

    schema: str | None = None
    id: str = ""
    prepared: str = ""
    sender: Sender | None = None


def prepared_text(prepared: datetime) -> str:
    """A caller's `prepared` instant as the envelope's ``date-time`` (#496).

    Every SDMX-JSON schema requires ``prepared`` to include a time zone, and a
    naive `datetime` has none to write, so it is refused rather than guessed.

    Raises:
        ValueError: If *prepared* is naive.
    """
    if prepared.utcoffset() is None:
        msg = f"prepared must be timezone-aware, got naive {prepared.isoformat()!r}; SDMX-JSON requires a zone"
        raise ValueError(msg)
    return prepared.isoformat()


# ── Annotation ↔ model ───────────────────────────────────────────────────────


def emit_annotations(annotations: Annotations | None) -> list[Annotation] | None:
    """Model ``Annotations`` → wire ``list[Annotation]``, or ``None`` when empty."""
    if not annotations:
        return None
    out: list[Annotation] = []
    for a in annotations:
        text, texts = emit_istr(a.text)
        links = [Link(rel="self", href=u.url, hreflang=u.lang, urn=u.urn, type=u.type) for u in a.urls] or None
        out.append(Annotation(id=a.id, title=a.title, type=a.type, text=text, texts=texts, links=links, value=a.value))
    return out


def _annotation_urls(a: Annotation) -> list[AnnotationURL]:
    """The annotation's URLs: each of its ``links`` with an ``href``, else a bare ``url``.

    Every link, whatever its ``rel``: an annotation's links *are* its
    ``AnnotationURL``s, each localised by ``hreflang`` and carrying the
    link's ``urn`` and ``type`` (#422).
    """
    urls = [AnnotationURL(link.href, link.hreflang, link.urn, link.type) for link in a.links or [] if link.href]
    if not urls and a.url:
        urls.append(AnnotationURL(a.url))
    return urls


#: The `Annotations` of everything read with none. It holds an immutable
#: tuple, so one instance serves them all: building one per item cost ~10 ms
#: per 100,000 codes (#466).
_NO_ANNOTATIONS = Annotations()


def ingest_annotations(annotations: list[Annotation] | None) -> Annotations:
    """Wire ``list[Annotation]`` → model ``Annotations``."""
    if not annotations:
        return _NO_ANNOTATIONS
    return Annotations(
        [
            _ModelAnnotation(
                id=a.id,
                title=a.title,
                type=a.type,
                urls=_annotation_urls(a),
                text=ingest_istr(a.text, a.texts),
                value=a.value,
            )
            for a in annotations
        ]
    )


# ── Shared encoder ───────────────────────────────────────────────────────────
# One reusable encoder; decode is per-type via ``msgspec.json.decode(data, type=T)``.
ENCODER = msgspec.json.Encoder()
