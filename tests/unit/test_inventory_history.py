"""Inventory 1.2's history: documents, writer rules and the reader fallback (GH-361; spec §6.1-§6.3, §6.6, §6.9)."""

import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import msgspec
import pytest

from sdmxlib.inventory import (
    CATALOG_NAME,
    HISTORY_FORMAT,
    HISTORY_NAME,
    OPTED_IN_CATALOG_FORMAT,
    CatalogHistory,
    Event,
    History,
    InventoryError,
    Leaf,
    PartialRecord,
    Root,
    SegmentHeader,
    change_list,
    decode_catalog,
    decode_history,
    decode_root,
    decode_segment,
    encode,
    encode_segment,
    leaf_ref,
    next_created_at,
    next_root,
    record,
    reinstate,
    retire,
    root_path,
    segment_line,
    segment_path,
    sha256_hex,
)
from tests.support.inventory import NOW, PRODUCER, new_catalog, write_version

pytestmark = pytest.mark.unit

_A, _B = "01a18e00-0000-7000-8000-000000000001", "01a18e00-0000-7000-8000-000000000002"
_CATALOG_ID = "01a18e00-0000-7000-8000-000000000000"
_SEGMENT = segment_path(0, 1, 3, "01a19000-0000-7000-8000-000000000001")


def _sealed(leaf: Leaf | None, edition: str, previous: str | None, product: str = "p") -> Leaf:
    return record(
        leaf,
        product_id=product,
        kind="data",
        lifecycle="revisable",
        edition_id=edition,
        previous=previous,
        manifest_sha256="0" * 64,
        schema_version="2.52",
        embargo=False,
        now=NOW,
    )


def _versions(root: Path, count: int, *, restore_epoch: int = 0) -> Path:
    new_catalog(root, restore_epoch=restore_epoch)
    for n in range(count):
        write_version(root, {f"p{n}": _sealed(None, _A, None, f"p{n}")}, restore_epoch=restore_epoch)
    return root


def _opt_in(root: Path, *, expired_through: int = 0, restore_epoch: int = 0, history: bool = True) -> None:
    catalog = decode_catalog((root / CATALOG_NAME).read_bytes())
    opted = msgspec.structs.replace(
        catalog, format_version=OPTED_IN_CATALOG_FORMAT, history=CatalogHistory(since_seq=1)
    )
    (root / CATALOG_NAME).write_bytes(encode(opted))
    if history:
        document = History(
            format_version=HISTORY_FORMAT,
            catalog_id=catalog.catalog_id,
            restore_epoch=restore_epoch,
            expired_through=expired_through,
            newest_segment=None,
            newest_segment_sha256=None,
            cleaned_through=None,
        )
        (root / HISTORY_NAME).write_bytes(encode(document))


# ── documents ────────────────────────────────────────────────────────────────


def test_a_written_root_carries_the_history_fields_linking_the_version_before_it(tmp_path: Path) -> None:
    _versions(tmp_path, 2)
    first, second = (decode_root((tmp_path / root_path(n)).read_bytes()) for n in (1, 2))
    assert first.has_history
    assert second.has_history
    assert first.previous_root_sha256 is None
    assert second.previous_root_sha256 == sha256_hex((tmp_path / root_path(1)).read_bytes())
    assert first.format_version == "1.2"
    assert second.created_at == first.created_at + timedelta(milliseconds=1)


