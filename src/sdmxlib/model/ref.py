"""Ref[T], SupportsUrn, UnresolvedRef, SdmxRef — SDMX reference abstraction."""

import typing
from collections.abc import Iterable
from typing import Any, ClassVar, Generic, Protocol, TypeVar, cast, runtime_checkable

from attrs import Attribute, field, frozen

from sdmxlib.model._binding import Resolver, current
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.urn import SdmxUrn


@runtime_checkable
class SupportsUrn(Protocol):
    """Protocol for SDMX artefacts that can be referenced by URN.

    Any MaintainableArtefact satisfies this protocol. Accepts anywhere
    a Ref target is needed — allows ``Ref.to(cl_freq)`` instead of
    ``Ref.unresolved(cl_freq.urn)``.

    That second spelling now works, and did not when this docstring was
    written: `urn` was the classmethod *builder*, so ``cl_freq.urn`` on an
    instance was a bound method and `Ref.unresolved` refused it. It is a
    property, and the builder is `urn_for`.
    """

    sdmx_package: ClassVar[str]
    sdmx_class: ClassVar[str]

    # Read-only properties (not bare ``attr: str``) so artefacts that derive
    # these — MetadataSet, DataflowBinding — satisfy the protocol too. A bare
    # ``agency_id: str`` member is an *invariant mutable* slot that a
    # ``@property`` can't fulfil; the property form is covariant and accepts
    # both plain attributes and properties. Mirrors `Maintainable`.
    @property
    def agency_id(self) -> str:
        """Maintainer agency identifier."""
        ...

    @property
    def id(self) -> str:
        """Artefact identifier."""
        ...

    @property
    def version(self) -> str:
        """Semantic version string."""
        ...


def short_urn(target: SupportsUrn | SdmxUrn[object]) -> str:
    """The ``AGENCY:ID(VERSION)`` form SDMX uses where a full URN is too long.

    It is the form SDMX-CSV's ``STRUCTURE_ID`` column carries, and
    `formats.sdmx_csv.reader.parse_dataflow_id` has always owned reading it —
    so the library parsed a format it made callers *format* by hand, five times
    over in `src/` alone (#409).

    Takes an artefact or a URN, because both are things a caller holds at the
    point of needing the string.

    Example:
        short_urn(flow)       # 'ESTAT:STS_INPR_M(1.0)'
        short_urn(flow.urn)   # the same
    """
    urn = target if isinstance(target, SdmxUrn) else artefact_urn(target)
    return f"{urn.agency}:{urn.id}({urn.version})"


def urn_of(target: SupportsUrn | Ref[object] | SdmxUrn[object]) -> SdmxUrn[object]:
    """The URN of whatever names an artefact — identity only, never structure.

    The three spellings a caller may hold: the artefact itself, a `Ref` from
    graph traversal, or a URN. Accepting all three is a convenience *over
    identity*; it never reads the artefact's own contents, so passing one
    resolved from another registry cannot make this one interpret anything
    with foreign structure.

    `SdmxUrn[object]`, as `SdmxUrn.parse` and `artefact_urn` now are too
    (#399): every caller here narrows the *result of the lookup* with
    `isinstance`, so the phantom is never recovered. See CLAUDE.md's litmus —
    `object` unless the erased type is needed back out.
    """
    if isinstance(target, Ref):
        return target.urn
    if isinstance(target, SdmxUrn):
        return target
    return artefact_urn(target)


def target_urn[T](target: SdmxUrn[T] | Ref[T]) -> SdmxUrn[T]:
    """The URN a `get` was asked for, from either spelling that names one.

    Narrower than `urn_of` on purpose, in both directions. It refuses the
    artefact itself — asking a registry for something you are holding is not
    a lookup — and it *recovers* ``T``, which `urn_of` erases to ``object``.
    That recovery is the whole reason this is generic: ``get`` returns
    ``T | None``, so the phantom has to survive the normalisation.

    **`str` is refused rather than accepted (#370).** It used to work on the
    two local registries and nowhere else, because their lookup ends in
    ``self._store.get(str(urn))`` and ``str`` of a ``str`` is itself. Nothing
    declared it, and the same accident made a `Ref` — which stringifies to
    ``Ref(CL [unresolved])`` — miss and return ``None`` with no error, so
    traversal code that worked against a `RestRegistry` silently found
    nothing locally. A bare string is not an artefact identity (#338); the
    fix is to build one, and the message says so.

    Raises:
        TypeError: For anything that is not an `SdmxUrn` or a `Ref`.
    """
    if isinstance(target, Ref):
        return target.urn
    if isinstance(target, SdmxUrn):
        return target
    got = type(target).__name__
    msg = (
        f"get() takes an SdmxUrn or a Ref, not {got} — "
        "build one with SdmxUrn.parse(text) for a URN string, or with "
        "<ArtefactClass>.urn_for(agency=..., id=..., version=...)"
    )
    raise TypeError(msg)


