"""CODR → SDMX pipeline using sdmxlib.

Statistics Canada publishes metadata for each table (PID) through its WDS
REST API.  This script fetches that metadata, builds an SDMX StructureMessage,
transforms the daily delta observation data, wraps it as a DSD-coupled Dataset,
validates it, and sinks to Parquet.

Two entry points:

    python codr.py structure <pid>          # build + write structure XML only
    python codr.py delta    <YYYYMMDD>      # download delta, process all PIDs

Contrast with the sdmx1-based approach in codr-delta-extract:
    - Typed Cube dataclass replaces repeated pidcube[0]['object'][...] access
    - Fixed concepts expressed as a list constant, not 10 individual appends
    - Declarative construction (list comprehensions) — no manual .append() loops
    - Attribute attachment via AttachmentLevel enum, not PrimaryMeasureRelationship()
    - Observation data wrapped as a Dataset — validate(), sink_parquet(), labels
    - Full type checking — no pyright suppression needed
"""

import io
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx
import polars as pl

import sdmxlib as sl

# ── External shared codelists (referenced, not built locally) ─────────────────

CL_FREQ = sl.Codelist.urn(agency="SDMX", id="CL_FREQ", version="2.1")
CL_OBS_STATUS = sl.Codelist.urn(agency="CA1", id="CL_OBS_STATUS", version="2.2")
CL_CONF_STATUS = sl.Codelist.urn(agency="SDMX", id="CL_CONF_STATUS", version="1.3")
CL_QUALITY = sl.Codelist.urn(agency="CA1", id="CL_QUALITY", version="1.0")
CL_UNIT_MEASURE = sl.Codelist.urn(agency="STC", id="CL_UNIT_MEASURE", version="1.5")
CL_UNIT_MULT = sl.Codelist.urn(agency="SDMX", id="CL_UNIT_MULT", version="1.1")
CL_DECIMALS = sl.Codelist.urn(agency="SDMX", id="CL_DECIMALS", version="1.0")

# ── Fixed concepts present in every CODR DSD ─────────────────────────────────

FIXED_CONCEPTS: list[tuple[str, str, str]] = [
    # (id, en label, fr label)
    ("TIME_PERIOD", "Time", "Temps"),
    ("FREQ", "Frequency", "Fréquence"),
    ("OBS_VALUE", "Observation Value", "Valeur d'observation"),
    ("OBS_STATUS", "Observation Status", "État d'observation"),
    ("CONFID", "Confidentiality Status", "État de confidentialité"),
    ("QUALITY", "Quality Indicator", "Indicateur de qualité"),
    ("UNIT_MEASURE", "Unit of Measure", "Unité de mesure"),
    ("UNIT_MULT", "Unit Multiplier", "Multiplicateur d'unité"),
    ("REPYEARSTART", "Rep Year Start", "Rep Year Start"),
    ("DGUID", "DGUID", "DGUID"),
    ("VECTOR", "Vector", "Vecteur"),
    ("DECIMALS", "Decimals", "Décimales"),
]

# ── WDS code mappings ─────────────────────────────────────────────────────────

# WDS frequencyCode → SDMX CL_FREQ code
FREQ_CODE_MAP: dict[str, str] = {
    "1": "D",   # Daily
    "2": "W",   # Weekly
    "7": "S",   # Semi-annual
    "9": "Q",   # Quarterly
    "10": "M",  # Monthly
    "12": "A",  # Annual
}

# WDS symbolCode → SDMX OBS_STATUS code
SYMBOL_CODE_MAP: dict[str, str] = {
    "0": "",    # No special status — blank
    "1": "E",   # Estimate
    "2": "F",   # Forecast
    "3": "D",   # Discontinued
    "4": "C",   # Confidential (suppressed)
    "5": "R",   # Revised
    "6": "X",   # Suppressed
    "9": "M",   # Missing
}


# ── Typed wrappers for the WDS pidcube JSON ───────────────────────────────────


@dataclass
class CubeMember:
    id: str
    name_en: str
    name_fr: str
    parent_id: str | None = None
    uom_code: str | None = None  # memberUomCode — CL_UNIT_MEASURE code for hasUom dims
    footnotes: list[tuple[str, str]] = field(default_factory=list)  # [(en, fr)]