def test_a_root_without_the_history_fields_reads_and_encodes_without_them(tmp_path: Path) -> None:
    _versions(tmp_path, 1)
    document = json.loads((tmp_path / root_path(1)).read_bytes())
    for field in ("previous_root_sha256", "changes"):
        del document[field]
    root = decode_root(json.dumps(document).encode())
    assert not root.has_history
    assert b"changes" not in encode(root)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.pop("changes"), "one history field without the other"),
        (lambda d: d.update(previous_root_sha256="a" * 64), "None only for version 1"),
        (lambda d: d.update(changes=d["changes"] * 2), "out of order or twice"),
        (lambda d: d["changes"][0]["entries"][0].pop("entry"), "without its entry"),
        (lambda d: d["changes"][0]["entries"][0].update({"from": None, "to": None}), "from nothing to nothing"),
        (lambda d: d["changes"][0]["entries"][0]["entry"].update(state="withdrawn"), "in state withdrawn"),
    ],
    ids=["one field", "hash on version 1", "repeated product", "no entry", "null to null", "entry state"],
)
def test_a_root_breaking_a_history_rule_is_refused(
    change: Callable[[dict[str, object]], object], message: str, tmp_path: Path
) -> None:
    _versions(tmp_path, 1)
    document = json.loads((tmp_path / root_path(1)).read_bytes())
    change(document)
    with pytest.raises(InventoryError, match=message):
        decode_root(json.dumps(document).encode())


def test_the_catalog_decode_by_who_reads_it() -> None:
    one = json.dumps({"format_version": "1.0", "catalog_id": _CATALOG_ID, "restore_epoch": 0}).encode()
    two = json.dumps(
        {"format_version": "2.0", "catalog_id": _CATALOG_ID, "restore_epoch": 0, "history": {"since_seq": 3}}
    ).encode()
    assert not decode_catalog(one, decoder="stage-1-writer").opted_in
    assert decode_catalog(two).opted_in
    opted = decode_catalog(two, decoder="stage-2-writer")
    assert isinstance(opted.history, CatalogHistory)
    assert opted.history.since_seq == 3
    with pytest.raises(InventoryError, match="without stage-2 support"):
        decode_catalog(two, decoder="stage-1-writer")
    with pytest.raises(InventoryError, match=r"history is present exactly in a 2\.x document"):
        decode_catalog(json.dumps(json.loads(two) | {"format_version": "1.0"}).encode())
    with pytest.raises(InventoryError, match=r"history is present exactly in a 2\.x document"):
        decode_catalog(json.dumps({"format_version": "2.0", "catalog_id": _CATALOG_ID}).encode())
    with pytest.raises(InventoryError, match=r"reads 1\.x and 2\.x"):
        decode_catalog(json.dumps({"format_version": "3.0", "catalog_id": _CATALOG_ID}).encode())


def test_history_decodes_and_refuses_a_hash_without_a_segment() -> None:
    fresh = {
        "format_version": "1.0",
        "catalog_id": _CATALOG_ID,
        "restore_epoch": 0,
        "expired_through": 0,
        "newest_segment": None,
        "newest_segment_sha256": None,
        "cleaned_through": None,
    }
    assert decode_history(json.dumps(fresh).encode()).expired_through == 0
    with pytest.raises(InventoryError, match="names no segment"):
        decode_history(json.dumps(fresh | {"newest_segment_sha256": "a" * 64}).encode())
    with pytest.raises(InventoryError, match="without its SHA-256"):
        decode_history(json.dumps(fresh | {"newest_segment": _SEGMENT}).encode())
    with pytest.raises(InventoryError, match=r"format 2\.0"):
        decode_history(json.dumps(fresh | {"format_version": "2.0"}).encode())


def _header(**fields: object) -> SegmentHeader:
    document: dict[str, object] = {
        "format_version": "1.0",
        "catalog_id": _CATALOG_ID,
        "kind": "expiry",
        "restore_epoch": 0,
        "first": 1,
        "last": 2,
        "previous_segment": None,
        "previous_segment_sha256": None,
        "expired_at": "2026-11-04T08:45:00Z",
        "actor": "tests",
        "roots": [1, 2],
        "leaves": [],
        "unread": [],
    }
    return msgspec.convert(document | fields, type=SegmentHeader)


def test_a_segment_round_trips_its_header_and_lines(tmp_path: Path) -> None:
    _versions(tmp_path, 3)
    stored = [(tmp_path / root_path(n)).read_bytes() for n in (1, 2)]
    lines = [segment_line(decode_root(raw), raw) for raw in stored]
    data = encode_segment(_header(), lines)
    assert data.count(b"\n") == 3
    assert json.loads(data.split(b"\n")[1])["root_sha256"] == sha256_hex(stored[0])
    assert list(json.loads(data.split(b"\n")[1]))[-1] == "root_sha256"
    assert decode_segment(data) == (_header(), tuple(lines))
    with pytest.raises(InventoryError, match="are not this root's"):
        segment_line(decode_root(stored[0]), stored[1])