def artefact_urn(artefact: SupportsUrn) -> SdmxUrn[object]:
    """Canonical `SdmxUrn` for a maintainable artefact.

    The returned URN matches the primary key written by
    `writers`, so external code sharing the same
    DuckDB connection can compute foreign keys without re-implementing
    the URN convention.

    **Prefer `artefact.urn` where the type is known.** This exists for code
    holding a `SupportsUrn` — a writer walking a heterogeneous list — and pays
    for that generality in the return type below. The property recovers
    `SdmxUrn[Self]` instead, so it carries no `Any`.

    Example:
        >>> str(artefact_urn(codelist))
        'urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ(1.0)'

    Returns `SdmxUrn[object]` (#399): a `SupportsUrn` can be any artefact, so
    the URN can name any. It was `SdmxUrn[Any]`, which let a caller recover
    whatever `Ref[T]` it assigned the result to; a caller that knows the type
    has `artefact.urn`, which is `SdmxUrn[Self]`.
    """
    return SdmxUrn.make(
        type(artefact),
        package=artefact.sdmx_package,
        artefact_class=artefact.sdmx_class,
        agency=artefact.agency_id,
        id=artefact.id,
        version=artefact.version,
    )


#: Covariant so a `Ref[Codelist]` is usable where a `Ref[object]` is wanted.
T_co = TypeVar("T_co", covariant=True)


