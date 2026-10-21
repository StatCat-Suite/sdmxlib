"""The model is checked against constraints read out of the SDMX-ML schemas.

Every conformance bug in this milestone was a constraint the XSD states and the
model does not enforce, and each was found the slow way — generate an artefact,
watch schema validation reject it, work out which side was wrong. That found
`DataType.URL`, `NCNameIDType`, the fixed `AgencyScheme.id`, seven gaps in
`DataStructure`. It works, but it only ever sees the constraints a generated
artefact happens to touch.

These tests invert it. `tests/support/xsd_facts` reads the constraints *out of*
the vendored schemas — roughly 300 pattern facets, 240 enumerations and the
fixed attributes, per version — and the model is compared against the whole set
at once. No generation involved, so no constraint is missed for want of a
strategy that reaches it.

The maintenance property is the real argument: refreshing the vendored schemas
changes the facts, and these tests report the difference. That is precisely what
you want to learn when SDMX-TWG publishes a new tag, and it is why the XSDs are
vendored at all.
"""

import enum
import importlib
import pkgutil
import re

import attrs
import pytest

import sdmxlib as sl
import sdmxlib.model as model_package
from sdmxlib.model import validation
from sdmxlib.model._rules import ncname_id_type
from tests.support import xsd_facts
from tests.support.repo import ROOT
from tests.support.xsd import SchemaVersion
from tests.unit.test_roundtrip_property_guard import KNOWN_GAPS

pytestmark = pytest.mark.unit


def _sdmxlib_enums() -> dict[str, type[enum.Enum]]:
    """Every Enum sdmxlib defines under `sdmxlib.model`."""
    found: dict[str, type[enum.Enum]] = {}
    for module_info in pkgutil.iter_modules(model_package.__path__):
        module = importlib.import_module(f"sdmxlib.model.{module_info.name}")
        for obj in vars(module).values():
            if isinstance(obj, type) and issubclass(obj, enum.Enum) and obj.__module__.startswith("sdmxlib"):
                found[obj.__name__] = obj
    return found


#: sdmxlib enum -> the XSD simple type it must not exceed, per version.
#: Discovered by matching value sets against the extracted enumerations rather
#: than by reading the schemas by eye.
_ENUM_TO_XSD_TYPE: dict[str, dict[str, str]] = {
    "DataType": {"2.1": "DataType", "3.0": "DataType", "3.1": "DataType"},
    "AttributeUsage": {"3.0": "UsageType", "3.1": "UsageType"},
    "ConstraintType": {"2.1": "ContentConstraintTypeCodeType", "3.0": "ConstraintRoleType"},
    "EpochPeriod": {"3.0": "EpochPeriodType", "3.1": "EpochPeriodType"},
    "ResolvePeriod": {"3.0": "ResolvePeriodType", "3.1": "ResolvePeriodType"},
    "ToValueType": {"2.1": "ToValueTypeType"},
    "ActionType": {"2.1": "ActionType", "3.0": "ActionType", "3.1": "ActionType"},
}

#: Enums with no single named XSD counterpart, and why. Listed rather than
#: skipped silently, so `test_every_enum_is_classified` can insist that a new
#: enum is either mapped or explained.
_ENUM_WITHOUT_NAMED_XSD_TYPE = {
    "CascadeSelection": "maps to an XSD union of xs:boolean and ExcludeRootType, not one named type",
    "InclusionMode": "expressed as element choice (Include/Exclude), not an enumerated attribute",
    "AttachmentLevel": "expressed structurally via AttributeRelationship, not an enumerated attribute",
    "Fold": "query-compiler matching strength (#255); never serialised, so no XSD counterpart",
    "ComponentRole": (
        "a classification *derived* from the IM's own component kinds (dimension, time, "
        "measure, attribute) so a backend capability can be declared over roles rather "
        "than over column names; the DSD already carries the distinction structurally, "
        "so there is nothing extra to serialise"
    ),
}


@pytest.mark.parametrize("enum_name", sorted(_ENUM_TO_XSD_TYPE))
def test_enum_members_are_all_accepted_by_the_schema(enum_name: str):
    """No enum member may be outside the XSD enumeration it serialises into.

    `DataType` carried four such members — `URL`, `Year`, `ReportingHour`,
    `Attachment` — each producing XML that failed validation in every version
    (#224). This states the rule for every mapped enum, so the next one cannot
    be added unnoticed.
    """
    members = {member.value for member in _sdmxlib_enums()[enum_name]}
    for version, xsd_type in _ENUM_TO_XSD_TYPE[enum_name].items():
        allowed = xsd_facts.enumerations(version).get(xsd_type)
        assert allowed is not None, f"{xsd_type} is not a named enumeration in {version}"
        extra = sorted(members - allowed)
        assert not extra, f"{enum_name} has {extra} which SDMX-ML {version} {xsd_type} does not accept"


def test_every_enum_is_classified():
    """A new enum must be mapped to an XSD type or explicitly explained.

    Without this the mapping silently stops covering the model: a new enum
    would simply not be checked, and nothing would say so.
    """
    unclassified = set(_sdmxlib_enums()) - set(_ENUM_TO_XSD_TYPE) - set(_ENUM_WITHOUT_NAMED_XSD_TYPE)
    assert not unclassified, (
        f"{sorted(unclassified)} are neither mapped to an XSD simple type nor listed in "
        "_ENUM_WITHOUT_NAMED_XSD_TYPE with a reason"
    )


