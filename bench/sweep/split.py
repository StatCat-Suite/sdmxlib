"""Cut an SDMX-ML structure document into smaller, valid ones.

Eurostat's 3.0 codelists, every version, are one 3.1 GB document; IMF's
codelists are 685 MB and parse to ~8 GB resident. The analysis holds a whole
parsed message, so above a size it reads the document in pieces instead: each
chunk is the original root, a copy of its header, and as many whole artefacts
as fit under a byte budget, each still inside its original container element.

Streaming, so the cost is one chunk at a time. Artefacts are never split, so
each chunk is a document a registry could have sent, and the stages that run
on it see exactly what they would have seen on the whole.
"""

from __future__ import annotations

import copy
import json
import shutil
from collections.abc import Iterator
from pathlib import Path

from lxml import etree

#: Documents above this are analysed in chunks.
SPLIT_ABOVE = 256 * 1024 * 1024

#: Serialised artefact bytes per chunk.
CHUNK_BYTES = 96 * 1024 * 1024

#: Element depths in a structure message: the root, then `Header` and
#: `Structures`, then a container (`Codelists`), then the artefacts.
_ROOT, _SECTION, _ARTEFACT = 1, 2, 4

_HEADER = "Header"
_STRUCTURES = "Structures"


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


class _Chunker:
    def __init__(self) -> None:
        self.root: etree._Element | None = None
        self.header: etree._Element | None = None
        self.structures_tag = ""
        self.pending: dict[str, list[bytes]] = {}
        self.size = 0
        self._depth = 0

    def start(self, element: etree._Element) -> None:
        self._depth += 1
        if self._depth == _ROOT:
            self.root = element
        elif self._depth == _SECTION and _local(str(element.tag)) == _STRUCTURES:
            self.structures_tag = str(element.tag)

    def end(self, element: etree._Element) -> bool:
        """Handle an `end` event. Returns whether it completed an artefact."""
        ended_at = self._depth
        self._depth -= 1
        if ended_at == _SECTION and _local(str(element.tag)) == _HEADER:
            self.header = copy.deepcopy(element)
            return False
        if ended_at != _ARTEFACT:
            return False
        parent = element.getparent()
        if parent is None:
            return False
        self.take(str(parent.tag), etree.tostring(element))
        # The streaming idiom: drop what is already serialised.
        element.clear(keep_tail=False)
        while element.getprevious() is not None:
            del parent[0]
        return True

    def take(self, container: str, artefact: bytes) -> None:
        self.pending.setdefault(container, []).append(artefact)
        self.size += len(artefact)

    def emit(self) -> bytes:
        if self.root is None:
            msg = "no root element seen before the first artefact"
            raise ValueError(msg)
        root = etree.Element(self.root.tag, nsmap=self.root.nsmap)
        if self.header is not None:
            root.append(copy.deepcopy(self.header))
        structures = etree.SubElement(root, self.structures_tag)
        for container, artefacts in self.pending.items():
            holder = etree.SubElement(structures, container)
            for artefact in artefacts:
                holder.append(etree.fromstring(artefact, parser=etree.XMLParser(huge_tree=True)))
        self.pending.clear()
        self.size = 0
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def chunks(path: Path, budget: int = CHUNK_BYTES) -> Iterator[bytes]:
    """`path` as a sequence of smaller documents of whole artefacts."""
    chunker = _Chunker()
    for event, element in etree.iterparse(path, events=("start", "end"), huge_tree=True, remove_comments=True):
        if not isinstance(element.tag, str):
            continue
        if event == "start":
            chunker.start(element)
        elif chunker.end(element) and chunker.size >= budget:
            yield chunker.emit()
    if chunker.pending:
        yield chunker.emit()


def chunk_dir(root: Path, key: str, resource: str) -> Path:
    """Where the chunks of one cached document are kept."""
    return root / "chunks" / key / resource


def ensure_chunks(body: Path, out: Path, budget: int = CHUNK_BYTES) -> list[Path]:
    """Split `body` into chunk files under `out`, or reuse the ones already there.

    Written to disk rather than handed between processes: a chunk is ~100 MB,
    and each one is analysed by a different worker. The manifest records the
    source's size and mtime and the budget, so a re-fetched document, or a
    changed budget, is split again rather than analysed from stale chunks.

    Returns:
        The chunk files, in document order.
    """
    stat = body.stat()
    want = {"source_size": stat.st_size, "source_mtime_ns": stat.st_mtime_ns, "budget": budget}
    manifest = out / "manifest.json"
    if manifest.exists():
        have = json.loads(manifest.read_text())
        paths = [out / name for name in have.get("chunks", [])]
        if {k: have.get(k) for k in want} == want and all(p.exists() for p in paths):
            return paths
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    paths = []
    for index, piece in enumerate(chunks(body, budget)):
        path = out / f"{index:04d}.xml"
        path.write_bytes(piece)
        paths.append(path)
    # Written last, so an interrupted split is never mistaken for a finished one.
    manifest.write_text(json.dumps({**want, "chunks": [p.name for p in paths]}, indent=1) + "\n")
    return paths
