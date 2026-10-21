"""SDMX-JSON round-trip for component conceptRoles, hierarchy hasFormalLevels /
level codingFormat / hierarchical-code validity, and per-mapping validity —
the #159 Table B gaps closed for the JSON structure layer.
"""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from sdmxlib.model.collections import ItemList
from sdmxlib.model.datastructure import AttachmentLevel, Attribute, DataStructure, Dimension
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, Level
from sdmxlib.model.mapping import RepresentationMap, RepresentationMapping
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from sdmxlib.model.urn import SdmxUrn
from tests.support.factories import concept_ref
from tests.support.roundtrip import roundtrip_json

pytestmark = pytest.mark.unit


def _cl_ref(id_: str) -> Ref[Any]:
    return Ref[Any].unresolved(
        SdmxUrn(package="codelist", artefact_class="Codelist", agency="T", id=id_, version="1.0")
    )


def test_json_concept_role_round_trips_on_components() -> None:
    dsd = DataStructure(
        id="DSD",
        maintainer="T",
        version="1.0",
        name={"en": "d"},
        dimensions=ItemList([Dimension(id="FREQ", concept=concept_ref("FREQ"), concept_role=("FREQUENCY",))]),
        attributes=ItemList(
            [
                Attribute(
                    id="A", concept=concept_ref("A"), attachment=AttachmentLevel.OBSERVATION, concept_role=("ENTITY",)
                )
            ]
        ),
    )
    back = roundtrip_json(StructureMessage(data_structures=[dsd])).data_structures["DSD"]
    assert tuple(back.dimensions["FREQ"].concept_role) == ("FREQUENCY",)
    assert tuple(back.attributes["A"].concept_role) == ("ENTITY",)


def test_json_hierarchy_formal_levels_coding_and_hcode_validity_round_trip() -> None:
    lvl = Level(id="L1", name={"en": "l1"}, coding_format=Facet(type="AlphaNumeric", max_length=3))
    h = Hierarchy(
        id="H",
        maintainer="T",
        version="1.0",
        name={"en": "h"},
        has_formal_levels=True,
        levels=[lvl],
        tree=[
            HierarchicalCode(
                id="R",
                code=_cl_ref("CL"),
                level=lvl,
                valid_from=datetime(2020, 6, 1),
                valid_to=datetime(2021, 6, 1),
            )
        ],
    )
    back = roundtrip_json(StructureMessage(hierarchies=[h])).hierarchies["H"]
    assert back.has_formal_levels is True
    coding = back.levels["L1"].coding_format
    assert isinstance(coding, Facet)
    assert coding.type == "AlphaNumeric"
    assert coding.max_length == 3
    assert back.tree["R"].valid_from == datetime(2020, 6, 1, tzinfo=UTC)
    assert back.tree["R"].valid_to == datetime(2021, 6, 1, tzinfo=UTC)


def test_json_hasformallevels_false_round_trips() -> None:
    h = Hierarchy(id="H2", maintainer="T", version="1.0", name={"en": "h"}, has_formal_levels=False)
    assert roundtrip_json(StructureMessage(hierarchies=[h])).hierarchies["H2"].has_formal_levels is False


def test_json_representation_map_per_mapping_validity_round_trips() -> None:
    rm = RepresentationMap(
        id="RM",
        maintainer="T",
        version="1.0",
        name={"en": "m"},
        sources=(_cl_ref("S"),),
        targets=(_cl_ref("D"),),
        mappings=(
            RepresentationMapping(
                sources=("A",), targets=("1",), valid_from=date(2020, 1, 1), valid_to=date(2021, 1, 1)
            ),
        ),
    )
    m = roundtrip_json(StructureMessage(representation_maps=[rm])).representation_maps["RM"].mappings[0]
    assert m.valid_from == date(2020, 1, 1)
    assert m.valid_to == date(2021, 1, 1)
