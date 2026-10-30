"""A published store leaves out the keys reads do not use, and a builder puts them back (GH-165).

`seal` rewrites ``metadata.duckdb`` without ``hierarchy_node``'s and
``artefact_ref``'s keys and ``artefact_ref``'s two indexes, which the census
bundle's read benchmarks measured as free. `LocalRegistry.build(previous=)`
restores them on the copy it extends, so a store being written refuses a
duplicate as it always did.
"""

from dataclasses import dataclass
from pathlib import Path

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.edition import check_edition
from sdmxlib.model.annotations import Annotation
from sdmxlib.storage._trim import TRIMMED_INDEXES, TRIMMED_TABLES, _definitions
from sdmxlib.storage.schema import create_schema
from tests.support.bundles import make_bundle, publish

pytestmark = pytest.mark.unit

_ANNOTATED = sl.Codelist(
    id="CL_NOTE",
    maintainer="T",
    version="1.0",
    name={"en": "noted"},
    codes=[sl.Code(id="A", name={"en": "a"}, annotations=[Annotation(type="NOTE", title="one")])],
    annotations=[Annotation(type="NOTE", title="list")],
)


@dataclass(frozen=True)
class Structure:
    """A store's keys and unique constraints, its indexes, and each trimmed table's column nullability."""

    keys: list[tuple[str, str]]
    indexes: set[str]
    nullable: list[tuple[str, str, bool]]


def _structure_of(conn: duckdb.DuckDBPyConnection) -> Structure:
    keys = conn.execute(
        """
        SELECT table_name, constraint_type FROM duckdb_constraints()
        WHERE schema_name = 'sdmx' AND constraint_type IN ('PRIMARY KEY', 'UNIQUE') ORDER BY ALL
        """
    ).fetchall()
    indexes = {str(name) for (name,) in conn.execute("SELECT index_name FROM duckdb_indexes()").fetchall()}
    nullable = conn.execute(
        """
        SELECT table_name, column_name, is_nullable FROM duckdb_columns()
        WHERE schema_name = 'sdmx' AND table_name IN ('artefact_ref', 'hierarchy_node') ORDER BY ALL
        """
    ).fetchall()
    return Structure(
        keys=[(str(t), str(c)) for t, c in keys],
        indexes=indexes,
        nullable=[(str(t), str(c), bool(n)) for t, c, n in nullable],
    )


def _structure(path: Path) -> Structure:
    with duckdb.connect(str(path), read_only=True) as conn:
        return _structure_of(conn)


def _fresh() -> Structure:
    with duckdb.connect() as conn:
        create_schema(conn).close()
        return _structure_of(conn)


def test_a_published_store_leaves_out_exactly_the_trimmed_keys_and_indexes(tmp_path: Path) -> None:
    fresh = _fresh()
    published = _structure(publish(make_bundle(tmp_path / "b", 10)) / "metadata.duckdb")
    left_out = [k for k in fresh.keys if k not in published.keys]
    assert left_out == [
        ("artefact_ref", "PRIMARY KEY"),
        ("hierarchy_node", "PRIMARY KEY"),
        ("hierarchy_node", "UNIQUE"),
    ]
    assert [k for k in published.keys if k not in fresh.keys] == [], "nothing added"
    assert fresh.indexes - published.indexes == set(TRIMMED_INDEXES)
    assert published.indexes <= fresh.indexes


def test_a_trimmed_table_keeps_every_column_constraint(tmp_path: Path) -> None:
    """Rebuilt from the schema's own definition: a key column stays NOT NULL without its key."""
    published = _structure(publish(make_bundle(tmp_path / "b", 10)) / "metadata.duckdb")
    assert published.nullable == _fresh().nullable
    assert ("artefact_ref", "source_urn", False) in published.nullable


def test_a_published_store_reads_and_checks_clean(tmp_path: Path) -> None:
    """`publish` recomputes every fingerprint from the trimmed store, so equal content is asserted too."""
    publication = publish(make_bundle(tmp_path / "b", 10, extra=(_ANNOTATED,)))
    report = check_edition(publication, at="publish")
    assert report.ok, "\n".join(map(str, report.findings))
    with sl.LocalRegistry.edition(publication) as reg:
        codelist = reg.get(_ANNOTATED.urn, eager=True)
        assert codelist is not None
        assert [a.title for a in codelist.codes["A"].annotations] == ["one"]


def test_extending_a_publication_restores_the_keys(tmp_path: Path) -> None:
    first = publish(make_bundle(tmp_path / "b", 10))
    with sl.LocalRegistry.build(first.parent, previous=first.name) as builder:
        second = builder.edition_dir
    restored = _structure(second / "metadata.duckdb")
    fresh = _fresh()
    assert restored.keys == fresh.keys
    assert restored.indexes == fresh.indexes
    with duckdb.connect(str(second / "metadata.duckdb")) as conn:
        row = conn.execute("SELECT * FROM sdmx.artefact_ref LIMIT 1").fetchone()
        assert row is not None
        with pytest.raises(duckdb.ConstraintException):
            conn.execute("INSERT INTO sdmx.artefact_ref VALUES (?, ?, ?, ?)", list(row))


def test_annotations_added_after_a_restore_take_new_ids(tmp_path: Path) -> None:
    """The store's sequences survive the rewrite, so a builder's next annotation does not reuse an id."""
    first = publish(make_bundle(tmp_path / "b", 10, extra=(_ANNOTATED,)))
    later = sl.Codelist(
        id="CL_LATER", maintainer="T", version="1.0", name={"en": "later"}, annotations=[Annotation(type="NOTE")]
    )
    with sl.LocalRegistry.build(first.parent, previous=first.name) as builder:
        builder.add(later)
        ids = [i for (i,) in builder.connection.execute("SELECT id FROM sdmx.annotation").fetchall()]
    assert len(ids) == len(set(ids)) == 3


def test_the_trimmed_names_are_the_schemas() -> None:
    """Discrimination: a renamed table or index in the DDL fails here, not as a silent no-op trim."""
    full, keyless, _, trimmed = _definitions()
    assert set(full) == set(keyless) == set(TRIMMED_TABLES)
    assert set(trimmed) == set(TRIMMED_INDEXES)
    assert all("PRIMARY KEY" in full[t] and "PRIMARY KEY" not in keyless[t] for t in TRIMMED_TABLES)
