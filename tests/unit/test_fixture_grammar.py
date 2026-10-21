"""The maximal fixture must obey SDMX's `<xs:choice>` groups — #359.

`tests/support/maximal.py` exists to set every field of every artefact, so the
field census can prove a round-trip loses nothing. "Every field" and "a document
the grammar permits" are not the same demand, and where they conflict the
grammar wins: a writer handed input no conformant document could carry will
correctly emit only part of it, and the census reads the rest coming back empty
and books a loss that was never real.

That has now cost three separate investigations — `Level.coding_format` (a
*restriction* of the ordinary facet type), `date`-vs-`datetime` and wildcard
URNs in #354, and twenty register entries in #359, where the fixture set every
member of `AttributeRelationship` and `ConstraintAttachment` at once. All twenty
were noise. #359 closed with "any XSD `<xs:choice>` is a place the fixture can
lie — worth a sweep of the remaining register for the same shape." This is that
sweep, kept.

## What the sweep found

The SDMX-ML 3.0 schemas declare 50 complex types with a multi-member
`<xs:choice>`. Most are registry and messaging shapes this library does not
model. Of the ones it does, almost all are **structurally incapable** of the
lie, because the model collapses the choice into one field rather than
mirroring it as siblings:

- `MetadataSetType` (`MetadataProvisionAgreement | Metadataflow`) — one
  `MetadataSet.structure` ref.
- `RepresentationMapType` (source/target codelist or datatype) — one `source`,
  one `target`.
- `CodelistExtensionType` (`Inclusive | ExclusiveCodeSelection`) — one
  `selection`, with the branch carried by a `mode` enum.
- `ValueType` (`StructuredText | Text`) — one `ReportedAttribute.value`.
- `ComponentValueSetType` and friends (`TimeRange | Value`) — only `Value` is
  modelled at all, so there is no second member to set.
- `RepresentationType` (`TextFormat | (Enumeration, EnumerationFormat?)`) —
  expressible as a lie, since `enumeration_format` is a separate field from
  `representation`. Checked: no fixture component sets an `enumeration_format`
  without an enumerated representation. The nesting matters here and a flat
  reading of the XSD gets it wrong — `EnumerationFormat` is not a third branch,
  it is an optional companion *inside* the second.

What remains is the shape that actually bit: **a model class with sibling
optional fields mirroring the choice**, with no discriminator forcing the
issue. Two of those exist, and they are declared below.

The lesson worth carrying: a choice modelled as one field plus a mode enum
cannot be lied about, and one modelled as N nullable siblings can. That is an
argument about the model, not about the fixture.
"""

from __future__ import annotations

from collections.abc import Iterable

import attrs
import pytest

from sdmxlib.model.constraint import ConstraintAttachment
from sdmxlib.model.datastructure import Attribute
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from tests.support import maximal

pytestmark = pytest.mark.unit


@attrs.frozen
class _Choice:
    """One XSD `<xs:choice>` the model mirrors as sibling nullable fields."""

    owner: type
    members: tuple[str, ...]
    xsd: str
    why: str


def _is_set(value: object) -> bool:
    """Whether a member is set: not None, and — for a member several may fill — not empty."""
    return value is not None and value not in ((), [])


#: The groups where "set every field" and "emit a valid document" disagree.
#:
#: Deliberately short. A group belongs here only when the model *can* express
#: two members at once — if a discriminator or a single field already forbids
#: it, there is nothing for a test to check and an entry would be ceremony.
_CHOICE_GROUPS: tuple[_Choice, ...] = (
    _Choice(
        owner=ConstraintAttachment,
        # The data sets and data sources are alternatives too, and some format
        # refuses each, so they are `shapes`'s, not the maximal fixture's (#422).
        members=("dataflows", "data_structures", "provision_agreements", "data_provider"),
        xsd="DataConstraintAttachmentType",
        why=(
            "A constraint attaches to dataflows, or structures, or provision agreements, "
            "or a provider — never to several kinds. Setting all four booked twelve false losses."
        ),
    ),
    _Choice(
        owner=Attribute,
        members=("related_dimensions", "related_group"),
        xsd="AttributeRelationshipType",
        why=(
            "The relationship's payload. `attachment` is the discriminator and SERIES/GROUP "
            "select which payload applies, but nothing stops both being set — which is how "
            "`related_group` was booked as a loss in every format that writes it correctly."
        ),
    ),
)


