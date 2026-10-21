"""What mutation found the SDMX-JSON structure reader's tests could not see (#458).

The triage of the rest — equivalent, and why — is in
`docs/design/mutation-evidence.md`.

Most of these share one cause, the one installment 4 found for the 2.1
reader: the fixtures state everything. SDMX-JSON carries each text twice, a
singular `name` in one language and a `names` map, and the writer always
emits both, so a reader that ignored the singular form read every fixture
correctly.
"""

import functools
import json
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any

import attrs
import pytest

import sdmxlib as sl
from sdmxlib.formats.gaps import without_dropped
from sdmxlib.model.errors import SdmxParseError
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import StructureMessage
from tests.support import maximal, shapes
from tests.support.field_census import differences
from tests.support.narrow import present
from tests.support.roundtrip import as_structure

pytestmark = pytest.mark.unit

#: The text fields SDMX-JSON pairs as `x` and `xs`.
_PAIRED = ("name", "description", "department", "role")


@functools.cache
def _written() -> bytes:
    """`maximal` as SDMX-JSON, less the measure dimension JSON cannot carry."""
    msg = maximal.message()
    dsds = [attrs.evolve(d, measure_dimension=None) for d in msg.data_structures]
    msg = attrs.evolve(msg, data_structures=sl.ArtefactList(dsds))
    return sl.serialize(without_dropped(msg, "json"), version="json")


def _strip_maps(node: object) -> int:
    """Drop every `xs` map whose singular `x` is beside it; return how many."""
    dropped = 0
    if isinstance(node, dict):
        for field in _PAIRED:
            if isinstance(node.get(field), str) and f"{field}s" in node:
                del node[f"{field}s"]
                dropped += 1
        for value in node.values():
            dropped += _strip_maps(value)
    elif isinstance(node, list):
        for value in node:
            dropped += _strip_maps(value)
    return dropped


def _texts(obj: object, path: str = "", seen: set[int] | None = None) -> Iterator[tuple[str, InternationalString]]:
    """Every paired text field in an artefact graph, by path."""
    seen = set() if seen is None else seen
    if id(obj) in seen:
        return
    seen.add(id(obj))
    cls = type(obj)
    if attrs.has(cls):
        for field in attrs.fields(cls):
            value: object = getattr(obj, field.name, None)
            if field.name in _PAIRED and isinstance(value, InternationalString):
                yield f"{path}.{field.name}", value
            elif not isinstance(value, (str, bytes, InternationalString, sl.Ref)):
                yield from _texts(value, f"{path}.{field.name}", seen)
    elif isinstance(obj, Iterable) and not isinstance(obj, (str, bytes, Mapping)):
        for i, item in enumerate(obj):
            yield from _texts(item, f"{path}[{i}]", seen)


def _all_texts(msg: StructureMessage) -> dict[str, InternationalString]:
    return {f"{type(a).__name__}:{a.id}{path}": text for a in msg.all_artefacts() for path, text in _texts(a)}


def test_a_singular_text_without_its_map_is_read() -> None:
    """A producer may send `name` alone; the reader falls back to it (`ingest_istr`)."""
    full = as_structure(sl.parse(_written()))
    doc = json.loads(_written())
    dropped = _strip_maps(doc)
    singular = as_structure(sl.parse(json.dumps(doc).encode()))
    expected, got = _all_texts(full), _all_texts(singular)
    assert expected.keys() == got.keys()
    # A floor, not a count: `maximal` grows, and a text shared between
    # artefacts (each one's maintainer) is reached once per artefact.
    assert dropped >= 100, f"the case lost its subject: only {dropped} maps stripped"
    held = [path for path, text in expected.items() if text]
    lost = [path for path in held if not got[path]]
    assert lost == [], f"{len(lost)} text(s) read empty from their singular form, e.g. {lost[:3]}"


# ── edits to the written document ─────────────────────────────────────────


def _doc() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(_written())
    return doc


def _read(doc: dict[str, Any], *, strict: bool = True) -> StructureMessage:
    return as_structure(sl.parse(json.dumps(doc).encode(), strict=strict))


def _refusals(doc: dict[str, Any]) -> list[str]:
    return [f"{r.urn}: {r.reason}" for r in _read(doc, strict=False).refused]


def _dsd(doc: dict[str, Any]) -> dict[str, Any]:
    dsd: dict[str, Any] = doc["data"]["dataStructures"][0]
    return dsd


