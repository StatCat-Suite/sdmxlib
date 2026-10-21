"""Every element SDMX-ML allows has an owner: the writer, the loss register, or the schema (#422).

The read-side register (`sdmx_ml._losses.LOSSES`) reports a construct a reader
drops — but only one somebody listed. A StructureMap's `EpochMap`, a
`DimensionList`'s annotations, a concept's `ISOConceptReference` and a dozen
more were read and discarded with no row, so a lossless parse of a document
holding them succeeded and said nothing. This census is what found them — in
3.x first, then in 2.1, where it also found a reference spelled `<URN>` read as
none, a metadata constraint read as a data one, and a structure set's related
structures neither written nor read.

It compares two sets of ``(parent, child)`` element pairs, per version:

- what the schema allows, from the closed-world table `_allowed_children`;
- what sdmxlib's writer emits for the maximal fixture and the version's shapes
  (`shapes.message()`, or `shapes.message_21()`).

Every pair the schema allows and the writer never emits, under an element the
writer does emit, must be named in `_UNWRITTEN` as one of four things:

- **loss** — a construct with a `LOSSES` row, so a read reports it;
- **refused** — an artefact class sdmxlib does not model, or its container,
  which a read refuses whole;
- **alternative** — a second spelling of something the writer spells the first
  way, which a named test reads;
- **absent** — a pair no valid document holds: the closed-world table is keyed
  by element *name*, so it over-allows where two types share one.

Checked in both directions, so a pair the writer starts emitting must leave the
table, and a new schema pair cannot go unclassified.
"""

import re
from collections.abc import Callable

import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats.sdmx_ml._allowed_children import ALLOWED, NAMESPACES
from sdmxlib.formats.sdmx_ml._common import METADATA_CONSTRAINT
from sdmxlib.formats.sdmx_ml._losses import LOSSES
from sdmxlib.formats.sdmx_ml21 import reader as ml21_reader
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import reader as ml30_reader
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import reader as ml31_reader
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.message import StructureMessage
from tests.support import maximal, shapes
from tests.support.field_census import differences
from tests.support.header import ELEMENTS as HEADER_ELEMENTS
from tests.support.header import header_only
from tests.support.narrow import present
from tests.support.xsd import SchemaVersion, assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

_FORMATS: dict[SchemaVersion, tuple[Callable[[StructureMessage], bytes], Callable[[bytes], StructureMessage]]] = {
    "2.1": (ml21_writer.write, ml21_reader.read),
    "3.0": (ml30_writer.write, ml30_reader.read),
    "3.1": (ml31_writer.write, ml31_reader.read),
}


def _loss(construct: str) -> str:
    return f"loss: {construct}"


_REFUSED = "refused: the container of a class sdmxlib does not model"
_ABSTRACT_COMPONENT = (
    "absent: `str:Component` names both a cube region's value set and the abstract `ComponentType` head, "
    "which no document instantiates"
)
_METADATA_ATTACHMENT = (
    "absent: a `MetadataConstraint`'s attachment, which shares the element name; "
    "a `DataConstraint`'s cannot hold it, and a MetadataConstraint is refused"
)

#: Every pair the 3.0 schema allows and the writer does not emit, classified.
_UNWRITTEN_30: dict[tuple[str, str], str] = {
    **{
        ("mes:Structures", f"str:{container}"): _REFUSED
        for container in (
            "CustomTypeSchemes",
            "MetadataConstraints",
            "NamePersonalisationSchemes",
            "Processes",
            "ReportingTaxonomies",
            "ReportingTaxonomyMaps",
            "RulesetSchemes",
            "TransformationSchemes",
            "UserDefinedOperatorSchemes",
            "VtlMappingSchemes",
        )
    },
    **{
        ("str:Component", child): _ABSTRACT_COMPONENT
        for child in ("com:Annotations", "com:Link", "str:ConceptIdentity", "str:LocalRepresentation")
    },
    **{
        ("str:ConstraintAttachment", f"str:{child}"): _METADATA_ATTACHMENT
        for child in (
            "MetadataProvider",
            "MetadataProvisionAgreement",
            "MetadataSet",
            "MetadataStructure",
            "Metadataflow",
        )
    },
}

