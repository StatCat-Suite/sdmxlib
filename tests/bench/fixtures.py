"""Synthetic StructureMessage builders for benchmarks.

Each builder produces a deterministic, in-memory message of a parameterised
size. No XML parsing involved — model objects assembled directly. Use
``serialize()`` from the format layer to get bytes for parse benchmarks.
"""

from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ArtefactList, ItemList
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn

_AGENCY = "BENCH"


def make_codelist(cl_id: str, n_codes: int) -> Codelist:
    """A Codelist of *n_codes* codes with bilingual names."""
    codes = [
        Code(
            id=f"C{i:04d}",
            name={"en": f"Code {i}", "fr": f"Code {i} (fr)"},
        )
        for i in range(n_codes)
    ]
    return Codelist(
        id=cl_id,
        maintainer=Agency(id=_AGENCY),
        version="1.0",
        name={"en": cl_id, "fr": f"{cl_id} (fr)"},
        codes=ItemList(codes),
    )


def make_concept_scheme(cs_id: str, concept_ids: list[str]) -> ConceptScheme:
    return ConceptScheme(
        id=cs_id,
        maintainer=Agency(id=_AGENCY),
        version="1.0",
        name={"en": cs_id},
        concepts=ItemList([Concept(id=cid, name={"en": cid}) for cid in concept_ids]),
    )


def _measure_concept(dsd_id: str) -> SdmxUrn[Concept]:
    """The measure's ConceptIdentity — required by every wire format."""
    return SdmxUrn(
        package="conceptscheme",
        artefact_class="Concept",
        agency=_AGENCY,
        id=f"CS_{dsd_id}",
        version="1.0",
        item_id="OBS_VALUE",
    )


def make_dsd(dsd_id: str, n_dims: int, codes_per_dim: int) -> tuple[DataStructure, list[Codelist], ConceptScheme]:
    """A DSD with *n_dims* enumerated dimensions, each backed by a fresh codelist.

    Returns the DSD along with its codelists and concept scheme so the
    caller can put them all into one StructureMessage.
    """
    codelists = [make_codelist(f"CL_{dsd_id}_D{i:02d}", codes_per_dim) for i in range(n_dims)]
    concept_ids = [*(f"D{i:02d}" for i in range(n_dims)), "OBS_VALUE"]
    cs = make_concept_scheme(f"CS_{dsd_id}", concept_ids)

    dims = []
    for i in range(n_dims):
        concept_urn: SdmxUrn[Concept] = SdmxUrn(
            package="conceptscheme",
            artefact_class="Concept",
            agency=_AGENCY,
            id=f"CS_{dsd_id}",
            version="1.0",
            item_id=f"D{i:02d}",
        )
        codelist_urn: SdmxUrn[Codelist] = SdmxUrn(
            package="codelist",
            artefact_class="Codelist",
            agency=_AGENCY,
            id=f"CL_{dsd_id}_D{i:02d}",
            version="1.0",
        )
        dims.append(
            Dimension(
                id=f"D{i:02d}",
                concept=Ref[Concept].unresolved(concept_urn),
                representation=Ref[Codelist].unresolved(codelist_urn),
            )
        )
    dsd = DataStructure(
        id=dsd_id,
        maintainer=Agency(id=_AGENCY),
        version="1.0",
        name={"en": dsd_id},
        dimensions=ItemList(dims),
        # SDMX-ML 2.1 requires a <PrimaryMeasure>, so a measure-less DSD has no
        # 2.1 representation and `serialize` refuses it (#319). The benchmarks
        # serialise to 2.1, so without this every parse/serialize/storage case
        # errored on the fixture rather than measuring anything (#269).
        measures=ItemList([Measure(id="OBS_VALUE", concept=Ref[Concept].unresolved(_measure_concept(dsd_id)))]),
    )
    return dsd, codelists, cs


def make_message(*, n_dataflows: int, n_dims: int, codes_per_dim: int) -> StructureMessage:
    """A StructureMessage carrying *n_dataflows* dataflows.

    Each dataflow points at a shared DSD; all artefacts share one concept
    scheme and one codelist-per-dimension family. Sized for parse /
    serialize / registry-build benchmarks.
    """
    dsd, codelists, cs = make_dsd("DSD_BENCH", n_dims=n_dims, codes_per_dim=codes_per_dim)
    dsd_ref: Ref[DataStructure] = Ref.unresolved(
        SdmxUrn(
            package="datastructure",
            artefact_class="DataStructure",
            agency=_AGENCY,
            id="DSD_BENCH",
            version="1.0",
        )
    )
    dataflows = [
        Dataflow(
            id=f"DF_{i:05d}",
            maintainer=Agency(id=_AGENCY),
            version="1.0",
            name={"en": f"Dataflow {i}"},
            structure=dsd_ref,
        )
        for i in range(n_dataflows)
    ]
    return StructureMessage(
        codelists=ArtefactList(codelists),
        concept_schemes=ArtefactList([cs]),
        data_structures=ArtefactList([dsd]),
        dataflows=ArtefactList(dataflows),
    )
