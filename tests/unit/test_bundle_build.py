"""`build_bundle`: a dataflow's closure and its observations into a new bundle, codelists cut (GH-223).

The source is a published bundle holding a dataflow `F` over a data structure
whose `GEO` dimension a hierarchical codelist enumerates, an allowed
constraint on `F`, and artefacts outside `F`'s closure. A `RestRegistry`
source is covered by the FMR cassettes in
`tests/integration/test_bundle_fetch_live.py`.
"""

from pathlib import Path

import polars as pl
import pytest

import sdmxlib as sl
from sdmxlib.bundle import ArtefactEntry, Producer, build_bundle, check_bundle, read_manifest, write_manifest
from sdmxlib.model.codelist import CascadeSelection
from sdmxlib.model.constraint import ConstraintAttachment, CubeRegion, KeyValue, RegionValue
from tests.support.factories import code_ref, concept_ref

pytestmark = pytest.mark.unit

_PRODUCER = Producer(name="test-builder", version="1.0")

GEO = sl.Codelist(
    id="CL",
    maintainer="T",
    version="1.0",
    name={"en": "geo"},
    codes=[
        sl.Code(id="A", name={"en": "A"}),
        *(sl.Code(id=f"A{n}", name={"en": f"A{n}"}, parent_id="A") for n in range(1, 10)),
        sl.Code(id="B", name={"en": "B"}),
        *(sl.Code(id=f"B{n}", name={"en": f"B{n}"}, parent_id="B") for n in range(1, 9)),
    ],
)
OTHER = sl.Codelist(
    id="CL_OTHER", maintainer="T", version="1.0", name={"en": "o"}, codes=[sl.Code(id="X", name={"en": "X"})]
)
CONCEPTS = sl.ConceptScheme(
    id="CS",
    maintainer="T",
    version="1.0",
    name={"en": "concepts"},
    concepts=[sl.Concept(id=c, name={"en": c}) for c in ("GEO", "OBS_VALUE")],
)
DSD = sl.DataStructure(
    id="DSD",
    maintainer="T",
    version="1.0",
    name={"en": "dsd"},
    dimensions=[sl.Dimension(id="GEO", concept=concept_ref("GEO"), representation=sl.Ref.to(GEO))],
    measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
)
FLOW = sl.Dataflow(id="F", maintainer="T", version="1.0", name={"en": "F"}, structure=sl.Ref.to(DSD))
SIBLING = sl.Dataflow(id="G", maintainer="T", version="1.0", name={"en": "G"}, structure=sl.Ref.to(DSD))
FLOW_URN = str(FLOW.urn)
GEO_URN = str(GEO.urn)


def _constraint(*values: RegionValue) -> sl.DataConstraint:
    return sl.DataConstraint(
        id="CC",
        maintainer="T",
        version="1.0",
        name={"en": "cc"},
        attachment=ConstraintAttachment(dataflows=(sl.Ref.to(FLOW),)),
        regions=(CubeRegion(keys=(KeyValue(dimension_id="GEO", values=frozenset(values)),)),),
    )


def _hierarchy() -> tuple[sl.Hierarchy, sl.HierarchyAssociation]:
    tree = [
        sl.HierarchicalCode(id="A", code=code_ref("A"), children=[sl.HierarchicalCode(id="A1", code=code_ref("A1"))])
    ]
    hierarchy = sl.Hierarchy(id="H", maintainer="T", version="1.0", name={"en": "h"}, tree=tree)
    association = sl.HierarchyAssociation(
        id="HA",
        maintainer="T",
        version="1.0",
        name={"en": "ha"},
        linked_hierarchy=sl.Ref.to(hierarchy),
        linked_object=sl.Ref[sl.Dimension].unresolved(
            sl.SdmxUrn.make(
                sl.Dimension,
                package="datastructure",
                artefact_class="Dimension",
                agency="T",
                id="DSD",
                version="1.0",
                item_id="GEO",
            )
        ),
    )
    return hierarchy, association


def _source(root: Path, *extra: sl.MaintainableArtefact) -> Path:
    """A published bundle holding `F`'s structures, `extra`, and artefacts outside `F`'s closure."""
    root.mkdir(parents=True)
    with sl.LocalRegistry.build(root) as reg:
        reg.add_all([CONCEPTS, GEO, OTHER, DSD, FLOW, SIBLING, *extra])
        publication = reg.publication
    write_manifest(publication, bundle_id="source", producer=_PRODUCER)
    return publication


