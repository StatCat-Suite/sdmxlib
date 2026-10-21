"""Synthesise the DSD↔MSD reference-metadata association from the lake.

Two composable, framework-free functions:

* `build_dsd_metadata_map` reads the lake and derives, per DataStructure,
  the MetadataStructures (MSDs) whose reference metadata describes a
  dataflow backed by that DSD.
* `annotate_structure_message` is a *pure* projection: it splices a
  ``type="METADATA"`` annotation (title = MSD URN) onto each DSD in a
  `StructureMessage`, the discovery convention the ``.Stat Suite`` Data
  Explorer reads (``getMSDIdentifiers`` — first METADATA-typed annotation,
  agency/id/version regex-extracted from the title).

Why a *projection*, not persisted DSD content
---------------------------------------------
In native SDMX 3.0 the DSD↔MSD relationship is structural — it lives on
the Metadataflow/MetadataProvisionAgreement (their ``targets``) and is
discovered via dynamically-generated ``links``, not baked into the DSD.
FMR emits those links at export time; it never mutates the stored DSD.
We follow that model: the lake keeps the native structures as the single
source of truth, and this builder derives the association on demand.

The ``type="METADATA"`` annotation is therefore a **legacy ``.Stat``
compatibility shim**, emitted only because every ``.Stat`` component
(DLM/NSI/Explorer) predates and cannot consume the SDMX 3.0 metadata
``links`` mechanism. It is deprecated-on-arrival: once consumers move to
FMR / native 3.0 links, `annotate_structure_message` can be dropped
without touching the derivation. See issue #162.

Because all three structure writers already serialise ``dsd.annotations``
(SDMX-JSON 2.0, SDMX-ML 2.1 / 3.0), the projection needs no writer
changes — it only has to place the annotation on the DSD before write.

Derivation sources
------------------
The association is derived from two sources, unioned:

* **populated ``MetadataSet.targets``** — the concrete reports filed against
  a dataflow; and
* **declared ``Metadataflow`` / ``MetadataProvisionAgreement`` targets** —
  the structural statement of what a metadata product may describe,
  including wildcarded ``WildcardUrnType`` references (e.g.
  ``Dataflow=ESTAT:CPI(*)``), which are expanded against the lake's concrete
  dataflow / DSD URNs.

Native SDMX 3.0 reference-metadata ``links`` (the FMR discovery mechanism
that supersedes this annotation) remain future work — see #191.

The producer side (bundle-build) and consumer side (a downstream SDMX REST
service, which can then delete its hand-rolled post-serialise splice) call
the same functions, so both emit identical annotations.

Example:
    import sdmxlib as sl
    from sdmxlib.builders.metadata_annotation import (
        build_dsd_metadata_map,
        annotate_structure_message,
    )

    with sl.LocalRegistry.bundle(bundle_uri) as reg:
        md_map = build_dsd_metadata_map(cat)
        msg = annotate_structure_message(msg, md_map)   # msg: StructureMessage
"""

import duckdb
from attrs import evolve, frozen

from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import artefact_urn
from sdmxlib.model.urn import SdmxUrn

#: Annotation ``type`` the ``.Stat`` Data Explorer keys on.
METADATA_ANNOTATION_TYPE = "METADATA"

#: MSD id suffix the Explorer prefers when several MSDs describe one DSD
#: (its ``R.find`` takes the first METADATA annotation; ``DATAFLOW_INFO`` is
#: the descriptive one). This ordering is an Explorer convention, not SDMX.
_DATAFLOW_INFO_SUFFIX = "_DATAFLOW_INFO"


@frozen
class DsdMetadataLink:
    """One derived association between a DSD and a describing MSD.

    ``msd_urn`` is the annotation title (and the record of interest). The
    remaining fields carry the metadataflow / provision / set URNs behind
    the association so a future native SDMX 3.0 ``links`` projection can
    build hrefs from the very same derivation, without re-querying.
    """

    msd_urn: str
    metadataflow_urn: str | None = None
    provision_urn: str | None = None
    set_urns: tuple[str, ...] = ()


# The map is assembled from a fixed, small number of queries — never one
# per DSD or per target. ``NUM_QUERIES`` pins that invariant for the guard
# test (see docs/design/caching.md: assert the count of round-trips).
NUM_QUERIES = 3

