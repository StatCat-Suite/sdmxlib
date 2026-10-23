"""Resolving a real SDMX bundle for the benchmark suite, and the shapes in it.

Every other fixture under `tests/bench/` is built in Python by `fixtures.py`,
and the largest size parameter anywhere is 10k items. That is not a criticism
of those benchmarks — it is why a second kind exists. The costs milestone %18
is about are invisible at 10k, and one of them is invisible at *any* size a
synthetic fixture reaches: `fixtures.py` attaches no annotations at all, while
`group_annotations_by_owner` is 52% of `q.code…collect()` on a real bundle
(#377).

So this tier reads a bundle that is not, and cannot be, in the repo.

## Resolution, and why a bad path is an error

`$SDMXLIB_BENCH_BUNDLE` names a bundle directory, and it is the only route:
unset, the tier skips and says so. There is no default location — a path
baked in here would name one machine's layout, and the justfile's
`set dotenv-load` lets a gitignored `.env` carry the variable instead.

Setting the variable is a *statement that you have the bundle*, so a path that
does not resolve raises instead of skipping. That distinction is the whole
point of this module. The one real-data hook that existed before it —
`test_data_writers._census_geo_path` — fell back to a sibling workspace that
did not exist then, and reported it as three skips at the tail of a suite
nobody reads the tail of:

    9 passed, 6 skipped
    SKIPPED [3] census codelist not available — checkout the sibling workspace

CLAUDE.md's standing note applies exactly: *a finding that cannot fail is a
finding nobody acts on.* `conftest.pytest_terminal_summary` prints the
skip reason as its own line for the same reason.

## Discovery, rather than hard-coded URNs

The benchmarks below ask the bundle which artefacts to use — its largest
codelist, its largest hierarchy, the smallest hierarchy that places codes from
that codelist. Naming `CL_GEOGRAPHY` here would make the tier a test of one
census bundle; asking makes it a test of whatever bundle it is pointed at,
and the shape each benchmark needs is a property any real bundle has.

It also keeps the numbers honest when the bundle is rebuilt: `H_CP_GEO_FED`
was "~700k nodes" in #154 and is 499,138 today.
"""

import hashlib
import os
import re
import shutil
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import duckdb

from sdmxlib.bundle import CANONICAL_FORM, FORMAT_VERSION
from sdmxlib.local import LocalRegistry
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.query.observations import Observations
from sdmxlib.storage.schema import SCHEMA_VERSION, get_schema_version
from tests.support.repo import ROOT

#: Names a bundle directory — one holding `meta.duckdb`. Set it and the tier
#: runs; set it wrongly and the tier raises rather than skipping.
ENV_VAR = "SDMXLIB_BENCH_BUNDLE"

#: What makes a directory a bundle at all. The *observation* tier needs more —
#: see `DATA_PLANE_FILES` — but requiring that here would refuse a
#: structural-only bundle for the structural benchmarks, which do not read a
#: single observation.
REQUIRED = "meta.duckdb"

#: What the observation tier needs on top of `REQUIRED` (#391).
#:
#: Checked per *test* rather than folded into `REQUIRED`, because the two tiers
#: want different things from one directory: a bundle with only `meta.duckdb`
#: should still run every structural benchmark and skip these, saying which
#: file it lacked.
DATA_PLANE_FILES = ("lake.ducklake", "parquet")

#: Ceiling on the partition the observation benchmarks materialise.
#:
#: "The biggest one" is the obvious choice and was wrong: the 2021 Census Profile bundle's
#: largest is `DisseminationArea` at **842,209,475** rows, and collecting it a
#: few times took this tier from two minutes to **35**. The partition only has
#: to be large enough for the clause costs to dominate — %19 measured its 4.5x
#: and 2600x on a 4.26 M-row one — so the largest *under* this bound is both
#: representative and affordable. On the census bundle it picks
#: `FederalElectoralDistrict`, 4,915,280 rows.
MAX_BENCH_PARTITION_ROWS = 10_000_000


def data_plane_missing(path: Path) -> str | None:
    """The first `DATA_PLANE_FILES` entry this bundle lacks, or `None`.

    Names the file rather than answering yes/no, so a skip says *which* half of
    the bundle is absent. "No data plane" and "no `parquet/`" are different
    situations for whoever built the directory.
    """
    for name in DATA_PLANE_FILES:
        if not (path / name).exists():
            return name
    return None


def data_plane_skip_reason(path: Path, missing: str) -> str:
    """Why the observation half is not running.

    Names `$SDMXLIB_BENCH_BUNDLE` deliberately: `conftest.pytest_terminal_summary`
    matches on it to decide whether to report the tier as unrun, and since #390
    these assertions are part of `just ci` — a skip the summary cannot see is a
    gap in the gate that prints nothing.
    """
    return (
        f"bundle at {path} has no {missing}, so the observation tier cannot run. "
        f"Point ${ENV_VAR} at a bundle with a data plane to run it."
    )


