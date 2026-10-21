"""Tests for Ref[T], UnresolvedRef, SdmxRef."""

import pytest
from attrs.exceptions import FrozenInstanceError

from sdmxlib.model.codelist import Codelist
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref, SdmxRef, UnresolvedRef
from sdmxlib.model.urn import SdmxUrn
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit

_URN_STR = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ(1.0)"


def _make_urn() -> SdmxUrn[object]:
    return SdmxUrn.parse(_URN_STR)


# ── Ref[T] ────────────────────────────────────────────────────────────────


def test_unresolved_ref():
    urn = _make_urn()
    ref = Ref[object].unresolved(urn)
    assert ref.urn == urn
    assert not ref.resolved


def test_unresolved_ref_call_raises():
    ref = Ref[object].unresolved(_make_urn())
    with pytest.raises(UnresolvedRef):
        ref()


def test_resolved_ref():
    urn = _make_urn()
    obj = object()
    ref = Ref[object].of(urn, obj)
    assert ref.resolved
    assert ref() is obj


def test_ref_urn_properties():
    urn = _make_urn()
    ref = Ref[object].unresolved(urn)
    assert ref.id == "CL_FREQ"
    assert ref.agency == "SDMX"
    assert ref.version == "1.0"


def test_ref_bool_always_true():
    ref = Ref[object].unresolved(_make_urn())
    assert bool(ref) is True


def test_ref_repr_unresolved():
    ref = Ref[object].unresolved(_make_urn())
    assert "unresolved" in repr(ref)
    assert "CL_FREQ" in repr(ref)


def test_ref_repr_resolved():
    ref = Ref[object].of(_make_urn(), object())
    assert "resolved" in repr(ref)


def test_ref_equality():
    urn = _make_urn()
    obj = object()
    a = Ref[object].of(urn, obj)
    b = Ref[object].of(urn, obj)
    assert a == b


# ── UnresolvedRef exception ───────────────────────────────────────────────


def test_unresolved_ref_exception_message():
    urn = _make_urn()
    ref = Ref[object].unresolved(urn)
    with pytest.raises(UnresolvedRef) as exc_info:
        ref()
    assert "CL_FREQ" in str(exc_info.value) or "SDMX" in str(exc_info.value)


def test_unresolved_ref_has_urn():
    urn = _make_urn()
    ref = Ref[object].unresolved(urn)
    with pytest.raises(UnresolvedRef) as exc_info:
        ref()
    assert exc_info.value.urn == urn


# ── Ref.to() ─────────────────────────────────────────────────────────────


def test_ref_to_codelist_is_resolved():
    cl = Codelist(id="CL_FREQ", maintainer="SDMX", version="1.0", name=InternationalString(en="Frequency"))
    ref = Ref.to(cl)
    assert ref.resolved
    assert ref() is cl


def test_ref_to_codelist_urn():
    cl = Codelist(id="CL_FREQ", maintainer="SDMX", version="1.0", name=InternationalString(en="Frequency"))
    ref = Ref.to(cl)
    assert ref.id == "CL_FREQ"
    assert ref.agency == "SDMX"
    assert ref.version == "1.0"
    assert str(ref.urn) == "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ(1.0)"


def test_ref_to_data_structure():
    dsd = DataStructure(id="DSD_TEST", maintainer="TEST", version="2.0", name=InternationalString(en="Test"))
    ref = Ref.to(dsd)
    assert ref.resolved
    assert ref() is dsd
    assert ref.id == "DSD_TEST"
    assert ref.agency == "TEST"
    assert ref.version == "2.0"


def test_ref_to_dataflow():
    df = Dataflow(id="DF_TEST", maintainer="TEST", version="1.0", name=InternationalString(en="Test"))
    ref = Ref.to(df)
    assert ref.resolved
    assert ref() is df
    assert ref.id == "DF_TEST"


# ── Ref rejects non-maintainable inputs (regression for #136 follow-up) ──


def test_ref_to_dimension_raises_type_error():
    """Ref.to(child_item) must fail loudly — Dimension is not maintainable."""
    dim = Dimension(id="GEO", concept=concept_ref("GEO"))
    with pytest.raises(TypeError, match="MaintainableArtefact"):
        Ref.to(dim)


def test_ref_construct_with_non_urn_raises_type_error():
    """A child item is not a URN and never was constructible this way."""
    dim = Dimension(id="GEO", concept=concept_ref("GEO"))
    with pytest.raises(TypeError, match="expects a SdmxUrn"):
        Ref(urn=dim)  # type: ignore[arg-type]


def test_ref_construct_with_arbitrary_object_raises_type_error():
    with pytest.raises(TypeError, match="expects a SdmxUrn"):
        Ref(urn="not-a-urn")  # type: ignore[arg-type]


def test_ref_construct_with_artefact_is_refused():
    """`Ref(artefact)` is gone — there is one way in, and it is `Ref.to` (#219).

    It used to work, identically to `Ref.to`, but could not be typed: the
    field declares `SdmxUrn[T]` while the constructor accepted a union, and
    no checker can model a post-init that rewrites the field. Rather than
    leave an expression that works at runtime and fails every type check,
    the form is refused and the error names the replacement.
    """
    cl = Codelist(id="CL_FREQ", maintainer="SDMX", version="1.0", name=InternationalString(en="Frequency"))
    with pytest.raises(TypeError, match=r"expects a SdmxUrn.*Use Ref\.to\("):
        Ref(urn=cl)  # type: ignore[arg-type]


def test_ref_to_interns_the_object():
    """`Ref.to` must resolve and derive the URN, as the old form did."""
    cl = Codelist(id="CL_FREQ", maintainer="SDMX", version="1.0", name=InternationalString(en="Frequency"))
    ref = Ref[Codelist].to(cl)
    assert ref.resolved
    assert ref() is cl
    assert ref.urn == Codelist.urn_for(agency=cl.agency_id, id=cl.id, version=cl.version)


# ── SdmxRef ───────────────────────────────────────────────────────────────


def test_sdmx_ref_all_none():
    ref = SdmxRef()
    assert ref.agency is None
    assert ref.id is None
    assert ref.version is None


def test_sdmx_ref_partial():
    ref = SdmxRef(agency="SDMX", id="CL_FREQ")
    assert ref.agency == "SDMX"
    assert ref.id == "CL_FREQ"
    assert ref.version is None


def test_sdmx_ref_is_frozen():
    ref = SdmxRef(id="X")
    with pytest.raises(FrozenInstanceError):
        ref.id = "Y"  # type: ignore[misc]


def test_sdmx_ref_equality():
    a = SdmxRef(agency="SDMX", id="CL_FREQ")
    b = SdmxRef(agency="SDMX", id="CL_FREQ")
    assert a == b
