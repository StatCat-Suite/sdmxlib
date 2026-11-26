"""The registry rules FMR 12.5.0 enforces beyond the XSD, replayed (GH-407).

`bench/fmr/registry_rules.py` recorded them against small messages of its own;
the migration's FMR target (GH-399) rests on these. Each assertion compares
what FMR did with what was sent, so it fails if the recording stops showing
the behaviour.
"""

import json

import pytest

from sdmxlib.formats import parse
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.representation import Facet
from tests.support.fmr_oracle import recordings

pytestmark = pytest.mark.integration

registry_rules = pytest.importorskip("bench.fmr.registry_rules")

_CASE = recordings("12.5.0") / "registry-rules"


def _outcome(step: str) -> dict[str, object]:
    loaded = json.loads((_CASE / "outcomes.json").read_text())
    assert isinstance(loaded, dict)
    found = loaded[step]
    assert isinstance(found, dict)
    return found


def _message(name: str) -> StructureMessage:
    msg = parse((_CASE / name).read_bytes())
    assert isinstance(msg, StructureMessage)
    return msg


def test_what_was_sent_is_what_the_recorder_builds_now():
    for name, body in registry_rules.steps():
        assert (_CASE / f"{name}.xml").read_bytes() == body, f"{name} changed: re-record with record-registry-rules"


def test_the_base_message_is_accepted_whole():
    outcome = _outcome("base")
    accepted = outcome["accepted"]
    assert isinstance(accepted, list)
    assert outcome["refusal"] is None
    assert len(accepted) == 5


def test_an_actual_data_constraint_is_refused():
    assert _outcome("actual")["refusal"] == (
        "urn:sdmx:org.sdmx.infomodel.registry.DataConstraint=AG:CC_ACTUAL(1.0.0): "
        "Submission of DataConstraints with Role specified as 'Actual' is not supported"
    )


def test_a_later_version_that_removes_codes_needs_the_next_major():
    assert _outcome("codelist-1.0.0")["refusal"] is None
    assert _outcome("codelist-1.1.0-removes")["refusal"] == (
        "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:CL_V(1.1.0): "
        "Version is lower then the expected version of 2.0.0"
    )


def test_a_later_version_that_only_adds_codes_is_a_minor():
    assert _outcome("codelist-1.1.0-adds")["refusal"] is None


def test_related_dimensions_come_back_in_the_dsd_order():
    sent = _message("base.xml").data_structures["AG:DSD"].attributes["A1"].related_dimensions
    served = _message("back/AG-ml30.xml").data_structures["AG:DSD"].attributes["A1"].related_dimensions
    assert sent == ["D2", "D1"]
    assert served == ["D1", "D2"]


def test_a_monolingual_metadata_attribute_comes_back_with_no_text_format():
    """3.0 reads a metadata attribute with no TextFormat as multilingual, so false becomes true."""
    sent = _message("base.xml").metadata_structures["AG:MSD"].attributes["MA"]
    served = _message("back/AG-ml30.xml").metadata_structures["AG:MSD"].attributes["MA"]
    assert sent.representation == Facet(type="String", is_multilingual=False)
    assert served.representation is None
    assert (sent.min_occurs, sent.max_occurs) == (0, 1)
    assert (served.min_occurs, served.max_occurs) == (1, None)


def test_a_non_ncname_parent_is_accepted_and_served():
    assert _outcome("codelist-non-ncname-parents")["refusal"] is None
    served = _message("back/AG-ml30.xml").codelists["AG:CL_P"]
    assert {code.id: code.parent_id for code in served.codes} == {"_T": None, "1": "_T", "2": "1"}
