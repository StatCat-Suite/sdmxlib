"""The Inventory's documents, rules and reader (GH-251; sharded catalog spec §6)."""

import json
from datetime import timedelta
from pathlib import Path

import jsonschema
import msgspec
import pytest

from sdmxlib.inventory import (
    CURRENT_NAME,
    INVENTORY_FORMAT,
    FullInventory,
    InventoryError,
    Leaf,
    LiveInventory,
    TransitionError,
    cancel,
    decode_leaf,
    decode_root,
    encode,
    head,
    local_fetch,
    record,
    reinstate,
    release,
    release_due,
    retire,
    rollback,
    root_path,
    schema,
    sha256_hex,
    supersede_pending,
)
from sdmxlib.inventory._format import generate_schemas
from tests.support.inventory import NOW, LostRaceError, create_exclusive, new_catalog, set_current, write_version

pytestmark = pytest.mark.unit

_SCHEMA = "2.52"
_A, _B, _C, _D = (f"01a18e00-0000-7000-8000-00000000000{n}" for n in range(1, 5))
_LATER = NOW + timedelta(hours=1)


def _sealed(
    leaf: Leaf | None,
    edition: str,
    previous: str | None,
    *,
    embargo: bool = False,
    schema_version: str = _SCHEMA,
    product: str = "14100287",
) -> Leaf:
    return record(
        leaf,
        product_id=product,
        kind="data",
        lifecycle="revisable",
        edition_id=edition,
        previous=previous,
        manifest_sha256="0" * 64,
        schema_version=schema_version,
        embargo=embargo,
        now=NOW,
        release_at=NOW if embargo else None,
    )


def _states(leaf: Leaf) -> dict[str, str]:
    return {e.edition_id: e.state for e in leaf.editions}


# ── documents and schemas ──────────────────────────────────────────────────


def test_the_shipped_schemas_are_the_models() -> None:
    generated = generate_schemas()
    for name in ("catalog", "current", "root", "leaf"):
        assert schema(name) == generated[name], f"regenerate src/sdmxlib/inventory/schemas/{name}.schema.json"


def test_a_written_root_and_leaf_meet_their_schemas_and_read_back(tmp_path: Path) -> None:
    new_catalog(tmp_path)
    leaf = _sealed(_sealed(None, _A, None), _B, _A)
    root = write_version(tmp_path, {"14100287": leaf})
    root_bytes = (tmp_path / root_path(root.seq)).read_bytes()
    leaf_bytes = (tmp_path / root.products["14100287"].leaf).read_bytes()
    jsonschema.validate(json.loads(root_bytes), json.loads(schema("root")))
    jsonschema.validate(json.loads(leaf_bytes), json.loads(schema("leaf")))
    assert decode_root(root_bytes) == root
    assert decode_leaf(leaf_bytes) == leaf
    assert _states(leaf) == {_A: "superseded", _B: "live"}


def test_a_newer_minor_reads_and_a_newer_major_is_refused(tmp_path: Path) -> None:
    new_catalog(tmp_path)
    root = write_version(tmp_path, {"p": _sealed(None, _A, None, product="p")})
    document = json.loads(encode(root))
    later_minor = json.dumps(document | {"format_version": "1.3", "added_in_1_3": True}).encode()
    assert decode_root(later_minor).seq == root.seq
    with pytest.raises(InventoryError, match=r"format 2\.0; this sdmxlib reads 1\.x"):
        decode_root(json.dumps(document | {"format_version": "2.0"}).encode())


def test_a_leaf_with_two_live_editions_at_one_schema_is_refused() -> None:
    leaf = _sealed(None, _A, None)
    two = msgspec.structs.replace(
        leaf, editions=(*leaf.editions, msgspec.structs.replace(leaf.editions[0], edition_id=_B))
    )
    with pytest.raises(InventoryError, match=r"two live editions at schema 2\.52"):
        decode_leaf(encode(two))


