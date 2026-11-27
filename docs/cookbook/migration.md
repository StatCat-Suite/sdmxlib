# Migrating a 2.1 registry to SDMX 3.0 and FMR

Moving a registry's structures from SDMX 2.1 (a .Stat NSI, say) to SDMX 3.0
(a Fusion Metadata Registry) is a set of changes to its artefacts. Some are
mechanical, such as `1.0` becoming `1.0.0`. Some need a person to choose,
such as whether a non-final `1.0` becomes a draft or a final version. Some
cannot be made at all. `sl.migration` finds all three before anything is
sent, makes the changes and the choices, and checks the result.

The steps, in order:

1. Copy the source registry, byte for byte.
2. Plan the migration: what changes, what needs a decision, what cannot move.
3. Decide, by policy and by answer.
4. Apply the plan.
5. Check the result against what the target already holds.
6. Submit it to the target, and read every accepted artefact back.

## 1. Copy the source

Every later step reads the copy, so it re-runs offline from the same input.
`download_all_structures` asks for every structure resource the API names,
every version of each, and writes the registry's bytes unparsed. Each
resource is recorded as `written`, `none-held` (a 404) or `refused`, in the
returned records and in `downloads.json` beside the files:

```python
nsi = "https://nsi.example.org/rest"
with sl.RestRegistry(nsi, api="2.1") as source:
    records = source.download_all_structures("copy/", fmt=sl.StructureFormat.SDMX_ML_2_1)
print([r.resource for r in records if r.status == "written"])   # ['codelist']
```

A registry reads a bare query as `latest`, which misses every older version
it holds, so the copy asks for `version=all`.

Read the copy back as one message:

```python
msg = sl.read_all_structures("copy/")
print(len(msg.all_artefacts()))   # 13
```

## 2. Plan

A plan reads the message and changes nothing. `Target.fmr("12.5.0")` is
SDMX-ML 3.0, plus the deviation FMR shares with .Stat NSI (code parents
like `1` or `_T`, which the 3.0.0 XSD cannot name), plus the rules FMR
12.5.0 was recorded enforcing beyond the XSD:

```python
target = sl.migration.Target.fmr("12.5.0")
plan = sl.migration.plan(msg, target=target)
print(len(plan.questions))   # 10
```

- `plan.changes` are made with no input: a final `1.0` becomes `1.0.0`, a
  `StructureSet` (which 3.0 removed) is left out, its maps kept.
- `plan.questions` need a person. Each has a stable `key`, its `options`, and
  usually a `recommended` option. A non-final `1.0` asks whether it becomes
  `1.0.0-draft`, `1.0.0`, or is left out.
- `plan.blocked` cannot move, and each says what would let it.

The rules are listed in the API reference under
[Migration](../api.md#migration).

## 3. Decide

A policy answers every question of one rule at once. One policy serves a
whole registry, since a rule that asks nothing of one message is not an
error:

```python
plan = sl.migration.plan(
    msg,
    target=target,
    policy={"version.non-final": "draft", "format.loss": "accept", "fmr.actual-constraint": "omit"},
)
print([q.rule for q in plan.open])   # ['fmr.release-calendar']
```

The last policy entry is FMR's: it refuses every Actual data constraint, so
this policy leaves them out of the load. One question is left: FMR refuses a
constraint with both a release calendar and cube regions, and the rule
recommends nothing, since stripping the calendar loses it. Answer it, and
override the policy for one artefact:

```python
answers = {q.key: "strip" for q in plan.open}
first = plan.questions[0]
answers[first.key] = "final"
```

A question key is the rule and the artefact's URN, so the same message
planned again asks the same keys, and answers recorded in a workbook apply
to the next run.

Two kinds of input come as arguments rather than answers:

- `annotation_refs={"METADATA"}` treats the titles of those annotation types
  as references, which .Stat uses to link a DSD to its MSD. They then follow
  every move.
- `agency_names={"CA1.QOL": {...}, "CA1:AGENCIES": {...}}` names the
  sub-agencies a maintainer such as `CA1.QOL` needs declared, and their
  parent's scheme if the message lacks it. The IM requires both names, and
  the plan invents none.

A registry's own conventions go in rules of the caller's own, which the
target hands to `plan` after the built-in ones. `omit_agencies` is one:
it leaves out every artefact a test or training agency maintains, and every
artefact that references one left out, so the load never names an artefact
it does not hold:

```python
target = sl.migration.Target.fmr("12.5.0", rules=[sl.migration.omit_agencies("TEST", "TRAINING")])
```

A `Rule` of your own yields `Change`, `Question` and `Blocked` findings with
the same edits, and declares the options its questions take, so a policy
answers them as it answers the built-in rules.

Three rules remodel what 3.0 has no place for:

- `fmr.id-case` asks to rename a maintainable whose id has lower-case
  letters: FMR stores `CL_low` as `CL_LOW`, and a reference to `CL_low`
  stops resolving. Two ids that differ only by case are blocked
  (`fmr.id-case-clash`), since FMR stores the second under the first one's
  URN.
- `hierarchy.association` binds each hierarchy a 2.1
  `HierarchicalCodelist` carried to the dimension its codelist enumerates,
  with a `HierarchyAssociation`, or asks when the codelist enumerates
  several.
- `structure.measure-dimension` asks whether a 2.1 MeasureDimension becomes
  a dimension enumerated by a codelist of its concepts, or one measure per
  concept.

## 4. Apply

`apply` makes the changes and the chosen answers. It moves each artefact's
identity with `sl.rewrite`, so every reference follows, and it checks that
the result is writable for the target:

```python
migrated = plan.apply(answers)
print(sorted({a.version for a in migrated.all_artefacts()}))   # ['1.0', '1.0.0', '1.0.0-draft']
```

The `1.0` left is the agency scheme's: SDMX-ML 3.x writes organisation
schemes with no version. An open question refuses to apply unless
`partial=True` says so.

## 5. Check against the target

A final version cannot change, and FMR refuses to replace one with other
content. Copy what the target already holds, the same way the source was
copied, and compare:

```python
fmr_url = "https://fmr.example.org/ws/public/sdmxapi/rest"
with sl.FmrRegistry(fmr_url, auth=("user", "password")) as fmr:
    fmr.download_all_structures("fmr-copy/", fmt=sl.StructureFormat.SDMX_ML_3_0)
held = sl.read_all_structures("fmr-copy/")
print(sl.migration.conflicts(migrated, held))   # ()
```

Each conflict names the artefact and `sl.diff`'s paths. A path FMR
normalises on load is its spelling, not a change: the plan reports those as
`fmr.related-dimensions` and `fmr.monolingual`.

## 6. Submit and verify

`submit` checks the message again, then sends each artefact alone, in an
order each can load in, so FMR's verdict names every artefact rather than
the first problem in an atomic message. `verify` reads every accepted
artefact back, and raises naming any FMR does not serve:

```python
with sl.FmrRegistry(fmr_url, auth=("user", "password")) as fmr:
    report = fmr.submit(migrated, target=target)
    report.verify(fmr)
print(len(report.accepted), len(report.refused))   # 12 0
```

`verify` is there because a 200 is not proof: FMR 12.4.2 reported success
on a submission it had truncated.
