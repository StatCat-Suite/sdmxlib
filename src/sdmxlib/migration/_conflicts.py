"""Final versions a migration would change in the target registry (GH-399).

A final version is immutable: the IM says so, and FMR refuses to replace one
with different content ("Semantically versioned structure can not be
modified"). A plan cannot see that, since it does not know what the target
holds. `conflicts` compares a migrated message with the target's holdings,
read from it with `RestRegistry.download_all_structures` and parsed, after
`Plan.apply` and before `FmrRegistry.submit`.

A registry's copy can differ from what was sent where the registry itself
normalises on load, which `Target.fmr`'s read-back changes report
(``fmr.related-dimensions``, ``fmr.monolingual``). Such a path in a conflict
is the registry's spelling, not a change; the rest is content the target
would refuse to replace.
"""

from attrs import field, frozen

from sdmxlib.model.diff import diff
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import artefact_urn


@frozen
class Conflict:
    """A final version the target holds with other content.

    Attributes:
        urn: The artefact, as text.
        differences: `sl.diff`'s path → example, migrated against held.
    """

    urn: str
    differences: dict[str, str] = field(hash=False)


def conflicts(migrated: StructureMessage, held: StructureMessage) -> tuple[Conflict, ...]:
    """Each final artefact of *migrated* that *held* has at the same URN with other content.

    Args:
        migrated: The message about to be submitted, from `Plan.apply`.
        held: What the target registry serves, parsed.

    Returns:
        One `Conflict` per such artefact, in *migrated*'s order. A draft is
        not one: a draft may be replaced.
    """
    holdings = {str(artefact_urn(artefact)): artefact for artefact in held.all_artefacts()}
    found: list[Conflict] = []
    for artefact in migrated.all_artefacts():
        urn = str(artefact_urn(artefact))
        other = holdings.get(urn)
        if other is None or not artefact.is_final:
            continue
        differences = diff(artefact, other)
        if differences:
            found.append(Conflict(urn=urn, differences=differences))
    return tuple(found)