#: sdmxlib validation pattern -> the XSD simple type it implements. The pattern
#: mismatch here is exactly what #221 fixed: `validate` applied `IDType`
#: everywhere, including positions the schema restricts to `NCNameIDType`.
_PATTERN_TO_XSD_TYPE = {
    "_ID_PATTERN": "IDType",
    "_NCNAME_PATTERN": "NCNameIDType",
    "_AGENCY_PATTERN": "NestedNCNameIDType",
}


#: Candidate ids spanning the interesting boundaries: leading digits, the
#: punctuation IDType allows and NCName does not, dots, and the characters
#: neither permits.
_ID_CANDIDATES = [
    "A", "z", "CL_FREQ", "dsd-cpi", "A1", "a-b_c",
    "0", "123ABC", "_", "-", "@", "$", "_LEAD", "0LEAD",
    "CL@FREQ", "CL$FREQ", "A_@$-B",
    "IMF.STAT", "A.B.C", "A.B@C", "0.1", ".SDMX", "SDMX.", "A..B",
    "", "CL FREQ", "CL/FREQ", "héllo", "CL=FREQ",
]  # fmt: skip


@pytest.mark.parametrize("attribute_name", sorted(_PATTERN_TO_XSD_TYPE))
@pytest.mark.parametrize("version", xsd_facts.all_versions())
def test_validation_patterns_accept_the_same_ids_as_the_schema(attribute_name: str, version: str):
    """Our id regexes must accept exactly what the schema's do.

    Compares *behaviour* over a corpus, not regex text. The two are written in
    different dialects — XSD pattern facets are implicitly anchored and escape
    ``-`` inside character classes, Python's are neither — so a textual
    comparison fails on differences that mean nothing. Asking whether they
    accept the same strings is the question that actually matters.

    This is the check that would have caught #221 directly: `validate` used
    `IDType` at positions the schema restricts to `NCNameIDType`.
    """
    xsd_type = _PATTERN_TO_XSD_TYPE[attribute_name]
    expected = xsd_facts.patterns(version).get(xsd_type)
    if expected is None:
        pytest.skip(f"{xsd_type} has no single pattern facet in {version}")

    ours = re.compile(getattr(validation, attribute_name))
    theirs = re.compile(f"^(?:{expected})$")  # XSD facets are whole-value anchored
    disagreements = [
        candidate for candidate in _ID_CANDIDATES if bool(ours.match(candidate)) != bool(theirs.match(candidate))
    ]
    assert not disagreements, f"{attribute_name} and SDMX-ML {version} {xsd_type} disagree on {disagreements}"


@pytest.mark.parametrize("version", xsd_facts.all_versions())
def test_fixed_id_attributes_are_enforced_where_a_caller_can_set_them(version: SchemaVersion):
    """Every `fixed` id a caller controls must be enforced by `validate`.

    The schemas pin a good many ids, but most are container element ids the
    writer generates itself — `DIMENSION_DESCRIPTOR`, `MEASURE_DESCRIPTOR`.
    The ones that matter are the maintainables a caller names, and those are
    enforced in `validation._FIXED_SCHEME_IDS` (#223).

    Enumerating them from the schemas is what turned fourteen possible
    investigations into one.
    """
    fixed = xsd_facts.fixed_attributes(version)
    caller_settable = {value for (owner, attr), value in fixed.items() if attr == "id" and owner.endswith("SchemeType")}
    enforced = set(validation._FIXED_SCHEME_IDS.values())
    # Only schemes sdmxlib models are in scope — it has no DataConsumerScheme.
    modelled = {"AGENCIES", "DATA_PROVIDERS"}
    missing = (caller_settable & modelled) - enforced
    assert not missing, f"{sorted(missing)} are fixed by SDMX-ML {version} but not enforced by validate()"


def test_the_extractor_finds_a_plausible_number_of_constraints():
    """Guard against the extractor silently returning nothing.

    Every test above is vacuously true if `xsd_facts` stops parsing — a
    renamed schema directory, a namespace change. This pins the order of
    magnitude rather than exact counts, which would churn on every refresh.
    """
    for version in xsd_facts.all_versions():
        assert len(xsd_facts.enumerations(version)) > 20, f"{version}: too few enumerations"
        assert len(xsd_facts.patterns(version)) > 15, f"{version}: too few pattern facets"
        assert len(xsd_facts.fixed_attributes(version)) > 10, f"{version}: too few fixed attributes"
        assert re.match(xsd_facts.patterns(version)["IDType"], "CL_FREQ"), f"{version}: IDType looks wrong"


# ── Required children and required choices (#228) ────────────────────────────
#
# The three extractions above — enumerations, pattern facets, fixed attributes —
# cover the constraint kinds that state a *value*. These two cover the kinds
# that state a *shape*, and that is where the yield has been: four of milestone
# 10's ten findings were `minOccurs>=1` violations, every one discovered by
# generating an artefact and reading a schema error.

