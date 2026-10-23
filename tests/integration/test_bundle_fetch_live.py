"""`fetch_bundle` against a real provider, replayed from cassettes (GH-68).

ECB's `EXR`, narrowed to one series and a quarter so the cassette stays small:
the structural closure, the observations, the binding and the manifest,
provenance included, end to end — and the bundle passes `check_bundle` with
every check, the structural closure among them.
"""

from pathlib import Path

import pytest

import sdmxlib as sl
from sdmxlib.bundle import check_bundle, fetch_bundle

pytestmark = pytest.mark.integration

_EXR = sl.Dataflow.urn_for(agency="ECB", id="EXR")
_KEY = "M.USD.EUR.SP00.A"


@pytest.mark.vcr
def test_a_slice_of_ecb_exr_lands_as_a_bundle_that_checks_clean(tmp_path: Path) -> None:
    root = tmp_path / "exr"
    manifest = fetch_bundle(sl.Provider.ECB, _EXR, root, key=_KEY, start="2024-01", end="2024-03")

    (binding,) = manifest.bindings
    assert (binding.dataflow, binding.observations) == (
        "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=ECB:EXR(1.0)",
        3,
    )
    provenance = manifest.provenance
    assert provenance is not None
    assert (provenance.source, provenance.api, provenance.key, provenance.start, provenance.end) == (
        "ECB",
        "2.1",
        _KEY,
        "2024-01",
        "2024-03",
    )
    assert provenance.dataflow == binding.dataflow, "fetched at the version `latest` resolved to"
    assert binding.dataflow in provenance.artefacts

    report = check_bundle(root)
    assert report.ok, "\n".join(map(str, report.findings))

    with sl.LocalRegistry.bundle(root, require_manifest=True) as reg:
        frame = reg.obs(sl.Dataflow.parse_urn(binding.dataflow)).frame()
    assert sorted(frame.get_column("TIME_PERIOD").to_list()) == ["2024-01", "2024-02", "2024-03"]
    assert set(frame.get_column("CURRENCY").cast(str).to_list()) == {"USD"}


@pytest.mark.vcr
def test_structures_only_fetches_no_observations(tmp_path: Path) -> None:
    root = tmp_path / "exr"
    manifest = fetch_bundle(sl.Provider.ECB, _EXR, root, structures_only=True)
    assert manifest.bindings == ()
    assert manifest.provenance is not None
    assert manifest.provenance.structures_only
    assert check_bundle(root).ok


@pytest.mark.vcr
def test_a_key_of_the_wrong_length_names_the_key_dimensions(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match=r"key 'M\.USD' has 2 segments; .* key is 5: FREQ\.CURRENCY\.CURRENCY_DENOM\.EXR_TYPE\.EXR_SUFFIX",
    ):
        fetch_bundle(sl.Provider.ECB, _EXR, tmp_path / "exr", key="M.USD")
