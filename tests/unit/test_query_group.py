"""`q.group` and `q.dsd.groups` — partial keys attributes attach to.

A `Group` names a subset of a DSD's key so that attributes can hang off it
rather than off every observation. It has no urn: it is identified by
`(dsd_urn, group_id)`, so every edge touching it is composite.

The interesting shape is `group.dimensions`. SDMX models membership as
`GroupDimension`, a class carrying nothing but a reference to a dimension, so
storage has a junction row between the group and the component. The accessor
declares the pair as **one compound path** — `("dimensions", "dimension")` —
and a caller writes `q.dsd.groups.dimensions.id` without ever naming the
junction. That is the same device as `q.dataflow.dsd` crossing the identity
split, applied to a link table instead.

`dimensions` deliberately reuses the name of the `q.dsd` facet. The word means
the same thing in both, and resolution is per relation, so neither shadows the
other — `test_query_edges.py::test_facets_do_not_collide_with_edge_names` was
narrowed from a global ban to a per-relation one to say so.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.datastructure import Attribute, DataStructure, Dimension, Group
from sdmxlib.model.organisation import Agency
from sdmxlib.query._edges import EDGES
from sdmxlib.query.accessors import q
from tests.support.factories import concept_ref

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Two DSDs, each with groups, and a group id repeated across them.

    `SIBLING` exists in both, so an edge that dropped the `dsd_urn` scope
    shows up as a group gaining another DSD's dimensions rather than as
    nothing at all. `INDIC` is in no group, which is what tells a real
    membership test from one that matches every dimension.
    """
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            DataStructure(
                id="CPI",
                maintainer=Agency(id="ESTAT"),
                name={"en": "CPI"},
                dimensions=[
                    Dimension(id="FREQ", concept=concept_ref("FREQ")),
                    Dimension(id="REF_AREA", concept=concept_ref("REF_AREA")),
                    Dimension(id="INDIC", concept=concept_ref("INDIC")),
                ],
                attributes=[
                    Attribute(attachment=sl.AttachmentLevel.DATASET, id="OBS_STATUS", concept=concept_ref("OBS_STATUS"))
                ],
                groups=[
                    Group(id="SIBLING", dimension_ids=["FREQ", "REF_AREA"]),
                    Group(id="GEO", dimension_ids=["REF_AREA"]),
                ],
            )
        )
        registry.add(
            DataStructure(
                id="TRADE",
                maintainer=Agency(id="OECD"),
                name={"en": "Trade"},
                dimensions=[Dimension(id="PARTNER", concept=concept_ref("PARTNER"))],
                groups=[Group(id="SIBLING", dimension_ids=["PARTNER"])],
            )
        )
        yield registry


# ── the relation ──────────────────────────────────────────────────────────


def test_groups_are_a_root(reg: sl.LocalRegistry) -> None:
    assert sorted(reg.query(q.group).ids()) == ["GEO", "SIBLING", "SIBLING"]


def test_a_group_id_is_not_unique_across_dsds(reg: sl.LocalRegistry) -> None:
    """Which is why the relation is keyed `(dsd_urn, group_id)` and the fixture
    repeats `SIBLING` — an `ids()` that looked unique would be hiding one."""
    assert reg.query(q.group).filter(q.group.id == "SIBLING").count() == 2
    assert type(q.group).__key__ == ("dsd_urn", "group_id")


def test_a_group_reaches_its_dsd(reg: sl.LocalRegistry) -> None:
    assert sorted(reg.query(q.group).filter(q.group.dsd.id == "CPI").ids()) == ["GEO", "SIBLING"]
    assert reg.query(q.group).filter(q.group.dsd.agency == "OECD").ids() == ["SIBLING"]


def test_a_dsd_fans_out_to_its_groups(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.dsd).order_by(q.dsd.id).select(dsd=q.dsd.id, groups=q.dsd.groups.id.list()).collect()
    rows = dict(zip(frame["dsd"].to_list(), (sorted(g) for g in frame["groups"].to_list()), strict=True))
    assert rows == {"CPI": ["GEO", "SIBLING"], "TRADE": ["SIBLING"]}


# ── the compound path through the junction ────────────────────────────────


def test_a_group_names_its_dimensions(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.group)
        .filter(q.group.dsd.id == "CPI")
        .order_by(q.group.id)
        .select(group=q.group.id, dims=q.group.dimensions.id.list())
        .collect()
    )
    rows = dict(zip(frame["group"].to_list(), (sorted(d) for d in frame["dims"].to_list()), strict=True))
    assert rows == {"GEO": ["REF_AREA"], "SIBLING": ["FREQ", "REF_AREA"]}