@pytest.mark.parametrize(
    ("header", "message"),
    [
        (_header(roots=[2, 1]), "out of order"),
        (_header(first=2), "says 2-2"),
        (_header(unread=[2]), "expiry segment lists unread"),
        (_header(kind="restore", leaves=["a" * 64]), "plans leaf deletions"),
        (_header(previous_segment=_SEGMENT), "without its hash"),
        (_header(roots=[1, 2, 3], last=3), r"plans \[1, 2, 3\]"),
    ],
    ids=["order", "first", "unread in expiry", "leaves in restore", "link without hash", "missing line"],
)
def test_a_segment_breaking_a_rule_is_refused(header: SegmentHeader, message: str, tmp_path: Path) -> None:
    _versions(tmp_path, 2)
    stored = [(tmp_path / root_path(n)).read_bytes() for n in (1, 2)]
    lines = [segment_line(decode_root(raw), raw) for raw in stored]
    with pytest.raises(InventoryError, match=message):
        decode_segment(encode_segment(header, lines))


def test_a_restore_segment_lists_an_unread_root_without_a_line(tmp_path: Path) -> None:
    _versions(tmp_path, 1)
    raw = (tmp_path / root_path(1)).read_bytes()
    header = _header(kind="restore", unread=[2])
    decoded, lines = decode_segment(encode_segment(header, [segment_line(decode_root(raw), raw)]))
    assert (decoded.unread, [line.seq for line in lines]) == ((2,), [1])


def test_a_segment_line_carrying_products_is_refused(tmp_path: Path) -> None:
    _versions(tmp_path, 2)
    stored = [(tmp_path / root_path(n)).read_bytes() for n in (1, 2)]
    data = encode_segment(_header(), [segment_line(decode_root(raw), raw) for raw in stored])
    header, first, second, _ = data.split(b"\n")
    widened = json.dumps(json.loads(first) | {"products": {}}).encode()
    with pytest.raises(InventoryError, match="carries products or retired"):
        decode_segment(b"\n".join([header, widened, second, b""]))


# ── the writer rules ─────────────────────────────────────────────────────────


def _root(seq: int, created_at: object = NOW) -> Root:
    return msgspec.convert(
        {
            "format_version": "1.2",
            "seq": seq,
            "previous_inventory": None if seq == 1 else seq - 1,
            "restore_epoch": 0,
            "created_at": created_at,
            "producer": {"name": "tests", "version": "0"},
            "embargo": False,
            "products": {},
            "retired": [],
            "event": {"actor": "tests", "reason": "test", "kind": "seal"},
        },
        type=Root,
        from_attributes=True,
    )


def test_created_at_steps_past_the_base_and_refuses_a_fast_one() -> None:
    assert next_created_at(None, NOW) == NOW
    assert next_created_at(_root(1, NOW), NOW - timedelta(minutes=1)) == NOW + timedelta(milliseconds=1)
    assert next_created_at(_root(1, NOW), NOW + timedelta(minutes=1)) == NOW + timedelta(minutes=1)
    assert next_created_at(_root(1, NOW + timedelta(minutes=5)), NOW) == NOW + timedelta(minutes=5, milliseconds=1)
    with pytest.raises(InventoryError, match=r"version 1 was created at 2026-10-09T12:35:00\.001000\+00:00"):
        next_created_at(_root(1, NOW + timedelta(minutes=5, milliseconds=1)), NOW)
    with pytest.raises(ValueError, match="names no time zone"):
        next_created_at(None, NOW.replace(tzinfo=None))


