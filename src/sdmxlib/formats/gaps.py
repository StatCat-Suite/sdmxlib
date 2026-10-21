"""What a wire format cannot carry — asked before writing, not discovered after.

The model is a *union* of the SDMX 2.1 and 3.0 information models, because the
library reads and writes both. So some artefacts carry information no given
format can express, and writing one to that format has three possible
outcomes:

* **refused** — `write` raises, naming the fix. A 2.1 DataStructure with no
  measures does this, and so does an unrepresentable version (#319).
* **dropped** — the field is silently absent from the output. A 2.1
  `legacy_is_final` into 3.0, a metadataflow's targets into 2.1. Or the whole
  artefact: a draft into 2.1, which `dropped` names with its cascade (#424).
* **downgraded** — reshaped into something the format does have.

**Dropping is the one that burns an author.** A refusal is visible; a drop is
not, and the round-trip guard that knows about it lives in this project's test
suite rather than in the caller's. This module makes the drops answerable in
advance: `unrepresentable(artefact, "3.0")` returns what would be lost and
why, before anything is written.

**One table, two readers.** The entries below are the declaration;
`tests/unit/test_roundtrip_property_guard.py` consults them rather than
keeping its own list, and its `xfail(strict=True)` is what *proves* each one —
fix a gap, the case passes unexpectedly, the suite fails, and the entry has to
go. A second table would have been free to drift from the first, and the drift
would read as "no known gaps".

That guard's own keys are test-case names, so a gap affecting every
maintainable — finality — appeared once, under whichever case happened to
test it. Keyed by type here, with base classes allowed, it applies where it
actually applies.
"""

from collections.abc import Callable, Iterable

import attrs

