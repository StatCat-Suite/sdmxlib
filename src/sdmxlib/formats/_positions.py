"""A dimension's stated position must agree with document order (#422, #449).

SDMX-ML 3.x's `Dimension/@position` and SDMX-JSON's `position` are both
optional and both "must be consistent with the position of the dimension in
the key descriptor". The model keeps no position of its own — order is list
order — so what a stated position can contradict is the *order*: a dimension
placed after another while it says it comes before it, or two dimensions
claiming one place. That is a document contradicting itself, refused like
any other.

A gap is not. Eurostat's SDMX 3.0 registry numbers 802 of its DSDs with a
number missing — `ESTAT:DSB_ICTEG01(7.0)` says 1, 2, 3, 5, 6, a dimension
removed and the rest left where they were — and the order is intact. Since
order is all the model holds, accepting the gap loses nothing, and refusing
it lost all 802 (#449). For the same reason the base is not fixed:
SDMX-JSON counts from 0, producers of SDMX-ML from 1, and either is
consistent.
"""

from sdmxlib.model.validation import SdmxConformanceError


def check_positions(stated: list[tuple[str, int | str | None]], *, element: str) -> None:
    """Refuse positions that contradict document order.

    Each dimension that states a position must state a larger one than every
    dimension before it that states one. A dimension that states none is
    skipped, and so is a missing number.

    Args:
        stated: Each dimension's id and stated position, in document order;
            None where it states none.
        element: The element or key named in the refusal.

    Raises:
        SdmxConformanceError: If a position is not an integer, or is not
            larger than the one stated before it.
    """
    before: tuple[str, int] | None = None
    for dim_id, pos in stated:
        if pos is None:
            continue
        try:
            place = int(pos)
        except ValueError:
            detail = f"dimension {dim_id!r} says position={pos!r}, which is not an integer"
            raise SdmxConformanceError(element, detail) from None
        if before is not None and place <= before[1]:
            detail = (
                f"dimension {dim_id!r} comes after {before[0]!r} in the dimension list but says "
                f"position={pos!r}, not more than its {before[1]!r}; a position must be consistent "
                "with document order"
            )
            raise SdmxConformanceError(element, detail)
        before = (dim_id, place)
