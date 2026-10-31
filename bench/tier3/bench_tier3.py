r"""Tier-3 reads from object storage: requests, bytes and time per DuckDB cache setting (GH-185, GH-231).

A pod's copy of tiers 1 and 2 opens the edition's data files in object
storage with ``remote=``. Each cache setting runs in a fresh process, which
reads each query twice, then in a second fresh process that reads them twice
again: the second process is a pod restart. DuckDB's HTTP log counts the
requests and their bytes, so the counts are what an object store would see.

Without ``--remote`` the edition's files go to Azurite, whose timings are one
machine's and carry no request latency. With ``--remote`` the bench reads an
edition already uploaded to Azure Blob Storage, signing in through DuckDB's
credential chain (``az login``, or the machine's managed identity), so its
timings are a cloud region's when it runs in that region.

    uv run python -m bench.tier3.bench_tier3 <edition>
    uv run python -m bench.tier3.bench_tier3 <edition> \
        --remote az://<container>/<product>/<edition id>/ --account <storage account> --region <region>

Writes ``baselines.json`` (Azurite) or ``baselines_azure.json`` (``--remote``)
with ``--update``; prints the table either way.
"""

import json
import multiprocessing
import resource
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path
from typing import Annotated

import httpx2
import typer

import sdmxlib as sl
from sdmxlib.edition import copy_edition, read_manifest
from sdmxlib.query.observations import ObsQuery
from tests.stores import _stores

_BASELINES = Path(__file__).with_name("baselines.json")
_BASELINES_AZURE = Path(__file__).with_name("baselines_azure.json")

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


def _worker(pod: Path, remote: str, secret: sl.StoreSecret, options: dict[str, str]) -> list[dict[str, object]]:
    """Each query read twice in this process: requests, bytes and seconds per read."""
    engine = sl.EditionEngine(secrets=[secret], threads=4)
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


def _run(pod: Path, remote: str, secret: sl.StoreSecret, options: dict[str, str]) -> list[dict[str, object]]:
    """`_worker` in a fresh process, as a pod that has just started."""
    with multiprocessing.get_context("spawn").Pool(1) as pool:
        return pool.apply(_worker, (pod, remote, secret, options))


def _measure(edition: Path, remote: str, secret: sl.StoreSecret) -> dict[str, dict[str, list[dict[str, object]]]]:
    """Every setting at a start and a restart, over a pod's copy of `edition`'s tiers 1 and 2."""
    with tempfile.TemporaryDirectory() as tmp:
        pod = Path(tmp) / "pod" / "census" / edition.name
        copy_edition(edition, pod, tiers=("required", "lazy"))
        return {
            name: {"start": _run(pod, remote, secret, options), "restart": _run(pod, remote, secret, options)}
            for name, options in SETTINGS.items()
        }


def _on_azurite(edition: Path) -> dict[str, dict[str, list[dict[str, object]]]]:
    """`_measure` against Azurite, with `edition` uploaded to it first."""
    missing = _stores.runtime_missing()
    if missing is not None:
        sys.exit(f"tier-3 bench needs a container runtime: {missing}")
    store = _stores.start(_stores.EMULATORS[0])
    try:
        with httpx2.Client(base_url=store.base, timeout=600) as client:
            _stores.upload_publication(_stores.uploader(store, client), edition, bundle="census")
        return _measure(edition, f"{_stores.root(store)}census/{edition.name}/", _stores.secret(store))
    finally:
        _stores.stop(store)


def main(
    edition: Annotated[Path, typer.Argument(help="The sealed edition directory, the pod's copy is taken from.")],
    *,
    remote: Annotated[
        str | None,
        typer.Option(help="The edition's URI in Azure Blob Storage, ending in '/'; Azurite when omitted."),
    ] = None,
    account: Annotated[str | None, typer.Option(help="The storage account --remote is in.")] = None,
    region: Annotated[str | None, typer.Option(help="The Azure region the bench runs in, for --update.")] = None,
    update: Annotated[bool, typer.Option(help="Overwrite the baselines file with these numbers.")] = False,
) -> None:
    """Measure tier-3 requests, bytes and time per DuckDB cache setting."""
    if remote is None:
        results = _on_azurite(edition)
        store, baselines = "azurite", _BASELINES
    else:
        if account is None or region is None:
            msg = "--remote needs --account and --region"
            raise typer.BadParameter(msg)
        secret = sl.StoreSecret("azure", provider="credential_chain", options={"account_name": account})
        results = _measure(edition, remote, secret)
        store, baselines = f"azure, {region}", _BASELINES_AZURE
    for name, runs in results.items():
        for phase, rows in runs.items():
            for row in rows:
                print(name, phase, row)
    if update:
        report = {
            "_edition": read_manifest(edition).edition_id,
            "_sdmxlib": metadata.version("sdmxlib"),
            "_store": store,
            "results": results,
        }
        baselines.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    typer.run(main)
