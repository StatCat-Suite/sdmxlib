"""SDMX-JSON 1.0 writer for ContentConstraint (availability) payloads.

The SDMX REST ``/availability`` endpoint pins SDMX-JSON **1.0**, whose
structure message names the artefact ``contentConstraints`` — the 1.0
spelling predates the 2.1 rename to ``dataConstraints`` that the
`sdmxlib.formats.sdmx_json.writer` (2.0) module emits. This module
"downgrades" our SDMX-3-style `DataConstraint` to the 1.0 wire
shape so the same domain object can be served on the legacy availability
endpoint and pre-baked into bundle caches.

The 1.0 contentConstraint differs from the 2.x dataConstraint in two
ways we care about here:

* it carries a ``type`` (``"Actual"`` / ``"Allowed"``), absent in 2.x;
* its cube-region inclusion flag is ``isIncluded`` (2.x uses ``include``).

Determinism: cube-region key values are sorted by dimension id and each
value list is sorted lexicographically, and no wall-clock timestamp is
stamped unless the caller passes ``prepared`` explicitly. Two calls with
the same inputs therefore produce byte-identical output — the bundle
cache acceptance criterion.

Part of the msgspec migration (milestone: SDMX-JSON dict+orjson → msgspec):
the 1.0 wire shape is typed ``msgspec.Struct`` DTOs (the 1.0 spelling
differs enough from the 2.0 DTOs that it warrants its own set), encoded via
the shared `_dto.ENCODER`.

Example:
    from sdmxlib.formats.sdmx_json_1_0.writer import write_contentconstraint

    json_bytes = write_contentconstraint(cc)
"""

from datetime import datetime

import msgspec

from sdmxlib.formats.sdmx_json._dto import ENCODER, Meta, Sender, WireStruct, _camel_rename, emit_istr, prepared_text
from sdmxlib.formats.sdmx_json._periods import zoneless_periods
from sdmxlib.formats.sdmx_json._structure_dto import (
    QueryableDataSourceJson,
    TimeRangeJson,
    to_queryable_data_source,
    to_time_range,
)
from sdmxlib.model.constraint import (
    ComponentValueSet,
    ConstraintAttachment,
    CubeRegion,
    DataConstraint,
    KeyValue,
    RegionValue,
    TimeRange,
)
from sdmxlib.model.validation import ValidationError, ValidationIssue

# ── Wire Structs (1.0 spelling) ─────────────────────────────────────────────


class KeyValueJson(WireStruct, rename=_camel_rename):
    """A cube-region ``keyValues[*]`` or ``attributes[*]``: an id + its values, or a time range.

    1.0's `CubeRegionKeyType` and `AttributeValueSetType` are the same shape.
    `values` is emitted whenever there is no time range, even empty — the
    availability builders' "no data" — and never beside one.
    """

    id: str
    values: list[str] | None = None
    time_range: TimeRangeJson | None = None


class CubeRegionJson(WireStruct, rename=_camel_rename):
    """A 1.0 cube region — ``isIncluded`` (2.x uses ``include``), keyValues and attributes."""

    # Always emitted; REQUIRED so a real False is never dropped by omit_defaults.
    is_included: bool
    key_values: list[KeyValueJson] = msgspec.field(default_factory=list)
    #: 1.0's name for what 2.x calls ``components``.
    attributes: list[KeyValueJson] = msgspec.field(default_factory=list)


class SetReferenceJson(WireStruct, rename=_camel_rename):
    """1.0 ``SetReferenceType``: a data set, named by its provider and id."""

    data_provider: str
    id: str


class ConstraintAttachmentJson(WireStruct, rename=_camel_rename):
    """1.0 ``ContentConstraintAttachmentType`` — 2.1's ContentConstraint attachment, in JSON.

    One data set and one simple data source, singular, as 2.1's
    `ContentConstraintAttachmentType` restricts them; the artefact kinds are
    arrays.
    """

    data_provider: str | None = None
    data_set: SetReferenceJson | None = None
    simple_data_source: str | None = None
    data_structures: list[str] | None = None
    dataflows: list[str] | None = None
    provision_agreements: list[str] | None = None
    queryable_data_sources: list[QueryableDataSourceJson] | None = None


