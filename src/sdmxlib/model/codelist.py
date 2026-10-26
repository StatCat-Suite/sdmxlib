"""Codelist and Code — enumerated value sets."""

import re
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from datetime import datetime
from enum import Enum
from typing import ClassVar, NotRequired, Protocol, Self, Unpack, runtime_checkable

from attrs import Attribute, evolve, field, frozen
from typing_extensions import TypedDict

from sdmxlib._time import as_utc
from sdmxlib.model._invariants import enforce
from sdmxlib.model._items import item_ref
from sdmxlib.model._rules import id_type, maintained_by, named, ncname_id_type, version_type
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.collections import ItemList, to_itemlist
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link, to_links
from sdmxlib.model.organisation import Agency, _to_agency, _to_annotations, _to_istring
from sdmxlib.model.ref import Ref, refuse_urn_text, to_ref
from sdmxlib.model.urn import ParsesUrn


@frozen(unsafe_hash=False)
class Code(ParsesUrn):
    """A single code within a Codelist."""

    #: What the SDMX-ML schema's ``fixed=`` supplies when a producer omits
    #: ``@package`` / ``@class`` on a ``<Ref>`` (#240). Declared on the *item*
    #: class, matching how maintainables carry theirs, so `resolve_ref` can
    #: fill in what the wire left out rather than dropping the reference.
    #:
    #: The pair describes the **item**, not its container: a concept URN is
    #: ``…conceptscheme.Concept=AG:CS(1.0).CONTACT``, so the class is `Concept`
    #: while the id path names the scheme.
    sdmx_package: ClassVar[str] = "codelist"
    sdmx_class: ClassVar[str] = "Code"

    id: str = field(validator=id_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    parent_id: str | None = None
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)


# ── Codelist extension (SDMX 3.0 / 3.1) ───────────────────────────────────────


class InclusionMode(Enum):
    """Whether a ``CodeSelection`` lists codes to include or to exclude.

    SDMX 3.0 / 3.1 ``CodelistExtensionType`` uses a ``<xs:choice>`` between
    ``<InclusiveCodeSelection>`` and ``<ExclusiveCodeSelection>``, both of
    type ``CodeSelectionType``. Semantically the two are one selection with
    a mode flag — we model the flag explicitly so a caller can't
    accidentally populate both branches.
    """

    INCLUDE = "inclusive"
    EXCLUDE = "exclusive"


class CascadeSelection(Enum):
    """How a ``MemberValue`` cascades into a hierarchical codelist.

    The SDMX 3.1 ``CascadeSelectionType`` is a string union of ``xs:boolean``
    plus the literal ``excluderoot``. Three semantic values; we keep them
    as a small enum.
    """

    #: Include the selected code AND all its descendants.
    INCLUDE_DESCENDANTS = "true"
    #: Include only the selected code itself (the XSD default).
    SELF_ONLY = "false"
    #: Include descendants but NOT the selected code itself.
    EXCLUDE_ROOT = "excluderoot"


@frozen(unsafe_hash=False)
class MemberValue:
    """A single code reference in a ``CodeSelection``.

    The ``value`` is a code id within the referenced (extended) codelist,
    optionally containing the ``%`` wildcard character per
    ``WildcardedMemberValueType``. ``cascade`` is the per-member cascade
    behaviour; ``None`` means accept the XSD default (``SELF_ONLY``).
    """

    value: str
    cascade: CascadeSelection | None = None


@frozen(unsafe_hash=False)
class CodeSelection:
    """An ordered list of code references with an inclusion mode.

    Maps to the SDMX 3.0 / 3.1 ``CodeSelectionType``. Wrapping the mode
    on the selection (rather than splitting into two parallel fields)
    keeps the XSD's mutual-exclusion invariant from being a runtime
    check the caller has to remember.
    """

    mode: InclusionMode
    members: tuple[MemberValue, ...] = field(factory=tuple, converter=tuple)


@frozen(unsafe_hash=False)
class CodelistExtension:
    """One extended codelist composed into an extending codelist.

    SDMX 3.0 / 3.1 ``CodelistType`` carries a (possibly empty) ordered
    sequence of ``CodelistExtension`` entries. Order matters — the
    XSD documents it as precedence order for conflict resolution.

    An extension contributes either all codes of the source codelist (no
    ``selection``) or a filtered subset (one ``CodeSelection`` with an
    include or exclude mode). The optional ``prefix`` is applied to all
    inherited code ids in the extending codelist, useful when extending
    codelists that would otherwise collide on code ids.

    Not modelled in SDMX-ML 2.1; the 2.1 serializer silently drops this
    field if the extending codelist is also written as 2.1 output.
    """

    codelist: Ref[Codelist] = field(converter=to_ref, validator=refuse_urn_text)
    selection: CodeSelection | None = None
    prefix: str | None = None


