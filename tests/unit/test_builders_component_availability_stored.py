"""`build_component_availability` over a published store's stored availability (GH-210).

A dimension whose selection covers every value it holds excludes no row, so
the stored availability answers it without a scan. These tests compare that
answer with the scan's and count the scans each key costs.
"""

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import pytest

import sdmxlib as sl
from sdmxlib.builders import contentconstraint
from sdmxlib.builders.contentconstraint import build_component_availability
from sdmxlib.edition._availability import survey
from sdmxlib.local.registry import EditionRegistry
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.constraint import DataConstraint
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import AttachmentLevel, Attribute, DataStructure, Dimension, Measure
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from sdmxlib.storage.writers import put_availability
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit

_DIMENSIONS = ("FREQ", "REF_AREA", "GENDER")
_DSD = DataStructure(
    id="DSD",
    maintainer=Agency(id="SDMX"),
    version="1.0",
    name={"en": "DSD"},
    dimensions=[Dimension(id=d, concept=concept_ref(d)) for d in _DIMENSIONS],
    attributes=[Attribute(id="OBS_STATUS", concept=concept_ref("OBS_STATUS"), attachment=AttachmentLevel.OBSERVATION)],
    measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
)
_FLOW = Dataflow(id="F", maintainer=Agency(id="SDMX"), version="1.0", name={"en": "F"}, structure=Ref.to(_DSD))
_CONCEPTS = sl.ConceptScheme(
    id="CS",
    maintainer="T",
    version="1.0",
    name={"en": "concepts"},
    concepts=[sl.Concept(id=c, name={"en": c}) for c in (*_DIMENSIONS, "OBS_STATUS", "OBS_VALUE")],
)
# Not a full cross product, so `available` differs from `exact` and a
# filter on one dimension changes what another holds: MX is annual and
# total-only, and CA has no quarterly women.
_ROWS = """
    ('A', 'CA', '_T', 'A', 1.0), ('A', 'CA', '1', 'A', 2.0), ('A', 'CA', '2', 'E', 3.0),
    ('Q', 'CA', '_T', 'A', 4.0), ('Q', 'CA', '1', 'P', 5.0),
    ('A', 'US', '_T', 'A', 6.0), ('A', 'US', '1', 'A', 7.0), ('A', 'US', '2', 'A', 8.0),
    ('Q', 'US', '_T', 'E', 9.0), ('Q', 'US', '1', 'A', 10.0), ('Q', 'US', '2', 'A', 11.0),
    ('A', 'MX', '_T', 'P', 12.0)
"""
_COMPONENTS = ("all", *_DIMENSIONS, "OBS_STATUS")
# Key slots on 2.1 are FREQ.REF_AREA.GENDER.
_KEYS = (
    "all",
    "..",
    "A+Q.CA+US+MX._T+1+2",  # every value of every dimension
    "A+Q+M.CA+US+MX+BR.",  # supersets, naming values the data lacks
    ".MX._T+1+2",  # the explorer's opening key: one area, every gender
    "A.CA+US+MX.",
    ".CA+US.1",  # strict subsets
    "Q..2",
    "Q.MX.",  # selects no row
    "A.CA._T",
)


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A store of `_FLOW`'s twelve rows and its availability, stored as `seal` stores it.

    The rows live in the store itself rather than a DuckLake lake, so the
    tests need no ``ducklake`` extension.
    """
    path = tmp_path_factory.mktemp("store") / "store.duckdb"
    with sl.LocalRegistry(path) as reg:
        reg.connection.execute("CREATE SCHEMA store.d")
        reg.connection.execute(
            "CREATE TABLE store.d.obs (FREQ TEXT, REF_AREA TEXT, GENDER TEXT, OBS_STATUS TEXT, OBS_VALUE DOUBLE)"
        )
        reg.connection.execute(f"INSERT INTO store.d.obs VALUES {_ROWS}")
        reg.add_all([_CONCEPTS, _DSD, _FLOW])
        binding = DataflowBinding.from_dsd(_DSD, dataflow=Ref.to(_FLOW), schema="d", table="obs", database_role="store")
        reg.add(binding)
        availability, non_codes = survey(reg, binding, obs_count=12)
        assert non_codes == []
        put_availability(reg.connection, str(_FLOW.urn), availability)
    return path


@pytest.fixture
def bundle(store: Path) -> Iterator[EditionRegistry]:
    """The store opened read-only, as a published bundle is."""
    with EditionRegistry(store) as reg:
        yield reg


@pytest.fixture
def scans(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The column of every ``SELECT DISTINCT`` `build_component_availability` runs, in order."""
    seen: list[str] = []
    original = contentconstraint._distinct_values

    def counting(conn, table, column, *, where=None, params=None):
        seen.append(column)
        return original(conn, table, column, where=where, params=params)

    monkeypatch.setattr(contentconstraint, "_distinct_values", counting)
    return seen


