"""SdmxClient — thin httpx2 wrapper around the SDMX REST API."""

import ssl
import threading
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Self, Unpack

import httpx2
from typing_extensions import TypedDict

from sdmxlib.api._freshness import Freshness, read_freshness
from sdmxlib.api.policy import RegistryUnavailable
from sdmxlib.formats import parse
from sdmxlib.formats.data_format import DataFormat
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.rest_api import ApiVersion

# Mirrors httpx2's private `_types.PrimitiveData`. Spelled out rather than
# imported because `httpx2._types` is not public API.
type _PrimitiveData = str | int | float | bool | None


class _HttpxTransportFields(TypedDict, total=False):
    """`HttpxOptions` minus ``auth`` — see there for the full rationale.

    Split out because `FmrRegistry` exposes its own richer ``auth``
    parameter (it also accepts a ``BearerToken``, which becomes a header
    rather than an httpx2 auth object). A caller-facing ``auth`` keyword and an
    ``Unpack`` shape that also carries ``auth`` are a static conflict, so that
    constructor accepts this narrower set and supplies ``auth`` itself.
    """

    # `params` and `cookies` mirror httpx2's `QueryParamTypes` / `CookieTypes`
    # in full. The tuple/list spellings are exact rather than widened to
    # `Sequence[...]`: httpx2's own aliases name `list` and `tuple` concretely,
    # and a `Sequence` here would not be assignable at the forwarding call.
    params: (
        httpx2.QueryParams
        | Mapping[str, _PrimitiveData | Sequence[_PrimitiveData]]
        | list[tuple[str, _PrimitiveData]]
        | tuple[tuple[str, _PrimitiveData], ...]
        | str
        | bytes
        | None
    )
    cookies: httpx2.Cookies | CookieJar | dict[str, str] | list[tuple[str, str]] | None
    verify: ssl.SSLContext | str | bool
    cert: str | tuple[str, str] | tuple[str, str, str] | None
    trust_env: bool
    http1: bool
    http2: bool
    proxy: httpx2.Proxy | httpx2.URL | str | None
    mounts: Mapping[str, httpx2.BaseTransport | None] | None
    follow_redirects: bool
    limits: httpx2.Limits
    max_redirects: int
    transport: httpx2.BaseTransport | None
    default_encoding: str | Callable[[bytes], str]


class _HttpxAuthField(TypedDict, total=False):
    """Just ``auth``, so it can be unpacked alongside `HttpxTransportOptions`.

    `FmrRegistry` supplies ``auth`` itself and forwards the rest untouched.
    Both halves have to be separate shapes for that to type-check: two ``**``
    unpackings in one call must have provably disjoint key sets, which they do
    not if either side is the combined `HttpxOptions`.
    """

    auth: httpx2.Auth | tuple[str | bytes, str | bytes] | Callable[[httpx2.Request], httpx2.Request] | None


class HttpxTransportOptions(_HttpxTransportFields, total=False, closed=True):
    """`_HttpxTransportFields`, closed (PEP 728).

    Forwarded with ``**``, and only a closed TypedDict is known to hold no
    other key.
    """


class HttpxAuthOption(_HttpxAuthField, total=False, closed=True):
    """`_HttpxAuthField`, closed (PEP 728), as `HttpxTransportOptions` is."""


class HttpxOptions(_HttpxTransportFields, _HttpxAuthField, total=False, closed=True):
    """The `httpx2.Client` options sdmxlib forwards, as a PEP 692 kwargs shape.

    This is the *supported surface*, not a mirror of httpx2: `headers`,
    `timeout` and `base_url` are deliberately absent because `SdmxClient` owns
    them — passing one used to be a runtime ``TypeError`` from a duplicate
    keyword, and is now a type error at the call site.

    Every option that *is* here carries httpx2's own type in full. Narrowing one
    below httpx2 would reject a call httpx2 would have accepted, and there is no
    SDMX-level reason to: none of these values is read by sdmxlib, and httpx2
    normalises them at construction (``params`` becomes a `httpx2.QueryParams`
    before any request is built), so all accepted spellings behave identically.
    The only legitimate narrowing is dropping an option entirely, as above.

    Used as ``**httpx_kwargs: Unpack[HttpxOptions]`` so the pass-through stays
    fully checked end to end. The alternative shapes both cost something the
    library should not pay: ``**kwargs: object`` is *provably* wrong at the
    httpx2 boundary (every one of its parameters rejects `object`, which is why
    a single blanket suppression was hiding 17 errors), and ``**kwargs: Any``
    is honest but silently accepts typos and wrong types at a public API.

    ``event_hooks`` is omitted on purpose: httpx2 types its hooks as
    ``Callable[..., Any]``, which would put `Any` back into this signature for
    a rarely-used option. Add it — with a narrower callable type — if a real
    caller needs it.

    The two bases split ``auth`` from everything else; see `HttpxAuthOption`.
    """


