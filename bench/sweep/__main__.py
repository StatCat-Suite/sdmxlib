"""`python -m bench.sweep` — see `bench/sweep/README.md`."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import typer

from bench.sweep.capacity import measure
from bench.sweep.endpoints import REGISTRIES, Endpoint, endpoints_of
from bench.sweep.fetch import fetch_host
from bench.sweep.report import SUMMARIES, delta, render, sweep, write

#: Raw documents and full per-artefact reports. Gitignored: gigabytes, and
#: specific to the day they were fetched.
OUT = Path(__file__).parent / "out"


Registries = Annotated[list[str], typer.Argument(help=f"One or more of {', '.join(REGISTRIES)}, or 'all'.")]
Resources = Annotated[list[str] | None, typer.Option("--resource", help="Only this resource; repeat for several.")]

app = typer.Typer()


def _registries(names: list[str]) -> list[str]:
    return list(REGISTRIES) if names == ["all"] else names


@app.command()
def fetch(
    registry: Registries,
    *,
    resource: Resources = None,
    refresh: Annotated[bool, typer.Option(help="Download again what is already cached.")] = False,
) -> None:
    """Download bulk structure documents into the cache."""
    by_host: dict[str, list[Endpoint]] = {}
    for name in _registries(registry):
        for endpoint in endpoints_of(name):
            by_host.setdefault(urlsplit(endpoint.base).netloc, []).append(endpoint)
    resources = tuple(resource) if resource else None
    # One thread per host: parallel across hosts, sequential within one.
    with ThreadPoolExecutor(max_workers=len(by_host)) as pool:
        futures = [
            pool.submit(fetch_host, OUT, group, refresh=refresh, resources=resources) for group in by_host.values()
        ]
        for future in futures:
            future.result()


@app.command()
def run(
    registry: Registries,
    *,
    resource: Resources = None,
    jobs: Annotated[
        int | None, typer.Option(help="Documents at once; what this machine has free when omitted (capacity.py).")
    ] = None,
) -> None:
    """Analyse cached documents and write the report and summary."""
    capacity = measure(jobs)
    print(f"machine: {capacity.describe()}", flush=True)
    for name in _registries(registry):
        report = sweep(OUT, name, capacity=capacity, resources=set(resource) if resource else None)
        full, summary = write(OUT, name, report)
        print(f"{name}: {summary} (full report: {full})")


@app.command()
def show(registry: Registries) -> None:
    """Print a committed summary's non-empty findings."""
    for name in _registries(registry):
        print(render(json.loads((SUMMARIES / f"{name}.json").read_text())))


@app.command("delta")
def delta_command(
    before: Annotated[Path, typer.Argument(help="The older summary; an empty file makes everything new.")],
    after: Annotated[Path, typer.Argument(help="The newer summary.")],
) -> None:
    """Print what moved between two summaries (see `just sweep-delta`)."""
    old: object = json.loads(before.read_text()) if before.stat().st_size else {}
    changes = delta(old, json.loads(after.read_text()))
    print(f"# {after.stem}: {len(changes)} change(s)")
    print("\n".join(changes))


if __name__ == "__main__":
    app()
