"""What a migration plan holds: its target, its findings and the edits they imply (GH-399).

A finding is one of three kinds. A `Change` is made with no input, a
`Question` needs a person to pick one of its `Option`s, and a `Blocked` item
cannot be migrated. A `Change` and each `Option` carry the `Edit`s that
`Plan.apply` makes: move an artefact to a new URN, set one of its fields, or
leave it out.
"""

from attrs import field, frozen

from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import Deviation

#: The SDMX versions a migration can target: the 3.x ones, read as
#: `formats.gaps.TARGETS` spells them.
_VERSIONS = frozenset({"3.0", "3.1", "json"})


@frozen
class Target:
    """What a migration moves a message to.

    Attributes:
        version: The format the result must be writable as: ``"3.0"``,
            ``"3.1"`` or ``"json"`` (SDMX-JSON 2.0).
        allow: The `Deviation`s the result may carry, passed to
            `check_serialisable` when the plan is applied.
    """

    version: str
    allow: frozenset[Deviation] = field(default=frozenset(), converter=frozenset)

    def __attrs_post_init__(self) -> None:
        """Refuse a version no migration targets."""
        if self.version not in _VERSIONS:
            msg = f"a migration targets one of {sorted(_VERSIONS)}, not {self.version!r}"
            raise ValueError(msg)


@frozen
class Move:
    """Give the artefact at `old` the URN `new`; every reference follows (`sl.rewrite`)."""

    old: SdmxUrn[object]
    new: SdmxUrn[object]


@frozen
class SetField:
    """Set the field `name` of the artefact at `urn` to `value`."""

    urn: SdmxUrn[object]
    name: str
    value: str | bool | tuple[str, ...] | None


@frozen
class Omit:
    """Leave the artefact at `urn` out of the result."""

    urn: SdmxUrn[object]


type Edit = Move | SetField | Omit


@frozen
class Change:
    """One change the plan makes with no input.

    Attributes:
        rule: The rule that found it, such as ``"version.semantic"``.
        urn: The artefact it changes, as text.
        path: The field it changes, such as ``"version"``.
        before: The field's value now, as text.
        after: The field's value after the change, as text.
        reason: Why the change is needed, naming the rule of the target format.
        edits: What `Plan.apply` does to make it.
    """

    rule: str
    urn: str
    path: str
    before: str | None
    after: str | None
    reason: str
    edits: tuple[Edit, ...] = field(converter=tuple)


@frozen
class Option:
    """One answer to a `Question`.

    Attributes:
        id: The answer's id, such as ``"draft"``: what a caller passes.
        consequence: What choosing it does.
        edits: What `Plan.apply` does when it is chosen.
    """

    id: str
    consequence: str
    edits: tuple[Edit, ...] = field(converter=tuple)


@frozen
class Question:
    """One decision the plan needs from a person.

    Attributes:
        key: ``"<rule>@<urn>"``, stable across runs, so an answer recorded
            once applies to the next plan of the same message.
        rule: The rule that asks it, such as ``"version.non-final"``; a
            policy answers every question of one rule.
        urn: The artefact it is about, as text.
        question: The question, in a sentence.
        options: The answers it takes.
        recommended: The id of the option to pick when in doubt, or None
            when the plan has no basis to recommend one.
        answer: The option id the plan's policy gave, or None while open.
    """

    key: str
    rule: str
    urn: str
    question: str
    options: tuple[Option, ...] = field(converter=tuple)
    recommended: str | None = None
    answer: str | None = None

    def option(self, option_id: str) -> Option:
        """The option with this id.

        Raises:
            ValueError: If the question has no such option, naming the ones it has.
        """
        for option in self.options:
            if option.id == option_id:
                return option
        msg = f"{self.key}: no option {option_id!r}; it takes {[o.id for o in self.options]}"
        raise ValueError(msg)


@frozen
class Blocked:
    """One artefact the plan cannot migrate.

    Attributes:
        rule: The rule that found it.
        urn: The artefact, as text.
        reason: Why it cannot move, and what would let it.
    """

    rule: str
    urn: str
    reason: str
