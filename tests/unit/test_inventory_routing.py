"""The Inventory's routing fields: ``dataflows``, ``pull_bytes`` and the structure entry's ``artefacts`` (GH-348).

The sharded catalog spec's §6.3 defines them, §7.3 routes from them, and
§10.3 has ``verify-catalog`` check them against the manifest.
"""

import json
from pathlib import Path

import jsonschema
import msgspec
import pytest

from sdmxlib.inventory import (
    InventoryError,
    Leaf,
    LeafEntry,
    LiveEdition,
    LiveInventory,
    PartialRecord,
    decode_leaf,
    encode,
    local_fetch,
    record,
    reinstate,
    release,
    retire,
    rollback,
    route,
    routing_fields,
    schema,
    sha256_hex,
    validate_leaf,
    verify_entry,
)
from sdmxlib.storage._manifest import ArtefactEntry, BindingCount, EditionFile, Manifest, Tier, encode_manifest
from tests.support.inventory import NOW, PRODUCER, write_version

pytestmark = pytest.mark.unit

_SCHEMA = "2.52"
_A, _B, _C = (f"01a18e00-0000-7000-8000-00000000000{n}" for n in range(1, 4))
_DF_A = "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=STC:DF_A(1.0)"
_DF_B = "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=STC:DF_B(1.0)"
_GEO = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=STC:CL_GEO(1.0)"
_FREQ = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=STC:CL_FREQ(1.0)"
_DSD = "urn:sdmx:org.sdmx.infomodel.datastructure.DataStructure=STC:DSD_A(1.0)"
_RECORDS = {_GEO: PartialRecord(fingerprint="g1"), _FREQ: PartialRecord(fingerprint="f1", partial_of="f0")}


def _file(path: str, size: int, tier: Tier) -> EditionFile:
    digests: dict[str, str] = {"sha256": "0" * 64, "md5": "0" * 32, "crc32c": "0" * 8}
    return EditionFile(path=path, size=size, digests=digests, root="edition", tier=tier)


def _binding(dataflow: str) -> BindingCount:
    return BindingCount(
        dataflow=dataflow, observations=1, structural_version=_DSD, fingerprint="t", structure_fingerprint="s"
    )


def _manifest(
    *, bindings: tuple[str, ...] = (_DF_B, _DF_A), product: str = "p", edition: str = _A, structure: bool = False
) -> Manifest:
    """A manifest with two required files (9,187,328 + 3,944,448 bytes), a remote and a lazy file."""
    artefacts = (
        ArtefactEntry(urn=_FREQ, stability="semantic", partial=True, fingerprint="f1", partial_of="f0"),
        ArtefactEntry(urn=_GEO, stability="semantic", partial=False, fingerprint="g1"),
        ArtefactEntry(urn=_DSD, stability="semantic", partial=True, fingerprint="d1"),
    )
    return Manifest(
        format_version="3.0",
        product_id=product,
        edition_id=edition,
        previous=None,
        created_at=NOW,
        producer=PRODUCER,
        sdmxlib_version="0",
        schema_version=_SCHEMA,
        duckdb_version="v1",
        duckdb_storage_version=1,
        ducklake_version=None,
        ducklake_spec_version=None,
        data_path="../data/",
        files=(
            _file("metadata.duckdb", 9187328, "required"),
            _file("tables.ducklake", 3944448, "required"),
            _file("search/en.duckdb", 1000, "lazy"),
            _file("t/0.parquet", 7425310, "remote"),
        ),
        bindings=() if structure else tuple(_binding(b) for b in bindings),
        artefacts=artefacts,
        canonical_form="1",
    )


