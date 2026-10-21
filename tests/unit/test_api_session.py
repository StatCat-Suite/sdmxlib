"""Unit tests for Session identity map."""

from pathlib import Path

import pytest

from sdmxlib.api.session import Completeness, Session, completeness_of
from sdmxlib.formats import parse
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.urn import SdmxUrn
from tests.support.roundtrip import as_structure

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "sdmx_ml21"


def _minimal() -> bytes:
    return (_FIXTURES / "minimal.xml").read_bytes()


def _msg():
    return as_structure(parse(_minimal()))


# ── ingest ────────────────────────────────────────────────────────────────────


def test_ingest_indexes_codelists():
    session = Session()
    msg = _msg()
    session.ingest(msg.all_artefacts())
    urn = SdmxUrn.make(
        Codelist,
        package="codelist",
        artefact_class="Codelist",
        agency="SDMX",
        id="CL_FREQ",
        version="1.0",
    )
    assert session.get(urn) is not None


def test_ingest_idempotent():
    session = Session()
    msg = _msg()
    session.ingest(msg.all_artefacts())
    session.ingest(msg.all_artefacts())
    assert len(session.all_of_class(Codelist)) == len(msg.codelists)


def test_ingest_indexes_data_structures():
    session = Session()
    msg = _msg()
    session.ingest(msg.all_artefacts())
    assert session.all_of_class(DataStructure)


def test_ingest_indexes_dataflows():
    session = Session()
    msg = _msg()
    session.ingest(msg.all_artefacts())
    assert session.all_of_class(Dataflow)


# ── get ───────────────────────────────────────────────────────────────────────


def test_get_returns_none_for_unknown():
    session = Session()
    urn = SdmxUrn.make(
        Codelist,
        package="codelist",
        artefact_class="Codelist",
        agency="MISSING",
        id="CL_NONE",
        version="1.0",
    )
    assert session.get(urn) is None


def test_get_returns_cached_artefact():
    session = Session()
    msg = _msg()
    session.ingest(msg.all_artefacts())
    urn = SdmxUrn.make(
        Codelist,
        package="codelist",
        artefact_class="Codelist",
        agency="SDMX",
        id="CL_FREQ",
        version="1.0",
    )
    result = session.get(urn)
    assert result is not None
    assert result.id == "CL_FREQ"


# ── all_of_class ──────────────────────────────────────────────────────────────


def test_all_of_class_returns_matching():
    session = Session()
    msg = _msg()
    session.ingest(msg.all_artefacts())
    codelists = session.all_of_class(Codelist)
    assert all(isinstance(c, Codelist) for c in codelists)


def test_all_of_class_empty_for_unknown():
    session = Session()
    session.ingest(_msg().all_artefacts())
    assert session.all_of_class(ProvisionAgreement) == []


# ── first_of_class ────────────────────────────────────────────────────────────


def test_first_of_class_returns_one():
    session = Session()
    session.ingest(_msg().all_artefacts())
    result = session.first_of_class(Codelist)
    assert result is not None
    assert isinstance(result, Codelist)


def test_first_of_class_none_for_unknown():
    session = Session()
    assert session.first_of_class(ProvisionAgreement) is None


# ── extract ───────────────────────────────────────────────────────────────────


def test_extract_finds_artefact():
    session = Session()
    msg = _msg()
    session.ingest(msg.all_artefacts())
    urn = SdmxUrn.make(
        Codelist,
        package="codelist",
        artefact_class="Codelist",
        agency="SDMX",
        id="CL_FREQ",
        version="1.0",
    )
    result = session.extract(msg, urn)
    assert result is not None
    assert result.id == "CL_FREQ"


def test_extract_returns_none_for_wrong_urn():
    session = Session()
    msg = _msg()
    session.ingest(msg.all_artefacts())
    urn = SdmxUrn.make(
        Codelist,
        package="codelist",
        artefact_class="Codelist",
        agency="TEST",
        id="CL_MISSING",
        version="1.0",
    )
    assert session.extract(msg, urn) is None


def test_extract_returns_none_for_unknown_class():
    session = Session()
    msg = _msg()
    session.ingest(msg.all_artefacts())
    urn = SdmxUrn.make(
        object,
        package="codelist",
        artefact_class="UnknownClass",
        agency="TEST",
        id="X",
        version="1.0",
    )
    assert session.extract(msg, urn) is None


