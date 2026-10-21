"""The gate: no artefact may serialise to an invalid document (#227).

For every artefact kind sdmxlib models and every wire target, `serialize` must
either produce a document that validates against the official schema, **or**
raise `ValidationError` naming what cannot be represented. Silently emitting an
invalid document is the failure mode this milestone exists to remove.

It is the worst of the three outcomes. The call succeeds, the bytes look
plausible, and the first sign of trouble is a consumer rejecting the file — by
which point sdmxlib is nowhere in the stack trace. Milestone 10 found ten
defects of exactly this shape, every one of them invisible to a suite that
already round-tripped all four formats, because a round-trip compares an
author-written list of fields and a dropped or malformed one is precisely the
field nobody listed.

**This module fixes nothing.** `KNOWN_INVALID` records every pair that fails
today, with the schema's own words. It is the milestone's burn-down: later
issues remove entries, and the milestone closes when it is empty. Entries are
asserted *still broken*, so fixing one fails this test until the entry goes —
a stale exemption is as much a defect as the bug it once described.

Bare artefacts are the floor, not the ceiling: this catches missing **required
children**. Constraints that only bite on populated artefacts — uniqueness,
required attributes, prohibited attributes — are phases 4 and 5.
"""

from collections.abc import Callable
from typing import Any, TypedDict

import pytest

import sdmxlib as sl
from sdmxlib.formats import serialize
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import MetadataMessage, StructureMessage
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import ValidationError
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema
from tests.support.roundtrip import structure_schema
from tests.support.xsd import assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

_WIRE_VERSIONS = ("2.1", "3.0", "3.1", "json")

_WRITERS = {
    "2.1": ml21_writer.write,
    "3.0": ml30_writer.write,
    "3.1": ml31_writer.write,
    "json": json_writer.write,
}


class _Base(TypedDict):
    """The shape `_base` returns, so `**_base()` splats with real types.

    A `dict[str, object]` here cost 263 findings on its own — every keyword of
    every constructor below, none of them a defect. CLAUDE.md's rule for a
    known-shape payload, applied.
    """

    maintainer: str
    version: str
    name: InternationalString


def _message_with(field: str) -> sl.StructureMessage:
    """A message holding one bare artefact, with the slot named at runtime.

    `field` is a `StructureMessage` slot name from `_BARE_ARTEFACTS`, so no
    static type describes this splat — every keyword is checked against its own
    element type and a `list[object]` satisfies none of the 23. One annotated
    local, per CLAUDE.md's rule for a genuinely dynamic step.
    """
    by_field: dict[str, Any] = {field: [_BARE_ARTEFACTS[field]()]}
    return sl.StructureMessage(**by_field)


def _base() -> _Base:
    """The fields every maintainable needs, and nothing else.

    Deliberately minimal: the question is what happens to an artefact carrying
    only its identity, which is the state a caller reaches first and the one
    the schemas have the most to say about.
    """
    return {"maintainer": "AG", "version": "1.0", "name": InternationalString(en="n")}


