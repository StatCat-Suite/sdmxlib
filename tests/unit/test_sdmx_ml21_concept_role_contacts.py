"""SDMX-ML 2.1 concept_role + organisation Contact round-trip (#159 Table B).

The 2.1 writer previously omitted `<str:ConceptRole>` on components and
`<str:Contact>` on Agency/DataProvider entirely; the reader parsed
neither. These pin the round-trip and XSD conformance.
"""

import pytest

from sdmxlib.formats.sdmx_ml21 import reader as r21
from sdmxlib.formats.sdmx_ml21 import writer as w21
from sdmxlib.model.collections import ItemList
from sdmxlib.model.datastructure import AttachmentLevel, Attribute, DataStructure, Dimension, Measure
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.organisation import Agency, AgencyScheme, Contact, DataProvider, DataProviderScheme
from tests.support.factories import concept_ref
from tests.support.roundtrip import structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit


def test_ml21_concept_role_round_trips_and_is_xsd_valid() -> None:
    # ConceptRole is valid on DimensionType and AttributeType in v2.1, but NOT
    # on PrimaryMeasureType — so it round-trips on dimensions and attributes.
    dsd = DataStructure(
        id="DSD",
        maintainer="T",
        version="1.0",
        name={"en": "d"},
        dimensions=ItemList([Dimension(id="FREQ", concept=concept_ref("FREQ"), concept_role=("FREQUENCY",))]),
        attributes=ItemList(
            [
                Attribute(
                    id="OBS_STATUS",
                    concept=concept_ref("OBS_STATUS"),
                    usage="mandatory",
                    attachment=AttachmentLevel.OBSERVATION,
                    concept_role=("ENTITY",),
                )
            ]
        ),
        measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS"))]),
    )
    written = w21.write(StructureMessage(data_structures=[dsd]))
    assert_valid_sdmx_ml(written, structure_schema("2.1"))
    back = r21.read(written).data_structures["DSD"]
    assert tuple(back.dimensions["FREQ"].concept_role) == ("FREQUENCY",)
    assert tuple(back.attributes["OBS_STATUS"].concept_role) == ("ENTITY",)


def test_ml21_contacts_round_trip_and_are_xsd_valid() -> None:
    contact = Contact(
        name={"en": "Support"},
        department={"en": "IT"},
        role={"en": "Owner"},
        phones=("+1 555",),
        faxes=("+1 556",),
        x400s=("/C=US/A=X",),
        uris=("https://example.org",),
        emails=("s@example.org",),
    )
    ags = AgencyScheme(
        id="AGENCIES",
        maintainer="T",
        version="1.0",
        name={"en": "a"},
        agencies=ItemList([Agency(id="T", name={"en": "T"}, contacts=(contact,))]),
    )
    dps = DataProviderScheme(
        id="DATA_PROVIDERS",
        maintainer="T",
        version="1.0",
        name={"en": "d"},
        providers=ItemList([DataProvider(id="P", name={"en": "P"}, contacts=(contact,))]),
    )
    written = w21.write(StructureMessage(agency_schemes=[ags], data_provider_schemes=[dps]))
    assert_valid_sdmx_ml(written, structure_schema("2.1"))
    back = r21.read(written)

    for org in (
        next(iter(back.agency_schemes)).agencies["T"],
        next(iter(back.data_provider_schemes)).providers["P"],
    ):
        (c,) = org.contacts
        assert c.department["en"].text == "IT"
        assert c.role["en"].text == "Owner"
        assert c.phones == ("+1 555",)
        assert c.faxes == ("+1 556",)
        assert c.x400s == ("/C=US/A=X",)
        assert c.uris == ("https://example.org",)
        assert c.emails == ("s@example.org",)
