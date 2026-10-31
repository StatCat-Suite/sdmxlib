"""The information-model rule census (#456): every normative sentence of the IM, and what owns it.

SDMX 3.1 Section 2 is vendored as text beside the XSDs; each `Rule` quotes it
verbatim and names one owner:

- `Validated` — named checks in `sdmxlib.model.validation`, each with a test
  that fails when the check is disabled;
- `Warned` — the same, for a rule no wire schema enforces: breaking it builds
  the artefact with an `ImRuleWarning` rather than refusing it (#481);
- `Structural` — the model's types cannot hold the violation;
- `NotChecked` — knowingly not enforced, with the reason, usually an issue.

The census is phased. This phase holds every sentence carrying a strong
marker — *must*, *cannot*, *mandatory*, *may not*, *shall* — in the model's
chapters (pp. 28-157; the front matter is revision history), which
`tests/unit/test_im_rule_census.py` re-derives from the text, so a sentence
cannot be left out by judgement. Beside them sit the rules the IM states
without a marker ("a maximum of one", "mutually exclusive") that a check or a
type already owns, so that every check citing the IM is cited back. The
multiplicity columns of the definitions tables are the next phase, chapter by
chapter; `UNSURVEYED_MULTIPLICITIES` lists the chapters still to do.

A quote's en dashes and single curly quotes are written as escapes, so the
source shows which characters they are; the strings are the IM's verbatim.
A quote may be cut with " … " where the PDF's table layout interleaves a
neighbouring column into a sentence; each fragment is still verbatim.
"""

import functools
import re

from attrs import frozen

from tests.support.repo import find_root

#: The vendored IM, as `pdftotext -layout` extracted it (see `tests/schemas/README.md`).
IM_TEXT = find_root() / "tests" / "schemas" / "sdmx_im" / "SDMX_3-1-0_SECTION_2_FINAL.txt"

#: The pages holding the model's chapters, 3 to 15 — what the census surveys.
FIRST_PAGE, LAST_PAGE = 28, 157

#: The words that make a sentence normative for this phase.
MARKERS = re.compile(r"\b(must|cannot|mandatory|may not|shall)\b", re.IGNORECASE)

#: How a quote marks an elision, where the layout interleaves a table column.
ELISION = " … "


@frozen
class Check:
    """A check in `sdmxlib.model.validation`, and a test that fails without it."""

    name: str
    #: ``path/to/test_file.py::test_name``, a plain function with no fixtures.
    test: str


@frozen
class Validated:
    """The rule is a named check."""

    checks: tuple[Check, ...]


@frozen
class Warned:
    """The rule is a named check that warns rather than refuses (#481)."""

    checks: tuple[Check, ...]


@frozen
class Structural:
    """The model's types cannot hold the violation."""

    reason: str


@frozen
class NotChecked:
    """Knowingly not enforced."""

    reason: str


type Owner = Validated | Warned | Structural | NotChecked


@frozen
class Rule:
    """One normative sentence of the IM, located by section and page."""

    id: str
    section: str
    page: int
    quote: str
    owner: Owner


def _v(*pairs: tuple[str, str]) -> Validated:
    return Validated(tuple(Check(name, test) for name, test in pairs))


def _sequence(rule: str) -> Warned:
    """`_check_facet_sequence`, cited by its case for `rule` in `test_im_rule_warnings.py` (#482)."""
    test = f"tests/unit/test_im_rule_warnings.py::test_a_facet_sequence_rule_warns[{rule}]"
    return Warned((Check("_check_facet_sequence", test),))


_DATA_SET = "a data set's conformance to its DSD is the data plane's: #459"
_VTL = "the VTL schemes are not modelled: #467"
_USAGE = "`usage` is an `AttributeUsage`, whose only members are MANDATORY and OPTIONAL"

