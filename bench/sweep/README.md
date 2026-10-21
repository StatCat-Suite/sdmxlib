# Registry sweep

The model is 3.x-native — milestone %20 (#423): it holds everything SDMX 3.x
carries, projects to 2.1 with every loss declared, and enforces the
format-independent rules as an artefact is built. The sweep was written to
measure that against what real registries publish, during the transition in
which they are moving from .Stat NSI (2.1 inside) to FMR (3.0 inside), and it
keeps measuring it.

It is a measuring instrument, not a gate. It needs the network and fetches
gigabytes, so it is never part of `just ci`. Its output is a committed
**summary** per registry, so an MR can show exactly which numbers it moved.

```bash
just sweep OECD             # fetch (cached) + analyse one registry
just sweep all              # every registry
just sweep-run OECD         # re-analyse the cache only — the inner loop
just sweep-show OECD        # the non-empty findings, readable
just sweep-delta OECD       # what moved since main (or: just sweep-delta OECD <ref>)
```

## What runs, per document

Every bulk structure query each API names (`endpoints.py`), including the
resources the model has no type for. That is how the sweep finds out what
registries actually publish.

| stage | asks | counts |
|---|---|---|
| `parse_error` | does the document (or each chunk) parse at all | — |
| `read_refused` | artefacts the reader refused — for contradicting the schema (#435), or for a rule the model refuses as it builds one (#492); the rest read on | artefacts per class and rule |
| `store_refused` | `LocalRegistry.add` | artefacts per rule |
| `store_diff` | the artefact read back from the store, diffed against itself | artefacts per field path |
| `targets → refused` | `check_serialisable` per artefact, per format | artefacts per rule |
| `targets → gaps` | `unrepresentable()` — the declared losses | artefacts per field |
| `targets → write_error` | the writer raising | artefacts per error |
| `targets → schema_invalid` | the output against the vendored XSD / JSON schema | occurrences per error |
| `read_lost` / `read_gained` | element paths in the source vs our re-write in the same format | occurrences per path |
| `cross_api` | the 2.1 and 3.0 reading of the same artefact, diffed | artefacts per field path |

`read_lost` is the read-side twin of `gaps.py` that #422 asks for, without
needing to be told what to look for: whatever we fail to write back, we failed
to read or failed to write. `read_lost_derivable` (full report only) holds the
paths `census.DERIVABLE` names with a reason — `@urn`, a boolean at its
schema default — so they do not bury the real losses.

Findings are **rule keys**: the message with its URNs, quoted ids and numbers
removed (`analyse.rule_key`), so equal problems count together and two runs
compare. The full report keeps one real example per key.

## Reading the cross-API diff

The left value is the 2.1 reading, the right one the 3.0 reading. What a
difference *means* depends on the engine (`endpoints.Endpoint.engine`):

- **NSI** (OECD): 2.1 inside, so its 3.0 document is a translation. A
  difference is a translation loss *or* ours.
- **FMR** (registry.sdmx.org): 3.0 inside, so its 3.0 document is the native
  one, and the 3.x reference the others are moving towards.
- **unverified**: nobody has checked. Fix the table when someone does.

Expect `legacy_is_final` wherever a 2.1 document declares a `1.0` final: 3.x
has no `isFinal` and reads finality from the version, so the 2.1 word is kept
beside it and 3.x cannot carry it (#420, #424).

## Files

| path | committed | what |
|---|---|---|
| `summaries/<REGISTRY>.json` | yes | counts only, maps cut to 40 rows |
| `out/raw/<REGISTRY>-<api>/` | no | the cached documents and their `.meta.json` |
| `out/reports/<REGISTRY>.json` | no | everything, with examples |

`fetch` re-requests only what is not settled — a 429, a gateway error, a
dropped connection, or a 200 whose body is not SDMX. A 404 or 501 is the
registry's answer and stays cached; `--refresh` re-asks everything. Requests
to one host are sequential with a pause, because OECD and registry.sdmx.org
both rate-limit.

Registry quirks the fetcher absorbs, each found by a crash:

- **Eurostat** sends some bodies as raw gzip with no `Content-Encoding`, and
  answers an overloaded request with a 200 HTML page. The first is
  decompressed by its magic bytes, the second is treated as a transient
  failure.
- **Eurostat's 3.0 API** refuses `~` for an all-artefact query and returns
  nothing for `+` (none of its versions is semver-final), so it is asked for
  `*`: every version, which for codelists is 3.1 GB.

## Cost, and how much of the machine it takes

Documents are independent, so `run` analyses several at once — as many as
this machine has **free right now** (`capacity.py`), printed as the first
line of every run:

```
machine: 10 cores, load 3.7; 10.7 GB available (vm_stat), budget 6.4 GB → 6 worker(s)
```

- **Workers**: idle cores (cores minus the one-minute load), minus one left
  for whoever is using the machine; at least one.
- **Memory**: 60 % of *available* memory (not installed) is a budget. Each job
  carries an estimate from the bytes it parses at once, and starts only while
  its estimate fits beside the running ones. A job bigger than the whole
  budget still runs — alone.
- macOS reads `vm_stat`; Linux reads `MemAvailable` and, inside a container,
  the cgroup limit. Elsewhere it runs one document at a time.
- `--jobs N` overrides all of it; `--jobs 1` is the old sequential run.

Work runs in two phases through the same scheduler:

1. **Split.** A document above `split.SPLIT_ABOVE` (256 MB) is cut into chunk
   files of whole artefacts under `out/chunks/`, one task per document. The
   chunks are kept, with a manifest of the source's size and mtime and the
   budget, so a re-run reuses them until the document is re-fetched.
2. **Analyse.** One task per whole document, per 2.1/3.0 pair (the cross-API
   diff needs both parsed messages in one process), and **per chunk**.

Chunk counts are merged in chunk order, which gives exactly the counts one
process would get reading the whole document — `tests/unit/test_sweep.py`
asserts the two reports are equal. A pair with a side too large to read whole
is analysed as two documents, and its `cross_api` entry says `skipped`.

Every task runs in a fresh process and records its own `peak_mb`, which is
what the estimate is calibrated from (`capacity._BYTES_FACTOR` lists the
measurements). Results are assembled in a fixed order, so the summary does not
depend on scheduling: BIS, OECD and IMF give the same summaries in parallel as
sequentially. IMF went from 18 min and 8.0 GB to 7.4 min with no process over
2.7 GB.

A split document's summary carries `chunks: N`. The census does not count
container elements (`Codelists`): how many a document has depends on how it
was batched or chunked, never on what it says.