def _recorded(
    leaf: Leaf | None,
    edition: str,
    previous: str | None,
    *,
    kind: str = "data",
    embargo: bool = False,
    routed: bool = True,
    artefacts: dict[str, PartialRecord] | None = None,
) -> Leaf:
    return record(
        leaf,
        product_id="p",
        kind="structure" if kind == "structure" else "data",
        lifecycle="revisable",
        edition_id=edition,
        previous=previous,
        manifest_sha256="0" * 64,
        schema_version=_SCHEMA,
        embargo=embargo,
        now=NOW,
        release_at=NOW if embargo else None,
        artefacts=artefacts,
        dataflows=[_DF_B, _DF_A, _DF_B] if routed else None,
        pull_bytes=13131776 if routed else None,
    )


def _entry(leaf: Leaf, edition: str) -> LeafEntry:
    return next(e for e in leaf.editions if e.edition_id == edition)


def _with_entry(leaf: Leaf, **changes: object) -> Leaf:
    return msgspec.structs.replace(leaf, editions=(msgspec.structs.replace(leaf.editions[0], **changes),))


# ── the model and the rules ───────────────────────────────────────────────────


def test_record_writes_the_routing_fields_sorted_and_unique() -> None:
    entry = _entry(_recorded(None, _A, None), _A)
    assert entry.has_routing
    assert (entry.dataflows, entry.pull_bytes) == ((_DF_A, _DF_B), 13131776)


def test_an_entry_without_the_routing_fields_writes_no_key_and_still_decodes() -> None:
    leaf = _recorded(None, _A, None, routed=False)
    raw = encode(leaf)
    document = json.loads(raw)["editions"][0]
    assert "dataflows" not in document
    assert "pull_bytes" not in document
    entry = decode_leaf(raw).editions[0]
    assert not entry.has_routing
    assert entry.dataflows is msgspec.UNSET


def test_a_leaf_with_the_routing_fields_round_trips() -> None:
    leaf = _recorded(None, _A, None, kind="structure", artefacts=_RECORDS)
    assert decode_leaf(encode(leaf)) == leaf


def test_record_takes_both_routing_fields_or_neither() -> None:
    with pytest.raises(ValueError, match="pass both or neither"):
        record(
            None,
            product_id="p",
            kind="data",
            lifecycle="revisable",
            edition_id=_A,
            previous=None,
            manifest_sha256="0" * 64,
            schema_version=_SCHEMA,
            embargo=False,
            now=NOW,
            dataflows=[_DF_A],
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"pull_bytes": msgspec.UNSET}, "one routing field without the other"),
        ({"dataflows": msgspec.UNSET}, "one routing field without the other"),
        ({"dataflows": (_DF_B, _DF_A)}, "dataflows out of order or twice"),
        ({"dataflows": (_DF_A, _DF_A)}, "dataflows out of order or twice"),
        ({"pull_bytes": -1}, "negative pull_bytes"),
    ],
)
def test_validate_leaf_refuses_a_broken_routing_field(changes: dict[str, object], message: str) -> None:
    leaf = _recorded(None, _A, None)
    validate_leaf(leaf)
    with pytest.raises(InventoryError, match=message):
        validate_leaf(_with_entry(leaf, **changes))


def test_decode_leaf_refuses_a_negative_pull_bytes() -> None:
    document = json.loads(encode(_recorded(None, _A, None)))
    document["editions"][0]["pull_bytes"] = -1
    with pytest.raises(InventoryError, match=">= 0"):
        decode_leaf(json.dumps(document).encode())


def test_every_transition_carries_the_routing_fields() -> None:
    """Release, supersede and rollback copy an entry's fields unchanged (spec §6.4)."""
    first = _recorded(None, _A, None, kind="structure", artefacts=_RECORDS)
    second = release(_recorded(first, _B, _A, kind="structure", embargo=True, routed=False), _B, now=NOW)
    back = rollback(second, schema_version=_SCHEMA, if_live=_B, now=NOW)
    for leaf in (second, back):
        entry = _entry(leaf, _A)
        assert (entry.dataflows, entry.pull_bytes, entry.artefacts) == ((_DF_A, _DF_B), 13131776, _RECORDS)
        assert not _entry(leaf, _B).has_routing


