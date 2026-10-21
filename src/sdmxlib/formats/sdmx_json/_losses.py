"""What an SDMX-JSON read could not hold, declared and detected (#422).

The SDMX-JSON twin of `formats/sdmx_ml/_losses.py`. JSON is read through typed
`msgspec` structs, and a struct ignores every key it does not declare, so each
of the two halves needs its own way to see what was skipped:

* **A section the reader has no struct for** — `processes`, `metadataConstraints`.
  `data` is decoded a second time as `dict[str, msgspec.Raw]`, which reads
  its keys and leaves every value unparsed, and each entry of a section no
  `StructureDataJson` field names is refused. As in SDMX-ML the account is of
  the document, not of a list: a section nobody thought to name is caught.
* **A key inside an artefact that no DTO declares** — a format's `startTime`,
  an item scheme's `isPartial`. Found by walking each artefact's raw JSON
  beside the struct that decoded it: a key the struct has no field for was
  skipped by the decode, so it is reported, named ``<Struct>.<key>``. Like
  the section account this is of the document, so a key nobody listed is
  caught — which hand-written rows were not, as the element census showed.

SDMX-ML's third shape — content a reader decodes and the model cannot hold —
has no JSON counterpart: every key a DTO declares is held (#422), and the JSON
census (`tests/unit/test_json_census.py`) fails on one that is declared and
never written, so a struct cannot grow a field the model drops.

What a read *supplies* is declared here too, as in SDMX-ML: `ASSUMPTIONS`,
each counted on the DTO that decoded an artefact (#474).
"""

import functools
from collections.abc import Callable

import msgspec
from attrs import frozen

from sdmxlib.formats.sdmx_json._dto import WireStruct
from sdmxlib.formats.sdmx_json._structure_dto import (
    AgencySchemeJson,
    DataConsumerSchemeJson,
    DataProviderSchemeJson,
    DataStructureJson,
    MaintainableJson,
    MetadataProviderSchemeJson,
    OrganisationUnitSchemeJson,
    StructureDataJson,
    StructureMapJson,
)
from sdmxlib.formats.sdmx_json._structure_ingest import ASSUMED_EPOCH_PERIOD, ASSUMED_VERSION
from sdmxlib.model._referenceable import PAIRS_30
from sdmxlib.model.errors import ReadAssumption, ReadGap, Refusal
from sdmxlib.model.urn import SdmxUrn

# ── sections the reader has no struct for ───────────────────────────────


class _Envelope(WireStruct):
    data: dict[str, msgspec.Raw] | None = None


class _Identity(WireStruct, rename={"agency_id": "agencyID"}):
    id: str = ""
    agency_id: str = ""
    version: str = "1.0"


#: The `data` keys the reader reads: one per `StructureDataJson` field.
_READ = frozenset(field.encode_name for field in msgspec.structs.fields(StructureDataJson))


def raw_sections(data: bytes) -> dict[str, msgspec.Raw]:
    """`data`'s sections by key, each value left unparsed."""
    return msgspec.json.decode(data, type=_Envelope).data or {}


def unread_sections(sections: dict[str, msgspec.Raw]) -> list[Refusal]:
    """A refusal for every artefact in a section no struct reads.

    Args:
        sections: The document's `data`, as `raw_sections` gives it.

    Returns:
        The refusals, in document order. Empty when every section is read.
    """
    refused: list[Refusal] = []
    for key, raw in sections.items():
        if key in _READ:
            continue
        name = _singular(key)
        package = next((p for p, c in PAIRS_30 if c == name), "unknown")
        try:
            entries = msgspec.json.decode(raw, type=list[_Identity])
        except msgspec.ValidationError:
            entries = [_Identity()]
        refused.extend(
            Refusal(
                artefact_class=name,
                urn=str(
                    SdmxUrn[object](
                        package=package, artefact_class=name, agency=e.agency_id, id=e.id, version=e.version
                    )
                ),
                reason=f"sdmxlib does not model {name}",
            )
            for e in entries
        )
    return refused


