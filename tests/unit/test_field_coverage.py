"""Every field of every artefact, through every serializer — #354.

`test_roundtrip_property_guard` has run since #159 and did not catch #345, #346
or #354. Not because it is wrong, but because of its *shape*: it compares
hand-written `Check`s over six of the twenty artefact slots, so a field is
guarded only if somebody remembered to write a lambda for it, and ten artefact
kinds had nobody.

This inverts the default. `tests/support/maximal.py` populates every field of
every artefact, `tests/support/field_census.py` walks the whole graph after a
round-trip, and the thing maintained by hand is the list of **known losses**
below. A field is covered by existing.

The list is a **debt register, not a baseline to live with.** Each entry is a
field a caller can set and not get back. Most are bugs; a few are genuine format
limits and belong in `formats/gaps.py` instead, which is where they move as they
are triaged. The register is checked for equality in both directions:

* a **new** path fails — the regression this file exists to stop;
* a **missing** path fails too, because a register nobody prunes stops being
  read. Fixing a loss means deleting its line, which is a visible diff.

Two things this cannot see, both worth knowing:

* A field **both** the writer and reader mishandle *symmetrically* — write
  garbage, read the same garbage back — round-trips clean. XSD validation
  covers part of that gap for the XML formats and `tests/unit/test_ml3_writer_parity.py`
  covers another part.
* A field the fixture does not populate. `test_the_fixture_populates_every_field`
  is what keeps that at zero.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sized
from pathlib import Path
from typing import Any

import attrs
import pytest

from sdmxlib.formats.gaps import unrepresentable, without_dropped
from sdmxlib.formats.sdmx_json import reader as json_reader
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_ml21 import reader as ml21_reader
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import reader as ml30_reader
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import reader as ml31_reader
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import _UNWRITABLE
from tests.support import maximal, shapes
from tests.support.field_census import census, differences
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema
from tests.support.xsd import SchemaVersion, assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

FORMATS = {
    "2.1": (ml21_writer, ml21_reader),
    "3.0": (ml30_writer, ml30_reader),
    "3.1": (ml31_writer, ml31_reader),
    "json": (json_writer, json_reader),
}

# ── The register ─────────────────────────────────────────────────────────────
#
# Grouped by root cause rather than alphabetically, because the groups are what
# get fixed. A group's comment says which of the three it is: a **bug** (the
# format can carry it and we do not), a **limit** (the format cannot, and it
# belongs in `gaps.py` once confirmed against the XSD), or **not a loss** (the
# two sides legitimately differ).

#: `legacy_is_final`, 2.1's `isFinal` where it disagrees with the version
#: (#420). `is_final` itself is derived from the version and is no field, so it
#: is carried wherever the version is. 2.1 carries the flag for every class
#: that has one; the 3.0-only classes and the organisation schemes have none.
#:
#: **Limit**, declared in `gaps.py`: SDMX 3.0 removed `isFinal` from every
#: maintainable, and SDMX-JSON encodes the same model (#358). This is the
#: complete list of 2.1-final classes the fixture carries to those formats.
_LEGACY_IS_FINAL_30 = {
    "categorisations.legacy_is_final",
    "category_schemes.legacy_is_final",
    "codelists.legacy_is_final",
    "concept_schemes.legacy_is_final",
    "constraints.legacy_is_final",
    "data_structures.legacy_is_final",
    "dataflows.legacy_is_final",
    "metadata_structures.legacy_is_final",
    "metadataflows.legacy_is_final",
    "provision_agreements.legacy_is_final",
}
_LEGACY_IS_FINAL: dict[str, set[str]] = {
    "2.1": set(),
    "3.0": _LEGACY_IS_FINAL_30,
    "3.1": _LEGACY_IS_FINAL_30,
    "json": _LEGACY_IS_FINAL_30,
}

#: Was the 3.0/3.1 validity-date group, and is **empty because it is fixed**.
#:
#: `validFrom` / `validTo` survived on a Codelist and vanished on eight other
#: maintainables per version. `_write_maintainable_attrs` had always emitted
#: them; those eight writers set `id` / `agencyID` / `version` by hand instead
#: of calling it — the organisation schemes because their version is
#: schema-fixed, the rest for no recorded reason — and so never reached the
#: line that writes validity. `_write_validity` now runs in all of them (#355).
#:
#: Kept as a named empty set rather than deleted, so the group still reads as
#: something that was checked.
_VALIDITY_DATES: set[str] = set()
#: **Bug**, every format. Fields no writer emits.
#:
#: This group was twice this size and half of it was the fixture's fault.
#: `related_measure` (since removed, #360), `related_group` and the three
#: non-dataflow constraint
#: anchors were registered as losses because the fixture set every member of a
#: **choice** at once — SDMX types `AttributeRelationship` and
#: `ConstraintAttachment` as choices, so an attribute related to dimensions
#: *and* a measure *and* a group is not expressible, and a writer emitting one
#: of them is correct. Real BIS data round-trips all five, which is how the
#: false entries were caught (#359).
#:
#: `Group.uri` was here too and is fixed: every writer emitted it and no reader
#: read it back (#355). So was `Agency.parent`, a declared limit — `AgencyType`
#: restricts `<Parent>` away in every format — until #419 took it out of the
#: model. Kept as a named empty set, per `_VALIDITY_DATES` above.
_COMPONENT_FIELDS: set[str] = set()

#: **Limit**, declared in `gaps.py`: `DimensionConstraint` is new in SDMX 3.1.
#: 2.1, 3.0 and SDMX-JSON 2.0 have no equivalent element. 3.1 carries it since
#: #355 — before that the field was unwritable everywhere.
_DIMENSION_CONSTRAINT = {"dataflows.dimension_constraint"}

#: All four `ConstraintAttachment` anchors round-trip. They were registered as
#: losses on a fixture that set all four on one constraint; the fixture now uses
#: one constraint per anchor and nothing is lost. Kept as an empty set so the
#: group's name still documents what was checked (#359).
_CONSTRAINTS: set[str] = set()

#: An `enumerationFormat`'s `decimals` and `is_multilingual` were here: it
#: constrains the *codes* of an enumerated representation, codes are strings,
#: and no format types the slot with either. #364 stopped the writers emitting
#: them; #419 has `Registry.add` and `serialize` refuse them, so the maximal
#: fixture no longer sets them. Kept as a named empty set, per
#: `_VALIDITY_DATES` above.
_ENUMERATION_FORMAT: set[str] = set()

#: **Resolved by removing the field** (#360), so there is nothing left to lose.
#:
#: This group recorded a real limit — SDMX 3.0's `AttributeRelationship` has no
#: measure reference at all; `<Observation/>` is `common:EmptyType` and the
#: choice offers only Dataflow, Dimension, Group and Observation — but it could
#: never be *measured*, and the note here said so: the reader answered
#: `related_measure="OBS_VALUE"` whether or not the wire said anything, and
#: #364 had renamed the fixture's measure to `OBS_VALUE` because 2.1 pins
#: `PrimaryMeasure/@id` to exactly that. The invented value matched the real
#: one, so the round-trip looked clean.
#:
#: Chasing that gave the answer: 2.1 pins the id *and* permits one measure, so
#: the field was constant where it was real and fabricated everywhere else.
#: `Attribute` no longer carries it, and `attachment` says the whole of what
#: any format encodes. Kept as a named empty set, per `_VALIDITY_DATES` above,
#: so the group still reads as something that was checked.
_RELATED_MEASURE: set[str] = set()

#: A `HierarchicalCode`'s own name and description were here: no format has
#: an element for them — it is identifiable, not nameable, and shows its code's
#: name — and #419 took them out of the model. Kept as a named empty set, per
#: `_VALIDITY_DATES` above.
_HIERARCHICAL_CODE: set[str] = set()

#: A `Level.coding_format`'s `decimals`, `is_multilingual` and time facets
#: were here: `CodingTextFormatType` has none of them in any format, and #419
#: refuses them at `Registry.add` and `serialize`. Kept as a named empty set,
#: per `_VALIDITY_DATES` above.
_CODING_FORMAT: set[str] = set()

#: Was 2.1's coding-format bounds, and is **empty because it is fixed**.
#: `minValue` / `maxValue` are `use="optional"` on `CodingTextFormatType` in
#: every version; #354 taught the 2.1 writer to emit them and #364 the 3.x
#: writers, and #355 finally taught the 2.1 *reader* to ask for them.
_CODING_BOUNDS_21: set[str] = set()

#: **Bug.** The MSD's 2.1-era target and report-structure lists, and two
#: `MetadataAttribute` fields.
_METADATA_STRUCTURE = {
    "metadata_structures.report_structures",
    "metadata_structures.targets",
}

#: **Limit**, declared in `gaps.py`: SDMX 3.0 replaced the measure dimension
#: with an explicit `MeasureList`, so 3.0, 3.1 and SDMX-JSON 2.0 (which mirrors
#: 3.0) have nowhere to put it. Only 2.1 round-trips it.
_MEASURE_DIMENSION = {"data_structures.measure_dimension"}

#: **Not a loss.** `_ComponentMixin.__attrs_post_init__` inherits the concept's
#: `core_representation` when a component declares none — SDMX's rule, and
#: correct. It shows up here because the fixture's concept `Ref` is unresolved
#: while the reader's is resolved against the message index, so the inheritance
#: fires on one side only. The wire carried exactly what it was given.
_CONCEPT_INHERITANCE = {"metadata_structures.attributes[0].children[0].representation"}

#: **Limit**, declared in `gaps.py`: SDMX-ML 2.1 has no `AnnotationValue`
#: element. Its `AnnotationType` has four children — Title, Type, URL, Text —
#: and 3.0 added a fifth for the *non-localised* body, which is the right home
#: for a machine-read payload where a language map is meaningless (#368).
#:
#: Twenty-odd paths for one field, because annotations hang off nearly every
#: artefact and item. That breadth is the point: before #368 the field did not
#: exist, so a 3.0 document carrying one round-tripped with it dropped and
#: *nothing here could see it* — the census can only diff fields the model has.
_ANNOTATION_VALUE = {
    "agency_schemes.agencies[0].annotations[0].value",
    "agency_schemes.annotations[0].value",
    "categorisations.annotations[0].value",
    "category_schemes.annotations[0].value",
    "category_schemes.categories[0].annotations[0].value",
    "category_schemes.categories[0].categories[0].annotations[0].value",
    "codelists.annotations[0].value",
    "codelists.codes[0].annotations[0].value",
    "codelists.codes[1].annotations[0].value",
    "concept_scheme_maps.annotations[0].value",
    "organisation_scheme_maps.annotations[0].value",
    "concept_schemes.annotations[0].value",
    "concept_schemes.concepts[0].annotations[0].value",
    "concept_schemes.concepts[1].annotations[0].value",
    "constraints.annotations[0].value",
    "data_provider_schemes.annotations[0].value",
    "data_provider_schemes.providers[0].annotations[0].value",
    "metadata_provider_schemes.annotations[0].value",
    "metadata_provider_schemes.providers[0].annotations[0].value",
    "data_consumer_schemes.annotations[0].value",
    "data_consumer_schemes.consumers[0].annotations[0].value",
    "organisation_unit_schemes.annotations[0].value",
    "organisation_unit_schemes.organisation_units[0].annotations[0].value",
    "metadata_provision_agreements.annotations[0].value",
    "data_structures.annotations[0].value",
    "data_structures.attributes[0].annotations[0].value",
    "data_structures.attributes[1].annotations[0].value",
    "data_structures.attributes[2].annotations[0].value",
    "data_structures.dimensions[0].annotations[0].value",
    "data_structures.measure_dimension.annotations[0].value",
    "data_structures.measures[0].annotations[0].value",
    "data_structures.time_dimension.annotations[0].value",
    "dataflows.annotations[0].value",
    "hierarchies.annotations[0].value",
    "hierarchies.levels[0].annotations[0].value",
    "hierarchies.tree[0].annotations[0].value",
    "hierarchies.tree[0].children[0].annotations[0].value",
    "hierarchies.tree[0].level.annotations[0].value",
    "hierarchies.levels[1].annotations[0].value",
    "hierarchies.tree[0].children[0].level.annotations[0].value",
    "hierarchical_codelists.annotations[0].value",
    "metadata_structures.annotations[0].value",
    "metadata_structures.attributes[0].annotations[0].value",
    "metadata_structures.attributes[0].children[0].annotations[0].value",
    "metadata_structures.report_structures[0].annotations[0].value",
    "metadata_structures.targets[0].annotations[0].value",
    "metadataflows.annotations[0].value",
    "provision_agreements.annotations[0].value",
    "representation_maps.annotations[0].value",
    "structure_maps.annotations[0].value",
    "structure_sets.annotations[0].value",
    "data_structures.groups[0].annotations[0].value",
    "data_structures.dimension_descriptor_annotations[0].value",
    "data_structures.attribute_descriptor_annotations[0].value",
    "data_structures.measure_descriptor_annotations[0].value",
    "metadata_structures.targets[0].target_objects[0].annotations[0].value",
    "concept_scheme_maps.maps[0].annotations[0].value",
    "organisation_scheme_maps.maps[0].annotations[0].value",
    "representation_maps.mappings[0].annotations[0].value",
    "structure_maps.maps[0].annotations[0].value",
}

#: **Limit**, declared in `gaps.py`: SDMX-ML 2.1 has one `AnnotationURL` and no
#: `xml:lang` on it. The fixture gives every annotation a second, localised URL
#: (#422), so the same sites lose it.
_ANNOTATION_URLS = {path.removesuffix(".value") + ".urls" for path in _ANNOTATION_VALUE}

#: **Limit**, declared in `gaps.py`: SDMX 3.0's additions to a DSD's
#: components, which 2.1 has no element or attribute for (#422).
_DSD_3X = frozenset(
    {
        "data_structures.attributes[0].related_measures",
        "data_structures.attributes[0].optional_dimensions",
        "data_structures.attributes[0].min_occurs",
        "data_structures.attributes[0].max_occurs",
        "data_structures.measures[0].max_occurs",
        "data_structures.metadata",
        "data_structures.metadata_attributes",
    }
)

#: **Limit**, declared in `gaps.py`: a region value set's and value's 3.x
#: qualifiers, which 2.1's `ComponentValueSetType` and `SimpleValueType`
#: have no attribute for (#422).
_REGION_3X = frozenset(
    {
        "constraints.regions[0].keys[0].remove_prefix",
        "constraints.regions[0].keys[0].valid_from",
        "constraints.regions[0].keys[0].valid_to",
        "constraints.regions[0].keys[0].values",
        "constraints.regions[0].components[0].remove_prefix",
        "constraints.regions[0].components[0].values",
        "constraints.regions[0].keys[1].time_range.valid_from",
        "constraints.regions[0].keys[1].time_range.valid_to",
    }
)

#: **Limit**, declared in `gaps.py`: SDMX-ML 2.1 has no geographic codelist,
#: so a `GeographicCodelist` and a `GeoGridCodelist` are written as the plain
#: codelists they are, and each code's feature set or grid cell — and the
#: grid's definition — has nowhere to go (#422).
_GEO_CODELIST_21 = frozenset(
    {
        "codelists.codes[0].value",
        "codelists.codes[1].value",
        "codelists.codes[0].geo_cell",
        "codelists.codes[1].geo_cell",
        "codelists.grid_definition",
    }
)

#: **Limit**, declared in `gaps.py`: SDMX-ML 2.1's `TextFormatType` has no
#: `SentinelValue` (#422).
_SENTINELS_21 = frozenset(
    {
        "concept_schemes.concepts[1].core_representation.sentinel_values",
        "data_structures.time_dimension.representation.sentinel_values",
    }
)

#: **Limit**, declared in `gaps.py`: SDMX-JSON gives a maintainable
#: `isExternalReference` and no URL to go with it (#422). Every maintainable
#: in the fixture that JSON writes, so read off the message, not listed.
_EXTERNAL_URLS_JSON = frozenset(
    f"{field.name}.{url}"
    for field in attrs.fields(StructureMessage)
    if not field.name.startswith("_")
    and field.name not in {"hierarchical_codelists", "structure_sets"}
    and getattr(maximal.message(), field.name)
    for url in ("service_url", "structure_url")
)


#: **Limit**, declared in `gaps.py`: SDMX-ML 3.1's `isPartialLanguage` and
#: `evolvingStructure` (#422), which 2.1 and 3.0 have no attribute for. Every
#: maintainable carries the first, so the paths are read off the message —
#: less the artefacts a version does not write at all.
def _sdmx_31_only(*unwritten: str) -> frozenset[str]:
    return frozenset(
        {
            f"{field.name}.is_partial_language"
            for field in attrs.fields(StructureMessage)
            if not field.name.startswith("_") and field.name not in unwritten and getattr(maximal.message(), field.name)
        }
        | {"data_structures.evolving_structure"}
    )


#: **Limit**, declared in `gaps.py`: SDMX-ML 2.1's alone, which 3.x and
#: SDMX-JSON removed (#422).
_2_1_ONLY = frozenset(
    {
        "structure_maps.is_extension",
        "hierarchies.tree[0].version",
        # A component map's ToTextFormat and ToValueType (#422).
        "structure_maps.maps[2].to_text_format",
        "structure_maps.maps[2].to_value_type",
    }
)

#: **Limit**, declared in `gaps.py`: what SDMX 3.x adds that 2.1's types do
#: not declare (#422).
_3X_ONLY_21 = frozenset(
    {
        "concept_schemes.concepts[3].min_occurs",
        "concept_schemes.concepts[3].max_occurs",
        "data_structures.measures[0].usage",
        "constraints.data_key_sets[0].keys[0].valid_from",
        "constraints.data_key_sets[0].keys[0].valid_to",
        "constraints.data_key_sets[0].keys[0].components",
        "concept_scheme_maps.maps[0].valid_from",
        "organisation_scheme_maps.maps[0].valid_from",
        "concept_scheme_maps.maps[0].valid_to",
        "organisation_scheme_maps.maps[0].valid_to",
        "metadata_structures.attribute_list_uri",
        "structure_maps.epoch_maps",
        "structure_maps.date_pattern_maps",
        "structure_maps.frequency_format_mappings",
        "structure_maps.fixed_value_maps",
    }
)

#: **Limit**, declared in `gaps.py`: 2.1's `IdentifiableType` has no
#: `<com:Link>`, so every link the fixture sets is dropped (#422).
_LINKS_21 = frozenset(
    {
        "agency_schemes.agencies[0].links",
        "agency_schemes.links",
        "categorisations.links",
        "category_schemes.categories[0].categories[0].links",
        "category_schemes.categories[0].links",
        "category_schemes.links",
        "codelists.codes[0].links",
        "codelists.codes[1].links",
        "codelists.links",
        "concept_scheme_maps.links",
        "organisation_scheme_maps.links",
        "concept_schemes.concepts[0].links",
        "concept_schemes.concepts[1].links",
        "concept_schemes.links",
        "constraints.links",
        "data_provider_schemes.links",
        "data_provider_schemes.providers[0].links",
        "metadata_provider_schemes.links",
        "metadata_provider_schemes.providers[0].links",
        "data_consumer_schemes.links",
        "data_consumer_schemes.consumers[0].links",
        "organisation_unit_schemes.links",
        "organisation_unit_schemes.organisation_units[0].links",
        "metadata_provision_agreements.links",
        "data_structures.attribute_descriptor_links",
        "data_structures.attributes[0].links",
        "data_structures.attributes[1].links",
        "data_structures.attributes[2].links",
        "data_structures.dimension_descriptor_links",
        "data_structures.dimensions[0].links",
        "data_structures.links",
        "data_structures.measure_descriptor_links",
        "data_structures.measures[0].links",
        "data_structures.time_dimension.links",
        "dataflows.links",
        "hierarchies.levels[0].links",
        "hierarchies.levels[1].links",
        "hierarchies.links",
        "hierarchies.tree[0].children[0].level.links",
        "hierarchies.tree[0].children[0].links",
        "hierarchies.tree[0].level.links",
        "hierarchies.tree[0].links",
        "metadata_structures.attribute_list_links",
        "metadata_structures.attributes[0].children[0].links",
        "metadata_structures.attributes[0].links",
        "metadata_structures.links",
        "metadataflows.links",
        "provision_agreements.links",
        "representation_maps.links",
        "structure_maps.links",
    }
)

#: **Limit**, declared in `gaps.py`: an annotation URL's URN and type are
#: SDMX-JSON's, on the link that spells it; SDMX-ML's `AnnotationURLType` is a
#: URI and an `xml:lang` (#422). The fixture sets both on every annotation's
#: second URL. 2.1 is not listed: it loses that URL whole (`_ANNOTATION_URLS`).
_ANNOTATION_URL_DETAIL = frozenset(
    f"{annotations}[0].urls[1].{attribute}"
    for annotations in (
        "agency_schemes.annotations",
        "agency_schemes.agencies[0].annotations",
        "categorisations.annotations",
        "category_schemes.annotations",
        "category_schemes.categories[0].annotations",
        "category_schemes.categories[0].categories[0].annotations",
        "codelists.annotations",
        "codelists.codes[0].annotations",
        "codelists.codes[1].annotations",
        "concept_scheme_maps.annotations",
        "organisation_scheme_maps.annotations",
        "concept_scheme_maps.maps[0].annotations",
        "organisation_scheme_maps.maps[0].annotations",
        "concept_schemes.annotations",
        "concept_schemes.concepts[0].annotations",
        "concept_schemes.concepts[1].annotations",
        "constraints.annotations",
        "constraints.data_key_sets[0].keys[0].annotations",
        "constraints.regions[0].annotations",
        "data_provider_schemes.annotations",
        "data_provider_schemes.providers[0].annotations",
        "data_structures.annotations",
        "data_structures.attributes[0].annotations",
        "data_structures.attributes[1].annotations",
        "data_structures.attributes[2].annotations",
        "data_structures.dimensions[0].annotations",
        "data_structures.groups[0].annotations",
        "data_structures.measures[0].annotations",
        "data_structures.metadata_attributes[0].annotations",
        "data_structures.metadata_attributes[1].annotations",
        "data_structures.time_dimension.annotations",
        "dataflows.annotations",
        "hierarchies.annotations",
        "hierarchies.levels[0].annotations",
        "hierarchies.levels[1].annotations",
        "hierarchies.tree[0].annotations",
        "hierarchies.tree[0].children[0].annotations",
        "hierarchies.tree[0].children[0].level.annotations",
        "hierarchies.tree[0].level.annotations",
        "hierarchy_associations.annotations",
        "metadata_provider_schemes.annotations",
        "metadata_provider_schemes.providers[0].annotations",
        "data_consumer_schemes.annotations",
        "data_consumer_schemes.consumers[0].annotations",
        "organisation_unit_schemes.annotations",
        "organisation_unit_schemes.organisation_units[0].annotations",
        "metadata_provision_agreements.annotations",
        "metadata_structures.annotations",
        "metadata_structures.attributes[0].annotations",
        "metadata_structures.attributes[0].children[0].annotations",
        "metadataflows.annotations",
        "provision_agreements.annotations",
        "representation_maps.annotations",
        "representation_maps.mappings[0].annotations",
        "structure_maps.annotations",
        "structure_maps.date_pattern_maps[0].annotations",
        "structure_maps.epoch_maps[0].annotations",
        "structure_maps.fixed_value_maps[0].annotations",
        "structure_maps.frequency_format_mappings[0].annotations",
        "structure_maps.maps[0].annotations",
        "value_lists.annotations",
        "value_lists.items[0].annotations",
        "data_structures.attribute_descriptor_annotations",
        "data_structures.dimension_descriptor_annotations",
        "data_structures.measure_descriptor_annotations",
        "metadata_structures.attribute_list_annotations",
    )
    for attribute in ("urn", "type")
)

#: **Limit**, declared in `gaps.py`: a link's language is SDMX-JSON's
#: `hreflang`; SDMX-ML 3.x's `LinkType` has none (#422). The fixture sets it on
#: every first link — 2.1's sites, which lose the link whole, and the 3.x-only
#: artefacts and components besides.
_LINK_LANGUAGES = frozenset(
    f"{links}[0].hreflang"
    for links in _LINKS_21
    | {
        "data_structures.metadata_attributes[0].links",
        "hierarchy_associations.links",
        "structure_maps.date_pattern_maps[0].links",
        "structure_maps.epoch_maps[0].links",
        "structure_maps.frequency_format_mappings[0].links",
        "value_lists.links",
    }
)

KNOWN_LOSSES: dict[str, frozenset[str]] = {
    "2.1": frozenset(
        _DIMENSION_CONSTRAINT
        | _CODING_BOUNDS_21
        | _ENUMERATION_FORMAT
        | _LEGACY_IS_FINAL["2.1"]
        | _COMPONENT_FIELDS
        | _CONSTRAINTS
        | _HIERARCHICAL_CODE
        | _CODING_FORMAT
        | _CONCEPT_INHERITANCE
        | _ANNOTATION_VALUE
        | _ANNOTATION_URLS
        | _DSD_3X
        | _REGION_3X
        | _GEO_CODELIST_21
        | _SENTINELS_21
        | _sdmx_31_only("hierarchy_associations", "value_lists")
        | _3X_ONLY_21
        | _LINKS_21
        | {
            # **Limit.** 2.1 has no codelist extension, no Metadataflow target
            # and no measure concept role. Each needs an XSD check before it
            # moves to `gaps.py`.
            #
            # `hierarchy_associations` was here too, as a whole-artefact loss.
            # It is not a loss — `validation._UNWRITABLE` already declares 2.1
            # cannot carry it, and `_round_trip` now clears what a target
            # refuses before writing. Declared-unwritable and silently-lossy
            # are different facts with different guards, and the register is
            # only for the second.
            #
            # These two are **declared limits**, not defects: 2.1's
            # `CodelistType` admits only Code children, and its single
            # `PrimaryMeasureType` has no ConceptRole. Both are still observed
            # losses, so they belong here — `KNOWN_DEFECTS` is the list they
            # left.
            "codelists.extensions",
            # The extension's inherited codes are written inline (#421), so
            # the codes come back as more than the codelist's own — declared
            # in `gaps.py` beside `extensions`.
            "codelists.codes",
            "data_structures.measures[0].concept_role",
            "metadataflows.targets",
            # Written as a ProvisionAgreement, which has no targets (#421).
            "metadata_provision_agreements.targets",
            # The 2.1 annotation group was here and is **fixed** (#355). It
            # was three separate causes wearing one label, which is why
            # counting call sites had not found it:
            #
            #   * the four scheme-map writers never emitted `<Annotations>`,
            #     and the reader never looked, so the loss was symmetric and
            #     invisible to a round-trip;
            #   * `StructureMap` likewise;
            #   * a `Hierarchy`'s annotations were written on the synthesised
            #     `<HierarchicalCodelist>` wrapper while the reader rebuilds
            #     the hierarchy from the inner `<Hierarchy>`;
            #   * a `HierarchicalCode`'s *were* written all along and simply
            #     never read back.
            # **Limit**, declared in `gaps.py`: 2.1 `CodeMapType` allows only
            # Annotations/Source/Target, so a value map's validity is lost.
            "representation_maps.mappings[0].valid_from",
            "representation_maps.mappings[0].valid_to",
            # A hierarchy's and the maps' validity, and their external
            # reference, were here until the 2.1 reader gave each its
            # container's (#421).
            # **Limit**, declared in `gaps.py`: a 2.1 MSD has no
            # MetadataAttributeList to annotate (#422).
            "metadata_structures.attribute_list_annotations",
            # **Limit**, declared in `gaps.py`: 2.1's cube region and data
            # key are not annotable (#422).
            "constraints.regions[0].annotations",
            "constraints.data_key_sets[0].keys[0].annotations",
        }
    ),
    "3.0": frozenset(
        _2_1_ONLY
        | _sdmx_31_only("hierarchical_codelists", "structure_sets")
        | _DIMENSION_CONSTRAINT
        | _ENUMERATION_FORMAT
        | _RELATED_MEASURE
        | _MEASURE_DIMENSION
        | _LEGACY_IS_FINAL["3.0"]
        | _VALIDITY_DATES
        | _COMPONENT_FIELDS
        | _CONSTRAINTS
        | _HIERARCHICAL_CODE
        | _CODING_FORMAT
        | _METADATA_STRUCTURE
        | _CONCEPT_INHERITANCE
        | _ANNOTATION_URL_DETAIL
        | _LINK_LANGUAGES
        | {"constraints.reference_period"}
    ),
    "json": frozenset(
        _2_1_ONLY
        | _EXTERNAL_URLS_JSON
        | _DIMENSION_CONSTRAINT
        | _ENUMERATION_FORMAT
        | _CODING_FORMAT
        | _RELATED_MEASURE
        | _MEASURE_DIMENSION
        | _LEGACY_IS_FINAL["json"]
        | _COMPONENT_FIELDS
        | _CONSTRAINTS
        | _HIERARCHICAL_CODE
        | _METADATA_STRUCTURE
        | _CONCEPT_INHERITANCE
        | {
            "constraints.reference_period",
            "constraints.release_calendar",
            # `metadataflows.targets` was here: SDMX-JSON carries them and the
            # DTO had no field, so they were lost silently. Fixed in #421.
            # **Bug**, SDMX-JSON only: `uri` on a Category. A Level's was here
            # too and is fixed — `LevelJson` had carried a `links` field all
            # along and its builder never filled it, the one artefact whose
            # constructor forgot the call every other one makes (#355).
        }
    ),
}
#: 3.1 loses everything 3.0 does, plus `release_calendar` — a declared limit,
#: the element was removed from `DataConstraintType` (#345).
#: 3.1 is 3.0's losses plus `release_calendar` (removed from
#: `DataConstraintType`, #345) and **minus** `dimension_constraint`, which 3.1
#: is the only version to have an element for. Derived from 3.0 rather than
#: restated so the shared entries cannot drift, but the subtraction is the
#: reminder that 3.1 is not simply 3.0-with-more-gaps.
KNOWN_LOSSES["3.1"] = (KNOWN_LOSSES["3.0"] | {"constraints.release_calendar"}) - _DIMENSION_CONSTRAINT - _sdmx_31_only()


#: Wire version names as `model.validation` spells them, per census format key.
_WIRE = {"2.1": "2.1", "3.0": "3.0", "3.1": "3.1", "json": "json"}


def _round_trip(fmt: str) -> tuple[StructureMessage, StructureMessage]:
    """Round-trip the maximal message, minus what this target *refuses*.

    A writer that cannot carry an artefact raises rather than dropping it
    (#225), so those slots are cleared before writing — otherwise this census
    would be asserting the refusal instead of measuring field loss, and the two
    are different questions with different guards.

    The list comes from `validation._UNWRITABLE` rather than a copy, so a new
    refusal cannot make this file quietly stop testing something.
    """
    writer, reader = FORMATS[fmt]
    # What the target leaves out whole — a 2.1 write's value list and what
    # names it — is `dropped`'s to report, not a field loss (#424, #422).
    original = without_dropped(maximal.message(), _WIRE[fmt])
    refused = _UNWRITABLE.get(_WIRE[fmt], {})
    if refused:
        # The slot names are only known at runtime, so the keyword expansion
        # cannot be checked statically — the pattern `StructureMessage.containing`
        # uses, confined to one annotated local.
        cleared: dict[str, Any] = dict.fromkeys(refused, ())
        original = attrs.evolve(original, **cleared)
    return original, reader.read(writer.write(original))


def _observed(fmt: str) -> tuple[set[str], list[str]]:
    """Changed field paths, and the same with values for a failure message."""
    original, back = _round_trip(fmt)
    paths: set[str] = set()
    detail: list[str] = []
    for slot in (a.name for a in StructureMessage.__attrs_attrs__ if not a.name.startswith("_")):  # pyrefly: ignore[missing-attribute]
        written = list(getattr(original, slot) or [])
        read_back = list(getattr(back, slot) or [])
        if not written:
            continue
        if len(written) != len(read_back):
            paths.add(f"{slot}  <<{len(written)} written, {len(read_back)} read back>>")
            continue
        for before, after in zip(written, read_back, strict=True):
            paths.update(census(before, after, slot))
            detail.extend(f"{slot}{d}" for d in differences(before, after))
    return paths, detail


@pytest.mark.parametrize("fmt", sorted(FORMATS))
def test_round_trip_loses_only_what_is_registered(fmt: str) -> None:
    """Every field survives, or is a line in `KNOWN_LOSSES` above."""
    observed, detail = _observed(fmt)
    expected = KNOWN_LOSSES[fmt]
    lines = {d.split(":")[0]: d for d in detail}
    new = sorted(observed - expected)
    fixed = sorted(expected - observed)
    assert not new, (
        f"{fmt} now loses fields that are not registered:\n"
        + "\n".join(f"    {lines.get(p, p)}" for p in new)
        + "\n\nEither the writer/reader dropped something it used to carry, or this is "
        "a real format limit — in which case add it to `formats/gaps.py` and to the "
        "register in this file, with the XSD reason."
    )
    assert not fixed, (
        f"{fmt} no longer loses these, so the register is stale:\n"
        + "\n".join(f"    {p}" for p in fixed)
        + "\n\nDelete them from KNOWN_LOSSES. A register nobody prunes stops being read."
    )


def _is_set(value: object) -> bool:
    """Whether a field carries something a serializer would have to write."""
    return value is not None and not (isinstance(value, Sized) and len(value) == 0)


def _walk_populated_fields(
    message: StructureMessage, skip_fields: set[str]
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Per class, which fields the fixture sets and which it declares."""
    import attrs

    from sdmxlib.model.ref import Ref

    populated: dict[str, set[str]] = {}
    declared: dict[str, set[str]] = {}
    seen: set[int] = set()

    def visit(obj: object) -> None:
        # A `Ref` is a leaf: it is one URN, and touching any other attribute
        # proxies to the artefact it points at, which raises when unresolved.
        cls = type(obj)
        if id(obj) in seen or isinstance(obj, Ref) or not attrs.has(cls):
            return
        seen.add(id(obj))
        name = cls.__name__
        fields = {f.name for f in attrs.fields(cls) if not f.name.startswith("_")} - skip_fields
        declared.setdefault(name, set()).update(fields)
        for field_name in fields:
            value: object = getattr(obj, field_name, None)
            if not isinstance(value, Ref) and not _is_set(value):
                continue
            populated.setdefault(name, set()).add(field_name)
            visit(value)
            # `Annotations` is a `Sequence` but not a list/tuple, and was not in
            # this list — so no `Annotation` field was ever checked for
            # population, and the fixture could leave one unset with nothing
            # noticing. That is exactly what happened to `value` when #368 added
            # it: the field the guard exists to protect was the field it could
            # not see.
            if isinstance(value, (list, tuple)) or type(value).__name__ in ("ItemList", "ArtefactList", "Annotations"):
                assert isinstance(value, Iterable)
                for item in value:
                    visit(item)

    for slot in (a.name for a in StructureMessage.__attrs_attrs__ if not a.name.startswith("_")):  # pyrefly: ignore[missing-attribute]
        for artefact in getattr(message, slot) or []:
            visit(artefact)
    return populated, declared


def test_the_census_can_tell_every_kind_of_value_apart() -> None:
    """A comparison needs a discrimination check — this one went without for a year.

    `InternationalString` is not a `dict`, so the census walked it as a
    sequence of its *keys*: every name, description and annotation text in the
    model compared equal to any other in the same languages. A 2.1 reader
    taking a description from `<com:Name>` passed this file; mutation found it
    (#458). Each pair here differs in one way and must be reported.
    """
    pairs = [
        (InternationalString(en="name"), InternationalString(en="description")),
        (InternationalString(en="a", fr="b"), InternationalString(en="a")),
        ({"en": "a"}, {"en": "b"}),
        (frozenset({"A"}), frozenset({"B"})),
        (("a", "b"), ("a", "c")),
        (("a",), ("a", "b")),
        ("1.0", "2.0"),
        (True, False),
    ]
    for before, after in pairs:
        assert list(differences(before, after)), f"{before!r} and {after!r} compared equal"


def test_the_fixture_populates_every_field() -> None:
    """The register only means something over fields the fixture actually sets.

    A field left unset makes every check over it vacuous — the writer may drop
    it and nothing notices, because there was nothing to lose. That is the
    failure mode this whole module exists to remove, so it is checked rather
    than intended.

    Coverage is per **class**, not per instance: a leaf `HierarchicalCode` has
    no children and a root `Code` has no parent, so demanding every field on
    every object would be demanding an infinite fixture. What it demands is
    that *some* instance of each class exercises each field — which is what
    makes the round-trip comparison non-vacuous for that field.
    """
    #: Not payload. `maintainer` is an identity — an `Agency` built from the id
    #: string, whose name and contacts live in the `AgencyScheme` this fixture
    #: also carries — so descending into it would demand a fully-described
    #: agency on all seventeen artefacts while testing nothing.
    skip_fields = {"maintainer"}
    #: Fields only a shape some format *refuses* can set: a substring source
    #: value, which SDMX-ML 2.1 refuses (#242); a constraint's data sources,
    #: which SDMX-ML 3.1 and SDMX-JSON have none of; and its data sets, an
    #: attribute's attachment groups and a group's attachment constraint,
    #: which only 2.1 has (#422). The maximal fixture is written in every format, so
    #: it cannot hold them; the shapes message beside each does, and the
    #: round trips over it are where they are checked. Asserted below, so an
    #: exemption cannot outlive the coverage it points at.
    outside_maximal: list[tuple[str, set[str], Callable[[], StructureMessage]]] = [
        ("SourceValue", {"start_index", "end_index"}, shapes.message),
        ("ConstraintAttachment", {"simple_data_sources", "queryable_data_sources"}, shapes.message),
        (
            "QueryableDataSource",
            {"data_url", "is_rest_datasource", "is_web_service_datasource", "wsdl_url", "wadl_url"},
            shapes.message,
        ),
        ("ConstraintAttachment", {"data_sets"}, shapes.message_21),
        ("DataSetReference", {"data_provider", "id"}, shapes.message_21),
        ("Attribute", {"attachment_groups"}, shapes.message_21),
        ("Group", {"attachment_constraint"}, shapes.message_21),
    ]
    exempt: dict[str, set[str]] = {}
    for cls, fields, _ in outside_maximal:
        exempt.setdefault(cls, set()).update(fields)

    populated, declared = _walk_populated_fields(maximal.message(), skip_fields)
    missing = sorted(
        f"{cls}.{field}"
        for cls, fields in declared.items()
        for field in fields - populated.get(cls, set()) - exempt.get(cls, set())
    )
    assert not missing, (
        "no instance in the maximal fixture sets these, so every round-trip check "
        "over them is vacuous:\n" + "\n".join(f"    {p}" for p in missing)
    )
    for cls, fields, message in outside_maximal:
        in_shapes, _ = _walk_populated_fields(message(), skip_fields)
        assert fields <= in_shapes.get(cls, set()), f"shapes.{message.__name__}() no longer sets {cls}.{sorted(fields)}"


# ── conformance: is what we wrote a document anyone else can read? ─────────

#: Which schema holds each target's output.
#:
#: SDMX-JSON is held to **both** 2.0 and 2.1. 2.0 is what `formats.sdmx_json`
#: emits; 2.1 is the stricter reading of the same document, since it closes
#: every object with `unevaluatedProperties: false` where 2.0 leaves them open.
#: Passing both means the bytes are consumable by a 2.1 client without a second
#: writer — and it is what keeps that true, since the one thing that broke it
#: (a Level's `codingFormat` carrying `decimals` / `isMultiLingual`) validated
#: perfectly well under 2.0 alone.
#:
#: Checking only the vendored schema, rather than the emitted version, is how
#: #358 was nearly decided from the wrong file.
#: XML wire format -> the schema set that validates it. The `"json"` entry the
#: single map used to carry was a *JSON*-schema version, unreachable from the
#: branch below and untypable alongside these — `_JSON_SCHEMAS` is where the
#: JSON side already lives, so it was two maps wearing one name.
_SCHEMA_OF: dict[str, SchemaVersion] = {"2.1": "2.1", "3.0": "3.0", "3.1": "3.1"}
_JSON_SCHEMAS = ("2.0", "2.1")


@pytest.mark.parametrize("fmt", sorted(FORMATS))
def test_what_the_census_writes_is_schema_valid(fmt: str) -> None:
    """The census compares a round-trip; this asks whether the middle is legal.

    A round-trip is blind to output that is wrong in a way our own reader
    forgives, which is the whole class #345, #356 and #357 belong to. The
    maximal fixture drives it because it is the one input that reaches every
    field — and when this check was first pointed at it, that output carried
    **23 XSD errors in 3.0 and 3.1 and 7 in SDMX-JSON**, none of which anything
    in the suite could see (#364).

    Four of those were real: `enumerationFormat` carrying `decimals` and
    `isMultiLingual`, which every target prohibits, and a Level's
    `startValue` / `endValue` emitted through `str()` where the schema types
    them `xs:positiveInteger` — the 2.1 bug from #354 recurring verbatim in the
    3.0 and 3.1 writers. The rest were the fixture being maximal beyond the
    grammar again.
    """
    written = FORMATS[fmt][0].write(maximal.message())
    if fmt == "json":
        for version in _JSON_SCHEMAS:
            assert_valid_sdmx_json(written, compile_json_schema("structure", version))
    else:
        assert_valid_sdmx_ml(written, compile_structure_schema(_SCHEMA_OF[fmt]))


def test_the_official_2_1_sample_parses() -> None:
    """We can *read* SDMX-JSON 2.1, not only write bytes it accepts.

    The write half is held by `_JSON_SCHEMAS` above. This is the other
    direction, against the sample the SDMX-TWG ships with the 2.1 schema —
    a document nobody here authored, so it carries whatever the standard's own
    authors thought a structure message looks like.

    It parses today with no 2.1-specific code, which is the useful finding:
    the reader is keyed on section names and those did not change. What did
    change is that 2.1 *closes* every object, so the burden fell on the writer
    (#364) rather than here.
    """
    sample = Path(__file__).resolve().parents[1] / "schemas" / "sdmx_json" / "2.1" / "samples"
    raw = (sample / "structure-constructed-sample.json").read_bytes()

    message = json_reader.read(raw)

    filled = {
        a.name
        for a in StructureMessage.__attrs_attrs__  # pyrefly: ignore[missing-attribute]
        if not a.name.startswith("_") and getattr(message, a.name)
    }
    assert filled == {
        "agency_schemes",
        "categorisations",
        "category_schemes",
        "codelists",
        "concept_schemes",
        "constraints",
        "data_structures",
        "dataflows",
    }, "the sample's sections, so a reader that quietly stopped filling one fails here"
    # What the model has no field for, reported rather than dropped (#422):
    # every key no struct declares — found by walking the raw JSON beside the
    # structs, so a key nobody listed is reported too — and a link's language.
    # Its dataflow's `rel="structure"` navigation link is a `Link` now. Exact,
    # by construct: modelling one takes its line out.
    assert sorted({gap.construct for gap in message.lost}) == [
        "AgencySchemeJson.x-extension",
        "AttributeJson.x-extension",
        "AttributeListJson.id",
        "CategorisationJson.x-extension",
        "CategoryJson.x-extension",
        "CategorySchemeJson.x-extension",
        "CodeJson.x-extension",
        "CodelistJson.x-extension",
        "ConceptJson.x-extension",
        "ConceptSchemeJson.x-extension",
        "ConstraintAttachmentJson.x-extension",
        "CubeRegionJson.x-extension",
        "DataConstraintJson.x-extension",
        "DataStructureJson.x-extension",
        "DataflowJson.x-extension",
        "DimensionJson.x-extension",
        "DimensionListJson.id",
        "GroupJson.x-extension",
        "KeyValueJson.x-extension",
        "MeasureJson.x-extension",
        "MeasureListJson.id",
        "MeasureListJson.x-extension",
        "OrganisationJson.x-extension",
        "Representation.x-extension",
        "TimeDimensionJson.x-extension",
    ]
    assert message.refused == ()


# ── declared limit, or defect? ────────────────────────────────────────────

#: Losses that are **not** declared in `gaps.py` — the real remainder of #355.
#:
#: `KNOWN_LOSSES` above mixes two different facts: a field the format cannot
#: carry, and a field it can carry that we drop. That made the register's size
#: useless as progress — it *grew* for SDMX-JSON during the v0.50.0 release
#: work, as invalid output was converted into declared limits. This set is the
#: half that can actually shrink.
#:
#: Derived by subtraction rather than listed twice: whatever
#: `unrepresentable()` claims for an artefact is a limit, and anything left
#: over is here. So a new `gaps.py` entry moves a line out of this set without
#: anyone editing it, and a *wrong* entry cannot hide a defect — it would have
#: to describe a field that really is lost.
KNOWN_DEFECTS: frozenset[str] = frozenset(
    {
        # 2.1-only, each its own investigation.
        # **Not a defect** — `_ComponentMixin.__attrs_post_init__` inherits the
        # concept's `core_representation` when a component declares none, which
        # is SDMX's rule. It appears here because the fixture's concept `Ref` is
        # unresolved and the reader's is resolved, so the inheritance fires on
        # one side only. Kept in this set rather than declared as a gap,
        # because nothing was lost on the wire.
        "metadata_structures.attributes[].children[].representation",
    }
)


def _relative(path: str) -> str:
    """A census path as `gaps.py` spells it — artefact-relative, no indices."""
    return re.sub(r"\[\d+\]", "[]", path.split(".", 1)[1] if "." in path else "")


def test_every_loss_is_a_declared_limit_or_a_listed_defect() -> None:
    """Partition the register, so "how much is left" has an answer.

    Each observed loss must be either something `gaps.py` says the format
    cannot carry, or a line in `KNOWN_DEFECTS`. A loss that is neither is new
    and unclassified — the state this test exists to prevent.

    Checked in both directions: a defect that stops reproducing must leave the
    list, for the reason the register itself is pruned.
    """
    unclassified: dict[str, set[str]] = {}
    stale = set(KNOWN_DEFECTS)
    for fmt in FORMATS:
        declared = {
            gap.field
            for slot in (a.name for a in StructureMessage.__attrs_attrs__ if not a.name.startswith("_"))  # pyrefly: ignore[missing-attribute]
            for artefact in getattr(maximal.message(), slot) or []
            for gap in unrepresentable(artefact, fmt)
        }
        for path in _observed(fmt)[0]:
            general = re.sub(r"\[\d+\]", "[]", path)
            if _relative(path) in declared:
                continue
            stale.discard(general)
            if general not in KNOWN_DEFECTS:
                unclassified.setdefault(general, set()).add(fmt)

    assert not unclassified, (
        "these losses are neither declared in `gaps.py` nor listed as defects:\n"
        + "\n".join(f"    {p}  ({', '.join(sorted(w))})" for p, w in sorted(unclassified.items()))
        + "\n\nDeclare it a format limit, or add it to KNOWN_DEFECTS with what it is."
    )
    assert not stale, (
        f"these defects no longer reproduce: {sorted(stale)}. Delete them — a list nobody prunes stops being read."
    )
