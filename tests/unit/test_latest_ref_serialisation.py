"""A reference with no version cannot be written in any format (#233).

`SdmxUrn.version` defaults to the sentinel `"latest"`, which stringifies with
**no `(version)` part at all**:

    DataStructure.urn_for(agency="AG", id="DSD")
    -> urn:sdmx:org.sdmx.infomodel.datastructure.DataStructure=AG:DSD

SDMX-ML 3.0 references are URN *strings*, whose pattern facets require the
parenthesised version, so the same artefact wrote a 3.0 document the schema
rejects — with no error from us. SDMX-ML 2.1's `<Ref>` makes the version
optional, and was exempt for that reason until its XSD's own words were read:
an omitted version *defaults to 1.0*. The 2.1 document was valid and said
something nobody had said, so 2.1 refuses too.

## Why it survived

Every strategy in `tests/support/strategies.py` passes an explicit version when
building a reference, so the `"latest"` path was generated nowhere, and no
fixture used it either. It was found by hand while writing the MetadataSet
strategy for #223: the first target URN drawn used the default and both the XSD
and the JSON schema rejected it.

## Refuse, do not substitute

#233 lists resolving the reference to the artefact's real version at write time
as an option. That is worse than refusing: it silently changes *which artefact
the document names*. `"latest"` is a question — "whatever is newest when you
read this" — and a serialised reference has to be an answer. So the write stops
and the caller supplies one.

The blast radius was measured before choosing, as the issue asked: adding the
check broke **zero** existing tests, which is the other half of the story —
nothing in the suite exercised the sentinel on a write path at all.
"""

import functools

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats import serialize
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.message import MetadataMessage, StructureMessage
from sdmxlib.model.metadataset import MetadataSet
from sdmxlib.model.ref import Ref
from sdmxlib.model.validation import ValidationError
from tests.support.narrow import present
from tests.support.strategies import agency_ids, ncname_ids, versions
from tests.support.xsd import assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit


@functools.cache
def _schema_30() -> etree.XMLSchema:
    return compile_structure_schema("3.0")


def _dataflow(*, versioned: bool) -> Dataflow:
    """A dataflow whose DSD reference either carries a version or does not."""
    urn = (
        DataStructure.urn_for(agency="AG", id="DSD", version="1.0")
        if versioned
        else DataStructure.urn_for(agency="AG", id="DSD")
    )
    return Dataflow(
        id="DF",
        maintainer="AG",
        version="1.0",
        name={"en": "Flow"},
        structure=Ref[DataStructure].unresolved(urn),
    )


# ── The defect ───────────────────────────────────────────────────────────────


def test_the_sentinel_stringifies_without_a_version() -> None:
    """The root of it, stated once so the rest reads as consequence."""
    assert str(DataStructure.urn_for(agency="AG", id="DSD")).endswith("DataStructure=AG:DSD")
    assert str(DataStructure.urn_for(agency="AG", id="DSD", version="1.0")).endswith("DataStructure=AG:DSD(1.0)")


def test_writing_a_latest_reference_to_30_is_refused() -> None:
    """The headline: it used to emit invalid XML and say nothing."""
    msg = StructureMessage(dataflows=[_dataflow(versioned=False)])
    with pytest.raises(ValidationError, match="requires one on every URN reference"):
        serialize(msg, version="3.0")


def test_the_error_names_the_reference_and_the_fix() -> None:
    """A validation error that does not say which ref is a bug report, not a fix."""
    msg = StructureMessage(dataflows=[_dataflow(versioned=False)])
    with pytest.raises(ValidationError) as excinfo:
        serialize(msg, version="3.0")
    text = str(excinfo.value)
    assert "Dataflow:DF.structure" in text, "must name the path"
    assert "DataStructure=AG:DSD" in text, "must name the referenced artefact"
    assert "explicit version" in text, "must name the fix"


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1", "json"])
def test_every_format_refuses_it(version: str) -> None:
    msg = StructureMessage(dataflows=[_dataflow(versioned=False)])
    with pytest.raises(ValidationError):
        serialize(msg, version=version)


def test_21_refuses_it_because_its_schema_reads_no_version_as_1_0() -> None:
    """2.1 was exempt, on the reasoning that its `<Ref>` makes the version
    optional, so a legal document exists. It does — and the XSD documents the
    omitted `version` as *defaulting to 1.0*, so that legal document names 1.0
    to any reader that follows the schema: the silent change this module
    refuses for 3.0. The exemption went once that was read (#429's review).
    """
    msg = StructureMessage(dataflows=[_dataflow(versioned=False)])
    with pytest.raises(ValidationError, match=r"reads a reference with no version as version 1\.0"):
        serialize(msg, version="2.1")
    assert serialize(StructureMessage(dataflows=[_dataflow(versioned=True)]), version="2.1")


