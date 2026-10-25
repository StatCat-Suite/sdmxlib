"""Sweep a whole registry, and reduce the result to a committable summary.

Two outputs per registry:

- `out/reports/<REGISTRY>.json` — everything, with one example per rule key.
  Local only: it names thousands of real artefacts and changes every fetch.
- `summaries/<REGISTRY>.json` — the counts alone, keys sorted, each finding
  map cut to its most frequent rows. Committed, so a milestone-20 MR's diff
  shows how its numbers moved and `delta` can print the change.
"""

from __future__ import annotations

import json
import multiprocessing
import sys
import time
from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from pathlib import Path
from resource import RUSAGE_SELF, getrusage

import httpx2
from attrs import frozen

from bench.sweep.analyse import (
    DocumentReport,
    Totals,
    analyse,
    analyse_chunk,
    chunk_files,
    cross_api,
    document_report,
    is_split,
    rule_key,
)
from bench.sweep.capacity import Capacity, estimate
from bench.sweep.endpoints import CROSS_API_PAIRS, Api, Endpoint, endpoints_of
from bench.sweep.fetch import raw_path, read_meta
from bench.sweep.split import CHUNK_BYTES
from sdmxlib.model.message import StructureMessage

SUMMARIES = Path(__file__).parent / "summaries"

#: Rows kept per finding map in a committed summary. The full report has all
#: of them; the summary is for reading in a diff, and a map with hundreds of
#: one-off keys means the rule-key normalisation needs work, which the
#: `(+N more)` row makes visible rather than hiding.
_ROWS = 40


@frozen
class Doc:
    """One cached document: an endpoint and a resource."""

    endpoint: Endpoint
    resource: str

    @property
    def label(self) -> str:
        return f"{self.endpoint.key} {self.resource}"


@frozen
class Whole:
    """Documents analysed whole in one job: one, or a 2.1/3.0 pair diffed together.

    A pair is one job because `cross_api` needs both parsed messages at once,
    and handing a parsed message between processes would cost more than
    parsing it.
    """

    docs: tuple[Doc, ...]


@frozen
class Split:
    """A document too large to analyse whole: one job per chunk.

    `pair` names the cross-API entry it would have had, so the report says the
    comparison was skipped rather than leaving the reader to wonder.
    """

    doc: Doc
    pair: str | None


@frozen
class Task:
    """A function to run in a worker, with what it is expected to cost."""

    run: Callable[..., object]
    args: tuple[object, ...]
    estimate: int
    label: str


@frozen
class Outcome:
    """A task's result, its wall time, and its peak memory (None in-process)."""

    result: object
    seconds: float
    peak_mb: int | None


_SKIPPED = {"skipped": "a side was analysed in chunks; the comparison needs both whole"}


def sweep(root: Path, registry: str, *, capacity: Capacity, resources: set[str] | None = None) -> dict[str, object]:
    """Analyse every cached document of `registry`, and diff its two APIs.

    Two phases through the same scheduler: split every large document into
    chunk files, then analyse every whole document and every chunk as
    separate jobs. Results are assembled in report order, and chunk counts
    merged in chunk order, so the report does not depend on the schedule.
    """
    endpoints = {e.api: e for e in endpoints_of(registry)}
    report: dict[str, object] = {"registry": registry, "engine": next(iter(endpoints.values())).engine}
    apis: dict[str, dict[str, object]] = {}
    for api, endpoint in endpoints.items():
        apis[api] = {"base": endpoint.base, "fetched": _fetched(root, endpoint), "resources": {}}
    report["apis"] = apis
    pairs: dict[str, str] = dict(CROSS_API_PAIRS) if {"2.1", "3.0"} <= endpoints.keys() else {}
    units = _units(root, endpoints, pairs, resources)
    chunk_lists = _split_all(root, units, capacity)
    outcomes = iter(_schedule(_analysis_tasks(root, units, chunk_lists), capacity))
    crosses: dict[str, object] = {}
    for unit in units:
        if isinstance(unit, Whole):
            _place_whole(apis, crosses, unit, next(outcomes))
            continue
        _resources(apis, unit.doc.endpoint.api)[unit.doc.resource] = _assemble(root, unit, chunk_lists[unit], outcomes)
        if unit.pair is not None:
            crosses[unit.pair] = _SKIPPED
    if pairs:
        report["cross_api"] = dict(sorted(crosses.items()))
    return report


