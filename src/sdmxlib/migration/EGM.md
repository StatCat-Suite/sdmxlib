# sdmxlib.migration

## Overview

`sdmxlib.migration` moves a `StructureMessage` read from an SDMX 2.1 registry
to SDMX 3.x (GH-399). `plan` finds what the move takes and changes nothing.
`Plan.apply` makes the changes and the options a person chose, and returns
the migrated message. The rules are the version rules (`_versions.py`), the
structure rules (`_structure.py`), which read what the target format
cannot carry from `formats.gaps`, the hierarchy rule (`_hierarchies.py`),
and, for an FMR target, the FMR rules
(`_fmr.py`), which state what FMR 12.5.0 was recorded doing beyond the XSD.

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
- `Target(version, allow=frozenset(), registry=None, rules=())`
  (`_types.py`): `version` is `"3.0"`, `"3.1"` or `"json"`; `allow` holds
  the `Deviation`s the result may carry; `registry` names the registry whose
  rules the plan adds; `rules` are the caller's own, run after the built-in
  ones. `Target.fmr(release, *, rules=())` is SDMX-ML 3.0 with
  `non-ncname-parent` and FMR's rules, for a release in `FMR_RELEASES`
  (`"12.5.0"`).
- `Rule(find, questions={})` (`_types.py`): a caller's rule. `find(msg)`
  yields findings; `questions` maps each rule id its questions carry to the
  option ids they take.
- `omit_agencies(*agencies)` (`_agencies.py`): a `Rule` whose
  `agency.omitted` changes leave out what the agencies maintain and,
  repeatedly, what references an artefact left out; a late-bound reference
  names every version it may resolve to.
- `Change`, `Question`, `Option` and `Blocked` (`_types.py`): the findings.
  A `Question.key` is `"<rule>@<urn>"`.
- `Move`, `SetField`, `Omit` and their union `Edit` (`_types.py`): what a
  `Change` or `Option` does when applied.
- The version rules (`_versions.py`): `version.semantic`,
  `version.is-final-dropped` (changes); `version.non-final`,
  `version.semantic-non-final`, `version.external` (questions);
  `version.2.1-only`, `version.unit-scheme` (blocked). The module docstring
  states each.
- The structure rules (`_structure.py`): `format.dropped`,
  `format.converted`, `metadataflow.targets`, `msd.target-layer` (changes);
  `format.loss` and `structure.measure-dimension` (questions; the latter's
  options `dimension` and `measures` carry the remodelled DSD, codelist and
  constraints as `Add` edits); `metadataflow.targets` when no target can be
  derived, and `structure.measure-dimension` when its concept scheme is not
  in the message or the codelist id is taken (blocked).
- The hierarchy rule (`_hierarchies.py`): `hierarchy.association` adds a
  `HierarchyAssociation` for each hierarchy an HCL carried, to the one
  dimension its codelist enumerates (change), or to each of several
  (question); unbound when there is none (change, no edit).
- The FMR rules (`_fmr.py`), for an FMR target: `fmr.actual-constraint`,
  `fmr.next-major` and `fmr.id-case` (questions; the next major blocked when
  the message holds it), `fmr.id-case-clash` (blocked),
  `fmr.related-dimensions` and `fmr.monolingual` (changes, no edit).
- The FMR refusals (`_fmr_refusals.py`), for an FMR target: one question per
  artefact and SDMX-ML 3.0 construct FMR 12.5.0 refuses (concept roles,
  sentinel values, links in components, partial codelists, empty
  constraints, duplicate component maps and 13 more), each `strip` (where the
  artefact survives), `omit` or `keep`. A construct is found and stripped by
  the XPath the FMR oracle's ledger recorded its refusal under.
- `Transform(urn, name)` (`_types.py`): an edit that rebuilds an artefact with
  a rule's transform (`_plan.TRANSFORMS`). `Add(artefact, versioned_as=None)`: an
  edit that puts an artefact in the result, replacing one with its URN; with
  `versioned_as`, its version moves as that artefact's does.