#: One bare artefact per `StructureMessage` field. Built lazily so a
#: constructor that starts raising shows up as a test failure rather than a
#: collection error.
_BARE_ARTEFACTS: dict[str, Callable[[], object]] = {
    "codelists": lambda: sl.Codelist(id="X", **_base()),
    "value_lists": lambda: sl.ValueList(id="X", **_base()),
    "concept_schemes": lambda: sl.ConceptScheme(id="X", **_base()),
    "categorisations": lambda: sl.Categorisation(id="X", **_base()),
    "category_schemes": lambda: sl.CategoryScheme(id="X", **_base()),
    "dataflows": lambda: sl.Dataflow(id="X", **_base()),
    "data_structures": lambda: sl.DataStructure(id="X", **_base()),
    "hierarchies": lambda: sl.Hierarchy(id="X", **_base()),
    "hierarchy_associations": lambda: sl.HierarchyAssociation(id="X", **_base()),
    "agency_schemes": lambda: sl.AgencyScheme(id="AGENCIES", **_base()),
    "data_provider_schemes": lambda: sl.DataProviderScheme(id="DATA_PROVIDERS", **_base()),
    "metadata_provider_schemes": lambda: sl.MetadataProviderScheme(id="METADATA_PROVIDERS", **_base()),
    "data_consumer_schemes": lambda: sl.DataConsumerScheme(id="DATA_CONSUMERS", **_base()),
    "organisation_unit_schemes": lambda: sl.OrganisationUnitScheme(id="X", **_base()),
    "structure_maps": lambda: sl.StructureMap(id="X", **_base()),
    "concept_scheme_maps": lambda: sl.ConceptSchemeMap(id="X", **_base()),
    "category_scheme_maps": lambda: sl.CategorySchemeMap(id="X", **_base()),
    "organisation_scheme_maps": lambda: sl.OrganisationSchemeMap(id="X", **_base()),
    "representation_maps": lambda: sl.RepresentationMap(id="X", **_base()),
    "constraints": lambda: sl.DataConstraint(id="X", **_base()),
    "attachment_constraints": lambda: sl.AttachmentConstraint(id="X", **_base()),
    "provision_agreements": lambda: sl.ProvisionAgreement(id="X", **_base()),
    "metadata_structures": lambda: sl.MetadataStructure(id="X", **_base()),
    "metadataflows": lambda: sl.Metadataflow(id="X", **_base()),
    "metadata_provision_agreements": lambda: sl.MetadataProvisionAgreement(id="X", **_base()),
    # 2.1-only containers. A bare one is legal there and refused elsewhere,
    # which is exactly what the gate asserts.
    "structure_sets": lambda: sl.StructureSet(id="X", **_base()),
    "hierarchical_codelists": lambda: sl.HierarchicalCodelist(id="X", **_base()),
    # 3.1's alone, and no artefact: no identity to give it, only a target and
    # a region (#421). Left out of every other target.
    "availability_constraints": lambda: sl.AvailabilityConstraint(
        attachment=sl.ConstraintAttachment(
            dataflows=(sl.Ref[object].unresolved(sl.Dataflow.urn_for(agency="X", id="X", version="1.0")),),
        ),
        region=sl.CubeRegion(),
    ),
}

#: (message field, wire version) -> the schema's own complaint.
#:
#: The burn-down for milestone 11 — **now empty** (#229). It opened with
#: twenty-four entries across eleven artefact kinds, all the same shape: the
#: model makes a field optional so a partially-assembled artefact stays
#: *storable*, and nothing re-asked the question at the write boundary.
#:
#: Six turned out to be writer bugs rather than missing rules — an element or
#: JSON key that is itself optional, emitted empty instead of omitted — and
#: eighteen became rules in `validation._REQUIRED_CONTENT`. Keeping the table
#: rather than deleting it: the next artefact kind, or the next schema
#: refresh, will want somewhere to record a gap that is understood but not yet
#: fixed.
#:
#: Reasons name the element and the child the schema wanted, trimmed from its
#: own message — enough to act on without re-running the validator. Two forms,
#: kept parseable on purpose so `test_xsd_conformance` can check the schema
#: extraction against these observed failures:
#:
#:     "Element: missing Child"              — a required child element
#:     "Element: missing one of A|B|C"       — a required xs:choice
#:
#: When one of these is fixed the entry must be deleted, and
#: `test_bare_artefact_is_valid_or_refused` fails until it is.
KNOWN_INVALID: dict[tuple[str, str], str] = {}


def _is_schema_valid(wire: str, msg: StructureMessage) -> bool:
    """Whether *msg* written as *wire* validates against the official schema.

    Goes through the raw writer rather than `serialize`, because the caller has
    already established that `serialize` did not refuse — re-entering it would
    just repeat the validation step.
    """
    written = _WRITERS[wire](msg)
    try:
        if wire == "json":
            assert_valid_sdmx_json(written, compile_json_schema("structure"))
        else:
            assert_valid_sdmx_ml(written, structure_schema(wire))
    except AssertionError:
        return False
    return True


