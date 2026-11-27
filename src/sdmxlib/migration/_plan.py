"""`plan` a migration, then `Plan.apply` the answers to it (GH-399)."""

from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence

import attrs
from attrs import field, frozen

from sdmxlib.migration import _agencies, _fmr, _fmr_refusals, _structure, _versions
from sdmxlib.migration._types import (
    Add,
    Blocked,
    Change,
    Edit,
    Move,
    Omit,
    Question,
    Rule,
    SetField,
    Target,
    Transform,
)
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.collections import ArtefactList
from sdmxlib.model.istring import InternationalString, to_istring
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import SupportsUrn, artefact_urn
from sdmxlib.model.rewrite import rewrite
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import check_serialisable

type _Finding = Change | Question | Blocked


@frozen
class _Inputs:
    """What a plan's rules read beside the message."""

    target: Target
    annotation_refs: frozenset[str]
    agency_names: Mapping[str, InternationalString]


#: The rules a plan runs, in order. Each yields findings for one message.
_RULES: tuple[Callable[[StructureMessage, _Inputs], Iterator[_Finding]], ...] = (
    lambda msg, given: _versions.findings(msg, annotation_refs=given.annotation_refs),
    lambda msg, given: _structure.findings(msg, given.target),
    lambda msg, given: _fmr.findings(msg, given.target),
    lambda msg, given: _fmr_refusals.findings(msg, given.target),
    lambda msg, given: _agencies.findings(msg, given.agency_names),
)

#: The transform each `Transform` edit names, from every rule module that has one.
TRANSFORMS: Mapping[str, Callable[[SupportsUrn], SupportsUrn]] = {**_fmr_refusals.TRANSFORMS}

#: `attrs.evolve` for an artefact whose class the checker cannot see here.
_evolve: Callable[..., object] = attrs.evolve


@frozen
class Plan:
    """What a migration of one message would do, before anything is done.

    Attributes:
        target: What the message moves to.
        changes: What `apply` makes with no input.
        questions: What needs a person, policy answers included.
        blocked: What cannot be migrated, and why.
        annotation_refs: The annotation types whose titles the plan treats as
            references.
    """

    target: Target
    changes: tuple[Change, ...]
    questions: tuple[Question, ...]
    blocked: tuple[Blocked, ...]
    annotation_refs: frozenset[str] = field(converter=frozenset)
    _msg: StructureMessage = field(alias="msg", repr=False)

    @property
    def open(self) -> tuple[Question, ...]:
        """The questions no policy answered."""
        return tuple(question for question in self.questions if question.answer is None)

    def apply(self, answers: Mapping[str, str] | None = None, *, partial: bool = False) -> StructureMessage:
        """The migrated message: the changes, plus each question's chosen option.

        An answer overrides the policy's for its question. Edits are made in
        one order: leave out, add, set fields, run transforms, then move identities with
        `sl.rewrite`, so every reference follows. Two moves of one artefact
        compose: a rename and a version move make one move. The result is checked as
        writable in the target format.

        Args:
            answers: Question key to option id.
            partial: Apply with questions left open. An open question makes
                no edit, so its artefact keeps its 2.1 version.

        Returns:
            A new message; the planned one is left as it was.

        Raises:
            ValueError: If an answer names a question the plan has not got,
                or an option its question does not take, or a question is left
                open and *partial* is False.
            ValidationError: If the result cannot be written in the target
                format, or the moves collide (`sl.rewrite`).
        """
        edits = [edit for change in self.changes for edit in change.edits]
        edits.extend(self._chosen(answers or {}, partial=partial))
        omitted = {str(edit.urn) for edit in edits if isinstance(edit, Omit)}
        kept = _with_added(_without(self._msg, omitted), [edit.artefact for edit in edits if isinstance(edit, Add)])
        fields: dict[str, dict[str, object]] = {}
        for edit in edits:
            if isinstance(edit, SetField) and str(edit.urn) not in omitted:
                fields.setdefault(str(edit.urn), {})[edit.name] = edit.value
        transforms: dict[str, list[str]] = {}
        for edit in edits:
            if isinstance(edit, Transform) and str(edit.urn) not in omitted:
                transforms.setdefault(str(edit.urn), []).append(edit.name)
        updated = _with_transforms(_with_fields(kept, fields), transforms)
        moves = _composed([edit for edit in edits if isinstance(edit, Move) and str(edit.old) not in omitted])
        migrated = rewrite(updated, moves, annotation_refs=self.annotation_refs)
        check_serialisable(migrated, version=self.target.version, allow=self.target.allow)
        return migrated

    def _chosen(self, answers: Mapping[str, str], *, partial: bool) -> Iterator[Edit]:
        by_key = {question.key: question for question in self.questions}
        unknown = sorted(set(answers) - set(by_key))
        if unknown:
            msg = f"answers to questions the plan has not got: {unknown}"
            raise ValueError(msg)
        unanswered: list[str] = []
        for question in self.questions:
            choice = answers.get(question.key, question.answer)
            if choice is None:
                unanswered.append(question.key)
                continue
            yield from question.option(choice).edits
        if unanswered and not partial:
            shown = ", ".join(unanswered[:3]) + (f" and {len(unanswered) - 3} more" if len(unanswered) > 3 else "")
            msg = f"{len(unanswered)} question(s) open: {shown}; answer them, or apply with partial=True"
            raise ValueError(msg)


