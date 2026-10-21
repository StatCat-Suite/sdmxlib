"""Every model datetime is held tagged UTC (#440).

The store returned datetimes tagged UTC (`_time.from_storage`, #321) while the
model held whatever a reader gave it — naive for an unqualified
`xs:dateTime` — so a constraint parsed from OECD (`validFrom` with no zone)
was not equal to itself read back from `LocalRegistry`. The model now applies
the store's reading at its own boundary: a naive value is UTC.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from sdmxlib._time import as_utc
from sdmxlib.formats import parse, serialize
from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.constraint import DataConstraint, ReferencePeriod
from sdmxlib.model.message import StructureMessage
from tests.support.fidelity import compare

pytestmark = pytest.mark.unit


def test_a_naive_value_is_labelled_and_an_aware_one_converted() -> None:
    assert as_utc(datetime(2020, 1, 1)) == datetime(2020, 1, 1, tzinfo=UTC)
    five_east = datetime(2020, 1, 1, 5, tzinfo=timezone(timedelta(hours=5)))
    converted = as_utc(five_east)
    assert converted == datetime(2020, 1, 1, tzinfo=UTC)
    assert converted.tzinfo is UTC
    assert as_utc(None) is None


def test_every_model_datetime_is_normalised_on_construction() -> None:
    codelist = Codelist(name={"en": "C"}, id="C", maintainer="A", valid_from=datetime(2020, 1, 1))
    period = ReferencePeriod(start_period=datetime(2020, 1, 1), end_period=datetime(2021, 1, 1))
    assert codelist.valid_from is not None
    assert codelist.valid_from.tzinfo is UTC
    assert period.start_period.tzinfo is UTC


def test_a_parsed_constraint_equals_itself_read_back_from_the_store() -> None:
    """The identity #440 was about: `parse(...) == registry.get(...)`."""
    constraint = DataConstraint(
        id="CC",
        maintainer="AG",
        name={"en": "c"},
        valid_from=datetime(1950, 1, 1),
        reference_period=ReferencePeriod(start_period=datetime(1950, 1, 1), end_period=datetime(2020, 12, 31)),
    )
    parsed = parse(serialize(StructureMessage(constraints=[constraint]), version="2.1"))
    assert isinstance(parsed, StructureMessage)
    (from_document,) = parsed.constraints
    with LocalRegistry(":memory:") as registry:
        registry.add(from_document)
        from_store = registry.get(from_document.urn)
    assert from_store == from_document


def test_fidelity_compares_instants_and_still_sees_a_changed_one() -> None:
    """Discrimination for `fidelity._normalise_instant`: the same instant written
    with and without a zone is equal; a different instant is not."""

    def doc(valid_from: str) -> bytes:
        return f'<S><Codelist id="C" agencyID="A" version="1.0" validFrom="{valid_from}"/></S>'.encode()

    assert compare(doc("2020-01-01T00:00:00"), doc("2020-01-01T00:00:00+00:00")) == ([], [])
    assert compare(doc("2020-01-01T05:00:00+05:00"), doc("2020-01-01T00:00:00Z")) == ([], [])
    assert compare(doc("2020-01-01T00:00:00"), doc("2020-01-01T01:00:00+00:00")) != ([], [])
