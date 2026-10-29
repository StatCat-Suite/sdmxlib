"""Run one cached registry document through every stage a real one meets.

For each document: count its paths, parse it, put every artefact through
the store and read it back, and for each target format
ask what is refused, what is declared lost, whether what is written is
schema-valid, and — for the format the document came in — what the reader
dropped on the way.

Every finding is reduced to a **rule key**: the message with its specific
values (URNs, quoted ids, numbers) taken out. Counting keys is what makes two
runs comparable, and what makes the summary a table of *kinds* of problem
rather than a list of artefacts. One example of each key is kept in the full
report so a count can always be traced back to a real artefact.

Counts are of **artefacts** for every per-artefact stage (read_refused,
refused, gaps, store) and of **occurrences** for the two stages that read a whole
document (schema errors and the path census). The summary says which is which.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Any, TypedDict

from lxml import etree

from bench.sweep.census import census, gained, lost
from bench.sweep.endpoints import Endpoint
from bench.sweep.fetch import raw_path, read_meta
from bench.sweep.modeldiff import diff
from bench.sweep.split import SPLIT_ABOVE, chunk_dir, ensure_chunks
from sdmxlib.formats import parse, serialize
from sdmxlib.formats.gaps import unrepresentable
from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import SupportsUrn, artefact_urn
from sdmxlib.model.validation import ValidationError, check_serialisable
from tests.support.json_schema import compile_json_schema
from tests.support.xsd import SchemaVersion, compile_structure_schema

#: Every format the sweep projects into. 3.1 is included although no registry
#: serves it yet: the constraint split (#69) is 3.1's, and the cost of 3.1 is
#: worth knowing before anyone serves it.
TARGETS: tuple[str, ...] = ("2.1", "3.0", "3.1", "json")

#: Artefacts per written batch. Bounds the size of one schema validation (the
#: IMF codelists are 685 MB as a single document) and keeps a writer crash
#: attributable to a few hundred artefacts rather than to a whole registry.
_BATCH = 200

#: A URN through its `(version)` and any `.item` suffix, but not the bracket a
#: message may wrap it in: `(urn:…=X:CL(1.0))` must leave the outer `)`.
_URN = re.compile(r"urn:sdmx:[^\s'\",;()]+(?:\([^()\s]*\))?(?:\.[^\s'\",;()]+)?")
_QUOTED = re.compile("'[^']*'|\"[^\"]*\"|\u2018[^\u2019]*\u2019")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)*\b")
_SPACE = re.compile(r"\s+")
#: `3 node id(s) repeat in another branch: BYINS at A.BYINS, B.BYINS and 1 more.`
#: A counted finding lists its first few examples, and how many it lists varies
#: per artefact (#450). The list runs to the sentence's end, a `. ` — an
#: example may hold a `.` (a hierarchy path) but not a `. `.
_EXAMPLES = re.compile(r"(\b\d+ [\w ]*\(s\)[^:]*: ).*?(?=\. |\.?$)")
_KEY_LENGTH = 200


def rule_key(text: str) -> str:
    """`text` with its specific values replaced, so equal problems count together."""
    first = text.strip().splitlines()[0] if text.strip() else text
    key = _EXAMPLES.sub(r"\1<examples>", first)
    key = _URN.sub("<urn>", key)
    key = _QUOTED.sub("<q>", key)
    key = _NUMBER.sub("<n>", key)
    key = _SPACE.sub(" ", key).strip()
    return key[:_KEY_LENGTH]


#: `DataStructure:BIS_CBPOL.attributes[TITLE]` → `DataStructure.attributes[]`:
#: an issue path names the artefact and the item, and both vary per finding.
#: `Registry.add` names it `Class:agency:id` (#419), and an agency may nest —
#: `IMF.STA.DS` — so the agency runs to its colon and only the id stops at a dot.
_PATH_OWNER = re.compile(r"^(\w+):(?:[^:\s]+:)?[^.\s:]+")
_PATH_INDEX = re.compile(r"\[[^\]]*\]")


def issue_key(path: str, message: str) -> str:
    """A `ValidationIssue` as a rule key: the path without ids, the message without values."""
    return rule_key(f"{_PATH_INDEX.sub('[]', _PATH_OWNER.sub(r'\1', path))}: {message}")


def refusal_key(reason: str) -> str:
    """A `Refusal` reason as a rule key, without the item ids its paths carry.

    A reader refusing what `add` would names each broken rule by its path
    (#419), and the item ids in a path vary per finding.
    """
    return rule_key(_PATH_INDEX.sub("[]", reason))


def _issue_keys(exc: ValidationError) -> set[str]:
    return {issue_key(issue.path, issue.message) for issue in exc.issues}


_NAMESPACED = re.compile(r"\{[^}]*\}")
_XSD_VALUE = re.compile(r"(value|attribute value) '[^']*'")
_XSD_KEY_SEQUENCE = re.compile(r"key-sequence \[[^\]]*\]")


def xsd_key(message: str) -> str:
    """An XSD error with its value removed but its element and facet kept.

    `rule_key` would reduce every lxml message to `Element <q>: [facet <q>]`,
    which counts all schema errors as one. The element name and the facet are
    the finding; only the offending value varies.
    """
    key = _NAMESPACED.sub("", message.strip().splitlines()[0])
    key = _XSD_VALUE.sub(r"\1 <v>", key)
    key = _XSD_KEY_SEQUENCE.sub("key-sequence [<v>]", key)
    return _SPACE.sub(" ", key)[:_KEY_LENGTH]


def _error_key(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {rule_key(str(exc))}"


class Tally:
    """Key → count, with the first example of each key."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.examples: dict[str, str] = {}

    def add(self, key: str, example: str, n: int = 1) -> None:
        """Count `key` `n` times, keeping `example` if it is the first."""
        self.counts[key] += n
        self.examples.setdefault(key, example)

    def merge(self, other: Tally) -> None:
        """Add `other`'s counts; its examples only for keys this has not seen.

        Merging chunk tallies in chunk order gives exactly what one tally fed
        every chunk in that order would: the same counts, the same first
        examples, the same key order for `most_common` to break ties with.
        """
        for key, n in other.counts.items():
            self.add(key, other.examples[key], n)

    def as_report(self) -> dict[str, dict[str, object]]:
        """Most frequent first, each with its count and example."""
        return {key: {"n": n, "example": self.examples[key]} for key, n in self.counts.most_common()}


class TargetReport(TypedDict):
    """What projecting one document into one format did."""

    written: int
    refused: dict[str, dict[str, object]]
    gaps: dict[str, dict[str, object]]
    write_error: dict[str, dict[str, object]]
    schema_invalid: dict[str, dict[str, object]]


class DocumentReport(TypedDict, total=False):
    """Everything the sweep learned from one cached document."""

    status: int
    bytes: int
    error: str | None
    seconds: float
    artefacts: dict[str, int]
    parse_error: str
    read_refused: dict[str, dict[str, object]]
    store_refused: dict[str, dict[str, object]]
    store_diff: dict[str, dict[str, object]]
    targets: dict[str, TargetReport]
    read_lost: dict[str, int]
    read_lost_derivable: dict[str, int]
    read_gained: dict[str, int]
    read_gained_derivable: dict[str, int]
    chunks: int
    peak_mb: int


def is_split(body: Path) -> bool:
    """Whether `body` is analysed in chunks rather than whole."""
    return body.exists() and body.stat().st_size > SPLIT_ABOVE


def analyse(root: Path, endpoint: Endpoint, resource: str) -> tuple[DocumentReport, StructureMessage | None]:
    """Run one cached document through every stage, in this process.

    A document above `split.SPLIT_ABOVE` is analysed chunk by chunk from the
    files `split.ensure_chunks` keeps — the same files, in the same order, the
    parallel path hands to separate workers, so the two give the same counts.
    A split document returns no message: the cross-API comparison needs the
    whole of both documents at once.

    Returns:
        The report, and the parsed message for the cross-API comparison
        (None when there was no document, it did not parse, or it was split).
    """
    body = raw_path(root, endpoint, resource)
    if not body.exists():
        return document_report(root, endpoint, resource, None), None
    start = time.perf_counter()
    if is_split(body):
        totals = Totals(endpoint.api)
        for index, path in enumerate(chunk_files(root, endpoint, resource)):
            totals.merge(analyse_chunk(endpoint.api, path, index))
        return document_report(root, endpoint, resource, totals, seconds=time.perf_counter() - start), None
    totals = Totals(endpoint.api)
    message = totals.take(body.read_bytes(), 0)
    return document_report(root, endpoint, resource, totals, seconds=time.perf_counter() - start), message


def chunk_files(root: Path, endpoint: Endpoint, resource: str) -> list[Path]:
    """The chunk files of a split document, splitting it first if need be."""
    return ensure_chunks(raw_path(root, endpoint, resource), chunk_dir(root, endpoint.key, resource))


def analyse_chunk(api: str, path: Path, index: int) -> Totals:
    """One chunk's counts, for merging into its document's."""
    totals = Totals(api)
    totals.take(path.read_bytes(), index)
    return totals


def document_report(
    root: Path, endpoint: Endpoint, resource: str, totals: Totals | None, *, seconds: float | None = None
) -> DocumentReport:
    """A document's report: what the fetch said, then what the stages found."""
    meta = read_meta(raw_path(root, endpoint, resource))
    if meta is None:
        return DocumentReport(status=0, error="not fetched")
    report = DocumentReport(status=meta["status"], bytes=meta["bytes"], error=meta["error"])
    if totals is None:
        return report
    totals.fill(report)
    if totals.pieces > 1:
        report["chunks"] = totals.pieces
    if seconds is not None:
        report["seconds"] = round(seconds, 2)
    return report


class Totals:
    """Every stage's counts for one document, or one chunk of it — mergeable."""

    def __init__(self, api: str) -> None:
        self.api = api
        self.pieces = 0
        self.parse_errors = Tally()
        self.read_refused = Tally()
        self.artefacts: Counter[str] = Counter()
        self.store_refused = Tally()
        self.store_diff = Tally()
        self.targets = {target: _TargetTotals() for target in TARGETS}
        self.source: Counter[str] = Counter()
        self.written: Counter[str] = Counter()

    def take(self, data: bytes, index: int) -> StructureMessage | None:
        """Run one document (or chunk number `index`) through every stage."""
        self.pieces += 1
        self.source.update(census(data))
        try:
            # Not strict: a reader refuses an artefact that contradicts the
            # schema and reads the rest (#435), and the rest is what every
            # later stage is about. The refusals are a stage of their own.
            parsed = parse(data, version=self.api, strict=False)
        except Exception as exc:
            self.parse_errors.add(_error_key(exc), f"chunk {index}")
            return None
        if not isinstance(parsed, StructureMessage):
            self.parse_errors.add(f"not a structure message: {type(parsed).__name__}", f"chunk {index}")
            return None
        for refusal in parsed.refused:
            self.read_refused.add(
                f"{refusal.artefact_class}: {refusal_key(refusal.reason)}", f"{refusal.urn} — {refusal.reason[:200]}"
            )
        artefacts = parsed.all_artefacts()
        self.artefacts.update(type(a).__name__ for a in artefacts)
        _store(artefacts, self.store_refused, self.store_diff)
        for target, totals in self.targets.items():
            written = _project(artefacts, target, totals, census_out=target == self.api)
            if written is not None:
                self.written.update(written)
        return parsed

    def merge(self, other: Totals) -> None:
        """Add another piece's counts. Merge in chunk order: see `Tally.merge`."""
        self.pieces += other.pieces
        for mine, theirs in (
            (self.parse_errors, other.parse_errors),
            (self.read_refused, other.read_refused),
            (self.store_refused, other.store_refused),
            (self.store_diff, other.store_diff),
        ):
            mine.merge(theirs)
        self.artefacts.update(other.artefacts)
        self.source.update(other.source)
        self.written.update(other.written)
        for target, totals in self.targets.items():
            totals.merge(other.targets[target])

    def fill(self, report: DocumentReport) -> None:
        if self.parse_errors.counts:
            report["parse_error"] = "; ".join(self.parse_errors.counts)
        if self.parse_errors.counts and not self.artefacts:
            # Nothing parsed, so nothing was written back: the whole source is lost.
            report["read_lost"] = dict(sorted(self.source.items()))
            return
        report["artefacts"] = dict(sorted(self.artefacts.items()))
        report["read_refused"] = self.read_refused.as_report()
        report["store_refused"] = self.store_refused.as_report()
        report["store_diff"] = self.store_diff.as_report()
        report["targets"] = {target: totals.report() for target, totals in self.targets.items()}
        report["read_lost"], report["read_lost_derivable"] = lost(self.source, self.written)
        report["read_gained"], report["read_gained_derivable"] = gained(self.source, self.written)


class _TargetTotals:
    """One format's counts, summed over the pieces of one document."""

    def __init__(self) -> None:
        self.written = 0
        self.refused = Tally()
        self.gaps = Tally()
        self.write_error = Tally()
        self.invalid = Tally()

    def merge(self, other: _TargetTotals) -> None:
        self.written += other.written
        for mine, theirs in (
            (self.refused, other.refused),
            (self.gaps, other.gaps),
            (self.write_error, other.write_error),
            (self.invalid, other.invalid),
        ):
            mine.merge(theirs)

    def report(self) -> TargetReport:
        return TargetReport(
            written=self.written,
            refused=self.refused.as_report(),
            gaps=self.gaps.as_report(),
            write_error=self.write_error.as_report(),
            schema_invalid=self.invalid.as_report(),
        )


def _name(artefact: object) -> str:
    if isinstance(artefact, SupportsUrn):
        return str(artefact_urn(artefact))
    return repr(artefact)[:80]


def _store(artefacts: list[MaintainableArtefact], refused: Tally, differs: Tally) -> None:
    """Add every artefact to a fresh in-memory store, read each back, and diff."""
    with LocalRegistry(":memory:") as store:
        kept = []
        for artefact in artefacts:
            try:
                store.add(artefact)
            except ValidationError as exc:
                for key in _issue_keys(exc):
                    refused.add(key, f"{_name(artefact)} — {exc.issues[0]}")
            except Exception as exc:
                refused.add(_error_key(exc), _name(artefact))
            else:
                kept.append(artefact)
        for artefact in kept:
            try:
                back = store.get(artefact_urn(artefact), eager=True)
            except Exception as exc:
                differs.add(f"read back: {_error_key(exc)}", _name(artefact))
                continue
            for path, example in diff(artefact, back).items():
                differs.add(path, f"{_name(artefact)}: {example}")


def _project(
    artefacts: list[MaintainableArtefact], target: str, totals: _TargetTotals, *, census_out: bool
) -> Counter[str] | None:
    """Refusals, declared gaps, writer errors and schema errors for one format.

    Returns:
        The path census of what was written, when `census_out` asks for it.
    """
    accepted: list[MaintainableArtefact] = []
    refused: list[MaintainableArtefact] = []
    for artefact in artefacts:
        try:
            check_serialisable(StructureMessage.containing(artefact), version=target)
        except ValidationError as exc:
            for key in _issue_keys(exc):
                totals.refused.add(key, f"{_name(artefact)} — {exc.issues[0]}")
            refused.append(artefact)
            continue
        except Exception as exc:
            totals.refused.add(_error_key(exc), _name(artefact))
            refused.append(artefact)
            continue
        accepted.append(artefact)
        for gap in unrepresentable(artefact, target):
            totals.gaps.add(gap.field, f"{_name(artefact)} — {gap}")
    written = Counter[str]() if census_out else None
    for batch in _batches(accepted):
        for artefact, wire in _write(batch, target, totals.write_error):
            totals.written += 1 if artefact is not None else len(batch)
            _schema_errors(wire, target, totals.invalid)
            if written is not None and target != "json":
                written.update(census(wire))
    if written is not None and target != "json":
        written.update(_census_unchecked(refused, target))
    return written


def _census_unchecked(refused: list[MaintainableArtefact], target: str) -> Counter[str]:
    """The census of the refused artefacts, written without the check.

    The census measures the reader, not the write check: an artefact the check
    refuses was still read, so it is written for the census alone — or a
    refusal would read as every path it holds lost.
    """
    written = Counter[str]()
    for batch in _batches(refused):
        for _, wire in _write(batch, target, Tally()):
            written.update(census(wire))
    return written


def _batches(items: list[MaintainableArtefact]) -> Iterator[list[MaintainableArtefact]]:
    for at in range(0, len(items), _BATCH):
        yield items[at : at + _BATCH]


def _message(artefacts: list[MaintainableArtefact]) -> StructureMessage:
    slots: dict[str, list[object]] = {}
    for artefact in artefacts:
        slots.setdefault(type(artefact).msg_field, []).append(artefact)
    # Slot names are only known at runtime; the same one-local confinement
    # `StructureMessage.containing` uses.
    kwargs: dict[str, Any] = slots
    return StructureMessage(**kwargs)


def _write(batch: list[MaintainableArtefact], target: str, errors: Tally) -> Iterator[tuple[object | None, bytes]]:
    """Write `batch` whole, or artefact by artefact when the whole fails.

    Yields `(None, bytes)` for a batch written whole, and `(artefact, bytes)`
    for each artefact written alone after a batch failed — so a crash is
    attributed to the artefacts that cause it and the rest are still checked.
    """
    try:
        yield None, serialize(_message(batch), version=target, validate=False)
    except Exception:
        for artefact in batch:
            try:
                yield artefact, serialize(_message([artefact]), version=target, validate=False)
            except Exception as exc:
                errors.add(_error_key(exc), _name(artefact))
        return


_XSD: dict[str, SchemaVersion] = {"2.1": "2.1", "3.0": "3.0", "3.1": "3.1"}


@cache
def _xsd(target: str) -> etree.XMLSchema:
    return compile_structure_schema(_XSD[target])


def _schema_errors(wire: bytes, target: str, invalid: Tally) -> None:
    if target == "json":
        validator = compile_json_schema("structure")
        for error in validator.iter_errors(json.loads(wire)):
            where = "/".join("[]" if isinstance(p, int) else p for p in error.absolute_path)
            invalid.add(f"{where}: {rule_key(error.message)}", error.message[:200])
        return
    schema = _xsd(target)
    if schema.validate(etree.fromstring(wire, parser=etree.XMLParser(huge_tree=True))):
        return
    for entry in schema.error_log:
        invalid.add(xsd_key(entry.message), f"line {entry.line}: {entry.message[:200]}")


def _by_urn(message: StructureMessage) -> dict[str, object]:
    return {_name(a): a for a in message.all_artefacts()}


def cross_api(left: StructureMessage, right: StructureMessage) -> dict[str, object]:
    """Pair the two readings of each artefact by URN and diff them.

    `left` is the 2.1 reading, `right` the 3.0 one; a path's example reads
    `2.1 value → 3.0 value`.
    """
    a, b = _by_urn(left), _by_urn(right)
    differs = Tally()
    for urn in sorted(a.keys() & b.keys()):
        for path, example in diff(a[urn], b[urn]).items():
            differs.add(path, f"{urn}: {example}")
    return {
        "paired": len(a.keys() & b.keys()),
        "only_2.1": len(a.keys() - b.keys()),
        "only_3.0": len(b.keys() - a.keys()),
        "diff": differs.as_report(),
    }
