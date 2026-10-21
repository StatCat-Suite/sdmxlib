# Concurrency / read-path benchmarks

Pinned baseline numbers used to gauge perf impact of the concurrency
refactor in issues #75–#79. Not part of `just ci` — these run on demand.

## Usage

```bash
just bench           # run all benches, print delta vs baselines.json
just bench-update    # overwrite baselines.json with current numbers
```

Workloads live in `bench_concurrency.py`. Baselines live in
`baselines.json`, pinned to a specific library version (`_version` key in
the file). Regressions print visibly but do **not** fail the run — bumping
the baseline is an explicit decision via `just bench-update`.

## What's measured

| Workload | What it stresses |
|---|---|
| `single_get_dataflow` | bare `reg.get(urn)` cost — leaf artefact |
| `single_get_codelist_eager` | `reg.get(urn, eager=True)` — Ref resolution path |
| `single_code_map` | `reg.code_map(...)` — direct SQL helper |
| `concurrent_get_mixed_{N}w` | mixed `reg.get` at N workers, ops/sec |
| `concurrent_cache_hit_32w` | 32 threads × same URN — the FIFO-cache canary |

## Notes on running on `main` (pre-fix)

Concurrent workloads will produce errors on `main` (the bug from #75).
The harness counts those errors and reports them alongside throughput.
Throughput numbers in a buggy state are still a useful comparison
*lower bound* — once #78 lands, errors should drop to zero and
throughput should equal or exceed the buggy version's apparent ceiling.