#: Types read by eye from the vendored XSDs, to check the extractor itself.
#:
#: An extractor is only as trustworthy as its handling of the nesting, and
#: getting that wrong is silent: it produces a plausible table that is wrong in
#: one direction or the other. Under-reporting loses coverage;
#: **over-reporting is worse**, because acting on it means refusing documents
#: that are valid — the mistake made once already in !231, where a grep-built
#: table rejected four map types the 2.1 writer had always emitted correctly.
#:
#: Each entry exercises a different part of the walk.
_HAND_CHECKED_CHILDREN: dict[tuple[str, str], tuple[str, ...]] = {
    # Straightforward: a required element in the type's own sequence.
    ("3.0", "HierarchyType"): ("Name", "HierarchicalCode"),
    # Required element inherited through complexContent/extension from
    # MetadataflowBaseType — the pattern SDMX uses for nearly every
    # maintainable, so getting it wrong would mis-report almost everything.
    ("3.0", "MetadataflowType"): ("Name", "Target"),
    # The false positive that motivated hand-checking. A naive walk reports
    # Source and Target here; both sit under a particle that makes them
    # optional, and a categorisation carrying neither really does serialise to
    # valid 3.0 — verified by writing one.
    ("3.0", "CategorisationType"): ("Name",),
    # The whole sequence is minOccurs="0", so nothing inside it is
    # *unconditionally* required, even though a set naming a structure must
    # also name a Target. Deliberate under-report; see the docstring.
    ("3.0", "MetadataSetType"): ("Name",),
    # Every child sits inside a required xs:choice, so none is required on its
    # own — the type must carry one of them, which is what `required_choices`
    # is for. This is the case that fails when the choice branch of the walk is
    # removed: without it all four targets read as required, and a table built
    # on that would refuse every constraint naming only one. Added because a
    # control showed the three entries above all still pass with
    # choice-awareness deleted, so nothing was guarding the over-report the
    # walk exists to prevent.
    ("3.0", "ConstraintAttachmentType"): (),
    # Same shape reached through complexContent/restriction rather than a
    # plain sequence — the metadata header's three-way structure choice.
    ("3.0", "GenericMetadataStructureType"): (),
}

#: Hand-read anchors for `required_choices`, so it is checked directly and not
#: only in aggregate by the gate cross-check below.
_HAND_CHECKED_CHOICES: dict[tuple[str, str], tuple[str, ...]] = {
    ("3.0", "ConstraintAttachmentType"): ("DataProvider", "MetadataProvider", "MetadataSet", "SimpleDataSource"),
    ("3.0", "GenericMetadataStructureType"): ("ProvisionAgreement", "StructureUsage", "Structure"),
    ("3.0", "RepresentationMapType"): ("SourceCodelist", "SourceDataType"),
}


@pytest.mark.parametrize(("version", "type_name"), sorted(_HAND_CHECKED_CHILDREN))
def test_required_children_matches_a_hand_read_of_the_schema(version: str, type_name: str):
    """The extractor must agree with a human reading of six chosen types."""
    expected = _HAND_CHECKED_CHILDREN[version, type_name]
    got = xsd_facts.required_children(version).get(type_name, ())
    assert got == expected, f"{version} {type_name}: extractor says {got}, the schema says {expected}"


@pytest.mark.parametrize(("version", "type_name"), sorted(_HAND_CHECKED_CHOICES))
def test_required_choices_matches_a_hand_read_of_the_schema(version: str, type_name: str):
    """The alternatives extracted must be the ones the schema offers."""
    expected = _HAND_CHECKED_CHOICES[version, type_name]
    got = xsd_facts.required_choices(version).get(type_name, ())
    assert expected in got, f"{version} {type_name}: extractor says {got}, the schema offers {expected}"


#: `_REQUIRED_CONTENT` entry -> the XSD complex type that states the rule.
#: Usually the artefact's own type; the exceptions are requirements that live
#: on a *nested* type, which the extraction reaches but the artefact mapping
#: does not.
_RULE_XSD_TYPE = {
    "hierarchies": "HierarchyType",
    "hierarchy_associations": "HierarchyAssociationType",
    "data_structures": "DimensionListType",  # nested under DataStructureComponents
    "structure_maps": "StructureMapType",
    "concept_scheme_maps": "ConceptSchemeMapType",
    "category_scheme_maps": "CategorySchemeMapType",
    "organisation_scheme_maps": "OrganisationSchemeMapType",
    "representation_maps": "RepresentationMapType",
}

#: Overrides where the type depends on the version. A RepresentationMap is
#: written as a `<CodelistMap>` in 2.1 and a `<RepresentationMap>` in 3.x, so
#: the rule for the same model field is stated against different types.
_RULE_XSD_TYPE_BY_VERSION = {
    ("representation_maps", "2.1"): "CodelistMapType",
}