def test_next_root_takes_both_history_fields_or_neither(tmp_path: Path) -> None:
    _versions(tmp_path, 2)
    raw, other = ((tmp_path / root_path(n)).read_bytes() for n in (1, 2))
    base = decode_root(raw)
    event = Event(actor="tests", reason="test", kind="describe")
    with pytest.raises(InventoryError, match="given both or neither"):
        next_root(base, products=base.products, event=event, producer=PRODUCER, now=NOW, previous_bytes=raw)
    with pytest.raises(InventoryError, match="are not version 1's root"):
        next_root(
            base, products=base.products, event=event, producer=PRODUCER, now=NOW, previous_bytes=other, changes=()
        )
    with pytest.raises(InventoryError, match="previous_bytes is None, and version 1 exists"):
        next_root(
            base, products=base.products, event=event, producer=PRODUCER, now=NOW, previous_bytes=None, changes=()
        )
    plain = next_root(base, products=base.products, event=event, producer=PRODUCER, now=NOW)
    assert not plain.has_history
    assert plain.created_at == NOW
    linked = next_root(
        base, products=base.products, event=event, producer=PRODUCER, now=NOW, previous_bytes=raw, changes=()
    )
    assert (linked.previous_root_sha256, linked.changes) == (sha256_hex(raw), ())


def test_the_change_list_of_a_new_and_a_removed_product() -> None:
    added = _sealed(None, _A, None, "new")
    gone = _sealed(None, _B, None, "gone")
    _, added_ref = leaf_ref(added)
    _, gone_ref = leaf_ref(gone)
    base = msgspec.structs.replace(_root(1), products={"gone": gone_ref})
    changes = change_list(base, {"gone": gone}, products={"new": added_ref}, leaves={"new": added}, retired=())
    assert [(c.product_id, c.leaf_sha256.from_, c.leaf_sha256.to) for c in changes] == [
        ("gone", gone_ref.leaf_sha256, None),
        ("new", None, added_ref.leaf_sha256),
    ]
    assert [(e.from_, e.to) for c in changes for e in c.entries] == [("live", None), (None, "live")]
    with pytest.raises(InventoryError, match="new's leaf in the new version is needed"):
        change_list(base, {"gone": gone}, products={"new": added_ref}, leaves={}, retired=())
    with pytest.raises(InventoryError, match="does not hash to"):
        change_list(base, {"gone": added}, products={"new": added_ref}, leaves={"new": added}, retired=())


def test_the_entry_item_copies_seven_fields_and_leaves_out_the_artefacts() -> None:
    leaf = record(
        None,
        product_id="p",
        kind="data",
        lifecycle="revisable",
        edition_id=_A,
        previous=None,
        manifest_sha256="0" * 64,
        schema_version="2.52",
        embargo=False,
        now=NOW,
        artefacts={"urn:x": PartialRecord(fingerprint="f")},
    )
    _, ref = leaf_ref(leaf)
    (item,) = change_list(None, {}, products={"p": ref}, leaves={"p": leaf}, retired=())
    entry = msgspec.to_builtins(item.entries[0])["entry"]
    assert list(entry) == [
        "edition_id",
        "previous",
        "state",
        "manifest_sha256",
        "schema_version",
        "state_changed_at",
        "release_at",
    ]
    assert list(msgspec.to_builtins(item.entries[0])) == ["edition_id", "from", "to", "entry"]


def test_reinstate_keeps_the_artefacts() -> None:
    first = _sealed(None, _A, None)
    leaf = _sealed(first, _B, _A)
    kept, stones = retire(leaf, [_A], now=NOW)
    artefacts = {"urn:x": PartialRecord(fingerprint="f", partial_of="g")}
    back, _ = reinstate(
        kept,
        stones,
        edition_id=_A,
        previous=None,
        manifest_sha256="0" * 64,
        schema_version="2.52",
        now=NOW,
        artefacts=artefacts,
    )
    assert next(e for e in back.editions if e.edition_id == _A).artefacts == artefacts


def test_the_reference_writer_refuses_an_opted_in_catalog(tmp_path: Path) -> None:
    _versions(tmp_path, 1)
    _opt_in(tmp_path)
    with pytest.raises(InventoryError, match="without stage-2 support"):
        write_version(tmp_path, {})
