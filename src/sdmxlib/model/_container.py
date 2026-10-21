"""The ids of what SDMX-ML 2.1 nests in a maintainable container (#421).

2.1 nests a hierarchy in a ``HierarchicalCodelist`` and a map in a
``StructureSet``, so each one's id is unique only within its container. SDMX
3.0 made both maintainable and removed the containers, so the id must now be
unique within an agency. OECD publishes 210 hierarchies called ``H``, one in
each of as many codelists, and kept bare they collapsed onto one URN.

The rule is the Fusion Metadata Registry's for hierarchies, which ECB and BIS
already serve: ``<inner>@<container>``, so ``H`` in ``HCL_AGE`` is
``H@HCL_AGE``. Maps follow it too, so the two containers convert alike.
"""


def member_id(inner_id: str, container_id: str) -> str:
    """The id an artefact nested in a 2.1 container has as a maintainable.

    A member whose id is its container's keeps it: that is the codelist a 2.1
    writer wraps around a standalone hierarchy, FMR's and this library's alike.
    `inner_id` is the inverse.

    Args:
        inner_id: The nested element's own id.
        container_id: The id of the container around it.

    Returns:
        The member's id.
    """
    return inner_id if inner_id == container_id else f"{inner_id}@{container_id}"


def inner_id(member_id: str, container_id: str) -> str | None:
    """The id `member_id` was given, or None when it gave no such member id.

    Args:
        member_id: A hierarchy's or map's id.
        container_id: The id of the container it is written in.

    Returns:
        The id to write on the nested element, or None when a 2.1 reader would
        not give this member back its id from that container.
    """
    if member_id == container_id:
        return member_id
    inner = member_id.removesuffix(f"@{container_id}")
    # `X@X` is no id `member_id` gives: inner `X` in `X` keeps its bare id.
    return inner if inner not in {"", member_id, container_id} else None