def schema_skip_reason(conn: duckdb.DuckDBPyConnection) -> str | None:
    """Why this bundle cannot be read by this build, or `None` if it can.

    The store is a cache and migration is drop-and-rebuild, so a bundle built
    by an older sdmxlib is not readable here — and this tier opens it
    *read-only* by design, so it cannot rebuild it either.

    A skip rather than a raise, for the reason `data_plane_skip_reason` gives:
    the bundle exists and the path is right, one half of it is just not usable
    for this run. Names both versions, because "rebuild it" is only actionable
    if you can see what changed. Names `$SDMXLIB_BENCH_BUNDLE` so
    `conftest.pytest_terminal_summary` reports the tier as unrun on its own
    line — a schema bump silently un-gating twenty assertions is exactly what
    that hook exists to prevent.
    """
    found = get_schema_version(conn)
    if found == SCHEMA_VERSION:
        return None
    return (
        f"the bundle at ${ENV_VAR} is on schema {found or 'none'} and this build "
        f"uses {SCHEMA_VERSION}, so it cannot be read. Rebuild the bundle to run "
        f"this tier."
    )


def schema_skip_reason_for(path: Path) -> str | None:
    """`schema_skip_reason` for a bundle *directory*, opening it read-only.

    For callers that are about to open the store for write — the check has to
    happen before that, since a mismatched version is dropped and rebuilt
    rather than refused.
    """
    conn = connect(path)
    try:
        return schema_skip_reason(conn)
    finally:
        conn.close()


class BundlePathError(Exception):
    """`$SDMXLIB_BENCH_BUNDLE` is set and does not name a usable bundle.

    Deliberately not a `pytest.skip`. Skipping here is what let a stale path go
    unnoticed for months — see the module docstring.
    """


def bundle_path() -> Path | None:
    """The bundle directory, or None when there is nothing to run against.

    Raises:
        BundlePathError: if `$SDMXLIB_BENCH_BUNDLE` is set but does not name a
            directory containing `meta.duckdb`. The message says which of the
            two failed, because "no bundle" and "wrong path" must not read
            alike.
    """
    declared = os.environ.get(ENV_VAR)
    if declared:
        path = Path(declared)
        if not path.is_dir():
            msg = f"${ENV_VAR} is set to {declared!r}, which is not a directory"
            raise BundlePathError(msg)
        if not (path / REQUIRED).is_file():
            msg = f"${ENV_VAR} is set to {declared!r}, which has no {REQUIRED}"
            raise BundlePathError(msg)
        return path
    return None


def skip_reason() -> str:
    """Why the tier is not running — shown per-test and in the run summary."""
    return (
        f"no bundle to benchmark against: ${ENV_VAR} is unset. Set it to a bundle "
        f"directory (one holding {REQUIRED}) — in the environment or a gitignored .env — "
        f"to run this tier."
    )


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    """A read-only connection to the bundle's structural store.

    Read-only because a benchmark must not alter the thing it measures, and
    because DuckDB's file lock is exclusive: a concurrent writer on the same
    bundle (a catalog build, say) otherwise surfaces as an opaque IO error
    halfway through a run.
    """
    conn = duckdb.connect(str(path / REQUIRED), read_only=True)
    # Bounded so a runaway scan fails as a DuckDB error rather than as an
    # OOM kill, which reports nothing and takes the session with it.
    conn.execute("SET memory_limit='8GB'")
    return conn


@dataclass(frozen=True)
class Shapes:
    """The four artefacts the read benchmarks run against, and their sizes.

    Discovered per bundle rather than named. `small_hierarchy_over_codelist` is
    the one that needs explaining: it is the *smallest* hierarchy placing codes
    from `codelist`, and it exists to isolate #378 — an eager read whose cost
    comes from the codelist its nodes reference rather than from its own size.
    Pairing its node count against `codelist_codes` is what makes that visible.
    """

    #: `SdmxUrn`, not `str`. The bundle stores URNs as text and these are read
    #: straight out of SQL, so parsing here is the boundary: `get` takes an
    #: identity, and a bare string stopped being one in #370. Parsing at
    #: discovery also means a bundle holding an unparseable URN fails *there*,
    #: naming the row, rather than at the first read that uses it.
    codelist: SdmxUrn[object]
    codelist_codes: int
    big_hierarchy: SdmxUrn[object]
    big_hierarchy_nodes: int
    small_hierarchy_over_codelist: SdmxUrn[object]
    small_hierarchy_nodes: int