@runtime_checkable
class EnumerationLike(Protocol):
    """What a resolved enumeration offers: a codelist, eager or lazy, or a `ValueList`.

    A component's representation may enumerate its values by either kind in
    SDMX 3.x. This is the part both answer — the ids, their labels and
    how many there are — so the label maps and the value checks built on a
    component work whichever it holds. Reach for `CodelistLike` when you need
    `Code` objects, which a value list does not have.
    """

    @property
    def id(self) -> str:
        """The artefact's identifier, unique within its agency and version."""
        ...

    @property
    def version(self) -> str:
        """The artefact's version."""
        ...

    def code_ids(self) -> list[str]:
        """Every code id — or value — in order, without building objects.

        The cheap path on each: a list comprehension over an in-memory
        `ItemList`, or one projection of a single column out of SQL.
        """
        ...

    def code_map(self, *, lang: str) -> dict[str, str]:
        """``{code_id: label}`` in ``lang``, falling back to the id."""
        ...

    def __len__(self) -> int:
        """How many codes, or values, it holds."""
        ...


@runtime_checkable
class CodelistLike(EnumerationLike, Protocol):
    """What a resolved codelist offers, whether it is eager or lazy.

    `Dimension.resolved_codelist` follows the component's `Ref`. When the DSD
    came from a `LocalRegistry` that ref resolves to a `LazyCodelist`, not a
    `Codelist` — so declaring `Codelist` was a lie, and one that reached
    users: `polars.schema`, `polars.validation_report`,
    `polars.structure_frame`, `Dataset.pivot` and `Dataset.label` all raised
    `TypeError` on a DSD read from a local registry, because they iterated or
    took `len()` of something that had become a `Query`.

    The two classes already shared 28 public members. This declares the part
    callers use, and the gaps it exposed were closed rather than narrowed
    around: `Codelist` gained `code_ids`, `code_map`, `materialize` and
    `__len__`, each of which `LazyCodelist` already had in substance. So there
    is no `isinstance` branch anywhere — every member answers on its own
    cheapest path, a SQL projection on one side and a comprehension on the
    other.

    Materialising instead was measured and rejected: callers want ids or a
    count, and on `CL_GEOGRAPHY` those cost 92 ms and 22 ms against 2,854 ms
    to materialise — 31x and 129x. Materialising survives as `materialize()`,
    for the two callers that genuinely need each
    `Code` and should say so in one visible call.

    **Methods rather than the fields they wrap, which is not a style choice.**
    `code_ids()` exists because a protocol member declared as
    ``codes: ItemList[Code] | Query[...]`` does not check: basedpyright reads
    `Codelist.codes` as `ItemList[Code]` for attribute access and as
    `__converterDescriptor_codes` for protocol matching, so the same member
    satisfies one and not the other. `name` and `description` are absent for
    the identical reason and no other — both are real, both are on both
    classes, and neither can be declared here until that is fixed. Minimal
    repro in `tests/typing/probe_codelist_like.py`; reach for the concrete
    class meanwhile.
    """

    def codes_for(self, ids: Iterable[str]) -> dict[str, Code]:
        """``{code_id: Code}`` for exactly *ids*, each fully built.

        Ids the codelist does not hold are absent from the result rather than
        an error — a caller resolving observed values meets values outside the
        enumeration, and the id is then its own label.

        The targeted counterpart to `materialize`: one batched read on a lazy
        codelist, whose cost follows ``len(ids)`` rather than the codelist. The
        SDMX-JSON data writer called `materialize` per component per message
        and paid for every code in a 579,951-code geography to label the
        handful a response observed.
        """
        ...

    @classmethod
    def codes_for_each(cls, requests: Sequence[tuple[Self, Collection[str]]]) -> list[dict[str, Code]]:
        """`codes_for` for several codelists of this class at once, answered in request order.

        A lazy codelist answers them in one read per store, where asking each
        in turn cost three statements apiece. `codes_for_all` groups a
        caller's requests by class and asks each class once.
        """
        ...

    def partial(self, code_ids: Iterable[str]) -> Codelist:
        """This codelist cut down to *code_ids* and their ancestors, flagged ``is_partial``.

        What SDMX-REST returns for a codelist a query uses only part of:
        ``detail=referencepartial`` keeps the codes a dataflow's constraints
        name, and ``/availability`` references keep the codes with data, with
        their parent codes. Every kept code brings its ancestors, so each
        ``parent_id`` names a code in the result. Codes keep their order.

        Raises:
            KeyError: If an id is not a code of this codelist, naming it.
            TypeError: If *code_ids* is a single string, which would be read
                as its characters.
        """
        ...

    def materialize(self) -> Codelist:
        """This codelist with every `Code` built, for callers needing more.

        Free on an eager `Codelist`, which returns itself; a full read on a
        lazy one, whose cost is the whole codelist. A caller that needs some
        codes' annotations or ``parent_id`` rather than all of them wants
        `codes_for` instead — that was the SDMX-JSON data writer.
        """
        ...