#: 3.1: 3.0's, less what 3.1 removed and plus what it added.
_UNWRITTEN_31: dict[tuple[str, str], str] = {
    **{pair: kind for pair, kind in _UNWRITTEN_30.items() if pair != ("str:ConstraintAttachment", "str:MetadataSet")},
}

_URN_FORM = (
    "alternative: 2.1's `<URN>` spelling of a reference the writer spells `<Ref>`, "
    "read by `test_sdmx_ml_2_1_reads_a_reference_spelled_as_a_urn`"
)
_ALIAS_FORM = "alternative: the alias form of a hierarchical code's code, read by `tests/unit/test_hcl_alias_form.py`"
_METADATA_CONSTRAINT = (
    "refused: a 2.1 constraint on metadata, refused whole as 3.x's MetadataConstraint is "
    "(`test_a_2_1_constraint_on_metadata_is_refused`)"
)
_TRANSFORMATION = "refused: a reference by this name occurs only inside a 2.1 `Transformation`, which is refused"

#: Every pair the 2.1 schema allows and the writer does not emit, classified.
_UNWRITTEN_21: dict[tuple[str, str], str] = {
    **{
        ("mes:Structures", f"str:{container}"): _REFUSED
        for container in (
            "CustomTypes",
            "NamePersonalisations",
            "Processes",
            "ReportingTaxonomies",
            "Rulesets",
            "Transformations",
            "UserDefinedOperators",
            "VtlMappings",
        )
    },
    **{
        (f"str:{parent}", "URN"): _URN_FORM
        for parent in (
            # A group's attachment constraint (#422).
            "AttachmentConstraint",
            "Code",
            "ConceptIdentity",
            "ConceptRole",
            "DataProvider",
            "DataStructure",
            "Dataflow",
            "Enumeration",
            "IncludedCodelist",
            "ProvisionAgreement",
            "RelatedStructure",
            "Source",
            "Structure",
            "StructureUsage",
            "Target",
        )
    },
    **{("str:HierarchicalCode", f"str:{child}"): _ALIAS_FORM for child in ("CodeID", "CodelistAliasRef")},
    # A data set's provider, spelled as `<URN>` rather than `<Ref>`.
    ("com:DataProvider", "URN"): _URN_FORM,
    **{
        ("str:StructureSet", f"str:{child}"): _loss(f"str:StructureSet/str:{child}")
        for child in ("HybridCodelistMap", "ReportingTaxonomyMap")
    },
    **dict.fromkeys(
        (
            ("str:ConstraintAttachment", "str:MetadataSet"),
            ("str:ConstraintAttachment", "str:MetadataStructure"),
            ("str:ConstraintAttachment", "str:Metadataflow"),
            ("str:ContentConstraint", "str:MetadataKeySet"),
            ("str:ContentConstraint", "str:MetadataTargetRegion"),
            ("str:AttachmentConstraint", "str:MetadataKeySet"),
            ("str:MetadataStructure", "Ref"),
            ("str:MetadataStructure", "URN"),
            ("str:Metadataflow", "Ref"),
            ("str:Metadataflow", "URN"),
            # A metadata target region's key values and attributes, which
            # share their names with a cube region's.
            *(
                (parent, f"com:{child}")
                for parent in ("com:Attribute", "com:KeyValue")
                for child in ("DataKey", "DataSet", "Object")
            ),
        ),
        _METADATA_CONSTRAINT,
    ),
    **{
        (f"str:{parent}", child): _TRANSFORMATION
        for parent in ("Codelist", "Concept", "ConceptScheme")
        for child in ("Ref", "URN")
    },
}

