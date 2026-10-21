"""Every maintainable's `msg_field` names a real slot — #362.

`msg_field` is a string on the artefact class that says which message
collection it belongs in. `StructureMessage.containing` dispatches on it, and
`Session.ingest` walks the collections it names. Nothing checked the two
agreed, so a class could name a slot that did not exist and stay wrong until
someone called `containing()` and got a `TypeError`.

`MetadataProvisionAgreement` did exactly that: modelled, exported, storable,
and pointing at `metadata_provision_agreements`, a slot no message had. It was
invisible to an audit that enumerated message slots — which is how it survived
one — and visible immediately to this, which enumerates the classes instead.

The pairing is what makes it worth a test. Either side alone looks fine; only
comparing them finds a half-wired artefact.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import attrs
import pytest

import sdmxlib.model as model_pkg
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.message import MetadataMessage, StructureMessage

pytestmark = pytest.mark.unit


def _maintainable_classes() -> dict[str, type]:
    """Every `MaintainableArtefact` subclass in `sdmxlib.model`.

    Walked rather than listed: a hand-written list is a second place to forget
    the artefact, which is the failure this file exists to catch.
    """
    modules = [importlib.import_module(f"sdmxlib.model.{m.name}") for m in pkgutil.iter_modules(model_pkg.__path__)]
    return {
        name: obj
        for imported in modules
        for name, obj in vars(imported).items()
        if inspect.isclass(obj) and issubclass(obj, MaintainableArtefact) and obj is not MaintainableArtefact
    }


def _slots(message: type) -> set[str]:
    # `_`-prefixed fields are not slots: `StructureMessage._refused` (#435).
    return {a.name for a in attrs.fields(message) if not a.name.startswith("_")}


def test_every_maintainable_declares_a_message_field() -> None:
    """`msg_field` is how a message is built from an artefact — it is required."""
    missing = sorted(name for name, cls in _maintainable_classes().items() if not getattr(cls, "msg_field", None))
    assert not missing, (
        f"these maintainables declare no msg_field, so StructureMessage.containing() cannot place them: {missing}"
    )


def test_every_message_field_names_a_real_slot() -> None:
    """The slot a maintainable names must exist on one of the two messages.

    `MetadataSet` legitimately belongs to `MetadataMessage` rather than
    `StructureMessage`, so both are accepted — the check is that *some* message
    can hold it, not that a particular one can.
    """
    known = _slots(StructureMessage) | _slots(MetadataMessage)
    dangling = sorted(
        f"{name}.msg_field = {getattr(cls, 'msg_field', None)!r}"
        for name, cls in _maintainable_classes().items()
        if getattr(cls, "msg_field", None) not in known
    )
    assert not dangling, (
        "these maintainables name a message slot that does not exist:\n"
        + "\n".join(f"    {d}" for d in dangling)
        + "\n\nAdd the slot to StructureMessage (and to all_artefacts, the readers "
        "and the writers), or correct the msg_field."
    )


def test_every_structure_slot_is_claimed_by_a_maintainable() -> None:
    """And the reverse: a slot nothing writes to is dead weight.

    Catches the other half-wiring — a collection added to the message whose
    artefact class was never pointed at it, so it can only ever be empty.
    """
    claimed = {getattr(cls, "msg_field", None) for cls in _maintainable_classes().values()}
    # 3.1's availability statements have no identity, so no maintainable class
    # — the 3.1 reader and an Actual constraint's conversion fill it (#421).
    claimed.add("availability_constraints")
    orphans = sorted(_slots(StructureMessage) - claimed)
    assert not orphans, (
        f"these StructureMessage slots are named by no maintainable's msg_field, so "
        f"nothing can ever be placed in them: {orphans}"
    )
