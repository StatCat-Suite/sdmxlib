"""A message's header is read and written in every format (#480).

A structure message's header holds the seven elements `StructureHeaderType`
allows, and a reference-metadata message's four more. Each round-trips
whole through SDMX-ML 2.1, 3.0 and 3.1. SDMX-JSON's ``meta`` carries all
but what `formats.gaps` declares. A message without one is written with
fixed placeholders, so a write is reproducible, and read back as none.
"""

from collections.abc import Callable
from datetime import UTC, datetime

import attrs
import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats import parse, serialize
from sdmxlib.formats.sdmx_json import metadata as json_metadata
from sdmxlib.formats.sdmx_ml30 import metadata as ml30_metadata
from sdmxlib.model.errors import ValidationError
from sdmxlib.model.header import ActionType, Header, MetadataHeader, Party, Sender
from sdmxlib.model.message import MetadataMessage, StructureMessage
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet
from sdmxlib.model.organisation import Contact, MetadataProvider
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema
from tests.support.roundtrip import as_structure
from tests.support.xsd import SchemaVersion, assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

_XML: tuple[SchemaVersion, ...] = ("2.1", "3.0", "3.1")
_CONTACT = Contact(
    name={"en": "Desk", "fr": "Bureau"},
    department={"en": "Dissemination"},
    role={"en": "Contact"},
    phones=("+1-555-0100",),
    faxes=("+1-555-0101",),
    x400s=("x400",),
    uris=("https://example.org/desk",),
    emails=("desk@example.org",),
)
_HEADER = Header(
    id="REQ_1",
    test=True,
    prepared=datetime(2026, 10, 20, 12, 30, 45, tzinfo=UTC),
    sender=Sender(id="ESTAT", name={"en": "Eurostat", "fr": "Eurostat-fr"}, contacts=(_CONTACT,), timezone="-05:00"),
    receivers=(Party(id="ECB", name={"en": "ECB"}, contacts=(_CONTACT,)), Party(id="IMF")),
    name={"en": "A message", "de": "Eine Nachricht"},
    source={"en": "Census", "fr": "Recensement"},
)
_CODELIST = sl.Codelist(id="CL", maintainer="AG", name={"en": "c"})


def _with(header: Header | None) -> StructureMessage:
    return StructureMessage(header=header, codelists=[_CODELIST])


# ── a structure message ──────────────────────────────────────────────────────


@pytest.mark.parametrize("version", _XML)
def test_a_header_round_trips_whole_through_sdmx_ml(version: SchemaVersion) -> None:
    written = serialize(_with(_HEADER), version=version)
    assert_valid_sdmx_ml(written, compile_structure_schema(version))
    assert as_structure(parse(written)).header == _HEADER


def test_sdmx_json_carries_all_but_its_declared_gaps() -> None:
    written = serialize(_with(_HEADER), version="json")
    assert_valid_sdmx_json(written, compile_json_schema("structure", "2.0"))
    back = as_structure(parse(written)).header
    assert [gap.field for gap in sl.unrepresentable(_HEADER, "json")] == ["source", "sender.timezone"]
    assert back == attrs.evolve(_HEADER, source={}, sender=attrs.evolve(_HEADER.sender, timezone=None))


@pytest.mark.parametrize("version", [*_XML, "json"])
def test_no_header_writes_the_placeholders_and_reads_back_as_none(version: str) -> None:
    written = serialize(_with(None), version=version)
    assert written == serialize(_with(None), version=version), "the same message, the same bytes"
    assert b"SDMX_MSG" in written
    assert as_structure(parse(written)).header is None


@pytest.mark.parametrize("version", _XML)
def test_a_header_beside_the_placeholders_is_read(version: str) -> None:
    """The discrimination check: the placeholder values plus a receiver are a header, not the placeholders."""
    header = Header(
        id="SDMX_MSG",
        prepared=datetime(2000, 1, 1, tzinfo=UTC),
        sender=Sender(id="sdmxlib"),
        receivers=(Party(id="R"),),
    )
    assert as_structure(parse(serialize(_with(header), version=version))).header == header


@pytest.mark.parametrize("version", _XML)
def test_a_contacts_id_is_sdmx_jsons_alone(version: str) -> None:
    """SDMX-ML's message `ContactType` has no id; SDMX-JSON's has, and keeps it."""
    header = attrs.evolve(_HEADER, sender=attrs.evolve(_HEADER.sender, contacts=(attrs.evolve(_CONTACT, id="DESK"),)))
    (gap,) = sl.unrepresentable(header, version)
    assert gap.field == "sender.contacts[].id"
    assert as_structure(parse(serialize(_with(header), version=version))).header == _HEADER
    json_back = as_structure(parse(serialize(_with(header), version="json"))).header
    assert json_back is not None
    assert json_back.sender.contacts[0].id == "DESK"


