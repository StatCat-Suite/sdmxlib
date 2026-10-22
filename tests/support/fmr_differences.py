"""Every way FMR 12.4.2's copy of our artefacts differs from what we sent, and whose it is (#457).

`tests.support.fmr_oracle.differences` reads both through our reader and
lists what differs beyond the gaps `formats.gaps` already declares. Each
difference is classified by one `Rule` here, found by reading FMR's raw
response rather than inferred from the diff -- a field our reader came back
without could as well be one it failed to read, and three were (#489, #490,
and the coding-format types below):

- **fmr-drops** -- FMR serves the artefact without it.
- **fmr-changes** -- FMR serves a different value, which means something else.
- **fmr-normalises** -- FMR serves a different spelling of the same value.
- **fmr-converts** -- FMR moves between 2.1's shapes and 3.x's differently from sdmxlib.
- **fmr-invalid** -- FMR serves a document its schema rejects, and our reader
  refuses what is invalid; `test_fmr_oracle.py` checks the documents really fail.
- **reader-limit** -- our reader refuses, by name, a construct it does not read.
- **ours** -- a defect of ours, with its issue.
- **equivalent** -- the two sides hold equal values in different types.

Checked both ways: every difference is matched by exactly one rule -- the
rules are disjoint, so a broad rule cannot quietly absorb what a narrow one
was written for -- and every rule matches a difference, so a rule FMR stops
needing is reported.
"""

from typing import Literal

from tests.support.interop import Rule

type Kind = Literal[
    "fmr-drops", "fmr-changes", "fmr-normalises", "fmr-converts", "fmr-invalid", "reader-limit", "ours", "equivalent"
]


_ANY = r".+"
_PATH = r"\w+(?:\.\w+(?:\[\])?)*"

