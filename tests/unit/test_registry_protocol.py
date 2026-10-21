"""Tests for the RegistryReader Protocol — InMemoryRegistry + LocalRegistry conformance."""

import pytest

import sdmxlib as sl
from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.registry import RegistryReader

pytestmark = pytest.mark.unit


def _cl() -> Codelist:
    return Codelist(
        id="CL_FREQ",
        maintainer="SDMX",
        version="1.0",
        name=InternationalString(en="Frequency"),
        codes=[Code(id="A", name=InternationalString(en="Annual"))],
    )


# ── runtime_checkable conformance ───────────────────────────────────────


def test_in_memory_registry_satisfies_protocol():
    reg = InMemoryRegistry()
    assert isinstance(reg, RegistryReader)


def test_local_registry_satisfies_protocol():
    with LocalRegistry(":memory:") as local:
        assert isinstance(local, RegistryReader)


def test_protocol_methods_present():
    """Every RegistryReader implementation exposes get + __contains__."""
    reg = InMemoryRegistry()
    assert hasattr(reg, "get")
    assert hasattr(reg, "__contains__")


# ── Functional parity ──────────────────────────────────────────────────


def test_in_memory_get_and_contains_work_via_protocol():
    """A function typed as `RegistryReader` can use both protocol methods."""
    mem = InMemoryRegistry()
    cl = _cl()
    mem.add(cl)  # add is on InMemoryRegistry, not the Protocol
    reg: RegistryReader = mem
    urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    assert urn in reg
    assert reg.get(urn) is cl


def test_local_get_and_contains_work_via_protocol():
    with LocalRegistry(":memory:") as local:
        local.add(_cl())
        urn = Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
        # Use through the Protocol interface
        reg: RegistryReader = local
        assert urn in reg
        assert reg.get(urn) is not None


# ── Public exports ─────────────────────────────────────────────────────


def test_sl_exports_protocol_and_concrete_class():
    """`sl.RegistryReader` is the Protocol; `sl.LocalRegistry` is the DuckDB-backed concrete class."""
    assert sl.RegistryReader is RegistryReader
    assert sl.LocalRegistry is LocalRegistry
    assert sl.InMemoryRegistry is InMemoryRegistry
