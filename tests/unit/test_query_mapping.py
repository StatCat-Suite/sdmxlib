"""`q.representation_map` and `q.representation_mapping` — value translation.

A `RepresentationMap` says how values of some representations become values of
others. It is the artefact behind "what does this provider's `GEO` mean in our
terms", and the question worth asking of a catalogue is *which maps translate
out of this codelist* — one hop, once the endpoints are edges.

SDMX 3.x makes the map N:M (#422): several endpoints a side, each a codelist
or a primitive data type, and mappings holding one value per endpoint. So the
endpoints are fan-outs, `sources` and `targets`, each reaching its `codelist`
(null for a data type), and a mapping's values are stored lists queried with
`.has()`. The names are SDMX-ML 3.x's element names.

Storage carries all of it — the data-type endpoints and the regex and index
mappings it used to drop are tested below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.mapping import MapDataType, RepresentationMap, RepresentationMapping, SourceValue
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from sdmxlib.query.accessors import q

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


def _cl(cid: str) -> Ref[object]:
    return Ref[object].unresolved(Codelist.urn_for(agency="ESTAT", id=cid, version="1.0"))


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Three codelists and two maps sharing a source.

    Two maps because a fixture with one cannot tell a correlated lookup from an
    unconditional one, and they share `CL_A` as source so "maps out of this
    codelist" returns more than one. `CL_C` is the target of one map and the
    source of neither, which is what distinguishes the two endpoint sides.
    `RM_AC` is N:M — a data type beside each codelist — so a side's endpoints
    are several rows.
    """
    with sl.LocalRegistry(":memory:") as registry:
        for cid in ("CL_A", "CL_B", "CL_C"):
            registry.add(
                Codelist(
                    id=cid,
                    maintainer=Agency(id="ESTAT"),
                    name={"en": f"List {cid}"},
                    codes=[Code(id="X", name={"en": "x"})],
                )
            )
        registry.add(
            RepresentationMap(
                id="RM_AB",
                maintainer=Agency(id="ESTAT"),
                name={"en": "A to B"},
                sources=(_cl("CL_A"),),
                targets=(_cl("CL_B"),),
                mappings=(
                    RepresentationMapping(sources=("A1",), targets=("B1",)),
                    RepresentationMapping(sources=("A2",), targets=("B2",)),
                ),
            )
        )
        registry.add(
            RepresentationMap(
                id="RM_AC",
                maintainer=Agency(id="ESTAT"),
                name={"en": "A to C"},
                sources=(_cl("CL_A"), MapDataType("String")),
                targets=(MapDataType("Integer"), _cl("CL_C")),
                mappings=(RepresentationMapping(sources=("A1", "free"), targets=("7", "C9")),),
            )
        )
        yield registry


# ── the roots ─────────────────────────────────────────────────────────────


def test_the_roots_discriminate(reg: sl.LocalRegistry) -> None:
    assert sorted(reg.query(q.representation_map).ids()) == ["RM_AB", "RM_AC"]
    assert reg.query(q.representation_mapping).count() == 3


def test_a_representation_map_collects_as_a_domain_object(reg: sl.LocalRegistry) -> None:
    found = reg.query(q.representation_map).filter(q.representation_map.id == "RM_AB").collect()
    assert type(found[0]).__name__ == "RepresentationMap"


def test_a_mapping_is_keyed_by_its_position(reg: sl.LocalRegistry) -> None:
    """`A1` is a source value in both maps, and a mapping has no id of its own,
    so the relation is keyed by map and position."""
    assert reg.query(q.representation_mapping).filter(q.representation_mapping.sources.has("A1")).count() == 2
    assert type(q.representation_mapping).__key__ == ("representation_map_urn", "ordinal")


# ── the endpoints ─────────────────────────────────────────────────────────


def test_the_two_sides_are_different_hops(reg: sl.LocalRegistry) -> None:
    """Both reach `sdmx.artefact` and mean different things. Collapsing them
    would answer this the same either way — it does not."""
    by_source = q.representation_map.sources.codelist.id
    by_target = q.representation_map.targets.codelist.id
    assert sorted(reg.query(q.representation_map).filter(by_source == "CL_A").ids()) == ["RM_AB", "RM_AC"]
    assert reg.query(q.representation_map).filter(by_source == "CL_C").ids() == []
    assert reg.query(q.representation_map).filter(by_target == "CL_C").ids() == ["RM_AC"]


