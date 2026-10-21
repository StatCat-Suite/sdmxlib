"""Read a document artefact by artefact, refusing the ones that are wrong (#435).

Every structure reader — SDMX-ML 2.1, the shared 3.0/3.1 reader, SDMX-JSON —
used to let one `SdmxConformanceError` escape from wherever it was raised, so
the unit of failure was the whole document. registry.sdmx.org's 3.0
``datastructure`` document is 23 DSDs; three of them enumerate a dimension
with a ConceptScheme, which the schema forbids, and the reader returned none
of the 23.

The unit of failure is now the maintainable artefact. `build_each` is the one
place that decides it: every top-level loop in every reader goes through it,
so the catch is written once rather than pasted into twenty loops, and it is
narrow — `SdmxConformanceError`, the "this document is wrong" signal, and
`ValidationError`, a rule the model refuses the artefact for as it is built.
Anything else is our bug, and a reader that swallowed it would turn a defect
into a silently shorter message.

What a refusal does *not* do: construct a partial artefact (the build either
returns one whole or raises), or refuse what references the refused artefact
(its ref is simply left unresolved, as for any artefact a message omits).

What no store would take is refused the same way (#419): one invalid DSD
in a registry's bulk document used to fail the whole `add_all`. Its rules ran
after the build, over everything a reader built, and withdrew any reference
already resolved to a refused artefact. Every rule now runs as the artefact is
built (#492), so a refused artefact is never built, never in the document's
index, and a reference to it is left unresolved like any other.
"""

from collections.abc import Callable, Iterable

from lxml import etree

from sdmxlib.formats.sdmx_ml._closed_world import check_artefact
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.errors import Refusal
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import SdmxConformanceError, ValidationError

#: What a refusal is named by — the element's own agency, id and version.
type Identity = tuple[str, str, str | None]


def build_each[E, T](
    elements: Iterable[E],
    build: Callable[[E], T],
    cls: type[MaintainableArtefact],
    identify: Callable[[E], Identity],
    refused: list[Refusal],
    *,
    check: Callable[[E], None] | None = None,
) -> list[T]:
    """Build one artefact per element, refusing each that contradicts the schema.

    Args:
        elements: The document's elements (or DTOs) for one artefact kind.
        build: Builds one artefact from one element. Raises
            `SdmxConformanceError` if the element contradicts the schema, or
            `ValidationError` if the model refuses a value it holds.
        cls: The model class built — names the refusal's class and URN.
        identify: Reads an element's agency, id and version, to name it if it
            is refused.
        refused: Where refusals are recorded; the reader turns it into
            `StructureMessage.refused`.
        check: Run on each element before it is built; raises
            `SdmxConformanceError` to refuse it. The SDMX-ML readers pass
            ``check_artefact``, which refuses an element the schema allows
            nowhere in the subtree (#436) — run *first*, so the refusal names
            the stray element rather than what its absence broke downstream.

    Returns:
        The artefacts built, in document order, without the refused ones.
    """
    built: list[T] = []
    for el in elements:
        try:
            if check is not None:
                check(el)
            built.append(build(el))
        # A value no SDMX id or version can hold is refused as the model's
        # field is set (#492), and refuses the artefact as a contradiction does.
        except (SdmxConformanceError, ValidationError) as exc:
            agency, id_, version = identify(el)
            refused.append(
                Refusal(artefact_class=cls.sdmx_class, urn=_urn(cls, agency, id_, version), reason=_reason(exc))
            )
    return built


def _reason(exc: SdmxConformanceError | ValidationError) -> str:
    """A refusal's reason, its issues written as `refuse_invalid` writes them."""
    if isinstance(exc, ValidationError):
        return "; ".join(f"{issue.path} — {issue.message}" for issue in exc.issues)
    return str(exc)


def build_each_xml[T](
    elements: Iterable[etree._Element],
    build: Callable[[etree._Element], T],
    cls: type[MaintainableArtefact],
    refused: list[Refusal],
) -> list[T]:
    """`build_each` for SDMX-ML: named by `xml_identity`, checked by ``check_artefact``.

    Every SDMX-ML top-level loop goes through here, so closing the world
    (#436) — refusing an artefact whose subtree holds an element the schema
    allows nowhere under its parent — is one argument in one place rather
    than one per loop.

    Args:
        elements: The document's elements for one artefact kind.
        build: Builds one artefact from one element.
        cls: The model class built.
        refused: Where refusals are recorded.

    Returns:
        The artefacts built, in document order, without the refused ones.
    """
    return build_each(elements, build, cls, xml_identity, refused, check=check_artefact)


def _urn(cls: type[MaintainableArtefact], agency: str, id_: str, version: str | None) -> str:
    """URN text for a refused element, from what the element says.

    Built directly rather than through `SdmxUrn.make`, which validates the
    agency and id: an element can be refused *and* carry a bad id, and the
    refusal still has to name it.
    """
    urn = SdmxUrn[object](
        package=cls.sdmx_package,
        artefact_class=cls.sdmx_class,
        agency=agency,
        id=id_,
        version=version or "latest",
    )
    return str(urn)


def refuse_xml(el: etree._Element, cls: type[MaintainableArtefact], reason: str, refused: list[Refusal]) -> None:
    """Refuse an SDMX-ML element whole, named as `build_each_xml` names a refusal.

    For an element of a class the model has, holding content of a kind it has
    not — so the refusal is a reading, not a schema contradiction, and is
    recorded without raising.

    Args:
        el: The artefact's element.
        cls: The model class its URN is named for.
        reason: Why it is refused.
        refused: Where refusals are recorded.
    """
    agency, id_, version = xml_identity(el)
    refused.append(
        Refusal(artefact_class=etree.QName(el).localname, urn=_urn(cls, agency, id_, version), reason=reason)
    )


def xml_identity(el: etree._Element) -> Identity:
    """An SDMX-ML maintainable element's ``agencyID``, ``id`` and ``version``.

    ``version`` defaults as the readers default it, so the refusal names the
    URN the artefact would have had.
    """
    return el.get("agencyID", ""), el.get("id", ""), el.get("version", "1.0")