@dataclass
class CubeDimension:
    position: int  # 1-based
    name_en: str
    name_fr: str
    has_uom: bool
    members: list[CubeMember]

    @property
    def dim_id(self) -> str:
        """The SDMX component id for this dimension."""
        return f"DIM{self.position}"


@dataclass
class Cube:
    """Typed view over a single pidcube response."""

    product_id: str  # e.g. "14100287"
    title_en: str
    title_fr: str
    freq_code: str  # e.g. "10" (monthly)
    subject_codes: list[str]
    is_archived: bool
    dimensions: list[CubeDimension]

    @property
    def sdmx_freq(self) -> str:
        """Map WDS frequencyCode to an SDMX CL_FREQ code.

        Falls back to the raw freq_code if not in the map — unknown frequencies
        should be investigated rather than silently dropped.
        """
        return FREQ_CODE_MAP.get(self.freq_code, self.freq_code)

    @property
    def dim_ids(self) -> list[str]:
        """Ordered dimension ids: ["DIM1", "DIM2", ...]."""
        return [d.dim_id for d in self.dimensions]

    @property
    def uom_dim_ids(self) -> list[str]:
        """Ids of dimensions that carry a unit-of-measure."""
        return [d.dim_id for d in self.dimensions if d.has_uom]

    @classmethod
    def from_wds(cls, raw: list[dict]) -> "Cube":
        """Parse the raw WDS getCubeMetadata response."""
        obj = raw[0]["object"]
        dimensions = [
            CubeDimension(
                position=i + 1,
                name_en=dim["dimensionNameEn"],
                name_fr=dim["dimensionNameFr"],
                has_uom=dim.get("hasUom", False),
                members=[
                    CubeMember(
                        id=str(m["memberId"]),
                        name_en=m["memberNameEn"],
                        name_fr=m["memberNameFr"],
                        parent_id=str(m["parentMemberId"]) if m.get("parentMemberId") else None,
                        uom_code=m.get("memberUomCode") or None,
                        footnotes=[
                            (fn["footnoteEn"], fn["footnoteFr"])
                            for fn in m.get("footnote", [])
                        ],
                    )
                    for m in dim["member"]
                ],
            )
            for i, dim in enumerate(obj["dimension"])
        ]
        return cls(
            product_id=str(obj["productId"]),
            title_en=obj["cubeTitleEn"],
            title_fr=obj["cubeTitleFr"],
            freq_code=str(obj.get("frequencyCode", "")),
            subject_codes=obj.get("subjectCode") or [],
            is_archived=obj.get("archiveStatusCode") == "1",
            dimensions=dimensions,
        )


# ── HTTP fetch ────────────────────────────────────────────────────────────────


def fetch_cube(pid: str) -> Cube:
    """Fetch and parse the WDS pidcube metadata for a given PID."""
    url = "https://www150.statcan.gc.ca/t1/wds/rest/getCubeMetadata"
    resp = httpx.post(
        url,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        content=f'[{{"productId":{pid}}}]',
        timeout=60.0,
    )
    resp.raise_for_status()
    return Cube.from_wds(resp.json())


def fetch_delta(run_date: date, dest: Path | None = None) -> Path:
    """Stream the daily delta ZIP from Stats Canada and extract the CSV.

    The delta ZIP is published at:
        https://www150.statcan.gc.ca/n1/delta/{YYYYMMDD}.zip

    It contains a single CSV named {YYYYMMDD}.csv with every observation
    that changed that day across all CODR tables.

    Args:
        run_date: The date of the delta to download.
        dest:     Directory to write the extracted CSV. Defaults to cwd.

    Returns:
        Path to the extracted CSV file.
    """
    date_str = run_date.strftime("%Y%m%d")
    url = f"https://www150.statcan.gc.ca/n1/delta/{date_str}.zip"
    dest_dir = dest or Path.cwd()
    csv_path = dest_dir / f"{date_str}.csv"

    if csv_path.exists():
        print(f"  Delta CSV already present: {csv_path}")
        return csv_path

    print(f"  Downloading delta {date_str} …")
    zip_bytes = io.BytesIO()
    with httpx.stream("GET", url, timeout=300.0) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_bytes(chunk_size=8 * 1024 * 1024):
            zip_bytes.write(chunk)

    zip_bytes.seek(0)
    with zipfile.ZipFile(zip_bytes) as zf:
        csv_name = f"{date_str}.csv"
        zf.extract(csv_name, dest_dir)

    print(f"  Extracted: {csv_path}")
    return csv_path


