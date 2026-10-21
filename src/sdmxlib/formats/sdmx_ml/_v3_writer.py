"""The SDMX-ML v3 structure writer, shared by 3.0 and 3.1 (#346).

`sdmx_ml31/writer.py` was a copy of `sdmx_ml30/writer.py`: 47 of 50 functions
byte-identical, with **three** genuine wire-format divergences. Those three are
now a `V3Generation` flag set rather than a second copy of the file.

Unlike the reader — which merged with nothing to parameterise (#346, !487) —
the writer needs that set, and it is the "what this generation omits" of about
three entries the issue predicted:

| flag | 3.0 | 3.1 | XSD |
| --- | --- | --- | --- |
| `categorisation_version` | yes | no | 3.1 marks `CategorisationBaseType/@version` `use="prohibited"` |
| `data_constraint_role` | yes | no | 3.1 removed `@role`; Actual moved to `AvailabilityConstraint` |
| `release_calendar` | yes | no | 3.1 removed `<ReleaseCalendar>` from `DataConstraintType` |
| `dimension_constraint` | no | yes | `DataflowBaseType` extends `StructureUsageType` with it in 3.1 alone |
| `partial_language` | no | yes | 3.1 added `@isPartialLanguage` to `MaintainableBaseType` |
| `evolving_structure` | no | yes | 3.1 added `@evolvingStructure` to `DataStructureType` |
| `availability_constraint` | no | yes | 3.1 added `<AvailabilityConstraints>`, where Actual content went |

Each flag has a row in `formats/gaps.py` saying the same thing to a caller, and
`tests/unit/test_ml3_writer_generations.py` asserts the two agree — two
statements of one fact are how #345 happened.

The version *grammar* is not a flag. `check_writable(version, "3.0")` is
correct for both: `model/version.py` declares two grammars, `2.1` and `3.0`,
and 3.1 shares 3.0's.

A class rather than a closure factory, per #209 — see `_v3_reader` for the
same decision and the measurement behind it.
"""

from datetime import datetime

import attrs
from lxml import etree

