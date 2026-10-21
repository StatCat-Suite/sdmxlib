"""`q.hierarchy` and `q.hcode` — the tree relation, as an ordinary root.

A hierarchy node is *not* a code, and the pair is the reason `RowKind` exists
at all: `sdmx.code` is keyed by `(codelist_urn, code_id)` with a `parent_id`,
while `sdmx.hierarchy_node` is keyed by `(hierarchy_urn, node_id)` with a
`parent_node_id` and an optional reference to a code in some codelist. Same
tree shape, two relations, and a predicate built for one must not compile
against the other.

What is **not** here is recursion. `narrower_transitive` / `broader_transitive`
stay named domain verbs on the artefact rather than becoming path syntax — the
corollary that keeps every path in this language finite and the compiler free
of recursive CTEs. `q.hcode.parent.id` reaches one level, and that is all a path
is ever asked to do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

import sdmxlib as sl
from sdmxlib.model.annotations import Annotation
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, HierarchyAssociation, Level
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import SdmxConformanceError
from sdmxlib.query._edges import EDGES
from sdmxlib.query.accessors import q
from tests.support.factories import code_urn, concept_ref
from tests.support.narrow import present

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

_COUNTRY = Level(id="COUNTRY", name={"en": "Country"})
_PROVINCE = Level(id="PROVINCE", name={"en": "Province"})


def _code(item_id: str) -> Ref[Code]:
    return Ref[Code].unresolved(
        SdmxUrn.make(
            Code,
            package="codelist",
            artefact_class="Code",
            agency="STATCAN",
            id="CL_GEO",
            version="1.0",
            item_id=item_id,
        )
    )


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """One codelist and two hierarchies over it.

    Two hierarchies on purpose: a query that dropped the `hierarchy_urn`
    correlation would still answer every single-hierarchy question correctly.
    `ONT` appears in both, under different parents, so node identity is not
    node id.
    """
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            Codelist(
                id="CL_GEO",
                maintainer=Agency(id="STATCAN"),
                name={"en": "Geography"},
                codes=[
                    Code(id="CA", name={"en": "Canada"}),
                    Code(id="ON", name={"en": "Ontario"}),
                    Code(id="QC", name={"en": "Quebec"}),
                ],
            )
        )
        registry.add(
            Hierarchy(
                id="GEO",
                maintainer=Agency(id="STATCAN"),
                name={"en": "Geography"},
                has_formal_levels=True,
                levels=[_COUNTRY, _PROVINCE],
                tree=[
                    HierarchicalCode(
                        id="CANADA",
                        level=_COUNTRY,
                        code=_code("CA"),
                        annotations=[Annotation(id="a1", type="SOURCE", title="census")],
                        children=[
                            HierarchicalCode(id="ONT", level=_PROVINCE, code=_code("ON")),
                            HierarchicalCode(id="QUE", level=_PROVINCE, code=_code("QC")),
                        ],
                    )
                ],
            )
        )
        registry.add(
            Hierarchy(
                id="ALT",
                maintainer=Agency(id="STATCAN"),
                name={"en": "Alternate"},
                tree=[HierarchicalCode(id="ONT", code=_code("ON"))],
            )
        )
        yield registry


# ── the node relation ─────────────────────────────────────────────────────


def test_nodes_are_a_root(reg: sl.LocalRegistry) -> None:
    assert sorted(reg.query(q.hcode).ids()) == ["CANADA", "ONT", "ONT", "QUE"]


def test_a_node_id_is_not_unique_across_hierarchies(reg: sl.LocalRegistry) -> None:
    """Which is why the relation is keyed by `node_urn` and the fixture has two
    hierarchies — an `ids()` that looked unique would be hiding the second."""
    assert reg.query(q.hcode).filter(q.hcode.id == "ONT").count() == 2
    assert len(set(reg.query(q.hcode).urns())) == 4


def test_filtering_by_level(reg: sl.LocalRegistry) -> None:
    assert sorted(reg.query(q.hcode).filter(q.hcode.level.id == "PROVINCE").ids()) == ["ONT", "QUE"]


# ── the level as a relation, not a string ─────────────────────────────────


def test_the_level_id_and_the_level_are_one_query(reg: sl.LocalRegistry) -> None:
    """`level_id` was a bare string column until the edge was declared, which
    made it the last of the one-off traversals: a level could be *named* but
    its label and index were unreachable. Declaring the hop does not create a
    second way to ask the same question — trimming collapses them."""
    by_column = reg.query(q.hcode).filter(q.hcode.level.id == "PROVINCE")
    by_hop = reg.query(q.hcode).filter(q.hcode.level.id == "PROVINCE")
    assert by_column.to_sql().sql == by_hop.to_sql().sql
    assert sorted(by_hop.ids()) == ["ONT", "QUE"]


def test_a_node_reaches_its_levels_label(reg: sl.LocalRegistry) -> None:
    """What the hop is for. The column carries an id and nothing else."""
    assert sorted(reg.query(q.hcode).filter(q.hcode.level.name["en"] == "Province").ids()) == ["ONT", "QUE"]


def test_a_node_reaches_its_levels_index(reg: sl.LocalRegistry) -> None:
    """`level.index` is the rung's position; `node.depth` counts a node's own
    ancestors. They agree here and need not in a hierarchy that skips a level,
    which is why both are exposed."""
    frame = (
        reg.query(q.hcode)
        .filter(q.hcode.level.id.is_not_null())
        .order_by(q.hcode.id)
        .select(node=q.hcode.id, level=q.hcode.level.name["en"], idx=q.hcode.level.index)
        .collect()
    )
    assert frame.to_dicts() == [
        {"node": "CANADA", "level": "Country", "idx": 0},
        {"node": "ONT", "level": "Province", "idx": 1},
        {"node": "QUE", "level": "Province", "idx": 1},
    ]


def test_the_level_edge_is_scoped_by_its_hierarchy(reg: sl.LocalRegistry) -> None:
    """`(hierarchy_urn, level_id)` — composite, for the reason the tree edges
    are. A level id is unique within a hierarchy, not across them, so a
    single-column edge would join a node to another hierarchy's rung."""
    edge = next(e for e in EDGES if e.name == "level" and e.source == "hierarchy_node")
    assert edge.columns == (("hierarchy_urn", "hierarchy_urn"), ("level_id", "level_id"))


