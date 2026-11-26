"""`plan` a migration, then `Plan.apply` the answers to it (GH-399)."""

from collections.abc import Callable, Collection, Iterable, Iterator, Mapping

import attrs
from attrs import field, frozen

from sdmxlib.migration import _structure, _versions
from sdmxlib.migration._types import Blocked, Change, Edit, Move, Omit, Question, SetField, Target
from sdmxlib.model.collections import ArtefactList
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import artefact_urn
from sdmxlib.model.rewrite import rewrite
from sdmxlib.model.validation import check_serialisable

type _Finding = Change | Question | Blocked

#: The rules a plan runs, in order. Each yields findings for one message,
#: given the target and the annotation types whose titles are references.
_RULES: tuple[Callable[[StructureMessage, Target, frozenset[str]], Iterator[_Finding]], ...] = (
    lambda msg, _, annotation_refs: _versions.findings(msg, annotation_refs=annotation_refs),
    lambda msg, target, _: _structure.findings(msg, target),
)

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
        one order: leave out, set fields, then move identities with
        `sl.rewrite`, so every reference follows. The result is checked as
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
        kept = _without(self._msg, omitted)
        fields: dict[str, dict[str, object]] = {}
        for edit in edits:
            if isinstance(edit, SetField) and str(edit.urn) not in omitted:
                fields.setdefault(str(edit.urn), {})[edit.name] = edit.value
        updated = _with_fields(kept, fields)
        moves = {edit.old: edit.new for edit in edits if isinstance(edit, Move) and str(edit.old) not in omitted}
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

    Returns:
        The plan: its changes, questions and blocked items.

    Raises:
        ValueError: If the policy names a rule no rule asks, or an option that
            rule's questions do not take. A rule that asks nothing of this
            message is fine, so one policy serves every message of a registry.
    """
    types = frozenset(annotation_refs)
    found = _without_omitted([finding for rule in _RULES for finding in rule(msg, target, types)])
    questions = tuple(_answered(_of(found, Question), policy or {}))
    return Plan(
        target=target,
        changes=tuple(_of(found, Change)),
        questions=questions,
        blocked=tuple(_of(found, Blocked)),
        annotation_refs=types,
        msg=msg,
    )


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
QUESTIONS: dict[str, tuple[str, ...]] = {**_versions.QUESTIONS, **_structure.QUESTIONS}


def _answered(questions: Iterable[Question], policy: Mapping[str, str]) -> Iterator[Question]:
    for rule, choice in policy.items():
        options = QUESTIONS.get(rule)
        if options is None:
            msg = f"policy for {rule!r}, which no rule asks; the rules that ask are {sorted(QUESTIONS)}"
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
