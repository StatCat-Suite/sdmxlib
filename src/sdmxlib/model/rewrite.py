"""Rewriting artefacts' identities, with every reference following them.

Two rewrites share one walk over the model's references:

- `rewrite(msg, mapping)` changes chosen artefacts' URNs — version, id or
  agency — as a 2.1 → 3.x migration does (GH-406), and moves every
  reference, item reference and `Link.urn` naming them.
- `rewrite_versions` rewrites versions only SDMX 2.1 accepts, at ingest,
  described below.

## Versions only SDMX 2.1 accepts (#420, #424)

They are refused at ingest, unless rewritten.

The model holds SDMX 3.x, and 3.x has no spelling for `1.03` or `1.2.3.4`:
its grammar is one or two parts, or exactly three, with no leading zeros
(`model.version`). So an artefact at one of those versions, or a reference
naming one, is refused when it enters a registry — not when it is parsed,
because a 2.1 document read and written back as 2.1 never needs a 3.x form.

The two cases need different fixes, which is why the escape is a rewrite
rather than a flag. `1.03` has exactly one 3.x spelling: the 2.1 schema says it
equals `1.3`, and `strip_leading_zeros` makes that rewrite. `1.2.3.4` has none
— `1.2.3` may exist, and `1.2.34` is a guess — so only the caller can name it,
with a mapping.

A rewrite applies to a **batch**: every artefact in it and every reference
inside it, so a dataflow pointing at `DSD(1.03)` follows the DSD to `1.3`.
Collisions are always an error — two versions rewritten to one, or a
rewritten version the registry already holds from elsewhere — and so is a
reference already in the registry to the old version, which the rewrite
cannot reach and would leave dangling (unless the old version is itself held,
when the reference still answers). Each registry checks the last two
against what it holds; this module does everything that needs only the
batch.

In practice this is rare: the six registries the sweep reads publish no
artefact version 3.x lacks. The default is an error rather than a silent
rewrite for that reason.
"""

import functools
import re
from collections.abc import Callable, Iterable, Mapping
from typing import TypeIs

import attrs
from attrs import frozen

from sdmxlib.model._items import owner_classes, scheme_urn_of
from sdmxlib.model._rebind import bind
from sdmxlib.model._refshape import can_reach_ref, link_fields, walk_fields
from sdmxlib.model._resolvers import MapResolver
from sdmxlib.model.collections import ArtefactList, ItemList
from sdmxlib.model.errors import SdmxParseError
from sdmxlib.model.link import Link
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref, SupportsUrn, artefact_urn
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import ValidationError, ValidationIssue, _collect_refs
from sdmxlib.model.version import compliance

#: How a batch's versions are rewritten: a mapping from the version as
#: published to the version to ingest, or a function applied to every version.
type VersionRewrite = Mapping[str, str] | Callable[[str], str]

#: `attrs.evolve` for a value known only to be *some* attrs instance. pyrefly
#: checks `evolve`'s argument against the concrete class it can see, and here
#: the class is whatever the walk reached, so it can see none; the rebuilt
#: value is returned as `object` and narrowed by the caller (`_rebuild`).
_evolve: Callable[..., object] = attrs.evolve

#: How a walk changes one reference's URN: the URN itself when it stays.
type _Follow = Callable[[SdmxUrn[object]], SdmxUrn[object]]

_LEADING_ZEROS = re.compile(r"^\d+(\.\d+)*$")


def strip_leading_zeros(version: str) -> str:
    """`1.03` → `1.3`: the one 2.1-only spelling with an exact 3.x equivalent.

    SDMX-ML 2.1's `VersionType` documents that "a version of 1.3 and 1.03 would
    be equivalent, as both parse to an integer value of 3", so this changes the
    spelling and not the version. Anything else comes back unchanged —
    including `1.2.3.4`, which has no 3.x equivalent and stays refused.

    Args:
        version: A version as published.

    Returns:
        The version with each numeric part's leading zeros removed.
    """
    if not _LEADING_ZEROS.match(version):
        return version
    return ".".join(str(int(part)) for part in version.split("."))