def test_required_content_rules_agree_with_the_schemas():
    """Every write-boundary rule must be one the schemas actually state.

    This is the loop the milestone closes. `validation._REQUIRED_CONTENT` has
    to live in `src/` — the vendored XSDs are test-only and `sdmxlib.model` may
    not depend on them — so the rules are *stated* there and *verified* here.
    A rule the schema does not support fails; so does a schema refresh that
    changes a cardinality out from under one.

    It replaced a cross-check against the conformance gate's `KNOWN_INVALID`,
    which was the right anchor while that table had entries in it and became
    vacuous the moment #229 emptied it. A test that passes because its input is
    empty is worse than no test, so it was swapped for one whose input is the
    rules themselves — which cannot be empty without the rules disappearing.
    """
    wrong: list[str] = []
    for message_field, attribute, element, versions in validation._REQUIRED_CONTENT:
        for version in xsd_facts.all_versions():
            xsd_type = _RULE_XSD_TYPE_BY_VERSION.get((message_field, version), _RULE_XSD_TYPE[message_field])
            required = element in xsd_facts.required_children(version).get(xsd_type, ())
            in_choice = any(element in branch for branch in xsd_facts.required_choices(version).get(xsd_type, ()))
            stated = version in versions
            schema_says = required or in_choice
            if stated and not schema_says:
                wrong.append(
                    f"{message_field}.{attribute}: rule requires <{element}> for {version}, but {xsd_type} does not"
                )
            if schema_says and not stated:
                wrong.append(
                    f"{message_field}.{attribute}: {version} {xsd_type} requires <{element}>, "
                    "but the rule does not apply there"
                )
    assert not wrong, "write-boundary rules disagree with the schemas:\n  " + "\n  ".join(wrong)


def test_every_required_content_rule_is_mapped_to_a_type():
    """A new rule must name the XSD type that justifies it.

    Without this the check above silently skips it — an unmapped rule would
    raise `KeyError`, which is a worse failure than a sentence saying what to
    add.
    """
    unmapped = sorted({field for field, _a, _e, _v in validation._REQUIRED_CONTENT} - set(_RULE_XSD_TYPE))
    assert not unmapped, f"{unmapped} are required-content rules with no XSD type in _RULE_XSD_TYPE"


@pytest.mark.parametrize("version", xsd_facts.all_versions())
def test_shape_extractions_find_a_plausible_number_of_constraints(version: SchemaVersion):
    """Guard against the two new extractions silently returning nothing.

    Same role as `test_the_extractor_finds_a_plausible_number_of_constraints`
    for the value-level kinds: every assertion above is vacuously true if the
    walk stops matching. Order of magnitude, not exact counts, so a schema
    refresh does not churn this.
    """
    children = xsd_facts.required_children(version)
    choices = xsd_facts.required_choices(version)
    assert len(children) > 100, f"{version}: only {len(children)} types with required children"
    assert len(choices) > 15, f"{version}: only {len(choices)} types with a required choice"
    assert "Name" in children.get("HierarchyType", ()), f"{version}: HierarchyType looks wrong"


# ── The extraction, mapped onto the model ────────────────────────────────────

#: Model class -> the XSD complex type it serialises into. Only artefacts that
#: ride in a message; the nested types (`DimensionListType`,
#: `ConstraintAttachmentType`) are reached through their parents and are not
#: separately mapped, which is why `DataStructure` and `DataConstraint` look
#: unconstrained here while the gate records failures for both.
_ARTEFACT_XSD_TYPE = {
    "Codelist": "CodelistType",
    "ValueList": "ValueListType",
    "ConceptScheme": "ConceptSchemeType",
    "DataStructure": "DataStructureType",
    "Dataflow": "DataflowType",
    "Categorisation": "CategorisationType",
    "CategoryScheme": "CategorySchemeType",
    "ProvisionAgreement": "ProvisionAgreementType",
    "MetadataStructure": "MetadataStructureType",
    "Metadataflow": "MetadataflowType",
    "MetadataProvisionAgreement": "MetadataProvisionAgreementType",
    "Hierarchy": "HierarchyType",
    "HierarchyAssociation": "HierarchyAssociationType",
    "AgencyScheme": "AgencySchemeType",
    "DataProviderScheme": "DataProviderSchemeType",
    "MetadataProviderScheme": "MetadataProviderSchemeType",
    "DataConsumerScheme": "DataConsumerSchemeType",
    "OrganisationUnitScheme": "OrganisationUnitSchemeType",
    "DataConstraint": "DataConstraintType",
    "AttachmentConstraint": "AttachmentConstraintType",
    "AvailabilityConstraint": "AvailabilityConstraintType",
    "StructureMap": "StructureMapType",
    "ConceptSchemeMap": "ConceptSchemeMapType",
    "CategorySchemeMap": "CategorySchemeMapType",
    "OrganisationSchemeMap": "OrganisationSchemeMapType",
    "RepresentationMap": "RepresentationMapType",
    "MetadataSet": "MetadataSetType",
    "StructureSet": "StructureSetType",
    "HierarchicalCodelist": "HierarchicalCodelistType",
}

