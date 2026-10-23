"""Session — identity map and artefact construction."""

from collections.abc import Iterable
from enum import IntEnum
from typing import Any, cast

from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.collections import ArtefactList
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.urn import SdmxUrn


class Completeness(IntEnum):
    """How completely a URN's references are known — #344.

    This replaced a boolean. `_resolved_map` recorded *that* a URN had been
    fetched with its references and not *how completely*, so a
    ``detail=referencepartial`` fetch and a ``detail=full`` one set the same
    flag and the second was a cache hit on the first. A caller writing
    ``partial=False`` against a pinned URN was handed the trimmed codelists,
    silently — #338's defect verbatim, and its regression test missed it
    because every case there used `latest`, which re-asks for other reasons.

    **Ordered, and compared with `>=`.** The question a read asks is "is what
    we hold at least as complete as what was requested", which a set of flags
    cannot answer: `FULL` must satisfy a request for `PARTIAL` (full is more
    than enough) while `PARTIAL` must not satisfy `FULL`. `IntEnum` so that
    ordering is the type's own, not a comparison table beside it.

    Not a second cache keyed by detail. The session is an identity map holding
    **one** object per URN — a fuller fetch *fills in* the object an earlier
    one handed out, which `test_a_fuller_fetch_upgrades_the_interned_codelist`
    documents — so this is a record of what has been learned about that one
    object, not a second copy of it.
    """

    #: Fetched without references. The artefact's own fields, nothing followed.
    UNRESOLVED = 0
    #: References followed, item schemes trimmed to what the structure uses
    #: (``detail=referencepartial``).
    PARTIAL = 1
    #: References followed and untrimmed (``detail=full``).
    FULL = 2


def completeness_of(*, detail: str, references: str) -> Completeness:
    """What a fetch with these wire parameters learns.

    Keyed on `references` first: ``detail=full`` with ``references=none``
    is a complete *artefact* and no references at all, so it is `UNRESOLVED`
    here. The two words mean different things — `detail` is about fields,
    `references` about depth — and reading `detail` alone would mark the
    commonest unresolved fetch in the library as the most complete one there
    is.
    """
    if references == "none":
        return Completeness.UNRESOLVED
    return Completeness.FULL if detail == "full" else Completeness.PARTIAL