def is_2_1_only(version: str) -> bool:
    """Whether `version` is valid in SDMX-ML 2.1 and has no SDMX 3.x form."""
    return compliance(version) == {"2.1"}


@frozen
class RewrittenVersion:
    """One artefact whose version was rewritten at ingest.

    Attributes:
        urn: The artefact's URN as ingested, at the new version.
        original: The version as published.
        version: The version as ingested.
    """

    urn: str
    original: str
    version: str


def check_ingestable(artefacts: Iterable[SupportsUrn]) -> None:
    """Refuse an artefact whose version 3.x cannot hold.

    What no format can write is refused as the artefact is built (#492), so
    this asks only what depends on the store's model: a 2.1-only version.

    Accumulated across the batch, so one error names every problem.

    Args:
        artefacts: The artefacts about to be added.

    Raises:
        ValidationError: If an artefact's version, or any version a
            reference inside it names, is valid only in SDMX 2.1.
    """
    issues: list[ValidationIssue] = []
    for artefact in artefacts:
        label = f"{artefact.sdmx_class}:{artefact.agency_id}:{artefact.id}"
        if is_2_1_only(artefact.version):
            issues.append(ValidationIssue(path=f"{label}.version", message=_refusal(artefact.version)))
        issues.extend(
            ValidationIssue(
                path=path,
                message=f"the reference to {ref.urn.agency}:{ref.urn.id}: {_refusal(ref.urn.version)}",
            )
            for path, ref in _collect_refs(artefact, label)
            if is_2_1_only(ref.urn.version)
        )
    if issues:
        raise ValidationError(issues)


def _refusal(version: str) -> str:
    fixed = strip_leading_zeros(version)
    if fixed != version and not is_2_1_only(fixed):
        how = f"rewrite_version=sl.versions.strip_leading_zeros makes it {fixed!r}, its equal in 2.1"
    else:
        how = f'rewrite_version={{"{version}": ...}} names the version to use'
    return (
        f"version {version!r} is valid only in SDMX 2.1, and the model holds SDMX 3.x; "
        f"ingest it with add_all(..., {how})"
    )


def rewrite_versions[T: SupportsUrn](
    artefacts: Iterable[T], rewrite: VersionRewrite | None
) -> tuple[list[T], tuple[RewrittenVersion, ...]]:
    """Rewrite every version in a batch, artefacts and references alike.

    Copy-on-write: an artefact the rewrite does not touch is returned as the
    same object, and one it does is rebuilt along the changed path only. The
    caller's objects are never mutated — a parsed message handed to
    `add_all` is still the message that was parsed.

    Args:
        artefacts: The batch.
        rewrite: A mapping from published to ingested version, a function
            applied to every version, or None to leave the batch alone.

    Returns:
        The batch with versions rewritten, and one `RewrittenVersion` per artefact
        whose own version changed.

    Raises:
        ValidationError: If two artefacts of the batch would share an identity
            only because of the rewrite.
    """
    batch = list(artefacts)
    if rewrite is None:
        return batch, ()
    apply = _as_function(rewrite)
    follow = _version_follower(apply)
    out: list[T] = []
    rewrites: list[RewrittenVersion] = []
    claimed: dict[str, str] = {}
    issues: list[ValidationIssue] = []
    for artefact in batch:
        original = artefact.version
        new = _rebuild(artefact, follow)
        if apply(original) != original:
            new = attrs.evolve(new, version=apply(original))
            rewrites.append(RewrittenVersion(urn=str(artefact_urn(new)), original=original, version=new.version))
        urn = str(artefact_urn(new))
        before = claimed.setdefault(urn, original)
        if before != original:
            issues.append(
                ValidationIssue(
                    path=f"{artefact.sdmx_class}:{artefact.agency_id}:{artefact.id}.version",
                    message=f"versions {before!r} and {original!r} are both rewritten to {new.version!r}",
                )
            )
        out.append(new)
    if issues:
        raise ValidationError(issues)
    return out, tuple(rewrites)