#: Required child element -> the model field that carries it. `Name` is
#: universal and omitted. Where a schema requires something the model has no
#: field for, the entry is `None` and the reason is stated — that is a
#: modelling gap, and it is the answer phase 3 needs before it can decide
#: whether a pair is a write-boundary rule, a writer bug, or a model change.
_CHILD_TO_FIELD: dict[str, str | None] = {
    "Source": "source",
    "Target": "targets",  # Metadataflow.targets; the maps override below
    # The tree is the field; `Hierarchy._codes` is a derived flat index.
    "HierarchicalCode": "tree",
    "LinkedHierarchy": "linked_hierarchy",
    "LinkedObject": "linked_object",
    "Dataflow": "dataflow",
    "DataProvider": "data_provider",
    "StructureUsage": "dataflow",  # 2.1's name for a ProvisionAgreement's flow
    "ComponentMap": "maps",
    # 2.1 offers a choice of CodelistMap|ValueMap where 3.0 names the source
    # and target sides separately; both land in a RepresentationMap's
    # `mappings`.
    "CodelistMap": "mappings",
    "ValueMap": "mappings",
    "ConceptMap": "maps",
    "CategoryMap": "maps",
    "OrganisationMap": "maps",
    # MetadataProvisionAgreementType's two required refs.
    "Metadataflow": "metadataflow",
    "MetadataProvider": "metadata_provider",
    "SourceCodelist": "sources",
    "SourceDataType": "sources",
    "TargetCodelist": "targets",
    "TargetDataType": "targets",
    # 3.1's AvailabilityConstraintType requires both, one of each (#421).
    "ConstraintAttachment": "attachment",
    "CubeRegion": "region",
    # SDMX-ML 2.1 wrapped reported metadata in a <Report> layer that SDMX 3.0
    # removed; sdmxlib follows 3.0 and does not model it. It cannot be a
    # write-boundary rule because there is nothing to require — the 2.1
    # metadata format is out of scope, which `formats.serialize` states by
    # refusing a MetadataMessage on 2.1 outright.
    "Report": None,
}

#: Maps use Source/Target for the two ends rather than a target *list*, so the
#: shared `_CHILD_TO_FIELD` entry for "Target" does not apply to them.
_MAP_CLASSES = {
    "StructureMap",
    "ConceptSchemeMap",
    "CategorySchemeMap",
    "OrganisationSchemeMap",
    "RepresentationMap",
    "Categorisation",
}


def test_every_serialisable_artefact_is_mapped_to_its_xsd_type():
    """A new artefact class must be mapped, or the extraction stops covering it.

    The same completeness discipline as the conformance gate's bare-artefact
    table: an unmapped class is not a smaller check, it is no check, and
    nothing else would say so.
    """
    import sdmxlib as sl
    from sdmxlib.model.message import MetadataMessage, StructureMessage

    carried: set[str] = set()
    for message in (StructureMessage, MetadataMessage):
        for field in attrs.fields(message):
            if field.name.startswith("_"):
                continue
            singular = getattr(sl, "".join(w.title() for w in field.name.rstrip("s").split("_")), None)
            if singular is not None:
                carried.add(singular.__name__)
    missing = sorted(carried - set(_ARTEFACT_XSD_TYPE))
    assert not missing, f"{missing} are serialisable but not mapped to an XSD type"


@pytest.mark.parametrize("class_name", sorted(_ARTEFACT_XSD_TYPE))
@pytest.mark.parametrize("version", xsd_facts.all_versions())
def test_every_required_child_maps_to_a_model_field(version: str, class_name: str):
    """Whatever a schema requires, the model must be able to express.

    Distinct from the conformance gate, which asks what a *bare* artefact does
    on the wire. This asks the prior question: could a caller satisfy the
    requirement at all? A required child with no model field is a modelling
    gap — no write-boundary rule can fix it, because there would be nothing to
    require — and knowing which of the two a failure is, before writing the
    fix, is the point of extracting the schema rather than reading errors.

    2.1's `<Report>` layer is the one live example, and it is out of scope
    rather than missing: SDMX 3.0 removed it and sdmxlib follows 3.0.
    """
    import sdmxlib as sl

    xsd_type = _ARTEFACT_XSD_TYPE[class_name]
    artefact = getattr(sl, class_name)
    fields = {f.name for f in attrs.fields(artefact)}

    required = xsd_facts.required_children(version).get(xsd_type, ())
    branches = xsd_facts.required_choices(version).get(xsd_type, ())
    wanted = [c for c in required if c != "Name"] + [b[0] for b in branches]

    unmappable: list[str] = []
    for child in wanted:
        if child == "Target" and class_name in _MAP_CLASSES:
            continue  # a map's ends are Source/Target, not a target list
        assert child in _CHILD_TO_FIELD, (
            f"SDMX-ML {version} {xsd_type} requires <{child}>, which is not in _CHILD_TO_FIELD. "
            "Map it to the model field that carries it, or to None with a reason"
        )
        field = _CHILD_TO_FIELD[child]
        if field is not None and field not in fields:
            unmappable.append(f"<{child}> -> {field!r}, which {class_name} does not have")

    assert not unmappable, f"SDMX-ML {version} {xsd_type} requires children the model cannot express: {unmappable}"


# ── Attribute-level constraints (#230) ───────────────────────────────────────