class ContentConstraintJson(WireStruct, rename=_camel_rename):
    """A 1.0 ``contentConstraints[*]`` — the downgraded DataConstraint."""

    # id/agencyID/version/type always emit — REQUIRED so omit_defaults can't drop them.
    id: str
    agency_id: str
    version: str
    #: The 1.0 Allowed/Actual role, inline (absent in 2.x).
    type: str
    name: str | None = None
    names: dict[str, str] | None = None
    description: str | None = None
    descriptions: dict[str, str] | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    constraint_attachment: ConstraintAttachmentJson | None = None
    cube_regions: list[CubeRegionJson] | None = None


# ── Converters (model → DTO) ────────────────────────────────────────────────


def _to_attachment(attach: ConstraintAttachment) -> ConstraintAttachmentJson | None:
    """ConstraintAttachment → 1.0 ``constraintAttachment``, every alternative it holds.

    Until #422 this wrote a provider under ``dataProviders``, a key 1.0 does
    not define; the schema allows unknown keys, so nothing failed. Several
    data sets or simple data sources are refused before this runs.
    """
    if attach.is_empty:
        return None
    data_set = attach.data_sets[0] if attach.data_sets else None
    return ConstraintAttachmentJson(
        data_provider=str(attach.data_provider.urn) if attach.data_provider is not None else None,
        data_set=SetReferenceJson(data_provider=str(data_set.data_provider.urn), id=data_set.id) if data_set else None,
        simple_data_source=attach.simple_data_sources[0] if attach.simple_data_sources else None,
        data_structures=[str(r.urn) for r in attach.data_structures] or None,
        dataflows=[str(r.urn) for r in attach.dataflows] or None,
        provision_agreements=[str(r.urn) for r in attach.provision_agreements] or None,
        queryable_data_sources=[to_queryable_data_source(source) for source in attach.queryable_data_sources] or None,
    )


def _unsayable_attachment(attach: ConstraintAttachment) -> list[tuple[str, str]]:
    """What of an attachment SDMX-JSON 1.0 has no form for, as (field, message).

    1.0's attachment holds one data set and one simple data source.
    """
    return [
        (name, f"SDMX-JSON 1.0 attaches a content constraint to at most one {one}, not {len(values)}")
        for name, one, values in (
            ("data_sets", "data set", attach.data_sets),
            ("simple_data_sources", "simple data source", attach.simple_data_sources),
        )
        if len(values) > 1
    ]


def _selection(selection_id: str, values: frozenset[str], time_range: TimeRange | None) -> KeyValueJson:
    if time_range is not None:
        return KeyValueJson(id=selection_id, time_range=to_time_range(time_range))
    return KeyValueJson(id=selection_id, values=sorted(values))


def _to_cube_region(region: CubeRegion) -> CubeRegionJson:
    """CubeRegion → 1.0 shape: ``isIncluded`` + deterministically sorted keyValues and attributes.

    Selections are ordered by id and each value list is sorted
    lexicographically so the output is deterministic regardless of the
    frozenset iteration order on `KeyValue.values`. A time range is written
    in the 2.x spelling, which 1.0's `TimeRangeValueType` shares.
    """
    return CubeRegionJson(
        is_included=region.include,
        key_values=[
            _selection(kv.dimension_id, kv.codes(), kv.time_range)
            for kv in sorted(region.keys, key=lambda kv: kv.dimension_id)
        ],
        attributes=[
            _selection(c.component_id, c.codes(), c.time_range)
            for c in sorted(region.components, key=lambda c: c.component_id)
        ],
    )


def _to_content_constraint(cc: DataConstraint) -> ContentConstraintJson:
    name, names = emit_istr(cc.name)
    desc, descs = emit_istr(cc.description)
    return ContentConstraintJson(
        id=cc.id,
        agency_id=cc.agency_id,
        version=cc.version,
        type=cc.constraint_type.value,
        name=name,
        names=names,
        description=desc,
        descriptions=descs,
        valid_from=cc.valid_from.isoformat() if cc.valid_from is not None else None,
        valid_to=cc.valid_to.isoformat() if cc.valid_to is not None else None,
        constraint_attachment=_to_attachment(cc.attachment),
        cube_regions=[_to_cube_region(r) for r in cc.regions] or None,
    )


