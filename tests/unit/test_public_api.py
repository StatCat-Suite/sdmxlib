"""Smoke tests for the top-level sdmxlib public API surface."""

import json

import pytest

import sdmxlib as sl

pytestmark = pytest.mark.unit


def _concept(item_id: str) -> sl.Ref[sl.Concept]:
    """A ConceptIdentity for a component."""
    return sl.Ref[sl.Concept].unresolved(
        sl.SdmxUrn.make(
            sl.Concept,
            package="conceptscheme",
            artefact_class="Concept",
            agency="TEST",
            id="CS",
            version="1.0",
            item_id=item_id,
        )
    )


def _make_msg() -> sl.StructureMessage:
    cl = sl.Codelist(
        id="CL_FREQ",
        maintainer="SDMX",
        name={"en": "Frequency"},
        codes=[sl.Code(id="A", name={"en": "Annual"})],
    )
    dsd = sl.DataStructure(
        name={"en": "DSD_TEST"},
        id="DSD_TEST",
        maintainer="TEST",
        # Concepts are required: every SDMX format demands ConceptIdentity on a
        # component, so a DSD without them cannot be serialised at all (#224).
        dimensions=[sl.Dimension(id="FREQ", representation=cl, concept=_concept("FREQ"))],
        measures=[sl.Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"))],
    )
    # The scheme the component concepts point at must be present, or
    # validate_references reports them broken — a message that declares
    # ConceptIdentity should carry the concepts it identifies.
    cs = sl.ConceptScheme(
        id="CS",
        maintainer="TEST",
        version="1.0",
        name={"en": "Concepts"},
        concepts=[sl.Concept(id="FREQ", name={"en": "Frequency"}), sl.Concept(id="OBS_VALUE", name={"en": "Value"})],
    )
    return sl.StructureMessage(codelists=[cl], concept_schemes=[cs], data_structures=[dsd])


# ── to_xml ────────────────────────────────────────────────────────────────────


def test_to_xml_returns_bytes():
    msg = _make_msg()
    result = sl.to_xml(msg)
    assert isinstance(result, bytes)
    assert b"CL_FREQ" in result


def test_to_xml_version_21_default():
    msg = _make_msg()
    result = sl.to_xml(msg)
    assert b"2.1" in result or b"SDMXMessage" in result


def test_to_xml_version_30():
    msg = _make_msg()
    result = sl.to_xml(msg, version="3.0")
    assert isinstance(result, bytes)
    assert b"CL_FREQ" in result


def test_to_xml_pretty_print():
    msg = _make_msg()
    compact = sl.to_xml(msg)
    pretty = sl.to_xml(msg, pretty_print=True)
    assert len(pretty) >= len(compact)
    assert b"\n" in pretty


def test_to_xml_invalid_version():
    with pytest.raises(ValueError, match="Unsupported"):
        sl.to_xml(_make_msg(), version="99.9")


# ── to_json ───────────────────────────────────────────────────────────────────


def test_to_json_returns_bytes():
    msg = _make_msg()
    result = sl.to_json(msg)
    assert isinstance(result, bytes)
    parsed = json.loads(result)
    assert "data" in parsed


def test_to_json_contains_codelist():
    msg = _make_msg()
    parsed = json.loads(sl.to_json(msg))
    ids = [cl["id"] for cl in parsed["data"].get("codelists", [])]
    assert "CL_FREQ" in ids


def test_to_json_pretty_print():
    msg = _make_msg()
    pretty = sl.to_json(msg, pretty_print=True)
    assert b"\n" in pretty


# ── from_dict / to_dict ───────────────────────────────────────────────────────


def test_to_dict_accessible_from_top_level():
    cl = sl.Codelist(id="CL_FREQ", maintainer="SDMX", name={"en": "Frequency"})
    d = sl.to_dict(cl)
    assert d["id"] == "CL_FREQ"
    assert d["name"] == {"en": "Frequency"}


def test_from_dict_accessible_from_top_level():
    cl = sl.from_dict(sl.Codelist, {"id": "CL_FREQ", "agency_id": "SDMX", "name": {"en": "Frequency"}})
    assert cl.id == "CL_FREQ"


def test_round_trip_via_top_level():
    cl = sl.Codelist(id="CL_FREQ", maintainer="SDMX", name={"en": "Frequency"})
    cl2 = sl.from_dict(sl.Codelist, sl.to_dict(cl))
    assert cl2.id == cl.id
    assert cl2.name["en"].text == "Frequency"


# ── validate / validate_references ───────────────────────────────────────────


def test_validate_accessible_from_top_level():
    cl = sl.Codelist(name={"en": "CL_FREQ"}, id="CL_FREQ", maintainer="SDMX")
    assert sl.validate(sl.StructureMessage(codelists=[cl])) is None  # raises on issues, returns None on success


def test_validate_raises_at_top_level():
    # One codelist in the message twice: what only a message can show (#492).
    cl = sl.Codelist(name={"en": "CL"}, id="CL", maintainer="SDMX")
    with pytest.raises(sl.ValidationError) as exc:
        sl.validate(sl.StructureMessage(codelists=[cl, cl]))
    assert exc.value.issues
    assert isinstance(exc.value.issues[0], sl.ValidationIssue)


def test_construction_raises_the_same_error_at_top_level():
    """A bad id is refused as it is set (#492), with the error `validate` raises."""
    with pytest.raises(sl.ValidationError) as exc:
        sl.Codelist(name={"en": "x"}, id="bad id", maintainer="SDMX")
    assert [i.path for i in exc.value.issues] == ["Codelist.id"]


def test_validate_references_accessible_from_top_level():
    msg = _make_msg()
    # All refs are resolved (codelist is in message) — no raise.
    sl.validate_references(msg)


def test_reference_validation_error_accessible():
    assert issubclass(sl.ReferenceValidationError, Exception)


def test_reference_issue_type_accessible():
    issue = sl.ReferenceIssue(source="x", target_urn="urn:sdmx:...")
    assert "BROKEN" in str(issue)


# ── all expected names in __all__ ────────────────────────────────────────────


def test_public_api_exports_all_expected_names():
    expected = {
        # format functions
        "parse",
        "serialize",
        "to_xml",
        "to_json",
        # dict conversion
        "from_dict",
        "to_dict",
        # validation
        "validate",
        "validate_references",
        "ValidationIssue",
        "ValidationError",
        "ReferenceIssue",
        "ReferenceValidationError",
        # core model types
        "Codelist",
        "Code",
        "DataStructure",
        "Dimension",
        "MeasureDimension",
        "Attribute",
        "Measure",
        "Dataflow",
        "ConceptScheme",
        "Concept",
        "StructureMessage",
        "Ref",
        "SdmxUrn",
        "InternationalString",
        "Facet",
        "DataType",
        "Link",
        # hierarchy
        "Hierarchy",
        "HierarchicalCode",
        # constraint
        "ReferencePeriod",
        "ReleaseCalendar",
        "DataSetReference",
        "QueryableDataSource",
        # mapping (3.0-native)
        "CategorySchemeMap",
        "ComponentMap",
        "ConceptSchemeMap",
        "ItemMap",
        "RepresentationMap",
        "StructureMap",
        "RepresentationMapping",
        "SourceValue",
        # registry
        "LocalRegistry",
        "RegistryReader",
        "RestRegistry",
        "Provider",
    }
    missing = expected - set(sl.__all__)
    assert not missing, f"Missing from __all__: {missing}"