#: (complex type, attribute) that a schema prohibits somewhere, mapped to the
#: model field it would have carried — or None where the model has no such
#: field and the prohibition therefore costs nothing.
#:
#: This is the silent-loss inventory. A prohibited attribute must be dropped by
#: any correct writer, so the output validates *because* the value was
#: discarded — the one failure mode schema validation cannot see, and the
#: reason these are enumerated rather than left to be discovered.
_PROHIBITED_TO_FIELD: dict[tuple[str, str], str | None] = {
    # Live, and the reason this extraction exists: refused at the write
    # boundary in every SDMX-ML version rather than silently dropped.
    ("OrganisationSchemeBaseType", "version"): "version",
    ("AgencySchemeType", "version"): "version",
    # Live, and recorded in KNOWN_GAPS rather than refused — 3.1 prohibits it
    # while 2.1 and 3.0 carry it, so refusing would make a 3.1 downgrade fail
    # for every categorisation rather than for an unusual one.
    ("CategorisationBaseType", "version"): "version",
    # Not modelled. Message-envelope and structure-specific-schema plumbing:
    # sdmxlib emits neither a namespace/schemaURL pair nor the
    # dimensionAtObservation / explicitMeasures flags, which belong to data
    # messages and generated schemas rather than to structural metadata.
    ("DataStructureRequestType", "namespace"): None,
    ("DataStructureRequestType", "schemaURL"): None,
    ("GenericDataStructureRequestType", "explicitMeasures"): None,
    ("GenericDataStructureType", "explicitMeasures"): None,
    ("GenericDataStructureType", "namespace"): None,
    ("GenericDataStructureType", "schemaURL"): None,
    ("GenericMetadataStructureType", "namespace"): None,
    ("MetadataStructureType", "dimensionAtObservation"): None,
    ("MetadataStructureType", "explicitMeasures"): None,
    # Local reference containers; sdmxlib writes the id only.
    ("LocalCodelistMapRefType", "containerID"): None,
    ("LocalDataStructureComponentRefType", "containerID"): None,
    # Representation cardinality and metadata-attribute usage: 3.x moved
    # min/max occurs onto the component rather than its representation, and
    # sdmxlib follows the component. MetadataAttributeUsage and
    # MetadataAttributeValueSet are not modelled at all.
    ("DataStructureRepresentationType", "minOccurs"): None,
    ("SimpleDataStructureRepresentationType", "maxOccurs"): None,
    ("MetadataAttributeRepresentationType", "maxOccurs"): None,
    ("MetadataAttributeRepresentationType", "minOccurs"): None,
    ("MetadataAttributeUsageBaseType", "id"): None,
    ("MetadataAttributeValueSetType", "validFrom"): None,
    ("MetadataAttributeValueSetType", "validTo"): None,
    # `SimpleKeyValueType` types the <Value> inside a DataKeySet key, which is
    # a different path from the cube-region member values where sdmxlib does
    # emit `cascadeValues` (that context allows it). The DataKey writer emits
    # bare <Value> text and no attributes at all, so these cannot be reached.
    ("SimpleKeyValueType", "cascadeValues"): None,
    ("SimpleKeyValueType", "validFrom"): None,
    ("SimpleKeyValueType", "validTo"): None,
    # sdmxlib does not model an explicit position for the time dimension; it
    # is written last in the dimension list, which is what the position would
    # have said.
    ("TimeDimensionType", "position"): None,
    # Time-series observations belong to data messages, not structures.
    ("TimeSeriesObsType", "type"): None,
    ("TimeSeriesType", "TIME_PERIOD"): None,
}

#: Type families whose prohibitions cannot reach sdmxlib, with the reason.
#:
#: Grouped rather than enumerated one by one: each family is mechanically
#: uniform — the same attribute prohibited on a dozen sibling restrictions —
#: and sixty individual entries saying the same sentence would obscure the
#: three that matter rather than document them. A family still has to be
#: *named*, so a schema refresh introducing an unfamiliar type fails here.
_UNREACHABLE_TYPE_FAMILIES: dict[str, str] = {
    "Ref": (
        "2.1 local reference containers. sdmxlib writes <Ref id='...'/> and nothing else, so the "
        "container/parent attributes these prohibit are never emitted in the first place"
    ),
    "TextFormat": (
        "restrictions of SimpleComponentTextFormatType that narrow which facets may appear. sdmxlib "
        "emits facets only where the model carries them; the one live case is a Level's CodingFormat, "
        "recorded in KNOWN_GAPS"
    ),
    "ValueSet": "constraint value sets with per-value validity, which sdmxlib does not model",
    "CubeRegionType": "per-region validity dates, which CubeRegion does not model",
    "GroupDimensionBaseType": "sdmxlib sets the id on <Group>, never on the <GroupDimension> inside it",
    "Query": "query message types; sdmxlib neither reads nor writes them",
    "Registry": "registry interface types",
    "Submit": "registry submission types",
    "Subscription": "registry subscription types",
    "Notification": "registry notification types",
    "StructureSpecific": "structure-specific data messages, out of scope",
    "Data": "data-message types; the structural DataStructure* entries are listed individually above",
}


def _family_reason(type_name: str) -> str | None:
    """The family excusing *type_name*, if any."""
    for marker, reason in _UNREACHABLE_TYPE_FAMILIES.items():
        if type_name.startswith(marker) or type_name.endswith(marker) or marker in type_name:
            return reason
    return None


def _prohibited_pairs() -> set[tuple[str, str]]:
    """Every (type, attribute) prohibited in any vendored version."""
    return {
        (type_name, attribute)
        for version in xsd_facts.all_versions()
        for type_name, attributes in xsd_facts.prohibited_attributes(version).items()
        for attribute in attributes
    }


