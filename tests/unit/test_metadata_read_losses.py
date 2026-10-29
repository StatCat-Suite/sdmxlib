"""A metadata message records what its reader dropped, and `lossless=True` refuses it (GH-288).

Both metadata readers read a header they cannot build as none. They used to
discard the `ReadGap` that said so, and the SDMX-JSON reader skipped a metadata
set's undeclared keys without counting them, so `parse(..., lossless=True)`
returned a smaller message than its document and raised nothing.
"""

import re
from datetime import UTC, datetime

import pytest

import sdmxlib as sl
from sdmxlib.formats.sdmx_json.metadata import write as write_json
from sdmxlib.formats.sdmx_ml30.metadata import write as write_xml
from sdmxlib.model.header import MetadataHeader, Sender
from sdmxlib.model.message import MetadataMessage
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.metadatastructure import MetadataStructure
from tests.support.factories import code_urn, footnote_metadataflow

pytestmark = pytest.mark.unit


def _message() -> MetadataMessage:
    msd = MetadataStructure(name={"en": "MSD"}, id="MSD_FOOTNOTES", maintainer="STATCAN")
    metadata_set = MetadataSet(
        id="FOOTNOTE_A",
        maintainer="STATCAN",
        structure=footnote_metadataflow(msd, name=None),
        name={"en": "Footnote A"},
        targets=[code_urn("CL_CHARACTERISTIC", "1", agency="STATCAN")],
        attributes=[ReportedAttribute(id="FREQ", value="M")],
    )
    header = MetadataHeader(id="MSG", prepared=datetime(2026, 1, 1, tzinfo=UTC), sender=Sender(id="S"))
    return MetadataMessage(metadata_sets=[metadata_set], header=header)


def _json(*, prepared: str | None = None, extra_key: bool = False) -> bytes:
    raw = write_json(_message())
    if prepared is not None:
        raw, n = re.subn(rb'"prepared":"[^"]*"', b'"prepared":"' + prepared.encode() + b'"', raw)
        assert n == 1
    if extra_key:
        raw, n = re.subn(rb'"targets":\[', b'"somethingNew":1,"targets":[', raw)
        assert n == 1
    return raw


def _xml(*, prepared: str | None = None) -> bytes:
    raw = write_xml(_message())
    if prepared is not None:
        raw, n = re.subn(rb"(<mes:Prepared>)[^<]*(</mes:Prepared>)", rb"\g<1>" + prepared.encode() + rb"\g<2>", raw)
        assert n == 1
    return raw


@pytest.mark.parametrize("document", [_json, _xml], ids=["json", "xml"])
def test_a_clean_metadata_message_loses_nothing(document) -> None:
    """The discrimination check: the same documents, unbroken, read whole."""
    msg = sl.parse(document(), lossless=True)
    assert isinstance(msg, MetadataMessage)
    assert msg.lost == ()
    assert msg.header is not None


@pytest.mark.parametrize(("document", "construct"), [(_json, "meta"), (_xml, "mes:Header")], ids=["json", "xml"])
def test_an_unbuildable_header_is_recorded_as_lost(document, construct: str) -> None:
    msg = sl.parse(document(prepared="not a date"))
    assert isinstance(msg, MetadataMessage)
    assert msg.header is None
    assert [(gap.construct, gap.reason.startswith("read as no header")) for gap in msg.lost] == [(construct, True)]
    assert len(msg.metadata_sets) == 1, "the sets are still read"


@pytest.mark.parametrize("document", [_json, _xml], ids=["json", "xml"])
def test_lossless_refuses_a_metadata_message_with_an_unbuildable_header(document) -> None:
    with pytest.raises(sl.LossyReadError, match=r"^1 loss reading") as caught:
        sl.parse(document(prepared="not a date"), lossless=True)
    assert len(caught.value.lost) == 1
    assert caught.value.assumed == ()
    assert isinstance(caught.value.message, MetadataMessage)


def test_a_metadata_set_key_no_struct_declares_is_recorded_as_lost() -> None:
    msg = sl.parse(_json(extra_key=True))
    assert isinstance(msg, MetadataMessage)
    assert [(gap.urn, gap.construct, gap.count) for gap in msg.lost] == [
        (str(msg.metadata_sets[0].urn), "MetadataSetJson.somethingNew", 1)
    ]
    with pytest.raises(sl.LossyReadError):
        sl.parse(_json(extra_key=True), lossless=True)
