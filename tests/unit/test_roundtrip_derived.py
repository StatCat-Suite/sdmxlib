"""Every field of every persistable artefact, set and read back (#316).

Three bugs of one shape shipped — `Facet.is_multilingual`, `Concept.parent_id`,
`DataProvider.contacts` — where the field existed in the model and storage
silently dropped it. All three were found by someone reading an inventory, not
by anything going red, because **an absence does not fail a test**: every
existing round-trip asserts that what it put in came back, over a fixture that
did not happen to set the missing field.

Two things make this guard different from the round-trip tests that missed
them:

**The field list is derived.** `attrs.fields()` supplies it, so a field added
tomorrow is covered the day it lands. A hand-written list can only cover the
fields someone thought of, and the bug is always the field nobody thought of.

**The values are distinguishable, not merely valid.** `is_final=False`
round-trips perfectly through a reader that never populates it, because the
default is also `False`. Every sentinel differs from its field's default, so
"came back equal" cannot be satisfied by code that does nothing.

It found four gaps on its first run, and they are recorded below as citations
rather than skipped — a guard whose exclusions have no reasons becomes the
place bugs hide.
"""

from __future__ import annotations

from collections.abc import Callable

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.model.ref import SupportsUrn
from sdmxlib.model.registry import artefact_urn
from sdmxlib.storage.writers import _WRITER_DISPATCH
from tests.support.sentinels import build

pytestmark = pytest.mark.unit


@attrs.define
class _FactoryProbe:
    items: list[int] = attrs.field(factory=list)


#: The class a factory default is an instance of. attrs' stubs declare
#: `attrs.Factory` as an overloaded function, so it cannot be passed to
#: `isinstance` as written; at runtime it is this class, read here off a real
#: factory default rather than off the stub.
_FACTORY: type = type(attrs.fields(_FactoryProbe).items.default)


#: Fields known not to survive, with the issue that owns each. Keyed
#: `(artefact, field)`; a bare `"*"` artefact means every type.
#:
#: **Empty, and that is the current state of the store**: every field of every
#: persistable artefact survives a write and a read. It opened with sixteen
#: entries across #320, #322 and #323 — the guard's first run — and each was
#: deleted by the fix rather than by a decision to tolerate it.
#:
#: An entry here is a **defect**, not a by-design gap: `formats/gaps.py` is
#: where a permanent limitation is declared, and nothing belongs in both.
#: Fixing one makes this guard fail on the unexpected pass, which is the signal
#: to delete the entry — so the table cannot quietly outlive the bug.
KNOWN_LOSSES: dict[tuple[str, str], str] = {}

#: Types this guard cannot yet build a full instance of, and why. Listed rather
#: than filtered silently: an unbuildable type is uncovered, and uncovered is
#: what this whole guard exists to make visible.
CANNOT_BUILD: dict[str, str] = {
    "MetadataSet": "requires a `structure` argument with no sentinel — needs an MSD to point at",
    "AgencyScheme": "SDMX fixes the id to AGENCIES, which the generic id sentinel violates",
    "DataProviderScheme": "SDMX fixes the id to DATA_PROVIDERS, likewise",
    "MetadataProviderScheme": "SDMX fixes the id to METADATA_PROVIDERS, likewise (#315)",
    "DataConsumerScheme": "SDMX fixes the id to DATA_CONSUMERS, likewise (#469)",
}


def _persistable() -> list[type]:
    return sorted(_WRITER_DISPATCH, key=lambda cls: cls.__name__)


def _known(artefact: str, field: str) -> str | None:
    return KNOWN_LOSSES.get((artefact, field)) or KNOWN_LOSSES.get(("*", field))


@pytest.mark.parametrize("cls", _persistable(), ids=lambda cls: cls.__name__)
def test_every_field_survives_a_store_and_load(cls: type) -> None:
    """Set every field to a distinguishable value, write, read, compare.

    A mismatch here is one of three things, and the message says which is
    expected: a real storage gap, a field that legitimately cannot survive
    (none so far — those live in `formats/gaps.py`), or a sentinel this
    harness built wrongly.
    """
    if cls.__name__ in CANNOT_BUILD:
        pytest.skip(f"{cls.__name__}: {CANNOT_BUILD[cls.__name__]}")

    artefact, sentinels, unbuildable = build(cls)
    losses: list[str] = []
    unexpected_passes: list[str] = []
    with sl.LocalRegistry(":memory:") as registry:
        assert isinstance(artefact, SupportsUrn), f"{cls.__name__} was built without a URN"
        registry.add(artefact)
        stored = registry.get(artefact_urn(artefact))
        assert stored is not None, f"{cls.__name__} did not come back at all"
        # A lazy proxy stands in for two types, and holds the registry's
        # connection — so materialising and comparing both happen *inside* the
        # block. Doing it after the close raises "Connection already closed",
        # which reads as a storage bug and is a bug in the harness.
        # `hasattr` narrows in neither checker; one annotated local takes the
        # dynamic step instead (CLAUDE.md, "Typed core, untyped shell").
        materialize: Callable[[], object] | None = getattr(stored, "materialize", None)
        stored = materialize() if materialize is not None else stored

        for name, sent in sentinels.items():
            if name in unbuildable:
                continue
            got = getattr(stored, name, _MISSING)
            matched = repr(got) == repr(sent)
            reason = _known(cls.__name__, name)
            if not matched and reason is None:
                losses.append(f"{name}: sent {sent!r}, got {got!r}")
            elif matched and reason is not None:
                unexpected_passes.append(f"{name} now survives — delete its KNOWN_LOSSES entry ({reason})")

    assert not losses, f"{cls.__name__} lost fields on a store/load round-trip:\n  " + "\n  ".join(losses)
    assert not unexpected_passes, "\n  ".join(unexpected_passes)


class _Missing:
    def __repr__(self) -> str:
        return "<attribute absent>"


_MISSING = _Missing()


# ── the guard's own coverage, kept visible ────────────────────────────────


def test_every_persistable_type_is_covered_or_explained() -> None:
    """A type this harness cannot build is uncovered, and uncovered silently is
    the condition #316 exists to end."""
    for cls in _persistable():
        if cls.__name__ in CANNOT_BUILD:
            continue
        artefact, sentinels, _ = build(cls)
        assert sentinels, f"{cls.__name__} produced no sentinels at all"
        assert artefact is not None


def test_the_sentinels_differ_from_the_defaults() -> None:
    """The property that makes a pass mean something.

    A sentinel equal to its field's default cannot distinguish a working
    reader from one that does nothing — which is exactly how
    `Facet.is_multilingual` survived 4,882 passing tests.
    """
    for cls in _persistable():
        if cls.__name__ in CANNOT_BUILD:
            continue
        _artefact, sentinels, _ = build(cls)
        for field in attrs.fields(cls):
            if field.name not in sentinels:
                continue
            default = field.default
            if default is attrs.NOTHING or isinstance(default, _FACTORY):
                continue
            assert repr(sentinels[field.name]) != repr(default), (
                f"{cls.__name__}.{field.name}'s sentinel equals its default, so this guard "
                f"cannot tell a working reader from one that does nothing"
            )


def test_known_losses_are_defects_not_declared_gaps() -> None:
    """Every exclusion cites an issue.

    `formats/gaps.py` is where a *permanent* limitation is declared; a defect
    recorded there would tell an author the loss is by design. Everything here
    is expected to be fixed and deleted.
    """
    for (artefact, field), reason in KNOWN_LOSSES.items():
        assert reason.startswith("#"), f"({artefact}, {field}) has no issue reference"