def test_a_node_without_a_level_survives_the_hop(reg: sl.LocalRegistry) -> None:
    """The fixture's `ALT` node has no level. An inner join here would drop it
    from a projection, which is what `optional=True` on the edge prevents."""
    frame = reg.query(q.hcode).order_by(q.hcode.urn).select(node=q.hcode.id, level=q.hcode.level.name["en"]).collect()
    assert None in frame["level"].to_list()
    assert len(frame) == reg.query(q.hcode).count()


def test_filtering_by_depth(reg: sl.LocalRegistry) -> None:
    """`depth` is baked at write time, like `code.depth`."""
    assert sorted(reg.query(q.hcode).filter(q.hcode.depth == 0).ids()) == ["CANADA", "ONT"]
    assert sorted(reg.query(q.hcode).filter(q.hcode.depth == 1).ids()) == ["ONT", "QUE"]


def test_parent_id_reaches_one_level(reg: sl.LocalRegistry) -> None:
    """One level, and deliberately only one. Recursion stays a domain verb."""
    assert sorted(reg.query(q.hcode).filter(q.hcode.parent.id == "CANADA").ids()) == ["ONT", "QUE"]
    assert sorted(reg.query(q.hcode).filter(q.hcode.parent.id.is_null()).ids()) == ["CANADA", "ONT"]


