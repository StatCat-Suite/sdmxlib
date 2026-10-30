"""What a value alone decides, refused as the field is set (#492).

An SDMX id, agency id, version or name, or a facet's bound, is valid or not
by itself, with no need to see the rest of the artefact. Those rules used to run only in
`validate()`, which binds only where something calls it: a store read trusted
its rows, so a value the rules refuse could reach the model and stay there
until a write found it. As attrs field validators they run on construction,
and on `attrs.evolve` — the model is frozen, so there is no assignment — so
no path builds such a value.

Here, below the model classes, because `sdmxlib.model.validation` imports
them. What depends on more than one value, such as unique item ids or a parent
that exists, runs once the whole artefact is built (`_invariants`).

Which grammar applies is decided by position, as the XSD decides it:

- ``IDType`` (``[A-Za-z0-9_@$-]+``): most maintainables, and items and
  components.
- ``NCNameIDType`` (``[A-Za-z][A-Za-z0-9_-]*``): Codelist, ConceptScheme and
  CategoryScheme, whose ids become XML names.
- ``NestedNCNameIDType`` (NCNames, dot-separated): the maintainer's agency id.

A test that needs a value these refuse, to check what refuses it later, builds
it inside ``attrs.validators.disabled()`` (`tests/support/unchecked.py`).
"""

import re
from typing import Protocol

import attrs

from sdmxlib.model.errors import ValidationError, ValidationIssue
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.version import compliance

ID_PATTERN = r"^[A-Za-z0-9_@$-]+$"
NCNAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]*$"
AGENCY_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z][A-Za-z0-9_-]*)*$"
ID_RE = re.compile(ID_PATTERN)
NCNAME_RE = re.compile(NCNAME_PATTERN)
AGENCY_RE = re.compile(AGENCY_PATTERN)
#: Printable ASCII with a non-space in it: a name `named` passes without a scan.
PLAIN_NAME_RE = re.compile(r" *[!-~][ -~]*")


def id_problem(value: str) -> str | None:
    """Why *value* is not an ``IDType``, or None if it is."""
    if not value:
        return "id must not be empty"
    if not ID_RE.match(value):
        return f"id {value!r} contains invalid characters (allowed: A-Za-z0-9_@$-)"
    return None


def ncname_id_problem(value: str) -> str | None:
    """Why *value* is not an ``NCNameIDType``, or None if it is.

    Stricter than `id_problem`: it must start with a letter, and ``@`` / ``$``
    are not allowed, because these ids become XML names in structure-specific
    schemas.
    """
    if not value:
        return "id must not be empty"
    if not NCNAME_RE.match(value):
        return (
            f"id {value!r} is not a valid NCName (must start with a letter, then "
            "A-Za-z0-9_- only) — SDMX restricts this position to NCNameIDType "
            "because the id becomes an XML name"
        )
    return None


def agency_id_problem(value: str) -> str | None:
    """Why *value* is not a ``NestedNCNameIDType``, or None if it is."""
    if not value:
        return "agency_id must not be empty"
    if not AGENCY_RE.match(value):
        return (
            f"agency_id {value!r} is not valid — SDMX types it NestedNCNameIDType: "
            "dot-separated segments, each starting with a letter, then A-Za-z0-9_- only"
        )
    return None


def version_problem(value: str) -> str | None:
    """Why *value* is a version **neither** SDMX-ML grammar allows (#319), or None.

    The union, not one of them, because a store is format-agnostic: it holds
    what a registry published, and which format that came from — or will be
    written to — is not its business. Checking 2.1's grammar alone refused
    `1.0.0-draft`, which is conformant 3.0.

    The union is deliberately *looser* than either grammar: it accepts
    `1.2.3.4-draft`, which conforms to nothing. The writer catches that —
    `version.check_writable` refuses whatever the target format cannot
    express, which is where the question has an answer.
    """
    if compliance(value):
        return None
    return (
        f"version {value!r} conforms to no SDMX-ML version: expected digits separated "
        "by dots (2.1), or one to three parts with an optional prerelease "
        "such as '1.0.0-draft' (3.0)."
    )


XML_FORBIDDEN = frozenset(
    [chr(c) for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)]
    + [chr(c) for c in range(0xD800, 0xE000)]  # lone surrogates
    + ["\ufffe", "\uffff"]
)


def first_xml_incompatible(text: str) -> str | None:
    """The first XML-incompatible character in *text*, or None.

    Args:
        text: Candidate string.

    Returns:
        The offending character, or None if the text is representable in XML.
    """
    # Printable ASCII holds none of the forbidden characters, and the two C
    # checks are ~6x faster than the scan on real labels (#466). Tab, LF and
    # CR are allowed but not printable, so text holding them is scanned.
    if text.isascii() and text.isprintable():
        return None
    # isdisjoint short-circuits and costs ~150 ns on a typical label, so the
    # scan is affordable on every name of every artefact (~22 ms for the
    # 78k-code StatCan geography codelist).
    if XML_FORBIDDEN.isdisjoint(text):
        return None
    return next(c for c in text if c in XML_FORBIDDEN)