# ── SDMX artefact builders ────────────────────────────────────────────────────


def build_concept_scheme(cube: Cube, agency: str, version: str) -> sl.ConceptScheme:
    """Build a ConceptScheme for the PID.

    Dimension concepts are named after each dimension; fixed cross-cutting
    concepts (FREQ, TIME_PERIOD, OBS_VALUE, etc.) come from the constant list.
    """
    dim_concepts = [
        sl.Concept(
            id=dim.dim_id,
            name={"en": dim.name_en, "fr": dim.name_fr},
        )
        for dim in cube.dimensions
    ]
    fixed_concepts = [sl.Concept(id=cid, name={"en": en, "fr": fr}) for cid, en, fr in FIXED_CONCEPTS]
    return sl.ConceptScheme(
        id=f"CONCEPTS_{cube.product_id}",
        maintainer=agency,
        version=version,
        name={"en": f"Concepts for {cube.product_id}"},
        concepts=dim_concepts + fixed_concepts,
    )


def build_codelists(cube: Cube, agency: str, version: str) -> list[sl.Codelist]:
    """Build one Codelist per dimension from the WDS member lists."""
    return [
        sl.Codelist(
            id=f"CL_{cube.product_id}_{dim.dim_id}",
            maintainer=agency,
            version=version,
            name={"en": dim.name_en, "fr": dim.name_fr},
            codes=[
                sl.Code(
                    id=member.id,
                    name={"en": member.name_en, "fr": member.name_fr},
                )
                for member in dim.members
            ],
        )
        for dim in cube.dimensions
    ]


def build_dsd(cube: Cube, agency: str, version: str) -> sl.DataStructure:
    """Build the DataStructureDefinition for the PID.

    Dimensions:
        FREQ (shared CL_FREQ)
        DIM1 … DIMn (per-table codelists)
        TIME_PERIOD (time dimension)

    Measure:
        OBS_VALUE (numeric)

    Attributes (all observation-level unless noted):
        REPYEARSTART, OBS_STATUS, CONFID, QUALITY
        UNIT_MEASURE, UNIT_MULT (series-level if any dimension has hasUom)
        DGUID (series-level, attached to first dimension)
        VECTOR, DECIMALS
    """
    freq_dim = sl.Dimension(id="FREQ", representation=CL_FREQ)

    table_dims = [
        sl.Dimension(
            id=dim.dim_id,
            representation=sl.Codelist.urn(
                agency=agency,
                id=f"CL_{cube.product_id}_{dim.dim_id}",
                version=version,
            ),
        )
        for dim in cube.dimensions
    ]

    time_dim = sl.TimeDimension()

    obs = sl.AttachmentLevel.OBSERVATION
    all_dim_ids = cube.dim_ids
    uom_dims = cube.uom_dim_ids or all_dim_ids
    first_dim = [cube.dimensions[0].dim_id] if cube.dimensions else []

    attributes = [
        sl.Attribute(id="REPYEARSTART", attachment=obs, representation=sl.Facet(type="String")),
        sl.Attribute(id="OBS_STATUS", attachment=obs, representation=CL_OBS_STATUS),
        sl.Attribute(id="CONFID", attachment=obs, representation=CL_CONF_STATUS),
        sl.Attribute(id="QUALITY", attachment=obs, representation=CL_QUALITY),
        sl.Attribute(
            id="UNIT_MEASURE",
            attachment=sl.AttachmentLevel.SERIES,
            related_dimensions=uom_dims,
            representation=CL_UNIT_MEASURE,
        ),
        sl.Attribute(
            id="UNIT_MULT",
            attachment=sl.AttachmentLevel.SERIES,
            related_dimensions=uom_dims,
            representation=CL_UNIT_MULT,
        ),
        sl.Attribute(
            id="DGUID",
            attachment=sl.AttachmentLevel.SERIES,
            related_dimensions=first_dim,
            representation=sl.Facet(type="String"),
        ),
        sl.Attribute(
            id="VECTOR",
            attachment=sl.AttachmentLevel.SERIES,
            related_dimensions=all_dim_ids,
            representation=sl.Facet(type="String"),
        ),
        sl.Attribute(
            id="DECIMALS",
            attachment=sl.AttachmentLevel.SERIES,
            related_dimensions=all_dim_ids,
            representation=CL_DECIMALS,
        ),
    ]

    title = f"Archived - {cube.title_en}" if cube.is_archived else cube.title_en

    return sl.DataStructure(
        id=f"DSD_{cube.product_id}",
        maintainer=agency,
        version=version,
        name={"en": title},
        dimensions=[freq_dim, *table_dims],
        time_dimension=time_dim,
        measures=[sl.Measure(id="OBS_VALUE", representation=sl.Facet(type="Double"))],
        attributes=attributes,
        annotations=[
            sl.Annotation(
                id="METADATA",
                title="urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataStructure=STC:MSD_STC_FOOTNOTES(1.1)",
                type="METADATA",
            )
        ],
    )


