"""LocalRegistry — DuckDB-backed persistent SDMX artefact store.

All tables live in the ``sdmx`` DuckDB schema. The registry creates
a cursor with ``search_path = 'sdmx'`` for clean internal SQL.

Path-style construction: ``LocalRegistry(uri)`` is a cheap value object that
stashes the URI; the DuckDB connection is opened by ``open()`` /
``__enter__``. Use as a context manager::

    import sdmxlib as sl

    # Populate from a remote registry
    with sl.RestRegistry("https://sdmx.oecd.org/public/rest/") as remote:
        dsd = remote.DataStructure(agency="OECD", id="DSD_BLI").resolve()
        with sl.LocalRegistry("oecd.duckdb") as local:
            local.ingest(remote.registry)

    # Use later without network
    with sl.LocalRegistry("oecd.duckdb") as local:
        dsd = local.get(dsd_urn)
        cm = local.code_map(codelist_urn, lang="en")

    # Share an existing DuckDB connection (e.g. with statcatlib): use the
    # ``from_connection`` classmethod, which doesn't take ownership of the
    # connection's lifecycle.
    import duckdb

    conn = duckdb.connect("shared.duckdb")
    with sl.LocalRegistry.from_connection(conn) as local:
        local.add(...)
"""

import functools
import logging
import re
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from importlib import metadata
from pathlib import Path
from typing import ClassVar, Final, Literal, Self, cast, overload, override

import attrs
import duckdb

from sdmxlib._duckdb import count, first
from sdmxlib.data_store import WriteSource
from sdmxlib.formats.gaps import TARGETS, describe, unrepresentable
from sdmxlib.formats.sdmx_csv.reader import _resolve_structure
from sdmxlib.formats.sdmx_json.data import read_observations
from sdmxlib.local._scan import resolve_flow, scan_csv_into, scan_frame_into
from sdmxlib.local.data_store import DuckDBDataStore, store_path
from sdmxlib.local.engine import BundleEngine, spill_directory
from sdmxlib.local.object_store import data_uri, sql_literal
from sdmxlib.model._binding import binding
from sdmxlib.model._invariants import advise
from sdmxlib.model._items import find_item, owner_classes
from sdmxlib.model._refshape import can_reach_ref, walk_plan
from sdmxlib.model._tree import tree_rows
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.binding import DataflowBinding, DataflowEntry, DriftError, DriftIssue, _validate_binding
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ArtefactList, ItemList, Maintainable
from sdmxlib.model.constraint import AvailabilityConstraint
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.errors import Refusal
from sdmxlib.model.hierarchy import AmbiguousHierarchyError, HierarchicalCode, Hierarchy
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.metadataset import MetadataSection, MetadataSet, ReportedAttributeValue
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.ref import Ref, SupportsUrn, UnresolvedRef, target_urn, urn_of
from sdmxlib.model.registry import artefact_urn
from sdmxlib.model.representation import Facet
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import SdmxConformanceError, ValidationError, ValidationIssue, metadata_sets_advisories
from sdmxlib.model.version_rewrite import (
    RewrittenVersion,
    VersionRewrite,
    check_ingestable,
    collision,
    dangling,
    names,
    original_urn,
    rewrite_versions,
)
from sdmxlib.query import accessors as query_accessors
from sdmxlib.query._expr import Cols, RowKind
from sdmxlib.query.accessors import ArtefactRow, ReportedAttributeRow
from sdmxlib.query.observations import ObsQuery
from sdmxlib.query.query import Query, Resolver, build
from sdmxlib.query.raw import RawSql, prepare
from sdmxlib.session import SessionFactory, adopt
from sdmxlib.storage import readers, resolve, writers
from sdmxlib.storage._kinds import CLASS_TO_NAME
from sdmxlib.storage._kinds import NAME_TO_CLASS as _TYPE_REGISTRY
from sdmxlib.storage._manifest import (
    DATA_PATH,
    MANIFEST_NAME,
    Manifest,
    ManifestError,
    read_manifest,
    refuse_engine_mismatch,
)
from sdmxlib.storage._trim import restore
from sdmxlib.storage.lazy import LazyCodelist, LazyHierarchy
from sdmxlib.storage.readers import get_availability, make_lazy_codelist
from sdmxlib.storage.schema import (
    SCHEMA_VERSION,
    ReadOnlySchemaError,
    StoreSchemaError,
    check_scan_integrity,
    connection_is_read_only,
    get_schema_version,
    holds_rows,
    meta_value,
    migrate,
    schema_exists,
    sdmx_catalog,
    use_sdmx,
)

_SIBLING_INDEX = re.compile(r"\[\d+\]")


def _sdmxlib_version() -> str:
    """This sdmxlib's version, for a message; ``"unknown"`` when not installed as a package."""
    try:
        return metadata.version("sdmxlib")
    except metadata.PackageNotFoundError:
        return "unknown"


def _declared_path(attribute_path: str) -> str:
    """The MSD path a reported ``attribute_path`` instantiates.

    Drops every ``[n]`` sibling suffix, e.g. ``"QUALITY[1].COVERAGE[1]"`` ->
    ``"QUALITY.COVERAGE"``. The *path*, not the leaf: an attribute id may repeat
    under another parent in one MSD (#439), and each may be typed differently.
    """
    return _SIBLING_INDEX.sub("", attribute_path)


#: Item classes with a batched by-URN read, so resolving a few does not load a
#: whole scheme (#378). A hierarchy node's stored URN carries its path, which
#: is exactly what a reference to it carries, so it can join the codes (#439).
_TARGETED_READS: Final[dict[str, Callable[[duckdb.DuckDBPyConnection, Sequence[str]], Mapping[str, object]]]] = {
    "Code": resolve.codes_by_urn,
    "HierarchicalCode": resolve.hcodes_by_urn,
}


def _memo_items(parent: object, owner: type, item_urns: list[SdmxUrn[object]], memo: _CallScopedMemo) -> None:
    """Memoise each of *item_urns* found in *parent*, by path (#439)."""
    for parsed in item_urns:
        target = find_item(parent, parsed, owner)
        if target is not None:
            memo.set_local(str(parsed), target)


def _owner_of(artefact_class: str) -> type[MaintainableArtefact] | None:
    """The stored scheme class holding items of URN class *artefact_class*.

    Derived from the model (`model._items.owner_classes`), not listed: the
    resolver kept a two-row table — `Concept` and `Code` — so a reference to a
    category, a hierarchy node or a metadata attribute was never resolved by a
    `LocalRegistry` while an `InMemoryRegistry` resolved it (#439). The first
    owner that storage registers; the model has no item class with two today.
    """
    for owner in owner_classes(artefact_class):
        if _TYPE_REGISTRY.get(owner.__name__) is owner:
            return owner
    return None


#: Items the resolver cache may hold across calls, and the reason for it.
#:
#: **Items, not entries.** The bound used to be 256 *entries*, which said
#: nothing about memory because entries differ in size by four orders of
#: magnitude: on the 2021 Census Profile bundle the five largest artefacts total ~4.4 GB and
#: occupy 5 of 256 slots, so a service could sit on 4.4 GB while its counter
#: read 2% full. `docs/design/caching.md` requires a runtime cache to be
#: bounded, and that satisfied it on paper only (#386).
#:
#: **What an item weighs**, measured on the 2021 Census Profile bundle with `tracemalloc`, in
#: one process so a one-time 4.4 MB warm-up is not charged to each artefact:
#:
#: | artefact | items | retained | bytes/item |
#: | --- | --- | --- | --- |
#: | `CL_GEOGRAPHY` (`Codelist`) | 579,951 | 404.5 MB | **731** |
#: | `H_CP_GEO_FED2023` (`Hierarchy`) | 499,143 | 913.1 MB | 1,918 |
#: | `H_CP_GEO_ADMINISTRATIVE` | 63,404 | 130.6 MB | 2,061 |
#: | `H_CP_GEO_FSA` | 1,660 | 3.3 MB | ~2,000 |
#: | `H_CP_GEO_HCCSS` | 28 | 0.07 MB | — |
#: | `DF_CP_2021` (`Dataflow`) | 0 | 0.08 MB | — |
#:
#: So the cost is linear in items *within* a class and the spread *across*
#: classes is 2.9x, not 10,000x — which is what makes an item count a fair
#: weigher where an entry count was not.
#:
#: **1,000,000 items is therefore 0.7 GB of codes to 2.0 GB of nodes.** It
#: holds either of that bundle's two largest artefacts outright — the codelist
#: at 0.40 GB or the largest hierarchy at 0.91 GB — but not both at once. A
#: consumer who needs both raises it; the point of the number is that raising
#: it is now a memory decision someone can make.
@functools.lru_cache(maxsize=512)
def _scheme_urn_for(artefact_class: str, package: str, agency: str, resource_id: str, version: str) -> str | None:
    """The parent scheme's URN string for an item URN's parts, or None.

    **Keyed on everything but the item id**, which is the point: every item
    URN in one scheme answers identically, and the readers hand these over in
    batches of hundreds of thousands — 499,138 on the 2021 Census Profile's
    `H_CP_GEO_FED`, one per node. Deriving it per item built and stringified
    an `SdmxUrn` half a million times for one string per codelist.

    The same observation `SdmxUrn.parse_item` makes about the scheme half of
    an item URN, one level up: there it is the *parse* that re-derives a
    constant, here it is the *construction*.

    Bounded by count at 512 like `parse_item`'s, and right for the same reason
    it would be wrong in #386 — the entries are one short string per scheme in
    play, uniform and tiny.

    Returns:
        The URN string, or `None` when the item's class has no known parent
        scheme, or that scheme is not a registered type. Both mean nothing can
        resolve the item, and the caller drops it.
    """
    del package  # The owner's own package, as `scheme_urn_of` takes it — see there.
    owner = _owner_of(artefact_class)
    if owner is None:
        return None
    return str(
        SdmxUrn.make(
            owner,
            package=owner.sdmx_package,
            artefact_class=owner.sdmx_class,
            agency=agency,
            id=resource_id,
            version=version,
        )
    )


DEFAULT_RESOLVE_CACHE_ITEMS = 1_000_000

#: What an artefact retaining no items still weighs.
#:
#: Measured above: a zero-item artefact retains 0.00-0.08 MB, which is roughly
#: what 64 items weigh at ~1 KB each. A pure weigher would call these free and
#: lose the one thing an entry count was good at — bounding a flood of small
#: artefacts. At this floor, filling the default budget with nothing but empty
#: artefacts costs ~15,600 entries and ~1 GB, which is the budget behaving as
#: written.
#:
#: It is also what a *lazy* artefact weighs, and correctly so: `LazyCodelist`
#: holds a URN and a connection, not 579,951 codes.
ITEM_FLOOR = 64

#: Attributes that hold an artefact's items, most-populous first.
#:
#: Probed in order and the first `ItemList` or `tuple` wins — a tuple because
#: that is what `Hierarchy.codes` is since a node id stopped naming one node
#: (#439). Exact-type rather than `isinstance`, which is what keeps a lazy
#: artefact off this path: a
#: `LazyCodelist.codes` is a `Query`, and asking it for a length would run SQL
#: — a weigher that queries the database on every insert is not a weigher.
#:
#: `Hierarchy.codes` is the *flat* index over every node, not `tree`, which
#: holds only the roots. The two are the same objects (#383), so counting both
#: would double, and counting `tree` alone would weigh a 499,143-node
#: hierarchy as 1.
#:
#: `tests/unit/test_resolver_cache_weight.py` fails when a model class grows an
#: `ItemList` field this list does not reach, so a new scheme type cannot go
#: quietly unweighed.
_ITEM_ATTRS: Final = (
    "codes",
    "concepts",
    "categories",
    "agencies",
    "providers",
    "consumers",
    "organisation_units",
    "dimensions",
    "attributes",
    "items",
)


def weigh(value: object) -> int:
    """What one cache entry counts against the bound, in items.

    O(1): a length of an already-built `ItemList`, never a traversal and never
    a query. `docs/design/caching.md` asks that a cache not pay to know its own
    size, and an estimate in bytes would cost a walk of the whole graph on
    every insert.

    Returns:
        The item count, never below `ITEM_FLOOR`.
    """
    for attr in _ITEM_ATTRS:
        items = getattr(value, attr, None)
        # `type(...) is` rather than `isinstance`: a subclass could be lazy,
        # and the point of the check is "already in memory", not "list-like".
        if type(items) is ItemList or type(items) is tuple:
            return max(len(items), ITEM_FLOOR)
    return ITEM_FLOOR


_logger = logging.getLogger(__name__)
"""Per-artefact write timings are emitted at DEBUG level.

Enable with::

    logging.getLogger("sdmxlib.local.registry").setLevel(logging.DEBUG)

Each ``add()`` call emits one line::

    artefact_persist class=Codelist wall=0.0431 urn=urn:sdmx:...=ESTAT:CL_FREQ(1.0)

A ``transaction()`` block emits one summary at exit::

    transaction_summary count=523 wall=187.4 by_class=[Codelist:1 Hierarchy:12 MetadataSet:498 …]

Default level is WARNING, so production callers see no output and pay
no measurement cost (the timer is gated on ``isEnabledFor(DEBUG)``).
"""


