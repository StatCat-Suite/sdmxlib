"""RegistryReader — Protocol for SDMX artefact sources.

A *RegistryReader* is anything that can produce an SDMX artefact given its
URN. The Protocol is the read-side seam shared by every backend: the
in-memory store (`InMemoryRegistry`),
the DuckDB-backed persistent store (`Registry`), the
HTTP-backed REST client (`RestRegistry`), and any
future federated dispatcher.

Two methods cover what every consumer needs:

- ``get(urn) → Artefact | None`` — look up by URN, ``None`` on miss.
- ``__contains__(urn) → bool`` — membership check.

Higher-level operations (transitive resolution, bulk fetch, browse) are
specific to particular registries and live on those concrete classes,
not on the Protocol.

Example:
    def lookup(reg: RegistryReader, urn: SdmxUrn) -> Codelist | None:
        return reg.get(urn)


    # Works for any backend:
    lookup(in_memory_reg, urn)
    lookup(local_reg, urn)
    lookup(rest_reg, urn)
"""

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from sdmxlib.model.ref import Ref, SupportsUrn, artefact_urn
from sdmxlib.model.urn import SdmxUrn

# The first parameter of each method below is positional-only (``/``). That is
# load-bearing, not style: protocol conformance matches positional-or-keyword
# parameters by *name*, and `RestRegistry.get` names its first parameter
# ``urn_or_type`` because it is overloaded (URN lookup and query-builder forms).
# Under a named parameter it therefore failed to satisfy this Protocol — which
# is why two helpers used to take `registry: object` plus a blanket
# attr-defined suppression instead. Positional-only makes the name irrelevant
# and every real registry conforms.
#
# (Spelling that suppression out here in prose is not possible: checkers scan
# every comment for the directive, so writing it literally would silence this
# line. That is the same blanket-matching behaviour issue #195 is about.)


@runtime_checkable
class SupportsGet(Protocol):
    """A source that can look one artefact up by URN.

    The narrowest useful read seam. Prefer this over `RegistryReader` for
    helpers that only ever call ``get`` — demanding ``__contains__`` as well
    would exclude sources that have no cheap membership test.
    """

    def get[T](self, target: SdmxUrn[T] | Ref[T], /) -> T | None:
        """Look up an artefact by URN, or by a `Ref` naming one.

        Returns ``None`` if absent. Positional-only, so the four
        implementations are interchangeable without agreeing on a keyword.

        Both spellings are accepted because graph traversal hands out `Ref`s
        and a caller should not have to unwrap one to look it up. A `str` is
        not accepted — see `target_urn` for why, and #370 for the divergence
        that made it worth stating.
        """
        ...


@runtime_checkable
class SupportsAddAll(Protocol):
    """A sink that can intern a batch of artefacts.

    The narrowest write seam, and what `formats.parse(registry=…)` actually
    needs — it calls `add_all` and nothing else. That parameter was declared
    `InMemoryRegistry`, which a `LocalRegistry` is not, so passing one
    type-checked nowhere while working everywhere (#347).
    """

    def add_all(self, artefacts: Iterable[SupportsUrn], /) -> object:
        """Intern every artefact, in order.

        Returns `object`: what a sink reports back is its own business, and
        both registries return the versions they rewrote (#420).
        """
        ...


@runtime_checkable
class SupportsGetAll(Protocol):
    """A source that can enumerate every artefact of a Python type."""

    def get_all[T](self, artefact_type: type[T], /) -> list[T]:
        """All artefacts of the given type. Empty list when there are none."""
        ...


@runtime_checkable
class RegistryReader(SupportsGet, Protocol):
    """A URN-keyed source of SDMX artefacts.

    Minimal Protocol — implementations may offer richer APIs (transitive
    resolution, bulk fetch, browse) but every RegistryReader must support
    these two operations so federation dispatchers can compose them
    uniformly.
    """

    def __contains__(self, target: object, /) -> bool:
        """True if the artefact named by `target` is available from this source.

        Takes an `SdmxUrn` or a `Ref`, matching `get`. `object` rather than
        that union because ``in`` must answer *no* for an unrelated value
        instead of raising — the narrowing happens inside (#370).

        Positional-only, like `get`: ``in`` never calls it by keyword, and
        without the marker every implementation has to agree on the parameter
        *name* to satisfy this Protocol. They did not — `RestRegistry` said
        ``urn`` — which is a naming constraint no caller can benefit from.
        """
        ...


__all__ = [
    "RegistryReader",
    "SupportsAddAll",
    "SupportsGet",
    "SupportsGetAll",
    "artefact_urn",
]