def test_a_reinstated_entry_keeps_its_routing_fields_and_artefacts() -> None:
    leaf = _recorded(_recorded(None, _A, None), _B, _A)
    kept, stones = retire(leaf, [_A], now=NOW)
    back, left = reinstate(
        kept,
        stones,
        edition_id=_A,
        previous=None,
        manifest_sha256="0" * 64,
        schema_version=_SCHEMA,
        now=NOW,
        artefacts=_RECORDS,
        dataflows=[_DF_B, _DF_A],
        pull_bytes=13131776,
    )
    entry = _entry(back, _A)
    assert (entry.state, entry.dataflows, entry.pull_bytes, entry.artefacts) == (
        "superseded",
        (_DF_A, _DF_B),
        13131776,
        _RECORDS,
    )
    assert left == ()


def test_full_artefacts_are_the_records_without_partial_of() -> None:
    entry = _entry(_recorded(None, _A, None, kind="structure", artefacts=_RECORDS), _A)
    assert entry.full_artefacts == (_GEO,)
    assert _entry(_recorded(None, _A, None), _A).full_artefacts is None


# ── the schema ────────────────────────────────────────────────────────────────


def test_leaves_with_and_without_the_routing_fields_meet_the_leaf_schema() -> None:
    leaf_schema = json.loads(schema("leaf"))
    without = _recorded(None, _A, None, routed=False)
    with_fields = _recorded(without, _B, _A, kind="data")
    document = json.loads(encode(with_fields))
    assert [("dataflows" in e) for e in document["editions"]] == [False, True]
    jsonschema.validate(document, leaf_schema)
    jsonschema.validate(json.loads(encode(without)), leaf_schema)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"pull_bytes": None}, "'pull_bytes' is a dependency of 'dataflows'"),
        ({"dataflows": None}, "'dataflows' is a dependency of 'pull_bytes'"),
        ({"dataflows": [_DF_A, _DF_A]}, "has non-unique elements"),
        ({"pull_bytes": -1}, "less than the minimum of 0"),
    ],
)
def test_the_leaf_schema_refuses_a_broken_routing_field(change: dict[str, object], message: str) -> None:
    document = json.loads(encode(_recorded(None, _A, None)))
    entry = document["editions"][0]
    for key, value in change.items():
        if value is None:
            del entry[key]
        else:
            entry[key] = value
    with pytest.raises(jsonschema.ValidationError, match=message):
        jsonschema.validate(document, json.loads(schema("leaf")))


# ── the reader, routing and verify-catalog ────────────────────────────────────


def test_the_live_view_carries_the_routing_fields(tmp_path: Path) -> None:
    write_version(
        tmp_path,
        {
            "p": _recorded(None, _A, None, kind="structure", artefacts=_RECORDS),
            "q": msgspec.structs.replace(_recorded(None, _A, None, routed=False), product_id="q"),
        },
    )
    view = LiveInventory(local_fetch(tmp_path), schema_version=_SCHEMA).refresh()
    routed, bare = view.head("p"), view.head("q")
    assert (routed.dataflows, routed.pull_bytes, routed.artefacts) == ((_DF_A, _DF_B), 13131776, _RECORDS)
    assert (bare.dataflows, bare.pull_bytes, bare.artefacts) == (None, None, None)


def test_routing_fields_come_from_the_manifest() -> None:
    fields = routing_fields(_manifest(bindings=(_DF_B, _DF_A, _DF_B)))
    assert fields.dataflows == (_DF_A, _DF_B)
    assert fields.pull_bytes == 9187328 + 3944448
    # The partial DSD has no partial_of, so it has no record.
    assert fields.artefacts == _RECORDS
    assert routing_fields(_manifest(structure=True)).dataflows == ()


def _live(tmp_path: Path, leaf: Leaf, raw: bytes) -> LiveEdition:
    """The leaf's one edition, live, with `raw` as its manifest."""
    entry = msgspec.structs.replace(leaf.editions[0], manifest_sha256=sha256_hex(raw))
    write_version(tmp_path, {"p": msgspec.structs.replace(leaf, editions=(entry,))})
    return LiveInventory(local_fetch(tmp_path), schema_version=_SCHEMA).refresh().head("p")


