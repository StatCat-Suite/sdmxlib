"""The dataflow root, and the identity split it has to cross.

`sdmx.dataflow` holds `(urn, dsd_urn)` and nothing else — every column a caller
would name lives on `sdmx.artefact`. So a dataflow accessor has its columns on
one table and its one outgoing edge on another, and which of the two it roots
at decides whether either works.

It roots at `artefact`. Rooting at the extension is the reading that looks
right and is not: `q.dataflow.agency == "ESTAT"` then binds `agency_id` against
a two-column table and fails outright, and `reg.query(q.dataflow).ids()` looks
for `resource_id` there too. That shipped, because nothing exercised this root
— the whole file exists so that stays true only once.

The cost of rooting on the spine is that `dsd` needs the compound hop
`("dataflow", "dsd")`, and `dsd.dataflows` needs `("flows", "spine")` to get back.
Both are physical layout and neither is spelled by a caller.

`dsd` targets `_Dsd`, so `q.dataflow.dsd.dimensions` works — the traversal
every SDMX client starts from. That is paid for by definition order rather
than machinery: `_Dsd` is declared before `_Dataflow`, and `_ManyDataflow` is
based on `_Maintainable` so `_Dsd.flows` does not point back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.organisation import Agency
from sdmxlib.query.accessors import q
from tests.support.factories import concept_ref

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Two DSDs, and one of them backing two flows.

    A DSD with a *single* flow cannot tell a to-many `flows` from a to-one, and
    a second DSD with none is what makes an INNER-vs-LEFT mistake visible.
    """
    with sl.LocalRegistry(":memory:") as registry:
        freq = Codelist(
            id="CL_FREQ",
            maintainer=Agency(id="ESTAT"),
            name={"en": "Frequency"},
            codes=[Code(id="A", name={"en": "Annual"})],
        )
        registry.add(freq)
        cpi = DataStructure(
            id="CPI",
            maintainer=Agency(id="ESTAT"),
            name={"en": "Consumer prices"},
            dimensions=[Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=freq)],
        )
        trade = DataStructure(id="TRADE", maintainer=Agency(id="OECD"), name={"en": "Trade"})
        registry.add(cpi)
        registry.add(trade)
        registry.add(
            Dataflow(
                id="CPI_M",
                maintainer=Agency(id="ESTAT"),
                name={"en": "CPI monthly"},
                description={"en": "Monthly consumer prices"},
                structure=cpi,
            )
        )
        registry.add(
            Dataflow(
                id="CPI_A",
                maintainer=Agency(id="OECD"),
                name={"en": "CPI annual"},
                structure=cpi,
            )
        )
        yield registry


# ── the root is the spine, so hoisted columns bind ────────────────────────


def test_the_root_sees_only_dataflows(reg: sl.LocalRegistry) -> None:
    """Rooted on `artefact`, the discriminator is doing the work the extension
    table used to do implicitly — and there are five artefacts here."""
    assert sorted(reg.query(q.dataflow).ids()) == ["CPI_A", "CPI_M"]


def test_a_hoisted_column_filters(reg: sl.LocalRegistry) -> None:
    """`agency_id` is not on `sdmx.dataflow`; rooted there this is a binder
    error, not a wrong answer."""
    assert reg.query(q.dataflow).filter(q.dataflow.agency == "ESTAT").ids() == ["CPI_M"]


def test_every_hoisted_column_binds(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.dataflow)
        .filter(q.dataflow.id == "CPI_M")
        .select(
            id=q.dataflow.id,
            agency=q.dataflow.agency,
            version=q.dataflow.version,
            urn=q.dataflow.urn,
        )
        .collect()
    )
    row = frame.to_dicts()[0]
    assert row["id"] == "CPI_M"
    assert row["agency"] == "ESTAT"
    assert row["urn"].startswith("urn:")


def test_a_side_table_label_reads(reg: sl.LocalRegistry) -> None:
    """The correlated `localized_text` subquery has to correlate against the
    spine alias, which is only the root alias if the root *is* the spine."""
    frame = reg.query(q.dataflow).select(id=q.dataflow.id, label=q.dataflow.name["en"]).collect()
    assert dict(zip(frame["id"].to_list(), frame["label"].to_list(), strict=True)) == {
        "CPI_M": "CPI monthly",
        "CPI_A": "CPI annual",
    }


def test_the_terminals_resolve_dataflows(reg: sl.LocalRegistry) -> None:
    found = reg.query(q.dataflow).filter(q.dataflow.id == "CPI_M").one()
    assert isinstance(found, Dataflow)
    assert found.id == "CPI_M"


# ── out to the extension: `dataflow.dsd` ──────────────────────────────────


def test_the_dsd_edge_crosses_the_split(reg: sl.LocalRegistry) -> None:
    """`dsd_urn` lives on the extension, so this is `("dataflow", "dsd")` — two
    declared edges behind one attribute."""
    assert sorted(reg.query(q.dataflow).filter(q.dataflow.dsd.id == "CPI").ids()) == ["CPI_A", "CPI_M"]


def test_the_dsd_edge_reaches_the_dsds_own_columns(reg: sl.LocalRegistry) -> None:
    """`CPI_A` is maintained by OECD while its DSD is ESTAT's, so a query that
    confused the two would answer this one wrong rather than empty."""
    assert sorted(reg.query(q.dataflow).filter(q.dataflow.dsd.agency == "ESTAT").ids()) == ["CPI_A", "CPI_M"]
    assert reg.query(q.dataflow).filter(q.dataflow.dsd.agency == "OECD").ids() == []


