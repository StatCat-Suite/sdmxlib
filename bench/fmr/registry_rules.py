"""Record the registry rules FMR enforces beyond the XSD, for the migration's FMR target (GH-407).

Loading a .Stat space into FMR 12.5.0 (GH-399) showed four behaviours the
oracle's documents do not exercise. Each is recorded here against a small
message of its own, so the replay test (`test_fmr_registry_rules.py`) and the
migration's FMR rules rest on what FMR did rather than on a note:

- an `Actual` DataConstraint is refused;
- a later final version that removes codes from the one before is refused,
  and one that only adds codes is accepted;
- an attribute's related dimensions come back in the DSD's order;
- a metadata attribute's ``isMultiLingual="false"`` comes back true;
- a code whose parent is not an NCName (``1``, ``_T``) is accepted in
  SDMX-ML 3.0.0, which the XSD forbids, and served back with its parent.

Writes ``<version>/registry-rules/`` beside the oracle's recordings: each
document sent, FMR's outcome for it, and what FMR then serves.
"""

import json
from pathlib import Path

from bench.fmr import documents
from bench.fmr.record import FORMATS, RECORDINGS, Fmr, outcome
from sdmxlib.formats import to_xml
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.constraint import ConstraintAttachment, CubeRegion, DataConstraint, KeyValue
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import AttachmentLevel, Attribute, DataStructure, Dimension, Measure
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet

_AGENCY = "AG"


def base() -> StructureMessage:
    """A DSD whose attribute relates to its dimensions out of order, its flow, and an MSD with a monolingual facet."""
    concepts = ConceptScheme(
        id="CS",
        maintainer=_AGENCY,
        version="1.0.0",
        name={"en": "concepts"},
        concepts=[Concept(id=c, name={"en": c}) for c in ("D1", "D2", "A1", "OBS", "MA")],
    )
    codes = Codelist(
        id="CL",
        maintainer=_AGENCY,
        version="1.0.0",
        name={"en": "codes"},
        codes=[Code(id=c, name={"en": c}) for c in "XY"],
    )
    dsd = DataStructure(
        id="DSD",
        maintainer=_AGENCY,
        version="1.0.0",
        name={"en": "dsd"},
        dimensions=[Dimension(id=d, concept=concepts.ref_to(d), representation=Ref.to(codes)) for d in ("D1", "D2")],
        attributes=[
            Attribute(
                id="A1",
                concept=concepts.ref_to("A1"),
                attachment=AttachmentLevel.SERIES,
                related_dimensions=["D2", "D1"],
            )
        ],
        measures=[Measure(id="OBS", concept=concepts.ref_to("OBS"), representation=Facet(type="Double"))],
    )
    flow = Dataflow(id="DF", maintainer=_AGENCY, version="1.0.0", name={"en": "flow"}, structure=Ref.to(dsd))
    msd = MetadataStructure(
        id="MSD",
        maintainer=_AGENCY,
        version="1.0.0",
        name={"en": "msd"},
        attributes=[
            MetadataAttribute(
                id="MA", concept=concepts.ref_to("MA"), representation=Facet(type="String", is_multilingual=False)
            )
        ],
    )
    return StructureMessage(
        concept_schemes=[concepts],
        codelists=[codes],
        data_structures=[dsd],
        dataflows=[flow],
        metadata_structures=[msd],
    )


def actual(base: StructureMessage) -> StructureMessage:
    """An Actual constraint on the base's flow."""
    (flow,) = base.dataflows
    constraint = DataConstraint(
        id="CC_ACTUAL",
        maintainer=_AGENCY,
        version="1.0.0",
        name={"en": "actual"},
        constraint_type="Actual",
        attachment=ConstraintAttachment(dataflows=[Ref.to(flow)]),
        regions=[CubeRegion(keys=[KeyValue(dimension_id="D1", values=["X"])])],
    )
    return StructureMessage(constraints=[constraint])


def _codelist(version: str, codes: str) -> StructureMessage:
    return StructureMessage(
        codelists=[
            Codelist(
                id="CL_V",
                maintainer=_AGENCY,
                version=version,
                name={"en": "versioned"},
                codes=[Code(id=c, name={"en": c}) for c in codes],
            )
        ]
    )


def _parented() -> StructureMessage:
    """Codes whose parents are not NCNames, `1` and SDMX's `_T`, which the 3.0.0 XSD cannot name (GH-398)."""
    codes = [
        Code(id="_T", name={"en": "total"}),
        Code(id="1", name={"en": "one"}, parent_id="_T"),
        Code(id="2", name={"en": "two"}, parent_id="1"),
    ]
    return StructureMessage(
        codelists=[Codelist(id="CL_P", maintainer=_AGENCY, version="1.0.0", name={"en": "parents"}, codes=codes)]
    )


def steps() -> list[tuple[str, bytes]]:
    """Each step's name and the document it submits, in order."""
    built = base()
    return [
        ("base", to_xml(built, version="3.0")),
        ("actual", to_xml(actual(built), version="3.0", validate=False)),
        ("codelist-1.0.0", to_xml(_codelist("1.0.0", "AB"), version="3.0")),
        ("codelist-1.1.0-removes", to_xml(_codelist("1.1.0", "A"), version="3.0")),
        ("codelist-1.1.0-adds", to_xml(_codelist("1.1.0", "ABC"), version="3.0")),
        ("codelist-non-ncname-parents", to_xml(_parented(), version="3.0", allow={"non-ncname-parent"})),
    ]


def _write(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content)
    else:
        path.write_bytes(content)


def record(url: str, auth: tuple[str, str]) -> Path:
    """Record each step against the FMR at `url`; the directory written."""
    fmr = Fmr(url, auth)
    out = RECORDINGS / fmr.version() / "registry-rules"
    planned = steps()
    setup = documents.setup([body for _, body in planned])
    agencies = [_AGENCY]
    fmr.wipe(agencies)
    fmr.submit(setup).raise_for_status()
    _write(out / "setup.xml", setup)
    outcomes: dict[str, dict[str, object]] = {}
    for name, body in planned:
        _write(out / f"{name}.xml", body)
        outcomes[name] = outcome(fmr.submit(body))
    _write(out / "outcomes.json", json.dumps(outcomes, indent=2, sort_keys=True) + "\n")
    served = fmr.fetch(_AGENCY, FORMATS["ml30"][0])
    _write(out / "back" / f"{_AGENCY}-ml30.xml", served.content)
    fmr.wipe(agencies)
    return out
