"""Constraints extracted from the vendored SDMX-ML schemas.

Every conformance bug found in this milestone was a constraint the XSD states
and the model does not enforce — and each was found the slow way, by generating
an artefact and watching schema validation reject it. Enumerations
(`DataType.URL`), pattern facets (`NCNameIDType`), fixed values
(`AgencyScheme.id`), one at a time.

This module inverts that. Rather than discovering constraints by tripping over
them, it *reads them out of the schemas* so the model can be checked against
the whole set at once. The schemas hold roughly 300 pattern facets, 237
enumerations and 19 fixed attributes per version; nobody is going to state
those by hand, and they are the point of vendoring the XSDs in the first place.

Two properties make this worth having over more generated round-trips:

- **It is exhaustive.** A generated artefact exercises the constraints it
  happens to touch. This sees all of them, including the ones no strategy
  currently reaches.
- **It costs nothing to maintain.** Refreshing the vendored schemas changes
  the facts, and a test comparing model to facts reports the difference — which
  is exactly what you want to know when SDMX-TWG publishes a new tag.

Test-only; not packaged with sdmxlib.
"""

import functools
import pathlib

import attrs
from lxml import etree

from tests.support.xsd import SchemaVersion

_SCHEMA_DIR = {"2.1": "sdmx_ml21", "3.0": "sdmx_ml30", "3.1": "sdmx_ml31"}
_XS = "{http://www.w3.org/2001/XMLSchema}"


def _roots(version: SchemaVersion) -> list[etree._Element]:
    directory = pathlib.Path(__file__).parent.parent / "schemas" / _SCHEMA_DIR[version]
    return [etree.parse(str(p)).getroot() for p in sorted(directory.glob("*.xsd"))]


@functools.cache
def enumerations(version: SchemaVersion) -> dict[str, frozenset[str]]:
    """Named simple types to the set of values their enumeration facets allow.

    Args:
        version: SDMX-ML version.

    Returns:
        ``{simple type name: {allowed values}}``, only for types that restrict
        by enumeration.
    """
    out: dict[str, frozenset[str]] = {}
    for root in _roots(version):
        for simple_type in root.iter(f"{_XS}simpleType"):
            name = simple_type.get("name")
            if name is None:
                continue
            values = {e.get("value") for e in simple_type.iter(f"{_XS}enumeration")}
            if values:
                out[name] = frozenset(v for v in values if v is not None)
    return out


@functools.cache
def patterns(version: SchemaVersion) -> dict[str, str]:
    """Named simple types to their single pattern facet, where they have one.

    Args:
        version: SDMX-ML version.

    Returns:
        ``{simple type name: regex}``. Types with several patterns are skipped
        rather than guessed at.
    """
    out: dict[str, str] = {}
    for root in _roots(version):
        for simple_type in root.iter(f"{_XS}simpleType"):
            name = simple_type.get("name")
            if name is None:
                continue
            found = [p.get("value") for p in simple_type.iter(f"{_XS}pattern")]
            if len(found) == 1 and found[0] is not None:
                out[name] = found[0]
    return out


@functools.cache
def fixed_attributes(version: SchemaVersion) -> dict[tuple[str, str], str]:
    """Attributes the schema pins to one literal value.

    These are the constraints most easily violated in code, because nothing
    about the model hints that the field is not free — ``AgencyScheme.id`` must
    be ``AGENCIES``, ``PrimaryMeasure/@id`` must be ``OBS_VALUE``.

    Args:
        version: SDMX-ML version.

    Returns:
        ``{(complex type name, attribute name): fixed value}``. The complex
        type is the nearest named ancestor, or ``"?"`` when the attribute sits
        inside an anonymous one.
    """
    out: dict[tuple[str, str], str] = {}
    for root in _roots(version):
        for attribute in root.iter(f"{_XS}attribute"):
            fixed = attribute.get("fixed")
            name = attribute.get("name")
            if fixed is None or name is None:
                continue
            owner = "?"
            for ancestor in attribute.iterancestors(f"{_XS}complexType"):
                if ancestor.get("name"):
                    owner = ancestor.get("name", "?")
                    break
            out[owner, name] = fixed
    return out