_UNWRITTEN: dict[SchemaVersion, dict[tuple[str, str], str]] = {
    "2.1": _UNWRITTEN_21,
    "3.0": _UNWRITTEN_30,
    "3.1": _UNWRITTEN_31,
}

#: What each version's census writes beside `maximal`: 2.1 cannot carry all of
#: `shapes.message()` and has shapes of its own; 3.1 has no data sources.
_SHAPES: dict[SchemaVersion, Callable[[], StructureMessage]] = {
    "2.1": shapes.message_21,
    "3.0": shapes.message,
    "3.1": shapes.message_31,
}


def _qname(el: etree._Element, prefixes: dict[str, str]) -> str:
    """As `ALLOWED` spells it: prefixed, or bare for 2.1's unqualified `Ref` and `URN`."""
    name = etree.QName(el)
    return f"{prefixes[name.namespace]}:{name.localname}" if name.namespace else name.localname


def _documents(version: SchemaVersion) -> list[etree._Element]:
    write, _ = _FORMATS[version]
    return [etree.fromstring(write(message)) for message in (maximal.message(), _SHAPES[version]())]


def _unwritten(version: SchemaVersion, documents: list[etree._Element] | None = None) -> set[tuple[str, str]]:
    """The allowed pairs the documents never hold, under an element they do hold."""
    prefixes = {uri: prefix for prefix, uri in NAMESPACES[version].items()}
    written: set[tuple[str, str]] = set()
    for doc in _documents(version) if documents is None else documents:
        for el in doc.iter():
            parent = el.getparent()
            if isinstance(el.tag, str) and parent is not None:
                written.add((_qname(parent, prefixes), _qname(el, prefixes)))
    emitted = {name for pair in written for name in pair}
    allowed = {(parent, child) for parent, children in ALLOWED[version].items() for child in children}
    return {pair for pair in allowed - written if pair[0] in emitted}


@pytest.mark.parametrize("version", sorted(_FORMATS))
def test_every_unwritten_pair_is_classified(version: SchemaVersion) -> None:
    # Under the header, exactly what a structure message never holds (#480).
    header = {pair for pair in _unwritten(version) if pair[0] in header_only(version)}
    assert header == set(HEADER_ELEMENTS[version]), "the header's unwritten pairs changed: classify them"
    unwritten = _unwritten(version) - header
    table = set(_UNWRITTEN[version])
    unclassified = sorted(unwritten - table)
    assert not unclassified, (
        f"SDMX-ML {version} allows these, and nothing here writes them or says why: "
        + "".join(f"\n    {p} > {c}" for p, c in unclassified)
        + "\n\nInject one into the maximal message and read it: if it is held, write it in `shapes.message()`; "
        "if it is dropped, give it a `LOSSES` row; if no document can hold it, say so here."
    )
    stale = sorted(table - unwritten)
    assert not stale, f"these are written now, or no longer allowed — delete them: {stale}"


@pytest.mark.parametrize("version", sorted(_FORMATS))
def test_every_loss_named_here_is_a_registered_row(version: SchemaVersion) -> None:
    rows = {loss.construct for loss in LOSSES if version in loss.versions}
    named = {kind.removeprefix("loss: ") for kind in _UNWRITTEN[version].values() if kind.startswith("loss: ")}
    assert named <= rows, f"no LOSSES row for {sorted(named - rows)}"


@pytest.mark.parametrize("version", sorted(_FORMATS))
def test_every_refused_container_holds_an_unmodelled_class(version: SchemaVersion) -> None:
    modelled = {name for name in dir(sl) if isinstance(getattr(sl, name), type)}
    for (_, child), kind in _UNWRITTEN[version].items():
        if kind == _REFUSED:
            cls = _singular(child.removeprefix("str:"))
            assert cls not in modelled, f"{child} is {cls} or holds it, which sdmxlib models"


