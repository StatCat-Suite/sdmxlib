"""An attribute's related dimensions, matched to the DSD's when only their case differs (#454).

SDMX ids are case-sensitive, and an attribute attached at series level names
dimensions of its own DSD. Eurostat's SDMX-ML 3.0 publishes 14 DSDs whose
relationships say ``FREQ`` for a dimension declared ``freq`` — 2,426 such
references, every one a case slip, none naming a dimension that is not
there; its 2.1 serialisation of the same DSDs gets them right. Read as
written, those relationships named nothing, and validation refuses that.

So each reader matches a reference that names exactly one dimension ignoring
case to that dimension, before the DSD is built, and the read records it
(`ASSUMPTIONS`, counted on the document, not here). A reference matching
none, or more than one, is left as written for validation to refuse.
"""

from collections.abc import Iterable

import attrs

from sdmxlib.model.datastructure import Attribute, MetadataAttributeUsage


def match_dimension_case[H: (Attribute, MetadataAttributeUsage)](
    holders: Iterable[H], dimension_ids: Iterable[str]
) -> list[H]:
    """*holders*, each with its related and optional dimensions spelled as the DSD spells them.

    Args:
        holders: A DSD's attributes, or its metadata attribute usages.
        dimension_ids: The DSD's dimension ids, the time dimension's included.

    Returns:
        The holders, the unchanged ones the same objects.
    """
    ids = set(dimension_ids)
    by_folded: dict[str, list[str]] = {}
    for dimension_id in ids:
        by_folded.setdefault(dimension_id.casefold(), []).append(dimension_id)

    def matched(name: str) -> str:
        if name in ids:
            return name
        candidates = by_folded.get(name.casefold(), [])
        return candidates[0] if len(candidates) == 1 else name

    out: list[H] = []
    for holder in holders:
        related = holder.related_dimensions
        if related is None or all(name in ids for name in related):
            out.append(holder)
            continue
        out.append(
            attrs.evolve(
                holder,
                related_dimensions=[matched(name) for name in related],
                optional_dimensions=frozenset(matched(name) for name in holder.optional_dimensions),
            )
        )
    return out
