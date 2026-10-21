"""SDMX-ML 3.1 writer: domain model → lxml.

The implementation is `sdmxlib.formats.sdmx_ml._v3_writer.V3Writer`, shared
with 3.0 (#346). This module binds it to the 3.1 namespace URIs and to
`V3_1`, the flag set saying what 3.1's XSD allows.

It used to be a copy of the 3.0 writer — 47 of 50 functions byte-identical.
The three that genuinely differed are now flags rather than a second file:
`Categorisation` without `@version`, `DataConstraint` without `@role` or
`<ReleaseCalendar>`, and `Dataflow` with `<DimensionConstraint>`. Each has a
matching row in `formats/gaps.py`.

Example:
    from sdmxlib.formats.sdmx_ml31.writer import write

    xml_bytes = write(msg)
"""

from sdmxlib.formats.sdmx_ml._v3_writer import V3_1, V3Writer
from sdmxlib.model.message import StructureMessage

from .namespaces import COM, MES, STR

#: Bound once at import. `V3Writer` holds the namespace triple, the helpers
#: derived from it and the generation flags — no per-call state.
_WRITER = V3Writer(com=COM, str_=STR, mes=MES, generation=V3_1)


def write(
    msg: StructureMessage,
    *,
    pretty_print: bool = False,
    metadata_links: dict[str, list[str]] | None = None,
    base_url: str | None = None,
) -> bytes:
    """Serialise a StructureMessage to SDMX-ML 3.1 XML bytes.

    Args:
        msg: The StructureMessage to serialise.
        pretty_print: If True, indent the output.
        metadata_links: Optional ``artefact_urn -> [metadataset_urn, ...]`` map
            (from ``builders.metadata_annotation.build_metadata_target_links``).
            Emits a native SDMX 3.1 ``<com:Link rel="metadata">`` per report.
        base_url: Optional base for the emitted link hrefs.

    Returns:
        The serialised message as XML bytes.
    """
    return _WRITER.write(msg, pretty_print=pretty_print, metadata_links=metadata_links, base_url=base_url)


def write_kept(msg: StructureMessage, *, pretty_print: bool = False) -> bytes:
    """`write` for a message `without_dropped` has already narrowed to SDMX-ML 3.1, for `serialize` (#464)."""
    return _WRITER.write_kept(msg, pretty_print=pretty_print)