def test_a_date_only_prepared_reads_as_its_midnight() -> None:
    """`HeaderTimeType` is `xs:dateTime` or `xs:date`."""
    root = etree.fromstring(serialize(_with(_HEADER), version="3.0"))
    (prepared,) = root.iter("{*}Prepared")
    prepared.text = "2026-10-20"
    header = as_structure(parse(etree.tostring(root))).header
    assert header is not None
    assert header.prepared == datetime(2026, 10, 20, tzinfo=UTC)


@pytest.mark.parametrize("version", [*_XML, "json"])
def test_a_header_the_model_cannot_hold_is_a_loss_not_a_failure(version: str) -> None:
    """A sender with no id: the artefacts read, the header is None, and the loss is recorded."""
    written = serialize(_with(_HEADER), version=version)
    if version == "json":
        broken = written.replace(b'"id":"ESTAT"', b'"id":null')
    else:
        broken = written.replace(b' id="ESTAT"', b"")
    assert broken != written
    msg = as_structure(parse(broken, strict=False))
    assert (msg.header, list(msg.codelists)) == (None, [_CODELIST])
    assert [gap.construct for gap in msg.lost] == ["meta" if version == "json" else "mes:Header"]
    with pytest.raises(sl.LossyReadError):
        parse(broken, lossless=True)


# ── a reference-metadata message ─────────────────────────────────────────────

_PROVIDER = MetadataProvider.parse_urn(
    "urn:sdmx:org.sdmx.infomodel.base.MetadataProvider=AG:METADATA_PROVIDERS(1.0).P1"
)
_METADATA_HEADER = MetadataHeader(
    id="MD_1",
    prepared=datetime(2026, 1, 1, tzinfo=UTC),
    sender=Sender(id="AG", timezone="Z"),
    receivers=(Party(id="R"),),
    name={"en": "metadata"},
    source={"en": "src"},
    metadata_provider=Ref[MetadataProvider].unresolved(_PROVIDER),
    data_set_action=ActionType.REPLACE,
    data_set_ids=("DS1", "DS2"),
    extracted=datetime(2026, 1, 2, tzinfo=UTC),
)
_SET = MetadataSet(
    id="MS",
    maintainer="AG",
    name={"en": "MS"},
    structure=Ref[Metadataflow].unresolved(
        SdmxUrn(package="metadatastructure", artefact_class="Metadataflow", agency="AG", id="MDF", version="1.0")
    ),
)


def test_a_metadata_header_round_trips_whole_through_sdmx_ml_3_0() -> None:
    written = ml30_metadata.write(MetadataMessage(header=_METADATA_HEADER, metadata_sets=[_SET]))
    assert ml30_metadata.read(written).header == _METADATA_HEADER


def test_sdmx_json_has_no_place_for_a_metadata_headers_four() -> None:
    gaps = {gap.field for gap in sl.unrepresentable(_METADATA_HEADER, "json")}
    assert gaps == {"source", "sender.timezone", "metadata_provider", "data_set_action", "data_set_ids", "extracted"}
    back = json_metadata.read(json_metadata.write(MetadataMessage(header=_METADATA_HEADER, metadata_sets=[_SET])))
    assert back.header == MetadataHeader(
        id="MD_1", prepared=datetime(2026, 1, 1, tzinfo=UTC), sender=Sender(id="AG"), receivers=(Party(id="R"),),
        name={"en": "metadata"},
    )  # fmt: skip


@pytest.mark.parametrize(
    ("write", "read"),
    [(ml30_metadata.write, ml30_metadata.read), (json_metadata.write, json_metadata.read)],
    ids=["ml30", "json"],
)
def test_a_metadata_message_without_a_header_reads_back_as_none(
    write: Callable[[MetadataMessage], bytes], read: Callable[[bytes], MetadataMessage]
) -> None:
    assert read(write(MetadataMessage(metadata_sets=[_SET]))).header is None


# ── the model ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: Party(id="has space"), id="party-id"),
        pytest.param(lambda: Sender(id="S", timezone="EST"), id="timezone"),
        pytest.param(lambda: Header(id="", prepared=datetime(2026, 1, 1), sender=Sender(id="S")), id="header-id"),
        pytest.param(
            lambda: MetadataHeader(id="M", prepared=datetime(2026, 1, 1), sender=Sender(id="S"), data_set_ids=("a b",)),
            id="data-set-id",
        ),
    ],
)
def test_what_no_header_can_hold_is_refused_as_it_is_built(build: object) -> None:
    assert callable(build)
    with pytest.raises(ValidationError):
        build()


@pytest.mark.parametrize("timezone", ["Z", "+14:00", "-05:30", "+00:00"])
def test_a_timezone_is_z_or_an_offset(timezone: str) -> None:
    assert Sender(id="S", timezone=timezone).timezone == timezone


def test_prepared_is_held_utc() -> None:
    naive = Header(id="M", prepared=datetime(2026, 1, 1, 12), sender=Sender(id="S"))
    assert naive.prepared == datetime(2026, 1, 1, 12, tzinfo=UTC)
