"""Tests for sdmxlib.storage.schema — DuckDB v2 relational schema."""

import duckdb
import pytest

from sdmxlib.storage.schema import (
    ALL_TABLES,
    ALL_VIEWS,
    SCHEMA_VERSION,
    create_schema,
    get_schema_version,
    migrate,
    schema_exists,
)

pytestmark = pytest.mark.unit


def _table_names(cursor: duckdb.DuckDBPyConnection) -> set[str]:
    """The stored relations — `BASE TABLE` only.

    `information_schema.tables` lists views too, so an unfiltered read made
    `hierarchy_applicability` (#372) look like a missing table. Filtering here
    rather than subtracting `ALL_VIEWS` at each call site keeps "table" meaning
    one thing.
    """
    rows = cursor.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'sdmx' AND table_type = 'BASE TABLE'"
    ).fetchall()
    return {r[0] for r in rows}


def _view_names(cursor: duckdb.DuckDBPyConnection) -> set[str]:
    rows = cursor.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'sdmx' AND table_type = 'VIEW'"
    ).fetchall()
    return {r[0] for r in rows}


def _column_info(cursor: duckdb.DuckDBPyConnection, table: str) -> dict[str, str]:
    """Return {column_name: data_type} for a table in the sdmx schema."""
    rows = cursor.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'sdmx' AND table_name = ?
        ORDER BY ordinal_position
        """,
        [table],
    ).fetchall()
    return {r[0]: r[1] for r in rows}


# ── Schema lifecycle ────────────────────────────────────────────────


def test_creates_all_tables():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    assert _table_names(cursor) == ALL_TABLES
    assert _view_names(cursor) == ALL_VIEWS


def test_idempotent():
    conn = duckdb.connect(":memory:")
    create_schema(conn)
    cursor = create_schema(conn)
    assert _table_names(cursor) == ALL_TABLES


def test_schema_exists():
    conn = duckdb.connect(":memory:")
    assert not schema_exists(conn)
    create_schema(conn)
    assert schema_exists(conn)


def test_schema_version():
    conn = duckdb.connect(":memory:")
    assert get_schema_version(conn) is None
    create_schema(conn)
    assert get_schema_version(conn) == SCHEMA_VERSION


def test_migrate_drops_and_rebuilds():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    cursor.execute(
        """
        INSERT INTO artefact (urn, artefact_type, agency_id, resource_id)
        VALUES ('urn:test', 'Codelist', 'TEST', 'CL')
        """
    )
    cursor2 = migrate(conn)
    row = cursor2.execute("SELECT COUNT(*) FROM artefact").fetchone()
    assert row is not None
    assert row[0] == 0


# ── Catalogue spine (artefact) ──────────────────────────────────────


def test_artefact_columns():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    cols = _column_info(cursor, "artefact")
    assert "urn" in cols
    assert "artefact_type" in cols
    assert "agency_id" in cols
    assert "resource_id" in cols
    assert "version" in cols
    assert "is_final" in cols
    assert "is_external_ref" in cols
    assert "valid_from" in cols
    assert "valid_to" in cols
    assert "source_registry" in cols
    assert "fetched_at" in cols
    # No JSON blob column
    assert "_document" not in cols


def test_artefact_no_language_columns():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    cols = _column_info(cursor, "artefact")
    lang_cols = [c for c in cols if c.endswith(("_en", "_fr"))]
    assert lang_cols == []


# ── Localized text ──────────────────────────────────────────────────


def test_localized_text_columns():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    cols = _column_info(cursor, "localized_text")
    assert "owner_urn" in cols
    assert "field" in cols
    assert "lang" in cols
    assert "text" in cols


# ── Annotations ─────────────────────────────────────────────────────


def test_annotation_columns():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    cols = _column_info(cursor, "annotation")
    assert "id" in cols
    assert "owner_urn" in cols
    assert "ordinal" in cols
    assert "annotation_id" in cols
    assert "title" in cols
    assert "annotation_type" in cols
    # Several URLs now, each with its language — and SDMX-JSON's URN and
    # type — in their own table (#422).
    assert "url" not in cols
    assert set(_column_info(cursor, "annotation_url")) == {
        "annotation_id",
        "position",
        "url",
        "lang",
        "link_urn",
        "link_type",
    }


def test_annotation_text_columns():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    cols = _column_info(cursor, "annotation_text")
    assert "annotation_id" in cols
    assert "lang" in cols
    assert "text" in cols


# ── Reference graph ─────────────────────────────────────────────────


def test_artefact_ref_columns():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    cols = _column_info(cursor, "artefact_ref")
    assert "source_urn" in cols
    assert "target_urn" in cols
    assert "role" in cols
    assert "ordinal" in cols


# ── Item tables with MAP columns ────────────────────────────────────


def test_code_has_map_columns():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    cols = _column_info(cursor, "code")
    assert cols["name"] == "MAP(VARCHAR, VARCHAR)"
    assert cols["description"] == "MAP(VARCHAR, VARCHAR)"
    assert "code_urn" in cols
    assert "codelist_urn" in cols
    assert "code_id" in cols
    assert "parent_id" in cols
    assert "position" in cols
    assert "depth" in cols
    assert cols["ancestor_ids"] == "VARCHAR[]"
    # No old-style columns
    assert "_annotations" not in cols
    assert "parent_code_id" not in cols


def test_concept_has_map_columns():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    cols = _column_info(cursor, "concept")
    assert cols["name"] == "MAP(VARCHAR, VARCHAR)"
    assert cols["description"] == "MAP(VARCHAR, VARCHAR)"
    assert "concept_urn" in cols
    assert "repr_kind" in cols
    assert "codelist_urn" in cols
    assert "text_type" in cols


def test_category_has_map_columns():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    cols = _column_info(cursor, "category")
    assert cols["name"] == "MAP(VARCHAR, VARCHAR)"
    assert "depth" in cols
    assert cols["ancestor_ids"] == "VARCHAR[]"


def test_agency_has_map_columns():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    cols = _column_info(cursor, "agency")
    assert cols["name"] == "MAP(VARCHAR, VARCHAR)"
    assert "agency_urn" in cols
    assert "scheme_urn" in cols


def test_data_provider_has_map_columns():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    cols = _column_info(cursor, "data_provider")
    assert cols["name"] == "MAP(VARCHAR, VARCHAR)"
    assert "provider_urn" in cols


# ── Structure tables ────────────────────────────────────────────────


def test_dsd_component_columns():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    cols = _column_info(cursor, "dsd_component")
    assert "dsd_urn" in cols
    assert "component_id" in cols
    assert "component_type" in cols
    assert "position" in cols
    assert "concept_urn" in cols
    assert "repr_kind" in cols
    assert "codelist_urn" in cols
    assert "text_type" in cols
    assert "min_length" in cols
    assert "max_length" in cols
    assert "usage_status" in cols
    assert "attachment_level" in cols
    assert "attachment_group" in cols


def test_dsd_group_and_group_dimension():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    assert "dsd_group" in _table_names(cursor)
    assert "group_dimension" in _table_names(cursor)
    cols = _column_info(cursor, "group_dimension")
    assert "dsd_urn" in cols
    assert "group_id" in cols
    assert "dimension_id" in cols


# ── Extension tables ────────────────────────────────────────────────


def test_dataflow_columns():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    cols = _column_info(cursor, "dataflow")
    assert "urn" in cols
    assert "dsd_urn" in cols


def test_provision_agreement_columns():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    cols = _column_info(cursor, "provision_agreement")
    assert "urn" in cols
    assert "dataflow_urn" in cols
    assert "data_provider_urn" in cols


def test_categorisation_columns():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    cols = _column_info(cursor, "categorisation")
    assert "urn" in cols
    assert "source_urn" in cols
    assert "target_urn" in cols


def test_hierarchy_node_columns():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    cols = _column_info(cursor, "hierarchy_node")
    assert "node_urn" in cols
    assert "hierarchy_urn" in cols
    assert "node_id" in cols
    assert "path" in cols
    assert "parent_path" in cols
    assert "position" in cols
    assert "code_urn" in cols
    # No name or description: a hierarchical code is identifiable, not nameable (#419).
    assert "name" not in cols
    assert "description" not in cols
    assert "depth" in cols
    assert cols["ancestor_ids"] == "VARCHAR[]"
    assert cols["ancestor_paths"] == "VARCHAR[]"


# ── Constraint tables ───────────────────────────────────────────────


def test_constraint_tables():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    tables = _table_names(cursor)
    assert "data_constraint" in tables
    assert "cube_region" in tables
    assert "region_key_value" in tables

    cols = _column_info(cursor, "data_constraint")
    assert "constraint_type" in cols

    cols = _column_info(cursor, "region_key_value")
    assert "constraint_urn" in cols
    assert "component_id" in cols
    assert "value" in cols


# ── Mapping tables ──────────────────────────────────────────────────


def test_mapping_tables():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)
    tables = _table_names(cursor)
    assert "representation_map" in tables
    assert "representation_map_source" in tables
    assert "representation_map_target" in tables
    assert "representation_mapping" in tables


# ── MAP column round-trip ───────────────────────────────────────────


def test_map_column_insert_and_read():
    conn = duckdb.connect(":memory:")
    cursor = create_schema(conn)

    cursor.execute(
        """
        INSERT INTO code (code_urn, codelist_urn, code_id, name, description)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            "urn:sdmx:org.sdmx.infomodel.codelist.Code=TEST:CL(1.0).A",
            "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=TEST:CL(1.0)",
            "A",
            {"en": "Annual", "fr": "Annuel"},
            {"en": "Annual frequency"},
        ],
    )

    row = cursor.execute("SELECT name['en'], name['fr'], description['en'] FROM code WHERE code_id = 'A'").fetchone()
    assert row == ("Annual", "Annuel", "Annual frequency")

    # Missing language returns None
    row = cursor.execute("SELECT name['de'] FROM code WHERE code_id = 'A'").fetchone()
    assert row is not None
    assert row[0] is None


