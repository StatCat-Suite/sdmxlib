# sdmxlib development tasks

# A gitignored `.env` sets per-machine variables without exporting them — the
# bundle tier's `SDMXLIB_BENCH_BUNDLE`, chiefly, which has no default location.
set dotenv-load

default:
    @just --list

# ── Dev ───────────────────────────────────────────────────────────────────────

# Ruff, with its *warnings* treated as failures too (#283).
#
# Ruff exits 0 on a `warning:` line, so a warning never fails the gate — and
# the working habit that develops is to filter them out of check output as
# noise. Two of them ("Invalid `# noqa` directive", from the literal text
# `# noqa` appearing inside a doc comment) survived three milestones that way.
# A finding that cannot fail is a finding nobody acts on.
#
# ruff 0.16 has no native warnings-as-errors flag; re-check when it does.
[group('dev')]
lint:
    #!/usr/bin/env bash
    set -uo pipefail
    out=$(uv run ruff check src tests bench 2>&1)
    status=$?
    echo "$out"
    if [[ $status -ne 0 ]]; then exit $status; fi
    if grep -q "^warning:" <<< "$out"; then
        echo "error: ruff emitted warnings; they fail the gate (#283)." >&2
        exit 1
    fi

# pyrefly at `preset = "all"` minus a documented deny-list (see pyrefly.toml)
# is the only type checker (#425). Keep it at 0 findings.
#
# It covers `src`, `tests` and `bench`. `bench/` is in because it was outside
# every gate and rotted to an ImportError unnoticed: the API moved (`Registry`
# -> `LocalRegistry`, `urn` -> `urn_for`) and nothing said so. `tests/` carries
# two subtractions, in pyrefly.toml's `[[sub-config]]`. Dead suppressions fail
# here too — `unused-ignore` and `unused-type-ignore` are both on — which is
# what retired `check-dead-ignores`.
[group('dev')]
typecheck:
    uv run pyrefly check

[group('dev')]
checkfmt:
    uv run ruff format --check src tests bench

# Docstrings stay pure Google style — no RST roles, no NumPy sections (#374).
#
# Three arms, because `ruff.toml`'s `convention = "google"` is not the gate it
# looks like: ruff's pydocstyle rules check a docstring's *structure* and never
# read its markup. The grep this replaced covered the roles only; the other two
# arms catch a NumPy section header and, via pydoclint's DOC003, a Sphinx
# field-list docstring (`:param x:`) that carries no role and no underline for
# a grep to find.
[group('dev')]
check-docstrings:
    #!/usr/bin/env bash
    set -euo pipefail
    uv run python tools/check_docstrings.py --check

# Fail if `Any` in src/ grows past the recorded baseline (#284).
#
# `ANN401` bans an `Any`-annotated parameter, which is what CLAUDE.md claims
# is enforced — but it does not look *inside* generic arguments, and that is
# where 112 of the 171 occurrences live. `def f(x: Any)` is a finding;
# `def f(x: dict[str, Any])` is silent.
#
# No linter in the stack has a rule for it, so this is a ratchet rather than
# a rule: the same technique the pyrefly deny-list uses, where a number that
# cannot grow stands in for a check nobody has written. Each issue in %16
# lowers `bench/any_baseline.txt`, and lowering it is a visible diff.
[group('dev')]
check-any:
    #!/usr/bin/env bash
    set -euo pipefail
    uv run python tools/count_any.py --check

# Only public PyPI may be named in this repository. Development happens on
# machines that resolve through a private corporate mirror, and a lockfile,
# an `index-url`, or a stray `.netrc` would publish one organisation's
# internal infrastructure to every consumer of a public library. The guard is
# an allow-list rather than a search for the mirror's hostname: writing that
# hostname into the gate would itself be the leak.
[group('dev')]
check-indexes:
    #!/usr/bin/env bash
    set -euo pipefail
    uv run python tools/check_indexes.py --check

# Rewrite `formats/sdmx_ml/_allowed_children.py` from the vendored XSDs (#436).
#
# Run after refreshing `tests/schemas/`. The readers refuse an artefact holding
# an element its schema allows nowhere under that parent, and this table is
# what "allows" means; `test_closed_world.py` fails while it is stale.
[group('dev')]
generate-allowed-children:
    uv run python tools/generate_allowed_children.py

