"""Refuse an SDMX-ML artefact holding an element its schema allows nowhere there (#436).

The readers fetch the children they know by qualified name and never look at
the rest, so an element they do not expect vanished silently. Eurostat's 3.0
AgencyScheme ships ``<str:Name>`` where the schema requires ``<com:Name>``: the
reader found no name, and ``validate()`` then refused the scheme for having
none — reporting the symptom two steps from its cause. An injected
``<str:Bogus>`` disappeared the same way, without even that.

`check_children` closes the world: every element in an artefact's subtree must
be one the schema allows under its parent, per `_allowed_children` — a table
generated from the vendored XSDs. It is lenient where the schema is
context-sensitive (a parent name's children are the union over every context
that declares it), so what it refuses is an element *no* context allows.

The same walk closes it for attributes (#446): an attribute the schema allows
on that element nowhere refuses the artefact, as an element does. A misspelt
optional one (``validfrom``) was read past, the value lost. ``xsi:``
attributes are always allowed; an element with ``xs:anyAttribute`` admits any.

Nothing under a parent the table does not know is checked. Under a known
parent such an element is itself the finding, and the walk stops there; the
only way to reach one is through an open (``xs:any``) parent, whose content
the schema deliberately leaves free, so its whole subtree goes unchecked too.

`check_children` walks artefact subtrees — what `build_each_xml` hands over.
The frame around them, the message header and the ``Structures`` containers
between, has no artefact to refuse, so `frame_refusals` checks it separately
and names what it refuses by its place (#447).
"""

from typing import Literal

from lxml import etree

from sdmxlib.formats.sdmx_ml._allowed_children import ALLOWED, ATTRIBUTES, NAMESPACES, OPEN, OPEN_ATTRIBUTES
from sdmxlib.model.errors import Refusal
from sdmxlib.model.validation import SdmxConformanceError

type SdmxMlVersion = Literal["2.1", "3.0", "3.1"]

_VERSIONS: tuple[SdmxMlVersion, ...] = ("2.1", "3.0", "3.1")


def _clark(name: str, namespaces: dict[str, str]) -> str:
    """The table's ``prefix:local`` → Clark notation, as lxml spells ``.tag``."""
    prefix, sep, local = name.partition(":")
    return f"{{{namespaces[prefix]}}}{local}" if sep else name


#: Per version: the table re-keyed by Clark notation, once at import. The walk
#: then compares ``.tag`` directly — it visits every element of a document,
#: and spelling each one in ``prefix:local`` first cost a fifth of the parse.
_ALLOWED_CLARK: dict[str, dict[str, frozenset[str]]] = {
    v: {
        _clark(parent, NAMESPACES[v]): frozenset(_clark(c, NAMESPACES[v]) for c in children)
        for parent, children in ALLOWED[v].items()
    }
    for v in _VERSIONS
}
_OPEN_CLARK: dict[str, frozenset[str]] = {v: frozenset(_clark(p, NAMESPACES[v]) for p in OPEN[v]) for v in _VERSIONS}

_XML_NS = "http://www.w3.org/XML/1998/namespace"
_XSI = "{http://www.w3.org/2001/XMLSchema-instance}"


def _clark_attribute(name: str, namespaces: dict[str, str]) -> str:
    """The table's attribute name as lxml spells an attribute key: ``xml:lang`` qualified, the rest bare."""
    if name.startswith("xml:"):
        return f"{{{_XML_NS}}}{name[4:]}"
    return _clark(name, namespaces)


#: Per version: element (Clark) → its allowed attributes as lxml keys them,
#: once at import, like `_ALLOWED_CLARK`. Attribute-open elements are left out,
#: so a missing entry means "not checked".
_ATTRIBUTES_CLARK: dict[str, dict[str, frozenset[str]]] = {
    v: {
        _clark(element, NAMESPACES[v]): frozenset(_clark_attribute(a, NAMESPACES[v]) for a in names)
        for element, names in ATTRIBUTES[v].items()
        if element not in OPEN_ATTRIBUTES[v]
    }
    for v in _VERSIONS
}