# ── The facet column group, which is derived rather than repeated ────────────


def test_every_representation_bearing_table_carries_the_same_facet_columns(cursor):
    """`Representation` is one IM concept, so it is one column group.

    It used to be three hand-written copies, and they had drifted twice by the
    time anyone compared them: `concept` prefixed every column `repr_` while
    `dsd_component` and `metadata_attribute` did not, and
    `metadata_attribute` was missing `is_multilingual` outright — so a
    `MetadataAttribute` carrying a multilingual facet round-tripped as `None`
    while a `Concept` and a `Dimension` kept it.

    Neither drift was visible from any one table, which is the point of
    checking them against each other rather than against a hand-written list.
    The DDL now interpolates `schema._FACET_COLUMNS` into all three.
    """
    from sdmxlib.storage.schema import FACET_COLUMN_NAMES

    expected = set(FACET_COLUMN_NAMES)
    assert expected, "the derived name list is empty — the DDL parse is wrong"

    for table in ("concept", "dsd_component", "metadata_attribute"):
        cols = {r[0] for r in cursor.execute(f"DESCRIBE sdmx.{table}").fetchall()}
        assert expected <= cols, f"{table} is missing {sorted(expected - cols)}"


def test_the_facet_names_are_read_off_the_ddl_not_restated(cursor):
    """`FACET_COLUMN_NAMES` is parsed from `_FACET_COLUMNS`, so it cannot
    disagree with what the schema actually creates.

    A hand-maintained list beside the DDL is the same failure one level up:
    it would go stale exactly when a column is added, which is the moment it
    is supposed to help.
    """
    from sdmxlib.storage.schema import _FACET_COLUMNS, FACET_COLUMN_NAMES

    for name in FACET_COLUMN_NAMES:
        assert f"    {name} " in _FACET_COLUMNS, f"{name} is not a column of the group"
    assert "repr_kind" in FACET_COLUMN_NAMES, "the arm discriminator belongs to the group"
    assert "codelist_urn" in FACET_COLUMN_NAMES, "the enumerated arm belongs to the group"


def test_no_table_still_uses_the_repr_prefix(cursor):
    """`concept` prefixed its facet columns and the other two did not.

    Asserted as an absence so the normalisation cannot be half-undone by a
    future table declaring its own prefixed copy.
    """
    for table in ("concept", "dsd_component", "metadata_attribute"):
        cols = {r[0] for r in cursor.execute(f"DESCRIBE sdmx.{table}").fetchall()}
        prefixed = {c for c in cols if c.startswith("repr_") and c != "repr_kind"}
        assert not prefixed, f"{table} still carries prefixed facet columns: {sorted(prefixed)}"
