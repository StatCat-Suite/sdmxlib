"""Count every element and attribute path in an SDMX-ML structure document.

The read-loss check (#422) without a read-side register: count the paths in
what a registry sent, count them again in what this library writes back to
the same format, and every path whose count fell is content the reader did not
keep. It needs no list of what the model lacks, which is the point — that list
is what it is meant to produce.

Paths are local names under `Structures`, so 2.1 and 3.0 namespaces do not
split a count. A run of the same element nested in itself (a 2.1
`HierarchicalCode` inside a `HierarchicalCode`, a `Code` tree) is collapsed to
one step, or a deep hierarchy would make one path per depth and the counts
would compare depths rather than content.

Attributes are counted as `path/@name`. `xml:lang` is `@lang`, so a `Name` in
three languages written back in one shows as two lost `Name/@lang`. A boolean
attribute is counted with its value — `@isFinal=true` — because dropping a
`false` the schema defaults to `false` loses nothing, and dropping a `true`
loses the whole point of the attribute. The few non-boolean attributes with a
schema default (`_KEYED_BY_VALUE`) are counted with their value for the same
reason: an omitted `maxOccurs="1"` is still `1`.

An element with nothing in it — no text, no children, no attribute but
`xml:lang` — is counted under `path(empty)`, so a registry's `<AnnotationURL/>`
does not read as a lost URL.

Some lost paths carry no information: the writer omits an `@urn` it can derive
from the artefact's identity, an attribute at its schema default, or an empty
element. `DERIVABLE`
names each one with its reason, and `lost` reports them apart, so the real
losses are not buried under thousands of `@urn`s — but nothing is dropped
from the count without a written reason.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from lxml import etree

#: Appended to the path of an element with nothing in it (`_is_empty`), so
#: `<AnnotationURL/>` and `<AnnotationText xml:lang="en"/>` count apart from
#: ones that say something.
_EMPTY = "(empty)"

#: Path suffix → why losing it loses nothing.
DERIVABLE: dict[str, str] = {
    "@urn": "derived from the artefact's agency, id and version",
    "@isExternalReference=false": "the schema default",
    "@isFinal=false": "the schema default",
    "@isPartial=false": "the schema default",
    "@leveled=false": "the schema default",
    "@maxOccurs=1": "the schema default",
    "@minOccurs=1": "the schema default",
    "@usage=optional": "the schema default",
    _EMPTY: "an element with no text, no children and no attribute but xml:lang says nothing",
}

_BOOLEAN = frozenset({"true", "false"})

#: Non-boolean attributes the SDMX-ML XSDs give a default, so their value
#: decides whether omitting them loses anything.
_KEYED_BY_VALUE = frozenset({"maxOccurs", "minOccurs", "usage"})

_HEADER = "Header"
_STRUCTURES = "Structures"


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def census(source: Path | bytes) -> Counter[str]:
    """Path → occurrences, for every element and attribute below `Structures`.

    Streams, so a 700 MB document costs the depth of its tree in memory rather
    than its size.
    """
    walker = _Walker()
    target = source if isinstance(source, Path) else _BytesSource(source)
    for event, element in etree.iterparse(target, events=("start", "end"), huge_tree=True, remove_comments=True):
        tag = element.tag
        if not isinstance(tag, str):
            continue
        if event == "start":
            walker.start(_local(tag))
        else:
            walker.end(_local(tag), element)
            # The usual streaming idiom: an ended element's subtree is
            # counted, so drop it and the siblings already ended before it.
            element.clear(keep_tail=False)
            parent = element.getparent()
            while parent is not None and element.getprevious() is not None:
                del parent[0]
    return walker.counts


class _Walker:
    """The path state `census` carries between `start` and `end` events.

    An element is counted at its `end`, when whether it is empty is known.
    """

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self._path: list[str] = []
        #: One entry per open element: whether it added a step to `_path`, so
        #: its `end` knows whether to remove one, and the path it is counted
        #: under, or None for one that is not counted.
        self._open: list[tuple[bool, str | None]] = []
        self._in_header = 0
        self._below_structures = False

    def start(self, name: str) -> None:
        if name == _HEADER and not self._below_structures:
            self._in_header += 1
        if self._in_header or not self._below_structures:
            self._below_structures = self._below_structures or name == _STRUCTURES
            self._open.append((False, None))
            return
        step = not self._path or self._path[-1] != name
        if step:
            self._path.append(name)
        # A container (`Codelists`) is not counted: how many there are depends
        # on how a document was batched or chunked, never on what it says.
        self._open.append((step, "/".join(self._path) if len(self._path) > 1 else None))

    def end(self, name: str, element: etree._Element) -> None:
        step, key = self._open.pop()
        if key is not None:
            self._count(key, element)
        if step:
            self._path.pop()
        if name == _HEADER and self._in_header:
            self._in_header -= 1
        if name == _STRUCTURES:
            self._below_structures = False

    def _count(self, key: str, element: etree._Element) -> None:
        attributes = [(_local(a), v) for a, v in element.attrib.items()]
        if _is_empty(element, attributes):
            key += _EMPTY
        self.counts[key] += 1
        for name, value in attributes:
            suffix = f"={value}" if value in _BOOLEAN or name in _KEYED_BY_VALUE else ""
            self.counts[f"{key}/@{name}{suffix}"] += 1


def _is_empty(element: etree._Element, attributes: list[tuple[str, str]]) -> bool:
    """No text, no children, and no attribute but a language: nothing to keep."""
    return len(element) == 0 and not (element.text or "").strip() and all(n == "lang" for n, _ in attributes)


class _BytesSource:
    """A file-like over `bytes`, since `iterparse` wants a file or a path."""

    def __init__(self, data: bytes) -> None:
        self._data = memoryview(data)
        self._at = 0

    def read(self, size: int = -1) -> bytes:
        end = len(self._data) if size < 0 else self._at + size
        chunk = bytes(self._data[self._at : end])
        self._at = min(end, len(self._data))
        return chunk


def _derivable(path: str) -> bool:
    # An empty element's own path, and its `@lang`, are both under `_EMPTY`.
    return _EMPTY in path or any(path.endswith(f"/{d}") for d in DERIVABLE)


def lost(before: Counter[str], after: Counter[str]) -> tuple[dict[str, int], dict[str, int]]:
    """Paths whose count fell from `before` to `after`, and by how much.

    Returns:
        The real losses, then the `DERIVABLE` ones.
    """
    real: dict[str, int] = {}
    derivable: dict[str, int] = {}
    for path, n in sorted(before.items()):
        if n > after[path]:
            bucket = derivable if _derivable(path) else real
            bucket[path] = n - after[path]
    return real, derivable


def gained(before: Counter[str], after: Counter[str]) -> dict[str, int]:
    """Paths whose count rose — the writer adding what the source did not say."""
    return {path: n - before[path] for path, n in sorted(after.items()) if n > before[path]}
