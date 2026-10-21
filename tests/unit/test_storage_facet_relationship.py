"""Storage round-trip coverage for facet detail + attribute relationship +
contact fields — the #159 Table B storage gaps closed by adding columns.

The generic guard (test_roundtrip_property_guard) covers the concept facet
detail, SERIES attribute relationship, and contact faxes/x400s. This file
pins the attribute-relationship variants the guard doesn't exercise
(GROUP -> related_group; observation carries only the level) plus the full
component Facet, so the reverse-mapping stays correct.
"""

import duckdb
import pytest

from sdmxlib.model.collections import ItemList
from sdmxlib.model.datastructure import AttachmentLevel, Attribute, DataStructure, Dimension, Group
from sdmxlib.model.representation import Facet
from sdmxlib.storage.readers import get_artefact
from sdmxlib.storage.schema import create_schema
from sdmxlib.storage.writers import put_data_structure
from tests.support.factories import concept_ref
from tests.support.narrow import present

pytestmark = pytest.mark.unit


def _roundtrip(dsd: DataStructure) -> DataStructure:
    conn = create_schema(duckdb.connect(":memory:"))
    put_data_structure(conn, dsd)
    urn = str(DataStructure.urn_for(agency=dsd.agency_id, id=dsd.id, version=dsd.version))
    read = get_artefact(conn, urn, eager=True)
    assert isinstance(read, DataStructure)
    return read


def test_attribute_group_relationship_round_trips() -> None:
    dsd = DataStructure(
        id="DSD",
        maintainer="T",
        version="1.0",
        name={"en": "d"},
        dimensions=ItemList([Dimension(id="FREQ", concept=concept_ref("FREQ"))]),
        groups=ItemList([Group(id="G1", dimension_ids=["FREQ"])]),
        attributes=ItemList(
            [Attribute(id="A_G", concept=concept_ref("A_G"), attachment=AttachmentLevel.GROUP, related_group="G1")]
        ),
    )
    attr = _roundtrip(dsd).attributes["A_G"]
    assert attr.attachment is AttachmentLevel.GROUP
    assert attr.related_group == "G1"


def test_attribute_observation_attachment_round_trips() -> None:
    dsd = DataStructure(
        id="DSD",
        maintainer="T",
        version="1.0",
        name={"en": "d"},
        dimensions=ItemList([Dimension(id="FREQ", concept=concept_ref("FREQ"))]),
        attributes=ItemList([Attribute(id="A_O", concept=concept_ref("A_O"), attachment=AttachmentLevel.OBSERVATION)]),
    )
    attr = _roundtrip(dsd).attributes["A_O"]
    assert attr.attachment is AttachmentLevel.OBSERVATION


def test_component_full_facet_round_trips() -> None:
    facet = Facet(
        type="Integer",
        min_length=1,
        max_length=5,
        min_value=0.0,
        max_value=100.0,
        decimals=2,
        pattern="[0-9]+",
        is_sequence=True,
        interval=1.0,
        start_value=1.0,
        end_value=9.0,
        is_multilingual=True,
    )
    dsd = DataStructure(
        id="DSD",
        maintainer="T",
        version="1.0",
        name={"en": "d"},
        dimensions=ItemList([Dimension(id="D", concept=concept_ref("D"))]),
        # An attribute's: a dimension's text format is never multilingual (#503).
        attributes=ItemList(
            [Attribute(id="A", concept=concept_ref("A"), representation=facet, attachment=AttachmentLevel.DATASET)]
        ),
    )
    rep = _roundtrip(dsd).attributes["A"].representation
    assert isinstance(rep, Facet)
    assert rep.min_value == 0.0
    assert rep.max_value == 100.0
    assert rep.decimals == 2
    assert rep.pattern == "[0-9]+"
    assert rep.is_sequence is True
    assert rep.start_value == 1.0
    assert rep.end_value == 9.0
    assert rep.is_multilingual is True


# ── Every representation-bearing owner keeps the whole facet ─────────────────


def test_a_full_facet_round_trips_from_all_three_owners():
    """`Concept`, `Dimension` and `MetadataAttribute` must agree.

    They did not. `metadata_attribute` had no `is_multilingual` column and its
    writer never read the field, so a `MetadataAttribute` carrying a
    multilingual facet came back with `is_multilingual=None` while the other
    two kept it — silent, and invisible to 4,882 passing tests, because every
    facet fixture in the suite happened to leave that one field unset.

    Written as a *comparison between the three owners* rather than as three
    separate assertions: the bug was drift between copies of one column group,
    and a per-owner test is exactly what fails to notice drift.
    """
    from sdmxlib.local import LocalRegistry
    from sdmxlib.model.concept import Concept, ConceptScheme
    from sdmxlib.model.datastructure import AttachmentLevel, Attribute, DataStructure, Dimension
    from sdmxlib.model.istring import InternationalString
    from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
    from sdmxlib.model.representation import DataType, Facet

    facet = Facet(
        type=DataType.String,
        min_length=1,
        max_length=50,
        min_value=0.0,
        max_value=99.5,
        decimals=2,
        pattern="[A-Z]+",
        is_sequence=True,
        interval=1.0,
        start_value=1.0,
        end_value=9.0,
        is_multilingual=True,
    )

    with LocalRegistry(":memory:") as reg:
        reg.add(
            MetadataStructure(
                id="MSD",
                maintainer="AG",
                version="1.0",
                name=InternationalString(en="MSD"),
                attributes=[MetadataAttribute(id="A", concept=concept_ref("A"), representation=facet)],
            )
        )
        reg.add(
            ConceptScheme(
                id="CS",
                maintainer="AG",
                version="1.0",
                name=InternationalString(en="CS"),
                concepts=[Concept(id="C", name=InternationalString(en="C"), core_representation=facet)],
            )
        )
        reg.add(
            DataStructure(
                id="DSD",
                maintainer="AG",
                version="1.0",
                name=InternationalString(en="DSD"),
                dimensions=[Dimension(id="D", concept=concept_ref("D"))],
                # An attribute's: a dimension's text format is never multilingual (#503).
                attributes=[
                    Attribute(
                        id="X", concept=concept_ref("X"), representation=facet, attachment=AttachmentLevel.DATASET
                    )
                ],
            )
        )

        # Locals first: pyrefly 1.2.0 does not solve `present(reg.get(...))` when the
        # inner call is itself generic (`T | None`) — see #425's repro.
        msd = reg.get(MetadataStructure.urn_for(agency="AG", id="MSD", version="1.0"))
        scheme = reg.get(ConceptScheme.urn_for(agency="AG", id="CS", version="1.0"))
        dsd = reg.get(DataStructure.urn_for(agency="AG", id="DSD", version="1.0"))
        got = {
            "MetadataAttribute": present(msd).attributes["A"].representation,
            "Concept": present(scheme).concepts["C"].core_representation,
            "Attribute": present(dsd).attributes["X"].representation,
        }

    # Every owner returns the facet it was given, and they agree with each other.
    for owner, rep in got.items():
        assert rep == facet, f"{owner} lost part of the facet: {rep}"
    import attrs

    shapes = {tuple(sorted(attrs.asdict(r).items())) for r in got.values()}
    assert len(shapes) == 1, "the three owners disagree, which is the drift this guards"