@frozen(unsafe_hash=False)
class Codelist(MaintainableArtefact):
    """A finite set of valid codes for an enumerated component.

    Constructor accepts convenient Python types alongside the strict stored
    types — ``dict[str, str]`` for name/description, ``list[Code]`` for
    codes. After construction the stored types are always strict.

    Example:
        cl = Codelist(
            id="CL_FREQ", maintainer="SDMX",
            name={"en": "Frequency"},
            codes=[Code(id="A", name={"en": "Annual"})],
        )
        cl.codes["A"]      # Code by id
        cl.roots()         # top-level codes
        cl.narrower("A")   # direct children of code A (SKOS-aligned)
    """

    sdmx_package: ClassVar[str] = "codelist"
    sdmx_class: ClassVar[str] = "Codelist"
    msg_field: ClassVar[str] = "codelists"

    id: str = field(validator=ncname_id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    #: SDMX 2.1's `isFinal`, kept only where it disagrees with the version
    #: (see `MaintainableArtefact.is_final`). Only the 2.1 reader sets it.
    legacy_is_final: bool | None = None
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    valid_from: datetime | None = field(default=None, converter=as_utc)
    valid_to: datetime | None = field(default=None, converter=as_utc)
    codes: ItemList[Code] = field(factory=ItemList, converter=to_itemlist)
    #: 3.0 / 3.1 codelist composition. Each entry references another
    #: codelist whose codes contribute (whole or filtered) to this one.
    #: Order is significant: a later extension takes precedence on a
    #: conflicting code id (IM §4.3.3.2, #486). Not modelled in 2.1.
    extensions: tuple[CodelistExtension, ...] = field(factory=tuple, converter=tuple)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)
    #: SDMX ``@isExternalReference``, ``@serviceURL``, ``@structureURL`` — see
    #: `MaintainableArtefact` (#422).
    is_external_reference: bool = False
    service_url: str | None = None
    structure_url: str | None = None
    #: SDMX 3.1 ``@isPartialLanguage``: some text is missing a language the
    #: rest carries (#422).
    is_partial_language: bool = False
    #: SDMX ``@isPartial``: the scheme holds a subset of its items (#422).
    is_partial: bool = False

    # ── SKOS/XKOS hierarchy navigation ────────────────────────────────────

    def ref_to(self, code_id: str) -> Ref[Code]:
        """Build a resolved `Ref` to a code within this codelist.

        The URN is built from *this* scheme's agency, id and version, so it
        cannot name a parent that does not exist — which is what six hand-written
        keyword arguments to `SdmxUrn.make` invite, and what
        `validate_references` now correctly reports as broken.

        Raises:
            KeyError: If no code with this id is in this codelist.

        Example:
            HierarchicalCode(id="CA", code=cl.ref_to("CA"))
        """
        return item_ref(self, code_id, Code)

    def roots(self) -> ItemList[Code]:
        """Top-level codes (``broader IS NULL``)."""
        return self.codes.filter(lambda c: c.parent_id is None)

    def leaves(self) -> ItemList[Code]:
        """Codes that no other code names as parent."""
        parents = {c.parent_id for c in self.codes if c.parent_id is not None}
        return self.codes.filter(lambda c: c.id not in parents)

    def narrower(self, code_id: str) -> ItemList[Code]:
        """Direct children (SKOS narrower) of ``code_id``."""
        return self.codes.filter(lambda c: c.parent_id == code_id)

    def broader(self, code_id: str) -> Code | None:
        """Direct parent (SKOS broader) of ``code_id``. ``None`` for roots."""
        target = self.codes.get(code_id)
        if target is None or target.parent_id is None:
            return None
        return self.codes.get(target.parent_id)

    def narrower_transitive(
        self, code_id: str, *, depth: int | None = None, include_self: bool = False
    ) -> ItemList[Code]:
        """All descendants of ``code_id``. ``depth`` bounds the hop count.

        ``include_self`` defaults off because this is a *closure*, and SKOS
        specifies ``narrowerTransitive`` as non-reflexive. It matches
        `LazyCodelist.narrower_transitive`, which is the same walk over a
        store.
        """
        result: list[Code] = []
        if include_self:
            start = self.codes.get(code_id)
            if start is not None:
                result.append(start)
        frontier: list[tuple[str, int]] = [(code_id, 0)]
        seen: set[str] = {code_id}
        by_parent: dict[str, list[Code]] = {}
        for c in self.codes:
            if c.parent_id is not None:
                by_parent.setdefault(c.parent_id, []).append(c)
        while frontier:
            current, hops = frontier.pop(0)
            if depth is not None and hops >= depth:
                continue
            for child in by_parent.get(current, []):
                if child.id in seen:
                    continue
                seen.add(child.id)
                result.append(child)
                frontier.append((child.id, hops + 1))
        return ItemList(result)

    def broader_transitive(
        self, code_id: str, *, depth: int | None = None, include_self: bool = False
    ) -> ItemList[Code]:
        """All ancestors of ``code_id``. ``depth`` bounds the hop count.

        ``include_self`` as in `narrower_transitive`: off by default, and the
        node itself comes first when asked for, so the result stays
        leaf-to-root.
        """
        result: list[Code] = []
        current = self.codes.get(code_id)
        if current is None:
            return ItemList(result)
        if include_self:
            result.append(current)
        hops = 0
        while current is not None and current.parent_id is not None:
            if depth is not None and hops >= depth:
                break
            parent = self.codes.get(current.parent_id)
            if parent is None:
                break
            result.append(parent)
            current = parent
            hops += 1
        return ItemList(result)

    def depth_of(self, code_id: str) -> int:
        """Depth of ``code_id`` in the hierarchy (root = 0)."""
        current = self.codes.get(code_id)
        if current is None:
            msg = f"Code {code_id!r} not in codelist {self.id!r}"
            raise KeyError(msg)
        depth = 0
        while current.parent_id is not None:
            parent = self.codes.get(current.parent_id)
            if parent is None:
                break
            current = parent
            depth += 1
        return depth

    # ── Bulk projections ──────────────────────────────────────────────────

    def depths(self) -> dict[str, int]:
        """``{code_id: depth}`` for every code (root = 0)."""
        by_id = {c.id: c for c in self.codes}
        cache: dict[str, int] = {}

        def _depth(cid: str) -> int:
            if cid in cache:
                return cache[cid]
            c = by_id.get(cid)
            if c is None or c.parent_id is None:
                cache[cid] = 0
            else:
                cache[cid] = _depth(c.parent_id) + 1
            return cache[cid]

        return {c.id: _depth(c.id) for c in self.codes}

    def code_ids(self) -> list[str]:
        """Every code id, in order.

        The eager half of `CodelistLike.code_ids`; on a `LazyCodelist` the same
        call projects one column out of SQL rather than building 579,951
        `Code` objects to read their ids.
        """
        return self.codes.ids()

    def codes_for(self, ids: Iterable[str]) -> dict[str, Code]:
        """``{code_id: Code}`` for exactly *ids*; unknown ids are absent.

        The eager half of `CodelistLike.codes_for` — an `ItemList.get` per id.
        """
        return {cid: code for cid in ids if (code := self.codes.get(cid)) is not None}

    @classmethod
    def codes_for_each(cls, requests: Sequence[tuple[Self, Collection[str]]]) -> list[dict[str, Code]]:
        """The eager half of `CodelistLike.codes_for_each`: each `codes_for` in turn, there being nothing to batch."""
        return [codelist.codes_for(ids) for codelist, ids in requests]

    def materialize(self) -> Codelist:
        """This codelist — it is already materialised.

        The eager half of `CodelistLike.materialize`. Returning ``self`` rather
        than a copy: the protocol promises the codes are built, not that the
        result is fresh, and copying a 579,951-code codelist to honour a call
        that changes nothing would be the cost this whole issue is about.
        """
        return self

    def expand_wildcards(self, available: Iterable[Codelist] = ()) -> Codelist:
        """This codelist with each wildcarded member value of its extensions spelled out.

        SDMX-ML 3.x lets a code selection name codes by pattern: ``A%`` is
        every code of the extended codelist whose id starts with ``A``.
        SDMX-JSON's member value is a plain id, so a pattern is written as
        the ids it matches now, each with the member's cascade. A code added
        to the extended codelist later is then not selected.

        Args:
            available: Codelists to find an extended codelist in when the
                extension's reference is not resolved — a message's own,
                say.

        Returns:
            A new codelist, or this one when no member value is wildcarded.

        Raises:
            LookupError: If a wildcarded member cannot be expanded;
                `wildcard_misfits` says why in advance.
        """
        if not any(_wildcarded(extension) for extension in self.extensions):
            return self
        if misfits := wildcard_misfits(self, available):
            msg = f"cannot expand the wildcarded member values of {self.urn}: {'; '.join(misfits)}"
            raise LookupError(msg)
        by_urn = {str(cl.urn): cl for cl in available}
        return evolve(self, extensions=tuple(_expanded(ext, by_urn) for ext in self.extensions))

    def apply_extensions(self, available: Iterable[Codelist] = ()) -> Codelist:
        """This codelist with the codes its extensions inherit, and no extensions.

        SDMX 3.x lets a codelist extend others: each `CodelistExtension`
        contributes the extended codelist's codes, all of them or a selection,
        each id behind an optional prefix. This is the codelist a reader of
        the 3.x document means — what SDMX-ML 2.1, which has no extensions,
        has to be given instead.

        Precedence follows the IM (§4.3.3.2: "the latest Codelist used
        overrides any previous Codelist") and FMR, which flattens the same
        way: the codelist's own codes win over inherited ones, and a
        later extension over an earlier one. An inherited code keeps the place
        it first appeared in. A selected code whose parent was not selected becomes a
        root, as in the extending codelist itself. An extended codelist that
        extends others has its own extensions applied first.

        Args:
            available: Codelists to find an extended codelist in when the
                extension's reference is not resolved — a message's own,
                say. A resolved reference is used as it stands.

        Returns:
            A new codelist, or this one when it extends nothing.

        Raises:
            LookupError: If an extended codelist is neither resolved nor in
                `available`; `unresolved_extensions` names each in advance.
            ValueError: If the extensions form a cycle.
            TypeError: If a geographic codelist would inherit codes of another
                kind, which 3.x allows and the class cannot hold. SDMX-ML 2.1
                writes such a codelist as a plain one, where they can.
        """
        if not self.extensions:
            return self
        return _applied(self, {str(cl.urn): cl for cl in available}, frozenset())

    def __len__(self) -> int:
        """How many codes this codelist holds.

        `LazyCodelist` has had this since it existed — it is one `COUNT(*)`
        there rather than a materialised list — and its absence here was the
        reason a caller counting codes had to know which class it held (#398).
        """
        return len(self.codes)

    def code_map(self, *, lang: str) -> dict[str, str]:
        """``{code_id: label}`` in ``lang``, falling back to the id.

        The one member `LazyCodelist` had and this class did not, which is why
        callers wanting labels had to ask which class they were holding — see
        `sdmxlib.model.protocols.CodelistLike`. The lazy side projects
        it straight out of SQL; here it is a dict comprehension over codes that
        are already in memory, so neither needs a cache
        (`docs/design/caching.md`).

        Falls back to the id rather than omitting the code, because a label map
        with holes in it silently drops rows when a caller joins on it.
        """
        return {c.id: c.name.get(lang) or c.id for c in self.codes}

    def broader_map(self) -> dict[str, str | None]:
        """``{code_id: parent_id}`` for every code; ``None`` for roots."""
        return {c.id: c.parent_id for c in self.codes}

    def narrower_map(self) -> dict[str, tuple[str, ...]]:
        """``{code_id: (child_id, ...)}`` for every code, in declaration order."""
        children: dict[str, list[str]] = {c.id: [] for c in self.codes}
        for c in self.codes:
            if c.parent_id is not None and c.parent_id in children:
                children[c.parent_id].append(c.id)
        return {k: tuple(v) for k, v in children.items()}

    def ancestors_map(self) -> dict[str, tuple[str, ...]]:
        """``{code_id: (ancestor_id, ...)}`` root-first, excluding self."""
        by_id = {c.id: c for c in self.codes}
        cache: dict[str, tuple[str, ...]] = {}

        def _anc(cid: str) -> tuple[str, ...]:
            if cid in cache:
                return cache[cid]
            c = by_id.get(cid)
            if c is None or c.parent_id is None:
                cache[cid] = ()
            else:
                cache[cid] = (*_anc(c.parent_id), c.parent_id)
            return cache[cid]

        return {c.id: _anc(c.id) for c in self.codes}

    def descendants_map(self) -> dict[str, tuple[str, ...]]:
        """``{code_id: (descendant_id, ...)}`` in pre-order DFS, excluding self."""
        narrower = self.narrower_map()
        cache: dict[str, tuple[str, ...]] = {}

        def _desc(cid: str) -> tuple[str, ...]:
            if cid in cache:
                return cache[cid]
            out: list[str] = []
            for child_id in narrower.get(cid, ()):
                out.append(child_id)
                out.extend(_desc(child_id))
            cache[cid] = tuple(out)
            return cache[cid]

        return {c.id: _desc(c.id) for c in self.codes}

    def inherit_along_broader[T](self, values: dict[str, T]) -> dict[str, T]:
        """Propagate ``values`` down the parent chain.

        For each code, walk the ``broader`` chain and assign the first
        value encountered. Codes whose chain has no explicit value are
        omitted from the result.

        Example:
            cl.inherit_along_broader({"CA": "country", "ON": "province"})
            # → {"CA": "country", "ON": "province",
            #    "QC": "country",   # QC's chain: QC → CA, picks "country"
            #    "TOR": "province", # TOR's chain: TOR → ON → CA, picks "province"
            #    ...}
        """
        by_id = {c.id: c for c in self.codes}
        result: dict[str, T] = {}
        for c in self.codes:
            cur_id: str | None = c.id
            while cur_id is not None:
                if cur_id in values:
                    result[c.id] = values[cur_id]
                    break
                cur = by_id.get(cur_id)
                cur_id = cur.parent_id if cur is not None else None
        return result

    def add(self, codes: Code | list[Code]) -> Codelist:
        """Return a new Codelist with *codes* added.

        Accepts a single Code or a list of Codes.
        """
        new = [codes] if isinstance(codes, Code) else list(codes)
        return evolve(self, codes=[*self.codes, *new])

    def drop(self, code_ids: str | list[str]) -> Codelist:
        """Return a new Codelist with the given code(s) removed.

        Accepts a single id string or a list of id strings.
        Raises KeyError if any id is not present.
        """
        ids = [code_ids] if isinstance(code_ids, str) else list(code_ids)
        missing = [i for i in ids if i not in self.codes]
        if missing:
            raise KeyError(missing[0] if len(missing) == 1 else missing)
        id_set = set(ids)
        return evolve(self, codes=[c for c in self.codes if c.id not in id_set])

    def partial(self, code_ids: Iterable[str]) -> Codelist:
        """This codelist cut down to *code_ids* and their ancestors, flagged ``is_partial``.

        What SDMX-REST returns for a codelist a query uses only part of:
        ``detail=referencepartial`` keeps the codes a dataflow's constraints
        name, and ``/availability`` references keep the codes with data, with
        their parent codes. Every kept code brings its ancestors, so each
        ``parent_id`` names a code in the result. Codes keep their order.

        The result carries no extensions: it lists its codes. An inherited code
        is a code here only once `apply_extensions` has run.

        Args:
            code_ids: The codes to keep.

        Returns:
            A new `Codelist` of the kept codes, with ``is_partial`` set.

        Raises:
            KeyError: If an id is not a code of this codelist, naming it.
            TypeError: If *code_ids* is a single string, which would be read
                as its characters.
        """
        asked = partial_ids(code_ids)
        by_id = {code.id: code for code in self.codes}
        unknown = sorted(asked - by_id.keys())
        if unknown:
            raise KeyError(unknown_codes_message(self.urn, unknown, extended=bool(self.extensions)))
        kept: set[str] = set()
        for code_id in asked:
            current: str | None = code_id
            while current is not None and current not in kept and current in by_id:
                kept.add(current)
                current = by_id[current].parent_id
        return evolve(self, codes=[c for c in self.codes if c.id in kept], is_partial=True, extensions=())

    # ── Geographic codelists (SDMX 3.0 / 3.1) ─────────────────────────────────────

    def __attrs_post_init__(self) -> None:
        # Its whole-artefact rules, now that it is whole (#492).
        enforce(self)