RULES: tuple[Rule, ...] = (
    # 3 — Base
    Rule(
        "IM-3.2.2.1-1",
        "3.2.2.1",
        30,
        "Concrete classes based on IdentifiableArtefact all inherit the ability to be uniquely identified.",
        _v(
            (
                "_check_duplicate_maintainables",
                "tests/unit/test_xsd_unique_constraints.py::test_the_error_names_the_class_and_the_identity",
            )
        ),
    ),
    Rule(
        "IM-3.2.2.2-1",
        "3.2.2.2",
        31,
        "The unique identifier of the object.",
        _v(
            (
                "_check_duplicate_ids",
                "tests/unit/test_validation.py::test_duplicate_code_ids_are_refused_as_the_codelist_is_built",
            ),
            (
                "_check_sibling_duplicates",
                "tests/unit/test_nested_paths.py::test_a_repeat_among_siblings_is_refused[hierarchy]",
            ),
            (
                "_check_component_ids_unique",
                "tests/unit/test_ref_to.py::test_duplicate_component_ids_are_a_validation_issue",
            ),
        ),
    ),
    Rule(
        "IM-3.2.2.2-2",
        "3.2.2.2",
        31,
        "uri Universal resource identifier that may or may not be resolvable.",
        NotChecked("permissive: a uri need not resolve, so there is nothing to refuse"),
    ),
    Rule(
        "IM-3.2.2.2-3",
        "3.2.2.2",
        32,
        "A version string following SDMX versioning rules.",
        Structural("`version` is refused as it is set, by `_rules.version_type` (#492)"),
    ),
    Rule(
        "IM-3.5.3.1-1",
        "3.5.3.1",
        37,
        "A “partial” ItemScheme cannot be maintained independently in its partial form i.e., it cannot contain "
        "Items that are not present in the full ItemScheme and the content of any one Item (e.g., names and "
        "descriptions) cannot deviate from the content in the full ItemScheme.",
        Warned(
            (
                Check(
                    "_check_partial_items",
                    "tests/unit/test_partial_schemes.py::test_a_partial_with_an_item_the_full_scheme_lacks_warns",
                ),
            )
        ),
    ),
    Rule(
        "IM-3.5.3.1-2",
        "3.5.3.1",
        37,
        "the Item itself is not a MaintainableArtefact and therefore cannot have an independent Agency",
        Structural("no item class has a maintainer field; an item's agency is its scheme's"),
    ),
    Rule(
        "IM-3.5.3.1-3",
        "3.5.3.1",
        37,
        "The Item can be hierarchic and so one Item can have child Items.",
        _v(("_check_item_parents", "tests/unit/test_item_parents.py::test_a_dangling_parent_is_refused")),
    ),
    Rule(
        "IM-3.5.3.1-4",
        "3.5.3.1",
        37,
        "The restriction of the hierarchic association is that a child Item can have only parent Item.",
        Structural("an item's parent is `parent_id: str | None`, one id at most"),
    ),
    Rule(
        "IM-3.6.3.1-1",
        "3.6.3.1",
        41,
        "Each Component takes its semantic (and possibly also its representation) from a Concept in a ConceptScheme.",
        _v(
            (
                "_check_component_concept",
                "tests/unit/test_serialisation_conformance.py::test_the_check_names_every_offending_component",
            )
        ),
    ),
    Rule(
        "IM-3.6.3.3-1",
        "3.6.3.3",
        45,
        "If this attribute holds a value of true, a start value or time and a numeric or time interval must be "
        "supplied.",
        _sequence("3.6.3.3-1"),
    ),
    Rule(
        "IM-3.6.3.3-2",
        "3.6.3.3",
        45,
        "The interval attribute specifies the permitted interval (increment) in a sequence. In order for this to "
        "be used, the isSequence attribute must have a value of true.",
        _sequence("3.6.3.3-2"),
    ),
    Rule(
        "IM-3.6.3.3-3",
        "3.6.3.3",
        45,
        "The startValue facet is used in conjunction with the isSequence and interval facets (which must be set "
        "in order to use this facet). This facet is used for a numeric sequence and indicates the starting point "
        "of the sequence. This value is mandatory for a numeric sequence to be expressed.",
        _sequence("3.6.3.3-3"),
    ),
    Rule(
        "IM-3.6.3.3-4",
        "3.6.3.3",
        45,
        "The endValue facet is used in conjunction with the isSequence and interval facets (which must be set in "
        "order to use this facet).",
        _sequence("3.6.3.3-4"),
    ),
    Rule(
        "IM-3.6.3.3-5",
        "3.6.3.3",
        45,
        "The timeInterval facet indicates the permitted duration in a time sequence. In order for this to be "
        "used, the isSequence facet must have a value of true.",
        _sequence("3.6.3.3-5"),
    ),
    Rule(
        "IM-3.6.3.3-6",
        "3.6.3.3",
        45,
        "The startTime facet is used in conjunction with the isSequence and timeInterval facets (which must be "
        "set in order to use this facet). This attribute is used for a time sequence and indicates the start "
        "time of the sequence. This value is mandatory for a time sequence to be expressed.",
        _sequence("3.6.3.3-6"),
    ),
    Rule(
        "IM-3.6.3.3-7",
        "3.6.3.3",
        45,
        "The endTime facet is used in conjunction with the isSequence and timeInterval facets (which must be set "
        "in order to use this facet).",
        _sequence("3.6.3.3-7"),
    ),
    # 4 — Item schemes
    Rule(
        "IM-4.3.3.1-1",
        "4.3.3.1",
        49,
        "the prefix property is used to ensure uniqueness of inherited Codes in the extending … Codelist in case "
        "conflicting Codes must be included in the latter.",
        NotChecked(
            "a conflict between inherited codes resolves by precedence (#486); the prefix is the author's way to "
            "keep both, so there is nothing to refuse"
        ),
    ),
    Rule(
        "IM-4.3.3.1-2",
        "4.3.3.1",
        49,
        "Each CodelistExtension association may include one InclusiveCodeSelection or one ExclusiveCodeSelection",
        Structural("`CodelistExtension.selection` is one `CodeSelection | None`, whose `mode` is include or exclude"),
    ),
    Rule(
        "IM-4.3.3.2-1",
        "4.3.3.2",
        50,
        "The latest Codelist used overrides any previous Codelist.",
        NotChecked(
            "a precedence, not a refusal: `Codelist.apply_extensions` lets the later extension win, as FMR 12.4.2 "
            "does (#486, `tests/integration/test_fmr_extension_precedence.py`)"
        ),
    ),
    Rule(
        "IM-4.3.3.2-2",
        "4.3.3.2",
        50,
        "A property to indicate if the child Codes of the selected Code shall be included in the selection.",
        Structural("`MemberValue.cascade` is a `CascadeSelection`: true, false or excludeRoot"),
    ),
    Rule(
        "IM-4.5.2-1",
        "4.5.2",
        57,
        "both ConceptScheme and Concept have the association to InternationalString to support a multi-lingual name",
        Structural("`name` is required, and refused as it is set by `_rules.named` (#492)"),
    ),
    Rule(
        "IM-4.6.4-1",
        "4.6.4",
        62,
        "Each Categorisation can associate one IdentifiableArtefact with one Category.",
        Structural("`Categorisation.source` and `.target` are one `Ref` each"),
    ),
    Rule(
        "IM-4.7.2.1-1",
        "4.7.2.1",
        64,
        "AgencyScheme \u2013 contains Agency which is restricted to a flat list of agencies (i.e., there is no "
        "hierarchy).",
        Structural("`Agency` has no parent field"),
    ),
    Rule(
        "IM-4.7.2.1-2",
        "4.7.2.1",
        64,
        "DataProviderScheme \u2013 contains DataProvider which is restricted to a flat list of agencies (i.e., there "
        "is no hierarchy).",
        Structural("`DataProvider` has no parent field"),
    ),
    # 5 — Data structure definition and data set
    Rule(
        "IM-5.3.2.1-1",
        "5.3.2.1",
        75,
        "Each Dataflow has a maximum of one DataStructureDefinition specified",
        Structural("`Dataflow.structure` is one `Ref[DataStructure] | None`"),
    ),
    Rule(
        "IM-5.3.2.1-2",
        "5.3.2.1",
        75,
        "A Dataflow may optionally define which Dimensions it uses, by defining a DimensionConstraint (this is a "
        "mandatory requirement if the DataStructureDefinition sets its\u2019 evolvingStructure property to "
        "\u2018true\u2019 and is sematically referenced by the Dataflow).",
        Warned(
            (
                Check(
                    "_check_evolving_structure_constraints",
                    "tests/unit/test_evolving_structure.py::test_a_late_bound_flow_of_an_evolving_dsd_warns[minor]",
                ),
            )
        ),
    ),
    Rule(
        "IM-5.3.2.1-3",
        "5.3.2.1",
        76,
        "The GroupDimensionDescriptor must be identified (GroupDimensionDescriptor.id)",
        Structural("`Group.id` is a required field"),
    ),
    Rule(
        "IM-5.3.2.1-4",
        "5.3.2.1",
        76,
        "There can be a maximum of one TimeDimension specified in the DimensionDescriptor.",
        Structural("`DataStructure.time_dimension` is one `TimeDimension | None`"),
    ),
    Rule(
        "IM-5.3.2.1-5",
        "5.3.2.1",
        76,
        "The TimeDimension must contain a valid representation of time",
        _v(
            (
                "_check_time_text_type",
                "tests/unit/test_time_text_type.py::test_a_non_time_text_type_is_invalid",
            )
        ),
    ),
    Rule(
        "IM-5.3.2.1-6",
        "5.3.2.1",
        76,
        "and cannot be coded.",
        Structural("`TimeDimension.representation` is a `Facet | None`; it has no enumeration"),
    ),
    Rule(
        "IM-5.3.2.1-7",
        "5.3.2.1",
        76,
        "a maxOccurs attribute limiting the maximum number of values per Measure (which may be set to 'unbounded' "
        "for unlimited occurrences)",
        _v(
            (
                "_check_3x_components",
                (
                    "tests/unit/test_dsd_3x_components.py::"
                    "test_an_addition_that_says_nothing_coherent_is_refused[max zero]"
                ),
            )
        ),
    ),
    Rule(
        "IM-5.3.2.1-8",
        "5.3.2.1",
        76,
        "the usage attribute indicates whether a Measure must be reported or not, by the corresponding values: "
        "mandatory or optional.",
        Structural(_USAGE),
    ),
    Rule(
        "IM-5.3.2.1-9",
        "5.3.2.1",
        76,
        "The DataAttribute can be indicated if it must be reported or not, by the corresponding value of the "
        "usage attribute: i.e., mandatory or optional.",
        Structural(_USAGE),
    ),
    Rule(
        "IM-5.3.2.1-10",
        "5.3.2.1",
        76,
        "A DataAttribute or a MetadataAttribute (i.e., an AttributeComponent) is specified as being +relatedTo an "
        "AttributeRelationship",
        Structural("`attachment` is required, and refused as it is set by `_rules.attachment_stated` (#508)"),
    ),
    Rule(
        "IM-5.3.2.1-11",
        "5.3.2.1",
        77,
        "these relationships are mutually exclusive, and therefore only one of the following is possible.",
        Structural("`attachment` is one `AttachmentLevel`"),
    ),
    Rule(
        "IM-5.3.2.2-1",
        "5.3.2.2",
        81,
        "The Time Dimension, when specified, must be the last within the Dimension Descriptor.",
        Structural("the time dimension is its own field, never among `dimensions`, and is written after them"),
    ),
    Rule(
        "IM-5.3.2.2-2",
        "5.3.2.2",
        81,
        "Usage Defines whether a Data Attribute must be reported or not.",
        Structural(_USAGE),
    ),
    Rule(
        "IM-5.3.2.2-3",
        "5.3.2.2",
        82,
        "Usage Defines whether a Measure must be reported or not.",
        Structural(_USAGE),
    ),
    Rule(
        "IM-5.3.2.2-4",
        "5.3.2.2",
        82,
        "+dimensions Association to the set of Dimensions to which the Data Attribute is related.",
        _v(
            (
                "_check_attachments",
                "tests/unit/test_attachments.py::test_a_series_attachment_naming_an_unknown_dimension_is_refused",
            )
        ),
    ),
    Rule(
        "IM-5.3.2.2-5",
        "5.3.2.2",
        82,
        "+groupKey Association to the Group Dimension Descriptor which specifies the set of Dimensions to which "
        "the Data Attribute is attached.",
        _v(
            (
                "_check_21_groupings",
                "tests/unit/test_dsd_21_constructs.py::test_validate_refuses_a_grouping_2_1_cannot_mean[an-undeclared-group]",
            )
        ),
    ),
    Rule(
        "IM-5.3.2.2-6",
        "5.3.2.2",
        83,
        "This facet indicates that an Attribute or a Measure has sentinel values with special meaning within their "
        "data type.",
        Warned(
            (
                Check(
                    "_check_sentinel_values",
                    "tests/unit/test_constraint_values.py::test_a_sentinel_value_of_another_type_warns",
                ),
            )
        ),
    ),
    Rule(
        "IM-5.4.3.1-1",
        "5.4.3.1",
        85,
        "Note that the DataSet must conform to the DataStructureDefinition associated to the Dataflow for which "
        "this DataSet is an “instance of data”.",
        NotChecked(_DATA_SET),
    ),
    Rule(
        "IM-5.4.3.1-2",
        "5.4.3.1",
        85,
        "In the actual DataSet as exchanged there must, of course, be a reference to the DataStructureDefinition",
        NotChecked(_DATA_SET),
    ),
    Rule(
        "IM-5.4.3.1-3",
        "5.4.3.1",
        85,
        "this specification is made at the level of the DataSet (i.e., it must be the same Dimension for the "
        "entire DataSet).",
        NotChecked(_DATA_SET),
    ),
    Rule(
        "IM-5.4.3.1-4",
        "5.4.3.1",
        85,
        "The actual value that the CodedDimensionValue can take must be one of the Codes in the Codelist "
        "specified as the Representation of the Dimension in the DataStructureDefinition.",
        NotChecked(_DATA_SET),
    ),
    Rule(
        "IM-5.4.3.2-1",
        "5.4.3.2",
        87,
        "Note that the Data Structure Definition is the same as that associated (non-mandatory) to the Dataflow.",
        NotChecked(_DATA_SET),
    ),
    Rule(
        "IM-5.4.3.2-2",
        "5.4.3.2",
        88,
        "the Code must exist in the Code list associated with the Dimension in the Data Structure Definition.",
        NotChecked(_DATA_SET),
    ),
    Rule(
        "IM-5.4.3.2-3",
        "5.4.3.2",
        90,
        "the Code must exist in the Codelist(s) associated with the Measure(s) in the Data Structure Definition.",
        NotChecked(_DATA_SET),
    ),
    Rule(
        "IM-5.4.3.2-4",
        "5.4.3.2",
        91,
        "the Code must exist in the Code list associated with the Data Attribute in the Data Structure Definition.",
        NotChecked(_DATA_SET),
    ),
    # 7 — Metadata structure definition and metadata set
    Rule(
        "IM-7.3.4.1-1",
        "7.3.4.1",
        98,
        "The MetadataAttribute can be specified as having multiple occurrences and/or specified as being "
        "mandatory (minOccurs=1 or more) or optional (minOccurs=0).",
        _v(
            (
                "_check_metadata_attribute_occurs",
                "tests/unit/test_metadata_attribute_codelist.py::test_occurrences_that_are_no_range_are_refused[max-below-min]",
            )
        ),
    ),
    Rule(
        "IM-7.3.4.1-2",
        "7.3.4.1",
        98,
        "If the Representation is enumerated, then is must use a Codelist.",
        Structural(
            "`MetadataAttribute.representation` is `Ref[Codelist] | Facet | None`; its converter and "
            "`codelist_representation`, which the readers use, refuse a ValueList (GH-28)"
        ),
    ),
    Rule(
        "IM-7.3.4.1-3",
        "7.3.4.1",
        98,
        "The MetadataProvisionAgreement is linked to a Metadataflow.",
        _v(
            (
                "_check_metadata_provision_refs",
                "tests/unit/test_metadata_provision_21.py::test_2_1_refuses_what_would_not_come_back[no-provider]",
            )
        ),
    ),
    Rule(
        "IM-7.4.2.1-1",
        "7.4.2.1",
        101,
        "Note that the MetadataSet must conform to the MetadataStructureDefinition associated to the Metadataflow "
        "or MetadataProvisionAgreement",
        Warned(
            (
                Check(
                    "_check_metadata_set_attributes",
                    "tests/unit/test_metadata_set_conformance.py::test_an_attribute_the_msd_lacks_warns",
                ),
            )
        ),
    ),
    Rule(
        "IM-7.4.2.1-2",
        "7.4.2.1",
        101,
        "In the actual MetadataSet, as exchanged, there must, of course, be a reference to the "
        "MetadataStructureDefinition and optionally a Metadataflow or a MetadataProvisionAgreement",
        Structural(
            "`MetadataSet.structure` is a required reference to a Metadataflow or MetadataProvisionAgreement, "
            "through which the MSD is reached; SDMX 3.x's wire form names the flow, not the MSD"
        ),
    ),
    Rule(
        "IM-7.4.2.1-3",
        "7.4.2.1",
        102,
        "a set of TargetIdentifiableObjects, which must be part of those specified in the relevant Metadataflow "
        "or MetadataProvisionAgreement.",
        Warned(
            (
                Check(
                    "_check_metadata_set_targets",
                    "tests/unit/test_metadata_set_conformance.py::test_a_target_outside_the_flows_warns[in-memory]",
                ),
            )
        ),
    ),
    Rule(
        "IM-7.4.2.1-4",
        "7.4.2.1",
        102,
        "the content is a string value that must conform to the Representation specified for the "
        "MetadataAttribute in the MetadataStructureDefinition.",
        Warned(
            (
                Check(
                    "_check_metadata_set_values",
                    "tests/unit/test_metadata_set_conformance.py::test_a_value_its_text_format_does_not_allow_warns",
                ),
            )
        ),
    ),
    # 8 — Hierarchy
    Rule(
        "IM-8.1-1",
        "8.1",
        106,
        "it may not be sufficient for supporting the more complex associations between codes",
        NotChecked("not a constraint: it motivates the Hierarchy artefact"),
    ),
    Rule(
        "IM-8.2.2.1-1",
        "8.2.2.1",
        107,
        "the Codes participating in a Hierarchy are not themselves contained in the list \u2013 they are referenced "
        "from the list",
        Structural("`HierarchicalCode.code` is a `Ref[Code]`"),
    ),
    Rule(
        "IM-8.3.2.2-1",
        "8.3.2.2",
        110,
        "+code Association to the Code that is used at the specific point in the hierarchy.",
        _v(
            (
                "_validate_hierarchy",
                "tests/unit/test_hcl_alias_form.py::test_a_node_without_a_code_is_refused_as_it_is_built",
            )
        ),
    ),
    Rule(
        "IM-8.3.2.2-2",
        "8.3.2.2",
        110,
        "+level Association to a Level where levels have been defined for the Hierarchy.",
        _v(
            (
                "_check_node_levels",
                "tests/unit/test_hierarchy_levels.py::test_a_node_naming_a_level_the_hierarchy_lacks_is_refused",
            )
        ),
    ),
    # 9, 10, 11 — Maps
    Rule(
        "IM-9.1.2.1-1",
        "9.1.2.1",
        111,
        "The StructureMap can either map a source and target DataStructureDefinition or a source and target "
        "Dataflow, it cannot mix source and target types.",
        Warned(
            (
                Check(
                    "_check_structure_map_kinds",
                    "tests/unit/test_structure_map_rules.py::test_a_map_of_a_structure_to_a_structure_usage_warns",
                ),
            )
        ),
    ),
    Rule(
        "IM-9.1.2.1-2",
        "9.1.2.1",
        112,
        "If a ComponentMap has more than 1 source or target, a RepresentationMap must be used to describe how the "
        "values map",
        Warned(
            (
                Check(
                    "_check_component_map_representation",
                    "tests/unit/test_structure_map_rules.py::test_a_many_to_one_component_map_without_a_representation_map_warns",
                ),
            )
        ),
    ),
    Rule(
        "IM-9.1.4.2-1",
        "9.1.4.2",
        114,
        "The frequency to convert the input date into. Mutually exclusive with freqDimension.",
        _v(
            (
                "_check_date_map_frequency",
                "tests/unit/test_structure_map_rules.py::test_a_date_map_names_one_frequency_source[both]",
            )
        ),
    ),
    Rule(
        "IM-10.1.2.1-1",
        "10.1.2.1",
        117,
        "the RepresentationMapping sourceValues and targetValues must match the order.",
        Warned(
            (
                Check(
                    "_check_mapping_arity",
                    "tests/unit/test_structure_map_rules.py::test_a_mapping_of_the_wrong_arity_warns[too-few-sources]",
                ),
            )
        ),
    ),
    Rule(
        "IM-11.1-1",
        "11.1",
        120,
        "A single source ItemScheme is mapped to a single target ItemScheme.",
        Structural("each item scheme map's `source` and `target` are one `Ref` each"),
    ),
    # 12, 13 — Constraints and provisioning
    Rule(
        "IM-12.2.2.1-1",
        "12.2.2.1",
        124,
        "the ConstrainableArtefacts must be associated with a specific Structure. Therefore, whilst the "
        "Constraint itself may not be linked directly to a DataStructureDefinition or MetadataStructureDefinition",
        NotChecked(
            "the IM itself makes a dataflow's DSD non-mandatory (§5.4.3.2, p. 87), so `Dataflow.structure` stays "
            "optional and a constraint on a flow without one is not refused"
        ),
    ),
    Rule(
        "IM-12.3.2.1-1",
        "12.3.2.1",
        125,
        "An AvailableDataConstraint contains only 1 CubeRegion",
        Structural("`AvailabilityConstraint.region` is one `CubeRegion`"),
    ),
    Rule(
        "IM-12.3.3.1-1",
        "12.3.3.1",
        127,
        "A DataConstraint is restricted to a maximum of 2 CubeRegions, one to define included (allowable) "
        "content, and the other to define disallowed content (isIncluded=false).",
        _v(
            (
                "_check_cube_region_inclusion",
                "tests/unit/test_serialisation_conformance.py::test_two_included_cube_regions_are_refused_in_3x[3.1]",
            )
        ),
    ),
    Rule(
        "IM-12.3.3.1-3",
        "12.3.3.1",
        127,
        "A DataConstraint has a choice of two ways of specifying value subsets",
        Warned(
            (
                Check(
                    "_check_constraint_choice",
                    "tests/unit/test_im_rule_warnings.py::test_a_constraint_with_regions_and_key_sets_warns",
                ),
            )
        ),
    ),
    Rule(
        "IM-12.3.3.1-2",
        "12.3.3.1",
        127,
        "the value in ComponentValue.value and MemberValue.value must be consistent with the Representation "
        "declared for the Component",
        Warned(
            (
                Check(
                    "_check_constraint_values",
                    "tests/unit/test_constraint_values.py::test_a_key_value_its_codelist_lacks_warns[CC_data_structures]",
                ),
            )
        ),
    ),
    Rule(
        "IM-12.3.3.2-1",
        "12.3.3.2",
        132,
        "locale The locale that the values must adhere to in the dataset.",
        NotChecked(_DATA_SET),
    ),
    Rule(
        "IM-13.2.1-1",
        "13.2.1",
        135,
        "all of the artefacts in this sub model must be accessible to an application that is responsible for "
        "data and metadata registration",
        NotChecked("an obligation on a registry application, not on an artefact"),
    ),
    Rule(
        "IM-13.2.2-1",
        "13.2.2",
        137,
        "Links the Data Provider to the relevant Structure Usage (i.e., the Dataflow) for which the provider "
        "supplies data.",
        _v(
            (
                "_check_provision_agreement",
                "tests/unit/test_serialisation_conformance.py::test_a_provision_agreement_needs_both_its_edges",
            )
        ),
    ),
    # 15 — VTL
    Rule(
        "IM-15.3.2.1-1",
        "15.3.2.1",
        146,
        "The TransformationScheme must include the attribute vtlVersion expressed as a string",
        NotChecked(_VTL),
    ),
    Rule(
        "IM-15.3.2.1-2",
        "15.3.2.1",
        147,
        "An artefact cannot be result of more than one Transformation.",
        NotChecked(_VTL),
    ),
    Rule(
        "IM-15.3.2.1-3",
        "15.3.2.1",
        148,
        "The VtlMappingScheme associated to a TransformationScheme must contain the mappings between the "
        "references to the SDMX artefacts from the TransformationScheme and the structured identifiers of these "
        "SDMX artefacts.",
        NotChecked(_VTL),
    ),
    Rule(
        "IM-15.3.2.1-4",
        "15.3.2.1",
        149,
        "the UserDefinedOperatorScheme can link to zero or one VtlMappingScheme, which must contain the mappings "
        "between the VTL references and the structured URN of the corresponding SDMX artefacts",
        NotChecked(_VTL),
    ),
    Rule(
        "IM-15.3.2.1-5",
        "15.3.2.1",
        149,
        "the UserDefinedOperatorScheme can link to zero, one or more RulesetScheme, which must contain the "
        "definition of these Rulesets",
        NotChecked(_VTL),
    ),
    Rule(
        "IM-15.3.2.1-6",
        "15.3.2.1",
        150,
        "When an alternative mapping method is applied for some Dataflow, this must be specified in "
        "toVtlMappingMethod or fromVtlMappingMethod.",
        NotChecked(_VTL),
    ),
    Rule(
        "IM-15.3.2.1-7",
        "15.3.2.1",
        152,
        "it must be specified in the vtlLiteralFormat attribute of the CustomType",
        NotChecked(_VTL),
    ),
    Rule(
        "IM-15.3.2.2-1",
        "15.3.2.2",
        154,
        "dataflowAlias Alias used in VTL to reference a SDMX dataflow (it can be the URN, the abbreviated URN or "
        "a user defined alias). The alias must be univocal: different SDMX artefacts cannot have the same VTL "
        "alias.",
        NotChecked(_VTL),
    ),
    Rule(
        "IM-15.3.2.2-2",
        "15.3.2.2",
        154,
        "codelistAlias Name used in VTL to reference a SDMX codelist. The name/alias must be univocal: different "
        "SDMX artefacts cannot have the same VTL alias.",
        NotChecked(_VTL),
    ),
    Rule(
        "IM-15.3.2.2-3",
        "15.3.2.2",
        155,
        "conceptAlias Name used in VTL to reference a SDMX concept. The name/alias must be univocal: different "
        "SDMX artefacts cannot have the same VTL alias.",
        NotChecked(_VTL),
    ),
    Rule(
        "IM-15.3.2.2-4",
        "15.3.2.2",
        156,
        "datatype Custom specification of the external (SDMX) data type in which the VTL data type must be converted",
        NotChecked(_VTL),
    ),
)