#: Per version: namespace URI → the table's prefix, to name a finding.
_PREFIX: dict[str, dict[str, str]] = {v: {uri: p for p, uri in ns.items()} for v, ns in NAMESPACES.items()}

#: Structure namespace URI → its version: an artefact element names its own.
BY_STRUCTURE_NS: dict[str, SdmxMlVersion] = {NAMESPACES[v]["str"]: v for v in _VERSIONS}

#: Message namespace URI → its version: ``mes:Structures`` names the frame's.
_BY_MESSAGE_NS: dict[str, SdmxMlVersion] = {NAMESPACES[v]["mes"]: v for v in _VERSIONS}


def check_artefact(el: etree._Element) -> None:
    """`check_children` for an artefact element, at the version its namespace names.

    The 2.1, 3.0 and 3.1 structure namespaces are distinct, so the element
    says which table governs it and no reader has to thread its version here.

    Args:
        el: A maintainable artefact element (``str:Codelist`` and so on).

    Raises:
        SdmxConformanceError: If the subtree holds an element the schema does
            not allow under its parent.
    """
    version = BY_STRUCTURE_NS.get(etree.QName(el).namespace or "")
    if version is not None:
        check_children(el, version)


def frame_refusals(structures: etree._Element) -> tuple[list[Refusal], set[etree._Element]]:
    """What the frame around the artefacts holds that its schema allows nowhere (#447).

    The frame is the message header and the ``Structures`` containers. A stray
    element there is inside no artefact, so it is refused by its place, as
    3.1's identity-less availability constraints are: ``mes:Structures/str:Bogus[1]``.
    A container the schema allows nowhere, or that carries an attribute it does
    not allow, is refused whole, its contents with it; a stray child of a known
    container is refused alone. Only the frame is walked: the artefacts below
    are `check_children`'s, when they are built.

    Args:
        structures: The document's ``mes:Structures`` element.

    Returns:
        The refusals, and the elements they cover, which the account must not
        account for again.
    """
    version = _BY_MESSAGE_NS.get(etree.QName(structures).namespace or "")
    if version is None:
        return [], set()
    refusals: list[Refusal] = []
    covered: set[etree._Element] = set()
    message = structures.getparent()
    header = None if message is None else message.find(_clark("mes:Header", NAMESPACES[version]))
    if header is not None:
        try:
            check_children(header, version)
        except SdmxConformanceError as exc:
            refusals.append(Refusal(artefact_class="mes:Header", urn="mes:Header", reason=str(exc)))
    containers = _ALLOWED_CLARK[version].get(str(structures.tag), frozenset())
    for container in structures:
        if not isinstance(container.tag, str):
            continue  # a comment or processing instruction
        if container.tag not in containers:
            refusals.append(_placed(container, _unexpected(container, version), version))
            covered.add(container)
            continue
        on = _ATTRIBUTES_CLARK[version].get(container.tag, frozenset())
        stray = [name for name in container.attrib if name not in on and not name.startswith(_XSI)]
        if stray:
            refusals.append(_placed(container, _unexpected_attribute(container, stray[0], version), version))
            covered.add(container)
            continue
        allowed = _ALLOWED_CLARK[version].get(container.tag, frozenset())
        for child in container:
            if isinstance(child.tag, str) and child.tag not in allowed:
                refusals.append(_placed(child, _unexpected(child, version), version))
                covered.add(child)
    return refusals, covered


def _placed(el: etree._Element, exc: SdmxConformanceError, version: SdmxMlVersion) -> Refusal:
    """A refusal named by *el*'s place: its ancestors' names and its position among same-named siblings."""
    names = [_compact(node, version) for node in reversed([el, *el.iterancestors()])]
    index = 1 + sum(1 for sibling in el.itersiblings(preceding=True) if sibling.tag == el.tag)
    place = "/".join(names[1:])  # the document element is the message; its name says nothing
    return Refusal(artefact_class=_compact(el, version), urn=f"{place}[{index}]", reason=str(exc))


