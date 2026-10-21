"""Tests for SdmxUrn[T]."""

import pytest
from attrs.exceptions import FrozenInstanceError

from sdmxlib.model.urn import SdmxUrn

pytestmark = pytest.mark.unit

_FULL_URN = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ(1.0)"
_LATEST_URN = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ"
_ITEM_URN = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ(1.0).A"
_DSD_URN = "urn:sdmx:org.sdmx.infomodel.datastructure.DataStructure=ESTAT:CPI(2.0)"


def test_parse_full_urn():
    urn = SdmxUrn.parse(_FULL_URN)
    assert urn.package == "codelist"
    assert urn.artefact_class == "Codelist"
    assert urn.agency == "SDMX"
    assert urn.id == "CL_FREQ"
    assert urn.version == "1.0"
    assert urn.item_id is None


def test_parse_urn_without_version():
    urn = SdmxUrn.parse(_LATEST_URN)
    assert urn.version == "latest"
    assert urn.is_latest


def test_parse_item_urn():
    urn = SdmxUrn.parse(_ITEM_URN)
    assert urn.item_id == "A"
    assert urn.is_item_urn


def test_parse_dsd_urn():
    urn = SdmxUrn.parse(_DSD_URN)
    assert urn.package == "datastructure"
    assert urn.artefact_class == "DataStructure"
    assert urn.agency == "ESTAT"
    assert urn.id == "CPI"
    assert urn.version == "2.0"


def test_parse_invalid_urn():
    with pytest.raises(ValueError, match="Invalid SDMX URN"):
        SdmxUrn.parse("not-a-urn")


def test_str_round_trip():
    urn = SdmxUrn.parse(_FULL_URN)
    assert str(urn) == _FULL_URN


def test_str_latest_omits_version():
    urn = SdmxUrn.parse(_LATEST_URN)
    assert str(urn) == _LATEST_URN


def test_str_item_urn():
    urn = SdmxUrn.parse(_ITEM_URN)
    assert str(urn) == _ITEM_URN


def test_make_factory():
    urn = SdmxUrn.make(
        object,
        package="codelist",
        artefact_class="Codelist",
        agency="SDMX",
        id="CL_FREQ",
        version="1.0",
    )
    assert urn.id == "CL_FREQ"
    assert urn.agency == "SDMX"
    assert str(urn) == _FULL_URN


def test_make_invalid_agency():
    with pytest.raises(ValueError, match="Invalid agency"):
        SdmxUrn.make(
            object,
            package="codelist",
            artefact_class="Codelist",
            agency="BAD AGENCY",
            id="CL_FREQ",
        )


def test_make_invalid_id():
    with pytest.raises(ValueError, match="Invalid artefact"):
        SdmxUrn.make(
            object,
            package="codelist",
            artefact_class="Codelist",
            agency="SDMX",
            id="CL FREQ",
        )


def test_equality_and_hashing():
    a = SdmxUrn.parse(_FULL_URN)
    b = SdmxUrn.parse(_FULL_URN)
    assert a == b
    assert hash(a) == hash(b)


def test_frozen():
    urn = SdmxUrn.parse(_FULL_URN)
    with pytest.raises(FrozenInstanceError):
        urn.id = "other"  # type: ignore[misc]


def test_is_latest_false_for_versioned():
    urn = SdmxUrn.parse(_FULL_URN)
    assert not urn.is_latest


def test_is_item_urn_false_for_scheme():
    urn = SdmxUrn.parse(_FULL_URN)
    assert not urn.is_item_urn


# ── URN regex — parse edge cases ─────────────────────────────────────────────


def test_parse_agency_with_dots():
    """Agency IDs using NestedIDType (e.g. IMF.STAT, A.B.C) must parse correctly."""
    urn = SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=IMF.STAT:CL_FREQ(1.0)")
    assert urn.agency == "IMF.STAT"
    assert urn.id == "CL_FREQ"


def test_parse_agency_three_level_dots():
    urn = SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=A.B.C:CL_X(1.0)")
    assert urn.agency == "A.B.C"


def test_parse_item_id_with_nested_dots():
    """Item URNs can have dots in item_id for nested item references."""
    urn = SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=SDMX:CS_COMMON(1.0).PARENT.CHILD")
    assert urn.item_id == "PARENT.CHILD"
    assert urn.is_item_urn


def test_parse_multipart_version():
    urn = SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ(1.2.3)")
    assert urn.version == "1.2.3"


def test_parse_version_single_digit():
    urn = SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ(2)")
    assert urn.version == "2"


def test_parse_no_version_with_item():
    """URN without version parens but with item suffix."""
    urn = SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ.A")
    assert urn.version == "latest"
    assert urn.item_id == "A"


