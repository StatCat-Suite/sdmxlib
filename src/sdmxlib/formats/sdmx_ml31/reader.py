"""SDMX-ML 3.1 reader: lxml → domain model.

The implementation is `sdmxlib.formats.sdmx_ml._v3_reader.V3Reader`, shared
with 3.0 (#346). This module binds it to the 3.1 namespace URIs and exposes
`read`.

It used to be a copy of the 3.0 reader — *"forked at parity"*, and measured at
AST level **30 of 30 functions byte-identical**. On the read path 3.1 differs
from 3.0 only by omitting attributes 3.0 emitted
(``Categorisation/@version``, ``DataConstraint/@role``), which the same parsing
code reads happily either way. So there was nothing to parameterise but the
namespace URIs, and those already lived in `namespaces.py`.
"""

from sdmxlib.formats.sdmx_ml._v3_reader import V3Reader
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.registry import SupportsAddAll

from .namespaces import COM, MES, STR

#: Bound once at import. `V3Reader` holds only the namespace triple and the
#: helpers derived from it, so one instance serves every call.
_READER = V3Reader(com=COM, str_=STR, mes=MES)


def read(data: bytes, *, registry: SupportsAddAll | None = None) -> StructureMessage:
    """Parse SDMX-ML 3.1 bytes into a StructureMessage.

    References within the message are auto-resolved.

    If *registry* is provided all parsed artefacts are added to it via
    ``registry.add_all()``, their Refs bound to it for cross-message resolution.

    Args:
        data: SDMX-ML 3.1 structure-message bytes.
        registry: Optional store to add every parsed artefact to.

    Returns:
        The parsed `StructureMessage`.
    """
    return _READER.read(data, registry=registry)