@frozen(unsafe_hash=False)
class GeoFeatureSetCode(Code):
    """A code standing for a set of geographic features (SDMX 3.x).

    Its URN is a plain `Code`'s: SDMX gives geographic codes no URN class of
    their own, so this is a `Code` with one more field rather than a new kind
    of item.
    """

    #: The feature set, in whatever pattern the codelist's producer uses (a
    #: WKT geometry, a list of identifiers). Required by the XSD.
    value: str = field(kw_only=True)


@frozen(unsafe_hash=False)
class GeoGridCode(Code):
    """A code standing for one cell of a geographic grid (SDMX 3.x).

    A `Code` with the cell it stands for; see `GeoFeatureSetCode` for why it
    is not its own kind of item.
    """

    #: The value assigning the code to one cell of its codelist's
    #: `GeoGridCodelist.grid_definition`. Required by the XSD.
    geo_cell: str = field(kw_only=True)


def _codes_of(kind: type[Code]) -> Callable[[object, Attribute[ItemList[Code]], ItemList[Code]], None]:
    """An attrs validator: every code is a `kind`.

    The codes stay typed `ItemList[Code]` rather than narrowing to `kind` on the
    subclass: a caller holding the instance as a `Codelist` could `attrs.evolve`
    plain codes into it. So the class is checked here instead, on construction
    and on `attrs.evolve` (the model is frozen, #492).
    """

    def check(_instance: object, attribute: Attribute[ItemList[Code]], value: ItemList[Code]) -> None:
        wrong = [code.id for code in value if not isinstance(code, kind)]
        if wrong:
            msg = f"{attribute.name} must all be {kind.__name__}; not {', '.join(map(repr, wrong[:5]))}"
            raise TypeError(msg)

    return check


