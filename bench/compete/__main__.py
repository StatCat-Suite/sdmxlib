"""`python -m bench.compete` — sdmxlib against pysdmx and sdmx1. See `bench/compete/README.md`.

Four questions, over one corpus held in every format: how fast each library
reads and writes, whether they agree on the content, what each keeps of what
the documents hold, and whether what each writes is schema-valid and readable
by the others.
"""

from __future__ import annotations

import argparse
import io
import statistics
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import httpx2

from bench.compete.libs import (
    LIBS,
    PYSDMX_DEFAULT,
    Fidelity,
    Key,
    Message,
    Summary,
    Times,
    differences,
    dropped,
    fidelity,
    label,
    read,
    refused_by_default,
    source_fidelity,
    summary,
    timed,
    write,
)
from sdmxlib.formats import parse, serialize
from sdmxlib.model.annotations import Annotation
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.message import StructureMessage

#: The corpus, fetched on first run and kept here (gitignored): it is BIS's
#: data, which the repository does not redistribute, and a cached copy keeps
#: reruns offline and comparable.
CACHE = Path(__file__).parent / "cache"
#: When the cached corpus was fetched, ISO 8601, UTC.
FETCHED = CACHE / "fetched"
SETS = ("codelist", "datastructure")
#: Wire format -> the corpus file's suffix.
FORMATS = {"2.1": "2.1.xml", "3.0": "3.0.xml", "json": "2.0.json"}
#: Wire format -> (URL template, Accept). BIS serves the same artefacts in all
#: three, so one snapshot holds the same content in each.
SOURCES = {
    "2.1": ("https://stats.bis.org/api/v1/{s}/BIS/all/latest", "application/vnd.sdmx.structure+xml;version=2.1"),
    "3.0": (
        "https://stats.bis.org/api/v2/structure/{s}/BIS/*/~",
        "application/vnd.sdmx.structure+xml;version=3.0.0",
    ),
    "json": (
        "https://stats.bis.org/api/v2/structure/{s}/BIS/*/~",
        "application/vnd.sdmx.structure+json;version=2.0.0",
    ),
}


# ── the corpus ───────────────────────────────────────────────────────────────


def _path(s: str, fmt: str) -> Path:
    return CACHE / f"bis-{s}-{FORMATS[fmt]}"


def corpus(*, refresh: bool) -> dict[tuple[str, str], bytes]:
    """Every (set, format) document, fetched once and then read from `CACHE`.

    All six are fetched together whenever any is missing, so the formats stay
    one snapshot; a partial cache would compare different days' content.
    """
    if refresh or not FETCHED.exists() or not all(_path(s, fmt).exists() for s in SETS for fmt in FORMATS):
        CACHE.mkdir(exist_ok=True)
        FETCHED.unlink(missing_ok=True)
        with httpx2.Client(timeout=httpx2.Timeout(30.0, read=300.0), follow_redirects=True) as client:
            for s in SETS:
                for fmt, (url, accept) in SOURCES.items():
                    print(f"fetching {s} {fmt} from BIS ...")
                    response = client.get(url.format(s=s), headers={"Accept": accept})
                    response.raise_for_status()
                    partial = _path(s, fmt).with_suffix(".part")
                    partial.write_bytes(response.content)
                    partial.replace(_path(s, fmt))
        # Written last: its presence marks a complete snapshot.
        FETCHED.write_text(datetime.now(UTC).isoformat(timespec="seconds"))
    return {(s, fmt): _path(s, fmt).read_bytes() for s in SETS for fmt in FORMATS}


# ── the run ──────────────────────────────────────────────────────────────────


def _read_all(data: dict[tuple[str, str], bytes], failures: dict[Key, str]) -> dict[Key, Message]:
    messages: dict[Key, Message] = {}
    for (s, fmt), raw in data.items():
        for lib in LIBS:
            try:
                messages[(lib, s, fmt)] = read(lib, raw)
            except Exception as error:  # a competitor's failure is a result, not a crash
                failures[(lib, s, fmt)] = f"{type(error).__name__}: {error}"
    return messages


def _time_reads(
    data: dict[tuple[str, str], bytes], messages: dict[Key, Message], failures: dict[Key, str], rounds: int
) -> Times:
    """Interleaved: every library once per round, so drift lands on all of them."""
    times: Times = {}
    for _ in range(rounds):
        for (s, fmt), raw in data.items():
            for lib in (*LIBS, PYSDMX_DEFAULT):
                readable = (lib if lib != PYSDMX_DEFAULT else "pysdmx", s, fmt) in messages
                if not readable or (lib, s, fmt) in failures:
                    continue
                try:
                    times.setdefault(("read", lib, s, fmt), []).append(timed(lambda lib=lib, raw=raw: read(lib, raw)))
                except Exception as error:  # pysdmx's validating read refuses invalid input
                    failures[(lib, s, fmt)] = f"{type(error).__name__}: {error}"
                    times.pop(("read", lib, s, fmt), None)
    return times


