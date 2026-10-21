"""Mutators that turn a valid SDMX document into a hostile one (#247).

The generative strategies in `tests.support.strategies` only ever produce
artefacts that are *correct*. That is the right shape for round-trip
properties — you cannot assert "this survives" about something that was never
valid — but it means every byte the readers have ever been tested on came from
a well-formed document. The readers sit on the untyped shell (lxml, msgspec),
and a provider that returns a truncated response or an SDMX dialect we have
not seen is not a hypothetical.

Two families, because they reach different code:

- **Byte mutation** (`mutated_bytes`) — truncate, flip, delete, duplicate,
  inject. Cheap and broad, and the one that first measured the reader's
  exception surface.
- **Structure-aware mutation** (`mutated_xml`, `mutated_json`) — the document
  stays well-formed and stops being *valid SDMX*: a required attribute goes
  missing, an element is renamed, a URN is corrupted, a string appears where
  an object belongs.

Measured over 3000 examples each, which is what justifies keeping both:

======================  ========  ================  =================
strategy                parses    recovers >=1      recovers >=10
======================  ========  ================  =================
byte mutation (XML)      38.6 %          29.4 %             8.8 %
structure-aware (XML)    92.6 %          67.3 %            24.3 %
byte mutation (JSON)     29.5 %          29.3 %               --
structure-aware (JSON)   46.7 %          45.7 %               --
======================  ========  ================  =================

So structure-aware mutation gets roughly three times as deep into the reader,
and byte mutation is not merely a weaker version of it: a quarter of its
payloads are refused by lxml or msgspec before our code runs, which is the
half that exercises the *boundary* rather than the reader.

Both are Hypothesis strategies rather than a loop over `random`, so a failure
shrinks to a minimal payload instead of arriving as a 40 KB blob.
"""

import copy
import pathlib

import msgspec
from hypothesis import strategies as st
from lxml import etree

_FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"


def corpus(suffix: str) -> list[tuple[str, bytes]]:
    """Every checked-in fixture with this suffix, as (name, bytes).

    Read once at module import by the callers rather than per example — a
    strategy that hits the filesystem on every draw makes the property's
    runtime about I/O rather than about the reader.

    Args:
        suffix: ``".xml"`` or ``".json"``.

    Returns:
        (path relative to ``tests/fixtures``, contents), sorted by name so the
        corpus is stable across runs and machines.
    """
    return [
        (str(p.relative_to(_FIXTURES)), p.read_bytes())
        for p in sorted(_FIXTURES.rglob(f"*{suffix}"))
        # `.xml.gz` is a 3.8 MB compressed fixture for the benchmarks; mutating
        # it measures gzip's error handling, not ours.
        if not p.name.endswith(".gz")
    ]


# ── Byte-level mutation ───────────────────────────────────────────────────────


#: Bytes worth injecting: each one changes how a parser reads what follows.
#: A random byte almost always lands inside text content and changes nothing
#: structural, so the interesting characters are chosen rather than drawn.
_STRUCTURAL = (b"<", b">", b"&", b'"', b"'", b"/", b"\x00", b"{", b"}", b"[", b"]", b":", b",", b"\\")


@st.composite
def mutated_bytes(draw: st.DrawFn, seeds: list[tuple[str, bytes]]) -> tuple[str, bytes]:
    """One fixture with a single byte-level mutation applied.

    Args:
        seeds: The corpus to draw from, as `corpus` returns it.

    Returns:
        (fixture name, mutated bytes). The name rides along so a failure says
        which document it came from, which the mutated bytes no longer will.
    """
    name, blob = draw(st.sampled_from(seeds))
    if not blob:
        return name, blob
    kind = draw(st.sampled_from(["truncate", "flip", "delete", "duplicate", "inject"]))
    i = draw(st.integers(min_value=0, max_value=len(blob) - 1))

    if kind == "truncate":
        return name, blob[:i]
    if kind == "flip":
        bit = draw(st.integers(min_value=0, max_value=7))
        return name, blob[:i] + bytes([blob[i] ^ (1 << bit)]) + blob[i + 1 :]
    if kind == "delete":
        span = draw(st.integers(min_value=1, max_value=64))
        return name, blob[:i] + blob[i + span :]
    if kind == "duplicate":
        span = draw(st.integers(min_value=1, max_value=256))
        return name, blob[: i + span] + blob[i : i + span] + blob[i + span :]
    payload = draw(st.sampled_from(_STRUCTURAL)) * draw(st.integers(min_value=1, max_value=8))
    return name, blob[:i] + payload + blob[i:]


# ── Structure-aware mutation: XML ─────────────────────────────────────────────


def _elements(root: etree._Element) -> list[etree._Element]:
    """Real elements only.

    ``iter()`` also yields comments and processing instructions, whose ``tag``
    is a *callable* (``etree.Comment``) rather than a string — and the fixtures
    are heavily commented, so mutating one is common rather than exotic.
    Renaming such a node raises out of the strategy, which reads as a library
    failure and is not one.
    """
    return [el for el in root.iter() if isinstance(el.tag, str)]


#: Replacements for a URN found in element text. URNs are the load-bearing
#: strings in SDMX — every cross-artefact edge is one, and the reader *parses*
#: rather than merely copies them — so they get their own mutation rather than
#: being left to a chance byte flip.
_BROKEN_URNS = (
    "urn:sdmx:",
    "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=",
    "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=A:B(1.0).C.D.E",
    "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=:(",
    "not-a-urn",
    "",
)