def collision(rewrite: RewrittenVersion) -> ValidationIssue:
    """The issue for a rewritten URN the registry already holds from elsewhere."""
    return ValidationIssue(
        path=f"{rewrite.urn}.version",
        message=(
            f"rewriting {rewrite.original!r} to {rewrite.version!r} collides with {rewrite.urn}, which the "
            "registry already holds and not as a rewrite of this version; a rewrite may not replace another artefact"
        ),
    )


def dangling(source: str, rewrite: RewrittenVersion) -> ValidationIssue:
    """The issue for a reference outside the batch to a version the batch rewrites."""
    return ValidationIssue(
        path=source,
        message=(
            f"references {original_urn(rewrite)}, which this batch rewrites to {rewrite.version!r}; the reference "
            "would dangle — add its artefact in the same batch, or drop it first"
        ),
    )


def names(ref: SdmxUrn[object], artefact: str) -> bool:
    """Whether a reference names the artefact `artefact`, or an item inside it."""
    if not ref.is_item_urn:
        return str(ref) == artefact
    return any(str(scheme_urn_of(ref, owner)) == artefact for owner in owner_classes(ref.artefact_class))


def original_urn(rewrite: RewrittenVersion) -> str:
    """The URN the artefact had at its published version."""
    urn = SdmxUrn.parse(rewrite.urn)
    return str(attrs.evolve(urn, version=rewrite.original))


def _is_attrs_instance(value: object) -> TypeIs[attrs.AttrsInstance]:
    # On the value, not `attrs.has(type(value))`: that guard lands on the
    # discarded `type(value)` and narrows nothing. A class is not an instance.
    return not isinstance(value, type) and attrs.has(type(value))


def _as_function(rewrite: VersionRewrite) -> Callable[[str], str]:
    if isinstance(rewrite, Mapping):
        return lambda version: rewrite.get(version, version)
    return rewrite


def _version_follower(apply: Callable[[str], str]) -> _Follow:
    """A reference's URN with its version passed through *apply*; a `latest` one unchanged."""

    def follow(urn: SdmxUrn[object]) -> SdmxUrn[object]:
        if urn.is_latest:
            return urn
        version = apply(urn.version)
        return urn if version == urn.version else attrs.evolve(urn, version=version)

    return follow


def _rebuild[O](obj: O, follow: _Follow, *, links: bool = False) -> O:
    """*obj* with every reference's URN passed through *follow*, sharing what is unchanged.

    With *links*, a `Link` whose `urn` is a URN *follow* changes is rebuilt
    too. `rewrite_versions` leaves links alone: it runs at ingest, where
    reading every code's links of a 579,951-code codelist is a cost the
    version rewrite has never paid.
    """
    value = _rewrite_value(obj, follow, links=links)
    # `attrs.evolve` keeps the class, so this only narrows what is known.
    return value if isinstance(value, type(obj)) else obj


def _rewrite_value(value: object, follow: _Follow, *, links: bool) -> object:
    if isinstance(value, Ref):
        urn = follow(value.urn)
        return value if urn == value.urn else Ref[object].unresolved(urn)
    if links and isinstance(value, Link):
        return _rewrite_link(value, follow)
    if isinstance(value, ItemList):
        items = [_rebuild(item, follow, links=links) for item in value]
        changed = any(new is not old for new, old in zip(items, value, strict=True))
        return ItemList(items) if changed else value
    if isinstance(value, (list, tuple)):
        elements = [_rewrite_value(element, follow, links=links) for element in value]
        if not any(new is not old for new, old in zip(elements, value, strict=True)):
            return value
        return tuple(elements) if isinstance(value, tuple) else elements
    if not _is_attrs_instance(value):
        return value
    cls = type(value)
    names = _fields(cls, links=links)
    if not names:
        return value
    changes: dict[str, object] = {}
    aliases = {f.name: f.alias for f in attrs.fields(cls)}
    for name in names:
        old = getattr(value, name, None)
        new = _rewrite_value(old, follow, links=links)
        if new is not old:
            changes[aliases[name]] = new
    return _evolve(value, **changes) if changes else value