# ── keys no struct declares ─────────────────────────────────────────────

_UNDECLARED_REASON = "the reader has no field for it, so the decode skipped it"


def undeclared(urn: str, raw: object, dto: object) -> list[ReadGap]:
    """What of one artefact's JSON no struct declared, as one `ReadGap` per key name.

    Args:
        urn: The artefact built from `dto`.
        raw: The artefact's JSON, decoded untyped.
        dto: The struct the reader decoded from the same JSON.

    Returns:
        The losses, in the order first met.
    """
    counts: dict[str, int] = {}
    _walk(raw, dto, counts)
    return [ReadGap(urn=urn, construct=key, count=n, reason=_UNDECLARED_REASON) for key, n in counts.items()]


@functools.cache
def _declared(struct: type[msgspec.Struct]) -> dict[str, str]:
    """A struct class's wire names → field names, once per class.

    Cached on the class, which is fixed: `msgspec.structs.fields` per object
    was most of the walk's cost — a 100,000-code codelist read in 8.7 s took
    16.8 s with it (#422).
    """
    return dict(zip(struct.__struct_encode_fields__, struct.__struct_fields__, strict=True))


def _walk(raw: object, dto: object, counts: dict[str, int]) -> None:
    """Count, under `dto`, every key of `raw` its struct has no field for."""
    if isinstance(dto, msgspec.Struct) and isinstance(raw, dict):
        fields = _declared(type(dto))
        for key, value in raw.items():
            name = fields.get(str(key))
            if name is None:
                construct = f"{type(dto).__name__}.{key}"
                counts[construct] = counts.get(construct, 0) + 1
            else:
                _walk(value, getattr(dto, name), counts)
    elif isinstance(dto, list) and isinstance(raw, list):
        # A list decodes element for element, in order, so the two align.
        for raw_item, dto_item in zip(raw, dto, strict=False):
            _walk(raw_item, dto_item, counts)


def _singular(key: str) -> str:
    """`valueLists` → `ValueList`, `processes` → `Process`, `reportingTaxonomies` → `ReportingTaxonomy`."""
    if key.endswith("ies"):
        stem = key[:-3] + "y"
    elif key.endswith("sses"):
        stem = key[:-2]
    else:
        stem = key.removesuffix("s")
    return stem[:1].upper() + stem[1:]


# ── values the reader assumes ───────────────────────────────────────────


@frozen
class Assumption:
    """One required construct the reader fills in when a document omits it (#474).

    SDMX-ML's `Assumption`, counted on the DTO rather than with an XPath.

    Attributes:
        construct: The missing construct, as the format spells it.
        assumed: The value the reader uses in its place.
        reason: Why that value.
        count: How many occurrences an artefact's DTO is missing.
    """

    construct: str
    assumed: str
    reason: str
    count: Callable[[MaintainableJson], int]


def _unrelated_attributes(dto: MaintainableJson) -> int:
    if not isinstance(dto, DataStructureJson) or dto.data_structure_components is None:
        return 0
    attribute_list = dto.data_structure_components.attribute_list
    if attribute_list is None:
        return 0
    return sum(a.attribute_relationship is None for a in attribute_list.attributes)


def _unrelated_usages(dto: MaintainableJson) -> int:
    if not isinstance(dto, DataStructureJson) or dto.data_structure_components is None:
        return 0
    attribute_list = dto.data_structure_components.attribute_list
    if attribute_list is None:
        return 0
    return sum(u.attribute_relationship is None for u in attribute_list.metadata_attribute_usages or ())