def test_a_node_label_is_its_code_s(reg: sl.LocalRegistry) -> None:
    """A hierarchical code is identifiable, not nameable: the label it shows is its code's (#419)."""
    found = reg.query(q.hcode).filter(q.hcode.code.name["en"] == "Ontario")
    assert found.ids() == ["ONT", "ONT"], "one placement in each hierarchy"
    assert not hasattr(q.hcode, "name")


def test_node_annotations(reg: sl.LocalRegistry) -> None:
    """A fourth typed edge off `annotation.owner_urn`, keyed by `node_urn`."""
    assert reg.query(q.hcode).filter(q.hcode.annotations.type == "SOURCE").ids() == ["CANADA"]
    assert reg.query(q.hcode).filter(q.hcode.annotations.title == "census").ids() == ["CANADA"]


def test_a_codes_annotations_are_not_a_nodes(reg: sl.LocalRegistry) -> None:
    """The reason the edges are typed rather than one polymorphic hub."""
    assert reg.query(q.code).filter(q.code.annotations.title == "census").ids() == []


# ── the edges out ─────────────────────────────────────────────────────────


def test_node_to_hierarchy(reg: sl.LocalRegistry) -> None:
    assert sorted(reg.query(q.hcode).filter(q.hcode.hierarchy.id == "ALT").ids()) == ["ONT"]
    assert reg.query(q.hcode).filter(q.hcode.hierarchy.agency == "STATCAN").count() == 4


def test_node_to_code(reg: sl.LocalRegistry) -> None:
    """A node places a code that lives in some codelist — a different relation
    with a different key, which is the whole point of keeping them apart."""
    assert sorted(reg.query(q.hcode).filter(q.hcode.code.id == "ON").ids()) == ["ONT", "ONT"]
    frame = reg.query(q.hcode).order_by(q.hcode.id).select(node=q.hcode.id, code=q.hcode.code.name["en"]).collect()
    assert dict(zip(frame["node"].to_list(), frame["code"].to_list(), strict=True)) == {
        "CANADA": "Canada",
        "ONT": "Ontario",
        "QUE": "Quebec",
    }


def test_node_to_code_to_codelist(reg: sl.LocalRegistry) -> None:
    """Two hops past the node, which is where a path stops being a convenience
    and starts being the point."""
    assert reg.query(q.hcode).filter(q.hcode.code.codelist.id == "CL_GEO").count() == 4
    assert reg.query(q.hcode).filter(q.hcode.code.codelist.id == "CL_NOPE").count() == 0


def test_a_node_whose_code_is_not_stored(reg: sl.LocalRegistry) -> None:
    """The code a node places may be in another store, so the edge must LEFT
    join — an INNER would drop the row from its own projection.

    It was a node with no code at all until #419 refused one at `add`: every
    format requires the reference, so only the referent can be missing.
    """
    with sl.LocalRegistry(":memory:") as bare:
        bare.add(
            Hierarchy(
                id="H",
                maintainer=Agency(id="E"),
                name={"en": "h"},
                tree=[HierarchicalCode(id="N", code=code_urn("CL", "ELSEWHERE"))],
            )
        )
        frame = bare.query(q.hcode).select(node=q.hcode.id, code=q.hcode.code.id).collect()
        assert frame.to_dicts() == [{"node": "N", "code": None}]


def test_a_node_without_a_code_is_refused() -> None:
    """No format can write one (#434), so no store holds one (#419): it cannot be built (#492)."""
    with pytest.raises(sl.ValidationError, match="must reference the code"):
        Hierarchy(id="H", maintainer=Agency(id="E"), name={"en": "h"}, tree=[HierarchicalCode(id="N")])


# ── the hierarchy root, and its fan-out ───────────────────────────────────


def test_hierarchy_is_a_root_with_its_own_discriminator(reg: sl.LocalRegistry) -> None:
    """`sdmx.artefact` holds the codelist too, so without the discriminator
    this returns it."""
    assert sorted(reg.query(q.hierarchy).ids()) == ["ALT", "GEO"]
    assert "CL_GEO" not in reg.query(q.hierarchy).ids()