def _rewrite_link(link: Link, follow: _Follow) -> Link:
    """*link* with its `urn` followed, when it is an SDMX URN; otherwise *link* itself."""
    if link.urn is None:
        return link
    try:
        urn = SdmxUrn.parse(link.urn)
    except SdmxParseError:
        return link
    new = follow(urn)
    return link if new == urn else attrs.evolve(link, urn=str(new))


@functools.cache
def _fields(cls: type, *, links: bool) -> tuple[str, ...]:
    """The fields a rewrite reads on *cls*: those that can reach a `Ref`, and with *links* a `Link`."""
    refs = walk_fields(cls) if can_reach_ref(cls) else ()
    if not links:
        return refs
    return (*refs, *(name for name in link_fields(cls) if name not in refs))


# ── Identity rewrite (GH-406) ──────────────────────────────────────────────

#: An artefact's URN as it is, to its URN as it should be.
type UrnMap = Mapping[SdmxUrn[object], SdmxUrn[object]]


def rewrite(msg: StructureMessage, mapping: UrnMap) -> StructureMessage:
    """*msg* with artefacts' identities changed, and every reference to them following.

    Each key is a maintainable's URN and its value the URN it should have: a
    new version, id or agency, of the same class. An artefact of *msg* whose
    URN is a key takes the value's id, version and maintainer, and every
    reference to it, or to an item or component inside it (`SdmxUrn.owner`),
    is changed to match. So is a `Link` whose `urn` names one. A key *msg*
    does not hold still redirects the references to it, for artefacts held
    elsewhere that a migration re-versions too.

    The result's references are bound to the result, so following one finds
    the rewritten artefact. *msg* is left as it was.

    Args:
        msg: The message to rewrite.
        mapping: Old maintainable URN to new, each with an exact version.

    Returns:
        The rewritten message; *msg* itself when the mapping changes nothing.

    Raises:
        ValidationError: Accumulated over the whole mapping and message:
            a key or value that is an item URN or names no one version, a
            value of another class than its key, two keys mapped to one URN, a
            value that is the URN of an artefact *msg* holds and the mapping
            does not move, and a reference with no exact version (`latest`,
            `1.0+.0`) to an artefact whose id or agency the mapping changes.

    Example:
        old = sl.Codelist.urn_for(agency="STC", id="CL_AREA", version="1.7")
        new = sl.Codelist.urn_for(agency="STC", id="CL_AREA", version="2.0.0")
        migrated = sl.rewrite(msg, {old: new})
    """
    table = _check_mapping(msg, mapping)
    if not table:
        return msg
    _check_inexact_refs(msg, table)
    follow = _identity_follower(table)

    index: dict[str, object] = {}
    resolver = MapResolver(index)

    def moved[A: SupportsUrn](artefact: A) -> A:
        new = _rebuild(artefact, follow, links=True)
        target = table.get(str(artefact_urn(artefact)))
        if target is not None:
            changed = _evolve(new, id=target.id, version=target.version, maintainer=target.agency)
            new = changed if isinstance(changed, type(artefact)) else new
        bound = bind(new, resolver)
        index[str(artefact_urn(bound))] = bound
        return bound

    lists = {
        field.name: [moved(artefact) for artefact in value]
        for field in attrs.fields(StructureMessage)
        if isinstance(value := getattr(msg, field.name), ArtefactList)
    }
    # Not artefacts: no identity to move or index, though their references follow.
    availability = tuple(
        bind(_rebuild(constraint, follow, links=True), resolver) for constraint in msg.availability_constraints
    )
    # `header` named, so the artefact lists unpacked need not suit it.
    return attrs.evolve(msg, availability_constraints=availability, header=msg.header, **lists)