def _singular(container: str) -> str:
    """`ReportingTaxonomies` → `ReportingTaxonomy`, `Processes` → `Process`, `RulesetSchemes` → `RulesetScheme`."""
    if container.endswith("ies"):
        return container[:-3] + "y"
    if container.endswith("sses"):
        return container[:-2]
    return container.removesuffix("s")


def test_the_census_can_find_a_drop() -> None:
    """Discrimination: a pair the writer stops emitting is reported as unclassified."""
    documents = _documents("3.0")
    for doc in documents:
        for code in list(doc.iter(f"{{{NAMESPACES['3.0']['str']}}}Code")):
            present(code.getparent()).remove(code)
    assert ("str:Codelist", "str:Code") not in _unwritten("3.0")
    assert ("str:Codelist", "str:Code") in _unwritten("3.0", documents) - set(_UNWRITTEN_30)


@pytest.mark.parametrize("version", sorted(_FORMATS))
def test_the_shapes_are_written_validly_and_read_back(version: SchemaVersion) -> None:
    """What `shapes.message()` contributes to the census is held, not merely emitted."""
    write, read = _FORMATS[version]
    written = write(_SHAPES[version]())
    assert_valid_sdmx_ml(written, compile_structure_schema(version))
    back = read(written)
    assert back.lost == ()
    assert back.refused == ()
    read_back = {str(artefact.urn): artefact for artefact in back.all_artefacts()}
    for original in _SHAPES[version]().all_artefacts():
        # Field by field, as the census compares: a reference the reader leaves
        # unresolved is the same reference. Less what `gaps.py` declares the
        # version cannot write — 2.1's, here.
        declared = {gap.field for gap in sl.unrepresentable(original, version)}
        found = [
            d
            for d in differences(original, read_back[str(original.urn)])
            if re.sub(r"\[\d+\]", "[]", d.path).removeprefix(".") not in declared
        ]
        assert not found, f"{original.urn} lost {found}"


# ── defects the censuses found, pinned until fixed ────────────────────────


@pytest.mark.parametrize("version", sorted(_FORMATS))
def test_a_hierarchy_of_two_levels_is_a_chain(version: SchemaVersion) -> None:
    """3.x's `Level` holds the next level down, and a hierarchy holds one (#422).

    The writer emitted siblings, which the XSD rejects past one level, and the
    reader read siblings — so a conformant document's second level was lost.
    The maximal fixture now has two, which the shapes test validates.
    """
    write, read = _FORMATS[version]
    doc = etree.fromstring(write(maximal.message()))
    str_ns = NAMESPACES[version]["str"]
    (top,) = doc.iterfind(f".//{{{str_ns}}}Hierarchy/{{{str_ns}}}Level")
    assert [el.get("id") for el in top.iter(f"{{{str_ns}}}Level")] == ["L1", "L2"]
    back = read(etree.tostring(doc))
    assert [level.id for level in back.hierarchies[0].levels] == ["L1", "L2"]


def test_sdmx_ml_2_1_refuses_a_regex_or_index_value_map() -> None:
    """2.1 has no form for either: `serialize` refuses them (#242) rather than writing a pattern as a code id.

    The writer alone does not check — it was handed one and wrote XSD-invalid
    output when these variants first entered a fixture — which is why they
    live in the shapes, written to 3.x and JSON only, and not in `maximal`.
    """
    source = shapes.message()
    message = StructureMessage(
        codelists=list(source.codelists),
        representation_maps=[rm for rm in source.representation_maps if rm.id == "RM_VARIANTS"],
    )
    with pytest.raises(sl.ValidationError, match=r"regex mapping has no 2\.1 form") as caught:
        sl.serialize(message, version="2.1")
    assert len(caught.value.issues) == 2


