"""Document-level round-trip fidelity: does `D -> model -> D` return `D`?

The round-trip properties elsewhere start from a generated *model artefact* and
check ``model -> D -> model``. That direction cannot see a whole class of bug:
a field the reader never populates and the writer never emits was never in the
generated artefact, so nothing looks for it. Starting from a *document* is what
makes the loss observable.

The rule this supports is stated in #237: reading a document and writing it back
to the same format must not lose or reshape information. Transforming the
document into sdmxlib's model is fine and is the library's job — the internal
shape is free to differ from any wire format. What is not fine is a
transformation that cannot be undone.

**Comparison is by identity, not position.** Two documents carrying the same
artefacts in a different section order carry the same information, so elements
are keyed by name plus whatever distinguishes them from their siblings (id,
agencyID, version, xml:lang). An earlier positional version of this reported a
hundred false losses the moment the writer emitted sections in its own order.

Test-only; not packaged with sdmxlib.
"""

import hashlib
from datetime import UTC, datetime

from lxml import etree

from tests.support.xsd_defaults import without_defaults

#: Attributes that identify an element among its siblings, so paths stay stable
#: when order changes.
_IDENTIFYING = ("id", "agencyID", "version", "{http://www.w3.org/XML/1998/namespace}lang")

#: Elements that describe the *transmission* rather than the artefacts, and so
#: are legitimately regenerated rather than preserved.
#:
#: The SDMX schemas are explicit: ``ID`` is "an identification for the message,
#: assigned by the sender", ``Prepared`` is "the date the message was
#: prepared", ``Sender`` is "the party that is transmitting the message". None
#: of it describes a codelist or a data structure. Parsing a message and
#: re-serialising it produces a *new* message carrying the same artefacts, and
#: giving that message its own header is correct rather than lossy.
#:
#: Scoped to structure and metadata messages, which is all sdmxlib writes
#: through this path. A *data* message header carries fields that do describe
#: the payload — ``Extracted``, ``ReportingBegin``/``ReportingEnd``,
#: ``EmbargoDate``, ``DataSetAction``, ``Source`` — and those would need
#: revisiting if data messages ever come through here.
_ENVELOPE_ELEMENTS = frozenset({"Header"})

#: Attributes that are not content. ``schemaLocation`` is a hint to a validator
#: about where to fetch the XSD; it says nothing about the artefacts.
_NOT_CONTENT = frozenset({"schemaLocation", "noNamespaceSchemaLocation"})


def _local(name: object) -> str:
    text = str(name)
    return etree.QName(text).localname if "}" in text else text


def _identity(el: etree._Element) -> str:
    """Element name plus the attributes that distinguish it from its siblings."""
    bits = [_local(el.tag)]
    bits.extend(f"{_local(key)}={value}" for key in _IDENTIFYING if (value := el.get(key)) is not None)
    return "|".join(bits)


def _comparable_attributes(el: etree._Element) -> dict[str, str]:
    """The element's attributes, normalised so equivalent forms compare equal.

    Drops non-content hints and
    drops ``urn`` when it is exactly the URN the other identifying attributes
    already imply — a redundant restatement, not extra information. A ``urn``
    that says something *different* is kept, because then it is not redundant.
    """
    out: dict[str, str] = {}
    for raw_key, value in el.attrib.items():
        key = _local(raw_key)
        if key in _NOT_CONTENT:
            continue
        if key == "urn" and _restates_identity(el, value):
            continue
        out[key] = _normalise_value(value)
    return out


def _normalise_value(value: str) -> str:
    """A lexical form reduced to its value space: a number, then an instant."""
    return _normalise_instant(_normalise_number(value))


