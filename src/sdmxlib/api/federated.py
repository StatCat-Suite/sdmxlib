"""FederatedRegistry — agency-routed dispatch across multiple sources.

The headline of #46. Composes any number of `Registry`
instances (in-memory, local DuckDB, REST) and dispatches each ``get(urn)``
call to the source responsible for the URN's maintenance agency. The user
sees a single registry that resolves URNs from many backends.

Cache-mode v1 — each agency has exactly one source. Peer-mode (multiple
sources cooperating to answer a single URN) is intentionally out of
scope; see #46 for that conversation.

Example:
    # rebuild=True: a cache can be fetched again, so a store schema change may
    # empty it rather than refuse to open (GH-57).
    local = sl.LocalRegistry("cache.duckdb", source_registry="local", rebuild=True).open()
    estat = sl.RestRegistry(sl.Provider.ESTAT, store=local)
    sdmx_global = sl.RestRegistry(sl.Provider.SDMX_GLOBAL, store=local)

    fed = sl.FederatedRegistry(
        routing={"ESTAT": estat, "SDMX": sdmx_global},
        default=local,  # locally-authored agencies dispatch here
    )

    # Routes by maintenance agency:
    dsd = fed.get(sl.DataStructure.urn_for(agency="ESTAT", id="X", version="1.0"))
    # → estat → HTTP fetch → materialise to local

    cl = fed.get(sl.Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="2.1"))
    # → sdmx_global → HTTP fetch → materialise to local

    own = fed.get(sl.Codelist.urn_for(agency="MY_ORG", id="CL_X", version="1.0"))
    # → falls through to local (no routing entry)
"""

import annotationlib
import functools
import inspect
from typing import overload

import httpx2

from sdmxlib.api.policy import RegistryUnavailable
from sdmxlib.api.registry import Resolve
from sdmxlib.model.binding import DataflowBinding, DriftError, DriftIssue, _validate_binding
from sdmxlib.model.ref import Ref, target_urn
from sdmxlib.model.registry import RegistryReader
from sdmxlib.model.urn import SdmxUrn


@functools.cache
def _get_parameters(source_type: type) -> frozenset[str]:
    """Keyword names ``source_type.get`` accepts.

    Read off the signature rather than declared in a Protocol, because
    `runtime_checkable` checks that a method *exists* and not what it takes —
    every registry would match. Cached on the type: the signature is fixed at
    class definition, and `inspect.signature` on every dispatch would put
    reflection in the read path.
    """
    getter = getattr(source_type, "get", None)
    if getter is None:  # pragma: no cover - a source without `get` is not a source
        return frozenset()
    # Names only, so annotations are never evaluated: since PEP 649 a plain
    # `signature` evaluates them, and a source's `get` may name a type it
    # imports for type checking alone.
    return frozenset(inspect.signature(getter, annotation_format=annotationlib.Format.FORWARDREF).parameters)


