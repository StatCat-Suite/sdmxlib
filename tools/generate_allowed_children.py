"""Generate the SDMX-ML allowed-children table from the vendored XSDs (#436).

## Why this exists

The SDMX-ML readers fetch the children they know by qualified name and never
look at the rest, so an element they do not expect vanishes silently.
Eurostat's 3.0 AgencyScheme ships ``<str:Name>`` where the schema requires
``<com:Name>``: the reader found no name, and ``validate()`` then refused the
scheme for having none — a symptom two steps away from its cause.

The readers now check every artefact subtree against *what the schema allows
under each parent* (`sdmxlib.formats.sdmx_ml._closed_world`). Stating that by
hand for ~400 element names per version is not going to happen, and it is
exactly what the vendored schemas already say — so this reads it out of them
and ships the result as data. The XSDs stay test-only; the table is the part
the library needs.

## What it resolves

Named and anonymous complex types; ``xs:extension`` (appends to the base's
content model) and ``xs:restriction`` (replaces it); ``xs:sequence`` /
``xs:choice`` / ``xs:all`` at any nesting; ``xs:group`` refs; element
``ref=``; ``substitutionGroup`` members (transitively, abstract heads
dropped, since an abstract element cannot appear); ``form`` /
``elementFormDefault`` for local names; a particle a restriction sets to
``maxOccurs="0"``, which it prohibits; and imports/includes, by loading every
schema of a version into one symbol table. A type that allows ``xs:any``, or
an element with no type at all (``xs:anyType``), makes its parent *open*.

Lenient by design: where one parent name is declared with different types in
different contexts, its allowed children are the **union**. The goal is
catching elements no context allows, not context-precise validation — that is
what the XSD itself is for.

## Attributes (#446)

The same table for attributes: per element name, the attributes some context
allows on it. They resolve differently from children, and the difference is
the point of most of these schemas' restrictions: an ``xs:extension`` adds to
its base's attributes, but an ``xs:restriction`` **inherits** them — it does
not replace them as it does a content model — and removes one only by
redeclaring it ``use="prohibited"``. That is how 3.0 takes ``version`` and
``isFinal`` off an organisation scheme. ``xs:attributeGroup`` refs resolve
through the symbol table, ``xml:lang`` by its fixed namespace (the ``xml``
prefix is implicit, never in an ``nsmap``), and ``xs:anyAttribute`` makes an
element's attributes *open*.

Only parents reachable from ``mes:Structures`` or ``mes:Header`` are emitted:
the check covers artefact subtrees, the containers above them, and the
message header (#447), and nothing else.

## Usage

    uv run python tools/generate_allowed_children.py           # rewrite the module
    uv run python tools/generate_allowed_children.py --check   # exit 1 if stale

``tests/unit/test_closed_world.py`` runs the same `render()` in memory and
fails when the shipped module differs from it.
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "tests" / "schemas"
TARGET = ROOT / "src" / "sdmxlib" / "formats" / "sdmx_ml" / "_allowed_children.py"

#: SDMX-ML version → vendored schema directory.
VERSIONS = {"2.1": "sdmx_ml21", "3.0": "sdmx_ml30", "3.1": "sdmx_ml31"}

_XS_NS = "http://www.w3.org/2001/XMLSchema"
_XS = f"{{{_XS_NS}}}"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_ANY_TYPE = f"{{{_XS_NS}}}anyType"

#: ruff's ``line-length``; `_set_entry` reproduces the formatter's layout at it.
_LINE_LENGTH = 120

#: Namespace suffix (after ``…/schemas/vX_Y/``) → the prefix the table uses.
#: The readers already spell these prefixes (``str:``, ``com:``, ``mes:``); the
#: rest only appear if a structure subtree ever reaches them.
_PREFIXES = {
    "message": "mes",
    "structure": "str",
    "common": "com",
    "registry": "reg",
    "query": "query",
    "message/footer": "footer",
    "metadata/generic": "md",
    "metadata/structurespecific": "mdss",
    "data/generic": "gen",
    "data/structurespecific": "dss",
}


class Schema:
    """Every vendored XSD of one SDMX-ML version, as one symbol table.

    Imports and includes are honoured by loading the whole directory: each
    file is a module of the same schema set, and a lookup by qualified name
    then finds a type wherever it was declared.

    Args:
        directory: The version's vendored schema directory.
    """

    def __init__(self, directory: Path) -> None:
        self.roots = [etree.parse(str(p)).getroot() for p in sorted(directory.glob("*.xsd"))]
        self.types: dict[str, etree._Element] = {}
        self.groups: dict[str, etree._Element] = {}
        self.elements: dict[str, etree._Element] = {}
        self.members: dict[str, list[str]] = defaultdict(list)
        self.attribute_groups: dict[str, etree._Element] = {}
        for root in self.roots:
            tns = root.get("targetNamespace", "")
            for node in root:
                name = node.get("name")
                if name is None or not isinstance(node.tag, str):
                    continue
                qname = _clark(tns, name)
                if node.tag == f"{_XS}complexType":
                    self.types[qname] = node
                elif node.tag == f"{_XS}group":
                    self.groups[qname] = node
                elif node.tag == f"{_XS}attributeGroup":
                    self.attribute_groups[qname] = node
                elif node.tag == f"{_XS}element":
                    self.elements[qname] = node
                    head = node.get("substitutionGroup")
                    if head is not None:
                        self.members[resolve(node, head)].append(qname)

    def declarations(self) -> list[tuple[str, etree._Element]]:
        """Every element declaration (global and local) with its qualified name."""
        out: list[tuple[str, etree._Element]] = []
        for root in self.roots:
            out.extend((self.decl_name(el), el) for el in root.iter(f"{_XS}element") if el.get("name") is not None)
        return out

    def decl_name(self, el: etree._Element) -> str:
        """An element declaration's qualified name, honouring ``form``."""
        root = el.getroottree().getroot()
        tns = root.get("targetNamespace", "")
        if el.getparent() is root:
            return _clark(tns, el.get("name", ""))
        form = el.get("form", root.get("elementFormDefault", "unqualified"))
        return _clark(tns if form == "qualified" else "", el.get("name", ""))

    def substitutes(self, qname: str) -> set[str]:
        """*qname* and every substitution-group member, transitively, minus abstract ones."""
        out: set[str] = set()
        stack = [qname]
        while stack:
            name = stack.pop()
            el = self.elements.get(name)
            if el is None or el.get("abstract") != "true":
                out.add(name)
            stack.extend(self.members.get(name, ()))
        return out

    def decl_content(self, el: etree._Element) -> tuple[set[str], bool]:
        """The children an element declaration allows, and whether it is open."""
        type_name = el.get("type")
        if type_name is not None:
            return self.named_content(resolve(el, type_name))
        anonymous = el.find(f"{_XS}complexType")
        if anonymous is not None:
            return self.type_content(anonymous)
        if el.find(f"{_XS}simpleType") is not None:
            return set(), False
        head = el.get("substitutionGroup")
        if head is not None:
            # A member with no type of its own takes its head's.
            return self.decl_content(self.elements[resolve(el, head)])
        return set(), True  # no type at all is xs:anyType

    def named_content(self, qname: str) -> tuple[set[str], bool]:
        """Content of a named type; a simple type (or builtin) has no children."""
        if qname == _ANY_TYPE:
            return set(), True
        node = self.types.get(qname)
        if node is None:
            return set(), False
        return self.type_content(node)

    def type_content(self, ct: etree._Element) -> tuple[set[str], bool]:
        """Content of a complex type node, named or anonymous."""
        for child in ct:
            if child.tag == f"{_XS}simpleContent":
                return set(), False
            if child.tag == f"{_XS}complexContent":
                for derivation in child:
                    if derivation.tag == f"{_XS}extension":
                        names, is_open = self.named_content(resolve(derivation, derivation.get("base", "")))
                        own, own_open = self.particles(derivation)
                        return names | own, is_open or own_open
                    if derivation.tag == f"{_XS}restriction":
                        return self.particles(derivation)
        return self.particles(ct)

    def particles(self, node: etree._Element) -> tuple[set[str], bool]:
        """Element names a model group (or derivation body) admits."""
        names: set[str] = set()
        is_open = False
        for child in node:
            if child.get("maxOccurs") == "0":
                # Prohibited: a restriction keeps a particle at maxOccurs="0"
                # to say the derived type forbids what its base allowed —
                # 3.x's MetadataAttributeUsage and its ConceptIdentity (#422).
                continue
            if child.tag == f"{_XS}element":
                ref = child.get("ref")
                names |= self.substitutes(resolve(child, ref)) if ref is not None else {self.decl_name(child)}
            elif child.tag in {f"{_XS}sequence", f"{_XS}choice", f"{_XS}all"}:
                more, more_open = self.particles(child)
                names |= more
                is_open = is_open or more_open
            elif child.tag == f"{_XS}group":
                more, more_open = self.particles(self.groups[resolve(child, child.get("ref", ""))])
                names |= more
                is_open = is_open or more_open
            elif child.tag == f"{_XS}any":
                is_open = True
        return names, is_open


    # ── attributes (#446) ───────────────────────────────────────────────────

    def decl_attributes(self, el: etree._Element) -> tuple[set[str], bool]:
        """The attributes an element declaration allows, and whether they are open."""
        type_name = el.get("type")
        if type_name is not None:
            return self.named_attributes(resolve(el, type_name))
        anonymous = el.find(f"{_XS}complexType")
        if anonymous is not None:
            return self.type_attributes(anonymous)
        if el.find(f"{_XS}simpleType") is not None:
            return set(), False
        head = el.get("substitutionGroup")
        if head is not None:
            return self.decl_attributes(self.elements[resolve(el, head)])
        return set(), True  # no type at all is xs:anyType

    def named_attributes(self, qname: str) -> tuple[set[str], bool]:
        """Attributes of a named type; a simple type (or builtin) has none."""
        if qname == _ANY_TYPE:
            return set(), True
        node = self.types.get(qname)
        if node is None:
            return set(), False
        return self.type_attributes(node)

    def type_attributes(self, ct: etree._Element) -> tuple[set[str], bool]:
        """Attributes of a complex type: a derivation's base's, plus its own, less what it prohibits."""
        for child in ct:
            if child.tag in {f"{_XS}simpleContent", f"{_XS}complexContent"}:
                for derivation in child:
                    if derivation.tag in {f"{_XS}extension", f"{_XS}restriction"}:
                        base, base_open = self.named_attributes(resolve(derivation, derivation.get("base", "")))
                        own, own_open = self.declared_attributes(derivation)
                        allowed = (base | {n for n, prohibited in own.items() if not prohibited}) - {
                            n for n, prohibited in own.items() if prohibited
                        }
                        # A restriction may only narrow an anyAttribute; an extension adds to it.
                        is_open = own_open if derivation.tag == f"{_XS}restriction" else base_open or own_open
                        return allowed, is_open
        own, is_open = self.declared_attributes(ct)
        return {n for n, prohibited in own.items() if not prohibited}, is_open

    def declared_attributes(self, node: etree._Element) -> tuple[dict[str, bool], bool]:
        """Attribute name → whether *node* prohibits it, through attribute groups; and anyAttribute."""
        out: dict[str, bool] = {}
        is_open = False
        for child in node:
            if child.tag == f"{_XS}attribute":
                out[self.attribute_name(child)] = child.get("use") == "prohibited"
            elif child.tag == f"{_XS}attributeGroup":
                more, more_open = self.declared_attributes(self.attribute_groups[resolve(child, child.get("ref", ""))])
                out.update(more)
                is_open = is_open or more_open
            elif child.tag == f"{_XS}anyAttribute":
                is_open = True
        return out, is_open

    def attribute_name(self, attribute: etree._Element) -> str:
        """An attribute declaration's name: unqualified unless referenced or ``form="qualified"``."""
        ref = attribute.get("ref")
        if ref is not None:
            if ref.startswith("xml:"):
                return _clark(_XML_NS, ref[4:])  # the xml prefix is implicit, never in an nsmap
            return resolve(attribute, ref)
        root = attribute.getroottree().getroot()
        name = attribute.get("name", "")
        form = attribute.get("form", root.get("attributeFormDefault", "unqualified"))
        return _clark(root.get("targetNamespace", ""), name) if form == "qualified" else name


