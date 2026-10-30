# Seal cost

What sealing a product's next edition costs, phase by phase: `LocalRegistry.build(previous=)`
copies the previous edition's store and puts back the keys the published store
leaves out (GH-165), and `write_manifest` fingerprints, settles the lake,
counts and surveys each binding, and compacts the store again (GH-248). Not
part of `just ci`: the census run reads the 2 GB published census bundle and
takes about five minutes per seal.

```bash
# A CODR table: build its first edition from StatCan's WDS, then seal a monthly update.
uv run python -m bench.seal.build_codr <raw> <product>
uv run --with psutil python -m bench.seal.measure_seal codr <product> <first edition> 5 <raw>/14100287.csv

# Census: seal a correction edition of an APFS clone of the published bundle
# (cp -cR <bundle>/data <product>/data; cp -cR <bundle>/<edition> <product>/), so the original is only read.
uv run --with psutil python -m bench.seal.measure_seal census <product> <edition> 3
```

`<raw>` holds the table's WDS answers: `metadata.json` from
`POST https://www150.statcan.gc.ca/t1/wds/rest/getCubeMetadata` with
`[{"productId":14100287}]`, and `14100287.csv` unzipped from the URL
`getFullTableDownloadCSV/14100287/en` returns.

Each phase is timed by wrapping the function that does it. psutil, when
installed, adds the disk's write counter around each phase; it is
system-wide, so it counts any other writer too.

## Thresholds

Set before measuring, on GH-248:

- **Census:** the key restore takes at most 60 s. Both alternatives GH-248
  names (strip keys only in the sealed copy, or carry an unstripped copy
  forward) remove only that step.
- **CODR:** the whole seal of a one-month update takes at most 10 s, so 7,000
  tables reseal in about 5 h on 4 workers.

## Results

2026-10-30, sdmxlib at `5acf218d` (0.79.0), DuckDB 2.0.0, Apple M5 with 10
cores and 24 GB, APFS. Medians.

| phase | census (3 runs) | written | CODR 14100287 (5 runs) | written |
|---|---|---|---|---|
| copy of the previous store | 0.25 s | 497 MiB | 0.00 s | 7 MiB |
| key restore | **6.34 s** | 793 MiB | 0.01 s | 0.1 MiB |
| append one month | | | 0.66 s | |
| artefact fingerprints | 66.48 s | 26 MiB | 0.03 s | |
| `settle_lake` | 0.03 s | | 0.01 s | |
| binding counts (table fingerprint) | 217.36 s | 63 MiB | 0.64 s | |
| binding survey | 6.40 s | 10 MiB | 0.09 s | 0.1 MiB |
| `compact_for_publication` | 14.92 s | 1,796 MiB | 0.05 s | 18 MiB |
| file digests | 0.83 s | | 0.02 s | |
| **seal** | **314.31 s** | 3,231 MiB | **1.60 s** | 37 MiB |

| `metadata.duckdb` | census | CODR |
|---|---|---|
| previous edition, sealed | 499 MiB | 8 MiB |
| after the key restore | 1,231 MiB | 8 MiB |
| this edition, sealed | 499 MiB | 8 MiB |

Peak resident memory: 4.8 GiB for the first census seal, 0.8 GiB for CODR.

Both are under their thresholds, so the seal keeps its design: the census key
restore takes 6.3 s of 60, and the CODR seal 1.6 s of 10.

The copy is an APFS clone here, so it costs no time; on a filesystem without
clones it is a full copy of the store, 499 MiB for census. Most of the census
seal is the binding counts, which recompute the table fingerprint over 1.14
billion rows (217 s, 69 % of the seal), and the artefact fingerprints (66 s).
Every design recomputes both, including for an edition that changes no
observation.