def plan(
    msg: StructureMessage,
    *,
    target: Target,
    policy: Mapping[str, str] | None = None,
    annotation_refs: Collection[str] = (),
    agency_names: Mapping[str, Mapping[str, str] | InternationalString] | None = None,
) -> Plan:
    """Find what moving *msg* to *target* takes. Changes nothing.

    Args:
        msg: The message to migrate, as read from the source registry.
        target: What it moves to.
        policy: Rule to option id, answering every question of that rule,
            such as ``{"version.non-final": "draft"}``. An answer passed to
            `Plan.apply` overrides it for one question.
        annotation_refs: Annotation types whose `title` is a reference, such
            as ``{"METADATA"}``, .Stat's link from a DSD to its MSD. Such a
            title follows a move like any reference, and an artefact only it
            names is asked about by ``version.external``. Annotation text is
            opaque in the IM, so none is a reference unless named here.
        agency_names: A name for each sub-agency the message's maintainers
            need declared, such as ``{"CA1.QOL": {"en": "Quality of Life"}}``,
            and for each agency scheme the message lacks, under
            ``"CA1:AGENCIES"``. The IM requires both names, so a sub-agency
            with none is blocked (`migration._agencies`).

    Returns:
        The plan: its changes, questions and blocked items.

    Raises:
        ValueError: If the policy names a rule no rule asks, or an option that
            rule's questions do not take. A rule that asks nothing of this
            message is fine, so one policy serves every message of a registry.
            Also if a caller's `Rule` claims a built-in rule id, or asks a
            question its `questions` does not declare as asked.
    """
    types = frozenset(annotation_refs)
    names = {key: to_istring(value) for key, value in (agency_names or {}).items()}
    given = _Inputs(target=target, annotation_refs=types, agency_names=names)
    asks = _asks(target)
    found = _without_omitted(
        [
            *(finding for rule in _RULES for finding in rule(msg, given)),
            *(finding for rule in target.rules for finding in _checked(rule, msg)),
        ]
    )
    questions = tuple(_answered(_of(found, Question), policy or {}, asks))
    return Plan(
        target=target,
        changes=tuple(_of(found, Change)),
        questions=questions,
        blocked=tuple(_of(found, Blocked)),
        annotation_refs=types,
        msg=msg,
    )


def _composed(moves: Sequence[Move]) -> dict[SdmxUrn[object], SdmxUrn[object]]:
    """Each artefact's new URN, from every move of it in order.

    A move changes the parts of the URN it changes, its id or its version, so
    `fmr.id-case`'s rename and the version rule's move make one move. Where
    two moves change the same part, the later one wins, so a chosen option's
    version overrides a change's.
    """
    composed: dict[SdmxUrn[object], SdmxUrn[object]] = {}
    for move in moves:
        current = composed.get(move.old, move.old)
        composed[move.old] = attrs.evolve(
            current,
            id=move.new.id if move.new.id != move.old.id else current.id,
            version=move.new.version if move.new.version != move.old.version else current.version,
        )
    return composed


def _without_omitted(found: list[_Finding]) -> list[_Finding]:
    """*found* less the findings about an artefact a `Change` leaves out, which nothing else then needs."""
    omitted = {str(edit.urn) for f in found if isinstance(f, Change) for edit in f.edits if isinstance(edit, Omit)}
    return [
        f
        for f in found
        if f.urn not in omitted or (isinstance(f, Change) and any(isinstance(e, Omit) for e in f.edits))
    ]


def _of[F: (Change, Question, Blocked)](found: Iterable[_Finding], kind: type[F]) -> Iterator[F]:
    return (finding for finding in found if isinstance(finding, kind))


#: Every rule that asks, and the options its questions take.
QUESTIONS: dict[str, tuple[str, ...]] = {
    **_versions.QUESTIONS,
    **_structure.QUESTIONS,
    **_fmr.QUESTIONS,
    **_fmr_refusals.QUESTIONS,
}