def _check_mapping(msg: StructureMessage, mapping: UrnMap) -> dict[str, SdmxUrn[object]]:
    """The mapping as old URN text to new URN, less identity entries; raises on any problem."""
    issues: list[ValidationIssue] = []
    table: dict[str, SdmxUrn[object]] = {}
    claimed: dict[str, str] = {}
    for old, new in mapping.items():
        path = f"rewrite[{old}]"
        sides = (("key", old), ("value", new))
        items = [f"{side} {urn}" for side, urn in sides if urn.is_item_urn]
        inexact = [f"{side} {urn}" for side, urn in sides if not _exact(urn)]
        problems: list[str] = []
        if items:
            problems.append(f"{' and '.join(items)}: an item URN; only a maintainable's identity changes")
        if inexact:
            problems.append(f"{' and '.join(inexact)}: names no one version")
        if (old.package, old.artefact_class) != (new.package, new.artefact_class):
            problems.append(f"{new} is a {new.artefact_class}, and {old} a {old.artefact_class}")
        if problems:
            issues.extend(ValidationIssue(path=path, message=problem) for problem in problems)
            continue
        if old == new:
            continue
        before = claimed.setdefault(str(new), str(old))
        if before != str(old):
            issues.append(ValidationIssue(path=path, message=f"{before} and {old} are both mapped to {new}"))
            continue
        table[str(old)] = new
    held = {str(artefact_urn(artefact)) for artefact in msg.all_artefacts()}
    issues.extend(
        ValidationIssue(
            path=f"rewrite[{old}]",
            message=f"{new} is held by the message and not moved by the mapping, so two artefacts would share it",
        )
        for old, new in table.items()
        if str(new) in held and str(new) not in table
    )
    if issues:
        raise ValidationError(issues)
    return table


def _check_inexact_refs(msg: StructureMessage, table: Mapping[str, SdmxUrn[object]]) -> None:
    """Refuse a reference with no exact version to an artefact whose id or agency the mapping changes."""
    renamed = {
        _unversioned(SdmxUrn.parse(old)): str(new)
        for old, new in table.items()
        if _unversioned(SdmxUrn.parse(old)) != _unversioned(new)
    }
    if not renamed:
        return
    issues: list[ValidationIssue] = []
    for artefact in msg.all_artefacts():
        label = f"{artefact.sdmx_class}:{artefact.agency_id}:{artefact.id}"
        for path, ref in _collect_refs(artefact, label):
            if _exact(ref.urn):
                continue
            owner = _owner(ref.urn)
            target = renamed.get(_unversioned(owner))
            if target is not None:
                issues.append(
                    ValidationIssue(
                        path=path,
                        message=(
                            f"references {ref.urn} with no exact version, and the mapping moves that artefact "
                            f"to {target}; name the version, or map the reference's artefact explicitly"
                        ),
                    )
                )
    if issues:
        raise ValidationError(issues)


def _identity_follower(table: Mapping[str, SdmxUrn[object]]) -> _Follow:
    """A reference's URN moved with its maintainable, when the mapping moves it."""

    def follow(urn: SdmxUrn[object]) -> SdmxUrn[object]:
        target = table.get(str(_owner(urn)))
        if target is None:
            return urn
        return attrs.evolve(urn, agency=target.agency, id=target.id, version=target.version)

    return follow


def _owner(urn: SdmxUrn[object]) -> SdmxUrn[object]:
    """`urn.owner()`, or *urn* itself for an item class no scheme holds, which no mapping can move."""
    try:
        return urn.owner()
    except ValueError:
        return urn


def _exact(urn: SdmxUrn[object]) -> bool:
    """Whether *urn* names one version, as opposed to `latest` or a late-bound range."""
    return not urn.is_latest and bool(compliance(urn.version))


def _unversioned(urn: SdmxUrn[object]) -> tuple[str, str, str, str]:
    return (urn.package, urn.artefact_class, urn.agency, urn.id)
