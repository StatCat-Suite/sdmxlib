"""`q.metadata_set`, `q.reported_attribute`, `q.metadata_target`.

The relations `MetadataValueQuery` needs before it can be migrated, and the
last of the four DSLs to get accessors.

Reference metadata has a shape the rest of the model does not. A `MetadataSet`
is maintainable — it sits on the artefact spine with an extension naming what
it was reported *against* — but its contents are two side tables: the reported
values, and the artefacts those values are *about*. SDMX 3.0 dropped the v2.1
`MetadataReport` layer, so one set carries a single attribute tree applying to
all its targets, which is why `targets` hangs off the set rather than off each
value.

A reported value has no URN. It is identified by its position in a set's
attribute tree, so the resolving terminals refuse this root and `ids()` returns
attribute paths.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.organisation import Agency
from sdmxlib.query.accessors import q
from tests.support.factories import concept_ref

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Two sets over one flow, describing different artefacts.

    Two on purpose: a query that dropped the `set_urn` correlation would answer
    every single-set question correctly. `CONTACT` is reported in both, so the
    attribute path is not an identifier either.
    """
    with sl.LocalRegistry(":memory:") as registry:
        msd = MetadataStructure(
            id="MSD",
            maintainer=Agency(id="ESTAT"),
            name={"en": "Reporting"},
            attributes=[
                MetadataAttribute(id="CONTACT", concept=concept_ref("CONTACT")),
                MetadataAttribute(id="QUALITY", concept=concept_ref("QUALITY")),
            ],
        )
        registry.add(msd)
        flow = Metadataflow(id="MF", maintainer=Agency(id="ESTAT"), name={"en": "Flow"}, structure=msd)
        registry.add(flow)
        registry.add(Codelist(id="CL_GEO", maintainer=Agency(id="ESTAT"), name={"en": "Geography"}))
        registry.add(DataStructure(id="CPI", maintainer=Agency(id="ESTAT"), name={"en": "CPI"}))

        registry.add(
            MetadataSet(
                id="MS_GEO",
                maintainer=Agency(id="ESTAT"),
                name={"en": "About geography"},
                structure=flow,
                targets=[Codelist.urn_for(agency="ESTAT", id="CL_GEO", version="1.0")],
                # `CONTACT` twice: SDMX models repetition as sibling rows
                # sharing an id, never as a values list — so `sibling_index` is
                # part of what identifies a row, and a fixture without a
                # repeat cannot tell that key from a shorter one.
                attributes=[
                    ReportedAttribute(id="CONTACT", value="stats@estat"),
                    ReportedAttribute(id="CONTACT", value="backup@estat"),
                    ReportedAttribute(id="QUALITY", value="high"),
                ],
            )
        )
        registry.add(
            MetadataSet(
                id="MS_CPI",
                maintainer=Agency(id="ESTAT"),
                name={"en": "About CPI"},
                structure=flow,
                targets=[DataStructure.urn_for(agency="ESTAT", id="CPI", version="1.0")],
                attributes=[ReportedAttribute(id="CONTACT", value="prices@estat")],
            )
        )
        yield registry


# ── the set ───────────────────────────────────────────────────────────────


def test_metadata_sets_are_a_root(reg: sl.LocalRegistry) -> None:
    """`sdmx.artefact` holds the MSD, the flow, a codelist and a DSD too, so
    without the discriminator this returns all of them."""
    assert sorted(reg.query(q.metadata_set).ids()) == ["MS_CPI", "MS_GEO"]


def test_a_set_reaches_the_structure_it_reports_against(reg: sl.LocalRegistry) -> None:
    """`structure_urn` sits on the extension table, so this crosses the
    identity split — the same compound hop `q.dataflow.dsd` uses."""
    assert sorted(reg.query(q.metadata_set).filter(q.metadata_set.structure.id == "MF").ids()) == [
        "MS_CPI",
        "MS_GEO",
    ]
    assert reg.query(q.metadata_set).filter(q.metadata_set.structure.id == "NOPE").ids() == []


def test_a_set_is_a_maintainable(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.metadata_set)
        .order_by(q.metadata_set.id)
        .select(id=q.metadata_set.id, agency=q.metadata_set.agency, label=q.metadata_set.name["en"])
        .collect()
    )
    assert frame.to_dicts() == [
        {"id": "MS_CPI", "agency": "ESTAT", "label": "About CPI"},
        {"id": "MS_GEO", "agency": "ESTAT", "label": "About geography"},
    ]


# ── the values ────────────────────────────────────────────────────────────


def test_values_are_a_root(reg: sl.LocalRegistry) -> None:
    assert sorted(reg.query(q.reported_attribute).ids()) == ["CONTACT", "CONTACT", "CONTACT[1]", "QUALITY"]


def test_a_repeated_attribute_gets_a_subscripted_path(reg: sl.LocalRegistry) -> None:
    """SDMX models repetition as sibling rows sharing an id, and the writer
    disambiguates them *in the path* — `CONTACT`, `CONTACT[1]` — with
    `sibling_index` carrying the same ordinal.

    So the two agree, and a caller can read either. Worth pinning because the
    redundancy is not obvious: a reader expecting `(set, path)` to repeat for
    siblings would build the wrong index.
    """
    frame = (
        reg.query(q.reported_attribute)
        .filter(q.reported_attribute.metadata_set.id == "MS_GEO")
        .filter(q.reported_attribute.attribute_path.str.starts_with("CONTACT"))
        .order_by(q.reported_attribute.position)
        .select(p=q.reported_attribute.attribute_path, i=q.reported_attribute.position, v=q.reported_attribute.scalar)
        .collect()
    )
    assert frame.to_dicts() == [
        {"p": "CONTACT", "i": 0, "v": "stats@estat"},
        {"p": "CONTACT[1]", "i": 1, "v": "backup@estat"},
    ]