class Session:
    """Session-scoped identity map for SDMX artefacts.

    Interns artefacts by URN so the same object is returned for the
    same URN within a session. Also merges successive StructureMessages
    into the identity map.

    Two indices, both keyed by the **versioned** URN string:
    - ``_map``: URN → artefact (all ingested)
    - ``_completeness``: URN → `Completeness`, how deeply it has been fetched

    The second is a record, not a second cache: `_map` holds the one object
    per URN, and a fuller fetch replaces it — a reference held into the older
    one finds the fuller one through its registry (#492). `get_at_least` reads the pair — see `Completeness` for why
    it is a level and not the boolean it replaced (#344).

    **There were two more, keyed ``cls:agency:id``, and they answered a
    `latest` URN from cache.** That is what #338 reports: a pointer served
    from a cache that never re-asks where it points, for the life of a process
    that may run for weeks. Removed rather than bounded — a `latest` URN is
    not an artefact identity and has no business being a cache key. The
    artefact a `latest` fetch resolves to is still interned here under its
    *pinned* URN, which is immutable and cacheable forever, so
    ``get(CL_X(1.0))`` after ``get(CL_X(latest))`` still hits.
    """

    def __init__(self) -> None:
        # ``_map``/``_resolved_map`` only store + return-via-get (the unsafe
        # object→T narrowing is localized to ``get``), so ``object`` suffices
        # and admits the registry-cache path that writes an unbounded ``T``.
        self._map: dict[str, object] = {}
        self._completeness: dict[str, Completeness] = {}
        self._class_tomsg_field: dict[str, str] = {}

    def ingest(
        self, artefacts: Iterable[MaintainableArtefact], *, completeness: Completeness = Completeness.UNRESOLVED
    ) -> None:
        """Add artefacts to the identity map — a message's, as the registry stored them.

        The registry's copies, not the message's own (#492): `add` stores an
        artefact with its references bound to the registry, so those are the
        ones that resolve across messages.

        ``completeness`` says how deeply the *fetch* went, and applies to every
        artefact in the message — as the boolean it replaced did. A response to
        one request describes one depth.

        Recorded with `max`: learning is monotonic. A later shallower fetch of
        a URN already known fully must not downgrade it, or the next deep read
        re-asks for something this process already holds.
        """
        for artefact in artefacts:
            cls_name = type(artefact).__name__
            package = getattr(type(artefact), "sdmx_package", None)
            if package is None:
                continue
            urn = SdmxUrn.make(
                type(artefact),
                package=package,
                artefact_class=cls_name,
                agency=artefact.agency_id,
                id=artefact.id,
                version=artefact.version,
            )
            msg_field = getattr(type(artefact), "msg_field", None)
            if msg_field and cls_name not in self._class_tomsg_field:
                self._class_tomsg_field[cls_name] = msg_field
            key = str(urn)
            self._map[key] = artefact
            self._completeness[key] = max(self._completeness.get(key, Completeness.UNRESOLVED), completeness)

    def get[T](self, urn: SdmxUrn[T]) -> T | None:
        """Return a cached artefact by URN, or None if not cached.

        **A `latest` URN never hits.** It names a *pointer*, not an artefact,
        and only the authority knows where it points now. This used to answer
        from a `_latest` index and so served a superseded artefact for the life
        of the registry — which, for the FastAPI shape this API is built for,
        is the life of the process (#338).

        The pinned URN a `latest` fetch resolves to *is* cached, forever: a
        pinned version is immutable under SDMX's versioning rules, and no
        provider header shortens that. So the first `get(latest)` costs a
        request and every later `get(CL_X(1.0))` is free.
        """
        hit = self._map.get(str(urn))
        if hit is not None:
            return hit  # pyrefly: ignore[bad-return]
        return None

    def get_at_least[T](self, urn: SdmxUrn[T], completeness: Completeness) -> T | None:
        """A cached artefact, if it is known at least this completely.

        `None` otherwise — including when the artefact *is* held but less
        completely than asked. That case must go to
        the provider, and returning the shallower object is #344.

        Same `latest` rule as `get` — see there for why.
        """
        key = str(urn)
        if self._completeness.get(key, Completeness.UNRESOLVED) < completeness:
            return None
        hit = self._map.get(key)
        if hit is not None:
            return hit  # pyrefly: ignore[bad-return]
        return None

    def completeness_for(self, urn: SdmxUrn[object]) -> Completeness:
        """How completely `urn` is known here. `UNRESOLVED` when not held."""
        return self._completeness.get(str(urn), Completeness.UNRESOLVED)

    def first_of_class[T](self, artefact_type: type[T]) -> T | None:
        """Return the first cached artefact whose type matches artefact_type."""
        for v in self._map.values():
            if isinstance(v, artefact_type):
                return v
        return None

    def all_of_class[T](self, artefact_type: type[T]) -> list[T]:
        """Return all cached artefacts whose type matches artefact_type."""
        return [v for v in self._map.values() if isinstance(v, artefact_type)]

    def extract[T](self, msg: StructureMessage, urn: SdmxUrn[T]) -> T | None:
        """Pull a specific artefact out of a freshly-parsed message by URN.

        Looks in the StructureMessage collection named by the artefact
        type's ``msg_field`` class attribute.
        """
        field = self._class_tomsg_field.get(urn.artefact_class)
        if field is None:
            return None
        # The slot name is only known at runtime. Annotate what `getattr`
        # hands back, then recover `T` in one place: `T` here is unbounded
        # (bounding it to `Maintainable` would cascade through every
        # `RestRegistry.get[T]`-shaped caller), so the element type cannot be
        # threaded statically. This is the documented "localize the unsafe
        # object -> T step to the accessor" case from CLAUDE.md, written as a
        # cast rather than a suppression so it belongs to no single checker.
        collection: ArtefactList[Any] | None = getattr(msg, field, None)
        if collection is None:
            return None
        return cast("T | None", collection.get(urn))
