"""What a migration plan holds: its target, its findings and the edits they imply (GH-399).

A finding is one of three kinds. A `Change` is made with no input, a
`Question` needs a person to pick one of its `Option`s, and a `Blocked` item
cannot be migrated. A `Change` and each `Option` carry the `Edit`s that
`Plan.apply` makes: move an artefact to a new URN, set one of its fields,
rebuild it with a rule's transform, add one, or leave it out.
"""

from collections.abc import Callable, Iterable, Mapping
from typing import Self

from attrs import field, frozen

from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import Deviation

#: The SDMX versions a migration can target: the 3.x ones, read as
#: `formats.gaps.TARGETS` spells them.
_VERSIONS = frozenset({"3.0", "3.1", "json"})

#: The FMR releases `Target.fmr` can name: the ones whose registry rules
#: `bench/fmr/registry_rules.py` recorded.
FMR_RELEASES: tuple[str, ...] = ("12.5.0",)


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


@frozen
class Transform:
    """Rebuild the artefact at `urn` with the transform a rule registers under `name`.

    For an edit no field-level change expresses, such as taking a construct
    out of an artefact's SDMX-ML 3.0 form (`migration._fmr_refusals`).
    """

    urn: SdmxUrn[object]
    name: str


@frozen
class Add:
    """Put `artefact` in the result, replacing one with its URN if the message holds it."""

    artefact: MaintainableArtefact = field(eq=False)

    @property
    def urn(self) -> SdmxUrn[object]:
        """The added artefact's URN."""
        return SdmxUrn.parse(str(self.artefact.urn))


type Edit = Move | SetField | Omit | Transform | Add


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


type Finding = Change | Question | Blocked


def _to_questions(value: Mapping[str, Iterable[str]]) -> dict[str, tuple[str, ...]]:
    return {rule: tuple(options) for rule, options in value.items()}


@frozen
class Rule:
    """A rule of the caller's own, which a `Target` hands to `plan`.

    `plan` runs it after its own rules and treats its findings as theirs: a
    `Change`'s edits are made, a `Question` is answered by `policy=` or
    `Plan.apply`, and a `Blocked` is reported.

    Example:
        def no_tests(msg):
            for codelist in msg.codelists:
                if codelist.id.startswith("TEST_"):
                    urn = sl.SdmxUrn.parse(str(codelist.urn))
                    yield Change(rule="local.test", urn=str(urn), path="", before=None,
                                 after=None, reason="a test codelist", edits=(Omit(urn=urn),))

        target = Target.fmr("12.5.0", rules=[Rule(find=no_tests)])

    Attributes:
        find: Yields the rule's findings for a message.
        questions: Each rule id its questions carry, and the option ids they
            take, as `QUESTIONS` holds them for the built-in rules. A
            question of a rule id missing here is refused.
    """

    find: Callable[[StructureMessage], Iterable[Finding]] = field(eq=False)
    questions: Mapping[str, tuple[str, ...]] = field(factory=dict, converter=_to_questions)


@frozen
class Target:
    """What a migration moves a message to.

    Attributes:
        version: The format the result must be writable as: ``"3.0"``,
            ``"3.1"`` or ``"json"`` (SDMX-JSON 2.0).
        allow: The `Deviation`s the result may carry, passed to
            `check_serialisable` when the plan is applied.
        registry: The registry the result is loaded into, such as
            ``"fmr-12.5.0"``, whose own rules the plan adds; None for none.
        rules: The caller's own rules, which the plan runs after its own.
    """

    version: str
    allow: frozenset[Deviation] = field(default=frozenset(), converter=frozenset)
    registry: str | None = None
    rules: tuple[Rule, ...] = field(default=(), converter=tuple)

    def __attrs_post_init__(self) -> None:
        """Refuse a version no migration targets."""
        if self.version not in _VERSIONS:
            msg = f"a migration targets one of {sorted(_VERSIONS)}, not {self.version!r}"
            raise ValueError(msg)

    @classmethod
    def fmr(cls, release: str, *, rules: Iterable[Rule] = ()) -> Self:
        """The target for loading into Fusion Metadata Registry `release`.

        SDMX-ML 3.0, with the non-NCName code parents FMR accepts, and the
        rules FMR enforces beyond the XSD (`migration._fmr`), followed by
        the caller's own `rules`.

        Raises:
            ValueError: If `release` is not one whose rules were recorded.
        """
        if release not in FMR_RELEASES:
            msg = f"no recorded FMR rules for {release!r}; the releases recorded are {list(FMR_RELEASES)}"
            raise ValueError(msg)
        return cls(version="3.0", allow=frozenset({"non-ncname-parent"}), registry=f"fmr-{release}", rules=tuple(rules))