def _availability(
    reg: EditionRegistry, key: str, component_id: str, mode: Literal["exact", "available"]
) -> DataConstraint:
    return build_component_availability(
        reg, _FLOW.urn, name={"en": "Availability"}, key=key, component_id=component_id, api="2.1", mode=mode
    )


@pytest.mark.parametrize("mode", ["exact", "available"])
@pytest.mark.parametrize("component_id", _COMPONENTS)
@pytest.mark.parametrize("key", _KEYS)
def test_the_stored_answer_equals_the_scan(
    bundle: EditionRegistry,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    component_id: str,
    mode: Literal["exact", "available"],
):
    fast = _availability(bundle, key, component_id, mode)
    monkeypatch.setattr(contentconstraint, "_held_values", lambda _registry, _urn: {})
    assert fast == _availability(bundle, key, component_id, mode)


def test_the_bundle_stores_availability(bundle: EditionRegistry):
    # Without it every equivalence above compares the scan with itself.
    stored = bundle.availability(_FLOW.urn)
    assert stored is not None
    assert {kv.dimension_id: kv.codes() for kv in stored.region.keys} == {
        "FREQ": {"A", "Q"},
        "REF_AREA": {"CA", "US", "MX"},
        "GENDER": {"_T", "1", "2"},
    }


def test_a_key_selecting_every_value_scans_nothing(bundle: EditionRegistry, scans: list[str]):
    cc = _availability(bundle, "A+Q.CA+US+MX._T+1+2", "all", "exact")
    assert scans == []
    assert {kv.dimension_id: kv.codes() for kv in cc.regions[0].keys}["REF_AREA"] == {"CA", "US", "MX"}


def test_the_explorers_opening_key_scans_nothing(bundle: EditionRegistry, scans: list[str]):
    # Available mode drops REF_AREA's own selection, and GENDER's covers
    # every value, so no constraint is left to scan under.
    cc = _availability(bundle, ".MX._T+1+2", "REF_AREA", "available")
    assert scans == []
    assert cc.regions[0].keys[0].codes() == {"CA", "US", "MX"}


def test_a_key_selecting_a_strict_subset_scans_every_component(bundle: EditionRegistry, scans: list[str]):
    cc = _availability(bundle, ".CA+US.1", "all", "exact")
    assert scans == ["FREQ", "GENDER", "REF_AREA"]
    assert {kv.dimension_id: kv.codes() for kv in cc.regions[0].keys}["FREQ"] == {"A", "Q"}


def test_an_attribute_scans_under_a_covering_key(bundle: EditionRegistry, scans: list[str]):
    # Stored availability lists dimensions only.
    _availability(bundle, "all", "OBS_STATUS", "exact")
    assert scans == ["OBS_STATUS"]


def test_a_writable_store_scans_past_its_stored_availability(store: Path, tmp_path: Path):
    # A publication extending another starts with that one's availability,
    # stale once its lake gains a row, so a writable store reads as having none.
    path = tmp_path / "store.duckdb"
    shutil.copyfile(store, path)
    with sl.LocalRegistry(path) as reg:
        reg.connection.execute("INSERT INTO store.d.obs VALUES ('A', 'BR', '_T', 'A', 13.0)")
        assert reg.availability(_FLOW.urn) is not None
        cc = build_component_availability(
            reg,
            _FLOW.urn,
            name={"en": "Availability"},
            key="all",
            component_id="REF_AREA",
            api="2.1",
            mode="exact",
        )
    assert cc.regions[0].keys[0].codes() == {"CA", "US", "MX", "BR"}