def name_problem(name: InternationalString) -> tuple[str, str] | None:
    """Where and why *name* names nothing, as ``(path, message)``, or None if it names.

    SDMX types every maintainable and every item-scheme item as
    ``NameableArtefact``, whose ``<com:Name>`` carries no ``minOccurs``, so a
    name is required. A language whose text is blank names nothing in it
    (#452): ``TextType`` is ``xs:string``, so the schema admits it, but a
    strict registry treats it as missing — FMR refused a codelist for 498,786
    codes named ``""``.
    """
    if not name:
        return "name", "name is required (SDMX NameableArtefact)"
    for lang, text in name._backing().items():
        # The character first: Python strips some XML-forbidden characters as
        # whitespace (U+000B, U+000C), and "cannot be written" is the finding.
        bad = first_xml_incompatible(text)
        if bad is not None:
            return f"name[{lang}]", (
                f"contains U+{ord(bad):04X}, which XML cannot represent — "
                "SDMX string types are XSD types, so this value has no valid "
                "SDMX-ML form in any version"
            )
        if not text.strip():
            return f"name[{lang}]", "name is blank in this language; a name must name"
    return None


def _refuse(instance: object, path: str, problem: str | None) -> None:
    if problem is not None:
        raise ValidationError([ValidationIssue(path=f"{type(instance).__name__}.{path}", message=problem)])


def id_type(instance: object, attribute: attrs.Attribute[str], value: str) -> None:
    """Field validator: the field is an ``IDType``.

    Raises:
        ValidationError: Naming the class and field, if it is not.
    """
    # The common case returns after one regex call: this runs once per item
    # built, 579,951 times for the census geography codelist, and the bundle
    # tier's per-row call budget (`test_collect_stays_close_to_frame`) counts it.
    if value and ID_RE.match(value):
        return
    _refuse(instance, attribute.name, id_problem(value))


def ncname_id_type(instance: object, attribute: attrs.Attribute[str], value: str) -> None:
    """Field validator: the field is an ``NCNameIDType``.

    Raises:
        ValidationError: Naming the class and field, if it is not.
    """
    if value and NCNAME_RE.match(value):
        return
    _refuse(instance, attribute.name, ncname_id_problem(value))


def version_type(instance: object, attribute: attrs.Attribute[str], value: str) -> None:
    """Field validator: either SDMX-ML grammar accepts the version.

    Raises:
        ValidationError: Naming the class and field, if neither does.
    """
    _refuse(instance, attribute.name, version_problem(value))


def named(instance: object, _attribute: attrs.Attribute[InternationalString], value: InternationalString) -> None:
    """Field validator: the name names something, in every language it has.

    Raises:
        ValidationError: Naming the class and the language, if it does not.
    """
    # The common case: printable ASCII that is not all spaces holds no
    # forbidden character, and one regex call per language says so. This runs
    # once per item built — 579,951 times for the census geography codelist —
    # and the bundle tier's per-row call budget counts it: a generator over
    # three string checks cost 13 calls a row there, this costs 5.
    texts = value._backing()
    if texts:
        for text in texts.values():
            if not PLAIN_NAME_RE.fullmatch(text):
                break
        else:
            return
    problem = name_problem(value)
    if problem is not None:
        path, message = problem
        raise ValidationError([ValidationIssue(path=f"{type(instance).__name__}.{path}", message=message)])


def attachment_stated(instance: object, attribute: attrs.Attribute[object], value: object) -> None:
    """Field validator: an attribute says what it attaches to (#421, #508).

    Required as well as checked: the field has no default, so leaving it out
    is a `TypeError` at the call. This catches the explicit ``None`` — a store
    row written before the field was required, or a caller passing one
    through — since attrs checks no types.

    Raises:
        ValidationError: If *value* is ``None``.
    """
    if value is None:
        _refuse(
            instance,
            attribute.name,
            "every SDMX format requires an attribute's relationship, but attachment is None; "
            "set it — AttachmentLevel.DATASET for one reported once per data set",
        )


def positive_integer(instance: object, attribute: attrs.Attribute[int | None], value: int | None) -> None:
    """Field validator: the field is absent or an ``xs:positiveInteger``.

    A facet's ``decimals``, ``maxLength`` and ``minLength``: every SDMX-ML
    schema types them ``xs:positiveInteger``, and every SDMX-JSON schema an
    integer with a minimum of 1, so 0 has no form in any format (#224).

    Raises:
        ValidationError: Naming the class and field, if it is below 1.
    """
    if value is not None and value < 1:
        _refuse(
            instance,
            attribute.name,
            f"{attribute.name} must be a positive integer (SDMX types it xs:positiveInteger), got {value}",
        )


def some_measures(
    instance: object, attribute: attrs.Attribute[tuple[str, ...] | None], value: tuple[str, ...] | None
) -> None:
    """Field validator: an attribute's measure relationship names a measure, or is unset (#497).

    The IM relates an attribute "to one or more Measures", and the lack of a
    relationship "defaults to a relationship to all Measures" (SDMX 3.1,
    Section 2, lines 1402-1408); SDMX-ML 3.x requires a ``<Measure>`` and
    SDMX-JSON gives ``measureRelationship`` ``minItems: 1``. So an empty one
    names nothing any format can write — and reading it as unset would widen
    it to every measure, silently.

    Raises:
        ValidationError: Naming the class and field, if it is empty.
    """
    if value is not None and not value:
        _refuse(
            instance,
            attribute.name,
            f"{attribute.name} is empty, which no SDMX format can write: a measure relationship names one "
            "or more measures, and an unset one (None) relates the attribute to every measure",
        )


class _HasId(Protocol):
    """What a maintainer is, to its validator: something with an id."""

    @property
    def id(self) -> str:
        """The maintainer's id."""
        ...


def maintained_by[T: _HasId](instance: object, _attribute: attrs.Attribute[T], value: T) -> None:
    """Field validator: the maintainer's id is a ``NestedNCNameIDType``.

    Named ``agency_id`` in what it raises, the property every maintainable
    reads it through.

    Raises:
        ValidationError: Naming the class and ``agency_id``, if it is not.
    """
    _refuse(instance, "agency_id", agency_id_problem(value.id))