def test_every_prohibited_attribute_is_classified():
    """A prohibited attribute must be mapped to a model field or excused.

    The inventory is the deliverable. An unclassified entry is a value the
    library might be dropping on the floor with a conformant document to show
    for it, and nothing else in the suite would say so — the round-trip
    properties only compare fields somebody thought to list, and schema
    validation is satisfied precisely because the value is gone.
    """
    unclassified = sorted(
        pair
        for pair in _prohibited_pairs()
        if pair not in _PROHIBITED_TO_FIELD and _family_reason(pair[0]) is None and pair[0] != "?"
    )
    assert not unclassified, (
        f"{unclassified} are prohibited by some SDMX-ML version and unclassified. "
        "Map each to the model field it would carry, or to None if the model has no such field"
    )


def test_prohibited_attributes_that_map_to_a_field_are_handled():
    """Every live prohibition must be refused or recorded — never just lost.

    Three (type, attribute) pairs land on a model field. Two are the same
    `version` story with different answers, and the difference is deliberate:

    - **organisation schemes** cannot carry a non-default version in *any*
      format — SDMX-JSON makes it a `const` too — so `validate` refuses it
      whatever the target (#416). Nothing is lost because nothing is written.
    - **Categorisation** carries its version fine in 2.1 and 3.0 and loses it
      only in 3.1, so refusing would break every 3.1 downgrade rather than an
      unusual one. Recorded in `KNOWN_GAPS` instead.

    Stating both here keeps the choice visible; a future reader seeing one
    refused and one tolerated should find the reason next to them.
    """
    live = {pair: field for pair, field in _PROHIBITED_TO_FIELD.items() if field is not None}
    assert live, "the mapping has no live entries — did the extraction stop finding prohibitions?"

    refused = {("OrganisationSchemeBaseType", "version"), ("AgencySchemeType", "version")}
    recorded = {("CategorisationBaseType", "version")}
    assert set(live) == refused | recorded, (
        f"live prohibitions changed to {sorted(live)}; each must be refused at the write "
        "boundary or recorded in KNOWN_GAPS, and this test updated to say which"
    )
    assert ("ml31", "categorisation", "version") in KNOWN_GAPS, (
        "the 3.1 Categorisation version loss must stay recorded in KNOWN_GAPS"
    )


@pytest.mark.parametrize("version", xsd_facts.all_versions())
def test_attribute_extractions_find_a_plausible_number_of_constraints(version: SchemaVersion):
    """Guard against the attribute extractions silently returning nothing."""
    required = xsd_facts.required_attributes(version)
    prohibited = xsd_facts.prohibited_attributes(version)
    assert len(required) > 40, f"{version}: only {len(required)} types with a required attribute"
    assert len(prohibited) > 15, f"{version}: only {len(prohibited)} types with a prohibited attribute"


def test_a_coding_format_keeps_what_its_type_permits_and_refuses_the_rest():
    """`CodingTextFormatType` restricts a Level's ``<CodingFormat>`` in every format.

    It permits the length and pattern facets and has no `decimals`. That was
    a declared loss, every writer dropping it, until #419 refused it at
    `Registry.add` and `serialize` instead: no format can carry it, so holding
    it only deferred the loss. Both halves are asserted — the refusal *and*
    what survives without it — because a refusal alone would pass just as well
    if every coding format were refused.
    """
    from sdmxlib.formats import parse, serialize
    from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, Level
    from sdmxlib.model.istring import InternationalString
    from sdmxlib.model.message import StructureMessage
    from sdmxlib.model.representation import DataType, Facet
    from sdmxlib.model.validation import ValidationError
    from tests.support.factories import code_urn
    from tests.support.roundtrip import as_structure, structure_schema
    from tests.support.xsd import assert_valid_sdmx_ml

    def message(facet: Facet) -> StructureMessage:
        hierarchy = Hierarchy(
            id="H",
            maintainer="AG",
            version="1.0",
            name=InternationalString(en="n"),
            levels=[Level(id="L1", name=InternationalString(en="l"), coding_format=facet)],
            tree=[HierarchicalCode(id="HC1", code=code_urn("CL", "A", agency="AG"))],
        )
        return StructureMessage(hierarchies=[hierarchy])

    # AlphaNumeric, not String: a CodingFormat's textType is SimpleCodeDataType.
    # A sequence with its interval and start, as IM 3.6.3.3 requires (#482).
    facet = Facet(
        type=DataType.AlphaNumeric,
        max_length=5,
        min_length=2,
        is_sequence=True,
        interval=1.0,
        start_value=1.0,
        pattern="[A-Z]+",
    )
    for version in ("2.1", "3.0", "3.1", "json"):
        with pytest.raises(ValidationError, match=r"coding_format\.decimals"):
            serialize(message(attrs.evolve(facet, decimals=2)), version=version)

    # Schema-validated, not just re-read: a reader and writer sharing a blind
    # spot is what the official schema is here to see through. The 2.1 writer
    # once dropped maxLength and pattern too, behind a comment asserting they
    # were not permitted; they are.
    written_21 = serialize(message(facet), version="2.1")
    assert_valid_sdmx_ml(written_21, structure_schema("2.1"))
    for written in (written_21, serialize(message(facet), version="json")):
        kept = as_structure(parse(written)).hierarchies[0].levels[0].coding_format
        assert kept is not None
        assert kept.is_sequence is True
        assert (kept.max_length, kept.min_length, kept.pattern) == (5, 2, "[A-Z]+")


