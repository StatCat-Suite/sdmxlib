"""Build CODR table 14100287 as a product's first edition, through sdmxlib's public API.

    uv run python -m bench.seal.build_codr <raw dir> <product dir>

`<raw dir>` holds ``metadata.json``, the table's WDS ``getCubeMetadata``
answer, and ``14100287.csv``, its full-table CSV (`bench/seal/README.md`). The
first edition holds every month but the latest; `measure_seal` then seals a
second edition that adds the latest month, as a monthly release does.
"""

import json
import sys
import time
from pathlib import Path
from typing import Annotated

import duckdb
import typer

import sdmxlib as sl
from sdmxlib.edition import Producer, seal

AGENCY = "STC"
PID = "14100287"
DIMS = ("GEO", "LFC", "GENDER", "AGE", "STATISTICS", "DATA_TYPE")
ATTRIBUTES = ("UNIT_MEASURE", "UNIT_MULT", "OBS_STATUS", "DECIMALS", "VECTOR")
SCHEMA, TABLE = "codr", "t14100287"


def structures(raw: Path) -> tuple[list[sl.MaintainableArtefact], sl.DataStructure, sl.Dataflow]:
    meta = json.loads((raw / "metadata.json").read_text())[0]["object"]
    codelists = []
    for did, dim in zip(DIMS, meta["dimension"], strict=True):
        codes = [
            sl.Code(
                id=str(m["memberId"]),
                name={"en": m["memberNameEn"], "fr": m["memberNameFr"]},
                parent_id=None if m.get("parentMemberId") is None else str(m["parentMemberId"]),
            )
            for m in dim["member"]
        ]
        names = {"en": dim["dimensionNameEn"], "fr": dim["dimensionNameFr"]}
        codelists.append(sl.Codelist(id=f"CL_{did}", maintainer=AGENCY, version="1.0", name=names, codes=codes))
    concept_ids = (*DIMS, "TIME_PERIOD", "OBS_VALUE", *ATTRIBUTES)
    concepts = sl.ConceptScheme(
        id=f"CS_{PID}",
        maintainer=AGENCY,
        version="1.0",
        name={"en": f"Concepts of table {PID}"},
        concepts=[sl.Concept(id=c, name={"en": c}) for c in concept_ids],
    )
    dsd = sl.DataStructure(
        id=f"DSD_{PID}",
        maintainer=AGENCY,
        version="1.0",
        name={"en": meta["cubeTitleEn"], "fr": meta["cubeTitleFr"]},
        dimensions=[
            sl.Dimension(id=did, concept=concepts.ref_to(did), representation=sl.Ref.to(cl))
            for did, cl in zip(DIMS, codelists, strict=True)
        ],
        time_dimension=sl.TimeDimension(id="TIME_PERIOD", concept=concepts.ref_to("TIME_PERIOD")),
        measures=[
            sl.Measure(id="OBS_VALUE", concept=concepts.ref_to("OBS_VALUE"), representation=sl.Facet(type="Double"))
        ],
        attributes=[
            sl.Attribute(
                id=a,
                concept=concepts.ref_to(a),
                attachment=sl.AttachmentLevel.OBSERVATION,
                representation=sl.Facet(type="String"),
            )
            for a in ATTRIBUTES
        ],
    )
    flow = sl.Dataflow(id=f"DF_{PID}", maintainer=AGENCY, version="1.0", name=dsd.name, structure=sl.Ref.to(dsd))
    return [*codelists, concepts, dsd, flow], dsd, flow


#: The CSV's rows as observations; `{months}` selects which reference periods.
OBSERVATIONS = """
SELECT
    {dims},
    REF_DATE AS TIME_PERIOD,
    TRY_CAST(VALUE AS DOUBLE) AS OBS_VALUE,
    UOM AS UNIT_MEASURE,
    SCALAR_FACTOR AS UNIT_MULT,
    NULLIF(concat_ws('', STATUS, SYMBOL), '') AS OBS_STATUS,
    CAST(DECIMALS AS VARCHAR) AS DECIMALS,
    VECTOR
FROM read_csv($csv, header = true, all_varchar = true)
WHERE {months}
ORDER BY ALL
"""


def observations_sql(months: str) -> str:
    dims = ",\n    ".join(f"split_part(COORDINATE, '.', {i + 1}) AS {d}" for i, d in enumerate(DIMS))
    return OBSERVATIONS.format(dims=dims, months=months)


def latest_month(conn: duckdb.DuckDBPyConnection, csv: Path) -> str:
    row = conn.execute(
        "SELECT max(REF_DATE) FROM read_csv($csv, header = true, all_varchar = true)", {"csv": str(csv)}
    ).fetchone()
    return str(row[0]) if row else ""


def main(
    raw: Annotated[Path, typer.Argument(help="The directory holding metadata.json and the table's CSV.")],
    product: Annotated[Path, typer.Argument(help="The product directory to build the first edition in.")],
) -> None:
    """Build the table's first edition, every month but the latest, and seal it."""
    csv = raw / f"{PID}.csv"
    artefacts, dsd, flow = structures(raw)
    start = time.perf_counter()
    with sl.LocalRegistry.build(product) as registry:
        registry.add_all(artefacts)
        latest = latest_month(registry.connection, csv)
        source = registry.connection.sql(
            observations_sql("REF_DATE < $latest"), params={"csv": str(csv), "latest": latest}
        )
        registry.write_observations(schema=SCHEMA, table=TABLE, source=source)
        registry.add(sl.DataflowBinding.from_dsd(dsd, dataflow=sl.Ref.to(flow), schema=SCHEMA, table=TABLE))
        edition = registry.edition_dir
    seal(edition, product_id=PID, producer=Producer(name="seal-measure", version="0"), lifecycle="revisable")
    sys.stdout.write(f"first edition {edition.name}: before {latest}, {time.perf_counter() - start:.0f} s\n")


if __name__ == "__main__":
    typer.run(main)