def test_nodes_fan_out_from_a_hierarchy(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.hierarchy).filter(q.hierarchy.hcodes.level.id == "PROVINCE").ids() == ["GEO"]


def test_the_binder_groups_onto_one_node(reg: sl.LocalRegistry) -> None:
    """`GEO` has a node at PROVINCE and a node coded `CA`, but no *single* node
    that is both."""
    same = q.hierarchy.hcodes.any(q.hcode.level.id == "PROVINCE", q.hcode.code.id == "CA")
    assert reg.query(q.hierarchy).filter(same).ids() == []
    both = q.hierarchy.hcodes.any(q.hcode.level.id == "PROVINCE", q.hcode.code.id == "ON")
    assert reg.query(q.hierarchy).filter(both).ids() == ["GEO"]


def test_collecting_a_hierarchys_nodes(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.hierarchy).select(h=q.hierarchy.id, nodes=q.hierarchy.hcodes.id.list()).collect()
    counts = dict(zip(frame["h"].to_list(), (len(n) for n in frame["nodes"].to_list()), strict=True))
    assert counts == {"GEO": 3, "ALT": 1}


def test_grouping_nodes_by_level(reg: sl.LocalRegistry) -> None:
    rows = reg.query(q.hcode).group_by(q.hcode.level.id).len().collect().to_dicts()
    assert rows == [
        {"id": "COUNTRY", "len": 1},
        {"id": "PROVINCE", "len": 2},
        {"id": None, "len": 1},
    ]


# ── nodes and codes are different relations ───────────────────────────────


def test_a_node_predicate_does_not_reach_the_code_relation(reg: sl.LocalRegistry) -> None:
    """Both have `id`, `parent_id` and `depth`, and they mean different things.
    The row kind keeps them apart statically; this is the runtime evidence that
    they *would* answer differently."""
    assert sorted(reg.query(q.code).filter(q.code.parent.id.is_null()).ids()) == ["CA", "ON", "QC"]
    assert sorted(reg.query(q.hcode).filter(q.hcode.parent.id.is_null()).ids()) == ["CANADA", "ONT"]


def test_the_two_roots_have_different_tiebreaks(reg: sl.LocalRegistry) -> None:
    assert type(q.hcode).__key__ == ("node_urn",)
    assert type(q.code).__key__ == ("code_urn",)
    assert type(q.hcode).__table__ == "hierarchy_node"


# ── hierarchy associations: where a hierarchy is applied ──────────────────


def test_a_hierarchy_association_names_its_three_slots(reg: sl.LocalRegistry) -> None:
    """All three matter: the hierarchy, the object it organises, and the
    context that application holds in. Never blocked on #308 — the slots were
    already columns on the extension table, unlike a constraint's attachment.
    """
    dsd = DataStructure(id="CPI", maintainer=Agency(id="STATCAN"), name={"en": "CPI"})
    reg.add(dsd)
    reg.add(Dataflow(id="DF", maintainer=Agency(id="STATCAN"), name={"en": "Flow"}, structure=dsd))
    reg.add(
        HierarchyAssociation(
            id="HA",
            maintainer=Agency(id="STATCAN"),
            name={"en": "GEO on the CPI flow"},
            linked_hierarchy=Ref[object].unresolved(Hierarchy.urn_for(agency="STATCAN", id="GEO", version="1.0")),
            linked_object=Ref[object].unresolved(Dataflow.urn_for(agency="STATCAN", id="DF", version="1.0")),
            context_object=Ref[object].unresolved(DataStructure.urn_for(agency="STATCAN", id="CPI", version="1.0")),
        )
    )
    frame = reg.query(q.hierarchy_association).select(
        h=q.hierarchy_association.hierarchy.id,
        o=q.hierarchy_association.linked_object.id,
        c=q.hierarchy_association.context_object.id,
    )
    assert frame.collect().to_dicts() == [{"h": "GEO", "o": "DF", "c": "CPI"}]


