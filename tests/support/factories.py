"""Shared builders for domain objects used across the test suite.

Grew out of an audit that found 19 helper names defined in three or more test
modules, of which only one family was byte-identical across its definitions.
The rest had drifted — same name, subtly different artefact — which meant each
module was quietly testing against its own idea of a valid input.

Where two call sites genuinely need different artefacts, express that as an
argument rather than a second builder, so the difference is visible at the
call site instead of buried in a near-duplicate definition.

Test-only; not packaged with sdmxlib.
"""

from collections.abc import Sequence

import duckdb

from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import AttachmentLevel, Attribute, DataStructure, Dimension, Measure
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import DataType, Facet
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.storage.schema import create_schema


def code_ref(item_id: str, *, agency: str = "T", codelist_id: str = "CL", version: str = "1.0") -> Ref[Code]:
    """An unresolved `Ref` to a code: what a hierarchical code places (#492 requires one).

    Args:
        item_id: The code's id within the codelist.
        agency: Maintainer agency id of the codelist.
        codelist_id: Id of the codelist.
        version: Version of the codelist.

    Returns:
        An unresolved reference carrying the item URN.
    """
    return Ref[Code].unresolved(
        SdmxUrn(
            package="codelist",
            artefact_class="Code",
            agency=agency,
            id=codelist_id,
            version=version,
            item_id=item_id,
        )
    )


def concept_ref(item_id: str, *, agency: str = "T", scheme_id: str = "CS", version: str = "1.0") -> Ref[Concept]:
    """An unresolved `Ref` to a concept inside a concept scheme.

    The defaults reproduce the URN four test modules had each hard-coded
    identically — ``T:CS(1.0)``.

    Args:
        item_id: The concept's id within the scheme.
        agency: Maintainer agency id of the owning scheme.
        scheme_id: Id of the owning concept scheme.
        version: Version of the owning concept scheme.

    Returns:
        An unresolved reference carrying the item URN.
    """
    return Ref[Concept].unresolved(
        SdmxUrn(
            package="conceptscheme",
            artefact_class="Concept",
            agency=agency,
            id=scheme_id,
            version=version,
            item_id=item_id,
        )
    )


def minimal_dsd(
    *,
    id: str = "DSD_TEST",
    agency: str = "SDMX",
    version: str = "1.0",
    name: InternationalString | None = None,
) -> DataStructure:
    """A DataStructure with no components — identity fields only.

    For tests about persistence, lookup or referencing, where the DSD is a
    thing to store and retrieve rather than a shape to interpret.

    Args:
        id: Artefact id.
        agency: Maintainer agency id.
        version: Artefact version.
        name: Name; defaults to ``InternationalString(en="Test")``.

    Returns:
        The DataStructure.
    """
    return DataStructure(
        id=id,
        maintainer=agency,
        version=version,
        name=name if name is not None else InternationalString(en="Test"),
    )


FREQ_CODES_NAMED = (
    Code(id="A", name=InternationalString(en="Annual")),
    Code(id="Q", name=InternationalString(en="Quarterly")),
    Code(id="M", name=InternationalString(en="Monthly")),
)
"""FREQ codes carrying names — the SDMX-JSON writers emit ``name`` per value."""

FREQ_CODES_BARE = (Code(name={"en": "A"}, id="A"), Code(name={"en": "Q"}, id="Q"), Code(name={"en": "M"}, id="M"))
"""FREQ codes without names, so label lookup falls back to the code id."""

STATUS_CODES_BARE = (Code(name={"en": "A"}, id="A"), Code(name={"en": "P"}, id="P"))

STATUS_CODES_NAMED = (
    Code(id="A", name=InternationalString(en="Normal")),
    Code(id="P", name=InternationalString(en="Provisional")),
)


