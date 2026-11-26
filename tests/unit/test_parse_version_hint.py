"""A version hint that contradicts the document is refused, not read as empty.

FMR 12.5.0 answers a 2.1 REST path with SDMX-ML 3.0 for some artefacts. The
client passed its API version to `parse` as a hint, and the 2.1 reader, keyed
on its own namespace, found nothing in a 3.0 document: the artefact came back
as None with nothing refused (found by `SubmissionReport.verify`, GH-400).
"""

import httpx2
import pytest

import sdmxlib as sl
from sdmxlib.formats import parse
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.model.errors import SdmxParseError
from tests.support.narrow import present

pytestmark = pytest.mark.unit

_CODELIST = sl.Codelist(
    id="CL", maintainer="AG", version="1.0.0", name={"en": "c"}, codes=[sl.Code(id="A", name={"en": "a"})]
)
_MSG = sl.StructureMessage(codelists=[_CODELIST])


@pytest.mark.parametrize(
    ("written", "hint", "found"),
    [(ml30_writer.write, "2.1", "3.0"), (ml21_writer.write, "3.0", "2.1"), (ml30_writer.write, "3.1", "3.0")],
)
def test_a_hint_naming_another_ml_version_is_refused(written, hint: str, found: str) -> None:
    with pytest.raises(SdmxParseError, match=rf"the document is SDMX-ML {found}, and version='{hint}' was asked for"):
        parse(written(_MSG), version=hint)


@pytest.mark.parametrize(("written", "hint"), [(ml30_writer.write, "3.0"), (ml21_writer.write, "2.1")])
def test_a_hint_that_agrees_reads_the_document(written, hint: str) -> None:
    msg = parse(written(_MSG), version=hint)
    assert isinstance(msg, sl.StructureMessage)
    assert [c.id for c in msg.codelists] == ["CL"]


def test_a_2_1_client_reads_a_3_0_answer() -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=ml30_writer.write(_MSG), headers={"content-type": "application/xml"})

    with sl.RestRegistry("https://x.test/s", api="2.1", transport=httpx2.MockTransport(answer)) as reg:
        found = reg.get(sl.Codelist.urn_for(agency="AG", id="CL", version="1.0.0"))
    assert present(found).codes.ids() == ["A"]