# Path 1 — populated MetadataSet.targets. The MSD sits one hop behind a
# Metadataflow-backed set and two hops behind an MPA-backed one (MPA →
# Metadataflow → MSD); both resolved with LEFT JOINs + COALESCE.
# ``structure_kind`` is the set's structure-ref artefact class, exactly as
# ``local.registry._msd_urn_behind`` branches on it.
_POPULATED_TARGET_SQL = """
SELECT
    df.dsd_urn                                       AS dsd_urn,
    COALESCE(mf_direct.msd_urn, mf_via_mpa.msd_urn)  AS msd_urn,
    COALESCE(mf_direct.urn, mf_via_mpa.urn)          AS metadataflow_urn,
    mpa.urn                                          AS provision_urn,
    ms.urn                                           AS set_urn
FROM sdmx.dataflow df
JOIN sdmx.metadata_target mt ON mt.target_urn = df.urn
JOIN sdmx.metadata_set ms    ON ms.urn = mt.set_urn
LEFT JOIN sdmx.metadataflow mf_direct
       ON ms.structure_kind = 'Metadataflow'
      AND mf_direct.urn = ms.structure_urn
LEFT JOIN sdmx.metadata_provision_agreement mpa
       ON ms.structure_kind = 'MetadataProvisionAgreement'
      AND mpa.urn = ms.structure_urn
LEFT JOIN sdmx.metadataflow mf_via_mpa
       ON mf_via_mpa.urn = mpa.metadataflow_urn
ORDER BY dsd_urn, msd_urn, set_urn
"""

# Path 2 — declared (structural) Metadataflow / MPA targets, which may be
# wildcarded, so the target→DSD expansion happens in Python (see
# _expand_declared_target), not SQL. Same MSD-behind-the-structure logic.
_DECLARED_TARGET_SQL = """
SELECT
    dt.target_urn                                    AS target_urn,
    COALESCE(mf_direct.msd_urn, mf_via_mpa.msd_urn)  AS msd_urn,
    COALESCE(mf_direct.urn, mf_via_mpa.urn)          AS metadataflow_urn,
    mpa.urn                                          AS provision_urn
FROM sdmx.metadata_declared_target dt
LEFT JOIN sdmx.metadataflow mf_direct
       ON mf_direct.urn = dt.source_urn
LEFT JOIN sdmx.metadata_provision_agreement mpa
       ON mpa.urn = dt.source_urn
LEFT JOIN sdmx.metadataflow mf_via_mpa
       ON mf_via_mpa.urn = mpa.metadataflow_urn
ORDER BY dt.source_urn, dt.ordinal
"""


def _msd_id(msd_urn: str) -> str:
    """The MSD's id from its URN, or the raw URN if it won't parse."""
    try:
        return SdmxUrn.parse(msd_urn).id
    except ValueError:
        return msd_urn


def _dataflow_info_first(msd_urn: str) -> tuple[int, str]:
    """Sort key placing ``*_DATAFLOW_INFO`` MSDs first, then by URN."""
    is_info = _msd_id(msd_urn).endswith(_DATAFLOW_INFO_SUFFIX)
    return (0 if is_info else 1, msd_urn)


def _wild_eq(pattern: str, value: str) -> bool:
    """A URN component matches when it is ``*`` (wildcard) or equal."""
    return pattern in ("*", value)


def _urn_matches(pattern: SdmxUrn[object], candidate: SdmxUrn[object]) -> bool:
    """True if a (possibly wildcarded) target URN matches a concrete one."""
    return (
        pattern.artefact_class == candidate.artefact_class
        and _wild_eq(pattern.agency, candidate.agency)
        and _wild_eq(pattern.id, candidate.id)
        and _wild_eq(pattern.version, candidate.version)
    )


def _expand_declared_target(
    target_urn: str,
    flow_candidates: list[tuple[SdmxUrn[object], str]],
    dsd_candidates: list[tuple[SdmxUrn[object], str]],
) -> list[str]:
    """Resolve a declared target to the DSD URN(s) it identifies.

    ``Dataflow`` targets map through the dataflow's ``dsd_urn``; ``DataStructure``
    targets identify a DSD directly. Both accept ``WildcardUrnType`` patterns
    (``*`` in agency / id / version). Any other artefact class is irrelevant to
    a DSD-level annotation and yields nothing.
    """
    try:
        pattern = SdmxUrn.parse(target_urn)
    except ValueError:
        return []
    if pattern.artefact_class == "Dataflow":
        return [dsd_urn for flow_urn, dsd_urn in flow_candidates if _urn_matches(pattern, flow_urn)]
    if pattern.artefact_class == "DataStructure":
        return [dsd_urn for dsd_parsed, dsd_urn in dsd_candidates if _urn_matches(pattern, dsd_parsed)]
    return []