@frozen(unsafe_hash=False)
class GeographicCodelist(Codelist):
    """A codelist of geographic feature sets (SDMX 3.x).

    A `Codelist` in every respect SDMX gives it — its URN, its references, the
    components it enumerates — whose codes are each a `GeoFeatureSetCode`.
    SDMX-ML 2.1 has no such class and writes it as a plain codelist; see
    `sdmxlib.unrepresentable`.

    Example:
        GeographicCodelist(
            id="CL_REGION", maintainer="ESTAT", name={"en": "Regions"},
            codes=[GeoFeatureSetCode(id="BE1", value="POLYGON((...))")],
        )
    """

    codes: ItemList[Code] = field(factory=ItemList, converter=to_itemlist, validator=_codes_of(GeoFeatureSetCode))


@frozen(unsafe_hash=False)
class GeoGridCodelist(Codelist):
    """A codelist of geographic grid cells (SDMX 3.x).

    A `Codelist`, as `GeographicCodelist` is, whose codes are each a
    `GeoGridCode` and which defines the grid they divide.

    Example:
        GeoGridCodelist(
            id="CL_GRID", maintainer="ESTAT", name={"en": "1 km grid"},
            grid_definition="EPSG:3035;1km",
            codes=[GeoGridCode(id="C1", geo_cell="1kmN2599E4498")],
        )
    """

    #: The grid's definition: a regular expression, per the XSD. Required.
    grid_definition: str = field(kw_only=True)
    codes: ItemList[Code] = field(factory=ItemList, converter=to_itemlist, validator=_codes_of(GeoGridCode))


