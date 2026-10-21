"""`q.category`, `q.categorisation`, and `q.artefact.type`.

Categorisation is how a catalogue is browsed — "what is filed under Prices" —
and it is the shape the path language handles least like the rest of the model.
A `Categorisation` is a *binding*: two ends, both pointing at `sdmx.artefact`,
meaning entirely different things. `artefact` is what was categorised;
`category` is what it was categorised under. Naming them apart is the whole
content of the accessor, and a test below shows they answer differently.

`_Categorisation` is rooted on the **extension** rather than the spine, unlike
`_Dataflow` and `_MetadataSet`. That is deliberate: its own maintainable
surface is almost never the question, and rooting on the extension means there
are no hoisted columns to bind against the wrong table — the defect
`test_query_dataflow.py` exists for.

A `Category` is the third relation in this schema with `id` / `parent_id` /
`depth` meaning something of its own, after `code` and `hierarchy_node`. Same
tree shape, three keys, and a predicate for one must not compile against
another.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.category import Categorisation, Category, CategoryScheme
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.query.accessors import q

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


def _category_urn(scheme: str, item_id: str) -> SdmxUrn[Category]:
    return SdmxUrn.make(
        Category,
        package="categoryscheme",
        artefact_class="Category",
        agency="ESTAT",
        id=scheme,
        version="1.0",
        item_id=item_id,
    )


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """One scheme, a two-level tree, and two artefacts filed under it.

    Two categorisations pointing at *different* categories, because a fixture
    with one cannot tell a correlated lookup from an unconditional one. The
    codelist is filed under a leaf and the dataflow under the root, so the two
    ends of the binding never coincide.
    """
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            CategoryScheme(
                id="CS",
                maintainer=Agency(id="ESTAT"),
                name={"en": "Subject matter"},
                categories=[
                    Category(
                        id="ECON",
                        name={"en": "Economy"},
                        categories=[Category(id="PRICES", name={"en": "Prices"})],
                    )
                ],
            )
        )
        registry.add(Codelist(id="CL_GEO", maintainer=Agency(id="ESTAT"), name={"en": "Geography"}))
        dsd = DataStructure(id="CPI", maintainer=Agency(id="ESTAT"), name={"en": "CPI"})
        registry.add(dsd)
        registry.add(Dataflow(id="DF_CPI", maintainer=Agency(id="ESTAT"), name={"en": "CPI flow"}, structure=dsd))

        registry.add(
            Categorisation(
                id="CAT_CL",
                maintainer=Agency(id="ESTAT"),
                name={"en": "geo under prices"},
                source=Ref[object].unresolved(Codelist.urn_for(agency="ESTAT", id="CL_GEO", version="1.0")),
                target=Ref[object].unresolved(_category_urn("CS", "ECON.PRICES")),
            )
        )
        registry.add(
            Categorisation(
                id="CAT_DF",
                maintainer=Agency(id="ESTAT"),
                name={"en": "flow under economy"},
                source=Ref[object].unresolved(Dataflow.urn_for(agency="ESTAT", id="DF_CPI", version="1.0")),
                target=Ref[object].unresolved(_category_urn("CS", "ECON")),
            )
        )
        yield registry


# ── categories ────────────────────────────────────────────────────────────


def test_categories_are_a_root(reg: sl.LocalRegistry) -> None:
    assert sorted(reg.query(q.category).ids()) == ["ECON", "PRICES"]


def test_a_category_is_a_tree(reg: sl.LocalRegistry) -> None:
    """`depth` is baked at write time, as for codes and hierarchy nodes."""
    frame = (
        reg.query(q.category)
        .order_by(q.category.id)
        .select(
            id=q.category.id,
            parent=q.category.parent.id,
            depth=q.category.depth,
            label=q.category.name["en"],
        )
        .collect()
    )
    assert frame.to_dicts() == [
        {"id": "ECON", "parent": None, "depth": 0, "label": "Economy"},
        {"id": "PRICES", "parent": "ECON", "depth": 1, "label": "Prices"},
    ]


def test_a_category_reaches_its_scheme(reg: sl.LocalRegistry) -> None:
    assert sorted(reg.query(q.category).filter(q.category.scheme.id == "CS").ids()) == ["ECON", "PRICES"]
    assert reg.query(q.category).filter(q.category.scheme.id == "NOPE").ids() == []


def test_a_category_is_not_a_code(reg: sl.LocalRegistry) -> None:
    """Three relations now carry `id` / `parent_id` / `depth`. The row kind
    keeps their predicates apart statically; this is the runtime evidence they
    would answer differently."""
    assert type(q.category).__table__ == "category"
    assert type(q.category).__key__ == ("category_urn",)
    assert reg.query(q.code).count() == 0
    assert reg.query(q.category).count() == 2


# ── categorisations: two ends, both artefacts ─────────────────────────────


def test_a_categorisation_names_both_ends(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.categorisation)
        .order_by(q.categorisation.artefact.id)
        .select(what=q.categorisation.artefact.id, under=q.categorisation.category.id)
        .collect()
    )
    assert frame.to_dicts() == [
        {"what": "CL_GEO", "under": "PRICES"},
        {"what": "DF_CPI", "under": "ECON"},
    ]


def test_the_two_ends_are_different_hops(reg: sl.LocalRegistry) -> None:
    """Both point at `sdmx.artefact`. Collapsing them into one would answer
    this the same either way — it does not."""
    assert reg.query(q.categorisation).filter(q.categorisation.artefact.id == "CL_GEO").count() == 1
    assert reg.query(q.categorisation).filter(q.categorisation.artefact.id == "PRICES").count() == 0
    assert reg.query(q.categorisation).filter(q.categorisation.category.id == "PRICES").count() == 1


def test_filing_is_found_by_category(reg: sl.LocalRegistry) -> None:
    """ "What is filed under Prices" — the catalogue question, as a path."""
    found = reg.query(q.categorisation).filter(q.categorisation.category.id == "PRICES")
    assert found.select(what=q.categorisation.artefact.id).collect()["what"].to_list() == ["CL_GEO"]


def test_filing_is_found_by_the_categorys_label(reg: sl.LocalRegistry) -> None:
    """Two hops past the binding, and the second is an inline `MAP`."""
    found = reg.query(q.categorisation).filter(q.categorisation.category.name["en"] == "Economy")
    assert found.select(what=q.categorisation.artefact.id).collect()["what"].to_list() == ["DF_CPI"]


def test_filing_is_found_by_the_scheme(reg: sl.LocalRegistry) -> None:
    """Three hops: binding, category, scheme."""
    assert reg.query(q.categorisation).filter(q.categorisation.category.scheme.id == "CS").count() == 2


def test_the_binder_groups_onto_one_categorisation(reg: sl.LocalRegistry) -> None:
    """No single binding files `CL_GEO` under `ECON` — it is under `PRICES`."""
    same = q.categorisation.artefact.id == "CL_GEO"
    assert reg.query(q.categorisation).filter(same, q.categorisation.category.id == "ECON").count() == 0
    assert reg.query(q.categorisation).filter(same, q.categorisation.category.id == "PRICES").count() == 1


def test_grouping_filings_by_category(reg: sl.LocalRegistry) -> None:
    rows = reg.query(q.categorisation).group_by(q.categorisation.category.id).len().collect().to_dicts()
    assert rows == [{"id": "ECON", "len": 1}, {"id": "PRICES", "len": 1}]


# ── the discriminator, exposed ────────────────────────────────────────────


def test_the_artefact_type_is_a_column(reg: sl.LocalRegistry) -> None:
    """The named roots apply this for you. Exposed for a query over
    `q.artefact` that has to narrow by type at runtime — which is what
    `reg.artefacts(T)` does."""
    assert reg.query(q.artefact).filter(q.artefact.type == "Codelist").ids() == ["CL_GEO"]
    assert sorted(reg.query(q.artefact).filter(q.artefact.type.is_in(["Codelist", "Dataflow"])).ids()) == [
        "CL_GEO",
        "DF_CPI",
    ]


def test_the_type_column_agrees_with_the_named_roots(reg: sl.LocalRegistry) -> None:
    """A mismatch between the exposed column and `__constraint__` would be
    silent — an empty result is not an error."""
    assert reg.query(q.artefact).filter(q.artefact.type == "Codelist").ids() == reg.query(q.codelist).ids()
    assert reg.query(q.artefact).filter(q.artefact.type == "Dataflow").ids() == reg.query(q.dataflow).ids()


def test_grouping_the_catalogue_by_type(reg: sl.LocalRegistry) -> None:
    counts = {
        row["type"]: row["len"] for row in reg.query(q.artefact).group_by(q.artefact.type).len().collect().to_dicts()
    }
    assert counts["Categorisation"] == 2
    assert counts["Codelist"] == 1
    assert counts["Dataflow"] == 1
