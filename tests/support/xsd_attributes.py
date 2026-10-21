"""The attributes each SDMX-ML element allows, read from the vendored XSDs (#422).

The attribute twin of `sdmx_ml._allowed_children`: that table says which
child *elements* a parent allows, and drives the element census. Attributes
were outside it, so an attribute the readers never look at — `isPartial`,
`isExternalReference`, a text format's `timeInterval` — vanished with no
row anywhere. This reads the same schema sets for the other half.

Test-only, like the XSDs it reads. Keyed as `ALLOWED` is — by element name,
``prefix:local``, the union over every declaration of that name — so where
two types share a name the answer over-allows, and the census says so.

Resolved: named and anonymous complex types, `xs:extension` (the base's
attributes plus its own), `xs:restriction` (the base's, less any
``use="prohibited"``, plus its own), `simpleContent` and `complexContent`
alike, `xs:attributeGroup` references, `ref=` attributes (``xml:lang``),
and an element with no type of its own taking its substitution head's.
"""

import functools
from collections import defaultdict
from pathlib import Path

from lxml import etree

from sdmxlib.formats.sdmx_ml._allowed_children import ALLOWED, NAMESPACES
from tests.support.xsd import SchemaVersion

_SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
_DIRECTORY: dict[SchemaVersion, str] = {"2.1": "sdmx_ml21", "3.0": "sdmx_ml30", "3.1": "sdmx_ml31"}
_XS = "{http://www.w3.org/2001/XMLSchema}"
_XML_NS = "http://www.w3.org/XML/1998/namespace"


def _clark(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}" if ns else local


def _resolve(node: etree._Element, value: str) -> str:
    """A ``prefix:local`` QName attribute value in Clark notation, via *node*'s in-scope map."""
    prefix, _, local = value.rpartition(":")
    # `xml` is bound implicitly in every document, so no nsmap lists it.
    ns = _XML_NS if prefix == "xml" else node.nsmap.get(prefix or None) or ""
    return _clark(ns, local)


class _Schema:
    """One version's schema set as one symbol table — the shape the generator uses."""

    def __init__(self, directory: Path) -> None:
        self.roots = [etree.parse(str(p)).getroot() for p in sorted(directory.glob("*.xsd"))]
        self.types: dict[str, etree._Element] = {}
        self.groups: dict[str, etree._Element] = {}
        self.elements: dict[str, etree._Element] = {}
        for root in self.roots:
            tns = root.get("targetNamespace", "")
            for node in root:
                name = node.get("name")
                if name is None or not isinstance(node.tag, str):
                    continue
                if node.tag == f"{_XS}complexType":
                    self.types[_clark(tns, name)] = node
                elif node.tag == f"{_XS}attributeGroup":
                    self.groups[_clark(tns, name)] = node
                elif node.tag == f"{_XS}element":
                    self.elements[_clark(tns, name)] = node

    def declarations(self) -> list[tuple[str, etree._Element]]:
        """Every element declaration with its qualified name, honouring ``form``."""
        out: list[tuple[str, etree._Element]] = []
        for root in self.roots:
            tns = root.get("targetNamespace", "")
            default_form = root.get("elementFormDefault", "unqualified")
            for el in root.iter(f"{_XS}element"):
                name = el.get("name")
                if name is None:
                    continue
                qualified = el.getparent() is root or el.get("form", default_form) == "qualified"
                out.append((_clark(tns if qualified else "", name), el))
        return out

    def element_attributes(self, el: etree._Element) -> set[str]:
        type_name = el.get("type")
        if type_name is not None:
            return self.type_attributes(_resolve(el, type_name))
        anonymous = el.find(f"{_XS}complexType")
        if anonymous is not None:
            return self.content_attributes(anonymous)
        head = el.get("substitutionGroup")
        if head is not None and (head_el := self.elements.get(_resolve(el, head))) is not None:
            return self.element_attributes(head_el)
        return set()

    def type_attributes(self, qname: str) -> set[str]:
        node = self.types.get(qname)
        return set() if node is None else self.content_attributes(node)

    def content_attributes(self, ct: etree._Element) -> set[str]:
        for child in ct:
            if child.tag in {f"{_XS}complexContent", f"{_XS}simpleContent"}:
                for derivation in child:
                    if derivation.tag in {f"{_XS}extension", f"{_XS}restriction"}:
                        inherited = self.type_attributes(_resolve(derivation, derivation.get("base", "")))
                        own, prohibited = self.own_attributes(derivation)
                        return (inherited - prohibited) | own
        own, _ = self.own_attributes(ct)
        return own

    def own_attributes(self, node: etree._Element) -> tuple[set[str], set[str]]:
        """The attributes *node* declares directly or through groups, and those it prohibits."""
        allowed: set[str] = set()
        prohibited: set[str] = set()
        for child in node:
            if child.tag == f"{_XS}attribute":
                name = child.get("name") or _compact(_resolve(child, child.get("ref", "")))
                (prohibited if child.get("use") == "prohibited" else allowed).add(name)
            elif child.tag == f"{_XS}attributeGroup":
                group = self.groups.get(_resolve(child, child.get("ref", "")))
                if group is not None:
                    more, gone = self.own_attributes(group)
                    allowed |= more
                    prohibited |= gone
        return allowed - prohibited, prohibited


def _compact(qname: str) -> str:
    """An attribute's name: bare when unqualified, ``xml:lang`` for the one that is."""
    if qname.startswith(f"{{{_XML_NS}}}"):
        return "xml:" + qname.split("}", 1)[1]
    return qname.rpartition("}")[2]


@functools.cache
def allowed_attributes(version: SchemaVersion) -> dict[str, frozenset[str]]:
    """Per element name, as `ALLOWED` keys it: the attributes some declaration of it allows.

    Only elements `ALLOWED` lists — those reachable from ``mes:Structures``.
    """
    schema = _Schema(_SCHEMAS / _DIRECTORY[version])
    prefixes = {uri: prefix for prefix, uri in NAMESPACES[version].items()}
    found: dict[str, set[str]] = defaultdict(set)
    for qname, el in schema.declarations():
        ns, _, local = qname[1:].partition("}") if qname.startswith("{") else ("", "", qname)
        name = f"{prefixes[ns]}:{local}" if ns in prefixes else local
        if name in ALLOWED[version]:
            found[name] |= schema.element_attributes(el)
    return {name: frozenset(attributes) for name, attributes in found.items()}