def build_dataflow(cube: Cube, agency: str, version: str, dsd: sl.DataStructure) -> sl.Dataflow:
    """Build the DataflowDefinition referencing the DSD."""
    title_en = f"Archived - {cube.title_en}" if cube.is_archived else cube.title_en
    title_fr = f"Archivé - {cube.title_fr}" if cube.is_archived else cube.title_fr

    annotations = [
        sl.Annotation(id="LAYOUT_CELL_ALIGN", title="RIGHT", type="LAYOUT_CELL_ALIGN"),
        sl.Annotation(
            id="NOT_DISPLAYED",
            title="OBS_STATUS=A,UNIT_MULT=0,VECTOR,DGUID",
            type="NOT_DISPLAYED",
        ),
        sl.Annotation(
            id="COMBINED_UNIT_MEASURE",
            title="COMBINED_UNIT_MEASURE:UNIT_MEASURE,UNIT_MULT",
            type="COMBINED_CONCEPTS",
        ),
    ]
    if cube.is_archived:
        annotations.append(sl.Annotation(id="SEARCH_WEIGHT", title="-10", type="SEARCH_WEIGHT"))

    return sl.Dataflow(
        id=f"DF_{cube.product_id}",
        maintainer=agency,
        version=version,
        name={"en": title_en, "fr": title_fr},
        structure=dsd,
        annotations=annotations,
    )


def build_categorisations(
    cube: Cube,
    agency: str,
    version: str,
    dataflow: sl.Dataflow,
) -> list[sl.Categorisation]:
    """Build Categorisation objects linking the dataflow into the subject hierarchy.

    Each StatCan subject code (e.g. "12", "1216", "121601") corresponds to a node in
    the CAT_SUBJECTS CategoryScheme maintained by CA1.
    """
    cat_by_id = {code: sl.Category(id=code) for code in cube.subject_codes}

    df_urn = sl.Dataflow.urn(agency=agency, id=dataflow.id, version=version)
    df_ref = sl.Ref[sl.Dataflow].of(df_urn, dataflow)

    return [
        sl.Categorisation(
            id=f"CT_{cube.product_id}_{code}",
            maintainer=agency,
            version=version,
            name={"en": f"CT_{cube.product_id}_{code}"},
            source=df_ref,
            target=sl.Ref[sl.Category].of(
                sl.SdmxUrn.make(
                    sl.Category,
                    package="categoryscheme",
                    artefact_class="Category",
                    agency="CA1",
                    id=code,
                ),
                cat_by_id[code],
            ),
        )
        for code in cube.subject_codes
    ]