class CodeFields(TypedDict, closed=True):
    """`Code`'s fields, which every geographic code shares — for `code_of_kind`."""

    id: str
    name: NotRequired[InternationalString]
    description: NotRequired[InternationalString]
    parent_id: NotRequired[str | None]
    annotations: NotRequired[Annotations]
    uri: NotRequired[str | None]
    links: NotRequired[tuple[Link, ...]]


def code_of_kind(*, value: str | None, geo_cell: str | None, **fields: Unpack[CodeFields]) -> Code:
    """The code a reader rebuilds from a flat record: its class is which geographic field is set.

    Args:
        value: A `GeoFeatureSetCode`'s feature set, or None.
        geo_cell: A `GeoGridCode`'s cell, or None.
        **fields: The fields every code has.

    Returns:
        A `GeoFeatureSetCode`, a `GeoGridCode`, or — with neither set — a `Code`.
    """
    if value is not None:
        return GeoFeatureSetCode(**fields, value=value)
    if geo_cell is not None:
        return GeoGridCode(**fields, geo_cell=geo_cell)
    return Code(**fields)


#: `CodelistLike.codes_for_each`, bound to a codelist's class.
type _CodesForEach = Callable[[Sequence[tuple[CodelistLike, Collection[str]]]], list[dict[str, Code]]]