def test_an_endpoint_reaches_the_codelists_own_columns(reg: sl.LocalRegistry) -> None:
    """The reason these are hops rather than raw URN columns."""
    frame = (
        reg.query(q.representation_map)
        .filter(q.representation_map.id == "RM_AB")
        .select(
            rm=q.representation_map.id,
            src=q.representation_map.sources.codelist.id.list(),
            tgt=q.representation_map.targets.codelist.name["en"].list(),
        )
        .collect()
    )
    assert frame.to_dicts() == [{"rm": "RM_AB", "src": ["CL_A"], "tgt": ["List CL_B"]}]


def test_the_endpoints_cross_the_extension(reg: sl.LocalRegistry) -> None:
    """The endpoint rows hang off the `representation_map` extension, not the
    spine — `targets` is already `metadata_set`'s hop into `artefact`, and a
    name resolves to the first edge carrying it."""
    assert q.representation_map.sources.codelist.id.path == ("representation_map", "sources", "codelist")
    assert q.representation_map.targets.data_type.path == ("representation_map", "targets")
    assert q.metadata_set.targets.path == ("targets",)


def test_an_endpoint_reaches_a_codelists_codes(reg: sl.LocalRegistry) -> None:
    """The endpoint is typed as `_Codelist`, not the bare maintainable, so a
    map reaches the codes it translates between."""
    found = reg.query(q.representation_map).filter(q.representation_map.sources.codelist.codes.id == "X")
    assert sorted(found.ids()) == ["RM_AB", "RM_AC"]


def test_a_data_type_endpoint_is_held(reg: sl.LocalRegistry) -> None:
    """Storage dropped a data-type endpoint until #422; its `codelist` is null."""
    assert reg.query(q.representation_map).filter(q.representation_map.targets.data_type == "Integer").ids() == [
        "RM_AC"
    ]
    frame = (
        reg.query(q.representation_map)
        .filter(q.representation_map.id == "RM_AC")
        .select(
            types=q.representation_map.sources.data_type.list(),
            positions=q.representation_map.sources.position.list(),
        )
        .collect()
    )
    (row,) = frame.to_dicts()
    assert sorted(zip(row["positions"], row["types"], strict=True), key=lambda p: p[0]) == [(0, None), (1, "String")]


# ── the mappings ──────────────────────────────────────────────────────────


def test_finding_the_map_that_translates_a_value(reg: sl.LocalRegistry) -> None:
    """The catalogue question, from the value's side."""
    mappings = q.representation_map.mappings
    assert sorted(reg.query(q.representation_map).filter(mappings.sources.has("A1")).ids()) == ["RM_AB", "RM_AC"]
    assert reg.query(q.representation_map).filter(mappings.targets.has("C9")).ids() == ["RM_AC"]


def test_a_mapping_reaches_its_map(reg: sl.LocalRegistry) -> None:
    mapping = q.representation_mapping
    assert reg.query(mapping).filter(mapping.representation_map.id == "RM_AC").count() == 1
    assert reg.query(mapping).filter(mapping.representation_map.id == "NOPE").count() == 0


def test_the_binder_groups_onto_one_mapping(reg: sl.LocalRegistry) -> None:
    """`RM_AB` maps A1 to B1 and A2 to B2, but no single mapping pairs A2 with
    B1 — the check a fan-out without `.any()` cannot make."""
    mapping = q.representation_mapping
    same = q.representation_map.mappings.any(mapping.sources.has("A2"), mapping.targets.has("B1"))
    assert reg.query(q.representation_map).filter(same).ids() == []
    both = q.representation_map.mappings.any(mapping.sources.has("A2"), mapping.targets.has("B2"))
    assert reg.query(q.representation_map).filter(both).ids() == ["RM_AB"]


# ── what storage carries ──────────────────────────────────────────────────


def test_every_mapping_is_persisted_whole(reg: sl.LocalRegistry) -> None:
    """Regex and index mappings, and a mapping with no target, all persist —
    `put_representation_map` wrote only the literal variant until #422, so a
    query saw fewer rows than the artefact declared."""
    rm = RepresentationMap(
        id="RM_RE",
        maintainer=Agency(id="ESTAT"),
        name={"en": "regex"},
        sources=(_cl("CL_A"),),
        targets=(_cl("CL_B"),),
        mappings=(
            RepresentationMapping(sources=("L1",), targets=("T1",)),
            RepresentationMapping(sources=(SourceValue("A(.*)", is_regex=True),), targets=(r"B\1",)),
            RepresentationMapping(sources=(SourceValue("XYZ", start_index=1, end_index=2),)),
        ),
    )
    reg.add(rm)
    mapping = q.representation_mapping
    assert reg.query(mapping).filter(mapping.representation_map.id == "RM_RE").count() == 3
    back = reg.get(RepresentationMap.urn_for(agency="ESTAT", id="RM_RE", version="1.0"))
    assert back is not None
    assert back.mappings == rm.mappings
