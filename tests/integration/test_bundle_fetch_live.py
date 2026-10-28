"""`fetch_bundle` against a real provider, replayed from cassettes (GH-68).

ECB's `EXR`, narrowed to one series and a quarter so the cassette stays small:
the structural closure, the observations, the binding and the manifest,
provenance included, end to end — and the bundle passes `check_bundle` with
every check, the structural closure among them.

The FMR cassettes pin the closure's incoming references (GH-221): a data
constraint, a hierarchy and a hierarchy association that each refer to
`ECB:EXR(1.0)` and that `EXR` itself does not reference. They were recorded
against a local FMR 12.4.2 (`bench/fmr/README.md`) holding ECB's `EXR` closure
and, submitted on top of it, `tests/integration/fmr/12.4.2/incoming.xml`:

    curl -u root:password -H "ACTION: REPLACE" --data-binary @tests/integration/fmr/12.4.2/incoming.xml http://localhost:8080/ws/secure/sdmxapi/rest/
    uv run pytest tests/integration/test_bundle_fetch_live.py -k fmr --record-mode=rewrite
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

    publication = root / manifest.token
    report = check_bundle(publication, at="publish")
    assert report.ok, "\n".join(map(str, report.findings))

    with sl.LocalRegistry.bundle(publication) as reg:
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
    assert check_bundle(root / manifest.token, at="publish").ok
    # ECB's ECB_EXR1 serves six more dataflows under references=parents;
    # none of them is EXR's to keep.
    dataflows = {a.urn for a in manifest.artefacts if ".Dataflow=" in a.urn}
    assert dataflows == {"urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=ECB:EXR(1.0)"}


@pytest.mark.vcr
def test_a_key_of_the_wrong_length_names_the_key_dimensions(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match=r"key 'M\.USD' has 2 segments; .* key is 5: FREQ\.CURRENCY\.CURRENCY_DENOM\.EXR_TYPE\.EXR_SUFFIX",
    ):
        fetch_bundle(sl.Provider.ECB, _EXR, tmp_path / "exr", key="M.USD")


_FMR = "http://localhost:8080"
_PROBES = {
    "urn:sdmx:org.sdmx.infomodel.registry.DataConstraint=ECB:CC_EXR_PROBE(1.0)",
    "urn:sdmx:org.sdmx.infomodel.codelist.Hierarchy=ECB:H_CURRENCY_PROBE(1.0)",
    "urn:sdmx:org.sdmx.infomodel.codelist.HierarchyAssociation=ECB:HA_EXR_CURRENCY_PROBE(1.0)",
}


def _fetched_from_fmr(tmp_path: Path, *, path: str, api: str) -> set[str]:
    root = tmp_path / "exr"
    flow = sl.Dataflow.urn_for(agency="ECB", id="EXR", version="1.0")
    manifest = fetch_bundle(f"{_FMR}{path}", flow, root, structures_only=True, api=api, source_id="FMR")
    assert check_bundle(root / manifest.token, at="publish").ok
    assert manifest.provenance is not None
    return set(manifest.provenance.artefacts)


@pytest.mark.vcr
def test_fmr_3_0_closure_holds_what_refers_to_the_dataflow(tmp_path: Path) -> None:
    """``references=all`` over 3.0: the constraint, the hierarchy and the association all arrive."""
    assert _PROBES.issubset(_fetched_from_fmr(tmp_path, path="/sdmx/v2", api="3.0"))


@pytest.mark.vcr
def test_fmr_2_1_closure_holds_no_hierarchy_association(tmp_path: Path) -> None:
    """SDMX 2.1 has no hierarchy association, so over 2.1 only the constraint is kept.

    FMR serves the hierarchy too, but without the association nothing links it
    to `EXR` or its data structure, so it is outside the structural closure.
    """
    fetched = _fetched_from_fmr(tmp_path, path="/ws/public/sdmxapi/rest", api="2.1")
    constraint = "urn:sdmx:org.sdmx.infomodel.registry.DataConstraint=ECB:CC_EXR_PROBE(1.0)"
    assert _PROBES & fetched == {constraint}
