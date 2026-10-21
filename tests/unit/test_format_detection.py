"""`parse()` must route on the envelope, not on what the content happens to say (#220).

Detection used to substring-search the first few KB of the raw bytes for
sentinel tokens. A *structure* message whose content merely mentioned one — a
codelist of SDMX message types, an annotation citing the metadata schema — was
routed to the metadata reader. In SDMX-ML 3.0 and SDMX-JSON that returned an
empty `MetadataMessage` with **no error**: the artefacts silently vanished.

These tests pin the envelope-based replacement. The property at the bottom is
the one that would have caught the original bug.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sdmxlib.formats import parse
from sdmxlib.formats.sdmx_json import metadata as json_metadata
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import metadata as ml30_metadata
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ItemList
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import MetadataMessage, StructureMessage
from tests.support.roundtrip import FIXTURES
from tests.support.strategies import names

pytestmark = pytest.mark.unit

_WRITERS = {
    "2.1": ml21_writer.write,
    "3.0": ml30_writer.write,
    "3.1": ml31_writer.write,
    "json": json_writer.write,
}

#: Tokens the old substring detection keyed on. Each is plausible inside a real
#: structure message — a codelist *of message types*, documentation prose.
_SENTINELS = [
    "Message types including GenericMetadata",
    "Reported per the metadata-schema rules",
    'Envelope with a "metadataSets" array',
    "GenericMetadata",
]


def _message_naming(text: str) -> StructureMessage:
    return StructureMessage(
        codelists=[
            Codelist(
                id="CL_MSGTYPE",
                maintainer="TEST",
                version="1.0",
                name=InternationalString(en=text),
                codes=ItemList([Code(id="X", name=InternationalString(en="x"))]),
            )
        ]
    )


@pytest.mark.parametrize("fmt", list(_WRITERS))
@pytest.mark.parametrize("sentinel", _SENTINELS)
def test_structure_message_mentioning_a_sentinel_stays_a_structure_message(fmt: str, sentinel: str):
    """Content must not decide the message kind — the envelope must."""
    written = _WRITERS[fmt](_message_naming(sentinel))
    got = parse(written)
    assert isinstance(got, StructureMessage), f"{fmt}: routed to {type(got).__name__}"
    assert got.codelists.ids() == ["CL_MSGTYPE"], f"{fmt}: artefacts lost"


@pytest.mark.parametrize("fmt", list(_WRITERS))
def test_sentinel_beyond_the_old_byte_window_is_also_ignored(fmt: str):
    """The old detection only looked at the first 4 KB; the new one has no window.

    A document whose sentinel appears *after* that prefix was routed correctly
    before by luck. Nothing should depend on where in the payload the text sits.
    """
    padding = "x" * 6000
    written = _WRITERS[fmt](_message_naming(f"{padding} GenericMetadata"))
    assert isinstance(parse(written), StructureMessage)


@pytest.mark.parametrize("writer", [json_metadata.write, ml30_metadata.write], ids=["json", "3.0"])
def test_real_metadata_messages_are_still_detected(writer):
    """The fix must not overshoot: genuine metadata still routes to metadata."""
    sample = (FIXTURES / "sdmx_metadata" / "sdmx_twg_constructed_sample.json").read_bytes()
    msg = json_metadata.read(sample)
    assert isinstance(parse(writer(msg)), MetadataMessage)


@given(text=names(), fmt=st.sampled_from(list(_WRITERS)))
def test_written_structure_messages_parse_back_as_structure_messages(text, fmt: str):
    """For any artefact text, ``parse(write(msg))`` preserves the message kind.

    This is the property the #220 bug violated. It is stated over generated
    content precisely because the failure depended on *what the text said* —
    a class of bug no fixture set is likely to contain on purpose.
    """
    msg = StructureMessage(
        codelists=[
            Codelist(
                id="CL",
                maintainer="TEST",
                version="1.0",
                name=text,
                codes=ItemList([Code(id="X", name=InternationalString(en="x"))]),
            )
        ]
    )
    assert isinstance(parse(_WRITERS[fmt](msg)), StructureMessage)
