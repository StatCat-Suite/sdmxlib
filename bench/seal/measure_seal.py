"""Time each phase of a seal: `LocalRegistry.build(previous=)`, a change, then `seal` (GH-248).

    uv run --with psutil python -m bench.seal.measure_seal census <product> <previous> <runs>
    uv run --with psutil python -m bench.seal.measure_seal codr <product> <previous> <runs> <csv>

Each phase is timed by wrapping the function that does it, in the module that
calls it, so the library runs unchanged. With psutil installed, each phase
also reports the disk's write counter around it: system-wide, so another
writer on the machine inflates it. Each run's edition is deleted after it.

The census run changes nothing: a correction edition of a final product. The
CODR run appends the latest month of `build_codr`'s table, as a monthly
release does.
"""

import copy
import importlib
import json
import resource
import shutil
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import duckdb
import typer

import sdmxlib as sl
import sdmxlib.edition._write as write_module
import sdmxlib.local.registry as registry_module
from bench.seal import build_codr
from sdmxlib.edition import Producer, seal


class Kind(StrEnum):
    """The product a run seals."""

    CENSUS = "census"
    CODR = "codr"


#: The functions timed, by the module attribute each phase is called through.
_WRAPPED = (
    (write_module, "artefact_entries", "artefact fingerprints"),
    (write_module, "settle_lake", "settle_lake"),
    (write_module, "lake_files", "lake file list"),
    (write_module, "binding_counts", "binding counts"),
    (write_module, "_survey_bindings", "binding survey"),
    (write_module, "compact_for_publication", "compact_for_publication"),
    (write_module, "list_files", "file digests"),
    (write_module, "_data_inventory", "data file digests"),
    (registry_module, "restore", "key restore"),
    (shutil, "copyfile", "copy"),
)


@dataclass
class Run:
    edition: str = ""
    seconds: dict[str, float] = field(default_factory=dict)
    written: dict[str, int] = field(default_factory=dict)
    store_bytes: dict[str, int] = field(default_factory=dict)
    peak_rss: int = 0

    def reset(self) -> None:
        self.edition = ""
        self.seconds.clear()
        self.written.clear()
        self.store_bytes.clear()
        self.peak_rss = 0


def _disk_written() -> Callable[[], int]:
    """The disk's cumulative bytes written, from psutil, or a counter that reads 0 without it."""
    try:
        psutil = importlib.import_module("psutil")
    except ModuleNotFoundError:
        return lambda: 0

    def written() -> int:
        counters = psutil.disk_io_counters()
        return 0 if counters is None else int(counters.write_bytes)

    return written


_WRITTEN = _disk_written()
_CURRENT = Run()


def _timed[**P, R](name: str, function: Callable[P, R]) -> Callable[P, R]:
    def timed(*args: P.args, **kwargs: P.kwargs) -> R:
        written = _WRITTEN()
        start = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            _CURRENT.seconds[name] = _CURRENT.seconds.get(name, 0.0) + time.perf_counter() - start
            _CURRENT.written[name] = _CURRENT.written.get(name, 0) + _WRITTEN() - written

    return timed


def _instrument() -> None:
    for module, attribute, name in _WRAPPED:
        setattr(module, attribute, _timed(name, getattr(module, attribute)))


def _size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def seal_once(kind: Kind, product: Path, previous: str, csv: Path | None) -> Run:
    run = _CURRENT
    run.reset()
    manifest = json.loads((product / previous / "manifest.json").read_text())
    written = _WRITTEN()
    start = time.perf_counter()
    with sl.LocalRegistry.build(product, previous=previous) as registry:
        run.seconds["build(previous=)"] = time.perf_counter() - start
        edition = registry.edition_dir
        run.store_bytes["restored"] = _size(edition / "metadata.duckdb")
        if kind == "codr" and csv is not None:
            changed = time.perf_counter()
            latest = build_codr.latest_month(duckdb.connect(), csv)
            source = registry.connection.sql(
                build_codr.observations_sql("REF_DATE = $latest"), params={"csv": str(csv), "latest": latest}
            )
            registry.write_observations(schema=build_codr.SCHEMA, table=build_codr.TABLE, source=source, mode="append")
            run.seconds["change (append one month)"] = time.perf_counter() - changed
    sealing = time.perf_counter()
    seal(
        edition,
        product_id=manifest["product_id"],
        producer=Producer(name="seal-measure", version="0"),
        lifecycle="final" if kind == "census" else "revisable",
        expire_snapshots=kind == "census",
    )
    run.seconds["seal"] = time.perf_counter() - sealing
    run.seconds["seal total"] = time.perf_counter() - start
    run.written["seal total"] = _WRITTEN() - written
    run.edition = edition.name
    run.store_bytes["previous"] = _size(product / previous / "metadata.duckdb")
    run.store_bytes["sealed"] = _size(edition / "metadata.duckdb")
    # Kilobytes on Linux, bytes on macOS.
    scale = 1 if sys.platform == "darwin" else 1024
    run.peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
    return run


def main(
    kind: Annotated[Kind, typer.Argument(help="census changes nothing; codr appends the CSV's latest month.")],
    product: Annotated[Path, typer.Argument(help="The product directory.")],
    previous: Annotated[str, typer.Argument(help="The edition id each run builds on.")],
    runs: Annotated[int, typer.Argument(help="Seals to time; the median is reported.")],
    csv: Annotated[Path | None, typer.Argument(help="build_codr's table CSV, for a codr run.")] = None,
) -> None:
    """Time each phase of a seal over `runs` editions built on `previous`."""
    if kind is Kind.CODR and csv is None:
        msg = "a codr run needs the table's CSV"
        raise typer.BadParameter(msg)
    _instrument()
    results: list[Run] = []
    for number in range(runs):
        run = seal_once(kind, product, previous, csv)
        results.append(copy.deepcopy(run))
        sys.stdout.write(json.dumps({"run": number, **asdict(run)}) + "\n")
        shutil.rmtree(product / run.edition)
    sys.stdout.write(f"\nmedian over {runs} runs\n")
    for name in results[0].seconds:
        seconds = statistics.median(r.seconds[name] for r in results)
        written = statistics.median(r.written.get(name, 0) for r in results)
        sys.stdout.write(f"  {name:32} {seconds:8.2f} s  {written / 2**20:9.1f} MiB written\n")


if __name__ == "__main__":
    typer.run(main)
