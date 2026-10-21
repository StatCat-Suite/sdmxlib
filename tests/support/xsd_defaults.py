"""Which attributes a written SDMX-ML document states at their schema default (#503).

A default belongs to an element's *type*, not its name: ``textType`` defaults
to ``String`` on a component's format and ``ObservationalTimePeriod`` on a
time dimension's, both ``str:TextFormat``. So this walks a document from its
root declaration, finds each element's declaration in its parent's type, and
reads the defaults off that type: an ``xs:extension`` adds to its base's
attributes, an ``xs:restriction`` inherits them and may redeclare or
prohibit one, through ``xs:attributeGroup`` refs.

libxml2 can insert defaults while validating (`XMLSchema(attribute_defaults=True)`),
but not for an element of an imported namespace, which is every ``str:``
element of a message: it was measured filling nothing in an SDMX document.
"""

import functools
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

from lxml import etree

from tests.support.repo import ROOT

_XS = "{http://www.w3.org/2001/XMLSchema}"
_DIRS = {"2.1": "sdmx_ml21", "3.0": "sdmx_ml30", "3.1": "sdmx_ml31"}


def _clark(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}" if ns else local


def _resolve(node: etree._Element, value: str) -> str:
    prefix, _, local = value.rpartition(":")
    return _clark(node.nsmap.get(prefix or None) or "", local)


class _Schema:
    """Every vendored XSD of one version, as one symbol table, with typed lookups."""

    def __init__(self, directory: Path) -> None:
        self.types: dict[str, etree._Element] = {}
        self.groups: dict[str, etree._Element] = {}
        self.attribute_groups: dict[str, etree._Element] = {}
        self.elements: dict[str, etree._Element] = {}
        self.attributes: dict[str, etree._Element] = {}
        self.members: dict[str, list[str]] = defaultdict(list)
        #: Per type node, what `children` and `defaults` found: each is asked once per element.
        self._children: dict[etree._Element, dict[str, etree._Element]] = {}
        self._defaults: dict[etree._Element, dict[str, str]] = {}
        for path in sorted(directory.glob("*.xsd")):
            root = etree.parse(str(path)).getroot()
            tns = root.get("targetNamespace", "")
            for node in root:
                name = node.get("name")
                if name is None or not isinstance(node.tag, str):
                    continue
                qname = _clark(tns, name)
                table = {
                    f"{_XS}complexType": self.types,
                    f"{_XS}group": self.groups,
                    f"{_XS}attributeGroup": self.attribute_groups,
                    f"{_XS}element": self.elements,
                    f"{_XS}attribute": self.attributes,
                }.get(node.tag)
                if table is not None:
                    table[qname] = node
                head = node.get("substitutionGroup")
                if node.tag == f"{_XS}element" and head is not None:
                    self.members[_resolve(node, head)].append(qname)

    def complex_type(self, decl: etree._Element) -> etree._Element | None:
        """An element declaration's complex type, named or anonymous; None for a simple one."""
        named = decl.get("type")
        if named is not None:
            return self.types.get(_resolve(decl, named))
        anonymous = decl.find(f"{_XS}complexType")
        if anonymous is not None:
            return anonymous
        head = decl.get("substitutionGroup")
        return None if head is None else self.complex_type(self.elements[_resolve(decl, head)])

    def children(self, ct: etree._Element) -> dict[str, etree._Element]:
        """Child element name → its declaration, under a complex type."""
        if ct not in self._children:
            self._children[ct] = self._find_children(ct)
        return self._children[ct]

    def _find_children(self, ct: etree._Element) -> dict[str, etree._Element]:
        for child in ct:
            if child.tag == f"{_XS}simpleContent":
                return {}
            if child.tag == f"{_XS}complexContent":
                for derivation in child:
                    if derivation.tag == f"{_XS}extension":
                        base = self.types.get(_resolve(derivation, derivation.get("base", "")))
                        inherited = {} if base is None else self.children(base)
                        return {**inherited, **self._particles(derivation)}
                    if derivation.tag == f"{_XS}restriction":
                        return self._particles(derivation)
        return self._particles(ct)

    def _particles(self, node: etree._Element) -> dict[str, etree._Element]:
        out: dict[str, etree._Element] = {}
        for child in node:
            if child.tag == f"{_XS}element":
                ref = child.get("ref")
                if ref is None:
                    out[self._local_name(child)] = child
                else:
                    for member in self._substitutes(_resolve(child, ref)):
                        out[member] = self.elements[member]
            elif child.tag in {f"{_XS}sequence", f"{_XS}choice", f"{_XS}all"}:
                out.update(self._particles(child))
            elif child.tag == f"{_XS}group":
                out.update(self._particles(self.groups[_resolve(child, child.get("ref", ""))]))
        return out

    def _local_name(self, decl: etree._Element) -> str:
        root = decl.getroottree().getroot()
        form = decl.get("form", root.get("elementFormDefault", "unqualified"))
        return _clark(root.get("targetNamespace", "") if form == "qualified" else "", decl.get("name", ""))

    def _substitutes(self, qname: str) -> Iterator[str]:
        stack = [qname]
        while stack:
            name = stack.pop()
            yield name
            stack.extend(self.members.get(name, ()))

    def defaults(self, ct: etree._Element) -> dict[str, str]:
        """Attribute name → its default, under a complex type."""
        if ct not in self._defaults:
            self._defaults[ct] = self._find_defaults(ct)
        return self._defaults[ct]

    def _find_defaults(self, ct: etree._Element) -> dict[str, str]:
        for child in ct:
            if child.tag in {f"{_XS}simpleContent", f"{_XS}complexContent"}:
                for derivation in child:
                    if derivation.tag in {f"{_XS}extension", f"{_XS}restriction"}:
                        base = self.types.get(_resolve(derivation, derivation.get("base", "")))
                        inherited = {} if base is None else self.defaults(base)
                        return self._declared(derivation, inherited)
        return self._declared(ct, {})

    def _declared(self, node: etree._Element, inherited: dict[str, str]) -> dict[str, str]:
        out = dict(inherited)
        for child in node:
            if child.tag == f"{_XS}attribute":
                ref = child.get("ref")
                decl = self.attributes.get(_resolve(child, ref), child) if ref is not None else child
                name = ref.rpartition(":")[2] if ref is not None else child.get("name", "")
                # A `fixed` value applies to an absent attribute as a default does.
                default = next(
                    (
                        v
                        for v in (child.get("default"), child.get("fixed"), decl.get("default"), decl.get("fixed"))
                        if v
                    ),
                    None,
                )
                out.pop(name, None)
                # A required attribute is written whatever its value (#503).
                if default is not None and child.get("use") not in {"prohibited", "required"}:
                    out[name] = default
            elif child.tag == f"{_XS}attributeGroup":
                out = self._declared(self.attribute_groups[_resolve(child, child.get("ref", ""))], out)
        return out