def _asks(target: Target) -> dict[str, tuple[str, ...]]:
    """Every rule the plan for *target* can ask: the built-in ones and the caller's."""
    asks = dict(QUESTIONS)
    for rule in target.rules:
        for rule_id, options in rule.questions.items():
            if rule_id in QUESTIONS:
                msg = f"a caller's rule cannot ask {rule_id!r}, which is a built-in rule"
                raise ValueError(msg)
            asks[rule_id] = options
    return asks


def _checked(rule: Rule, message: StructureMessage) -> Iterator[_Finding]:
    """A caller's rule's findings, refusing a question it did not declare."""
    for finding in rule.find(message):
        if isinstance(finding, Question):
            asked = [option.id for option in finding.options]
            declared = rule.questions.get(finding.rule)
            if declared is None or set(declared) != set(asked):
                msg = (
                    f"{finding.key}: a caller's rule asked {finding.rule!r} with options {asked}, "
                    f"and its questions= declares {list(declared) if declared is not None else 'no such rule'}"
                )
                raise ValueError(msg)
        yield finding


def _answered(
    questions: Iterable[Question], policy: Mapping[str, str], asks: Mapping[str, tuple[str, ...]]
) -> Iterator[Question]:
    for rule, choice in policy.items():
        options = asks.get(rule)
        if options is None:
            msg = f"policy for {rule!r}, which no rule asks; the rules that ask are {sorted(asks)}"
            raise ValueError(msg)
        if choice not in options:
            msg = f"policy for {rule!r}: no option {choice!r}; it takes {list(options)}"
            raise ValueError(msg)
    for question in questions:
        choice = policy.get(question.rule)
        yield question if choice is None else attrs.evolve(question, answer=choice)


def _without(msg: StructureMessage, omitted: set[str]) -> StructureMessage:
    if not omitted:
        return msg
    kept = {
        attribute.name: [artefact for artefact in value if str(artefact_urn(artefact)) not in omitted]
        for attribute in attrs.fields(StructureMessage)
        if isinstance(value := getattr(msg, attribute.name), ArtefactList)
    }
    return attrs.evolve(msg, header=msg.header, **kept)


def _with_added(msg: StructureMessage, added: Sequence[MaintainableArtefact]) -> StructureMessage:
    """*msg* with each added artefact in its slot, replacing one with its URN."""
    if not added:
        return msg
    replacing = {str(artefact.urn) for artefact in added}
    # Each added artefact in a message of its own, which puts it in its slot
    # by its class (`StructureMessage.containing`); merged slot by slot.
    singles = [StructureMessage.containing(artefact) for artefact in added]
    lists = {
        attribute.name: [
            *(a for a in value if str(artefact_urn(a)) not in replacing),
            *(
                a
                for single in singles
                if isinstance(extra := getattr(single, attribute.name), ArtefactList)
                for a in extra
            ),
        ]
        for attribute in attrs.fields(StructureMessage)
        if isinstance(value := getattr(msg, attribute.name), ArtefactList)
    }
    return attrs.evolve(msg, header=msg.header, **lists)


def _with_transforms(msg: StructureMessage, transforms: Mapping[str, Sequence[str]]) -> StructureMessage:
    """*msg* with each listed artefact rebuilt by its transforms, in the order listed."""
    if not transforms:
        return msg

    def rebuilt[A](artefact: A, urn: str) -> A:
        new: object = artefact
        for name in transforms.get(urn, ()):
            if isinstance(new, SupportsUrn):
                new = TRANSFORMS[name](new)
        return new if isinstance(new, type(artefact)) else artefact

    lists = {
        attribute.name: [rebuilt(artefact, str(artefact_urn(artefact))) for artefact in value]
        for attribute in attrs.fields(StructureMessage)
        if isinstance(value := getattr(msg, attribute.name), ArtefactList)
    }
    return attrs.evolve(msg, header=msg.header, **lists)


def _with_fields(msg: StructureMessage, fields: Mapping[str, Mapping[str, object]]) -> StructureMessage:
    if not fields:
        return msg

    def updated[A](artefact: A, urn: str) -> A:
        changes = fields.get(urn)
        if not changes:
            return artefact
        new = _evolve(artefact, **changes)
        return new if isinstance(new, type(artefact)) else artefact

    lists = {
        attribute.name: [updated(artefact, str(artefact_urn(artefact))) for artefact in value]
        for attribute in attrs.fields(StructureMessage)
        if isinstance(value := getattr(msg, attribute.name), ArtefactList)
    }
    return attrs.evolve(msg, header=msg.header, **lists)