- The agency rule (`_agencies.py`): `agency.scheme` (change) adds or extends
  the agency scheme a sub-agency maintainer (`CA1.QOL`) needs, named from
  `plan(..., agency_names=)`; `agency.unnamed` (blocked) when a name is
  missing, since the IM requires one and the plan invents none.
- `conflicts(migrated, held)` (`_conflicts.py`): each final artefact the
  target holds at the same URN with other content, with `sl.diff`'s paths,
  checked after `apply` and before `submit` against holdings downloaded from
  the target. A path a registry normalises on load (`fmr.*` read-back
  changes) is its spelling, not a change.
- `QUESTIONS` (`_plan.py`): every rule that asks, and its option ids.

## Invariants

- `plan` reads the message and builds nothing from it; `apply` leaves the
  planned message unchanged and returns a new one.
- A question key is the rule id and the artefact's URN, so the same message
  planned twice asks the same keys, and an answer recorded once applies again.
- `apply` makes its edits in one order: omit, add, set fields, run transforms, then move
  identities with `sl.rewrite`. An `Add` of an artefact an `Omit` leaves out
  is not made. Moves of one artefact compose part by part, so
  `fmr.id-case`'s rename and the version rule's `1.0.0` make one move;
  where two change the same part, a chosen option's overrides a change's,
  so `fmr.next-major`'s `2.0.0` wins over the version rule's `1.1.0`. Through `sl.rewrite`, every reference, item reference and
  `Link.urn` follows a move. It ends with `check_serialisable` for
  `target.version` and `target.allow`.
- `apply` refuses an answer to a question the plan does not hold, an option a
  question does not take, and an open question unless `partial=True`; `plan`
  refuses a policy for a rule no rule asks (`QUESTIONS` and the caller's
  rules' `questions`), and an option the rule does not take. It refuses a
  caller's rule that claims a built-in rule id, or asks a question it did
  not declare with the options it declared. A rule that asks nothing of one message is not refused,
  so one policy serves a whole registry.
- A finding about an artefact a `Change` leaves out is dropped from the plan:
  a `StructureSet` asks no version question.
- The structure rules state nothing the writer would not do: they read
  `formats.gaps.dropped`, `formats.gaps.unrepresentable` and
  `Metadataflow.with_derived_targets`, and each gap is covered by exactly one
  rule. Each refusal is a `ValueError` naming the key or rule.
- Organisation schemes are never versioned: every format fixes four of them
  at `1.0`, and 3.x prohibits a version on all five.
- An annotation is a reference only when its type is in `annotation_refs`;
  annotation text is otherwise opaque, as in the IM.
- `version.external` recommends no option: the plan cannot see the 2.1
  `isFinal` of an artefact the message does not hold.

## Side Effects

- None. Both functions are pure over the message.

## Verification

- `tests/integration/test_fmr_refusals.py`: the refusal table equal to the
  ledger's 3.0 adjustments for 12.5.0, each fixture matching exactly its
  ledger adjustments, and nothing the ledger removed left once stripped.
- `tests/integration/test_fmr_target.py`: the FMR rules' predictions held
  against FMR 12.5.0's recorded answers to the same messages
  (`tests/integration/fmr/12.5.0/registry-rules/`).
- `tests/unit/test_migration_rules.py`, `test_migration_hierarchies.py` and
  `test_migration_measure_dimension.py`: caller rules and `omit_agencies`,
  the hierarchy associations, and both MeasureDimension remodels written
  and XSD-valid.
- `tests/unit/test_migration_versions.py` and
  `tests/unit/test_migration_structure.py`: the plan of `sdmx_ml21` fixtures,
  apply's refusals, and for each rule module one message that fires every
  rule a known number of times.
- Run: `just test` and `just check`.