def test_the_junction_is_never_named(reg: sl.LocalRegistry) -> None:
    """One attribute, two hops. `GroupDimension` is a real SDMX class but holds
    only a reference, so exposing it as a step would make every call site say
    the same uninteresting word twice."""
    assert q.group.dimensions.position.path == ("dimensions", "dimension")
    assert type(q.group.dimensions).__table__ == "dsd_component"


def test_reading_only_the_id_does_not_reach_the_component(reg: sl.LocalRegistry) -> None:
    """Trimming pays off twice over on a junction.

    `group_dimension.dimension_id` *is* the component's id, so
    `group.dimensions.id` sheds the second hop entirely — one correlated
    `EXISTS` against the link table rather than a join to `dsd_component` as
    well. Any other column keeps the hop, because only the key is recoverable
    from the junction row.
    """
    assert q.group.dimensions.id.path == ("dimensions",)
    assert q.group.dimensions.id.table == "group_dimension"
    assert q.group.dimensions.id.column == "dimension_id"
    assert "dsd_component" not in reg.query(q.group).filter(q.group.dimensions.id == "FREQ").to_sql().sql
    assert "dsd_component" in reg.query(q.group).filter(q.group.dimensions.position == 0).to_sql().sql


def test_membership_discriminates(reg: sl.LocalRegistry) -> None:
    """`INDIC` is a dimension of `CPI` and in no group. A join that matched
    every component of the DSD would pass the previous test and fail here."""
    assert sorted(reg.query(q.group).filter(q.group.dimensions.id == "REF_AREA").ids()) == ["GEO", "SIBLING"]
    assert reg.query(q.group).filter(q.group.dimensions.id == "INDIC").ids() == []


def test_membership_does_not_cross_dsds(reg: sl.LocalRegistry) -> None:
    """Both DSDs have a `SIBLING`. Without `dsd_urn` on both hops, CPI's would
    pick up `PARTNER`."""
    frame = (
        reg.query(q.group)
        .filter(q.group.id == "SIBLING")
        .order_by(q.group.dsd.id)
        .select(dsd=q.group.dsd.id, dims=q.group.dimensions.id.list())
        .collect()
    )
    rows = dict(zip(frame["dsd"].to_list(), (sorted(d) for d in frame["dims"].to_list()), strict=True))
    assert rows == {"CPI": ["FREQ", "REF_AREA"], "TRADE": ["PARTNER"]}


def test_a_dsd_is_found_through_its_groups_dimensions(reg: sl.LocalRegistry) -> None:
    """Three hops from the DSD: groups, the junction, the component."""
    assert reg.query(q.dsd).filter(q.dsd.groups.dimensions.id == "FREQ").ids() == ["CPI"]
    assert reg.query(q.dsd).filter(q.dsd.groups.dimensions.id == "PARTNER").ids() == ["TRADE"]


def test_the_binder_groups_onto_one_group(reg: sl.LocalRegistry) -> None:
    """`CPI` has a group with `FREQ` and a group with `REF_AREA` alone, but no
    single group that is `GEO` *and* contains `FREQ`."""
    same = q.dsd.groups.any(q.group.id == "GEO", q.group.dimensions.id == "FREQ")
    assert reg.query(q.dsd).filter(same).ids() == []
    both = q.dsd.groups.any(q.group.id == "SIBLING", q.group.dimensions.id == "FREQ")
    assert reg.query(q.dsd).filter(both).ids() == ["CPI"]


# ── the declaration ───────────────────────────────────────────────────────


def test_every_edge_into_a_group_is_composite(reg: sl.LocalRegistry) -> None:
    """A group has no urn, so `(dsd_urn, group_id)` is the only thing that
    identifies one. A single-column edge would join across DSDs."""
    group = next(e for e in EDGES if e.name == "group" and e.source == "group_dimension")
    assert group.columns == (("dsd_urn", "dsd_urn"), ("group_id", "group_id"))
    dimension = next(e for e in EDGES if e.name == "dimension" and e.source == "group_dimension")
    assert dimension.columns == (("dsd_urn", "dsd_urn"), ("dimension_id", "component_id"))


def test_the_facet_and_the_inverse_share_a_name_on_purpose(reg: sl.LocalRegistry) -> None:
    """`dsd.dimensions` is a facet over `dsd_component`; `group.dimensions` is
    an inverse over `group_dimension`. Resolution is per relation, so both
    stand — and they mean the same word."""
    from sdmxlib.query._joins import resolve_path

    assert resolve_path("artefact", ("dimensions",))[-1].table == "dsd_component"
    assert resolve_path("dsd_group", ("dimensions",))[-1].table == "group_dimension"
