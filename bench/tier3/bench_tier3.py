"""Tier-3 reads from object storage: requests, bytes and time per DuckDB cache setting (GH-185).

The census publication's data files go to Azurite; a pod's copy of tiers 1
and 2 opens them with ``remote=``. Each cache setting runs in a fresh process,
which reads each query twice, then in a second fresh process that reads them
twice again: the second process is a pod restart. DuckDB's HTTP log counts the
requests and their bytes, so the counts are what an object store would see;
the timings are an emulator's on one machine, and say nothing about a cloud
region's latency.

    uv run python -m bench.tier3.bench_tier3 --publication <census publication>

Writes ``bench/tier3/baselines.json`` with ``--update``; prints the table
either way.
"""

import argparse
import json
import multiprocessing
import resource
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path

import httpx2

import sdmxlib as sl
from sdmxlib.edition import copy_edition, read_manifest
from sdmxlib.query.observations import ObsQuery
from tests.stores import _stores

_BASELINES = Path(__file__).with_name("baselines.json")

#: Each setting, as the DuckDB options it sets on the engine's connection.
SETTINGS: dict[str, dict[str, str]] = {
    "off": {"enable_external_file_cache": "false"},
    "memory": {"enable_external_file_cache": "true"},
    "memory, 400MB limit": {"enable_external_file_cache": "true", "memory_limit": "'400MB'"},
    "memory, 1MB min block": {
        "enable_external_file_cache": "true",
        "external_file_cache_remote_min_block_size": "1048576",
    },
    "memory, grid sizing": {"enable_external_file_cache": "true", "external_file_cache_request_sizing": "'GRID'"},
    "spill, 400MB limit": {
        "enable_external_file_cache": "true",
        "external_file_cache_spill": "true",
        "memory_limit": "'400MB'",
    },
}


def _queries(reg: sl.EditionRegistry, flow: sl.SdmxUrn[sl.Dataflow]) -> dict[str, ObsQuery]:
    """One geography, one characteristic and the count: the middle value in sort order of each dimension."""
    availability = reg.availability(flow)
    assert availability is not None
    values = {key.dimension_id: sorted(v.value for v in key.values) for key in availability.region.keys}
    area, characteristic = values["REF_AREA"], values["CHARACTERISTIC"]
    obs = reg.obs(flow)
    return {
        "one geography": obs.filter(obs.col("REF_AREA") == area[len(area) // 2]),
        "one characteristic": obs.filter(obs.col("CHARACTERISTIC") == characteristic[len(characteristic) // 2]),
        "count": obs,
    }


def _worker(pod: Path, remote: str, port: int, options: dict[str, str]) -> list[dict[str, object]]:
    """Each query read twice in this process: requests, bytes and seconds per read."""
    store = _stores.Running(_stores.EMULATORS[0], "", port)
    engine = sl.EditionEngine(secrets=[_stores.secret(store)], threads=4)
    conn = engine.connection
    for name, value in options.items():
        conn.execute(f"SET {name} = {value}")
    conn.execute("SET redact_http_logs = false")
    conn.execute("CALL enable_logging('HTTP')")
    found: list[dict[str, object]] = []
    with sl.LocalRegistry.edition(pod, remote=remote, engine=engine) as reg:
        (binding,) = reg.get_all(sl.DataflowBinding)
        flow = sl.Dataflow.parse_urn(str(binding.dataflow.urn))
        for query, built in _queries(reg, flow).items():
            for read in (1, 2):
                conn.execute("CALL truncate_duckdb_logs()")
                start = time.perf_counter()
                rows = built.count() if query == "count" else built.frame().height
                seconds = time.perf_counter() - start
                requests, fetched = conn.execute(
                    """
                    SELECT
                        count(*),
                        coalesce(sum(TRY_CAST(coalesce(
                            response.headers['content-length'], response.headers['Content-Length']
                        ) AS BIGINT)), 0)
                    FROM duckdb_logs_parsed('HTTP') WHERE request.type = 'GET'
                    """
                ).fetchone() or (0, 0)
                found.append(
                    {"query": query, "read": read, "rows": rows, "gets": requests, "bytes": fetched, "s": seconds}
                )
        cached = conn.execute(
            "SELECT coalesce(sum(nr_bytes) FILTER (loaded), 0), coalesce(sum(nr_bytes) FILTER (spilled), 0) "
            "FROM duckdb_external_file_cache()"
        ).fetchone() or (0, 0)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    found.append({"cache_bytes": cached[0], "spilled_bytes": cached[1], "peak_rss_bytes": peak})
    return found


def _run(pod: Path, remote: str, port: int, options: dict[str, str]) -> list[dict[str, object]]:
    """`_worker` in a fresh process, as a pod that has just started."""
    with multiprocessing.get_context("spawn").Pool(1) as pool:
        return pool.apply(_worker, (pod, remote, port, options))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publication", type=Path, required=True)
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    publication: Path = args.edition_dir
    missing = _stores.runtime_missing()
    if missing is not None:
        sys.exit(f"tier-3 bench needs a container runtime: {missing}")
    store = _stores.start(_stores.EMULATORS[0])
    try:
        with tempfile.TemporaryDirectory() as tmp:
            with httpx2.Client(base_url=store.base, timeout=600) as client:
                _stores.upload_publication(_stores.uploader(store, client), publication, bundle="census")
            pod = Path(tmp) / "pod" / "census" / publication.name
            copy_edition(publication, pod, tiers=("required", "lazy"))
            remote = f"{_stores.root(store)}census/{publication.name}/"
            results = {
                name: {
                    "start": _run(pod, remote, store.host_port, options),
                    "restart": _run(pod, remote, store.host_port, options),
                }
                for name, options in SETTINGS.items()
            }
    finally:
        _stores.stop(store)
    for name, runs in results.items():
        for phase, rows in runs.items():
            for row in rows:
                print(name, phase, row)
    if args.update:
        report = {
            "_publication": read_manifest(publication).edition_id,
            "_sdmxlib": metadata.version("sdmxlib"),
            "_store": "azurite",
            "results": results,
        }
        _BASELINES.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
