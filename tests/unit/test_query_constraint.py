"""`q.data_constraint`, `q.cube_region`, `q.key_value` — what a flow contains.

This cluster is why #308 existed, and the answer turned out to be a **layout**
change rather than a new edge mechanism.

An attachment was stored only as `artefact_ref` rows discriminated by `role`,
which made `constraint.dataflow` a to-one hop through a junction with a
predicate on the intermediate — something `Edge` cannot express (no predicate)
and `Facet` cannot either (it refines a fan-out and yields to-many). Worse, the
to-one-ness was not derivable: `artefact_ref` is keyed
`(source_urn, target_urn, role)`, so several rows may share a role, and other
roles genuinely do (`code`, `concept_identity`, `target[N]`).

`provision_agreement` already showed the way — it writes its `dataflow_urn` and
`data_provider_urn` as *columns* on its extension table **and** as ref rows, so
queries join on columns while the generic inbound-reference index keeps
working. `data_constraint` did the same for its four attachment slots.
Cardinality went back to being derived, and the query language gained nothing
it has to maintain.

Then #422 found that SDMX attaches a constraint to *several* dataflows, data
structures or provision agreements, and a column holds one. Each kind became a
table of its own, keyed by position — still an ordinary foreign key, now
read from the other end, so `constraint.dataflows` is a fan-out and
`dataflow.constraints` crosses one table. The provider stays a column.

`test_each_kind_is_a_table_not_a_role_filtered_ref` is what stops that being
quietly undone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.constraint import ConstraintAttachment, CubeRegion, DataConstraint, KeyValue
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.organisation import Agency, DataProvider, DataProviderScheme
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.query.accessors import q
from tests.support.factories import concept_ref

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

#: Named rather than written as a literal at the call site: `== True` is what
#: this DSL means (it compiles to SQL, not to a Python truth test), but ruff
#: reads the literal form as the E712 mistake it usually is.
INCLUDED = True
EXCLUDED = False


def _flow(fid: str) -> Ref[object]:
    return Ref[object].unresolved(Dataflow.urn_for(agency="ESTAT", id=fid, version="1.0"))


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Two flows, an Actual and an Allowed constraint, and an excluded region.

    The two constraint types are what tells an availability query from a
    validity one, and the excluded region is what tells a query that reads
    `is_included` from one that ignores it and inverts its own answer.
    """
    with sl.LocalRegistry(":memory:") as registry:
        dsd = DataStructure(
            id="CPI",
            maintainer=Agency(id="ESTAT"),
            name={"en": "CPI"},
            dimensions=[
                Dimension(id="FREQ", concept=concept_ref("FREQ")),
                Dimension(id="REF_AREA", concept=concept_ref("REF_AREA")),
            ],
        )
        registry.add(dsd)
        for fid in ("DF_CPI", "DF_BARE"):
            registry.add(Dataflow(id=fid, maintainer=Agency(id="ESTAT"), name={"en": fid}, structure=dsd))
        registry.add(
            DataConstraint(
                id="DC_ACTUAL",
                maintainer=Agency(id="ESTAT"),
                name={"en": "What is there"},
                constraint_type="Actual",
                attachment=ConstraintAttachment(dataflows=(_flow("DF_CPI"),)),
                regions=(
                    CubeRegion(
                        include=True,
                        keys=(
                            KeyValue(dimension_id="FREQ", values=frozenset({"A", "Q"})),
                            KeyValue(dimension_id="REF_AREA", values=frozenset({"EU"})),
                        ),
                    ),
                    CubeRegion(include=False, keys=(KeyValue(dimension_id="REF_AREA", values=frozenset({"XX"})),)),
                ),
            )
        )
        registry.add(
            DataConstraint(
                id="DC_ALLOWED",
                maintainer=Agency(id="ESTAT"),
                name={"en": "What may be there"},
                constraint_type="Allowed",
                attachment=ConstraintAttachment(
                    data_structures=(
                        Ref[object].unresolved(DataStructure.urn_for(agency="ESTAT", id="CPI", version="1.0")),
                    )
                ),
                regions=(CubeRegion(include=True, keys=(KeyValue(dimension_id="FREQ", values=frozenset({"M"})),)),),
            )
        )
        yield registry


# ── the layout decision #308 turned on ────────────────────────────────────


def test_each_kind_is_a_table_not_a_role_filtered_ref(reg: sl.LocalRegistry) -> None:
    """The attachment is read from the kind's table, keyed by constraint and position.

    Stored only as `artefact_ref` rows, `constraint.dataflows` would be a hop
    through a junction filtered on `role` — no mechanism here expresses that,
    and its cardinality is not derivable. As a table of its own it is an
    ordinary foreign key, read from the other end.

    The ref rows are still written, because the generic inbound-reference index
    reads them. That duplication is deliberate and matches
    `put_provision_agreement`.
    """
    flows = reg.sql("SELECT constraint_urn, ordinal, dataflow_urn FROM sdmx.data_constraint_dataflow").collect()
    assert flows.rows() == [
        (
            "urn:sdmx:org.sdmx.infomodel.registry.DataConstraint=ESTAT:DC_ACTUAL(1.0)",
            0,
            "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=ESTAT:DF_CPI(1.0)",
        )
    ]
    assert reg.sql("SELECT count(*) AS n FROM sdmx.data_constraint_dsd").collect()["n"].to_list() == [1]
    roles = reg.sql("SELECT DISTINCT role FROM sdmx.artefact_ref WHERE role LIKE 'attachment_%' ORDER BY 1").collect()
    assert roles["role"].to_list() == ["attachment_dataflow", "attachment_dsd"]