RULES: tuple[Rule[Kind], ...] = (
    # FMR serves a document its schema rejects.
    Rule(
        r"REFUSED \w+: a link needs a rel - at …",
        r".+:json(?:10|20)",
        "fmr-invalid",
        "FMR serves an annotation's URL as a link with no rel, which both SDMX-JSON schemas require; the reader "
        "refuses the artefact that carries it",
    ),
    Rule(
        r"REFUSED (?:DataStructure|ConceptScheme): <Enumeration>: schema requires a Codelist reference here, .+",
        r"2\.1-maximal:ml30|3\.0-maximal:ml21",
        "fmr-invalid",
        "FMR serves an Enumeration naming a ConceptScheme (3.0, for 2.1's measure dimension) or a ValueList "
        "(2.1, which has none); the XSD types it a codelist reference",
    ),
    Rule(
        r"REFUSED OrganisationUnitScheme: <str:OrganisationUnitScheme>: @version is not allowed on it …; .+",
        r".+:ml30",
        "fmr-invalid",
        "FMR serves a 3.0 organisation unit scheme with @version, which `OrganisationSchemeBaseType` prohibits "
        "on every organisation scheme (#469)",
    ),
    # FMR is 3.x inside, and 3.x has no version for an organisation scheme.
    Rule(
        r"MISSING OrganisationUnitScheme AG:UNITS_21\(2\.1\.0\)|EXTRA OrganisationUnitScheme AG:UNITS_21\(1\.0\)",
        r"2\.1-shape:.+",
        "fmr-changes",
        "FMR holds a 2.1 organisation unit scheme at 1.0, whatever version 2.1 gave it: 3.x prohibits the "
        "version, and sdmxlib refuses to write such a scheme as 3.x rather than drop it (#469)",
    ),
    Rule(
        r"REFUSED (?:DataConstraint|Hierarchy): sdmxlib does not model (?:ContentConstraint|HierarchicalCodelist)",
        r".+:json10",
        "reader-limit",
        "the SDMX-JSON 1.0 reader refuses 1.0's contentConstraints and hierarchicalCodelists by name (#489)",
    ),
    # FMR's moves between 2.1 and 3.x.
    Rule(
        r"MISSING (?:StructureSet AG:SS|HierarchicalCodelist AG:HCL)\(1\.0\)",
        r"2\.1-maximal:ml21",
        "fmr-converts",
        "FMR holds a 2.1 container as the 3.x artefacts in it, and serves a hierarchy back in a container named "
        "after it rather than the original",
    ),
    Rule(
        r"MISSING (?:StructureMap AG:SM|RepresentationMap AG:RM)@SS\(1\.0\)|EXTRA (?:StructureMap AG:SM|"
        r"RepresentationMap AG:RM)\(1\.0\)",
        r"2\.1-maximal:.+",
        "fmr-converts",
        "a 2.1 StructureSet's maps become standalone 3.x maps: FMR keeps their own ids (SM, RM); sdmxlib qualifies "
        "them by their set (SM@SS, #421)",
    ),
    Rule(
        r"MISSING MetadataProviderScheme AG_MD:METADATA_PROVIDERS\(1\.0\)|EXTRA DataProviderScheme "
        r"AG_MD:DATA_PROVIDERS\(1\.0\)",
        r"2\.1-maximal:.+",
        "fmr-converts",
        "2.1 has no metadata provider scheme; sdmxlib's 2.1 convention for one (#421) reaches FMR as a data "
        "provider scheme",
    ),
    Rule(
        r"MetadataStructure\.(?:targets \(length\)|report_structures \(length\)|report_structures\[\]\.(?:id|"
        r"target_ids\[\]|annotations \(length\)))",
        r".+:ml21",
        "fmr-converts",
        "FMR's 2.1 MSD has a reporting layer of its own making -- one report 'Report' on one target 'Target' -- "
        "where sdmxlib refuses to write a 3.x MSD as 2.1 without one (`_check_msd_in_21`)",
    ),
    Rule(
        r"Hierarchy\.annotations \(length\)",
        r"2\.1-maximal:ml(?:21|30)",
        "fmr-converts",
        "FMR gives the hierarchy its 2.1 HierarchicalCodelist's annotations as well as its own",
    ),
    Rule(
        r"Codelist\.extensions \(length\)",
        r"3\.0-shape:.+",
        "fmr-converts",
        "FMR resolves a codelist extension into codes and serves no extension; this one inherits nothing",
    ),
    # FMR leaves it out.
    Rule(
        r"DataConstraint\.data_key_sets\[\]\.keys\[\]\."
        r"(?:valid_(?:from|to) \(type\)|components\[\]\.(?:include|values))",
        r"3\.0-maximal:ml30",
        "fmr-drops",
        "FMR's 3.0 key set serves a Key without @validFrom/@validTo, its Component without @include, and the "
        "component's Value without @cascadeValues and @xml:lang (seen once #491 gave key sets their own constraint)",
    ),
    Rule(
        r"(?:(?:DataStructure|MetadataStructure)\.attributes\[\]\.representation"
        r"|ConceptScheme\.concepts\[\]\.core_representation) \(type\)",
        r".+",
        "fmr-drops",
        "FMR drops a TextFormat that states nothing beyond its type's defaults, which sdmxlib leaves out (#503): "
        "the component comes back with no local representation, the concept with no core one",
    ),
    Rule(
        r"DataStructure\.measures\[\]\.representation\.is_multilingual",
        r"(?:2\.1|3\.0)-shape:ml30",
        "fmr-changes",
        "FMR's 3.0 output leaves out a measure's isMultiLingual='false', whose absence 3.0 reads as true",
    ),
    Rule(
        r"MISSING (?:ConceptSchemeMap AG:CSM@SS|OrganisationSchemeMap AG:OSM@SS|CategorySchemeMap AG:CSM)\(1\.0\)",
        r"2\.1-(?:maximal|shape):.+",
        "fmr-drops",
        "FMR keeps a 2.1 StructureSet's structure and codelist maps and silently drops its concept, organisation "
        "and category scheme maps, reporting the submission a success",
    ),
    Rule(
        r"MISSING (?:StructureMap|RepresentationMap) AG:\w+(?:@SS)?\(1\.0\)",
        r"3\.0-(?:maximal|shape):(?:ml21|json10)",
        "fmr-drops",
        "FMR's 2.1 and JSON 1.0 output leave out 3.x structure and representation maps; sdmxlib writes the ones "
        "2.1 can hold into a StructureSet",
    ),
    Rule(
        r"MISSING (?:GeographicCodelist AG:CL_GEO|GeoGridCodelist AG:CL_GRID|MetadataProvisionAgreement AG:MPA|"
        r"MetadataProviderScheme AG_MD:METADATA_PROVIDERS)\(1\.0\)",
        r"3\.0-maximal:(?:ml21|json10)",
        "fmr-drops",
        "FMR's 2.1 and JSON 1.0 output leave out what 2.1 has no element for; sdmxlib writes them -- geographic "
        "codelists as plain ones, metadata provisioning by its convention (#421) -- declaring what goes",
    ),
    Rule(
        r"MISSING (?:ValueList AG:VL|HierarchyAssociation AG:HA)\(1\.0\)",
        r"3\.0-maximal:json10",
        "fmr-drops",
        "FMR's JSON 1.0 output leaves out value lists and hierarchy associations, which the 1.0 schema has no "
        "section for",
    ),
    Rule(
        r"DataStructure\.measures\[\]\.min_occurs",
        r"3\.0-shape:json10",
        "fmr-drops",
        "FMR's JSON 1.0 output gives a measure no minOccurs, which the 1.0 schema has only on a metadata "
        "attribute: 2.1's primary measure has none, so a 3.x optional measure reads back required",
    ),
    Rule(
        r"MISSING MetadataStructure AG:MSD(?:_SHAPES)?\(1\.0\)",
        r".+:json10",
        "fmr-drops",
        "FMR's JSON 1.0 output has no metadataStructures, though the 1.0 schema declares the section",
    ),
    Rule(
        rf"{_PATH}\.(?:uri|service_url|structure_url|\w+_uri) \(type\)",
        r".+:ml(?:21|30)",
        "fmr-drops",
        "FMR keeps no uri on an artefact or its component lists and groups, and drops serviceURL and structureURL "
        "on geographic codelists",
    ),
    Rule(
        rf"{_PATH}\.is_external_reference",
        _ANY,
        "fmr-changes",
        'FMR serves isExternalReference="false" for every artefact: it holds the content, so it does not keep '
        "the flag it was given",
    ),
    Rule(
        rf"{_PATH}\.(?:links|annotations\[\]\.urls) \(length\)",
        r"3\.0-maximal:ml30",
        "fmr-drops",
        "FMR keeps some com:Link elements and one AnnotationURL per annotation -- the last -- and drops the rest",
    ),
    Rule(
        rf"{_PATH}\.(?:\w+_descriptor_annotations|attribute_list_annotations|groups\[\]\.annotations) \(length\)",
        r".+-maximal:ml(?:21|30)",
        "fmr-drops",
        "FMR keeps no annotations on component lists or groups",
    ),
    Rule(
        r"DataStructure\.(?:dimensions\[\]|attributes\[\]|measure_dimension)\.concept_role \(length\)",
        r"2\.1-maximal:ml21",
        "fmr-drops",
        "FMR's 2.1 output carries no ConceptRole",
    ),
    Rule(
        r"DataStructure\.attributes\[\]\.attachment_groups \(length\)",
        r"2\.1-shape:ml21",
        "fmr-drops",
        "FMR's 2.1 output carries no AttachmentGroup beside an attribute's dimensions",
    ),
    Rule(
        r"DataStructure\.attributes\[\]\.optional_dimensions",
        r"3\.0-maximal:ml30",
        "fmr-drops",
        "FMR drops @optional on an attribute relationship's dimension",
    ),
    Rule(
        r"DataConstraint\.(?:regions \(length\)|regions\[\]\.(?:keys|components) \(length\))",
        _ANY,
        "fmr-drops",
        "FMR drops a cube region's time-range selections -- emptying a region that held only one -- and with "
        "them @include, validity and xml:lang on what it keeps",
    ),
    Rule(
        r"DataConstraint\.reference_period \(type\)",
        r"2\.1-maximal:ml21",
        "fmr-drops",
        "FMR's 2.1 output carries no ReferencePeriod",
    ),
    Rule(
        r"Hierarchy\.tree\[\](?:\.children\[\])?\.level \(type\)",
        r".+-maximal:ml(?:21|30)",
        "fmr-drops",
        "FMR serves no hierarchical code's level reference",
    ),
    Rule(
        r"Hierarchy\.tree\[\]\.version \(type\)",
        r"2\.1-maximal:ml21",
        "fmr-drops",
        "FMR's 2.1 output carries no hierarchical code's version",
    ),
    Rule(
        r"StructureMap\.(?:frequency_format_mappings|fixed_value_maps\[\]\.values) \(length\)",
        r"3\.0-maximal:ml30",
        "fmr-drops",
        "FMR keeps no FrequencyFormatMapping and one value of a FixedValueMap",
    ),
    # FMR changes it.
    Rule(
        r"DataConstraint\.id",
        r".+-maximal:ml(?:21|30)",
        "fmr-changes",
        "FMR upper-cases an id with lower-case letters (CC_data_provider becomes CC_DATA_PROVIDER); SDMX ids are "
        "case-sensitive, so references to the original no longer resolve",
    ),
    Rule(
        r"DataStructure\.(?:time_dimension|dimensions\[\])\.representation\.end_time",
        _ANY,
        "fmr-changes",
        "FMR serves a month endTime '2030-12' as '2030-12-01T23:59:59', the end of the month's first day -- "
        "30 days short",
    ),
    Rule(
        r"(?:MetadataStructure\.attributes\[\](?:\.children\[\])?|DataStructure\.attributes\[\]|"
        r"ConceptScheme\.concepts\[\])\.(?:min_occurs|max_occurs(?: \(type\))?)",
        _ANY,
        "fmr-changes",
        "FMR does not keep occurrence bounds: an attribute's maxOccurs='unbounded' is dropped (so 1), and a "
        "metadata attribute's 0..1 becomes 1..unbounded",
    ),
    Rule(
        r"StructureMap\.(?:annotations|fixed_value_maps\[\]\.annotations) \(length\)",
        r"3\.0-maximal:ml30",
        "fmr-changes",
        "FMR moves a FixedValueMap's annotation onto its StructureMap",
    ),
    # FMR respells it.
    Rule(
        rf"(?!DataStructure\.(?:time_dimension|dimensions\[\])\.representation\.end_time$){_PATH}\.(?:start_time|end_time)",
        _ANY,
        "fmr-normalises",
        "FMR serves a date facet as a dateTime: a start at 00:00:00, an end at 23:59:59 of the same day",
    ),
    Rule(
        r"DataStructure\.attributes\[\]\.related_measures \(type\)",
        r"3\.0-maximal:ml30",
        "fmr-normalises",
        "FMR lists every measure where none was named; IM 5.3.2.1: 'Lack of the MeasureRelationship defaults to "
        "a relationship to all Measures'",
    ),
    Rule(
        r"RepresentationMap\.mappings\[\]\.sources\[\]\.start_index \(type\)",
        r"3\.0-shape:.+",
        "fmr-normalises",
        'FMR omits startIndex="0", the start of the value either way',
    ),
    # Our two readers.
    Rule(
        r"Hierarchy\.levels\[\]\.coding_format\.(?:start_value|end_value|min_value|max_value|interval) \(type\)",
        r"2\.1-maximal:ml30|3\.0-maximal:ml21",
        "equivalent",
        "the 2.1 reader keeps CodingTextFormatType's xs:integer bounds as int and the 3.x reader as float; the "
        "values are equal",
    ),
)