def all_versions() -> tuple[SchemaVersion, ...]:
    """The SDMX-ML versions with vendored schemas."""
    return ("2.1", "3.0", "3.1")


@functools.cache
def _named_complex_types(version: SchemaVersion) -> dict[str, etree._Element]:
    """Every named ``xs:complexType`` in the version, by name."""
    out: dict[str, etree._Element] = {}
    for root in _roots(version):
        for complex_type in root.iter(f"{_XS}complexType"):
            name = complex_type.get("name")
            if name is not None:
                out[name] = complex_type
    return out


def _element_name(element: etree._Element) -> str | None:
    """Local name of an element particle, whether declared inline or by ref."""
    name = element.get("name")
    if name is not None:
        return name
    ref = element.get("ref")
    return ref.split(":")[-1] if ref is not None else None


def _is_strictly_required(element: etree._Element, stop: etree._Element) -> bool:
    """Whether *element* must appear, given the particles between it and *stop*.

    An element is only strictly required when nothing on the path to its type
    makes it avoidable:

    - its own ``minOccurs`` is not ``0``;
    - no ancestor particle is an ``xs:choice`` — a branch is by definition
      optional, since another branch may be taken instead;
    - no ancestor particle carries ``minOccurs="0"`` — an optional group makes
      everything inside it optional too.

    Skipping this walk is what makes a naive extractor over-report, and an
    over-reporting table is worse than none: it produces refusals for documents
    that are perfectly valid.
    """
    if element.get("minOccurs") == "0":
        return False
    node = element.getparent()
    while node is not None and node is not stop:
        if node.tag == f"{_XS}choice" or node.get("minOccurs") == "0":
            return False
        node = node.getparent()
    return True


@functools.cache
def required_children(version: SchemaVersion) -> dict[str, tuple[str, ...]]:
    """Complex types to the child elements a document must carry.

    Follows ``complexContent/extension`` into the base type, since SDMX puts
    most cardinality in a ``…BaseType``. A ``complexContent/restriction``
    *redefines* the content model rather than adding to it, so its base is not
    followed — `GenericMetadataStructureType` restricts `MetadataStructureType`
    down to a three-way choice, and inheriting the base's particles would
    describe a type that does not exist.

    **This under-reports on purpose.** It answers "must this child always
    appear", not "must it appear given some other child does". SDMX-ML 3.0's
    `MetadataSetType` wraps its whole sequence in ``minOccurs="0"``: a set may
    carry nothing at all, but one that names a structure must also name a
    Target. Conditional requirements like that are real and are enforced in
    `sdmxlib.model.validation`; they are simply not what this extraction
    describes, and reporting them here would mean guessing at the condition.

    Args:
        version: SDMX-ML version.

    Returns:
        ``{complex type name: (required child element names, in order)}``,
        omitting types with none.
    """
    types = _named_complex_types(version)

    @functools.cache
    def resolve(name: str, seen: tuple[str, ...] = ()) -> tuple[str, ...]:
        complex_type = types.get(name)
        if complex_type is None or name in seen:
            return ()
        inherited: tuple[str, ...] = ()
        for extension in complex_type.iter(f"{_XS}extension"):
            base = extension.get("base")
            if base is not None and extension.getparent() is not None:
                inherited = resolve(base.split(":")[-1], (*seen, name))
            break
        own = tuple(
            child_name
            for element in complex_type.iter(f"{_XS}element")
            if (child_name := _element_name(element)) is not None and _is_strictly_required(element, complex_type)
        )
        return inherited + own

    return {name: children for name in types if (children := resolve(name))}