# ── The XSD, which is the actual authority ───────────────────────────────────


def test_a_versioned_reference_validates_and_that_is_the_contrast() -> None:
    """Proves the refusal is about the sentinel, not about the document shape.

    Without this, the test above would pass equally if the writer were broken
    in some unrelated way.
    """
    written = serialize(StructureMessage(dataflows=[_dataflow(versioned=True)]), version="3.0")
    assert_valid_sdmx_ml(written, _schema_30())
    assert b"DataStructure=AG:DSD(1.0)" in written


def test_the_xsd_rejects_what_we_now_refuse() -> None:
    """The check earns its place only if the schema really disagrees.

    Written by bypassing validation, so this asserts the *schema's* opinion
    rather than ours — if a future SDMX-ML revision relaxed the facet, this is
    the test that would notice and the refusal could go.
    """
    written = serialize(StructureMessage(dataflows=[_dataflow(versioned=False)]), version="3.0", validate=False)
    with pytest.raises(AssertionError):
        assert_valid_sdmx_ml(written, _schema_30())


# ── Reference metadata takes the same rule ───────────────────────────────────


def _metadata_set(*, versioned: bool) -> MetadataSet:
    target = (
        sl.Codelist.urn_for(agency="AG", id="CL", version="1.0")
        if versioned
        else sl.Codelist.urn_for(agency="AG", id="CL")
    )
    return MetadataSet(
        id="MDS",
        maintainer="AG",
        # x.y.z, because SDMX-JSON metadata requires semver where SDMX-ML does not.
        version="1.0.0",
        name={"en": "Set"},
        # A Metadataflow, not a MetadataStructure: #232 established that both
        # wire forms offer a choice of Metadataflow or MetadataProvisionAgreement
        # and nothing else. This fixture used an MSD and only "worked" because
        # the 3.0 writer mislabelled it as a <md:Metadataflow>.
        structure=Ref[object].unresolved(sl.Metadataflow.urn_for(agency="AG", id="MDF", version="1.0")),
        targets=[Ref[object].unresolved(target)],
    )


@pytest.mark.parametrize("version", ["3.0", "json"])
def test_a_metadata_target_needs_a_version_too(version: str) -> None:
    """Targets go through a different entry point and were missed at first.

    `check_metadata_serialisable` is a separate function from
    `check_serialisable`, so the structure-side fix does not reach it. There is
    no 2.1 exemption here at all: SDMX 3.0 dropped the v2.1 MetadataReport
    layer, so every wire form a MetadataSet has is a 3.x one.
    """
    msg = MetadataMessage(metadata_sets=[_metadata_set(versioned=False)])
    with pytest.raises(ValidationError, match="require one on a target URN"):
        serialize(msg, version=version)


@pytest.mark.parametrize("version", ["3.0", "json"])
def test_a_versioned_metadata_target_still_writes(version: str) -> None:
    msg = MetadataMessage(metadata_sets=[_metadata_set(versioned=True)])
    assert serialize(msg, version=version)


# ── Generative guard ─────────────────────────────────────────────────────────


@settings(max_examples=max(5, present(settings.default).max_examples // 5), deadline=None)
@given(
    agency=agency_ids(),
    dsd_id=ncname_ids(),
    flow_id=ncname_ids(),
    flow_version=versions(),
    ref_version=st.one_of(st.none(), versions()),
)
def test_the_version_of_a_reference_decides_whether_it_can_be_written(
    agency: str, dsd_id: str, flow_id: str, flow_version: str, ref_version: str | None
) -> None:
    """One property covering both branches, over generated ids.

    #233 suggested adding ``"latest"`` to `versions()` instead. That would be
    wrong now the behaviour is decided: artefact versions and *reference*
    versions are different things, and seeding the shared strategy would make
    every unrelated 3.0 round-trip property fail rather than cover this path.
    The sentinel is drawn here, where the assertion knows what to expect.

    Both branches in one property on purpose — an assertion that only ever saw
    the refusing case would pass just as well if `serialize` had been broken
    outright.
    """
    urn = (
        DataStructure.urn_for(agency=agency, id=dsd_id, version=ref_version)
        if ref_version is not None
        else DataStructure.urn_for(agency=agency, id=dsd_id)
    )
    flow = Dataflow(
        id=flow_id,
        maintainer=agency,
        version=flow_version,
        name={"en": "Flow"},
        structure=Ref[DataStructure].unresolved(urn),
    )
    msg = StructureMessage(dataflows=[flow])

    if ref_version is None:
        for version in ("2.1", "3.0"):
            with pytest.raises(ValidationError, match="explicit version"):
                serialize(msg, version=version)
    else:
        assert serialize(msg, version="2.1")
        assert b"(" + ref_version.encode() + b")" in serialize(msg, version="3.0")