def data_dsd(
    *,
    freq_codes: Sequence[Code] = FREQ_CODES_NAMED,
    status_codes: Sequence[Code] | None = None,
) -> tuple[DataStructure, Dataflow]:
    """The canonical data-plane shape: ``FREQ`` x ``REF_AREA`` -> ``OBS_VALUE``.

    One fully enumerated dimension plus one free-text dimension, which is the
    typical real-world SDMX shape, and a Dataflow bound to it.

    The *codes* are the caller's business and the *shape* is shared. Three
    SDMX-JSON data test modules previously each defined this whole structure
    inline and disagreed about the codes — two names, three names, three
    unnamed — with nothing recording why. Passing them explicitly puts that
    disagreement on one visible line per module instead of inside three
    40-line near-duplicates.

    Args:
        freq_codes: Codes for ``CL_FREQ``.
        status_codes: Codes for ``CL_OBS_STATUS``. ``None`` omits the
            ``OBS_STATUS`` attribute entirely.

    Returns:
        ``(dsd, dataflow)``.
    """
    cl_freq = Codelist(
        id="CL_FREQ",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Frequency"),
        codes=list(freq_codes),
    )
    attributes: list[Attribute] = []
    if status_codes is not None:
        cl_status = Codelist(
            id="CL_OBS_STATUS",
            maintainer="TEST",
            version="1.0",
            name=InternationalString(en="Observation status"),
            codes=list(status_codes),
        )
        attributes.append(
            Attribute(
                attachment=AttachmentLevel.DATASET,
                id="OBS_STATUS",
                representation=Ref[Codelist].to(cl_status),
                concept=concept_ref("OBS_STATUS"),
            )
        )
    dsd = DataStructure(
        id="DSD_TEST",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Test DSD"),
        dimensions=[
            Dimension(id="FREQ", representation=Ref[Codelist].to(cl_freq), concept=concept_ref("FREQ")),
            Dimension(id="REF_AREA", concept=concept_ref("REF_AREA")),
        ],
        attributes=attributes,
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    flow = Dataflow(
        id="DF_TEST",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Test Dataflow"),
        structure=Ref[DataStructure].to(dsd),
    )
    return dsd, flow


def code_urn(
    codelist_id: str,
    code_id: str,
    *,
    agency: str = "SDMX",
    version: str = "1.0",
) -> SdmxUrn[Code]:
    """URN of a code inside a codelist — the convention the storage writers use.

    Six modules built this identically apart from the agency, which each had
    hard-coded to whichever fixture it grew up next to.

    Args:
        codelist_id: Id of the owning codelist.
        code_id: Id of the code within it.
        agency: Maintainer agency id of the codelist.
        version: Codelist version.

    Returns:
        The item-level URN.
    """
    return SdmxUrn.make(
        Code,
        package="codelist",
        artefact_class="Code",
        agency=agency,
        id=codelist_id,
        version=version,
        item_id=code_id,
    )


def memory_cursor() -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB connection with the sdmx schema created.

    Returns:
        The connection, ready for the storage writers.
    """
    return create_schema(duckdb.connect(":memory:"))


def dsd_binding(
    dsd: DataStructure,
    flow: Dataflow,
    *,
    schema: str = "data",
    table: str = "obs",
    dataflow_ref: Ref[Dataflow] | None = None,
) -> DataflowBinding:
    """An identity binding derived from *dsd* — the data-plane test default.

    Six modules defined this, four of them byte-identical. Distinct from the
    hand-built `DataflowBinding(...)` fixtures in the binding-, drift- and
    inspection-focused modules: those exist to exercise the binding itself
    (partition layouts, structural versions, sidecars) and are genuinely
    different artefacts, so they are deliberately not folded in here.

    Args:
        dsd: Structure to derive the column maps from.
        flow: Dataflow the binding is for.
        schema: Database schema holding the observations table.
        table: Observations table name.
        dataflow_ref: Override the dataflow ref — pass an unresolved one to
            exercise the not-yet-resolved path.

    Returns:
        The binding.
    """
    ref = dataflow_ref if dataflow_ref is not None else Ref[Dataflow].to(flow)
    return DataflowBinding.from_dsd(dsd, dataflow=ref, schema=schema, table=table)


def footnote_msd(
    *,
    name: str = "Footnote MSD",
    attribute_id: str = "FOOTNOTE_TEXT",
) -> MetadataStructure:
    """The minimal reference-metadata MSD: one free-text attribute.

    Seven modules built an ``MSD_FOOTNOTES`` for ``STATCAN``, differing only in
    which optional fields each happened to set — a name here, occurs bounds
    there, a concept ref, a description. This is the shared core. Modules
    needing a specific extra (a concept ref, a max_length, twenty annotated
    attributes) keep their own builder, because those differences are the
    point of those tests.

    Its attribute names a concept, because every format requires one and
    `Registry.add` refuses an attribute without (#419).

    Args:
        name: English name for the MSD.
        attribute_id: Id of the single metadata attribute.

    Returns:
        The MetadataStructure.
    """
    return MetadataStructure(
        id="MSD_FOOTNOTES",
        maintainer="STATCAN",
        version="1.0",
        name={"en": name},
        attributes=[
            MetadataAttribute(
                id=attribute_id, concept=concept_ref(attribute_id), representation=Facet(type=DataType.String)
            )
        ],
    )


OBSERVATION_ROWS: tuple[tuple[str, str | None, float], ...] = (
    ("A", "CA", 100.0),
    ("A", "US", 200.0),
    ("Q", "CA", 50.0),
    ("M", "US", 25.0),
    ("M", None, 25.0),
)
"""Default ``data.observations`` rows, ordered so prefixes are meaningful.

The last row's NULL ``REF_AREA`` is deliberate — it is what the availability
builders use to check that a missing dimension value is excluded rather than
reported as a code. Callers wanting the simpler shape slice this tuple, which
makes "no NULL row here" a visible choice rather than an absent line.
"""


def build_observations(
    local: LocalRegistry,
    *,
    rows: Sequence[tuple[str, str | None, float]] = OBSERVATION_ROWS,
) -> None:
    """Create and populate a ``data.observations`` table in *local*'s DuckDB.

    Four modules created this table identically and then inserted three, four
    or five rows, with nothing marking the difference as intentional.

    Args:
        local: LocalRegistry whose connection holds the table.
        rows: ``(FREQ, REF_AREA, OBS_VALUE)`` triples. ``REF_AREA`` may be
            None.
    """
    cur = local.connection.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS data")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS data.observations (
            FREQ TEXT,
            REF_AREA TEXT,
            OBS_VALUE DOUBLE
        )
    """)
    cur.execute("DELETE FROM data.observations")
    cur.executemany("INSERT INTO data.observations VALUES (?, ?, ?)", [list(r) for r in rows])


def footnote_metadataflow(
    msd: MetadataStructure | None = None,
    *,
    name: str | None = "Footnote flow",
) -> Metadataflow:
    """The metadataflow wrapping `footnote_msd`.

    Args:
        msd: Structure to wrap; defaults to a fresh `footnote_msd`.
        name: English name, or None to omit it entirely (some wire tests
            check the absent-name path).

    Returns:
        The Metadataflow.
    """
    structure = msd if msd is not None else footnote_msd()
    if name is None:
        # Two branches rather than **kwargs: an untyped kwargs dict would need a
        # suppression to reach a typed constructor, and this is only one field.
        return Metadataflow(
            name={"en": "MDF_FOOTNOTES"}, id="MDF_FOOTNOTES", maintainer="STATCAN", version="1.0", structure=structure
        )
    return Metadataflow(id="MDF_FOOTNOTES", maintainer="STATCAN", version="1.0", name={"en": name}, structure=structure)


def federated_catalog(local: LocalRegistry) -> LocalRegistry:
    """The registry itself — kept as a name while call sites migrate.

    `FederatedCatalog` composed three roles and this factory passed the *same*
    registry for all three, which is the clearest evidence the composition had
    nothing to compose: a registry holds artefacts, holds the bindings that are
    registrations over them, and owns the store their rows live in.

    Args:
        local: The registry.

    Returns:
        It, unchanged.
    """
    return local
