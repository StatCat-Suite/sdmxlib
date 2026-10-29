# sdmxlib_cli

## Overview

The `sdmxlib` command, a typer app shipped as the separate `sdmxlib-cli`
distribution so that `sdmxlib` itself never depends on typer (GH-67). Each
command is a thin wrapper over `sdmxlib.bundle`: the module parses arguments,
calls one library function, and prints its result. The library tests the
behaviour behind each command; `tests/unit/test_cli.py` tests the wrapping.

## Interface

- `app` — the root `typer.Typer`; the console script `sdmxlib = "sdmxlib_cli:app"`
  in `cli/pyproject.toml`. It holds one sub-app, `bundle`.
- `sdmxlib bundle check PATH --at {publish,receive,open} [--expect EARLIER]` —
  `check(path, *, at, expect)` calls `check_bundle(path, at=..., expect=...)`,
  prints each finding and a summary line
  (`<path>: N finding(s); checked ...`), and exits 1 when the report is not
  `ok`, 0 otherwise. `--at` is required.
- `sdmxlib bundle fetch SOURCE FLOW PATH [--key] [--start] [--end]
  [--structures-only] [--api 2.1] [--source-id]` — `fetch(...)` calls
  `fetch_bundle` and prints `<path>/<token>: <bundle_id>, <counts>; token <token>`.
  `PATH` must be absent or empty (`fetch_bundle` raises `FileExistsError`
  otherwise); the publication lands in `<path>/<token>/` and its data files in
  `<path>/data/`. `--api` and `--source-id` apply only to a URL `SOURCE`; a
  provider carries its own API version.
- `sdmxlib bundle copy SOURCE DESTINATION [--tiers 1,2,3]` — `copy(...)` calls
  `copy_bundle` and prints the destination, bundle id, token and file count.
- `sdmxlib bundle files MANIFEST [--tiers 1,2,3] [--manifest]` — `files(...)`
  reads the manifest with `read_manifest` and prints `bundle_files(...)` one
  path per line, for `rclone --files-from -` or azcopy; `--manifest` prints
  only `<token>/bundle.manifest.json`.
- `At` — `StrEnum` of `--at`'s choices (`publish`, `receive`, `open`), mapped
  to the library's `Profile` literals by `_PROFILES`.
- Private parsers: `_source(text)` (a `sl.Provider` by case-insensitive name,
  or the text itself when it contains `://`), `_flow(text)` (`AGENCY:ID(VERSION)`,
  `AGENCY:ID` meaning `latest`, or a `urn:` string, to `SdmxUrn[Dataflow]`),
  `_tiers(text)` (comma-separated `1`/`2`/`3` to `list[Tier]`).

## Invariants

- Commands hold no logic of their own beyond argument parsing and printing;
  behaviour changes belong in `sdmxlib.bundle`.
- A `SOURCE` that is neither a provider name nor a URL, and a bad tier, raise
  `typer.BadParameter` naming the accepted forms. `_flow` raises
  `BadParameter` only for an empty agency or id; a malformed URN or short form
  falls through to the library's `SdmxParseError`, and a malformed version
  such as `ECB:X(bad v)` is accepted as a URN (GH-265).
- `bundle files` accepts either a publication directory or a
  `bundle.manifest.json` staged elsewhere (its parent is used). Its listing
  leaves the manifest out and `--manifest` prints the manifest's path alone,
  so a copy tool moves the manifest in a second step, after every other file.
- `cli/pyproject.toml`'s version and its `sdmxlib==` pin equal sdmxlib's
  version; `test_the_cli_is_locked_to_sdmxlibs_version` asserts the three
  agree, and `just` release steps bump them together.
- `cli/LICENSE` is byte-for-byte sdmxlib's licence (asserted by a test).

## Side Effects

- `check` and `files` read the bundle (manifest, files, DuckDB catalog) and
  write nothing.
- `fetch` makes HTTP requests to the SDMX REST source and creates a new
  publication directory under `PATH`.
- `copy` writes files to `DESTINATION` and the shared data directory beside
  it, verifies them, and writes the manifest last.
- All commands write to stdout; `check` sets the process exit status.

## Verification

- Run: `uv run pytest tests/unit/test_cli.py`
- Full gate: `just ci`