def partial_ids(code_ids: Iterable[str]) -> set[str]:
    """The ids `partial` keeps, refusing a bare string, which iterates as its characters."""
    if isinstance(code_ids, str):
        msg = f"partial() takes code ids, not one string: write partial([{code_ids!r}])"
        raise TypeError(msg)
    return set(code_ids)


def unknown_codes_message(codelist: object, unknown: Sequence[str], *, extended: bool) -> str:
    """Why `partial` refused: the ids *codelist* does not hold, the first ten named."""
    shown = ", ".join(unknown[:10]) + (f", and {len(unknown) - 10} more" if len(unknown) > 10 else "")
    hint = "; it inherits codes through extensions, so apply them first" if extended else ""
    return f"{len(unknown)} id(s) are not codes of {codelist}: {shown}{hint}"


def codes_for_all[K](requests: Mapping[K, tuple[CodelistLike, Collection[str]]]) -> dict[K, dict[str, Code]]:
    """Each request's `CodelistLike.codes_for`, asking each codelist class once for all of its own (#441).

    For lazy codelists that is one read for every component of a message
    rather than three statements per component; for eager ones it is the same
    `codes_for` as before.

    Args:
        requests: Any key → the codelist and the ids wanted from it.

    Returns:
        The same keys → ``{code_id: Code}``; ids a codelist does not hold are absent.
    """
    # Keyed by each codelist's bound `codes_for_each`: a classmethod bound
    # through any instance of one class is equal to it bound through another,
    # so this groups by class, and each group's key is what answers it.
    by_class: defaultdict[_CodesForEach, list[K]] = defaultdict(list)
    for key, (codelist, _) in requests.items():
        by_class[codelist.codes_for_each].append(key)
    found: dict[K, dict[str, Code]] = {}
    for codes_for_each, keys in by_class.items():
        found.update(zip(keys, codes_for_each([requests[key] for key in keys]), strict=True))
    return found


class CodelistFields(TypedDict, closed=True):
    """`Codelist`'s fields, which both geographic kinds share — for `codelist_of_kind`."""

    id: str
    maintainer: Agency
    version: str
    name: InternationalString
    description: InternationalString
    legacy_is_final: bool | None
    annotations: Annotations
    valid_from: datetime | None
    valid_to: datetime | None
    codes: ItemList[Code]
    extensions: tuple[CodelistExtension, ...]
    uri: str | None
    links: tuple[Link, ...]
    is_external_reference: bool
    service_url: str | None
    structure_url: str | None
    is_partial_language: bool
    is_partial: bool


def codelist_of_kind(geo_type: str | None, grid_definition: str | None, **fields: Unpack[CodelistFields]) -> Codelist:
    """The codelist a reader rebuilds from a flat record, of the class `geo_type` names.

    Args:
        geo_type: ``"GeographicCodelist"``, ``"GeoGridCodelist"``, or None for a
            plain codelist — SDMX's own ``geoType`` values.
        grid_definition: A `GeoGridCodelist`'s grid definition.
        **fields: The fields every codelist has.

    Returns:
        The codelist.

    Raises:
        ValueError: If `geo_type` names neither geographic kind.
    """
    match geo_type:
        case None:
            return Codelist(**fields)
        case "GeographicCodelist":
            return GeographicCodelist(**fields)
        case "GeoGridCodelist":
            return GeoGridCodelist(**fields, grid_definition=grid_definition or "")
        case _:
            msg = f"unknown geographic codelist type {geo_type!r}"
            raise ValueError(msg)


# ── Codelist extension, applied (#421) ────────────────────────────────────────


def extended_codelist(extension: CodelistExtension, available: Mapping[str, Codelist]) -> Codelist | None:
    """The codelist `extension` extends: its resolved reference, else `available`'s by URN, else None."""
    if extension.codelist.resolved:
        return extension.codelist().materialize()
    return available.get(str(extension.codelist.urn))


def wildcard_misfits(codelist: Codelist, available: Iterable[Codelist] = ()) -> list[str]:
    """Why `Codelist.expand_wildcards` cannot spell out `codelist`'s wildcarded members, if it cannot (#451).

    Writers and validation ask this of every codelist in a message, each time
    with all of the message's codelists as `available`, so the index of them
    is built only for a codelist that has a wildcard to look up. Built for
    every one, it made a JSON write quadratic in the codelist count (#462).

    Args:
        codelist: The codelist whose extensions are checked.
        available: Codelists to find an extended codelist in when the
            extension's reference is not resolved.

    Returns:
        One reason per member value that cannot be expanded; empty when every
        one can, or none is wildcarded.
    """
    wildcarded = [extension for extension in codelist.extensions if _wildcarded(extension)]
    if not wildcarded:
        return []
    by_urn = {str(cl.urn): cl for cl in available}
    misfits: list[str] = []
    for extension in wildcarded:
        extended = _expandable_source(extension, by_urn)
        if extended is None:
            misfits.append(f"{extension.codelist.urn} is neither resolved nor among the codelists given")
            continue
        misfits.extend(
            f"member {member.value!r} matches no code of {extension.codelist.urn}"
            for member in _members(extension)
            if "%" in member.value and not _matching(extended, member.value)
        )
    return misfits