class _FIFOCache(dict[str, object]):
    """Bounded resolver cache. FIFO eviction, lock-free reads.

    Subclasses `dict` (not ``OrderedDict``) so that ``get`` /
    ``__contains__`` / ``__getitem__`` are plain dict ops — GIL-atomic
    in CPython and safe for multiple concurrent reader threads with no
    locking. Mutations (insert, evict, clear) hold a per-instance
    `Lock`.

    Eviction is FIFO (insertion order) rather than LRU. FIFO is a
    deliberate choice over LRU here: tracking recency would require
    mutating the cache on every hit, which would force every cache hit
    onto the lock and serialise reader threads. At realistic working-set
    sizes for SDMX parent-scheme reuse, the hit-rate difference between
    FIFO and LRU is small; the lock-free read path is more valuable.
    Documented as a known trade-off in ``docs/design/concurrency.md`` — revisit
    if ``resolve_cache_info()`` ever shows poor hit rates against a
    working set that should otherwise fit.

    ``maxitems == 0`` disables caching — inserts are silently dropped.

    **Bounded in items, not entries (#386).** See `DEFAULT_RESOLVE_CACHE_ITEMS`
    for what an item weighs and why an entry count was not a bound at all. The
    running total is maintained under the same lock the insert and eviction
    already held, so reads stay lock-free — the property this class exists for
    — and the concurrency design is unchanged.

    **An entry heavier than the whole bound is refused, not admitted.**
    Caffeine and Guava admit such an entry and let the eviction loop remove it
    again; the cost of that here is that inserting one 3 M-node hierarchy would
    first evict everything else on its way to evicting itself, so a single
    over-budget read empties the cache. Refusing keeps the resident weight
    under the bound at every instant and keeps a giant from displacing the
    working set it cannot join. `refused` counts them, so a consumer asking
    "why does this never cache" has somewhere to look.

    Hit/miss counters are best-effort under concurrency: lockless ``+=``
    on the counter ints races, so under contention the reported numbers
    may slightly under-count. They remain useful as a diagnostic.
    """

    def __init__(self, maxitems: int) -> None:
        super().__init__()
        if maxitems < 0:
            msg = f"resolve_cache_items must be >= 0, got {maxitems}"
            raise ValueError(msg)
        self.maxitems = maxitems
        self.hits = 0
        self.misses = 0
        self.refused = 0
        #: Weight per resident key, so eviction can subtract without re-weighing
        #: a value whose item count may have been mutated since it was stored.
        self._weights: dict[str, int] = {}
        self._weight = 0
        self._lock = threading.Lock()

    @property
    def weight(self) -> int:
        """Items currently resident. Never above `maxitems`."""
        return self._weight

    @override
    def __setitem__(self, key: str, value: object) -> None:
        if self.maxitems == 0:
            return
        weight = weigh(value)
        if weight > self.maxitems:
            # Weighed before the lock: an entry that cannot be admitted needs
            # no exclusion, and the counter is best-effort like hits/misses.
            self.refused += 1
            return
        with self._lock:
            # Replacing a resident key: drop its old weight rather than adding
            # to it, or a re-resolved artefact would inflate the total until
            # eviction emptied a cache that was never over its bound.
            self._weight -= self._weights.get(key, 0)
            super().__setitem__(key, value)
            self._weights[key] = weight
            self._weight += weight
            # Evict insertion-oldest entries past the cap. ``next(iter(self))``
            # is the first-inserted key for plain dicts in Python 3.7+. The
            # entry just inserted cannot be evicted here: it is the newest, and
            # it fits on its own or it was refused above.
            while self._weight > self.maxitems:
                oldest = next(iter(self))
                self._weight -= self._weights.pop(oldest)
                super().__delitem__(oldest)

    @override
    def get(self, key: str, default: object = None) -> object:
        # Lock-free read: plain dict ``__contains__`` / ``__getitem__``
        # are GIL-atomic in CPython. The counter increments race under
        # concurrency — accepted (see class docstring).
        if super().__contains__(key):
            self.hits += 1
            return super().__getitem__(key)
        self.misses += 1
        return default

    @override
    def clear(self) -> None:
        with self._lock:
            super().clear()
            self._weights.clear()
            self._weight = 0

    def reset_stats(self) -> None:
        self.hits = 0
        self.misses = 0
        self.refused = 0


class _CallScopedMemo(dict[str, object]):
    """Per-call resolver memo layered over the persistent FIFO cache.

    Two reasons for the overlay rather than passing the cache directly:

    1. **Correctness under tight cache sizes (incl. ``maxitems=0``).** The
       resolver writes URN→target during one phase and reads them back in
       a later phase of the same call. If those writes went straight to
       the cache, an entry could be evicted before the read — leaving Refs
       unresolved for callers using a small (or disabled) cache. The
       overlay holds in-flight entries in an unbounded per-call dict so
       single-call resolution is independent of cache size.
    2. **Promote-on-success.** Entries written during the call enter the
       persistent cache only at the end of the call, via `promote`.
       That keeps partial-failure semantics clean: if a call raises
       midway, the persistent cache is unchanged.
    """

    def __init__(self, cache: _FIFOCache) -> None:
        super().__init__()
        self._cache = cache
        self._local_only: set[str] = set()

    def set_local(self, key: str, value: object) -> None:
        """Remember for this call only — never promoted to the persistent cache.

        Scheme *items* go here. They are wanted for the rest of the call, and
        they must not enter the shared cache, because there are hundreds of
        thousands of them and it holds 256 entries.

        Measured before this existed: reading a 1,660-node hierarchy left the
        cache holding 256 `Code` entries and **no `Codelist`** — the parent
        scheme it had just loaded was evicted by the items it had just
        resolved. So the caching that `docs/design/performance.md` §Layer 3
        describes was being defeated by its own item memoisation for any
        artefact with more than 256 items, and every later eager read paid full
        price again. The items were near-useless in the cache regardless: a
        different hierarchy wants different codes (#378).
        """
        self[key] = value
        self._local_only.add(key)

    @override
    def __contains__(self, key: object) -> bool:
        return super().__contains__(key) or self._cache.__contains__(key)

    @override
    def get(self, key: str, default: object = None) -> object:
        if super().__contains__(key):
            return super().__getitem__(key)
        return self._cache.get(key, default)

    def promote(self) -> None:
        """Copy this call's resolved entries into the persistent cache.

        Entries recorded through `set_local` are skipped — see its docstring.
        """
        for k, v in self.items():
            if k not in self._local_only:
                self._cache[k] = v


class _MemoResolver:
    """What a `Ref` a store read builds finds its target in: that read's memo (#492).

    The memo holds what the read loaded, items included, over the registry's
    shared cache, so a lookup is a dict hit — `get` would rebuild the target
    from DuckDB, 1.4 to 2.5 ms each. Once the registry is closed a lookup raises,
    rather than hand out an artefact whose lazy parts can no longer read.
    """

    __slots__ = ("_memo", "_registry")

    def __init__(self, registry: BundleRegistry, memo: _CallScopedMemo) -> None:
        self._registry = registry
        self._memo = memo

    def lookup(self, urn: SdmxUrn[object], /) -> object | None:
        """What the read that bound the `Ref` loaded for *urn*, or None.

        Raises:
            UnresolvedRef: If the registry is closed.
        """
        if self._registry.closed:
            raise UnresolvedRef(urn, f"{urn} cannot be resolved: {self._registry!r} is closed")
        return self._memo.get(str(urn))


def _as_value_key(key: Sequence[object]) -> tuple[str, str, int]:
    """One `metadata_value` key row, narrowed.

    The rows come from our own `SELECT` of a NOT NULL `INTEGER`, so this can
    only fail if the schema moved — narrowed rather than cast, so it says so
    instead of raising somewhere unrelated.
    """
    set_urn, path, sibling = key
    if not isinstance(sibling, int):
        msg = f"sdmx.metadata_value.sibling_index is not an int: {sibling!r}"
        raise TypeError(msg)
    return str(set_urn), str(path), sibling


type _Resolver = "Callable[[BundleRegistry], Resolver | None]"


def _no_resolver(_registry: BundleRegistry) -> None:
    """No way to build a domain object from this root's rows."""
    return


#: Which relations `Query.collect()` can materialise, keyed by root table.
#:
#: A dispatch here rather than a member on the accessor: turning rows into
#: objects is `storage.readers`' job and the accessors are meant to know
#: nothing about storage beyond column names. A root absent from this table is
#: not broken — a `dsd_component` row has no object to become — and the
#: refusal in `Query._resolver` says so.
_RESOLVERS: dict[str, _Resolver] = {}


