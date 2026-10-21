"""Every `xs:unique` in the schemas is enforced, mapped, or explained (#231).

The last unextracted constraint kind. `xsd_facts` already reads pattern facets,
enumerations, fixed attributes and required children out of the vendored XSDs;
identity constraints were the gap, and they are the kind easiest to miss by eye
because an `xs:unique` sits *beside* an element declaration rather than inside
its type. Reading `CodelistType` tells you nothing about `UniqueCodelist`
hanging off `<Codelists>`. `DataConstraint_CubeRegionInclusion` was found in
milestone 10 only because a generated constraint happened to draw two regions
with the same `include` flag.

## What the audit found

67 constraints in 2.1, 74 in 3.0, 76 in 3.1, and they fall into seven shapes:

    36  (@id, @agencyID, @version)   on containers   <- the gap, now fixed
    32  (@id,)                       on schemes      already enforced
     2  (@include,)                  constraints     one enforced, one unmodelled
     2  (@TYPE,)                     registry types  not modelled
     1  (@xml:lang,)                 Attribute text  structurally impossible
     1  (@alias,)                    VTL             not modelled
     1  (@type,)  2.1 only           MeasureDimension  unreachable (#305)

So one real gap, spread across 36 constraints: **nothing stopped a message
carrying two artefacts of the same class with the same id, agency and
version**. Both serialised happily and every schema rejected the result.

The seventh shape is 2.1-only and was missed on the first pass — it is exactly
what `test_every_constraint_is_accounted_for` exists to catch, and it caught it
while this file was being written. Chasing it down turned up a separate defect:
a DSD containing a `MeasureDimension` raises `AttributeError` from validation
and cannot be serialised in any version (#305). So `SingleMeasureDimension` is
recorded as *unreachable* rather than enforced — there is nothing to enforce it
against until that is fixed.

That is roughly the yield #231 predicted — "do not open this expecting 42
fixes; open it expecting a complete list where today there is none, and some
number of real gaps in the residue". The prediction was right about the
residue's *size* and wrong about where it sat: the issue spot-checked item-id
uniqueness, found it covered, and inferred the remaining risk was in the odd
constraints like CubeRegion. In fact the odd ones were all fine — enforced or
unmodelled — and the gap was in the largest, most uniform group, which is
exactly the group a spot-check treats as obviously handled.

## The mapping is the deliverable

`_STATUS` below resolves every constraint to one of four verdicts and names the
rule where one exists. That is what makes the next schema refresh cheap: a new
tag changes the extracted facts, and `test_every_constraint_is_accounted_for`
reports precisely which constraints are new or gone.
"""

import pytest

from sdmxlib.formats import serialize
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import ValidationError
from tests.support import xsd_facts
from tests.support.xsd import SchemaVersion, assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

#: How each constraint *shape* is accounted for. Keyed on the `xs:field` xpath
#: tuple, because the schemas state the same rule that many times over — 36
#: separate constraints say "maintainables of this class are unique in the
#: message" and there is no value in listing them one by one.
_STATUS: dict[tuple[str, ...], tuple[str, str]] = {
    ("@id", "@agencyID", "@version"): (
        "enforced",
        "validation._check_duplicate_maintainables — added by #231, this was the gap",
    ),
    ("@id",): (
        "enforced",
        "validation._check_duplicate_ids — item ids within a scheme",
    ),
    ("@include",): (
        "partly enforced",
        ("validation._check_cube_region_inclusion covers DataConstraint; MetadataConstraint is not modelled"),
    ),
    ("@TYPE",): (
        "not modelled",
        (
            "Datasource/EventSelector belong to the registry's registration and "
            "subscription messages, which sdmxlib does not model"
        ),
    ),
    ("@xml:lang",): (
        "structurally impossible",
        (
            "InternationalString is a mapping keyed by language, so a duplicate "
            "tag cannot be represented, let alone written"
        ),
    ),
    ("@alias",): (
        "not modelled",
        "VtlMappingScheme and the VTL artefacts are not modelled",
    ),
    ("@type",): (
        "unreachable",
        (
            "SingleMeasureDimension — at most one MeasureDimension per 2.1 "
            "DimensionList. Unreachable because a DSD holding a MeasureDimension "
            "cannot be serialised at all today (#305), so there is nothing to "
            "enforce the constraint against. Revisit with #305."
        ),
    ),
}