#: Every check in `model.validation` no rule cites, and what it enforces
#: instead: a wire format's rule, or a part of another check.
NON_IM_CHECKS: dict[str, str] = {
    "_check_dimension_multilingual": "format: every format's dimension and time formats prohibit isMultiLingual (#503)",
    # The wire formats' identifier, version and reference grammars — the IM
    # leaves their syntax to the schemas and the technical notes.
    "_check_id": "format: IDType, the schemas' identifier pattern",
    "_check_item_ids": "format: IDType over an agency scheme's agency ids; other items refuse theirs as set",
    "_check_name": "an agency's name; every other name is refused as it is set (#492)",
    "_check_item_names": "an agency scheme's agency names, through `_check_name`",
    "_check_ref_urn": "format: a reference's URN syntax",
    "_check_fixed_scheme_version": "format: the version every format pins for an organisation scheme",
    "_check_fixed_scheme_id": "format: the ids the schemas pin to one literal value",
    "_check_artefact_versions": "format: the target grammar's version syntax (#319)",
    "_check_ref_versions": "format: a reference's version as the target can name it (#429)",
    "_check_latest_refs": "format: a concrete version on every reference (#233)",
    "_check_ref_classes": "format: a reference names a class the target defines (#430)",
    # Representation types as the schemas spell them.
    "_check_enumeration_format": "format: RepresentationType's choice, and what an enumeration format can carry",
    "_check_coding_format": "format: a Level's CodingFormat is SimpleCodeDataType",
    "_check_absent": "part of _check_enumeration_format and _check_coding_format",
    "_check_dsd_sentinels": "format: SentinelValueType sits on a text format only",
    # A data structure's parts as the schemas spell them.
    "_check_series": "part of _check_attachments",
    "_check_group": "part of _check_attachments",
    "_check_dsd_21_only": "format: what of a DSD only SDMX-ML 2.1 has",
    "_check_measure_dimension_version": "format: MeasureDimension exists in SDMX-ML 2.1 only (#305)",
    "_check_usage_links": "format: a metadata attribute usage has at most one link in SDMX-ML 3.x",
    # Constraints as the schemas spell them.
    "_check_key_sets": "format: DataKeySetType requires a key (#479)",
    "_check_attachment_choice": "format: ConstraintAttachmentType is a choice (#422)",
    "_check_region_values": "format: CubeKeyValueType and DataComponentValueType's qualifiers (#422)",
    "_check_cascade_in_2_1": "format: 2.1's cascadeValues is a boolean",
    "_check_empty_keys_in_ml": "format: SDMX-ML's KeyValue requires values or a time range (#433)",
    "_check_constraint_in_31": "format: what SDMX-ML 3.1 can carry of a constraint",
    "_check_availability_in_31": "format: what SDMX-ML 3.1 can carry of an availability constraint (#421)",
    "_check_attachment_in": "format: what each version can carry of a constraint attachment (#422)",
    "_check_constraints_in": "format: both constraint artefacts, as a version can write them",
    # Metadata as the schemas spell it.
    "_check_metadataflow_targets": "format: a 3.x metadataflow names a target",
    "_check_metadata_set_in_json": "format: what SDMX-JSON asks of a metadata set beyond SDMX-ML",
    "_check_msd_in_21": "format: a 2.1 MSD needs the report layer 3.0 removed",
    "_check_metadata_provision_in_21": "format: metadata provisioning in SDMX-ML 2.1 (#421)",
    # One version's projection of another's artefacts.
    "_check_representation_map_in_21": "format: a 2.1 CodelistMap maps codes to codes",
    "_check_text_format_mappings_in_21": "format: a 2.1 ToTextFormat goes with a ToValueType",
    "_check_structure_map_in_21": "format: a 2.1 StructureMap names components its own way",
    "_check_extensions_in_21": "format: SDMX-ML 2.1 has no codelist extensions (#421)",
    "_check_containers_in_21": "format: 2.1's HierarchicalCodelist and StructureSet containers (#421)",
    "_check_code_parents_in_30": "format: SDMX-ML 3.0.0 types a code's parent as an NCName (#432)",
    "_check_hierarchy_node_ids_in_3x": "format: SDMX-ML 3.x keys a node by id across the hierarchy (#439)",
    "_check_wildcards_in_json": "format: SDMX-JSON spells a wildcarded member value out (#451)",
    "_check_links_in_json": "format: links SDMX-JSON cannot carry (#422)",
    "_check_unit_scheme_version": "format: 3.x and SDMX-JSON fix an organisation scheme's version (#469)",
    # The write boundary's dispatch over the above.
    "_check_writer_coverage": "format: what the target writer would drop",
    "_check_required_content": "format: elements the target schema requires (#229)",
    "_check_version_specific": "format: valid SDMX, not valid in this version",
    "_check_message_version": "format: dispatch of the per-version checks",
    # Dispatch: each runs the checks for one class of artefact, and owns no rule itself.
    "_validate_maintainable": "dispatch",
    "_validate_codelist": "dispatch",
    "_validate_concept_scheme": "dispatch",
    "_validate_category_scheme": "dispatch",
    "_validate_agency_scheme": "dispatch",
    "_validate_data_provider_scheme": "dispatch",
    "_validate_data_consumer_scheme": "dispatch",
    "_validate_organisation_unit_scheme": "dispatch",
    "_validate_dsd": "dispatch",
    "_validate_metadata_structure": "dispatch",
    "_validate_structure_map": "dispatch",
    "_validate_data_constraint": "dispatch, and a selection's values-or-time-range choice, a format rule (#433)",
    "_validate_attachment_constraint": "dispatch",
}

