"""A product's unsealed edition, its listing, discarding and seal journal (GH-253 to GH-259).

The behaviour statcatlib's WorkingCopy rests on (statcatlib RFC §11 item 5),
each with its refusal.
"""

from pathlib import Path

import pytest

import sdmxlib as sl
from sdmxlib.edition import (
    MANIFEST_NAME,
    ManifestError,
    Producer,
    check_edition,
    clear_journal,
    discard,
    list_editions,
    read_journal,
    read_manifest,
    seal,
)
from sdmxlib.testing import Dim, Shape, synthetic_edition
from tests.support.bundles import make_bundle, publish

pytestmark = pytest.mark.unit

_PRODUCER = Producer(name="tests", version="0")
_SHAPE = Shape(dimensions=(Dim(codes=4), Dim(codes=3)), observations=12, periods=2)


def _next(sealed: Path) -> Path:
    with sl.LocalRegistry.build(sealed.parent, previous=sealed.name) as builder:
        return builder.edition_dir


# ── one unsealed edition per product (GH-253) ───────────────────────────────


def test_a_second_unsealed_edition_is_refused_naming_the_first(tmp_path: Path) -> None:
    first = make_bundle(tmp_path / "p", 10)
    with pytest.raises(ManifestError, match=rf"unsealed edition, {first.name}, started .*resume=.*discard"):
        sl.LocalRegistry.build(tmp_path / "p")
    assert [e.edition_id for e in list_editions(tmp_path / "p")] == [first.name], "nothing was created"
    with sl.LocalRegistry.build(tmp_path / "p", resume=first.name) as resumed:
        assert resumed.edition_dir == first, "resuming it is how a builder goes on"


def test_after_a_seal_or_a_discard_a_new_edition_starts(tmp_path: Path) -> None:
    sealed = publish(make_bundle(tmp_path / "p", 10))
    second = _next(sealed)
    discard(second)
    third = _next(sealed)
    assert [e.edition_id for e in list_editions(tmp_path / "p")] == [sealed.name, third.name]


# ── list_editions (GH-254) ───────────────────────────────────────────────────


def test_list_editions_reports_each_edition_sealed_or_not(tmp_path: Path) -> None:
    one = publish(make_bundle(tmp_path / "p", 10))
    two = publish(_next(one))
    three = _next(two)
    found = list_editions(tmp_path / "p")
    assert [(e.edition_id, e.sealed, e.previous) for e in found] == [
        (one.name, True, None),
        (two.name, True, one.name),
        (three.name, False, None),
    ]
    assert found[0].created_at <= found[1].created_at <= found[2].created_at
    assert sorted(p.name for p in (tmp_path / "p").iterdir()) == sorted(
        ["data", "journal", one.name, two.name, three.name]
    ), "listing writes nothing"


# ── discard (GH-255) ─────────────────────────────────────────────────────────


def test_discard_removes_an_unsealed_edition_and_refuses_a_sealed_one(tmp_path: Path) -> None:
    sealed = publish(make_bundle(tmp_path / "p", 10))
    before = sorted(p.relative_to(tmp_path).as_posix() for p in sealed.rglob("*"))
    with pytest.raises(ManifestError, match="is sealed"):
        discard(sealed)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in sealed.rglob("*")) == before
    unsealed = _next(sealed)
    discard(unsealed)
    assert not unsealed.exists()
    assert (tmp_path / "p" / "data").is_dir(), "data files are left for garbage collection"


def test_discard_refuses_a_directory_that_is_not_an_edition(tmp_path: Path) -> None:
    (tmp_path / "p" / "data").mkdir(parents=True)
    with pytest.raises(ManifestError, match="not an edition directory"):
        discard(tmp_path / "p" / "data")
    assert (tmp_path / "p" / "data").is_dir()


# ── an unsealed synthetic edition (GH-256) ──────────────────────────────────


def test_an_unsealed_synthetic_edition_takes_the_callers_tables_then_seals(tmp_path: Path) -> None:
    builder = synthetic_edition(tmp_path / "p", _SHAPE, seed=3, seal=False)
    with builder:
        builder.connection.execute("CREATE SCHEMA statcat")
        builder.connection.execute("CREATE TABLE statcat.dataset AS SELECT 'x' AS id")
        edition = builder.edition_dir
    assert not (edition / MANIFEST_NAME).exists()
    seal(edition, product_id="synthetic-3", producer=_PRODUCER)
    assert check_edition(edition, at="publish").ok
    with sl.LocalRegistry.edition(edition) as reg:
        assert reg.connection.execute("SELECT id FROM statcat.dataset").fetchall() == [("x",)]