@frozen
# UP046 wants `class Ref[T]`. Measured on pyrefly 1.2.0 / basedpyright 1.39.9,
# not assumed: under PEP 695 the two checkers disagree — basedpyright infers
# `T` *invariant* (from the mutable `_resolved` field) and rejects
# `Ref[Codelist]` where `Ref[object]` is wanted; pyrefly does not. Making the
# class frozen does not change basedpyright's answer. An explicit covariant
# TypeVar is the only spelling both gates accept, which is the same conclusion
# `SdmxUrn` reached in #201 for a different reason.
#
# Covariance is sound: the class is frozen, so nothing is ever written through
# a `Ref[T]` (#289, #492).
class Ref(Generic[T_co]):  # noqa: UP046 — see above; verified against both checkers
    """A reference to an SDMX artefact.

    Always carries the URN. Finds its target on access (#492): first in the
    `Resolver` of the registry that bound it — so a target added after the
    reference was made is found — then in the object it was built with, if
    `Ref.of` / `Ref.to` built it. Nothing is ever written to it: two `Ref`s
    with one URN are equal, and hash alike, whatever either can find.

    ``None`` on a parent field means "no reference exists."
    ``Ref[T]`` means "a reference exists, resolved or not."

    Access tiers:

    - ``ref.urn``, ``ref.id``, ``ref.agency`` — URN metadata, always available
    - ``ref.resolved`` — bool check
    - ``ref.codes``, ``ref.name`` etc — proxy to resolved object, raises
      ``UnresolvedRef`` if not yet resolved

    Construction:

        Ref.to(cl_freq)                   # from domain object — resolved
        Ref(cl_freq.urn)                  # from SdmxUrn — unresolved
        Ref.unresolved(urn)               # explicit unresolved factory
        Ref.of(urn, obj)                  # explicit resolved factory

    Example:
        ref = Ref(cl_freq_urn)
        ref.id       # "CL_FREQ"
        ref.resolved  # False
        ref.codes    # raises UnresolvedRef

        reg.add(cl_freq)
        reg.ref(cl_freq_urn).resolved  # True — bound to the registry
        reg.ref(cl_freq_urn).codes     # ItemList[Code]
    """

    urn: SdmxUrn[T_co]
    _target: T_co | None = field(default=None, eq=False, repr=False, alias="_target")
    _resolver: Resolver | None = field(default=None, eq=False, repr=False, alias="_resolver")

    def __attrs_post_init__(self) -> None:
        """Reject anything that is not a `SdmxUrn`.

        Validation only — the constructor takes a URN and nothing else. It used
        to *also* accept a `MaintainableArtefact` and rewrite ``self.urn`` in
        place, which no type checker can model: the field said `SdmxUrn[T]`,
        the constructor accepted a union, and the difference was carried by
        three suppressions in this file. Both forms were identical at runtime,
        so the only thing the artefact form added was an expression that could
        not typecheck at any call site. `Ref.to` is now the one way in (#219).
        """
        if isinstance(self.urn, SdmxUrn):
            return
        # Two distinct mistakes land here. Passing a maintainable artefact is
        # the old constructor form; passing a child item never worked, because
        # items are identifiable but not maintainable — their identity lives
        # inside the parent's URN as an item_id fragment.
        got = type(self.urn).__name__
        hint = (
            f"Use Ref.to({got.lower()}) instead."
            if isinstance(self.urn, MaintainableArtefact)
            else (
                "Child items (Dimension, Code, Concept, ...) are not maintainable — "
                "build the URN with SdmxUrn.make(<Class>, package=..., artefact_class=..., "
                "agency=..., id=..., version=..., item_id=...) and pass it to "
                "Ref.unresolved(urn) or Ref.of(urn, obj)."
            )
        )
        msg = f"Ref expects a SdmxUrn, got {got}. {hint}"
        raise TypeError(msg)

    # ── Resolution ────────────────────────────────────────────────────────

    def _find(self) -> T_co | None:
        """The target: the binding registry's, else the one built in, else None."""
        if self._resolver is not None:
            found = self._resolver.lookup(self.urn)
            if found is not None:
                # A resolver answers what the URN names — the contract
                # `Registry.get[T]` already states for the same lookup.
                return cast("T_co", found)
        return self._target

    @property
    def resolved(self) -> bool:
        """True if the target can be found now. False, too, if its registry is closed."""
        try:
            return self._find() is not None
        except UnresolvedRef:
            return False

    @property
    def bound(self) -> bool:
        """True if a registry binds this reference, whether or not it holds the target yet."""
        return self._resolver is not None

    def __call__(self) -> T_co:
        """Unwrap to the target.

        Raises:
            UnresolvedRef: If it cannot be found, or its registry is closed.
        """
        found = self._find()
        if found is None:
            raise UnresolvedRef(self.urn)
        return found

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 — dynamic proxy; see below
        """Proxy attribute access to the resolved object.

        The `Any` return is the one legitimate one in `model/`: this forwards
        an arbitrary attribute off the resolved artefact, so its type is only
        knowable at the call site (``ref.codes``, ``ref.dimensions``, …).
        Returning `object` would force a cast at every one of those.

        Allows ``ref.codes`` instead of unwrapping manually.
        Raises ``UnresolvedRef`` if the ref has not been resolved.
        """
        if name.startswith("__") or name in {"_target", "_resolver", "urn"}:
            # Not proxied: attrs and copy/pickle probe dunders, and a field
            # read before `__init__` set it must fail as itself.
            raise AttributeError(name)
        return getattr(self(), name)

    # ── URN metadata ──────────────────────────────────────────────────────

    @property
    def id(self) -> str:
        """Artefact id from the URN."""
        return self.urn.id

    @property
    def agency(self) -> str:
        """Agency id from the URN."""
        return self.urn.agency

    @property
    def version(self) -> str:
        """Version from the URN."""
        return self.urn.version

    @property
    def item_id(self) -> str | None:
        """Item id from the URN (e.g. the concept id within a ConceptScheme)."""
        return self.urn.item_id

    # ── Factories ─────────────────────────────────────────────────────────

    @classmethod
    def unresolved[U](cls, urn: SdmxUrn[U]) -> Ref[U]:
        """Create an unresolved Ref carrying the URN only.

        Built as ``Ref(...)`` rather than ``cls(...)``: on a class whose
        parameter is covariant, ``cls`` is bound to `T_co` and a factory
        generic in its own ``U`` cannot return through it. `Ref` is not
        subclassed, so nothing is lost (#289).
        """
        return Ref(urn=urn, _resolver=current())

    @classmethod
    def of[U](cls, urn: SdmxUrn[U], obj: U) -> Ref[U]:
        """Create a resolved Ref. Useful in tests and format readers."""
        return Ref(urn=urn, _target=obj, _resolver=current())

    @classmethod
    def to[U](cls, obj: U) -> Ref[U]:
        """Create a resolved Ref from a MaintainableArtefact instance.

        The one way to build a `Ref` from an artefact. Derives the URN and
        holds the object, so the attribute proxy works immediately.

        The object must be a ``MaintainableArtefact`` (Codelist, DataStructure,
        Dataflow, …). Child items like ``Dimension``, ``Code``, or ``Concept``
        are not maintainable — their identity lives inside the parent's URN as
        an item_id fragment, so this raises ``TypeError`` on them. Construct
        their URN explicitly via ``SdmxUrn.make(...)`` and pass it to
        `Ref.unresolved` or `Ref.of` instead.

        Args:
            obj: The maintainable artefact to reference.

        Returns:
            A resolved `Ref` carrying the derived URN.

        Raises:
            TypeError: If *obj* is not a `MaintainableArtefact`.

        Example:
            cl = Codelist(id="CL_FREQ", maintainer="SDMX", version="1.0", ...)
            ref = Ref.to(cl)
            ref.resolved   # True
        """
        if not isinstance(obj, MaintainableArtefact):
            msg = (
                f"Ref.to expects a MaintainableArtefact, got {type(obj).__name__}. "
                "Child items (Dimension, Code, Concept, ...) are not maintainable — "
                "build the URN with SdmxUrn.make(...) and pass it to "
                "Ref.unresolved(urn) or Ref.of(urn, obj)."
            )
            raise TypeError(msg)
        urn: SdmxUrn[U] = SdmxUrn.make(
            type(obj),
            package=obj.sdmx_package,
            artefact_class=obj.sdmx_class,
            agency=obj.agency_id,
            id=obj.id,
            version=obj.version,
        )
        return Ref(urn=urn, _target=obj, _resolver=current())

    # ── Boolean ───────────────────────────────────────────────────────────

    def __bool__(self) -> bool:
        """Always True — the Ref exists (use .resolved to check resolution)."""
        return True

    # ── Repr ──────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        if self.resolved:
            return f"Ref({self.urn.id} → resolved)"
        return f"Ref({self.urn.id} [unresolved])"