def test_a_hierarchy_reaches_where_it_is_applied(reg: sl.LocalRegistry) -> None:
    """The inverse — "which flows use this hierarchy" — and the reason the
    relation is worth exposing at all."""
    dsd = DataStructure(id="CPI", maintainer=Agency(id="STATCAN"), name={"en": "CPI"})
    reg.add(dsd)
    reg.add(Dataflow(id="DF", maintainer=Agency(id="STATCAN"), name={"en": "Flow"}, structure=dsd))
    reg.add(
        HierarchyAssociation(
            id="HA",
            maintainer=Agency(id="STATCAN"),
            name={"en": "GEO on the CPI flow"},
            linked_hierarchy=Ref[object].unresolved(Hierarchy.urn_for(agency="STATCAN", id="GEO", version="1.0")),
            linked_object=Ref[object].unresolved(Dataflow.urn_for(agency="STATCAN", id="DF", version="1.0")),
        )
    )
    assert reg.query(q.hierarchy).filter(q.hierarchy.associations.linked_object.id == "DF").ids() == ["GEO"]
    assert reg.query(q.hierarchy).filter(q.hierarchy.associations.linked_object.id == "NOPE").ids() == []


# ── include_self on the lazy walks ───────────────────────────────────────────


def test_lazy_transitive_walks_can_include_the_node_itself(reg: sl.LocalRegistry) -> None:
    """`LazyHierarchy`'s `include_self` branch, which nothing pinned.

    The eager `Hierarchy` equivalents got tests when `include_self` was added
    to them; the lazy pair already had the flag and was exercised only by a
    documentation driver. Since the hierarchies page now runs from the page
    itself, the contract belongs here.

    Off by default because a transitive closure is a set of *other* nodes —
    SKOS specifies `broaderTransitive` as non-reflexive.
    """
    h = present(reg.get(Hierarchy.urn_for(agency="STATCAN", id="GEO", version="1.0")))

    down = h.narrower_transitive("CANADA").ids()
    assert "CANADA" not in down
    assert set(h.narrower_transitive("CANADA", include_self=True).ids()) == {"CANADA", *down}

    up = h.broader_transitive("CANADA.ONT").ids()
    assert "ONT" not in up
    assert set(h.broader_transitive("CANADA.ONT", include_self=True).ids()) == {"ONT", *up}


def test_lazy_include_self_agrees_with_the_eager_walk(reg: sl.LocalRegistry) -> None:
    """Two implementations, one documented flag. Checked against each other
    rather than asserted twice — the same argument the query oracles make."""
    urn = Hierarchy.urn_for(agency="STATCAN", id="GEO", version="1.0")
    lazy = present(reg.get(urn))
    eager = lazy.materialize()

    for include_self in (False, True):
        assert sorted(lazy.narrower_transitive("CANADA", include_self=include_self).ids()) == sorted(
            n.id for n in eager.narrower_transitive("CANADA", include_self=include_self)
        )
        assert sorted(lazy.broader_transitive("CANADA.ONT", include_self=include_self).ids()) == sorted(
            n.id for n in eager.broader_transitive("CANADA.ONT", include_self=include_self)
        )