# SDMX REST resource path segments (lowercase endpoint name)
_RESOURCE_PATH: dict[str, str] = {
    "DataStructure": "datastructure",
    "Codelist": "codelist",
    "Dataflow": "dataflow",
    "ConceptScheme": "conceptscheme",
    "ProvisionAgreement": "provisionagreement",
    "CategoryScheme": "categoryscheme",
    "Categorisation": "categorisation",
    "DataConstraint": "dataconstraint",
    "AgencyScheme": "agencyscheme",
    "DataProviderScheme": "dataproviderorganisationscheme",
    "Hierarchy": "hierarchy",
    "StructureSet": "structureset",
}

#: Response codes the SDMX-REST standard defines for a request the service
#: will not serve *because of its size*. The standard's own answer to "is this
#: query too big": the server decides, and says so in a defined way.
#:
#: Worth translating rather than passing through as a bare `HTTPStatusError`,
#: because the remedy is specific and nothing else in the stack knows it — a
#: client-side bound can only guess at a provider's limits, while this *is*
#: the provider answering.
_TOO_LARGE = {
    413: "the request is larger than the service will serve",
    414: "the URL is longer than the service accepts",
}


class ResponseTooLargeError(Exception):
    """A provider refused a request as too large (SDMX-REST 413 / 414).

    Distinct from `PushdownError`, which is *our* refusal to plan a fetch that
    would be evaluated locally over everything. This one is the service's, it
    arrives after the request was sent, and the fix is the same either way:
    narrow the query.
    """


def _version_parts(version: str) -> tuple[int, ...]:
    """A dotted version as ints, trailing zeros dropped.

    So ``"2.1"`` and ``"2.1.0"`` compare equal. They have to: SDMX-ML 2.1's
    media types are registered with ``version=2.1`` while every other data
    media type carries three parts, and a provider echoing back the padded
    form is agreeing with us, not disagreeing.
    """
    parts = []
    for piece in version.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            return ()
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _check_content_type(response: httpx2.Response, fmt: DataFormat, *, url: str) -> None:
    """Refuse a response that is not the format that was asked for.

    Without this the format argument is decorative — which is precisely the
    defect it was added to fix. A provider that ignores `Accept` and serves
    SDMX-CSV 1.0.0 where 2.0.0 was requested sends a body with different
    columns, and nothing downstream would notice.

    Parameters other than ``version`` (``charset``, ``labels``) are ignored:
    they qualify the encoding, not the format. A response with no
    ``Content-Type`` at all passes — there is nothing to disagree with, and
    inventing a disagreement would refuse a body that may well be correct.
    """
    header = response.headers.get("content-type")
    if not header:
        return

    got_type, _, got_params = header.partition(";")
    want_type, _, want_params = fmt.media_type.partition(";")
    got_version = ""
    for param in got_params.split(";"):
        name, _, value = param.strip().partition("=")
        if name.lower() == "version":
            got_version = value.strip("\"' ")

    same_type = got_type.strip().lower() == want_type.lower()
    want_version = want_params.partition("=")[2]
    same_version = not got_version or _version_parts(got_version) == _version_parts(want_version)
    if same_type and same_version:
        return

    msg = (
        f"Asked for {fmt.media_type!r} but the response says {header!r}. The "
        f"provider ignored `Accept`, so the body is not the format requested — "
        f"pass the format it does serve, or use `obs()`, which parses "
        f"whatever arrives. URL: {url}"
    )
    raise ValueError(msg)


