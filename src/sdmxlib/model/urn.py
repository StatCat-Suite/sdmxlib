"""SdmxUrn[T] — typed artefact identity.

T is a phantom type parameter — no field stores it. It exists so the type
checker can carry the target type through ``reg.fetch(urn) -> T``.
"""

import functools
import re
from typing import ClassVar, Generic, Self, TypeVar

from attrs import field, frozen

from sdmxlib.model.errors import SdmxParseError

# Covariant because ``T`` is a pure phantom (no field stores it, never a
# parameter position), so ``SdmxUrn[Sub]`` is safely a ``SdmxUrn[Base]`` — e.g.
# a ``SdmxUrn[Dataflow]`` where a consumer wants ``SdmxUrn[object]``. PEP 695
# has no variance syntax, so this is the one place we use an explicit TypeVar.
T_co = TypeVar("T_co", covariant=True)

# urn:sdmx:org.sdmx.infomodel.<package>.<Class>=<AGENCY>:<ID>(<version>)
# optional item suffix: .<item_id>
_URN_RE = re.compile(
    r"urn:sdmx:org\.sdmx\.infomodel\.(?P<package>[^.]+)\.(?P<artefact_class>[^=]+)"
    r"=(?P<agency>[^:]+):(?P<id>[^.(]+)(?:\((?P<version>[^)]+)\))?(?:\.(?P<item_id>.+))?$"
)

# SDMX 2.1 / 3.0 formal type patterns from SDMXCommonReferences.xsd
# IDType:       [A-Za-z0-9_@$-]+
# NestedIDType: [A-Za-z0-9_@$-]+(\.[A-Za-z0-9_@$-]+)*   (dots for agency hierarchy)
_PACKAGE_RE = re.compile(r"^[a-z][a-z_]*$")
_CLASS_RE = re.compile(r"^[A-Z][A-Za-z]+$")
_AGENCY_RE = re.compile(r"^[A-Za-z0-9_@$-]+(\.[A-Za-z0-9_@$-]+)*$")
_ID_RE = re.compile(r"^[A-Za-z0-9_@$-]+$")