def _split_all(root: Path, units: list[Whole | Split], capacity: Capacity) -> dict[Split, list[Path] | str]:
    """Phase one: every large document into chunk files, in parallel."""
    splits = [unit for unit in units if isinstance(unit, Split)]
    tasks = [Task(_split_task, (root, u.doc), estimate(CHUNK_BYTES), f"split {u.doc.label}") for u in splits]
    return {unit: _paths(outcome.result) for unit, outcome in zip(splits, _schedule(tasks, capacity), strict=True)}


def _paths(result: object) -> list[Path] | str:
    if isinstance(result, list) and all(isinstance(p, Path) for p in result):
        return [p for p in result if isinstance(p, Path)]
    return str(result)


def _analysis_tasks(root: Path, units: list[Whole | Split], chunk_lists: dict[Split, list[Path] | str]) -> list[Task]:
    """Phase two: one task per whole document (or pair) and one per chunk, in report order."""
    tasks: list[Task] = []
    for unit in units:
        if isinstance(unit, Whole):
            cost = sum(estimate(_size(root, d)) for d in unit.docs)
            tasks.append(Task(_whole_task, (root, unit.docs), cost, " + ".join(d.label for d in unit.docs)))
            continue
        paths = chunk_lists[unit]
        if isinstance(paths, str):
            continue
        for index, path in enumerate(paths):
            label = f"{unit.doc.label} chunk {index + 1}/{len(paths)}"
            tasks.append(Task(_chunk_task, (unit.doc.endpoint.api, path, index), estimate(path.stat().st_size), label))
    return tasks


def _place_whole(apis: dict[str, dict[str, object]], crosses: dict[str, object], unit: Whole, outcome: Outcome) -> None:
    result = outcome.result
    if not isinstance(result, WholeResult):
        return
    for doc, found in zip(unit.docs, result.reports, strict=True):
        if outcome.peak_mb is not None:
            found["peak_mb"] = outcome.peak_mb
        _resources(apis, doc.endpoint.api)[doc.resource] = found
    if result.cross is not None:
        crosses[" / ".join(d.resource for d in unit.docs)] = result.cross


def _assemble(root: Path, unit: Split, paths: list[Path] | str, outcomes: Iterator[Outcome]) -> DocumentReport:
    """Merge a split document's chunk counts, in chunk order, into its report."""
    doc = unit.doc
    if isinstance(paths, str):
        report = document_report(root, doc.endpoint, doc.resource, None)
        report["error"] = f"split crashed: {paths}"
        return report
    totals = Totals(doc.endpoint.api)
    seconds = 0.0
    peaks: list[int] = []
    for _ in paths:
        outcome = next(outcomes)
        if isinstance(outcome.result, Totals):
            totals.merge(outcome.result)
        seconds += outcome.seconds
        if outcome.peak_mb is not None:
            peaks.append(outcome.peak_mb)
    report = document_report(root, doc.endpoint, doc.resource, totals, seconds=seconds)
    if peaks:
        report["peak_mb"] = max(peaks)
    return report


def _units(
    root: Path, endpoints: dict[Api, Endpoint], pairs: dict[str, str], resources: set[str] | None
) -> list[Whole | Split]:
    """Every document as a unit of work, in the order the report lists them."""
    paired_30 = set(pairs.values())
    units: list[Whole | Split] = []
    for api, endpoint in endpoints.items():
        for resource in endpoint.resources:
            if resources is not None and resource not in resources:
                continue
            if api == "3.0" and resource in paired_30:
                continue  # placed with its 2.1 partner
            doc = Doc(endpoint, resource)
            partner = pairs.get(resource) if api == "2.1" else None
            docs = (doc,) if partner is None else (doc, Doc(endpoints["3.0"], partner))
            if not any(is_split(raw_path(root, d.endpoint, d.resource)) for d in docs):
                units.append(Whole(docs))
                continue
            name = f"{resource} / {partner}" if partner is not None else None
            for one in docs:
                body = raw_path(root, one.endpoint, one.resource)
                units.append(Split(one, name) if is_split(body) else Whole((one,)))
                name = None  # name the skipped comparison once
    return units


def _size(root: Path, doc: Doc) -> int:
    body = raw_path(root, doc.endpoint, doc.resource)
    return body.stat().st_size if body.exists() else 0