# ── what an absent field means ─────────────────────────────────────────────


def test_a_member_value_without_cascade_values_has_none() -> None:
    doc = _doc()
    member = doc["data"]["codelists"][1]["codelistExtensions"][0]["inclusiveCodeSelection"]["memberValues"][0]
    del member["cascadeValues"]
    (extension,) = _read(doc).codelists[1].extensions
    assert present(extension.selection).members[0].cascade is None


def test_a_cube_region_without_include_includes() -> None:
    doc = _doc()
    regions = [r for c in doc["data"]["dataConstraints"] for r in c.get("cubeRegions", [])]
    assert regions, "the case lost its subject"
    for region in regions:
        region["include"] = False
    assert not any(r.include for c in _read(doc).constraints for r in c.regions)
    for region in regions:
        del region["include"]
    assert all(r.include for c in _read(doc).constraints for r in c.regions)


def test_a_time_dimension_without_an_id_is_time_period() -> None:
    doc = _doc()
    del _dsd(doc)["dataStructureComponents"]["dimensionList"]["timeDimension"]["id"]
    assert present(_read(doc).data_structures[0].time_dimension).id == "TIME_PERIOD"


def test_every_maintainable_reads_its_own_version() -> None:
    """`maximal` versions everything 1.0, which is also what an absent version means."""
    text = _written().replace(b'"version":"1.0"', b'"version":"2.0"').replace(b"(1.0)", b"(2.0)")
    msg = as_structure(sl.parse(text, strict=False))
    fixed = {"AgencyScheme", "DataProviderScheme", "MetadataProviderScheme"}
    read = {type(a).__name__: a.version for a in msg.all_artefacts() if type(a).__name__ not in fixed}
    assert {"Hierarchy", "MetadataProvisionAgreement"} <= read.keys(), "the case lost its subject"
    assert set(read.values()) == {"2.0"}, read


def test_a_single_primary_measure_is_read_as_the_measure() -> None:
    """Not in the 2.0 structure schema, but its own `constructed-sample-full.json` writes it."""
    doc = _doc()
    measure_list = _dsd(doc)["dataStructureComponents"]["measureList"]
    (measure,) = measure_list.pop("measures")
    measure_list["primaryMeasure"] = measure
    assert [m.id for m in _read(doc).data_structures[0].measures] == [measure["id"]]


# ── a refusal names what it refuses ───────────────────────────────────────


def test_a_max_occurs_that_is_neither_a_count_nor_unbounded_is_refused() -> None:
    doc = _doc()
    doc["data"]["conceptSchemes"][0]["concepts"][2]["coreRepresentation"]["maxOccurs"] = "many"
    assert any("<maxOccurs>: 'many' is neither a count nor 'unbounded'" in refusal for refusal in _refusals(doc)), (
        _refusals(doc)
    )


def test_a_time_dimension_without_its_concept_is_refused_by_name() -> None:
    doc = _doc()
    del _dsd(doc)["dataStructureComponents"]["dimensionList"]["timeDimension"]["conceptIdentity"]
    assert any("<timeDimension>: the schema requires a conceptIdentity" in refusal for refusal in _refusals(doc)), (
        _refusals(doc)
    )


@pytest.mark.parametrize(
    ("kind", "locate"),
    [
        ("dimension", lambda dsd: dsd["dataStructureComponents"]["dimensionList"]["dimensions"][0]),
        ("attribute", lambda dsd: dsd["dataStructureComponents"]["attributeList"]["attributes"][0]),
        ("measure", lambda dsd: dsd["dataStructureComponents"]["measureList"]["measures"][0]),
    ],
)
def test_a_component_with_neither_id_nor_concept_is_refused_by_kind(
    kind: str, locate: Callable[[dict[str, Any]], dict[str, Any]]
) -> None:
    doc = _doc()
    component = locate(_dsd(doc))
    del component["id"], component["conceptIdentity"]
    expected = f"<{kind}>: a component without an id takes its concept's, and this one has no conceptIdentity"
    assert any(expected in refusal for refusal in _refusals(doc)), _refusals(doc)