@functools.cache
def required_choices(version: SchemaVersion) -> dict[str, tuple[tuple[str, ...], ...]]:
    """Complex types to the alternative sets a document must pick from.

    The companion to `required_children`, and not derivable from it. A required
    ``xs:choice`` obliges a document to carry *one of* its branches while no
    single branch is itself required — so an extraction that only asks "must
    this child always appear" reports nothing at all for it.

    That gap is not academic. Six of the twenty-two SDMX-ML entries in the
    conformance gate's `KNOWN_INVALID` are exactly this shape: a
    ``ConstraintAttachment`` with none of its target elements, and a 3.0
    ``RepresentationMap`` with neither a source codelist nor a source value
    list. `required_children` explains the other sixteen and is silent on
    these.

    Args:
        version: SDMX-ML version.

    Returns:
        ``{complex type name: ((alternative element names), ...)}``, one tuple
        per required choice, omitting types with none.
    """
    types = _named_complex_types(version)

    @functools.cache
    def resolve(name: str, seen: tuple[str, ...] = ()) -> tuple[tuple[str, ...], ...]:
        complex_type = types.get(name)
        if complex_type is None or name in seen:
            return ()
        inherited: tuple[tuple[str, ...], ...] = ()
        for extension in complex_type.iter(f"{_XS}extension"):
            base = extension.get("base")
            if base is not None:
                inherited = resolve(base.split(":")[-1], (*seen, name))
            break
        own: list[tuple[str, ...]] = []
        for choice in complex_type.iter(f"{_XS}choice"):
            if choice.get("minOccurs") == "0" or not _is_strictly_required(choice, complex_type):
                continue
            branches = tuple(
                child_name
                for element in choice.iterchildren(f"{_XS}element")
                if (child_name := _element_name(element)) is not None
            )
            if len(branches) > 1:
                own.append(branches)
        return inherited + tuple(own)

    return {name: choices for name in types if (choices := resolve(name))}


def _attribute_owner(attribute: etree._Element) -> str:
    """Nearest named complex type enclosing *attribute*, or ``"?"``."""
    for ancestor in attribute.iterancestors(f"{_XS}complexType"):
        name = ancestor.get("name")
        if name:
            return name
    return "?"


@functools.cache
def _attributes_by_use(version: SchemaVersion, use: str) -> dict[str, frozenset[str]]:
    """Complex types to the attributes declared with ``use=<use>``."""
    out: dict[str, set[str]] = {}
    for root in _roots(version):
        for attribute in root.iter(f"{_XS}attribute"):
            name = attribute.get("name")
            if name is None or attribute.get("use") != use:
                continue
            out.setdefault(_attribute_owner(attribute), set()).add(name)
    return {owner: frozenset(names) for owner, names in out.items()}


def required_attributes(version: SchemaVersion) -> dict[str, frozenset[str]]:
    """Complex types to the attributes a document must carry.

    A required attribute the writer omits produces an invalid document — the
    same failure as a missing child element, and 2.1's
    ``LocalComponentListComponentRefType`` is the live example: it requires
    ``containerID``, ``class`` and ``package`` on every component reference,
    which `ComponentMap` does not model (#226).

    Args:
        version: SDMX-ML version.

    Returns:
        ``{complex type name: {attribute names}}``.
    """
    return _attributes_by_use(version, "required")


def prohibited_attributes(version: SchemaVersion) -> dict[str, frozenset[str]]:
    """Complex types to the attributes a document may **not** carry.

    The silent-loss kind, and the reason this is extracted separately from
    everything else: a correct writer must *drop* a prohibited attribute, so
    whatever the model held is gone with no error, no warning, and a document
    that validates. Schema validation cannot catch it — the output is
    conformant precisely because the value was discarded.

    Two are live. SDMX-ML 3.1 prohibits ``Categorisation/@version``, and 3.0
    *and* 3.1 prohibit it on ``OrganisationSchemeBaseType`` — so an
    AgencyScheme or DataProviderScheme version survives 2.1 (where it is fixed
    to ``1.0``) and SDMX-JSON, and is lost by both 3.x writers.

    Args:
        version: SDMX-ML version.

    Returns:
        ``{complex type name: {attribute names}}``.
    """
    return _attributes_by_use(version, "prohibited")