def _build(source: Path, path: Path, geo: list[str] | None, *, partials: bool = True) -> Path:
    observations = None if geo is None else {FLOW_URN: pl.DataFrame({"GEO": geo, "OBS_VALUE": [1.0] * len(geo)})}
    with sl.LocalRegistry.bundle(source) as reg:
        manifest = build_bundle(
            reg, [FLOW.urn], path, bundle_id="product", producer=_PRODUCER, observations=observations, partials=partials
        )
    return path / manifest.token


def _codes(publication: Path) -> list[str]:
    with sl.LocalRegistry.bundle(publication) as reg:
        codelist = reg.get(GEO.urn, eager=True)
        assert isinstance(codelist, sl.Codelist)
        return sorted(code.id for code in codelist.codes)


def _entry(publication: Path, urn: str) -> ArtefactEntry:
    (entry,) = (a for a in read_manifest(publication).artefacts if a.urn == urn)
    return entry


def test_the_closure_holds_the_constraint_and_leaves_out_what_it_does_not_reach(tmp_path: Path) -> None:
    constraint = _constraint(RegionValue("A1"))
    publication = _build(_source(tmp_path / "s", constraint), tmp_path / "p", ["A1"])

    held = {a.urn for a in read_manifest(publication).artefacts}
    assert str(constraint.urn) in held
    assert str(OTHER.urn) not in held, "a codelist nothing in the closure names"
    assert str(SIBLING.urn) not in held, "another dataflow of the same data structure"
    assert check_bundle(publication, at="publish").ok


def test_a_codelist_is_cut_to_the_codes_observed_and_their_ancestors(tmp_path: Path) -> None:
    source = _source(tmp_path / "s")
    publication = _build(source, tmp_path / "p", ["A1", "A2", "B3"])

    assert _codes(publication) == ["A", "A1", "A2", "B", "B3"]
    entry = _entry(publication, GEO_URN)
    assert entry.partial
    assert entry.cut_from == _entry(source, GEO_URN).fingerprint
    report = check_bundle(publication, at="publish", structures=source)
    assert report.ok, "\n".join(map(str, report.findings))


def test_codes_an_allowed_constraint_names_are_kept(tmp_path: Path) -> None:
    publication = _build(_source(tmp_path / "s", _constraint(RegionValue("B2"))), tmp_path / "p", ["A1"])

    assert _codes(publication) == ["A", "A1", "B", "B2"]


def test_a_cascading_constraint_keeps_the_descendants(tmp_path: Path) -> None:
    cascade = RegionValue("B", cascade=CascadeSelection.INCLUDE_DESCENDANTS)
    publication = _build(_source(tmp_path / "s", _constraint(cascade)), tmp_path / "p", ["A1"])

    assert _codes(publication) == ["A", "A1", "B", *(f"B{n}" for n in range(1, 9))]


def test_a_codelist_a_hierarchy_places_stays_full(tmp_path: Path) -> None:
    """SDMX has no partial hierarchy, so the codelist under one is not cut."""
    publication = _build(_source(tmp_path / "s", *_hierarchy()), tmp_path / "p", ["A1"])

    assert len(_codes(publication)) == len(GEO.codes)
    entry = _entry(publication, GEO_URN)
    assert (entry.partial, entry.cut_from) == (False, None)
    assert check_bundle(publication, at="publish").ok


def test_without_partials_every_codelist_stays_full(tmp_path: Path) -> None:
    publication = _build(_source(tmp_path / "s"), tmp_path / "p", ["A1"], partials=False)

    assert len(_codes(publication)) == len(GEO.codes)


def test_a_dataflow_without_observations_keeps_full_codelists(tmp_path: Path) -> None:
    publication = _build(_source(tmp_path / "s"), tmp_path / "p", None)

    assert len(_codes(publication)) == len(GEO.codes)
    assert read_manifest(publication).bindings == ()


def test_a_value_outside_the_codelist_is_refused_with_the_dimension_named(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"GEO holds C, which are not codes of .*Codelist=T:CL\(1\.0\)"):
        _build(_source(tmp_path / "s"), tmp_path / "p", ["A1", "C"])


def test_observations_for_a_dataflow_not_built_are_refused(tmp_path: Path) -> None:
    source = _source(tmp_path / "s")
    with sl.LocalRegistry.bundle(source) as reg, pytest.raises(ValueError, match=r"observations name .*G\(1\.0\)"):
        build_bundle(
            reg,
            [FLOW.urn],
            tmp_path / "p",
            bundle_id="p",
            producer=_PRODUCER,
            observations={str(SIBLING.urn): pl.DataFrame({"GEO": ["A"], "OBS_VALUE": [1.0]})},
        )


def test_a_path_that_is_not_empty_is_refused(tmp_path: Path) -> None:
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "x").write_text("x")
    with pytest.raises(FileExistsError, match="not empty"):
        _build(_source(tmp_path / "s"), tmp_path / "p", ["A1"])
