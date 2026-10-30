"""Link — SDMX 3.x's pointer from an identifiable artefact to another resource (#422)."""

from collections.abc import Iterable

from attrs import field, frozen
from attrs.converters import optional

from sdmxlib.model.istring import normalize_tag


@frozen
class Link:
    """One ``<com:Link>``: a resource an identifiable artefact points at.

    SDMX 3.x's ``LinkType``, which ``IdentifiableType`` allows any number of —
    "if there is reference metadata associated with a structure, a link to the
    metadata report can be dynamically inserted in the structure metadata".
    SDMX-ML 2.1 has no such element, so a 2.1 write drops links (declared in
    `formats/gaps.py`). SDMX-JSON spells one a ``links`` entry, with ``url`` as
    ``href``.

    Distinct from ``@uri``, which is the artefact's single descriptive
    pointer, and from `Ref`: ``urn`` is an ``xs:anyURI``, "an SDMX registry
    urn of the object being linked (if applicable)", so it is held as text
    and is not an edge of the artefact graph.

    Attributes:
        rel: The type of object linked to — ``"metadata"`` for a
            reference-metadata report.
        url: The linked resource's URL.
        urn: The linked object's SDMX registry URN, where it has one.
        type: The kind of link, such as ``"PDF"`` or ``"text"``.
        hreflang: The language of the linked resource, normalised by
            `normalize_tag`; a malformed one raises `InvalidTagError`.
            SDMX-JSON's alone: SDMX-ML's ``LinkType`` has no language, so an
            SDMX-ML write drops it (declared in `formats/gaps.py`).
    """

    rel: str
    url: str
    urn: str | None = None
    type: str | None = None
    hreflang: str | None = field(default=None, converter=optional(normalize_tag))


def to_links(value: Iterable[Link] | None) -> tuple[Link, ...]:
    """Attrs converter: links in any iterable, each a `Link`.

    Raises:
        TypeError: If an element is not a `Link` — a bare string is
            refused, not read as one link per character.
    """
    # Before any call: this runs on every code and node a query builds, and
    # nearly all of them have none, so the empty case is one call a row (#422).
    if not value:
        return ()
    if isinstance(value, str | Link):
        msg = f"links must be an iterable of Link, not {type(value).__name__}"
        raise TypeError(msg)
    links = tuple(value)
    for link in links:
        if not isinstance(link, Link):
            msg = f"links must be an iterable of Link, got a {type(link).__name__}"
            raise TypeError(msg)
    return links
