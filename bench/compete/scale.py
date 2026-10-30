"""`python -m bench.compete.scale` — how sdmxlib and pysdmx scale with codelist size.

The BIS corpus (`python -m bench.compete`) is 105 codelists of about a hundred
codes each, so per-artefact costs dominate it. Census geographies run to
hundreds of thousands of codes in one codelist, and there the per-code cost is
all that matters. So this reads single large codelists:

- **UNSD `CL_COMMODITY`** (68,191 codes) from the SDMX global registry, which
  serves SDMX-ML 2.1, 3.0 and SDMX-JSON 2.0;
- **ECB `CL_PROVIDER_FM_ID`** (103,712 codes), SDMX-ML 2.1 only;
- **synthetic codelists** of 1,000 to 500,000 codes, with two languages and a
  four-level hierarchy, written by sdmxlib in every format: a scaling curve,
  though from sdmxlib's own writer;
- **`CL_GEOGRAPHY`** from a 2021 Census Profile bundle, when one is present
  (`$SDMXLIB_BENCH_BUNDLE`, as for the bundle tier).

sdmx1 is left out. Its SDMX-ML 2.1 writer is quadratic in the code count:
500,000 codes took over four minutes a round, most of a run, and it measured
nothing new beyond that. `python -m bench.compete` still compares all three,
on codelists small enough not to matter.

Real documents are fetched on first run into the gitignored `cache/`, as for
the BIS corpus; the registry takes about two minutes per format. What sdmxlib
writes, the synthetic codelists and `CL_GEOGRAPHY`, is written afresh each run. One document
is processed at a time, so two libraries' models of a 500,000-code
codelist are never held at once.
"""

from __future__ import annotations

import argparse
import gc
import io
import os
import statistics
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import duckdb
import httpx2

from bench.compete.libs import Message, differences, dropped, fidelity, read, source_fidelity, summary, timed, write
from sdmxlib.formats import serialize
from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import ValidationError

CACHE = Path(__file__).parent / "cache" / "scale"
SIZES = (1_000, 10_000, 100_000, 500_000)
_SDMX_V1 = "https://registry.sdmx.org/ws/public/sdmxapi/rest/codelist/UNSD/CL_COMMODITY/1.0"
_SDMX_V2 = "https://registry.sdmx.org/sdmx/v2/structure/codelist/UNSD/CL_COMMODITY/1.0"
_ECB = "https://data-api.ecb.europa.eu/service/codelist/ECB/CL_PROVIDER_FM_ID/1.0"
_ACCEPT = {
    "2.1": "application/vnd.sdmx.structure+xml;version=2.1",
    "3.0": "application/vnd.sdmx.structure+xml;version=3.0.0",
    "json": "application/vnd.sdmx.structure+json;version=2.0.0",
}
#: Wire format -> cached file suffix.
_SUFFIX = {"2.1": "2.1.xml", "3.0": "3.0.xml", "json": "2.0.json"}
#: The libraries this tier compares; see the module docstring for sdmx1.
_LIBS = ("sdmxlib", "pysdmx")
_GEOGRAPHY = Codelist.urn_for(agency="STC_CENSUS", id="CL_GEOGRAPHY", version="1.0")


@dataclass(frozen=True)
class Document:
    """One codelist, held in one or more wire formats."""

    name: str
    codes: int
    files: dict[str, Path]


# ── the documents ────────────────────────────────────────────────────────────


def _fetch(client: httpx2.Client, url: str, fmt: str, path: Path, *, refresh: bool) -> Path:
    if refresh or not path.exists():
        print(f"fetching {path.name} ...")
        response = client.get(url, headers={"Accept": _ACCEPT[fmt]})
        response.raise_for_status()
        partial = path.with_suffix(".part")
        partial.write_bytes(response.content)
        partial.replace(path)
    return path


def _fetched(*, refresh: bool) -> list[Document]:
    """UNSD's and ECB's large codelists, from the cache or their registries."""
    CACHE.mkdir(parents=True, exist_ok=True)
    timeout = httpx2.Timeout(30.0, read=600.0)
    with httpx2.Client(timeout=timeout, follow_redirects=True) as client:
        unsd = {
            fmt: _fetch(
                client, _SDMX_V1 if fmt == "2.1" else _SDMX_V2, fmt, CACHE / f"unsd-commodity-{suffix}", refresh=refresh
            )
            for fmt, suffix in _SUFFIX.items()
        }
        ecb = {"2.1": _fetch(client, _ECB, "2.1", CACHE / "ecb-provider-fm-id-2.1.xml", refresh=refresh)}
    return [Document("UNSD CL_COMMODITY", 68_191, unsd), Document("ECB CL_PROVIDER_FM_ID", 103_712, ecb)]


def _synthetic_codelist(codes: int) -> Codelist:
    """`codes` codes named in two languages, each code's parent the one a tenth its index."""
    return Codelist(
        id=f"CL_SYNTHETIC_{codes}",
        maintainer="BENCH",
        version="1.0",
        name={"en": f"{codes} synthetic codes", "fr": f"{codes} codes synthétiques"},
        codes=[
            Code(
                id=f"C{i:07d}",
                name={"en": f"Code {i}", "fr": f"Code {i} (fr)"},
                parent_id=f"C{i // 10:07d}" if i >= 10 else None,
            )
            for i in range(codes)
        ],
    )


