# sdmxlib.api

## Overview

`sdmxlib.api` is the HTTP-facing layer. `SdmxClient` wraps an `httpx2.Client`
to fetch and parse SDMX REST resources. `RestRegistry` is a context-managed
connection to one endpoint: it keeps fetched artefacts in a `Session` identity
map and can write them through to a `LocalRegistry` store. `FederatedRegistry`
routes URN lookups across several sources by maintenance agency. The package
exports `BearerToken`, `FmrRegistry`, `Provider` and `RestRegistry`. Queries go
through `RestRegistry.query` (structures) and `RestRegistry.obs`
(observations).

## Interface

- `RestRegistry` (`registry.py`): connection to an SDMX REST endpoint. Takes a
  URL or a `ProviderInfo`, plus `api`, `timeout`, `headers`, `id`, `store`,
  `fetch_cache_size`, `latest_ttl`, `csv_media_type` and forwarded
  `HttpxOptions`. A `ProviderInfo` supplies its own `api`, and the `api`
  argument is then ignored. `store=` without an `id` raises `ValueError`.
- `RestRegistry.get` (`registry.py`): reads by URN or `Ref`, with `resolve=`,
  `partial=` and `expect=`. Raises `SdmxConformanceError` for an artefact the
  provider served and the reader refused.
- `RestRegistry.refused`, `registry` and `__contains__` (`registry.py`): the
  `Refusal`s of served artefacts the reader left out; the `InMemoryRegistry`
  every fetched artefact is added to; and membership by `SdmxUrn` or `Ref`
  in the session or the store, with no HTTP.
- `RestRegistry.query` (`registry.py`): builds a `Query[R, A]` (the path
  language over REST) against a local query store.
- `RestRegistry.obs` (`registry.py`): an `ObsQuery` over a dataflow's
  observations. Accepts a `Dataflow`, a `Ref` or a URN.
- `RestRegistry.download` (`registry.py`): downloads data in a required `fmt`.
  With `to` it writes the file and returns its `Path`, checking the suffix
  against `fmt`; without `to` it returns the bytes. The response
  `Content-Type` is checked against `fmt` too.
- `RestRegistry.load`, `scan_csv` and `scan_json` (`registry.py`): load a
  structure message, or scan local CSV or JSON data into an `ObsQuery`.
- `RestRegistry.check_binding_drift` and `validate_binding` (`registry.py`):
  compare a `DataflowBinding` with what the registry serves.
- `FmrRegistry` and `BearerToken` (`registry.py`): a `RestRegistry` for FMR.
  `auth=` takes a `BearerToken`, sent as an `Authorization` header, or a
  `(user, pass)` tuple, passed to httpx2. It always uses `api="2.1"` and
  forwards only `HttpxTransportOptions`.
- `FederatedRegistry` (`federated.py`): read-only router built from a
  `routing` map (agency id to `RegistryReader`) and an optional `default`.
  Provides `get`, `source_for`, `__contains__` and binding checks. `get`
  reads each source's `get` signature and passes `resolve`/`partial` only to
  a source that takes them; `partial=False` to a source without `partial`
  raises `TypeError`. `__contains__` takes an `SdmxUrn` only and returns
  `False` for anything else.
- `Provider` and `ProviderInfo` (`providers.py`): the built-in endpoints `ECB`,
  `BIS`, `ESTAT`, `IMF`, `OECD` and `SDMX_GLOBAL`, each with its REST API
  version. Look one up by attribute or by subscript. A `ProviderInfo` has
  `url`, `api`, `id` and an optional `csv_media_type`.
- `RegistryUnavailable` (`policy.py`): a source that could not be reached.
  Carries `source_id`, `cause` and `tried`.
- `Session` (`session.py`): identity map keyed by versioned URN. Provides
  `ingest`, `get`, `get_at_least`, `record_parents`, `completeness_for`,
  `first_of_class`, `all_of_class` and `extract`. `Completeness` and
  `completeness_of` rank a fetch's depth from its `detail` and `references`.
  This is a different `Session` from `sdmxlib/session.py`'s DuckDB session.