def build_structure_message(
    cube: Cube,
    agency: str = "STC",
    version: str = "1.0",
) -> sl.StructureMessage:
    """Build a complete SDMX StructureMessage for a CODR PID."""
    concept_scheme = build_concept_scheme(cube, agency, version)
    codelists = build_codelists(cube, agency, version)
    dsd = build_dsd(cube, agency, version)
    dataflow = build_dataflow(cube, agency, version, dsd)
    categorisations = build_categorisations(cube, agency, version, dataflow)

    return sl.StructureMessage(
        concept_schemes=[concept_scheme],
        codelists=codelists,
        data_structures=[dsd],
        dataflows=[dataflow],
        categorisations=categorisations,
    )


# ── Data processing ───────────────────────────────────────────────────────────


def _resolve_dsd(dsd: sl.DataStructure, codelists: list[sl.Codelist]) -> sl.DataStructure:
    """Intern the DSD and its codelists into a Registry so Ref resolution works.

    build_dsd() produces a DSD with unresolved Ref[Codelist] on each dimension.
    Adding everything to a Registry resolves those refs in-place, which is
    required for Dataset.validate() and collect(with_labels=True) to work.
    """
    reg = sl.InMemoryRegistry()
    reg.add(dsd)
    for cl in codelists:
        reg.add(cl)
    return dsd


def transform_obs(delta_csv: Path, cube: Cube) -> pl.LazyFrame:
    """Transform a WDS delta CSV into SDMX-shaped observation data for one PID.

    The delta CSV has one row per changed observation across all CODR tables.
    This function:
        1. Filters to the requested PID.
        2. Splits the dot-separated ``coordinate`` column into DIM1, DIM2, …
        3. Maps WDS code columns to SDMX equivalents (FREQ, OBS_STATUS, etc.).
        4. Selects and renames to the final SDMX column set.

    Returns a ``pl.LazyFrame`` — nothing is materialised until a terminal
    (collect / sink_parquet / sink_csv) is called.

    Delta CSV columns (camelCase as published by Stats Canada):
        productId, coordinate, value, symbolCode, securityCode,
        releaseTime, frequencyCode, scalarFactorCode, decimals,
        refPer, refPer2, dguid, vectorId, statusCode
    """
    n_dims = len(cube.dimensions)
    freq = cube.sdmx_freq

    # Map symbol code strings to OBS_STATUS codes via Polars replace
    symbol_old = list(SYMBOL_CODE_MAP.keys())
    symbol_new = list(SYMBOL_CODE_MAP.values())

    # UNIT_MEASURE: derived from the hasUom dimension's memberUomCode.
    # In the delta CSV, UNIT_MEASURE is not a column — it's encoded as the
    # coordinate value at the hasUom dimension position.  We build a replace
    # expression (memberId → memberUomCode) for each hasUom dim and combine
    # them with coalesce so that multi-dim tables still resolve to one value.
    # Members with no uom_code fall back to "_Z" (SDMX "not applicable").
    uom_exprs: list[pl.Expr] = []
    for dim in cube.dimensions:
        if dim.has_uom:
            old_ids = [m.id for m in dim.members]
            new_codes = [m.uom_code or "_Z" for m in dim.members]
            uom_exprs.append(
                pl.col(dim.dim_id).replace(old_ids, new_codes, default="_Z")
            )
    unit_measure_expr = (
        pl.coalesce(uom_exprs).alias("UNIT_MEASURE")
        if uom_exprs
        else pl.lit("_Z").alias("UNIT_MEASURE")
    )

    return (
        pl.scan_csv(delta_csv, infer_schema=False)
        # ── 1. Filter to this PID ─────────────────────────────────────────
        .filter(pl.col("productId") == cube.product_id)
        # ── 2. Split coordinate "1.2.3" into DIM1, DIM2, DIM3 ───────────
        .with_columns(
            pl.col("coordinate").str.split(".").alias("_coord_parts")
        )
        .with_columns(
            [
                pl.col("_coord_parts").list.get(i).alias(f"DIM{i + 1}")
                for i in range(n_dims)
            ]
        )
        .drop("_coord_parts", "coordinate")
        # ── 3. Map / rename observation columns ──────────────────────────
        .with_columns(
            # FREQ is fixed for the whole table
            pl.lit(freq).alias("FREQ"),
            # OBS_STATUS from symbolCode
            pl.col("symbolCode")
            .replace(symbol_old, symbol_new, default="")
            .alias("OBS_STATUS"),
            # OBS_VALUE as float
            pl.col("value").cast(pl.Float64).alias("OBS_VALUE"),
            # UNIT_MEASURE from the hasUom dimension member name
            unit_measure_expr,
        )
        # ── 4. Select and rename to final SDMX column set ─────────────
        .rename({
            "refPer": "TIME_PERIOD",
            "scalarFactorCode": "UNIT_MULT",
            "securityCode": "CONFID",
            "statusCode": "QUALITY",
            "vectorId": "VECTOR",
            "dguid": "DGUID",
            "decimals": "DECIMALS",
        })
        .select(
            "FREQ",
            *[f"DIM{i + 1}" for i in range(n_dims)],
            "TIME_PERIOD",
            "OBS_VALUE",
            "OBS_STATUS",
            "CONFID",
            "QUALITY",
            "UNIT_MEASURE",
            "UNIT_MULT",
            "VECTOR",
            "DGUID",
            "DECIMALS",
        )
    )