@functools.cache
def _schema(version: str) -> _Schema:
    return _Schema(ROOT / "tests" / "schemas" / _DIRS[version])


#: Identity: written even at a default, so what is named needs no schema to read (#503).
IDENTITY = frozenset(
    {"id", "agencyID", "version", "maintainableParentID", "maintainableParentVersion", "class", "package"}
)


def without_defaults(document: etree._Element) -> etree._Element:
    """`document` less every non-identity attribute stated at its type's default, in place.

    Two documents that differ only in which defaults they state mean the
    same, so a comparison of the two reads each this way. The version is the
    document's namespace's.
    """
    namespace = etree.QName(document).namespace or ""
    version = next((v for v, mark in (("2.1", "v2_1"), ("3.1", "v3_1"), ("3.0", "v3_0")) if mark in namespace), None)
    if version is None:
        return document
    # Deleted through the elements the walk holds: an lxml proxy's `id` is not
    # stable across traversals, so a second walk could not find them again.
    for el, attribute in [(el, a) for el, a in _stated(document, version) if a not in IDENTITY]:
        del el.attrib[attribute]
    return document


def stated_defaults(document: etree._Element, version: str) -> Iterator[tuple[str, str, str]]:
    """Each attribute `document` states at its type's default, as (element path, attribute, value).

    An element no declaration reaches (under ``xs:any``) is skipped with what is
    below it.
    """
    for el, attribute in _stated(document, version):
        path = "/".join(etree.QName(each).localname for each in (*reversed(list(el.iterancestors())), el))
        yield path, attribute, el.attrib[attribute]


def _stated(document: etree._Element, version: str) -> Iterator[tuple[etree._Element, str]]:
    schema = _schema(version)
    root_decl = schema.elements.get(document.tag) if isinstance(document.tag, str) else None
    if root_decl is None:
        return
    stack: list[tuple[etree._Element, etree._Element]] = [(document, root_decl)]
    while stack:
        el, decl = stack.pop()
        ct = schema.complex_type(decl)
        if ct is None:
            continue
        defaults = schema.defaults(ct)
        for attribute, value in el.attrib.items():
            if defaults.get(attribute) == value:
                yield el, attribute
        children = schema.children(ct)
        stack.extend(
            (child, child_decl)
            for child in el
            if isinstance(child.tag, str) and (child_decl := children.get(child.tag)) is not None
        )
