# pysdmx as an SDMX-ML 3.1 oracle

The FMR oracle (`bench/fmr/`) covers SDMX-ML 2.1 and 3.0, but FMR 12.4.2
reads and serves no 3.1 — `test_fmr_oracle.py` holds it to that. Until one
does, [pysdmx](https://github.com/bis-med-it/pysdmx) is the independent 3.1
implementation (#457): it reads our 3.1 maximal fixture and 3.1 shape, writes
back what it read as 3.1, and our reader diffs that against what was sent.

```bash
just pysdmx-record       # needs nothing running; writes tests/integration/pysdmx/<version>/
just test-integration    # replays the recordings offline; part of `just ci`
```

pysdmx is the optional `bench-compete` group. The recipe installs it into a
throwaway environment, so pandas, which pysdmx brings, never reaches the
project's. `bench/compete/` compares the same libraries on a BIS corpus, for
speed and agreement on what all three hold; this compares field by field,
on fixtures that set every field.

## What a recording holds

Per document (`3.1-maximal`, `3.1-shape`):

| file | what |
|---|---|
| `sent.xml` | the document pysdmx read: our writer's output with the ledger's adjustments applied |
| `necessity.json` | pysdmx's failure with each adjustment left out in turn |
| `read.json` | how many artefacts of each of its classes pysdmx read |
| `back.xml` | what pysdmx's writer made of its reading, in SDMX-ML 3.1 |

And once, `pysdmx.json`: the version.

## The two ledgers

- **`ledger.py`** — what is removed before pysdmx will take a document.
  pysdmx's reader raises on an element or attribute its model has no field for
  (`pysdmx-gap`), on a few it does model (`pysdmx-bug`), and its writer
  refuses a nameless value item (`pysdmx-writer`).
- **`tests/support/pysdmx_differences.py`** — every difference between what
  was sent and what pysdmx wrote back, read through our reader, beyond what
  `formats.gaps` declares for 3.1: `pysdmx-drops`, `pysdmx-changes`,
  `pysdmx-normalises`, `pysdmx-skips` or `pysdmx-invalid`. Each reason says
  whether the loss is pysdmx's model, its reader or its writer, from its
  in-memory reading.

Both are checked both ways by the replay test, as the FMR ones are.

**A whole artefact class can vanish silently.** pysdmx's reader skips a class
it has no model for — the scheme maps, geographic codelists — without an
error, while it raises on an unknown field inside a class it does model.
`read.json` and the `MISSING` differences are how that shows.