def make_dataset(
    lf: pl.LazyFrame,
    dsd: sl.DataStructure,
    codelists: list[sl.Codelist],
) -> sl.Dataset:
    """Wrap a transformed observation LazyFrame as a DSD-coupled Dataset.

    Resolves the DSD's codelist Refs (required for validate() and
    collect(with_labels=True)) and returns a Dataset. Nothing is materialised.
    """
    resolved_dsd = _resolve_dsd(dsd, codelists)
    return sl.Dataset(structure=resolved_dsd, data=lf)


def extract_footnotes(cube: Cube) -> pl.DataFrame:
    """Extract dimension member footnotes from the pidcube into a tidy DataFrame.

    Returns a DataFrame with columns:
        dim_id      - e.g. "DIM1"
        member_id   - the member code
        member_en   - member name in English
        footnote_en - footnote text in English
        footnote_fr - footnote text in French

    Empty if no members have footnotes.
    """
    rows = [
        {
            "dim_id": dim.dim_id,
            "member_id": member.id,
            "member_en": member.name_en,
            "footnote_en": fn_en,
            "footnote_fr": fn_fr,
        }
        for dim in cube.dimensions
        for member in dim.members
        for fn_en, fn_fr in member.footnotes
    ]
    return pl.DataFrame(
        rows,
        schema={
            "dim_id": pl.String,
            "member_id": pl.String,
            "member_en": pl.String,
            "footnote_en": pl.String,
            "footnote_fr": pl.String,
        },
    )


# ── Full per-PID pipeline ─────────────────────────────────────────────────────