class _Manifest:
    """A manifest fetch that counts its calls."""

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.calls = 0

    def __call__(self) -> bytes:
        self.calls += 1
        return self.raw


def test_a_structure_entry_routes_from_its_leaf_without_opening_its_manifest(tmp_path: Path) -> None:
    fetch = _Manifest(encode_manifest(_manifest(structure=True)))
    head = _live(tmp_path, _recorded(None, _A, None, kind="structure", artefacts=_RECORDS), fetch.raw)
    found = route(head, fetch)
    assert (found.dataflows, found.pull_bytes, found.homes) == ((_DF_A, _DF_B), 13131776, (_GEO,))
    assert fetch.calls == 0


def test_a_data_entry_with_the_routing_fields_routes_without_its_manifest_and_homes_nothing(tmp_path: Path) -> None:
    fetch = _Manifest(encode_manifest(_manifest()))
    head = _live(tmp_path, _recorded(None, _A, None, artefacts=_RECORDS), fetch.raw)
    assert route(head, fetch).homes == ()
    assert fetch.calls == 0


@pytest.mark.parametrize("kind", ["data", "structure"])
def test_an_entry_without_the_fields_routes_from_its_manifest(kind: str, tmp_path: Path) -> None:
    fetch = _Manifest(encode_manifest(_manifest(structure=kind == "structure")))
    head = _live(tmp_path, _recorded(None, _A, None, kind=kind, routed=False), fetch.raw)
    found = route(head, fetch)
    assert fetch.calls == 1
    assert found.pull_bytes == 9187328 + 3944448
    if kind == "structure":
        assert (found.dataflows, found.homes) == ((), (_GEO,))
    else:
        assert (found.dataflows, found.homes) == ((_DF_A, _DF_B), ())


def test_a_structure_entry_without_artefacts_takes_its_homes_from_its_manifest(tmp_path: Path) -> None:
    fetch = _Manifest(encode_manifest(_manifest(structure=True)))
    head = _live(tmp_path, _recorded(None, _A, None, kind="structure"), fetch.raw)
    found = route(head, fetch)
    assert (found.dataflows, found.pull_bytes, found.homes) == ((_DF_A, _DF_B), 13131776, (_GEO,))
    assert fetch.calls == 1


def test_a_manifest_that_is_not_the_entrys_is_refused(tmp_path: Path) -> None:
    raw = encode_manifest(_manifest())
    head = _live(tmp_path, _recorded(None, _A, None, routed=False), raw)
    with pytest.raises(InventoryError, match="manifest's SHA-256 is not"):
        route(head, lambda: raw + b" ")


def test_verify_entry_passes_fields_that_match_the_manifest() -> None:
    manifest = _manifest()
    fields = routing_fields(manifest)
    leaf = record(
        None,
        product_id="p",
        kind="data",
        lifecycle="revisable",
        edition_id=_A,
        previous=None,
        manifest_sha256="0" * 64,
        schema_version=_SCHEMA,
        embargo=False,
        now=NOW,
        artefacts=fields.artefacts,
        dataflows=fields.dataflows,
        pull_bytes=fields.pull_bytes,
    )
    assert verify_entry(leaf.editions[0], manifest) == []
    assert verify_entry(_recorded(None, _A, None, routed=False).editions[0], manifest) == []


def test_verify_entry_names_each_field_that_differs() -> None:
    """Discrimination: each filled field can disagree with the manifest."""
    entry = _recorded(None, _A, None, artefacts={_GEO: PartialRecord(fingerprint="other")}).editions[0]
    entry = msgspec.structs.replace(entry, dataflows=(_DF_A,), pull_bytes=1)
    found = verify_entry(entry, _manifest())
    assert len(found) == 3
    assert [m.split(": ")[1].split(" ")[0] for m in found] == ["artefacts", "dataflows", "pull_bytes"]