def test_a_metadata_attribute_with_neither_id_nor_concept_is_refused_by_kind() -> None:
    doc = _doc()
    attribute = doc["data"]["metadataStructures"][0]["metadataStructureComponents"]["metadataAttributeList"][
        "metadataAttributes"
    ][0]
    del attribute["id"], attribute["conceptIdentity"]
    expected = "<metadataAttribute>: a component without an id takes its concept's, and this one has no conceptIdentity"
    assert any(expected in refusal for refusal in _refusals(doc)), _refusals(doc)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("validFrom", "a validity timestamp must be an ISO 8601 date-time"),
    ],
)
def test_a_malformed_validity_names_what_it_is(field: str, message: str) -> None:
    doc = _doc()
    doc["data"]["agencySchemes"][0][field] = "x"
    with pytest.raises(SdmxParseError, match=rf"^{message}, got 'x'$"):
        _read(doc)


def test_a_malformed_cascade_names_the_attribute() -> None:
    doc = _doc()
    region_values = [
        v
        for c in doc["data"]["dataConstraints"]
        for r in c.get("cubeRegions", [])
        for kv in r.get("keyValues", [])
        for v in kv.get("values", [])
        if isinstance(v, dict) and "cascadeValues" in v
    ]
    assert region_values, "the case lost its subject"
    region_values[0]["cascadeValues"] = "sideways"
    with pytest.raises(SdmxParseError, match=r"^cascadeValues must be one of"):
        _read(doc)


def test_an_organisation_scheme_at_another_version_is_refused() -> None:
    """Its version is fixed at 1.0; it is refused only *because* `version` is read."""
    text = _written().replace(b'"version":"1.0"', b'"version":"2.0"').replace(b"(1.0)", b"(2.0)")
    msg = as_structure(sl.parse(text, strict=False))
    refused = sorted(r.artefact_class for r in msg.refused)
    assert refused == [
        "AgencyScheme",
        "DataConsumerScheme",
        "DataProviderScheme",
        "MetadataProviderScheme",
        "OrganisationUnitScheme",
    ]


def test_a_queryable_data_source_is_read_whole() -> None:
    """`DataConstraintAttachmentType` puts them beside dataflows, as SDMX-ML does.

    Built by hand: SDMX-JSON 2.1 dropped data sources, and the writer's
    `"json"` must validate against 2.1 as well as 2.0, so it refuses them.
    A 2.0 document may still carry one.
    """
    doc = _doc()
    constraint = next(c for c in doc["data"]["dataConstraints"] if "dataflows" in c.get("constraintAttachment", {}))
    source = {
        "isRESTDatasource": False,
        "isWebServiceDatasource": True,
        "dataURL": "https://example.org/ws",
        "wsdlURL": "https://example.org/ws?wsdl",
        "wadlURL": "https://example.org/rest?wadl",
    }
    constraint["constraintAttachment"]["queryableDataSources"] = [source]
    read = next(c for c in _read(doc).constraints if c.id == constraint["id"])
    (got,) = read.attachment.queryable_data_sources
    assert (got.data_url, got.is_rest_datasource, got.is_web_service_datasource, got.wsdl_url, got.wadl_url) == (
        "https://example.org/ws",
        False,
        True,
        "https://example.org/ws?wsdl",
        "https://example.org/rest?wadl",
    )


# ── everything written is read back, but what JSON declares it cannot carry ──


def _declared(gaps: tuple[object, ...]) -> set[str]:
    return {str(getattr(gap, "field", "")) for gap in gaps}


def _is_declared(path: str, declared: set[str]) -> bool:
    """Whether a difference falls under a declared gap: `tree[3].version` under `tree[].version`."""
    shape = re.sub(r"\[\d+\]", "[]", path.lstrip("."))
    return any(shape == field or shape.startswith((f"{field}.", f"{field}[")) for field in declared)


def _took_its_concepts(before: object, after: object) -> bool:
    """A component written without a representation reads back with its concept's.

    `RepresentationMixin.__attrs_post_init__` fills `representation` from the
    concept's core representation once the concept resolves, which it does in
    a message that carries the scheme. That is the model's rule, not a loss.
    """
    if getattr(before, "representation", object()) is not None:
        return False
    concept = getattr(after, "concept", None)
    if not isinstance(concept, sl.Ref) or not concept.resolved:
        return False
    supplied: object = getattr(after, "representation", None)
    return bool(supplied == concept().core_representation)


def _concept_supplied(before: object, after: object, path: str) -> bool:
    """`path` is a `.representation` the component took from its concept."""
    if not path.endswith(".representation"):
        return False
    owner = path.removesuffix(".representation")
    return _took_its_concepts(_at(before, owner), _at(after, owner))