def _require_csv(response: httpx2.Response, *, accept: str, url: str) -> None:
    """Refuse an observation fetch's response that is not CSV (GH-50).

    The data plane hands the body to DuckDB's CSV reader, which reads an
    SDMX-ML document as a CSV of one malformed column. BIS answers
    ``text/csv`` with SDMX-ML and a 200, and ECB and ESTAT answer an
    ``Accept`` listing two types the same way. Either SDMX-CSV version, or
    plain ``text/csv``, is fine: the reader takes both envelopes. As in
    `_check_content_type`, a response with no ``Content-Type`` passes.

    Raises:
        ValueError: If the response says it is something other than CSV.
    """
    header = response.headers.get("content-type")
    if not header:
        return
    media_type = header.partition(";")[0].strip().lower()
    if media_type == "text/csv" or media_type.endswith("+csv"):
        return
    msg = (
        f"Asked for {accept!r} but the response is {header!r}, not CSV, so it was not read. "
        "Pass the media type this provider serves CSV under as `csv_media_type=` "
        f"(see `sl.Provider` for the ones checked). URL: {url}"
    )
    raise ValueError(msg)


def _raise_for_status(response: httpx2.Response, *, url: str) -> None:
    """`raise_for_status`, with the two size refusals named.

    Everything else keeps httpx2's own error — there is nothing this layer can
    add to a 500 that the message does not already say.
    """
    reason = _TOO_LARGE.get(response.status_code)
    if reason is not None:
        msg = (
            f"{response.status_code}: {reason}. Narrow the query — fewer codes per "
            f"dimension, a shorter period, or a smaller `references=` — and try again. "
            f"URL: {url}"
        )
        raise ResponseTooLargeError(msg)
    response.raise_for_status()


def _now() -> datetime:
    """Now, as naive UTC.

    `sdmxlib._time`'s convention, so a stored instant means the same thing to
    every reader regardless of their machine's zone.
    """
    return datetime.now(UTC).replace(tzinfo=None)


#: Returned instead of a message when the provider confirmed nothing changed —
#: either a 304, or a stored window that has not expired so no request was made
#: at all. The caller reuses what it already has (#339).
#:
#: A sentinel rather than `None`, which `get_artefact` cannot use: `None` there
#: would be indistinguishable from a 404, and §5.8 spent a whole change making
#: "not here" mean exactly one thing.
NOT_MODIFIED = object()


