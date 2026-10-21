"""Every retired `ArtefactQuery` verb, answered by the path language instead.

`sl.artefact` carried four derived predicates the path language has no verb
for: `uses_codelist`, `uses_concept`, `in_category` and `has_metadata`. This
file is where each one's replacement is pinned.

**The expectations below are not hand-written.** In a0c04bc this file ran both
spellings against this same fixture and asserted they agreed; the old side was
deleted in the commit after, and the values it produced are what remains. So
these are a differential result frozen at the moment of deletion, not a guess
at what the new form ought to return — which is the difference between a
migration guide that was checked and one that was written.

Two of the four are the *two-route* shape the design argued about and settled:
`uses_concept` matches a DataStructure carrying such a component **and** a
Dataflow whose DSD does, so it spans two roots. The answer is not a set
operation — `union` across discriminators is refused, and a test below shows
the refusal — but an `|` of two `is_in` sub-queries, which is what SQLAlchemy,
ibis and polars all reach for. See `docs/design/query-language.md`.

One inconsistency in the old surface, recorded rather than reproduced:
`uses_concept` accepts a bare id while `uses_codelist` silently returns nothing
for one and needs a full URN.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.category import Categorisation, Category, CategoryScheme
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.query.accessors import q
from sdmxlib.query.query import union
from tests.support.factories import concept_ref

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

_CAT_URN = SdmxUrn.make(
    Category,
    package="categoryscheme",
    artefact_class="Category",
    agency="ESTAT",
    id="CAT",
    version="1.0",
    item_id="ECON",
)


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """One DSD used by one flow, plus a second flow with nothing on it.

    The bare flow is what tells a real predicate from one matching every row,
    and the DSD/flow pair is what makes the two-route verbs return two.
    """
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            Codelist(
                id="CL_GEO",
                maintainer=Agency(id="ESTAT"),
                name={"en": "Geo"},
                codes=[Code(id="EU", name={"en": "Europe"})],
            )
        )
        registry.add(
            ConceptScheme(
                id="CS",
                maintainer=Agency(id="ESTAT"),
                name={"en": "Concepts"},
                concepts=[Concept(id="REF_AREA", name={"en": "Area"})],
            )
        )
        dsd = DataStructure(
            id="CPI",
            maintainer=Agency(id="ESTAT"),
            name={"en": "CPI"},
            dimensions=[
                Dimension(
                    id="REF_AREA",
                    concept=Ref[object].unresolved(
                        Concept.urn_for(agency="ESTAT", scheme="CS", id="REF_AREA", version="1.0")
                    ),
                    representation=Ref[object].unresolved(Codelist.urn_for(agency="ESTAT", id="CL_GEO", version="1.0")),
                )
            ],
        )
        registry.add(dsd)
        registry.add(Dataflow(id="DF", maintainer=Agency(id="ESTAT"), name={"en": "Flow"}, structure=dsd))
        registry.add(
            DataStructure(
                id="BARE",
                maintainer=Agency(id="ESTAT"),
                name={"en": "Bare"},
                dimensions=[Dimension(id="X", concept=concept_ref("X"))],
            )
        )
        registry.add(
            CategoryScheme(
                id="CAT",
                maintainer=Agency(id="ESTAT"),
                name={"en": "Subjects"},
                categories=[Category(id="ECON", name={"en": "Economy"})],
            )
        )
        registry.add(
            Categorisation(
                id="FILE",
                maintainer=Agency(id="ESTAT"),
                name={"en": "flow under economy"},
                source=Ref[object].unresolved(Dataflow.urn_for(agency="ESTAT", id="DF", version="1.0")),
                target=Ref[object].unresolved(_CAT_URN),
            )
        )
        msd = MetadataStructure(
            id="MSD",
            maintainer=Agency(id="ESTAT"),
            name={"en": "MSD"},
            attributes=[
                MetadataAttribute(id="QUALITY", concept=concept_ref("QUALITY")),
                MetadataAttribute(id="CONTACT", concept=concept_ref("CONTACT")),
            ],
        )
        registry.add(msd)
        flow = Metadataflow(id="MF", maintainer=Agency(id="ESTAT"), name={"en": "MF"}, structure=msd)
        registry.add(flow)
        registry.add(
            MetadataSet(
                id="MS",
                maintainer=Agency(id="ESTAT"),
                name={"en": "MS"},
                structure=flow,
                targets=[Ref[object].unresolved(Dataflow.urn_for(agency="ESTAT", id="DF", version="1.0"))],
                attributes=[ReportedAttribute(id="QUALITY", value="good")],
            )
        )
        yield registry


# ── the two-route verbs ───────────────────────────────────────────────────


def _two_routes(reg: sl.LocalRegistry, value: str) -> list[str]:
    """The documented replacement: an `|` of two single-column sub-queries.

    Not a set operation. `union` compares rows of one relation, and these are
    two roots — see `test_union_will_not_span_two_roots`.
    """
    direct = reg.query(q.dsd).filter(q.dsd.components.codelist.id == value).select(urn=q.dsd.urn)
    via_flow = reg.query(q.dataflow).filter(q.dataflow.dsd.components.codelist.id == value).select(urn=q.dataflow.urn)
    found = reg.query(q.artefact).filter(q.artefact.urn.is_in(direct) | q.artefact.urn.is_in(via_flow))
    return sorted(found.ids())


def test_uses_codelist(reg: sl.LocalRegistry) -> None:
    assert _two_routes(reg, "CL_GEO") == ["CPI", "DF"]


def test_uses_concept(reg: sl.LocalRegistry) -> None:
    direct = reg.query(q.dsd).filter(q.dsd.components.concept.id == "REF_AREA").select(urn=q.dsd.urn)
    via_flow = (
        reg.query(q.dataflow).filter(q.dataflow.dsd.components.concept.id == "REF_AREA").select(urn=q.dataflow.urn)
    )
    found = reg.query(q.artefact).filter(q.artefact.urn.is_in(direct) | q.artefact.urn.is_in(via_flow))
    assert sorted(found.ids()) == ["CPI", "DF"]


def test_the_two_route_form_is_one_statement(reg: sl.LocalRegistry) -> None:
    """The point of `is_in` over a `Query` rather than a fetched list: the
    sub-queries are spliced, not materialised."""
    direct = reg.query(q.dsd).filter(q.dsd.components.codelist.id == "CL_GEO").select(urn=q.dsd.urn)
    compiled = reg.query(q.artefact).filter(q.artefact.urn.is_in(direct)).to_sql()
    assert "IN (SELECT" in compiled.sql


def test_it_discriminates(reg: sl.LocalRegistry) -> None:
    """`BARE` is a DSD with a dimension and no codelist. A predicate matching
    every structure would pass the tests above and fail here."""
    assert _two_routes(reg, "NOPE") == []


def test_union_will_not_span_two_roots(reg: sl.LocalRegistry) -> None:
    """Why the replacement is `|` and not `union`.

    A set operation compares rows of one relation; `q.dsd` and `q.dataflow` are
    two. Checked against the peers before settling it: SQLAlchemy writes
    `or_(exists, exists)`, ibis an `isin` disjunction, polars an `is_in` over
    two sets. None reaches for a union.
    """
    with pytest.raises(TypeError, match="cannot combine a query over"):
        # The refusal is the subject; the row kind catches most of it.
        union(reg.query(q.dsd), reg.query(q.dataflow))  # pyrefly: ignore[bad-argument-type]


# ── the single-route verbs ────────────────────────────────────────────────


def test_in_category(reg: sl.LocalRegistry) -> None:
    """`categorisations` is an inverse on every maintainable, so this is one
    hop plus one — no verb needed."""
    found = reg.query(q.artefact).filter(q.artefact.categorisations.category.id == "ECON")
    assert sorted(found.ids()) == ["DF"]
    assert reg.query(q.artefact).filter(q.artefact.categorisations.category.id == "NOPE").ids() == []


def test_has_metadata_anything_at_all(reg: sl.LocalRegistry) -> None:
    found = reg.query(q.artefact).filter(q.artefact.described_by.metadata_set.id.is_not_null())
    assert sorted(found.ids()) == ["DF"]


def test_has_metadata_narrowed_to_a_path(reg: sl.LocalRegistry) -> None:
    """A set is reachable from a target, and the *values* are reachable from
    the set only by rooting at them — `_MetadataSet` deliberately has no
    `reported_attributes` fan-out, because that cycle resolves in the richer
    direction. So the narrowing is a sub-query, which is what `is_in` is for.
    """
    cases: tuple[tuple[str, list[str]], ...] = (("QUALITY", ["DF"]), ("CONTACT", []))
    for path, expected in cases:
        sets = (
            reg.query(q.reported_attribute)
            .filter(q.reported_attribute.attribute_path == path)
            .select(urn=q.reported_attribute.metadata_set.urn)
        )
        found = reg.query(q.artefact).filter(q.artefact.described_by.metadata_set.urn.is_in(sets))
        assert sorted(found.ids()) == expected, path


def test_is_in_over_a_subquery_survives_a_fan_out(reg: sl.LocalRegistry) -> None:
    """The regression for a compiler bug this parity work found.

    `InRelation` was the one leaf compiled by calling `reference()` directly
    rather than through `_leaf`, so it assumed a flat column. Over a path
    crossing a fan-out it raised `KeyError` in the binder: `_add_prefix` stops
    at the fan-out and leaves the correlation to `_exists`, so the prefix it
    looked up was never registered. Every other operator went the right way,
    which is why it stayed hidden until a sub-query met `described_by`.
    """
    sets = reg.query(q.reported_attribute).select(urn=q.reported_attribute.metadata_set.urn)
    compiled = reg.query(q.artefact).filter(q.artefact.described_by.metadata_set.urn.is_in(sets)).to_sql()
    assert "EXISTS" in compiled.sql
    assert "IN (SELECT" in compiled.sql


# ── everything else was already a plain column ────────────────────────────


def test_the_plain_columns_need_no_translation(reg: sl.LocalRegistry) -> None:
    """Everything that was already a column on `sdmx.artefact` reads the same
    on either side, which is why only the four derived verbs needed proving."""
    assert sorted(reg.query(q.artefact).filter(q.artefact.agency == "ESTAT").ids()) == [
        "BARE",
        "CAT",
        "CL_GEO",
        "CPI",
        "CS",
        "DF",
        "FILE",
        "MF",
        "MS",
        "MSD",
    ]
    assert reg.query(q.artefact).filter(q.artefact.id == "CPI").ids() == ["CPI"]
    assert reg.query(q.artefact).filter(q.artefact.name["en"] == "CPI").ids() == ["CPI"]


def test_reg_artefacts_still_narrows_by_a_runtime_type(reg: sl.LocalRegistry) -> None:
    """`reg.artefacts(T)` survived the deletion because it answers a question
    the named roots cannot: the type is a *variable*. It returns the same
    `Query` every other root does now."""
    from sdmxlib.model.codelist import Codelist

    assert reg.artefacts(Codelist).ids() == ["CL_GEO"]
    assert sorted(reg.artefacts().ids()) == sorted(reg.query(q.artefact).ids())
    assert reg.artefacts(Codelist).filter(q.artefact.agency == "ESTAT").count() == 1
    with pytest.raises(TypeError, match="not a stored SDMX artefact type"):
        reg.artefacts(dict)