def _normalise_instant(value: str) -> str:
    """Collapse date-times that name the same instant.

    The model holds every datetime tagged UTC (#440, `_time.as_utc`), reading
    an unqualified ``xs:dateTime`` as UTC — the reading `_time` already chose
    for the store (#321). So ``2020-01-01T00:00:00`` comes back written as
    ``2020-01-01T00:00:00+00:00``: the same instant, a different spelling. A
    *different* instant still differs.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return parsed.isoformat()


def _normalise_number(value: str) -> str:
    """Collapse decimals that differ only in lexical form.

    ``xs:decimal`` defines equality on the *value space*, so ``15`` and
    ``15.0`` are the same number written two ways. sdmxlib parses facet bounds
    into floats and re-emits them canonically, which changes the spelling and
    nothing else; treating that as a loss would be reporting a difference the
    format says does not exist.
    """
    try:
        number = float(value)
    except ValueError:
        return value
    return f"{number:g}"


def _restates_identity(el: etree._Element, urn: str) -> bool:
    """Whether *urn* merely repeats identity the element already carries.

    Two shapes. A *maintainable* carries id, agencyID and version, and its URN
    ends with ``=AGENCY:ID(VERSION)``. An *item* — a Code, a Concept — carries
    only an id, because the rest of its identity comes from the scheme it sits
    in; its URN ends with ``.ID``.

    The item case is the one that matters in practice: real registry output
    stamps a urn on every code, and the ECB data-structure fixture alone has
    2542 of them. Treating those as content would drown every genuine finding
    in redundancy.
    """
    artefact_id = el.get("id")
    if not artefact_id:
        return False
    agency = el.get("agencyID")
    if agency:
        version = el.get("version")
        return urn.endswith(f"={agency}:{artefact_id}" + (f"({version})" if version else ""))
    # An item's URN ends `.ID`, qualified by the scheme it sits in. An
    # *Agency* is the exception: it is its own maintainer, so its URN ends
    # `=ID` with no scheme prefix — `...base.Agency=ECB` for `<Agency
    # id="ECB"/>`. Both restate the element's own id and add nothing.
    return urn.endswith((f".{artefact_id}", f"={artefact_id}"))


def _has_indistinguishable_sibling(el: etree._Element) -> bool:
    """Whether *el* shares its identity with a sibling."""
    parent = el.getparent()
    if parent is None:
        return False
    mine = _identity(el)
    return sum(1 for sib in parent if isinstance(sib.tag, str) and _identity(sib) == mine) > 1


def _content_key(el: etree._Element) -> str:
    """A stable digest of the element's whole subtree."""
    parts = [_local(el.tag), repr(sorted(_comparable_attributes(el).items())), (el.text or "").strip()]
    parts.extend(_content_key(child) for child in el if isinstance(child.tag, str))
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:12]


def signature(blob: bytes) -> dict[str, tuple[tuple[tuple[str, str], ...], str]]:
    """An identity-keyed map of every element in *blob* to its content.

    Args:
        blob: A serialised SDMX-ML document.

    Returns:
        ``{path: (attributes, text)}``, where *path* is built from element
        identities rather than positions.
    """
    out: dict[str, tuple[tuple[tuple[str, str], ...], str]] = {}
    # A document that omits a default and one that states it are the same
    # document: both read at the minimal spelling, each default the one its
    # element's type declares, in context (#503).
    _walk(without_defaults(etree.fromstring(blob)), "", out, {})
    return out


def _walk(
    el: etree._Element,
    path: str,
    out: dict[str, tuple[tuple[tuple[str, str], ...], str]],
    seen: dict[str, int],
) -> None:
    key = f"{path}/{_identity(el)}"
    if _has_indistinguishable_sibling(el):
        # Siblings with nothing to key on — cube-region <Value>s, annotations,
        # unkeyed refs. Disambiguating them by *position* would report a
        # reordering as a wholesale rewrite: the ECB dataflow's constraint
        # values come back as the same set in a different order, and a
        # positional key turned that into 139 phantom differences.
        #
        # So they are keyed by their own content instead, which makes the
        # comparison a multiset: same values in any order match, a changed or
        # dropped value still does not. Nothing in the SDMX schemas gives
        # meaning to the order of such a list — where SDMX does care about
        # order it says so with an explicit `position` attribute.
        key = f"{key}~{_content_key(el)}"
    seen[key] = seen.get(key, 0) + 1
    if seen[key] > 1:
        key = f"{key}#{seen[key]}"
    out[key] = (tuple(sorted(_comparable_attributes(el).items())), _normalise_instant((el.text or "").strip()))
    child_seen: dict[str, int] = {}
    for child in el:
        if isinstance(child.tag, str) and _local(child.tag) not in _ENVELOPE_ELEMENTS:
            _walk(child, key, out, child_seen)


def compare(original: bytes, rewritten: bytes) -> tuple[list[str], list[str]]:
    """What *rewritten* loses or alters relative to *original*.

    Args:
        original: The document that was read.
        rewritten: The result of parsing it and writing it back to the same
            format.

    Returns:
        ``(missing, changed)`` — paths present in *original* and absent from
        *rewritten*, and paths present in both whose content differs. Paths
        *added* by the rewrite are not reported: emitting a derivable extra is
        not a loss, and the rule under test is about losing information.
    """
    before, after = signature(original), signature(rewritten)
    missing = [key for key in before if key not in after]
    changed = [key for key in before if key in after and before[key] != after[key]]
    return missing, changed