class SdmxClient:
    """Thin httpx2 wrapper that fetches SDMX REST resources and parses them.

    Handles URL construction, Accept headers, and response parsing.

    **Caches freshness, never artefacts.** Artefacts are the Session's
    business, interned by pinned URN. What lives here is what one response said
    about *re-asking* it — `ETag`, `Last-Modified`, `max-age` — which is HTTP's
    question and belongs at the HTTP layer (#339). Bodies are deliberately not
    stored: the artefacts are already held one layer up, and the only thing
    this needs to answer is whether the pointer moved.

    Args:
        endpoint: Base URL of the SDMX REST endpoint.
        api: SDMX REST API version — ``"2.1"`` (default) or ``"3.0"``.
             Controls URL scheme and response format.
        timeout: HTTP request timeout in seconds.
        headers: Extra HTTP headers merged with defaults.
        latest_ttl: Seconds a `latest` answer may be reused without asking,
            *at minimum*. A floor over what the provider allows, never a
            ceiling: it can rescue a `no-store` provider whose header is
            boilerplate, and cannot shorten a generous one. Defaults to 0 —
            obey the provider exactly.
        freshness_size: How many URLs to remember freshness for. Bounded,
            per `docs/design/caching.md`; eviction only costs a request.
        csv_media_type: The ``Accept`` an observation fetch sends. Defaults to
            the SDMX-CSV the API version defines: 1.0 for 2.1, 2.0 for 3.0.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        api: str = "2.1",
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        latest_ttl: int = 0,
        freshness_size: int = 256,
        csv_media_type: str | None = None,
        **httpx_kwargs: Unpack[HttpxOptions],
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._latest_ttl = latest_ttl
        #: url -> what its last response said about re-asking it. Insertion
        #: ordered, evicted FIFO at `freshness_size`; a dropped entry costs one
        #: request, never a wrong answer.
        self._freshness: dict[str, Freshness] = {}
        self._freshness_size = freshness_size
        self._freshness_lock = threading.Lock()
        self._api = api
        self._v3 = api.startswith(("3", "2.0"))
        #: What an observation fetch asks for (GH-50): the provider's, else
        #: the SDMX-CSV the API version defines.
        self._csv_media_type = (
            csv_media_type or (DataFormat.SDMX_CSV_2_0_0 if self._v3 else DataFormat.SDMX_CSV_1_0_0).media_type
        )
        default_headers: dict[str, str]
        if self._v3:
            default_headers = {"Accept": "application/vnd.sdmx.structure+xml;version=3.0"}
        else:
            default_headers = {"Accept": "application/xml"}
        if headers:
            default_headers.update(headers)
        self._client = httpx2.Client(
            headers=default_headers,
            timeout=timeout,
            **httpx_kwargs,
        )

    def _remember(self, key: str, headers: Mapping[str, str]) -> None:
        """Record what a response said about re-asking it, bounded FIFO.

        Under the lock because eviction reads and mutates one dict; the read
        path above is a plain `dict.get`, which is GIL-atomic — the same split
        `_FIFOCache` documents and for the same reason.
        """
        with self._freshness_lock:
            self._freshness[key] = read_freshness(headers, now=_now())
            while len(self._freshness) > self._freshness_size:
                del self._freshness[next(iter(self._freshness))]

    def forget_freshness(self) -> None:
        """Drop every stored window, so the next ask goes to the provider.

        Called when a confirmation could not be honoured — the pointer was
        vouched for but what it pointed at is gone. Clearing all of it rather
        than one entry because the caller knows a URN and this is keyed by
        URL, and the cost of over-clearing is a request.
        """
        with self._freshness_lock:
            self._freshness.clear()

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ── URL building ──────────────────────────────────────────────────────────

    def _resource_url(
        self,
        resource: str,
        agency: str,
        id: str,
        version: str,
    ) -> str:
        """Build the resource URL for the given API version."""
        path = _RESOURCE_PATH.get(resource, resource.lower())
        if self._v3:
            # v2.x: /structure/{resource}/{agency}/{id}/{version}
            # "latest" sentinel becomes "~" on the wire
            ver = "~" if version == "latest" else version
            return f"{self._endpoint}/structure/{path}/{agency}/{id}/{ver}"
        # v1.5: /{resource}/{agency}/{id}/{version}
        return f"{self._endpoint}/{path}/{agency}/{id}/{version}"

    def _collection_url(self, resource: str, agency: str | None) -> str:
        """Build the collection URL (all artefacts of a type)."""
        path = _RESOURCE_PATH.get(resource, resource.lower())
        agency_seg = agency or ("*" if self._v3 else "all")
        if self._v3:
            return f"{self._endpoint}/structure/{path}/{agency_seg}"
        return f"{self._endpoint}/{path}/{agency_seg}"

    # ── Parse version ─────────────────────────────────────────────────────────

    @property
    def api(self) -> ApiVersion:
        """Which SDMX-REST generation this client speaks.

        Public because the *key* a caller builds depends on it — the time
        dimension takes a segment on 2.2.2 and does not on 1.5 (#337) — so a
        request builder must be able to ask rather than assume.
        """
        return "3.0" if self._v3 else "2.1"

    @property
    def _parse_version(self) -> str:
        """Format version string passed to the parser."""
        return "3.0" if self._v3 else "2.1"

    # ── HTTP methods ──────────────────────────────────────────────────────────

    def get_artefact(
        self,
        resource: str,
        agency: str,
        id: str,
        version: str = "latest",
        *,
        detail: str = "full",
        references: str = "none",
    ) -> StructureMessage:
        """Fetch a single maintainable artefact, unconditionally.

        **No freshness**, deliberately. This is the method
        `query._execute.StructureSource` declares, and a fetch that answered
        `NOT_MODIFIED` would write no artefacts — leaving #340's scope
        membership empty and the query with nothing to return. Only
        `get`-shaped reads, which have something held to fall back on, opt into
        freshness, via `get_artefact_if_changed`.

        Raises:
            KeyError: On a 404 — see `get_artefact_if_changed`.
            RegistryUnavailable: If the endpoint could not be reached.
        """
        message = self.get_artefact_if_changed(
            resource, agency, id, version, detail=detail, references=references, conditional=False
        )
        if not isinstance(message, StructureMessage):  # pragma: no cover - conditional=False never returns the sentinel
            err = f"unconditional fetch of {resource} {agency}:{id} returned {type(message).__name__}"
            raise TypeError(err)
        return message

    def get_artefact_if_changed(
        self,
        resource: str,
        agency: str,
        id: str,
        version: str = "latest",
        *,
        detail: str = "full",
        references: str = "none",
        conditional: bool = True,
    ) -> StructureMessage | object:
        """Fetch a single maintainable artefact, reusing a confirmed answer.

        Returns `NOT_MODIFIED` when the request was for `latest` and the
        provider confirmed the pointer has not moved — by a 304, or by a
        `max-age` window that has not expired, in which case no request goes
        out at all (#339). The caller reuses what it resolved last time.

        Pinned versions never take that path: they name an immutable artefact,
        so there is no pointer to confirm and the caller has already answered
        from its own session.

        Raises:
            KeyError: On a 404. SDMX-REST assigns that status the meaning
                *"no results found"*, so it is the commonest way a provider
                says "not here" — and §5.8 reserves `None` for exactly that.
                It used to surface as `HTTPStatusError`, which meant
                `RestRegistry.get` could return `None` only when a provider
                answered 200 with an empty message, and every provider that
                follows the spec instead raised.
            RegistryUnavailable: If the endpoint could not be reached.
        """
        url = self._resource_url(resource, agency, id, version)
        params: dict[str, str] = {"detail": detail}
        if references != "none":
            params["references"] = references

        # `latest` is a pointer, and only a pointer has a freshness question
        # (#338): a pinned URN names an immutable artefact, so re-asking it is
        # waste and *not* re-asking it is never wrong. Keyed on the full URL so
        # two details of the same resource do not share an answer.
        cache_key = f"{url}?{sorted(params.items())}" if conditional and version == "latest" else None
        validators: dict[str, str] = {}
        if cache_key is not None:
            known = self._freshness.get(cache_key)
            if known is not None:
                if known.is_fresh(now=_now(), floor=self._latest_ttl):
                    return NOT_MODIFIED
                validators = known.validators()

        with self._reaching(url):
            response = self._client.get(url, params=params, headers=validators or None)
        if response.status_code == httpx2.codes.NOT_FOUND:
            err = f"{resource} {agency}:{id}({version}) not found at {self._endpoint}"
            raise KeyError(err)
        if cache_key is not None:
            self._remember(cache_key, response.headers)
        if response.status_code == httpx2.codes.NOT_MODIFIED:
            # The provider confirmed the pointer has not moved. There is no
            # body to parse — that is the whole saving — so the caller reuses
            # what it resolved last time.
            return NOT_MODIFIED
        _raise_for_status(response, url=url)
        # Not strict: one refused artefact must not cost the rest of a
        # registry's document (#419). The refusals ride on the message.
        msg = parse(response.content, version=self._parse_version, strict=False)
        if not isinstance(msg, StructureMessage):
            err = f"Expected StructureMessage from structure endpoint, got {type(msg).__name__}"
            raise TypeError(err)
        return msg

    def get_all(
        self,
        resource: str,
        agency: str | None = None,
        *,
        detail: str = "full",
        references: str = "none",
    ) -> StructureMessage:
        """Fetch all artefacts of a type, optionally filtered by agency."""
        url = self._collection_url(resource, agency)
        params: dict[str, str] = {"detail": detail}
        if references != "none":
            params["references"] = references
        with self._reaching(url):
            response = self._client.get(url, params=params)
        _raise_for_status(response, url=url)
        # Not strict: one refused artefact must not cost the rest of a
        # registry's document (#419). The refusals ride on the message.
        msg = parse(response.content, version=self._parse_version, strict=False)
        if not isinstance(msg, StructureMessage):
            err = f"Expected StructureMessage from structure endpoint, got {type(msg).__name__}"
            raise TypeError(err)
        return msg

    @contextmanager
    def _reaching(self, url: str) -> Generator[None]:
        """Name the endpoint and the request when the transport fails (§5.8).

        Without this a dead provider surfaces as a bare
        ``ConnectError: connection refused``, naming neither the source nor
        what was tried — and in a federation "which one?" is the first
        question a caller has.

        **`TransportError`, not `HTTPError`.** The latter includes
        `HTTPStatusError`, so catching it would relabel a 404 — a perfectly
        good answer meaning "not here" — as an unreachable source, which is
        the very conflation §5.8 exists to remove.
        """
        try:
            yield
        except (httpx2.TransportError, OSError) as exc:
            raise RegistryUnavailable(self._endpoint, exc, tried=url) from exc

    def _data_url(
        self,
        agency: str,
        id: str,
        version: str,
        key: str,
    ) -> str:
        """Build the data URL for the given API version."""
        if self._v3:
            ver = "~" if version == "latest" else version
            return f"{self._endpoint}/data/dataflow/{agency}/{id}/{ver}/{key}"
        return f"{self._endpoint}/data/{agency},{id},{version}/{key}"

    def get_data_at(self, path: str, params: Mapping[str, str]) -> bytes:
        """Fetch SDMX-CSV bytes for an already-built request path and parameters.

        The seam `query._rest.DataRequest` needs: it owns the URL's shape —
        which differs by API version — and this owns the HTTP.

        Args:
            path: Path relative to the service root, as `DataRequest.path`
                gives it.
            params: Query parameters, as `DataRequest.params` gives them.

        Returns:
            The response body, unparsed.
        """
        url = f"{self._endpoint}/{path}"
        with self._reaching(url):
            response = self._client.get(url, params=dict(params), headers={"Accept": self._csv_media_type})
        _raise_for_status(response, url=url)
        _require_csv(response, accept=self._csv_media_type, url=url)
        return response.content

    def stream_data_at(self, path: str, params: Mapping[str, str], *, to: Path) -> None:
        """Stream an already-built data request straight to a file.

        The bounded counterpart to `get_data_at`, and the one a query should
        use: `get_data_at` returns `bytes`, and handing bytes to the SDMX-CSV
        reader takes its *eager* branch — `pl.read_csv` over a `BytesIO` —
        which puts the whole slice in memory before anything else happens. A
        path takes the lazy branch instead.

        Args:
            path: Path relative to the service root, from `DataRequest.path`.
            params: Query parameters, from `DataRequest.params`.
            to: Destination file. Written in chunks; never held whole.
        """
        url = f"{self._endpoint}/{path}"
        with (
            self._reaching(url),
            self._client.stream("GET", url, params=dict(params), headers={"Accept": self._csv_media_type}) as response,
        ):
            _raise_for_status(response, url=url)
            _require_csv(response, accept=self._csv_media_type, url=url)
            with to.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)

    def download_data(
        self,
        agency: str,
        id: str,
        version: str = "latest",
        *,
        fmt: DataFormat,
        key: str = "all",
        start_period: str | None = None,
        end_period: str | None = None,
        to: Path | None = None,
    ) -> bytes | None:
        """Fetch a dataflow's data in `fmt`, unparsed.

        The byte-faithful counterpart to `get_data_at` / `stream_data_at`, which
        ask for CSV (`csv_media_type`) because their caller is about to hand the
        body to DuckDB's CSV reader. Here the caller wants what the provider
        sent, so the format is the caller's to choose and nothing inspects the
        body afterwards.

        Args:
            agency: Dataflow maintainer agency id.
            id: Dataflow id.
            version: Dataflow version. Defaults to ``"latest"``.
            fmt: The format to ask for. Sent as ``Accept`` verbatim.
            key: SDMX key filter string. Defaults to ``"all"``.
            start_period: ISO period start.
            end_period: ISO period end.
            to: Destination file. When given, the body is streamed in chunks
                and never held whole, and the return is `None`.

        Returns:
            The response body, or `None` when it went to a file.

        Raises:
            ValueError: If the response's ``Content-Type`` names a different
                format than was asked for.
        """
        url = self._data_url(agency, id, version, key)
        params: dict[str, str] = {}
        if start_period is not None:
            params["startPeriod"] = start_period
        if end_period is not None:
            params["endPeriod"] = end_period
        headers = {"Accept": fmt.media_type}

        if to is None:
            with self._reaching(url):
                response = self._client.get(url, params=params, headers=headers)
            _raise_for_status(response, url=url)
            _check_content_type(response, fmt, url=url)
            return response.content

        with self._reaching(url), self._client.stream("GET", url, params=params, headers=headers) as response:
            _raise_for_status(response, url=url)
            _check_content_type(response, fmt, url=url)
            with to.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        return None