# Lint + typecheck (inner dev loop)
[group('dev')]
check: lint checkfmt check-docstrings check-any check-indexes typecheck

# Format code
[group('dev')]
fmt:
    uv run ruff format src tests bench
    uv run ruff check --fix src tests bench

# ── Test ──────────────────────────────────────────────────────────────────────

# Run unit tests, in parallel across every core.
#
# Serial, the unit tier was 8m31s of `just ci`'s ~10 min; on 8 workers it is
# ~3m10s with the same tests and the same Hypothesis example counts — this
# changes how fast the suite runs, not what it checks. `--dist worksteal`
# measured the same as the default, so the default stays.
#
# `-p no:benchmark`: pytest-benchmark refuses to run under xdist, and the
# warning it raises is an error under our `filterwarnings`. No unit test uses
# the `benchmark` fixture (the bench tier does, and runs serially); one that
# did would fail here with "fixture not found", not pass silently.
#
# Then the `serial` tests, alone. Each races 16 or 32 threads of DuckDB reads
# (#75), and under `-n` the other workers keep every core busy, so each thread
# waits on a CPU slice to take the GIL back after every query: 22 s alone
# became 125-149 s in the pool, and it set the tier's wall time. Run after,
# they cost their own ~29 s, and the whole tier 170-220 s became ~140 s.
[group('test')]
test:
    uv run pytest -m "unit and not serial" -n auto -p no:benchmark
    uv run pytest -m "unit and serial" -p no:benchmark

# Run documentation example tests
[group('test')]
test-docs:
    uv run pytest -m docs

# Run all tests with coverage — same selection as `just ci`, so the reported
# number is the one the gate actually enforces.
[group('test')]
coverage:
    uv run pytest -m "unit or docs or integration" --cov --cov-report=term-missing --cov-report=xml --cov-report=html
    @echo "HTML report: htmlcov/index.html"

# Run integration tests (replays from cassettes; no network required)
[group('test')]
test-integration:
    uv run pytest -m integration --block-network

# Run the same tests against real SDMX REST endpoints (requires network).
# --disable-recording bypasses VCR entirely; without it the cassettes replay
# and nothing reaches the network, which is what this recipe used to do.
[group('test')]
test-live:
    uv run pytest -m integration --disable-recording

# Re-record VCR cassettes from live endpoints (requires network)
[group('test')]
record-cassettes:
    uv run pytest -m integration --record-mode=all

# Re-record ONE test's cassette (requires network). Prefer this to
# `record-cassettes`: that rewrites all of them, so a change to one test
# arrives as sixty modified files and a reviewer cannot see which recording
# actually moved. Takes a `-k` expression.
#
# just record-cassette test_resolve_partial_false_has_more_codes_than_partial
[group('test')]
record-cassette selector:
    uv run pytest -m integration --record-mode=all -k "{{ selector }}"

# Run the bundle tier's **assertions** — everything that asserts a property,
# without the eight pytest-benchmark measurements that only report a number.
#
# The split is by what the two halves are *for*, not by what they cost: an
# assertion has to fail when the code regresses, a measurement is a table you
# read. `--benchmark-skip` happens to draw exactly that line, since the
# measuring tests are the ones taking the `benchmark` fixture.
#
# In `ci` because a tier the gate never runs cannot fail, and #386 proved it:
# renaming `resolve_cache_size` to `resolve_cache_items` passed `just check`,
# passed 6,284 unit tests, and left this tier with 19 errors that nobody would
# have seen. Skips in ~0 s on a machine without the bundle, and says so.
[group('test')]
bench-bundle-assertions *ARGS:
    uv run pytest tests/bench -m "bench and bundle" --benchmark-skip {{ BENCH_COLS }} -rs {{ ARGS }}

# Full CI gate: lint + typecheck + unit + docs + cassette-replayed integration
# + the bundle tier's assertions.
# Integration is in the gate because it replays offline in ~3 s and covers the
# HTTP-facing modules that unit tests structurally cannot. The bundle
# assertions are in it because they are the only thing standing between a
# rename in `LocalRegistry`'s public API and a silently broken tier — ~96 s on
# the machine that holds the bundle, nothing anywhere else.
[group('test')]
ci: check test test-docs test-integration bench-bundle-assertions docs-build