def test_sdmx_ml_2_1_refuses_an_n_to_m_map() -> None:
    """2.1 maps one to one; cutting an N:M map to its first pair would state a mapping the source never made (#422).

    One issue per thing that does not fit: the representation map's two
    sources and two targets, each side's data type, both mappings, and the
    structure map's component map. Its reference to the representation map
    is not refused: a 2.1 component map names a map of its own structure set
    or drops it, declared in `gaps.py` (#422).
    """
    source = shapes.message()
    message = StructureMessage(
        codelists=list(source.codelists),
        data_structures=list(source.data_structures),
        representation_maps=[rm for rm in source.representation_maps if rm.id == "RM_N_TO_M"],
        structure_maps=list(source.structure_maps),
    )
    with pytest.raises(sl.ValidationError) as caught:
        sl.serialize(message, version="2.1")
    paths = sorted(issue.path for issue in caught.value.issues)
    assert paths == [
        "RepresentationMap:RM_N_TO_M.mappings[0]",
        "RepresentationMap:RM_N_TO_M.mappings[1]",
        "RepresentationMap:RM_N_TO_M.sources",
        "RepresentationMap:RM_N_TO_M.sources",
        "RepresentationMap:RM_N_TO_M.targets",
        "RepresentationMap:RM_N_TO_M.targets",
        "StructureMap:SM_N_TO_M.maps[0]",
    ]


def test_n_to_m_maps_survive_3x_and_json() -> None:
    """What 2.1 refuses, every other format holds whole."""
    source = shapes.message()
    message = StructureMessage(
        codelists=list(source.codelists),
        data_structures=list(source.data_structures),
        representation_maps=[rm for rm in source.representation_maps if rm.id == "RM_N_TO_M"],
        structure_maps=list(source.structure_maps),
    )
    for version in ("3.0", "3.1", "json"):
        back = sl.parse(sl.serialize(message, version=version))
        assert isinstance(back, StructureMessage)
        (rm,) = back.representation_maps
        assert rm.mappings == message.representation_maps[0].mappings, version
        assert [str(e.urn) if isinstance(e, sl.Ref) else e for e in rm.sources] == [
            str(e.urn) if isinstance(e, sl.Ref) else e for e in message.representation_maps[0].sources
        ], version
        ((back_map,), (sent,)) = back.structure_maps[0].maps, message.structure_maps[0].maps
        assert (back_map.sources, back_map.targets) == (sent.sources, sent.targets), version
        assert str(present(back_map.representation_map).urn) == str(present(sent.representation_map).urn), version


def test_sdmx_ml_2_1_reads_a_reference_spelled_as_a_urn() -> None:
    """2.1's `ReferenceType` may be a `<URN>` alone, and reads as its `<Ref>` does (#422).

    The reader looked only for `<Ref>`: a schema-valid document spelling its
    references the other way lost every one of them, unreported. Every `<Ref>`
    the maximal message's and the 2.1 shapes' forms carry a URN for is respelled, under each
    parent the census classifies as that alternative — no fewer.
    """
    write, read = _FORMATS["2.1"]
    respelled: set[str] = set()
    for message in (maximal.message(), shapes.message_21()):
        written = write(message)
        doc = etree.fromstring(written)
        for ref in list(doc.iter("Ref")):
            parent = ref.getparent()
            if ref.get("agencyID") is None or parent is None:
                continue  # a local reference, which the schema forbids an agency and gives no URN form
            urn = ml21_reader._helpers.ref_el_to_urn(ref)
            assert urn is not None
            urn_el = etree.Element("URN")
            urn_el.text = str(urn)
            parent.replace(ref, urn_el)
            respelled.add(_qname(parent, {uri: p for p, uri in NAMESPACES["2.1"].items()}))
        respelled_doc = etree.tostring(doc)
        assert_valid_sdmx_ml(respelled_doc, compile_structure_schema("2.1"))
        back = read(respelled_doc)
        assert (back.lost, back.refused) == ((), ())
        read_back = {str(artefact.urn): artefact for artefact in back.all_artefacts()}
        for original in read(written).all_artefacts():
            found = list(differences(original, read_back[str(original.urn)]))
            assert not found, f"{original.urn} lost {found}"
    assert respelled == {parent for (parent, _), kind in _UNWRITTEN_21.items() if kind == _URN_FORM}