def _accumulate(
    grouped: dict[str, dict[str, DsdMetadataLink]],
    dsd_urn: str,
    msd_urn: str,
    metadataflow_urn: str | None,
    provision_urn: str | None,
    set_urn: str | None,
) -> None:
    """Merge one derived edge into the ``(dsd_urn, msd_urn)`` grouping."""
    by_msd = grouped.setdefault(dsd_urn, {})
    existing = by_msd.get(msd_urn)
    prior = existing.set_urns if existing else ()
    set_urns = (*prior, set_urn) if set_urn and set_urn not in prior else prior
    by_msd[msd_urn] = DsdMetadataLink(
        msd_urn=msd_urn,
        metadataflow_urn=metadataflow_urn or (existing.metadataflow_urn if existing else None),
        provision_urn=provision_urn or (existing.provision_urn if existing else None),
        set_urns=set_urns,
    )


def _resolve_conn(
    registry: LocalRegistry,
    conn: duckdb.DuckDBPyConnection | None,
) -> duckdb.DuckDBPyConnection:
    """The explicit ``conn`` or the registry's own DuckDB connection."""
    if conn is not None:
        return conn
    # Duck-typed probe for a DuckDB-backed store; annotate at the boundary so
    # the Any stops here instead of riding out through the return.
    resolved: duckdb.DuckDBPyConnection | None = registry.connection
    if resolved is None:
        err = (
            "conn is required when the registry does not expose a "
            "DuckDB `connection` (got "
            f"{type(registry).__name__}). Pass conn=<duckdb connection> explicitly."
        )
        raise TypeError(err)
    return resolved