def _written(name: str, codelist: Codelist) -> dict[str, Path]:
    """`codelist` as sdmxlib writes it today, in every format it will write.

    Written afresh on every run, never kept from an earlier one: the files are
    the writer's output, and a cached copy would measure whichever writer made
    it. A format sdmxlib refuses is left out, saying why: census geographies
    have codes whose ids start with a digit, which SDMX-ML 3.0.0 cannot name as
    a parent (a schema erratum fixed in 3.1, #432).
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    message = StructureMessage(codelists=[codelist])
    files: dict[str, Path] = {}
    for fmt, suffix in _SUFFIX.items():
        path = CACHE / f"{name}-{suffix}"
        path.unlink(missing_ok=True)
        try:
            path.write_bytes(serialize(message, version=fmt))
        except ValidationError as error:
            print(f"{name}: sdmxlib will not write SDMX-ML {fmt}: {str(error).splitlines()[-1][:160]}")
            continue
        files[fmt] = path
    return files


def _synthetic(sizes: tuple[int, ...]) -> list[Document]:
    return [Document(f"synthetic {n:,}", n, _written(f"synthetic-{n}", _synthetic_codelist(n))) for n in sizes]


def _bundle() -> list[Document]:
    """The census bundle's `CL_GEOGRAPHY`, when `$SDMXLIB_BENCH_BUNDLE` names one.

    Opened read-only, as the bundle tier opens it: the bundle is built by
    another workspace and is not this benchmark's to change.
    """
    declared = os.environ.get("SDMXLIB_BENCH_BUNDLE")
    if declared is None:
        print("$SDMXLIB_BENCH_BUNDLE is unset: the CL_GEOGRAPHY document is left out\n")
        return []
    path = Path(declared) / "metadata.duckdb"
    if not path.exists():
        print(f"no bundle at {path}: the CL_GEOGRAPHY document is left out\n")
        return []
    conn = duckdb.connect(str(path), read_only=True)
    try:
        codelist = LocalRegistry.from_connection(conn).get(_GEOGRAPHY, eager=True)
    finally:
        conn.close()
    if codelist is None:
        print(f"the bundle at {path} has no {_GEOGRAPHY}: left out\n")
        return []
    return [Document("census CL_GEOGRAPHY", len(codelist.codes), _written("cl-geography", codelist))]


# ── the run ──────────────────────────────────────────────────────────────────


def _median_ms(times: list[float]) -> float:
    return statistics.median(times) * 1000


def _report(document: Document, rounds: int) -> None:
    """Read every format with every library, write from each one's 2.1 read, and compare.

    Each read says what it dropped of the document's own count of the codes.
    A row where pysdmx keeps everything counted is the like-for-like timing.
    Elsewhere pysdmx is timed doing less: one language and no hierarchy.
    """
    print(f"## {document.name} ({document.codes:,} codes)\n")
    for fmt, path in document.files.items():
        raw = path.read_bytes()
        source = source_fidelity(fmt, raw)
        cells: list[str] = []
        reference = None
        for lib in _LIBS:
            try:
                message: Message = read(lib, raw)
            except Exception as error:  # a competitor's failure is a result, not a crash
                cells.append(f"{lib} FAIL: {type(error).__name__}: {str(error)[:50]}")
                continue
            times = [timed(lambda lib=lib, raw=raw: read(lib, raw)) for _ in range(rounds)]
            per_1k = _median_ms(times) / document.codes * 1000
            agreement = ""
            if lib == "sdmxlib":
                reference = summary(message)
            elif reference is not None:
                found = differences(reference, summary(message), parents=False)
                agreement = "" if not found else f" [differs: {found[0][:70]}]"
            lost = dropped(source, fidelity(message))
            kept = "keeps everything counted" if not lost else "drops " + "; ".join(lost)
            cells.append(f"{lib} {_median_ms(times):8.0f} ms ({per_1k:5.2f} ms/1k codes){agreement} — {kept}")
            if fmt == "2.1":
                cells.extend(_writes(lib, message, rounds, document.codes))
            del message
            gc.collect()
        print(f"read {fmt:4} {len(raw) / 1e6:6.1f} MB\n    " + "\n    ".join(cells) + "\n")


def _writes(lib: str, message: Message, rounds: int, codes: int) -> list[str]:
    out: list[str] = []
    for fmt in _SUFFIX:
        try:
            write(lib, message, fmt)
        except Exception as error:
            if not isinstance(error, NotImplementedError):
                out.append(f"  {lib} write {fmt}: FAIL {type(error).__name__}: {str(error)[:50]}")
            continue
        times = [timed(lambda fmt=fmt: write(lib, message, fmt)) for _ in range(rounds)]
        out.append(
            f"  {lib} write {fmt:4} {_median_ms(times):8.0f} ms ({_median_ms(times) / codes * 1000:5.2f} ms/1k codes)"
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m bench.compete.scale", description=__doc__)
    parser.add_argument("--rounds", type=int, default=3, help="rounds per measurement (median reported)")
    parser.add_argument("--sizes", type=int, nargs="*", default=list(SIZES), help="synthetic codelist sizes, in codes")
    parser.add_argument("--refresh", action="store_true", help="fetch the real codelists again")
    args = parser.parse_args()
    # Line by line, so a long run shows its progress in a log rather than at the end.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    rounds: int = args.rounds

    documents = [*_fetched(refresh=args.refresh), *_synthetic(tuple(args.sizes)), *_bundle()]
    print(f"sdmxlib {version('sdmxlib')}  pysdmx {version('pysdmx')}  rounds={rounds}")
    print(f"run {datetime.now(UTC).isoformat(timespec='seconds')}; pysdmx timed with input validation off\n")
    for document in documents:
        _report(document, rounds)


if __name__ == "__main__":
    main()