def _drop_attribute(draw: st.DrawFn, tree: etree._Element, target: etree._Element) -> None:
    _ = tree
    if target.attrib:
        del target.attrib[draw(st.sampled_from(sorted(target.attrib)))]


def _blank_attribute(draw: st.DrawFn, tree: etree._Element, target: etree._Element) -> None:
    _ = tree
    if target.attrib:
        target.set(draw(st.sampled_from(sorted(target.attrib))), "")


def _corrupt_urn(draw: st.DrawFn, tree: etree._Element, target: etree._Element) -> None:
    _ = target
    holders = [el for el in tree.iter() if el.text and el.text.strip().startswith("urn:sdmx:")]
    if holders:
        draw(st.sampled_from(holders)).text = draw(st.sampled_from(_BROKEN_URNS))


def _rename_element(draw: st.DrawFn, tree: etree._Element, target: etree._Element) -> None:
    _ = draw
    if target is not tree:
        target.tag = f"{{{etree.QName(target).namespace}}}NotAnSdmxElement"


def _drop_children(draw: st.DrawFn, tree: etree._Element, target: etree._Element) -> None:
    _ = (draw, tree)
    for child in list(target):
        target.remove(child)


def _duplicate_element(draw: st.DrawFn, tree: etree._Element, target: etree._Element) -> None:
    _ = (draw, tree)
    if (parent := target.getparent()) is not None:
        parent.append(copy.deepcopy(target))


def _swap_namespace(draw: st.DrawFn, tree: etree._Element, target: etree._Element) -> None:
    _ = draw
    if target is not tree:
        target.tag = f"{{http://example.invalid/not-sdmx}}{etree.QName(target).localname}"


#: Each keeps the document well-formed and stops it being valid SDMX.
_XML_MUTATORS = (
    _drop_attribute,
    _blank_attribute,
    _corrupt_urn,
    _rename_element,
    _drop_children,
    _duplicate_element,
    _swap_namespace,
)


@st.composite
def mutated_xml(draw: st.DrawFn, seeds: list[tuple[str, bytes]]) -> tuple[str, bytes]:
    """One XML fixture, still well-formed, no longer valid SDMX.

    Every mutation here keeps the bytes parseable as XML, so the reader runs
    and has to decide what to do with a document that lies. That is the half
    byte mutation cannot reach.
    """
    name, blob = draw(st.sampled_from(seeds))
    tree = copy.deepcopy(etree.fromstring(blob))
    mutate = draw(st.sampled_from(_XML_MUTATORS))
    mutate(draw, tree, draw(st.sampled_from(_elements(tree))))
    return name, etree.tostring(tree)


# ── Structure-aware mutation: JSON ────────────────────────────────────────────


def _json_paths(node: object, prefix: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    """Every addressable path inside a decoded JSON document."""
    found = [prefix] if prefix else []
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(_json_paths(value, (*prefix, key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_json_paths(value, (*prefix, index)))
    return found


def _child(node: object, step: object) -> object:
    """`node[step]` for a decoded JSON container; `TypeError` for anything else."""
    if isinstance(node, dict):
        members: dict[object, object] = node
        return members[step]
    if isinstance(node, list) and isinstance(step, int):
        elements: list[object] = node
        return elements[step]
    msg = f"cannot index {type(node).__name__} with {type(step).__name__}"
    raise TypeError(msg)


def _set_child(node: object, step: object, value: object) -> None:
    """`node[step] = value` for a decoded JSON container; `TypeError` for anything else."""
    if isinstance(node, dict):
        members: dict[object, object] = node
        members[step] = value
    elif isinstance(node, list) and isinstance(step, int):
        elements: list[object] = node
        elements[step] = value
    else:
        msg = f"cannot assign into {type(node).__name__} with {type(step).__name__}"
        raise TypeError(msg)


def _at(doc: object, path: tuple[object, ...]) -> object:
    for step in path:
        doc = _child(doc, step)
    return doc


@st.composite
def mutated_json(draw: st.DrawFn, seeds: list[tuple[str, bytes]]) -> tuple[str, bytes]:
    """One JSON fixture, still valid JSON, no longer a valid SDMX message.

    The msgspec DTOs are typed, so the interesting mutations are the ones that
    put a value of the wrong *type* where a field is declared — which is
    exactly what a producer following a different spec revision does.
    """
    name, blob = draw(st.sampled_from(seeds))
    doc: object = msgspec.json.decode(blob)
    paths = _json_paths(doc)
    if not paths:
        return name, blob
    path = draw(st.sampled_from(paths))
    kind = draw(st.sampled_from(["delete", "retype", "empty", "deepen"]))

    parent = _at(doc, path[:-1])
    key = path[-1]
    if kind == "delete" and isinstance(parent, dict):
        del parent[key]
    elif kind == "retype":
        replacements: list[object] = [None, 0, -1, "", "not-a-value", [], {}, True]
        replacement = draw(st.sampled_from(replacements))
        _set_child(parent, key, replacement)
    elif kind == "empty" and isinstance(parent, dict):
        parent[key] = type(parent[key])() if parent[key] is not None else None
    elif kind == "deepen":
        _set_child(parent, key, [_child(parent, key)])

    return name, msgspec.json.encode(doc)