def process_pid(
    pid: str,
    delta_csv: Path,
    out_dir: Path,
    agency: str = "STC",
    version: str = "1.0",
) -> None:
    """Run the full structure + data pipeline for one PID.

    Outputs:
        {out_dir}/{pid}/{pid}_structure.xml     SDMX-ML StructureMessage
        {out_dir}/{pid}/{pid}_data.parquet      Observation data (Parquet)
        {out_dir}/{pid}/{pid}_footnotes.csv     Footnotes (empty if none)
    """
    pid_dir = out_dir / pid
    pid_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Fetch pidcube metadata ─────────────────────────────────────────────
    print(f"  [{pid}] Fetching pidcube …")
    cube = fetch_cube(pid)
    print(f"  [{pid}] {cube.title_en}")
    n_codes = sum(len(d.members) for d in cube.dimensions)
    print(f"  [{pid}] {len(cube.dimensions)} dims, {n_codes} codes, freq={cube.sdmx_freq}")

    # ── 2. Build and write structure XML ─────────────────────────────────────
    codelists = build_codelists(cube, agency, version)
    dsd = build_dsd(cube, agency, version)
    dataflow = build_dataflow(cube, agency, version, dsd)
    categorisations = build_categorisations(cube, agency, version, dataflow)
    msg = sl.StructureMessage(
        concept_schemes=[build_concept_scheme(cube, agency, version)],
        codelists=codelists,
        data_structures=[dsd],
        dataflows=[dataflow],
        categorisations=categorisations,
    )
    xml_path = pid_dir / f"{pid}_structure.xml"
    xml_path.write_bytes(sl.to_xml(msg, pretty_print=True))
    print(f"  [{pid}] Structure written: {xml_path.name}")

    # ── 3. Transform observation data from delta CSV ──────────────────────────
    lf = transform_obs(delta_csv, cube)
    ds = make_dataset(lf, dsd, codelists)

    # ── 4. Validate codes against codelists ───────────────────────────────────
    report = ds.validate()
    if report.is_empty():
        print(f"  [{pid}] Validation: clean")
    else:
        print(f"  [{pid}] Validation: {len(report)} violation(s)")
        print(report.sort("count", descending=True).head(5))

    # ── 5. Sink observation data to Parquet ───────────────────────────────────
    parquet_path = pid_dir / f"{pid}_data.parquet"
    ds.sink_parquet(parquet_path)
    print(f"  [{pid}] Data written: {parquet_path.name}")

    # ── 6. Write footnotes ────────────────────────────────────────────────────
    footnotes = extract_footnotes(cube)
    fn_path = pid_dir / f"{pid}_footnotes.csv"
    footnotes.write_csv(fn_path)
    if not footnotes.is_empty():
        print(f"  [{pid}] Footnotes written: {fn_path.name} ({len(footnotes)} rows)")


def process_delta(
    run_date: date,
    out_dir: Path,
    agency: str = "STC",
    version: str = "1.0",
    pid_allowlist: list[str] | None = None,
) -> None:
    """Download the daily delta and process every PID it contains.

    Args:
        run_date:      Date of the delta to download and process.
        out_dir:       Root output directory. A subdirectory per PID is created.
        agency:        SDMX agency id for built artefacts.
        version:       Version string for built artefacts.
        pid_allowlist: If given, only process PIDs in this list.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Download and extract delta CSV ────────────────────────────────────────
    delta_csv = fetch_delta(run_date, dest=out_dir)

    # ── Find unique PIDs in the delta ─────────────────────────────────────────
    all_pids = (
        pl.scan_csv(delta_csv, infer_schema=False)
        .select("productId")
        .unique()
        .collect()["productId"]
        .to_list()
    )

    pids = [p for p in all_pids if pid_allowlist is None or p in pid_allowlist]
    print(f"Delta {run_date}: {len(pids)} PIDs to process")

    failed: list[str] = []
    for pid in sorted(pids):
        try:
            process_pid(pid, delta_csv, out_dir, agency=agency, version=version)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{pid}] FAILED: {exc}")
            failed.append(pid)

    if failed:
        print(f"\nFailed PIDs ({len(failed)}): {', '.join(failed)}")
    else:
        print(f"\nAll {len(pids)} PIDs processed successfully.")


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    def _usage() -> None:
        print("usage:")
        print("  python codr.py structure <pid> [agency] [version]")
        print("  python codr.py delta <YYYYMMDD> [out_dir] [pid ...]")
        sys.exit(1)

    if len(sys.argv) < 3:
        _usage()

    cmd = sys.argv[1]

    if cmd == "structure":
        pid = sys.argv[2]
        agency = sys.argv[3] if len(sys.argv) > 3 else "STC"
        version = sys.argv[4] if len(sys.argv) > 4 else "1.0"

        print(f"Fetching pidcube for {pid} …")
        cube = fetch_cube(pid)
        print(f"  {cube.title_en}")
        print(f"  {len(cube.dimensions)} dimensions, {sum(len(d.members) for d in cube.dimensions)} total codes")

        msg = build_structure_message(cube, agency=agency, version=version)
        out = Path(f"{pid}_structure.xml")
        out.write_bytes(sl.to_xml(msg, pretty_print=True))
        print(f"Written: {out}")

    elif cmd == "delta":
        date_str = sys.argv[2]
        run_date = date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
        out_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(date_str)
        pid_allowlist = sys.argv[4:] or None

        process_delta(run_date, out_dir, pid_allowlist=pid_allowlist)

    else:
        _usage()