class FederatedRegistry:
    """Read-side dispatcher across multiple Registry sources.

    Routes each URN-keyed lookup by maintenance agency. Configure with a
    ``routing`` map from agency id to a `Registry` source, plus
    an optional ``default`` source for agencies absent from the map.

    Satisfies the `Registry` Protocol —
    a ``FederatedRegistry`` can itself be a source in another
    ``FederatedRegistry`` (composition).

    Args:
        routing: Mapping of agency id (e.g. ``"ESTAT"``, ``"SDMX"``) to a
            `Registry` source. Lookup keys are matched against
            ``SdmxUrn.agency``.
        default: Optional fallback source for URNs whose agency is not
            in ``routing``. ``None`` (the default) means "no fallback":
            unrouted URNs raise `KeyError`.

    Note:
        FederatedRegistry is a *router*, not a *store*. It has no
        ``add()`` method — write to specific concrete registries
        (typically the local one in your routing chain).
    """

    def __init__(
        self,
        *,
        routing: dict[str, RegistryReader],
        default: RegistryReader | None = None,
    ) -> None:
        self._routing = dict(routing)
        self._default = default

    @property
    def routing(self) -> dict[str, RegistryReader]:
        """Read-only view of the agency → source map."""
        return dict(self._routing)

    @property
    def default(self) -> RegistryReader | None:
        """The fallback source used when no routing entry matches."""
        return self._default

    # ── Routing ──────────────────────────────────────────────────────────

    def _route(self, urn: SdmxUrn[object]) -> RegistryReader | None:
        """Pick the source responsible for ``urn``'s agency."""
        return self._routing.get(urn.agency, self._default)

    def source_for[T](self, urn: SdmxUrn[T]) -> RegistryReader | None:
        """Public version of the routing decision — useful for diagnostics."""
        return self._route(urn)

    # ── Registry Protocol ────────────────────────────────────────────────

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
        """Resolve ``target`` via the routed source.

        Routing rules:

        - URN agency in ``routing`` → dispatch to that source.
        - URN agency absent and ``default`` is set → dispatch to default.
        - URN agency absent and no default → raise ``KeyError``.

        **`None` means the source answered "not here".** An unreachable
        source raises `RegistryUnavailable` naming it and the URN — §5.8.
        There is no policy converting one into the other: they are different
        facts, and the caller's response differs (retry a dead source; fall
        back for a missing artefact).

        ``resolve`` and ``partial`` mean what they mean on `RestRegistry`, and
        are **translated** rather than forwarded blindly — see
        `_dispatch_kwargs` for which source gets which, and why a local one
        needs neither.

        Args:
            target: Identity of the artefact to read — a URN, or a `Ref`
                naming one. Routing reads the *agency* off it, so a `Ref`
                had to be unwrapped before this could route at all.
            resolve: How deep to follow references, as on `RestRegistry.get`:
                `True` for every reference, `"all"` or `"parents"` for the
                artefacts referring to it, a sequence of artefact types for
                only those.
            partial: `False` asks for untrimmed codelists.
            expect: The class the caller requires. The artefact is returned
                when it is an instance, `None` otherwise — see
                `RestRegistry.get` for why this exists.

        Raises:
            KeyError: If no source is routed for the URN's agency.
            TypeError: If ``partial=False`` routes to a source that cannot
                promise it.
            RegistryUnavailable: If the routed source could not be reached.
        """
        urn = target_urn(target)
        source = self._route(urn)
        if source is None:
            err = (
                f"No source registered for agency {urn.agency!r} and no default — "
                "add an entry to routing or supply default=..."
            )
            raise KeyError(err)
        kwargs = self._dispatch_kwargs(source, urn, resolve=resolve, partial=partial)
        try:
            found = source.get(urn, **kwargs)
        except RegistryUnavailable:
            # Already named by the source that raised it; re-wrapping would
            # bury the inner source's id behind this one's.
            raise
        except (httpx2.HTTPError, OSError) as exc:
            raise RegistryUnavailable(getattr(source, "id", None), exc, tried=str(urn)) from exc
        # Applied here rather than forwarded: `_dispatch_kwargs` sends each
        # source only what it understands, and a local source takes neither
        # `resolve` nor `partial` — nor, necessarily, `expect`. Checking the
        # answer is the one form every source supports.
        if expect is not None:
            return found if isinstance(found, expect) else None
        return found

    @staticmethod
    def _dispatch_kwargs(
        source: RegistryReader,
        urn: SdmxUrn[object],
        *,
        resolve: Resolve,
        partial: bool,
    ) -> dict[str, object]:
        """Which of ``resolve`` / ``partial`` this source can be asked for.

        The three `get`s do not share a signature — `RestRegistry` takes both,
        `LocalRegistry` takes neither — so a federation cannot forward its
        keywords blindly. It routes by *agency*, though, and the agency is in
        the URN, so it knows which source will answer before it asks. That is
        what makes translating possible rather than guessing.

        **``resolve`` is dropped for a source that does not take it, and that
        is not a silent no-op.** #340's rule is that depth is a choice about
        how many *requests* to make; a local store has already paid for all of
        them and resolves unconditionally. So the artefact a caller gets back
        already satisfies `resolve=True`, and `resolve=False` only ever
        declined work that was not going to happen. Either way the *result* is
        what was asked for — only the request count differs, and it is zero.

        **``partial=False`` is refused instead.** It has no such reading: a
        store holds whatever was written to it, so a local source can neither
        honour "untrimmed" nor discover that it has not. Answering it with
        trimmed codelists is the #338 defect exactly — a wrong answer to an
        explicit question — and that one shipped for a release because nothing
        raised. ``partial=True`` is the "no special request" value and is
        dropped like `resolve`.
        """
        accepted = _get_parameters(type(source))
        kwargs: dict[str, object] = {}
        if "resolve" in accepted:
            kwargs["resolve"] = resolve
        if "partial" in accepted:
            kwargs["partial"] = partial
        elif not partial:
            name = getattr(source, "id", None) or type(source).__name__
            msg = (
                f"partial=False routes to {name}, which cannot promise it: "
                f"{type(source).__name__}.get takes no partial=, and a store holds "
                "whatever was written to it. Ask the source that fetches — "
                f"fed.source_for(urn) — or accept what {name} has for {urn}."
            )
            raise TypeError(msg)
        return kwargs

    def __contains__(self, urn: object) -> bool:
        """True if the routed source already has ``urn`` cached.

        Like the underlying Registry membership: never triggers a network
        round-trip. ``False`` if no source is routed for the URN's agency.
        """
        if not isinstance(urn, SdmxUrn):
            return False
        source = self._route(urn)
        return source is not None and urn in source

    # ── Graph queries ────────────────────────────────────────────────────

    def check_binding_drift(self, binding: DataflowBinding) -> list[DriftIssue]:
        """Inspect ``binding`` for drift against this federated registry.

        Routes the bound Dataflow lookup through the configured sources;
        only the routed source is touched. Returns a list of
        `DriftIssue` — empty if no drift is detected. Use this when you
        want to enumerate or display drift; use ``validate_binding()``
        when you want to raise on any drift.
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

    def __repr__(self) -> str:
        agencies = ", ".join(sorted(self._routing))
        default = " +default" if self._default is not None else ""
        return f"FederatedRegistry(routes=[{agencies}]{default})"


__all__ = ["FederatedRegistry"]
