"""How long a `latest` pointer may go un-re-asked (#339).

#338 settled the *rule*: a pinned URN names an immutable artefact and caches
forever, while `latest` names a mutable pointer and is never itself a cache
key. It left the *window* — how long the library may skip re-asking where the
pointer points — as this issue.

**The provider decides, with a caller override.** This is the one place HTTP
cache semantics legitimately apply: a pinned artefact's lifetime is settled by
SDMX versioning and no proxy header shortens it, but *how long ago someone last
checked* is exactly what `max-age` and `ETag` are for.

Measured from the recorded cassettes, providers differ and the difference is
real:

| provider             | structures                                    | revalidation |
| -------------------- | --------------------------------------------- | ------------ |
| ECB, IMF             | `no-cache, no-store, max-age=0, must-revalidate` | none      |
| OECD                 | `public, max-age=86400`                        | Last-Modified |
| SDMX Global Registry | *(absent)*                                     | ETag         |

So OECD says a day and means it; the Global Registry offers a cheap 304; and
ECB/IMF say "never" on *codelists*, while stamping `isFinal="false"` on all
2420 artefacts recorded — both of which look like appliance defaults rather
than statements about volatility. That is why obedience alone is the wrong
default and `floor` exists.

The override is a **floor, not a ceiling**: it can only make a stingy provider
more permissive, never shorten what a generous one offered. A caller who wants
less can pass a pinned URN, which is not a pointer at all.
"""

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from attrs import frozen

__all__ = ["Freshness", "read_freshness"]

_MAX_AGE = re.compile(r"\bmax-age\s*=\s*(\d+)", re.IGNORECASE)


@frozen
class Freshness:
    """What one response said about reusing the answer it gave.

    Deliberately *not* the response body. The artefacts are already interned by
    pinned URN, so storing bodies here would hold a second copy of what the
    session already has — and the only question this needs to answer is
    "has the pointer moved?", which validators answer without one.
    """

    #: When the response was received. Naive UTC, per `sdmxlib._time`'s
    #: convention, so comparisons cannot shift with a reader's zone.
    stored_at: datetime
    #: `ETag`, for `If-None-Match`. The cheapest revalidation there is.
    etag: str | None = None
    #: `Last-Modified`, for `If-Modified-Since`. Weaker — one-second
    #: granularity — but the only validator OECD offers.
    last_modified: str | None = None
    #: `max-age`, in seconds, or `None` when the provider did not say.
    max_age: int | None = None
    #: `no-store` or `no-cache`, either of which means "ask again".
    no_store: bool = False

    def is_fresh(self, *, now: datetime, floor: int) -> bool:
        """Whether the pointer may be reused without asking.

        `floor` is the caller's override in seconds: the window is the larger
        of what the provider allowed and what the caller insisted on, so an
        override can rescue a `no-store` provider and can never shorten a
        generous one.

        A provider that said nothing at all gets `0` rather than a guess — the
        library has no basis for inventing a duration, and #338's rule (re-ask
        every time) is the safe end of that trade.
        """
        allowed = 0 if self.no_store else (self.max_age or 0)
        window = max(allowed, floor)
        return now - self.stored_at < timedelta(seconds=window)

    def validators(self) -> dict[str, str]:
        """Conditional-request headers, empty when the response offered none.

        Both when both are present: a provider may honour either, and sending
        one it ignores costs nothing while sending neither costs a whole body.
        """
        headers: dict[str, str] = {}
        if self.etag:
            headers["If-None-Match"] = self.etag
        if self.last_modified:
            headers["If-Modified-Since"] = self.last_modified
        return headers


def read_freshness(headers: Mapping[str, str], *, now: datetime) -> Freshness:
    """What `headers` permit, as a `Freshness`.

    Tolerant by construction. A malformed `Cache-Control` yields "no window
    stated" rather than an exception: this decides an optimisation, and a
    header a provider wrote badly must not fail a request that succeeded.
    """
    control = headers.get("cache-control", "")
    matched = _MAX_AGE.search(control)
    lowered = control.lower()
    return Freshness(
        stored_at=now.astimezone(UTC).replace(tzinfo=None) if now.tzinfo else now,
        etag=headers.get("etag"),
        last_modified=headers.get("last-modified"),
        max_age=int(matched.group(1)) if matched else None,
        no_store="no-store" in lowered or "no-cache" in lowered,
    )
