"""Download each endpoint's bulk structure documents into a local cache.

The documents are large — IMF's codelists are ~685 MB, Eurostat's take over
two minutes to arrive — and the analysis is iterated far more often than the
registries change. So a fetch writes the raw bytes once, beside a small
`.meta.json` saying what came back, and every later run reads the cache until
`--refresh` asks again.

Requests to one *host* are sequential, with a pause between them. A sweep is
a guest on public infrastructure: OECD and registry.sdmx.org both answered a
fast run of bulk queries with 429s, and both serve their 2.1 and 3.0 APIs from
one host, so per-endpoint parallelism doubled the rate each saw.
"""

from __future__ import annotations

import gzip
import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

import httpx2

from bench.sweep.endpoints import Endpoint

#: Long enough for Eurostat's codelists, which took more than 120 s when
#: probed. A bulk structure query is slow, not hung, and a timeout that cuts it
#: off records a gap that is the sweep's fault rather than the registry's.
_TIMEOUT = httpx2.Timeout(connect=30.0, read=900.0, write=30.0, pool=30.0)

_CHUNK = 1 << 20


class FetchMeta(TypedDict):
    """What one request returned, written beside the body."""

    url: str
    status: int
    content_type: str
    bytes: int
    seconds: float
    fetched_at: str
    server: str
    error: str | None


def raw_path(root: Path, endpoint: Endpoint, resource: str) -> Path:
    """Where `resource`'s body for `endpoint` is cached."""
    return root / "raw" / endpoint.key / f"{resource}.xml"


def meta_path(body: Path) -> Path:
    """The `.meta.json` beside a cached body."""
    return body.with_suffix(".meta.json")


def read_meta(body: Path) -> FetchMeta | None:
    """The cached metadata for `body`, or None if it was never fetched."""
    path = meta_path(body)
    if not path.exists():
        return None
    meta: FetchMeta = json.loads(path.read_text())
    return meta


#: Statuses that say "ask again later" rather than anything about the registry.
#: OECD answers a fast sequence of bulk queries with 429.
_TRANSIENT = frozenset({429, 502, 503, 504})

_ATTEMPTS = 5
_BACKOFF = 30.0
_MAX_WAIT = 300.0


def settled(meta: FetchMeta) -> bool:
    """Whether `meta` is an answer worth caching.

    A 404 or 501 is the registry saying it does not serve that resource, which
    is a finding; so is a 500, which BIS returns, repeatably, for the resources
    it does not implement. A 429, a gateway error or a dropped connection is
    not, and caching one would turn a bad minute into a permanent hole.
    """
    return meta["error"] is None and meta["status"] not in _TRANSIENT


def fetch(client: httpx2.Client, root: Path, endpoint: Endpoint, resource: str, *, refresh: bool) -> FetchMeta:
    """Fetch one resource into the cache, unless a settled answer is already there.

    A refusal is cached too, as metadata with no body: a 404 for `valuelist` is
    a finding about the registry, and asking again every run would only make
    the sweep slower at saying so. A transient failure is retried with backoff,
    honouring `Retry-After`, and is not treated as settled if it persists.
    """
    body = raw_path(root, endpoint, resource)
    if not refresh and (cached := read_meta(body)) is not None and settled(cached):
        return cached
    body.parent.mkdir(parents=True, exist_ok=True)
    meta, wait = _request(client, endpoint, resource, body)
    for attempt in range(1, _ATTEMPTS):
        if settled(meta):
            break
        time.sleep(min(wait if wait is not None else _BACKOFF * 2 ** (attempt - 1), _MAX_WAIT))
        meta, wait = _request(client, endpoint, resource, body)
    meta_path(body).write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def _request(client: httpx2.Client, endpoint: Endpoint, resource: str, body: Path) -> tuple[FetchMeta, float | None]:
    """One GET, streamed to `body`. Returns the metadata and any `Retry-After`."""
    url = endpoint.url(resource)
    start = time.perf_counter()
    status, content_type, server, size, error, retry_after = 0, "", "", 0, None, None
    partial = body.with_suffix(".part")
    try:
        with client.stream("GET", url, headers={"Accept": endpoint.accept}) as response:
            status = response.status_code
            content_type = response.headers.get("content-type", "")
            server = response.headers.get("server", "")
            retry_after = _seconds(response.headers.get("retry-after"))
            with partial.open("wb") as out:
                for chunk in response.iter_bytes(_CHUNK):
                    size += out.write(chunk)
    except httpx2.HTTPError as exc:
        error = f"{type(exc).__name__}: {exc}"
    if status == httpx2.codes.OK and error is None:
        error = _unwrap(partial)
    if status == httpx2.codes.OK and error is None:
        partial.replace(body)
    else:
        partial.unlink(missing_ok=True)
        body.unlink(missing_ok=True)
    meta = FetchMeta(
        url=url,
        status=status,
        content_type=content_type,
        bytes=size,
        seconds=round(time.perf_counter() - start, 3),
        fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
        server=server,
        error=error,
    )
    return meta, retry_after


_GZIP = b"\x1f\x8b"


def _unwrap(partial: Path) -> str | None:
    """Make a 200 body plain XML, or say why it is not an SDMX document.

    Two things Eurostat sent with a 200, both of which would otherwise be
    cached as "the registry's answer" and then crash the analysis:

    - a gzip stream with no `Content-Encoding`, so httpx2 does not decode it;
    - an HTML "Server temporarily unavailable" page. That is a transient
      failure wearing a success status, so it is reported as an error, which
      `settled` treats as worth asking again.
    """
    with partial.open("rb") as head:
        magic = head.read(2)
    if magic == _GZIP:
        plain = partial.with_suffix(".gunzip")
        with gzip.open(partial, "rb") as source, plain.open("wb") as out:
            shutil.copyfileobj(source, out, _CHUNK)
        plain.replace(partial)
    with partial.open("rb") as head:
        start = head.read(512).lstrip(b"\xef\xbb\xbf \t\r\n")
    if not start.startswith(b"<"):
        return f"not XML: {start[:40]!r}"
    if start[:200].lower().startswith((b"<!doctype html", b"<html")):
        return "not SDMX: an HTML page with status 200"
    return None


def _seconds(header: str | None) -> float | None:
    """`Retry-After` as seconds, when it is given as a number (not an HTTP date)."""
    if header is None or not header.strip().isdigit():
        return None
    return float(header)


#: Seconds between two requests to the same host.
_PAUSE = 2.0


def fetch_host(
    root: Path, endpoints: list[Endpoint], *, refresh: bool, resources: tuple[str, ...] | None = None
) -> None:
    """Fetch every resource of each of `endpoints` — all on one host — in turn."""
    with httpx2.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
        for endpoint in endpoints:
            _fetch_endpoint(client, root, endpoint, refresh=refresh, resources=resources)


def _fetch_endpoint(
    client: httpx2.Client, root: Path, endpoint: Endpoint, *, refresh: bool, resources: tuple[str, ...] | None
) -> None:
    for resource in resources or endpoint.resources:
        cached = read_meta(raw_path(root, endpoint, resource))
        if not refresh and cached is not None and settled(cached):
            continue
        meta = fetch(client, root, endpoint, resource, refresh=refresh)
        outcome = meta["error"] or f"{meta['status']} {meta['bytes']:,} B {meta['seconds']:.1f}s"
        print(f"{endpoint.key:>10} {resource:<28} {outcome}", flush=True)
        time.sleep(_PAUSE)
