"""Structure downloads: the format asked for is the format written, or nothing is (GH-402)."""

from pathlib import Path

import httpx2
import pytest

import sdmxlib as sl
from sdmxlib.api.client import SdmxClient
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.formats.structure_format import sniff

pytestmark = pytest.mark.unit

_MSG = sl.StructureMessage(
    codelists=[
        sl.Codelist(
            id="CL", maintainer="AG", version="1.0.0", name={"en": "c"}, codes=[sl.Code(id="A", name={"en": "a"})]
        )
    ]
)
_ML21, _ML30 = sl.StructureFormat.SDMX_ML_2_1, sl.StructureFormat.SDMX_ML_3_0


@pytest.mark.parametrize(
    ("written", "fmt"),
    [
        (ml21_writer.write, _ML21),
        (ml30_writer.write, _ML30),
        (ml31_writer.write, sl.StructureFormat.SDMX_ML_3_1),
        (json_writer.write, sl.StructureFormat.SDMX_JSON_2_0_0),
    ],
)
def test_sniff_reads_each_format_from_its_head(written, fmt: sl.StructureFormat) -> None:
    assert sniff(written(_MSG)[:4096]) == fmt.sniffed


def _registry(body: bytes, content_type: str) -> sl.RestRegistry:
    def answer(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body, headers={"content-type": content_type})

    return sl.RestRegistry("https://x.test/s", api="2.1", transport=httpx2.MockTransport(answer))


def test_a_generic_content_type_is_checked_by_the_body(tmp_path: Path) -> None:
    """FMR's shape: 3.0 under application/xml, when 2.1 was asked for."""
    target = tmp_path / "codelist.xml"
    with (
        _registry(ml30_writer.write(_MSG), "application/xml;charset=UTF-8") as reg,
        pytest.raises(ValueError, match=r"the body is 3\.0 under 'application/xml;charset=UTF-8'"),
    ):
        reg.download_structures("codelist", fmt=_ML21, to=target)
    assert not target.exists()


def test_a_generic_content_type_with_the_right_body_is_written(tmp_path: Path) -> None:
    body = ml21_writer.write(_MSG)
    with _registry(body, "application/xml") as reg:
        written = reg.download_structures("codelist", fmt=_ML21, to=tmp_path / "codelist.xml")
    assert written.read_bytes() == body


def test_an_sdmx_content_type_is_held_to_the_format() -> None:
    with (
        _registry(ml21_writer.write(_MSG), "application/vnd.sdmx.structure+xml;version=3.0.0") as reg,
        pytest.raises(ValueError, match=r"Asked for 'application/vnd\.sdmx\.structure\+xml;version=2\.1'"),
    ):
        reg.download_structures("codelist", fmt=_ML21)


def test_a_suffix_that_contradicts_the_format_is_refused(tmp_path: Path) -> None:
    with _registry(b"", "application/xml") as reg, pytest.raises(ValueError, match=r"ends in '\.json'"):
        reg.download_structures("codelist", fmt=_ML21, to=tmp_path / "codelist.json")


@pytest.mark.parametrize(
    ("api", "args", "url"),
    [
        ("2.1", ("codelist",), "https://x.test/s/codelist/all/all/all"),
        ("2.1", ("codelist", "AG", "CL", "latest"), "https://x.test/s/codelist/AG/CL/latest"),
        ("3.0", ("codelist",), "https://x.test/s/structure/codelist/*/*/*"),
        ("3.0", ("codelist", "AG", "all", "latest"), "https://x.test/s/structure/codelist/AG/*/~"),
    ],
)
def test_the_query_url_spells_all_and_latest_for_each_api(api: str, args: tuple[str, ...], url: str) -> None:
    with SdmxClient("https://x.test/s", api=api) as client:
        assert client.structure_query_url(*args) == url


def _copy(directory: Path, files: dict[str, bytes], statuses: dict[str, str]) -> Path:
    import json

    for name, body in files.items():
        (directory / name).write_bytes(body)
    records = [{"resource": n.split(".")[0], "url": "u", "status": statuses[n], "path": n} for n in statuses]
    (directory / "downloads.json").write_text(json.dumps(records))
    return directory


def test_a_copy_reads_back_as_one_message(tmp_path: Path) -> None:
    concepts = sl.StructureMessage(
        concept_schemes=[sl.ConceptScheme(id="CS", maintainer="AG", version="1.0.0", name={"en": "c"})]
    )
    copy = _copy(
        tmp_path,
        {"codelist.xml": ml21_writer.write(_MSG), "conceptscheme.xml": ml21_writer.write(concepts)},
        {"codelist.xml": "written", "conceptscheme.xml": "written", "process.xml": "none-held"},
    )
    read = sl.read_all_structures(copy)
    assert [c.id for c in read.codelists] == ["CL"]
    assert [c.id for c in read.concept_schemes] == ["CS"]


def test_an_artefact_in_two_files_is_refused(tmp_path: Path) -> None:
    body = ml21_writer.write(_MSG)
    copy = _copy(tmp_path, {"a.xml": body, "b.xml": body}, {"a.xml": "written", "b.xml": "written"})
    with pytest.raises(
        ValueError, match=r"1 artefact\(s\) in more than one file of the copy: .*CL\(1\.0\.0\) \(a\.xml and b\.xml\)"
    ):
        sl.read_all_structures(copy)
