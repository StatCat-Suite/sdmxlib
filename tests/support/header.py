"""What the element and attribute censuses find unwritten under the message header, and why (#447, #480).

The generated table keys an element by its name, so ``mes:Header`` holds every
message type's header at once: a structure message's seven elements, which
the maximal fixture now writes, and a data or reference-metadata message's
more. The censuses read structure messages, so those more are unwritten
there. Each is classified here, exactly, with its reason.

``mes:Structure`` is two elements by one name: a structure message's root and
a data or metadata header's structure reference. The footer is the root's.
Until #480 one rule set the whole header aside, and the name collision let it
absorb the footer unexamined.
"""

from sdmxlib.formats.sdmx_ml._allowed_children import ALLOWED

_DATA = "a data message's header: sdmxlib writes no data messages"
_METADATA = "a reference-metadata header's, written by the GenericMetadata writer; the census reads structure messages"
_FOOTER = "a message footer carries a registry's error or warning messages; sdmxlib writes none"

#: The unwritten element pairs under a header (or the footer), by version.
ELEMENTS: dict[str, dict[tuple[str, str], str]] = {
    version: {
        ("mes:Header", "mes:DataProvider"): _DATA,
        ("mes:Header", "mes:ReportingBegin"): _DATA,
        ("mes:Header", "mes:ReportingEnd"): _DATA,
        ("mes:Header", "mes:EmbargoDate"): _DATA,
        ("mes:Header", "mes:DataSetAction"): _METADATA,
        ("mes:Header", "mes:DataSetID"): _METADATA,
        ("mes:Header", "mes:Extracted"): _METADATA,
        ("mes:Header", "mes:Structure"): _METADATA,
        ("mes:Structure", "com:Structure"): _METADATA,
        ("mes:Structure", "com:StructureUsage"): _METADATA,
        ("mes:Structure", provision): _DATA,
        ("mes:Structure", "footer:Footer"): _FOOTER,
        # 2.1 has no metadata provider: its reference metadata is out of scope (#471).
        **({} if version == "2.1" else {("mes:Header", "mes:MetadataProvider"): _METADATA}),
    }
    # 2.1's schema spells it ProvisionAgrement.
    for version, provision in (
        ("2.1", "com:ProvisionAgrement"),
        ("3.0", "com:ProvisionAgreement"),
        ("3.1", "com:ProvisionAgreement"),
    )
}

#: The unwritten attribute pairs under a header's structure reference, the same in every version.
ATTRIBUTES: dict[tuple[str, str], str] = {
    ("mes:Structure", "structureID"): _METADATA,
    **{
        ("mes:Structure", attribute): _DATA
        for attribute in (
            "dimensionAtObservation",
            "explicitMeasures",
            "namespace",
            "schemaURL",
            "serviceURL",
            "structureURL",
        )
    },
}


def _reachable(version: str, start: str) -> set[str]:
    seen = {start}
    stack = [start]
    while stack:
        for child in ALLOWED[version].get(stack.pop(), ()):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def header_only(version: str) -> frozenset[str]:
    """Elements reachable from ``mes:Header`` and not from ``mes:Structures``: what only a header holds."""
    return frozenset(_reachable(version, "mes:Header") - _reachable(version, "mes:Structures"))