def _at(obj: object, path: str) -> object:
    for step in re.findall(r"\.(\w+)|\[(\d+)\]", path):
        attribute, index = step
        if attribute:
            obj = getattr(obj, attribute)
        else:
            assert isinstance(obj, Iterable)
            obj = list(obj)[int(index)]
    return obj


def test_every_artefact_reads_back_what_was_written_but_its_declared_gaps() -> None:
    """A census over the whole of `maximal`, filtered through `sl.unrepresentable`.

    The per-case round-trip guard (`test_roundtrip_property_guard`) names its
    cases, and an organisation scheme map was not one: its `uri`, `links`,
    validity and `isPartialLanguage` could all go unread.
    """
    msg = maximal.message()
    dsds = [attrs.evolve(d, measure_dimension=None) for d in msg.data_structures]
    msg = without_dropped(attrs.evolve(msg, data_structures=sl.ArtefactList(dsds)), "json")
    back = {str(sl.artefact_urn(a)): a for a in as_structure(sl.parse(_written())).all_artefacts()}
    found = {}
    for artefact in msg.all_artefacts():
        declared = _declared(sl.unrepresentable(artefact, "json"))
        read = back[str(sl.artefact_urn(artefact))]
        diffs = [
            d.path
            for d in differences(artefact, read)
            if not _is_declared(d.path, declared) and not _concept_supplied(artefact, read, d.path)
        ]
        if diffs:
            found[f"{type(artefact).__name__}:{artefact.id}"] = diffs
    assert found == {}


def test_a_position_out_of_document_order_is_refused_by_name() -> None:
    """msgspec refuses a non-integer `position` at decode, so order is what reaches the check."""
    doc = _doc()
    dimensions = _dsd(doc)["dataStructureComponents"]["dimensionList"]["dimensions"]
    second = {**dimensions[0], "id": "DIM2", "position": 0}
    dimensions[0]["position"] = 1
    dimensions.append(second)
    expected = (
        "<position>: dimension 'DIM2' comes after 'DIM' in the dimension list but says position=0, "
        "not more than its 1; a position must be consistent with document order"
    )
    assert any(expected in refusal for refusal in _refusals(doc)), _refusals(doc)


@pytest.mark.parametrize(
    ("list_key", "member_key", "component_id"),
    [("dimensionList", "dimensions", "DIM"), ("measureList", "measures", "OBS_VALUE")],
)
def test_a_component_without_an_id_takes_its_concepts(list_key: str, member_key: str, component_id: str) -> None:
    """The schema's default (#477): absent, a component's id is its concept's.

    Everything that names the component by id is renamed to the concept's id
    with it, so the document stays self-consistent.
    """
    doc = _doc()
    component = next(c for c in _dsd(doc)["dataStructureComponents"][list_key][member_key] if c["id"] == component_id)
    del component["id"]
    concept_id = component["conceptIdentity"].rsplit(".", 1)[1]
    renamed = json.loads(json.dumps(doc).replace(f'"{component_id}"', f'"{concept_id}"'))
    dsd = _read(renamed).data_structures[0]
    found = dsd.dimensions if list_key == "dimensionList" else dsd.measures
    assert concept_id in [c.id for c in found]
    assert component_id not in [c.id for c in found]


def test_a_malformed_item_map_date_names_what_it_is() -> None:
    doc = _doc()
    doc["data"]["conceptSchemeMaps"][0]["itemMaps"][0]["validFrom"] = "x"
    with pytest.raises(SdmxParseError, match=r"^validFrom as a date-time must be an ISO 8601 date-time, got 'x'$"):
        _read(doc)


def test_an_attachment_to_the_time_dimension_takes_its_case() -> None:
    """`match_dimension_case` is given the time dimension's id too."""
    doc = _doc()
    attribute = _dsd(doc)["dataStructureComponents"]["attributeList"]["attributes"][0]
    attribute["attributeRelationship"] = {"dimensions": ["time_period"]}
    (read,) = (a for a in _read(doc).data_structures[0].attributes if a.id == attribute["id"])
    assert read.related_dimensions == ["TIME_PERIOD"]