def resolve(node: etree._Element, value: str) -> str:
    """A ``prefix:local`` QName attribute value in Clark notation, via *node*'s in-scope map."""
    prefix, _, local = value.rpartition(":")
    ns = node.nsmap.get(prefix or None)
    return _clark(ns or "", local)


def _clark(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}" if ns else local


def contents(schema: Schema) -> tuple[dict[str, set[str]], set[str]]:
    """Allowed children per parent and the open parents, over the whole schema set.

    Args:
        schema: One version's schema set.

    Returns:
        ``(allowed, open)`` in Clark notation, for every declared element name.
    """
    allowed: dict[str, set[str]] = defaultdict(set)
    is_open: set[str] = set()
    for qname, el in schema.declarations():
        names, opened = schema.decl_content(el)
        allowed[qname] |= names
        if opened:
            is_open.add(qname)
    return dict(allowed), is_open


def table(schema: Schema) -> tuple[dict[str, set[str]], set[str]]:
    """`contents`, cut down to the parents reachable from ``mes:Structures`` and ``mes:Header``.

    Args:
        schema: One version's schema set.

    Returns:
        ``(allowed, open)`` in Clark notation.
    """
    allowed, is_open = contents(schema)
    starts = [q for q in allowed if q.endswith(("/message}Structures", "/message}Header"))]
    seen = set(starts)
    stack = list(starts)
    while stack:
        for child in allowed.get(stack.pop(), ()):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return {q: allowed.get(q, set()) for q in seen}, is_open & seen


