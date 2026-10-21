"""Tests for the pure-domain Registry."""

import attrs
import pytest

from sdmxlib.model.codelist import Codelist
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from tests.support.factories import concept_ref, minimal_dsd

pytestmark = pytest.mark.unit

_NAME = InternationalString(en="Test")


# ── Fixtures ──────────────────────────────────────────────────────────────


def _make_cl(cl_id: str = "CL_FREQ", agency: str = "SDMX") -> Codelist:
    return Codelist(id=cl_id, maintainer=agency, version="1.0", name=_NAME)


def _make_dsd_with_dim(dsd_id: str, dim: Dimension, agency: str = "SDMX") -> DataStructure:
    return DataStructure(
        id=dsd_id,
        maintainer=agency,
        version="1.0",
        name=_NAME,
        dimensions=[dim],
    )


# ── Basic store ───────────────────────────────────────────────────────────


def test_empty_registry():
    reg = InMemoryRegistry()
    assert len(reg) == 0
    assert repr(reg) == "InMemoryRegistry(empty)"


def test_add_and_get():
    reg = InMemoryRegistry()
    cl = _make_cl()
    reg.add(cl)
    urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    assert reg.get(urn) is cl


def test_get_missing_returns_none():
    reg = InMemoryRegistry()
    urn = Codelist.urn_for(agency="SDMX", id="NONEXISTENT", version="1.0")
    assert reg.get(urn) is None


def test_len():
    reg = InMemoryRegistry()
    reg.add(_make_cl("CL_A"))
    reg.add(_make_cl("CL_B"))
    assert len(reg) == 2


def test_contains_true():
    reg = InMemoryRegistry()
    cl = _make_cl()
    reg.add(cl)
    urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    assert urn in reg


def test_contains_false():
    reg = InMemoryRegistry()
    urn = Codelist.urn_for(agency="SDMX", id="MISSING", version="1.0")
    assert urn not in reg


def test_add_all():
    reg = InMemoryRegistry()
    reg.add_all([_make_cl("CL_A"), _make_cl("CL_B"), minimal_dsd()])
    assert len(reg) == 3


def test_get_all_by_type():
    reg = InMemoryRegistry()
    reg.add(_make_cl("CL_A"))
    reg.add(_make_cl("CL_B"))
    reg.add(minimal_dsd())
    codelists = reg.get_all(Codelist)
    assert len(codelists) == 2
    assert all(isinstance(c, Codelist) for c in codelists)


def test_get_all_empty():
    reg = InMemoryRegistry()
    reg.add(_make_cl())
    assert reg.get_all(DataStructure) == []


def test_repr_with_content():
    reg = InMemoryRegistry()
    reg.add(_make_cl())
    reg.add(minimal_dsd())
    r = repr(reg)
    assert "Codelist" in r
    assert "DataStructure" in r


# ── SupportsUrn guard ──────────────────────────────────────────────────────────


def test_add_non_haurn_raises():
    reg = InMemoryRegistry()
    with pytest.raises(TypeError):
        # The refusal is the subject, so the argument has to be written wrong.
        reg.add("not an artefact")  # pyrefly: ignore[bad-argument-type]


# ── Ref interning ─────────────────────────────────────────────────────────


def test_refs_for_one_urn_are_equal_and_find_one_artefact():
    """`ref` builds a fresh bound `Ref` each time; equal and hash-equal by URN (#492)."""
    reg = InMemoryRegistry()
    cl = _make_cl()
    reg.add(cl)
    urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    ref_a = reg.ref(urn)
    ref_b = reg.ref(urn)
    assert ref_a == ref_b
    assert hash(ref_a) == hash(ref_b)
    assert ref_a() is ref_b() is reg.get(urn)


def test_ref_resolved_after_add():
    reg = InMemoryRegistry()
    cl = _make_cl()
    reg.add(cl)
    urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    ref = reg.ref(urn)
    assert ref.resolved
    assert ref() is cl


def test_ref_unresolved_before_add():
    reg = InMemoryRegistry()
    urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    ref = reg.ref(urn)
    assert not ref.resolved


def test_ref_resolves_when_target_added_later():
    """Refs obtained before add() resolve when the target arrives."""
    reg = InMemoryRegistry()
    urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    ref = reg.ref(urn)  # unresolved
    assert not ref.resolved

    cl = _make_cl()
    reg.add(cl)  # resolves canonical → propagates to ref
    assert ref.resolved
    assert ref() is cl


# ── Ref field interning on artefacts ──────────────────────────────────────


def test_add_binds_the_stored_copy_and_leaves_the_callers_own():
    """`add` stores a copy whose references this registry binds; the caller's object is as built (#492)."""
    reg = InMemoryRegistry()
    cl_urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")

    dim = Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=Ref[Codelist].unresolved(cl_urn))
    dsd = _make_dsd_with_dim("DSD_TEST", dim)

    reg.add(dsd)
    stored = reg.get(dsd.urn)
    assert stored is not None
    representation = stored.dimensions["FREQ"].representation
    assert isinstance(representation, Ref)
    assert representation.bound
    assert representation == reg.ref(cl_urn)
    assert isinstance(dim.representation, Ref)
    assert not dim.representation.bound