@functools.cache
def _written_shapes() -> bytes:
    """`shapes`, which holds the category scheme map `maximal` does not."""
    msg = shapes.message()
    # Less the two data-source constraints the JSON writer refuses: SDMX-JSON
    # 2.1 has no data sources, and "json" output must validate against it.
    kept = [c for c in msg.constraints if c.id not in {"DC_QUERYABLE", "DC_SIMPLE"}]
    msg = attrs.evolve(msg, constraints=sl.ArtefactList(kept))
    return sl.serialize(without_dropped(msg, "json"), version="json")


def test_a_category_scheme_map_s_singular_texts_are_read() -> None:
    doc: dict[str, Any] = json.loads(_written_shapes())
    (csm,) = doc["data"]["categorySchemeMaps"]
    assert {"name", "names", "description", "descriptions"} <= csm.keys(), "the case lost its subject"
    del csm["names"], csm["descriptions"]
    (read,) = _read(doc).category_scheme_maps
    assert read.name
    assert read.description


def test_a_dsd_without_components_reads_as_the_model_s_defaults() -> None:
    """`dataStructureComponents` is optional; a stub reads as an empty DSD.

    `maximal` has every list, so no test had read a DSD without them.
    """
    doc = _doc()
    del _dsd(doc)["dataStructureComponents"]
    (dsd,) = _read(doc, strict=False).data_structures
    empty = sl.DataStructure(id=dsd.id, maintainer=dsd.maintainer, name=dsd.name)
    for field in (
        "dimensions",
        "measures",
        "groups",
        "dimension_descriptor_annotations",
        "attribute_descriptor_annotations",
        "measure_descriptor_annotations",
    ):
        assert getattr(dsd, field) == getattr(empty, field), field


def test_a_misplaced_dimension_without_an_id_is_named_by_its_concept() -> None:
    """It was named `''`: the check ran on the stated id, before the concept supplied one."""
    doc = _doc()
    dimensions = _dsd(doc)["dataStructureComponents"]["dimensionList"]["dimensions"]
    second = {key: value for key, value in dimensions[0].items() if key != "id"}
    second["position"] = 0
    dimensions[0]["position"] = 1
    dimensions.append(second)
    concept_id = second["conceptIdentity"].rsplit(".", 1)[1]
    expected = f"<position>: dimension {concept_id!r} comes after 'DIM' in the dimension list but says position=0"
    assert any(expected in refusal for refusal in _refusals(doc)), _refusals(doc)


# ── what the schema does not allow is refused, not dropped ─────────────────


def test_a_concept_role_that_is_not_a_concept_urn_is_refused() -> None:
    """It was skipped, and the role silently lost."""
    doc = _doc()
    dimension = _dsd(doc)["dataStructureComponents"]["dimensionList"]["dimensions"][0]
    dimension["conceptRoles"] = ["not-a-urn", *dimension["conceptRoles"]]
    assert any("<conceptRoles>: 'not-a-urn' is not a concept's URN" in r for r in _refusals(doc)), _refusals(doc)


@pytest.mark.parametrize("code", ["CL.PARENT", "urn:sdmx:not-a-code"])
def test_a_hierarchy_node_whose_code_is_not_a_code_urn_is_refused(code: str) -> None:
    """It warned and read the node as placing nothing — or, without `urn:`, said nothing at all."""
    doc = _doc()
    doc["data"]["hierarchies"][0]["hierarchicalCodes"][0]["code"] = code
    expected = f"<hierarchicalCode>: code {code!r} is not a code's URN"
    assert any(expected in r for r in _refusals(doc)), _refusals(doc)


def test_a_metadata_attribute_s_max_occurs_as_a_string_is_refused() -> None:
    """A count or "unbounded", as a representation's `maxOccurs` already was."""
    doc = _doc()
    msd = doc["data"]["metadataStructures"][0]["metadataStructureComponents"]["metadataAttributeList"]
    msd["metadataAttributes"][0]["maxOccurs"] = "3"
    expected = "<maxOccurs>: '3' is neither a count nor 'unbounded'"
    assert any(expected in r for r in _refusals(doc)), _refusals(doc)


def test_a_geo_grid_stub_without_its_grid_definition_has_none() -> None:
    """The schema requires `gridDefinition` "unless the artefact is returned as a stub"."""
    doc = _doc()
    del doc["data"]["geoGridCodelists"][0]["gridDefinition"]
    (grid,) = (c for c in _read(doc).codelists if isinstance(c, sl.GeoGridCodelist))
    assert grid.grid_definition == ""
