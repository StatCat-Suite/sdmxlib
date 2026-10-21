"""What mutation found the SDMX-JSON writer's tests could not see (#458).

The triage of the rest — equivalent, and why — is in
`docs/design/mutation-evidence.md`.

As with both SDMX-ML writers (installments 5 and 7), these are what only the
document holds: the envelope, which no reader returns, its layout, and the
metadata back-links' text.
"""

import json

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.formats.sdmx_json import metadata as json_metadata
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.model.message import MetadataMessage, StructureMessage
from sdmxlib.model.validation import ValidationError
from tests.support import maximal

pytestmark = pytest.mark.unit


def _flow(version: str = "1.0") -> sl.Dataflow:
    return sl.Dataflow(id="DF", maintainer="AG", version=version, name={"en": "df"})


def _message() -> StructureMessage:
    return StructureMessage(dataflows=[_flow()])


# ── the envelope ──────────────────────────────────────────────────────────


def test_the_envelope_is_the_one_sdmxlib_writes() -> None:
    """With no header, the placeholders SDMX-ML writes (#480): fixed, so a write is reproducible.

    `prepared` carries its zone: both schemas require one.
    """
    meta = json.loads(json_writer.write(_message()))["meta"]
    assert (meta["id"], meta["sender"], meta["prepared"]) == ("SDMX_MSG", {"id": "sdmxlib"}, "2000-01-01T00:00:00Z")
    assert meta["schema"] == json_writer.STRUCTURE_SCHEMA_URL
    assert json_writer.write(_message()) == json_writer.write(_message())


def test_a_written_document_is_compact_unless_pretty_printed() -> None:
    plain = json_writer.write(_message())
    pretty = json_writer.write(_message(), pretty_print=True)
    assert b"\n" not in plain
    second = pretty.splitlines()[1]
    assert second.startswith(b'  "'), "two-space indent"
    assert not second.startswith(b"   "), "two-space indent"
    assert json.loads(plain) == json.loads(pretty) | {"meta": json.loads(plain)["meta"]}


# ── the metadata back-links ───────────────────────────────────────────────


def _set_urn(set_id: str) -> str:
    return f"urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataSet=AG:{set_id}(1.0)"


@pytest.mark.parametrize("base", ["https://h/sdmx/v2/", "https://h/sdmx/v2", "/sdmx/v2/", "https://h/APIX/"])
def test_a_back_link_names_the_report_by_href_and_urn(base: str) -> None:
    """One trailing slash trimmed, a leading one kept."""
    flow = _flow()
    out = json_writer.write(_message(), metadata_links={str(flow.urn): [_set_urn("MS1")]}, base_url=base)
    (written,) = json.loads(out)["data"]["dataflows"]
    (link,) = (link for link in written["links"] if link.get("rel") == "metadata")
    assert link == {
        "rel": "metadata",
        "href": f"{base.rstrip('/')}/metadata/metadataset/AG/MS1/1.0",
        "urn": _set_urn("MS1"),
    }


def test_back_links_without_a_base_are_refused() -> None:
    flow = _flow()
    with pytest.raises(ValueError, match=r"^base_url is required when metadata_links is provided$"):
        json_writer.write(_message(), metadata_links={str(flow.urn): [_set_urn("MS1")]})


# ── what the schema's `VersionType` refuses ───────────────────────────────


def test_an_unwritable_version_names_its_artefact() -> None:
    """The structure schema's `VersionType` is SDMX-ML 3.0's; `1.2.3.4` is 2.1's alone."""
    with pytest.raises(ValidationError, match=r"Dataflow:DF\.version"):
        json_writer.write(StructureMessage(dataflows=[_flow("1.2.3.4")]))


def test_maximal_writes_and_reads_back() -> None:
    """A floor under the cases above: the writer's whole surface, not one artefact."""
    msg = maximal.message()
    dsds = [attrs.evolve(d, measure_dimension=None) for d in msg.data_structures]
    msg = attrs.evolve(msg, data_structures=sl.ArtefactList(dsds))
    assert sl.parse(json_writer.write(msg)) is not None


def test_the_metadata_envelope_states_its_zone_too() -> None:
    """The metadata schema's `prepared` also requires "ISO 8601 ... including time zone"."""
    meta = json.loads(json_metadata.write(MetadataMessage()))["meta"]
    assert meta["prepared"] == "2000-01-01T00:00:00Z"
