"""The shipped JSON Schemas of the Inventory 1.2 history formats (GH-361; sharded catalog spec §6.1-§6.3, §6.9).

Each schema has a valid example, and each invalid example differs from it by
one change, so a refusal names the rule that change breaks.
"""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import jsonschema
import pytest

from sdmxlib.inventory import decode_root, root_path, schema, sha256_hex
from tests.support.inventory import new_catalog, write_version

pytestmark = pytest.mark.unit

type Document = dict[str, object]
type Name = Literal["catalog-2.0", "root-1.2", "history", "segment-header", "segment-line"]
type Shipped = Literal["catalog", "current", "root", "leaf"] | Name

_SHIPPED: tuple[Shipped, ...] = (
    "catalog",
    "current",
    "root",
    "leaf",
    "catalog-2.0",
    "root-1.2",
    "history",
    "segment-header",
    "segment-line",
)
_CATALOG_ID = "01a18e00-0000-7000-8000-000000000000"
_OLD, _NEW = "01a18e63-e292-7250-a332-b79d9b7a7efd", "01a18e76-2bad-7480-abfe-2ba8a6889a45"
_SEGMENT = "history/0/000000000002-000000000004-01a19000-0000-7000-8000-000000000001.jsonl"
_EARLIER_SEGMENT = "history/0/000000000001-000000000001-01a18f00-0000-7000-8000-000000000001.jsonl"


def _entry(edition: str, previous: str | None, state: str) -> Document:
    return {
        "edition_id": edition,
        "previous": previous,
        "state": state,
        "manifest_sha256": "0" * 64,
        "schema_version": "2.52",
        "state_changed_at": "2026-10-09T12:30:00.412Z",
        "release_at": None,
    }


def _root() -> Document:
    """Spec §6.3's worked example: version 42, CODR table 14100287's release."""
    return {
        "format_version": "1.2",
        "seq": 42,
        "previous_inventory": 41,
        "restore_epoch": 0,
        "created_at": "2026-10-09T12:30:00.412Z",
        "producer": {"name": "statcatlib", "version": "0.14.0"},
        "embargo": True,
        "products": {"14100287": {"leaf": f"inventory/leaves/{'9' * 64}.json", "leaf_sha256": "9" * 64}},
        "retired": [{"product_id": "14100287", "edition_id": "01a17b02", "retired_at": "2026-09-30T00:00:00Z"}],
        "event": {"actor": "release-job@stc", "reason": "08:30 release", "kind": "release"},
        "extensions": {},
        "previous_root_sha256": "c" * 64,
        "changes": [
            {
                "product_id": "14100287",
                "leaf_sha256": {"from": "7" * 64, "to": "9" * 64},
                "entries": [
                    {"edition_id": _OLD, "from": "live", "to": "superseded", "entry": _entry(_OLD, None, "superseded")},
                    {"edition_id": _NEW, "from": "embargoed", "to": "live", "entry": _entry(_NEW, _OLD, "live")},
                ],
            }
        ],
    }


def _line() -> Document:
    line = {k: v for k, v in _root().items() if k not in ("products", "retired")}
    return line | {"root_sha256": "a" * 64}


def _header() -> Document:
    return {
        "format_version": "1.0",
        "catalog_id": _CATALOG_ID,
        "kind": "expiry",
        "restore_epoch": 0,
        "first": 2,
        "last": 4,
        "previous_segment": _EARLIER_SEGMENT,
        "previous_segment_sha256": "b" * 64,
        "expired_at": "2026-11-04T08:45:00Z",
        "actor": "release-job@stc",
        "roots": [2, 3, 4],
        "leaves": ["7" * 64],
        "unread": [],
    }


def _history() -> Document:
    return {
        "format_version": "1.0",
        "catalog_id": _CATALOG_ID,
        "restore_epoch": 0,
        "expired_through": 4,
        "newest_segment": _SEGMENT,
        "newest_segment_sha256": "d" * 64,
        "cleaned_through": _SEGMENT,
    }


def _catalog() -> Document:
    return {"format_version": "2.0", "catalog_id": _CATALOG_ID, "restore_epoch": 0, "history": {"since_seq": 3}}


