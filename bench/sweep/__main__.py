"""`python -m bench.sweep` — see `bench/sweep/README.md`."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from bench.sweep.capacity import measure
from bench.sweep.endpoints import REGISTRIES, Endpoint, endpoints_of
from bench.sweep.fetch import fetch_host
from bench.sweep.report import SUMMARIES, delta, render, sweep, write

#: Raw documents and full per-artefact reports. Gitignored: gigabytes, and
#: specific to the day they were fetched.
OUT = Path(__file__).parent / "out"


def _registries(names: list[str]) -> list[str]:
    return list(REGISTRIES) if names == ["all"] else names


def _fetch(args: argparse.Namespace) -> None:
    by_host: dict[str, list[Endpoint]] = {}
    for name in _registries(args.registry):
        for endpoint in endpoints_of(name):
            by_host.setdefault(urlsplit(endpoint.base).netloc, []).append(endpoint)
    resources = tuple(args.resource) if args.resource else None
    # One thread per host: parallel across hosts, sequential within one.
    with ThreadPoolExecutor(max_workers=len(by_host)) as pool:
        futures = [
            pool.submit(fetch_host, OUT, group, refresh=args.refresh, resources=resources) for group in by_host.values()
        ]
        for future in futures:
            future.result()


def _run(args: argparse.Namespace) -> None:
    capacity = measure(args.jobs)
    print(f"machine: {capacity.describe()}", flush=True)
    for registry in _registries(args.registry):
        report = sweep(OUT, registry, capacity=capacity, resources=set(args.resource) if args.resource else None)
        full, summary = write(OUT, registry, report)
        print(f"{registry}: {summary} (full report: {full})")


def _show(args: argparse.Namespace) -> None:
    for registry in _registries(args.registry):
        print(render(json.loads((SUMMARIES / f"{registry}.json").read_text())))


def _delta(args: argparse.Namespace) -> None:
    before: object = json.loads(args.before.read_text()) if args.before.stat().st_size else {}
    changes = delta(before, json.loads(args.after.read_text()))
    print(f"# {args.after.stem}: {len(changes)} change(s)")
    print("\n".join(changes))


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m bench.sweep")
    commands = parser.add_subparsers(required=True)

    fetch = commands.add_parser("fetch", help="download bulk structure documents into the cache")
    fetch.add_argument("registry", nargs="+", help=f"one or more of {', '.join(REGISTRIES)}, or 'all'")
    fetch.add_argument("--resource", action="append", help="only this resource (repeatable)")
    fetch.add_argument("--refresh", action="store_true", help="re-download what is already cached")
    fetch.set_defaults(run=_fetch)

    run = commands.add_parser("run", help="analyse cached documents and write report + summary")
    run.add_argument("registry", nargs="+", help=f"one or more of {', '.join(REGISTRIES)}, or 'all'")
    run.add_argument("--resource", action="append", help="only this resource (repeatable)")
    run.add_argument(
        "--jobs", type=int, help="documents at once (default: what this machine has free — see capacity.py)"
    )
    run.set_defaults(run=_run)

    show = commands.add_parser("show", help="print a committed summary's non-empty findings")
    show.add_argument("registry", nargs="+", help=f"one or more of {', '.join(REGISTRIES)}, or 'all'")
    show.set_defaults(run=_show)

    compare = commands.add_parser("delta", help="what moved between two summaries (see `just sweep-delta`)")
    compare.add_argument("before", type=Path, help="the older summary (empty file: everything is new)")
    compare.add_argument("after", type=Path, help="the newer summary")
    compare.set_defaults(run=_delta)

    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