def test_materialize_builds_no_level_it_then_discards(reg: sl.LocalRegistry) -> None:
    """A declared level is looked up, not rebuilt per node (#154).

    `levels_by_id.get(id, Level(id=id))` reads as a lookup with a fallback and
    is not one: a default argument is evaluated *eagerly*, so it built a
    throwaway `Level` for every node whose level was declared — which is nearly
    all of them. On the census bundle's FED hierarchy that was 499,138 wasted
    constructions, the single largest line in the profile, above
    `HierarchicalCode.__init__` itself. Each one also ran two
    `InternationalString` converters and an `Annotations` converter, so the
    waste was five objects per node, not one.

    Asserted as a **count of constructions**, per CLAUDE.md: the defect is
    work done, and a wall-clock assertion measures the machine as much as the
    code. This one fails deterministically on a loaded laptop.

    The fixture declares both levels and places every node at one, so the
    lookup hits every time and the count is exactly zero. A node at an
    *undeclared* level must still get a synthesised `Level` —
    `test_materialize_synthesises_a_level_that_was_never_declared` is the
    other half, and without it this test passes for a wrong implementation
    that simply drops the fallback.
    """
    built: list[str] = []
    original = Level.__init__

    def counting_init(self: Level, *args: Any, **kwargs: Any) -> None:
        built.append(str(kwargs.get("id", args[0] if args else "?")))
        original(self, *args, **kwargs)

    h = present(reg.get(Hierarchy.urn_for(agency="STATCAN", id="GEO", version="1.0")))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Level, "__init__", counting_init)
        eager = h.materialize()

    assert built == []
    # and the levels still arrived, so "zero constructions" is not "no levels"
    assert {n.level.id for n in eager.narrower_transitive("CANADA") if n.level} == {"PROVINCE"}


def test_a_stored_node_naming_an_undeclared_level_is_refused(reg: sl.LocalRegistry) -> None:
    """A node may name a level the hierarchy never declared: the writer stores
    `level_id` per node and does not check it against `levels`.

    It used to be read back with a `Level` made up on the spot. No format can
    write it (#455), so the store now refuses the hierarchy as it builds it
    (#492), through `materialize()` and `get(eager=True)` alike.
    """
    urn = Hierarchy.urn_for(agency="STATCAN", id="GEO", version="1.0")
    reg.sql(
        "UPDATE sdmx.hierarchy_node SET level_id = 'UNDECLARED' WHERE hierarchy_urn = $urn AND node_id = $node",
        urn=str(urn),
        node="ONT",
    ).collect()

    refusal = r"GEO\(1\.0\) is stored and refused: .*names level 'UNDECLARED', which this hierarchy does not define"
    lazy = present(reg.get(urn))
    with pytest.raises(SdmxConformanceError, match=refusal):
        lazy.materialize()
    with pytest.raises(SdmxConformanceError, match=refusal):
        reg.get(urn, eager=True)
    assert [r.urn for r in reg.refused] == [str(urn)]


# ── the component an association actually binds (#372) ────────────────────


def _flow_with_geo_dimension(reg: sl.LocalRegistry) -> SdmxUrn[object]:
    """A CPI flow whose DSD has a `REF_AREA` dimension. Returns the dim URN."""
    dsd = DataStructure(
        id="CPI",
        maintainer=Agency(id="STATCAN"),
        name={"en": "CPI"},
        dimensions=[sl.Dimension(id="REF_AREA", concept=concept_ref("REF_AREA"))],
    )
    reg.add(dsd)
    reg.add(Dataflow(id="DF", maintainer=Agency(id="STATCAN"), name={"en": "Flow"}, structure=dsd))
    return SdmxUrn.make(
        sl.Dimension,
        package="datastructure",
        artefact_class="Dimension",
        agency="STATCAN",
        id="CPI",
        version="1.0",
        item_id="REF_AREA",
    )


def test_an_association_reaches_the_component_it_binds(reg: sl.LocalRegistry) -> None:
    """The ordinary case, and the one the spine edge cannot answer.

    A HierarchyAssociation binds a hierarchy to a *dimension*. A Dimension URN
    is an item URN, so it never appears in `sdmx.artefact`, and every earlier
    test here linked a Dataflow — a maintainable — which is why the gap sat
    unnoticed while `linked_object` looked wired.
    """
    dim_urn = _flow_with_geo_dimension(reg)
    reg.add(
        HierarchyAssociation(
            id="HA",
            maintainer=Agency(id="STATCAN"),
            name={"en": "GEO on REF_AREA"},
            linked_hierarchy=Ref[object].unresolved(Hierarchy.urn_for(agency="STATCAN", id="GEO", version="1.0")),
            linked_object=Ref[object].unresolved(dim_urn),
        )
    )
    frame = reg.query(q.hierarchy_association).select(
        comp=q.hierarchy_association.component.id,
        dsd=q.hierarchy_association.component.dsd.id,
        h=q.hierarchy_association.hierarchy.id,
    )
    assert frame.collect().to_dicts() == [{"comp": "REF_AREA", "dsd": "CPI", "h": "GEO"}]