# Run the property-based tests with 20x the examples (local hunting; #215)
[group('test')]
test-properties:
    HYPOTHESIS_PROFILE=deep uv run pytest -m unit -k propert

# ── Mutation testing ──────────────────────────────────────────────────────────
# Local only, never CI. Coverage says a line ran; this says whether a test would
# have noticed it being wrong. See #213 and docs/design/mutation-testing.md.
#
# Budget an hour or more for the full scope, not the "~11 min" the pilot cost in
# #213 — the suite has roughly tripled since. Use `mutate-module` while working;
# the full run is for establishing a baseline.
#
# Two traps, both paid for in #252:
#
#   * Changing `[tool.mutmut]` in pyproject.toml alters the config fingerprint
#     and forces a full stats recollection — several minutes before the first
#     mutant runs. Time a scoped run *without* touching config, or you are
#     timing the recollection. (This invalidated one of #252's experiments.)
#   * mutmut forks per mutant, and a child that dies is booked as a timeout.
#     `tests/conftest.py` sets `no_proxy` so httpx cannot reach macOS
#     SystemConfiguration, which is not fork-safe and segfaulted 356 children.

# Mutate ONE module against ONE narrow set of tests. Start here.
#
#   just mutate-area src/sdmxlib/model/codelist.py tests/unit/test_codelist_skos.py
#
# Narrow is not only faster, it is more *correct*. mutmut forks each mutant off
# a parent that has already run the whole selected suite in-process, and
# children forked from a heavy parent are pathological — they get killed by
# RLIMIT_CPU and booked as timeouts (#303). Measured on the same function:
#
#   full test selection    14 mutants   14 timeouts   93 s
#   one test file          16 mutants    0 timeouts    7 s   <- correctly survived
#
# The score is "does this test file pin this code", not a module-wide score.
# See tools/mutate_area.py.
[group('mutate')]
mutate-area MODULE +TESTS:
    @uv run python tools/mutate_area.py {{ MODULE }} {{ TESTS }}

# Run mutation over the whole configured scope. Hours — prefer `mutate-area`
# unless you need a baseline. Goes through `tools/mutmut_forksafe.py`. That
# wrapper runs mutmut's fork-server mode (`process_isolation`), which keeps
# polars out of the forking parent (#314, and most of #303). It also preloads
# the native extensions and credits import-time hits (#458).
[group('mutate')]
mutate *ARGS:
    uv run python tools/mutmut_forksafe.py run --max-children 8 {{ ARGS }}

# Mutate one module or function:
#   just mutate-module sdmxlib.model.codelist
#   just mutate-module sdmxlib.model.validation.x__validate_hierarchy
#
# mutmut has no path flag, and it does **not** accept a prefix — passing one
# fails with "Filtered for specific mutants, but nothing matches". Only exact
# mutant names work, so this expands the prefix against `mutmut results` first.
#
# That means the module must already be in `only_mutate` and have been
# collected once. Widening the scope requires editing pyproject, which changes
# the config fingerprint and forces a full stats recollection — see the note
# above. `just mutate-scope` prints what is currently configured.
[group('mutate')]
mutate-module PREFIX *ARGS:
    #!/usr/bin/env bash
    set -uo pipefail
    names=$(uv run mutmut results | tr -d ' ' | cut -d: -f1 | grep "^{{ PREFIX }}" || true)
    if [ -z "$names" ]; then
        echo "no mutants match '{{ PREFIX }}'" >&2
        echo "is it in only_mutate? run: just mutate-scope" >&2
        exit 1
    fi
    echo "$(echo "$names" | wc -l | tr -d ' ') mutants match '{{ PREFIX }}'"
    uv run mutmut run --max-children 8 {{ ARGS }} $names

# What `only_mutate` currently covers, and how big each entry is
[group('mutate')]
mutate-scope:
    @uv run python tools/mutate_scope.py

# Show surviving mutants
[group('mutate')]
mutate-results:
    uv run mutmut results

# Show one mutant's diff: just mutate-show sdmxlib.model.validation.x_validate_references__mutmut_4
[group('mutate')]
mutate-show MUTANT:
    uv run mutmut show {{ MUTANT }}

# ── Benchmarks ────────────────────────────────────────────────────────────────
# Local-only performance harness. Excluded from `just test` / `just ci` via
# the `bench` marker. See bench/README.md for the workflow.