def discover(conn: duckdb.DuckDBPyConnection) -> Shapes:
    """Ask the bundle which artefacts have the shapes these benchmarks need.

    Raises:
        BundlePathError: if the bundle holds no codelist, no hierarchy, or no
            hierarchy placing codes from its largest codelist. All three are
            "this bundle cannot exercise the tier", which is a setup failure
            rather than a skip.
    """
    codelist = conn.execute(
        """
        SELECT codelist_urn, count(*) AS n
        FROM sdmx.code
        GROUP BY 1
        ORDER BY n DESC
        LIMIT 1
        """
    ).fetchone()
    if codelist is None:
        msg = "bundle has no codes — nothing for the read benchmarks to read"
        raise BundlePathError(msg)

    big = conn.execute(
        """
        SELECT hierarchy_urn, count(*) AS n
        FROM sdmx.hierarchy_node
        GROUP BY 1
        ORDER BY n DESC
        LIMIT 1
        """
    ).fetchone()
    if big is None:
        msg = "bundle has no hierarchy nodes — nothing for the hierarchy benchmarks to walk"
        raise BundlePathError(msg)

    # An item URN reuses its parent scheme's suffix (`…Code=AGENCY:ID(VER).item`),
    # so the codelist's own URN prefixes every code URN under it once the class
    # segment is rewritten. Bound as a parameter, never interpolated.
    code_urn_prefix = str(codelist[0]).replace(".Codelist=", ".Code=") + ".%"
    small = conn.execute(
        """
        SELECT hierarchy_urn, count(*) AS n
        FROM sdmx.hierarchy_node
        WHERE code_urn LIKE ?
        GROUP BY 1
        ORDER BY n ASC
        LIMIT 1
        """,
        [code_urn_prefix],
    ).fetchone()
    if small is None:
        msg = f"bundle has no hierarchy placing codes from {codelist[0]} — #378's shape is absent"
        raise BundlePathError(msg)

    return Shapes(
        codelist=SdmxUrn.parse(str(codelist[0])),
        codelist_codes=int(codelist[1]),
        big_hierarchy=SdmxUrn.parse(str(big[0])),
        big_hierarchy_nodes=int(big[1]),
        small_hierarchy_over_codelist=SdmxUrn.parse(str(small[0])),
        small_hierarchy_nodes=int(small[1]),
    )


@contextmanager
def counting(cls: type, method: str = "__init__") -> Generator[list[int]]:
    """Count constructions of *cls* over the block, per CLAUDE.md.

    The assertions in this tier are counts rather than clocks because the
    defects are *work done*: a discarded object per row costs the same whether
    the laptop is idle or compiling. A wall-clock ceiling on a 2.6 GB bundle
    measures the machine at least as much as the code.

    Yields a one-element list so the count is readable after the block:

        with counting(Level) as n:
            ...
        assert n[0] == 0

    Same monkeypatch shape as `test_materialize_builds_no_level_it_then_discards`
    (`ee10df9`), which is the guard that caught #154 and whose absence on the
    sibling builder is #377.
    """
    total = [0]
    original: Callable[..., object] = getattr(cls, method)

    def counting_method(*args: object, **kwargs: object) -> object:
        total[0] += 1
        return original(*args, **kwargs)

    setattr(cls, method, counting_method)
    try:
        yield total
    finally:
        setattr(cls, method, original)


@dataclass(frozen=True)
class ObsShapes:
    """The one dataflow the observation benchmarks run against, and its shape.

    Discovered per bundle rather than named, for the reason `Shapes` gives:
    writing `DF_CP_2021` and `GEO_LEVEL` here would make this a test of one
    census build instead of a test of whatever bundle it is pointed at.

    `sidecar` is the field that decides whether #388's benchmarks can run at
    all — pruning a join is not observable on a binding that has no join.
    """

    dataflow_urn: str
    #: Physical column the observations are partitioned by, or `None`.
    partition: str | None
    #: The partition the benchmarks run on: large enough for the %19 ratios to
    #: be real, small enough to materialise a few times — see
    #: `MAX_BENCH_PARTITION_ROWS`. And the smallest one, for contrast. Both
    #: `None` when the binding declares no partition column.
    big_partition: str | None
    big_partition_rows: int
    small_partition: str | None
    #: Whether the binding puts series attributes in a separate table.
    sidecar: bool
    #: An attribute id that lives on the sidecar, for the "does reference it"
    #: half of #388. `None` when there is no sidecar.
    sidecar_component: str | None


