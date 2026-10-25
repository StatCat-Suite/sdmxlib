"""Tests for as_structure(sl.read()) and RestRegistry.load() — local file reading API."""

from pathlib import Path

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from tests.support.roundtrip import as_structure

pytestmark = pytest.mark.unit

_ML21 = Path(__file__).parent.parent / "fixtures" / "sdmx_ml21"
_ML30 = Path(__file__).parent.parent / "fixtures" / "sdmx_ml30"


# ── as_structure(sl.read()) — basic usage ───────────────────────────────────────────────────


def test_read_from_path_object():
    msg = as_structure(sl.read(_ML21 / "minimal.xml"))
    assert isinstance(msg, StructureMessage)
    assert msg.codelists


def test_read_from_str_path():
    msg = as_structure(sl.read(str(_ML21 / "minimal.xml")))
    assert isinstance(msg, StructureMessage)
    assert msg.codelists


def test_read_from_bytes():
    data = (_ML21 / "minimal.xml").read_bytes()
    msg = as_structure(sl.read(data))
    assert isinstance(msg, StructureMessage)
    assert msg.codelists


def test_read_returns_structure_message():
    msg = as_structure(sl.read(_ML21 / "complete.xml"))
    assert isinstance(msg, StructureMessage)


# ── as_structure(sl.read()) with registry — single file ─────────────────────────────────────


def test_read_with_registry_populates_artefacts():
    reg = sl.InMemoryRegistry()
    msg = as_structure(sl.read(_ML21 / "minimal.xml", registry=reg))
    assert len(reg) == len(msg.all_artefacts())


def test_read_with_registry_artefacts_accessible_by_urn():
    reg = sl.InMemoryRegistry()
    msg = as_structure(sl.read(_ML21 / "minimal.xml", registry=reg))
    for cl in msg.codelists:
        urn = Codelist.urn_for(agency=cl.agency_id, id=cl.id, version=cl.version)
        assert reg.get(urn) is cl


# ── as_structure(sl.read()) with registry — multi-file cross-resolution ─────────────────────


def test_read_multiple_files_cross_resolves_refs():
    """Codelists loaded first resolve dimension Refs in a DSD loaded second."""
    reg = sl.InMemoryRegistry()
    cl_msg = as_structure(sl.read(_ML21 / "minimal.xml", registry=reg))
    if not cl_msg.codelists:
        pytest.skip("no Codelist in minimal.xml fixture")

    dsd_msg = as_structure(sl.read(_ML21 / "complete.xml", registry=reg))
    if not dsd_msg.data_structures:
        pytest.skip("no DataStructure in complete.xml fixture")

    cl_ids = {cl.id for cl in cl_msg.codelists}
    dsd = dsd_msg.data_structures[0]
    for dim in dsd.dimensions:
        if isinstance(dim.representation, sl.Ref) and dim.representation.id in cl_ids:
            assert isinstance(dim.representation, Ref)
            assert dim.representation.resolved


def test_read_order_independent_resolution():
    """DSD loaded first, codelists second — Refs still resolve."""
    reg = sl.InMemoryRegistry()
    dsd_msg = as_structure(sl.read(_ML21 / "complete.xml", registry=reg))
    if not dsd_msg.data_structures:
        pytest.skip("no DataStructure in complete.xml fixture")

    cl_msg = as_structure(sl.read(_ML21 / "minimal.xml", registry=reg))
    if not cl_msg.codelists:
        pytest.skip("no Codelist in minimal.xml fixture")

    cl_ids = {cl.id for cl in cl_msg.codelists}
    dsd = dsd_msg.data_structures[0]
    for dim in dsd.dimensions:
        if isinstance(dim.representation, sl.Ref) and dim.representation.id in cl_ids:
            assert isinstance(dim.representation, Ref)
            assert dim.representation.resolved


# ── as_structure(sl.read()) — public API surface ────────────────────────────────────────────


def test_read_is_exported_from_sl():
    assert hasattr(sl, "read")
    assert callable(sl.read)


def test_read_in_all():
    assert "read" in sl.__all__


# ── as_structure(sl.read()) — SDMX-ML 3.0 ───────────────────────────────────────────────────


def test_read_ml30_from_path():
    fixtures = list(_ML30.glob("*.xml"))
    if not fixtures:
        pytest.skip("no SDMX-ML 3.0 fixtures")
    msg = as_structure(sl.read(fixtures[0]))
    assert isinstance(msg, StructureMessage)


def test_read_ml30_with_registry():
    fixtures = list(_ML30.glob("*.xml"))
    if not fixtures:
        pytest.skip("no SDMX-ML 3.0 fixtures")
    reg = sl.InMemoryRegistry()
    msg = as_structure(sl.read(fixtures[0], registry=reg))
    assert len(reg) == len(msg.all_artefacts())


# ── RestRegistry.load() ───────────────────────────────────────────────────────
# Use a dummy URL — httpx2.Client is lazy and doesn't connect until a request is made.

_DUMMY = "http://localhost:9999/sdmx/v2"


def test_rest_registry_load_returns_message():
    """RestRegistry.load() returns a StructureMessage."""
    with sl.RestRegistry(_DUMMY) as reg:
        msg = reg.load(_ML21 / "minimal.xml")
    assert isinstance(msg, StructureMessage)
    assert msg.codelists


def test_rest_registry_load_populates_registry():
    """Loaded artefacts are accessible via the public .registry property."""
    with sl.RestRegistry(_DUMMY) as reg:
        msg = reg.load(_ML21 / "minimal.xml")
        assert len(reg.registry) == len(msg.all_artefacts())


def test_rest_registry_load_artefacts_resolvable():
    """Artefacts loaded from file are reachable by URN in the domain registry."""
    with sl.RestRegistry(_DUMMY) as reg:
        msg = reg.load(_ML21 / "minimal.xml")
        for cl in msg.codelists:
            urn = Codelist.urn_for(agency=cl.agency_id, id=cl.id, version=cl.version)
            assert reg.registry.get(urn) is cl


def test_rest_registry_load_from_bytes():
    """RestRegistry.load() also accepts raw bytes."""
    data = (_ML21 / "minimal.xml").read_bytes()
    with sl.RestRegistry(_DUMMY) as reg:
        msg = reg.load(data)
    assert msg.codelists


def test_rest_registry_load_str_path(tmp_path):
    """RestRegistry.load() accepts a str path."""
    xml = (_ML21 / "minimal.xml").read_bytes()
    file = tmp_path / "artefacts.xml"
    file.write_bytes(xml)
    with sl.RestRegistry(_DUMMY) as reg:
        msg = reg.load(str(file))
    assert msg.codelists