def _instances(owner: type) -> list[tuple[str, object]]:
    """Every instance of `owner` reachable from the maximal fixture, with a path."""
    found: list[tuple[str, object]] = []
    seen: set[int] = set()

    def visit(obj: object, path: str) -> None:
        # `cls` bound rather than `type(obj)` inline: `attrs.has` is a TypeGuard,
        # and on a discarded call expression it narrows nothing.
        cls = type(obj)
        if id(obj) in seen or isinstance(obj, Ref) or not attrs.has(cls):
            return
        seen.add(id(obj))
        if isinstance(obj, owner):
            found.append((path, obj))
        for field in attrs.fields(cls):
            value: object = getattr(obj, field.name, None)
            if value is None:
                continue
            visit(value, f"{path}.{field.name}")
            # `Iterable` rather than a list of container type names: `ItemList`,
            # `ArtefactList` and `Annotations` are all sequences, and naming
            # them individually means the next one is silently not walked —
            # which is how `Annotations` came to be skipped by the sibling
            # walker in `test_field_coverage` until #368. Strings are excluded
            # because iterating one yields characters, not children.
            if isinstance(value, Iterable) and not isinstance(value, str | bytes):
                for index, item in enumerate(value):
                    visit(item, f"{path}.{field.name}[{index}]")

    message = maximal.message()
    for slot in (a.name for a in attrs.fields(StructureMessage) if not a.name.startswith("_")):
        for artefact in getattr(message, slot) or []:
            visit(artefact, slot)
    return found


@pytest.mark.parametrize("choice", _CHOICE_GROUPS, ids=lambda c: c.owner.__name__)
def test_no_instance_sets_two_members_of_a_choice(choice: _Choice) -> None:
    """The lie itself: one object claiming two branches of an either/or.

    A writer handed this emits one branch, correctly, and the census books the
    others as losses. Twenty entries in #359 came from exactly this.
    """
    offenders = [
        f"{path}: {sorted(set_members)}"
        for path, obj in _instances(choice.owner)
        if len(set_members := [m for m in choice.members if _is_set(getattr(obj, m, None))]) > 1
    ]
    assert not offenders, (
        f"{choice.owner.__name__} mirrors {choice.xsd}, an <xs:choice> — at most one member "
        f"may be set on any one instance.\n{choice.why}\n\nOffending instances:\n"
        + "\n".join(f"    {o}" for o in offenders)
        + "\n\nUse one instance per branch, as the fixture does for the others."
    )


@pytest.mark.parametrize("choice", _CHOICE_GROUPS, ids=lambda c: c.owner.__name__)
def test_every_member_of_a_choice_is_exercised_somewhere(choice: _Choice) -> None:
    """And the other half: one instance per branch, not one branch total.

    Splitting a lying fixture into honest ones is easy to do by deleting rather
    than distributing — which would leave three of the four attachment kinds
    written by nobody and checked by nothing. The register would go quiet for
    the same reason it went noisy, and look like progress.
    """
    instances = _instances(choice.owner)
    assert instances, f"the fixture has no {choice.owner.__name__} at all, so this proves nothing"
    covered = {m for _, obj in instances for m in choice.members if _is_set(getattr(obj, m, None))}
    missing = sorted(set(choice.members) - covered)
    assert not missing, (
        f"no {choice.owner.__name__} in the maximal fixture sets {missing}, so the round-trip "
        f"never exercises {'that branch' if len(missing) == 1 else 'those branches'} of "
        f"{choice.xsd}. Add an instance per branch rather than widening an existing one."
    )


def test_a_declared_group_is_one_the_model_can_actually_get_wrong() -> None:
    """Guards the register above against ceremony.

    An entry earns its place only if the fields are siblings the model lets you
    set together. If a discriminator or a single field already makes the lie
    impossible, the test over it can never fail and its presence suggests a
    protection that is really coming from the type.
    """
    for choice in _CHOICE_GROUPS:
        names = {f.name for f in attrs.fields(choice.owner)}
        missing = sorted(set(choice.members) - names)
        assert not missing, f"{choice.owner.__name__} has no field(s) {missing} — the group is stale"
        assert len(choice.members) > 1, f"{choice.owner.__name__}: a one-member choice is not a choice"