_VALID: dict[Name, Callable[[], Document]] = {
    "catalog-2.0": _catalog,
    "root-1.2": _root,
    "history": _history,
    "segment-header": _header,
    "segment-line": _line,
}


def _validate(document: object, name: Shipped) -> None:
    jsonschema.validate(document, json.loads(schema(name)))


def _dict(value: object) -> Document:
    assert isinstance(value, dict)
    return {str(k): v for k, v in value.items()}


def _item(document: Document, index: int) -> Document:
    changes = document["changes"]
    assert isinstance(changes, list)
    entries = _dict(changes[0])["entries"]
    assert isinstance(entries, list)
    item = entries[index]
    assert isinstance(item, dict)
    return item


def _entry_of(document: Document, index: int) -> Document:
    entry = _item(document, index)["entry"]
    assert isinstance(entry, dict)
    return entry


def _set(**fields: object) -> Callable[[Document], None]:
    return lambda d: d.update(fields)


def _drop(field: str) -> Callable[[Document], None]:
    def change(document: Document) -> None:
        del document[field]

    return change


def _in_item(change: Callable[[Document], None]) -> Callable[[Document], None]:
    return lambda d: change(_item(d, 1))


# Each: (schema, the change from the valid example, the message the refusal carries).
_INVALID = [
    pytest.param("catalog-2.0", _drop("history"), "'history' is a required property", id="catalog without history"),
    pytest.param("catalog-2.0", _set(format_version="1.0"), "'1.0' does not match", id="catalog at major 1"),
    pytest.param("catalog-2.0", _set(history={"since_seq": 0}), "0 is less than the minimum of 1", id="since_seq 0"),
    pytest.param("root-1.2", _drop("changes"), "'changes' is a required property", id="root without changes"),
    pytest.param("root-1.2", _set(previous_root_sha256="C" * 64), "does not match", id="uppercase root hash"),
    pytest.param("root-1.2", _in_item(_drop("entry")), "'entry' is a required property", id="live item without entry"),
    pytest.param("root-1.2", _in_item(_set(to="tombstone")), "should not be valid", id="tombstone item with entry"),
    pytest.param("root-1.2", _in_item(_set(**{"from": "retired"})), "'retired' is not one of", id="unknown marker"),
    pytest.param(
        "root-1.2",
        lambda d: _item(d, 0).update({"from": None, "to": None}),
        "should not be valid",
        id="item from null to null",
    ),
    pytest.param(
        "root-1.2",
        lambda d: _drop("release_at")(_entry_of(d, 1)),
        "'release_at' is a required property",
        id="entry without release_at",
    ),
    pytest.param(
        "root-1.2",
        _set(event={"actor": "a", "reason": "r", "kind": "relabel"}),
        "'relabel' is not one of",
        id="unknown event kind",
    ),
    pytest.param(
        "history",
        _drop("cleaned_through"),
        "'cleaned_through' is a required property",
        id="history without cleaned_through",
    ),
    pytest.param("history", _set(newest_segment=None), "is not of type 'null'", id="no segment but a segment hash"),
    pytest.param("history", _set(newest_segment="history/0/2-4.jsonl"), "does not match", id="unpadded segment name"),
    pytest.param("segment-header", _set(kind="prune"), "'prune' is not one of", id="unknown segment kind"),
    pytest.param("segment-header", _set(unread=[3]), "is expected to be empty", id="expiry with unread roots"),
    pytest.param("segment-header", _set(kind="restore"), "is expected to be empty", id="restore with leaves"),
    pytest.param("segment-header", _set(roots=[]), "should be non-empty", id="no roots"),
    pytest.param(
        "segment-header", _set(previous_segment=None), "is not of type 'null'", id="first segment with a hash"
    ),
    pytest.param(
        "segment-line", _drop("root_sha256"), "'root_sha256' is a required property", id="line without root_sha256"
    ),
    pytest.param(
        "segment-line",
        _drop("previous_root_sha256"),
        "'previous_root_sha256' is a dependency of 'changes'",
        id="line with changes alone",
    ),
    pytest.param("segment-line", _set(products={}), "should not be valid", id="line with products"),
]


