"""Every way pysdmx 1.20.0's copy of our SDMX-ML 3.1 differs from what we sent, and whose it is (#457).

`tests.support.pysdmx_oracle.differences` reads both through our reader and
lists what differs beyond the gaps `formats.gaps` declares for 3.1. Each
difference is classified by one `Rule` here, found by reading pysdmx's output
and its in-memory model rather than inferred from the diff:

- **pysdmx-drops** -- pysdmx's copy leaves it out. Each reason says where it
  goes: pysdmx's model has no field for it, its reader leaves a field it has
  empty, or its writer leaves out a field it read.
- **pysdmx-changes** -- pysdmx's copy holds a different value, which means
  something else.
- **pysdmx-normalises** -- a different spelling of the same value.
- **pysdmx-skips** -- pysdmx's reader skips an artefact class it does not
  model, without saying so.
- **pysdmx-invalid** -- pysdmx writes what the 3.1 schema rejects, and our
  reader refuses what is invalid; `test_pysdmx_oracle.py` checks the document
  really fails.

Checked both ways, as `fmr_differences` is: every difference is matched by
exactly one rule, and every rule matches a difference.
"""

from typing import Literal

from tests.support.interop import Rule

type Kind = Literal["pysdmx-drops", "pysdmx-changes", "pysdmx-normalises", "pysdmx-skips", "pysdmx-invalid"]

_MAXIMAL = r"3\.1-maximal:ml31"
_SHAPE = r"3\.1-shape:ml31"
_BOTH = r"3\.1-(?:maximal|shape):ml31"
#: A representation, wherever one sits: a concept's, a component's, or one our reader resolves through a concept.
_FACET = r"\w+\.(?:\w+(?:\[\])?\.)*(?:core_representation|enumeration_format|representation)"