def discover_observations(registry: LocalRegistry) -> ObsShapes:
    """Ask the bundle which dataflow to benchmark, and what shape it has.

    Takes the registry rather than a connection because everything here comes
    from the *binding* — which table, which partition column, which components
    live on the sidecar — and only a registry reads bindings.

    Raises:
        BundlePathError: if the bundle binds no dataflow. That is "this bundle
            cannot exercise the tier", which `discover` also treats as a setup
            failure rather than a skip.
    """
    entries = registry.browse()
    if not entries:
        msg = "bundle binds no dataflow — nothing for the observation benchmarks to read"
        raise BundlePathError(msg)
    entry = entries[0]
    binding = entry.binding
    obs = registry.obs(entry.dataflow_urn)
    root = obs.plan.root
    assert isinstance(root, Observations), f"an observation query is rooted at Observations, not {type(root).__name__}"

    partition = root.__partitions__[0] if root.__partitions__ else None
    big = small = None
    big_rows = 0
    if partition is not None:
        # One grouped scan, 1.4 s over the census bundle's 1.14 B rows — the
        # partition column *is* the physical layout, so this is cheap.
        rows = (
            obs._cursor()
            .execute(
                f"SELECT {partition}, count(*) n FROM {binding.database_role}.{binding.table_location} "
                f"GROUP BY 1 ORDER BY n DESC"
            )
            .fetchall()
        )
        if rows:
            affordable = [r for r in rows if int(r[1]) <= MAX_BENCH_PARTITION_ROWS]
            # Largest under the bound, or the smallest there is when every
            # partition exceeds it — skipping in that case would make the tier
            # silently absent on exactly the biggest bundles.
            chosen = max(affordable, key=lambda r: int(r[1])) if affordable else rows[-1]
            big, big_rows = str(chosen[0]), int(chosen[1])
            small = str(rows[-1][0])

    sidecar = root.sidecar
    component = None
    if sidecar is not None:
        component = next(
            (
                cid
                for cid in root.component_ids
                if root._table_of(root[cid].column) == sidecar.table and root[cid].column not in sidecar.join_keys
            ),
            None,
        )
    return ObsShapes(
        dataflow_urn=str(entry.dataflow_urn),
        partition=partition,
        big_partition=big,
        big_partition_rows=big_rows,
        small_partition=small,
        sidecar=sidecar is not None,
        sidecar_component=component,
    )


# ── the published copy, cached across sessions (GH-127) ──────────────────


#: The modules whose source decides what a publication holds: a change to any
#: of them republishes the cached copy.
_PUBLISHING = (
    "src/sdmxlib/bundle/_write.py",
    "src/sdmxlib/bundle/_fingerprint.py",
    "src/sdmxlib/bundle/_canonical.py",
    "src/sdmxlib/storage/_manifest.py",
    "src/sdmxlib/storage/schema.py",
)


#: A cache entry's name: a key, or a key being built.
_ENTRY = re.compile(r"(\.building-)?[0-9a-f]{64}")


def cache_root() -> Path:
    """Where published copies are kept: ``$SDMXLIB_BENCH_CACHE``, else ``~/.cache/sdmxlib/bench``."""
    configured = os.environ.get("SDMXLIB_BENCH_CACHE")
    return Path(configured) if configured else Path.home() / ".cache" / "sdmxlib" / "bench"


def publication_key(source: Path) -> str:
    """What a published copy of `source` depends on, hashed: the source's files and the publishing code."""
    digest = hashlib.sha256()
    for name in ("meta.duckdb", "lake.ducklake", "bundle.manifest.json"):
        path = source / name
        stat = path.stat() if path.exists() else None
        digest.update(f"{name}:{stat.st_size if stat else -1}:{stat.st_mtime_ns if stat else -1}\n".encode())
    for part in (SCHEMA_VERSION, FORMAT_VERSION, CANONICAL_FORM, duckdb.__version__):
        digest.update(f"{part}\n".encode())
    for module in _PUBLISHING:
        digest.update((ROOT / module).read_bytes())
    return digest.hexdigest()


def cached_publication(key: str) -> Path | None:
    """The published publication cached under `key`, or None."""
    entry = cache_root() / key
    manifests = sorted(entry.glob("*/*/bundle.manifest.json"))
    return manifests[0].parent if len(manifests) == 1 else None


def cache_publication(key: str, build: Callable[[Path], Path]) -> Path:
    """Publish with `build` into the cache under `key`, dropping every other key's copy.

    Built beside the cache under a temporary name and renamed into place, so
    an interrupted build never reads as a cached one.
    """
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True)
    for stale in root.iterdir():
        # Only this cache's own entries: the directory may be one a user named.
        if _ENTRY.fullmatch(stale.name) and stale.name != key:
            shutil.rmtree(stale)
    staging = root / f".building-{key}"
    shutil.rmtree(staging, ignore_errors=True)
    publication = build(staging / "census")
    staging.rename(root / key)
    return root / key / "census" / publication.name
