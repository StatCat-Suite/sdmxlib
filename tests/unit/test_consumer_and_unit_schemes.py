"""The data consumer scheme and the organisation unit scheme, modelled (#469).

They were refused on read by name: the SDMX global registry publishes a
`DATA_CONSUMERS` scheme the 2026-10-19 sweep could not read. What is pinned
here is what the censuses cannot see: the round trip through every format, the
unit tree's parent rule, the fixed id and version, and where a unit scheme's
version, free in 2.1 alone, is refused.
"""

import json

import pytest

import sdmxlib as sl
from sdmxlib.formats import parse, serialize
from sdmxlib.model.message import StructureMessage
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema
from tests.support.roundtrip import as_structure
from tests.support.xsd import SchemaVersion, assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

_XML: dict[str, SchemaVersion] = {"2.1": "2.1", "3.0": "3.0", "3.1": "3.1"}
_FORMATS = [*_XML, "json"]


def _units(*, version: str = "1.0") -> sl.OrganisationUnitScheme:
    return sl.OrganisationUnitScheme(
        id="UNITS",
        maintainer="AG",
        version=version,
        name={"en": "units", "fr": "unités"},
        organisation_units=[
            sl.OrganisationUnit(
                id="HQ",
                name={"en": "head office"},
                contacts=(sl.Contact(name={"en": "desk"}, emails=("desk@example.org",)),),
            ),
            sl.OrganisationUnit(id="STATS", name={"en": "statistics"}, parent_id="HQ"),
        ],
    )


def _consumers() -> sl.DataConsumerScheme:
    return sl.DataConsumerScheme(
        id="DATA_CONSUMERS",
        maintainer="AG",
        name={"en": "consumers"},
        consumers=[sl.DataConsumer(id="C1", name={"en": "a consumer"}, uri="https://example.org/c1")],
    )


def _message(units: sl.OrganisationUnitScheme | None = None) -> StructureMessage:
    return StructureMessage(data_consumer_schemes=[_consumers()], organisation_unit_schemes=[units or _units()])


def _valid(version: str, data: bytes) -> None:
    if version == "json":
        assert_valid_sdmx_json(data, compile_json_schema("structure"))
    else:
        assert_valid_sdmx_ml(data, compile_structure_schema(_XML[version]))


@pytest.mark.parametrize("version", _FORMATS)
def test_both_schemes_round_trip_schema_valid(version: str) -> None:
    written = serialize(_message(), version=version)
    _valid(version, written)
    back = as_structure(parse(written))
    assert back.data_consumer_schemes[0] == _consumers()
    assert back.organisation_unit_schemes[0] == _units()
    assert (back.lost, back.refused) == ((), ())


def test_a_unit_parent_names_a_unit_of_its_scheme() -> None:
    with pytest.raises(sl.ValidationError, match=r"parent_id 'NOPE' is not an organisation unit in this scheme"):
        sl.OrganisationUnitScheme(
            id="UNITS",
            maintainer="AG",
            name={"en": "u"},
            organisation_units=[sl.OrganisationUnit(id="A", name={"en": "a"}, parent_id="NOPE")],
        )


def test_a_unit_tree_does_not_loop() -> None:
    with pytest.raises(sl.ValidationError, match="cycle"):
        sl.OrganisationUnitScheme(
            id="UNITS",
            maintainer="AG",
            name={"en": "u"},
            organisation_units=[
                sl.OrganisationUnit(id="A", name={"en": "a"}, parent_id="B"),
                sl.OrganisationUnit(id="B", name={"en": "b"}, parent_id="A"),
            ],
        )


@pytest.mark.parametrize(
    ("scheme_id", "version", "field"), [("CONSUMERS", "1.0", "id"), ("DATA_CONSUMERS", "2.0", "version")]
)
def test_a_consumer_scheme_has_the_fixed_id_and_version(scheme_id: str, version: str, field: str) -> None:
    with pytest.raises(sl.ValidationError, match=rf"DataConsumerScheme {field} is fixed"):
        sl.DataConsumerScheme(id=scheme_id, version=version, maintainer="AG", name={"en": "c"})


def test_sdmx_ml_21_holds_a_unit_scheme_at_any_version() -> None:
    """2.1 alone lets an organisation unit scheme carry another version, and the model holds it."""
    back = as_structure(parse(serialize(_message(_units(version="2.1.0")), version="2.1")))
    assert back.organisation_unit_schemes[0].version == "2.1.0"


@pytest.mark.parametrize("version", ["3.0", "3.1", "json"])
def test_a_unit_scheme_at_another_version_is_refused_where_the_format_fixes_it(version: str) -> None:
    with pytest.raises(sl.ValidationError, match=r"OrganisationUnitScheme:UNITS\.version .+ has no .+ form"):
        serialize(_message(_units(version="2.1.0")), version=version)


def test_the_sdmx_json_reader_refuses_a_unit_scheme_at_another_version() -> None:
    """The const its schema gives the version: refused as the entry it is, the rest read."""
    doc = json.loads(serialize(_message(), version="json"))
    doc["data"]["organisationUnitSchemes"][0]["version"] = "2.1.0"
    back = as_structure(parse(json.dumps(doc).encode(), strict=False))
    assert back.organisation_unit_schemes.ids() == []
    (refusal,) = back.refused
    assert "version is fixed to '1.0' in SDMX-JSON" in refusal.reason
    assert back.data_consumer_schemes.ids() == ["DATA_CONSUMERS"]


def test_the_query_language_walks_the_unit_tree_and_both_items_contacts() -> None:
    q = sl.q
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all([_units(), _consumers()])
        assert reg.query(q.organisation_unit).filter(q.organisation_unit.parent.id == "HQ").ids() == ["STATS"]
        assert reg.query(q.organisation_unit).filter(q.organisation_unit.children.id == "STATS").ids() == ["HQ"]
        has_desk = q.organisation_unit.contacts.emails.has("desk@example.org")
        assert reg.query(q.organisation_unit).filter(has_desk).ids() == ["HQ"]
        assert reg.query(q.data_consumer_scheme).filter(q.data_consumer_scheme.data_consumers.id == "C1").ids() == [
            "DATA_CONSUMERS"
        ]
