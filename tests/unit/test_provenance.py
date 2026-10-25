"""Which source answered, and when — §1.3's second chain rule.

The chain's three rules are what keep it from being magic: the order is
explicit and never inferred; **every result records which source answered**;
and a routing miss is an error naming what was tried.

The second one was half-kept. `source_registry` was threaded everywhere, and
`fetched_at` — declared right beside it in the DDL — was written by nothing.
Always NULL, in every code path, which is a schema promising provenance it does
not keep. Nothing failed, because nothing read it.

The rule adopted here is that the two travel **together**: an artefact that
came from a source records both; one added locally was not fetched from
anywhere and records neither. A timestamp without a source would imply a fetch
that never happened.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx2
import pytest

import sdmxlib as sl

pytestmark = pytest.mark.unit

_PROVENANCE = "SELECT source_registry, fetched_at FROM sdmx.artefact"

_BODY = (
    b'<?xml version="1.0"?><mes:Structure '
    b'xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message" '
    b'xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure" '
    b'xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">'
    b"<mes:Header><mes:ID>x</mes:ID><mes:Test>false</mes:Test>"
    b"<mes:Prepared>2026-01-01T00:00:00</mes:Prepared><mes:Sender id='S'/></mes:Header>"
    b"<mes:Structures><str:Codelists>"
    b'<str:Codelist id="CL_X" agencyID="AG" version="1.0" isFinal="false">'
    b'<com:Name xml:lang="en">x</com:Name>'
    b'<str:Code id="A"><com:Name xml:lang="en">a</com:Name></str:Code>'
    b"</str:Codelist></str:Codelists></mes:Structures></mes:Structure>"
)


def _codelist() -> sl.Codelist:
    return sl.Codelist(
        id="CL_A",
        maintainer="AG",
        version="1.0",
        name={"en": "a"},
        codes=[sl.Code(id="A", name={"en": "a"})],
    )


def test_an_artefact_from_a_source_records_both() -> None:
    with sl.LocalRegistry(":memory:", source_registry="ECB") as reg:
        reg.add(_codelist())
        [(source, fetched)] = reg.execute(_PROVENANCE)

    assert source == "ECB"
    assert fetched is not None, "`fetched_at` was declared and written by nothing"


def test_a_local_add_records_neither() -> None:
    """Two NULLs rather than a timestamp implying a fetch that never happened."""
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(_codelist())
        assert reg.execute(_PROVENANCE) == [(None, None)]


def test_the_pair_travels_together(tmp_path) -> None:
    """The invariant, over every way in. Either both are set or neither is —
    a source without a time cannot say when, and a time without a source
    cannot say from where, and each alone answers half of rule 2."""
    with sl.LocalRegistry(tmp_path / "s.duckdb", source_registry="A") as reg:
        reg.add(_codelist())
        reg.add(sl.Codelist(id="CL_B", maintainer="AG", version="1.0", name={"en": "b"}), source_registry="B")
        reg.add_all([sl.Codelist(id="CL_C", maintainer="AG", version="1.0", name={"en": "c"})])
        rows = reg.execute(_PROVENANCE)

    assert len(rows) == 3
    for source, fetched in rows:
        assert (source is None) == (fetched is None), f"half-recorded: {source!r} / {fetched!r}"


def test_the_timestamp_is_naive_utc() -> None:
    """`sdmxlib._time`'s convention (#321): a `TIMESTAMP` column has no zone,
    so an aware value returns a different instant per reader's machine."""
    before = datetime.now(UTC).replace(tzinfo=None)
    with sl.LocalRegistry(":memory:", source_registry="ECB") as reg:
        reg.add(_codelist())
        [(_, fetched)] = reg.execute(_PROVENANCE)
    after = datetime.now(UTC).replace(tzinfo=None)

    assert isinstance(fetched, datetime), "the column is a TIMESTAMP"
    assert fetched.tzinfo is None, "naive, or every reader sees a different instant"
    assert before <= fetched <= after, "and UTC, not local time"


def test_an_artefact_fetched_through_the_chain_records_the_remote() -> None:
    """The case the rule exists for: `store=` is a position in the chain, and
    what lands in it must say which source it came from."""
    with sl.LocalRegistry(":memory:") as store:
        with sl.RestRegistry(
            "https://x.test/s",
            id="ECB",
            store=store,
            transport=httpx2.MockTransport(
                lambda _r: httpx2.Response(200, content=_BODY, headers={"content-type": "application/xml"})
            ),
        ) as reg:
            reg.get(sl.Codelist.urn_for(agency="AG", id="CL_X", version="1.0"))
        [(source, fetched)] = store.execute(_PROVENANCE)

    assert source == "ECB", "not 'AG' — the maintainer is not the source that answered"
    assert fetched is not None
