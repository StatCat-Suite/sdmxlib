"""Versions only SDMX 2.1 accepts are refused at ingest, unless rewritten (#420, #424).

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

import re
from collections.abc import Callable, Iterable, Mapping
from typing import TypeIs

import attrs
from attrs import frozen

from sdmxlib.model._items import owner_classes, scheme_urn_of
from sdmxlib.model._refshape import can_reach_ref, walk_fields
from sdmxlib.model.collections import ItemList
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
    out: list[T] = []
    rewrites: list[RewrittenVersion] = []
    claimed: dict[str, str] = {}
    issues: list[ValidationIssue] = []
    for artefact in batch:
        original = artefact.version
        new = _rebuild(artefact, apply)
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


def _rebuild[O](obj: O, apply: Callable[[str], str]) -> O:
    """`obj` with every reference's version passed through `apply`, sharing what is unchanged."""
    value = _rewrite_value(obj, apply)
    # `attrs.evolve` keeps the class, so this only narrows what is known.
    return value if isinstance(value, type(obj)) else obj


def _rewrite_value(value: object, apply: Callable[[str], str]) -> object:
    if isinstance(value, Ref):
        urn = value.urn
        if urn.is_latest or apply(urn.version) == urn.version:
            return value
        return Ref[object].unresolved(attrs.evolve(urn, version=apply(urn.version)))
    if isinstance(value, ItemList):
        items = [_rebuild(item, apply) for item in value]
        changed = any(new is not old for new, old in zip(items, value, strict=True))
        return ItemList(items) if changed else value
    if isinstance(value, (list, tuple)):
        elements = [_rewrite_value(element, apply) for element in value]
        if not any(new is not old for new, old in zip(elements, value, strict=True)):
            return value
        return tuple(elements) if isinstance(value, tuple) else elements
    if not _is_attrs_instance(value):
        return value
    cls = type(value)
    if not can_reach_ref(cls):
        return value
    changes: dict[str, object] = {}
    aliases = {f.name: f.alias for f in attrs.fields(cls)}
    for name in walk_fields(cls):
        old = getattr(value, name, None)
        new = _rewrite_value(old, apply)
        if new is not old:
            changes[aliases[name]] = new
    return _evolve(value, **changes) if changes else value
