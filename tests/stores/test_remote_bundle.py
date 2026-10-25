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
from sdmxlib.bundle import check_on_open, copy_bundle
from sdmxlib.testing import Dim, Shape, synthetic_bundle
from tests.stores import _stores

pytestmark = pytest.mark.stores

_FLOW = sl.Dataflow.urn_for(agency="SYNTHETIC", id="DF_SYNTHETIC", version="1.0")
_SHAPE = Shape(dimensions=(Dim(codes=40, depth=2, fanout=(2, 5)), Dim(codes=7)), observations=1_500, periods=6)


def _frame(reg: sl.BundleRegistry) -> pl.DataFrame:
    return reg.obs(_FLOW).frame().sort("DIM_1", "DIM_2", "TIME_PERIOD")


def test_a_server_reads_every_row_from_the_store(store: _stores.Running, tmp_path: Path) -> None:
    bundle = tmp_path / "build" / "census"
    publication = bundle / synthetic_bundle(bundle, _SHAPE, seed=11).token
    with sl.LocalRegistry.bundle(publication) as reg:
        local = _frame(reg)
    with httpx2.Client(base_url=store.base, timeout=60) as client:
        _stores.upload_publication(_stores.uploader(store, client), publication, bundle="census")

    pod = tmp_path / "pod" / "census" / publication.name
    copy_bundle(publication, pod, tiers=(1, 2))
    assert not (pod.parent / "data").exists(), "the server holds no data file"
    digest = hashlib.sha256((publication / "bundle.manifest.json").read_bytes()).hexdigest()
    assert hashlib.sha256((pod / "bundle.manifest.json").read_bytes()).hexdigest() == digest

    remote = f"{_stores.root(store)}census/{publication.name}/"
    engine = sl.BundleEngine(secrets=[_stores.secret(store)])
    try:
        with sl.LocalRegistry.bundle(pod, remote=remote, engine=engine) as reg:
            assert reg.data_path == f"{_stores.root(store)}census/data/"
            assert _frame(reg).equals(local)
        report = check_on_open(pod, stamps=tmp_path / "stamps", remote=remote, engine=engine)
    finally:
        engine.close()
    assert report.at == "receive"
    assert report.ok, "\n".join(map(str, report.findings))
    assert {"remote", "lake", "bindings"} <= set(report.checked)