def _members(extension: CodelistExtension) -> tuple[MemberValue, ...]:
    """An extension's member values, none when it selects every code."""
    return () if extension.selection is None else extension.selection.members


def _wildcarded(extension: CodelistExtension) -> bool:
    return any("%" in member.value for member in _members(extension))


def _expandable_source(extension: CodelistExtension, available: Mapping[str, Codelist]) -> Codelist | None:
    """The codes a wildcard is matched against: the extended codelist's, its own extensions applied."""
    extended = extended_codelist(extension, available)
    if extended is None:
        return None
    if extended.extensions and unresolved_extensions(extended, available.values()):
        return None
    return extended.apply_extensions(available.values()) if extended.extensions else extended


def _matching(source: Codelist, value: str) -> list[str]:
    pattern = re.compile(".*".join(re.escape(part) for part in value.split("%")))
    return [code.id for code in source.codes if pattern.fullmatch(code.id)]


def _expanded(extension: CodelistExtension, available: Mapping[str, Codelist]) -> CodelistExtension:
    if extension.selection is None or not _wildcarded(extension):
        return extension
    source = _expandable_source(extension, available)
    if source is None:  # `wildcard_misfits` has refused this already
        return extension
    members: list[MemberValue] = []
    for member in extension.selection.members:
        values = _matching(source, member.value) if "%" in member.value else [member.value]
        members.extend(evolve(member, value=value) for value in values)
    unique = list(dict.fromkeys((m.value, m.cascade) for m in members))
    return evolve(
        extension,
        selection=evolve(extension.selection, members=tuple(MemberValue(value=v, cascade=c) for v, c in unique)),
    )


def unresolved_extensions(codelist: Codelist, available: Iterable[Codelist] = ()) -> list[str]:
    """The URN of every codelist `codelist` extends, directly or not, that cannot be found.

    What `Codelist.apply_extensions` would raise `LookupError` for, asked
    before anything is written.
    """
    by_urn = {str(cl.urn): cl for cl in available}
    missing: list[str] = []
    seen: set[str] = set()
    pending = [codelist]
    while pending:
        current = pending.pop()
        for extension in current.extensions:
            urn = str(extension.codelist.urn)
            if urn in seen:
                continue
            seen.add(urn)
            found = extended_codelist(extension, by_urn)
            if found is None:
                missing.append(urn)
            else:
                pending.append(found)
    return missing


def _applied(codelist: Codelist, available: Mapping[str, Codelist], within: frozenset[str]) -> Codelist:
    urn = str(codelist.urn)
    if urn in within:
        msg = f"codelist {urn} extends itself through its extensions"
        raise ValueError(msg)
    own = {code.id for code in codelist.codes}
    codes: dict[str, Code] = {code.id: code for code in codelist.codes}
    for extension in codelist.extensions:
        extended = extended_codelist(extension, available)
        if extended is None:
            msg = (
                f"{urn} extends {extension.codelist.urn}, which is neither resolved nor among the codelists given; "
                "resolve the reference or pass the extended codelist in `available`"
            )
            raise LookupError(msg)
        source = _applied(extended, available, within | {urn}) if extended.extensions else extended
        # A later extension replaces an earlier one's code, keeping its place.
        for code in _inherited(source, extension):
            if code.id not in own:
                codes[code.id] = code
    return evolve(codelist, codes=ItemList(list(codes.values())), extensions=())


def _inherited(source: Codelist, extension: CodelistExtension) -> list[Code]:
    """`source`'s codes that `extension` selects, behind its prefix, parents kept only when selected."""
    chosen = _selected(source, extension.selection)
    prefix = extension.prefix or ""
    return [
        evolve(
            code,
            id=prefix + code.id,
            parent_id=prefix + code.parent_id if code.parent_id is not None and code.parent_id in chosen else None,
        )
        for code in source.codes
        if code.id in chosen
    ]


def _selected(source: Codelist, selection: CodeSelection | None) -> set[str]:
    every = [code.id for code in source.codes]
    if selection is None:
        return set(every)
    descendants = source.descendants_map()
    named: set[str] = set()
    for member in selection.members:
        pattern = re.compile(".*".join(re.escape(part) for part in member.value.split("%")))
        for root in (code_id for code_id in every if pattern.fullmatch(code_id)):
            cascade = member.cascade or CascadeSelection.SELF_ONLY
            if cascade is not CascadeSelection.EXCLUDE_ROOT:
                named.add(root)
            if cascade is not CascadeSelection.SELF_ONLY:
                named.update(descendants.get(root, ()))
    return named if selection.mode is InclusionMode.INCLUDE else set(every) - named