def test_the_maintainable_hop_still_reads_the_urn_it_was_given(reg: sl.LocalRegistry) -> None:
    """`linked_object` keeps its contract for the URN and nothing further.

    Pinned rather than left implicit: the column reads without a join, so it
    answers even for an item link, while every *other* column on that hop is
    NULL. A reader who tries `linked_object.id` and gets None should find this
    test rather than conclude the write was wrong.
    """
    dim_urn = _flow_with_geo_dimension(reg)
    reg.add(
        HierarchyAssociation(
            id="HA",
            maintainer=Agency(id="STATCAN"),
            name={"en": "GEO on REF_AREA"},
            linked_hierarchy=Ref[object].unresolved(Hierarchy.urn_for(agency="STATCAN", id="GEO", version="1.0")),
            linked_object=Ref[object].unresolved(dim_urn),
        )
    )
    frame = reg.query(q.hierarchy_association).select(
        urn=q.hierarchy_association.linked_object.urn,
        name=q.hierarchy_association.linked_object.id,
    )
    assert frame.collect().to_dicts() == [{"urn": str(dim_urn), "name": None}]


def test_a_component_reaches_the_associations_that_bind_it(reg: sl.LocalRegistry) -> None:
    """The inverse — "which hierarchies apply to this dimension"."""
    dim_urn = _flow_with_geo_dimension(reg)
    reg.add(
        HierarchyAssociation(
            id="HA",
            maintainer=Agency(id="STATCAN"),
            name={"en": "GEO on REF_AREA"},
            linked_hierarchy=Ref[object].unresolved(Hierarchy.urn_for(agency="STATCAN", id="GEO", version="1.0")),
            linked_object=Ref[object].unresolved(dim_urn),
        )
    )
    matched = reg.query(q.component).filter(
        q.component.hierarchy_associations.any(q.hierarchy_association.hierarchy.id == "GEO")
    )
    assert matched.select(comp=q.component.id).collect().to_dicts() == [{"comp": "REF_AREA"}]

    missed = reg.query(q.component).filter(
        q.component.hierarchy_associations.any(q.hierarchy_association.hierarchy.id == "NOPE")
    )
    assert missed.select(comp=q.component.id).collect().to_dicts() == []


def test_the_component_hop_is_scoped_by_the_dsd_not_the_id_alone(reg: sl.LocalRegistry) -> None:
    """A second DSD with the same component id must not be swept in.

    The edge is composite for this reason: `REF_AREA` is unique within a DSD,
    not across the store. A single-column join on the item id would answer
    both flows and look right on any fixture with one DSD.
    """
    dim_urn = _flow_with_geo_dimension(reg)
    reg.add(
        DataStructure(
            id="TRADE",
            maintainer=Agency(id="STATCAN"),
            name={"en": "Trade"},
            dimensions=[sl.Dimension(id="REF_AREA", concept=concept_ref("REF_AREA"))],
        )
    )
    reg.add(
        HierarchyAssociation(
            id="HA",
            maintainer=Agency(id="STATCAN"),
            name={"en": "GEO on the CPI REF_AREA"},
            linked_hierarchy=Ref[object].unresolved(Hierarchy.urn_for(agency="STATCAN", id="GEO", version="1.0")),
            linked_object=Ref[object].unresolved(dim_urn),
        )
    )
    reached = reg.query(q.component).filter(
        q.component.hierarchy_associations.any(q.hierarchy_association.hierarchy.id == "GEO")
    )
    assert reached.select(dsd=q.component.dsd.id).collect().to_dicts() == [{"dsd": "CPI"}]