from sdmxlib.formats.gaps import with_derived_targets, without_dropped
from sdmxlib.formats.sdmx_ml._common import (
    SectionEntry,
    WriterHelpers,
    coding_format_attrs,
    core_enumeration_format,
    section_entry,
)
from sdmxlib.formats.sdmx_ml._header import write_header
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.category import Categorisation
from sdmxlib.model.codelist import (
    Code,
    Codelist,
    CodelistExtension,
    CodeSelection,
    GeoFeatureSetCode,
    GeographicCodelist,
    GeoGridCode,
    GeoGridCodelist,
    InclusionMode,
    MemberValue,
)
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.constraint import (
    AvailabilityConstraint,
    ConstraintType,
    CubeRegion,
    DataConstraint,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import (
    AttachmentLevel,
    Attribute,
    AttributeUsage,
    DataStructure,
    Dimension,
    Group,
    Measure,
    MetadataAttributeUsage,
    TimeDimension,
)
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, HierarchyAssociation
from sdmxlib.model.mapping import (
    CategorySchemeMap,
    ConceptSchemeMap,
    DatePatternMap,
    EpochMap,
    ItemSchemeMap,
    MapDataType,
    OrganisationSchemeMap,
    RepresentationMap,
    RepresentationMapping,
    StructureMap,
)
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.organisation import (
    Agency,
    AgencyScheme,
    Contact,
    DataConsumer,
    DataConsumerScheme,
    DataProvider,
    DataProviderScheme,
    MetadataProvider,
    MetadataProviderScheme,
    OrganisationUnit,
    OrganisationUnitScheme,
)
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref, artefact_urn
from sdmxlib.model.representation import Facet, Representation
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import check_writable
from sdmxlib.model.valuelist import ValueList

# ── Namespace helpers ─────────────────────────────────────────────────────────

# ── Shared writer helpers ─────────────────────────────────────────────────────
# Pulled from sdmxlib.formats.sdmx_ml._common and used as `_helpers.x` so each
# call site says where the helper comes from. Only the namespace tag builders
# keep short names; they go away with the prefixed-path conversion (#209).


# ── SDMX 3.0-specific overrides of common writers ──────────────────────────
# SDMX 3.0 made several breaking changes from 2.1:
#   - `isFinal` was removed from every maintainable artefact.
#   - `AgencyScheme` is sui generis — fixed id="AGENCIES", never versioned,
#     no isFinal.
#   - `Codelist.Code.Parent` is a simple text element containing the parent
#     code's id, not an element with a nested <Ref/>.
#   - The Concepts container was renamed to ConceptSchemes (in _SECTIONS).


def _plain_codelists(msg: StructureMessage) -> list[Codelist]:
    """The message's codelists that are neither geographic kind."""
    return [cl for cl in msg.codelists if not isinstance(cl, GeographicCodelist | GeoGridCodelist)]


def _codelists_of[C: Codelist](msg: StructureMessage, kind: type[C]) -> list[C]:
    """The message's codelists of one geographic kind, each in its own container."""
    return [cl for cl in msg.codelists if isinstance(cl, kind)]


@attrs.frozen
class V3Generation:
    """What one SDMX v3 generation's XSD allows the writer to emit.

    Four booleans rather than a version string, so a call site reads as the
    *capability* it depends on and a future 3.2 sets flags instead of adding
    branches on a version literal.
    """

    #: 3.1 marks `CategorisationBaseType/@version` `use="prohibited"`, so the
    #: 3.1 writer sets `agencyID`/`id` by hand instead of calling
    #: `_write_maintainable_attrs`.
    categorisation_version: bool

    #: `<str:DataConstraint role="Allowed|Actual">`. 3.1 removed it — its
    #: DataConstraint is allowed-only by definition, the Actual semantic having
    #: moved to `AvailabilityConstraint` (#69).
    data_constraint_role: bool

    #: `<ReleaseCalendar>` inside `DataConstraintType`. Removed in 3.1 for the
    #: same reason, and declared in `formats/gaps.py` alongside the one above.
    release_calendar: bool

    #: `<DimensionConstraint>` on a Dataflow — 3.1's alone. The dimensions a
    #: flow commits to, so a DSD growing one in a minor version does not
    #: silently widen the flow's key.
    dimension_constraint: bool

    #: `@isPartialLanguage` on a maintainable and `@evolvingStructure` on a
    #: DataStructure — both 3.1's alone (#422).
    partial_language: bool
    evolving_structure: bool

    #: `<str:AvailabilityConstraints>` — 3.1's alone, where the Actual role
    #: went: an Actual `DataConstraint` that fits is written there (#421).
    availability_constraint: bool

    @property
    def target(self) -> str:
        """The `formats.gaps` target this generation writes, for what it leaves out."""
        return "3.1" if self.availability_constraint else "3.0"


#: SDMX-ML 3.0.
V3_0 = V3Generation(
    categorisation_version=True,
    data_constraint_role=True,
    release_calendar=True,
    dimension_constraint=False,
    partial_language=False,
    evolving_structure=False,
    availability_constraint=False,
)

#: SDMX-ML 3.1.
V3_1 = V3Generation(
    categorisation_version=False,
    data_constraint_role=False,
    release_calendar=False,
    dimension_constraint=True,
    partial_language=True,
    evolving_structure=True,
    availability_constraint=True,
)


def _states_availability(dc: DataConstraint) -> bool:
    """Whether 3.1 writes `dc` as an `AvailabilityConstraint`: Actual, and fitting one (#421)."""
    return dc.constraint_type is ConstraintType.ACTUAL and not dc.availability_misfits()


class V3Writer:
    """One SDMX-ML v3 structure writer, bound to a generation.

    Args:
        com: the `com:` namespace URI for this generation.
        str_: the `str:` namespace URI.
        mes: the `mes:` namespace URI.
        generation: which capabilities its XSD allows.
    """

    def __init__(self, *, com: str, str_: str, mes: str, generation: V3Generation) -> None:
        # `grammar="3.0"` for both: `model/version.py` declares two version
        # grammars and 3.1 shares 3.0's, so this is the target, not the
        # generation.
        self._helpers = WriterHelpers(com=com, str_=str_, mes=mes, grammar="3.0")
        self._helpers.partial_language = generation.partial_language
        self._helpers.target = generation.target
        self._c = self._helpers.c
        self._s = self._helpers.s
        self._m = self._helpers.m
        self._nsmap = {"mes": mes, "str": str_, "com": com}
        self._gen = generation
        self._sections = self._build_sections()

    def _write_validity(self, el: etree._Element, obj: object) -> None:
        """Emit `validFrom` / `validTo`, which `MaintainableType` gives every one.

        `_write_maintainable_attrs` already does this, but eight writers per
        version set `id` / `agencyID` / `version` by hand instead of calling it —
        the organisation schemes because their version is schema-fixed, the rest
        for no reason anyone recorded. All eight therefore dropped an artefact's
        validity window silently, while a Codelist written through the shared
        helper kept it (#355).

        Defensive `getattr`: validity is declared per subclass rather than on the
        maintainable base.
        """
        valid_from: datetime | None = getattr(obj, "valid_from", None)
        if valid_from is not None:
            el.set("validFrom", valid_from.isoformat())
        valid_to: datetime | None = getattr(obj, "valid_to", None)
        if valid_to is not None:
            el.set("validTo", valid_to.isoformat())

    def _write_maintainable_attrs(self, el: etree._Element, obj: MaintainableArtefact) -> None:
        """Set id, agencyID, version, uri on a maintainable element. No isFinal in 3.0."""
        # The format-specific twin of `WriterHelpers.write_maintainable_attrs`,
        # so it owes the same check: 3.0 forbids `1.03` and `1.2.3.4`, which 2.1
        # permits and the store therefore holds (#319).
        check_writable(obj.version, "3.0", artefact=f"{type(obj).__name__}:{obj.id}")
        el.set("id", obj.id)
        el.set("agencyID", obj.agency_id)
        el.set("version", obj.version)
        self._helpers.write_uri(el, obj)
        # valid_from/valid_to are subclass-specific (not on the maintainable base),
        # so read them defensively off the getattr result.
        valid_from = getattr(obj, "valid_from", None)
        if valid_from is not None:
            el.set("validFrom", valid_from.isoformat())
        valid_to = getattr(obj, "valid_to", None)
        if valid_to is not None:
            el.set("validTo", valid_to.isoformat())
        self._helpers.write_reference_attrs(el, obj)

    @staticmethod
    def _set_uri(el: etree._Element, uri: str | None) -> None:
        """A component list's ``@uri`` (#422), which no model class carries for it."""
        if uri is not None:
            el.set("uri", uri)

    def _write_code(self, parent: etree._Element, code: Code) -> None:
        """SDMX 3.0: Parent is a simple-typed element with the parent id as text.

        A geographic code is written as its own element — `<str:GeoFeatureSetCode>`
        with its `@value`, `<str:GeoGridCode>` with a trailing `<str:GeoCell>` —
        wherever it sits, as the XSD's substitution group allows.
        """
        match code:
            case GeoFeatureSetCode():
                el = etree.SubElement(parent, self._s("GeoFeatureSetCode"))
                el.set("value", code.value)
            case GeoGridCode():
                el = etree.SubElement(parent, self._s("GeoGridCode"))
            case _:
                el = etree.SubElement(parent, self._s("Code"))
        el.set("id", code.id)
        self._helpers.write_uri(el, code)
        self._helpers.write_annotations(el, code.annotations)
        self._helpers.write_links(el, code.links)
        self._helpers.write_istring(el, code.name)
        if code.description:
            self._helpers.write_istring(el, code.description, "Description")
        if code.parent_id is not None:
            etree.SubElement(el, self._s("Parent")).text = code.parent_id
        if isinstance(code, GeoGridCode):
            etree.SubElement(el, self._s("GeoCell")).text = code.geo_cell

    def _write_member_value(self, parent: etree._Element, mv: MemberValue) -> None:
        """Emit a v3.0 ``<str:MemberValue>`` (``WildcardedMemberValueType``).

        The value text may contain the ``%`` wildcard character per the
        XSD pattern. ``cascadeValues`` is the optional ``CascadeSelectionType``
        union (``true`` / ``false`` / ``excluderoot``).
        """
        el = etree.SubElement(parent, self._s("MemberValue"))
        el.text = mv.value
        if mv.cascade is not None:
            el.set("cascadeValues", mv.cascade.value)

    def _write_code_selection(self, parent: etree._Element, sel: CodeSelection) -> None:
        """Emit the inclusive- or exclusive-selection wrapper per ``mode``.

        Both ``<str:InclusiveCodeSelection>`` and ``<str:ExclusiveCodeSelection>``
        are ``CodeSelectionType``: a ``<str:MemberValue>+`` sequence.
        """
        tag = "InclusiveCodeSelection" if sel.mode is InclusionMode.INCLUDE else "ExclusiveCodeSelection"
        el = etree.SubElement(parent, self._s(tag))
        for mv in sel.members:
            self._write_member_value(el, mv)

    def _write_codelist_extension(self, parent: etree._Element, ext: CodelistExtension) -> None:
        """Emit a v3.0 ``<str:CodelistExtension>``.

        The sequence is: ``<Codelist>`` URN-text → optional code selection
        (Inclusive or Exclusive choice). ``@prefix`` is an optional attribute.
        """
        el = etree.SubElement(parent, self._s("CodelistExtension"))
        if ext.prefix is not None:
            el.set("prefix", ext.prefix)
        self._write_urn_text(el, "Codelist", ext.codelist)
        if ext.selection is not None:
            self._write_code_selection(el, ext.selection)

    def _write_codelist(self, parent: etree._Element, cl: Codelist) -> None:
        match cl:
            case GeographicCodelist():
                el = etree.SubElement(parent, self._s("GeographicCodelist"))
            case GeoGridCodelist():
                el = etree.SubElement(parent, self._s("GeoGridCodelist"))
            case _:
                el = etree.SubElement(parent, self._s("Codelist"))
        self._write_maintainable_attrs(el, cl)
        if isinstance(cl, GeographicCodelist | GeoGridCodelist):
            # Fixed by each type's XSD, and required.
            el.set("geoType", type(cl).__name__)
        self._helpers.write_annotations(el, cl.annotations)
        self._helpers.write_links(el, cl.links)
        self._helpers.write_istring(el, cl.name)
        if cl.description:
            self._helpers.write_istring(el, cl.description, "Description")
        for code in cl.codes:
            self._write_code(el, code)
        # CodelistExtension is the final element in the v3.0 CodelistType
        # sequence, after all <Code> children. Empty extensions tuple emits
        # nothing — matches the XSD default.
        for ext in cl.extensions:
            self._write_codelist_extension(el, ext)
        if isinstance(cl, GeoGridCodelist):
            # An extension of the base sequence, so after the extensions.
            etree.SubElement(el, self._s("GridDefinition")).text = cl.grid_definition

    def _write_value_list(self, parent: etree._Element, vl: ValueList) -> None:
        """Emit a ``<str:ValueList>``: the maintainable header, then each ``<str:ValueItem>``."""
        el = etree.SubElement(parent, self._s("ValueList"))
        self._write_maintainable_attrs(el, vl)
        self._helpers.write_annotations(el, vl.annotations)
        self._helpers.write_links(el, vl.links)
        self._helpers.write_istring(el, vl.name)
        if vl.description:
            self._helpers.write_istring(el, vl.description, "Description")
        for item in vl.items:
            item_el = etree.SubElement(el, self._s("ValueItem"))
            item_el.set("id", item.id)
            self._helpers.write_annotations(item_el, item.annotations)
            # Optional here, unlike on a code: a value may be its own label.
            if item.name:
                self._helpers.write_istring(item_el, item.name)
            if item.description:
                self._helpers.write_istring(item_el, item.description, "Description")

    def _write_urn_text(self, parent: etree._Element, tag: str, ref: Ref[object]) -> None:
        """Emit `<{tag}>urn:...</{tag}>` for a Ref.

        SDMX 3.0 changed most cross-references from `<Element><Ref attrs.../></Element>`
        (the 2.1 shape) to simple URN-text elements containing the canonical SDMX
        URN string. Affects Enumeration, ConceptIdentity, Structure (in Dataflow),
        Source/Target (in Categorisation, scheme maps), and several others.
        """
        el = etree.SubElement(parent, self._s(tag))
        el.text = str(ref.urn)

    def _write_concept_identity(self, parent: etree._Element, ref: Ref[object]) -> None:
        """SDMX 3.0: ConceptIdentity is a URN-text element, not a wrapped Ref."""
        self._write_urn_text(parent, "ConceptIdentity", ref)

    def _write_representation(
        self,
        parent: etree._Element,
        representation: Representation,
        tag: str = "LocalRepresentation",
        enumeration_format: Facet | None = None,
        occurs: tuple[int, int | None] = (1, 1),
    ) -> None:
        """SDMX 3.0: Enumeration is a URN-text element, not a wrapped Ref.

        `occurs` is a DSD attribute's or measure's ``(min_occurs, max_occurs)``
        (#422), written only where it differs from the schema's default of 1.

        ``RepresentationType`` is ``TextFormat | (Enumeration, EnumerationFormat?)``
        here exactly as in 2.1, so an enumerated component may also carry a facet
        constraining the codes. `enumeration_format` was wired into the 2.1 writer
        only when it was added, so a component round-tripping through 3.x lost it
        (#243).
        """
        if representation is None and enumeration_format is None:
            return
        rep_el = etree.SubElement(parent, self._s(tag))
        min_occurs, max_occurs = occurs
        if min_occurs != 1:
            rep_el.set("minOccurs", str(min_occurs))
        if max_occurs != 1:
            rep_el.set("maxOccurs", "unbounded" if max_occurs is None else str(max_occurs))
        if isinstance(representation, Facet):
            self._helpers.write_text_format(rep_el, representation)
        elif isinstance(representation, Ref):
            self._write_urn_text(rep_el, "Enumeration", representation)
        if enumeration_format is not None:
            self._helpers.write_text_format(rep_el, enumeration_format, tag="EnumerationFormat")

    def _write_concept(self, parent: etree._Element, concept: Concept) -> None:
        """SDMX 3.0 Concept — CoreRepresentation uses URN-text Enumeration."""
        el = etree.SubElement(parent, self._s("Concept"))
        el.set("id", concept.id)
        self._helpers.write_uri(el, concept)
        self._helpers.write_annotations(el, concept.annotations)
        self._helpers.write_links(el, concept.links)
        self._helpers.write_istring(el, concept.name)
        if concept.description:
            self._helpers.write_istring(el, concept.description, "Description")
        # `Parent` is on `ConceptBaseType`, `CoreRepresentation` on the extension,
        # so the XSD sequences Parent first. 3.0 spells it as text (`Parent` is a
        # `SingleNCNameIDType`) where 2.1 wraps a `<Ref>` — the same split as
        # `Code.Parent`.
        if concept.parent_id is not None:
            etree.SubElement(el, self._s("Parent")).text = concept.parent_id
        self._write_representation(
            el,
            concept.core_representation,
            tag="CoreRepresentation",
            enumeration_format=core_enumeration_format(concept),
            occurs=(concept.min_occurs, concept.max_occurs),
        )
        self._helpers.write_iso_concept_reference(el, concept.iso_concept_reference)

    def _write_concept_scheme(self, parent: etree._Element, cs: ConceptScheme) -> None:
        el = etree.SubElement(parent, self._s("ConceptScheme"))
        self._write_maintainable_attrs(el, cs)
        self._helpers.write_annotations(el, cs.annotations)
        self._helpers.write_links(el, cs.links)
        self._helpers.write_istring(el, cs.name)
        if cs.description:
            self._helpers.write_istring(el, cs.description, "Description")
        for concept in cs.concepts:
            self._write_concept(el, concept)

    def _write_dataflow(self, parent: etree._Element, df: Dataflow) -> None:
        """SDMX 3.0: Dataflow.Structure is a URN-text element, not a wrapped Ref."""
        el = etree.SubElement(parent, self._s("Dataflow"))
        self._write_maintainable_attrs(el, df)
        self._helpers.write_annotations(el, df.annotations)
        self._helpers.write_links(el, df.links)
        self._helpers.write_istring(el, df.name)
        if df.description:
            self._helpers.write_istring(el, df.description, "Description")
        if isinstance(df.structure, Ref):
            self._write_urn_text(el, "Structure", df.structure)
        # After `Structure`, which is where `DataflowType` sequences it. It
        # carries the dimensions a dataflow *commits* to, so a DSD that grows
        # one in a minor version does not silently widen the flow's key (#355).
        if self._gen.dimension_constraint and df.dimension_constraint:
            dc_el = etree.SubElement(el, self._s("DimensionConstraint"))
            for dimension_id in df.dimension_constraint:
                etree.SubElement(dc_el, self._s("Dimension")).text = dimension_id

    def _write_categorisation(self, parent: etree._Element, cat: Categorisation) -> None:
        """SDMX 3.0: Source / Target are URN-text elements, not wrapped Refs."""
        el = etree.SubElement(parent, self._s("Categorisation"))
        if self._gen.categorisation_version:
            self._write_maintainable_attrs(el, cat)
        else:
            # 3.1 Categorisation XSD: ``<xs:attribute name="version" use="prohibited"/>``.
            # Emit only agencyID + id; skip the version attribute even when the
            # model carries one.
            el.set("agencyID", cat.agency_id)
            el.set("id", cat.id)
            self._helpers.write_uri(el, cat)
            self._helpers.write_reference_attrs(el, cat)
            if cat.valid_from is not None:
                el.set("validFrom", cat.valid_from.isoformat())
            if cat.valid_to is not None:
                el.set("validTo", cat.valid_to.isoformat())
        self._helpers.write_annotations(el, cat.annotations)
        self._helpers.write_links(el, cat.links)
        self._helpers.write_istring(el, cat.name)
        if cat.description:
            self._helpers.write_istring(el, cat.description, "Description")
        if cat.source is not None:
            self._write_urn_text(el, "Source", cat.source)
        if cat.target is not None:
            self._write_urn_text(el, "Target", cat.target)

    def _write_provision_agreement(self, parent: etree._Element, pa: ProvisionAgreement) -> None:
        """SDMX 3.0: Dataflow / DataProvider refs are URN-text elements."""
        el = etree.SubElement(parent, self._s("ProvisionAgreement"))
        self._write_maintainable_attrs(el, pa)
        self._helpers.write_annotations(el, pa.annotations)
        self._helpers.write_links(el, pa.links)
        self._helpers.write_istring(el, pa.name)
        if pa.description:
            self._helpers.write_istring(el, pa.description, "Description")
        if pa.dataflow is not None:
            self._write_urn_text(el, "Dataflow", pa.dataflow)
        if pa.data_provider is not None:
            self._write_urn_text(el, "DataProvider", pa.data_provider)

    def _write_metadata_provision_agreement(self, parent: etree._Element, mpa: MetadataProvisionAgreement) -> None:
        """SDMX 3.0: the reference-metadata analogue of ProvisionAgreement.

        Per `MetadataProvisionAgreementType`: `Metadataflow` and `MetadataProvider`
        are required URN-text refs, and `Target` is zero or more wildcard URNs —
        strings rather than references, since a wildcard names a class of artefacts
        that no resolvable reference can express.
        """
        el = etree.SubElement(parent, self._s("MetadataProvisionAgreement"))
        self._write_maintainable_attrs(el, mpa)
        self._helpers.write_annotations(el, mpa.annotations)
        self._helpers.write_links(el, mpa.links)
        self._helpers.write_istring(el, mpa.name)
        if mpa.description:
            self._helpers.write_istring(el, mpa.description, "Description")
        if mpa.metadataflow is not None:
            self._write_urn_text(el, "Metadataflow", mpa.metadataflow)
        if mpa.metadata_provider is not None:
            self._write_urn_text(el, "MetadataProvider", mpa.metadata_provider)
        for target in mpa.targets:
            etree.SubElement(el, self._s("Target")).text = target

    def _write_item_scheme_map(self, parent: etree._Element, tag: str, sm: ItemSchemeMap) -> None:
        """3.0 ItemScheme-map writer for the concept, category and organisation scheme maps.

        Per v3.0 ``ItemSchemeMapType``: Source/Target are URN-text refs, inner
        pairs are ``<str:ItemMap>`` with ``<str:SourceValue>`` and
        ``<str:TargetValue>`` text children (SingleValueMappingType).
        """
        el = etree.SubElement(parent, self._s(tag))
        el.set("id", sm.id)
        self._helpers.write_uri(el, sm)
        el.set("agencyID", sm.agency_id)
        el.set("version", sm.version)
        self._helpers.write_reference_attrs(el, sm)
        self._write_validity(el, sm)
        self._helpers.write_annotations(el, sm.annotations)
        self._helpers.write_links(el, sm.links)
        self._helpers.write_istring(el, sm.name)
        if sm.description:
            self._helpers.write_istring(el, sm.description, "Description")
        if sm.source is not None:
            self._write_urn_text(el, "Source", sm.source)
        if sm.target is not None:
            self._write_urn_text(el, "Target", sm.target)
        for pair in sm.maps:
            m_el = etree.SubElement(el, self._s("ItemMap"))
            if pair.valid_from is not None:
                m_el.set("validFrom", pair.valid_from.isoformat())
            if pair.valid_to is not None:
                m_el.set("validTo", pair.valid_to.isoformat())
            self._helpers.write_annotations(m_el, pair.annotations)
            etree.SubElement(m_el, self._s("SourceValue")).text = pair.source_id
            etree.SubElement(m_el, self._s("TargetValue")).text = pair.target_id

    def _write_representation_map(self, parent: etree._Element, rm: RepresentationMap) -> None:
        """3.0 RepresentationMap writer.

        Per v3.0 ``RepresentationMapType``: source/target may be either a
        codelist URN ref (``SourceCodelist`` / ``TargetCodelist``) or a
        primitive ``DataType`` literal (``SourceDataType`` / ``TargetDataType``).
        Inner pairs are ``<str:RepresentationMapping>`` with ``<str:SourceValue>``
        and ``<str:TargetValue>`` children (ValueMappingType).
        """
        el = etree.SubElement(parent, self._s("RepresentationMap"))
        el.set("id", rm.id)
        self._helpers.write_uri(el, rm)
        el.set("agencyID", rm.agency_id)
        el.set("version", rm.version)
        self._helpers.write_reference_attrs(el, rm)
        self._write_validity(el, rm)
        self._helpers.write_annotations(el, rm.annotations)
        self._helpers.write_links(el, rm.links)
        self._helpers.write_istring(el, rm.name)
        if rm.description:
            self._helpers.write_istring(el, rm.description, "Description")
        for side, endpoints in (("Source", rm.sources), ("Target", rm.targets)):
            for endpoint in endpoints:
                match endpoint:
                    case MapDataType(data_type=data_type):
                        etree.SubElement(el, self._s(f"{side}DataType")).text = data_type
                    case Ref():
                        self._write_urn_text(el, f"{side}Codelist", endpoint)
        for mapping in rm.mappings:
            self._write_representation_mapping(el, mapping)

    def _write_representation_mapping(self, parent: etree._Element, pair: RepresentationMapping) -> None:
        """Emit one ``<str:RepresentationMapping>`` — every source value, then every target value.

        A source value's optional ``isRegEx`` / ``startIndex`` / ``endIndex``
        attributes say how it matches. Per-mapping validity (``validFrom`` /
        ``validTo`` ``xs:date`` attributes) lives on the
        ``<RepresentationMapping>`` element itself.
        """
        m_el = etree.SubElement(parent, self._s("RepresentationMapping"))
        if pair.valid_from is not None:
            m_el.set("validFrom", pair.valid_from.isoformat())
        if pair.valid_to is not None:
            m_el.set("validTo", pair.valid_to.isoformat())
        self._helpers.write_annotations(m_el, pair.annotations)
        for source in pair.sources:
            sv_el = etree.SubElement(m_el, self._s("SourceValue"))
            sv_el.text = source.value
            if source.is_regex:
                sv_el.set("isRegEx", "true")
            if source.start_index is not None:
                sv_el.set("startIndex", str(source.start_index))
            if source.end_index is not None:
                sv_el.set("endIndex", str(source.end_index))
        for target in pair.targets:
            etree.SubElement(m_el, self._s("TargetValue")).text = target

    def _write_structure_map(self, parent: etree._Element, sm: StructureMap) -> None:
        """3.0 StructureMap writer (unifies 2.1 DataStructureMap + DataflowMap).

        Per v3.0 ``StructureMapType``: Source / Target are URN-text refs (either
        DataStructure or Dataflow); inner ComponentMap pairs carry source/target
        component ids.
        """
        el = etree.SubElement(parent, self._s("StructureMap"))
        el.set("id", sm.id)
        self._helpers.write_uri(el, sm)
        el.set("agencyID", sm.agency_id)
        el.set("version", sm.version)
        self._helpers.write_reference_attrs(el, sm)
        self._write_validity(el, sm)
        self._helpers.write_annotations(el, sm.annotations)
        self._helpers.write_links(el, sm.links)
        self._helpers.write_istring(el, sm.name)
        if sm.description:
            self._helpers.write_istring(el, sm.description, "Description")
        if sm.source is not None:
            self._write_urn_text(el, "Source", sm.source)
        if sm.target is not None:
            self._write_urn_text(el, "Target", sm.target)
        # In `StructureMapType`'s order (#422): date maps and formats, the
        # component maps, then the fixed values.
        self._write_date_maps(el, sm)
        for comp in sm.maps:
            m_el = etree.SubElement(el, self._s("ComponentMap"))
            self._helpers.write_annotations(m_el, comp.annotations)
            for source in comp.sources:
                etree.SubElement(m_el, self._s("Source")).text = source
            for target in comp.targets:
                etree.SubElement(m_el, self._s("Target")).text = target
            if comp.representation_map is not None:
                self._write_urn_text(m_el, "RepresentationMap", comp.representation_map)
        for fixed in sm.fixed_value_maps:
            fv_el = etree.SubElement(el, self._s("FixedValueMap"))
            self._helpers.write_annotations(fv_el, fixed.annotations)
            side = "Source" if fixed.source_id is not None else "Target"
            etree.SubElement(fv_el, self._s(side)).text = fixed.source_id or fixed.target_id
            for value in fixed.values:
                etree.SubElement(fv_el, self._s("Value")).text = value

    def _write_date_maps(self, el: etree._Element, sm: StructureMap) -> None:
        """A structure map's epoch and date-pattern maps and frequency formats (#422)."""
        for epoch_map in sm.epoch_maps:
            em_el = self._write_date_map(el, "EpochMap", epoch_map)
            em_el.set("basePeriod", epoch_map.base_period)
            em_el.set("epochPeriod", epoch_map.epoch_period.value)
        for pattern_map in sm.date_pattern_maps:
            pm_el = self._write_date_map(el, "DatePatternMap", pattern_map)
            pm_el.set("sourcePattern", pattern_map.source_pattern)
            pm_el.set("locale", pattern_map.locale)
        for mapping in sm.frequency_format_mappings:
            fm_el = etree.SubElement(el, self._s("FrequencyFormatMapping"))
            fm_el.set("id", mapping.id)
            self._helpers.write_uri(fm_el, mapping)
            self._helpers.write_annotations(fm_el, mapping.annotations)
            self._helpers.write_links(fm_el, mapping.links)
            etree.SubElement(fm_el, self._s("FrequencyId")).text = mapping.frequency_id
            etree.SubElement(fm_el, self._s("DatePattern")).text = mapping.date_pattern

    def _write_date_map(self, parent: etree._Element, tag: str, date_map: EpochMap | DatePatternMap) -> etree._Element:
        """What `DateMapType` gives an epoch and a date-pattern map alike (#422)."""
        el = etree.SubElement(parent, self._s(tag))
        el.set("id", date_map.id)
        self._helpers.write_uri(el, date_map)
        if date_map.resolve_period is not None:
            el.set("resolvePeriod", date_map.resolve_period.value)
        self._helpers.write_annotations(el, date_map.annotations)
        self._helpers.write_links(el, date_map.links)
        for source, target in date_map.component_maps:
            etree.SubElement(el, self._s("Source")).text = source
            etree.SubElement(el, self._s("Target")).text = target
        if date_map.frequency_dimension is not None:
            etree.SubElement(el, self._s("FrequencyDimension")).text = date_map.frequency_dimension
            for frequency in date_map.mapped_frequencies:
                etree.SubElement(el, self._s("MappedFrequencies")).text = frequency
        elif date_map.target_frequency_id is not None:
            etree.SubElement(el, self._s("TargetFrequencyID")).text = date_map.target_frequency_id
        return el

    def _write_concept_scheme_map(self, parent: etree._Element, csm: ConceptSchemeMap) -> None:
        self._write_item_scheme_map(parent, "ConceptSchemeMap", csm)

    def _write_category_scheme_map(self, parent: etree._Element, csm: CategorySchemeMap) -> None:
        self._write_item_scheme_map(parent, "CategorySchemeMap", csm)

    def _write_organisation_scheme_map(self, parent: etree._Element, osm: OrganisationSchemeMap) -> None:
        self._write_item_scheme_map(parent, "OrganisationSchemeMap", osm)

    def _write_concept_roles(self, parent: etree._Element, roles: tuple[str, ...]) -> None:
        """Emit one <str:ConceptRole> per role, referencing SDMX:CROSS_DOMAIN_CONCEPTS(1.0).

        SDMX 3.0 IM §5.3 defines the standard roles (MEASURE, FREQUENCY, TIME,
        ENTITY, COUNT, GEO) as items in the SDMX cross-domain concept scheme,
        which is the canonical home assumed here when re-serializing.
        """
        for role in roles:
            # SDMX 3.x types ConceptRole `common:ConceptReferenceType`, which is a
            # *simple* type restricted to `.+\.conceptscheme\.Concept=.+` — URN
            # text, not the 2.1 child-<Ref> shape. Emitting the 2.1 form produced
            # XML the schema rejects twice over: element content on a simple type,
            # and an empty string against the URN pattern.
            cr = etree.SubElement(parent, self._s("ConceptRole"))
            cr.text = f"urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=SDMX:CROSS_DOMAIN_CONCEPTS(1.0).{role}"

    # ── Codelists ─────────────────────────────────────────────────────────────────

    def _write_contact(self, parent: etree._Element, contact: Contact) -> None:
        el = etree.SubElement(parent, self._s("Contact"))
        if contact.id is not None:
            el.set("id", contact.id)
        if contact.name:
            # <com:Name> lives in the common namespace.
            self._helpers.write_istring(el, contact.name)
        # <str:Department> and <str:Role> are in the structure namespace per
        # the v3.0 ContactType XSD; emit them manually rather than via
        # self._helpers.write_istring (which targets the common namespace).
        for lang, text in contact.department.items():
            dept_el = etree.SubElement(el, self._s("Department"))
            dept_el.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
            dept_el.text = text
        for lang, text in contact.role.items():
            role_el = etree.SubElement(el, self._s("Role"))
            role_el.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
            role_el.text = text
        # The XSD models Telephone / Fax / X400 / URI / Email as an
        # ``<xs:choice maxOccurs="unbounded">`` — order between *kinds*
        # doesn't matter, only ordering within a kind. We emit in declaration
        # order of the model fields.
        for phone in contact.phones:
            etree.SubElement(el, self._s("Telephone")).text = phone
        for fax in contact.faxes:
            etree.SubElement(el, self._s("Fax")).text = fax
        for x400 in contact.x400s:
            etree.SubElement(el, self._s("X400")).text = x400
        for uri in contact.uris:
            etree.SubElement(el, self._s("URI")).text = uri
        for email in contact.emails:
            etree.SubElement(el, self._s("Email")).text = email

    def _write_agency(self, parent: etree._Element, agency: Agency) -> None:
        el = etree.SubElement(parent, self._s("Agency"))
        el.set("id", agency.id)
        self._helpers.write_uri(el, agency)
        self._helpers.write_annotations(el, agency.annotations)
        self._helpers.write_links(el, agency.links)
        if agency.name:
            self._helpers.write_istring(el, agency.name)
        if agency.description:
            self._helpers.write_istring(el, agency.description, "Description")
        for contact in agency.contacts:
            self._write_contact(el, contact)

    def _write_agency_scheme(self, parent: etree._Element, scheme: AgencyScheme) -> None:
        """SDMX 3.0 AgencyScheme: fixed id="AGENCIES", never versioned, no isFinal."""
        el = etree.SubElement(parent, self._s("AgencyScheme"))
        el.set("agencyID", scheme.agency_id)
        # Per SDMXStructureOrganisation.xsd the id is *fixed* to "AGENCIES" — we
        # echo the model field anyway so a malformed scheme surfaces in review.
        el.set("id", scheme.id)
        self._helpers.write_uri(el, scheme)
        self._helpers.write_reference_attrs(el, scheme)
        self._write_validity(el, scheme)
        self._helpers.write_annotations(el, scheme.annotations)
        self._helpers.write_links(el, scheme.links)
        if scheme.name:
            self._helpers.write_istring(el, scheme.name)
        if scheme.description:
            self._helpers.write_istring(el, scheme.description, "Description")
        for agency in scheme.agencies:
            self._write_agency(el, agency)

    def _write_data_provider(self, parent: etree._Element, provider: DataProvider) -> None:
        el = etree.SubElement(parent, self._s("DataProvider"))
        el.set("id", provider.id)
        self._helpers.write_uri(el, provider)
        self._helpers.write_annotations(el, provider.annotations)
        self._helpers.write_links(el, provider.links)
        if provider.name:
            self._helpers.write_istring(el, provider.name)
        if provider.description:
            self._helpers.write_istring(el, provider.description, "Description")
        for contact in provider.contacts:
            self._write_contact(el, contact)

    def _write_data_provider_scheme(self, parent: etree._Element, scheme: DataProviderScheme) -> None:
        """SDMX 3.0 DataProviderScheme: fixed id="DATA_PROVIDERS", never versioned."""
        el = etree.SubElement(parent, self._s("DataProviderScheme"))
        el.set("agencyID", scheme.agency_id)
        el.set("id", scheme.id)
        self._helpers.write_uri(el, scheme)
        self._helpers.write_reference_attrs(el, scheme)
        self._write_validity(el, scheme)
        self._helpers.write_annotations(el, scheme.annotations)
        self._helpers.write_links(el, scheme.links)
        if scheme.name:
            self._helpers.write_istring(el, scheme.name)
        if scheme.description:
            self._helpers.write_istring(el, scheme.description, "Description")
        for provider in scheme.providers:
            self._write_data_provider(el, provider)

    def _write_metadata_provider(self, parent: etree._Element, provider: MetadataProvider) -> None:
        el = etree.SubElement(parent, self._s("MetadataProvider"))
        el.set("id", provider.id)
        self._helpers.write_uri(el, provider)
        self._helpers.write_annotations(el, provider.annotations)
        self._helpers.write_links(el, provider.links)
        if provider.name:
            self._helpers.write_istring(el, provider.name)
        if provider.description:
            self._helpers.write_istring(el, provider.description, "Description")
        for contact in provider.contacts:
            self._write_contact(el, contact)

    def _write_metadata_provider_scheme(self, parent: etree._Element, scheme: MetadataProviderScheme) -> None:
        """The metadata twin of `_write_data_provider_scheme` (#315)."""
        el = etree.SubElement(parent, self._s("MetadataProviderScheme"))
        el.set("agencyID", scheme.agency_id)
        el.set("id", scheme.id)
        self._helpers.write_uri(el, scheme)
        self._helpers.write_reference_attrs(el, scheme)
        self._write_validity(el, scheme)
        self._helpers.write_annotations(el, scheme.annotations)
        self._helpers.write_links(el, scheme.links)
        if scheme.name:
            self._helpers.write_istring(el, scheme.name)
        if scheme.description:
            self._helpers.write_istring(el, scheme.description, "Description")
        for provider in scheme.providers:
            self._write_metadata_provider(el, provider)

    def _write_organisation(self, parent: etree._Element, tag: str, item: DataConsumer | OrganisationUnit) -> None:
        """One organisation item, a unit's `<Parent>` text before its contacts as the schema orders them (#469)."""
        el = etree.SubElement(parent, self._s(tag))
        el.set("id", item.id)
        self._helpers.write_uri(el, item)
        self._helpers.write_annotations(el, item.annotations)
        self._helpers.write_links(el, item.links)
        self._helpers.write_istring(el, item.name)
        if item.description:
            self._helpers.write_istring(el, item.description, "Description")
        if isinstance(item, OrganisationUnit) and item.parent_id is not None:
            etree.SubElement(el, self._s("Parent")).text = item.parent_id
        for contact in item.contacts:
            self._write_contact(el, contact)

    def _write_organisation_scheme(
        self, parent: etree._Element, tag: str, scheme: DataConsumerScheme | OrganisationUnitScheme
    ) -> None:
        """A data consumer or organisation unit scheme: never versioned, as every 3.x organisation scheme (#469)."""
        el = etree.SubElement(parent, self._s(tag))
        el.set("agencyID", scheme.agency_id)
        el.set("id", scheme.id)
        self._helpers.write_uri(el, scheme)
        self._helpers.write_reference_attrs(el, scheme)
        self._write_validity(el, scheme)
        self._helpers.write_annotations(el, scheme.annotations)
        self._helpers.write_links(el, scheme.links)
        self._helpers.write_istring(el, scheme.name)
        if scheme.description:
            self._helpers.write_istring(el, scheme.description, "Description")
        if isinstance(scheme, DataConsumerScheme):
            for consumer in scheme.consumers:
                self._write_organisation(el, "DataConsumer", consumer)
        else:
            for unit in scheme.organisation_units:
                self._write_organisation(el, "OrganisationUnit", unit)

    def _write_data_consumer_scheme(self, parent: etree._Element, scheme: DataConsumerScheme) -> None:
        self._write_organisation_scheme(parent, "DataConsumerScheme", scheme)

    def _write_organisation_unit_scheme(self, parent: etree._Element, scheme: OrganisationUnitScheme) -> None:
        self._write_organisation_scheme(parent, "OrganisationUnitScheme", scheme)

    def _write_dimension(self, parent: etree._Element, dim: Dimension, position: int) -> None:
        el = etree.SubElement(parent, self._s("Dimension"))
        el.set("id", dim.id)
        self._helpers.write_uri(el, dim)
        el.set("position", str(position))
        self._helpers.write_annotations(el, dim.annotations)
        self._helpers.write_links(el, dim.links)
        if isinstance(dim.concept, Ref):
            self._write_concept_identity(el, dim.concept)
        if isinstance(dim.representation, (Ref, Facet)):
            self._write_representation(el, dim.representation, enumeration_format=dim.enumeration_format)
        self._write_concept_roles(el, dim.concept_role)

    def _write_time_dimension(self, parent: etree._Element, td: TimeDimension) -> None:
        el = etree.SubElement(parent, self._s("TimeDimension"))
        el.set("id", td.id)
        self._helpers.write_uri(el, td)
        self._helpers.write_annotations(el, td.annotations)
        self._helpers.write_links(el, td.links)
        if isinstance(td.concept, Ref):
            self._write_concept_identity(el, td.concept)
        if td.representation is not None:
            local_rep = etree.SubElement(el, self._s("LocalRepresentation"))
            self._helpers.write_text_format(local_rep, td.representation)

    def _write_attribute_relationship(self, parent: etree._Element, attr: Attribute | MetadataAttributeUsage) -> None:
        """SDMX 3.0 AttributeRelationship — Observation (not ObservationValue).

        SDMX 3.0 dropped the 2.1 <None/> "no relationship" form. Dataset-level
        attachment now means "varies per dataflow" and is emitted as <Dataflow/>.
        """
        ar = etree.SubElement(parent, self._s("AttributeRelationship"))
        match attr.attachment:
            case AttachmentLevel.OBSERVATION:
                etree.SubElement(ar, self._s("Observation"))
            case AttachmentLevel.SERIES:
                # SDMX 3.0: Dimension is a simple-typed element with the
                # dimension id as text content (no nested Ref).
                for dim_id in attr.related_dimensions or []:
                    dim_el = etree.SubElement(ar, self._s("Dimension"))
                    dim_el.text = dim_id
                    if dim_id in attr.optional_dimensions:
                        dim_el.set("optional", "true")
            case AttachmentLevel.GROUP:
                # Per the v3.0 XSD, <Group> is IDType — the group's id as text
                # content. Empty without a related_group means the model is
                # incomplete; emit an empty element so the conformance error
                # surfaces at XSD-validation time rather than silently passing.
                etree.SubElement(ar, self._s("Group")).text = attr.related_group
            case _:
                # DATASET / None → Dataflow in 3.0.
                etree.SubElement(ar, self._s("Dataflow"))

    def _write_attribute(self, parent: etree._Element, attr: Attribute) -> None:
        el = etree.SubElement(parent, self._s("Attribute"))
        el.set("id", attr.id)
        self._helpers.write_uri(el, attr)
        # Optional is the schema's default, and left out (#503).
        if attr.usage is not AttributeUsage.OPTIONAL:
            el.set("usage", attr.usage.value)
        self._helpers.write_annotations(el, attr.annotations)
        self._helpers.write_links(el, attr.links)
        if isinstance(attr.concept, Ref):
            self._write_concept_identity(el, attr.concept)
        if isinstance(attr.representation, (Ref, Facet)):
            self._write_representation(
                el,
                attr.representation,
                enumeration_format=attr.enumeration_format,
                occurs=(attr.min_occurs, attr.max_occurs),
            )
        self._write_concept_roles(el, attr.concept_role)
        self._write_attribute_relationship(el, attr)
        if attr.related_measures is not None:
            relationship = etree.SubElement(el, self._s("MeasureRelationship"))
            for measure_id in attr.related_measures:
                etree.SubElement(relationship, self._s("Measure")).text = measure_id

    def _write_metadata_attribute_usage(self, parent: etree._Element, usage: MetadataAttributeUsage) -> None:
        """A `<str:MetadataAttributeUsage>` (#422).

        No `@urn`: the 3.0 XSD's pattern for it admits only a
        VtlMappingScheme, an erratum, so a written one is invalid.
        """
        el = etree.SubElement(parent, self._s("MetadataAttributeUsage"))
        self._helpers.write_uri(el, usage)
        self._helpers.write_annotations(el, usage.annotations)
        self._helpers.write_links(el, usage.links)
        etree.SubElement(el, self._s("MetadataAttributeReference")).text = usage.id
        self._write_attribute_relationship(el, usage)

    def _write_measure(self, parent: etree._Element, measure: Measure) -> None:
        """Write a single measure as <str:Measure> (SDMX 3.0 replaces PrimaryMeasure)."""
        el = etree.SubElement(parent, self._s("Measure"))
        el.set("id", measure.id)
        self._helpers.write_uri(el, measure)
        if measure.usage is not AttributeUsage.OPTIONAL:
            el.set("usage", measure.usage.value)
        self._helpers.write_annotations(el, measure.annotations)
        self._helpers.write_links(el, measure.links)
        if isinstance(measure.concept, Ref):
            self._write_concept_identity(el, measure.concept)
        if isinstance(measure.representation, (Ref, Facet)):
            self._write_representation(
                el,
                measure.representation,
                enumeration_format=measure.enumeration_format,
                occurs=(measure.min_occurs, measure.max_occurs),
            )
        self._write_concept_roles(el, measure.concept_role)

    def _write_group(self, parent: etree._Element, group: Group) -> None:
        """SDMX 3.0 Group: GroupDimension contains <DimensionReference>id</...>.

        An earlier cleanup-phase comment claimed 3.0 used a `dimensionRef=`
        attribute; the actual schema requires a `<DimensionReference>` child
        element with the dim id as text content (verified against
        SDMXStructureDataStructure.xsd@v3.0.0).
        """
        el = etree.SubElement(parent, self._s("Group"))
        el.set("id", group.id)
        self._helpers.write_uri(el, group)
        self._helpers.write_annotations(el, group.annotations)
        for dim_id in group.dimension_ids:
            gd = etree.SubElement(el, self._s("GroupDimension"))
            etree.SubElement(gd, self._s("DimensionReference")).text = dim_id

    def _write_data_structure(self, parent: etree._Element, dsd: DataStructure) -> None:
        el = etree.SubElement(parent, self._s("DataStructure"))
        self._write_maintainable_attrs(el, dsd)
        if self._gen.evolving_structure and dsd.evolving_structure:
            el.set("evolvingStructure", "true")
        self._helpers.write_annotations(el, dsd.annotations)
        self._helpers.write_links(el, dsd.links)
        self._helpers.write_istring(el, dsd.name)
        if dsd.description:
            self._helpers.write_istring(el, dsd.description, "Description")

        self._write_dsd_components(etree.SubElement(el, self._s("DataStructureComponents")), dsd)

        # `DataStructureType` puts it after the components (#422).
        if dsd.metadata is not None:
            self._write_urn_text(el, "Metadata", dsd.metadata)

    def _write_dsd_components(self, components: etree._Element, dsd: DataStructure) -> None:
        dim_list = etree.SubElement(components, self._s("DimensionList"))
        dim_list.set("id", "DimensionDescriptor")
        self._set_uri(dim_list, dsd.dimension_descriptor_uri)
        self._helpers.write_annotations(dim_list, dsd.dimension_descriptor_annotations)
        self._helpers.write_links(dim_list, dsd.dimension_descriptor_links)
        for i, dim in enumerate(dsd.dimensions, start=1):
            self._write_dimension(dim_list, dim, i)
        if dsd.time_dimension is not None:
            self._write_time_dimension(dim_list, dsd.time_dimension)

        if dsd.groups:
            for group in dsd.groups:
                self._write_group(components, group)

        if dsd.attributes or dsd.metadata_attributes:
            attr_list = etree.SubElement(components, self._s("AttributeList"))
            attr_list.set("id", "AttributeDescriptor")
            self._set_uri(attr_list, dsd.attribute_descriptor_uri)
            self._helpers.write_annotations(attr_list, dsd.attribute_descriptor_annotations)
            self._helpers.write_links(attr_list, dsd.attribute_descriptor_links)
            for attr in dsd.attributes:
                self._write_attribute(attr_list, attr)
            for usage in dsd.metadata_attributes:
                self._write_metadata_attribute_usage(attr_list, usage)

        if dsd.measures:
            measure_list = etree.SubElement(components, self._s("MeasureList"))
            measure_list.set("id", "MeasureDescriptor")
            self._set_uri(measure_list, dsd.measure_descriptor_uri)
            self._helpers.write_annotations(measure_list, dsd.measure_descriptor_annotations)
            self._helpers.write_links(measure_list, dsd.measure_descriptor_links)
            for measure in dsd.measures:
                self._write_measure(measure_list, measure)

    # ── Dataflows ─────────────────────────────────────────────────────────────────

    def _write_cube_region(self, parent: etree._Element, region: CubeRegion) -> None:
        """SDMX 3.0 CubeRegion: KeyValue lives in the str: namespace (was com: in 2.1)."""
        el = etree.SubElement(parent, self._s("CubeRegion"))
        if not region.include:
            el.set("include", "false")
        self._helpers.write_annotations(el, region.annotations)
        # `KeyValue*` then `Component*`; values and time ranges are `str:` too.
        for kv in region.keys:
            self._helpers.write_selection(el, self._s, "KeyValue", kv)
        for component in region.components:
            self._helpers.write_selection(el, self._s, "Component", component)

    def _data_constraints_for_3x(self, msg: StructureMessage) -> list[DataConstraint]:
        """Every constraint this target writes, including converted 2.1 ones.

        SDMX 3.0 unified 2.1's two constraint artefacts into one, so an
        `AttachmentConstraint` is written as the `DataConstraint` it is equivalent
        to rather than refused. Everything it carries — attachment, key set,
        identity, validity — is a `DataConstraint` field (#244).
        """
        converted = [*msg.constraints, *(ac.to_data_constraint() for ac in msg.attachment_constraints)]
        if not self._gen.availability_constraint:
            return converted
        return [dc for dc in converted if not _states_availability(dc)]

    def _availability_constraints(self, msg: StructureMessage) -> list[AvailabilityConstraint]:
        """3.1's availability: those read as such, and each Actual constraint that fits (#421)."""
        if not self._gen.availability_constraint:
            return []
        return [
            *msg.availability_constraints,
            *(dc.to_availability_constraint() for dc in msg.constraints if _states_availability(dc)),
        ]

    def _write_availability_constraint(self, parent: etree._Element, ac: AvailabilityConstraint) -> None:
        """SDMX 3.1 ``AvailabilityConstraintType``: annotable, with no identity of its own.

        Annotations, then the one attachment, then the one cube region; the
        counts are attributes.
        """
        el = etree.SubElement(parent, self._s("AvailabilityConstraint"))
        if ac.series_count is not None:
            el.set("seriesCount", str(ac.series_count))
        if ac.obs_count is not None:
            el.set("obsCount", str(ac.obs_count))
        self._helpers.write_annotations(el, ac.annotations)
        self._helpers.write_constraint_attachment(el, ac.attachment)
        self._write_cube_region(el, ac.region)

    def _write_data_constraint(self, parent: etree._Element, cc: DataConstraint) -> None:
        """SDMX 3.0: element name DataConstraint; `role` attribute (was `type` in 2.1).

        The model field is still called ``constraint_type`` for source
        compatibility; we map it onto the 3.0 ``role`` attribute here.
        """
        el = etree.SubElement(parent, self._s("DataConstraint"))
        self._write_maintainable_attrs(el, cc)
        if self._gen.data_constraint_role:
            el.set("role", cc.constraint_type.value)
        self._helpers.write_annotations(el, cc.annotations)
        self._helpers.write_links(el, cc.links)
        self._helpers.write_istring(el, cc.name)
        if cc.description:
            self._helpers.write_istring(el, cc.description, "Description")
        self._helpers.write_constraint_attachment(el, cc.attachment)
        # 3.0 sequence (per DataConstraintBaseType + DataConstraintType): ...
        # ConstraintAttachment → ReleaseCalendar → (DataKeySet | CubeRegion)*.
        # ReleaseCalendar comes BEFORE the cube regions (it lives in the base
        # type, regions in the extension).
        if self._gen.release_calendar and cc.release_calendar is not None:
            rc_el = etree.SubElement(el, self._s("ReleaseCalendar"))
            etree.SubElement(rc_el, self._s("Periodicity")).text = cc.release_calendar.periodicity
            etree.SubElement(rc_el, self._s("Offset")).text = cc.release_calendar.offset
            etree.SubElement(rc_el, self._s("Tolerance")).text = cc.release_calendar.tolerance
        # DataKeySet precedes CubeRegion in DataConstraintType's sequence.
        self._helpers.write_data_key_sets(el, cc.data_key_sets)
        for region in cc.regions:
            self._write_cube_region(el, region)
        # 3.0 dropped ReferencePeriod entirely from DataConstraint — the model
        # field is preserved on the in-memory artefact (e.g. when read from a
        # 2.1 source) but the 3.0 writer must not emit it. Silent drop.

    # ── Hierarchies ──────────────────────────────────────────────────────────────

    def _write_hierarchical_code(self, parent: etree._Element, hc: HierarchicalCode) -> None:
        """Emit a v3.0 / 3.1 ``<str:HierarchicalCode>`` per HierarchicalCodeType.

        Per the v3.0+ XSD: only Annotations + ``<Code>`` (URN-text ref) +
        optional nested ``<HierarchicalCode>`` + optional ``<Level>`` (text
        id reference) are allowed on the element. Name and Description are
        NOT permitted — they live on the underlying Codelist item.

        Per-node ``validFrom`` / ``validTo`` (``xs:dateTime``) attributes
        are allowed on the element and round-trip.
        """
        el = etree.SubElement(parent, self._s("HierarchicalCode"))
        el.set("id", hc.id)
        self._helpers.write_uri(el, hc)
        if hc.valid_from is not None:
            el.set("validFrom", hc.valid_from.isoformat())
        if hc.valid_to is not None:
            el.set("validTo", hc.valid_to.isoformat())
        self._helpers.write_annotations(el, hc.annotations)
        self._helpers.write_links(el, hc.links)
        if hc.code is not None:
            self._write_urn_text(el, "Code", hc.code)
        for child in hc.children:
            self._write_hierarchical_code(el, child)
        # SDMX 3.1: optional <Level> is text containing the level id. Per the
        # XSD sequence it comes AFTER child HierarchicalCode entries.
        if hc.level is not None:
            etree.SubElement(el, self._s("Level")).text = hc.level.id

    def _write_coding_format(self, parent: etree._Element, facet: Facet) -> None:
        """Emit a v3.0 ``<str:CodingFormat>`` (CodingTextFormatType) — see `coding_format_attrs`."""
        cf = etree.SubElement(parent, self._s("CodingFormat"))
        for name, value in coding_format_attrs(facet).items():
            cf.set(name, value)

    def _write_hierarchy(self, parent: etree._Element, hierarchy: Hierarchy) -> None:
        el = etree.SubElement(parent, self._s("Hierarchy"))
        self._write_maintainable_attrs(el, hierarchy)
        self._helpers.write_annotations(el, hierarchy.annotations)
        self._helpers.write_links(el, hierarchy.links)
        # SDMX 3.1 made hasFormalLevels a required attribute on HierarchyType.
        # 3.0 also defines it; emit on both for forward-compat.
        el.set("hasFormalLevels", "true" if hierarchy.has_formal_levels else "false")
        self._helpers.write_istring(el, hierarchy.name)
        if hierarchy.description:
            self._helpers.write_istring(el, hierarchy.description, "Description")
        # A chain, not siblings: a hierarchy holds one `Level`, and each level
        # holds the next one down. Siblings were XSD-invalid past one level
        # (#422), as the 2.1 writer's `_write_level_chain` already knew.
        holder = el
        for lv in hierarchy.levels:
            lv_el = etree.SubElement(holder, self._s("Level"))
            holder = lv_el
            lv_el.set("id", lv.id)
            self._helpers.write_uri(lv_el, lv)
            self._helpers.write_annotations(lv_el, lv.annotations)
            self._helpers.write_links(lv_el, lv.links)
            if lv.name:
                self._helpers.write_istring(lv_el, lv.name)
            if lv.description:
                self._helpers.write_istring(lv_el, lv.description, "Description")
            if lv.coding_format is not None:
                self._write_coding_format(lv_el, lv.coding_format)
        for hc in hierarchy.tree:
            self._write_hierarchical_code(el, hc)

    def _write_hierarchy_association(self, parent: etree._Element, ha: HierarchyAssociation) -> None:
        """Emit a v3.0 / 3.1 ``<str:HierarchyAssociation>``.

        Per ``HierarchyAssociationType``: MaintainableType attrs (no isFinal),
        then Name / Description, then required ``<LinkedHierarchy>``,
        required ``<LinkedObject>``, optional ``<ContextObject>`` — all three
        URN-text references.
        """
        el = etree.SubElement(parent, self._s("HierarchyAssociation"))
        el.set("agencyID", ha.agency_id)
        el.set("id", ha.id)
        self._helpers.write_uri(el, ha)
        el.set("version", ha.version)
        self._helpers.write_reference_attrs(el, ha)
        self._write_validity(el, ha)
        self._helpers.write_annotations(el, ha.annotations)
        self._helpers.write_links(el, ha.links)
        self._helpers.write_istring(el, ha.name)
        if ha.description:
            self._helpers.write_istring(el, ha.description, "Description")
        if ha.linked_hierarchy is not None:
            self._write_urn_text(el, "LinkedHierarchy", ha.linked_hierarchy)
        if ha.linked_object is not None:
            self._write_urn_text(el, "LinkedObject", ha.linked_object)
        if ha.context_object is not None:
            self._write_urn_text(el, "ContextObject", ha.context_object)

    # ── MetadataStructures (MSD) ──────────────────────────────────────────────────

    def _write_metadata_attribute(self, parent: etree._Element, attr: MetadataAttribute) -> None:
        """Emit a v3.0 ``<str:MetadataAttribute>`` per MetadataAttributeBaseType.

        Sequence: ConceptIdentity (URN-text, required) → LocalRepresentation
        (optional) → nested MetadataAttribute children. Attributes: id,
        minOccurs (default 1), maxOccurs (default 1), isPresentational.
        """
        el = etree.SubElement(parent, self._s("MetadataAttribute"))
        el.set("id", attr.id)
        self._helpers.write_uri(el, attr)
        # XSD default for minOccurs is 1 — omit when it matches.
        if attr.min_occurs != 1:
            el.set("minOccurs", str(attr.min_occurs))
        if attr.max_occurs != 1:
            el.set("maxOccurs", "unbounded" if attr.max_occurs is None else str(attr.max_occurs))
        if attr.is_presentational:
            el.set("isPresentational", "true")
        self._helpers.write_annotations(el, attr.annotations)
        self._helpers.write_links(el, attr.links)
        if isinstance(attr.concept, Ref):
            self._write_concept_identity(el, attr.concept)
        if isinstance(attr.representation, (Ref, Facet)):
            self._write_representation(el, attr.representation, enumeration_format=attr.enumeration_format)
        for child in attr.children:
            self._write_metadata_attribute(el, child)

    def _write_metadata_structure(self, parent: etree._Element, msd: MetadataStructure) -> None:
        """Emit a v3.0 ``<str:MetadataStructure>``.

        Per the v3.0 XSD: MaintainableType attrs (agencyID, id, version — no
        isFinal in 3.0), then Annotations → Name → Description → optional
        MetadataStructureComponents → MetadataAttributeList.
        """
        el = etree.SubElement(parent, self._s("MetadataStructure"))
        el.set("agencyID", msd.agency_id)
        el.set("id", msd.id)
        self._helpers.write_uri(el, msd)
        el.set("version", msd.version)
        self._helpers.write_reference_attrs(el, msd)
        self._write_validity(el, msd)
        self._helpers.write_annotations(el, msd.annotations)
        self._helpers.write_links(el, msd.links)
        self._helpers.write_istring(el, msd.name)
        if msd.description:
            self._helpers.write_istring(el, msd.description, "Description")

        if msd.attributes:
            components = etree.SubElement(el, self._s("MetadataStructureComponents"))
            attr_list = etree.SubElement(components, self._s("MetadataAttributeList"))
            attr_list.set("id", "MetadataAttributeDescriptor")
            self._set_uri(attr_list, msd.attribute_list_uri)
            self._helpers.write_annotations(attr_list, msd.attribute_list_annotations)
            self._helpers.write_links(attr_list, msd.attribute_list_links)
            for attr in msd.attributes:
                self._write_metadata_attribute(attr_list, attr)

    # ── Metadataflows ─────────────────────────────────────────────────────────────

    def _write_metadataflow(self, parent: etree._Element, mdf: Metadataflow) -> None:
        """Emit a v3.0 ``<str:Metadataflow>``.

        Per MetadataflowType: MaintainableType attrs (no isFinal in 3.0),
        Annotations → Name → Description → Structure (URN-text, optional) →
        Target (URN-text, one or more required).
        """
        el = etree.SubElement(parent, self._s("Metadataflow"))
        el.set("agencyID", mdf.agency_id)
        el.set("id", mdf.id)
        self._helpers.write_uri(el, mdf)
        el.set("version", mdf.version)
        self._helpers.write_reference_attrs(el, mdf)
        self._write_validity(el, mdf)
        self._helpers.write_annotations(el, mdf.annotations)
        self._helpers.write_links(el, mdf.links)
        self._helpers.write_istring(el, mdf.name)
        if mdf.description:
            self._helpers.write_istring(el, mdf.description, "Description")
        if isinstance(mdf.structure, Ref):
            self._write_urn_text(el, "Structure", mdf.structure)
        for target_urn in mdf.targets:
            etree.SubElement(el, self._s("Target")).text = target_urn

    # ── Entry point ───────────────────────────────────────────────────────────────

    # Table-driven dispatch for the top-level self.write(): per container, the list
    # of (StructureMessage attribute, per-item writer) pairs. SDMX-ML 3.0 split
    # the unified 2.1 OrganisationSchemes container into separate AgencySchemes
    # and DataProviderSchemes containers (per SDMXStructure.xsd v3.0). The 3.0
    # writer must emit the split form for the output to validate against the
    # v3.0 schema. SDMX-ML 3.0 also adds MetadataStructures and Metadataflows,
    # and renames HierarchicalCodelists to Hierarchies.
    def _build_sections(self) -> list[tuple[str, list[SectionEntry]]]:
        """The message's container order, with this writer's methods bound in.

        A method rather than the module-level table it was, because the entries
        name writer functions and those are now methods — a class-body list
        would hold them unbound. Built once in `__init__`; the order is the
        3.0/3.1 schema's and is identical between the two, which
        `test_ml3_fork_drift` measured before the merge.
        """
        return [
            ("AgencySchemes", [section_entry(lambda m: m.agency_schemes, self._write_agency_scheme)]),
            (
                "DataProviderSchemes",
                [section_entry(lambda m: m.data_provider_schemes, self._write_data_provider_scheme)],
            ),
            (
                "MetadataProviderSchemes",
                [section_entry(lambda m: m.metadata_provider_schemes, self._write_metadata_provider_scheme)],
            ),
            (
                "DataConsumerSchemes",
                [section_entry(lambda m: m.data_consumer_schemes, self._write_data_consumer_scheme)],
            ),
            (
                "OrganisationUnitSchemes",
                [section_entry(lambda m: m.organisation_unit_schemes, self._write_organisation_unit_scheme)],
            ),
            # A geographic codelist is a codelist with a container of its own.
            ("Codelists", [section_entry(_plain_codelists, self._write_codelist)]),
            (
                "GeographicCodelists",
                [section_entry(lambda m: _codelists_of(m, GeographicCodelist), self._write_codelist)],
            ),
            ("GeoGridCodelists", [section_entry(lambda m: _codelists_of(m, GeoGridCodelist), self._write_codelist)]),
            ("ValueLists", [section_entry(lambda m: m.value_lists, self._write_value_list)]),
            # SDMX 3.0 renamed the container element Concepts → ConceptSchemes
            # (consistent with the other plural-container names).
            ("ConceptSchemes", [section_entry(lambda m: m.concept_schemes, self._write_concept_scheme)]),
            ("CategorySchemes", [section_entry(lambda m: m.category_schemes, self._helpers.write_category_scheme)]),
            ("DataStructures", [section_entry(lambda m: m.data_structures, self._write_data_structure)]),
            ("Dataflows", [section_entry(lambda m: m.dataflows, self._write_dataflow)]),
            ("Categorisations", [section_entry(lambda m: m.categorisations, self._write_categorisation)]),
            ("ProvisionAgreements", [section_entry(lambda m: m.provision_agreements, self._write_provision_agreement)]),
            # SDMX 3.0: Constraints → DataConstraints (split from MetadataConstraints).
            ("DataConstraints", [section_entry(self._data_constraints_for_3x, self._write_data_constraint)]),
            (
                "AvailabilityConstraints",
                [section_entry(self._availability_constraints, self._write_availability_constraint)],
            ),
            # SDMX 3.0 replaced the unified 2.1 StructureSets container with one
            # top-level container per map kind. StructuresType is <xs:all> in 3.0,
            # so emission order between these isn't enforced.
            ("RepresentationMaps", [section_entry(lambda m: m.representation_maps, self._write_representation_map)]),
            ("StructureMaps", [section_entry(lambda m: m.structure_maps, self._write_structure_map)]),
            ("ConceptSchemeMaps", [section_entry(lambda m: m.concept_scheme_maps, self._write_concept_scheme_map)]),
            ("CategorySchemeMaps", [section_entry(lambda m: m.category_scheme_maps, self._write_category_scheme_map)]),
            (
                "OrganisationSchemeMaps",
                [section_entry(lambda m: m.organisation_scheme_maps, self._write_organisation_scheme_map)],
            ),
            ("Hierarchies", [section_entry(lambda m: m.hierarchies, self._write_hierarchy)]),
            (
                "HierarchyAssociations",
                [section_entry(lambda m: m.hierarchy_associations, self._write_hierarchy_association)],
            ),
            ("MetadataStructures", [section_entry(lambda m: m.metadata_structures, self._write_metadata_structure)]),
            ("Metadataflows", [section_entry(lambda m: m.metadataflows, self._write_metadataflow)]),
            (
                "MetadataProvisionAgreements",
                [section_entry(lambda m: m.metadata_provision_agreements, self._write_metadata_provision_agreement)],
            ),
        ]

    def _insert_metadata_links(self, el: etree._Element, set_urns: list[str], base_url: str) -> None:
        """Insert native SDMX 3.0 ``<com:Link rel="metadata">`` back-links.

        Per ``MaintainableBaseType`` the links sit after ``<com:Annotations>`` and
        before ``<com:Name>``; matches FMR's output (rel/url/urn, no ``type``).
        Appended after the links the artefact already holds, and a report one
        of those already points at is not linked twice (#422).
        """
        base = base_url.rstrip("/")
        held = [child for child in el if child.tag == self._c("Link")]
        linked = {child.get("urn") for child in held if child.get("rel") == "metadata"}
        at = (1 if len(el) and el[0].tag == self._c("Annotations") else 0) + len(held)
        for offset, set_urn in enumerate(u for u in set_urns if u not in linked):
            u = SdmxUrn.parse(set_urn)
            link = etree.Element(self._c("Link"))
            link.set("rel", "metadata")
            link.set("url", f"{base}/metadata/metadataset/{u.agency}/{u.id}/{u.version}")
            link.set("urn", set_urn)
            el.insert(at + offset, link)

    def write(
        self,
        msg: StructureMessage,
        *,
        pretty_print: bool = False,
        metadata_links: dict[str, list[str]] | None = None,
        base_url: str | None = None,
    ) -> bytes:
        """Serialise a StructureMessage to SDMX-ML 3.0 XML bytes.

        Args:
            msg: The StructureMessage to serialise.
            pretty_print: If True, indent the output.
            metadata_links: Optional ``artefact_urn -> [metadataset_urn, ...]`` map
                (from ``builders.metadata_annotation.build_metadata_target_links``).
                Emits a native SDMX 3.0 ``<com:Link rel="metadata">`` per report on
                each targeted maintainable — the FMR discovery mechanism (#191).
            base_url: REST service base used to build the link ``url``. Required
                when ``metadata_links`` is given.

        Example:
            from sdmxlib.formats.sdmx_ml30.writer import write

            xml_bytes = self.write(msg)
            xml_bytes = self.write(msg, pretty_print=True)  # indented output

        Raises:
            ValueError: If ``metadata_links`` is given without ``base_url``.
        """
        return self.write_kept(
            without_dropped(msg, self._gen.target),
            pretty_print=pretty_print,
            metadata_links=metadata_links,
            base_url=base_url,
        )

    def write_kept(
        self,
        msg: StructureMessage,
        *,
        pretty_print: bool = False,
        metadata_links: dict[str, list[str]] | None = None,
        base_url: str | None = None,
    ) -> bytes:
        """`write` for a message `without_dropped` has already narrowed to its SDMX-ML generation.

        `serialize` leaves out what the format cannot hold before it validates,
        then calls this: through `write`, the same walk ran twice, and it was half
        the cost of an SDMX-ML 2.1 DSD write (#464). Arguments as `write`.

        Raises:
            ValueError: If ``metadata_links`` is given without ``base_url``.
        """
        link_base: str | None = None
        if metadata_links:
            if base_url is None:
                msg_err = "base_url is required when metadata_links is provided"
                raise ValueError(msg_err)
            link_base = base_url
        # A metadataflow read from 2.1 is given the targets 3.x requires. What
        # the generation cannot hold at all, a HierarchicalCodelist or 3.0's
        # AvailabilityConstraint, `write` has already left out (#421).
        msg = with_derived_targets(msg)

        root = etree.Element(self._m("Structure"), nsmap=self._nsmap)

        write_header(root, msg.header, com=self._helpers.com, mes=self._helpers.mes)

        structures = etree.SubElement(root, self._m("Structures"))

        for container_tag, entries in self._sections:
            if not any(entry.get(msg) for entry in entries):
                continue
            container = etree.SubElement(structures, self._s(container_tag))
            for entry in entries:
                for item in entry.get(msg):
                    entry.write(container, item)
                    if metadata_links and link_base is not None and isinstance(item, MaintainableArtefact):
                        set_urns = metadata_links.get(str(artefact_urn(item)))
                        if set_urns:
                            self._insert_metadata_links(container[-1], set_urns, link_base)

        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=pretty_print)
