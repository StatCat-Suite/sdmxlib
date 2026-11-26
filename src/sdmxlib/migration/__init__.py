"""Migrate a message from SDMX 2.1 to 3.x: plan, decide, apply (GH-399).

`plan` finds what a migration takes without changing anything: the changes it
makes on its own, the questions a person has to answer, and what cannot move.
`Plan.apply` makes the changes and the chosen answers, moving each artefact's
identity with `sl.rewrite` so every reference follows, and checks that the
result is writable in the target format.

    plan = sl.migration.plan(msg, target=sl.migration.Target(version="3.0"),
                             policy={"version.non-final": "draft"})
    for question in plan.open:
        print(question.key, [option.id for option in question.options])
    migrated = plan.apply({"version.external@urn:...": "final"})

The rules are listed in `migration._versions` (versions),
`migration._structure` (what the target format cannot carry) and
`migration._fmr` and `migration._fmr_refusals` (what FMR does beyond the XSD,
and the 3.0 constructs it refuses, for `Target.fmr("12.5.0")`).
"""

from sdmxlib.migration._plan import Plan, plan
from sdmxlib.migration._types import Blocked, Change, Edit, Move, Omit, Option, Question, SetField, Target, Transform

__all__ = [
    "Blocked",
    "Change",
    "Edit",
    "Move",
    "Omit",
    "Option",
    "Plan",
    "Question",
    "SetField",
    "Target",
    "Transform",
    "plan",
]
