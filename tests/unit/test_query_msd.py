"""`q.msd`, `q.metadataflow`, `q.metadata_attribute` — the declared half.

Reference metadata has the same structure/instance split as data: an **MSD**
declares a tree of **MetadataAttribute** slots, and a **MetadataSet** reports
**ReportedAttribute** values against them. `test_query_metadata.py` covers the
reported half; this file covers the declaration, and the last test here is the
one that matters — both halves in one query, told apart by their names.

`metadata_attribute` is the fourth tree relation in this schema after `code`,
`category` and `hierarchy_node`, and its bake (`parent_attribute_id`, `depth`,
`ancestor_ids`) was already written at persist time. So ancestry costs nothing
new: the edges were declarable the day the composite mechanism landed, and only
the accessor was missing.

Two naming facts are load-bearing enough to test. The fan-out is
`metadata_attributes`, not `attributes`, because `artefact.attributes` is
already the DSD facet. And the relation carries no labels of its own — SDMX
takes an attribute's wording from its concept, so `name` is reached through the
hop rather than declared here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.organisation import Agency, MetadataProvider
from sdmxlib.model.ref import Ref
from sdmxlib.query.accessors import q
from tests.support.factories import concept_ref

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """One MSD with a two-level tree, a second MSD, and a flow over the first.

    Two MSDs because attribute ids are unique *within* one — both declare
    `CONTACT`, so an edge that dropped the `msd_urn` scope shows up as extra
    rows rather than as nothing at all.
    """
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            ConceptScheme(
                id="CS",
                maintainer=Agency(id="ESTAT"),
                name={"en": "Metadata concepts"},
                concepts=[Concept(id="CONTACT", name={"en": "Contact details"})],
            )
        )
        registry.add(
            MetadataStructure(
                id="MSD_Q",
                maintainer=Agency(id="ESTAT"),
                name={"en": "Quality"},
                attributes=[
                    MetadataAttribute(
                        id="CONTACT",
                        concept=Ref[Concept].unresolved(
                            Concept.urn_for(agency="ESTAT", scheme="CS", id="CONTACT", version="1.0")
                        ),
                        children=[
                            MetadataAttribute(id="EMAIL", concept=concept_ref("EMAIL"), min_occurs=1),
                            MetadataAttribute(id="PHONE", concept=concept_ref("PHONE"), max_occurs=3),
                        ],
                    ),
                    MetadataAttribute(id="QUALITY", concept=concept_ref("QUALITY"), max_occurs=None),
                ],
            )
        )
        registry.add(
            MetadataStructure(
                id="MSD_OTHER",
                maintainer=Agency(id="OECD"),
                name={"en": "Other"},
                attributes=[MetadataAttribute(id="CONTACT", concept=concept_ref("CONTACT"))],
            )
        )
        registry.add(
            Metadataflow(
                id="MF_Q",
                maintainer=Agency(id="ESTAT"),
                name={"en": "Quality reports"},
                structure=MetadataStructure(id="MSD_Q", maintainer=Agency(id="ESTAT"), name={"en": "Quality"}),
            )
        )
        yield registry


# ── the three roots ───────────────────────────────────────────────────────


def test_the_roots_discriminate(reg: sl.LocalRegistry) -> None:
    """All three sit on `sdmx.artefact` or beside it, so a missing constraint
    returns the whole catalogue rather than an error."""
    assert sorted(reg.query(q.msd).ids()) == ["MSD_OTHER", "MSD_Q"]
    assert reg.query(q.metadataflow).ids() == ["MF_Q"]
    assert sorted(reg.query(q.metadata_attribute).ids()) == ["CONTACT", "CONTACT", "EMAIL", "PHONE", "QUALITY"]


def test_an_msd_collects_as_a_domain_object(reg: sl.LocalRegistry) -> None:
    """Rooted on `artefact`, so the artefact resolver materialises it — no new
    resolver needed for either maintainable."""
    assert type(reg.query(q.msd).filter(q.msd.id == "MSD_Q").collect()[0]).__name__ == "MetadataStructure"
    assert type(reg.query(q.metadataflow).collect()[0]).__name__ == "Metadataflow"


# ── the slot tree ─────────────────────────────────────────────────────────


def test_slots_reach_their_msd(reg: sl.LocalRegistry) -> None:
    found = reg.query(q.metadata_attribute).filter(q.metadata_attribute.msd.id == "MSD_Q")
    assert sorted(found.ids()) == ["CONTACT", "EMAIL", "PHONE", "QUALITY"]


def test_an_msd_fans_out_to_its_slots(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.msd).order_by(q.msd.id).select(msd=q.msd.id, slots=q.msd.metadata_attributes.id.list()).collect()
    )
    counts = dict(zip(frame["msd"].to_list(), (sorted(s) for s in frame["slots"].to_list()), strict=True))
    assert counts == {"MSD_OTHER": ["CONTACT"], "MSD_Q": ["CONTACT", "EMAIL", "PHONE", "QUALITY"]}


def test_the_tree_is_scoped_by_its_msd(reg: sl.LocalRegistry) -> None:
    """Both MSDs declare `CONTACT`. A single-column self-edge would make
    `MSD_OTHER`'s `CONTACT` a parent of `MSD_Q`'s `EMAIL`."""
    children = reg.query(q.metadata_attribute).filter(q.metadata_attribute.parent.id == "CONTACT")
    assert sorted(children.ids()) == ["EMAIL", "PHONE"]
    scoped = children.filter(q.metadata_attribute.msd.id == "MSD_OTHER")
    assert scoped.ids() == []


def test_ancestry_is_transitive(reg: sl.LocalRegistry) -> None:
    under = reg.query(q.metadata_attribute).filter(q.metadata_attribute.ancestors.id == "CONTACT")
    assert sorted(under.ids()) == ["EMAIL", "PHONE"]


def test_the_roots_of_the_tree(reg: sl.LocalRegistry) -> None:
    """`parent.nested_id.is_null()` trims to `parent_path IS NULL` — no join."""
    found = reg.query(q.metadata_attribute).filter(
        q.metadata_attribute.msd.id == "MSD_Q", q.metadata_attribute.parent.nested_id.is_null()
    )
    assert sorted(found.ids()) == ["CONTACT", "QUALITY"]
    assert "JOIN sdmx.metadata_attribute" not in found.to_sql().sql


def test_depth_is_baked(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.metadata_attribute)
        .filter(q.metadata_attribute.msd.id == "MSD_Q")
        .order_by(q.metadata_attribute.id)
        .select(slot=q.metadata_attribute.id, depth=q.metadata_attribute.depth)
        .collect()
    )
    assert frame.to_dicts() == [
        {"slot": "CONTACT", "depth": 0},
        {"slot": "EMAIL", "depth": 1},
        {"slot": "PHONE", "depth": 1},
        {"slot": "QUALITY", "depth": 0},
    ]


# ── what an MSD declares about a slot ─────────────────────────────────────


def test_occurrence_bounds_are_queryable(reg: sl.LocalRegistry) -> None:
    """ "Which slots must be filled in" is a real question about an MSD, and it
    is a plain predicate rather than a domain verb."""
    mandatory = reg.query(q.metadata_attribute).filter(q.metadata_attribute.min_occurs >= 1)
    assert mandatory.ids() == ["EMAIL"]


def test_unbounded_repetition_is_a_null(reg: sl.LocalRegistry) -> None:
    """`max_occurs` is NULL for unbounded, so "repeatable without limit" is
    `is_null()` — worth pinning, because a caller would reasonably guess -1.

    The model's *default* is 1, not NULL, so an unbounded slot is one that said
    so. `QUALITY` in the fixture is the only one.
    """
    unbounded = reg.query(q.metadata_attribute).filter(
        q.metadata_attribute.msd.id == "MSD_Q", q.metadata_attribute.max_occurs.is_null()
    )
    assert unbounded.ids() == ["QUALITY"]
    assert reg.query(q.metadata_attribute).filter(q.metadata_attribute.max_occurs == 3).ids() == ["PHONE"]


def test_a_slot_takes_its_wording_from_its_concept(reg: sl.LocalRegistry) -> None:
    """The relation has no `name` column of its own — SDMX puts an attribute's
    wording on the concept, so the label is one hop away."""
    assert not hasattr(q.metadata_attribute, "name")
    found = reg.query(q.metadata_attribute).filter(q.metadata_attribute.concept.name["en"] == "Contact details")
    assert found.ids() == ["CONTACT"]


def test_a_slot_without_a_concept_survives_a_projection(reg: sl.LocalRegistry) -> None:
    """`concept_urn` is nullable and most slots here have none. An inner join
    would drop them, which is what `optional=True` on the edge prevents."""
    frame = (
        reg.query(q.metadata_attribute)
        .filter(q.metadata_attribute.msd.id == "MSD_Q")
        .select(slot=q.metadata_attribute.id, concept=q.metadata_attribute.concept.id)
        .collect()
    )
    assert len(frame) == 4
    assert None in frame["concept"].to_list()


# ── the flow, and the pair ────────────────────────────────────────────────


def test_a_metadataflow_reaches_its_msd_across_the_identity_split(reg: sl.LocalRegistry) -> None:
    """`msd_urn` lives on the `metadataflow` extension, so the hop is the
    compound `("metadataflow", "msd")` — the metadata-side mirror of
    `q.dataflow.dsd`."""
    assert reg.query(q.metadataflow).select(f=q.metadataflow.id, msd=q.metadataflow.msd.id).collect().to_dicts() == [
        {"f": "MF_Q", "msd": "MSD_Q"}
    ]
    assert q.metadataflow.msd.id.path == ("metadataflow", "msd")


def test_a_flow_reaches_the_slots_it_may_report(reg: sl.LocalRegistry) -> None:
    """Three hops: flow → msd → declared slots. The catalogue question this
    relation exists for."""
    found = reg.query(q.metadataflow).filter(q.metadataflow.msd.metadata_attributes.id == "QUALITY")
    assert found.ids() == ["MF_Q"]
    assert reg.query(q.metadataflow).filter(q.metadataflow.msd.metadata_attributes.id == "NOPE").ids() == []


def test_the_fan_out_is_not_called_attributes(reg: sl.LocalRegistry) -> None:
    """`artefact.attributes` is the DSD facet. One accessor cannot carry two
    meanings for one name, and the collision would be silent — a metadata query
    would quietly compile against `dsd_component`."""
    from sdmxlib.query._joins import resolve_path

    assert type(q.msd.metadata_attributes).__table__ == "metadata_attribute"
    # The collision is in the *graph*, not on this accessor: both names hang
    # off `artefact`, and the facet got there first.
    assert resolve_path("artefact", ("attributes",))[-1].table == "dsd_component"
    assert resolve_path("artefact", ("metadata_attributes",))[-1].table == "metadata_attribute"


def test_declared_and_reported_are_distinguishable_in_one_query(reg: sl.LocalRegistry) -> None:
    """The reason the spec's names were taken over `metadata_attribute` /
    `metadata_value`.

    Both relations are recursive and both are about "attributes", so a reader
    holding the two side by side needs the names to say which is declared and
    which is reported. They also carry different row kinds, so a predicate
    built for one cannot compile against the other.
    """
    declared = q.metadata_attribute.ancestors.id == "CONTACT"
    reported = q.reported_attribute.attribute_path.str.starts_with("CONTACT")
    assert type(q.metadata_attribute).__table__ == "metadata_attribute"
    assert type(q.reported_attribute).__table__ == "metadata_value"
    assert sorted(reg.query(q.metadata_attribute).filter(declared).ids()) == ["EMAIL", "PHONE"]
    assert reg.query(q.reported_attribute).filter(reported).ids() == []


# ── the provision agreement, and its missing half ─────────────────────────


def test_a_metadata_provision_agreement_reaches_its_flow(reg: sl.LocalRegistry) -> None:
    """Three hops end to end: agreement, metadataflow, MSD."""
    reg.add(
        MetadataProvisionAgreement(
            id="MPA",
            maintainer=Agency(id="ESTAT"),
            name={"en": "Quality provision"},
            metadataflow=Ref[object].unresolved(Metadataflow.urn_for(agency="ESTAT", id="MF_Q", version="1.0")),
            # A *provider* ref, not the flow's — the fixture reused the flow URN,
            # which nothing caught while `tests/` was unchecked (#347).
            metadata_provider=Ref[MetadataProvider].unresolved(
                MetadataProvider.urn_for(agency="ESTAT", id="MP_Q", version="1.0")
            ),
        )
    )
    found = reg.query(q.metadata_provision_agreement)
    assert found.ids() == ["MPA"]
    assert found.select(f=q.metadata_provision_agreement.metadataflow.id).collect()["f"].to_list() == ["MF_Q"]
    assert reg.query(q.metadata_provision_agreement).filter(
        q.metadata_provision_agreement.metadataflow.msd.id == "MSD_Q"
    ).ids() == ["MPA"]


def test_the_metadata_provider_is_reachable(reg: sl.LocalRegistry) -> None:
    """The relation the MPA's foreign key names now exists — #315.

    This test used to assert the *gap*: SDMX 3.0 has `MetadataProvider` and
    `MetadataProviderScheme`, this schema persisted neither, and
    `metadata_provider_urn` was `TEXT NOT NULL` holding a urn nothing could
    resolve. Its data twin was reachable, so the asymmetry read as an accessor
    someone had forgotten rather than a relation that was missing.

    Both halves are asserted — the accessor *and* the table — because the hop
    existing proves nothing on its own: an `Edge` declared against the spine
    compiles fine and matches zero rows, which is how the `data_provider` edge
    was wrong for as long as no accessor reached it.
    """
    assert hasattr(q.metadata_provision_agreement, "metadata_provider")
    assert hasattr(q.provision_agreement, "data_provider")
    with sl.LocalRegistry(":memory:") as empty:
        tables = {
            row[0]
            for row in empty.sql("SELECT table_name FROM information_schema.tables WHERE table_schema = 'sdmx'")
            .collect()
            .rows()
        }
    assert "data_provider" in tables
    assert "metadata_provider" in tables