def test_a_path_continues_past_the_dsd(reg: sl.LocalRegistry) -> None:
    """`flow -> dsd -> dimensions -> codelist`, the commonest traversal in SDMX.

    Five hops from the spine, the first of which is the identity split. `dsd`
    targets `_Dsd` rather than the generic maintainable, which is only possible
    because `_ManyDataflow` is based on `_Maintainable` — see its docstring.
    """
    found = reg.query(q.dataflow).filter(q.dataflow.dsd.dimensions.codelist.id == "CL_FREQ")
    assert sorted(found.ids()) == ["CPI_A", "CPI_M"]
    assert reg.query(q.dataflow).filter(q.dataflow.dsd.dimensions.codelist.id == "CL_NOPE").ids() == []


def test_a_fan_out_past_the_dsd_still_means_some_child(reg: sl.LocalRegistry) -> None:
    """The to-many hop is three deep and still opens a subquery rather than a
    join, so `TRADE`'s flows are not multiplied by `CPI`'s dimensions."""
    frame = reg.query(q.dataflow).select(flow=q.dataflow.id, dims=q.dataflow.dsd.dimensions.id.list()).collect()
    assert frame.height == 2
    assert all(d == ["FREQ"] for d in frame["dims"].to_list())


def test_the_binder_works_past_the_dsd(reg: sl.LocalRegistry) -> None:
    """`.any()` over a fan-out reached through the split — the child scope has
    to open at `dsd_component` while the correlation anchors three hops back."""
    same = q.dataflow.dsd.dimensions.any(q.component.id == "FREQ", q.component.codelist.id == "CL_FREQ")
    assert sorted(reg.query(q.dataflow).filter(same).ids()) == ["CPI_A", "CPI_M"]
    missed = q.dataflow.dsd.dimensions.any(q.component.id == "FREQ", q.component.codelist.id == "CL_NOPE")
    assert reg.query(q.dataflow).filter(missed).ids() == []


def test_a_component_reaches_back_to_its_dsd(reg: sl.LocalRegistry) -> None:
    """The cycle that used to be the one ordering could not break (#352).

    This test previously asserted the opposite, and its reasoning was sound
    for the mechanism it was written against: `_Dsd.components` needs
    `_ManyComponent`, which needs `_Component`, which would need `_Dsd` — a
    mutual recursion no amount of reordering resolves, and unlike `flows`
    there was no base class to weaken.

    Naming the target instead of passing the class dissolves it. `_Component`
    can say `"_Dsd"` before `_Dsd` exists, because nothing looks the name up
    until someone reads the edge. So the limit was the mechanism's, not the
    domain's — which is exactly what the old docstring said it wanted pinned
    so as not to be mistaken for.

    Traversal terminates because a path only grows as far as a caller dots:
    there is no eager expansion to run away.
    """
    assert type(q.component.dsd.dimensions).__name__ == "_ManyComponent"
    assert reg.query(q.component).filter(q.component.dsd.id == "CPI").ids() == ["FREQ"]

    # Two laps of the cycle still resolve, and still compile to finite SQL.
    assert type(q.component.dsd.components.dsd.components).__name__ == "_ManyComponent"


def test_the_dsd_projects(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.dataflow).select(flow=q.dataflow.id, dsd=q.dataflow.dsd.id).collect()
    assert dict(zip(frame["flow"].to_list(), frame["dsd"].to_list(), strict=True)) == {
        "CPI_M": "CPI",
        "CPI_A": "CPI",
    }


# ── back from the extension: `dsd.dataflows` ──────────────────────────────────


def test_flows_is_a_fan_out(reg: sl.LocalRegistry) -> None:
    """A DSD may back many dataflows — the cardinality that caught seven design
    examples out by looking to-one."""
    frame = reg.query(q.dsd).select(dsd=q.dsd.id, flows=q.dsd.dataflows.id.list()).collect()
    flows = dict(zip(frame["dsd"].to_list(), frame["flows"].to_list(), strict=True))
    assert sorted(flows["CPI"]) == ["CPI_A", "CPI_M"]
    assert flows["TRADE"] in ([], None)


def test_flows_reaches_hoisted_columns(reg: sl.LocalRegistry) -> None:
    """The second hop of `("flows", "spine")`. Without it the path stops on the
    extension row, where `agency_id` does not exist."""
    assert reg.query(q.dsd).filter(q.dsd.dataflows.agency == "OECD").ids() == ["CPI"]


def test_flows_does_not_multiply_the_parent(reg: sl.LocalRegistry) -> None:
    """Two flows on one DSD, still one row — a correlated subquery, not a
    join."""
    assert reg.query(q.dsd).filter(q.dsd.dataflows.agency == "ESTAT").ids() == ["CPI"]
    assert reg.query(q.dsd).select(dsd=q.dsd.id, n=q.dsd.dataflows.count()).collect().height == 2


def test_the_binder_groups_onto_one_flow(reg: sl.LocalRegistry) -> None:
    """No single flow of `CPI` is both ESTAT's and named "CPI annual"."""
    same = q.dsd.dataflows.any(q.dataflow.agency == "ESTAT", q.dataflow.id == "CPI_A")
    assert reg.query(q.dsd).filter(same).ids() == []
    both = q.dsd.dataflows.any(q.dataflow.agency == "OECD", q.dataflow.id == "CPI_A")
    assert reg.query(q.dsd).filter(both).ids() == ["CPI"]


def test_a_dsd_backing_nothing_is_kept_by_a_projection(reg: sl.LocalRegistry) -> None:
    """`TRADE` has no flow. The outward hop of the split must LEFT join, or the
    row disappears from its own projection."""
    frame = reg.query(q.dsd).select(dsd=q.dsd.id, n=q.dsd.dataflows.count()).collect()
    assert dict(zip(frame["dsd"].to_list(), frame["n"].to_list(), strict=True)) == {
        "CPI": 2,
        "TRADE": 0,
    }