# ── the attached catalog's name (GH-257) ────────────────────────────────────


def test_each_edition_in_an_engine_reports_its_catalog_and_resolves_two_part_names_in_it(tmp_path: Path) -> None:
    editions = []
    for name in ("a", "b"):
        builder = synthetic_edition(tmp_path / name, _SHAPE, seed=1, seal=False)
        with builder:
            builder.connection.execute("CREATE SCHEMA statcat")
            builder.connection.execute("CREATE TABLE statcat.marker AS SELECT ? AS product", [name])
            editions.append(builder.edition_dir)
        seal(editions[-1], product_id=name, producer=_PRODUCER)
    engine = sl.EditionEngine()
    with (
        sl.LocalRegistry.edition(editions[0], engine=engine) as a,
        sl.LocalRegistry.edition(editions[1], engine=engine) as b,
    ):
        assert a.catalog_name != b.catalog_name
        assert a.catalog_name.endswith("__metadata")
        for reg, name in ((a, "a"), (b, "b")):
            cursor = engine.connection.cursor()
            cursor.execute(f'USE "{reg.catalog_name}".statcat')
            assert cursor.execute("SELECT product FROM statcat.marker").fetchall() == [(name,)]
            assert cursor.execute("SELECT count(*) > 0 FROM sdmx.artefact").fetchall() == [(True,)]


# ── search/ carried forward (GH-258) ────────────────────────────────────────


def test_build_previous_carries_the_lazy_tier_files(tmp_path: Path) -> None:
    started = make_bundle(tmp_path / "p", 10)
    (started / "search").mkdir()
    (started / "search" / "index.json").write_text('{"tables": []}')
    (started / "search" / "vectors.lance").write_bytes(b"\x00\x01embeddings")
    sealed = publish(started)
    assert {f.path: f.tier for f in read_manifest(sealed).files if f.path.startswith("search/")} == {
        "search/index.json": "lazy",
        "search/vectors.lance": "lazy",
    }
    carried = _next(sealed)
    for name in ("index.json", "vectors.lance"):
        assert (carried / "search" / name).read_bytes() == (sealed / "search" / name).read_bytes()


def test_build_previous_carries_only_the_prefixes_named(tmp_path: Path) -> None:
    started = make_bundle(tmp_path / "p", 10)
    (started / "search").mkdir()
    (started / "search" / "index.json").write_text("{}")
    (started / "extra.json").write_text("{}")
    sealed = publish(started)
    with sl.LocalRegistry.build(tmp_path / "p", previous=sealed.name, carry=("extra.json",)) as builder:
        carried = builder.edition_dir
    assert (carried / "extra.json").exists()
    assert not (carried / "search").exists()


# ── the seal journal (GH-259) ────────────────────────────────────────────────


def test_a_seal_killed_before_its_manifest_leaves_a_journal_entry_and_no_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = make_bundle(tmp_path / "p", 10)

    def killed(_path: Path) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("sdmxlib.edition._write.compact_for_publication", killed)
    with pytest.raises(KeyboardInterrupt):
        seal(started, product_id="p", producer=_PRODUCER)
    assert not (started / MANIFEST_NAME).exists()
    assert [(e.product_id, e.edition_id, e.previous) for e in read_journal(tmp_path / "p")] == [
        ("p", started.name, None)
    ]


def test_a_completed_seal_leaves_both_until_the_writer_clears_the_entry(tmp_path: Path) -> None:
    sealed = publish(make_bundle(tmp_path / "p", 10))
    manifest = read_manifest(sealed)
    (entry,) = read_journal(tmp_path / "p")
    assert (entry.edition_id, entry.sealed_at) == (sealed.name, manifest.created_at)
    clear_journal(tmp_path / "p", sealed.name)
    assert read_journal(tmp_path / "p") == []
    clear_journal(tmp_path / "p", sealed.name)  # clearing twice is fine
