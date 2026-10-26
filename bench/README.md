# Benchmark harness

Local-only performance harness for sdmxlib. Wall via
[`pytest-benchmark`](https://pytest-benchmark.readthedocs.io/), memory
via [`pytest-memray`](https://pytest-memray.readthedocs.io/). Suite
lives under `tests/bench/` and is marked `bench` — excluded from
`just test` and `just ci` so the inner dev loop stays fast.

## Workflow

Refactor-safety pattern:

```bash
# Before the refactor — on the main branch
just bench-save pre-refactor

# Do the refactor on a branch
# ...

# After — diff current vs the saved baseline (report-only, no fail)
just bench-compare pre-refactor
```

Compare output is for human eyes. A consistent >10% wall regression
across multiple runs is a real signal; one-off noise on a laptop isn't.

## Recipes

| Recipe | What it does |
|---|---|
| `just bench` | Wall benchmarks (pytest-benchmark), synthetic fixtures |
| `just bench-mem` | Memory benchmarks (pytest-memray) |
| `just bench-all` | Both, sequentially |
| `just bench-save NAME` | Snapshot wall + memory under `bench/baselines/NAME/` |
| `just bench-compare NAME` | Diff current wall against named baseline |
| `just bench-bundle` | The real-bundle tier (#379) — see below |
| `just bench-bundle-assertions` | That tier's assertions only, no measurements — part of `just ci` |
| `just bench-compete` | sdmxlib vs pysdmx vs sdmx1: speed, agreement, validity — see [`compete/README.md`](compete/README.md) |
| `just bench-compete-scale` | sdmxlib vs pysdmx on very large codelists |
| `just bench-concurrency` | Concurrent read throughput — see [`concurrency/README.md`](concurrency/README.md) |

`bench/baselines/` is git-ignored — baselines stay local.

## Suite coverage

### Phase 1 — wire format + InMemoryRegistry

| File | Bench | Sizes |
|---|---|---|
| `test_parse.py` | `parse(bytes)` per format × size | small / medium / large |
| `test_serialize.py` | `serialize(msg)` per format × size | small / medium / large |
| `test_registry.py` | `InMemoryRegistry.add_all` + `.get(urn)` loop | 100 / 1k / 10k artefacts |

### Phase 2 — DuckDB storage + LazyCodelist + LocalRegistry

| File | Bench | Sizes |
|---|---|---|
| `test_storage.py` | `put_codelist` / `put_data_structure` write | 100 / 1k / 10k codes; 3×50, 10×200 DSDs |
| `test_ingest.py` | `LocalRegistry.add_all` end to end — refs, localised text, a mixed blend (#139) | synthetic, sized per shape |
| `test_lazy.py` | `LazyCodelist` construct + `code_map(lang=)` | 100 / 1k / 10k codes |
| `test_local_registry.py` | `LocalRegistry.get(urn, eager=…)` for Codelist / DSD / Dataflow | small / medium |
| `test_query_dsl.py` | The path language (#306): compile cost and execution cost, separately — one predicate, a chain, a correlated `EXISTS`, an annotation reach, `is_in` at three widths | synthetic |
| `test_hierarchy_set_ops.py` | The four hierarchy shapes a census consumer hits (#348) — **wall-clock ceilings**, ~20x the measured value, the one exception `caching.md` names | synthetic hierarchy |
| `test_validation_overhead.py` | A codelist's whole-artefact rules, which run as it is built (#492): absolute cost, and less than serialising it | 100k codes |
| `test_availability_scan_shape.py` | One batched availability scan vs N per-component scans (#277) — measured, the batched form is slower | 3M rows, several shapes |

### Data-message writers

| File | Bench | Sizes |
|---|---|---|
| `test_data_writers.py` | SDMX-CSV vs SDMX-JSON 1.0 vs 2.0 streaming writers on identical Arrow batches; an optional geography case reads the real bundle | small / medium / large rows |

### Phase 3 — navigation tax

| File | Bench | Sizes |
|---|---|---|
| `test_navigation.py` | Elegant attribute-walk vs hand-built dict baseline; single lookup, full walk, code_map | 5×50, 10×200 |

Tells you what the elegant `dsd.dimensions["FREQ"].codelist.codes` style
actually costs versus pre-resolving everything into a plain dict. Useful
as a baseline before optimising hot paths in the model layer.

### Competitive — sdmxlib vs sdmx1 vs pysdmx

| File | Bench | Fixtures |
|---|---|---|
| `test_compete.py` | Parse + serialize across three SDMX libraries | ECB DSD (490 KB), ESTAT HICP dataflow + descendants (3.8 MB) |

Auto-skipped unless the optional `bench-compete` dep group is installed:

```bash
uv sync --group bench-compete
```

Fixtures live at `tests/fixtures/sdmx_ml21/ecb_data_structure.xml` and
`tests/fixtures/sdmx_ml21/estat_hicp_descendants.xml.gz` — the only two
in our corpus all three libraries round-trip without raising
(sdmx1 doesn't implement `<ReleaseCalendar>`; pysdmx rejects the
`ContentConstraint type=` attribute on `ecb_dataflow.xml`). The 3.8 MB
fixture is gzipped to keep the repo lean (compresses ~95% — 200 KB on
disk, expanded once per session).

Synthetic fixtures via `tests/bench/fixtures.py` — deterministic, no XML
files involved, sizes are tunable.

## Adding benchmarks

Drop a `test_*.py` under `tests/bench/`. Use the `benchmark` fixture
for wall — pytest-memray picks up the same tests automatically when
run with `--memray`. No marker needed; `conftest.py` adds `bench` to
every test in the directory.

## The bundle tier (#379, milestone %18)

Everything above runs on fixtures `tests/bench/fixtures.py` builds in
Python. The largest size parameter in the original phases is **10k** items,
and none of them carry annotations at all — while `group_annotations_by_owner`
was measured at 52% of `q.code…collect()` on a real bundle when this tier was
written (#377, since fixed). So a second tier reads one.

| File | What it guards |
|---|---|
| `test_bundle_reads.py` | The read path (#379, %18): largest codelist and hierarchy, discovered rather than named |
| `test_bundle_observations.py` | The data plane, against 1.1 B observations (#391, %19) |
| `test_bundle_writes.py` | The write path (#140) |

```bash
export SDMXLIB_BENCH_BUNDLE=/path/to/bundle/<token>   # a publication: holds bundle.manifest.json
just bench-bundle
```

Marked `bundle` as well as `bench`, and **deselected from `just bench`**
(`-m "bench and not bundle"`). That split is the point: the bundle is
2.0 GB and cannot be in the repo, so leaving it in would make
`just bench` mean one thing on the machine that has the bundle and
another everywhere else — and `bench/baselines/NAME/wall.json` would
hold a different set of rows depending on who saved it.

**Resolution.** `$SDMXLIB_BENCH_BUNDLE`, else skip
(`tests/bench/_bundle.py`). Setting the variable is a statement that you have
the bundle, so a path that does not resolve **raises**; unset and
absent **skips**, with its own line in the run summary.

That asymmetry is the whole reason `tests/bench/_bundle.py` exists.
The tier's predecessor in `test_data_writers` carried its own resolution
whose fallback pointed at a sibling-workspace path that did not exist at the
time. It reported that as three `SKIPPED` lines in the tail of the output and
stayed dead for months. (`test_data_writers` now resolves through `_bundle`
too.) CLAUDE.md's standing note covers it exactly:
*a finding that cannot fail is a finding nobody acts on.*
`tests/unit/test_bench_bundle_resolution.py` guards the rule, and runs
in `just ci` where the tier itself never will.

**Assertions, not just measurements.** `just bench-bundle` omits
`--benchmark-only`, because this tier carries count assertions
alongside its benchmarks. `just bench-bundle-assertions` runs only those
(`--benchmark-skip`) and is part of `just ci`, which skips it in ~0 s and
says so on its own line on a machine without the bundle. Counts rather than clocks, per CLAUDE.md —
the defects in %18 are all *work done*, identical on a busy laptop and
an idle one.

Assertions for defects that are still open carry
`xfail(strict=True)` naming their issue, as
`tests/unit/test_roundtrip_property_guard.py` does. Strict is the
mechanism: when the fix lands the assertion starts passing, pytest
turns that XPASS into a failure, and the marker must come off in the
same commit. So the recipe is green today and cannot quietly stay
green through a fix.

## Not yet covered

- Federation / `FederatedRegistry` dispatch overhead
- `RestRegistry` against a live provider — the sweep (below) measures what
  real registries serve, not how fast we fetch it

## Sibling harnesses under `bench/`

Each has its own README; none is a pytest-benchmark suite.

| Directory | What it is | Recipe |
|---|---|---|
| [`concurrency/`](concurrency/README.md) | Concurrent read throughput, committed baselines pinned to library versions — ops/sec at N workers, per-worker error counts | `just bench-concurrency` |
| [`compete/`](compete/README.md) | sdmxlib vs pysdmx vs sdmx1 on one BIS corpus: speed, content agreement, schema validity | `just bench-compete`, `just bench-compete-scale` |
| [`sweep/`](sweep/README.md) | Fetches every bulk structure query public registries serve and measures what we refuse, lose, or write invalidly — a committed summary per registry | `just sweep REGISTRY…` |
| [`fmr/`](fmr/README.md) | FMR as an external oracle for SDMX-ML 2.1/3.0: records what a local FMR serves back for our fixtures | `just fmr-record` (replayed by `just test-integration`) |
| [`pysdmx_oracle/`](pysdmx_oracle/README.md) | pysdmx as the SDMX-ML 3.1 oracle FMR cannot be | `just pysdmx-record` (replayed by `just test-integration`) |
