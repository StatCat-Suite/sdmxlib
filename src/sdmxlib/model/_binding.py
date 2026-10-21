"""Which registry a `Ref` built right now belongs to (#492).

A `Ref` finds its target through the `Resolver` of the registry that bound
it, and a frozen `Ref` is bound as it is built or never. A reader builds the
references inside an artefact long before it knows their targets — a store
read builds 499,138 hierarchy nodes, each holding a `Ref`, and only then loads
the codes they place — so the reader names its registry's resolver here
first, and every `Ref` built inside `binding(...)` is bound to it.

Construction only. Nothing resolves through this: a `Ref` keeps the resolver
it was built with, wherever it is used afterwards. Private by module path
(CLAUDE.md, "The public / private barrier", rule 4): readers and registries
use it, and a user builds references with `Ref.of` / `Ref.to` or gets bound
ones from `registry.ref(urn)`.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol

from sdmxlib.model.urn import SdmxUrn


class Resolver(Protocol):
    """What a `Ref` asks for its target: a registry's URN → object map (#492).

    Owned by the registry that bound the `Ref`, never by the `Ref`: the map
    changes as the registry learns artefacts, and the `Ref` and every
    artefact holding it stay as built. A lookup is a dict hit, not a
    `Registry.get` — that rebuilds an artefact from a store, in milliseconds.
    """

    def lookup(self, urn: SdmxUrn[object], /) -> object | None:
        """What *urn* names, or None if this registry does not hold it.

        Raises:
            UnresolvedRef: If the registry can no longer look, saying why.
        """
        ...


_CURRENT: ContextVar[Resolver | None] = ContextVar("sdmxlib_ref_binding", default=None)


def current() -> Resolver | None:
    """The resolver a `Ref` built now is bound to, or None outside any `binding`."""
    return _CURRENT.get()


@contextmanager
def binding(resolver: Resolver) -> Generator[None]:
    """Bind every `Ref` built inside the block to *resolver*.

    Args:
        resolver: The registry's map the references will look their targets up in.

    Yields:
        Nothing; the binding holds for the block.
    """
    token = _CURRENT.set(resolver)
    try:
        yield
    finally:
        _CURRENT.reset(token)