# Calibration for microsecond-scale benchmarks. The query-DSL compile
# benchmarks run at 2-8 us, where pytest-benchmark's defaults time a *single*
# iteration per round: one scheduler blip or GC pause lands whole in a sample.
# Measured on the query suite, defaults vs these flags:
#
#   stddev / median, single-predicate compile   14%  ->  1.5%
#   iterations per round                          1  ->  1000
#   high-side outliers (of ~32k rounds)        1611  ->  3
#
# `min-time` makes calibration pack many iterations into each round so timer
# overhead amortises; `disable-gc` removes the long tail; `warmup` settles
# caches. Medians barely move under these — it was always the *spread* that
# was untrustworthy, which is why comparisons must read median and IQR, never
# mean and stddev.
#
# Run-to-run drift on identical code with these flags is <= 2%. Treat a delta
# under ~3% as noise; 5% is probably real; 10% certainly is.
BENCH_OPTS := "--benchmark-min-time=0.0005 --benchmark-disable-gc --benchmark-warmup=on"
BENCH_COLS := "--benchmark-columns=median,iqr,outliers,rounds,iterations --benchmark-sort=name"

# `-m "bench and not bundle"` deselects the real-bundle tier (#379). That tier
# reads a 2.6 GB bundle which is not, and cannot be, in the repo — so leaving
# it in would make `just bench` mean one thing on the machine that has the
# bundle and another everywhere else, and `bench/baselines/NAME/wall.json`
# would hold a different set of rows depending on who saved it. Run it with
# `just bench-bundle`.
#
# Run wall-time benchmarks (pytest-benchmark), synthetic fixtures only
[group('bench')]
bench *ARGS:
    uv run pytest tests/bench -m "bench and not bundle" --benchmark-only {{ BENCH_OPTS }} {{ BENCH_COLS }} {{ ARGS }}

# Run memory benchmarks (pytest-memray); --benchmark-disable so each test runs once
[group('bench')]
bench-mem:
    uv run pytest tests/bench -m "bench and not bundle" --memray --benchmark-disable

# Run both wall + memory
[group('bench')]
bench-all: bench bench-mem

# Needs $SDMXLIB_BENCH_BUNDLE pointing at a bundle directory (one holding
# `meta.duckdb`), in the environment or a `.env`. Unset, the
# tier skips and says so on its own line. *Set and wrong* is an error — that
# distinction is the issue: the previous real-data hook pointed at a workspace
# that no longer existed and reported it as three skips in the tail of the
# output for months.
#
# No `--benchmark-only`: this tier carries count assertions alongside its
# benchmarks, and those are the part that must fail when the code regresses.
# Assertions for open defects are `xfail(strict=True)` against their issue, so
# this recipe is green today and goes red the moment a fix lands without its
# marker removed.
#
# Run the real-bundle read benchmarks (#379, milestone %18)
[group('bench')]
bench-bundle *ARGS:
    uv run pytest tests/bench -m "bench and bundle" {{ BENCH_COLS }} -rs {{ ARGS }}

# sdmxlib against pysdmx and sdmx1 on one BIS corpus in SDMX-ML 2.1, 3.0 and
# SDMX-JSON 2.0: read/write speed, content agreement, and whether each
# library's output is schema-valid and readable by the others. Needs the
# optional `bench-compete` group; a report for humans, never in `ci`. See
# bench/compete/README.md.
#
# Compare sdmxlib with pysdmx and sdmx1 (speed, agreement, validity)
[group('bench')]
bench-compete *ARGS:
    uv run --group bench-compete python -m bench.compete {{ ARGS }}

# sdmxlib and pysdmx on single large codelists: UNSD CL_COMMODITY (68k
# codes), ECB CL_PROVIDER_FM_ID (104k), synthetic ones to 500k, and the
# bundle's CL_GEOGRAPHY (580k) when present. Fetches ~6 min on first run.
# sdmx1 is left out: its 2.1 writer is quadratic, minutes per round at 500k.
#
# Compare sdmxlib and pysdmx on very large codelists (scaling)
[group('bench')]
bench-compete-scale *ARGS:
    uv run --group bench-compete python -m bench.compete.scale {{ ARGS }}

