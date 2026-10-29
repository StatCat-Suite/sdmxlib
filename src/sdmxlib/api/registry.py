"""RestRegistry — HTTP client for an SDMX REST endpoint."""

import tempfile
import threading
from collections.abc import Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Literal, Never, Self, Unpack, cast, overload

import attrs

from sdmxlib._read_errors import reading_csv
from sdmxlib._singleflight import SingleFlight
from sdmxlib.api.client import NOT_MODIFIED, HttpxAuthOption, HttpxOptions, HttpxTransportOptions, SdmxClient
from sdmxlib.api.providers import ProviderInfo
from sdmxlib.api.session import Completeness, Session, completeness_of
from sdmxlib.formats import parse as _parse
from sdmxlib.formats.data_format import DataFormat
from sdmxlib.formats.sdmx_json.data import read_observations
from sdmxlib.local._scan import resolve_flow, scan_csv_into, scan_frame_into
from sdmxlib.local.data_store import DuckDBDataStore, column_types
from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model._binding import binding
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.binding import DataflowBinding, DriftError, DriftIssue, _validate_binding
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.errors import Refusal
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref, SupportsUrn, artefact_urn, target_urn
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import SdmxConformanceError
from sdmxlib.query._closure import closure, detail_for, reached_columns, references_for
from sdmxlib.query._common import canonical_key
from sdmxlib.query._execute import fetch_into
from sdmxlib.query._expr import AnyPred, Cols, Compare, RowKind
from sdmxlib.query._plan import Plan
from sdmxlib.query._pushdown import PushdownError
from sdmxlib.query._rest import data_request_for
from sdmxlib.query.observations import Observations, ObsQuery
from sdmxlib.query.query import Query

SDMX_GLOBAL_REGISTRY = "https://registry.sdmx.org/sdmx/v2"


#: Schema the fetched observation tables live in, inside the query store.
_FETCHED_SCHEMA = "sdmx_data"


def _fetched_table(urn: SdmxUrn[Dataflow]) -> str:
    """A table name for one dataflow's fetched rows.

    Derived from the URN rather than generated, so the same dataflow reuses one
    table across queries and a reader can tell which is which. Every character
    outside the SDMX id grammar becomes `_`, which cannot collide: SDMX ids are
    already restricted to letters, digits, `_`, `-` and `.`.
    """
    parts = f"{urn.agency}_{urn.id}_{urn.version}"
    return "obs_" + "".join(c if c.isalnum() or c == "_" else "_" for c in parts)


def _flow_urn(flow: Dataflow | Ref[Dataflow] | SdmxUrn[Dataflow]) -> SdmxUrn[Dataflow]:
    """The URN of whatever names a dataflow — identity only, never structure."""
    if isinstance(flow, Ref):
        return flow.urn
    if isinstance(flow, Dataflow):
        return flow.urn
    return flow


def _detail_of[R: "RowKind"](plan: Plan[R], projection: frozenset[tuple[str, str]] | None) -> str:
    """What `detail=` this plan and projection will ask for.

    Used as part of the fetch key. Derived by the same call the request itself
    makes, so the key cannot disagree with the wire: a second derivation here
    would be a place for the two to drift apart silently.
    """
    if projection is None:
        return "full"
    return detail_for(reached_columns(plan) | projection, closure(plan))


def _refuse_unbounded_fetch(root: Observations, residual: Sequence[AnyPred]) -> Never:
    """Raise rather than fetch a whole dataflow to filter it here (#515).

    A predicate the data key cannot carry — on a measure, say — runs locally
    after the fetch, which is correct and costs nothing extra when something
    else narrowed that fetch: a key slot, a period. When nothing did, the
    request is `/data/{flow}/all` and the filter is applied to every
    observation the provider holds. That is the §5 rule the structural planner
    applies, and `SDMX_REST_DATA_21`'s bound says the same — any pushed
    constraint is enough.

    An unfiltered query is not refused: asking for everything is a request
    for everything, said out loud.
    """
    dsd = root.dsd
    keys = ", ".join(dsd.key_slot_ids("3.0")) if dsd is not None else "a key dimension"
    columns = ", ".join(
        sorted(
            {root.binding.component_of(p.column.column) or p.column.column for p in residual if isinstance(p, Compare)}
        )
    )
    msg = (
        f"this filter cannot be sent to the provider ({columns or 'a local predicate'}), and nothing else narrows the "
        f"fetch, so it would download all of {root.binding.dataflow.urn.id} to filter it here. Add a filter the "
        f"data key carries — {keys} — or read the flow unfiltered with .frame() / .stream() and filter that."
    )
    raise PushdownError(msg)


def _to_references(reference_type: type) -> str:
    """Map a model type to its SDMX REST ``references=`` parameter string."""
    sdmx_class: str = getattr(reference_type, "sdmx_class", reference_type.__name__)
    return sdmx_class.lower()


#: What `RestRegistry.get` follows: nothing, every reference, the artefacts
#: referring to it as well (``"all"``), only those (``"parents"``), or the
#: references to these types.
type Resolve = bool | Literal["all", "parents"] | Sequence[type]


def _references_for(*, resolve: Resolve) -> str:
    """The `references=` a resolution depth asks for.

    Lifted out of `_read` when the freshness paths pushed it past the
    complexity gate — and it reads better alone, being the one place the
    `resolve` argument becomes a wire parameter. The two words come before
    the sequence case, which a `str` would otherwise satisfy.
    """
    if resolve is True:
        return "descendants"
    if resolve is False:
        return "none"
    if isinstance(resolve, str):
        return resolve
    return ",".join(sorted(_to_references(t) for t in resolve))


def _check_suffix(destination: Path, fmt: DataFormat) -> None:
    """Refuse a destination whose extension contradicts the format.

    Not inference — the format has already been chosen. This catches the
    disagreement, because a file named ``.xml`` holding CSV bytes misleads
    every later reader of it, including the one that opens it a year from now.
    An extension we do not recognise is left alone: ``.dat``, ``.gz`` and no
    extension at all are legitimate and say nothing about the contents.
    """
    suffix = destination.suffix.lower()
    if suffix in {"", ".csv", ".json", ".xml"} and suffix not in {"", fmt.suffix}:
        msg = (
            f"Destination {destination.name!r} ends in {suffix!r} but "
            f"{fmt.name} writes {fmt.suffix!r}. The format is not inferred "
            f"from the path — rename the file or pass the matching format."
        )
        raise ValueError(msg)


