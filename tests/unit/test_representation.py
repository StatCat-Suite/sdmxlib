"""Tests for DataType, Facet and Representation alias."""

import pytest
from attrs.exceptions import FrozenInstanceError

from sdmxlib.model.codelist import Codelist
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import DataType, Facet

pytestmark = pytest.mark.unit

_CL_URN = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ(1.0)"


# ── DataType ──────────────────────────────────────────────────────────────


def test_datatype_is_str():
    assert DataType.String == "String"
    assert DataType.ObservationalTimePeriod == "ObservationalTimePeriod"
    assert DataType.Integer == "Integer"


def test_datatype_str_compatible():
    facet = Facet(type="String")
    assert facet.type == DataType.String


# ── Facet ─────────────────────────────────────────────────────────────────


def test_facet_default():
    f = Facet()
    assert f.type is None
    assert f.max_length is None


def test_facet_with_fields():
    f = Facet(type=DataType.String, max_length=100)
    assert f.type == "String"
    assert f.max_length == 100


def test_facet_frozen():
    f = Facet(type=DataType.String)
    with pytest.raises(FrozenInstanceError):
        f.type = "Integer"  # type: ignore[misc]


def test_facet_equality():
    a = Facet(type=DataType.String, max_length=50)
    b = Facet(type=DataType.String, max_length=50)
    assert a == b


def test_facet_inequality():
    a = Facet(type=DataType.String)
    b = Facet(type=DataType.Integer)
    assert a != b


def test_facet_hash():
    a = Facet(type=DataType.String)
    b = Facet(type=DataType.String)
    assert hash(a) == hash(b)


def test_facet_pattern_matching():
    f = Facet(type=DataType.ObservationalTimePeriod)
    match f:
        case Facet(type=dtype):
            assert dtype == "ObservationalTimePeriod"
        case _:
            pytest.fail("Pattern match failed")


# ── Ref[Codelist] as enumerated representation ────────────────────────────


def test_enumerated_ref_from_urn():
    urn = Codelist.parse_urn(_CL_URN)
    ref = Ref[Codelist].unresolved(urn)
    assert ref.id == "CL_FREQ"
    assert ref.agency == "SDMX"


def test_enumerated_ref_equality():
    urn = Codelist.parse_urn(_CL_URN)
    a = Ref[Codelist].unresolved(urn)
    b = Ref[Codelist].unresolved(urn)
    assert a == b


def test_enumerated_ref_pattern_matching():
    urn = Codelist.parse_urn(_CL_URN)
    ref = Ref[Codelist].unresolved(urn)
    assert isinstance(ref, Ref)
    assert ref.id == "CL_FREQ"


# ── Representation type alias ─────────────────────────────────────────────


def test_representation_pattern_match_facet():
    rep: Ref[Codelist] | Facet | None = Facet(type=DataType.String)
    match rep:
        case Facet(type=dtype):
            assert dtype == "String"
        case _:
            pytest.fail("Should match Facet")


def test_representation_pattern_match_ref():
    urn = Codelist.parse_urn(_CL_URN)
    rep: Ref[Codelist] | Facet | None = Ref[Codelist].unresolved(urn)
    match rep:
        case Facet():
            pytest.fail("Should not match Facet")
        case Ref():
            assert rep.id == "CL_FREQ"
        case None:
            pytest.fail("Should not match None")


def test_representation_pattern_match_none():
    rep: Ref[Codelist] | Facet | None = None
    match rep:
        case None:
            pass
        case _:
            pytest.fail("Should match None")