#: The chapters whose definitions-table multiplicities are still to be surveyed
#: — the census's next phase, one chapter at a time.
UNSURVEYED_MULTIPLICITIES: tuple[tuple[str, str, int], ...] = (
    # (chapter, title, first page)
    ("3", "SDMX Base Package", 28),
    ("4", "Specific Item Schemes", 46),
    ("5", "Data Structure Definition and Dataset", 71),
    ("6", "Cube", 93),
    ("7", "Metadata Structure Definition and Metadata Set", 95),
    ("8", "Hierarchy", 106),
    ("9", "Structure Map", 111),
    ("10", "RepresentationMap", 116),
    ("11", "ItemSchemeMap", 120),
    ("12", "Constraints", 123),
    ("13", "Data Provisioning", 134),
    ("14", "Process", 139),
    ("15", "Validation and Transformation Language", 143),
)


@functools.cache
def page_text(page: int) -> str:
    """One page of the vendored IM, line numbers stripped and whitespace collapsed.

    The PDF numbers every line in the margin, so `pdftotext -layout` leaves
    them at the head of each line; a quote spanning a line break would not
    match with them in.
    """
    pages = IM_TEXT.read_text(encoding="utf-8").split("\f")
    lines = [re.sub(r"^\s*\d{1,4}(\s+\d{1,4})*(\s+|$)", "", line) for line in pages[page - 1].split("\n")]
    return re.sub(r"\s+", " ", " ".join(lines))


#: How far past one fragment the next may start: a table row's width of interleaved text.
_GAP = 400


def locate(rule: Rule) -> tuple[int, int] | None:
    """Where `rule.quote` sits on its page, as a span; None if it does not.

    Each fragment must follow the previous one within `_GAP` characters, so an
    elided quote cannot be assembled from words scattered across the page.
    """
    text = page_text(rule.page)
    fragments = rule.quote.split(ELISION)
    start = text.find(fragments[0])
    while start >= 0:
        end = start + len(fragments[0])
        for fragment in fragments[1:]:
            at = text.find(fragment, end)
            if at < 0 or at - end > _GAP:
                break
            end = at + len(fragment)
        else:
            return start, end
        start = text.find(fragments[0], start + 1)
    return None