# Network, and gigabytes on first fetch — never in `ci`. Fetches into the
# gitignored cache, then rewrites bench/sweep/summaries/. See
# bench/sweep/README.md.
#
# just sweep OECD          # one registry
# just sweep all           # every registry in bench/sweep/endpoints.py
#
# Sweep live registries through parse → validate → store → every writer (%20)
[group('bench')]
sweep +REGISTRIES:
    uv run python -m bench.sweep fetch {{ REGISTRIES }}
    uv run python -m bench.sweep run {{ REGISTRIES }}

# For iterating on sdmxlib itself, which is what the summaries measure.
#
# Re-analyse the cached registry documents without fetching
[group('bench')]
sweep-run +REGISTRIES:
    uv run python -m bench.sweep run {{ REGISTRIES }}

# Print a sweep summary's non-empty findings
[group('bench')]
sweep-show +REGISTRIES:
    uv run python -m bench.sweep show {{ REGISTRIES }}

# Needs a local Fusion Metadata Registry you may write to — never a public one,
# and never in `ci`, which replays the recordings instead
# (tests/integration/test_fmr_oracle.py). See bench/fmr/README.md.
#
# Record what FMR makes of our documents, for the replay test (#457)
[group('bench')]
fmr-record URL='http://127.0.0.1:8080':
    uv run python -m bench.fmr record --url {{ URL }}

# pysdmx is the optional `bench-compete` group, installed into a throwaway
# environment (`--isolated`) so it never lands in the project's: it brings
# pandas, which changes DuckDB's timings. Never in `ci`, which replays the
# recordings instead (tests/integration/test_pysdmx_oracle.py). See
# bench/pysdmx_oracle/README.md.
#
# Record what pysdmx makes of our SDMX-ML 3.1, for the replay test (#457)
[group('bench')]
pysdmx-record:
    uv run --isolated --group bench-compete python -m bench.pysdmx_oracle record

# What moved in one registry's sweep summary since REF — paste into the MR
[group('bench')]
sweep-delta REGISTRY REF='main':
    #!/usr/bin/env bash
    set -euo pipefail
    before=$(mktemp)
    git show "{{ REF }}:bench/sweep/summaries/{{ REGISTRY }}.json" > "$before" 2>/dev/null || true
    uv run python -m bench.sweep delta "$before" "bench/sweep/summaries/{{ REGISTRY }}.json"
    rm -f "$before"

# Snapshot the current bench results under bench/baselines/NAME/
[group('bench')]
bench-save NAME:
    #!/usr/bin/env bash
    set -euo pipefail
    DIR="bench/baselines/{{ NAME }}"
    mkdir -p "${DIR}"
    uv run pytest tests/bench -m "bench and not bundle" --benchmark-only {{ BENCH_OPTS }} \
        --benchmark-json="${DIR}/wall.json" --benchmark-columns=median,iqr
    uv run pytest tests/bench -m "bench and not bundle" --memray --benchmark-disable --memray-bin-path="${DIR}/mem" --memray-bin-prefix=mem
    echo "Saved baseline → ${DIR}"

# Diff current wall+mem against a saved baseline NAME; report-only
[group('bench')]
bench-compare NAME:
    #!/usr/bin/env bash
    set -euo pipefail
    DIR="bench/baselines/{{ NAME }}"
    if [[ ! -f "${DIR}/wall.json" ]]; then
        echo "error: no baseline at ${DIR}" >&2
        exit 1
    fi
    uv run pytest tests/bench -m "bench and not bundle" --benchmark-only --benchmark-compare="${DIR}/wall.json" --benchmark-columns=mean,median --benchmark-sort=name
    echo ""
    echo "Memory: re-run \`just bench-mem\` and eyeball against ${DIR}/mem/"

# ── Docs ──────────────────────────────────────────────────────────────────────

# Serve MkDocs dev server (hot-reload)
[group('docs')]
docs:
    uv run mkdocs serve

# Build static MkDocs site. `--strict` because a dangling mkdocstrings
# reference is otherwise a warning nobody reads: the §6 rename left
# `::: sdmxlib.local.registry.Registry` behind and the site went on building
# with a hole where the class reference should be. In the gate for that
# reason — the docs are part of the deliverable and the only check that reads
# every `:::` directive.
[group('docs')]
docs-build:
    uv run mkdocs build --strict

# ── Bench (concurrency suite) ─────────────────────────────────────────────────
# Standalone concurrency stress harness (issues #75–#79). Lives under
# bench/concurrency/. Distinct from the general pytest-benchmark suite
# defined above.

