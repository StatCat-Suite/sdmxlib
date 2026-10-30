# sdmxlib Blueprint

## System Overview

sdmxlib fetches, models, stores and queries SDMX: structural metadata,
reference metadata and observation data, from SDMX REST APIs, SDMX files or a
local DuckDB store. Users navigate a typed graph of frozen artefacts by
attribute access, query it with the `sl.q` path language, and read
observations through `obs()` as Polars frames. A published *edition* (a DuckDB
metadata store, a DuckLake of observations and a `manifest.json`) is
the unit the sibling repositories build on: statcatlib publishes catalogs of
editions, and sdmxlib-rest serves one over SDMX REST.

This repository holds two distributions released together at one version:
`sdmxlib` (`src/`) and `sdmxlib-cli` (`cli/`). `README.md` is the user guide
and PyPI description; `CLAUDE.md` holds the project's working rules; the
design documents are under `docs/design/`.

## Subdirectories & System Boundaries

- src: [`./src/EGM.md`](./src/EGM.md) - the `sdmxlib` package: model, wire
  formats, DuckDB storage and editions, the query language, the REST
  registries. Its index is [`./src/sdmxlib/EGM.md`](./src/sdmxlib/EGM.md).
- cli: [`./cli/EGM.md`](./cli/EGM.md) - `sdmxlib-cli`, the `sdmxlib edition`
  commands (`check`, `fetch`, `copy`, `files`) as thin wrappers over
  `sdmxlib.edition`.
- tools: [`./tools/EGM.md`](./tools/EGM.md) - repository gates run from the
  justfile (docstring style, prose style, the `Any` ratchet, the package-index
  allow-list), the XSD table generator, the MkDocs issue-link hook and the
  mutation-testing wrappers. Nothing here is imported by the package.
- fuzz: [`./fuzz/EGM.md`](./fuzz/EGM.md) - the Atheris runner for the fuzz
  targets in `tests/fuzz/targets.py`, run by `.github/workflows/fuzz.yml`.

`tests/`, `bench/`, `docs/`, `history/` and `examples/` carry no
contract: `.egmignore` lists all but `tests/`, which EGM's own rules exclude.
Each `bench/` tool has a `README.md`.

## Global Invariants

- Layering: `sdmxlib.model` imports nothing from `formats/`, `storage/`,
  `local/` or `api/`. Its domain types do no I/O; its one behavioural class,
  `rollup.AncestorMap`, queries DuckDB. `formats/` owns parsing and
  serialisation of the SDMX wire formats; edition and manifest JSON is read
  and written in `storage/` and `bundle/`. `query/` imports nothing from
  `local/`.
- Every domain object in `sdmxlib.model` is an attrs `@frozen` class; an
  artefact is equal by value and keyed by URN, and a change is an
  `attrs.evolve` copy. Every outgoing edge is a `Ref[T]`.
- A reader refuses what it cannot read with `SdmxParseError` (a
  `ValueError`); a value that does not fit its component's type is the one
  "does not fit its component's type" `ValueError`.
- SQL binds every value as a parameter; only validated identifiers are
  interpolated.
- Every stored timestamp is naive UTC (`_time.py`).
- `sdmxlib-cli`'s version and its `sdmxlib==` pin equal sdmxlib's version.
- Every type named in a public signature, and every exception a public
  docstring lists under `Raises:`, is importable from `sdmxlib`
  (`test_public_surface`); every public signature's annotations evaluate at
  runtime.
- pyrefly is the only type checker and ruff runs with `select = ["ALL"]`;
  both stay at zero findings, and a suppression needs the maintainer's
  approval (`CLAUDE.md`, "Protected files & suppressions").

## Global Side Effects

- DuckDB database files and DuckLake catalogs: a `LocalRegistry` store, a
  edition's `<edition_id>/metadata.duckdb` and its shared `data/` directory.
- HTTP requests to SDMX REST endpoints (`api/`), and reads from object
  stores for a remote edition's data files.
- Files written by publishing (`manifest.json`, last), by copies, and
  by an observation query's `sink_*`.
- Thread-local DuckDB cursors; spill files in the system temp directory.

## System Verification

- Inner loop: `just check` (lint, format, docstrings, prose, `Any`, indexes,
  workflows, pyrefly) and `just test` (unit tests).
- The gate: `just ci`, run locally; it adds the integration, docs, edition
  and object-store tiers and `just audit`. The hosted workflows are
  `check.yml` (`just check`), `docs.yml`, `fuzz.yml`, `zizmor.yml`,
  `scorecard.yml` and `release.yml`. None blocks a merge, except that `main`
  requires the `check` status.