def _attached_to(tag: str) -> Callable[[etree._Element], None]:
    """A constraint attached to one metadata artefact, `tag`, in place of what it had."""

    def attach(constraint: etree._Element) -> None:
        str_ns, com_ns = NAMESPACES["2.1"]["str"], NAMESPACES["2.1"]["com"]
        attachment = present(constraint.find("str:ConstraintAttachment", namespaces=NAMESPACES["2.1"]))
        for child in list(attachment):
            attachment.remove(child)
        target = etree.SubElement(attachment, f"{{{str_ns}}}{tag}")
        if tag == "MetadataSet":
            provider = etree.SubElement(target, f"{{{com_ns}}}DataProvider")
            etree.SubElement(
                provider, "URN"
            ).text = "urn:sdmx:org.sdmx.infomodel.base.DataProvider=AG:DATA_PROVIDERS(1.0).P"
            etree.SubElement(target, f"{{{com_ns}}}ID").text = "MS"
        else:
            etree.SubElement(target, "URN").text = f"urn:sdmx:org.sdmx.infomodel.metadatastructure.{tag}=AG:M(1.0)"

    return attach


def _metadata_target_region(constraint: etree._Element) -> None:
    str_ns = NAMESPACES["2.1"]["str"]
    etree.SubElement(constraint, f"{{{str_ns}}}MetadataTargetRegion", report="R", metadataTarget="T", include="true")


def _metadata_key_set(constraint: etree._Element) -> None:
    str_ns = NAMESPACES["2.1"]["str"]
    key_set = etree.SubElement(constraint, f"{{{str_ns}}}MetadataKeySet", isIncluded="true")
    key = etree.SubElement(key_set, f"{{{str_ns}}}Key")
    etree.SubElement(key, f"{{{str_ns}}}MetadataTarget").text = "TARGET"


@pytest.mark.parametrize(
    ("constraint", "make_it_metadata"),
    [
        pytest.param("ContentConstraint", _attached_to("MetadataStructure"), id="content-attached-to-an-msd"),
        pytest.param("ContentConstraint", _attached_to("Metadataflow"), id="content-attached-to-a-metadataflow"),
        pytest.param("ContentConstraint", _attached_to("MetadataSet"), id="content-attached-to-a-metadata-set"),
        pytest.param("ContentConstraint", _metadata_target_region, id="content-with-a-metadata-target-region"),
        pytest.param("AttachmentConstraint", _metadata_key_set, id="attachment-with-a-metadata-key-set"),
    ],
)
def test_a_2_1_constraint_on_metadata_is_refused(
    constraint: str, make_it_metadata: Callable[[etree._Element], None]
) -> None:
    """2.1 has one class per kind of constraint for data and metadata alike; sdmxlib holds the data one (#422).

    A metadata one was read as a data constraint, its attachment emptied and
    its metadata keys dropped. It is refused whole, as 3.x's
    `MetadataConstraint` is.
    """
    write, read = _FORMATS["2.1"]
    doc = etree.fromstring(write(shapes.message_21()))
    # Under `Constraints`: a group's reference to an attachment constraint
    # shares the element name (#422).
    str_ns = NAMESPACES["2.1"]["str"]
    el = next(doc.iterfind(f".//{{{str_ns}}}Constraints/{{{str_ns}}}{constraint}"))
    make_it_metadata(el)
    back = read(etree.tostring(doc))
    (refusal,) = back.refused
    assert (refusal.artefact_class, refusal.reason) == (constraint, METADATA_CONSTRAINT)
    assert refusal.urn.endswith(f"={el.get('agencyID')}:{el.get('id')}({el.get('version')})")
    assert refusal.urn not in {str(sl.artefact_urn(a)) for a in back.all_artefacts()}, "and nothing built from it"
    assert back.lost == ()