def test_walk_resolves_dimension_ref_when_codelist_present():
    """If the codelist is added first, the stored DSD's dimension Ref resolves on add(dsd)."""
    reg = InMemoryRegistry()
    cl = _make_cl()
    cl_urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    reg.add(cl)  # store codelist first

    dim = Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=Ref[Codelist].unresolved(cl_urn))
    reg.add(_make_dsd_with_dim("DSD_TEST", dim))
    stored = reg.get(DataStructure.urn_for(agency="SDMX", id="DSD_TEST", version="1.0"))
    assert stored is not None
    representation = stored.dimensions["FREQ"].representation
    assert isinstance(representation, Ref)
    assert representation.resolved
    assert representation() is cl


def test_walk_keeps_a_pre_resolved_refs_target():
    """A Ref built with its target keeps it; the registry's copy wins once added (#492)."""
    reg = InMemoryRegistry()
    cl = _make_cl()
    cl_urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")

    # Create a pre-resolved Ref, as a caller building a graph by hand would
    dim = Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=Ref[Codelist].of(cl_urn, cl))
    reg.add(_make_dsd_with_dim("DSD_TEST", dim))
    stored = reg.get(DataStructure.urn_for(agency="SDMX", id="DSD_TEST", version="1.0"))
    assert stored is not None
    representation = stored.dimensions["FREQ"].representation
    assert isinstance(representation, Ref)
    assert representation() is cl
    assert not reg.ref(cl_urn).resolved, "the registry itself holds no codelist"

    newer = _make_cl()
    reg.add(newer)
    assert representation() is newer


# ── Shared Ref propagation ─────────────────────────────────────────────────


def test_shared_ref_between_two_artefacts():
    """Two artefacts referencing one URN both find the codelist once it is added."""
    reg = InMemoryRegistry()
    cl_urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")

    dim_a = Dimension(id="FREQ_A", concept=concept_ref("FREQ_A"), representation=Ref[Codelist].unresolved(cl_urn))
    dim_b = Dimension(id="FREQ_B", concept=concept_ref("FREQ_B"), representation=Ref[Codelist].unresolved(cl_urn))

    reg.add(_make_dsd_with_dim("DSD_A", dim_a))
    reg.add(_make_dsd_with_dim("DSD_B", dim_b))
    dsd_a = reg.get(DataStructure.urn_for(agency="SDMX", id="DSD_A", version="1.0"))
    dsd_b = reg.get(DataStructure.urn_for(agency="SDMX", id="DSD_B", version="1.0"))
    assert dsd_a is not None
    assert dsd_b is not None
    a, b = dsd_a.dimensions["FREQ_A"], dsd_b.dimensions["FREQ_B"]
    assert isinstance(a.representation, Ref)
    assert isinstance(b.representation, Ref)
    assert a.representation == b.representation
    assert not a.representation.resolved

    cl = _make_cl()
    reg.add(cl)  # both find it

    assert a.representation() is cl
    assert b.representation() is cl


# ── artefact_urn (public URN builder) ─────────────────────────────────────


def test_artefact_urn_matches_storage_writer_pk():
    """Public artefact_urn() must match the URN written by storage.writers."""
    import sdmxlib as sl
    from sdmxlib.model.registry import artefact_urn

    cl = _make_cl(cl_id="CL_FREQ", agency="ESTAT")
    urn = artefact_urn(cl)

    assert isinstance(urn, sl.SdmxUrn)
    assert str(urn) == "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=ESTAT:CL_FREQ(1.0)"
    # Also reachable from the package root.
    assert sl.artefact_urn(cl) == urn


def test_artefact_urn_for_dataflow_and_dsd():
    from sdmxlib.model.dataflow import Dataflow
    from sdmxlib.model.registry import artefact_urn

    dsd = minimal_dsd(id="DSD_TEST", agency="SDMX")
    flow = Dataflow(id="DF_TEST", maintainer="SDMX", version="1.0", name=_NAME, structure=dsd.urn)

    assert str(artefact_urn(dsd)) == "urn:sdmx:org.sdmx.infomodel.datastructure.DataStructure=SDMX:DSD_TEST(1.0)"
    assert str(artefact_urn(flow)) == "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=SDMX:DF_TEST(1.0)"


# ── A Ref is frozen (#492) ──────────────────────────────────────────────────
#
# `Ref` publishes a read API and no write at all: it finds its target through
# the registry that bound it. The `_interning` seam that wrote one is gone.


def test_a_ref_cannot_be_written():
    ref = Ref[Codelist].unresolved(Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0"))
    # By name at runtime: the checker already refuses the assignment statically.
    field = "urn"
    with pytest.raises(attrs.exceptions.FrozenInstanceError):
        setattr(ref, field, Codelist.urn_for(agency="SDMX", id="CL_OTHER", version="1.0"))


def test_resolution_reaches_a_ref_taken_before_the_artefact():
    """A Ref a caller took before the artefact was added finds it afterwards (#492)."""
    reg = InMemoryRegistry()
    urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")

    early = reg.ref(urn)  # taken before the artefact is added
    assert not early.resolved

    cl = _make_cl()
    reg.add(cl)

    # the Ref a caller already holds now finds it
    assert early.resolved
    assert early() is cl
