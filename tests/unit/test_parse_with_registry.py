"""Tests for as_structure(parse(..., registry=reg)) — format layer Registry integration."""

from pathlib import Path

import pytest

import sdmxlib as sl
from sdmxlib.formats import parse
from sdmxlib.formats.sdmx_json.reader import read as read_json
from sdmxlib.formats.sdmx_ml21.reader import read as read_21
from sdmxlib.formats.sdmx_ml30.reader import read as read_30
from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.hierarchy import HierarchicalCodelist
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.ref import Ref
from tests.support.repo import ROOT
from tests.support.roundtrip import as_structure

pytestmark = pytest.mark.unit

_ML21 = Path(__file__).parent.parent / "fixtures" / "sdmx_ml21"
_ML30 = Path(__file__).parent.parent / "fixtures" / "sdmx_ml30"
_JSON = Path(__file__).parent.parent / "fixtures" / "sdmx_json"


# ── Without registry — existing behaviour unchanged ───────────────────────────


def test_parse_without_registry_unchanged():
    data = (_ML21 / "minimal.xml").read_bytes()
    msg = as_structure(parse(data))
    assert msg.codelists


# ── as_structure(parse()) with registry — SDMX-ML 2.1 ──────────────────────────────────────


def test_parse_21_interns_codelists():
    data = (_ML21 / "minimal.xml").read_bytes()
    reg = InMemoryRegistry()
    msg = as_structure(parse(data, registry=reg))
    assert len(reg.get_all(Codelist)) == len(msg.codelists)


def test_parse_21_artefacts_queryable_by_urn():
    data = (_ML21 / "minimal.xml").read_bytes()
    reg = InMemoryRegistry()
    msg = as_structure(parse(data, registry=reg))
    for cl in msg.codelists:
        urn = Codelist.urn_for(agency=cl.agency_id, id=cl.id, version=cl.version)
        assert reg.get(urn) is cl


def test_parse_21_ref_on_the_stored_artefact_is_bound_to_the_registry():
    """After parse with registry, the registry's copy references through the registry (#492).

    Its references were interned into canonical `Ref`s; a `Ref` is frozen now,
    and `add` stores a copy whose references look their targets up in it.
    """
    data = (_ML21 / "complete.xml").read_bytes()
    reg = InMemoryRegistry()
    msg = as_structure(parse(data, registry=reg))
    dsds = msg.data_structures
    if not dsds:
        pytest.skip("no DataStructure in fixture")
    stored = reg.get(dsds[0].urn)
    assert stored is not None
    representations = [dim.representation for dim in stored.dimensions if isinstance(dim.representation, Ref)]
    assert representations, "the fixture's DSD enumerates no dimension, so this checks nothing"
    for representation in representations:
        assert representation == reg.ref(representation.urn)
        assert representation.resolved == reg.ref(representation.urn).resolved
        if representation.resolved:
            assert representation() is reg.ref(representation.urn)()


def test_parse_21_cross_message_resolution():
    """Codelist parsed first → DSD parsed second → refs resolve automatically."""
    cl_data = (_ML21 / "minimal.xml").read_bytes()
    dsd_data = (_ML21 / "complete.xml").read_bytes()
    reg = InMemoryRegistry()

    cl_msg = as_structure(parse(cl_data, registry=reg))
    if not cl_msg.codelists:
        pytest.skip("no Codelist in minimal.xml fixture")

    dsd_msg = as_structure(parse(dsd_data, registry=reg))
    if not dsd_msg.data_structures:
        pytest.skip("no DataStructure in complete.xml fixture")

    # Any dimension whose codelist was in the first message should now be resolved
    dsd = dsd_msg.data_structures[0]
    cl_ids = {cl.id for cl in cl_msg.codelists}
    for dim in dsd.dimensions:
        if isinstance(dim.representation, Ref) and dim.representation.id in cl_ids:
            assert isinstance(dim.representation, Ref)
            assert dim.representation.resolved


# ── read_21() directly ────────────────────────────────────────────────────────


