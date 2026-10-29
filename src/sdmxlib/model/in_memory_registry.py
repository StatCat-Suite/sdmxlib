"""InMemoryRegistry — pure-domain SDMX artefact store.

The in-memory Registry is the SDMX Information Model concept of a local
artefact graph: a URN-keyed store whose artefacts reach each other through
``Ref`` objects bound to it.

Key behaviours:

- **Bound, not interned (#492)** — `add` stores a copy whose references look
  their targets up in this registry on access. Nothing is written to a `Ref`
  or an artefact afterwards, and the caller's own object stays as built.
- **Eager accept, lazy resolve** — add artefacts in any order; a reference
  finds its target as soon as the target is added.
- **Pure domain** — no network, no HTTP, no serialisation.

Example:
    reg = InMemoryRegistry()
    reg.add(dsd)
    reg.add(cl_freq)  # in either order
    reg.get(dsd.urn).dimensions["FREQ"].codelist()  # Codelist — no fetch needed
"""

from collections.abc import Iterable, Iterator
from typing import overload

from sdmxlib.model._invariants import advise
from sdmxlib.model._items import resolve_item
from sdmxlib.model._rebind import bind
from sdmxlib.model._resolvers import MapResolver
from sdmxlib.model.binding import DataflowBinding, DriftError, DriftIssue, _validate_binding
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.hierarchy import Hierarchy, _applicable_hierarchies, _unique_hierarchy
from sdmxlib.model.ref import Ref, SupportsUrn, artefact_urn, target_urn
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import ValidationError, _collect_refs, metadata_sets_advisories
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