def _dimensions_matched_by_case(dto: MaintainableJson) -> int:
    """A DSD's relationship dimensions that are no dimension's id but name exactly one ignoring case (#454)."""
    if not isinstance(dto, DataStructureJson) or dto.data_structure_components is None:
        return 0
    components = dto.data_structure_components
    ids = {d.id for d in components.dimension_list.dimensions if d.id is not None}
    if components.dimension_list.time_dimension is not None and components.dimension_list.time_dimension.id:
        ids.add(components.dimension_list.time_dimension.id)
    folded: dict[str, int] = {}
    for dimension_id in ids:
        folded[dimension_id.casefold()] = folded.get(dimension_id.casefold(), 0) + 1
    attribute_list = components.attribute_list
    if attribute_list is None:
        return 0
    relationships = [a.attribute_relationship for a in attribute_list.attributes]
    relationships += [u.attribute_relationship for u in attribute_list.metadata_attribute_usages or ()]
    return sum(
        1
        for relationship in relationships
        if relationship is not None
        for name in relationship.dimensions or ()
        if name not in ids and folded.get(name.casefold()) == 1
    )


def _epoch_maps_without_period(dto: MaintainableJson) -> int:
    if not isinstance(dto, StructureMapJson):
        return 0
    return sum(m.epoch_period is None for m in dto.epoch_maps or ())


def _time_dimensions_without_format(dto: MaintainableJson) -> int:
    if not isinstance(dto, DataStructureJson) or dto.data_structure_components is None:
        return 0
    td = dto.data_structure_components.dimension_list.time_dimension
    return int(td is not None and (td.local_representation is None or td.local_representation.format is None))


#: Organisation schemes are versionless in the SDMX 3.0 model, which SDMX-ML
#: 3.0 says by prohibiting the attribute; SDMX-JSON 2.0 just leaves it out.
_VERSIONLESS = (
    AgencySchemeJson,
    DataProviderSchemeJson,
    MetadataProviderSchemeJson,
    DataConsumerSchemeJson,
    OrganisationUnitSchemeJson,
)


def _unversioned(dto: MaintainableJson) -> int:
    return int(dto.version is None and not isinstance(dto, _VERSIONLESS))


_RELATIONSHIP_REASON = (
    "read as attached to the dataset, the tolerance SDMX-ML's reader applies to the same omission (#421)"
)

#: Every required construct the reader fills in when a document omits it. A
#: row leaves when the reader refuses instead, or the model holds the absence.
ASSUMPTIONS: tuple[Assumption, ...] = (
    Assumption("attributes[].attributeRelationship", "attachment DATASET", _RELATIONSHIP_REASON, _unrelated_attributes),
    Assumption(
        "metadataAttributeUsages[].attributeRelationship",
        "attachment DATASET",
        _RELATIONSHIP_REASON,
        _unrelated_usages,
    ),
    Assumption(
        "attributeRelationship.dimensions[]",
        "the dimension of that id in another case",
        "SDMX ids are case-sensitive, but a reference naming exactly one dimension ignoring case can mean only "
        "that one, as the SDMX-ML readers read Eurostat's (#454)",
        _dimensions_matched_by_case,
    ),
    Assumption(
        "epochMaps[].epochPeriod",
        ASSUMED_EPOCH_PERIOD,
        "the period an epoch counts in has no default in the schema; a day is the finest it allows",
        _epoch_maps_without_period,
    ),
    Assumption(
        "dataStructureComponents.dimensionList.timeDimension.localRepresentation.format",
        "ObservationalTimePeriod",
        "the schema requires a time dimension's format and defaults its type to ObservationalTimePeriod (#507)",
        _time_dimensions_without_format,
    ),
    Assumption(
        "version",
        ASSUMED_VERSION,
        "SDMX-JSON 2.0 reads an artefact without a version as un-versioned, which the model cannot hold (#478)",
        _unversioned,
    ),
)


def assumed(urn: str, dto: MaintainableJson) -> list[ReadAssumption]:
    """What the read supplied for the artefact `dto` decoded, per `ASSUMPTIONS` row."""
    found: list[ReadAssumption] = []
    for row in ASSUMPTIONS:
        n = row.count(dto)
        if n:
            found.append(
                ReadAssumption(urn=urn, construct=row.construct, assumed=row.assumed, count=n, reason=row.reason)
            )
    return found