class BundleRegistry(SessionFactory):
    """A DuckDB-backed SDMX store, read-only: what a published bundle opens as.

    It carries the read surface (``get``, ``get_all``, ``query``, ``obs``,
    ``dsd``, ``binding_for``, ``browse``, the label maps) and opens its
    database with DuckDB's ``read_only``, so any number of processes may hold
    one bundle open at once. `LocalRegistry` extends it with the writes.

    It has no ``sql()``. Several bundles can share one DuckDB engine, where a
    fully qualified name reaches any attached catalog, so a request-reachable
    raw SQL surface would read other bundles. ``connection``, ``execute`` and
    ``session`` remain for checkers and set-valued filters; they are not for
    a request path.

    Open a published bundle with ``LocalRegistry.bundle(path)``.

    **Concurrency.** Reads (``get`` / ``get_all`` / ``codelist`` /
    ``code_map`` / ``dsd_code_map`` / ``to_registry`` / ``__contains__``
    / ``__len__``) are safe to call from multiple threads against one
    registry. See ``docs/design/concurrency.md``.
    """

    #: Whether `open` connects read-only. `LocalRegistry` connects read-write.
    _READ_ONLY: ClassVar[bool] = True

    def __init__(
        self,
        uri: str | Path,
        *,
        source_registry: str | None = None,
        resolve_cache_items: int = DEFAULT_RESOLVE_CACHE_ITEMS,
        target: str | None = None,
        rebuild: bool = False,
    ) -> None:
        """Open or wrap a DuckDB-backed registry.

        ``rebuild`` lets opening drop and rebuild a store that holds artefacts
        on another schema version. Without it such a store is refused
        with `StoreSchemaError`, because the rebuild empties it: right for a
        cache that `RestRegistry(store=...)` can fetch again, and wrong for a
        bundle or a store filled with ``add``. An empty store, or a new file,
        is created or migrated either way.

        ``target`` declares which SDMX-ML version this store's contents are
        meant to be written as — ``"2.1"``, ``"3.0"`` or ``"3.1"``. Adding an
        artefact carrying something that format cannot represent then **raises**
        rather than storing it, because a declared target is a statement of
        intent and losing a field at write time is exactly what the declaration
        was made to prevent.

        ``None`` (the default) keeps the store format-agnostic, which is
        correct for anything holding *parsed* data: a cache of a 2.1 registry
        must accept what that registry publishes, and a target would refuse it.
        Declare one when **authoring**; leave it unset when mirroring.

        ``resolve_cache_items`` bounds the FIFO cache of resolved Ref
        targets that `get` / `get_all` / `to_registry`
        share across calls. Pass ``0`` to disable caching (matches
        pre-v0.27.2 behaviour — one full read per Ref). The cache is
        cleared on every mutation (``add`` / ``drop`` / ``replace``) for
        invalidation correctness. FIFO eviction (not LRU) keeps cache
        reads lock-free; see ``docs/design/concurrency.md``.
        """
        if target is not None and target not in TARGETS:
            msg = f"unknown target {target!r}; expected one of {', '.join(TARGETS)}"
            raise ValueError(msg)
        self._uri = store_path(uri, owner="LocalRegistry", wrap="sl.LocalRegistry.from_connection(conn)")
        self._source_registry = source_registry
        self._target = target
        #: Whether opening may drop a populated store on another schema (GH-57).
        self._rebuild = rebuild
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._cursor: duckdb.DuckDBPyConnection | None = None
        self._owns_connection = True
        self._resolve_cache: _FIFOCache = _FIFOCache(resolve_cache_items)
        # What a read refused since the store last changed, by URN (#492).
        self._refused: dict[str, Refusal] = {}
        self._in_transaction: bool = False
        # Per-transaction telemetry collected when DEBUG logging is on.
        # Tuple is (count, total_wall_seconds) keyed by artefact class.
        self._txn_stats: dict[str, tuple[int, float]] = {}
        #: Data store over this registry's own connection, built on first use.
        #: A registry serves observations through the same DuckDB it keeps
        #: artefacts in — one connection, so a relation-valued filter can see
        #: both sides (§5.9).
        self._data_store: DuckDBDataStore | None = None
        #: Held while the data store is built, so threads asking for
        #: observations at once build it, and attach the lake, once.
        self._data_plane_lock = threading.Lock()
        #: ATTACH spec a `bundle()` supplied, replayed when the store is built.
        self._attach: dict[str, str | tuple[str, str]] | None = None
        #: The lake's ``DATA_PATH``, a local directory or a remote URI, ending
        #: in ``/``; None where no lake is attached through the registry.
        self._data_path: str | None = None
        #: The manifest `bundle()` read, if the bundle had one (GH-66).
        self._manifest: Manifest | None = None
        #: The catalog whose ``sdmx`` schema every handle reads: the file's own
        #: once open, or the bundle's ``<prefix>__meta`` in a `BundleEngine`.
        self._catalog: str | None = None
        #: The engine `bundle()` attached this publication into, its prefix
        #: there, and whether this registry made the engine and closes it.
        self._engine: BundleEngine | None = None
        self._prefix: str | None = None
        self._owns_engine = False
        #: Each binding role to the catalog it names here: ``lake`` to the
        #: bundle's ``<prefix>__lake``. Empty where the role is the catalog.
        self._roles: dict[str, str] = {}
        # Per-thread read cursor (#80). Each thread that reads from this
        # LocalRegistry lazily gets its own cursor on first call; subsequent
        # calls on the same thread reuse it. Cleared on close().
        self._thread_local: threading.local = threading.local()

    @property
    def uri(self) -> Path:
        """The URI this registry references. Available before opening."""
        return self._uri

    @property
    def data_path(self) -> str | None:
        """Where the lake reads its data files: a local directory or a remote URI, ending in ``/``.

        The manifest's relative ``data_path`` resolved against the
        publication, or against its remote root (`bundle`'s ``remote``). None
        for a registry with no lake of its own.
        """
        return self._data_path

    @property
    def manifest(self) -> Manifest | None:
        """The bundle manifest `bundle()` read, or None (not a bundle, or unpublished).

        Its ``token`` changes at every publication and never on a read, so it
        is what a cache or an ETag keys on.
        """
        return self._manifest

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """The store's base DuckDB connection — stable, and safe to hold.

        Use it for setup, DDL, and as the base another component derives its
        own handles from (`DuckDBDataStore.from_registry` stores it in a
        field, which is correct precisely because this object does not move).

        **Not the handle to execute on from more than one thread.**
        `DuckDBPyConnection` is not thread-safe, which is why every query here
        runs on a per-thread handle instead. For custom SQL use
        `execute` or `session`, which resolve that handle for you and cannot be
        captured by accident.

        Raises:
            RuntimeError: If the registry hasn't been opened.
        """
        if self._conn is None:
            msg = (
                f"LocalRegistry({self._uri!r}) is not open. Use it as a context manager "
                "(``with LocalRegistry(uri) as reg: ...``) or call ``.open()`` first."
            )
            raise RuntimeError(msg)
        return self._conn

    @override
    def _session_base(self) -> duckdb.DuckDBPyConnection:
        return self.connection

    def _sdmx_catalog(self) -> str:
        """The catalog this registry's handles read, once known."""
        if self._catalog is None:
            msg = f"LocalRegistry({self._uri!r}) is not open"
            raise RuntimeError(msg)
        return self._catalog

    def _install_default_session_hooks(self) -> None:
        """Make every handle agree with the base connection (#300).

        Schema creation sets ``search_path = 'sdmx'`` on the connection it runs
        on, and nothing propagated that to the per-thread handles — so
        ``reg.connection`` resolved ``code`` while a handle needed
        ``sdmx.code``, and a ``SET`` reached exactly one of the three handles
        that existed. Applying it through the hook is what makes "the session"
        a single thing rather than a description of one of them.
        """
        self.on_connect(lambda handle: use_sdmx(handle, self._sdmx_catalog()))

    def open(self) -> Self:
        """Open the DuckDB connection and run any pending schema migrations.

        Returns ``self`` so the call can be chained with a ``with``::

            with LocalRegistry("store.duckdb").open() as reg:
                ...

        Idempotent within a single open/close cycle; calling ``open()``
        on an already-open registry is a no-op.
        """
        if self._conn is not None:
            return self
        if self._engine is not None and self._prefix is not None:
            # A handle of its own on the shared engine, reading this bundle's
            # catalog; the engine set the spill directory for every bundle.
            self._conn = self._engine.cursor(self._prefix)
            self._owns_connection = True
            self._initialise_schema()
            self._install_default_session_hooks()
            return self
        self._conn = duckdb.connect(str(self._uri), read_only=self._READ_ONLY)
        self._owns_connection = True
        if self._READ_ONLY:
            # A read-only database spills to `<file>.tmp` beside itself by
            # default: inside a published bundle, and shared by every process
            # holding it. Each process spills under the system temp instead.
            spill = str(spill_directory()).replace("'", "''")
            self._conn.execute(f"SET temp_directory = '{spill}'")
        self._initialise_schema()
        self._install_default_session_hooks()
        return self

    @classmethod
    def from_connection(
        cls,
        conn: duckdb.DuckDBPyConnection,
        *,
        source_registry: str | None = None,
        resolve_cache_items: int = DEFAULT_RESOLVE_CACHE_ITEMS,
        target: str | None = None,
        rebuild: bool = False,
    ) -> Self:
        """Wrap an existing DuckDB connection.

        The LocalRegistry does NOT take ownership of the connection — closing the
        LocalRegistry leaves the connection open. Use this when the connection
        is shared with another component (e.g. a co-located DuckLake catalog)
        that owns its lifecycle.

        ``resolve_cache_items`` configures the resolver cache (FIFO), ``target``
        declares an SDMX-ML version to hold the contents to, and ``rebuild``
        lets a populated store on another schema be dropped; all are described
        on `__init__`.
        """
        # `Self`-annotated because `cls.__new__(cls)` is otherwise untyped: the
        # local would be `Self@__new__` with no field information. (It also
        # silenced SLF001 before that rule was retired — see CLAUDE.md "The
        # public / private barrier".)
        instance: Self = cls.__new__(cls)
        if target is not None and target not in TARGETS:
            msg = f"unknown target {target!r}; expected one of {', '.join(TARGETS)}"
            raise ValueError(msg)
        instance._uri = Path(":connection:")
        instance._source_registry = source_registry
        # Set here as well as in `__init__`: this constructor goes through
        # `cls.__new__`, so it initialises every field itself and a new one
        # added to `__init__` alone is simply absent — an `AttributeError` at
        # first use, which is how this was found. It then happened a second
        # time, to `_data_store` and `_attach`, and made the whole data plane
        # raise `AttributeError` on any registry built this way. A comment was
        # not enough; `test_constructors_agree_on_fields` is the guard.
        instance._target = target
        instance._rebuild = rebuild
        instance._conn = conn
        instance._cursor = None
        instance._owns_connection = False
        instance._resolve_cache = _FIFOCache(resolve_cache_items)
        instance._refused = {}
        instance._in_transaction = False
        instance._txn_stats = {}
        instance._data_store = None
        instance._data_plane_lock = threading.Lock()
        # No ATTACH spec: a caller sharing a connection has already attached
        # whatever it wants visible. `_data_plane` then builds a store over
        # that connection and finds the lake already there.
        instance._attach = None
        instance._data_path = None
        instance._manifest = None
        instance._catalog = None
        instance._engine = None
        instance._prefix = None
        instance._owns_engine = False
        instance._roles = {}
        instance._thread_local = threading.local()
        instance._initialise_schema()
        return instance

    def _initialise_schema(self) -> None:
        """Migrate or open a cursor on the ``sdmx`` schema.

        Raises:
            StoreSchemaError: If the store holds artefacts on another schema
                version and ``rebuild`` was not asked for (GH-57).
            ReadOnlySchemaError: If the schema must be created or rebuilt and
                the connection is read-only.
        """
        if self._conn is None:
            return
        if self._catalog is None:
            self._catalog = sdmx_catalog(self._conn)
        version = get_schema_version(self._conn)
        exists = schema_exists(self._conn)
        if not exists or version != SCHEMA_VERSION:
            found = f"schema {version}" if exists else "no sdmx schema"
            if exists and not self._rebuild and holds_rows(self._conn):
                msg = (
                    f"{self._uri} holds artefacts on store {found}, and this sdmxlib "
                    f"({_sdmxlib_version()}) reads schema {SCHEMA_VERSION}, so it was not opened: "
                    f"migrating means dropping them. A bundle: rebuild it from its source with this "
                    f"sdmxlib, or read it with the one that built it. A cache that can be fetched "
                    f"again: open it with rebuild=True."
                )
                raise StoreSchemaError(msg)
            if connection_is_read_only(self._conn):
                msg = (
                    f"this store has {found}, and this build of sdmxlib uses schema {SCHEMA_VERSION}. "
                    f"Creating or rebuilding it needs a read-write connection."
                )
                raise ReadOnlySchemaError(msg)
            self._cursor = migrate(self._conn)
        else:
            self._cursor = use_sdmx(self._conn.cursor(), self._sdmx_catalog())
            # Only on the already-populated branch: a schema we just created
            # or rebuilt cannot have stale scan statistics, and skipping it
            # there keeps the check off every `:memory:` registry in the
            # suite. ~1 ms against the spine — see `check_scan_integrity`.
            check_scan_integrity(self._cursor)
        # Also point the connection itself — used by code paths that call
        # ``conn.execute()`` directly without going through ``_read_cursor``
        # (lazy facades' ``code_map`` forwarder, etc.). Cheap one-shot.
        use_sdmx(self._conn, self._sdmx_catalog())

    def __enter__(self) -> Self:
        if self._conn is None:
            self.open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the database connection (only if we own it).

        After ``close()``, the registry can be re-opened with ``open()``.
        Drops the per-thread cursor cache — reopen starts fresh.
        """
        if self._conn is not None and self._owns_connection:
            self._conn.close()
        self._conn = None
        self._cursor = None
        self._data_store = None
        if self._engine is not None and self._prefix is not None:
            self._engine.release(self._prefix)
            if self._owns_engine:
                self._engine.close()
            self._engine, self._prefix = None, None
        # Drop per-thread cursors. The cursor objects become orphaned —
        # DuckDB's close() invalidates them; Python GC reclaims the
        # handles. We can't iterate other threads' attributes on the
        # threadlocal, but reassigning replaces the storage for *this*
        # thread and lets the prior thread-local mapping get GC'd
        # (other threads' attrs become unreachable too).
        self._thread_local = threading.local()

    @property
    def _c(self) -> duckdb.DuckDBPyConnection:
        """Internal accessor — returns the live cursor or raises a clear error.

        Used by write paths (``add`` / ``drop`` / ``replace``) which are
        documented as single-threaded. Read paths must go through
        `_read_cursor` instead so concurrent reads each get an
        isolated cursor — see ``docs/design/concurrency.md`` and issue #75.
        """
        if self._cursor is None:
            msg = (
                f"LocalRegistry({self._uri!r}) is not open. Use it as a context manager "
                "(``with LocalRegistry(uri) as reg: ...``) or call ``.open()`` first."
            )
            raise RuntimeError(msg)
        return self._cursor

    @property
    def closed(self) -> bool:
        """True once `close` has run, or before `open`: a reference it bound can no longer resolve."""
        return self._conn is None

    def _require_open(self) -> duckdb.DuckDBPyConnection:
        """Return the live connection or raise a clear lifecycle error.

        Use when a caller needs the long-lived connection itself (e.g. to
        hand to a lazy facade) rather than a short-lived cursor.
        """
        if self._conn is None:
            msg = (
                f"LocalRegistry({self._uri!r}) is not open. Use it as a context manager "
                "(``with LocalRegistry(uri) as reg: ...``) or call ``.open()`` first."
            )
            raise RuntimeError(msg)
        return self._conn

    def _read_cursor(self) -> duckdb.DuckDBPyConnection:
        """Return this thread's read cursor, creating it on first call.

        Each thread gets its own cursor lazily; subsequent calls on the
        same thread reuse it. DuckDB requires per-thread cursors (the
        connection's default-cursor row buffer is overwritten by every
        ``execute()`` — see #75), but it does *not* require recreating
        the cursor per call. Thread-local reuse amortises the
        ``cursor()`` + ``USE`` cost across the thread's
        lifetime — typically the entire process for a long-lived
        FastAPI/uvicorn threadpool.

        Cleanup: the thread-local storage is dropped by `close`;
        otherwise it lives until the thread dies (Python GC closes the
        cursor object).

        **Never close what this returns**, and never use it as a context
        manager: a `DuckDBPyConnection` closes itself on leaving a ``with``
        block, and this one is shared by every later read on the thread.
        `to_registry` did, and every read after it on that thread raised
        "Connection already closed" (GH-115).
        """
        # `getattr` on threading.local is untyped; annotate at the boundary so
        # the Any stops here instead of riding out through the return.
        cur: duckdb.DuckDBPyConnection | None = getattr(self._thread_local, "cur", None)
        if cur is not None:
            return cur
        if self._conn is None:
            msg = (
                f"LocalRegistry({self._uri!r}) is not open. Use it as a context manager "
                "(``with LocalRegistry(uri) as reg: ...``) or call ``.open()`` first."
            )
            raise RuntimeError(msg)
        cur = adopt(self._conn, use_sdmx(self._conn.cursor(), self._sdmx_catalog()))
        self._thread_local.cur = cur
        return cur

    # ── Add ───────────────────────────────────────────────────────────────

    # ── Get ───────────────────────────────────────────────────────────────

    def codelist(self, urn: str | SdmxUrn[object]) -> LazyCodelist | None:
        """Return a LazyCodelist for tree navigation without materializing codes.

        Faster than ``get()`` for hierarchy traversal — skips ref resolution
        and only reads the codelist spine row::

            cl = local.codelist(geo_urn)
            cl.roots()  # top-level codes — SQL, no Python objects
            cl.narrower("1")  # direct children — SQL, only rows needed
        """
        # make_lazy_codelist opens its own internal cursor for spine
        # reads; the returned LazyCodelist holds the long-lived
        # connection for later method calls. ``_require_open`` enforces
        # the lifecycle precondition without creating a cursor we don't
        # need.
        return make_lazy_codelist(self._require_open(), str(urn))

    # `expect=` first: it is the only overload that names the result outright,
    # so a caller who passed it should get that answer whatever the URN's
    # phantom happens to be. See the docstring for why it exists.
    @overload
    def get[T](self, target: SdmxUrn[object] | Ref[object], /, *, expect: type[T], eager: bool = False) -> T | None: ...

    @overload
    def get(
        self, target: SdmxUrn[Codelist] | Ref[Codelist], /, *, eager: Literal[False] = False
    ) -> LazyCodelist | None: ...

    @overload
    def get(
        self, target: SdmxUrn[Hierarchy] | Ref[Hierarchy], /, *, eager: Literal[False] = False
    ) -> LazyHierarchy | None: ...

    @overload
    def get[T](self, target: SdmxUrn[T] | Ref[T], /, *, eager: bool = False) -> T | None: ...

    def get[T](
        self, target: SdmxUrn[object] | Ref[object], /, *, expect: type[T] | None = None, eager: bool = False
    ) -> object:
        """Look up an artefact by URN, or by a `Ref` naming one.

        For Codelists, returns a ``LazyCodelist`` by default (codes not
        materialized). Pass ``eager=True`` to get a real ``Codelist``.

        **An untyped URN answers `object | None`.** `SdmxUrn.parse(text)` and
        `artefact_urn(x)` are `SdmxUrn[object]`: a URN string can name
        anything, so this cannot say which class comes back, and the caller
        narrows. A typed URN — `Codelist.urn_for(...)`, `Codelist.parse_urn(text)`,
        `artefact.urn` — picks the matching overload and its class.

        `tests/typing/probe_urn_any.py` pins what is declared.

        **`expect=` names the class when the URN does not carry it.**
        It names the class, so `T` comes from the argument rather than from the
        URN's phantom, and the ambiguity above never arises:

            local.get(SdmxUrn.parse(text), expect=MetadataSet)   # MetadataSet | None

        The check is `isinstance` at runtime, which makes it the same operation
        the caller would otherwise write by hand — a URN read out of a store may
        legitimately name something this call site cannot use, and None is how
        that is said.

        **With the lazy roots, expect what comes back.** ``expect=Codelist``
        answers None for a stored codelist, because a `LazyCodelist` is not a
        `Codelist`. Ask for what you will get: ``expect=LazyCodelist``,
        ``expect=CodelistLike`` for either, or ``eager=True`` alongside
        ``expect=Codelist``.

        **The two lazy roots are declared, not implied.** A
        `SdmxUrn[Hierarchy]` used to give `Hierarchy | None` here while a
        `LazyHierarchy` came back — and that one has no ``codes``, ``tree`` or
        ``descendants`` at all, so `local.get(h_urn).codes` type-checked and
        raised `AttributeError`. The overloads above say which class each URN
        actually produces, in both `eager=` branches: the flag changes the
        returned type, and one declaration could not describe both.

        Every ``Ref`` on the returned artefact is bound to this store and
        finds its target on access, so standard traversal works::

            dsd = local.get(dsd_urn)
            dsd.dimensions["FREQ"].codelist  # -> LazyCodelist
            dsd.code_map(lang="en")  # works

        A `Ref` is accepted for the same reason `RestRegistry.get` accepts one
        — traversal hands them out. It used to return ``None`` here instead,
        silently; a `str` used to work here and nowhere else.
        """
        urn = target_urn(target)
        cur = self._read_cursor()
        # `get_artefact` dispatches on a runtime artefact-type column, so it
        # returns `Any` by design (CLAUDE.md). The annotation is where that
        # dies: the overloads above are what give a caller a real type back.
        memo = _CallScopedMemo(self._resolve_cache)
        with binding(_MemoResolver(self, memo)):
            try:
                result: object = readers.get_artefact(cur, str(urn), eager=eager, lazy_conn=self._conn)
            except SdmxConformanceError as exc:
                self._remember(exc)
                raise
            if result is None:
                return None
            if not isinstance(result, (LazyCodelist, LazyHierarchy)):
                self._resolve_refs(cur, result, memo=memo)
        memo.promote()
        if expect is not None and not isinstance(result, expect):
            return None
        return result

    def reported_attributes(self) -> Query[ReportedAttributeRow, MetadataSection]:
        """A lazy query over reported reference-metadata values.

        The SQL counterpart to `metadata_report`, which projects one set at a
        time. This filters across the whole catalogue in one statement — "every
        QUALITY.COVERAGE statement", "everything said about this dataflow"::

            reg.reported_attributes().filter(
                sl.q.reported_attribute.metadata_set.targets.artefact.urn == flow_urn
            ).collect()

        Exactly `reg.query(sl.q.reported_attribute)`, kept as a named entry point
        because "the reported values" is a question worth a name and because
        `metadata_report` is its per-set counterpart. It is the same
        `Query` every other root returns — the bespoke `MetadataValueQuery` is
        gone.

        Returns:
            A `Query` over `sdmx.metadata_value`. `collect()` yields MSD-typed
            `MetadataSection` rows; `select()` and `frame()` are the cheap
            terminals when the declared type information is not needed.
        """
        return self.query(query_accessors.q.reported_attribute)

    def _sections_for_keys(
        self, keys: Sequence[tuple[object, ...]], *, annotations: bool = True
    ) -> list[MetadataSection]:
        """MSD-typed sections for the given ``(set, path, sibling)`` triples.

        Routed through `metadata_report` rather than re-deriving the MSD join,
        so the typed view has exactly one implementation. The cost is one
        report per distinct set, which is why `collect()` on this root is the
        expensive terminal — `select()` is one statement.

        Results come back in the *query's* order, not the report's: a
        `MetadataSection` does not carry the set it came from, so the sections
        are indexed by their full key and then re-emitted following ``keys``.
        Without that, a caller's ``sort_by`` would be silently replaced by the
        report's own ``(path, sibling_index)`` ordering.

        `annotations=False` is refused rather than ignored — see
        `_resolve_artefact_keys` for why.
        """
        _refuse_annotation_opt_out(annotations, "q.reported_attribute")
        if not keys:
            return []
        triples = [_as_value_key(k) for k in keys]
        wanted = set(triples)
        index: dict[tuple[str, str, int], MetadataSection] = {}
        for set_urn in dict.fromkeys(k[0] for k in triples):
            for section in self.metadata_report(set_urn):
                key = (set_urn, section.path, section.sibling_index)
                if key in wanted:
                    index[key] = section
        return [index[k] for k in triples if k in index]

    def artefacts(self, artefact_type: type[object] | None = None) -> Query[ArtefactRow, Maintainable]:
        """A lazy query over every maintainable artefact, narrowed by class.

        The polymorphic counterpart to the named roots. `reg.query(q.codelist)`
        is the spelling when the type is known at the call site; this is for
        when it is a *variable*, which is the case `get_all` used to serve by
        loading and resolving everything::

            reg.artefacts(sl.Dataflow).filter(q.artefact.agency == "ESTAT").urns()

        It returns the same `Query` every other root does, so it
        composes with `filter` / `order_by` / `select` / `group_by` and the
        whole terminal surface. `artefact_type` applies the same discriminator
        the named roots carry — `q.artefact.type` is that column, exposed.

        Args:
            artefact_type: Narrow to one SDMX class. Omit to sweep every
                maintainable artefact in the catalogue.

        Returns:
            A `Query` over `sdmx.artefact`.

        Raises:
            TypeError: If *artefact_type* is not a stored SDMX artefact type —
                naming the class, since the alternative is an empty result.
        """
        self._require_open()
        root = query_accessors.q.artefact
        if artefact_type is None:
            return self.query(root)
        type_name = CLASS_TO_NAME.get(artefact_type)
        if type_name is None:
            msg = f"{artefact_type.__name__} is not a stored SDMX artefact type."
            raise TypeError(msg)
        return self.query(root).filter(root.type == type_name)

    def query[R: "RowKind", A](self, root: Cols[R, A]) -> Query[R, A]:
        """A lazy query over the relation `root` names.

        The generic entry point for the path query language: pass any accessor
        from `sl.q` and compose with `filter` / `limit` / `offset`, then take a
        terminal.

            reg.query(sl.q.dsd).filter(
                sl.q.dsd.dimensions.any(sl.q.component.concept.id == "REF_AREA")
            ).ids()

        Generic rather than one method per relation because the accessor is the
        witness: it carries both the relation and its row kind, so a predicate
        built for another root is a type error rather than a wrong join.

        Nothing runs until a terminal — `ids()`, `urns()`, `count()`,
        `exists()`, `collect()`, `frame()`. See `sdmxlib.query.query.Query`.

        Args:
            root: A relation accessor from `sl.q`.

        Returns:
            A `Query` over that relation, sharing this registry's connection
            and its batched resolver.
        """
        conn = self._require_open()
        return build(
            root,
            conn,
            _RESOLVERS.get(root.__table__, _no_resolver)(self),
            source_label=self._source_label(),
        )

    def _source_label(self) -> str:
        """How a refusal names this registry's database (§5.9).

        The uri, plus the object address when it is `:memory:` — two in-memory
        registries are two databases and a message naming both ``':memory:'``
        would identify neither.
        """
        uri = str(self._uri)
        return f"{uri!r} <0x{id(self):x}>" if uri == ":memory:" else repr(uri)

    def _refuse_unrepresentable(self, artefact: SupportsUrn) -> None:
        """Refuse an artefact this store's declared target cannot carry.

        Only when a ``target`` was declared. Without one the store is a mirror
        and must accept whatever a registry published; with one it is a
        workspace, and the whole value of saying so is that the loss surfaces
        where it was authored rather than at write time, when the artefact has
        been through everything else first.

        Raises:
            ValueError: Naming every field that would be lost and why. All of
                them, not the first — an author fixing one at a time learns
                the shape of the format one traceback at a time.
        """
        if self._target is None:
            return
        gaps = unrepresentable(artefact, self._target)
        if not gaps:
            return
        listed = "; ".join(f"{gap.field} ({gap.reason})" for gap in gaps)
        msg = (
            f"{type(artefact).__name__} {artefact.agency_id}:{artefact.id} carries "
            f"{len(gaps)} field(s) {describe(self._target)} cannot represent, and this "
            f"registry declares target={self._target!r}: {listed}. "
            f"Clear the field(s), or open the registry without a target."
        )
        raise ValueError(msg)

    def _resolve_artefact_keys(self, keys: Sequence[tuple[object, ...]], *, annotations: bool = True) -> list[object]:
        """Resolve `(urn,)` key rows to artefacts.

        `annotations=False` is refused rather than ignored. A maintainable
        artefact's annotations come from `readers.get_artefact`'s subtree read,
        which has no such switch, and quietly honouring the flag on some roots
        while dropping it on others is how a caller ends up believing they
        saved work they did not (#381).
        """
        _refuse_annotation_opt_out(annotations, "q.artefact")
        return self._resolve_urns_batch([str(key[0]) for key in keys])

    def _resolve_code_keys(self, keys: Sequence[tuple[object, ...]], *, annotations: bool = True) -> list[Code]:
        """Resolve `(code_urn,)` key rows to `Code` objects.

        The work lives in `storage.readers` because `LazyCodelist` needs it too
        and holds a connection rather than a registry.
        """
        return resolve.resolve_code_keys(self._require_open(), keys, annotations=annotations)

    def _resolve_hcode_keys(
        self, keys: Sequence[tuple[object, ...]], *, annotations: bool = True
    ) -> list[HierarchicalCode]:
        """Resolve `(node_urn,)` key rows to `HierarchicalCode` objects.

        The path language had no resolver for this relation, so
        `q.hcode.collect()` refused while `h.nodes.collect()` worked.
        """
        return resolve.resolve_hcode_keys(self._require_open(), keys, annotations=annotations)

    def _resolve_urns_batch[T](self, urns: list[str]) -> list[T]:
        """Load and resolve many artefacts, sharing one call-scoped memo.

        The batching matters for the same reason it does in `get_all`: a parent
        scheme referenced by fifty of the matches is loaded once, not fifty
        times.
        """
        if not urns:
            return []
        cur = self._read_cursor()
        memo = _CallScopedMemo(self._resolve_cache)
        out: list[T] = []
        with binding(_MemoResolver(self, memo)):
            for urn in urns:
                # `get_artefact` dispatches on a runtime type column, so no static
                # type describes its result. The annotation is where that `Any`
                # dies — see CLAUDE.md, "typed core, untyped shell".
                obj: T | None = readers.get_artefact(cur, urn, lazy_conn=self._conn, refused=self._refused)
                if obj is None:
                    continue
                if not isinstance(obj, (LazyCodelist, LazyHierarchy)):
                    self._resolve_refs(cur, obj, memo=memo)
                out.append(obj)
        memo.promote()
        return out

    def get_all[T](self, artefact_type: type[T]) -> list[T]:
        """All artefacts of the given Python type.

        Resolution shares one call-scoped memo across the batch and is
        backed by the registry's persistent ``_resolve_cache``, so any
        parent scheme referenced by multiple artefacts is loaded once,
        not once per artefact.
        """
        return self._resolve_urns_batch(readers.urns_of_type(self._read_cursor(), artefact_type))

    # ── Graph queries ──────────────────────────────────────────────────

    def _applicability(self, dataflow: Dataflow, dim_id: str) -> list[tuple[str, str, bool]]:
        """Rows of `sdmx.hierarchy_applicability` for one flow and component.

        `(hierarchy_urn, association_urn, scoped)`, ranked. The view states the
        scoped-beats-unscoped rule once, so the two methods below read it
        rather than restating it — which is what #372 was about: the same
        question answered twice, differently.
        """
        rows = (
            self._read_cursor()
            .execute(
                """
                SELECT hierarchy_urn, association_urn, scoped
                FROM hierarchy_applicability
                WHERE dataflow_urn = ? AND component_id = ?
                ORDER BY rank
                """,
                [str(artefact_urn(dataflow)), dim_id],
            )
            .fetchall()
        )
        return [(str(h), str(a), bool(s)) for h, a, s in rows]

    def applicable_hierarchies(
        self,
        dataflow: Dataflow,
        dim_id: str,
    ) -> list[Ref[Hierarchy]]:
        """All Hierarchies applicable to ``dim_id`` on ``dataflow``.

        See `InMemoryRegistry.applicable_hierarchies` for semantics. Read off
        `sdmx.hierarchy_applicability`, which is where the rule lives; the
        in-memory registry walks the objects for the same answer, and
        `tests/unit/test_query_applicability.py` compares them.
        """
        rows = self._applicability(dataflow, dim_id)
        return [Ref[Hierarchy].unresolved(Hierarchy.parse_urn(urn)) for urn, _ha, _scoped in rows]

    def unique_hierarchy(
        self,
        dataflow: Dataflow,
        dim_id: str,
    ) -> Ref[Hierarchy] | None:
        """The single applicable Hierarchy, ``None`` if none, raises on >1.

        See `InMemoryRegistry.unique_hierarchy` for semantics. "More than one"
        means within the *winning* tier: a scoped association beating an
        unscoped one is a resolution, not a conflict, while two scoped ones
        are a conflict the caller asserted would not happen.
        """
        rows = self._applicability(dataflow, dim_id)
        if not rows:
            return None
        best_is_scoped = rows[0][2]
        chosen = [row for row in rows if row[2] == best_is_scoped]
        if len(chosen) > 1:
            structure = dataflow.structure
            dsd_urn = structure.urn if structure is not None else None
            dim_urn = (
                str(
                    SdmxUrn.make(
                        Dimension,
                        package="datastructure",
                        artefact_class="Dimension",
                        agency=dsd_urn.agency,
                        id=dsd_urn.id,
                        version=dsd_urn.version,
                        item_id=dim_id,
                    )
                )
                if dsd_urn is not None
                else ""
            )
            raise AmbiguousHierarchyError(
                dim_urn=dim_urn,
                dataflow_urn=str(artefact_urn(dataflow)),
                ha_urns=sorted(association for _h, association, _s in chosen),
            )
        return Ref[Hierarchy].unresolved(Hierarchy.parse_urn(rows[0][0]))

    # ── The data plane ────────────────────────────────────────────────────

    @classmethod
    def bundle(
        cls,
        path: str | Path,
        *,
        remote: str | None = None,
        engine: BundleEngine | None = None,
        resolve_cache_items: int = DEFAULT_RESOLVE_CACHE_ITEMS,
    ) -> BundleRegistry:
        """Open a published bundle, read-only: artefacts and observations together.

        `path` is one publication of a bundle, the directory named by its
        token. Every publication of a bundle shares the ``data/`` beside it::

            <bundle>/data/                       data files, shared by every publication
            <bundle>/<token>/bundle.manifest.json   the manifest, written last
            <bundle>/<token>/meta.duckdb            sdmx.* artefacts, including the bindings
            <bundle>/<token>/lake.ducklake          DuckLake catalog over ``data/``

        The lake reads its data files from the manifest's ``data_path``,
        resolved against `path`, or against `remote` when the data files are
        in object storage.

        **Data files in object storage.** A server may hold only a
        publication's catalog files, tiers 1 and 2, on local disk and read its
        data files, tier 3, from the store they were published to. `remote`
        names the publication directory there, and the manifest's
        ``data_path`` is resolved against it, so nothing in the bundle names
        a location::

            engine = sl.BundleEngine(
                secrets=[
                    sl.StoreSecret(
                        "azure", provider="credential_chain", options={"account_name": "acct"}
                    )
                ]
            )
            reg = sl.LocalRegistry.bundle(
                "/var/bundles/census/019a…/",
                remote="abfss://c@acct.dfs.core.windows.net/bundles/census/019a…/",
                engine=engine,
            )

        Any store DuckDB reads: ``s3://`` (and S3-compatible servers),
        ``r2://``, ``gs://``, ``az://``, ``abfss://``, ``http(s)://``, and
        ``file://`` for a mounted one. The engine loads the extension the
        scheme needs and holds the credentials, as `StoreSecret` values.

        **Published means a manifest** (``bundle.manifest.json``,
        `sdmxlib.bundle`). It is required, read first, and its store schema
        version checked *before* ``meta.duckdb`` is opened, so a mismatched
        bundle is refused without the file being touched. It is `manifest`
        afterwards, whose ``token`` is what a cache keys on.

        **Read-only.** ``meta.duckdb`` opens with DuckDB's
        ``read_only`` and the lake attaches ``READ_ONLY``, so any number of
        processes may hold one bundle open at once, and a checker can run
        beside a server. The result is a `BundleRegistry`, which has no write
        methods and no ``sql()``. DuckDB's spill directory is moved outside
        the bundle, which is never written after publication. Open a bundle
        directory for building with ``LocalRegistry.build(path)``.

        The lake is ATTACHed lazily, when something first asks for
        observations, so opening a bundle to read a codelist costs nothing.

        **One engine for many bundles.** Pass a `BundleEngine` to
        attach the publication into it, beside every other bundle it holds,
        so they share one buffer pool, one thread pool and one
        ``memory_limit``. Without one, the bundle gets an engine of its own,
        closed with the registry. Either way its catalogs attach under a
        prefix of their own, ``<bundle id>__<token>__meta`` and ``__lake``,
        and a binding's ``lake`` role reads ``<prefix>__lake`` (`locate`).

        Args:
            path: The publication directory.
            remote: The publication directory in object storage, as a URI,
                when the data files are read from there; the data files are
                beside `path` when None.
            engine: The engine to attach into; one of its own when None.
            resolve_cache_items: As the `LocalRegistry` constructor.

        Returns:
            An open, read-only registry over the publication.

        Raises:
            StoreSchemaError: If the bundle was built on another schema version.
            StoreFormatError: If its DuckDB storage version or its DuckLake
                format is newer than this build reads.
            ManifestError: If there is no manifest, or it is malformed or in a
                format this build cannot read.
            ValueError: If `remote` is not a URI DuckDB reads.
        """
        root = Path(path)
        manifest = read_manifest(root)
        if manifest.schema_version != SCHEMA_VERSION:
            msg = (
                f"{root} is a bundle on store schema {manifest.schema_version} (its manifest says so), "
                f"and this sdmxlib ({_sdmxlib_version()}) reads schema {SCHEMA_VERSION}, so it was not "
                f"opened. Rebuild it from its source with this sdmxlib, or read it with the one that built it."
            )
            raise StoreSchemaError(msg)
        refuse_engine_mismatch(manifest, root)
        data = root / manifest.data_path if remote is None else data_uri(remote, manifest.data_path)
        owned = engine is None
        shared = BundleEngine() if engine is None else engine
        if remote is not None:
            shared.load_for_uri(remote)
        prefix = shared.attach(root, bundle_id=manifest.bundle_id, token=manifest.token)
        registry = BundleRegistry._over_bundle(
            root, data, resolve_cache_items=resolve_cache_items, lake=f"{prefix}__lake"
        )
        registry._manifest = manifest
        registry._engine, registry._prefix, registry._owns_engine = shared, prefix, owned
        registry._catalog = f"{prefix}__meta"
        registry._roles = {"lake": f"{prefix}__lake"}
        try:
            return registry.open()
        except Exception:
            registry.close()
            raise

    @classmethod
    def _over_bundle(
        cls,
        root: Path,
        data: Path | str,
        *,
        source_registry: str | None = None,
        resolve_cache_items: int = DEFAULT_RESOLVE_CACHE_ITEMS,
        lake: str = "lake",
    ) -> Self:
        """An unopened registry of this class over publication `root`, its lake's data files in `data`.

        `data` is a local directory, or a URI ending in ``/`` for data files
        in object storage.

        The lake attaches as `lake`: ``lake`` for a builder or the manifest
        writer, ``<prefix>__lake`` for a bundle in an engine.

        `bundle` opens a read-only one after checking the manifest;
        `LocalRegistry.build` and the manifest writer open a writable one.
        """
        registry = cls(
            root / "meta.duckdb",
            source_registry=source_registry,
            resolve_cache_items=resolve_cache_items,
        )
        # `OVERRIDE_DATA_PATH` because a DuckLake catalog records the data path
        # it was *written* with, and a bundle is a directory that gets moved,
        # copied and unpacked somewhere else. Without the override DuckLake
        # refuses the ATTACH of a moved catalog, and the whole data plane is
        # unreachable — which is how this was found.
        #
        # Overriding is right rather than merely convenient: the manifest's
        # `data_path`, resolved against where the publication is *now*, says
        # where its data files are. The catalog's remembered path is
        # provenance, not truth.
        read_only = ", READ_ONLY" if cls._READ_ONLY else ""
        data_path = data if isinstance(data, str) else f"{data.resolve()}/"
        registry._data_path = data_path
        registry._attach = {
            lake: (
                sql_literal(f"ducklake:{root / 'lake.ducklake'}"),
                f"(DATA_PATH {sql_literal(data_path)}, OVERRIDE_DATA_PATH true{read_only})",
            )
        }
        return registry

    def _data_plane(self) -> DuckDBDataStore:
        """This registry's data store, over its own connection.

        Named for the plane rather than `_store`, because `RestRegistry`'s
        `store=` now means *a position in the chain* — the `LocalRegistry` a
        remote one writes through. Two sibling classes with a `_store` meaning
        different things is the confusion `store=` was renamed to remove, so it
        is not reintroduced one underscore down.

        One connection for artefacts and observations, which is what lets a
        relation-valued filter — `obs.col(X).is_in(reg.query(q.code)…)` — see
        both sides. Two connections would make that subquery unresolvable.
        """
        from sdmxlib.local.data_store import DuckDBDataStore  # noqa: PLC0415  (cycle)

        self._require_open()
        store = self._data_store
        if store is not None:
            return store
        with self._data_plane_lock:
            # Checked again: another thread may have built it while this one waited.
            if self._data_store is None:
                self._data_store = DuckDBDataStore.from_registry(
                    self,
                    attach=self._attach,
                    extensions=["ducklake"] if self._attach else None,
                )
            return self._data_store

    def binding_for(self, flow: Dataflow | Ref[Dataflow] | SdmxUrn[Dataflow]) -> DataflowBinding | None:
        """Where this dataflow's observations live, and what they mean.

        A `DataflowBinding` is a *registration* in everything but name — the
        record of where a provider's data for one dataflow actually sits — so
        it is read from this registry like any other artefact.

        Args:
            flow: The dataflow, as an artefact, a `Ref`, or a URN.

        Returns:
            The binding, located (`locate`): its ``database_role`` names the
            catalog its table is in here, attached. `None` when this dataflow
            has no rows here.
        """
        urn = urn_of(flow)
        binding = self.get(DataflowBinding.urn_for(agency=urn.agency, id=urn.id, version=urn.version))
        return self.locate(binding) if isinstance(binding, DataflowBinding) else None

    def availability(self, flow: Dataflow | Ref[Dataflow] | SdmxUrn[Dataflow]) -> AvailabilityConstraint | None:
        """What data this dataflow holds: the stored answer to its unfiltered availability query.

        The SDMX 3.1 `AvailabilityConstraint`, which `write_manifest` stores at
        publish: one cube region of the values each dimension holds, and the
        observation count. Read from the store, so a server answers
        ``/availability/<flow>/all/all`` without scanning the observations.
        `build_component_availability` computes a filtered query, and reads
        from here each dimension the filter leaves unconstrained.

        Args:
            flow: The dataflow, as an artefact, a `Ref`, or a URN.

        Returns:
            The availability, or None when none is stored: the dataflow is not
            bound here, or its publication was written with
            ``availability=False``.
        """
        return get_availability(self._require_open(), str(urn_of(flow)))

    def locate(self, binding: DataflowBinding) -> DataflowBinding:
        """`binding` with its ``database_role`` naming the catalog its table is in here, attached.

        The stored binding names a role, ``lake``; a bundle in a `BundleEngine`
        attaches its lake as ``<prefix>__lake``, and SQL built from a binding
        (``<database_role>.<table_location>``) has to name that. A role this
        registry does not map is left as it is.

        The lake attaches when something first needs it, so opening a bundle
        to read a codelist costs nothing. A binding is that need: SQL built
        from it reads the catalog it names, so a catalog this registry
        attaches is attached before the binding is returned.
        """
        catalog = self._roles.get(binding.database_role, binding.database_role)
        if self._attach is not None and catalog in self._attach:
            self._data_plane()
        return binding if catalog == binding.database_role else attrs.evolve(binding, database_role=catalog)

    @property
    def lake_alias(self) -> str:
        """The catalog this registry's DuckLake attaches as: ``lake``, or ``<prefix>__lake`` in an engine."""
        return self._roles.get("lake", "lake")

    def dsd(self, flow: Dataflow | Ref[Dataflow] | SdmxUrn[Dataflow]) -> DataStructure | None:
        """The data structure this dataflow is built on, fully resolved.

        **Read back by URN, not off the dataflow's ref.** Locally the two
        converge — `get` already resolves a stored artefact's refs, so
        `flow.structure()` would usually answer too. By URN is the form that
        holds when it does *not*: a dataflow whose structure ref arrived
        unresolved still yields a resolved DSD here, and labelling skips any
        component whose codelist is unresolved.

        Args:
            flow: The dataflow, as an artefact, a `Ref`, or a URN.

        Returns:
            The `DataStructure`, or `None` when the dataflow or its structure
            cannot be resolved here.
        """
        dataflow = self.get(urn_of(flow))
        if not isinstance(dataflow, Dataflow) or not isinstance(dataflow.structure, Ref):
            return None
        structure = self.get(dataflow.structure.urn)
        return structure if isinstance(structure, DataStructure) else None

    def obs(self, flow: Dataflow | Ref[Dataflow] | SdmxUrn[Dataflow]) -> ObsQuery:
        """A lazy query over this dataflow's observations.

        The same language as `query`, rooted at a dataflow rather than a named
        relation — because there is no singleton observation relation to name:
        a DSD fixes each dataflow's columns, so the root is built per binding::

            obs = reg.obs(flow)
            (
                obs.filter(obs.col("REF_AREA") == "ON")
                .group_by(obs.col("SEX"))
                .agg(total=obs.col("OBS_VALUE").sum())
                .collect()
            )

        **Identity in, this registry's structure out.** `flow` names the
        dataflow; the DSD is resolved here, so an artefact resolved from
        another registry cannot make this one read its rows with foreign
        structure.

        Args:
            flow: The dataflow, as an artefact, a `Ref`, or a URN.

        Returns:
            An `ObsQuery`, lazy until a terminal.

        Raises:
            KeyError: If this registry has no binding for the dataflow — which
                means it holds no rows for it, and naming the flow is the fix.
        """
        binding = self.binding_for(flow)
        if binding is None:
            msg = (
                f"no DataflowBinding for {urn_of(flow)} — this registry holds no observations for "
                f"that dataflow. Add a binding, or open the bundle that has one."
            )
            raise KeyError(msg)
        return self._data_plane().obs(binding, dsd=self.dsd(flow))

    def browse(self) -> list[DataflowEntry]:
        """Every dataflow this registry holds rows for.

        Keyed on *bindings*, so it answers "which dataflows have observations
        here" rather than "which dataflows exist" — the second is
        `query(sl.q.dataflow)`, and a remote provider has no cheap way to
        answer the first at all, which is why this is a local method.

        Each entry's binding names the catalog its table is in here, as
        `binding_for` returns it (`locate`), so SQL built from it reads this
        bundle's lake.

        Returns:
            One `DataflowEntry` per binding, ordered by dataflow URN.
        """
        entries = [
            DataflowEntry(
                dataflow_urn=binding.dataflow.urn,
                binding=self.locate(binding),
                name=flow.name if isinstance(flow := self.get(binding.dataflow.urn), Dataflow) else None,
                source_registry=binding.source_registry,
            )
            for binding in self.get_all(DataflowBinding)
        ]
        entries.sort(key=lambda e: str(e.dataflow_urn))
        return entries

    def check_binding_drift(self, binding: DataflowBinding) -> list[DriftIssue]:
        """Inspect ``binding`` for drift against this registry.

        Returns one `DriftIssue` per detected mismatch — empty list means
        the pinned structural state matches the current registry. Use this
        when you want to enumerate or display drift; use
        ``validate_binding()`` when you want to raise on any drift.
        """
        return _validate_binding(binding, self)

    def validate_binding(self, binding: DataflowBinding) -> None:
        """Raise ``DriftError`` if ``binding`` has drifted against this registry.

        For inspection without raising, use ``check_binding_drift()``.

        Raises:
            DriftError: If any drift is detected. ``error.issues`` carries
                the full accumulated list.
        """
        issues = self.check_binding_drift(binding)
        if issues:
            raise DriftError(issues)

    # ── Reference-metadata queries ──────────────────────────────────────

    def metadata_for_target(
        self,
        target_urn: str | SdmxUrn[object],
        *,
        structure_urn: str | SdmxUrn[object] | None = None,
    ) -> list[MetadataSet]:
        """All MetadataSets whose targets include ``target_urn``.

        The reverse of ``MetadataSet.targets`` — given any artefact URN
        (a Code, Codelist, Concept, Dataflow, …), return every reported
        MetadataSet attached to it. This is the SDMX 3.0
        ``/metadata/structure/{artefactType}/…`` by-target lookup realised
        locally, and the primitive both the REST view and the search
        indexer navigate.

        Sets are returned without resolving their target/structure Refs
        (the URNs are all present) — cheap even for sets with many
        targets. Ordered by set URN for determinism.

        Args:
            target_urn: The artefact URN to find attached metadata for.
            structure_urn: Optional MSD or Metadataflow URN to filter by,
                e.g. return only reference-guide sets targeting the code.

        Returns:
            The matching MetadataSets (empty list if none).
        """
        cur = self._read_cursor()
        params: list[str] = [str(target_urn)]
        sql = "SELECT DISTINCT t.set_urn FROM metadata_target t"
        if structure_urn is not None:
            sql += " JOIN metadata_set s ON s.urn = t.set_urn AND s.structure_urn = ?"
            params.append(str(structure_urn))
        sql += " WHERE t.target_urn = ? ORDER BY t.set_urn"
        # target_urn is the last positional param; structure_urn (if any)
        # precedes it in SQL text, so order params to match.
        ordered = ([params[1]] if structure_urn is not None else []) + [params[0]]
        rows = cur.execute(sql, ordered).fetchall()

        sets: list[MetadataSet] = []
        for (set_urn,) in rows:
            artefact = readers.get_artefact(cur, set_urn, lazy_conn=self._conn, refused=self._refused)
            if artefact is not None:
                sets.append(cast("MetadataSet", artefact))
        return sets

    def metadata_report(
        self,
        set_urn: str | SdmxUrn[object],
    ) -> list[MetadataSection]:
        """Flattened, MSD-typed view of a MetadataSet's reported values.

        Returns one ``MetadataSection`` per ``metadata_value`` row,
        each carrying the leaf attribute's declared ``text_type`` (e.g.
        ``"String"`` / ``"XHTML"``) and ``is_presentational`` flag joined
        from the MSD. Sections come in the set's document order, the order the
        attributes were written.

        This is the sectioned input a chunker / search indexer consumes —
        it turns the reported tree + schema into a flat, typed list without
        the caller re-joining the MSD. Returns an empty list if the set has
        no values (or does not exist).

        Args:
            set_urn: The MetadataSet URN to project.
        """
        cur = self._read_cursor()
        type_by_path = self._msd_attribute_types(cur, str(set_urn))

        rows = cur.execute(
            """
            SELECT attribute_path, sibling_index, value_kind, scalar, i18n
            FROM metadata_value
            WHERE set_urn = ?
            ORDER BY ordinal
            """,
            [str(set_urn)],
        ).fetchall()

        sections: list[MetadataSection] = []
        for attribute_path, sibling_index, value_kind, scalar_val, i18n_val in rows:
            declared = _declared_path(attribute_path)
            leaf_id = declared.rsplit(".", 1)[-1]
            text_type, is_pres = type_by_path.get(declared, (None, False))
            # Same type the destination field declares (MetadataSection.value).
            value: ReportedAttributeValue | None
            if value_kind == "i18n":
                value = InternationalString.of(dict(i18n_val)) if i18n_val else None
            else:
                value = scalar_val
            sections.append(
                MetadataSection(
                    path=attribute_path,
                    attribute_id=leaf_id,
                    sibling_index=sibling_index,
                    value=value,
                    text_type=text_type,
                    is_presentational=is_pres,
                )
            )
        return sections

    def _msd_urn_behind(self, cur: duckdb.DuckDBPyConnection, structure_urn: str, structure_kind: str) -> str | None:
        """Walk a MetadataSet's structure ref through to its MSD URN.

        A set references a Metadataflow or a MetadataProvisionAgreement, so the
        MSD is one or two hops away (MPA → Metadataflow → MSD). Returns None if
        a hop can't be resolved.
        """
        if structure_kind == "MetadataProvisionAgreement":
            mpa = readers.get_artefact(cur, structure_urn, lazy_conn=self._conn, refused=self._refused)
            flow_ref = getattr(mpa, "metadataflow", None)
            if flow_ref is None:
                return None
            structure_urn, structure_kind = str(flow_ref.urn), "Metadataflow"
        if structure_kind == "Metadataflow":
            flow = readers.get_artefact(cur, structure_urn, lazy_conn=self._conn, refused=self._refused)
            structure_ref = getattr(flow, "structure", None)
            return str(structure_ref.urn) if structure_ref is not None else None
        return structure_urn

    def _msd_attribute_types(
        self,
        cur: duckdb.DuckDBPyConnection,
        set_urn: str,
    ) -> dict[str, tuple[str | None, bool]]:
        """Map ``attribute path → (text_type, is_presentational)`` for a set's MSD.

        Resolves the set's structure (the MSD behind a Metadataflow, or behind a
        MetadataProvisionAgreement → Metadataflow) and walks its attribute tree.
        Empty if the structure is absent.
        """
        set_row = first(
            cur.execute(
                "SELECT structure_urn, structure_kind FROM metadata_set WHERE urn = ?",
                [set_urn],
            )
        )
        if set_row is None:
            return {}
        structure_urn, structure_kind = set_row

        msd_urn = self._msd_urn_behind(cur, structure_urn, structure_kind)
        if msd_urn is None:
            return {}
        msd = readers.get_artefact(cur, msd_urn, lazy_conn=self._conn, refused=self._refused)
        if not isinstance(msd, MetadataStructure):
            return {}

        out: dict[str, tuple[str | None, bool]] = {}
        for row in tree_rows(msd):
            attr, path = row.node, row.path
            text_type: str | None = None
            if isinstance(attr.representation, Facet) and attr.representation.type is not None:
                text_type = str(attr.representation.type)
            out[path] = (text_type, attr.is_presentational)
        return out

    # ── Ref resolution ─────────────────────────────────────────────────

    def _pending_urns(
        self,
        pending: list[tuple[object, str | None, Ref[object]]],
        memo: dict[str, object],
    ) -> list[SdmxUrn[object]]:
        """The distinct URNs in *pending* that *memo* cannot already answer."""
        needed: list[SdmxUrn[object]] = []
        seen: set[str] = set()
        for _owner, _name, ref in pending:
            urn_str = str(ref.urn)
            if urn_str in memo or urn_str in seen:
                continue
            seen.add(urn_str)
            needed.append(ref.urn)
        return needed

    def _frontier(
        self,
        pending: list[tuple[object, str | None, Ref[object]]],
        memo: dict[str, object],
        walked: set[int],
    ) -> list[object]:
        """The targets the memo now holds and no round has walked: the next frontier.

        Nothing is written: each `Ref` is bound to this call's memo as it was
        built (#492), so filling the memo is what resolved it. Following the
        new targets is what makes transitive references resolve within one
        call.
        """
        frontier: list[object] = []
        for _owner, _name, ref in pending:
            target = memo.get(str(ref.urn))
            if target is not None and id(target) not in walked:
                frontier.append(target)
        return frontier

    def _resolve_refs(
        self,
        cur: duckdb.DuckDBPyConnection,
        obj: object,
        *,
        memo: _CallScopedMemo | None = None,
        walked: set[int] | None = None,
    ) -> None:
        """Resolve every unresolved ``Ref`` in *obj*'s attrs graph.

        A breadth-first walk whose rounds are three named stages:

        1. `_collect_refs` — recursive Python-only walk recording
           ``(owner, field_name, Ref)`` for unresolved Refs; no SQL.
        2. `_pending_urns` + `_resolve_urns` — deduplicate, then bulk-load,
           populating a shared per-call memo keyed by URN string.
        3. `_frontier` — the newly reached targets, the next round's work, so
           transitive references resolve in the same call (a DSD ref resolves
           to a Codelist whose own Refs also get resolved). Nothing is
           assigned: every `Ref` built here is bound to the memo (#492).

        Idempotent: already-resolved Refs are skipped. ``memo`` and ``walked``
        may be passed in to share state across artefacts (`get_all`'s batch
        path does).
        """
        if memo is None:
            # A memo over a zero-sized cache: per-call only, nothing promoted.
            # Concrete rather than a plain dict because `_resolve_scheme_items`
            # needs `set_local` to keep scheme items out of the shared cache
            # (#378).
            memo = _CallScopedMemo(_FIFOCache(0))
        if walked is None:
            walked = set()

        frontier: list[object] = [obj]
        while frontier:
            pending: list[tuple[object, str | None, Ref[object]]] = []
            for item in frontier:
                self._collect_refs(item, walked, pending)
            if not pending:
                break

            needed = self._pending_urns(pending, memo)
            if needed:
                self._resolve_urns(cur, needed, memo)

            frontier = self._frontier(pending, memo, walked)

    def _collect_refs(
        self,
        obj: object,
        walked: set[int],
        pending: list[tuple[object, str | None, Ref[object]]],
    ) -> None:
        """Pure-Python walk that records unresolved Refs into *pending*.

        `can_reach_ref` subsumes the `attrs.has` guard this used to open with,
        and answers from a per-class cache rather than re-deriving the class's
        shape per object. For a `Code` it is False, so the walk returns without
        reading a single field — 579,951 times on the 2021 Census Profile's
        `CL_GEOGRAPHY`, where this function was 52% of the eager read (#376).

        A class that returns False is deliberately *not* added to `walked`.
        Marking it would cost a `set` entry per object for a lookup that can
        only ever short-circuit a call that already returns immediately. The
        frontier in `_resolve_refs` still terminates: a re-visited object
        contributes nothing to `pending`, and the next frontier is built from
        `pending`.
        """
        # Bind the class once: `attrs.has` is a TypeGuard, and a guard on a
        # `type(obj)` *call* lands on the discarded call expression, not on
        # `obj` — so it cannot be carried to the next use. A local can.
        # Mirrors `InMemoryRegistry._walk_refs`.
        cls = type(obj)
        if isinstance(obj, type) or not can_reach_ref(cls):
            return
        oid = id(obj)
        if oid in walked:
            return
        walked.add(oid)

        # One cached lookup, not two: `walk_plan` returns the fields to read
        # with the container-of-`Ref` ones already removed, so #392's case
        # costs this walk a branch and no extra call. Three classes in the
        # model have such a field and this runs once per object in the graph —
        # 499,138 times on `H_CP_GEO_FED` — so a per-field branch would have
        # charged every node for a shape almost none of them has.
        fields, ref_containers = walk_plan(cls)
        if ref_containers:
            _drain_ref_containers(obj, ref_containers, pending)

        for name in fields:
            value = getattr(obj, name, None)
            if value is None:
                continue
            if isinstance(value, Ref):
                if not value.resolved:
                    pending.append((obj, name, value))
            elif isinstance(value, (ItemList, ArtefactList, list, tuple)):
                for item in value:
                    self._collect_refs(item, walked, pending)
            else:
                # No `attrs.has(...) and not isinstance(value, type)` guard:
                # `_collect_refs` opens with exactly that pair, so re-asking it
                # here only decided whether the early return happened at this
                # frame or the next. Dropping it costs a call on a non-attrs
                # leaf and buys the branch #392 needs.
                self._collect_refs(value, walked, pending)

    def _resolve_urns(
        self,
        cur: duckdb.DuckDBPyConnection,
        urns: list[SdmxUrn[object]],
        memo: _CallScopedMemo,
    ) -> None:
        """Bulk-load the targets for *urns*, populating *memo* in place.

        Item URNs (Code, Concept, …) are grouped by their parent scheme URN,
        so the work is one batched read per distinct scheme rather than one per
        ref — O(distinct parent schemes) in SQL round-trips, not O(refs), which
        is what makes large Hierarchies and wide DSDs tractable.

        Since #378 that read fetches *the items asked for* rather than the
        whole scheme. The grouping still matters — it is what makes a single
        batched read possible — but the read is now proportional to the answer
        instead of to the scheme.
        """
        item_urns_by_parent, top_urns = self._partition_urns(urns)
        for parent_urn_str, item_urns in item_urns_by_parent.items():
            self._resolve_scheme_items(cur, parent_urn_str, item_urns, memo)
        self._resolve_top_urns(cur, top_urns, memo)

    def _partition_urns(
        self, urns: list[SdmxUrn[object]]
    ) -> tuple[dict[str, list[SdmxUrn[object]]], list[SdmxUrn[object]]]:
        """Split *urns* into item URNs grouped by parent scheme, and top-level URNs.

        Item URNs whose artefact class has no known parent scheme are dropped —
        nothing can resolve them, so they simply stay absent from the memo.

        Args:
            urns: The URNs to partition.

        Returns:
            A ``(item_urns_by_parent, top_urns)`` pair.
        """
        item_urns_by_parent: dict[str, list[SdmxUrn[object]]] = {}
        top_urns: list[SdmxUrn[object]] = []
        for parsed in urns:
            if parsed.item_id is None:
                top_urns.append(parsed)
                continue
            parent_urn_str = self._parent_scheme_urn(parsed)
            if parent_urn_str is None:
                continue
            item_urns_by_parent.setdefault(parent_urn_str, []).append(parsed)
        return item_urns_by_parent, top_urns

    def _resolve_scheme_items(
        self,
        cur: duckdb.DuckDBPyConnection,
        parent_urn_str: str,
        item_urns: list[SdmxUrn[object]],
        memo: _CallScopedMemo,
    ) -> None:
        """Memoise every requested item, reading as little as possible for it.

        Three routes, in order of cost:

        1. **A scheme already resolved** — free, and covers the caller who
           genuinely wants the whole thing.
        2. **A targeted batched read** of exactly the item URNs asked for. This
           is the #378 fix. The previous behaviour loaded the item's *entire*
           parent scheme, which is why a 28-node hierarchy over `CL_GEOGRAPHY`
           cost 2.09 s to read 28 codes out of 579,951. Reading only what was
           asked for is never slower and up to 178x faster — see
           `resolve.codes_by_urn` for the measurements.
        3. **Loading the scheme**, for the two cases (2) cannot serve.

        Any item that turns out not to exist is skipped rather than raising: an
        absent memo entry is how an unresolvable Ref is expressed.

        Args:
            cur: Cursor to read through.
            parent_urn_str: URN of the scheme holding every URN in *item_urns*.
            item_urns: Item URNs to resolve. Must be non-empty.
            memo: Resolution memo, mutated in place.
        """
        sample = item_urns[0]
        owner = _owner_of(sample.artefact_class)
        if owner is None:
            return
        if self._items_from_cached_scheme(parent_urn_str, owner, item_urns, memo):
            return
        targeted = _TARGETED_READS.get(sample.artefact_class)
        if targeted is not None and sample.version != "latest":
            for urn_str, item in targeted(cur, [str(u) for u in item_urns]).items():
                memo.set_local(urn_str, item)
            return
        self._items_from_loaded_scheme(cur, parent_urn_str, owner, sample, item_urns, memo)

    def _items_from_cached_scheme(
        self,
        parent_urn_str: str,
        owner: type,
        item_urns: list[SdmxUrn[object]],
        memo: _CallScopedMemo,
    ) -> bool:
        """Serve the items from a scheme already built, if there is one.

        Free when it hits, and it is why the targeted read below does not have
        to worry about a caller who *does* want the whole scheme: if something
        else in this call (or a recent one) already loaded it, that is used.

        A cached `LazyCodelist` or `LazyHierarchy` does not count: its
        subscript queries the store once per item, where the targeted read
        queries once per scheme. A data structure's read caches its codelists
        as facades, and a 499,138-node hierarchy read after it took 337 s
        instead of 0.8 s (GH-150).
        """
        parent = memo.get(parent_urn_str)
        if parent is None or isinstance(parent, (LazyCodelist, LazyHierarchy)):
            return False
        _memo_items(parent, owner, item_urns, memo)
        return True

    def _items_from_loaded_scheme(
        self,
        cur: duckdb.DuckDBPyConnection,
        parent_urn_str: str,
        owner: type,
        sample: SdmxUrn[object],
        item_urns: list[SdmxUrn[object]],
        memo: _CallScopedMemo,
    ) -> None:
        """Load the whole parent scheme, then take the items from it.

        The pre-#378 path, kept for the two cases the targeted read cannot
        serve: a `Concept`, which has no batched by-URN reader (concept schemes
        are small — 349 concepts in the whole 2021 Census Profile bundle — so there is
        nothing to win by adding one), and a `version="latest"` item URN, whose
        stored `code_urn` carries a concrete version instead and so cannot be
        matched directly.
        """
        parent = self._load_parent_scheme(cur, parent_urn_str, owner, sample, memo)
        if parent is None:
            return
        _memo_items(parent, owner, item_urns, memo)

    def _resolve_top_urns(
        self,
        cur: duckdb.DuckDBPyConnection,
        top_urns: list[SdmxUrn[object]],
        memo: dict[str, object],
    ) -> None:
        """Load each maintainable URN not already memoized, one round-trip apiece.

        Args:
            cur: Cursor to load through.
            top_urns: Maintainable-level URNs (no ``item_id``).
            memo: Resolution memo, mutated in place.
        """
        for parsed in top_urns:
            urn_str = str(parsed)
            if urn_str in memo:
                continue
            target = readers.get_artefact(cur, urn_str, lazy_conn=self._conn, refused=self._refused)
            if target is not None:
                memo[urn_str] = target

    def _parent_scheme_urn(self, parsed: SdmxUrn[object]) -> str | None:
        """The parent scheme URN string for an item-level URN, or None.

        A thin unpack over `_scheme_urn_for`, which does the work and holds
        the cache — see there for why there is one.
        """
        return _scheme_urn_for(parsed.artefact_class, parsed.package, parsed.agency, parsed.id, parsed.version)

    def _load_parent_scheme(
        self,
        cur: duckdb.DuckDBPyConnection,
        parent_urn_str: str,
        owner: type,
        sample: SdmxUrn[object],
        memo: dict[str, object],
    ) -> object | None:
        """Load (eager) a parent scheme for item resolution, memoized.

        Honours the version=``"latest"`` fallback that the previous
        per-ref resolver used.
        """
        # The memo is heterogeneous by construction; annotate on the way out
        # so the Any does not ride along into the caller.
        cached: object | None = memo.get(parent_urn_str)
        if cached is not None:
            return cached
        parent = readers.get_artefact(cur, parent_urn_str, eager=True, refused=self._refused)
        if parent is None and sample.version == "latest":
            row = first(
                cur.execute(
                    """
                SELECT urn FROM artefact
                WHERE artefact_type = ? AND agency_id = ? AND resource_id = ?
                """,
                    [owner.__name__, sample.agency, sample.id],
                )
            )
            if row:
                parent = readers.get_artefact(cur, row[0], eager=True, refused=self._refused)
        if parent is not None:
            memo[parent_urn_str] = parent
        return parent

    # ── Ref ────────────────────────────────────────────────────────────────

    def ref[T](self, urn: SdmxUrn[T]) -> Ref[T]:
        """Return an unresolved Ref for this URN."""
        return Ref[T].unresolved(urn)

    # ── Contains / len ────────────────────────────────────────────────────

    def __contains__(self, target: object, /) -> bool:
        """True if this store holds the artefact named by `target`.

        Takes a `Ref` as well as an `SdmxUrn`, for the same reason `get` does
        (#370) — ``ref in reg`` answered ``False`` for a *present* artefact.
        Anything else is still ``False`` rather than an error: ``in`` is a
        question, and the answer for an unrelated object is "no".
        """
        if isinstance(target, (SdmxUrn, Ref)):
            cur = self._read_cursor()
            row = first(
                cur.execute(
                    "SELECT 1 FROM artefact WHERE urn = ?",
                    [str(target_urn(target))],
                )
            )
            return row is not None
        return False

    def __len__(self) -> int:
        cur = self._read_cursor().execute("SELECT COUNT(*) FROM artefact")
        return count(cur)

    def __repr__(self) -> str:
        if self.closed:
            # A repr that raises takes every error message naming it down too.
            return f"LocalRegistry({self._uri!r}, closed)"
        cur = self._read_cursor()
        rows = cur.execute("SELECT artefact_type, COUNT(*) FROM artefact GROUP BY artefact_type").fetchall()
        counts = ", ".join(f"{name}={cnt}" for name, cnt in rows)
        return f"LocalRegistry({counts or 'empty'})"

    # ── Mutations ─────────────────────────────────────────────────────────

    # ── Refusals ──────────────────────────────────────────────────────────

    @property
    def refused(self) -> tuple[Refusal, ...]:
        """Every stored artefact a read refused since the store last changed.

        A store read checks each artefact it builds as `add` would. `get` raises
        for one it refuses; every other read — `get_all`, a query's
        `collect()`, resolving a reference — leaves it out, records it here,
        and reads on, as `parse(strict=False)` does. A reference to it stays
        unresolved. `add`, `drop` and `replace` clear it: what they changed may
        be what was refused.
        """
        return tuple(self._refused.values())

    def _remember(self, exc: SdmxConformanceError) -> None:
        for refusal in exc.refused:
            self._refused[refusal.urn] = refusal

    # ── Resolver cache ────────────────────────────────────────────────────

    def clear_resolve_cache(self) -> None:
        """Purge the resolver cache and reset hit/miss counters.

        Useful for explicit invalidation hooks (e.g. a webhook telling the
        service that upstream metadata changed without going through this
        registry's mutation API). Cache also clears automatically on every
        ``add`` / ``drop`` / ``replace``.
        """
        self._resolve_cache.clear()
        self._resolve_cache.reset_stats()

    def resolve_cache_info(self) -> dict[str, int]:
        """Return resolver cache stats: entries, weight, max_items, hits, misses, refused.

        Intended for ops dashboards and integration tests asserting cache
        behaviour. ``entries`` and ``weight`` are 0 after construction or any
        mutation and rise as ``get`` / ``get_all`` populate it; ``weight`` is
        the one capped, at ``max_items``.

        **``weight`` is the number to watch, not ``entries``.** An entry is an
        artefact, and artefacts differ in size by
        four orders of magnitude, so five of them can be 4.4 GB. ``entries``
        remains reported because a hit rate is per lookup, not per item.

        ``refused`` counts artefacts too heavy for the bound to hold at all.
        A non-zero value with a poor hit rate is the signature of a working set
        that does not fit: raise ``resolve_cache_items``, or accept the re-read.
        """
        return {
            "entries": len(self._resolve_cache),
            "weight": self._resolve_cache.weight,
            "max_items": self._resolve_cache.maxitems,
            "hits": self._resolve_cache.hits,
            "misses": self._resolve_cache.misses,
            "refused": self._resolve_cache.refused,
        }

    # ── Direct SQL helpers ────────────────────────────────────────────────

    def code_map(self, codelist_urn: str | SdmxUrn[object], *, lang: str) -> dict[str, str]:
        """Return {code_id: label} for a codelist without creating Code objects."""
        return readers.code_map(self._read_cursor(), str(codelist_urn), lang=lang)

    def dsd_code_map(self, dsd_urn: str | SdmxUrn[object], *, lang: str) -> dict[str, dict[str, str]]:
        """Return {component_id: {code_id: label}} for all enumerated dimensions."""
        return readers.code_map_for_dsd(self._read_cursor(), str(dsd_urn), lang=lang)

    # ── Bulk ──────────────────────────────────────────────────────────────

    # ── Escape hatch ──────────────────────────────────────────────────────

    def to_registry(self) -> InMemoryRegistry:
        """Load everything into an `InMemoryRegistry`, every reference bound to it.

        Each artefact is built bound to the new registry, so `add`
        copies nothing, and every reference finds its target there once the
        load is done — the whole store is in it, so nothing is resolved here.
        """
        reg = InMemoryRegistry()
        cur = self._read_cursor()
        with binding(reg._resolver):
            rows = cur.execute("SELECT urn FROM artefact").fetchall()
            for (urn_str,) in rows:
                obj = readers.get_artefact(cur, urn_str, eager=True, refused=self._refused)
                if obj is not None:
                    reg.add(obj)
        return reg


class LocalRegistry(BundleRegistry):
    """DuckDB-backed persistent SDMX artefact store: `BundleRegistry`'s reads plus the writes.

    Path-style construction. ``LocalRegistry(uri)`` is cheap — it just stores
    the URI. The DuckDB connection is opened by ``open()`` (or implicitly
    by ``__enter__`` when used as a context manager) and closed on exit.

    Typical usage::

        with LocalRegistry("store.duckdb") as reg:
            reg.add(codelist)
            cl = reg.get(codelist_urn)  # LazyCodelist

    Sharing an existing DuckDB connection (e.g. when co-located with a
    DuckLake catalog the caller already opened) is done via
    `from_connection`, which doesn't take ownership of the
    connection's lifecycle. ``LocalRegistry.build(path)`` opens a bundle
    directory for building; ``LocalRegistry.bundle(path)`` opens a published
    one, read-only, as a `BundleRegistry`.

    **Concurrency.** Reads are safe from multiple threads, as on
    `BundleRegistry`. Writes (``add`` / ``drop`` / ``replace`` /
    ``add_all`` / ``ingest``) are single-threaded — serialise externally
    if you need to mutate from multiple threads. See
    ``docs/design/concurrency.md`` for the model and deployment
    recipes.
    """

    _READ_ONLY: ClassVar[bool] = False

    @classmethod
    def build(
        cls,
        path: str | Path,
        *,
        previous: str | None = None,
        resume: str | None = None,
        source_registry: str | None = None,
        resolve_cache_items: int = DEFAULT_RESOLVE_CACHE_ITEMS,
    ) -> Self:
        """Start a new publication of the bundle at `path`, and open it for building.

        Mints the publication's token, a UUIDv7, and creates its directory,
        ``<path>/<token>/``, which `publication` names. ``meta.duckdb`` and the
        lake open read-write; the lake writes its data files into
        ``<path>/data/``, which every publication of the bundle shares. A
        builder writes artefacts and observations, closes, then publishes with
        ``sdmxlib.bundle.write_manifest(builder.publication, ...)``; readers open
        the result with ``LocalRegistry.bundle(builder.publication)``.

        With `previous`, the new publication extends that one: its
        ``meta.duckdb`` and ``lake.ducklake`` are copied in, so the builder
        changes only what differs and the lake writes only new data files.
        Without it, both start empty. Neither touches an earlier publication's
        directory, and nothing is ever deleted from ``data/``: files an earlier
        publication lists stay until garbage collection finds no manifest
        listing them.

        With `resume`, it reopens a publication `build` started and nobody has
        published yet, for a builder that writes in more than one step.

        Args:
            path: The bundle directory, holding ``data/`` and one directory per
                publication. Created if missing.
            previous: The token of the published publication to extend, or
                None for a bundle's first.
            resume: The token of an unpublished publication to reopen, instead
                of starting one.
            source_registry: Provenance tag for the artefacts added.
            resolve_cache_items: As the constructor.

        Returns:
            An open, writable registry over the publication.

        Raises:
            ManifestError: If `previous` is not a published publication of this
                bundle, or `resume` is not an unpublished one.
            ValueError: If both `previous` and `resume` are given.
        """
        bundle = Path(path)
        if resume is not None:
            if previous is not None:
                msg = "pass previous= to start a publication or resume= to reopen one, not both"
                raise ValueError(msg)
            return cls._resume(bundle, resume, source_registry=source_registry, resolve_cache_items=resolve_cache_items)
        token = str(uuid.uuid7())
        publication = bundle / token
        if previous is not None:
            source = bundle / previous
            extended = read_manifest(source)
            refuse_engine_mismatch(extended, source)
            if extended.data_path != DATA_PATH:
                msg = f"{source} keeps its data files outside {bundle / 'data'}, so a publication cannot extend it"
                raise ManifestError(msg)
            publication.mkdir(parents=True)
            for name in ("meta.duckdb", "lake.ducklake"):
                if (source / name).exists():
                    shutil.copyfile(source / name, publication / name)
        else:
            publication.mkdir(parents=True)
        (bundle / "data").mkdir(exist_ok=True)
        registry = cls._over_bundle(
            publication,
            bundle / "data",
            source_registry=source_registry,
            resolve_cache_items=resolve_cache_items,
        ).open()
        if previous is not None:
            # Published stores leave out keys a reader does not need; a store
            # being written gets them back (GH-165).
            restore(registry.connection)
        registry.connection.execute("DELETE FROM sdmx._meta WHERE key IN ('publication_token', 'previous_token')")
        registry.connection.execute("INSERT INTO sdmx._meta (key, value) VALUES ('publication_token', ?)", [token])
        if previous is not None:
            registry.connection.execute("INSERT INTO sdmx._meta (key, value) VALUES ('previous_token', ?)", [previous])
        return registry

    @classmethod
    def _resume(cls, bundle: Path, token: str, *, source_registry: str | None, resolve_cache_items: int) -> Self:
        """`build(bundle, resume=token)`: reopen an unpublished publication writable."""
        publication = bundle / token
        if (publication / MANIFEST_NAME).exists():
            msg = (
                f"{publication} is published, and a publication is never rewritten. "
                f"Start a new one with LocalRegistry.build({str(bundle)!r}, previous={token!r})."
            )
            raise ManifestError(msg)
        if not (publication / "meta.duckdb").is_file():
            msg = f"{publication} is not a publication LocalRegistry.build started"
            raise ManifestError(msg)
        registry = cls._over_bundle(
            publication, bundle / "data", source_registry=source_registry, resolve_cache_items=resolve_cache_items
        ).open()
        if meta_value(registry.connection, "publication_token") != token:
            registry.close()
            msg = f"{publication} is not a publication LocalRegistry.build started"
            raise ManifestError(msg)
        return registry

    @property
    def publication(self) -> Path:
        """The publication directory `build` created, named by its token.

        Raises:
            ValueError: If this registry was not opened with `build`.
        """
        if meta_value(self.connection, "publication_token") is None:
            msg = f"{self.uri} is not a publication; LocalRegistry.build(path) starts one"
            raise ValueError(msg)
        return self.uri.parent

    @contextmanager
    def transaction(self) -> Generator[None]:
        """Group writes into a single DuckDB transaction.

        Wraps any sequence of ``add`` / ``drop`` / ``replace`` calls in
        one BEGIN/COMMIT so DuckDB amortises WAL flush and index
        maintenance across the batch instead of paying autocommit per
        statement::

            with reg.transaction():
                for artefact in many_artefacts:
                    reg.add(artefact)

        On exception the transaction rolls back and is re-raised.
        Either way, the resolver cache is cleared once at exit (a
        partial in-flight state may have left it stale).

        Nestable as a no-op: re-entering ``transaction`` inside an
        already-open block does not emit a second BEGIN — DuckDB
        rejects nested transactions, and silently joining the outer
        block is what callers usually want.

        When the ``sdmxlib.local.registry`` logger is set to DEBUG,
        a one-line ``transaction_summary`` is emitted at exit with the
        artefact-count and total-wall-time breakdown by class.
        """
        if self._in_transaction:
            yield
            return
        conn = self._c
        conn.begin()
        self._in_transaction = True
        self._txn_stats.clear()
        t_start = time.perf_counter() if _logger.isEnabledFor(logging.DEBUG) else 0.0
        try:
            yield
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            self._in_transaction = False
            self._resolve_cache.clear()
            self._refused.clear()
            if _logger.isEnabledFor(logging.DEBUG) and self._txn_stats:
                total = time.perf_counter() - t_start
                count = sum(n for n, _ in self._txn_stats.values())
                by_class = " ".join(f"{cls}:{n}({wall:.2f}s)" for cls, (n, wall) in sorted(self._txn_stats.items()))
                _logger.debug("transaction_summary count=%d wall=%.2f by_class=[%s]", count, total, by_class)
                self._txn_stats.clear()

    def add(self, artefact: SupportsUrn, *, source_registry: str | None = None) -> None:
        """Add or replace an artefact in the store.

        ``source_registry`` overrides the registry-level default for this
        call. ``None`` (default) means "use the registry-level default."

        Clears the resolver cache: any cached resolved target may now be
        stale (e.g. a freshly-added Codelist replaces the one a previous
        ``get`` had cached).

        **Single-threaded.** Mutations are not safe under concurrent
        callers — do not invoke ``add`` / ``drop`` / ``replace`` from
        multiple threads against the same ``LocalRegistry``. Concurrent reads
        through ``get`` / ``get_all`` / ``code_map`` etc. are safe. See
        ``docs/design/concurrency.md``.
        """
        # `validate`, and a version only SDMX 2.1 accepts, which has no place
        # in a 3.x model; the rewrite that admits one is `add_all`'s (#420).
        check_ingestable([artefact])
        self._put(artefact, source_registry=source_registry)
        advise(metadata_sets_advisories([artefact], self.get))

    def _put(self, artefact: SupportsUrn, *, source_registry: str | None) -> None:
        """`add` without `check_ingestable`, for a batch `add_all` has checked whole."""
        self._refuse_unrepresentable(artefact)
        tag = source_registry if source_registry is not None else self._source_registry
        timed = _logger.isEnabledFor(logging.DEBUG)
        t0 = time.perf_counter() if timed else 0.0
        writers.put_artefact(self._c, artefact, source_registry=tag)
        if timed:
            wall = time.perf_counter() - t0
            cls = type(artefact).__name__
            _logger.debug("artefact_persist class=%s wall=%.4f urn=%s", cls, wall, artefact_urn(artefact))
            if self._in_transaction:
                n, total = self._txn_stats.get(cls, (0, 0.0))
                self._txn_stats[cls] = (n + 1, total + wall)
        # Inside an open ``transaction()`` block the cache is cleared
        # once at exit; per-call clearing would just churn it.
        if not self._in_transaction:
            self._resolve_cache.clear()
            self._refused.clear()

    def add_all(
        self,
        artefacts: Iterable[SupportsUrn],
        *,
        source_registry: str | None = None,
        rewrite_version: VersionRewrite | None = None,
    ) -> tuple[RewrittenVersion, ...]:
        """Add multiple artefacts in a single transaction.

        Equivalent to::

            with reg.transaction():
                for a in artefacts:
                    reg.add(a, source_registry=source_registry)

        except that every version in the batch is checked before anything is
        written, so a refusal names every problem at once.

        Convenience wrapper for the common case where the caller has a
        ready iterable. For interleaved or conditional writes, use
        ``transaction()`` directly.

        Args:
            artefacts: The batch.
            source_registry: Overrides the registry-level default, as for `add`.
            rewrite_version: Rewrites versions SDMX 3.x cannot hold — a mapping
                from published to ingested version, or a function such as
                `sl.versions.strip_leading_zeros`. Applies to every artefact
                in the batch and every reference inside one, and each rewrite
                is recorded in the store's `version_rewrite` table.

        Returns:
            One `RewrittenVersion` per artefact whose version changed.

        Raises:
            ValidationError: If a version only SDMX 2.1 accepts is left after
                the rewrite, if the rewrite makes two artefacts one or
                replaces one the store holds, or if an artefact outside the
                batch references a version the batch rewrites.
        """
        batch, rewrites = rewrite_versions(artefacts, rewrite_version)
        check_ingestable(batch)
        with self.transaction():
            self._refuse_rewrites(batch, rewrites)
            for artefact in batch:
                self._put(artefact, source_registry=source_registry)
            for rewrite in rewrites:
                writers.put_version_rewrite(self._c, rewrite.urn, rewrite.original)
        advise(metadata_sets_advisories(batch, self.get))
        return rewrites

    def _refuse_rewrites(self, batch: list[SupportsUrn], rewrites: tuple[RewrittenVersion, ...]) -> None:
        """What only the store can know: a collision, or a reference left dangling.

        A URN the store holds *as a rewrite of the same version* is no
        collision: that is the same batch ingested again.
        """
        if not rewrites:
            return
        issues: list[ValidationIssue] = []
        in_batch = {str(artefact_urn(artefact)) for artefact in batch}
        for rewrite in rewrites:
            held = first(
                self._c.execute(
                    """
                SELECT r.original_version
                FROM artefact a LEFT JOIN version_rewrite r ON r.urn = a.urn
                WHERE a.urn = ?
                """,
                    [rewrite.urn],
                )
            )
            if held is not None and held[0] != rewrite.original:
                issues.append(collision(rewrite))
            # Only a reference to a version nothing here answers dangles: under
            # a mapping such as `{"1.0": "1.0.0"}` a held `1.0` stays, and so
            # do the references to it.
            if first(self._c.execute("SELECT 1 FROM artefact WHERE urn = ?", [original_urn(rewrite)])):
                continue
            old = SdmxUrn.parse(original_urn(rewrite))
            # A prefilter: `LIKE` treats `_` in an id as a wildcard, and the
            # exact test is `names`, on the parsed URN.
            candidates = self._c.execute(
                "SELECT source_urn, target_urn FROM artefact_ref WHERE target_urn LIKE ?",
                [f"%={old.agency}:{old.id}({old.version})%"],
            ).fetchall()
            issues.extend(
                dangling(source, rewrite)
                for source, target in candidates
                if source not in in_batch and names(SdmxUrn.parse(target), str(old))
            )
        if issues:
            raise ValidationError(issues)

    def write_observations(
        self,
        *,
        schema: str,
        table: str,
        source: WriteSource,
        partition_by: tuple[str, ...] = (),
        sorted_by: tuple[str, ...] = (),
        mode: Literal["overwrite", "append"] = "overwrite",
    ) -> None:
        """Write rows into a table of this registry's lake, ``<lake>.<schema>.<table>``.

        The data-plane half of building a publication (`build`): the rows a
        `DataflowBinding` then names by ``table_location="<schema>.<table>"``.
        ``source`` is anything this registry's connection can read: a Polars
        frame, an Arrow table or reader, or a relation on `connection`,
        including one over a database attached to it.

        Args:
            schema: Schema in the lake, created if absent.
            table: Table in the schema.
            source: The rows.
            partition_by: Columns to partition the table by, in order. The
                lake's catalog records them, and queries read them from there.
            sorted_by: DuckLake sort expressions, outermost first, set as the
                table's ``SORTED BY`` so DuckLake writes and keeps the rows
                sorted (`DuckDBDataStore.write_observations`).
            mode: ``"overwrite"`` replaces the table; ``"append"`` inserts
                into it and keeps its partitioning.

        Raises:
            KeyError: If this registry has no lake: one `build` starts has it,
                `LocalRegistry(path)` has not.
            ValueError: If a name is not an identifier, a ``partition_by``
                column is not one of ``source``'s, or an append names
                ``partition_by`` or ``sorted_by``.
        """
        store = self._data_plane()
        attached = {
            name for (name,) in store.connection.execute("SELECT database_name FROM duckdb_databases()").fetchall()
        }
        if self.lake_alias not in attached:
            msg = (
                f"this registry has no lake ({self.lake_alias!r} is not attached; attached: "
                f"{', '.join(sorted(attached))}), so there is nowhere to write {schema}.{table}. "
                "A publication's registry has one: LocalRegistry.build(bundle)."
            )
            raise KeyError(msg)
        store.write_observations(
            schema=schema,
            table=table,
            source=source,
            partition_by=partition_by,
            sorted_by=sorted_by,
            mode=mode,
            lake_alias=self.lake_alias,
        )

    def sql(self, text: str, **params: object) -> RawSql:
        """Run raw SQL against this registry's layout — the escape hatch.

        Two things at once. A terminal:

            reg.sql("SELECT agency_id, count(*) FROM sdmx.artefact GROUP BY 1").collect()

        and a single-column relation the structural DSL can splice:

            urns = reg.sql("SELECT urn FROM sdmx.artefact WHERE resource_id LIKE $p", p="CL_%")
            reg.query(sl.q.dataflow).filter(sl.q.dataflow.urn.is_in(urns))

        Deliberately a *different method on a different object* from `filter`.
        The structural DSL still never accepts text; this is visibly not it at
        the call site, which is the property that ban was protecting.

        **Values are parameters, never interpolation.** Placeholders are
        `$name` and every one needs a matching keyword argument. Name columns
        through `sl.schema` rather than as literal strings, so a raw query is
        coupled to the layout and a schema change is a loud failure.

        Anything reached often through here is a candidate for the DSL — that
        is what the hatch is for.

        Args:
            text: One SQL statement, with `$name` placeholders for values.
            params: A value per placeholder.

        Returns:
            A lazy `RawSql`. Nothing has run yet.

        Raises:
            RawSqlError: If the SQL will not parse, is more than one statement,
                or its placeholders and arguments do not correspond.
            RuntimeError: If the registry is closed.
        """
        return prepare(self._require_open(), text, params)

    def scan_csv(self, source: str | Path | bytes) -> ObsQuery:
        """An SDMX-CSV file's observations, as a query — the structure this registry holds.

        The file names its own structure (``STRUCTURE`` / ``STRUCTURE_ID``,
        or 1.0's ``DATAFLOW``), and it is looked up here, so what is needed
        is the dataflow and its structure — not a binding, and not rows::

            with sl.LocalRegistry(":memory:") as reg:
                reg.add_all([codelists, concepts, dsd, flow])
                obs = reg.scan_csv("exr.csv")
                obs.pivot()

        The same `ObsQuery` `obs(flow)` returns, over the file's
        rows: filters, `pivot`, `validate`, the sinks. A path is read in place
        on every query, and once as it is scanned, to refuse a delete row;
        bytes are loaded once. See `sdmxlib.local._scan`.

        Args:
            source: An SDMX-CSV file, or its bytes.

        Returns:
            An `ObsQuery` over the file, lazy until a terminal.

        Raises:
            ValueError: If the file names no structure, or one this registry
                cannot resolve; or if it names a data structure rather than a
                dataflow or provision agreement — an observation query is over
                a dataflow, and a structure alone does not say which.
            SdmxParseError: If the data is not readable SDMX-CSV, lacks a
                column the structure declares, or holds a ``D`` (delete) row
                in its ``ACTION`` column, which says what to remove rather
                than what was observed.
            ValueError: If a value does not fit its component's type, when the
                file is read; for a path, when a query reads the value.
        """
        structure, flow = _resolve_structure(source, self)
        if flow is None:
            msg = (
                f"this file names the data structure {structure.id}, not a dataflow — an observation query is "
                "over a dataflow, and a structure alone does not say which. Name the dataflow in the file "
                "(STRUCTURE=dataflow), or a provision agreement for it."
            )
            raise ValueError(msg)
        return scan_csv_into(self._data_plane(), source, dsd=structure, dataflow=flow)

    def scan_json(self, source: str | Path | bytes) -> ObsQuery:
        """An SDMX-JSON data message's observations, as a query — the structure this registry holds.

        Either version, 1.0 or 2.0, told apart by its envelope. The message
        names its dataflow by a ``rel="dataflow"`` link, which is looked up
        here, as `scan_csv` looks up a file's ``STRUCTURE_ID``. JSON does not
        stream, so the message is decoded and its rows loaded once.

        Args:
            source: An SDMX-JSON data message, or its bytes.

        Returns:
            An `ObsQuery` over the message's observations.

        Raises:
            SdmxParseError: If the message is not a readable SDMX-JSON data message.
            ValueError: If it names no dataflow, names one this registry does
                not hold, or holds a value that does not fit its component's type.
        """
        frame, urn = read_observations(source)
        dsd, flow = resolve_flow(urn, self.get)
        return scan_frame_into(self._data_plane(), frame, dsd=dsd, dataflow=flow)

    def drop(self, urn: SdmxUrn[object]) -> bool:
        """Remove an artefact by URN. Returns True if it existed.

        Clears the resolver cache (cached resolved targets may reference
        the dropped artefact).

        **Single-threaded** — see `add`.
        """
        result = writers.drop_artefact(self._c, str(urn))
        self._resolve_cache.clear()
        self._refused.clear()
        return result

    def replace(self, old_urn: SdmxUrn[object], new: SupportsUrn) -> None:
        """Remove the old artefact and add the new one.

        Each leg clears the resolver cache via `add` / `drop`;
        the net effect is a single fully-cleared cache at the end.

        **Single-threaded** — see `add`.
        """
        writers.drop_artefact(self._c, str(old_urn))
        self.add(new)

    def ingest(
        self,
        registry: InMemoryRegistry,
        *,
        source_registry: str | None = None,
    ) -> None:
        """Copy all artefacts from an `InMemoryRegistry` into this store.

        ``source_registry`` overrides the registry-level default for this
        bulk import.
        """
        self.add_all(registry._store.values(), source_registry=source_registry)


def _drain_ref_containers(
    obj: object,
    ref_containers: frozenset[str],
    pending: list[tuple[object, str | None, Ref[object]]],
) -> None:
    """Record the `Ref`s of every container-of-`Ref` field on *obj* (#392).

    Reached only for the three classes that have such a field; `walk_plan` has
    already removed them from the fields the caller loops over, so this and
    that loop cannot double-count.
    """
    for name in ref_containers:
        value = getattr(obj, name, None)
        if value is not None:
            _collect_container_refs(obj, value, pending)


def _collect_container_refs(
    owner: object,
    value: Iterable[object],
    pending: list[tuple[object, str | None, Ref[object]]],
) -> None:
    """Record the unresolved `Ref`s of a container-of-`Ref`s field (#392).

    Reached only for a field `ref_container_fields` names — five in the whole
    model, each holding a handful of elements — so the `isinstance` here is
    free. Asking it of *every* container's elements was not: on `CL_GEOGRAPHY`
    that was 579,951 questions the class already knew the answer to, and the
    entire measured cost of this fix before the branch moved to the field.

    `None` for the field name because a `Ref` inside a container has no
    `owner.name`; nothing is assigned to either kind any more (#492).
    """
    pending.extend((owner, None, item) for item in value if isinstance(item, Ref) and not item.resolved)


def _refuse_annotation_opt_out(annotations: bool, root: str) -> None:  # noqa: FBT001
    """Raise unless *annotations* is True, naming the roots that can opt out.

    A flag silently ignored is worse than a flag refused: the caller measures
    no improvement and has no way to learn why (#381).
    """
    if not annotations:
        msg = (
            f"collect(annotations=False) is not supported on {root} — its objects "
            "are built by a reader that always reads them. It is supported on "
            "q.code and q.hcode, where the annotation join is a separate query."
        )
        raise ValueError(msg)


_RESOLVERS.update(
    {
        "artefact": lambda reg: reg._resolve_artefact_keys,
        "code": lambda reg: reg._resolve_code_keys,
        "hierarchy_node": lambda reg: reg._resolve_hcode_keys,
        "metadata_value": lambda reg: reg._sections_for_keys,
    }
)
