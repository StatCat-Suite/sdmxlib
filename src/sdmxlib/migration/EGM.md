# sdmxlib.migration

## Overview

`sdmxlib.migration` moves a `StructureMessage` read from an SDMX 2.1 registry
to SDMX 3.x (GH-399). `plan` finds what the move takes and changes nothing.
`Plan.apply` makes the changes and the options a person chose, and returns
the migrated message. The rules so far are the version rules
(`_versions.py`); the remodel rules, constraints and registry targets such as
FMR come next, as GH-399 records.

## Interface

- `plan(msg, *, target, policy=None, annotation_refs=())` (`_plan.py`):
  returns a `Plan`. `policy` maps a rule id to an option id and answers every
  question of that rule. `annotation_refs` names annotation types whose
  `title` is a reference (.Stat's `METADATA`): such a title follows moves, and
  an artefact only it names gets a `version.external` question.
- `Plan` (`_plan.py`): `target`, `changes`, `questions` (policy answers set
  on `Question.answer`), `blocked`, and `open`, the questions no policy
  answered. `Plan.apply(answers=None, *, partial=False)` returns the migrated
  message; `answers` maps a question key to an option id and overrides the
  policy.
- `Target(version, allow=frozenset())` (`_types.py`): `version` is `"3.0"`,
  `"3.1"` or `"json"`; `allow` holds the `Deviation`s the result may carry.
- `Change`, `Question`, `Option` and `Blocked` (`_types.py`): the findings.
  A `Question.key` is `"<rule>@<urn>"`.
- `Move`, `SetField`, `Omit` and their union `Edit` (`_types.py`): what a
  `Change` or `Option` does when applied.
- The version rules (`_versions.py`): `version.semantic`,
  `version.is-final-dropped` (changes); `version.non-final`,
  `version.semantic-non-final`, `version.external` (questions);
  `version.2.1-only`, `version.unit-scheme` (blocked). The module docstring
  states each.

## Invariants

- `plan` reads the message and builds nothing from it; `apply` leaves the
  planned message unchanged and returns a new one.
- A question key is the rule id and the artefact's URN, so the same message
  planned twice asks the same keys, and an answer recorded once applies again.
- `apply` makes its edits in one order: omit, set fields, then move
  identities with `sl.rewrite`, so every reference, item reference and
  `Link.urn` follows a move. It ends with `check_serialisable` for
  `target.version` and `target.allow`.
- `apply` refuses an answer to a question the plan does not hold, an option a
  question does not take, and an open question unless `partial=True`; `plan`
  refuses a policy for a rule that asked nothing, and an option the rule does
  not take. Each refusal is a `ValueError` naming the key or rule.
- Organisation schemes are never versioned: every format fixes four of them
  at `1.0`, and 3.x prohibits a version on all five.
- An annotation is a reference only when its type is in `annotation_refs`;
  annotation text is otherwise opaque, as in the IM.
- `version.external` recommends no option: the plan cannot see the 2.1
  `isFinal` of an artefact the message does not hold.

## Side Effects

- None. Both functions are pure over the message.

## Verification

- `tests/unit/test_migration_versions.py`: the plan of `sdmx_ml21` fixtures,
  apply's refusals, and one message that fires every version rule a known
  number of times.
- Run: `just test` and `just check`.