def test_a_release_at_on_an_entry_that_is_not_embargoed_is_refused() -> None:
    leaf = _sealed(None, _A, None)
    stamped = msgspec.structs.replace(leaf, editions=(msgspec.structs.replace(leaf.editions[0], release_at=NOW),))
    with pytest.raises(InventoryError, match="is live and has a release_at"):
        decode_leaf(encode(stamped))


def test_a_naive_time_is_refused() -> None:
    with pytest.raises(ValueError, match="names no time zone"):
        record(
            None,
            product_id="p",
            kind="data",
            lifecycle="final",
            edition_id=_A,
            previous=None,
            manifest_sha256="0" * 64,
            schema_version=_SCHEMA,
            embargo=False,
            now=NOW.replace(tzinfo=None),
        )


# ── transitions ────────────────────────────────────────────────────────────


def test_a_seal_with_embargo_off_goes_live_and_supersedes_its_base() -> None:
    first = _sealed(None, _A, None)
    assert _states(first) == {_A: "live"}
    second = _sealed(first, _B, _A)
    assert _states(second) == {_A: "superseded", _B: "live"}
    assert head(second, _SCHEMA) is not None


def test_a_non_fast_forward_seal_is_refused_naming_both_editions() -> None:
    leaf = _sealed(_sealed(None, _A, None), _B, _A)
    with pytest.raises(TransitionError, match=rf"{_C} is built on {_A}.*live edition at schema 2.52 is {_B}") as raised:
        _sealed(leaf, _C, _A)
    assert raised.value.rule == "fast-forward"


def test_a_new_schemas_first_edition_may_build_on_the_old_schemas_live_one() -> None:
    """The rollout of spec §9.2: one live edition per schema version."""
    leaf = _sealed(_sealed(None, _A, None), _B, _A, schema_version="2.53")
    assert _states(leaf) == {_A: "live", _B: "live"}


def test_an_embargoed_edition_releases_only_when_due_or_explicitly() -> None:
    leaf = _sealed(_sealed(None, _A, None), _B, _A, embargo=True)
    assert _states(leaf) == {_A: "live", _B: "embargoed"}
    late = msgspec.structs.replace(leaf.editions[1], release_at=_LATER)
    pending = msgspec.structs.replace(leaf, editions=(leaf.editions[0], late))
    with pytest.raises(TransitionError, match="not due") as raised:
        release(pending, _B, now=NOW)
    assert raised.value.rule == "not-due"
    assert _states(release(pending, _B, now=_LATER)) == {_A: "superseded", _B: "live"}
    assert _states(release(pending, _B, now=NOW, explicit=True)) == {_A: "superseded", _B: "live"}


def test_release_due_releases_what_it_can_and_reports_the_rest() -> None:
    good = _sealed(_sealed(None, _A, None, product="good"), _B, _A, embargo=True, product="good")
    forked = _sealed(_sealed(None, _A, None, product="forked"), _B, _A, product="forked")
    forked = _sealed(forked, _C, _A, embargo=True, product="forked")
    changed, refused = release_due({"good": good, "forked": forked}, NOW)
    assert _states(changed["good"]) == {_A: "superseded", _B: "live"}
    assert "forked" not in changed
    assert list(refused) == [f"forked/{_C}"]
    assert "is built on" in refused[f"forked/{_C}"]


def test_cancel_and_supersede_pending_withdraw_an_embargoed_edition() -> None:
    leaf = _sealed(_sealed(None, _A, None), _B, _A, embargo=True)
    assert _states(cancel(leaf, _B, now=NOW))[_B] == "withdrawn"
    corrected = _sealed(leaf, _C, _A, embargo=True)
    assert _states(supersede_pending(corrected, _B, by=_C, now=NOW)) == {
        _A: "live",
        _B: "withdrawn",
        _C: "embargoed",
    }
    with pytest.raises(TransitionError) as raised:
        cancel(leaf, _A, now=NOW)
    assert raised.value.rule == "state"