def test_the_attachment_survives_a_round_trip(reg: sl.LocalRegistry) -> None:
    found = reg.query(q.data_constraint).filter(q.data_constraint.id == "DC_ACTUAL").collect()[0]
    assert [str(ref.urn) for ref in found.attachment.dataflows] == [
        "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=ESTAT:DF_CPI(1.0)"
    ]


# ── the roots ─────────────────────────────────────────────────────────────


def test_the_roots_discriminate(reg: sl.LocalRegistry) -> None:
    assert sorted(reg.query(q.data_constraint).ids()) == ["DC_ACTUAL", "DC_ALLOWED"]
    assert reg.query(q.cube_region).count() == 3
    assert sorted(reg.query(q.key_value).ids()) == ["A", "EU", "M", "Q", "XX"]


def test_a_constraint_collects_as_a_domain_object(reg: sl.LocalRegistry) -> None:
    assert type(reg.query(q.data_constraint).collect()[0]).__name__ == "DataConstraint"


def test_the_constraint_type_lives_on_the_extension(reg: sl.LocalRegistry) -> None:
    """The first spine-rooted accessor to expose a column of its *own*
    extension table. Declared with `col()` it would carry an empty path and
    bind against the artefact alias, which the binder rejects — `extcol`
    carries the identity split instead.
    """
    assert q.data_constraint.constraint_type.path == ("data_constraint",)
    frame = (
        reg.query(q.data_constraint)
        .order_by(q.data_constraint.id)
        .select(c=q.data_constraint.id, kind=q.data_constraint.constraint_type)
        .collect()
    )
    assert frame.to_dicts() == [
        {"c": "DC_ACTUAL", "kind": "Actual"},
        {"c": "DC_ALLOWED", "kind": "Allowed"},
    ]


# ── what a constraint is attached to ──────────────────────────────────────


def test_the_kinds_are_different_hops(reg: sl.LocalRegistry) -> None:
    """One kind is set. Collapsing them would answer this the same either
    way — it does not."""
    assert reg.query(q.data_constraint).filter(q.data_constraint.dataflows.id == "DF_CPI").ids() == ["DC_ACTUAL"]
    assert reg.query(q.data_constraint).filter(q.data_constraint.dsds.id == "CPI").ids() == ["DC_ALLOWED"]
    assert reg.query(q.data_constraint).filter(q.data_constraint.dsds.id == "DF_CPI").ids() == []


def test_an_unset_kind_is_empty_not_a_dropped_row(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.data_constraint)
        .order_by(q.data_constraint.id)
        .select(c=q.data_constraint.id, flows=q.data_constraint.dataflows.id.list())
        .collect()
    )
    assert frame.to_dicts() == [{"c": "DC_ACTUAL", "flows": ["DF_CPI"]}, {"c": "DC_ALLOWED", "flows": []}]


def test_a_constraint_on_several_flows_reaches_each(reg: sl.LocalRegistry) -> None:
    """Several of a kind is the case a column could not hold (#422)."""
    reg.add(
        DataConstraint(
            id="DC_BOTH",
            maintainer=Agency(id="ESTAT"),
            name={"en": "Both flows"},
            attachment=ConstraintAttachment(dataflows=(_flow("DF_CPI"), _flow("DF_BARE"))),
        )
    )
    both = reg.query(q.data_constraint).filter(q.data_constraint.id == "DC_BOTH")
    assert sorted(both.select(flows=q.data_constraint.dataflows.id.list()).collect()["flows"].to_list()[0]) == [
        "DF_BARE",
        "DF_CPI",
    ]
    reaching = reg.query(q.dataflow).filter(q.dataflow.constraints.id == "DC_BOTH")
    assert sorted(reaching.ids()) == ["DF_BARE", "DF_CPI"]
    (back,) = both.collect()
    assert [ref.urn.id for ref in back.attachment.dataflows] == ["DF_CPI", "DF_BARE"], "in order"


def test_a_flow_reached_from_its_constraint_reaches_its_dsd(reg: sl.LocalRegistry) -> None:
    """The fan-out is a whole dataflow, not a bare maintainable."""
    found = reg.query(q.data_constraint).filter(q.data_constraint.dataflows.dsd.id == "CPI")
    assert found.ids() == ["DC_ACTUAL"]