def build_dsd_metadata_map(
    registry: LocalRegistry,
    *,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> dict[str, list[DsdMetadataLink]]:
    """Derive ``dsd_urn -> [DsdMetadataLink]`` from the metadata graph.

    Unions two sources, deduped by ``(dsd_urn, msd_urn)``:

    * **Populated** ``MetadataSet.targets`` — concrete reports filed against a
      dataflow (``dataflow → metadata_target → metadata_set → structure →
      MSD``).
    * **Declared** ``Metadataflow`` / ``MetadataProvisionAgreement`` targets —
      the structural statement of what a metadata product may describe. These
      may be wildcarded (``Dataflow=ESTAT:CPI(*)``); wildcard expansion against
      the lake's concrete dataflow / DSD URNs happens in Python.

    Runs a fixed `NUM_QUERIES` round-trips regardless of how many DSDs or
    targets exist. A DSD with no metadata associated with it (by either path)
    is absent from the map; its MSDs are ordered ``*_DATAFLOW_INFO`` first.

    Args:
        registry: An open `LocalRegistry`. Used for its DuckDB connection
            when ``conn`` is omitted.
        conn: DuckDB connection to query. Defaults to the registry's own
            connection; pass an explicit
            connection when the data store is not DuckDB-backed.

    Returns:
        Mapping of DSD URN → the MSD associations describing it, empty when
        nothing associates metadata with any dataflow / DSD.

    Raises:
        TypeError: If ``conn`` is omitted and the registry cannot
            not expose a DuckDB ``connection``.
    """
    conn = _resolve_conn(registry, conn)

    grouped: dict[str, dict[str, DsdMetadataLink]] = {}

    # Path 1: populated set targets — dsd_urn is resolved in SQL.
    for dsd_urn, msd_urn, metadataflow_urn, provision_urn, set_urn in conn.execute(_POPULATED_TARGET_SQL).fetchall():
        if not dsd_urn or not msd_urn:  # unresolved MSD hop → skip
            continue
        _accumulate(grouped, dsd_urn, msd_urn, metadataflow_urn, provision_urn, set_urn)

    # Path 2: declared targets — expand (possibly wildcarded) target URNs
    # against the lake's concrete dataflow / DSD URNs (fetched once).
    dataflow_rows = conn.execute("SELECT urn, dsd_urn FROM sdmx.dataflow").fetchall()
    flow_candidates = [(SdmxUrn.parse(flow_urn), dsd_urn) for flow_urn, dsd_urn in dataflow_rows if dsd_urn]
    dsd_candidates = list({dsd_urn for _, dsd_urn in dataflow_rows if dsd_urn})
    dsd_parsed = [(SdmxUrn.parse(dsd_urn), dsd_urn) for dsd_urn in dsd_candidates]
    for target_urn, msd_urn, metadataflow_urn, provision_urn in conn.execute(_DECLARED_TARGET_SQL).fetchall():
        if not msd_urn:
            continue
        for dsd_urn in _expand_declared_target(target_urn, flow_candidates, dsd_parsed):
            _accumulate(grouped, dsd_urn, msd_urn, metadataflow_urn, provision_urn, None)

    return {
        dsd_urn: [by_msd[msd] for msd in sorted(by_msd, key=_dataflow_info_first)]
        for dsd_urn, by_msd in grouped.items()
    }


# Native SDMX 3.0 back-links are keyed on the *populated* report targets — the
# concrete artefacts each MetadataSet was filed against — mirroring FMR, which
# emits the link only where a report actually exists (not for declared/wildcard
# metadataflow targets). One round-trip.
_TARGET_LINK_SQL = """
SELECT mt.target_urn AS target_urn, mt.set_urn AS set_urn
FROM sdmx.metadata_target mt
ORDER BY mt.target_urn, mt.set_urn
"""


def build_metadata_target_links(
    registry: LocalRegistry,
    *,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> dict[str, list[str]]:
    """Derive ``target_urn -> [metadataset_urn, ...]`` for native 3.0 links.

    The input to the SDMX 3.0 ``rel="metadata"`` discovery link (see
    ``sdmxlib.formats.sdmx_json.writer.write``'s ``metadata_links`` param):
    for each artefact a reported MetadataSet targets, the report URN(s) that
    describe it. Keyed on the *actual* target artefact (dataflow, DSD, …),
    matching where FMR places the link. One SQL round-trip; empty when no
    reference metadata has been reported.

    Args:
        registry: An open `LocalRegistry`. Used for its DuckDB connection
            when ``conn`` is omitted.
        conn: DuckDB connection to query. Defaults to the registry's own
            store connection.

    Returns:
        Mapping of target artefact URN → the MetadataSet report URNs filed
        against it (ordered), empty when nothing is reported.

    Raises:
        TypeError: If ``conn`` is omitted and the registry cannot
            not expose a DuckDB ``connection``.
    """
    conn = _resolve_conn(registry, conn)
    out: dict[str, list[str]] = {}
    for target_urn, set_urn in conn.execute(_TARGET_LINK_SQL).fetchall():
        if target_urn and set_urn:
            out.setdefault(target_urn, []).append(set_urn)
    return out


def annotate_structure_message(
    message: StructureMessage,
    dsd_metadata_map: dict[str, list[DsdMetadataLink]],
) -> StructureMessage:
    """Return a copy of *message* with METADATA annotations spliced onto DSDs.

    For each DataStructure in ``message.data_structures`` whose URN is in
    ``dsd_metadata_map``, append one ``Annotation(type="METADATA",
    title=msd_urn)`` per associated MSD, preserving any existing annotations
    and their order. **Idempotent**: an MSD already covered by a METADATA
    annotation with the same title (e.g. one ingested from an OECD/.Stat
    source) is not duplicated.

    The message and its DataStructure objects are left untouched — a new
    `StructureMessage` with new DSD instances is returned (`attrs.evolve`),
    so annotations never leak into DSDs shared through a registry identity
    map. When ``dsd_metadata_map`` is empty the input message is returned
    unchanged.

    Args:
        message: The structure message to project.
        dsd_metadata_map: Output of `build_dsd_metadata_map`.

    Returns:
        A `StructureMessage` — the same object if nothing changed, else a
        copy carrying the spliced annotations.
    """
    if not dsd_metadata_map:
        return message

    new_dsds = []
    changed = False
    for dsd in message.data_structures:
        links = dsd_metadata_map.get(str(artefact_urn(dsd)))
        if not links:
            new_dsds.append(dsd)
            continue

        existing_titles = {a.title for a in dsd.annotations.by_type(METADATA_ANNOTATION_TYPE)}
        additions = [
            Annotation(type=METADATA_ANNOTATION_TYPE, title=link.msd_urn)
            for link in links
            if link.msd_urn not in existing_titles
        ]
        if not additions:
            new_dsds.append(dsd)
            continue

        new_dsds.append(evolve(dsd, annotations=Annotations([*dsd.annotations, *additions])))
        changed = True

    if not changed:
        return message
    return evolve(message, data_structures=new_dsds)
