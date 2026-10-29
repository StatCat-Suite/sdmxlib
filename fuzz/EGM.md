# fuzz

## Overview

`fuzz/` holds the Atheris runner for sdmxlib's fuzz targets
([GH-241](https://github.com/StatCat-Suite/sdmxlib/issues/241)). The targets
themselves, what each feeds its bytes to and which exceptions it allows, live in
`tests/fuzz/targets.py`. This directory only drives them under libFuzzer.

Atheris ships Linux x86_64 wheels only, so the runner runs in
`.github/workflows/fuzz.yml` and not on macOS. That job fuzzes each target for
10 minutes weekly and for 1 minute on a pull request that touches `fuzz/**`,
`tests/fuzz/**`, a reader or the workflow.

## Interface

- `run.py` (script): `PYTHONPATH=. uv run --group fuzz python fuzz/run.py TARGET CORPUS [libFuzzer flags]`
  - `TARGET`: a key of `tests.fuzz.targets.TARGETS`: `ml21`, `ml30`, `ml31`,
    `json`, `detect`, `data_json`, `data_csv`. An unknown name exits with the
    list of valid ones.
  - `CORPUS`: a directory; created if missing.
  - Remaining arguments pass to libFuzzer (for example `-max_total_time=600`,
    `-timeout=10`, `-rss_limit_mb=2048`, `-artifact_prefix=crashes/<target>/`).
- `run.main()`: copies the target's seeds into the corpus, calls
  `atheris.Setup` with `-max_len=targets.MAX_LEN` (65536), and runs
  `atheris.Fuzz()` with a callback that calls `targets.check(target, data)`.
- `__init__.py`: package marker with a docstring only.

## Invariants

- `tests.fuzz.targets` is imported inside `atheris.instrument_imports(include=["sdmxlib"])`,
  so only `sdmxlib` code is coverage-instrumented. Any new import of sdmxlib
  modules that the fuzzer should explore must happen through that block.
- Every run copies the current seeds into the corpus as
  `seed-<parent dir>-<file name>`, so a corpus restored from the CI cache still
  holds the current seeds. `targets.seeds` leaves out any fixture over
  `MAX_LEN` (65536) bytes, so such a file never enters the corpus.
- A finding is any exception `targets.check` refuses (raised as
  `UnexpectedReaderError`), or a hang or unbounded memory that libFuzzer
  reports. The allowed exceptions are defined in `tests/fuzz/targets.py`, not
  here.
- `fuzz/` is outside pyrefly's scope because `atheris` cannot be resolved where
  the checker runs; ruff lints and formats it (`just lint`, `just fmt`).
- A crash input that the hosted job finds is saved under
  `tests/fuzz/crashes/<target>/` with its fix, and
  `tests/unit/test_fuzz_crashes.py` replays it on every platform without Atheris.

## Side Effects

- Creates the corpus directory and writes seed copies into it.
- libFuzzer writes new corpus inputs into `CORPUS` and crash artefacts under
  the `-artifact_prefix` path.
- Runs CPU-bound until libFuzzer stops (time limit, crash, or signal).

## Verification

- Replay saved crashes (any platform): `uv run pytest tests/unit/test_fuzz_crashes.py`
- Full local gate: `just ci`
- Fuzzing itself (Linux x86_64 only): `uv sync --group fuzz`, then
  `PYTHONPATH=. uv run python fuzz/run.py ml30 corpus/ml30 -max_total_time=60`