def test_rollback_is_conditional_on_the_live_edition() -> None:
    leaf = _sealed(_sealed(_sealed(None, _A, None), _B, _A), _C, _B)
    back = rollback(leaf, schema_version=_SCHEMA, if_live=_C, now=NOW)
    assert _states(back) == {_A: "superseded", _B: "live", _C: "withdrawn"}
    assert _states(rollback(leaf, schema_version=_SCHEMA, if_live=_C, to=_A, now=NOW))[_A] == "live"
    # A retry after a competing release: the live edition is no longer C.
    competing = _sealed(back, _D, _B)
    with pytest.raises(TransitionError, match=f"expects {_C} live") as raised:
        rollback(competing, schema_version=_SCHEMA, if_live=_C, now=NOW)
    assert raised.value.rule == "if-live"


def test_a_withdrawn_edition_is_never_released_again() -> None:
    leaf = rollback(_sealed(_sealed(None, _A, None), _B, _A), schema_version=_SCHEMA, if_live=_B, now=NOW)
    with pytest.raises(TransitionError) as raised:
        release(leaf, _B, now=NOW, explicit=True)
    assert raised.value.rule == "state"
    changed, _ = release_due({"14100287": leaf}, _LATER)
    assert changed == {}


def test_retired_editions_become_tombstones_and_only_reinstate_records_them() -> None:
    leaf = _sealed(_sealed(None, _A, None), _B, _A)
    kept, stones = retire(leaf, [_A], now=NOW)
    assert _states(kept) == {_B: "live"}
    assert [(t.product_id, t.edition_id) for t in stones] == [("14100287", _A)]
    with pytest.raises(TransitionError) as raised:
        record(
            kept,
            product_id="14100287",
            kind="data",
            lifecycle="revisable",
            edition_id=_A,
            previous=None,
            manifest_sha256="0" * 64,
            schema_version=_SCHEMA,
            embargo=False,
            now=NOW,
            retired={_A},
        )
    assert raised.value.rule == "tombstone"
    back, remaining = reinstate(
        kept, stones, edition_id=_A, previous=None, manifest_sha256="0" * 64, schema_version=_SCHEMA, now=NOW
    )
    assert _states(back) == {_A: "superseded", _B: "live"}
    assert remaining == ()
    with pytest.raises(TransitionError):
        retire(kept, [_B], now=NOW)


# ── reading ────────────────────────────────────────────────────────────────


def _catalog(tmp_path: Path, versions: int) -> Path:
    new_catalog(tmp_path)
    leaf: Leaf | None = None
    previous = None
    for n in range(versions):
        edition = f"01a18e00-0000-7000-8000-0000000001{n:02d}"
        leaf = _sealed(leaf, edition, previous)
        previous = edition
        write_version(tmp_path, {"14100287": leaf})
    return tmp_path


def test_the_reader_finds_a_version_current_does_not_name_by_probing(tmp_path: Path) -> None:
    root = _catalog(tmp_path, 2)
    write_version(root, {"other": _sealed(None, _A, None, product="other")}, update_current=False)
    assert json.loads((root / CURRENT_NAME).read_text())["seq"] == 2
    assert FullInventory(local_fetch(root)).refresh().seq == 3


def test_the_reader_never_goes_back_to_a_lower_seq(tmp_path: Path) -> None:
    root = _catalog(tmp_path, 3)
    reader = FullInventory(local_fetch(root)).refresh()
    assert reader.seq == 3
    (root / root_path(3)).unlink()  # as a restored older copy of the catalog would read
    set_current(root, 2, _sha(root, 2))
    assert reader.refresh().seq == 3, "a reader holding 3 never accepts 2"
    assert FullInventory(local_fetch(root)).refresh().seq == 2, "a new reader takes what is there"


