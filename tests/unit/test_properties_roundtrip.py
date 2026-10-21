"""Generative round-trip properties across every serializer (#216).

`test_roundtrip_property_guard` states the same property over *hand-built*
artefacts, one field at a time, and owns the `KNOWN_GAPS` table recording which
fields a given wire format cannot carry. This file states it over *generated*
artefacts, and imports that table rather than restating it — two lists of known
gaps would drift, and the first symptom would be a property failing on a gap
somebody already understood.

Why generate at all, when fixtures exist: the suppression-audit milestone found
five defects in exactly this territory, and every one needed an input the
fixtures did not contain. Most recently #220 (a codelist whose *name* mentioned
"GenericMetadata" was parsed as an empty MetadataMessage) and #221 (text no XML
can represent). Neither is the sort of thing a fixture author writes on purpose.

**Round-trip is not equality.** The comparison below is field-by-field with the
asymmetries named, not a blanket ``==``. A too-generous comparison silently
weakens every property here, so each exception is justified where it is made
rather than folded into a helper nobody re-reads.
"""

from collections.abc import Callable, Iterable, Sequence
from typing import Any

import attrs
import duckdb
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.gaps import dropped
from sdmxlib.formats.sdmx_json import reader as json_reader
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.category import CategoryScheme
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.collections import ItemList
from sdmxlib.model.concept import ConceptScheme
from sdmxlib.model.constraint import (
    AttachmentConstraint,
    ConstraintAttachment,
    ConstraintType,
    DataConstraint,
    DataKey,
    DataKeySet,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.hierarchy import HierarchicalCode, HierarchicalCodelist, Hierarchy, HierarchyAssociation
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.mapping import ComponentMap, StructureMap, StructureSet
from sdmxlib.model.message import MetadataMessage, StructureMessage
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.organisation import AgencyScheme, MetadataProvider, MetadataProviderScheme
from sdmxlib.model.ref import Ref, artefact_urn
from sdmxlib.model.representation import Facet
from sdmxlib.model.validation import _UNWRITABLE, ValidationError
from sdmxlib.storage.readers import get_artefact
from sdmxlib.storage.schema import create_schema
from sdmxlib.storage.writers import put_codelist, put_concept_scheme
from tests.support.factories import concept_ref
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema
from tests.support.narrow import present
from tests.support.roundtrip import structure_schema
from tests.support.strategies import (
    agency_schemes,
    attachment_constraints,
    categorisations,
    category_scheme_maps,
    category_schemes,
    codelists,
    concept_scheme_maps,
    concept_schemes,
    data_constraints,
    data_provider_schemes,
    data_structures,
    dataflows,
    hierarchies,
    hierarchy_associations,
    metadata_sets,
    metadata_structures,
    metadataflows,
    organisation_scheme_maps,
    provision_agreements,
    representation_maps,
    structure_maps,
)
from tests.support.xsd import assert_valid_sdmx_ml
from tests.unit.test_roundtrip_property_guard import KNOWN_GAPS

#: The strategies draw every facet and constraint combination the formats carry,
#: the IM-discouraged ones too: those must round-trip, and warn as they are
#: built (#481), which is not what these properties test.
pytestmark = [pytest.mark.unit, pytest.mark.filterwarnings("ignore::sdmxlib.ImRuleWarning")]

#: These properties validate every generated document against the official XSD
#: or JSON schema, which costs far more than the round-trip itself — so they run
#: a tenth of the profile's examples. Scaled from the profile rather than fixed,
#: so `HYPOTHESIS_PROFILE=deep just test-properties` still explores them hard.
_EXAMPLES = max(5, present(settings.default).max_examples // 10)

#: (writer, schema-validator) per SDMX-ML version. Reading goes through the
#: auto-detecting `parse`, so the detection path (#220) is exercised too.
_XML_FORMATS = {
    "ml21": ("2.1", ml21_writer.write),
    "ml30": ("3.0", ml30_writer.write),
    "ml31": ("3.1", ml31_writer.write),
}

_FORMAT_IDS = [*_XML_FORMATS, "json"]

#: Test format id -> the `serialize(version=...)` value for the same target.
_WIRE_VERSION = {fmt: ver for fmt, (ver, _write) in _XML_FORMATS.items()} | {"json": "json"}


def _round_trip(fmt: str, msg: StructureMessage) -> StructureMessage:
    """Write *msg* in *fmt*, validate it against the official schema, read it back.

    The schema step is the reason this is stronger than a bare round-trip: our
    reader and writer share a blind spot, so a non-conformant document they
    agree on would otherwise pass silently.

    Args:
        fmt: One of ``ml21`` / ``ml30`` / ``ml31`` / ``json``.
        msg: Message to round-trip.

    Returns:
        The re-parsed message.
    """
    if fmt == "json":
        written = json_writer.write(msg)
        assert_valid_sdmx_json(written, compile_json_schema("structure"))
        return json_reader.read(written)
    version, write = _XML_FORMATS[fmt]
    written = write(msg)
    assert_valid_sdmx_ml(written, structure_schema(version))
    parsed = parse(written)
    assert isinstance(parsed, StructureMessage)
    return parsed


def _gap(fmt: str, case: str, field: str) -> bool:
    """Whether the shared table records *field* as uncarryable by *fmt*."""
    return (fmt, case, field) in KNOWN_GAPS


# ── Codelist ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", _FORMAT_IDS)
@settings(max_examples=_EXAMPLES)
@given(cl=codelists())
def test_codelist_round_trips(fmt: str, cl: Codelist):
    """Identity, name, description and the full code list survive a round-trip."""
    back = _round_trip(fmt, StructureMessage(codelists=[cl])).codelists[0]

    assert back.id == cl.id
    assert back.agency_id == cl.agency_id
    assert back.version == cl.version
    assert back.name == cl.name
    assert back.description == cl.description
    if not _gap(fmt, "codelist", "is_final"):
        assert back.is_final == cl.is_final

    assert list(back.codes.ids()) == list(cl.codes.ids()), "code identity or order changed"
    for original, returned in zip(cl.codes, back.codes, strict=True):
        assert returned.name == original.name, f"code {original.id} name"
        assert returned.description == original.description, f"code {original.id} description"


@pytest.mark.parametrize("fmt", _FORMAT_IDS)
@settings(max_examples=_EXAMPLES)
@given(cl=codelists())
def test_codelist_annotations_round_trip(fmt: str, cl: Codelist):
    """Annotations survive with their id, type, title and localised text.

    Kept separate from the identity property so a failure says which half
    broke — annotations are a distinct wire structure in every format.
    """
    back = _round_trip(fmt, StructureMessage(codelists=[cl])).codelists[0]
    assert len(back.annotations) == len(cl.annotations)
    for original, returned in zip(cl.annotations, back.annotations, strict=True):
        assert returned.id == original.id
        assert returned.type == original.type
        assert returned.title == original.title
        assert returned.text == original.text


# ── ConceptScheme ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", _FORMAT_IDS)
@settings(max_examples=_EXAMPLES)
@given(cs=concept_schemes())
def test_concept_scheme_round_trips(fmt: str, cs: ConceptScheme):
    """Concept identity, order and names survive."""
    back = _round_trip(fmt, StructureMessage(concept_schemes=[cs])).concept_schemes[0]
    assert back.id == cs.id
    assert back.agency_id == cs.agency_id
    assert back.version == cs.version
    assert back.name == cs.name
    assert list(back.concepts.ids()) == list(cs.concepts.ids())
    for original, returned in zip(cs.concepts, back.concepts, strict=True):
        assert returned.name == original.name, f"concept {original.id} name"


# ── Two formats must agree with each other ───────────────────────────────────


@settings(max_examples=_EXAMPLES)
@given(cl=codelists(), pair=st.sampled_from([(a, b) for a in _FORMAT_IDS for b in _FORMAT_IDS if a < b]))
def test_formats_agree_with_each_other(cl: Codelist, pair: tuple[str, str]):
    """Any two serializers must recover the same artefact.

    A round-trip property alone cannot catch a field that *both* sides of one
    format drop symmetrically — the classic blind spot the `KNOWN_GAPS` table
    exists to document. Comparing formats against each other does: a field one
    format carries and another silently loses shows up here even though each
    format round-trips its own output perfectly.
    """
    left, right = pair
    msg = StructureMessage(codelists=[cl])
    a = _round_trip(left, msg).codelists[0]
    b = _round_trip(right, msg).codelists[0]

    assert a.id == b.id
    assert a.name == b.name
    assert a.description == b.description
    assert list(a.codes.ids()) == list(b.codes.ids())
    for ca, cb in zip(a.codes, b.codes, strict=True):
        assert ca.name == cb.name, f"code {ca.id} name differs between {left} and {right}"


# ── DuckDB storage ───────────────────────────────────────────────────────────


@settings(max_examples=_EXAMPLES)
@given(cl=codelists())
def test_codelist_round_trips_through_storage(cl: Codelist):
    """The storage layer is a serializer too, and gets the same property."""
    conn = create_schema(duckdb.connect(":memory:"))
    put_codelist(conn, cl)
    back = get_artefact(conn, str(Codelist.urn_for(agency=cl.agency_id, id=cl.id, version=cl.version)), eager=True)
    assert back is not None
    assert back.id == cl.id
    assert back.name == cl.name
    assert list(back.codes.ids()) == list(cl.codes.ids())
    for original, returned in zip(cl.codes, back.codes, strict=True):
        assert returned.name == original.name, f"code {original.id} name"


@settings(max_examples=_EXAMPLES)
@given(cs=concept_schemes())
def test_concept_scheme_round_trips_through_storage(cs: ConceptScheme):
    conn = create_schema(duckdb.connect(":memory:"))
    put_concept_scheme(conn, cs)
    back = get_artefact(conn, str(ConceptScheme.urn_for(agency=cs.agency_id, id=cs.id, version=cs.version)), eager=True)
    assert back is not None
    assert back.id == cs.id
    assert list(back.concepts.ids()) == list(cs.concepts.ids())


# ── DataStructure ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", _FORMAT_IDS)
@settings(max_examples=_EXAMPLES)
@given(dsd=data_structures())
def test_data_structure_round_trips(fmt: str, dsd: DataStructure):
    """Components survive with their ids, order and representations.

    The centre of the model, and the most structurally complex artefact: the
    XSD constrains component ordering, id patterns and the representation
    choice, so the schema step in `_round_trip` is doing most of the work here.
    """
    back = _round_trip(fmt, StructureMessage(data_structures=[dsd])).data_structures[0]

    assert back.id == dsd.id
    assert back.agency_id == dsd.agency_id
    assert back.version == dsd.version
    assert back.name == dsd.name
    assert list(back.dimensions.ids()) == list(dsd.dimensions.ids()), "dimension identity or order"
    assert list(back.measures.ids()) == list(dsd.measures.ids()), "measure identity or order"
    assert list(back.attributes.ids()) == list(dsd.attributes.ids()), "attribute identity or order"


@pytest.mark.parametrize("fmt", _FORMAT_IDS)
@settings(max_examples=_EXAMPLES)
@given(dsd=data_structures())
def test_dimension_facets_round_trip(fmt: str, dsd: DataStructure):
    """Every facet bound on a dimension survives.

    `facets()` draws all eleven bounds independently, which is stronger than
    the fixtures: those set three or four between them, so the other seven were
    only ever exercised by the writer's own table.
    """
    back = _round_trip(fmt, StructureMessage(data_structures=[dsd])).data_structures[0]
    for original, returned in zip(dsd.dimensions, back.dimensions, strict=True):
        want, got = original.representation, returned.representation
        if not isinstance(want, Facet):
            continue
        assert isinstance(got, Facet), f"dimension {original.id}: representation kind changed"
        for bound in ("type", "max_length", "min_length", "decimals", "pattern", "is_sequence", "is_multilingual"):
            if _gap(fmt, "dsd", f"facet.{bound}"):
                continue
            assert getattr(got, bound) == getattr(want, bound), f"dimension {original.id}: facet.{bound}"


@pytest.mark.parametrize("fmt", _FORMAT_IDS)
@settings(max_examples=_EXAMPLES)
@given(dsd=data_structures())
def test_enumeration_format_round_trips(fmt: str, dsd: DataStructure):
    """A facet constraining an enumerated component's codes must survive.

    `RepresentationType` allows a codelist reference *and* a format together —
    "a code from CL_FREQ, and that code is one character". sdmxlib modelled
    only one or the other until #237, then carried the new field in the 2.1
    reader and writer alone, so it was silently dropped by 3.0, 3.1 and
    SDMX-JSON (#243).

    Stated as its own property rather than folded into the facet one above,
    because the two ask different questions: that one is about a component
    whose representation *is* a facet, this one about a component whose
    representation is an enumeration and which carries a facet beside it.

    Six mutations — dropping the field in either direction of any of the three
    formats — passed the whole suite before this existed. Emitting nothing is
    schema-valid, so validation cannot see it and only an explicit comparison
    can.
    """
    back = _round_trip(fmt, StructureMessage(data_structures=[dsd])).data_structures[0]
    for original, returned in zip(dsd.dimensions, back.dimensions, strict=True):
        want = original.enumeration_format
        if want is None:
            continue
        got = returned.enumeration_format
        assert got is not None, f"dimension {original.id}: enumeration_format dropped by {fmt}"
        for bound in ("type", "min_length", "max_length", "min_value", "max_value", "pattern", "is_sequence"):
            assert getattr(got, bound) == getattr(want, bound), f"dimension {original.id}: enumeration_format.{bound}"


# ── Dataflow ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", _FORMAT_IDS)
@settings(max_examples=_EXAMPLES)
@given(df=dataflows())
def test_dataflow_round_trips(fmt: str, df: Dataflow):
    """Identity survives, and the structure reference keeps its URN."""
    back = _round_trip(fmt, StructureMessage(dataflows=[df])).dataflows[0]
    assert back.id == df.id
    assert back.agency_id == df.agency_id
    assert back.version == df.version
    assert back.name == df.name
    assert back.structure is not None, "structure reference lost"
    assert back.structure.urn == present(df.structure).urn, "structure URN changed"


# ── validate() must agree with the schema (#221) ─────────────────────────────


@pytest.mark.parametrize("fmt", _FORMAT_IDS)
@settings(max_examples=_EXAMPLES)
@given(cl=codelists())
def test_validate_passing_implies_schema_valid(fmt: str, cl: Codelist):
    """If `validate` accepts an artefact, its serialisation must validate.

    This is the invariant #221 was really about. `validate` applied the
    permissive `IDType` to every id, so it accepted maintainable and agency ids
    the XSD rejects — and `serialize(validate=True)`, the default, reads as a
    promise that the output is valid.

    Stated over generated artefacts because the strategies now model all three
    id types (`IDType` for items, `NCNameIDType` for maintainables and
    concepts, `NestedNCNameIDType` for agencies); a disagreement means either
    the strategy or `validate` has drifted from the schemas.
    """
    _round_trip(fmt, StructureMessage(codelists=[cl]))


# ── AgencyScheme ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", _FORMAT_IDS)
@settings(max_examples=_EXAMPLES)
@given(scheme=agency_schemes())
def test_agency_scheme_round_trips(fmt: str, scheme: AgencyScheme):
    """Agencies survive with their ids, order and names."""
    back = _round_trip(fmt, StructureMessage(agency_schemes=[scheme])).agency_schemes[0]
    assert back.id == scheme.id
    assert list(back.agencies.ids()) == list(scheme.agencies.ids())
    for original, returned in zip(scheme.agencies, back.agencies, strict=True):
        assert returned.name == original.name, f"agency {original.id} name"


@pytest.mark.parametrize("fmt", _FORMAT_IDS)
@settings(max_examples=_EXAMPLES)
@given(scheme=agency_schemes())
def test_agency_contacts_round_trip(fmt: str, scheme: AgencyScheme):
    """Contact sub-fields survive.

    `emails` / `telephones` / `faxes` / `uris` each carry `minItems: 1` in the
    SDMX-JSON schema, so an empty one has to be omitted rather than emitted —
    the class of bug #216 found for `concepts` and `categories`. Drawing them
    empty-or-not exercises both sides.
    """
    back = _round_trip(fmt, StructureMessage(agency_schemes=[scheme])).agency_schemes[0]
    for original, returned in zip(scheme.agencies, back.agencies, strict=True):
        assert len(returned.contacts) == len(original.contacts), f"agency {original.id} contact count"
        for want, got in zip(original.contacts, returned.contacts, strict=True):
            for field_name in ("emails", "phones", "faxes", "uris"):
                if _gap(fmt, "agency", f"contact.{field_name}"):
                    continue
                assert tuple(getattr(got, field_name)) == tuple(getattr(want, field_name)), field_name


# ── Hierarchy ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", _FORMAT_IDS)
@settings(max_examples=_EXAMPLES)
@given(h=hierarchies())
def test_hierarchy_round_trips(fmt: str, h: Hierarchy):
    """The nested tree survives with its shape intact.

    Recursion is the interesting part: a flattening bug shows up as the same
    codes in a different arrangement, which a top-level id comparison alone
    would miss.
    """
    back = _round_trip(fmt, StructureMessage(hierarchies=[h])).hierarchies[0]
    assert back.id == h.id

    def shape(nodes: Iterable[HierarchicalCode]) -> list[tuple[str, object]]:
        return [(n.id, shape(n.children)) for n in nodes]

    assert shape(back.tree) == shape(h.tree), "tree shape changed"


# ── Registry-driven coverage for the remaining classes ───────────────────────
#
# The per-class properties above assert *specific* structure — component order,
# tree shape, contact sub-fields — because those are what each artefact is
# about. This table covers the rest with the property they all share: write it
# in every format, validate the document against the official schema, read it
# back, and check identity survives.
#
# Adding a class is one line here. That matters because the alternative — a
# bespoke property per class — is how the previous slices went, and the yield
# per class was falling while the boilerplate was not.

_REGISTRY: dict[str, tuple[Callable[[], st.SearchStrategy[object]], str]] = {
    "CategoryScheme": (category_schemes, "category_schemes"),
    "Categorisation": (categorisations, "categorisations"),
    "ProvisionAgreement": (provision_agreements, "provision_agreements"),
    "MetadataStructure": (metadata_structures, "metadata_structures"),
    "Metadataflow": (metadataflows, "metadataflows"),
    "DataConstraint": (data_constraints, "constraints"),
    "AttachmentConstraint": (attachment_constraints, "attachment_constraints"),
    "RepresentationMap": (representation_maps, "representation_maps"),
    "StructureMap": (structure_maps, "structure_maps"),
    "ConceptSchemeMap": (concept_scheme_maps, "concept_scheme_maps"),
    "CategorySchemeMap": (category_scheme_maps, "category_scheme_maps"),
    "OrganisationSchemeMap": (organisation_scheme_maps, "organisation_scheme_maps"),
    "DataProviderScheme": (data_provider_schemes, "data_provider_schemes"),
    "HierarchyAssociation": (hierarchy_associations, "hierarchy_associations"),
}


#: (wire version, message field) pairs the writer *converts* rather than
#: carrying as-is. SDMX 3.0 folded 2.1's two constraint artefacts into one, so
#: an AttachmentConstraint is emitted as the DataConstraint it equals — which
#: is what the old refusal advised and could not be done until DataConstraint
#: gained a key set (#241, #244).
_CONVERTED_ON_WRITE = {
    ("3.0", "attachment_constraints"),
    ("3.1", "attachment_constraints"),
    ("json", "attachment_constraints"),
}

#: (wire version, message field) pairs the writer can carry, but only when the
#: message supplies the context the format needs. 2.1 names a mapped component
#: by class and component-list id as well as by id, and both are read off the
#: mapped structure — so a StructureMap alone in a message has no 2.1 form
#: while the same map beside its structures does (#242). The lone-artefact
#: draw below is therefore a refusal, and
#: `test_structure_map_round_trips_in_21_beside_its_structures` covers the
#: case that converts.
_NEEDS_MESSAGE_CONTEXT = {("2.1", "structure_maps")}

#: (wire version, message field) pairs the target has no class for, so leaves
#: the artefact out whole and writes the rest (#421). Refused until then, which
#: blocked the whole message over one artefact the format cannot hold.
_LEFT_OUT = {("2.1", "hierarchy_associations")}


def _message_with(message_field: str, artefact: object) -> StructureMessage:
    """A one-artefact message, with the slot named at runtime.

    `message_field` comes from `_REGISTRY`, so no static type describes this
    splat: every keyword is checked against its own element type and a
    `list[object]` satisfies none of the 23. CLAUDE.md's rule for a genuinely
    dynamic step — one annotated local, so the `Any` dies here instead of at
    each of the four call sites (93 findings between them).
    """
    by_field: dict[str, Any] = {message_field: [artefact]}
    return StructureMessage(**by_field)


def _written_as_availability(wire: str, artefact: object) -> bool:
    """Whether `wire` writes `artefact` as an identity-less AvailabilityConstraint (#421)."""
    return (
        wire == "3.1"
        and isinstance(artefact, DataConstraint)
        and artefact.constraint_type is ConstraintType.ACTUAL
        and not artefact.availability_misfits()
    )


@pytest.mark.parametrize("fmt", _FORMAT_IDS)
@pytest.mark.parametrize("artefact_name", sorted(_REGISTRY))
@settings(max_examples=_EXAMPLES, deadline=None)
@given(data=st.data())
def test_registered_artefacts_round_trip(fmt: str, artefact_name: str, data: st.DataObject):
    """Every registered artefact survives a schema-validated round-trip.

    The schema step is what makes this more than a re-read: our reader and
    writer share a blind spot, so a non-conformant document they agree on
    would otherwise pass.
    """
    strategy, message_field = _REGISTRY[artefact_name]
    # `_REGISTRY` maps a name to a strategy for one of 23 unrelated artefact
    # types, so the drawn value has no useful static type — the annotation is
    # where that dies rather than at each comparison below.
    artefact: Any = data.draw(strategy())
    msg = _message_with(message_field, artefact)

    wire = _WIRE_VERSION[fmt]
    if message_field in _UNWRITABLE.get(wire, {}):
        # This target cannot carry the artefact, and until #225 dropped it
        # silently. Goes through `serialize` rather than `_round_trip` because
        # that is where `check_serialisable` runs — the writers are reachable
        # directly and still drop, which is exactly why the guard belongs at
        # the boundary every caller uses.
        with pytest.raises(ValidationError, match="cannot carry"):
            serialize(msg, version=wire)
        return

    if (wire, message_field) in _NEEDS_MESSAGE_CONTEXT:
        with pytest.raises(ValidationError, match="which the message does not carry"):
            serialize(msg, version=wire)
        return

    if (wire, message_field) in _LEFT_OUT:
        # Said, then done: `dropped` names it, and the document lacks it.
        assert [drop.urn for drop in dropped(msg, wire)] == [str(artefact_urn(artefact))]
        assert not getattr(_round_trip(fmt, msg), message_field)
        return

    if (wire, message_field) in _CONVERTED_ON_WRITE:
        # SDMX 3.0 unified 2.1's two constraint artefacts, so an
        # AttachmentConstraint is written as the DataConstraint it is
        # equivalent to. The content survives; the artefact *kind* does not,
        # because a 3.0 document does not record which of the two it was
        # (#244). Asserted rather than tolerated, in both directions.
        back = _round_trip(fmt, msg)
        assert not back.attachment_constraints, "the 2.1-only artefact must not survive as itself"
        assert len(back.constraints) == 1, f"{artefact_name} did not arrive as a DataConstraint on {wire}"
        # `artefact` is drawn from the strategy registry, so the checker only
        # knows `object`; the assertion above pins what actually arrived.
        converted = back.constraints[0]
        assert isinstance(artefact, AttachmentConstraint)
        assert converted.id == artefact.id
        assert converted.data_key_sets == artefact.data_key_sets
        # The attachment is the other half of what an AttachmentConstraint
        # says, and dropping it leaves valid output — `ConstraintAttachment`
        # is optional — so only an explicit comparison catches it.
        assert converted.attachment.is_empty == artefact.attachment.is_empty
        if not artefact.attachment.is_empty:
            assert str(present(converted.attachment.dataflows[0]).urn) == str(
                present(artefact.attachment.dataflows[0]).urn
            )
        return

    if _written_as_availability(wire, artefact):
        # 3.1 states Actual content as an identity-less AvailabilityConstraint
        # (#421). One that does not fit is refused by `serialize`, which
        # `_round_trip` bypasses, so it falls through to the checks below. A
        # fitting draw is rare at default width — `deep` reaches it; the
        # deterministic guard is `test_availability_constraint.py`.
        back = _round_trip(fmt, msg)
        assert (list(back.constraints), list(back.availability_constraints)) == (
            [],
            [artefact.to_availability_constraint()],
        )
        return

    back = _round_trip(fmt, msg)
    recovered: Sequence[Any] = getattr(back, message_field)
    assert len(recovered) == 1, f"{artefact_name} did not survive as {message_field}"
    returned = recovered[0]
    assert returned.id == artefact.id
    assert returned.agency_id == artefact.agency_id
    assert returned.name == artefact.name
    if not _gap(fmt, artefact_name.lower(), "version"):
        assert returned.version == artefact.version
    # A DataConstraint may select by whole key as well as by region; the field
    # existed nowhere until #241, so a key set was dropped by every format.
    if hasattr(artefact, "data_key_sets"):
        assert returned.data_key_sets == artefact.data_key_sets, f"{artefact_name}: data_key_sets"


#: Message fields exercised by a hand-written property earlier in this file
#: rather than through `_REGISTRY`.
_COVERED_BY_BESPOKE_PROPERTY = {
    "codelists",
    "concept_schemes",
    "data_structures",
    "dataflows",
    "agency_schemes",
    "hierarchies",
}

#: Fields with no generative coverage, and what a strategy for each would have
#: to build. Each needs a non-trivial sub-structure and the yield per class has
#: been falling, so they are listed rather than done. Tracked in #223.
_NO_GENERATIVE_COVERAGE: dict[str, str] = {
    # SDMX-ML 2.1 containers, covered by the document-fidelity harness rather
    # than generatively: the interesting property is that a *2.1 document's*
    # container survives a round-trip, which a generated model artefact cannot
    # express — the containers exist only on that one target (#239).
    "structure_sets": "2.1-only container; exercised by the document-fidelity fixtures",
    "hierarchical_codelists": "2.1-only container; exercised by the document-fidelity fixtures",
    # Gained a StructureMessage slot in #362. Every one of its fields is
    # compared across all four targets by the maximal-fixture census in
    # `test_field_coverage.py`, which is stronger than a generated round-trip
    # for this artefact — it has three fields and two of them are references,
    # so a strategy would generate the same shape every time.
    "metadata_provision_agreements": "every field covered by the #354 census in test_field_coverage.py",
    # Gained a slot in #315. Same reasoning: the maximal-fixture census
    # compares every field across all four targets, and it caught this
    # artefact's `is_final` the moment the fixture carried one — which a
    # generated round-trip over a two-field scheme would not have.
    "metadata_provider_schemes": "every field covered by the #354 census in test_field_coverage.py",
    # Gained slots in #469, on the same ground: the maximal fixture carries a
    # populated scheme of each, and the census compares every field across all
    # four targets and the store.
    "data_consumer_schemes": "every field covered by the #354 census in test_field_coverage.py",
    "organisation_unit_schemes": "every field covered by the #354 census in test_field_coverage.py",
    # Gained a slot in #422. The census compares every field across the 3.x and
    # JSON targets and the store; 2.1 leaves the artefact out, which
    # `test_value_lists.py` pins rather than a generated round-trip could.
    "value_lists": "every field covered by the #354 census in test_field_coverage.py",
    # Gained a slot in #421: SDMX 3.1's availability, which has no identity, so
    # no registry strategy or URN-keyed comparison applies. Its 3.1 round trip
    # is the shapes census's, and its conversions are `test_availability_constraint.py`'s.
    "availability_constraints": "3.1-only and identity-less; covered by the shapes census and its own tests",
}
# `MetadataSet` is deliberately absent: it is not a `StructureMessage` field at
# all but rides in a `MetadataMessage`, a different envelope with its own
# reader and writer. `MetadataProvisionAgreement` was in the same sentence
# until #362 gave it a slot — the stale-entry half of the check below is what
# caught them both.


def test_every_serialisable_field_is_covered_or_excused():
    """The matrix must not silently stop growing.

    Enumerates what `StructureMessage` can carry and insists each field is
    either exercised by a property here or listed with a reason. A gap nobody
    notices is how the coverage matrix got to 2 of 20 in the first place.

    Keyed on *field names*, not class names. An earlier version derived one
    from the other by lowercasing and stripping underscores and plurals, and
    it silently mismatched three fields — including `hierarchies`, which was
    covered all along. Deriving identifiers from each other by string surgery
    is how a guard ends up guarding nothing.
    """
    registered = {field for _strategy, field in _REGISTRY.values()}
    accounted = registered | _COVERED_BY_BESPOKE_PROPERTY | set(_NO_GENERATIVE_COVERAGE)
    carried = {f.name for f in attrs.fields(StructureMessage) if not f.name.startswith("_")}

    uncovered = sorted(carried - accounted)
    assert not uncovered, f"{uncovered} are carried by StructureMessage but neither covered nor excused"

    stale = sorted(accounted - carried)
    assert not stale, f"{stale} are listed here but are not StructureMessage fields"


#: Every (wire version, message field) pair the refusal table names.
_UNWRITABLE_PAIRS = sorted((version, field) for version, fields in _UNWRITABLE.items() for field in fields)


def _unwritable_exemplar(message_field: str) -> object:
    """The exemplar for *message_field*, insisting one exists.

    A new `_UNWRITABLE` entry with no exemplar would otherwise surface as a
    bare `KeyError`, which says nothing about what to do next.
    """
    exemplars = _one_of_each_unwritable_kind()
    assert message_field in exemplars, (
        f"_UNWRITABLE gained {message_field!r}; add an exemplar to "
        "_one_of_each_unwritable_kind so the entry can be justified"
    )
    return exemplars[message_field]


def _one_of_each_unwritable_kind() -> dict[str, object]:
    """A minimal artefact for each field `_UNWRITABLE` names.

    Hand-built rather than generated: the point is to exercise the *writer*,
    and a minimal artefact makes a failure unambiguous.
    """
    msd_urn = MetadataStructure.urn_for(agency="AG", id="MSD", version="1.0")
    dataflow = Ref[Dataflow].unresolved(Dataflow.urn_for(agency="AG", id="DF", version="1.0"))
    return {
        "metadataflows": Metadataflow(
            id="MDF",
            maintainer="AG",
            version="1.0",
            name=InternationalString(en="n"),
            structure=Ref[MetadataStructure].unresolved(msd_urn),
            targets=(str(Dataflow.urn_for(agency="AG", id="DF", version="1.0")),),
        ),
        "metadata_provision_agreements": MetadataProvisionAgreement(
            id="MPA",
            maintainer="AG",
            version="1.0",
            name=InternationalString(en="n"),
            metadataflow=Ref[Metadataflow].unresolved(Metadataflow.urn_for(agency="AG", id="MDF", version="1.0")),
            # `MetadataProvider.urn_for` now builds this — #315. Hand-rolling
            # it through `SdmxUrn.make(object, ...)` was the only way before
            # the class existed.
            metadata_provider=Ref[MetadataProvider].unresolved(
                MetadataProvider.urn_for(agency="AG", id="P1", version="1.0")
            ),
        ),
        "metadata_provider_schemes": MetadataProviderScheme(
            id="METADATA_PROVIDERS",
            maintainer="AG",
            version="1.0",
            name=InternationalString(en="n"),
            providers=[MetadataProvider(id="P1", name=InternationalString(en="p"))],
        ),
        "metadata_structures": MetadataStructure(
            id="MSD",
            maintainer="AG",
            version="1.0",
            name=InternationalString(en="n"),
            attributes=ItemList([MetadataAttribute(id="A", concept=concept_ref("A", agency="AG", scheme_id="CS"))]),
        ),
        "hierarchy_associations": HierarchyAssociation(
            id="HA",
            maintainer="AG",
            version="1.0",
            name=InternationalString(en="n"),
            linked_hierarchy=Ref[Hierarchy].unresolved(Hierarchy.urn_for(agency="AG", id="H", version="1.0")),
            linked_object=dataflow,
        ),
        "structure_sets": StructureSet(
            id="SS",
            maintainer="AG",
            version="1.0",
            name=InternationalString(en="n"),
        ),
        "hierarchical_codelists": HierarchicalCodelist(
            id="HCL",
            maintainer="AG",
            version="1.0",
            name=InternationalString(en="n"),
        ),
        "attachment_constraints": AttachmentConstraint(
            id="AC",
            maintainer="AG",
            version="1.0",
            name=InternationalString(en="n"),
            attachment=ConstraintAttachment(dataflows=(dataflow,)),
            data_key_sets=(DataKeySet(keys=(DataKey(values=(("FREQ", "A"),)),)),),
        ),
    }


#: A target that *can* carry each field, to prove the refusal is about the
#: target rather than the artefact being unwritable everywhere.
_CARRIED_BY = {
    "metadataflows": "ml30",
    "metadata_provision_agreements": "ml30",
    "metadata_provider_schemes": "ml30",
    "metadata_structures": "ml30",
    "hierarchy_associations": "ml30",
    "attachment_constraints": "ml21",
    "structure_sets": "ml21",
    "hierarchical_codelists": "ml21",
}


@pytest.mark.parametrize(("wire", "message_field"), _UNWRITABLE_PAIRS)
def test_writers_refuse_rather_than_dropping_what_they_cannot_write(wire: str, message_field: str):
    """Serialising must fail loudly on artefacts the target writer omits (#225).

    Until this check the loss was *silent*: `serialize` succeeded, the document
    validated, and the artefacts were simply gone. Found by the registry
    property above, which asked whether a generated metadataflow came back and
    got an empty list — and then, in the next slice, an attachment constraint.

    Stated separately from that property because it is the refusal that is
    under test, not the round-trip — and because a regression here would make
    the property pass for the wrong reason.
    """
    msg = _message_with(message_field, _unwritable_exemplar(message_field))
    carrier = _CARRIED_BY[message_field]
    assert len(getattr(_round_trip(carrier, msg), message_field)) == 1, f"{carrier} must still carry it"
    with pytest.raises(ValidationError, match="cannot carry"):
        serialize(msg, version=wire)


@pytest.mark.parametrize(("wire", "message_field"), _UNWRITABLE_PAIRS)
def test_unwritable_lists_exactly_what_each_writer_drops(wire: str, message_field: str):
    """Every entry in the table must be one that writer really loses.

    The table is a *refusal* list, so a wrong entry is not a harmless
    over-approximation — it rejects a serialisation that works. An earlier
    version was assembled by grepping the writer for field accesses and named
    all four map types; the 2.1 writer emits every one of them, and the table
    broke `test_serialize_ml21_complete`, which had been round-tripping a
    representation map through 2.1 the whole time.

    So the entries are justified against each writer's behaviour, not its
    source. This bypasses `serialize` deliberately — going through it would
    hit the very check being justified.

    Two failure modes count as justification, because both are real losses:
    the writer *drops* the artefact (metadataflows in 2.1), or it emits it in
    a form the official schema rejects. ``structure_maps`` in 2.1 used to be
    the example of the second, "where the element needs a component class the
    model does not carry" — which was the wrong diagnosis. The class is not
    carried by the *map*; it is carried by the structure the map names, so the
    entry was refusing a serialisation that works whenever the message holds
    that structure. It is gone, and the conditional refusal that replaced it
    lives in `_check_structure_map_in_21` (#242). What is not justified here
    is a clean, schema-valid round-trip — that is a working serialisation
    being refused.
    """
    msg = _message_with(message_field, _unwritable_exemplar(message_field))
    fmt = next(f for f, v in _WIRE_VERSION.items() if v == wire)
    written = json_writer.write(msg) if fmt == "json" else _XML_FORMATS[fmt][1](msg)

    schema_valid = True
    try:
        if fmt == "json":
            assert_valid_sdmx_json(written, compile_json_schema("structure"))
        else:
            assert_valid_sdmx_ml(written, structure_schema(wire))
    except AssertionError:
        schema_valid = False

    reader = json_reader.read if fmt == "json" else parse
    recovered = getattr(reader(written), message_field)
    assert len(recovered) == 0 or not schema_valid, (
        f"the {wire} writer round-trips {message_field} to schema-valid output, "
        "so _UNWRITABLE refuses a serialisation that works"
    )


#: Each target and the artefact class it lacks, so leaves out whole (#421).
_LEFT_OUT_PAIRS = sorted(
    [
        ("2.1", "hierarchy_associations"),
        *((wire, field) for wire in ("3.0", "3.1", "json") for field in ("hierarchical_codelists", "structure_sets")),
    ]
)


@pytest.mark.parametrize(("wire", "message_field"), _LEFT_OUT_PAIRS)
def test_a_class_the_target_lacks_is_left_out_and_named(wire: str, message_field: str):
    """Left out, not refused: `dropped` names it, and the rest is written (#421).

    The other half of the refusal guard above. These were refusals until #421,
    which blocked every message holding one — every 2.1 hierarchy a registry
    served could not reach 3.x. A carrier proves the target is what lacks it.
    """
    artefact = _one_of_each_unwritable_kind()[message_field]
    assert isinstance(artefact, MaintainableArtefact)
    msg = _message_with(message_field, artefact)
    carrier = _CARRIED_BY[message_field]
    assert len(getattr(_round_trip(carrier, msg), message_field)) == 1, f"{carrier} must still carry it"
    assert [drop.urn for drop in dropped(msg, wire)] == [str(artefact_urn(artefact))]
    fmt = next(f for f, v in _WIRE_VERSION.items() if v == wire)
    assert not getattr(_round_trip(fmt, msg), message_field)


@pytest.mark.parametrize("fmt", ["ml30", "ml31", "json"])
def test_artefact_level_structure_map_round_trips(fmt: str):
    """A StructureMap with no component maps survives the 3.x formats (#226).

    Needed because the generative property always draws component maps, so the
    empty case — a map between two structures with no component pairs — is
    otherwise never exercised.

    2.1 is excluded because this is the one structure-map shape it still
    cannot carry: ``StructureMapType`` requires at least one ComponentMap, so
    a map between two structures that pairs no components has nothing to
    write. `test_structure_map_round_trips_in_21_beside_its_structures`
    covers the shape that does convert.
    """
    dsd = DataStructure.urn_for(agency="AG", id="DSD", version="1.0")
    sm = StructureMap(
        id="SM",
        maintainer="AG",
        version="1.0",
        name=InternationalString(en="n"),
        source=Ref[DataStructure].unresolved(dsd),
        target=Ref[DataStructure].unresolved(DataStructure.urn_for(agency="AG", id="DSD2", version="1.0")),
        maps=(),
    )
    back = _round_trip(fmt, StructureMessage(structure_maps=[sm]))
    assert back.structure_maps.ids() == ["SM"]


def test_structure_map_with_no_component_maps_is_refused_by_21():
    """2.1 requires at least one ComponentMap, so an empty map has no form."""
    sm = StructureMap(
        id="SM",
        maintainer="AG",
        version="1.0",
        name=InternationalString(en="n"),
        source=Ref[DataStructure].unresolved(DataStructure.urn_for(agency="AG", id="DSD", version="1.0")),
        target=Ref[DataStructure].unresolved(DataStructure.urn_for(agency="AG", id="DSD2", version="1.0")),
        maps=(),
    )
    with pytest.raises(ValidationError, match="at least one ComponentMap"):
        serialize(StructureMessage(structure_maps=[sm]), version="2.1")


@settings(max_examples=_EXAMPLES, deadline=None)
@given(source=data_structures(), target=data_structures())
def test_structure_map_round_trips_in_21_beside_its_structures(source: DataStructure, target: DataStructure):
    """2.1 carries a StructureMap when the message carries what it maps (#242).

    The generative property draws each artefact alone, which for a structure
    map is exactly the case 2.1 cannot write — it references a component by
    class and component-list id, and both are read off the mapped structure.
    Supplied with the structures, the same map converts. The drawn components
    span the kinds, so the class is derived per component rather than assumed:
    a dimension, a time dimension, an attribute and a measure carry three
    different descriptor ids between them.

    Schema-validated on the way out, because the whole failure mode here is
    emitting a ``<Ref>`` that our own reader is happy to read back.
    """
    # Two draws can land on the same identity, which is a duplicate artefact
    # rather than a map between two structures — and the XSD says so.
    target = attrs.evolve(target, id=f"{target.id}_TARGET")

    def mappable(dsd: DataStructure) -> list[str]:
        """Every component id, time dimension included — `components` omits it."""
        return [c.id for c in dsd.components] + ([] if dsd.time_dimension is None else [dsd.time_dimension.id])

    pairs = tuple(
        ComponentMap(sources=(s,), targets=(t,)) for s, t in zip(mappable(source), mappable(target), strict=False)
    )
    sm = StructureMap(
        id="SM",
        maintainer="AG",
        version="1.0",
        name=InternationalString(en="n"),
        source=Ref.to(source),
        target=Ref.to(target),
        maps=pairs,
    )
    msg = StructureMessage(data_structures=[source, target], structure_maps=[sm])
    if not pairs:
        with pytest.raises(ValidationError, match="at least one ComponentMap"):
            serialize(msg, version="2.1")
        return

    back = _round_trip("ml21", msg)
    assert back.structure_maps.ids() == ["SM"]
    assert back.structure_maps[0].maps == pairs


# ── @uri, on every identifiable artefact (#238) ──────────────────────────────
#
# `uri` is declared on SDMX's `IdentifiableType`, so it lands on every
# maintainable, item and component — forty model classes and four formats. A
# field that wide is exactly the kind nothing notices going missing: it was
# read by nothing and written by nothing, and the only reason anyone found out
# is that the ECB's DSD links to its statistics vocabulary and the
# document-fidelity harness compared attributes.
#
# So it is asserted rather than assumed, artefact by artefact. The generative
# strategies do not draw a uri — adding one to forty strategies would state the
# property in the place least likely to be read — so these set it explicitly
# and check it comes back.

#: Artefacts whose format has no place to put a uri, with the reason.
#: Not "not implemented": a refusal of the format's own, in the same shape as
#: `KNOWN_GAPS`.
_URI_GAPS: dict[tuple[str, str], str] = {
    # Every SDMX-ML metadata set is fine; the *attributes* inside one are not.
    # `AttributeType` extends `AnnotableType`, not `IdentifiableType`, so a
    # reported attribute has no @uri in any format.
}

_URI = "https://example.org/vocabulary/thing/1"


@pytest.mark.parametrize("fmt", _FORMAT_IDS)
@pytest.mark.parametrize("artefact_name", sorted(_REGISTRY))
@settings(max_examples=max(3, _EXAMPLES // 2), deadline=None)
@given(data=st.data())
def test_uri_survives_on_every_maintainable(fmt: str, artefact_name: str, data: st.DataObject):
    """Setting `uri` on any registered artefact, it comes back unchanged."""
    strategy, message_field = _REGISTRY[artefact_name]
    # One of 23 unrelated artefact types, as in `test_registered_artefacts_round_trip`:
    # `attrs.evolve` needs a statically-known attrs class, which no narrowing of
    # the drawn `object` can supply, so the dynamic step dies on this local.
    drawn: Any = data.draw(strategy())
    artefact = attrs.evolve(drawn, uri=_URI)
    msg = _message_with(message_field, artefact)
    wire = _WIRE_VERSION[fmt]

    if (
        message_field in _UNWRITABLE.get(wire, {})
        or (wire, message_field) in _NEEDS_MESSAGE_CONTEXT
        or (wire, message_field) in _LEFT_OUT
    ):
        pytest.skip(f"{wire} cannot carry {message_field} at all — covered by the refusal and left-out guards")
    if (wire, message_field) in _CONVERTED_ON_WRITE:
        pytest.skip(f"{wire} converts {message_field} to another artefact kind")
    # Its uri goes with its identity, a declared loss (`formats.gaps`).
    assume(not _written_as_availability(wire, artefact))

    back = getattr(_round_trip(fmt, msg), message_field)
    assert len(back) == 1, f"{artefact_name} did not survive {fmt}"
    assert back[0].uri == _URI, f"{fmt} dropped {artefact_name}.uri"


@pytest.mark.parametrize("fmt", _FORMAT_IDS)
@settings(max_examples=max(3, _EXAMPLES // 2), deadline=None)
@given(cl=codelists(), cs=concept_schemes(), cats=category_schemes())
def test_uri_survives_on_items(fmt: str, cl: Codelist, cs: ConceptScheme, cats: CategoryScheme):
    """Items carry a uri too — `IdentifiableType`, not `MaintainableType`.

    The maintainable case above would pass with the item paths untouched,
    which is precisely the partial fix #238 warned about.
    """
    if not cl.codes or not cs.concepts or not cats.categories:
        pytest.skip("nothing drawn to carry a uri")
    cl = attrs.evolve(cl, codes=ItemList([attrs.evolve(cl.codes[0], uri=_URI), *list(cl.codes)[1:]]))
    cs = attrs.evolve(cs, concepts=ItemList([attrs.evolve(cs.concepts[0], uri=_URI), *list(cs.concepts)[1:]]))
    cats = attrs.evolve(
        cats, categories=ItemList([attrs.evolve(cats.categories[0], uri=_URI), *list(cats.categories)[1:]])
    )

    back = _round_trip(fmt, StructureMessage(codelists=[cl], concept_schemes=[cs], category_schemes=[cats]))
    assert back.codelists[0].codes[0].uri == _URI, f"{fmt} dropped Code.uri"
    assert back.concept_schemes[0].concepts[0].uri == _URI, f"{fmt} dropped Concept.uri"
    assert back.category_schemes[0].categories[0].uri == _URI, f"{fmt} dropped Category.uri"


@pytest.mark.parametrize("fmt", _FORMAT_IDS)
@settings(max_examples=max(3, _EXAMPLES // 2), deadline=None)
@given(dsd=data_structures())
def test_uri_survives_on_dsd_components(fmt: str, dsd: DataStructure):
    """Components are identifiable, so each of them carries a uri as well."""
    dsd = attrs.evolve(
        dsd,
        dimensions=ItemList([attrs.evolve(d, uri=_URI) for d in dsd.dimensions]),
        attributes=ItemList([attrs.evolve(a, uri=_URI) for a in dsd.attributes]),
        measures=ItemList([attrs.evolve(m, uri=_URI) for m in dsd.measures]),
        time_dimension=None if dsd.time_dimension is None else attrs.evolve(dsd.time_dimension, uri=_URI),
    )
    back = _round_trip(fmt, StructureMessage(data_structures=[dsd])).data_structures[0]
    for kind, components in (
        ("dimension", back.dimensions),
        ("attribute", back.attributes),
        ("measure", back.measures),
    ):
        for component in components:
            assert component.uri == _URI, f"{fmt} dropped {kind} {component.id}.uri"
    if back.time_dimension is not None:
        assert back.time_dimension.uri == _URI, f"{fmt} dropped time dimension uri"


# ── Reference metadata: the other envelope (#223) ────────────────────────────
#
# `MetadataMessage` is the second thing sdmxlib serialises, and until #234 it
# had neither generative coverage nor any schema validation of emitted XML —
# `serialize` returned early for it, skipping validation entirely, and
# `tests/support/xsd` exposed no metadata compiler so nothing ever checked the
# output against `SDMXMetadataGeneric.xsd`.
#
# It turns out no new compiler was needed: `SDMXMessage.xsd` is the entry point
# for the whole version, `<mes:GenericMetadata>` included. The schema was
# sitting there compiled the whole time; nobody had pointed a metadata document
# at it.

_METADATA_FORMATS = ["ml30", "json"]


def _metadata_round_trip(fmt: str, msg: MetadataMessage) -> MetadataMessage:
    """Write, validate against the official schema, read back."""
    if fmt == "json":
        written = serialize(msg, version="json")
        assert_valid_sdmx_json(written, compile_json_schema("metadata"))
    else:
        written = serialize(msg, version="3.0")
        assert_valid_sdmx_ml(written, structure_schema("3.0"))
    back = parse(written)
    assert isinstance(back, MetadataMessage)
    return back


def _flatten(
    attributes: Iterable[ReportedAttribute], prefix: str = "", *, as_text: bool = False
) -> list[tuple[str, object]]:
    """(path, value) for every reported attribute, depth-first.

    Compares the *tree*, not just the top level — nesting and sibling
    repetition are the two things the model is explicit about, so a comparison
    that only checked ids at depth 0 would miss both.

    Args:
        attributes: The ItemList to walk.
        prefix: Accumulated path, for recursion.
        as_text: Stringify scalar values before comparing. Needed for SDMX-ML
            3.0, where a reported value is element text with no type marker —
            the type comes from the MSD, which the message does not carry, so
            ``7`` reads back as ``"7"``. Recorded in `KNOWN_GAPS`; SDMX-JSON
            has native types and keeps all of them.

    Returns:
        (path, value) pairs in document order.
    """
    out: list[tuple[str, object]] = []
    for index, attribute in enumerate(attributes):
        path = f"{prefix}{index}:{attribute.id}"
        value = attribute.value
        if as_text and value is not None and not isinstance(value, InternationalString):
            value = str(value)
        out.append((path, value))
        out.extend(_flatten(attribute.children, f"{path}/", as_text=as_text))
    return out


@pytest.mark.parametrize("fmt", _METADATA_FORMATS)
@settings(max_examples=_EXAMPLES, deadline=None)
@given(ms=metadata_sets())
def test_metadata_set_round_trips(fmt: str, ms: MetadataSet):
    """A populated MetadataSet survives, tree and targets intact.

    One combination has no round trip and is asserted to *refuse* instead, which
    is the milestone's rule: a valid document or a `ValidationError`, never a
    silent invalid one. A set bound to an **unresolved** MetadataProvisionAgreement
    has no SDMX-ML 3.0 form, because the header's choice is ProvisionAgreement /
    StructureUsage / Structure and an MPA URN matches none of their patterns. It
    writes fine as JSON, which is why this is a per-format branch rather than a
    strategy exclusion — excluding it would drop the MPA arm from JSON coverage
    too (#232).
    """
    if fmt == "ml30" and ms.structure.urn.artefact_class == "MetadataProvisionAgreement":
        with pytest.raises(ValidationError):
            serialize(MetadataMessage(metadata_sets=[ms]), version="3.0")
        return
    back = _metadata_round_trip(fmt, MetadataMessage(metadata_sets=[ms])).metadata_sets[0]

    assert back.id == ms.id
    assert back.agency_id == ms.agency_id
    assert back.version == ms.version
    assert back.name == ms.name
    assert {str(t.urn) for t in back.targets} == {str(t.urn) for t in ms.targets}
    as_text = _gap(fmt, "metadataset", "attribute.value_type")
    assert _flatten(back.attributes, as_text=as_text) == _flatten(ms.attributes, as_text=as_text), (
        "reported attribute tree changed"
    )


@pytest.mark.parametrize("fmt", _METADATA_FORMATS)
def test_repeated_reported_attributes_stay_separate(fmt: str):
    """Repetition is sibling attributes sharing an id, never a values list.

    Stated as its own case because the generative property draws non-unique
    ids but is not guaranteed to produce a collision in any given run, and
    this is the modelling decision most likely to be "helpfully" collapsed by
    a future reader or writer change.
    """
    ms = MetadataSet(
        id="MS",
        maintainer="AG",
        version="1.0.0",
        name=InternationalString(en="n"),
        structure=Ref[Metadataflow].unresolved(Metadataflow.urn_for(agency="AG", id="MDF", version="1.0")),
        targets=[Ref[Codelist].unresolved(Codelist.urn_for(agency="AG", id="CL", version="1.0"))],
        attributes=ItemList(
            [
                ReportedAttribute(id="NOTE", value="first"),
                ReportedAttribute(id="NOTE", value="second"),
            ]
        ),
    )
    back = _metadata_round_trip(fmt, MetadataMessage(metadata_sets=[ms])).metadata_sets[0]
    assert [(a.id, a.value) for a in back.attributes] == [("NOTE", "first"), ("NOTE", "second")]


def test_metadata_message_is_validated_before_writing():
    """`serialize` must check a MetadataMessage, not just a StructureMessage.

    It returned early for the metadata envelope, so a set with no targets —
    which neither SDMX-ML 3.0 nor SDMX-JSON accepts — serialised silently to
    an invalid document. Every other finding on the metadata side was reachable
    only because this check did not exist (#234).
    """
    ms = MetadataSet(
        id="MS",
        maintainer="AG",
        version="1.0.0",
        name=InternationalString(en="n"),
        structure=Ref[Metadataflow].unresolved(Metadataflow.urn_for(agency="AG", id="MDF", version="1.0")),
        attributes=ItemList([ReportedAttribute(id="A", value="v")]),
    )
    msg = MetadataMessage(metadata_sets=[ms])
    for version in ("3.0", "json"):
        with pytest.raises(ValidationError, match="at least one target"):
            serialize(msg, version=version)
    # ...and the escape hatch still works, since `validate=False` means it.
    assert serialize(msg, version="3.0", validate=False)


def test_json_metadata_requires_a_semantic_version():
    """SDMX-JSON pins metadata VersionType to x.y.z; SDMX-ML states no pattern.

    So the model's default ``"1.0"`` is valid XML and invalid JSON, and a
    default-constructed MetadataSet had no valid SDMX-JSON form at all. The
    asymmetry is real and version-specific, which is why the check is scoped to
    the JSON target rather than applied to the model (#234).
    """
    ms = MetadataSet(
        id="MS",
        maintainer="AG",
        version="1.0",
        name=InternationalString(en="n"),
        structure=Ref[Metadataflow].unresolved(Metadataflow.urn_for(agency="AG", id="MDF", version="1.0")),
        targets=[Ref[Codelist].unresolved(Codelist.urn_for(agency="AG", id="CL", version="1.0"))],
        attributes=ItemList([ReportedAttribute(id="A", value="v")]),
    )
    msg = MetadataMessage(metadata_sets=[ms])
    with pytest.raises(ValidationError, match="semantic version"):
        serialize(msg, version="json")
    assert_valid_sdmx_ml(serialize(msg, version="3.0"), structure_schema("3.0"))


def test_every_metadata_message_field_is_covered():
    """`MetadataMessage` gets the same completeness guard as `StructureMessage`.

    The structure-side guard is keyed on `StructureMessage` fields, so it can
    say nothing about this envelope — which is exactly how `MetadataSet` came
    to be listed as a structure field, and then excused, rather than covered.
    """
    carried = {f.name for f in attrs.fields(MetadataMessage) if not f.name.startswith("_")}
    assert carried == {"metadata_sets"}, f"MetadataMessage gained {sorted(carried - {'metadata_sets'})}; cover it"


def test_metadataflow_round_trips_through_2_1_without_its_targets():
    """2.1 carries a metadataflow but not its targets (#225).

    `MetadataflowType` is a plain StructureUsage in 2.1 — Annotations?, Name,
    Description?, Structure? — with no ``<Target>`` element at all. SDMX 3.0
    added Target and made it mandatory, which is why the 3.0 property asserts
    the opposite.

    Until the 2.1 emitter existed the whole artefact was refused, and before
    that it was dropped in silence. Both halves are asserted here: the
    identity and structure reference survive, and the targets do not — so a
    regression in either direction shows up as a failure rather than as a
    quietly different document.
    """
    msd_urn = MetadataStructure.urn_for(agency="AG", id="MSD", version="1.0")
    mdf = Metadataflow(
        id="MDF",
        maintainer="AG",
        version="1.0",
        name=InternationalString(en="flow"),
        structure=Ref[MetadataStructure].unresolved(msd_urn),
        targets=(str(Dataflow.urn_for(agency="AG", id="DF", version="1.0")),),
    )
    back = _round_trip("ml21", StructureMessage(metadataflows=[mdf])).metadataflows[0]

    assert back.id == "MDF"
    assert back.agency_id == "AG"
    assert back.version == "1.0"
    assert back.name == mdf.name
    assert back.structure is not None
    assert str(back.structure.urn) == str(msd_urn), "the MSD reference must survive"

    assert _gap("ml21", "metadataflow", "targets"), "the loss below must stay recorded"
    assert back.targets == (), "2.1 has no Target element, so targets cannot survive"
    # ...and 3.0 does carry them, so this is a version difference rather than
    # a writer that forgets.
    assert _round_trip("ml30", StructureMessage(metadataflows=[mdf])).metadataflows[0].targets == mdf.targets