def check_children(el: etree._Element, version: SdmxMlVersion) -> None:
    """Refuse the first element in *el*'s subtree its parent does not allow.

    Comments and processing instructions are not elements and are skipped.
    The walk is in document order, so the finding is the first a reader of
    the file would meet.

    Args:
        el: The subtree's root — an artefact element.
        version: The SDMX-ML version whose table applies.

    Raises:
        SdmxConformanceError: Naming the unexpected element, its parent and
            its line; or an attribute the element does not allow, and its line.
    """
    allowed = _ALLOWED_CLARK[version]
    is_open = _OPEN_CLARK[version]
    attributes = _ATTRIBUTES_CLARK[version]
    # Per open ancestor, what it allows: None where its content goes unchecked
    # (a parent unknown to the table, or open), which then covers its whole
    # subtree. Seeded with a pseudo-parent allowing the root, so the loop has
    # no first-element case. `iterwalk` rather than a hand-kept stack of
    # elements: it walks in C, yields elements only (no comments or PIs), and
    # measured a third faster over OECD's 1M-element codelist document.
    expected: list[frozenset[str] | None] = [frozenset({str(el.tag)})]
    for event, node in etree.iterwalk(el, events=("start", "end")):
        if event == "end":
            expected.pop()
            continue
        tag = str(node.tag)
        within = expected[-1]
        if within is None:
            expected.append(None)
        elif tag not in within:
            raise _unexpected(node, version)
        else:
            on = attributes.get(tag)
            if on is not None:
                # lxml's `keys()` is the attribute names; SIM118's fix, `for name in
                # node`, iterates the child elements instead. `node.attrib`, which it
                # accepts, builds a proxy per element: +7% of a parse, not +5% (#446).
                for name in node.keys():  # noqa: SIM118
                    if name not in on and not name.startswith(_XSI):
                        raise _unexpected_attribute(node, name, version)
            expected.append(None if tag in is_open else allowed.get(tag))


def _unexpected(child: etree._Element, version: SdmxMlVersion) -> SdmxConformanceError:
    parent = child.getparent()
    parent_name = "" if parent is None else _compact(parent, version)
    expected = ALLOWED[version].get(parent_name, frozenset())
    detail = (
        f"not allowed in <{parent_name}> (line {child.sourceline}); "
        f"the SDMX-ML {version} schema allows {', '.join(sorted(expected)) or 'no child elements'} there"
    )
    return SdmxConformanceError(_compact(child, version), detail)


def _unexpected_attribute(el: etree._Element, name: str, version: SdmxMlVersion) -> SdmxConformanceError:
    element = _compact(el, version)
    shown = f"xml:{etree.QName(name).localname}" if name.startswith(f"{{{_XML_NS}}}") else name
    allowed = sorted(ATTRIBUTES[version].get(element, frozenset()))
    detail = (
        f"@{shown} is not allowed on it (line {el.sourceline}); "
        f"the SDMX-ML {version} schema allows {', '.join(f'@{a}' for a in allowed) or 'no attributes'} there"
    )
    return SdmxConformanceError(element, detail)


def _compact(el: etree._Element, version: SdmxMlVersion) -> str:
    """An element's ``prefix:local`` name; a namespace the schemas never declare stays Clark."""
    qname = etree.QName(el)
    if qname.namespace is None:
        return qname.localname
    prefix = _PREFIX[version].get(qname.namespace)
    return qname.text if prefix is None else f"{prefix}:{qname.localname}"


def required(el: etree._Element, attribute: str) -> str:
    """`el`'s `attribute`, which the schema requires; its absence refuses the artefact (#474).

    Reading it with a default (``el.get(attribute, "")``) built the artefact
    with an empty value nothing downstream noticed: a link with no url, an
    epoch map with no base period. There is no value worth assuming for these,
    so the artefact is refused, as #435 refuses any other schema contradiction.
    Where there is one worth assuming, the reader keeps its default and
    `_losses.ASSUMPTIONS` declares it.

    Raises:
        SdmxConformanceError: If `el` does not carry `attribute`.
    """
    value = el.get(attribute)
    if value is None:
        raise SdmxConformanceError(
            etree.QName(el).localname, f"the schema requires @{attribute} here, and the document omits it"
        )
    return value
