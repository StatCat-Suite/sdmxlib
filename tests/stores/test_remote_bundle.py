"""A server holding tiers 1 and 2 reads a bundle's data files from each object store (GH-180).

The publish job's sequence against Azurite, an S3 server and a GCS
emulator: publish locally, upload the files unchanged (data first, manifest
last), copy tiers 1 and 2 to the server, then open with `remote=` and the
engine holding the store's secret. The rows read must equal the local read,
and the receive check must pass on the server's copy.
"""

import hashlib
from pathlib import Path

import httpx2
import polars as pl
import pytest

import sdmxlib as sl
from sdmxlib.edition import check_on_open, copy_edition, read_manifest, verify_remote
from sdmxlib.testing import Dim, Shape, synthetic_edition
from tests.stores import _stores
from tests.support.narrow import present

pytestmark = pytest.mark.stores

_FLOW = sl.Dataflow.urn_for(agency="SYNTHETIC", id="DF_SYNTHETIC", version="1.0")
_SHAPE = Shape(dimensions=(Dim(codes=40, depth=2, fanout=(2, 5)), Dim(codes=7)), observations=1_500, periods=6)


def _frame(reg: sl.EditionRegistry) -> pl.DataFrame:
    return reg.obs(_FLOW).frame().sort("DIM_1", "DIM_2", "TIME_PERIOD")


def test_a_server_reads_every_row_from_the_store(store: _stores.Running, tmp_path: Path) -> None:
    bundle = tmp_path / "build" / "census"
    publication = bundle / synthetic_edition(bundle, _SHAPE, seed=11).edition_id
    with sl.LocalRegistry.edition(publication) as reg:
        local = _frame(reg)
    manifest = read_manifest(publication)
    with httpx2.Client(base_url=store.base, timeout=60) as client:
        _stores.upload_publication(_stores.uploader(store, client), publication, bundle="census")
        # Every file, verified where it is stored, by a digest the store keeps.
        keys = {
            e.path: f"census/data/{e.path}" if e.root == "data" else f"census/{publication.name}/{e.path}"
            for e in manifest.files
        }
        kept = {path: _stores.lookup(store, client, key) for path, key in keys.items()}
    assert verify_remote(manifest, lambda e: kept[e.path], tiers=("required", "lazy", "remote")) == []
    assert all(present(kept[e.path]).digests for e in manifest.files), "the store kept a digest of every file"

    pod = tmp_path / "pod" / "census" / publication.name
    copy_edition(publication, pod, tiers=("required", "lazy"))
    assert not (pod.parent / "data").exists(), "the server holds no data file"
    digest = hashlib.sha256((publication / "manifest.json").read_bytes()).hexdigest()
    assert hashlib.sha256((pod / "manifest.json").read_bytes()).hexdigest() == digest

    remote = f"{_stores.root(store)}census/{publication.name}/"
    engine = sl.EditionEngine(secrets=[_stores.secret(store)])
    try:
        with sl.LocalRegistry.edition(pod, remote=remote, engine=engine) as reg:
            assert reg.data_path == f"{_stores.root(store)}census/data/"
            assert _frame(reg).equals(local)
        report = check_on_open(pod, stamps=tmp_path / "stamps", remote=remote, engine=engine)
    finally:
        engine.close()
    assert report.at == "receive"
    assert report.ok, "\n".join(map(str, report.findings))
    assert {"remote", "lake", "bindings"} <= set(report.checked)