@frozen
# UP046 wants `class SdmxUrn[T_co]`. Measured, not assumed (#201): under PEP 695
# the two checkers *disagree*, because `T_co` is a pure phantom — it appears
# nowhere in the class body, so variance inference has nothing to infer from and
# each tool falls back differently. basedpyright infers covariant; pyrefly infers
# invariant and then rejects `SdmxUrn[Codelist]` where `SdmxUrn[object]` is
# wanted — the exact assignment 9234d27 made covariant to fix a real defect.
# An explicit covariant TypeVar is the only spelling both gates accept.
class SdmxUrn(Generic[T_co]):  # noqa: UP046 — see above; verified against both checkers
    """Globally unique identifier for an SDMX artefact.

    ``T`` is a phantom type — not stored at runtime, used by the type
    checker to propagate the target type through
    ``reg.fetch(urn: SdmxUrn[T]) -> T``.

    Example:
        SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ(1.0)")
        SdmxUrn.make(
            Codelist,
            package="codelist",
            artefact_class="Codelist",
            agency="SDMX",
            id="CL_FREQ",
            version="1.0",
        )
    """

    _PACKAGE_PREFIX: ClassVar[str] = "urn:sdmx:org.sdmx.infomodel"

    package: str
    artefact_class: str
    agency: str
    id: str
    version: str = "latest"
    item_id: str | None = None
    # Lazy cache for __str__ (issue #117). The URN is frozen so the string
    # is invariant for the lifetime of the object. Skipped from eq/repr/init.
    _str_cache: str | None = field(init=False, default=None, eq=False, repr=False)

    # ── Factories ─────────────────────────────────────────────────────────

    @classmethod
    def parse(cls, urn: str) -> SdmxUrn[object]:
        """Parse a full URN string, naming no particular artefact type.

        `SdmxUrn[object]`: a URN string can name anything, so nothing
        downstream may assume what. `get(SdmxUrn.parse(text))` answers
        `object | None` and the caller narrows, or passes `expect=`. A caller
        that *knows* the type — a reader filling a `Ref[Concept]` from a stored
        string — parses on the class instead: `Concept.parse_urn(text)`, which
        is `SdmxUrn[Concept]` (see `ParsesUrn`).

        It was `SdmxUrn[Any]` until #399, so that those readers' refs could
        become whatever `Ref[T]` they were assigned to. The same `Any` made
        `get(SdmxUrn.parse(text))` answer a confident, wrong `LazyCodelist`
        under basedpyright and an unchecked `Any` under pyrefly.
        """
        m = _URN_RE.match(urn)
        if not m:
            msg = f"Invalid SDMX URN: {urn!r}"
            # A `SdmxParseError` (which is a `ValueError`, so direct callers
            # passing user input are unaffected) because a document carrying
            # this string is a document that cannot be read — and every
            # cross-artefact edge in SDMX is a URN, so the readers hit this
            # constantly on malformed input (#247).
            raise SdmxParseError(msg)
        # One `group()` returning a tuple, not six returning scalars. Same
        # groups, same order, same values — but `parse` is on the hot path of
        # every reader and of `LazyHierarchy.materialize`, where six calls per
        # URN became 3.0M `re.Match.group` calls for one 499k-node hierarchy
        # (#154).
        package, artefact_class, agency, id_, version, item_id = m.group(
            "package", "artefact_class", "agency", "id", "version", "item_id"
        )
        return cls(
            package=package,
            artefact_class=artefact_class,
            agency=agency,
            id=id_,
            version=version or "latest",
            item_id=item_id,
        )

    @classmethod
    def make[U](
        cls,
        artefact_type: type[U],  # noqa: ARG003  # phantom type witness; unused at runtime
        /,
        *,
        package: str,
        artefact_class: str,
        agency: str,
        id: str,
        version: str = "latest",
        item_id: str | None = None,
    ) -> SdmxUrn[U]:
        """Low-level factory with basic validation.

        ``artefact_type`` is a phantom type witness — unused at runtime,
        used by the type checker to bind ``U`` and propagate the artefact
        type through ``session.get`` and ``session.extract``.
        """
        # `SdmxParseError` rather than a bare `ValueError` for the same reason
        # as `parse` above: readers reach this with ids taken straight out of a
        # document, so a bad one means the document is unreadable (#247).
        if not _AGENCY_RE.match(agency):
            msg = f"Invalid agency id: {agency!r}"
            raise SdmxParseError(msg)
        if not _ID_RE.match(id):
            msg = f"Invalid artefact id: {id!r}"
            raise SdmxParseError(msg)
        return cls(
            package=package,
            artefact_class=artefact_class,
            agency=agency,
            id=id,
            version=version,
            item_id=item_id,
        )

    @classmethod
    def parse_item(cls, urn: str) -> SdmxUrn[object]:
        """Parse an *item* URN, reusing the scheme half across a batch.

        Every item URN in one scheme shares everything but its `item_id`, and
        the readers parse them in batches of hundreds of thousands — 499,138
        on the 2021 Census Profile's `H_CP_GEO_FED`, one per node. Re-running the full
        regex for each is re-deriving a constant.

        This splits at `")."`, caches the scheme half, and builds the rest
        directly: **0.85 us to 0.55 us**, a 1.53x saving measured over 500,000
        URNs drawn from two interleaved codelists (so a single hoisted prefix
        would not have been enough).

        Falls back to `parse` for anything without that separator — a
        versionless or non-item URN — so the result is `parse`'s for every
        input, which `tests/unit/test_urn_parse_item.py` checks differentially.

        The cache is keyed on the scheme prefix, so it holds one small tuple
        per codelist or concept scheme in play. Bounding it by *count* is right
        here for the reason it is wrong in #386: the entries are uniform and
        tiny.
        """
        return _from_fields(_item_fields(urn))

    # ── String representation ──────────────────────────────────────────────

    def __str__(self) -> str:
        """Round-trip to full URN string. Cached on first call (#117)."""
        cached = self._str_cache
        if cached is not None:
            return cached
        base = f"{self._PACKAGE_PREFIX}.{self.package}.{self.artefact_class}={self.agency}:{self.id}"
        if self.version and self.version != "latest":
            base = f"{base}({self.version})"
        if self.item_id:
            base = f"{base}.{self.item_id}"
        # @frozen blocks attribute assignment; bypass via object.__setattr__
        # since the cache is invariant for the lifetime of this URN.
        object.__setattr__(self, "_str_cache", base)
        return base

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def is_latest(self) -> bool:
        """True if version is the sentinel "latest"."""
        return self.version == "latest"

    @property
    def is_item_urn(self) -> bool:
        """True if this URN points to an item within a scheme (e.g. a Code)."""
        return self.item_id is not None