def test_parse_id_with_hyphen():
    urn = SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL-FREQ(1.0)")
    assert urn.id == "CL-FREQ"


def test_parse_id_with_at_sign():
    """@ is valid in SDMX IDType per SDMXCommonReferences.xsd."""
    urn = SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL@FREQ(1.0)")
    assert urn.id == "CL@FREQ"


def test_parse_id_with_dollar():
    """$ is valid in SDMX IDType per SDMXCommonReferences.xsd."""
    urn = SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL$FREQ(1.0)")
    assert urn.id == "CL$FREQ"


def test_parse_round_trip_dotted_agency():
    raw = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=IMF.STAT:CL_FREQ(1.0)"
    assert str(SdmxUrn.parse(raw)) == raw


def test_parse_round_trip_nested_item():
    raw = "urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=SDMX:CS_COMMON(1.0).PARENT.CHILD"
    assert str(SdmxUrn.parse(raw)) == raw


# Malformed URNs that must be rejected
@pytest.mark.parametrize(
    "bad_urn",
    [
        "not-a-urn",
        "",
        "urn:sdmx:org.sdmx.infomodel",  # no class=agency:id
        "urn:sdmx:org.sdmx.infomodel.codelist.Codelist",  # missing = separator
        "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=",  # empty agency:id
        "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX",  # no colon before id
        "urn:other:org.sdmx.infomodel.codelist.Codelist=SDMX:X",  # wrong urn namespace
        "URN:SDMX:org.sdmx.infomodel.codelist.Codelist=SDMX:X",  # uppercase prefix
    ],
)
def test_parse_invalid_urns_rejected(bad_urn: str) -> None:
    with pytest.raises(ValueError, match="Invalid SDMX URN"):
        SdmxUrn.parse(bad_urn)


# ── make() — ID and agency pattern boundary tests ────────────────────────────


@pytest.mark.parametrize(
    "valid_id",
    [
        "A",  # single char
        "CL_FREQ",  # uppercase + underscore
        "dsd-cpi",  # lowercase + hyphen
        "123",  # numeric only
        "CL@FREQ",  # @ sign (SDMX IDType spec)
        "CL$FREQ",  # $ sign (SDMX IDType spec)
        "A_@$-B",  # all non-alphanum chars combined
        "X" * 50,  # long id
    ],
)
def test_make_valid_ids(valid_id: str) -> None:
    urn = SdmxUrn.make(object, package="codelist", artefact_class="Codelist", agency="SDMX", id=valid_id)
    assert urn.id == valid_id


@pytest.mark.parametrize(
    "bad_id",
    [
        "",  # empty
        "CL FREQ",  # space
        "CL.FREQ",  # dot not allowed in IDType
        "CL/FREQ",  # slash
        "CL:FREQ",  # colon
        "CL(FREQ)",  # parens
        "CL=FREQ",  # equals
        "CL+FREQ",  # plus
        "héllo",  # non-ASCII
    ],
)
def test_make_invalid_ids_rejected(bad_id: str) -> None:
    with pytest.raises(ValueError, match="Invalid artefact"):
        SdmxUrn.make(object, package="codelist", artefact_class="Codelist", agency="SDMX", id=bad_id)


@pytest.mark.parametrize(
    "valid_agency",
    [
        "SDMX",  # simple
        "IMF",  # simple
        "IMF.STAT",  # one dot (NestedIDType)
        "A.B.C",  # two dots
        "MY_AGENCY",  # underscore
        "MY-AGENCY",  # hyphen
        "A",  # single char
        "AG@NCY",  # @ sign
        "AG$NCY",  # $ sign
    ],
)
def test_make_valid_agencies(valid_agency: str) -> None:
    urn = SdmxUrn.make(object, package="codelist", artefact_class="Codelist", agency=valid_agency, id="CL_X")
    assert urn.agency == valid_agency


@pytest.mark.parametrize(
    "bad_agency",
    [
        "",  # empty
        "MY AGENCY",  # space
        ".SDMX",  # leading dot — invalid NestedIDType
        "SDMX.",  # trailing dot — invalid NestedIDType
        "A..B",  # consecutive dots — invalid NestedIDType
        ".",  # dot only
        "..",  # double dot only
        "A:B",  # colon
        "A/B",  # slash
    ],
)
def test_make_invalid_agencies_rejected(bad_agency: str) -> None:
    with pytest.raises(ValueError, match="Invalid agency"):
        SdmxUrn.make(object, package="codelist", artefact_class="Codelist", agency=bad_agency, id="CL_X")