# ── Entry point ─────────────────────────────────────────────────────────────


def write_contentconstraint(
    cc: DataConstraint,
    *,
    prepared: datetime | None = None,
    message_id: str = "SDMXLIB",
    sender_id: str = "SDMXLIB",
    pretty_print: bool = False,
) -> bytes:
    """Serialise a `DataConstraint` to SDMX-JSON 1.0 structure-message bytes.

    Emits the 1.0 ``meta`` / ``data`` / ``errors`` envelope with the
    constraint under ``data.contentConstraints``.

    Args:
        cc: The constraint to serialise (typically an ``Actual`` availability
            constraint produced by
            `sdmxlib.builders.contentconstraint.build_dataflow_constraint`).
        prepared: Value for ``meta.prepared``. ``None`` (the default) omits
            it, keeping output byte-identical across builds — the bundle
            cache determinism requirement. Pass a stable, build-derived
            instant when full 1.0-schema conformance is required (the schema
            lists ``prepared`` as required). Timezone-aware: the schema
            requires a zone, so a naive one is refused with `ValueError`.
        message_id: Value for ``meta.id``.
        sender_id: Value for ``meta.sender.id``.
        pretty_print: If True, indent the output with 2-space indentation.

    Returns:
        UTF-8 encoded JSON bytes of an SDMX-JSON 1.0 structure message.

    Raises:
        ValidationError: If a value set excludes its values or carries a
            qualifier — 3.x's `removePrefix` or validity, or a value's
            cascade, language or validity. 1.0's `CubeRegionKeyType` has
            none of them, and an excluding set written without its flag
            would read as an inclusion — the opposite (#422). The
            availability builders this writer serves produce neither.
    """
    issues = [
        ValidationIssue(path=f"DataConstraint:{cc.id}.attachment.{name}", message=message)
        for name, message in _unsayable_attachment(cc.attachment)
    ]
    issues += [
        ValidationIssue(path=f"DataConstraint:{cc.id}.regions[{r}].{kind}[{selection_id}].{what}", message=message)
        for r, region in enumerate(cc.regions)
        for kind, selection_id, selection in (
            *(("keys", kv.dimension_id, kv) for kv in region.keys),
            *(("components", c.component_id, c) for c in region.components),
        )
        for what, message in _unsayable_in_1_0(selection)
    ]
    issues += zoneless_periods(cc)
    if issues:
        raise ValidationError(issues)
    doc: dict[str, object] = {
        "meta": Meta(
            id=message_id,
            prepared=prepared_text(prepared) if prepared is not None else "",
            sender=Sender(id=sender_id),
        ),
        "data": {"contentConstraints": [_to_content_constraint(cc)]},
        # 1.0 envelope always carries the (empty) errors array.
        "errors": [],
    }
    encoded = ENCODER.encode(doc)
    return msgspec.json.format(encoded, indent=2) if pretty_print else encoded


def _unsayable_in_1_0(selection: KeyValue | ComponentValueSet) -> list[tuple[str, str]]:
    """What of a value set SDMX-JSON 1.0 has no form for, as (field, message)."""
    fix = "write SDMX-JSON 2.0 or SDMX-ML instead"
    found: list[tuple[str, str]] = []
    if not selection.include:
        found.append(("include", f"SDMX-JSON 1.0 cannot exclude a value set's values; {fix}"))
    if selection.remove_prefix or (isinstance(selection, KeyValue) and (selection.valid_from or selection.valid_to)):
        found.append(("remove_prefix", f"SDMX-JSON 1.0 has no value-set qualifiers; {fix}"))
    time_range = selection.time_range
    if time_range is not None and (time_range.valid_from or time_range.valid_to):
        found.append(("time_range", f"SDMX-JSON 1.0 has no time range validity; {fix}"))
    if any(v != RegionValue(v.value) for v in selection.values):
        found.append(("values", f"SDMX-JSON 1.0 has no value qualifiers (cascade, language, validity); {fix}"))
    return found