def attribute_table(schema: Schema, reachable: set[str]) -> tuple[dict[str, set[str]], set[str]]:
    """Allowed attributes per element and the attribute-open elements, over `reachable` (#446).

    Args:
        schema: One version's schema set.
        reachable: The element names `table` kept.

    Returns:
        ``(allowed, open)`` in Clark notation, the union over every declaration of a name.
    """
    allowed: dict[str, set[str]] = defaultdict(set)
    is_open: set[str] = set()
    for qname, el in schema.declarations():
        if qname not in reachable:
            continue
        names, opened = schema.decl_attributes(el)
        allowed[qname] |= names
        if opened:
            is_open.add(qname)
    return {q: allowed.get(q, set()) for q in reachable}, is_open


def compact(qname: str) -> str:
    """Clark notation → the table's ``prefix:local`` form (bare local when unqualified)."""
    if not qname.startswith("{"):
        return qname
    ns, _, local = qname[1:].partition("}")
    if ns == _XML_NS:
        return f"xml:{local}"
    suffix = ns.split("/schemas/", 1)[1].split("/", 1)[1]
    return f"{_PREFIXES[suffix]}:{local}"


def namespaces(schema: Schema) -> dict[str, str]:
    """Prefix → namespace URI for every SDMX namespace in the version's schema set."""
    out: dict[str, str] = {}
    for root in schema.roots:
        tns = root.get("targetNamespace", "")
        if "/schemas/" in tns:
            out[compact(_clark(tns, "x")).split(":", 1)[0]] = tns
    return dict(sorted(out.items()))


