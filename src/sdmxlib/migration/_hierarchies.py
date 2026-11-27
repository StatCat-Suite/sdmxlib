"""The hierarchy rule of a 2.1 → 3.x migration: bind each hierarchy an HCL carried (GH-428).

SDMX-ML 2.1 groups hierarchies in a `HierarchicalCodelist` and binds them to
nothing. The 2.1 reader promotes each one to a top-level `Hierarchy`, and
`format.dropped` leaves the container out of 3.x. In 3.x a hierarchy applies
to a dimension only through a `HierarchyAssociation`, so without one no
consumer finds it (`unique_hierarchy`, `applicable_hierarchies`).

- **hierarchy.association** (change): a hierarchy whose codes all come from
  one codelist, which enumerates exactly one dimension in the message, is
  bound to that dimension by a new association with no context.
- **hierarchy.association** (question): that codelist enumerates several
  dimensions. Bound to each of them (recommended), or to none.
- **hierarchy.association** (change, no edit): its codes come from several
  codelists or none, or its codelist enumerates no dimension in the message,
  so it is written unbound.

A hierarchy that an association in the message already links asks nothing.
Each association is maintained by the hierarchy's agency, named as the
hierarchy is, and has the id ``<hierarchy>@<DSD>@<dimension>``. It has the
DSD's version and moves with it (`Add.versioned_as`), so each version of a
DSD gets its own and a draft DSD's is a draft. Its references are rewritten
with the rest of the message, so they follow the version moves.
"""

from collections.abc import Iterable, Iterator

from sdmxlib.migration._types import Add, Blocked, Change, Option, Question
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, HierarchyAssociation
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref, artefact_urn
from sdmxlib.model.urn import SdmxUrn

type Finding = Change | Question | Blocked

#: The rule id of every finding here.
RULE = "hierarchy.association"

#: Each rule here that asks, and the option ids its questions take.
QUESTIONS: dict[str, tuple[str, ...]] = {RULE: ("each", "none")}


def findings(msg: StructureMessage) -> Iterator[Finding]:
    """A finding for each hierarchy an HCL in *msg* carried and no association links."""
    carried = {str(ref.urn) for hcl in msg.hierarchical_codelists for ref in hcl.hierarchies}
    linked = {
        str(association.linked_hierarchy.urn)
        for association in msg.hierarchy_associations
        if association.linked_hierarchy is not None
    }
    for hierarchy in msg.hierarchies:
        urn = str(artefact_urn(hierarchy))
        if urn in carried and urn not in linked:
            yield _finding(hierarchy, msg.data_structures)


def _finding(hierarchy: Hierarchy, structures: Iterable[DataStructure]) -> Finding:
    urn = str(artefact_urn(hierarchy))
    codelists = sorted({str(code.code.urn.owner()) for code in _codes(hierarchy.tree) if code.code is not None})
    if len(codelists) != 1:
        held = "no codelist" if not codelists else f"{len(codelists)} codelists"
        return _unbound(urn, f"its codes come from {held}, so no one dimension's codes are the ones it arranges")
    (codelist,) = codelists
    dimensions = [
        (dsd, dimension)
        for dsd in structures
        for dimension in dsd.dimensions
        if isinstance(dimension.representation, Ref) and str(dimension.representation.urn) == codelist
    ]
    if not dimensions:
        return _unbound(urn, f"no dimension in the message is enumerated by {codelist}")
    associations = [_association(hierarchy, dsd, dimension) for dsd, dimension in dimensions]
    if len(associations) == 1:
        (association,) = associations
        return Change(
            rule=RULE,
            urn=urn,
            path="",
            before=None,
            after=str(artefact_urn(association)),
            reason=(
                f"3.x binds a hierarchy to a dimension with a HierarchyAssociation, which 2.1 lacks; "
                f"{_shown(dimensions[0])} is the one dimension enumerated by {codelist}"
            ),
            edits=(_add(association, dimensions[0][0]),),
        )
    shown = ", ".join(_shown(pair) for pair in dimensions)
    return Question(
        key=f"{RULE}@{urn}",
        rule=RULE,
        urn=urn,
        question=(
            f"{hierarchy.agency_id}:{hierarchy.id} arranges the codes of {codelist}, which enumerates {shown}. "
            "3.x binds a hierarchy to each dimension it applies to. Which does it apply to?"
        ),
        options=(
            Option(
                id="each",
                consequence=f"bound to {shown}, by an association each",
                edits=tuple(
                    _add(association, dsd) for association, (dsd, _) in zip(associations, dimensions, strict=True)
                ),
            ),
            Option(id="none", consequence="written unbound, so no consumer finds it for a dimension", edits=()),
        ),
        recommended="each",
    )


def _unbound(urn: str, why: str) -> Change:
    return Change(
        rule=RULE,
        urn=urn,
        path="",
        before=None,
        after=None,
        reason=f"written unbound, since {why}; bind it with a HierarchyAssociation",
        edits=(),
    )


def _association(hierarchy: Hierarchy, dsd: DataStructure, dimension: Dimension) -> HierarchyAssociation:
    dsd_urn = SdmxUrn.parse(str(artefact_urn(dsd)))
    return HierarchyAssociation(
        id=f"{hierarchy.id}@{dsd.id}@{dimension.id}",
        maintainer=hierarchy.agency_id,
        version=dsd.version,
        name=hierarchy.name,
        linked_hierarchy=Ref[Hierarchy].unresolved(Hierarchy.parse_urn(str(artefact_urn(hierarchy)))),
        linked_object=Ref[object].unresolved(
            SdmxUrn[object](
                "datastructure", "Dimension", dsd_urn.agency, dsd_urn.id, dsd_urn.version, item_id=dimension.id
            )
        ),
    )


def _add(association: HierarchyAssociation, dsd: DataStructure) -> Add:
    return Add(artefact=association, versioned_as=SdmxUrn.parse(str(artefact_urn(dsd))))


def _shown(pair: tuple[DataStructure, Dimension]) -> str:
    dsd, dimension = pair
    return f"{dsd.agency_id}:{dsd.id}({dsd.version}).{dimension.id}"


def _codes(codes: Iterable[HierarchicalCode]) -> Iterator[HierarchicalCode]:
    """Each hierarchical code and, depth first, its children."""
    for code in codes:
        yield code
        yield from _codes(code.children)
