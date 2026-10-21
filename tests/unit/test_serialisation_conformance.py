"""Which rules `Registry.add` enforces, and which wait for the writer (#224, #419).

A rule whose answer is the same in every format is `validate`'s, and
`Registry.add` runs it: a component with no ``ConceptIdentity`` has no form in
SDMX-ML 2.1, 3.0, 3.1 or SDMX-JSON, so a store holding one holds something it
can never serve. A rule whose answer depends on the format is
`check_serialisable`'s, asked only when a format is chosen: a DSD with no
dimensions is valid SDMX-JSON and refused by every SDMX-ML schema.

Until #419 the concept rule was the second kind, so a concept-less DSD stored
cleanly and serialised to a document every consumer rejects — found by putting
a generated DataStructure through the schema oracle (#223).
"""

from typing import Any

import attrs
import pytest

from sdmxlib.formats import serialize
from sdmxlib.formats.gaps import unrepresentable
from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model.collections import ItemList
from sdmxlib.model.constraint import CubeRegion, DataConstraint, KeyValue
from sdmxlib.model.datastructure import AttachmentLevel, Attribute, DataStructure, Dimension, Measure
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.organisation import AgencyScheme, DataProviderScheme, MetadataProviderScheme
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.representation import DataType, Facet
from sdmxlib.model.validation import ValidationError, check_serialisable
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


def _bound(name: str) -> dict[str, Any]:
    """One `Facet` bound, named at runtime."""
    return {name: 0}


_NAME = InternationalString(en="n")


def _dsd(*, with_concepts: bool) -> DataStructure:
    def ref(cid: str):
        return concept_ref(cid) if with_concepts else None

    return DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name=_NAME,
        dimensions=ItemList([Dimension(id="FREQ", concept=ref("FREQ"))]),
        attributes=ItemList([Attribute(id="UNIT", concept=ref("UNIT"), attachment=AttachmentLevel.DATASET)]),
        measures=ItemList([Measure(id="OBS_VALUE", concept=ref("OBS_VALUE"))]),
    )


# ── The split ────────────────────────────────────────────────────────────────


def test_a_dsd_without_concepts_is_refused_as_it_is_built():
    """No format can write it (#419), so it cannot be built (#492): `add` and every
    format's `serialize` refused it, and now nothing can be handed one to refuse."""
    with pytest.raises(ValidationError, match="ConceptIdentity is required"):
        _dsd(with_concepts=False)


def test_a_shape_only_sdmx_ml_refuses_is_stored():
    """The other half of the split: a DSD with no dimensions is valid SDMX-JSON."""
    dsd = DataStructure(id="DSD", maintainer="AG", version="1.0", name=_NAME)
    with LocalRegistry(":memory:") as reg:
        reg.add(dsd)
    assert serialize(StructureMessage(data_structures=[dsd]), version="json")
    with pytest.raises(ValidationError, match="requires a <Dimension>"):
        serialize(StructureMessage(data_structures=[dsd]), version="3.0")


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1", "json"])
def test_serialize_accepts_it_once_concepts_are_present(version: str):
    """The converse — the check must not block a conformant DSD."""
    assert serialize(StructureMessage(data_structures=[_dsd(with_concepts=True)]), version=version)


def test_the_check_names_every_offending_component():
    """All problems surface at once, rather than one per attempt."""
    with pytest.raises(ValidationError) as exc:
        _dsd(with_concepts=False)
    message = str(exc.value)
    for component in ("FREQ", "UNIT", "OBS_VALUE"):
        assert component in message, f"{component} not reported"


# ── Facet bounds the XSD types xs:positiveInteger ────────────────────────────


@pytest.mark.parametrize("bound", ["decimals", "max_length", "min_length"])
def test_zero_facet_bounds_are_refused_as_they_are_set(bound: str):
    """These are `xs:positiveInteger`, so 0 was emitted as invalid XML (#224).

    Refused by `Facet` itself (#492), so every representation is covered: a
    measure's, a concept's and a level's, not only the DSD components
    `validate()` used to walk.
    """
    with pytest.raises(ValidationError, match=rf"Facet\.{bound} — {bound} must be a positive integer"):
        Facet(type=DataType.String, **_bound(bound))
    with pytest.raises(ValidationError, match="must be a positive integer"):
        attrs.evolve(Facet(type=DataType.String), **_bound(bound))


# ── DataType members that no schema accepts ──────────────────────────────────


def test_removed_datatypes_are_gone():
    """`URL`, `ReportingHour`, `Year` and `Attachment` were in no schema enum.

    Each sat beside its valid counterpart — URI, GregorianYear, ReportingYear —
    and a Facet carrying one produced XML that failed validation in every
    SDMX-ML version.
    """
    values = {d.value for d in DataType}
    assert values.isdisjoint({"URL", "ReportingHour", "Year", "Attachment"})
    assert {"URI", "GregorianYear", "ReportingYear"} <= values, "the valid counterparts must remain"


# ── Version-specific downgrade hazards ───────────────────────────────────────
#
# Valid SDMX 3.0 with no 2.1 representation. Before #224 the writer emitted a
# document that failed schema validation and the first sign was a consumer
# rejecting the file.


def _conformant(**component_kwargs) -> DataStructure:
    return DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name=_NAME,
        dimensions=ItemList(
            [Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=Facet(**component_kwargs))]
        ),
        measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
    )