type _Fields = tuple[str, str, str, str, str, str | None]


def _item_fields(urn: str) -> _Fields:
    """`(package, artefact_class, agency, id, version, item_id)` — `parse_item`'s fast path.

    Splits an item URN at `")."` and reuses the cached scheme half; anything
    without that separator goes through `parse`, so the fields are `parse`'s
    for every input (`tests/unit/test_urn_parse_item.py` checks it).
    """
    head, separator, item_id = urn.partition(").")
    if not separator:
        full = SdmxUrn.parse(urn)
        return full.package, full.artefact_class, full.agency, full.id, full.version, full.item_id
    package, artefact_class, agency, resource_id, version = _scheme_parts(head + separator)
    return package, artefact_class, agency, resource_id, version, item_id


def _from_fields[T](fields: _Fields) -> SdmxUrn[T]:
    """One `SdmxUrn`, its phantom taken from where the result is used."""
    package, artefact_class, agency, resource_id, version, item_id = fields
    return SdmxUrn(
        package=package,
        artefact_class=artefact_class,
        agency=agency,
        id=resource_id,
        version=version,
        item_id=item_id,
    )


class ParsesUrn:
    """`<Class>.parse_urn(text)`: parse a URN string as naming *this* class (#399).

    The parsing half of `urn_for`, and typed the same way — the class is the
    witness, so `Concept.parse_urn(text)` is `SdmxUrn[Concept]` and
    `Ref.unresolved(Concept.parse_urn(text))` is a `Ref[Concept]`. That is what
    the readers need when they rebuild a typed reference from a stored string,
    and what `SdmxUrn.parse` stopped supplying when it became `SdmxUrn[object]`.

    A mixin rather than a parameter on `SdmxUrn.parse`: a `kind=` argument
    would be a type witness the function never reads, which ruff rightly calls
    unused. Here `cls` is the witness and is never unused. Items share no base
    class, so each one a reader needs inherits this alongside
    `MaintainableArtefact`.

    Not checked against the URN's class at runtime: the readers check a
    reference's class against its slot where they build it, and pick the class
    to parse with from the URN where a slot admits two — an enumeration may be a
    `Codelist` or a `ValueList` (`representation.enumeration_ref`). Item URNs
    take `parse_item`'s fast path — the readers parse them by the hundred
    thousand (#154).
    """

    __slots__ = ()

    @classmethod
    def parse_urn(cls, urn: str) -> SdmxUrn[Self]:
        """`urn` as a `SdmxUrn` naming this class. See `ParsesUrn`."""
        return _from_fields(_item_fields(urn))


@functools.lru_cache(maxsize=512)
def _scheme_parts(prefix: str) -> tuple[str, str, str, str, str]:
    """The scheme half of an item URN, parsed once per distinct scheme.

    Takes the prefix up to and including `")."` and parses it with a throwaway
    item id, so the one implementation of the URN grammar stays `parse`.
    """
    parsed = SdmxUrn.parse(prefix + "_")
    return parsed.package, parsed.artefact_class, parsed.agency, parsed.id, parsed.version or ""