def _schedule(tasks: list[Task], capacity: Capacity) -> list[Outcome]:
    """Run `tasks` within `capacity`, returning their outcomes in task order.

    Largest first, so the long jobs start early and the small ones fill the
    gaps. A task is launched only while a worker is free and its estimate fits
    in what the running tasks leave of the memory budget — except that a task
    always runs when nothing else is, so one bigger than the whole budget
    still runs, alone.
    """
    if capacity.workers == 1:
        return [_measured(task.run, task.args, task.label, peak=False) for task in tasks]
    outcomes: dict[int, Outcome] = {}
    pending = sorted(range(len(tasks)), key=lambda i: tasks[i].estimate, reverse=True)
    budget = capacity.budget if capacity.budget is not None else sum(task.estimate for task in tasks)
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=capacity.workers, mp_context=context, max_tasks_per_child=1) as pool:
        running: dict[Future[Outcome], int] = {}
        while pending or running:
            in_flight = sum(tasks[i].estimate for i in running.values())
            while pending and len(running) < capacity.workers:
                fits = next((i for i in pending if in_flight + tasks[i].estimate <= budget or not running), None)
                if fits is None:
                    break
                pending.remove(fits)
                task = tasks[fits]
                running[pool.submit(_measured, task.run, task.args, task.label, peak=True)] = fits
                in_flight += task.estimate
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                outcomes[running.pop(future)] = future.result()
    return [outcomes[i] for i in range(len(tasks))]


def _measured(run: Callable[..., object], args: tuple[object, ...], label: str, *, peak: bool) -> Outcome:
    """Run one task, timing it and, in a fresh worker, reading its peak memory."""
    start = time.perf_counter()
    result = run(*args)
    seconds = time.perf_counter() - start
    peak_mb = _peak_mb() if peak else None
    memory = f", peak {peak_mb:,} MB" if peak_mb is not None else ""
    print(f"  {label} ({seconds:.0f}s{memory})", flush=True)
    return Outcome(result=result, seconds=seconds, peak_mb=peak_mb)


@frozen
class WholeResult:
    """What a `Whole` job found."""

    reports: tuple[DocumentReport, ...]
    cross: dict[str, object] | None


def _whole_task(root: Path, docs: tuple[Doc, ...]) -> WholeResult:
    """Analyse whole documents, and diff them when they are a pair."""
    found = [_one(root, doc) for doc in docs]
    cross = None
    match found:
        case [(_, left), (_, right)] if left is not None and right is not None:
            cross = cross_api(left, right)
        case _:
            pass
    return WholeResult(reports=tuple(report for report, _ in found), cross=cross)


def _split_task(root: Path, doc: Doc) -> list[Path] | str:
    """Split one document into chunk files; a crash comes back as its message."""
    try:
        return chunk_files(root, doc.endpoint, doc.resource)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"[:300]


def _chunk_task(api: str, path: Path, index: int) -> Totals:
    """One chunk's counts; a crash is counted as that chunk's parse error."""
    try:
        return analyse_chunk(api, path, index)
    except Exception as exc:
        totals = Totals(api)
        totals.pieces = 1
        totals.parse_errors.add(f"sweep crashed: {type(exc).__name__}: {rule_key(str(exc))}", f"chunk {index}")
        return totals