@functools.cache
def id_attribute_type(version: SchemaVersion, type_name: str) -> str | None:
    """The simple type a complex type's ``@id`` is declared as.

    Follows ``extension`` and ``restriction`` bases, since SDMX declares ``@id``
    once on a base type and inherits it nearly everywhere. Returns the local
    name — ``"IDType"``, ``"NCNameIDType"`` — or None if the type declares no
    id anywhere in its chain.

    The distinction is load-bearing and easy to get backwards. Only three
    maintainables restrict ``@id`` to ``NCNameIDType``; the rest inherit
    ``MaintainableBaseType/@id``, which is the permissive ``IDType``. Applying
    the strict form to all of them made sdmxlib refuse real ECB registry
    output, whose categorisation ids are UUIDs.

    Args:
        version: SDMX-ML version.
        type_name: Complex type to inspect.

    Returns:
        Local name of the id's simple type, or None.
    """
    return _id_type(version, type_name, ())


def _id_type(version: SchemaVersion, type_name: str, seen: tuple[str, ...]) -> str | None:
    types = _named_complex_types(version)
    complex_type = types.get(type_name)
    if complex_type is None or type_name in seen:
        return None
    for attribute in complex_type.iter(f"{_XS}attribute"):
        if attribute.get("name") == "id" and attribute.get("type"):
            return attribute.get("type", "").split(":")[-1]
    for tag in (f"{_XS}extension", f"{_XS}restriction"):
        for node in complex_type.iter(tag):
            base = node.get("base")
            if base:
                return _id_type(version, base.split(":")[-1], (*seen, type_name))
    return None


@attrs.frozen
class UniqueConstraint:
    """One ``xs:unique`` identity constraint, as the schema states it."""

    #: Element the constraint hangs off — ``Codelists``, ``Codelist``, ….
    owner: str
    #: The constraint's own ``@name``, e.g. ``UniqueCodelist``.
    name: str
    #: ``xs:selector/@xpath`` — which descendants the constraint ranges over.
    selector: str
    #: ``xs:field/@xpath`` values, in document order — what must be unique.
    fields: tuple[str, ...]


@functools.cache
def unique_constraints(version: SchemaVersion) -> tuple[UniqueConstraint, ...]:
    """Every ``xs:unique`` in the schemas for *version*.

    These are the constraint kind the other extractors here miss, and the one
    easiest to miss reading a schema by eye: an identity constraint sits
    *beside* an element declaration rather than inside its type, so
    `CodelistType` says nothing about `UniqueCodelist` hanging off
    ``<Codelists>``. `DataConstraint_CubeRegionInclusion` was found in
    milestone 10 only because a generated constraint happened to draw two
    regions with the same ``include`` flag.

    Args:
        version: SDMX-ML version.

    Returns:
        Constraints in document order, deduplicated — the same element is
        declared in more than one schema file in places.
    """
    found: dict[tuple[str, str], UniqueConstraint] = {}
    for root in _roots(version):
        for node in root.iter(f"{_XS}unique"):
            parent = node.getparent()
            name = node.get("name")
            if parent is None or name is None:
                continue
            selector = node.find(f"{_XS}selector")
            found.setdefault(
                (parent.get("name") or "?", name),
                UniqueConstraint(
                    owner=parent.get("name") or "?",
                    name=name,
                    selector="" if selector is None else (selector.get("xpath") or ""),
                    fields=tuple(f.get("xpath") or "" for f in node.findall(f"{_XS}field")),
                ),
            )
    return tuple(found.values())