@pytest.mark.parametrize("version", xsd_facts.all_versions())
def test_every_constraint_is_accounted_for(version: SchemaVersion) -> None:
    """No `xs:unique` may go unmapped — the audit's standing guard.

    This is what makes a schema refresh cheap to re-audit: a new tag that adds
    a constraint of an unseen shape fails here, naming it, instead of being
    silently absent from anyone's mental model.
    """
    unmapped = [c for c in xsd_facts.unique_constraints(version) if c.fields not in _STATUS]
    assert not unmapped, (
        f"SDMX-ML {version} states xs:unique constraints of an unrecognised shape: "
        f"{[(c.owner, c.name, c.fields) for c in unmapped]}. "
        "Add each to _STATUS with a verdict and the rule that covers it."
    )


@pytest.mark.parametrize("version", xsd_facts.all_versions())
def test_the_audit_is_not_vacuous(version: SchemaVersion) -> None:
    """The guard above passes trivially if extraction returns nothing.

    It did once, while the extractor was being written — `xs:unique` nodes were
    being looked for under the wrong parent and the audit reported a clean
    bill over an empty set.
    """
    constraints = xsd_facts.unique_constraints(version)
    assert len(constraints) > 50, f"expected dozens of constraints in {version}, got {len(constraints)}"
    shapes = {c.fields for c in constraints}
    assert len(shapes) >= 5, f"expected several distinct constraint shapes, got {shapes}"


def test_the_status_table_has_no_dead_entries() -> None:
    """A mapping nothing matches is a claim about a constraint that is gone.

    Without this, a refresh that *removes* a constraint leaves an entry
    asserting coverage of something the schemas no longer state.
    """
    live = {c.fields for version in xsd_facts.all_versions() for c in xsd_facts.unique_constraints(version)}
    dead = set(_STATUS) - live
    assert not dead, f"_STATUS describes constraint shapes no schema states any more: {dead}"


# ── The gap this audit found ─────────────────────────────────────────────────


def _duplicate_message() -> StructureMessage:
    """Two distinct codelists sharing id, agency and version."""
    return StructureMessage(
        codelists=[
            Codelist(id="CL", maintainer="AG", version="1.0", name={"en": name}, codes=[Code(id="A", name={"en": "a"})])
            for name in ("first", "second")
        ]
    )


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1"])
def test_duplicate_maintainables_are_refused(version: SchemaVersion) -> None:
    """The gap, closed. Every SDMX-ML version states this constraint."""
    with pytest.raises(ValidationError, match="unique within the message"):
        serialize(_duplicate_message(), version=version)


def test_the_error_names_the_class_and_the_identity() -> None:
    with pytest.raises(ValidationError) as excinfo:
        serialize(_duplicate_message(), version="3.0")
    text = str(excinfo.value)
    assert "Codelist:CL" in text
    assert "2 Codelist artefacts" in text
    assert "'1.0'" in text, "the version is part of the identity and must be shown"


@pytest.mark.parametrize("version", ["2.1", "3.0"])
def test_the_xsd_agrees_that_it_is_invalid(version: SchemaVersion) -> None:
    """The schema's opinion, not ours — obtained by bypassing validation.

    If a future SDMX-ML revision dropped these constraints, this is the test
    that would notice and the rule could go.
    """
    written = serialize(_duplicate_message(), version=version, validate=False)
    with pytest.raises(AssertionError):
        assert_valid_sdmx_ml(written, compile_structure_schema(version))


def test_differing_in_any_one_field_is_fine() -> None:
    """The constraint is over the triple, so changing any part separates them.

    Guards against a fix that keyed on id alone, which would refuse two
    genuinely distinct versions of the same codelist — a normal thing to carry
    in one message.
    """
    for changed in ({"version": "2.0"}, {"maintainer": "OTHER"}, {"id": "CL2"}):
        other = {"id": "CL", "maintainer": "AG", "version": "1.0", **changed}
        msg = StructureMessage(
            codelists=[
                Codelist(
                    id="CL", maintainer="AG", version="1.0", name={"en": "a"}, codes=[Code(id="A", name={"en": "a"})]
                ),
                Codelist(
                    id=other["id"],
                    maintainer=other["maintainer"],
                    version=other["version"],
                    name={"en": "b"},
                    codes=[Code(id="A", name={"en": "a"})],
                ),
            ]
        )
        assert serialize(msg, version="3.0"), f"differing by {changed} must be allowed"


def test_item_id_uniqueness_is_the_other_rule_and_still_holds() -> None:
    """The `(@id,)` shape, checked so the mapping above is not taken on trust.

    Refused at construction (#492); #231 described it as refused by `validate`,
    which was where the rule ran until then.
    """
    with pytest.raises(ValidationError, match="duplicate code id"):
        Codelist(
            id="CL",
            maintainer="AG",
            version="1.0",
            name={"en": "n"},
            codes=[Code(id="A", name={"en": "a"}), Code(id="A", name={"en": "b"})],
        )
