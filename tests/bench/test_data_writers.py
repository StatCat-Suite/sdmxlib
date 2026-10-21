"""Throughput benchmarks for the data-message writers.

Compares the three streaming writers — SDMX-CSV, SDMX-JSON 1.0,
SDMX-JSON 2.0 — on identical observation batches. Establishes a
baseline for performance regressions and a starting point for any
future optimisation work.

Sizing strategy: small / medium / large parameters scale the number
of observation rows, with a fixed DSD shape (3 enumerated dimensions,
one free-text dim, one obs-level attribute). Each writer is fed the
*same* list of Arrow record batches so the comparison isn't muddied
by varying input.

An optional ``census_geography`` benchmark stresses the upfront
codelist-seeding path in ``_data_common.ComponentTable`` by using the
real 78k-row 2021 Census geography codelist (skipped when the
bundle `_bundle` resolves isn't present). That
codelist is large enough to surface any quadratic blowup in the
position-table init that the synthetic sizes wouldn't show.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import pyarrow as pa
import pytest

from sdmxlib.formats.sdmx_csv.writer import write_sdmx_csv
from sdmxlib.formats.sdmx_json.data_v1 import write_sdmx_json_data as write_v1
from sdmxlib.formats.sdmx_json.data_v2 import write_sdmx_json_data as write_v2
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import AttachmentLevel, Attribute, DataStructure, Dimension, Measure, TimeDimension
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from tests.bench import _bundle
from tests.support.factories import concept_ref, dsd_binding

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


# ── synthetic fixtures ────────────────────────────────────────────────────────


def _make_dsd(n_freq_codes: int = 4, n_status_codes: int = 3) -> tuple[DataStructure, Dataflow]:
    """Three-dim DSD: FREQ (enumerated) + REF_AREA + TIME_PERIOD (free-text)
    with one enumerated obs-level attribute (OBS_STATUS)."""
    cl_freq = Codelist(
        id="CL_FREQ",
        maintainer="BENCH",
        version="1.0",
        name=InternationalString(en="Frequency"),
        codes=[Code(name={"en": "x"}, id=f"F{i}") for i in range(n_freq_codes)],
    )
    cl_status = Codelist(
        id="CL_STATUS",
        maintainer="BENCH",
        version="1.0",
        name=InternationalString(en="Status"),
        codes=[Code(name={"en": "x"}, id=f"S{i}") for i in range(n_status_codes)],
    )
    dsd = DataStructure(
        id="DSD_BENCH",
        maintainer="BENCH",
        version="1.0",
        name=InternationalString(en="Bench DSD"),
        dimensions=[
            Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=Ref[Codelist].to(cl_freq)),
            Dimension(id="REF_AREA", concept=concept_ref("REF_AREA")),
            Dimension(id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD")),
        ],
        attributes=[
            Attribute(
                attachment=AttachmentLevel.DATASET,
                id="OBS_STATUS",
                concept=concept_ref("OBS_STATUS"),
                representation=Ref[Codelist].to(cl_status),
            )
        ],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    flow = Dataflow(
        id="DF_BENCH",
        maintainer="BENCH",
        version="1.0",
        name=InternationalString(en="Bench Dataflow"),
        structure=Ref[DataStructure].to(dsd),
    )
    return dsd, flow


def _make_batches(n_rows: int, *, chunk_rows: int = 10_000) -> list[pa.RecordBatch]:
    """Generate *n_rows* synthetic observations chunked into Arrow batches.

    Deterministic so repeated bench runs are comparable. Cycles through
    the codelists so every code position is exercised; REF_AREA and
    TIME_PERIOD are free-text and will grow lazy position tables.
    """
    freq_cycle = ["F0", "F1", "F2", "F3"]
    status_cycle = ["S0", "S1", "S2"]
    df = pl.DataFrame(
        {
            "FREQ": [freq_cycle[i % len(freq_cycle)] for i in range(n_rows)],
            "REF_AREA": [f"AREA{i % 200:03d}" for i in range(n_rows)],
            "TIME_PERIOD": [f"2020-{(i % 12) + 1:02d}" for i in range(n_rows)],
            "OBS_VALUE": [float(i) for i in range(n_rows)],
            "OBS_STATUS": [status_cycle[i % len(status_cycle)] for i in range(n_rows)],
        }
    )
    table = df.to_arrow()
    return list(table.to_batches(max_chunksize=chunk_rows))


_SIZES = [
    pytest.param(100, id="small_100"),
    pytest.param(10_000, id="medium_10k"),
    pytest.param(100_000, id="large_100k"),
]


@pytest.fixture(scope="module")
def dsd_flow() -> tuple[DataStructure, Dataflow]:
    return _make_dsd()


@pytest.fixture(scope="module", params=_SIZES)
def sized_batches(request: pytest.FixtureRequest) -> list[pa.RecordBatch]:
    return _make_batches(request.param)


# ── writer callers ───────────────────────────────────────────────────────────


def _consume(byte_iter: Iterable[bytes]) -> int:
    """Drain a writer generator and report total bytes. Without this the
    pytest-benchmark harness would only time the generator construction."""
    n = 0
    for chunk in byte_iter:
        n += len(chunk)
    return n


def _run_csv(batches: list[pa.RecordBatch], _flow: Ref[Dataflow], dsd: DataStructure) -> int:
    return _consume(write_sdmx_csv(batches, dsd, structure_id="BENCH:DF_BENCH(1.0)"))


def _run_json_v1(batches: list[pa.RecordBatch], flow: Ref[Dataflow], dsd: DataStructure) -> int:
    return _consume(
        write_v1(
            batches,
            dsd,
            flow,
            message_id="bench",
            prepared=datetime(2026, 6, 16, tzinfo=UTC),
            sender_id="BENCH",
            lang="en",
        )
    )


def _run_json_v2(batches: list[pa.RecordBatch], flow: Ref[Dataflow], dsd: DataStructure) -> int:
    return _consume(
        write_v2(
            batches,
            dsd,
            flow,
            message_id="bench",
            prepared=datetime(2026, 6, 16, tzinfo=UTC),
            sender_id="BENCH",
            lang="en",
        )
    )


_WRITERS: list[tuple[str, Callable[[list[pa.RecordBatch], Ref[Dataflow], DataStructure], int]]] = [
    ("csv", _run_csv),
    ("json_v1", _run_json_v1),
    ("json_v2", _run_json_v2),
]


@pytest.mark.parametrize(("name", "writer"), _WRITERS, ids=[n for n, _ in _WRITERS])
def test_writer_throughput(
    benchmark,
    sized_batches: list[pa.RecordBatch],
    dsd_flow: tuple[DataStructure, Dataflow],
    name: str,
    writer: Callable[[list[pa.RecordBatch], Ref[Dataflow], DataStructure], int],
) -> None:
    """Time each writer end-to-end on the same Arrow batches.

    The benchmark harness reports min/mean/median across rounds. To
    compare formats at a fixed size, group by the size id in
    pytest-benchmark's output (``--benchmark-group-by=param:sized_batches``).
    """
    dsd, flow = dsd_flow
    flow_ref = dsd_binding(dsd, flow, table="observations").dataflow

    def run() -> int:
        return writer(sized_batches, flow_ref, dsd)

    n_bytes = benchmark(run)
    # Sanity: every writer must actually emit something.
    assert n_bytes > 0


# ── optional: the census geography codelist ───────────────────────────────────


def _census_geo_path() -> Path | None:
    """Resolve the census bundle's geography_attributes parquet path.

    Resolution is `_bundle.bundle_path()` — the same one the read tier uses
    (#379) — plus the parquet within the bundle, found by its table name
    (`parquet/*/geography_attributes`) rather than a bundle-specific directory.
    ``$SDMXLIB_BENCH_GEOGRAPHY`` still names the parquet directly, for anyone who
    has that one file without the rest of the bundle.

    This used to carry its own resolution, whose fallback was a sibling
    workspace that did not exist then. It resolved to ``None`` and skipped,
    quietly, for months. Sharing the
    resolution means there is one path to keep correct rather than two, and the
    shared one raises when it is pointed somewhere wrong.
    """
    import os

    env_path = os.environ.get("SDMXLIB_BENCH_GEOGRAPHY")
    if env_path:
        p = Path(env_path)
        return p if p.exists() else None
    bundle = _bundle.bundle_path()
    if bundle is None:
        return None
    matches = sorted((bundle / "parquet").glob("*/geography_attributes/*.parquet"))
    return matches[0] if matches else None


def _load_census_geo_codelist() -> Codelist | None:
    """Materialise the 2021 Census geography codelist (~78k entries).

    Returns None if no bundle is configured — the benchmark then skips. Codes carry the official ``GEO_NAME`` label
    so the writer's lang-aware name lookup also gets exercised.
    """
    path = _census_geo_path()
    if path is None:
        return None
    df = pl.read_parquet(path).select(["REF_AREA", "GEO_NAME"]).unique()
    codes = [
        Code(id=row["REF_AREA"], name=InternationalString(en=row["GEO_NAME"]))
        for row in df.iter_rows(named=True)
        if row["REF_AREA"]
    ]
    return Codelist(
        id="CL_CENSUS_GEO",
        maintainer="STATCAN",
        version="2021",
        name=InternationalString(en="Census 2021 geographies"),
        codes=codes,
    )


@pytest.fixture(scope="module")
def census_setup() -> tuple[DataStructure, Dataflow, list[pa.RecordBatch]] | None:
    """Build a DSD + sample batches against the census geography codelist.

    Sized to 50k observations — enough to surface any per-row blowup
    while staying under a second per writer for routine bench runs.
    Skip the whole bench if the codelist parquet isn't accessible.
    """
    cl = _load_census_geo_codelist()
    if cl is None:
        return None
    cl_status = Codelist(
        id="CL_STATUS",
        maintainer="BENCH",
        version="1.0",
        name=InternationalString(en="Status"),
        codes=[Code(name={"en": "S0"}, id="S0"), Code(name={"en": "S1"}, id="S1")],
    )
    dsd = DataStructure(
        id="DSD_CENSUS",
        maintainer="BENCH",
        version="2021",
        name=InternationalString(en="Census bench DSD"),
        dimensions=[
            Dimension(id="REF_AREA", concept=concept_ref("REF_AREA"), representation=Ref[Codelist].to(cl)),
            Dimension(id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD")),
        ],
        attributes=[
            Attribute(
                attachment=AttachmentLevel.DATASET,
                id="OBS_STATUS",
                concept=concept_ref("OBS_STATUS"),
                representation=Ref[Codelist].to(cl_status),
            )
        ],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    flow = Dataflow(
        id="DF_CENSUS",
        maintainer="BENCH",
        version="2021",
        name=InternationalString(en="Census bench dataflow"),
        structure=Ref[DataStructure].to(dsd),
    )

    # Sample 50k REF_AREA codes (with replacement-by-cycling) so the
    # batches mostly hit pre-seeded positions, not the lazy path.
    sample_ids = [c.id for c in cl.codes[: min(20_000, len(cl.codes))]]
    n_rows = 50_000
    df = pl.DataFrame(
        {
            "REF_AREA": [sample_ids[i % len(sample_ids)] for i in range(n_rows)],
            "TIME_PERIOD": [f"2021-{(i % 12) + 1:02d}" for i in range(n_rows)],
            "OBS_VALUE": [float(i) for i in range(n_rows)],
            "OBS_STATUS": ["S0" if i % 2 == 0 else "S1" for i in range(n_rows)],
        }
    )
    batches = list(df.to_arrow().to_batches(max_chunksize=10_000))
    return dsd, flow, batches


@pytest.mark.parametrize(("name", "writer"), _WRITERS, ids=[n for n, _ in _WRITERS])
def test_census_geography_throughput(
    benchmark,
    census_setup: tuple[DataStructure, Dataflow, list[pa.RecordBatch]] | None,
    name: str,
    writer: Callable[[list[pa.RecordBatch], Ref[Dataflow], DataStructure], int],
) -> None:
    """Optional bench against the 2021 Census's 78k-entry geography codelist.

    Stresses the writer's upfront ComponentTable seeding (which walks
    every code in every enumerated dim before the first observation
    is emitted). Skipped automatically when no bundle is configured.
    """
    if census_setup is None:
        pytest.skip(_bundle.skip_reason())
    dsd, flow, batches = census_setup
    flow_ref = dsd_binding(dsd, flow, table="observations").dataflow

    def run() -> int:
        return writer(batches, flow_ref, dsd)

    n_bytes = benchmark(run)
    assert n_bytes > 0


# ── .Stat-shape: a census profile envelope ────────────────────────────────────


@pytest.fixture(scope="module")
def dot_stat_shape_setup() -> tuple[DataStructure, Dataflow, list[pa.RecordBatch]] | None:
    """Mirror a census profile envelope shape that .Stat consumes.

    Real workload characteristics:

    - Large REF_AREA codelist (the census: 78k+ geographies).
    - **Sparse observations** — a typical .Stat Data Explorer query is for a
      single area or small slice, not the whole universe.
    - **Single annual TIME_PERIOD** value across all rows (FREQ=A5,
      e.g. ``2021``). Exercises the ``_period_bounds`` path on a
      single value, where the cost has to stay negligible.
    - Many CENSUS_CHARACTERISTIC measures collapsed into a single
      observation map (modeled here as a CHAR dimension with ~200
      values).

    Sized to ~10k observations — a realistic upper bound for one
    flow's .Stat Data Explorer table fetch. Smaller than the census bench
    (50k rows) because the .Stat consumer's parse cost grows with
    body bytes too, so this size matches the real interactive
    workload.
    """
    cl_geo = _load_census_geo_codelist()
    if cl_geo is None:
        return None
    cl_freq = Codelist(
        id="CL_FREQ",
        maintainer="BENCH",
        version="1.0",
        name=InternationalString(en="Frequency"),
        codes=[Code(id="A5", name=InternationalString(en="Every 5 years"))],
    )
    cl_char = Codelist(
        id="CL_CHARACTERISTIC",
        maintainer="BENCH",
        version="1.0",
        name=InternationalString(en="Census characteristic"),
        codes=[Code(name={"en": "x"}, id=f"CHAR{i:04d}") for i in range(200)],
    )
    dsd = DataStructure(
        id="DSD_DOT_STAT",
        maintainer="BENCH",
        version="2021",
        name=InternationalString(en=".Stat-shape DSD"),
        dimensions=[
            Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=Ref[Codelist].to(cl_freq)),
            Dimension(id="REF_AREA", concept=concept_ref("REF_AREA"), representation=Ref[Codelist].to(cl_geo)),
            Dimension(
                id="CHARACTERISTIC", concept=concept_ref("CHARACTERISTIC"), representation=Ref[Codelist].to(cl_char)
            ),
        ],
        time_dimension=TimeDimension(id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD")),
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    flow = Dataflow(
        id="DF_DOT_STAT",
        maintainer="BENCH",
        version="2021",
        name=InternationalString(en=".Stat dataflow"),
        structure=Ref[DataStructure].to(dsd),
    )
    # Sparse query: 50 areas x all 200 characteristics x 1 year = 10k rows.
    # Mirrors a typical .Stat Data Explorer "small area selection across all
    # indicators" query against Census Profile.
    geo_sample = [c.id for c in cl_geo.codes[:50]]
    char_ids = [f"CHAR{i:04d}" for i in range(200)]
    rows = [
        {
            "FREQ": "A5",
            "REF_AREA": geo_sample[i % len(geo_sample)],
            "CHARACTERISTIC": char_ids[(i // len(geo_sample)) % len(char_ids)],
            "TIME_PERIOD": "2021",
            "OBS_VALUE": float(i),
        }
        for i in range(50 * 200)
    ]
    df = pl.DataFrame(rows)
    batches = list(df.to_arrow().to_batches(max_chunksize=10_000))
    return dsd, flow, batches


@pytest.mark.parametrize(("name", "writer"), _WRITERS, ids=[n for n, _ in _WRITERS])
def test_dot_stat_shape_throughput(
    benchmark,
    dot_stat_shape_setup: tuple[DataStructure, Dataflow, list[pa.RecordBatch]] | None,
    name: str,
    writer: Callable[[list[pa.RecordBatch], Ref[Dataflow], DataStructure], int],
) -> None:
    """Bench a census profile envelope shape a .Stat Data Explorer reads.

    Sanity check that:

    - The bloat fix (only emit observed REF_AREA codes, not all 78k)
      keeps the JSON body small enough that the consumer parse cost is
      reasonable.
    - The time-bounds emission on TIME_PERIOD adds negligible cost
      compared to the codelist seeding it replaced.
    - Per-batch encode throughput stays competitive with the SDMX-CSV
      writer for the real workload shape, not just synthetic data.

    Skipped when no bundle is configured.
    """
    if dot_stat_shape_setup is None:
        pytest.skip(_bundle.skip_reason())
    dsd, flow, batches = dot_stat_shape_setup
    flow_ref = dsd_binding(dsd, flow, table="observations").dataflow

    def run() -> int:
        return writer(batches, flow_ref, dsd)

    n_bytes = benchmark(run)
    assert n_bytes > 0