def _tag() -> dict[str, str]:
    tags: dict[str, str] = {}
    for line in (SCHEMAS / "VERSION").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(":")
        if key.strip() in VERSIONS.values():
            tags[key.strip()] = value.strip()
    return tags


def _q(name: str) -> str:
    return f'"{name}"'


def _set_entry(indent: str, key: str, names: list[str]) -> list[str]:
    """``key: frozenset({...}),`` laid out exactly as ``ruff format`` lays it out.

    Emitting the formatter's own layout, rather than piping through it, keeps
    the generator free of a subprocess and the drift test free of ruff: the
    shipped module passes ``ruff format --check`` because this matches it.
    """
    if not names:
        return [f"{indent}{key}: frozenset(),"]
    body = "{" + ", ".join(_q(n) for n in names) + "}"
    one_line = f"{indent}{key}: frozenset({body}),"
    if len(one_line) <= _LINE_LENGTH:
        return [one_line]
    inner = indent + "    "
    if len(inner + body) <= _LINE_LENGTH:
        return [f"{indent}{key}: frozenset(", inner + body, f"{indent}),"]
    return [
        f"{indent}{key}: frozenset(",
        inner + "{",
        *(f"{inner}    {_q(n)}," for n in names),
        inner + "}",
        f"{indent}),",
    ]