def test_read_21_without_registry():
    data = (_ML21 / "minimal.xml").read_bytes()
    msg = read_21(data)
    assert msg.codelists


def test_read_21_with_registry():
    data = (_ML21 / "minimal.xml").read_bytes()
    reg = InMemoryRegistry()
    msg = read_21(data, registry=reg)
    assert len(reg) == len(msg.all_artefacts())


# ── SDMX-ML 3.0 ───────────────────────────────────────────────────────────────


def test_read_30_with_registry():
    fixtures = list((_ML30).glob("*.xml"))
    if not fixtures:
        pytest.skip("no SDMX-ML 3.0 fixtures")
    reg = InMemoryRegistry()
    msg = read_30(fixtures[0].read_bytes(), registry=reg)
    assert len(reg) == len(msg.all_artefacts())


# ── SDMX-JSON ─────────────────────────────────────────────────────────────────


def test_read_json_with_registry():
    data = (_JSON / "minimal.json").read_bytes()
    reg = InMemoryRegistry()
    msg = read_json(data, registry=reg)
    assert len(reg) == len(msg.all_artefacts())


def test_parse_auto_detect_json_with_registry():
    data = (_JSON / "minimal.json").read_bytes()
    reg = InMemoryRegistry()
    msg = as_structure(parse(data, registry=reg))
    assert len(reg) == len(msg.all_artefacts())


# ── Registry exports ──────────────────────────────────────────────────────────


def test_sl_in_memory_registry_is_domain_class():
    """sl.InMemoryRegistry is the pure-domain store, not the REST client."""
    assert sl.InMemoryRegistry is InMemoryRegistry


def test_sl_registry_reader_is_protocol():
    """sl.RegistryReader is the runtime-checkable Protocol that all registries satisfy."""
    assert isinstance(InMemoryRegistry(), sl.RegistryReader)


def test_a_2_1_message_with_a_hierarchy_ingests() -> None:
    """The shape that had no test, and broke without one (#395).

    SDMX-ML 2.1 has no way to write a `<str:Hierarchy>` except inside a
    `<str:HierarchicalCodelist>`, so *every* 2.1 document carrying a hierarchy
    carries the container too. Nothing here parsed one into a registry, so:

    - before #394 the container was **dropped silently** — its name, which
      #239 added it to preserve, vanished on ingest;
    - #394 turned that into a refusal, which broke ingest for the whole
      document;
    - #395 stores it, and both problems go.

    A fixture rather than a hand-built message, because the point is the
    document shape a real 2.1 producer emits.
    """
    xml = (ROOT / "tests/fixtures/sdmx_ml21/with_hierarchy.xml").read_bytes()
    with LocalRegistry(":memory:") as reg:
        msg = as_structure(parse(xml, registry=reg))
        stored = dict(reg.connection.execute("SELECT artefact_type, count(*) FROM sdmx.artefact GROUP BY 1").fetchall())

    assert stored.get("HierarchicalCodelist") == 1, f"the container was not stored: {stored}"
    assert stored.get("Hierarchy") == 1, "and neither was its member lost"
    assert any(type(a).__name__ == "HierarchicalCodelist" for a in msg.all_artefacts())


def test_the_container_round_trips_with_its_own_name() -> None:
    """#239's case, now surviving storage as well as parsing.

    A codelist named "Geographic hierarchy" containing a hierarchy named
    "Canada → provinces → cities" used to come back with only the second.
    """
    xml = (ROOT / "tests/fixtures/sdmx_ml21/with_hierarchy.xml").read_bytes()
    with LocalRegistry(":memory:") as reg:
        msg = as_structure(parse(xml, registry=reg))
        original = next(a for a in msg.all_artefacts() if isinstance(a, HierarchicalCodelist))
        back = reg.get(original.urn)

    assert isinstance(back, HierarchicalCodelist)
    assert back.name == original.name, "the container's own name"
    assert [str(r.urn) for r in back.hierarchies] == [str(r.urn) for r in original.hierarchies]