@pytest.mark.parametrize("wire", _WIRE_VERSIONS)
@pytest.mark.parametrize("field", sorted(_BARE_ARTEFACTS))
def test_bare_artefact_is_valid_or_refused(field: str, wire: str):
    """Valid, or refused with a reason. Never a quietly-invalid document.

    `KNOWN_INVALID` entries are asserted *still* broken. That direction is
    deliberate: an exemption that outlives the bug it describes is worse than
    no exemption, because it goes on granting permission nobody re-examines.
    Fixing one of these fails here until the entry is deleted.
    """
    msg = _message_with(field)
    expected_invalid = (field, wire) in KNOWN_INVALID

    try:
        serialize(msg, version=wire)
    except ValidationError:
        assert not expected_invalid, (
            f"({field}, {wire}) is now refused rather than emitted invalid — delete its KNOWN_INVALID entry"
        )
        return

    valid = _is_schema_valid(wire, msg)
    if expected_invalid:
        assert not valid, f"({field}, {wire}) now serialises validly — delete its KNOWN_INVALID entry"
    else:
        assert valid, (
            f"a bare {field} serialises to a document SDMX {wire} rejects, with no refusal. "
            "Either fix it, or record it in KNOWN_INVALID with the schema's complaint"
        )


@pytest.mark.parametrize("wire", _WIRE_VERSIONS)
@pytest.mark.parametrize("field", sorted(_BARE_ARTEFACTS))
def test_refusals_are_validation_errors(field: str, wire: str):
    """A refusal must be a `ValidationError`, not just any exception.

    The invariant is *valid or refused with a reason*, and "a reason" means a
    `ValidationError` carrying `issues` a caller can inspect — not an exception
    type that happens to escape a writer.

    This caught the 2.1 `DataStructure` path raising a bare `ValueError` from
    inside the writer, mid-document. The rule it enforced was right —
    `MeasureListType` requires a `<PrimaryMeasure>`, so a measure-less DSD has
    no 2.1 encoding — but it fired after `serialize` had already decided the
    message was writable, which is the wrong place to learn it is not.
    """
    msg = _message_with(field)
    try:
        serialize(msg, version=wire)
    except ValidationError:
        pass
    except Exception as exc:  # noqa: BLE001 — the point is that nothing else may escape
        pytest.fail(
            f"serialising a bare {field} to {wire} raised {type(exc).__name__}: {exc}. "
            "A refusal must be a ValidationError so callers can inspect .issues"
        )


def test_every_message_field_has_a_bare_artefact():
    """A new `StructureMessage` field must be added to `_BARE_ARTEFACTS`.

    Without this the gate silently stops covering the model — the same failure
    the structure-side coverage guard exists to prevent, and the reason the
    matrix reached 2-of-20 before anyone counted.
    """
    import attrs

    carried = {f.name for f in attrs.fields(StructureMessage) if not f.name.startswith("_")}
    missing = sorted(carried - set(_BARE_ARTEFACTS))
    assert not missing, f"{missing} are carried by StructureMessage but have no bare artefact in the gate"

    stale = sorted(set(_BARE_ARTEFACTS) - carried)
    assert not stale, f"{stale} are in the gate but are not StructureMessage fields"


def test_known_invalid_only_names_real_fields():
    """Guard the burn-down table's own keys.

    A typo'd field name would make an entry unreachable — it would exempt
    nothing, and the pair it was meant to describe would fail with a confusing
    message about recording something already recorded.
    """
    bad_fields = sorted({field for field, _wire in KNOWN_INVALID if field not in _BARE_ARTEFACTS})
    assert not bad_fields, f"KNOWN_INVALID names {bad_fields}, which are not in _BARE_ARTEFACTS"

    bad_wires = sorted({wire for _field, wire in KNOWN_INVALID if wire not in _WIRE_VERSIONS})
    assert not bad_wires, f"KNOWN_INVALID names wire versions {bad_wires}, which do not exist"


# ── The other envelope (#232) ────────────────────────────────────────────────
#
# `MetadataMessage` is the second thing sdmxlib serialises, and the gate above
# only ever saw `StructureMessage`. The same invariant applies to it: a valid
# document, or a `ValidationError` naming what cannot be represented.
#
# The set below is the cross product of the structure binding the schema allows
# with the two wire forms — deliberately including the combination that has no
# 3.0 representation, since "refused with a reason" is a passing outcome here
# and asserting it is the point.

_METADATA_WIRES = ("3.0", "json")

#: How the set's structure reference is built. `MetadataSetType` sequences a
#: choice of MetadataProvisionAgreement or Metadataflow, so these are the two
#: legal bindings; a MetadataStructure reference is refused outright and is
#: covered by `test_metadata_structure_binding_is_refused`.
_METADATA_BINDINGS = {
    "metadataflow": lambda: Ref[object].unresolved(Metadataflow.urn_for(agency="AG", id="MDF", version="1.0")),
    "provision_agreement": lambda: Ref[object].unresolved(
        MetadataProvisionAgreement.urn_for(agency="AG", id="MPA", version="1.0")
    ),
}