class RestRegistry:
    """Connection to an SDMX REST endpoint.

    Use as a context manager. All artefact access happens within the scope.

    Args:
        endpoint: Base URL or `ProviderInfo`.
                  Pass a ``ProviderInfo`` from `Provider`
                  to have the API version set automatically.
        api: SDMX REST API version — ``"2.1"`` (default) or ``"3.0"``.
             Ignored when *endpoint* is a ``ProviderInfo``.
        id: Stable identifier for this registry, used as the
            ``source_registry`` provenance tag when materialising through
            to a local store. Picked up automatically from
            ``ProviderInfo.id`` when *endpoint* is a built-in
            ``Provider.X``; required when materialising and constructing
            from a raw URL.
        store: Optional `LocalRegistry`. When
            set, every successfully fetched artefact is written through to
            the local store with ``source_registry`` set to ``id``. Pinned
            URNs are then served from local DuckDB on subsequent calls.
        fetch_cache_size: How many distinct questions to remember having
            fetched (default 256). Bounds the fetch cache; eviction only
            means a later identical query fetches again.
        latest_ttl: Seconds a `latest` answer may be reused without asking,
            *at minimum*. A floor over what the provider's
            `Cache-Control` allows, never a ceiling: it can rescue a provider
            whose `no-store` is appliance boilerplate, and cannot shorten a
            generous one. Defaults to 0 — obey the provider exactly. Wanting
            *less* is spelled with a pinned URN, which is not a pointer.
        csv_media_type: The ``Accept`` `obs` fetches observations with.
            Defaults to the `ProviderInfo`'s, else the SDMX-CSV the
            API version defines: 1.0 for 2.1, 2.0 for 3.0. Providers differ —
            ECB serves CSV only for ``text/csv``, Eurostat only for SDMX-CSV
            1.0 — so pass one when a bare URL's provider refuses the default.

    **Thread safety: concurrent reads are safe and do not duplicate work.**

    Stated because it was previously unstated, which is not the same as
    absent — the intended shape of this API is a server (§4.1 of
    `docs/design/registry-and-catalog.md` is a FastAPI facade), and an
    unstated contract in that setting is one nobody can rely on and nobody
    can violate. The commitment matches the local `LocalRegistry`'s, and the
    reasons are in `docs/design/concurrency.md`:

    - The query store is built once under a double-checked lock. Two threads
      previously built two `:memory:` stores, one of which was leaked still
      open with a thread's fetched artefacts inside it — so a query could
      read an empty store and correctly report nothing.
    - Two threads asking the same question fetch **once**: the second waits
      on the first rather than repeating the HTTP round-trip
      (`_singleflight.SingleFlight`).
    - Two threads asking one dataflow different observation questions take
      turns, because the table holds one question's rows at a time.

    Mutating calls — `load`, and anything that writes to a `store`
    store — inherit the local `LocalRegistry`'s rule and remain single-threaded.

    Example:
        # Cache-mode federation: structural reads cached locally. A cache can
        # be fetched again, so it may be rebuilt when sdmxlib's store schema
        # moves; any other store on an old schema is refused instead.
        with (
            sl.LocalRegistry("cache.duckdb", rebuild=True) as local,
            sl.RestRegistry(sl.Provider.ECB, store=local) as reg,
        ):
            dsd = reg.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True)

        # Single-source ad-hoc.
        with sl.RestRegistry(sl.Provider.ECB) as reg:
            for flow_id in reg.query(sl.q.dataflow).filter(
                sl.q.dataflow.agency == "ECB"
            ).ids():
                print(flow_id)
    """

    def __init__(
        self,
        endpoint: str | ProviderInfo,
        *,
        api: str = "2.1",
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        id: str | None = None,
        store: LocalRegistry | None = None,
        fetch_cache_size: int = 256,
        latest_ttl: int = 0,
        csv_media_type: str | None = None,
        **httpx_kwargs: Unpack[HttpxOptions],
    ) -> None:
        if isinstance(endpoint, ProviderInfo):
            url = endpoint.url
            api = endpoint.api
            if id is None:
                id = endpoint.id
            if csv_media_type is None:
                csv_media_type = endpoint.csv_media_type
        else:
            url = endpoint

        if store is not None and id is None:
            msg = (
                "RestRegistry requires id when store is set — "
                "the local store needs a source-registry tag for provenance. "
                "Pass a Provider.X (auto-fills id) or supply id=... explicitly."
            )
            raise ValueError(msg)

        self.id = id
        self._store = store
        #: `latest` URN -> the pinned URN it last resolved to (#339). Read only
        #: after the provider confirms the pointer has not moved; bounded FIFO,
        #: and an eviction costs a fetch rather than a wrong answer.
        self._settled: dict[str, SdmxUrn[object]] = {}
        self._settled_size = 256
        #: Guards `_settled`'s writes: two threads evicting at capacity pick
        #: the same oldest key, and the second `del` raises `KeyError`.
        self._settled_lock = threading.Lock()
        #: What the provider served and the reader refused (#419), by URN —
        #: so `get` of one says why rather than that it does not exist.
        self._refused: dict[str, Refusal] = {}
        #: Store this registry created for `query` when no `store`
        #: was supplied. Owned here, so closed here.
        self._query_store_owned: LocalRegistry | None = None
        #: Guards the lazy construction of `_query_store_owned`. Without it two
        #: threads build two `:memory:` stores, one of which is leaked still
        #: open with a thread's fetched artefacts inside it.
        self._store_lock = threading.Lock()
        #: Plans already fetched for, keyed by `canonical_key` with the
        #: `detail=` and `references=` the fetch asked for (`_fetch_for`).
        #: Bounded and single-flight — see `_singleflight.SingleFlight`.
        self._fetched = SingleFlight(fetch_cache_size)
        #: Table -> the question whose rows it currently holds. See
        #: `_fetch_data_for` for why one table serves one question at a time,
        #: and why this one cannot be a `SingleFlight`.
        self._fetched_data: dict[str, tuple[object, ...]] = {}
        #: Guards `_fetched_data` and the ingest it gates. Held *across* the
        #: fetch, so two threads asking one dataflow different questions take
        #: turns rather than interleaving two CSVs into one table.
        self._data_lock = threading.Lock()
        self._client = SdmxClient(
            url,
            api=api,
            timeout=timeout,
            headers=headers,
            latest_ttl=latest_ttl,
            csv_media_type=csv_media_type,
            **httpx_kwargs,
        )
        self._session = Session()
        self._registry = InMemoryRegistry()

    @property
    def registry(self) -> InMemoryRegistry:
        """The `InMemoryRegistry` populated by every artefact fetched so far.

        Artefacts from all ``get()``, ``query()``, ``fetch()``, and
        ``resolve()`` calls accumulate here, every Ref bound to it. Cross-request
        Ref resolution happens automatically — a codelist fetched in one
        request resolves dimension Refs in a DSD fetched in another.

        Example:
            with sl.RestRegistry(...) as rest:
                dsd = rest.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True)
                codelists = rest.registry.get_all(sl.Codelist)
        """
        return self._registry

    def load(self, source: str | Path | bytes) -> StructureMessage:
        """Load artefacts from a local SDMX file into this registry.

        Useful for pre-populating the registry with local files before making
        network requests, or for mixing local and remote artefacts. All loaded
        artefacts are treated as fully resolved — no follow-up HTTP requests
        are made.

        References are bound to this registry: artefacts loaded from file resolve Refs in
        artefacts already present (from earlier ``load()`` or HTTP calls), and
        vice-versa.

        Args:
            source: File path (``str`` or ``Path``) or raw bytes.

        Returns:
            The parsed `StructureMessage`.

        Example:
            with sl.RestRegistry("https://...") as reg:
                reg.load("local_constraints.xml")  # pre-populate
                dsd = reg.get(sl.DataStructure.urn_for(agency="ESTAT", id="CPI"), resolve=True)
        """
        data = Path(source).read_bytes() if isinstance(source, (str, Path)) else source
        msg = _parse(data, registry=self._registry)
        if not isinstance(msg, StructureMessage):
            err = (
                f"RestRegistry.load() accepts Structure messages only; got {type(msg).__name__}. "
                "Use sl.parse() directly for MetadataMessage payloads."
            )
            raise TypeError(err)
        # parse() already populated self._registry; absorb handles
        # Session ingest + materialise-through.
        # FULL, because a file is the caller's own assertion of what they
        # have. Marking it PARTIAL would send `partial=False` to the network
        # after an explicit `load`, ignoring the document they just handed us.
        self._session.ingest(self._stored(msg), completeness=Completeness.FULL)
        if self._store is not None:
            for artefact in msg.all_artefacts():
                self._store.add(artefact, source_registry=self.id)
        return msg

    @property
    def refused(self) -> tuple[Refusal, ...]:
        """Artefacts a provider served that this registry refused, each with why.

        A registry's document is read artefact by artefact: one that
        contradicts the schema, or breaks a rule `add` enforces, is left out
        and the rest are read, so one bad DSD does not cost a bulk fetch every
        other. `get` of a refused artefact raises with its reason.
        """
        return tuple(self._refused.values())

    def close(self) -> None:
        """Close the HTTP client, and the query store if this registry made one.

        A `store` passed in belongs to the caller and is left open —
        closing someone else's store on the way out is the kind of tidiness
        that loses data.
        """
        self._client.close()
        if self._query_store_owned is not None:
            self._query_store_owned.close()
            self._query_store_owned = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def __contains__(self, target: object, /) -> bool:
        """True if this artefact is already cached locally — no HTTP fetch.

        Checks the in-process Session and the ``store`` local store
        (when configured). Returns ``False`` for URNs that would require a
        network round-trip; this matches the `RegistryReader` Protocol's
        semantics for membership.

        Takes an `SdmxUrn` or a `Ref`, matching `get` and the other two
        registries (#370). Anything else is ``False`` rather than an error.
        """
        if not isinstance(target, (SdmxUrn, Ref)):
            return False
        urn = target_urn(target)
        if self._session.get(urn) is not None:
            return True
        if self._store is not None and urn.version != "latest":
            return urn in self._store
        return False

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _absorb(self, msg: StructureMessage, *, completeness: Completeness) -> None:
        """Ingest a parsed message into Session + inner LocalRegistry, and
        materialise through to the local store if configured.

        ``source_registry=self.id`` tags every artefact in the local store
        so federation can distinguish remote-cached from locally-authored.
        What the reader refused is remembered in `refused`, and the rest is
        ingested (#419).
        """  # noqa: D205
        for refusal in msg.refused:
            self._refused[refusal.urn] = refusal
        self._registry.add_all(msg.all_artefacts())
        self._session.ingest(self._stored(msg), completeness=completeness)
        if self._store is not None:
            for artefact in msg.all_artefacts():
                self._store.add(artefact, source_registry=self.id)

    def _held[T](self, parsed: T | None) -> T | None:
        """The inner registry's copy of *parsed*, which a later fetch's artefacts resolve against (#492).

        The parsed one's references are bound to the document it came in, so
        a codelist a fuller fetch brings later would never reach it.
        """
        if not isinstance(parsed, SupportsUrn):
            return parsed
        held = self._registry.get(artefact_urn(parsed), expect=type(parsed))
        return parsed if held is None else held

    def _binding(self) -> AbstractContextManager[None]:
        """Bind what a fetch parses to the inner registry, so `_absorb`'s `add_all` copies nothing (#492)."""
        return binding(self._registry._resolver)

    def _stored(self, msg: StructureMessage) -> list[MaintainableArtefact]:
        """The inner registry's copy of each artefact in *msg*: the ones whose references it binds (#492)."""
        return [
            found
            for artefact in msg.all_artefacts()
            if (found := self._registry.get(artefact.urn, expect=MaintainableArtefact)) is not None
        ]

    def _raise_if_refused(self, urn: SdmxUrn[object]) -> None:
        """Raise naming why, if the artefact `urn` names was served and refused.

        A `latest` URN names no version, so it matches a refusal of any
        version of the same artefact. It reads a snapshot, since `_absorb` on
        another thread may be adding a refusal.

        Raises:
            SdmxConformanceError: If `urn` names a refused artefact.
        """
        for refusal in tuple(self._refused.values()):
            refused = SdmxUrn.parse(refusal.urn)
            same = (refused.artefact_class, refused.agency, refused.id) == (urn.artefact_class, urn.agency, urn.id)
            if same and urn.version in {"latest", refused.version}:
                detail = f"{refusal.urn} was served and refused: {refusal.reason}"
                raise SdmxConformanceError(refusal.artefact_class, detail, refused=(refusal,))

    def _local_cache_lookup[T](self, urn: SdmxUrn[T]) -> T | None:
        """Read-before-fetch cache lookup against ``store``.

        Returns the cached artefact if present in the local store; ``None``
        otherwise (no store, ``(latest)`` URN — must always
        re-fetch — or absent). On hit, the artefact is interned into the
        in-process Session and LocalRegistry so subsequent calls in this process
        hit in-memory.
        """
        if self._store is None or urn.version == "latest":
            return None
        cached = self._store.get(urn)
        if cached is None:
            return None
        # Warm the in-process state. Some artefacts (e.g. LazyCodelist) don't
        # satisfy SupportsUrn cleanly; skip in-memory interning for those while
        # keeping Session caching.
        if isinstance(cached, SupportsUrn):
            self._registry.add(cached)
            # The stored copy, whose references the inner registry binds (#492).
            cached = self._registry.get(urn) or cached
        self._session._map[str(urn)] = cached
        return cached

    def _fetch_one[T](
        self,
        artefact_type: type[T],
        agency: str,
        id: str,
        version: str = "latest",
        *,
        detail: str,
        references: str,
    ) -> T:
        """Fetch a single artefact with explicit wire parameters."""
        resource = getattr(artefact_type, "sdmx_class", artefact_type.__name__)
        package = getattr(artefact_type, "sdmx_package", resource.lower())
        urn = SdmxUrn.make(
            artefact_type,
            package=package,
            artefact_class=resource,
            agency=agency,
            id=id,
            version=version,
        )
        learns = completeness_of(detail=detail, references=references)
        held = self._already_held(urn, completeness=learns, references=references)
        if held is not None:
            return held
        with self._binding():
            fetched = self._client.get_artefact_if_changed(
                resource, agency, id, version, detail=detail, references=references
            )
        if fetched is NOT_MODIFIED:
            confirmed = self._confirmed(urn, completeness=learns)
            if confirmed is not None:
                return confirmed
            return self._fetch_one(artefact_type, agency, id, version, detail=detail, references=references)
        if not isinstance(fetched, StructureMessage):  # pragma: no cover - the client returns one or the sentinel
            err = f"expected a StructureMessage for {urn}, got {type(fetched).__name__}"
            raise TypeError(err)
        self._absorb(fetched, completeness=learns)
        result = self._held(self._session.extract(fetched, urn))
        if result is None:
            self._raise_if_refused(urn)
            err = f"{resource} {agency}:{id}({version}) not found in response"
            raise KeyError(err)
        if version == "latest" and isinstance(result, SupportsUrn):
            self._remember_pointer(str(urn), artefact_urn(result))
        return result

    def _already_held[T](self, urn: SdmxUrn[T], *, completeness: Completeness, references: str) -> T | None:
        """What this process can answer without asking anyone.

        The in-process session first, then the `store=` position for pinned
        URNs only — `latest` is a pointer and never served from either without
        the authority confirming it (#338).

        Lifted out of `_fetch_one` when the freshness paths pushed it past the
        complexity gate. Reads better split anyway: this is "what do we have",
        and what follows is "what did the provider say".
        """
        if references != "none":
            return self._session.get_at_least(urn, completeness)
        cached = self._session.get_at_least(urn, completeness)
        if cached is not None:
            return cached
        return self._local_cache_lookup(urn)

    def _confirmed[T](self, urn: SdmxUrn[T], *, completeness: Completeness, parents: bool = False) -> T | None:
        """What a confirmed-unmoved pointer resolves to, or `None` to ask again.

        `None` means the provider vouched for a pointer whose artefact this
        process no longer holds — evicted from the session under us. The
        freshness that vouched for it is dropped on the way out, so the retry
        goes to the provider instead of looping on the same confirmation.
        """
        held = self._settled_answer(urn, completeness=completeness, parents=parents)
        if held is None:
            self._forget(str(urn))
        return held

    def _settled_answer[T](self, urn: SdmxUrn[T], *, completeness: Completeness, parents: bool = False) -> T | None:
        """What a confirmed-unmoved `latest` pointer resolves to, if still held.

        This is a `latest -> pinned` map, which #338 deleted. The difference is
        that this one is read **only after the authority has confirmed the
        pointer** — by a 304, or by a window it granted — where the deleted one
        answered from cache and never asked. Same shape, opposite guarantee.
        """
        settled = self._settled.get(str(urn))
        if settled is None:
            return None
        # Keyed by the pinned URN this `latest` resolved to, so whatever is
        # held there is a `T`. The session's map is `dict[str, object]` and
        # cannot say so — the documented "localize the unsafe object -> T step
        # to the accessor" case from CLAUDE.md, written as a cast rather than a
        # suppression so it belongs to no single checker.
        held = self._session.get_at_least(settled, completeness, parents=parents)
        return cast("T | None", held)

    def _remember_pointer(self, latest: str, pinned: SdmxUrn[object]) -> None:
        """Record `latest → pinned`, bounded FIFO.

        Only consulted after the authority confirms the pointer has not moved,
        so a stale entry cannot be served — which is what separates this from
        the `_latest` index #338 removed.
        """
        with self._settled_lock:
            self._settled[latest] = pinned
            while len(self._settled) > self._settled_size:
                del self._settled[next(iter(self._settled))]

    def _forget(self, latest: str) -> None:
        """Drop a pointer and the freshness that vouched for it."""
        with self._settled_lock:
            self._settled.pop(latest, None)
        self._client.forget_freshness()

    def _fetch_all_with[T](
        self,
        artefact_type: type[T],
        agency: str | None,
        *,
        detail: str,
        references: str,
    ) -> list[T]:
        """Fetch all artefacts of a type with explicit wire parameters."""
        resource = getattr(artefact_type, "sdmx_class", artefact_type.__name__)
        with self._binding():
            msg = self._client.get_all(resource, agency, detail=detail, references=references)
        self._absorb(msg, completeness=completeness_of(detail=detail, references=references))
        return self._session.all_of_class(artefact_type)

    # ── Generic entry points ──────────────────────────────────────────────────

    def query[R: "RowKind", A](self, root: Cols[R, A]) -> Query[R, A]:
        """A lazy query over the relation `root` names, served over HTTP.

        The same entry point and the same language as `LocalRegistry.query`. What
        differs is where the rows come from: this plans the query against what
        SDMX REST can answer, fetches exactly what it needs, materialises it,
        and then runs the query locally.

            reg.query(sl.q.dataflow).filter(
                sl.q.dataflow.agency == "ECB",
                sl.q.dataflow.dsd.agency == "ECB",
                sl.q.dataflow.dsd.dimensions.any(sl.q.component.concept.id == "REF_AREA"),
            ).ids()

        **A scan that leaves anything to filter locally needs an agency**, and
        that is the one thing this surface asks for that the local one does
        not. The URL is `/structure/{resource}/{agency}/{id}/{version}`, so
        what REST can answer is a listing narrowed by those three; a predicate
        it cannot evaluate runs over whatever came back. Without an agency that
        is every agency's artefacts, and the planner refuses rather than fetch
        a registry to filter three rows from it. A query REST answers whole —
        a bare listing, or equality on `id` or `version` — needs none. A
        dataflow's agency does not imply its DSD's — cross-agency structure
        reuse is normal SDMX — so the example above states both. The refusal
        names the column to add.

        **Where the rows are kept.** `store`, if set, which makes the
        fetch a one-off across the store's whole life; otherwise an in-memory
        store living as long as this registry. Repeating a query costs nothing
        either way — the plan is keyed by meaning, so `filter(a).filter(b)` and
        `filter(b).filter(a)` share a fetch.

        Nothing runs, and nothing is fetched, until a terminal. That is what
        lets the planner see the whole question: `reg.query(sl.q.dataflow)`
        names no agency, and fetching there would fetch everything.

        Args:
            root: A relation accessor from `sl.q`.

        Returns:
            A `Query` over that relation, which fetches before it answers.

        Raises:
            PushdownError: At the terminal, when the query cannot be served
                within the bound. The message names what could not be pushed
                and what would make it admissible.
        """
        return attrs.evolve(self._query_store().query(root), prepare=self._fetch_for)

    def _query_store(self) -> LocalRegistry:
        """Where fetched artefacts are put, and queried from.

        `store` when the caller supplied one — then the fetch is paid
        once for the life of that store rather than once per session, which is
        what §12 of `query-pushdown.md` concludes is nearly always the right
        trade. Otherwise an in-memory store owned by this registry and closed
        with it.
        """
        if self._store is not None:
            return self._store
        # Double-checked: the common case is a plain attribute read, and only
        # the first arrival per registry pays the lock.
        if self._query_store_owned is None:
            from sdmxlib.local.registry import LocalRegistry  # noqa: PLC0415  (cycle)

            with self._store_lock:
                if self._query_store_owned is None:
                    self._query_store_owned = LocalRegistry(":memory:").open()
        return self._query_store_owned

    def _fetch_for[R: "RowKind"](
        self,
        plan: Plan[R],
        projection: frozenset[tuple[str, str]] | None = None,
        projected_paths: frozenset[tuple[str, ...]] = frozenset(),
    ) -> None:
        """Fetch what `plan` needs, once per distinct question.

        Keyed on `canonical_key` rather than on the plan object: two plans that
        *mean* the same thing must share a fetch, and #270 built that key for
        exactly this kind of consumer. Without it, `count()` followed by
        `ids()` on the same query would fetch twice.

        **The projection is part of the key, not only of the request.** It
        decides `detail=allstubs` versus `full` (§5.14), so two calls with the
        same plan and different projections need different fetches. Keyed on
        the *derived* detail rather than the projection itself, because that is
        what actually differs on the wire — two projections that both reduce to
        `allstubs` still share one fetch.

        Without this the failure is silent and the worst shape available:
        `select(id).ids()` fetches stubs, then `collect()` on the same plan
        finds the key present, skips the fetch, and resolves artefacts whose
        items were never retrieved — a codelist with zero codes and no error.

        **So is the depth the projection reaches** (GH-271). A `Select` that
        reads `dataflow.dsd.id` needs `references=children`, and its plan alone
        asks for `none`. Keyed on the derived `references=` value, as `detail=`
        is: keyed on the plan alone, a `collect()` that fetched with `none`
        would satisfy the projection, which would then read null.
        """
        store = self._query_store()
        key = canonical_key(plan)
        detail = _detail_of(plan, projection)
        references = references_for(plan.root.__table__, closure(plan) | projected_paths)
        scope = str(key)
        # `full` subsumes `allstubs`: everything a stub carries is in a full
        # artefact, so a narrow ask after a wide fetch is pure waste. The
        # reverse does not hold, which is what the key is for.
        if detail != "full" and (key, "full", references) in self._fetched:
            return
        self._fetched.run(
            (key, detail, references),
            lambda: self._fetch_and_scope(store, plan, projection, projected_paths, scope),
        )

    def _fetch_and_scope[R: "RowKind"](
        self,
        store: LocalRegistry,
        plan: Plan[R],
        projection: frozenset[tuple[str, str]] | None,
        projected_paths: frozenset[tuple[str, ...]],
        scope: str,
    ) -> None:
        """Fetch, recording which artefacts came back under `scope` (§5.13).

        The membership rows are what let the query answer from its own fetch
        instead of from everything the store holds — see `Query._fetch_scope`
        for why this is a table rather than a timestamp.

        Replaced rather than appended: a re-fetch under the same key is a fresh
        answer to the same question, and leaving the previous one behind would
        let an artefact the provider has since withdrawn keep answering, which
        is the defect this exists to remove.

        **On the store's session, never on `store.connection`.** #300's rule,
        and the first draft of this method broke it — writing scope rows on the
        base connection from a thread while another read observations on it
        made `test_alternating_questions_on_one_dataflow_do_not_interleave`
        flaky, which is the #75 corruption mode arriving by a new route. The
        same mistake `_current_catalog` had made, found the same way.
        """
        with store.session() as session:
            session.execute("DELETE FROM sdmx.fetch_scope WHERE fetch_key = ?", [scope])
            urns: list[str] = []
            fetch_into(
                store, plan, self._client, projection=projection, projected_paths=projected_paths, record=urns.append
            )
            for urn in urns:
                session.execute(
                    "INSERT OR IGNORE INTO sdmx.fetch_scope (fetch_key, urn) VALUES (?, ?)",
                    [scope, urn],
                )

    # ── URN / Ref-based access ────────────────────────────────────────────────

    @overload
    def get[T](
        self,
        target: SdmxUrn[object] | Ref[object],
        /,
        *,
        expect: type[T],
        resolve: Resolve = False,
        partial: bool = True,
    ) -> T | None: ...

    @overload
    def get[T](
        self,
        target: SdmxUrn[T] | Ref[T],
        /,
        *,
        resolve: Resolve = False,
        partial: bool = True,
    ) -> T | None: ...

    def get[T](
        self,
        target: SdmxUrn[object] | Ref[object],
        /,
        *,
        expect: type[T] | None = None,
        resolve: Resolve = False,
        partial: bool = True,
    ) -> object:
        """Read one artefact by identity.

        Read order: in-process Session → local-store cache (when a store is
        set and the URN is pinned) → HTTP. So this makes **zero or one**
        request, not one by definition — the same call against a
        `LocalRegistry` makes none.

        `resolve` is reference *depth*, and it collapses what used to be three
        entry points — `fetch()`, `resolve()`, and the `GetQuery` returned by
        `get(type, agency=…, id=…)`:

            reg.get(urn)                          # skeleton; refs unresolved
            reg.get(urn, resolve=True)            # every reference followed
            reg.get(urn, resolve=[sl.Codelist])   # only refs of these types
            reg.get(urn, resolve=True, partial=False)   # and untrimmed
            reg.get(urn, resolve="all")           # and what refers to it
            reg.get(urn, resolve="parents")       # only what refers to it

        `"all"` and `"parents"` bring the artefacts that refer to this one:
        the constraints on a dataflow, and the hierarchy associations linked
        to it or to its DSD's dimensions. A ``descendants`` fetch never
        returns them, so a bundle's structural closure needs one of these.
        Over SDMX 2.1 no hierarchy association arrives, since 2.1
        has none. `"parents"` asks for the artefact's own fields in full and
        follows none of its references.

        **The default differs from `LocalRegistry.get`, and that is not an
        inconsistency.** Resolving is a join away locally and another round
        trip here, so each states the default its cost implies rather than
        agreeing on a number neither would pick.

        Args:
            target: A URN, or a `Ref` from graph traversal. Positional-only,
                and the same two types every other `get` takes.
            resolve: `False` for a skeleton, `True` for every reference,
                `"all"` for every reference and every artefact referring to
                this one, `"parents"` for only the referring ones, or a
                sequence of artefact types to follow only those.
            partial: When resolving, trim referenced ItemSchemes by their
                DataConstraints. `False` for a full audit.
            expect: The class the caller requires — the artefact is returned
                when it is an instance, `None` otherwise. See below.

        **`expect=` names the class and gets that class or None.** A URN
        parsed from a string carries no type a checker can use, and what one
        checker infers for `get(parse(...))` is not what another does.
        Passing the class states the expectation where the caller holds it:

            reg.get(SdmxUrn.parse(text), expect=MetadataSet)   # MetadataSet | None

        The check is `isinstance`, the same operation the caller would write by
        hand, so a response naming something else answers None rather than a
        mistyped object.

        Returns:
            The artefact, or `None` when no source has it.

        Raises:
            SdmxConformanceError: If the provider served the artefact and it
                was refused — see `refused`.
        """
        urn = target_urn(target)
        try:
            found = self._read(urn, resolve=resolve, partial=partial)
        except KeyError:
            return None
        if expect is not None:
            return found if isinstance(found, expect) else None
        return found

    def _read[T](self, urn: SdmxUrn[T], *, resolve: Resolve, partial: bool) -> T:
        """`get` without the not-found translation, for callers that require one."""
        wants_refs = bool(resolve) and resolve != "parents"
        detail = ("referencepartial" if partial else "full") if wants_refs else "full"
        references = _references_for(resolve=resolve)
        wanted = completeness_of(detail=detail, references=references)
        # The two words are the only `str` values; a type list is unhashable.
        parents = isinstance(resolve, str)
        cached = self._session.get_at_least(urn, wanted, parents=parents)
        if cached is not None:
            return cached
        if not wants_refs:
            from_local = self._local_cache_lookup(urn)
            if from_local is not None:
                return from_local
        with self._binding():
            fetched = self._client.get_artefact_if_changed(
                urn.artefact_class,
                urn.agency,
                urn.id,
                urn.version,
                detail=detail,
                references=references,
            )
        if fetched is NOT_MODIFIED:
            confirmed = self._confirmed(urn, completeness=wanted, parents=parents)
            if confirmed is not None:
                return confirmed
            return self._read(urn, resolve=resolve, partial=partial)
        if not isinstance(fetched, StructureMessage):  # pragma: no cover - one or the sentinel
            err = f"expected a StructureMessage for {urn}, got {type(fetched).__name__}"
            raise TypeError(err)
        self._absorb(fetched, completeness=wanted)
        result = self._held(self._session.extract(fetched, urn))
        if result is None:
            self._raise_if_refused(urn)
            err = f"{urn} not found in response"
            raise KeyError(err)
        if parents and isinstance(result, SupportsUrn):
            self._session.record_parents(artefact_urn(result))
        if urn.version == "latest" and isinstance(result, SupportsUrn):
            self._remember_pointer(str(urn), artefact_urn(result))
        return result

    @overload
    def download(
        self,
        flow: Dataflow | Ref[Dataflow] | SdmxUrn[Dataflow],
        *,
        fmt: DataFormat,
        key: str = "all",
        start_period: str | None = None,
        end_period: str | None = None,
        to: None = None,
    ) -> bytes: ...

    @overload
    def download(
        self,
        flow: Dataflow | Ref[Dataflow] | SdmxUrn[Dataflow],
        *,
        fmt: DataFormat,
        key: str = "all",
        start_period: str | None = None,
        end_period: str | None = None,
        to: Path | str,
    ) -> Path: ...

    def download(
        self,
        flow: Dataflow | Ref[Dataflow] | SdmxUrn[Dataflow],
        *,
        fmt: DataFormat,
        key: str = "all",
        start_period: str | None = None,
        end_period: str | None = None,
        to: Path | str | None = None,
    ) -> bytes | Path:
        """Fetch a dataflow's data in `fmt`, exactly as the provider sent it.

        Byte-faithful passthrough — nothing here parses, validates or reorders
        the body, which is what separates it from `obs`. Use it to
        keep a provider's own file, to feed a tool that wants SDMX-ML, or to
        capture a payload for a bug report.

            reg.download(flow_urn, fmt=sl.DataFormat.SDMX_ML_3_0, to="exr.xml")

        **Identity in, bytes out, and no structural fetch.** `flow` names the
        dataflow and nothing more; unlike `obs` this needs no DSD,
        because there is no key to widen and no columns to interpret. A
        download therefore costs exactly one request.

        **The format is asked for, never inferred.** A path extension is
        decorative on the SDMX-REST side and would be a silent trap here: a
        file named ``.xml`` written with CSV bytes is worse than a refusal. So
        `fmt` is required, `to`'s extension is only *checked* against it, and
        the response's ``Content-Type`` is checked too — a provider that
        ignores ``Accept`` is caught rather than believed.

        Args:
            flow: The dataflow, as an artefact, a `Ref`, or a URN.
            fmt: The format to request, sent as ``Accept``.
            key: SDMX key filter string, e.g. ``"M.USD.EUR.SP00.A"``. Passed
                through as given — this is the provider's key grammar, not a
                `ComponentFilter`.
            start_period: ISO period start, e.g. ``"2020-01"``.
            end_period: ISO period end.
            to: Destination file. When given, the body is streamed there in
                chunks and never held whole — the only form suitable for a
                multi-GB flow.

        Returns:
            The response body when `to` is `None`, otherwise the `Path`
            written.

        Raises:
            ValueError: If `to`'s extension contradicts `fmt`, or if the
                response's ``Content-Type`` is not the format asked for.
        """
        urn = _flow_urn(flow)
        destination = Path(to) if to is not None else None
        if destination is not None:
            _check_suffix(destination, fmt)

        body = self._client.download_data(
            urn.agency,
            urn.id,
            urn.version,
            fmt=fmt,
            key=key,
            start_period=start_period,
            end_period=end_period,
            to=destination,
        )
        if destination is not None:
            return destination
        if body is None:  # pragma: no cover - the client returns bytes without `to`
            msg = "download_data returned no body for an in-memory download"
            raise RuntimeError(msg)
        return body

    def obs(self, flow: Dataflow | Ref[Dataflow] | SdmxUrn[Dataflow]) -> ObsQuery:
        """A lazy query over this dataflow's observations, served over HTTP.

        The same entry point and the same language as `LocalRegistry.obs`.
        What differs is where the rows come from: this plans the query against
        what SDMX REST's `/data` endpoint can answer, fetches exactly that,
        materialises it, and then runs the *original* query locally — the
        structural `query()` design, one plane over.

            obs = reg.obs(flow_urn)
            obs.filter(obs.col("CURRENCY").is_in(["USD", "GBP"])).frame()

        **Identity in, this registry's structure out.** `flow` is used only to
        name the dataflow; the DSD is resolved *here*, so a `Dataflow` resolved
        from another registry cannot make this one interpret its rows with
        foreign structure.

        **The binding is synthesised, not stored.** SDMX-CSV names its columns
        by component id, so `DataflowBinding.from_dsd` describes the fetched
        table exactly — same artefact type as a producer's binding, different
        provenance, and nothing downstream can tell them apart.

        Args:
            flow: The dataflow, as an artefact, a `Ref`, or a URN.

        Returns:
            An `ObsQuery`, lazy until a terminal.

        Raises:
            ValueError: If the dataflow or its structure cannot be resolved —
                without a DSD there is no key order, and a key built on a guess
                queries a different slice silently.
        """
        # Bound to the dataflow as fetched, not as asked for: the data URL names
        # its version, and BIS and OECD refuse `latest` there with a 400 that
        # ECB and Eurostat let pass (GH-51).
        resolved, dsd = self._resolve_data_structure(_flow_urn(flow))
        urn = resolved.urn
        store = DuckDBDataStore.from_registry(self._query_store())
        binding = DataflowBinding.from_dsd(
            dsd,
            dataflow=Ref[Dataflow].unresolved(urn),
            schema=_FETCHED_SCHEMA,
            table=_fetched_table(urn),
            database_role=self._current_catalog(),
            source_registry=self.id or "rest",
        )
        return attrs.evolve(store.obs(binding, dsd=dsd), prepare=self._fetch_data_for)

    def _current_catalog(self) -> str:
        """The query store's own catalog, which is what a binding calls its database role.

        Asked rather than assumed because it differs by how the store was
        opened — `memory` for the in-memory one this registry builds itself, a
        file's stem for a `store`.

        **Run on the store's per-thread handle, not on `.connection`.** That is
        #300's rule, and this line was the last place in `src/` breaking it.
        `DuckDBPyConnection` is not thread-safe: two threads executing on the
        base connection overwrite each other's row buffer (#75), so `fetchone`
        returns `None` and the "DuckDB always answers this" branch below fires.
        It was reachable after all — the concurrency tests hit it on roughly
        one run in three before this changed, which is why the `pragma: no
        cover` marking it unreachable is gone.
        """
        rows = self._query_store().execute("SELECT current_database()")
        if not rows:
            msg = "the query store did not report a current database"
            raise RuntimeError(msg)
        return str(rows[0][0])

    def _resolve_data_structure(self, urn: SdmxUrn[Dataflow]) -> tuple[Dataflow, DataStructure]:
        """The dataflow as fetched, and its structure resolved, or a refusal naming what is missing."""
        flow = self._fetch_one(
            Dataflow, urn.agency, urn.id, urn.version, detail="referencepartial", references="descendants"
        )
        if flow.structure is None:
            msg = f"Dataflow {urn} has no structure reference, so its observations have no key order."
            raise ValueError(msg)
        dsd = self._read(flow.structure.urn, resolve=True, partial=True)
        if not isinstance(dsd, DataStructure):
            msg = f"{flow.structure.urn} did not resolve to a DataStructure."
            raise TypeError(msg)
        return flow, dsd

    def _fetch_data_for[R: "RowKind"](
        self,
        plan: Plan[R],
        _projection: frozenset[tuple[str, str]] | None = None,
        _projected_paths: frozenset[tuple[str, ...]] = frozenset(),
    ) -> None:
        """Fetch the slice this plan needs, and replace the table with it.

        The projection and its paths are accepted and ignored: `detail=` and
        `references=` are *structure* parameters, and a data request has no
        equivalent — SDMX-CSV returns the columns the key selects either way.

        **One table per dataflow, holding one question's rows.** A second,
        different query overwrites it — and evicts the first from `_fetched`,
        so re-running the first re-fetches rather than reading the second's
        rows. Appending instead would be correct for *filtering* (the original
        query re-applies every predicate in SQL, so a superset is harmless) and
        wrong for *counting*: two questions whose pushed requests overlap would
        land the same rows twice.

        The cost is a re-fetch when a caller alternates between two queries on
        one dataflow. The alternative — a table per question — would mean the
        root's table depending on predicates that are not known when the root
        is built.
        """
        root = plan.root
        if not isinstance(root, Observations) or root.dsd is None:
            return
        request, residual = data_request_for(plan.predicates, binding=root.binding, dsd=root.dsd, api=self._client.api)
        if residual and not request.narrows:
            _refuse_unbounded_fetch(root, residual)
        table = f"{root.binding.database_role}.{root.binding.table_location}"
        key = canonical_key(plan)
        if self._fetched_data.get(table) == key:
            return
        # The lock spans the ingest, not just the check. Two threads asking
        # one dataflow *different* questions must take turns: the table holds
        # one question's rows, and interleaved ingests would leave it holding
        # neither. This is why the data plane cannot use `SingleFlight`, which
        # accumulates keys — here a second key *replaces* the first.
        with self._data_lock:
            if self._fetched_data.get(table) == key:
                return
            # Through a file that DuckDB reads itself. The two obvious
            # alternatives both cost more on *both* axes, measured on 2M rows:
            # reading the response body into polars is 0.30 s / 660 MB, scanning
            # to Arrow IPC and back is 0.27 s / 608 MB, and this is 0.21 s /
            # 307 MB. See `DuckDBDataStore.ingest_csv`.
            with tempfile.TemporaryDirectory(prefix="sdmxlib-fetch-") as tmp:
                csv_path = Path(tmp) / "data.csv"
                self._client.stream_data_at(request.path, request.params, to=csv_path)
                schema, _, name = root.binding.table_location.partition(".")
                # A provider's response is read here, in full, so what DuckDB
                # cannot read is reported as the response's fault (GH-247).
                with reading_csv(structure=root.dsd.id):
                    DuckDBDataStore.from_registry(self._query_store()).ingest_csv(
                        schema=schema,
                        table=name,
                        path=csv_path,
                        types=column_types(root.binding, root.dsd),
                        lake_alias=root.binding.database_role,
                    )
            self._fetched_data[table] = key

    def scan_csv(self, source: str | Path | bytes) -> ObsQuery:
        """An SDMX-CSV file's observations, as a query — the structure fetched from this registry.

        Reads the file's ``DATAFLOW`` / ``STRUCTURE_ID`` column to name the
        dataflow, fetches it with its structure, and reads the file with it:
        the same `ObsQuery` `obs(flow)` returns, over the file's rows
        instead of a fetch. A path is read in place on every query, and once
        as it is scanned, to refuse a delete row; bytes are loaded once. See
        `sdmxlib.local._scan`.

        Args:
            source: File path or raw SDMX-CSV bytes.

        Raises:
            ValueError: If the ``DATAFLOW`` column is absent or unparseable.
            SdmxParseError: If the data is not readable SDMX-CSV, lacks a
                column the structure declares, or holds a ``D`` (delete) row
                in its ``ACTION`` column, which says what to remove rather
                than what was observed.
            ValueError: If a value does not fit its component's type, when the
                file is read; for a path, when a query reads the value.

        Example:
            with sl.RestRegistry(sl.Provider.ECB) as reg:
                obs = reg.scan_csv("ecb_exr.csv")
                obs.frame(labels="en")
        """
        from sdmxlib.formats.sdmx_csv.reader import (  # noqa: PLC0415  # lazy: defer CSV import
            _peek_dataflow_id,
            parse_dataflow_id,
        )

        dataflow_str = _peek_dataflow_id(source)
        if dataflow_str is None:
            msg = (
                "DATAFLOW column not found in CSV — cannot auto-resolve structure. "
                "Pass the DataStructure explicitly to slpl.scan_csv()."
            )
            raise ValueError(msg)

        agency, id_, version = parse_dataflow_id(dataflow_str)
        flow = self._fetch_one(
            Dataflow,
            agency,
            id_,
            version,
            detail="referencepartial",
            references="descendants",
        )

        if flow.structure is None:
            msg = f"Dataflow {flow.id!r} has no structure reference"
            raise ValueError(msg)

        structure: DataStructure = flow.structure()
        return scan_csv_into(
            DuckDBDataStore.from_registry(self._query_store()), source, dsd=structure, dataflow=Ref.to(flow)
        )

    def scan_json(self, source: str | Path | bytes) -> ObsQuery:
        """An SDMX-JSON data message's observations, as a query — the structure fetched from this registry.

        Either version, told apart by its envelope. The dataflow its
        ``rel="dataflow"`` link names is fetched with its structure, as
        `scan_csv` does, and the message's rows are loaded once.

        Args:
            source: An SDMX-JSON data message, or its bytes.

        Returns:
            An `ObsQuery` over the message's observations.

        Raises:
            SdmxParseError: If the message is not a readable SDMX-JSON data message.
            ValueError: If it names no dataflow, or holds a value that does not
                fit its component's type.
        """
        frame, urn = read_observations(source)

        def fetch(flow: SdmxUrn[Dataflow]) -> Dataflow:
            return self._fetch_one(
                Dataflow, flow.agency, flow.id, flow.version, detail="referencepartial", references="descendants"
            )

        dsd, flow = resolve_flow(urn, fetch)
        return scan_frame_into(DuckDBDataStore.from_registry(self._query_store()), frame, dsd=dsd, dataflow=flow)

    # ── Graph queries ─────────────────────────────────────────────────────────

    def check_binding_drift(self, binding: DataflowBinding) -> list[DriftIssue]:
        """Inspect ``binding`` for drift against this registry.

        Performs at most one HTTP fetch (the bound Dataflow). Returns a
        list of `DriftIssue` — empty if no drift is detected. Use this
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


# ── FmrRegistry ───────────────────────────────────────────────────────────────


class BearerToken:
    """Bearer token for FMR authentication."""

    def __init__(self, token: str) -> None:
        self.token = token


class FmrRegistry(RestRegistry):
    """A `RestRegistry` configured for Fusion Metadata Registry endpoints.

    Defaults to the SDMX Global Registry.

    Example:
        with sl.FmrRegistry() as reg:
            ...
        with sl.FmrRegistry("https://my-fmr.example.com/sdmxapi/rest") as reg:
            ...
        with sl.FmrRegistry(auth=sl.BearerToken("eyJ...")) as reg:
            ...
        with sl.FmrRegistry(auth=("user", "pass")) as reg:
            ...
    """

    def __init__(
        self,
        endpoint: str = SDMX_GLOBAL_REGISTRY,
        *,
        auth: tuple[str, str] | BearerToken | None = None,
        timeout: float = 30.0,
        id: str | None = None,
        store: LocalRegistry | None = None,
        # Transport options only: this constructor owns `auth` (it also takes a
        # BearerToken, which becomes a header rather than an httpx2 auth object),
        # so the forwarded shape must not carry one.
        **kwargs: Unpack[HttpxTransportOptions],
    ) -> None:
        headers: dict[str, str] = {}
        # Kept as its own shape rather than merged into `kwargs`: a TypedDict is
        # open (a runtime value may carry keys its declared type does not), so
        # copying the narrower shape into the wider one is unsound — pyrefly's
        # `open-unpacking` rejects it, correctly. Two unpackings at the call
        # site cost nothing and stay checked.
        auth_option: HttpxAuthOption = {}
        if isinstance(auth, BearerToken):
            headers["Authorization"] = f"Bearer {auth.token}"
        elif isinstance(auth, tuple):
            auth_option["auth"] = auth
        # The SDMX Global Registry currently serves 2.1 format responses.
        super().__init__(
            endpoint,
            api="2.1",
            timeout=timeout,
            headers=headers,
            id=id,
            store=store,
            **kwargs,
            **auth_option,
        )