@pytest.mark.parametrize("version", xsd_facts.all_versions())
def test_coding_format_text_type_is_restricted_to_simple_code_types(version: SchemaVersion):
    """A Level CodingFormat may only declare Alpha / AlphaNumeric / Numeric.

    `CodingTextFormatType/@textType` is `SimpleCodeDataType` — three values,
    where `DataType` has forty-four — because a coding format describes the
    *shape of a code*, not a value domain. sdmxlib emitted any DataType there,
    producing a document all three schemas reject.

    Stated as its own test because a control found nothing guarding the rule:
    the gap test above had been switched to a valid `AlphaNumeric` facet, so
    deleting the rule broke nothing.
    """
    from sdmxlib.formats import serialize
    from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, Level
    from sdmxlib.model.istring import InternationalString
    from sdmxlib.model.message import StructureMessage
    from sdmxlib.model.representation import DataType, Facet
    from sdmxlib.model.validation import ValidationError
    from tests.support.factories import code_urn

    def _hierarchy(data_type: DataType) -> StructureMessage:
        return StructureMessage(
            hierarchies=[
                Hierarchy(
                    id="H",
                    maintainer="AG",
                    version="1.0",
                    name=InternationalString(en="n"),
                    levels=[Level(id="L1", name=InternationalString(en="l"), coding_format=Facet(type=data_type))],
                    tree=[HierarchicalCode(id="HC1", code=code_urn("CL", "A", agency="AG"))],
                )
            ]
        )

    with pytest.raises(ValidationError, match="CodingFormat"):
        serialize(_hierarchy(DataType.String), version=version)
    assert serialize(_hierarchy(DataType.AlphaNumeric), version=version), "the permitted types must still write"


#: Every maintainable sdmxlib models, by the name of its XSD complex type.
_MAINTAINABLE_CLASSES = (
    "Codelist", "ConceptScheme", "CategoryScheme", "Categorisation", "Dataflow", "DataStructure",
    "Metadataflow", "MetadataStructure", "ProvisionAgreement", "AgencyScheme", "DataProviderScheme",
    "MetadataProviderScheme", "DataConsumerScheme", "OrganisationUnitScheme",
    "Hierarchy", "HierarchyAssociation", "StructureMap", "ConceptSchemeMap", "CategorySchemeMap",
    "OrganisationSchemeMap",
    "RepresentationMap", "DataConstraint", "AttachmentConstraint",
)  # fmt: skip


@pytest.mark.parametrize("version", xsd_facts.all_versions())
def test_ncname_id_maintainables_match_the_schemas(version: SchemaVersion):
    """The model must apply NCNameIDType exactly where the schemas do.

    Only three maintainables restrict `@id` to `NCNameIDType`; every other one
    inherits `MaintainableBaseType/@id`, which is the permissive `IDType`
    allowing leading digits, `@` and `$`.

    Applying the strict form to all of them was a **false refusal on real
    data**: the ECB registry publishes categorisations with UUID ids like
    `21e97b57-5950-eaab-eead-1534306b28af`, its documents validate against the
    2.1 XSD, and sdmxlib refused to write them back. Found by the
    document-fidelity harness, which could not round-trip
    `ecb_dataflow.xml` at all.

    Recomputed here from the schemas rather than restated, so a refresh that
    changes an id's type reports the difference instead of silently making
    the model wrong in one direction or the other. The model's side is read
    off each class's `id` field, whose validator refuses as the id is set (#492).
    """
    from_schema = {
        name for name in _MAINTAINABLE_CLASSES if xsd_facts.id_attribute_type(version, f"{name}Type") == "NCNameIDType"
    }
    from_model = {
        name for name in _MAINTAINABLE_CLASSES if attrs.fields_dict(getattr(sl, name))["id"].validator is ncname_id_type
    }
    assert from_schema == from_model, (
        f"SDMX-ML {version} restricts {sorted(from_schema)} to NCNameIDType, but the model applies it to "
        f"{sorted(from_model)}"
    )


def test_a_uuid_categorisation_id_is_accepted():
    """The concrete case: real ECB registry output must round-trip.

    Kept as its own test because the parametrised check above compares two
    computed sets, and a reader should be able to see the input that motivated
    it without reconstructing it.
    """
    from sdmxlib.formats import parse, serialize
    from tests.support.roundtrip import as_structure

    blob = (ROOT / "tests/fixtures/sdmx_ml21/ecb_dataflow.xml").read_bytes()
    ids = [c.id for c in as_structure(parse(blob)).categorisations]
    assert any(c[0].isdigit() for c in ids), "fixture no longer carries a digit-leading id; pick another"

    # The refusal is what regressed, so the assertion is that the ids come
    # back — not merely that `serialize` returned.
    back = [c.id for c in as_structure(parse(serialize(as_structure(parse(blob)), version="2.1"))).categorisations]
    assert sorted(back) == sorted(ids)
