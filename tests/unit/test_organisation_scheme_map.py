"""`OrganisationSchemeMap`, modelled (#448).

SDMX-ML 2.1 (inside a StructureSet), 3.0 and 3.1, and SDMX-JSON all carry it;
the model had no class, so the 3.x and JSON readers refused it and 2.1
reported it lost. Its ends are any organisation scheme, and need not be of
one kind: agencies onto data providers is the case here.
"""

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.formats import parse, serialize
from sdmxlib.model.mapping import ConceptSchemeMap, ItemMap, OrganisationSchemeMap
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.query.accessors import q
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema
from tests.support.narrow import present
from tests.support.roundtrip import as_structure, structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_AGENCIES = sl.AgencyScheme(
    id="AGENCIES", maintainer="AG", name={"en": "agencies"}, agencies=[sl.Agency(id="A1", name={"en": "a1"})]
)
_PROVIDERS = sl.DataProviderScheme(
    id="DATA_PROVIDERS",
    maintainer="AG",
    name={"en": "providers"},
    providers=[sl.DataProvider(id="P1", name={"en": "p1"})],
)
_MAP = OrganisationSchemeMap(
    id="OSM",
    maintainer="AG",
    name={"en": "agencies to providers"},
    source=Ref.to(_AGENCIES),
    target=Ref.to(_PROVIDERS),
    maps=(ItemMap(source_id="A1", target_id="P1"),),
)


def _message() -> StructureMessage:
    return StructureMessage(
        agency_schemes=[_AGENCIES], data_provider_schemes=[_PROVIDERS], organisation_scheme_maps=[_MAP]
    )


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1", "json"])
def test_every_format_reads_back_the_map_it_wrote(version: str) -> None:
    written = serialize(_message(), version=version)
    if version == "json":
        for schema in ("2.0", "2.1"):
            assert_valid_sdmx_json(written, compile_json_schema("structure", schema))
    else:
        assert_valid_sdmx_ml(written, structure_schema(version))
    back = as_structure(parse(written))
    assert back.refused == ()
    assert back.lost == ()
    (osm,) = back.organisation_scheme_maps
    # By URN: no reader indexes organisation schemes, so the ends come back
    # unresolved, as a reference to an agency scheme always has.
    assert attrs.evolve(osm, source=None, target=None) == attrs.evolve(_MAP, source=None, target=None)
    assert present(osm.source).urn == _AGENCIES.urn
    assert present(osm.target).urn == _PROVIDERS.urn


def test_2_1_names_it_for_its_structure_set() -> None:
    """The container rule every 2.1 map follows (#421): `<map>@<set>` inside a declared set."""
    in_set = attrs.evolve(_MAP, id="OSM@SS")
    structure_set = sl.StructureSet(id="SS", maintainer="AG", name={"en": "set"}, maps=(Ref.to(in_set),))
    msg = StructureMessage(
        agency_schemes=[_AGENCIES],
        data_provider_schemes=[_PROVIDERS],
        organisation_scheme_maps=[in_set],
        structure_sets=[structure_set],
    )
    written = serialize(msg, version="2.1")
    assert b'<str:OrganisationSchemeMap id="OSM"' in written
    (back,) = as_structure(parse(written)).organisation_scheme_maps
    assert back.id == "OSM@SS"


def test_the_store_holds_it_and_the_query_reaches_either_kind_of_end() -> None:
    concept_map = ConceptSchemeMap(
        id="CSM",
        maintainer="AG",
        name={"en": "concepts"},
        source=Ref[object].unresolved(sl.ConceptScheme.urn_for(agency="AG", id="CS", version="1.0")),
        target=Ref[object].unresolved(sl.ConceptScheme.urn_for(agency="AG", id="CS", version="1.0")),
        maps=(ItemMap(source_id="X", target_id="Y"), ItemMap(source_id="Z", target_id="W")),
    )
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all([_AGENCIES, _PROVIDERS, _MAP, concept_map])
        stored = reg.get(_MAP.urn)
        assert isinstance(stored, OrganisationSchemeMap)
        assert stored.maps == _MAP.maps
        assert reg.query(q.organisation_scheme_map).ids() == ["OSM"]
        ends = (
            reg.query(q.organisation_scheme_map)
            .select(source=q.organisation_scheme_map.source.id, target=q.organisation_scheme_map.target.id)
            .collect()
        )
        assert ends.to_dicts() == [{"source": "AGENCIES", "target": "DATA_PROVIDERS"}]
        pairs = reg.query(q.organisation_scheme_map).select(n=q.organisation_scheme_map.item_maps.count()).collect()
        assert pairs.to_dicts() == [{"n": 1}], "the shared item_map table keeps the concept map's two apart"
