"""Shared SDMX-JSON intra-message URN → Ref resolution.

A pure function over ``(urn, index, cls)`` — no reader or ingest state — so both
the reader (dict paths still being migrated) and the ingest module can import it
without a cycle. This is the shared resolver that replaced the per-call
``resolve`` callback (a generic ``resolve_urn`` handles every target type via
``cls``, so families that resolve to Codelist / Concept / DataStructure / … all
use one function).
"""

from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import ParsesUrn, SdmxUrn


def resolve_any_urn(urn_val: object) -> Ref[object] | None:
    """`resolve_urn` for a reference that may name any artefact.

    A categorisation's source and target, or a constraint's attachment, can be
    any maintainable, so there is no class to parse the URN as or to guard the
    resolved object with. Split from `resolve_urn` when `SdmxUrn.parse` became
    `SdmxUrn[object]` (#399): the typed path parses on the class
    (`cls.parse_urn`), which `object` has not got, and saying "any" by name
    here is clearer than an `object` witness there.
    """
    if not isinstance(urn_val, str) or not urn_val.startswith("urn:"):
        return None
    try:
        urn = SdmxUrn.parse(urn_val)
    except ValueError, TypeError:
        return None
    # Bound to the document's index, which the reader names (#492).
    return Ref.unresolved(urn)


def resolve_urn[T: ParsesUrn](urn_val: object, cls: type[T]) -> Ref[T] | None:
    """Parse a URN string into a `Ref` bound to the document's index (#492).

    *cls* is both a type witness and a guard: a URN naming another class is
    carried unbound, so it never resolves to a mistyped artefact. See
    ``docs/design/phantom-generics.md``.
    """
    if not isinstance(urn_val, str) or not urn_val.startswith("urn:"):
        return None
    try:
        urn = cls.parse_urn(urn_val)
    except ValueError, TypeError:
        return None
    if urn.artefact_class != cls.__name__:
        # A URN naming another class in a slot typed for *cls*: carried, never
        # bound, so it cannot resolve to a mistyped artefact (policy (a)).
        return Ref(urn=urn)
    # Bound to the document's index, which the reader names (#492).
    return Ref.unresolved(urn)