# ── completeness (#344) ───────────────────────────────────────────────────────

_CL_FREQ = SdmxUrn.make(
    object,
    package="codelist",
    artefact_class="Codelist",
    agency="SDMX",
    id="CL_FREQ",
    version="1.0",
)


def test_a_deeper_request_than_what_is_held_does_not_hit():
    """The bug. A `referencepartial` fetch used to set a boolean that a
    `partial=False` read then hit, so the caller was handed trimmed codelists
    having explicitly asked for untrimmed."""
    session = Session()
    session.ingest(_msg().all_artefacts(), completeness=Completeness.PARTIAL)

    assert session.get_at_least(_CL_FREQ, Completeness.PARTIAL) is not None
    assert session.get_at_least(_CL_FREQ, Completeness.FULL) is None, "PARTIAL must not satisfy FULL"


def test_a_shallower_request_than_what_is_held_hits():
    """The other direction, which the ordering settles for free: a full answer
    is more than a partial request asked for, not less. A fix that keyed the
    cache on `partial` instead would miss here and re-fetch for nothing."""
    session = Session()
    session.ingest(_msg().all_artefacts(), completeness=Completeness.FULL)

    for wanted in Completeness:
        assert session.get_at_least(_CL_FREQ, wanted) is not None, wanted


def test_an_unresolved_ingest_satisfies_only_an_unresolved_request():
    session = Session()
    session.ingest(_msg().all_artefacts())

    assert session.get_at_least(_CL_FREQ, Completeness.UNRESOLVED) is not None
    assert session.get_at_least(_CL_FREQ, Completeness.PARTIAL) is None
    assert session.get_at_least(_CL_FREQ, Completeness.FULL) is None


def test_a_urn_never_ingested_is_unresolved_rather_than_missing():
    """`completeness_for` has to answer for a URN it has never seen, and
    `UNRESOLVED` is the honest answer: nothing is known about its references.
    A `KeyError` would push the same default onto every caller."""
    assert Session().completeness_for(_CL_FREQ) is Completeness.UNRESOLVED


def test_learning_is_monotonic():
    """A later, shallower ingest must not downgrade what is already known.

    The realistic path is an all-stubs sweep: `browse`/`query` fetches every
    artefact of a type at `references=none`, and one of them may be a DSD this
    process already holds untrimmed. Without `max` that sweep silently forgets
    the deep fetch, and the next `partial=False` read pays for it again — a
    regression visible only as extra requests, which is exactly the kind
    nothing catches.
    """
    session = Session()
    session.ingest(_msg().all_artefacts(), completeness=Completeness.FULL)
    session.ingest(_msg().all_artefacts(), completeness=Completeness.UNRESOLVED)

    assert session.completeness_for(_CL_FREQ) is Completeness.FULL
    assert session.get_at_least(_CL_FREQ, Completeness.FULL) is not None


def test_repeated_ingests_climb_to_the_deepest_seen():
    session = Session()
    for level in (Completeness.UNRESOLVED, Completeness.PARTIAL, Completeness.UNRESOLVED):
        session.ingest(_msg().all_artefacts(), completeness=level)
    assert session.completeness_for(_CL_FREQ) is Completeness.PARTIAL


# ── completeness_of: `detail` and `references` are different questions ────────


@pytest.mark.parametrize(
    ("detail", "references", "expected"),
    [
        ("full", "none", Completeness.UNRESOLVED),
        ("allstubs", "none", Completeness.UNRESOLVED),
        ("referencepartial", "descendants", Completeness.PARTIAL),
        ("full", "descendants", Completeness.FULL),
        ("full", "children", Completeness.FULL),
    ],
)
def test_completeness_of_reads_references_before_detail(detail, references, expected):
    """``detail=full&references=none`` is the trap: a complete *artefact* with
    no references followed at all. It is the commonest unresolved fetch in the
    library, and reading `detail` alone would mark it the most complete thing
    there is — turning every shallow `get` into a cache hit for `partial=False`.
    """
    assert completeness_of(detail=detail, references=references) is expected