def _write_all(messages: dict[Key, Message], failures: dict[Key, str]) -> tuple[dict[Key, bytes], dict[Key, str]]:
    """Each library writes every format from its own read of the 2.1 file.

    Returns what each wrote, and the writes sdmxlib's default `serialize`
    would have refused, with why: those are timed (the writer still runs)
    but never reported as sdmxlib's output (#500).
    """
    written: dict[Key, bytes] = {}
    refusals: dict[Key, str] = {}
    for s in SETS:
        for lib in LIBS:
            if (source := messages.get((lib, s, "2.1"))) is None:
                continue
            for fmt in FORMATS:
                try:
                    written[(lib, s, fmt)] = write(lib, source, fmt)
                except Exception as error:
                    failures[("write " + lib, s, fmt)] = f"{type(error).__name__}: {error}"
                    continue
                if (refusal := refused_by_default(source, fmt)) is not None:
                    refusals[(lib, s, fmt)] = refusal
    return written, refusals


def _time_writes(written: dict[Key, bytes], messages: dict[Key, Message], times: Times, rounds: int) -> None:
    for _ in range(rounds):
        for lib, s, fmt in written:
            source = messages[(lib, s, "2.1")]
            times.setdefault(("write", lib, s, fmt), []).append(
                timed(lambda lib=lib, source=source, fmt=fmt: write(lib, source, fmt))
            )


def _cell(
    lib: str, times: list[float] | None, base: list[float] | None, failure: str | None, *, refused: bool = False
) -> str:
    if not times:
        return f"{lib} {'FAIL: ' + failure[:60] if failure else 'n/a'}"
    median = statistics.median(times)
    ratio = f" ({median / statistics.median(base):.1f}x)" if base and lib != "sdmxlib" else ""
    return f"{lib} {median * 1000:.1f} ms{ratio}{' (refused by default)' if refused else ''}"


def _report_times(
    data: dict[tuple[str, str], bytes], times: Times, failures: dict[Key, str], refusals: dict[Key, str]
) -> None:
    print("## Read (median; x = multiple of sdmxlib's time)\n")
    for (s, fmt), raw in data.items():
        base = times.get(("read", "sdmxlib", s, fmt))
        cells = [
            _cell(lib, times.get(("read", lib, s, fmt)), base, failures.get((lib, s, fmt)))
            for lib in (*LIBS, PYSDMX_DEFAULT)
        ]
        print(f"{s:13} {fmt:4} {len(raw) / 1e6:4.1f} MB | " + " | ".join(cells))
    print("\n## Write, each library from its own read of the 2.1 file (median, unvalidated)\n")
    for s in SETS:
        for fmt in FORMATS:
            base = times.get(("write", "sdmxlib", s, fmt))
            cells = [
                _cell(
                    lib,
                    times.get(("write", lib, s, fmt)),
                    base,
                    failures.get(("write " + lib, s, fmt)),
                    refused=(lib, s, fmt) in refusals,
                )
                for lib in LIBS
            ]
            print(f"{s:13} {fmt:4} | " + " | ".join(cells))


def _report_agreement(summaries: dict[Key, Summary]) -> None:
    print("\n## Read agreement: each library vs sdmxlib, same file\n")
    for s in SETS:
        for fmt in FORMATS:
            for lib in LIBS[1:]:
                if (reference := summaries.get(("sdmxlib", s, fmt))) and (other := summaries.get((lib, s, fmt))):
                    found = differences(reference, other, parents=lib == "sdmx1")
                    print(f"{s:13} {fmt:4} sdmxlib vs {lib:7}: {'; '.join(found) or 'identical'}")
    print("\n## Format agreement: each library's 3.0 and JSON read vs its own 2.1 read\n")
    for s in SETS:
        for lib in LIBS:
            for fmt in ("3.0", "json"):
                if (reference := summaries.get((lib, s, "2.1"))) and (other := summaries.get((lib, s, fmt))):
                    found = differences(reference, other, parents=lib != "pysdmx")
                    print(f"{s:13} {lib:7} 2.1 vs {fmt:4}: {'; '.join(found) or 'identical'}")


def _report_fidelity(data: dict[tuple[str, str], bytes], messages: dict[Key, Message]) -> None:
    """What each library keeps of the BIS codelists, against a count taken from the documents' text."""
    print("\n## What each library keeps of the codes, against the document's own count\n")
    for fmt in FORMATS:
        source = source_fidelity(fmt, data[("codelist", fmt)])
        print(f"codelist {fmt:4} holds {_described(source)}")
        for lib in LIBS:
            if (message := messages.get((lib, "codelist", fmt))) is not None:
                lost = dropped(source, fidelity(message))
                print(f"    {lib:7}: {'kept everything counted' if not lost else 'dropped ' + '; '.join(lost)}")