def _bare_metadata_set(binding: str) -> MetadataSet:
    """The smallest MetadataSet that is not refused for some unrelated reason.

    Targets are non-empty and the version is semver because both are required
    on the wire — leaving either out would make every case here refuse for a
    reason that has nothing to do with the binding under test.
    """
    return MetadataSet(
        id="MDS",
        maintainer="AG",
        version="1.0.0",
        name={"en": "Set"},
        structure=_METADATA_BINDINGS[binding](),
        targets=[Ref[object].unresolved(Codelist.urn_for(agency="AG", id="CL", version="1.0"))],
        attributes=[ReportedAttribute(id="NOTE", value="text")],
    )


@pytest.mark.parametrize("wire", _METADATA_WIRES)
@pytest.mark.parametrize("binding", sorted(_METADATA_BINDINGS))
def test_metadata_message_is_valid_or_refused(binding: str, wire: str):
    """The gate's invariant, on the metadata envelope.

    `(provision_agreement, 3.0)` refuses: the header's choice is
    ProvisionAgreement / StructureUsage / Structure, and an unresolved
    MetadataProvisionAgreement URN matches none of those pattern facets. That
    is a pass — the rule is *valid or refused*, not *always writable*.
    """
    msg = MetadataMessage(metadata_sets=[_bare_metadata_set(binding)])
    try:
        written = serialize(msg, version=wire)
    except ValidationError:
        return

    if wire == "json":
        assert_valid_sdmx_json(written, compile_json_schema("metadata"))
    else:
        assert_valid_sdmx_ml(written, compile_structure_schema("3.0"))


@pytest.mark.parametrize("wire", _METADATA_WIRES)
@pytest.mark.parametrize("binding", sorted(_METADATA_BINDINGS))
def test_metadata_refusals_are_validation_errors(binding: str, wire: str):
    """A metadata refusal must also be a `ValidationError`, not any exception.

    Before #232 an unresolved MetadataProvisionAgreement did not refuse at all;
    it was written into `<com:Structure>`, whose facet demands a
    MetadataStructure URN, so the failure surfaced as a schema rejection at the
    consumer rather than as an error here.
    """
    msg = MetadataMessage(metadata_sets=[_bare_metadata_set(binding)])
    try:
        serialize(msg, version=wire)
    except ValidationError:
        pass
    except Exception as exc:  # noqa: BLE001 — the point is that nothing else may escape
        pytest.fail(f"serialising a {binding} metadata set to {wire} raised {type(exc).__name__}: {exc}")


_MSD_URN = MetadataStructure.urn_for(agency="AG", id="MSD", version="1.0")


@pytest.mark.parametrize("wire", _METADATA_WIRES)
def test_metadata_structure_binding_is_refused(wire: str):
    """An MSD is not one of the two bindings either wire form offers.

    It used to serialise: the 3.0 writer emitted `<md:Metadataflow>` holding an
    MSD URN, which the pattern facet rejects, and SDMX-JSON rejected it too.
    Both now refuse before any bytes are produced (#232).
    """
    msg = MetadataMessage(
        metadata_sets=[
            MetadataSet(
                id="MDS",
                maintainer="AG",
                version="1.0.0",
                name={"en": "Set"},
                # Parsed from text rather than built from `MetadataStructure`,
                # because `structure` accepts only the two refs SDMX 3.0's
                # `MetadataSetType` offers and this test exists to feed it a
                # third. Parsing is also the honest shape: an unresolved ref
                # off the wire carries a URN and no known target type, which is
                # exactly why the refusal has to happen at serialise time.
                structure=Ref[object].unresolved(SdmxUrn.parse(str(_MSD_URN))),  # pyrefly: ignore[bad-argument-type]
                targets=[Ref[object].unresolved(Codelist.urn_for(agency="AG", id="CL", version="1.0"))],
                attributes=[ReportedAttribute(id="NOTE", value="text")],
            )
        ]
    )
    with pytest.raises(ValidationError, match="Metadataflow or a MetadataProvisionAgreement"):
        serialize(msg, version=wire)