def render() -> str:
    """The generated module's source.

    Returns:
        Exactly what `TARGET` should contain.
    """
    tags = _tag()
    header = ", ".join(f"{VERSIONS[v]} {tags[VERSIONS[v]]}" for v in VERSIONS)
    lines = [
        '"""Allowed children per SDMX-ML parent element — GENERATED, do not edit (#436).',
        "",
        "Generated by ``tools/generate_allowed_children.py`` from the vendored XSDs",
        "under ``tests/schemas/``, at these upstream tags:",
        "",
        f"    {header}",
        "",
        "After refreshing the schemas, regenerate with",
        "``just generate-allowed-children``.",
        "",
        "Names are ``prefix:local`` under `NAMESPACES`; an unqualified local element",
        "is its bare local name. A parent in `OPEN` admits any child (``xs:any`` or",
        "``xs:anyType`` in some context). Where a parent is declared with different",
        "types in different contexts its children are the union — the generator's",
        "docstring says why.",
        "",
        "`ATTRIBUTES` is the same for attributes (#446): an element's unqualified",
        "attributes by local name, ``xml:lang`` as such. One in `OPEN_ATTRIBUTES`",
        "admits any attribute (``xs:anyAttribute``).",
        '"""',
        "",
        "#: Per SDMX-ML version: prefix → namespace URI.",
        "NAMESPACES: dict[str, dict[str, str]] = {",
    ]
    tables: dict[str, tuple[dict[str, set[str]], set[str]]] = {}
    attribute_tables: dict[str, tuple[dict[str, set[str]], set[str]]] = {}
    for version, directory in VERSIONS.items():
        schema = Schema(SCHEMAS / directory)
        tables[version] = table(schema)
        attribute_tables[version] = attribute_table(schema, set(tables[version][0]))
        lines.append(f"    {_q(version)}: {{")
        lines += [f"        {_q(prefix)}: {_q(uri)}," for prefix, uri in namespaces(schema).items()]
        lines.append("    },")
    lines += ["}", "", "#: Per SDMX-ML version: parent → the children some schema context allows under it."]
    lines.append("ALLOWED: dict[str, dict[str, frozenset[str]]] = {")
    for version, (allowed, _) in tables.items():
        lines.append(f"    {_q(version)}: {{")
        for parent in sorted(allowed, key=compact):
            lines += _set_entry("        ", _q(compact(parent)), sorted(compact(c) for c in allowed[parent]))
        lines.append("    },")
    lines += ["}", "", "#: Per SDMX-ML version: parents that admit any child."]
    lines.append("OPEN: dict[str, frozenset[str]] = {")
    for version, (_, opened) in tables.items():
        lines += _set_entry("    ", _q(version), sorted(compact(c) for c in opened))
    lines += ["}", "", "#: Per SDMX-ML version: element → the attributes some schema context allows on it."]
    lines.append("ATTRIBUTES: dict[str, dict[str, frozenset[str]]] = {")
    for version, (allowed, _) in attribute_tables.items():
        lines.append(f"    {_q(version)}: {{")
        for element in sorted(allowed, key=compact):
            lines += _set_entry("        ", _q(compact(element)), sorted(compact(a) for a in allowed[element]))
        lines.append("    },")
    lines += ["}", "", "#: Per SDMX-ML version: elements that admit any attribute."]
    lines.append("OPEN_ATTRIBUTES: dict[str, frozenset[str]] = {")
    for version, (_, opened) in attribute_tables.items():
        lines += _set_entry("    ", _q(version), sorted(compact(c) for c in opened))
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> int:
    """Write the module, or with ``--check`` report whether it is stale."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the shipped module is stale")
    args = parser.parse_args()
    source = render()
    if args.check:
        if TARGET.read_text(encoding="utf-8") != source:
            sys.stderr.write(f"{TARGET.relative_to(ROOT)} is stale; run tools/generate_allowed_children.py\n")
            return 1
        return 0
    TARGET.write_text(source, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