@pytest.mark.parametrize("name", _SHIPPED)
def test_every_shipped_schema_is_a_valid_draft_2020_12_schema(name: Shipped) -> None:
    jsonschema.Draft202012Validator.check_schema(json.loads(schema(name)))


@pytest.mark.parametrize("name", _VALID)
def test_the_valid_example_meets_its_schema(name: Name) -> None:
    _validate(_VALID[name](), name)


@pytest.mark.parametrize(("name", "change", "message"), _INVALID)
def test_an_invalid_example_is_refused(name: Name, change: Callable[[Document], None], message: str) -> None:
    document = copy.deepcopy(_VALID[name]())
    change(document)
    with pytest.raises(jsonschema.ValidationError, match=message):
        _validate(document, name)


def test_every_new_schema_has_invalid_examples() -> None:
    assert {p.values[0] for p in _INVALID} == set(_VALID)


def test_a_restore_header_lists_unread_roots_and_no_leaves() -> None:
    header = _header() | {"kind": "restore", "leaves": [], "unread": [4]}
    _validate(header, "segment-header")


def test_the_first_segment_and_an_opted_in_catalog_before_its_first_expiry() -> None:
    _validate(_header() | {"previous_segment": None, "previous_segment_sha256": None}, "segment-header")
    fresh = _history() | {"expired_through": 0, "newest_segment": None, "newest_segment_sha256": None}
    _validate(fresh | {"cleaned_through": None}, "history")
    with pytest.raises(jsonschema.ValidationError, match="is not of type 'null'"):
        _validate(fresh, "history")


def test_the_change_list_marks_a_retire_a_reinstate_and_a_dropped_tombstone() -> None:
    root = _root()
    root["event"] = {"actor": "admin@stc", "reason": "restore drill", "kind": "reinstate"}
    root["changes"] = [
        {
            "product_id": "14100287",
            "leaf_sha256": {"from": "7" * 64, "to": "9" * 64},
            "entries": [
                {"edition_id": _OLD, "from": "superseded", "to": "tombstone"},
                {
                    "edition_id": _NEW,
                    "from": "tombstone",
                    "to": "superseded",
                    "entry": _entry(_NEW, _OLD, "superseded"),
                },
            ],
        },
        {
            "product_id": "p2",
            "leaf_sha256": {"from": "8" * 64, "to": "8" * 64},
            "entries": [
                {"edition_id": "01a17b02", "from": "tombstone", "to": None},
            ],
        },
    ]
    _validate(root, "root-1.2")
    _validate(root, "root")  # Inventory 1.2 adds the reinstate kind to every root


def test_a_root_with_the_history_fields_meets_the_1_0_schema_and_decodes() -> None:
    """A 1.x reader ignores the history fields (spec §6.2)."""
    root = _root()
    _validate(root, "root")
    assert decode_root(json.dumps(root).encode()).seq == 42


def test_a_written_root_meets_both_root_schemas(tmp_path: Path) -> None:
    new_catalog(tmp_path)
    write_version(tmp_path, {})
    written = write_version(tmp_path, {})
    document = json.loads((tmp_path / root_path(written.seq)).read_bytes())
    _validate(document, "root")
    _validate(document, "root-1.2")


def test_a_root_without_the_history_fields_meets_only_the_root_schema() -> None:
    document = {k: v for k, v in _root().items() if k not in ("previous_root_sha256", "changes")}
    _validate(document, "root")
    with pytest.raises(jsonschema.ValidationError, match="'previous_root_sha256' is a required property"):
        _validate(document, "root-1.2")
    with pytest.raises(jsonschema.ValidationError, match="'previous_root_sha256' is a dependency of 'changes'"):
        _validate(document | {"changes": []}, "root")


def test_a_segment_line_is_its_root_without_products_and_retired() -> None:
    root = _root()
    stored = json.dumps(root, separators=(",", ":")).encode()
    line = {k: v for k, v in root.items() if k not in ("products", "retired")} | {"root_sha256": sha256_hex(stored)}
    _validate(line, "segment-line")
    assert list(line)[4] == "created_at"
    without_history = {k: v for k, v in line.items() if k not in ("previous_root_sha256", "changes")}
    _validate(without_history, "segment-line")