def _described(source: Fidelity) -> str:
    languages = ", ".join(sorted(source.languages or ())) or "none"
    return (
        f"{source.codes:,} codes; languages {languages}; {source.parents:,} parents; "
        f"{source.annotations:,} annotations; {source.descriptions:,} descriptions"
    )


#: A code with a French name only: the case where losing languages becomes
#: mislabelling them, which the BIS corpus (English only) cannot show.
_FRENCH_ONLY = "B"


def probe() -> StructureMessage:
    """One small codelist holding what the BIS corpus doesn't: two languages, and a name in French only.

    It also has parents, descriptions and annotations in both languages, so
    each library's losses show on one page.
    """
    note = Annotation(type="NOTE", text={"en": "note", "fr": "remarque"})
    return StructureMessage(
        codelists=[
            Codelist(
                id="CL_PROBE",
                maintainer="BENCH",
                version="1.0",
                name={"en": "Probe", "fr": "Sonde"},
                codes=[
                    Code(
                        id="TOT",
                        name={"en": "Total", "fr": "Total (fr)"},
                        description={"en": "All", "fr": "Tous"},
                        annotations=[note],
                    ),
                    Code(id="A", name={"en": "Alpha", "fr": "Alpha (fr)"}, parent_id="TOT"),
                    Code(id=_FRENCH_ONLY, name={"fr": "Seulement en français"}, parent_id="TOT"),
                ],
            )
        ]
    )


def _report_probe() -> None:
    """The probe codelist through each library's read, then through each one's own writer and back into sdmxlib."""
    print("\n## The probe codelist: two languages, and one name in French only\n")
    message = probe()
    for fmt in FORMATS:
        data = serialize(message, version=fmt)
        source = source_fidelity(fmt, data)
        print(f"probe {fmt:4} holds {_described(source)}")
        for lib in LIBS:
            try:
                held = read(lib, data)
            except Exception as error:  # a competitor's failure is a result, not a crash
                print(f"    {lib:7}: FAIL {type(error).__name__}: {str(error)[:80]}")
                continue
            lost = dropped(source, fidelity(held))
            line = "kept everything counted" if not lost else "dropped " + "; ".join(lost)
            try:
                back = parse(write(lib, held, fmt), strict=False)
            except Exception as error:  # a writer that cannot write this format is a result too
                print(f"    {lib:7}: {line}; its {fmt} write: {type(error).__name__}")
                continue
            assert isinstance(back, StructureMessage)
            codes = {code.id: code for codelist in back.codelists for code in codelist.codes}
            french = codes.get(_FRENCH_ONLY)
            seen = french.name.as_dict() if french is not None else None
            print(f"    {lib:7}: {line}; after its own {fmt} write, sdmxlib reads {_FRENCH_ONLY}'s name as {seen}")


def _report_written(written: dict[Key, bytes], refusals: dict[Key, str], summaries: dict[Key, Summary]) -> None:
    print("\n## Written output: schema validity, then read back by each library vs its read of the source\n")
    for (lib, s, fmt), out in written.items():
        refusal = refusals.get((lib, s, fmt))
        print(f"{s:13} {lib:7} -> {fmt:4} {len(out) / 1e6:4.1f} MB  {label(fmt, out, refusal)}")
        if refusal is not None:
            continue  # not sdmxlib's output: nothing to read back
        for reader in LIBS:
            if (reference := summaries.get((reader, s, "2.1"))) is None:
                continue
            try:
                found = differences(reference, summary(read(reader, out)), parents=reader != "pysdmx")
            except Exception as error:
                print(f"      read by {reader:7}: FAIL {type(error).__name__}: {str(error)[:100]}")
                continue
            print(f"      read by {reader:7}: {'; '.join(found) or 'identical'}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m bench.compete", description=__doc__)
    parser.add_argument("--rounds", type=int, default=7, help="interleaved rounds per measurement (median reported)")
    parser.add_argument("--refresh", action="store_true", help="fetch the corpus from BIS again, replacing the cache")
    args = parser.parse_args()
    # Line by line, so a long run shows its progress in a log rather than at the end.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    rounds: int = args.rounds

    data = corpus(refresh=args.refresh)
    print(f"sdmxlib {version('sdmxlib')}  sdmx1 {version('sdmx1')}  pysdmx {version('pysdmx')}  rounds={rounds}")
    print(f"corpus: BIS, fetched {FETCHED.read_text()}\n")
    failures: dict[Key, str] = {}
    messages = _read_all(data, failures)
    summaries = {key: summary(message) for key, message in messages.items()}
    times = _time_reads(data, messages, failures, rounds)
    written, refusals = _write_all(messages, failures)
    _time_writes(written, messages, times, rounds)

    _report_times(data, times, failures, refusals)
    _report_agreement(summaries)
    _report_fidelity(data, messages)
    _report_probe()
    _report_written(written, refusals, summaries)
    if failures:
        print("\n## Failures\n")
        for key, failure in failures.items():
            print(f"  {' '.join(key)}: {failure[:160]}")


if __name__ == "__main__":
    main()