# Run concurrency benchmarks; compare to bench/concurrency/baselines.json
[group('bench')]
bench-concurrency:
    uv run python bench/concurrency/bench_concurrency.py

# Overwrite bench/concurrency/baselines.json with current numbers
[group('bench')]
bench-concurrency-update:
    uv run python bench/concurrency/bench_concurrency.py --update

# ── Release ───────────────────────────────────────────────────────────────────

# Step 1 — Preview next version and unreleased changelog (no files changed)
[group('release')]
release-1-preview:
    @echo "Next version: $(git-cliff --bumped-version)"
    @echo ""
    @git-cliff --bump --unreleased

# Step 2 — Write CHANGELOG.md (auto-version; pass VERSION to override)
[group('release')]
release-2-changelog VERSION='':
    #!/usr/bin/env bash
    # By default git-cliff determines the next version from commit messages.
    # Pass VERSION to pin (e.g. when a "!" breaking-change marker would push
    # an unwanted major bump):
    #   just release-2-changelog              # auto
    #   just release-2-changelog 0.14.0       # pin
    set -euo pipefail
    OVERRIDE="{{ VERSION }}"
    if [[ -n "${OVERRIDE}" ]]; then
        NEXT="v${OVERRIDE#v}"
        echo "Writing changelog pinned to ${NEXT}..."
        git-cliff --tag "${NEXT}" -o CHANGELOG.md
        NEXT_HINT=" ${NEXT#v}"
    else
        NEXT=$(git-cliff --bumped-version)
        echo "Writing changelog for ${NEXT}..."
        git-cliff --bump -o CHANGELOG.md
        NEXT_HINT=""
    fi
    echo ""
    echo "Review CHANGELOG.md, then run: just release-3-tag${NEXT_HINT}"

# Step 3 — Bump version + commit + tag (auto-version; pass VERSION to override)
[group('release')]
release-3-tag VERSION='':
    #!/usr/bin/env bash
    # Pass VERSION to pin (must match whatever was passed to
    # release-2-changelog):
    #   just release-3-tag                    # auto
    #   just release-3-tag 0.14.0             # pin
    set -euo pipefail
    OVERRIDE="{{ VERSION }}"
    if [[ -n "${OVERRIDE}" ]]; then
        VER="${OVERRIDE#v}"
    else
        VER=$(git-cliff --bumped-version)
        VER="${VER#v}"
    fi
    uv version "${VER}"
    git add CHANGELOG.md pyproject.toml
    git commit -m "chore(release): v${VER}"
    git tag -a "v${VER}" -m "v${VER}"
    echo "Tagged v${VER} — run: just release-4-push"

# Step 4 — Push commits + tags to GitLab and mint a release, then publish to PyPI
[group('release')]
release-4-push:
    #!/usr/bin/env bash
    set -euo pipefail
    VER=$(git describe --tags --abbrev=0)
    git push
    git push --tags
    NOTES=$(git-cliff --latest --strip all)
    glab release create "${VER}" --name "${VER}" --notes "${NOTES}"
    echo "Released ${VER} on GitLab"
    echo "Next: just release-5-pypi"

# Step 5 — Build and publish to PyPI (requires UV_PUBLISH_TOKEN env var)
[group('release')]
release-5-pypi:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -z "${UV_PUBLISH_TOKEN:-}" ]]; then
        echo "error: UV_PUBLISH_TOKEN is not set" >&2
        exit 1
    fi
    VER=$(git describe --tags --abbrev=0)
    # `uv publish` defaults to public PyPI, but a shell configured for a
    # private mirror redirects it silently. Check the target by allow-list,
    # for the same reason `just check-indexes` does.
    ALLOWED="upload.pypi.org test.pypi.org"
    TARGET="${UV_PUBLISH_URL:-https://upload.pypi.org/legacy/}"
    HOST=$(printf '%s' "${TARGET}" | sed -E 's#^[a-z]+://([^/]+).*#\1#')
    if ! grep -qw -- "${HOST}" <<< "${ALLOWED}"; then
        echo "error: publish target is not a public index (${HOST})" >&2
        echo "       unset UV_PUBLISH_URL to publish to PyPI." >&2
        exit 1
    fi
    rm -rf dist
    uv build
    uv publish
    echo "Published ${VER} to PyPI"