def test_a_restore_epoch_change_resets_the_floor(tmp_path: Path) -> None:
    root = _catalog(tmp_path, 3)
    reader = FullInventory(local_fetch(root)).refresh()
    (root / root_path(3)).unlink()
    write_version(root, {}, restore_epoch=1, kind="restore")
    set_current(root, 3, _sha(root, 3), restore_epoch=1)
    assert reader.refresh().restore_epoch == 1
    assert reader.seq == 3


def test_a_current_whose_root_does_not_match_falls_back(tmp_path: Path) -> None:
    root = _catalog(tmp_path, 2)
    set_current(root, 2, "f" * 64)
    reader = FullInventory(local_fetch(root)).refresh()
    assert reader.seq == 2, "the probe from version 1 finds 2 again"
    assert any("SHA-256" in s for s in reader.skipped)


def test_an_invalid_root_is_skipped_and_reported(tmp_path: Path) -> None:
    root = _catalog(tmp_path, 2)
    create_exclusive(root / root_path(3), b"{not json")
    reader = FullInventory(local_fetch(root)).refresh()
    assert reader.seq == 2
    assert len(reader.skipped) == 1
    assert reader.skipped[0].startswith(f"{root_path(3)}: an Inventory root is not JSON")


def test_a_describe_root_validates_and_both_readers_take_it(tmp_path: Path) -> None:
    root = _catalog(tmp_path, 1)
    write_version(root, {}, kind="describe", update_current=False)
    document = json.loads((root / root_path(2)).read_bytes())
    document["extensions"] = {"statcat": {"title": {"en": "Census catalog"}}}
    data = json.dumps(document).encode()
    (root / root_path(2)).write_bytes(data)
    set_current(root, 2, sha256_hex(data))
    jsonschema.validate(document, json.loads(schema("root")))
    full = FullInventory(local_fetch(root)).refresh()
    assert (full.seq, full.root.event.kind, full.skipped) == (2, "describe", [])
    assert full.root.extensions == document["extensions"]
    live = LiveInventory(local_fetch(root), schema_version=_SCHEMA).refresh()
    assert (live.seq, live.skipped) == (2, [])
    assert live.head("14100287").edition_id == "01a18e00-0000-7000-8000-000000000100"


def test_exclusive_create_refuses_a_second_writer(tmp_path: Path) -> None:
    root = _catalog(tmp_path, 1)
    with pytest.raises(LostRaceError):
        create_exclusive(root / root_path(1), b"{}")


def test_the_live_view_never_holds_an_embargoed_entry(tmp_path: Path) -> None:
    new_catalog(tmp_path)
    live = _sealed(_sealed(None, _A, None, product="live"), _B, _A, embargo=True, product="live")
    hidden = _sealed(None, _C, None, embargo=True, product="hidden")
    write_version(tmp_path, {"live": live, "hidden": hidden}, embargo=True)
    view = LiveInventory(local_fetch(tmp_path), schema_version=_SCHEMA).refresh()
    assert view.products() == ["live"]
    assert view.head("live").edition_id == _A
    with pytest.raises(KeyError, match="hidden has no live edition"):
        view.head("hidden")
    full = FullInventory(local_fetch(tmp_path)).refresh()
    assert full.products() == ["hidden", "live"]
    assert {e.state for e in full.entries("live")} == {"live", "embargoed"}
    assert not hasattr(view, "leaf")
    assert not hasattr(view, "entries")


def test_the_live_view_routes_by_schema_version(tmp_path: Path) -> None:
    new_catalog(tmp_path)
    write_version(
        tmp_path, {"p": _sealed(_sealed(None, _A, None, product="p"), _B, _A, schema_version="2.53", product="p")}
    )
    assert LiveInventory(local_fetch(tmp_path), schema_version="2.52").refresh().head("p").edition_id == _A
    assert LiveInventory(local_fetch(tmp_path), schema_version="2.53").refresh().head("p").edition_id == _B


def test_the_format_version_is_the_spec_s() -> None:
    assert INVENTORY_FORMAT == "1.0"


def _sha(root: Path, seq: int) -> str:
    return sha256_hex((root / root_path(seq)).read_bytes())