- `SdmxClient` (`client.py`): the httpx2 wrapper. Provides `get_artefact`,
  `get_artefact_if_changed`, `get_all`, `get_data_at`, `stream_data_at`,
  `download_data`, `forget_freshness`, `close` and the `api` property (the
  REST API version, which decides a request's key slots). `_freshness.py`
  holds the `Freshness` record.
- `HttpxOptions`, `HttpxTransportOptions` and `HttpxAuthOption` (`client.py`):
  closed TypedDicts (PEP 728). `HttpxOptions` is every httpx2 option
  `RestRegistry` forwards; it combines the transport options and the `auth`
  option. `event_hooks` is left out on purpose. `ResponseTooLargeError` is
  defined here too.

## Invariants

- `Provider` cannot be subclassed: `__init_subclass__` raises `TypeError`.
  `Provider["X"]` for an unknown name raises a `KeyError` that lists the
  providers.
- `FederatedRegistry.get` raises `KeyError` when no route matches the URN's
  agency and no default is set. It re-raises `RegistryUnavailable` as is, and
  wraps any other `httpx2.HTTPError` or `OSError` as
  `RegistryUnavailable(source.id, exc, tried=urn)`. It applies `expect` itself
  and returns `None` when the result is a different type.
- `SdmxClient` turns only `httpx2.TransportError` and `OSError` into "source
  unreachable". `get_artefact` and `get_artefact_if_changed` map a 404 to
  `KeyError`; `get_all` and the data methods raise httpx2's
  `HTTPStatusError` for it. A 413 or 414 raises `ResponseTooLargeError`.
- `Session` completeness never goes down: `ingest` keeps the higher of the old
  and new values. Artefacts whose class has no `sdmx_package` are skipped.
- `SdmxClient` caches freshness metadata (ETag, Last-Modified, max-age) only
  for conditional `latest`-version structure calls, keyed by URL and sorted
  params, in a bounded FIFO behind a lock. Within a fresh window such a call
  returns `NOT_MODIFIED` without a request. It never caches response bodies.
- For API versions starting with `3` or `2.0`, the default `Accept` header is
  the SDMX 3.0 structure XML type; for all others it is `application/xml`.
- Concurrent identical `query()` fetches run once, through `SingleFlight`.
  `get()` does not use it. The observation path serialises its fetch and
  ingest under `_data_lock`, one question per table at a time.
- `RestRegistry._settled`, the bounded `latest -> pinned` map, is written
  under `_settled_lock`, and `_raise_if_refused` reads a snapshot of the
  refusal record, so concurrent `get`s neither collide evicting a pointer
  nor fail iterating a refusal record another fetch is adding to (GH-286).
  The refusal record has no bound; it holds one entry per refused artefact.
- With `store=` set, a pinned `get` with `resolve=False` or `"parents"` is
  served from the store, when the store holds it, before any HTTP. A `latest`
  URN is never served from the store.
- An `obs()` response DuckDB cannot read raises `SdmxParseError`, and a value
  that does not fit its component's type the "does not fit its component's
  type" `ValueError` (`reading_csv` around the ingest,
  `sdmxlib._read_errors`). `scan_csv` and `scan_json` raise the same two.

## Side Effects

- Makes HTTP GETs through the single `httpx2.Client` that `SdmxClient` owns.
  The client carries default headers, a timeout (30 s by default) and the
  forwarded options.
- `get_artefact_if_changed` sends conditional requests: a 304 means unchanged
  and a 404 means not found. `stream_data_at` writes the response to a file,
  and `download_data` writes one only when `to` is given.
- `RestRegistry` is a context manager. `close()` closes the HTTP client, and
  also the query store if the registry created it.
- With `store=` set, every fetched artefact is written to that `LocalRegistry`,
  tagged `source_registry=<id>`.
- The query store is created once, under a double-checked lock.
- No automatic retry. A transport failure surfaces as `RegistryUnavailable`.

## Verification

- Run: `just test` (unit tests) and `just test-integration` (end-to-end
  tests replayed from recorded cassettes, no network).