def test_a_provider_is_a_to_one_hop(reg: sl.LocalRegistry) -> None:
    reg.add(
        DataProviderScheme(
            id="DATA_PROVIDERS",
            maintainer="ESTAT",
            name={"en": "p"},
            providers=[DataProvider(id="P1", name={"en": "1"})],
        )
    )
    urn = "urn:sdmx:org.sdmx.infomodel.base.DataProvider=ESTAT:DATA_PROVIDERS(1.0).P1"
    reg.add(
        DataConstraint(
            id="DC_PROVIDER",
            maintainer=Agency(id="ESTAT"),
            name={"en": "One provider"},
            attachment=ConstraintAttachment(data_provider=Ref[object].unresolved(SdmxUrn.parse(urn))),
        )
    )
    assert reg.query(q.data_constraint).filter(q.data_constraint.data_provider.id == "P1").ids() == ["DC_PROVIDER"]
    assert reg.query(q.data_constraint).filter(q.data_constraint.data_provider.id == "P2").ids() == []


def test_a_flow_reaches_its_constraints(reg: sl.LocalRegistry) -> None:
    """The inverse, and the question a catalogue actually asks."""
    assert reg.query(q.dataflow).filter(q.dataflow.constraints.constraint_type == "Actual").ids() == ["DF_CPI"]
    assert reg.query(q.dataflow).filter(q.dataflow.constraints.constraint_type == "Allowed").ids() == []


def test_a_flow_with_no_constraints_survives_a_projection(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.dataflow)
        .order_by(q.dataflow.id)
        .select(flow=q.dataflow.id, kinds=q.dataflow.constraints.constraint_type.list())
        .collect()
    )
    rows = dict(zip(frame["flow"].to_list(), frame["kinds"].to_list(), strict=True))
    # `[]`, not None — an empty fan-out collects to empty, as polars does (#341).
    assert rows == {"DF_BARE": [], "DF_CPI": ["Actual"]}


# ── regions and values ────────────────────────────────────────────────────


def test_a_constraint_fans_out_to_its_regions(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.data_constraint)
        .order_by(q.data_constraint.id)
        .select(c=q.data_constraint.id, regions=q.data_constraint.cube_regions.index.list())
        .collect()
    )
    rows = dict(zip(frame["c"].to_list(), (sorted(v) for v in frame["regions"].to_list()), strict=True))
    assert rows == {"DC_ACTUAL": [0, 1], "DC_ALLOWED": [0]}


def test_inclusion_is_readable_and_discriminates(reg: sl.LocalRegistry) -> None:
    """The same region shape means "only these" or "not these" depending on
    this flag, so a query that ignores it inverts its own answer."""
    excluded = reg.query(q.key_value).filter(q.key_value.region.is_included == EXCLUDED)
    assert excluded.ids() == ["XX"]
    included = reg.query(q.key_value).filter(q.key_value.region.is_included == INCLUDED)
    assert sorted(included.ids()) == ["A", "EU", "M", "Q"]


def test_values_are_one_row_each(reg: sl.LocalRegistry) -> None:
    """Storage flattens `KeyValue(dimension_id, values[])` to one row per
    value, which is what makes this a plain predicate rather than a
    containment test."""
    assert sorted(reg.query(q.key_value).filter(q.key_value.component == "FREQ").ids()) == ["A", "M", "Q"]
    assert sorted(reg.query(q.key_value).filter(q.key_value.component == "REF_AREA").ids()) == ["EU", "XX"]


def test_a_value_reaches_its_constraint(reg: sl.LocalRegistry) -> None:
    """Two hops back up: value, region, constraint."""
    found = reg.query(q.key_value).filter(q.key_value.region.constraint.id == "DC_ALLOWED")
    assert found.ids() == ["M"]


def test_the_availability_question_end_to_end(reg: sl.LocalRegistry) -> None:
    """Four hops, and the reason the cluster is worth exposing: *which flows
    actually carry data for EU*."""
    found = reg.query(q.dataflow).filter(
        q.dataflow.constraints.constraint_type == "Actual",
        q.dataflow.constraints.cube_regions.key_values.value == "EU",
    )
    assert found.ids() == ["DF_CPI"]


def test_the_binder_groups_onto_one_region(reg: sl.LocalRegistry) -> None:
    """`DC_ACTUAL` has an included region with `EU` and an excluded region with
    `XX`, but no single region holding both."""
    same = q.data_constraint.cube_regions.any(
        q.cube_region.is_included == EXCLUDED,
        q.cube_region.key_values.value == "EU",
    )
    assert reg.query(q.data_constraint).filter(same).ids() == []
    both = q.data_constraint.cube_regions.any(
        q.cube_region.is_included == INCLUDED,
        q.cube_region.key_values.value == "EU",
    )
    assert reg.query(q.data_constraint).filter(both).ids() == ["DC_ACTUAL"]


def test_a_region_is_scoped_to_its_constraint(reg: sl.LocalRegistry) -> None:
    """`region_idx` restarts at 0 per constraint, so the edge to `cube_region`
    is composite — a single-column join would merge the two constraints'
    regions."""
    from sdmxlib.query._edges import EDGES

    edge = next(e for e in EDGES if e.name == "region" and e.source == "region_key_value")
    assert edge.columns == (("constraint_urn", "constraint_urn"), ("region_idx", "region_idx"))
    assert reg.query(q.key_value).filter(q.key_value.region.constraint.id == "DC_ACTUAL").count() == 4