def _peak_mb() -> int:
    """This process's peak resident memory. Per task, since each runs in a fresh worker."""
    peak = getrusage(RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS and kilobytes on Linux.
    return peak // (1024 * 1024) if sys.platform == "darwin" else peak // 1024


def _resources(apis: dict[str, dict[str, object]], api: str) -> dict[str, object]:
    found = apis[api]["resources"]
    if not isinstance(found, dict):
        msg = f"resources for {api} is not a mapping"
        raise TypeError(msg)
    return found


def _one(root: Path, doc: Doc) -> tuple[DocumentReport, StructureMessage | None]:
    try:
        return analyse(root, doc.endpoint, doc.resource)
    except Exception as exc:
        # A crash in one stage of one document is a finding, not a reason to
        # lose the other forty documents' worth of sweep.
        return DocumentReport(status=-1, error=f"sweep crashed: {type(exc).__name__}: {exc}"[:300]), None


def _fetched(root: Path, endpoint: Endpoint) -> str | None:
    stamps = [
        meta["fetched_at"]
        for resource in endpoint.resources
        if (meta := read_meta(raw_path(root, endpoint, resource))) is not None
    ]
    return min(stamps) if stamps else None


#: Fields that measure the machine, not the code: left out of the summary.
_MACHINE = frozenset({"seconds", "peak_mb"})


def summarise(report: object) -> object:
    """`report` with examples and timings removed and long maps cut.

    A finding map is recognised by shape — every value a `{"n": …, "example":
    …}` row — so a new stage in `analyse` is summarised without this function
    learning its name.
    """
    if isinstance(report, dict):
        if report and all(isinstance(v, dict) and "n" in v and "example" in v for v in report.values()):
            counts = {str(k): v["n"] for k, v in report.items()}
            return _cut(counts)
        return {str(k): summarise(v) for k, v in sorted(report.items()) if k not in _MACHINE}
    if isinstance(report, list):
        return [summarise(v) for v in report]
    return report


def _cut(counts: dict[str, object]) -> dict[str, object]:
    if len(counts) <= _ROWS:
        return dict(sorted(counts.items()))
    kept = dict(sorted(list(counts.items())[:_ROWS]))
    kept[f"(+{len(counts) - _ROWS} more keys)"] = sum(int(str(n)) for n in list(counts.values())[_ROWS:])
    return kept


def write(root: Path, registry: str, report: dict[str, object]) -> tuple[Path, Path]:
    """Write the full report and the summary. Returns their paths."""
    full = root / "reports" / f"{registry}.json"
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    summary = SUMMARIES / f"{registry}.json"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps(summarise(report), indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    return full, summary


def render(summary: dict[str, object]) -> str:
    """The non-empty findings of a summary, as indented text for a terminal or an MR."""
    lines = [f"# {summary.get('registry')} (engine: {summary.get('engine')})"]
    apis = summary.get("apis")
    for api, entry in _items(apis):
        lines.append(f"\n## SDMX {api} API — fetched {_get(entry, 'fetched')}")
        unserved: list[str] = []
        for resource, doc in _items(_get(entry, "resources")):
            status = _get(doc, "status")
            if status != httpx2.codes.OK:
                unserved.append(f"{resource}({status})")
                continue
            lines.extend(_document(resource, doc))
        if unserved:
            lines.append(f"not served: {', '.join(unserved)}")
    crosses = summary.get("cross_api")
    if crosses:
        lines.append("\n## 2.1 vs 3.0 reading of the same artefact (2.1 → 3.0)")
        for pair, cross in _items(crosses):
            head = f"{pair}: paired={_get(cross, 'paired')}"
            head += f" only_2.1={_get(cross, 'only_2.1')} only_3.0={_get(cross, 'only_3.0')}"
            lines.append(head)
            lines.extend(_rows(_get(cross, "diff"), 1))
    return "\n".join(lines)


def _document(resource: str, doc: object) -> list[str]:
    counts = ", ".join(f"{k} {v}" for k, v in _items(_get(doc, "artefacts")))
    out = [f"{resource}: {counts or 'no artefacts'}"]
    if (crash := _get(doc, "error")) or (crash := _get(doc, "parse_error")):
        out.append(f"  FAILED: {crash}")
    for section in ("read_refused", "store_refused", "store_diff", "read_lost", "read_gained"):
        if rows := _rows(_get(doc, section), 2):
            out.extend([f"  {section}:", *rows])
    for target, report in _items(_get(doc, "targets")):
        for section in ("refused", "write_error", "schema_invalid", "gaps"):
            if rows := _rows(_get(report, section), 2):
                out.extend([f"  → {target} {section}:", *rows])
    return out if len(out) > 1 else []


def _items(node: object) -> list[tuple[str, object]]:
    return [(str(k), v) for k, v in node.items()] if isinstance(node, dict) else []


def _get(node: object, key: str) -> object:
    return node.get(key) if isinstance(node, dict) else None


def _rows(node: object, depth: int) -> list[str]:
    return [f"{'  ' * depth}{n!s:>7}  {key}" for key, n in _items(node)]


def flatten(node: object, prefix: str = "") -> dict[str, object]:
    """Every leaf of a summary as `a / b / c → value`, for comparing two of them."""
    if not isinstance(node, dict):
        return {prefix: node}
    out: dict[str, object] = {}
    for key, child in node.items():
        if key in {"fetched", "bytes"}:
            continue  # moves on every fetch and says nothing about the code
        out.update(flatten(child, f"{prefix} / {key}" if prefix else str(key)))
    return out


def delta(before: object, after: object) -> list[str]:
    """What changed between two summaries, one line per leaf that moved."""
    old, new = flatten(before), flatten(after)
    return [
        f"{old.get(key, '—')!s:>8} → {new.get(key, '—')!s:<8} {key}"
        for key in sorted(old.keys() | new.keys())
        if old.get(key) != new.get(key)
    ]