class InMemoryRegistry:
    """In-memory SDMX artefact store with URN identity and bound references.

    Satisfies the `Registry` Protocol via ``get(urn)`` and
    ``__contains__(urn)``.

    Example:
        reg = InMemoryRegistry()
        reg.add_all([cl_freq, concept_scheme, dsd])

        dsd = reg.get(DataStructure.urn_for(agency="ESTAT", id="CPI", version="1.0"))
        dims = reg.get_all(Dimension)  # not typically used; prefer dsd.dimensions
    """

    def __init__(self) -> None:
        self._store: dict[str, SupportsUrn] = {}  # str(urn) → artefact
        # What every `Ref` this registry binds looks its target up in (#492).
        self._resolver = MapResolver(self._store)
        # Python type → {str(urn) → artefact}. Keyed by URN rather than held
        # as a list for two reasons that turned out to be one line: a list
        # membership test is O(n) with attrs' deep `__eq__`, making `add`
        # quadratic, and it dedups by *equality* while `_store` replaces by
        # URN — so re-adding a modified artefact left the stale copy visible
        # to `get_all` and invisible to `get` (#138).
        self._by_type: dict[type, dict[str, SupportsUrn]] = {}
        # str(urn) → the version it was published at, for each artefact
        # `add_all` rewrote (#420). Cleared when the URN is added again.
        self._rewritten: dict[str, str] = {}

    # ── Public API ────────────────────────────────────────────────────────

    def add(self, artefact: SupportsUrn) -> None:
        """Add an artefact to the registry.

        - Stores a copy whose every ``Ref`` is bound to this registry, so it
          finds its target here — now, or once the target is added.
          The caller's object is left as built: read the stored one back
          with `get`.
        - Every reference already bound here finds this artefact at once.

        A version only SDMX 2.1 accepts, on the artefact or on a reference
        inside it, is refused: the model holds SDMX 3.x. `add_all`
        takes a rewrite for those.

        Raises:
            TypeError: If the artefact does not satisfy ``SupportsUrn``.
            ValidationError: If a version only SDMX 2.1 accepts is present.
        """
        _require_urn(artefact)
        check_ingestable([artefact])
        self._rewritten.pop(str(artefact_urn(artefact)), None)
        self._intern(artefact)
        advise(metadata_sets_advisories([artefact], self.get))

    def add_all(
        self, artefacts: Iterable[SupportsUrn], *, rewrite_version: VersionRewrite | None = None
    ) -> tuple[RewrittenVersion, ...]:
        """Add multiple artefacts in any order, refusing the batch as a whole.

        Every artefact is checked before any is added, so a refusal leaves the
        registry as it was and names every problem at once.

        Args:
            artefacts: The batch.
            rewrite_version: Rewrites versions SDMX 3.x cannot hold — a mapping
                from published to ingested version, or a function such as
                `sl.versions.strip_leading_zeros`. Applies to every artefact
                in the batch and every reference inside one.

        Returns:
            One `RewrittenVersion` per artefact whose version changed.

        Raises:
            ValidationError: If a version only SDMX 2.1 accepts is left after
                the rewrite, if the rewrite makes two artefacts one or
                replaces one this registry holds, or if an artefact outside the
                batch references a version the batch rewrites.
        """
        batch = list(artefacts)
        for artefact in batch:
            _require_urn(artefact)
        batch, rewrites = rewrite_versions(batch, rewrite_version)
        check_ingestable(batch)
        self._refuse_rewrites(batch, rewrites)
        for artefact in batch:
            self._rewritten.pop(str(artefact_urn(artefact)), None)
            self._intern(artefact)
        for rewrite in rewrites:
            self._rewritten[rewrite.urn] = rewrite.original
        advise(metadata_sets_advisories(batch, self.get))
        return rewrites

    def _refuse_rewrites(self, batch: list[SupportsUrn], rewrites: tuple[RewrittenVersion, ...]) -> None:
        """What only this registry can know: a collision, or a reference left dangling."""
        if not rewrites:
            return
        issues = [
            collision(rewrite)
            for rewrite in rewrites
            if rewrite.urn in self._store and self._rewritten.get(rewrite.urn) != rewrite.original
        ]
        in_batch = {str(artefact_urn(artefact)) for artefact in batch}
        # Only a reference to a version nothing here answers dangles: under a
        # mapping such as `{"1.0": "1.0.0"}` a held `1.0` stays, and so do
        # the references to it.
        rewrites = tuple(rewrite for rewrite in rewrites if original_urn(rewrite) not in self._store)
        for urn, held in self._store.items():
            if urn in in_batch:
                continue
            for path, ref in _collect_refs(held, urn):
                issues.extend(dangling(path, rewrite) for rewrite in rewrites if names(ref.urn, original_urn(rewrite)))
        if issues:
            raise ValidationError(issues)

    def _intern(self, artefact: SupportsUrn) -> None:
        urn_str = str(artefact_urn(artefact))
        stored = bind(artefact, self._resolver)
        # A URN can change class on replacement: a `GeographicCodelist` shares
        # `sdmx_class` with `Codelist`, so the two have one URN. The replaced
        # entry leaves its own class's bucket, or `get_all` of that class
        # would keep it while `get` returned the new one (GH-263).
        replaced = self._store.get(urn_str)
        if replaced is not None and type(replaced) is not type(stored):
            self._by_type[type(replaced)].pop(urn_str, None)
        self._store[urn_str] = stored
        # Track by Python type for get_all(), under the same key `_store`
        # uses so the two cannot disagree about what is stored.
        self._by_type.setdefault(type(stored), {})[urn_str] = stored
        self._resolver.forget_items()

    @overload
    def get[T](self, target: SdmxUrn[object] | Ref[object], /, *, expect: type[T]) -> T | None: ...

    @overload
    def get[T](self, target: SdmxUrn[T] | Ref[T], /) -> T | None: ...

    def get[T](self, target: SdmxUrn[object] | Ref[object], /, *, expect: type[T] | None = None) -> T | None:
        """Look up an artefact by exact URN, or by a `Ref` naming one.

        Returns ``None`` if absent. A `str` raises `TypeError` — it used to
        work here by accident and nowhere else.

        An **item** URN answers the item: `get` of
        ``…codelist.Code=AG:CL(1.0).A`` is that `Code`, found inside the
        codelist this registry holds. The store keeps maintainables, so this
        used to answer None for every item URN — which made
        ``reg.get(dim.concept.urn)`` a silent miss while
        ``reg.get(dsd.urn)`` worked, and there is no reading of "look up an
        artefact by URN" under which a `Code` is not one. The lookup is
        lazy: a miss, then one O(1) step inside the scheme the URN names, so
        holding a 579,951-code codelist costs nothing until someone asks for a
        code.

        **`expect=` asks for a class and gets that class or None.** A URN
        parsed from a string carries no type — `SdmxUrn.parse` answers
        `SdmxUrn[object]`, so this answers `object | None`. Passing the
        class states the expectation where the caller actually holds it:

            reg.get(SdmxUrn.parse(text), expect=MetadataSet)   # MetadataSet | None

        The check is `isinstance`, which makes it the same operation a caller
        writes by hand — a store row may legitimately name something other than
        what this call site can use, and returning None is how that is said.
        """
        urn = target_urn(target)
        found = self._store.get(str(urn))
        if found is None:
            found = resolve_item(self._store, urn)
        if expect is not None:
            return found if isinstance(found, expect) else None
        return found  # pyrefly: ignore[bad-return]

    def get_all[T](self, artefact_type: type[T]) -> list[T]:
        """All artefacts of the given Python type (exact match, not subclasses).

        In insertion order, and one entry per URN: re-adding an artefact
        replaces it here exactly as it does in `get`.
        """
        return list(self._by_type.get(artefact_type, {}).values())  # pyrefly: ignore[bad-return]

    def ref[T](self, urn: SdmxUrn[T]) -> Ref[T]:
        """A `Ref` bound to this registry: it finds its target here, now or once it is added."""
        return Ref(urn=urn, _resolver=self._resolver)

    def __contains__(self, target: object, /) -> bool:
        """True if an artefact with this URN is stored.

        Takes a `Ref` as well as an `SdmxUrn`, for the same reason `get` does
        — ``ref in reg`` answered ``False`` for a *present* artefact before
        #370. Anything else is still ``False`` rather than an error: ``in``
        is a question, and the answer for an unrelated object is "no".

        Answers for an item URN whatever `get` answers. Kept in step
        deliberately: ``urn in reg`` being False while ``reg.get(urn)`` returns
        the code is not a smaller bug than #405, it is a stranger one.
        """
        if isinstance(target, (SdmxUrn, Ref)):
            urn = target_urn(target)
            return str(urn) in self._store or resolve_item(self._store, urn) is not None
        return False

    # ── Graph queries ────────────────────────────────────────────────────

    def applicable_hierarchies(
        self,
        dataflow: Dataflow,
        dim_id: str,
    ) -> list[Ref[Hierarchy]]:
        """All Hierarchies whose HierarchyAssociations bind ``dim_id`` on ``dataflow``.

        Returns dataflow-scoped HAs first (sorted lex by HA URN), then
        unscoped HAs (also lex-sorted). HAs whose ``context_object``
        names a different dataflow are skipped.

        Per the SDMX 3.x IM the relation is many-to-many — this is the
        canonical enumeration surface. Use `unique_hierarchy` when the
        caller asserts at most one applies.
        """
        return _applicable_hierarchies(self, dataflow, dim_id)

    def unique_hierarchy(
        self,
        dataflow: Dataflow,
        dim_id: str,
    ) -> Ref[Hierarchy] | None:
        """The single Hierarchy applicable to ``dim_id`` on ``dataflow``, when exactly one applies.

        Returns ``None`` if no HierarchyAssociation binds the dimension.
        Returns the single applicable Hierarchy's `Ref` if exactly one
        applies (dataflow-scoped wins over unscoped at the same dim).
        Raises `AmbiguousHierarchyError` if more than one HA applies at
        the chosen scope — call `applicable_hierarchies` to enumerate.
        """
        return _unique_hierarchy(self, dataflow, dim_id)

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

    def __len__(self) -> int:
        """Number of artefacts stored."""
        return len(self._store)

    def __iter__(self) -> Iterator[SupportsUrn]:
        """Every artefact stored, in insertion order, one per URN."""
        return iter(list(self._store.values()))

    def __repr__(self) -> str:
        counts = ", ".join(f"{t.__name__}={len(v)}" for t, v in self._by_type.items())
        return f"InMemoryRegistry({counts or 'empty'})"

    # ── Internals ─────────────────────────────────────────────────────────


def _require_urn(artefact: object) -> None:
    if not isinstance(artefact, SupportsUrn):
        msg = (
            f"{type(artefact).__name__} does not satisfy SupportsUrn — "
            "it needs sdmx_package, sdmx_class, agency_id, id, version"
        )
        raise TypeError(msg)