def test_a_value_carries_its_scalar_and_its_kind(reg: sl.LocalRegistry) -> None:
    """`kind` is exposed rather than inferred: reading `scalar` on a localised
    row yields `NULL` rather than an error, so the caller needs to be able to
    ask which one a row has."""
    frame = (
        reg.query(q.reported_attribute)
        .filter(q.reported_attribute.attribute_path == "QUALITY")
        .select(
            path=q.reported_attribute.attribute_path,
            scalar=q.reported_attribute.scalar,
            kind=q.reported_attribute.kind,
        )
        .collect()
    )
    assert frame.to_dicts() == [{"path": "QUALITY", "scalar": "high", "kind": "scalar"}]


def test_a_value_reaches_its_set(reg: sl.LocalRegistry) -> None:
    """The attribute path repeats across sets, so this is the hop that makes a
    value identifiable at all."""
    found = reg.query(q.reported_attribute).filter(
        q.reported_attribute.attribute_path == "CONTACT",
        q.reported_attribute.metadata_set.id == "MS_CPI",
    )
    frame = found.select(v=q.reported_attribute.scalar).collect()
    assert frame["v"].to_list() == ["prices@estat"]


def test_a_value_reaches_the_structure_through_its_set(reg: sl.LocalRegistry) -> None:
    """Three hops, the middle one crossing the identity split."""
    assert reg.query(q.reported_attribute).filter(q.reported_attribute.metadata_set.structure.id == "MF").count() == 4


def test_a_value_has_no_urn(reg: sl.LocalRegistry) -> None:
    """It is identified by its position in a set's tree, not by an identity of
    its own — so `urns()` has nothing to hand back.

    `collect()` still works: a resolver is registered for this root and takes
    the *key* rather than a URN, which is the whole reason that contract is
    keys and not URNs. It yields MSD-typed `MetadataSection` rows.
    """
    with pytest.raises(TypeError, match="have no URN"):
        reg.query(q.reported_attribute).urns()
    sections = reg.query(q.reported_attribute).collect()
    assert {type(s).__name__ for s in sections} == {"MetadataSection"}
    assert len(sections) == 4


def test_grouping_values_by_path(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.reported_attribute).group_by(q.reported_attribute.attribute_path).len().collect().to_dicts() == [
        {"attribute_path": "CONTACT", "len": 2},
        {"attribute_path": "CONTACT[1]", "len": 1},
        {"attribute_path": "QUALITY", "len": 1},
    ]


# ── the targets ───────────────────────────────────────────────────────────


def test_targets_name_the_artefacts_a_set_describes(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.metadata_target)
        .order_by(q.metadata_target.metadata_set.id)
        .select(set=q.metadata_target.metadata_set.id, about=q.metadata_target.artefact.id)
        .collect()
    )
    assert frame.to_dicts() == [
        {"set": "MS_CPI", "about": "CPI"},
        {"set": "MS_GEO", "about": "CL_GEO"},
    ]


def test_the_two_edges_off_a_target_mean_different_things(reg: sl.LocalRegistry) -> None:
    """Both point at `sdmx.artefact`. `set` is the payload the target belongs
    to; `artefact` is what the payload is about. Collapsing them into one hop
    would answer this the same either way — it does not."""
    assert reg.query(q.metadata_target).filter(q.metadata_target.metadata_set.id == "MS_GEO").count() == 1
    assert reg.query(q.metadata_target).filter(q.metadata_target.artefact.id == "MS_GEO").count() == 0
    assert reg.query(q.metadata_target).filter(q.metadata_target.artefact.id == "CL_GEO").count() == 1


def test_a_set_fans_out_to_its_targets(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.metadata_set).filter(q.metadata_set.targets.artefact.id == "CL_GEO").ids() == ["MS_GEO"]


def test_finding_the_metadata_reported_about_an_artefact(reg: sl.LocalRegistry) -> None:
    """The question reference metadata exists to answer, and the reason
    `q.reported_attribute.metadata_set.targets` is worth the edge."""
    found = reg.query(q.reported_attribute).filter(q.reported_attribute.metadata_set.targets.artefact.id == "CL_GEO")
    frame = found.order_by(q.reported_attribute.attribute_path).select(v=q.reported_attribute.scalar).collect()
    assert frame["v"].to_list() == ["stats@estat", "backup@estat", "high"]


def test_the_binder_groups_onto_one_target(reg: sl.LocalRegistry) -> None:
    same = q.metadata_set.targets.any(
        q.metadata_target.artefact.id == "CL_GEO",
        q.metadata_target.artefact.agency == "ESTAT",
    )
    assert reg.query(q.metadata_set).filter(same).ids() == ["MS_GEO"]
    missed = q.metadata_set.targets.any(
        q.metadata_target.artefact.id == "CL_GEO",
        q.metadata_target.artefact.agency == "OECD",
    )
    assert reg.query(q.metadata_set).filter(missed).ids() == []


# ── the cycle that stayed ─────────────────────────────────────────────────


def test_a_set_does_not_fan_out_to_its_values(reg: sl.LocalRegistry) -> None:
    """Pinned so it is not mistaken for an oversight.

    `_MetadataSet.values` would need `_ManyMetadataValue`, which needs
    `_MetadataValue`, which needs `_MetadataSet` for its own `set` edge — the
    same true cycle `q.component.dsd` has. The richer direction won: a value
    reaches its set's targets, and a set does not fan out to its values. Root
    at `q.reported_attribute` for that question, as below.
    """
    assert not hasattr(q.metadata_set, "values")
    assert reg.query(q.reported_attribute).filter(q.reported_attribute.metadata_set.id == "MS_GEO").count() == 3