class UnresolvedRef(Exception):  # noqa: N818
    """Raised when accessing an unresolved Ref."""

    def __init__(self, urn: SdmxUrn[object], reason: str | None = None) -> None:
        self.urn = urn
        self.reason = reason
        super().__init__(
            reason or f"{urn} has not been resolved — add the artefact to the registry this reference is bound to."
        )


@frozen
class SdmxRef:
    """Partial artefact identity for filtering and lookup.

    All fields optional. ``None`` means "match any."
    Used as ``ArtefactList`` key and in ``Query``.

    Example:
        SdmxRef(agency="SDMX", id="CL_FREQ")
        SdmxRef(id="CL_FREQ", version="1.0")
    """

    agency: str | None = None
    id: str | None = None
    version: str | None = None


def _ref_target(declared: object) -> str:
    """The class a field's `Ref[...]` annotation names, or "object" when it names none.

    Read off the annotation's structure, not its text: a field declared before
    the class it refers to holds a `ForwardRef` in its place (PEP 649).
    """
    if typing.get_origin(declared) is Ref:
        (target,) = typing.get_args(declared)
        if isinstance(target, typing.ForwardRef):
            return target.__forward_arg__
        return target.__name__ if isinstance(target, type) else "object"
    return next((found for arg in typing.get_args(declared) if (found := _ref_target(arg)) != "object"), "object")


def refuse_urn_text(instance: object, attribute: Attribute[object], value: object) -> None:
    """Attrs validator for every `Ref`-typed field: a URN *string* is refused (#418).

    No reference field converts text, and until #418 none refused it either: 33
    of the 34 such fields the maximal fixture reaches stored a `str` as given,
    and the first thing to notice was a writer's `AttributeError` on `.urn`,
    naming neither the artefact nor the field. Converting text instead was
    considered and declined — `get()` dropped `str` for the same reason (#370):
    the typed URN is the API, and the class that parses it is what says what a
    URN may name.

    Only text is checked. Every other wrong type is the checker's to catch; a
    string is the one that passes for a URN by eye. Runs on `attrs.evolve` too;
    there is no assignment, every model class being frozen (#492).
    """
    values = value if isinstance(value, (list, tuple)) else (value,)
    if not any(isinstance(v, str) for v in values):
        return
    target = _ref_target(attribute.type)
    fix = "SdmxUrn.parse(text)" if target == "object" else f"{target}.parse_urn(text)"
    msg = (
        f"{type(instance).__name__}.{attribute.name} got a URN string; it takes a Ref, an SdmxUrn "
        f"or the artefact itself. Parse the text first: {fix}."
    )
    raise TypeError(msg)


def to_ref[T](v: Ref[T] | SdmxUrn[T]) -> Ref[T]:
    """Attrs converter: an `SdmxUrn` becomes an unresolved `Ref` (#418).

    What the hand-written converters on `Dimension.concept` and friends already
    did, shared, for the reference fields that had no converter and so stored
    a bare `SdmxUrn` that every writer then failed on.
    """
    return Ref[T].unresolved(v) if isinstance(v, SdmxUrn) else v


def to_optional_ref[T](v: Ref[T] | SdmxUrn[T] | None) -> Ref[T] | None:
    """`to_ref` for a field that may be absent."""
    return None if v is None else to_ref(v)


def to_refs[T](v: Iterable[Ref[T] | SdmxUrn[T]]) -> tuple[Ref[T], ...]:
    """`to_ref` over a sequence field, stored as a tuple."""
    return tuple(to_ref(x) for x in v)
