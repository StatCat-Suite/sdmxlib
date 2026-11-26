"""`RestRegistry.download_structures` and `download_all_structures` against FMR 12.5.0, replayed (GH-402).

Recorded against a local FMR (`bench/fmr/README.md`, the ``fmr-12.5``
container on port 8081). Each test loads agency ``AG`` and two versions of a
codelist first, through `FmrRegistry.submit`, so a test stands alone.

To re-record against a fresh container:
``pytest tests/integration/test_structure_download.py --record-mode=all``.
"""

import collections
import hashlib
import json
from pathlib import Path

import httpx2
import pytest

import sdmxlib as sl
from sdmxlib.api.resources import RESOURCES_21
from tests.support.narrow import present

pytestmark = pytest.mark.integration

_ENDPOINT = "http://127.0.0.1:8081/ws/public/sdmxapi/rest"
_ML21 = sl.StructureFormat.SDMX_ML_2_1


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, object]:
    """The integration matchers, and no Authorization header in a cassette."""
    return {"match_on": ["method", "scheme", "host", "port", "path", "query"], "filter_headers": ["authorization"]}


def _codelist(version: str, codes: str) -> sl.Codelist:
    return sl.Codelist(
        id="CL",
        maintainer="AG",
        version=version,
        name={"en": "c"},
        codes=[sl.Code(id=c, name={"en": c}) for c in codes],
    )


def _loaded() -> sl.FmrRegistry:
    """FMR holding agency AG and CL 1.0.0 and 1.1.0."""
    fmr = sl.FmrRegistry(_ENDPOINT, auth=("root", "password"))
    held = fmr.get(sl.AgencyScheme.urn_for(agency="SDMX", id="AGENCIES", version="1.0"))
    agencies = present(held)
    if "AG" not in agencies.agencies.ids():
        agencies = agencies.add(sl.Agency(id="AG", name={"en": "test agency"}))
    fmr.submit(sl.StructureMessage(agency_schemes=[agencies]))
    fmr.submit(sl.StructureMessage(codelists=[_codelist("1.0.0", "AB"), _codelist("1.1.0", "ABC")]))
    return fmr


def _versions(body: bytes) -> list[str]:
    msg = sl.parse(body)
    assert isinstance(msg, sl.StructureMessage)
    return sorted(c.version for c in msg.codelists)


@pytest.mark.vcr
def test_every_version_is_fetched_by_default():
    with _loaded() as fmr:
        every = fmr.download_structures("codelist", fmt=_ML21, agency="AG")
        latest = fmr.download_structures("codelist", fmt=_ML21, agency="AG", version="latest")
    assert _versions(every) == ["1.0.0", "1.1.0"]
    assert _versions(latest) == ["1.1.0"]


@pytest.mark.vcr
def test_a_whole_registry_copy_records_each_resource(tmp_path: Path):
    with _loaded() as fmr:
        records = fmr.download_all_structures(tmp_path, fmt=_ML21, agency="AG")
    assert len(records) == len(RESOURCES_21) == 19
    by_status = collections.Counter(record.status for record in records)
    assert by_status == {"written": 1, "none-held": 17, "refused": 1}
    (written,) = [record for record in records if record.status == "written"]
    assert written.resource == "codelist"
    body = present(written.path).read_bytes()
    assert _versions(body) == ["1.0.0", "1.1.0"]
    assert written.sha256 == hashlib.sha256(body).hexdigest()
    (refused,) = [record for record in records if record.status == "refused"]
    assert (refused.resource, refused.http_status) == ("attachmentconstraint", 400)
    assert refused.reason == "Unknown REST Resource: attachmentconstraint"
    manifest = json.loads((tmp_path / "downloads.json").read_text())
    assert [entry["status"] for entry in manifest] == [record.status for record in records]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["codelist.xml", "downloads.json"]


@pytest.mark.vcr
def test_a_format_the_registry_cannot_serve_is_its_refusal():
    with _loaded() as fmr, pytest.raises(httpx2.HTTPStatusError, match="406"):
        fmr.download_structures("codelist", fmt=sl.StructureFormat.SDMX_ML_3_1, agency="AG")