from sdmxlib.model._items import owner_classes, scheme_urn_of
from sdmxlib.model.annotations import AnnotationURL
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.category import Categorisation, CategoryScheme
from sdmxlib.model.codelist import Code, Codelist, GeoFeatureSetCode, GeographicCodelist, GeoGridCode, GeoGridCodelist
from sdmxlib.model.collections import ArtefactList
from sdmxlib.model.concept import ConceptScheme
from sdmxlib.model.constraint import (
    AttachmentConstraint,
    AvailabilityConstraint,
    ConstraintType,
    DataConstraint,
    RegionValue,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import AttributeUsage, DataStructure
from sdmxlib.model.header import Header, MetadataHeader
from sdmxlib.model.hierarchy import HierarchicalCodelist, Hierarchy, HierarchyAssociation
from sdmxlib.model.mapping import (
    CategorySchemeMap,
    ConceptSchemeMap,
    OrganisationSchemeMap,
    RepresentationMap,
    StructureMap,
    StructureSet,
)
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.organisation import (
    AgencyScheme,
    DataConsumerScheme,
    DataProviderScheme,
    MetadataProviderScheme,
    OrganisationUnitScheme,
)
from sdmxlib.model.ref import Ref, artefact_urn
from sdmxlib.model.representation import Facet
from sdmxlib.model.validation import _PRIMARY_MEASURE_ID_21, _collect_refs
from sdmxlib.model.valuelist import ValueList
from sdmxlib.model.version import compliance

#: The targets a caller can name. `3.0` and `3.1` are listed separately
#: because they differ — 3.1 prohibits `Categorisation/@version` — even though
#: they share a version *grammar* (see `model.version.check_writable`, a
#: different axis of the same question).
#:
#: `json` is SDMX-JSON. It belongs here for the same reason the wire formats
#: do, and was missing until #358 — so an SDMX-JSON limit had nowhere to be
#: declared, and the one we had was emitted instead. One target for both 1.0
#: and 2.0: nothing declared here so far distinguishes them.
#:
#: `storage` is the DuckDB store, and belongs here for the same reason the
#: wire formats do: it is somewhere an artefact goes and comes back, and it
#: does not carry everything. It is not an SDMX-ML version, so `Gap.__str__`
#: names it without the prefix.
TARGETS = ("2.1", "3.0", "3.1", "json", "storage")


def describe(target: str) -> str:
    """`target` as prose, for a message.

    A function rather than an f-string at each site: two of them said
    "SDMX-ML storage", which is not a thing. One place to be wrong is better
    than two, and this one is checked.
    """
    if target == "storage":
        return "the DuckDB store"
    if target == "json":
        return "SDMX-JSON"
    return f"SDMX-ML {target}"


@attrs.frozen
class Gap:
    """One piece of information a target format cannot carry.

    Attributes:
        target: The format, one of `TARGETS`.
        field: Dotted path as a reader of the model would write it.
        reason: Why the format cannot carry it, in terms of the format.
    """

    target: str
    field: str
    reason: str

    def __str__(self) -> str:
        return f"{self.field} is not representable in {describe(self.target)}: {self.reason}"


@attrs.frozen
class _Entry:
    """A gap, plus how to tell whether *this* artefact actually hits it.

    The predicate matters: `legacy_is_final=None` loses nothing when written to
    3.0, because the version already says it. Reporting every artefact of
    a type as lossy would make the check noise, and noise is not consulted.
    """

    targets: tuple[str, ...]
    applies_to: type
    field: str
    reason: str
    carried: Callable[[object], bool]


def _has(attribute: str) -> Callable[[object], bool]:
    """True when the attribute holds something a format would have to carry."""

    def check(obj: object) -> bool:
        value: object = getattr(obj, attribute, None)
        return bool(value)

    return check


def _renamed_measure(obj: object) -> bool:
    """True when the one measure 2.1 can carry is named anything but its pinned id."""
    return isinstance(obj, DataStructure) and len(obj.measures) == 1 and obj.measures[0].id != _PRIMARY_MEASURE_ID_21


def _legacy_finality_disagrees(obj: object) -> bool:
    """True when a 2.1 `isFinal` says something the version does not.

    Not `_has`: `legacy_is_final=False` on a `1.0.0` is information — 2.1 said
    not final, the version says final — and `bool(False)` would call it empty.
    """
    legacy: object = getattr(obj, "legacy_is_final", None)
    return isinstance(obj, MaintainableArtefact) and legacy is not None and legacy != obj.is_final


def _any_map_has(attribute: str, collection: str = "maps") -> Callable[[object], bool]:
    def check(obj: object) -> bool:
        maps: Iterable[object] = getattr(obj, collection, ())
        return any(getattr(item, attribute, None) for item in maps)

    return check


def _unnameable_in_21(obj: object) -> bool:
    """A component map's value map 2.1 cannot name from the structure map's set (#422).

    A 2.1 ``<CodelistMap>`` reference is local — an id in the same structure
    set — and a reader puts a set's maps under its agency and version, so a
    `RepresentationMap` of the structure map's agency and version can be
    named, and so can a value map a 2.1 reader built, which shares them. An
    epoch or date-pattern map, or a map under another agency or version,
    cannot.
    """
    agency: object = getattr(obj, "agency_id", None)
    version: object = getattr(obj, "version", None)
    maps: Iterable[object] = getattr(obj, "maps", ())
    for comp_map in maps:
        ref: object = getattr(comp_map, "representation_map", None)
        if not isinstance(ref, Ref):
            continue
        urn = ref.urn
        if urn.artefact_class != "RepresentationMap" or (urn.agency, urn.version) != (agency, version):
            return True
    return False


def _any_item_differs(collection: str, attribute: str, default: object) -> Callable[[object], bool]:
    """True when an item holds something other than `default` — `_has` for a falsy value that means something."""

    def check(obj: object) -> bool:
        items: Iterable[object] = getattr(obj, collection, ())
        return any(getattr(item, attribute, default) != default for item in items)

    return check


def _holds_code(kind: type[Code], *, unless: type[Codelist] | None) -> Callable[[object], bool]:
    """True when a codelist holds a `kind` of code and is not the `unless` kind that can carry it."""

    def check(obj: object) -> bool:
        if not isinstance(obj, Codelist) or (unless is not None and isinstance(obj, unless)):
            return False
        return any(isinstance(code, kind) for code in obj.codes)

    return check


def _any_selection(kind: str, test: Callable[[object], bool]) -> Callable[[object], bool]:
    """True when a region's `keys` or `components` holds a value set passing `test`."""

    def check(obj: object) -> bool:
        regions: Iterable[object] = getattr(obj, "regions", ())
        return any(test(selection) for region in regions for selection in getattr(region, kind, ()))

    return check


def _any_value(test: Callable[[RegionValue], bool]) -> Callable[[object], bool]:
    def check(selection: object) -> bool:
        values: Iterable[RegionValue] = getattr(selection, "values", ())
        return any(test(value) for value in values)

    return check


def _any_item_has(collection: str, attribute: str) -> Callable[[object], bool]:
    def check(obj: object) -> bool:
        items: Iterable[object] = getattr(obj, collection, ())
        return any(getattr(item, attribute, None) for item in items)

    return check


def _any_at(path: str, attribute: str) -> Callable[[object], bool]:
    """True when a part reachable at *path* carries *attribute*: `_any_item_has` below the top level."""

    def check(obj: object) -> bool:
        return any(getattr(part, attribute, None) for part in _reach(obj, path))

    return check


def _annotation_value_at(path: str) -> Callable[[object], bool]:
    """True when an annotation reachable at *path* carries a non-localised value."""
    return _annotation_at(path, lambda ann: bool(getattr(ann, "value", None)))


def _beyond_2_1_urls(ann: object) -> bool:
    """More URLs than 2.1's one, or a localised one — what a 2.1 write cannot keep (#422)."""
    urls: tuple[AnnotationURL, ...] = getattr(ann, "urls", ())
    return len(urls) > 1 or any(u.lang is not None for u in urls)


def _url_has(attribute: str) -> Callable[[object], bool]:
    """True when one of an annotation's URLs carries `attribute` — SDMX-JSON's `urn` or `type` (#422)."""

    def check(ann: object) -> bool:
        urls: tuple[AnnotationURL, ...] = getattr(ann, "urls", ())
        return any(getattr(u, attribute) is not None for u in urls)

    return check


def _annotation_at(path: str, test: Callable[[object], bool]) -> Callable[[object], bool]:
    """True when an annotation reachable at *path* passes *test*.

    *path* is the register's own spelling with the trailing
    ``annotations[].value`` removed — ``codes[]``, ``tree[].children[]``,
    ``time_dimension``, or ``""`` for the artefact's own annotations. A walk
    rather than 35 hand-written predicates, because `Annotation` hangs off
    nearly every artefact and item in the model: this is the one genuinely
    cross-cutting field in the table, and a per-site predicate would be 35
    chances to write the same walk slightly differently.
    """
    # A path to a host whose field is `annotations`, or — ending ``_`` — to
    # one whose field is named for what it annotates (`_ANNOTATION_SITES`).
    host_path, field_prefix = path.rpartition(".")[::2] if path.endswith("_") else (path, "")
    field = f"{field_prefix}annotations"

    def check(obj: object) -> bool:
        return any(test(ann) for host in _reach(obj, host_path) for ann in getattr(host, field, ()) or ())

    return check


def _sentinels_at(path: str) -> Callable[[object], bool]:
    """True when a text format reachable at *path* — a representation — holds sentinel values (#422)."""

    def check(obj: object) -> bool:
        return any(isinstance(facet, Facet) and facet.sentinel_values for facet in _reach(obj, path))

    return check


def _reach(start: object, path: str) -> list[object]:
    """Every object at *path* below *start*: ``a[].b`` walks each ``a``, then its ``b``."""
    objs: list[object] = [start]
    for step in (s for s in path.split(".") if s):
        attr, many = step.removesuffix("[]"), step.endswith("[]")
        reached: list[object] = []
        for obj in objs:
            value: object = getattr(obj, attr, None)
            if value is None:
                continue
            if many and isinstance(value, Iterable):
                reached.extend(value)
            elif not many:
                reached.append(value)
        objs = reached
    return objs


def _links_at(path: str) -> Callable[[object], bool]:
    """True when a `links` field at *path* — ``codes[].links``, ``dimension_descriptor_links`` — holds a link."""
    host_path, _, field = path.rpartition(".")

    def check(obj: object) -> bool:
        return any(getattr(host, field, ()) for host in _reach(obj, host_path))

    return check


def _link_languages_at(path: str) -> Callable[[object], bool]:
    """True when a link in the `links` field at *path* names its resource's language (#422)."""
    host_path, _, field = path.rpartition(".")

    def check(obj: object) -> bool:
        return any(
            getattr(link, "hreflang", None) is not None
            for host in _reach(obj, host_path)
            for link in getattr(host, field, ())
        )

    return check


def _any_node_has(attribute: str) -> Callable[[object], bool]:
    """True when any node in a hierarchy's tree carries `attribute`."""

    def check(obj: object) -> bool:
        def walk(nodes: Iterable[object]) -> bool:
            for node in nodes:
                if getattr(node, attribute, None):
                    return True
                if walk(getattr(node, "children", ()) or ()):
                    return True
            return False

        return walk(getattr(obj, "tree", ()) or ())

    return check


def _extended(version: str) -> bool:
    """Whether `version` carries an extension (`1.0.0-draft`), which only 3.x can write.

    The versions only 3.x accepts are exactly those: its numeric forms are a
    subset of 2.1's (`model.version.compliance`).
    """
    return compliance(version) == {"3.0"}


def _states_availability(obj: object) -> bool:
    """An Actual constraint SDMX-ML 3.1 writes as an `AvailabilityConstraint` (#421)."""
    return (
        isinstance(obj, DataConstraint)
        and obj.constraint_type is ConstraintType.ACTUAL
        and not obj.availability_misfits()
    )


def _lacks_targets(obj: object) -> bool:
    """A metadataflow whose 3.x targets are derived on write, having none of its own (#421)."""
    return isinstance(obj, Metadataflow) and not obj.targets


def _mandatory_measure(obj: object) -> bool:
    """A DSD with a mandatory measure, which 2.1 reads back optional, as 3.x defaults it (#421)."""
    return isinstance(obj, DataStructure) and any(m.usage is AttributeUsage.MANDATORY for m in obj.measures)


def _has_wildcard_member(obj: object) -> bool:
    """A codelist whose extension selects codes by a `%` pattern (#451)."""
    return isinstance(obj, Codelist) and any(
        "%" in member.value
        for extension in obj.extensions
        if extension.selection is not None
        for member in extension.selection.members
    )


def _is_draft(obj: object) -> bool:
    return isinstance(obj, MaintainableArtefact) and _extended(obj.version)


def _draft_refs(artefact: MaintainableArtefact) -> list[Ref[object]]:
    return [ref for _path, ref in _collect_refs(artefact, "") if not ref.urn.is_latest and _extended(ref.urn.version)]


def _references_a_draft(obj: object) -> bool:
    return isinstance(obj, MaintainableArtefact) and bool(_draft_refs(obj))


def _value_list_refs(artefact: MaintainableArtefact) -> list[Ref[object]]:
    """References naming a `ValueList`, which 2.1 has no class for (#422)."""
    return [ref for _path, ref in _collect_refs(artefact, "") if ref.urn.artefact_class == ValueList.sdmx_class]


def _references_a_value_list(obj: object) -> bool:
    return isinstance(obj, MaintainableArtefact) and bool(_value_list_refs(obj))


#: Every place an `Annotation` hangs, as the register spells it — the
#: artefact's own (``""``), each item collection beneath it, and the fields
#: holding the annotations of something unmodelled, which are named for it:
#: ``dimension_descriptor_`` for `DataStructure.dimension_descriptor_annotations`.
#: Each is the field's spelling with ``annotations`` cut off its end.
_ANNOTATION_SITES: tuple[tuple[type, str], ...] = (
    (MaintainableArtefact, ""),
    (AgencyScheme, "agencies[]."),
    (CategoryScheme, "categories[]."),
    (CategoryScheme, "categories[].categories[]."),
    (Codelist, "codes[]."),
    (ConceptScheme, "concepts[]."),
    (DataProviderScheme, "providers[]."),
    (DataConsumerScheme, "consumers[]."),
    (OrganisationUnitScheme, "organisation_units[]."),
    (DataStructure, "dimensions[]."),
    (DataStructure, "attributes[]."),
    (DataStructure, "measures[]."),
    (DataStructure, "measure_dimension."),
    (DataStructure, "time_dimension."),
    (DataStructure, "groups[]."),
    (DataStructure, "dimension_descriptor_"),
    (DataStructure, "attribute_descriptor_"),
    (DataStructure, "measure_descriptor_"),
    (Hierarchy, "levels[]."),
    (Hierarchy, "tree[]."),
    (Hierarchy, "tree[].children[]."),
    (Hierarchy, "tree[].level."),
    (Hierarchy, "tree[].children[].level."),
    (MetadataStructure, "attributes[]."),
    (MetadataStructure, "attributes[].children[]."),
    (MetadataStructure, "report_structures[]."),
    (MetadataStructure, "targets[]."),
    (MetadataStructure, "targets[].target_objects[]."),
    (RepresentationMap, "mappings[]."),
    (ConceptSchemeMap, "maps[]."),
    (CategorySchemeMap, "maps[]."),
    (OrganisationSchemeMap, "maps[]."),
    (StructureMap, "maps[]."),
)

#: `_ANNOTATION_SITES` and the places only SDMX 3.x annotates: a 2.1 write
#: loses those whole, so a gap in an annotation's parts is 3.x's to declare
#: there — an annotation URL's URN and type (#422).
_ANNOTATION_SITES_3X: tuple[tuple[type, str], ...] = (
    *_ANNOTATION_SITES,
    (DataConstraint, "regions[]."),
    (DataConstraint, "data_key_sets[].keys[]."),
    (DataStructure, "metadata_attributes[]."),
    (MetadataStructure, "attribute_list_"),
    (StructureMap, "date_pattern_maps[]."),
    (StructureMap, "epoch_maps[]."),
    (StructureMap, "fixed_value_maps[]."),
    (StructureMap, "frequency_format_mappings[]."),
    (ValueList, "items[]."),
)


#: Every `links` field in the model, by the path a reader of the model would
#: write (#422) — SDMX 3.x's ``<com:Link>``, which ``IdentifiableType`` carries
#: and 2.1's does not. `_ANNOTATION_SITES`' shape, for the same reason.
_LINK_SITES: tuple[tuple[type, str], ...] = (
    (MaintainableArtefact, "links"),
    (AgencyScheme, "agencies[].links"),
    (CategoryScheme, "categories[].links"),
    (CategoryScheme, "categories[].categories[].links"),
    (Codelist, "codes[].links"),
    (ConceptScheme, "concepts[].links"),
    (DataProviderScheme, "providers[].links"),
    (MetadataProviderScheme, "providers[].links"),
    (DataConsumerScheme, "consumers[].links"),
    (OrganisationUnitScheme, "organisation_units[].links"),
    (DataStructure, "dimensions[].links"),
    (DataStructure, "attributes[].links"),
    (DataStructure, "measures[].links"),
    (DataStructure, "time_dimension.links"),
    (DataStructure, "metadata_attributes[].links"),
    (DataStructure, "dimension_descriptor_links"),
    (DataStructure, "attribute_descriptor_links"),
    (DataStructure, "measure_descriptor_links"),
    (Hierarchy, "levels[].links"),
    (Hierarchy, "tree[].links"),
    (Hierarchy, "tree[].children[].links"),
    (Hierarchy, "tree[].level.links"),
    (Hierarchy, "tree[].children[].level.links"),
    (MetadataStructure, "attributes[].links"),
    (MetadataStructure, "attributes[].children[].links"),
    (MetadataStructure, "attribute_list_links"),
    (StructureMap, "epoch_maps[].links"),
    (StructureMap, "date_pattern_maps[].links"),
    (StructureMap, "frequency_format_mappings[].links"),
)


#: Every gap this library knows about, and the only place it is written down.
#:
#: Ordered by target then type so a reader can scan one format's losses. Each
#: `reason` names the *format's* constraint rather than the library's, because
#: the fix is always to write another format or drop the field — never to
#: change sdmxlib.
_ENTRIES: tuple[_Entry, ...] = (
    _Entry(
        targets=("3.0", "3.1", "json"),
        applies_to=MaintainableArtefact,
        field="legacy_is_final",
        # SDMX 3.0 dropped `isFinal` and reads finality from the version, and
        # so does the model (#420): `is_final` is derived, so it is carried by
        # every format the version is. What 3.0 cannot carry is a 2.1
        # `isFinal` that *disagrees* with the version — a `1.0` declared
        # final — which is all `legacy_is_final` ever holds (#424).
        #
        # SDMX-JSON belongs with them: it encodes the SDMX 3.0 information
        # model, so `isFinal` appears in no version of its structure schema,
        # and 2.1.0 closes every object with `unevaluatedProperties: false`
        # (#358).
        reason="SDMX 3.0 has no isFinal; finality is read from the version, and this one says otherwise",
        carried=_legacy_finality_disagrees,
    ),
    _Entry(
        targets=("3.0", "3.1", "json"),
        applies_to=DataStructure,
        field="measure_dimension",
        # Found by #354's field census: the 3.0/3.1 writers never read the
        # field, and this table said nothing was lost. 3.0 replaced the
        # measure dimension with a plain `MeasureList`, so there is nowhere for
        # it to go rather than somewhere we forgot to write.
        reason="SDMX 3.0 removed MeasureDimension in favour of an explicit MeasureList",
        carried=_has("measure_dimension"),
    ),
    _Entry(
        targets=("2.1",),
        applies_to=DataStructure,
        field="measures[].id",
        # Converted rather than refused (#421): IMF serves single-measure 3.0
        # DSDs named for what they measure, and the measure's concept and
        # representation survive. Only the id is lost — a 2.1 round trip
        # comes back as OBS_VALUE. Several measures are refused by `validate`.
        reason=f"SDMX-ML 2.1 pins the primary measure id to {_PRIMARY_MEASURE_ID_21}, so the writer renames it",
        carried=_renamed_measure,
    ),
    # A hierarchy and the maps have no validity dates or external reference
    # rows here: 2.1 nests each in a maintainable container, which carries
    # them, and the reader gives the container's back to each (#421). Until
    # then they were a dozen declared losses (#357).
    #
    # A draft is not a field 2.1 drops but an artefact it drops whole, and
    # `dropped(msg, "2.1")` names which, with the cascade a message implies.
    # Declared here too, so asking of one artefact answers as well (#424).
    _Entry(
        targets=("2.1",),
        applies_to=MaintainableArtefact,
        field="version",
        reason=(
            "2.1 has no form for a version extension (a draft), so the artefact is left out of 2.1 output, "
            "with whatever in the message references it"
        ),
        carried=_is_draft,
    ),
    _Entry(
        targets=("2.1",),
        applies_to=MaintainableArtefact,
        field="references",
        reason="2.1 cannot name a draft in a reference, so the artefact is left out of 2.1 output",
        carried=_references_a_draft,
    ),
    # A value list is SDMX 3.x's alone. Left out of 2.1 as a draft is, with
    # whatever names one — a component it enumerates has no 2.1 form (#422).
    _Entry(
        targets=("2.1",),
        applies_to=ValueList,
        field="sdmx_class",
        reason="2.1 has no ValueList, so it is left out of 2.1 output, with whatever in the message references it",
        carried=lambda _: True,
    ),
    _Entry(
        targets=("2.1",),
        applies_to=MaintainableArtefact,
        field="references",
        reason="2.1 cannot name a ValueList in a reference, so the artefact is left out of 2.1 output",
        carried=_references_a_value_list,
    ),
    # Each target's half of the hierarchy shapes (#421): 2.1 binds a
    # hierarchy to nothing, and 3.x holds none in a codelist.
    _Entry(
        targets=("2.1",),
        applies_to=HierarchyAssociation,
        field="sdmx_class",
        reason="2.1 has no HierarchyAssociation, so it is left out of 2.1 output",
        carried=lambda _: True,
    ),
    *(
        _Entry(
            targets=("3.0", "3.1", "json"),
            applies_to=cls,
            field="sdmx_class",
            reason=f"SDMX 3.0 removed the {cls.__name__}, so it is left out; its {members} are written without it",
            carried=lambda _: True,
        )
        for cls, members in ((HierarchicalCodelist, "hierarchies"), (StructureSet, "maps"))
    ),
    # SDMX 3.0's additions to a DSD's components (#422). 2.1's
    # `AttributeRelationshipType` has no measure relationship and no optional
    # dimension, its `RepresentationType` no occurrence bounds, and its
    # `DataStructureType` no MSD and no metadata attribute usage.
    _Entry(
        targets=("2.1",),
        applies_to=DataStructure,
        field="attributes[].related_measures",
        reason="2.1 has no MeasureRelationship; an attribute relates to the observation or to nothing",
        carried=_any_item_differs("attributes", "related_measures", None),
    ),
    _Entry(
        targets=("2.1",),
        applies_to=DataStructure,
        field="attributes[].optional_dimensions",
        reason="2.1's AttributeRelationship cannot mark a related dimension optional",
        carried=_any_item_has("attributes", "optional_dimensions"),
    ),
    *(
        _Entry(
            targets=("2.1",),
            applies_to=DataStructure,
            field=f"{collection}[].{bound}",
            reason="2.1 has no multi-valued components: a representation carries no minOccurs or maxOccurs",
            carried=_any_item_differs(collection, bound, 1),
        )
        for collection in ("attributes", "measures")
        for bound in ("min_occurs", "max_occurs")
    ),
    _Entry(
        targets=("2.1",),
        applies_to=DataStructure,
        field="metadata",
        reason="2.1's DataStructureType names no metadata structure",
        carried=_has("metadata"),
    ),
    _Entry(
        targets=("2.1",),
        applies_to=DataStructure,
        field="metadata_attributes",
        reason="2.1 has no MetadataAttributeUsage; a DSD reports data attributes only",
        carried=_has("metadata_attributes"),
    ),
    # A region value set's and value's 3.x qualifiers (#422). 2.1's
    # `ComponentValueSetType` has `include` and nothing else, and its
    # `SimpleValueType` a boolean `cascadeValues` and nothing else — so no
    # `removePrefix`, no validity, no `xml:lang`. (`excluderoot` is refused,
    # not dropped: `validation._check_cascade_in_2_1`.)
    *(
        _Entry(
            targets=("2.1",),
            applies_to=DataConstraint,
            field=f"regions[].{kind}[].remove_prefix",
            reason="2.1's value sets have no removePrefix",
            carried=_any_selection(kind, _has("remove_prefix")),
        )
        for kind in ("keys", "components")
    ),
    *(
        _Entry(
            targets=("2.1",),
            applies_to=DataConstraint,
            field=f"regions[].keys[].{bound}",
            reason="2.1's value sets have no validity period",
            carried=_any_selection("keys", _has(bound)),
        )
        for bound in ("valid_from", "valid_to")
    ),
    _Entry(
        targets=("2.1",),
        applies_to=DataConstraint,
        field="regions[].keys[].values",
        reason="2.1's region values have no validity period",
        carried=_any_selection("keys", _any_value(lambda v: v.valid_from is not None or v.valid_to is not None)),
    ),
    *(
        _Entry(
            targets=("2.1",),
            applies_to=DataConstraint,
            field=f"regions[].{kind}[].time_range.{bound}",
            reason="2.1's TimeRangeValueType has no validity period",
            carried=_any_selection(
                kind, lambda s, bound=bound: getattr(getattr(s, "time_range", None), bound, None) is not None
            ),
        )
        for kind in ("keys", "components")
        for bound in ("valid_from", "valid_to")
    ),
    _Entry(
        targets=("2.1",),
        applies_to=DataConstraint,
        field="regions[].components[].values",
        reason="2.1's region values have no language or validity period",
        carried=_any_selection(
            "components", _any_value(lambda v: v.lang is not None or v.valid_from is not None or v.valid_to is not None)
        ),
    ),
    _Entry(
        targets=("2.1",),
        applies_to=Codelist,
        field="extensions",
        # `CodelistType` in 2.1 is a restriction admitting only Annotations /
        # Name / Description / Code. Codelist extension is a 3.0 feature — a
        # codelist that derives from another by selecting and prefixing its
        # codes — and 2.1 has no element for it. The writer inlines the
        # inherited codes (#421), so what is lost is the extension itself: a
        # 2.1 round trip comes back holding the codes, extending nothing.
        reason=(
            "2.1 CodelistType admits only Code children; the inherited codes are written inline "
            "and the extension itself is lost"
        ),
        carried=_has("extensions"),
    ),
    _Entry(
        targets=("2.1",),
        applies_to=Codelist,
        field="codes",
        # The other half of the same conversion: what an extension inherits is
        # written as the codelist's own codes, so a 2.1 round trip comes back
        # with more codes than it was given and nothing saying which (#421).
        reason="2.1 has no codelist extension; the inherited codes are written, and read back, as the codelist's own",
        carried=_has("extensions"),
    ),
    # SDMX-JSON's MemberValueType is a plain id; SDMX-ML 3.x's may hold `%`.
    # A wildcard is written as the ids it matches when written (#451).
    _Entry(
        targets=("json",),
        applies_to=Codelist,
        field="extensions[].selection.members[].value",
        reason=(
            "SDMX-JSON's member value is a plain id: a `%` wildcard is written as the codes it matches now, "
            "so codes added to the extended codelist later are not selected"
        ),
        carried=_has_wildcard_member,
    ),
    # The geographic codelists are SDMX 3.x's. 2.1 writes each as the plain
    # codelist it is, so what is lost is what makes it geographic: its kind,
    # a grid's definition, and each code's feature set or grid cell.
    *(
        _Entry(
            targets=("2.1",),
            applies_to=cls,
            field="geo_type",
            reason=f"2.1 has no {cls.__name__}; it is written as a plain Codelist",
            carried=lambda _: True,
        )
        for cls in (GeographicCodelist, GeoGridCodelist)
    ),
    _Entry(
        targets=("2.1",),
        applies_to=GeoGridCodelist,
        field="grid_definition",
        reason="2.1 has no GeoGridCodelist, so nothing to hold its grid definition",
        carried=lambda _: True,
    ),
    # Any codelist may hold a geographic code — they are in `Code`'s XSD
    # substitution group — and 2.1 has neither. SDMX-JSON has no substitution
    # groups: only its geographic codelists' own keys hold one.
    _Entry(
        targets=("2.1",),
        applies_to=Codelist,
        field="codes[].value",
        reason="2.1 has no GeoFeatureSetCode; it is written as a plain Code",
        carried=_holds_code(GeoFeatureSetCode, unless=None),
    ),
    _Entry(
        targets=("2.1",),
        applies_to=Codelist,
        field="codes[].geo_cell",
        reason="2.1 has no GeoGridCode; it is written as a plain Code",
        carried=_holds_code(GeoGridCode, unless=None),
    ),
    _Entry(
        targets=("json",),
        applies_to=Codelist,
        field="codes[].value",
        reason="only an SDMX-JSON geographicCodelists entry holds a GeoFeatureSetCode",
        carried=_holds_code(GeoFeatureSetCode, unless=GeographicCodelist),
    ),
    _Entry(
        targets=("json",),
        applies_to=Codelist,
        field="codes[].geo_cell",
        reason="only an SDMX-JSON geoGridCodelists entry holds a GeoGridCode",
        carried=_holds_code(GeoGridCode, unless=GeoGridCodelist),
    ),
    _Entry(
        targets=("2.1",),
        applies_to=DataStructure,
        field="measures[].concept_role",
        # 2.1 has one measure, `PrimaryMeasureType`, and it is a restriction
        # admitting only Annotations / ConceptIdentity / LocalRepresentation.
        # A concept *role* on it has nowhere to go — 3.0's `MeasureType` added
        # `ConceptRole` when it allowed several measures.
        reason="2.1 PrimaryMeasureType has no ConceptRole; roles on measures arrived in SDMX 3.0",
        carried=_any_item_has("measures", "concept_role"),
    ),
    _Entry(
        targets=("2.1",),
        applies_to=Metadataflow,
        field="targets",
        reason="2.1 MetadataflowType has no Target element",
        carried=_has("targets"),
    ),
    # 2.1 writes a metadata provision agreement as it provisioned metadata
    # itself, a ProvisionAgreement over the metadataflow (#421), and that has
    # nowhere for the targets 3.x restricts the agreement to.
    _Entry(
        targets=("2.1",),
        applies_to=MetadataProvisionAgreement,
        field="targets",
        reason="2.1 writes a metadata provision agreement as a ProvisionAgreement, which has no Target element",
        carried=_has("targets"),
    ),
    # And the other way: 3.x requires the targets, which a flow read from 2.1
    # lacks, so they are derived from its metadata structure's
    # IdentifiableObjectTargets as wildcard URNs (#421) and read back as the
    # flow's own. A flow they cannot be derived for is refused by `validate`.
    _Entry(
        targets=("3.0", "3.1", "json"),
        applies_to=Metadataflow,
        field="targets",
        reason=(
            "3.x requires a metadataflow's targets, which 2.1 declares in the metadata structure instead: "
            "they are derived from its IdentifiableObjectTargets as wildcard URNs and read back as the flow's own"
        ),
        carried=_lacks_targets,
    ),
    _Entry(
        targets=("2.1",),
        applies_to=RepresentationMap,
        field="mappings[].valid_from",
        reason="2.1 CodeMapType allows only Annotations/Source/Target — no validity dates",
        carried=_any_map_has("valid_from", "mappings"),
    ),
    _Entry(
        targets=("2.1",),
        applies_to=RepresentationMap,
        field="mappings[].valid_to",
        reason="2.1 CodeMapType allows only Annotations/Source/Target — no validity dates",
        carried=_any_map_has("valid_to", "mappings"),
    ),
    _Entry(
        targets=("3.1",),
        applies_to=Categorisation,
        field="version",
        reason="3.1 marks CategorisationBaseType/@version use='prohibited'",
        carried=_has("version"),
    ),
    # SDMX 3.0 unified 2.1's two constraint artefacts, so an
    # AttachmentConstraint is written as the DataConstraint it equals — its
    # keys and attachment survive. What does not is the kind: the document
    # does not say which of the two it was, and it reads back as a
    # DataConstraint. Converted since #244 and undeclared until #421.
    _Entry(
        targets=("3.0", "3.1", "json"),
        applies_to=AttachmentConstraint,
        field="sdmx_class",
        reason=(
            "SDMX 3.0 removed AttachmentConstraint, so it is written as the Allowed DataConstraint "
            "it equals and reads back as one"
        ),
        carried=lambda _: True,
    ),
    _Entry(
        targets=("3.0", "3.1", "json"),
        applies_to=DataConstraint,
        field="reference_period",
        # 3.0 dropped `<ReferencePeriod>` from the constraint. The name still
        # occurs in the 3.0 schema *set* — on a registration-query type in
        # `SDMXRegistryRegistration.xsd` — which is why a grep for it reads as
        # "3.0 has this" and is wrong. SDMX-JSON has it in neither version.
        reason="SDMX 3.0 removed ReferencePeriod from the constraint types",
        carried=_has("reference_period"),
    ),
    # 3.1 removed `@role` from DataConstraint and gave Actual content an
    # artefact of its own, `AvailabilityConstraint`, which has no identity. An
    # Actual constraint that fits one is written as one (#421); one that does
    # not is refused by `validate`, so it is not a gap. The role used to be
    # declared lost here: written without it, Actual read back as Allowed
    # (#346).
    _Entry(
        targets=("3.1",),
        applies_to=DataConstraint,
        field="sdmx_class",
        reason=(
            "SDMX-ML 3.1 states Actual content as an AvailabilityConstraint, which has no identity: "
            "the id, agency, version, name, description, validity, uri and links are lost, and it reads "
            "back as an AvailabilityConstraint"
        ),
        carried=_states_availability,
    ),
    # And the other way: only SDMX-ML 3.1 has one, and no target but 3.1 can
    # write one as an Actual DataConstraint, which needs an identity it lacks.
    _Entry(
        targets=("2.1", "3.0", "json", "storage"),
        applies_to=AvailabilityConstraint,
        field="sdmx_class",
        reason="only SDMX-ML 3.1 has AvailabilityConstraint, so it is left out",
        carried=lambda _: True,
    ),
    _Entry(
        targets=("3.1", "json"),
        applies_to=DataConstraint,
        field="release_calendar",
        # Declared three times in the 3.0 XSD and not at all in 3.1: the
        # element was removed rather than moved. The writer had always dropped
        # it and this table had always said nothing was lost, which is the
        # shape #345 is about — a drop the mechanism built to answer for drops
        # denied.
        # `json` joins 3.1 because sdmxlib's SDMX-JSON must satisfy **both**
        # 2.0 and 2.1: 2.0 defines `releaseCalendar` and 2.1 does not, and 2.1
        # closes every object, so emitting it would make 2.1 output invalid.
        reason="3.1 removed ReleaseCalendar from DataConstraintType entirely",
        carried=_has("release_calendar"),
    ),
    _Entry(
        targets=("3.0", "3.1"),
        applies_to=MetadataSet,
        field="attributes[].value_type",
        reason="GenericMetadata carries a reported value as element text with no type marker",
        carried=lambda obj: bool(getattr(obj, "attributes", ())),
    ),
    # SDMX 3.0 restructured the MSD. `MetadataTarget` and `ReportStructure`
    # are 2.1 constructs and appear in **no** 3.0 or 3.1 schema — 3.0 replaced
    # the target/report layer with a flat `MetadataAttributeList`, the same
    # simplification that dropped `MetadataReport` from the message side.
    # SDMX-JSON encodes the 3.0 model and has neither.
    *(
        _Entry(
            targets=("3.0", "3.1", "json"),
            applies_to=MetadataStructure,
            field=field,
            reason="SDMX 3.0 replaced the MSD's target and report layer with a flat MetadataAttributeList",
            carried=_has(field),
        )
        for field in ("targets", "report_structures")
    ),
    # SDMX 3.x's date and value maps (#422), which 2.1's StructureMap has no
    # element for; its component maps name no representation map either.
    *(
        _Entry(
            targets=("2.1",),
            applies_to=StructureMap,
            field=field,
            reason="SDMX-ML 2.1's StructureMap has no epoch, date-pattern, frequency-format or fixed-value map",
            carried=_has(field),
        )
        for field in ("epoch_maps", "date_pattern_maps", "frequency_format_mappings", "fixed_value_maps")
    ),
    _Entry(
        targets=("2.1",),
        applies_to=StructureMap,
        field="maps[].representation_map",
        reason=(
            "SDMX-ML 2.1's ComponentMap names a codelist map of its own structure set, or lists value pairs "
            "inline; an epoch or date-pattern map, or a map of another agency or version, has no 2.1 form"
        ),
        carried=_unnameable_in_21,
    ),
    # SDMX-ML 2.1's alone (#422): `RepresentationMapType`'s ToTextFormat and
    # ToValueType have no counterpart in 3.x or SDMX-JSON.
    *(
        _Entry(
            targets=("3.0", "3.1", "json"),
            applies_to=StructureMap,
            field=f"maps[].{field}",
            reason="SDMX 3.x's ComponentMap has no ToTextFormat or ToValueType; it was SDMX-ML 2.1's",
            carried=_any_map_has(field),
        )
        for field in ("to_text_format", "to_value_type")
    ),
    # SDMX-ML 2.1's alone (#422): neither 3.x nor SDMX-JSON has the attribute.
    _Entry(
        targets=("3.0", "3.1", "json"),
        applies_to=StructureMap,
        field="is_extension",
        reason="isExtension is SDMX-ML 2.1's; SDMX 3.0 removed it",
        carried=_has("is_extension"),
    ),
    _Entry(
        targets=("3.0", "3.1", "json"),
        applies_to=Hierarchy,
        field="tree[].version",
        reason="a hierarchical code's informational version is SDMX-ML 2.1's; SDMX 3.0 removed it",
        carried=_any_node_has("version"),
    ),
    # SDMX 3.x's, which 2.1's `ConceptRepresentation` does not declare (#422).
    *(
        _Entry(
            targets=("2.1",),
            applies_to=ConceptScheme,
            field=f"concepts[].{field}",
            reason="SDMX-ML 2.1's CoreRepresentation has no minOccurs or maxOccurs",
            carried=_any_item_differs("concepts", field, 1),
        )
        for field in ("min_occurs", "max_occurs")
    ),
    # SDMX-ML 3.1's additions (#422); 2.1 and 3.0 have no attribute for them.
    _Entry(
        targets=("2.1", "3.0"),
        applies_to=MaintainableArtefact,
        field="is_partial_language",
        reason="isPartialLanguage was added in SDMX-ML 3.1",
        carried=_has("is_partial_language"),
    ),
    _Entry(
        targets=("2.1", "3.0"),
        applies_to=DataStructure,
        field="evolving_structure",
        reason="evolvingStructure was added in SDMX-ML 3.1",
        carried=_has("evolving_structure"),
    ),
    # SDMX 3.x's, which 2.1's types do not declare (#422).
    # 2.1's AttachmentGroup: groups an attribute related to dimensions may
    # also be reported with. 3.x has no form for it; leaving it out narrows
    # where a value may be reported, and was refused until #421.
    _Entry(
        targets=("3.0", "3.1", "json"),
        applies_to=DataStructure,
        field="attributes[].attachment_groups",
        reason="only SDMX-ML 2.1 has AttachmentGroup; the attribute keeps its dimensions",
        carried=_any_item_has("attributes", "attachment_groups"),
    ),
    _Entry(
        targets=("2.1",),
        applies_to=DataStructure,
        field="measures[].usage",
        reason="SDMX-ML 2.1's PrimaryMeasure has no usage, so a mandatory measure reads back optional",
        carried=_mandatory_measure,
    ),
    *(
        _Entry(
            targets=("2.1",),
            applies_to=DataConstraint,
            field=f"data_key_sets[].keys[].{field}",
            reason="SDMX-ML 2.1's DataKeyType has no validity and no Component",
            carried=_any_at("data_key_sets[].keys[]", field),
        )
        for field in ("valid_from", "valid_to", "components")
    ),
    *(
        _Entry(
            targets=("2.1",),
            applies_to=cls,
            field=f"maps[].{field}",
            reason="SDMX-ML 2.1's item maps have no validity",
            carried=_any_map_has(field),
        )
        for cls in (ConceptSchemeMap, CategorySchemeMap, OrganisationSchemeMap)
        for field in ("valid_from", "valid_to")
    ),
    _Entry(
        targets=("2.1",),
        applies_to=MetadataStructure,
        field="attribute_list_uri",
        reason="an SDMX-ML 2.1 MSD has no MetadataAttributeList",
        carried=_has("attribute_list_uri"),
    ),
    # SDMX-JSON gives a maintainable `isExternalReference` and no URL to say
    # where the full artefact is (#422).
    *(
        _Entry(
            targets=("json",),
            applies_to=MaintainableArtefact,
            field=field,
            reason="SDMX-JSON has isExternalReference but no serviceURL or structureURL",
            carried=_has(field),
        )
        for field in ("service_url", "structure_url")
    ),
    # 2.1's `CubeRegionType` and `DataKeyType` are not annotable; 3.x's
    # are (#422).
    *(
        _Entry(
            targets=("2.1",),
            applies_to=DataConstraint,
            field=f"{field}[].annotations",
            reason=f"SDMX-ML 2.1's {element} has no Annotations",
            carried=_annotation_at(f"{field}[]", bool),
        )
        for field, element in (("regions", "CubeRegionType"), ("data_key_sets[].keys", "DataKeyType"))
    ),
    # And the other way: 2.1 has no attribute list to annotate. Its
    # attributes sit in report structures (#422).
    _Entry(
        targets=("2.1",),
        applies_to=MetadataStructure,
        field="attribute_list_annotations",
        reason="an SDMX-ML 2.1 MSD has no MetadataAttributeList; its attributes sit in report structures",
        carried=_has("attribute_list_annotations"),
    ),
    # The store, likewise: the 2.1 layer has no table. 3.0 removed it from the
    # standard (the entry above), and #421 converts it to the canonical 3.x
    # shape on ingest, after which the store has nothing of it left to carry.
    # Tables for a layer due to be read away would be a migration in each
    # direction for no reader (#442).
    *(
        _Entry(
            targets=("storage",),
            applies_to=MetadataStructure,
            field=field,
            reason="the store holds the 3.x MSD; the 2.1 target and report layer is converted on ingest (#421)",
            carried=_has(field),
        )
        for field in ("targets", "report_structures")
    ),
    _Entry(
        targets=("2.1", "3.0", "json"),
        applies_to=Dataflow,
        field="dimension_constraint",
        # 3.1's alone: `DataflowBaseType` extends `StructureUsageType` with a
        # `<DimensionConstraint>`, and neither 2.1 nor 3.0 has any equivalent —
        # nor does SDMX-JSON 2.0, the version this library emits. (2.1 of
        # SDMX-JSON does define `dimensionConstraint`, which is why this is
        # declared for `json` rather than for a JSON version: one target covers
        # both, and 2.0 is what we write.)
        #
        # The 3.1 writer did not emit it either until #355, so the field was
        # unwritable everywhere despite one format having a home for it.
        reason="DimensionConstraint is new in SDMX 3.1; earlier versions have no equivalent",
        carried=_has("dimension_constraint"),
    ),
    # `AnnotationValue` — the non-localised annotation body — is a **3.0
    # addition**: 2.1's `AnnotationType` has four children (Title, Type, URL,
    # Text) and 3.0's has five. Writing 2.1 drops it, and there is nowhere for
    # it to go rather than somewhere the writer forgot (#368).
    #
    # Twenty entries for one field, because annotations hang off nearly
    # everything. The first covers the artefact's own annotations for *every*
    # maintainable at once — `unrepresentable` matches on `isinstance` — and
    # the rest name the item collections beneath them, which is the spelling a
    # reader of the model would use and so what the census compares against.
    *(
        _Entry(
            targets=("2.1",),
            applies_to=cls,
            field=f"{prefix}annotations[].value",
            reason="SDMX-ML 2.1 has no AnnotationValue element; it was added in 3.0",
            carried=_annotation_value_at(prefix.rstrip(".")),
        )
        for cls, prefix in _ANNOTATION_SITES
    ),
    # `<com:Link>` is SDMX 3.x's (#422): 2.1's `IdentifiableType` has no such
    # child, so a 2.1 write drops every link.
    *(
        _Entry(
            targets=("2.1",),
            applies_to=cls,
            field=path,
            reason="SDMX-ML 2.1's IdentifiableType has no Link; it was added in 3.0",
            carried=_links_at(path),
        )
        for cls, path in _LINK_SITES
    ),
    # A link's language is SDMX-JSON's `hreflang` (#422); SDMX-ML 3.x's
    # `LinkType` has `rel`, `url`, `urn` and `type` and nothing more. 2.1 is
    # not listed: it drops the whole link, above.
    *(
        _Entry(
            targets=("3.0", "3.1"),
            applies_to=cls,
            field=f"{path}[].hreflang",
            reason="SDMX-ML's LinkType has no language",
            carried=_link_languages_at(path),
        )
        for cls, path in _LINK_SITES
    ),
    # A text format's sentinel values are SDMX 3.x's (#422); 2.1's
    # `TextFormatType` has no such child.
    *(
        _Entry(
            targets=("2.1",),
            applies_to=cls,
            field=f"{path}.sentinel_values",
            reason="SDMX-ML 2.1's TextFormatType has no SentinelValue",
            carried=_sentinels_at(path),
        )
        for cls, path in (
            (DataStructure, "dimensions[].representation"),
            (DataStructure, "attributes[].representation"),
            (DataStructure, "measures[].representation"),
            (DataStructure, "time_dimension.representation"),
            (ConceptScheme, "concepts[].core_representation"),
            (MetadataStructure, "attributes[].representation"),
            (MetadataStructure, "attributes[].children[].representation"),
        )
    ),
    # SDMX-ML 2.1's `AnnotationType` has one `AnnotationURL`, unlocalised; 3.x
    # allows several, each with an `xml:lang`. A 2.1 write keeps the first
    # (#422). Same twenty sites as the value, for the same reason.
    *(
        _Entry(
            targets=("2.1",),
            applies_to=cls,
            field=f"{prefix}annotations[].urls",
            reason="SDMX-ML 2.1 has one unlocalised AnnotationURL; the rest, and any language, are not written",
            carried=_annotation_at(prefix.rstrip("."), _beyond_2_1_urls),
        )
        for cls, prefix in _ANNOTATION_SITES
    ),
    # An annotation URL's URN and type are SDMX-JSON's, on the link that
    # spells it (#422); SDMX-ML's `AnnotationURLType` is a URI with an
    # `xml:lang` and nothing more, in either version.
    *(
        _Entry(
            targets=("2.1", "3.0", "3.1"),
            applies_to=cls,
            field=f"{prefix}annotations[].urls[].{attribute}",
            reason=f"SDMX-ML's AnnotationURL has no {attribute}; SDMX-JSON's link has",
            carried=_annotation_at(prefix.rstrip("."), _url_has(attribute)),
        )
        for cls, prefix in _ANNOTATION_SITES_3X
        for attribute in ("urn", "type")
    ),
    # SDMX-ML 2.1 gives a data structure's attributes and primary measure
    # the simple text format, which prohibits `isMultiLingual`; 3.x gives
    # them the basic one, whose default is `true` in 3.0 (#503).
    *(
        _Entry(
            targets=("2.1",),
            applies_to=DataStructure,
            field=f"{kind}[].representation.is_multilingual",
            reason="SDMX-ML 2.1's SimpleComponentTextFormatType prohibits isMultiLingual",
            carried=_any_at(f"{kind}[].representation", "is_multilingual"),
        )
        for kind in ("attributes", "measures")
    ),
    # The message header (#480). SDMX-JSON's `meta` holds the parties and the
    # message's names, but no source, no sender time zone and none of a
    # metadata header's four; SDMX-ML's message `ContactType` has no `@id`,
    # which SDMX-JSON's contact has.
    _Entry(
        targets=("json",),
        applies_to=Header,
        field="source",
        reason="SDMX-JSON's meta has no Source",
        carried=_has("source"),
    ),
    _Entry(
        targets=("json",),
        applies_to=Header,
        field="sender.timezone",
        reason="SDMX-JSON's meta sender has no Timezone",
        carried=lambda header: bool(_reach(header, "sender.timezone")),
    ),
    *(
        _Entry(
            targets=("json",),
            applies_to=MetadataHeader,
            field=attribute,
            reason=f"SDMX-JSON's meta has no {element}",
            carried=_has(attribute),
        )
        for attribute, element in (
            ("metadata_provider", "MetadataProvider"),
            ("data_set_action", "DataSetAction"),
            ("data_set_ids", "DataSetID"),
            ("extracted", "Extracted"),
        )
    ),
    *(
        _Entry(
            targets=("2.1", "3.0", "3.1"),
            applies_to=Header,
            field=f"{party}.contacts[].id",
            reason="SDMX-ML's message ContactType has no id; SDMX-JSON's contact has",
            carried=lambda header, party=party: any(
                getattr(contact, "id", None) is not None for contact in _reach(header, f"{party}.contacts[]")
            ),
        )
        for party in ("sender", "receivers[]")
    ),
)


def unrepresentable(artefact: object, target: str) -> tuple[Gap, ...]:
    """What writing `artefact` to `target` would silently lose.

    Answers only for *this* artefact: a `Codelist` with `legacy_is_final` unset loses
    nothing to 3.0, so nothing is reported. That is what makes the result worth
    reading rather than a restatement of the table.

    Does **not** report what would be *refused* — an unrepresentable version,
    or a 2.1 DataStructure with no measures. A draft into 2.1 is not refused
    but left out, so it is reported here; what else in a message goes with
    it is `dropped`'s to say, since one artefact cannot see the rest. Those raise at `write` with a
    message of their own, and a caller who sees them cannot miss them. This is
    for the losses that make no sound.

    Args:
        artefact: A maintainable, or anything with the fields below.
        target: One of `TARGETS`.

    Returns:
        The gaps, in table order. Empty when the artefact writes losslessly.

    Raises:
        ValueError: If `target` is not a known format.

    Example:
        for gap in sl.unrepresentable(codelist, "3.0"):
            print(gap)
    """
    if target not in TARGETS:
        msg = f"unknown target {target!r}; expected one of {', '.join(TARGETS)}"
        raise ValueError(msg)
    return tuple(
        Gap(target=target, field=entry.field, reason=entry.reason)
        for entry in _ENTRIES
        if target in entry.targets and isinstance(artefact, entry.applies_to) and entry.carried(artefact)
    )


def declared_gaps(applies_to: type | None = None) -> tuple[tuple[str, str, str, str], ...]:
    """The table itself, as `(target, type name, field, reason)` rows.

    For a caller documenting what a format costs, and for the round-trip guard,
    which consults this rather than keeping its own copy.
    """
    return tuple(
        (target, entry.applies_to.__name__, entry.field, entry.reason)
        for entry in _ENTRIES
        for target in entry.targets
        if applies_to is None or entry.applies_to is applies_to
    )


# ── Artefacts a target leaves out whole (#424) ───────────────────────────


@attrs.frozen
class DroppedArtefact:
    """One artefact a write to a target leaves out, and why.

    Attributes:
        urn: The artefact left out — for an `AvailabilityConstraint`, which has
            no URN, `availability_label`'s name for it (#421).
        cause: The artefact that caused it: `urn` itself when the artefact is a
            draft or of a class the target lacks, otherwise what it
            references, directly or through other artefacts the write leaves
            out.
        reason: Why, in terms of the format.
    """

    urn: str
    cause: str
    reason: str

    def __str__(self) -> str:
        return f"{self.urn} is left out: {self.reason}"


def dropped(msg: StructureMessage, target: str) -> tuple[DroppedArtefact, ...]:
    """The artefacts writing `msg` to `target` leaves out, each with its cause.

    SDMX-ML 2.1's version grammar has no form for an extension, so a draft
    (`1.0.0-draft`) cannot be written. Stripping the suffix would make a draft
    look final and could collide with a real `1.0.0`, so the artefact is left
    out whole (#424).

    An artefact of a class the target lacks is left out too (`_NO_SUCH_CLASS`):
    a `ValueList` or a `HierarchyAssociation` from 2.1 (#422, #421), a
    `HierarchicalCodelist` or a `StructureSet` from 3.x and SDMX-JSON (#421),
    whose hierarchies or maps are written without it. Every target but
    SDMX-ML 3.1, the store included, leaves out an `AvailabilityConstraint`.

    The rest of the message follows. An artefact that references a draft, or
    any value list, cannot name it in 2.1 and is left out too, and so — repeatedly, until
    nothing changes — is one that references an artefact the write leaves out,
    so the output never points at something it does not contain. A reference to
    an artefact that was never in the message is left alone: whether it exists
    is not this write's to say.

    For a caller that serves artefacts: one asked for by name and left out is
    a refusal (a 404 naming `reason`), and one left out of a listing is a note
    on it. `serialize` cannot tell the two apart; this is how the caller does.

    Args:
        msg: The message about to be written.
        target: One of `TARGETS`.

    Returns:
        The artefacts left out, in message order, then the availability
        constraints. Empty for a message with nothing the target lacks.

    Raises:
        ValueError: If `target` is not a known format.
    """
    if target not in TARGETS:
        err = f"unknown target {target!r}; expected one of {', '.join(TARGETS)}"
        raise ValueError(err)
    unheld = _availability_left_out(msg, target)
    if target != "2.1" and target not in _NO_SUCH_CLASS:
        return unheld
    artefacts = msg.all_artefacts()
    urns = [str(artefact_urn(artefact)) for artefact in artefacts]
    out: dict[str, DroppedArtefact] = {}
    for urn, artefact in zip(urns, artefacts, strict=True):
        if (left_out := _left_out(urn, artefact, target)) is not None:
            out[urn] = left_out
    if target != "2.1" and not out:
        # Only 2.1 leaves out an artefact for what it names, so elsewhere
        # nothing left out means no references worth walking.
        return unheld
    references: list[list[str]] = []
    for urn, artefact in zip(urns, artefacts, strict=True):
        references.append(
            sorted({str(name) for _path, ref in _collect_refs(artefact, "") for name in _maintainables(ref)})
        )
        if urn in out or target != "2.1":
            continue
        naming = _names_the_unnameable(urn, artefact, out)
        if naming is not None:
            out[urn] = naming
    _follow(urns, references, out, target)
    return (*(out[urn] for urn in urns if urn in out), *unheld)


def _availability_left_out(msg: StructureMessage, target: str) -> tuple[DroppedArtefact, ...]:
    """The message's availability constraints, when `target` is not SDMX-ML 3.1 (#421).

    Each is named by what it states availability for, having no URN of its own.
    """
    if target == "3.1":
        return ()
    reason = f"{describe(target)} has no AvailabilityConstraint, which only SDMX-ML 3.1 has"
    return tuple(
        DroppedArtefact(urn=label, cause=label, reason=reason)
        for label in (availability_label(ac) for ac in msg.availability_constraints)
    )


def availability_label(ac: AvailabilityConstraint) -> str:
    """How `dropped` names an `AvailabilityConstraint`, which has no URN: by its target."""
    return f"AvailabilityConstraint({ac.target.urn if ac.target is not None else 'no single target'})"


#: Artefact classes a target has no form for at all, so leaves out whole.
_NO_SUCH_CLASS: dict[str, dict[type, str]] = {
    "2.1": {
        ValueList: "SDMX-ML 2.1 has no ValueList",
        # SDMX 3.0's binding of a hierarchy to what it organises; 2.1 binds a
        # hierarchy to nothing (#421).
        HierarchyAssociation: "SDMX-ML 2.1 has no HierarchyAssociation",
    },
    **{
        target: {
            cls: (
                f"{describe(target)} has no {cls.__name__}: SDMX 3.0 removed it, "
                f"and its {members} are written without it"
            )
            for cls, members in ((HierarchicalCodelist, "hierarchies"), (StructureSet, "maps"))
        }
        for target in ("3.0", "3.1", "json")
    },
}


def _follow(urns: list[str], references: list[list[str]], out: dict[str, DroppedArtefact], target: str) -> None:
    """Leave out, repeatedly until nothing changes, whatever references something left out."""
    changed = True
    while changed:
        changed = False
        for urn, named in zip(urns, references, strict=True):
            hit = next((name for name in named if name in out and name != urn), None)
            if urn not in out and hit is not None:
                out[urn] = DroppedArtefact(
                    urn=urn,
                    cause=out[hit].cause,
                    reason=f"it references {hit}, which {describe(target)} output leaves out",
                )
                changed = True


def _left_out(urn: str, artefact: MaintainableArtefact, target: str) -> DroppedArtefact | None:
    """Why `target` leaves `artefact` out for what it is — a draft, a class it lacks — or None."""
    if target == "2.1" and _extended(artefact.version):
        return DroppedArtefact(
            urn=urn,
            cause=urn,
            reason=f"version {artefact.version!r} is a draft, and SDMX-ML 2.1 has no form for its extension",
        )
    reason = next(
        (why for cls, why in _NO_SUCH_CLASS.get(target, {}).items() if isinstance(artefact, cls)),
        None,
    )
    return None if reason is None else DroppedArtefact(urn=urn, cause=urn, reason=reason)


def _names_the_unnameable(
    urn: str, artefact: MaintainableArtefact, out: dict[str, DroppedArtefact]
) -> DroppedArtefact | None:
    """Why 2.1 leaves `artefact` out for what it names — a draft, a value list — or None.

    Whether or not the message holds what it names: a 2.1 reference cannot
    spell either.
    """
    if (draft := next(iter(_draft_refs(artefact)), None)) is not None:
        cause = _cause(draft, out)
        return DroppedArtefact(urn=urn, cause=cause, reason=f"it references {cause}, a draft SDMX-ML 2.1 cannot name")
    if (value_list := next(iter(_value_list_refs(artefact)), None)) is not None:
        cause = str(value_list.urn)
        return DroppedArtefact(
            urn=urn, cause=cause, reason=f"it references {cause}, a ValueList SDMX-ML 2.1 cannot name"
        )
    return None


def without_dropped(msg: StructureMessage, target: str) -> StructureMessage:
    """`msg` less what `dropped` names for `target` — the message a writer writes.

    The same object when nothing is left out, so a write without drafts pays
    for the walk and nothing else.
    """
    gone = {drop.urn for drop in dropped(msg, target)}
    if not gone:
        return msg
    kept = {
        field.name: [artefact for artefact in value if str(artefact_urn(artefact)) not in gone]
        for field in attrs.fields(StructureMessage)
        if isinstance(value := getattr(msg, field.name), ArtefactList)
    }
    held = msg.availability_constraints if target == "3.1" else ()
    # `header` named, so the artefact lists unpacked need not suit it.
    return attrs.evolve(msg, availability_constraints=held, header=msg.header, **kept)


def with_derived_targets(msg: StructureMessage) -> StructureMessage:
    """`msg` with each target-less metadataflow given the targets its structure implies (#421).

    What a 3.x writer writes: 3.x requires a metadataflow's targets, and one
    read from SDMX-ML 2.1 has none (`Metadataflow.with_derived_targets`). A
    flow they cannot be derived for is left as it is, for `validate` to
    refuse. The same object when no flow lacks targets.
    """
    if all(mdf.targets for mdf in msg.metadataflows):
        return msg
    flows = [
        mdf
        if mdf.targets or mdf.target_misfits(msg.metadata_structures)
        else mdf.with_derived_targets(msg.metadata_structures)
        for mdf in msg.metadataflows
    ]
    return attrs.evolve(msg, metadataflows=flows)


def _maintainables(ref: Ref[object]) -> list[object]:
    """The maintainable URNs a reference may name: itself, or the scheme holding its item."""
    if not ref.urn.is_item_urn:
        return [ref.urn]
    return [scheme_urn_of(ref.urn, owner) for owner in owner_classes(ref.urn.artefact_class)]


def _cause(ref: Ref[object], out: dict[str, DroppedArtefact]) -> str:
    """The draft a reference names, as the maintainable's URN when it can be told."""
    candidates = [str(urn) for urn in _maintainables(ref)]
    return next((urn for urn in candidates if urn in out), candidates[0] if candidates else str(ref.urn))