@pytest.mark.parametrize(
    ("label", "dsd"),
    [
        pytest.param("XHTML facet", _conformant(type=DataType.XHTML), id="xhtml"),
    ],
)
def test_valid_30_artefacts_that_cannot_be_written_as_21_are_refused(label: str, dsd: DataStructure):
    """Refused for 2.1, accepted for 3.0 — the artefact is fine, the target is not."""
    msg = StructureMessage(data_structures=[dsd])
    with pytest.raises(ValidationError):
        serialize(msg, version="2.1")
    assert serialize(msg, version="3.0"), f"{label} must remain valid SDMX 3.0"


def test_a_multilingual_attribute_is_left_out_of_2_1_and_declared() -> None:
    """2.1 gives an attribute the simple text format, which prohibits isMultiLingual; 3.x the basic one (#503).

    A declared drop, not a refusal: a 3.0 attribute is multilingual unless it
    says otherwise, so refusing would refuse nearly every 3.0 structure.
    """
    dsd = attrs.evolve(
        _conformant(),
        attributes=ItemList(
            [
                Attribute(
                    id="TITLE",
                    concept=concept_ref("TITLE"),
                    representation=Facet(is_multilingual=True),
                    attachment=AttachmentLevel.DATASET,
                )
            ]
        ),
    )
    assert [gap.field for gap in unrepresentable(dsd, "2.1")] == ["attributes[].representation.is_multilingual"]
    written = serialize(StructureMessage(data_structures=[dsd]), version="2.1")
    assert b"isMultiLingual" not in written
    assert b"isMultiLingual" not in serialize(StructureMessage(data_structures=[dsd]), version="3.0"), "3.0's default"


def test_a_multilingual_dimension_is_refused_as_it_is_built() -> None:
    with pytest.raises(ValidationError, match="prohibits isMultiLingual"):
        _conformant(is_multilingual=True)


# ── Organisation schemes carry fixed ids and versions ────────────────────────


@pytest.mark.parametrize(("cls", "fixed_id"), [(AgencyScheme, "AGENCIES"), (DataProviderScheme, "DATA_PROVIDERS")])
def test_organisation_scheme_ids_are_fixed(cls, fixed_id: str):
    """The XSD pins these with a `fixed` attribute, in every version.

    An AgencyScheme called anything else serialised to XML that failed
    validation, and nothing stopped you building one (#223).
    """
    cls(id=fixed_id, maintainer="AG", version="1.0", name=_NAME)
    with pytest.raises(ValidationError, match="is fixed to"):
        cls(id="MY_SCHEME", maintainer="AG", version="1.0", name=_NAME)


_OFF_VERSION = [
    pytest.param(AgencyScheme, "AGENCIES", id="AgencyScheme"),
    pytest.param(DataProviderScheme, "DATA_PROVIDERS", id="DataProviderScheme"),
    pytest.param(MetadataProviderScheme, "METADATA_PROVIDERS", id="MetadataProviderScheme"),
]


@pytest.mark.parametrize(("cls", "fixed_id"), _OFF_VERSION)
def test_organisation_scheme_version_is_refused_as_it_is_built(cls: type, fixed_id: str) -> None:
    """No format can carry an organisation scheme off version 1.0 (#416), so it cannot be built (#492).

    2.1 fixes the attribute, 3.0 and 3.1 prohibit it, and SDMX-JSON makes it a
    `const`. Two earlier versions of this rule each believed one exception: that
    3.x relaxed it (#230 — the writer dropped it and the reader brought back
    1.0), then that SDMX-JSON "round-trips the version untouched" (#416 — it
    did, schema-invalid, because nothing checked). And `MetadataProviderScheme`
    was never in the set, so its 3.x write lost the version silently. It was a
    writer's refusal in every format; it is the constructor's now.
    """
    cls(id=fixed_id, maintainer="AG", version="1.0", name=_NAME)
    with pytest.raises(ValidationError, match=rf"{cls.__name__} version is fixed to '1.0'"):
        cls(id=fixed_id, maintainer="AG", version="2.0", name=_NAME)


def test_a_provision_agreement_needs_both_its_edges() -> None:
    """`ProvisionAgreementType` requires a StructureUsage and a DataProvider in every SDMX-ML version (#225)."""
    pa = ProvisionAgreement(id="PA", maintainer="AG", name=_NAME)
    with pytest.raises(ValidationError) as caught:
        check_serialisable(StructureMessage(provision_agreements=[pa]))
    assert {issue.path for issue in caught.value.issues} == {
        "ProvisionAgreement:PA.dataflow",
        "ProvisionAgreement:PA.data_provider",
    }


@pytest.mark.parametrize("version", ["3.0", "3.1"])
def test_two_included_cube_regions_are_refused_in_3x(version: str) -> None:
    """`DataConstraint_CubeRegionInclusion` is unique over `@include`: one region of each kind (#226)."""
    region = CubeRegion(include=True, keys=[KeyValue(dimension_id="FREQ", values=["A"])])
    dc = DataConstraint(id="DC", maintainer="AG", name=_NAME, regions=[region, region])
    with pytest.raises(ValidationError, match="at most one CubeRegion with include=true"):
        check_serialisable(StructureMessage(constraints=[dc]), version=version)