RULES: tuple[Rule[Kind], ...] = (
    # pysdmx writes what the 3.1 schema rejects.
    Rule(
        r"\w+\.(?:\w+\[\]\.)*annotations\[\]\.urls \(length\)",
        _MAXIMAL,
        "pysdmx-invalid",
        "pysdmx's reader keeps an annotation's localised AnnotationURLs as the raw parsed list, and its writer "
        "writes the list's Python repr as one URL, which is no anyURI",
    ),
    Rule(
        r"MISSING ValueList AG:VL\(1\.0\)",
        _MAXIMAL,
        "pysdmx-invalid",
        "pysdmx models a value list as a Codelist and writes it inside str:Codelists, where the 3.1 schema "
        "allows only Codelist; our reader refuses it there",
    ),
    # pysdmx's reader skips the class.
    Rule(
        r"MISSING (?:CategorySchemeMap|ConceptSchemeMap|OrganisationSchemeMap|OrganisationUnitScheme|"
        r"GeographicCodelist|GeoGridCodelist) .+",
        _BOTH,
        "pysdmx-skips",
        "pysdmx has no model for the class, and its reader skips it without a word",
    ),
    # pysdmx's model has no field for it.
    Rule(
        r"\w+\.(?:\w+(?:\[\])?\.)*(?:\w+_)?links \(length\)",
        _MAXIMAL,
        "pysdmx-drops",
        "pysdmx's model has no links (SDMX 3.x's com:Link) anywhere",
    ),
    Rule(
        r"\w+\.(?:\w+(?:\[\])?\.)*(?:name|description|text|department|role) \(languages\)",
        _MAXIMAL,
        "pysdmx-drops",
        "pysdmx's model holds one string where SDMX holds an international string; its reader keeps English",
    ),
    Rule(
        _FACET + r"\.sentinel_values \(length\)",
        _BOTH,
        "pysdmx-drops",
        "pysdmx's Facets have no sentinel values",
    ),
    Rule(
        r"DataStructure\.\w+\[\]\.concept_role \(length\)",
        _MAXIMAL,
        "pysdmx-drops",
        "pysdmx's Component has no concept roles",
    ),
    Rule(
        r"(?:Codelist\.codes|ConceptScheme\.concepts)\[\]\.parent_id \(type\)",
        _MAXIMAL,
        "pysdmx-drops",
        "pysdmx's Code and Concept have no parent",
    ),
    Rule(
        r"DataStructure\.(?:\w+_descriptor_(?:annotations \(length\)|uri \(type\))"
        r"|(?:dimensions\[\]|measures\[\]|attributes\[\]|time_dimension)\.annotations \(length\))",
        _MAXIMAL,
        "pysdmx-drops",
        "pysdmx's DSD has no component lists, so nothing for their uri and annotations, and its Component "
        "has no annotations",
    ),
    Rule(
        r"DataStructure\.metadata_attributes\[\] \(only left\)|DataStructure\.metadata \(type\)",
        _MAXIMAL,
        "pysdmx-drops",
        "pysdmx's DSD has no metadata structure or metadata attribute usages, new in SDMX 3.0",
    ),
    Rule(
        r"DataConstraint\.(?:regions\[\]|data_key_sets\[\]\.keys\[\])\.(?:annotations|components) \(length\)",
        _MAXIMAL,
        "pysdmx-drops",
        "pysdmx's CubeRegion and DataKey have no annotations and restrict dimensions only, not attributes",
    ),
    Rule(
        r"DataConstraint\.regions\[\]\.keys\[\]\.(?:remove_prefix|time_range\.valid_(?:from|to) \(type\))",
        _MAXIMAL,
        "pysdmx-drops",
        "pysdmx's CubeKeyValue has no removePrefix, and its time range no validity",
    ),
    Rule(
        r"DataConstraint\.regions\[\]\.keys\[\]\.values",
        _MAXIMAL,
        "pysdmx-drops",
        "pysdmx's CubeValue has no cascadeValues, and its reader leaves a value's validity, which it has, empty",
    ),
    Rule(
        r"RepresentationMap\.mappings\[\]\.annotations \(length\)|StructureMap\.maps\[\]\.annotations \(length\)",
        _MAXIMAL,
        "pysdmx-drops",
        "pysdmx's ValueMap and ComponentMap have no annotations",
    ),
    Rule(
        r"Hierarchy\.tree\[\]\.(?:children\[\]\.)?uri \(type\)|CategoryScheme\.categories\[\]\.(?:categories\[\]\.)?uri"
        r" \(type\)",
        _MAXIMAL,
        "pysdmx-drops",
        "pysdmx's HierarchicalCode has no uri; its Category has one, and its reader leaves it empty",
    ),
    # pysdmx's model has the field, and its reader leaves it empty.
    Rule(
        r"(?:MetadataStructure|Hierarchy|HierarchyAssociation)\.uri \(type\)"
        r"|MetadataStructure\.attributes\[\]\.(?:children\[\]\.)?(?:uri \(type\)|annotations \(length\))"
        r"|MetadataStructure\.attribute_list_(?:uri \(type\)|annotations \(length\))"
        r"|Hierarchy\.levels\[\]\.(?:uri|coding_format) \(type\)",
        _MAXIMAL,
        "pysdmx-drops",
        "the reader leaves empty what pysdmx's model holds: an MSD's, a hierarchy's and an association's uri, "
        "a metadata attribute's uri and annotations, a level's uri and coding format. An MSD's attribute list "
        "has nowhere to go",
    ),
    Rule(
        r"DataConstraint\.data_key_sets\[\]\.keys\[\]\.valid_(?:from|to) \(type\)",
        _MAXIMAL,
        "pysdmx-drops",
        "pysdmx's DataKey has validity, and its reader leaves it empty",
    ),
    # pysdmx's reader fills it, and its writer leaves it out.
    Rule(
        r"\w+\.(?:\w+\[\]\.)*(?:service_url|structure_url) \(type\)",
        _MAXIMAL,
        "pysdmx-drops",
        "pysdmx reads a maintainable's serviceURL and structureURL, and its writer leaves them out",
    ),
    Rule(
        r"\w+\.\w+\[\]\.contacts\[\]\.id \(type\)",
        _MAXIMAL,
        "pysdmx-drops",
        "pysdmx reads a contact's id, and its writer leaves it out",
    ),
    Rule(
        r"DataStructure\.groups\[\]\.uri \(type\)",
        _MAXIMAL,
        "pysdmx-drops",
        "pysdmx reads a group's uri, and its writer leaves it out",
    ),
    Rule(
        r"RepresentationMap\.mappings\[\]\.valid_(?:from|to) \(type\)",
        _MAXIMAL,
        "pysdmx-drops",
        "pysdmx reads a mapping's validity, and its writer leaves it out",
    ),
    # pysdmx's copy means something else.
    Rule(
        r"DataStructure\.measures\[\]\.usage",
        _MAXIMAL,
        "pysdmx-changes",
        "pysdmx reads usage='mandatory' as required, and its writer leaves usage out of a Measure, where the "
        "3.1 default is optional",
    ),
    Rule(
        r"(?:DataStructure\.(?:measures|attributes)|MetadataStructure\.attributes(?:\[\]\.children)?"
        r"|ConceptScheme\.concepts)\[\]\.(?:min_occurs|max_occurs(?: \(type\))?)",
        _BOTH,
        "pysdmx-changes",
        "pysdmx leaves out a representation's minOccurs and maxOccurs, so the bounds become the defaults (1, 1): "
        "an optional value becomes required, a repeatable one single",
    ),
    Rule(
        r"DataStructure\.attributes\[\]\.(?:attachment|related_dimensions \(type\)|related_measures \(type\))",
        _MAXIMAL,
        "pysdmx-changes",
        "pysdmx attaches an attribute related to a dimension and a measure to the observation, and relates an "
        "observation-level attribute to the measure as well",
    ),
    Rule(
        r"DataStructure\.attributes\[\]\.representation \(type\)",
        _MAXIMAL,
        "pysdmx-changes",
        "pysdmx writes an empty TextFormat for an attribute with no local representation, which no longer "
        "takes its concept's",
    ),
    Rule(
        r"DataConstraint\.regions\[\]\.keys\[\]\.include",
        _MAXIMAL,
        "pysdmx-changes",
        "pysdmx's CubeKeyValue has no include, so a key value a region excludes comes back included",
    ),
    Rule(
        _FACET + r"\.is_multilingual",
        _MAXIMAL,
        "pysdmx-changes",
        "pysdmx's reader takes isMultiLingual='true' as False, and its writer leaves out a false flag",
    ),
    Rule(
        r"DataStructure\.attributes\[\]\.usage",
        _BOTH,
        "pysdmx-changes",
        "pysdmx reads an attribute with no usage as mandatory; 3.1's AttributeType defaults it optional, which "
        "sdmxlib leaves out (#503)",
    ),
    Rule(
        r"(?:MetadataStructure\.attributes\[\]\.representation|ConceptScheme\.concepts\[\]\.core_representation)"
        r" \(type\)",
        _BOTH,
        "pysdmx-drops",
        "pysdmx drops a TextFormat that states nothing beyond its defaults, which sdmxlib leaves out (#503)",
    ),
    Rule(
        _FACET + r"\.end_time|DataConstraint\.regions\[\]\.keys\[\]\.valid_to",
        _MAXIMAL,
        "pysdmx-changes",
        "pysdmx reads a date as a datetime at midnight, so an end bound of a day becomes that day's first "
        "instant and excludes the rest of it",
    ),
    Rule(
        r"StructureMap\.fixed_value_maps\[\]\.values \(length\)",
        _MAXIMAL,
        "pysdmx-changes",
        "pysdmx's reader keeps a FixedValueMap's several values as a list, and its writer writes the list's "
        "Python repr as one value",
    ),
    Rule(
        r"RepresentationMap\.mappings\[\]\.sources\[\]\.(?:value|is_regex|start_index \(type\)|end_index \(type\))",
        _SHAPE,
        "pysdmx-changes",
        "pysdmx's reader keeps a SourceValue with isRegEx or startIndex/endIndex as the raw parsed dict, and its "
        "writer writes the dict's Python repr as a literal value",
    ),
    Rule(
        r"RepresentationMap\.(?:sources|targets) \(length\)",
        _SHAPE,
        "pysdmx-changes",
        "pysdmx's RepresentationMap has one source and one target, so of a codelist and a data type on one "
        "side it keeps the data type",
    ),
    # The same value, spelt differently.
    Rule(
        _FACET + r"\.start_time|DataConstraint\.regions\[\]\.keys\[\]\.valid_from",
        _MAXIMAL,
        "pysdmx-normalises",
        "pysdmx reads a date as a datetime at midnight, which is the day's start: the same start bound",
    ),
)