_LINK = "an annotation's URL served as a link with no rel, which the schema requires"
_UNIT_VERSION = "an organisation unit scheme with @version, which 3.0 prohibits on every organisation scheme (#469)"

#: Every FMR response that fails its schema -- the XSD for SDMX-ML, the
#: vendored JSON schema for SDMX-JSON -- by ``case/agency-format``, and why.
#: Exact both ways: a response newly invalid, or newly valid, fails the test.
INVALID: dict[str, str] = {
    "2.1-maximal/AG-json10": _LINK,
    "2.1-maximal/AG-json20": _LINK,
    "2.1-maximal/AG_MD-json10": _LINK,
    "2.1-maximal/AG_MD-json20": _LINK,
    "3.0-maximal/AG-json10": _LINK,
    "3.0-maximal/AG-json20": _LINK,
    "3.0-maximal/AG_MD-json20": _LINK,
    "2.1-maximal/AG-ml30": f"an Enumeration naming a ConceptScheme, for 2.1's measure dimension; and {_UNIT_VERSION}",
    "3.0-maximal/AG-ml21": "a reference of class ValueList, which 2.1 does not have",
    "3.0-maximal/AG-ml30": (
        "AnnotationValue written before AnnotationURL and AnnotationText, out of AnnotationType's order. Our "
        "reader does not check element order, so it reads these annotations rather than refusing them; "
        f"and {_UNIT_VERSION}"
    ),
    "3.0-maximal/AG_MD-ml30": "as AG-ml30: annotation children out of order",
    "2.1-shape/AG-json10": "a constraint type 'allowed', where the 1.0 schema has 'Allowed' and 'Actual'",
    "2.1-shape/AG-json20": (
        "a facet endTime with no time zone, which the schema's date-time requires; "
        "seen once formats were checked (#496)"
    ),
    "3.0-shape/AG-json10": "a constraint type 'allowed', where the 1.0 schema has 'Allowed' and 'Actual'",
    "2.1-shape/AG-ml30": _UNIT_VERSION,
    "3.0-shape/AG-json20": "an empty targetValues for a mapping with no target, where the schema requires one or more",
}
