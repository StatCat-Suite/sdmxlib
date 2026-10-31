"""Table, artefact, structure and bundle fingerprints in the manifest (GH-127).

A fingerprint names content: equal content, equal fingerprint, whatever the
token, file bytes or row order; any change to the content, a different one.
`tests/fixtures/fingerprints.json` records the values one fixed input gives,
so a DuckDB upgrade that changes them fails here and forces a new
`CANONICAL_FORM` rather than passing unseen.
"""

import json
from pathlib import Path

import attrs
import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.edition import CANONICAL_FORM, Manifest, Producer, check_edition, read_manifest, seal
from sdmxlib.edition._fingerprint import References, table_fingerprint
from sdmxlib.model.constraint import ConstraintAttachment, CubeRegion, KeyValue
from sdmxlib.model.ref import Ref
from sdmxlib.testing import Dim, Shape, synthetic_edition
from tests.support.bundles import CONCEPTS, DSD, FLOW, make_bundle

pytestmark = pytest.mark.unit

_PRODUCER = Producer(name="tests", version="0")
_FIXTURE = Path(__file__).parents[1] / "fixtures" / "fingerprints.json"
_CONSTRAINT = sl.DataConstraint(
    id="DC",
    maintainer="SDMX",
    name={"en": "allowed"},
    constraint_type=sl.ConstraintType.ALLOWED,
    attachment=ConstraintAttachment(dataflows=(Ref.to(FLOW),)),
    regions=(CubeRegion(keys=(KeyValue(dimension_id="GEO", values=frozenset({"G1"})),)),),
)
_UNRELATED = sl.Codelist(id="CL_OTHER", maintainer="SDMX", version="1.0", name={"en": "other"})


def _published(root: Path, *, rows: int = 10, extra: tuple[sl.SupportsUrn, ...] = ()) -> Manifest:
    return seal(make_bundle(root, rows, extra=extra), product_id="b", producer=_PRODUCER)


def _table(rows: str) -> str:
    """The table fingerprint of a lake table holding `rows`, a SQL ``VALUES`` list of ``(k, v)``."""
    conn = duckdb.connect()
    conn.execute("CREATE SCHEMA db; CREATE SCHEMA db.s")
    conn.execute(f"CREATE TABLE db.s.t AS SELECT * FROM (VALUES {rows}) AS v(k, v)")
    binding = sl.DataflowBinding.from_dsd(DSD, dataflow=Ref.to(FLOW), schema="s", table="t", database_role="db")
    return table_fingerprint(conn, binding)


# ── table ─────────────────────────────────────────────────────────────────


def test_row_order_does_not_move_the_table_fingerprint() -> None:
    assert _table("('a', 1.0), ('b', 2.0)") == _table("('b', 2.0), ('a', 1.0)")


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("('a', 1.0), ('b', 2.0)", "('a', 2.0), ('b', 1.0)"),
        ("('a', 1.0), ('a', 1.0)", "('b', 1.0), ('b', 1.0)"),
        ("('a', 1.0)", "('a', 1.0), ('a', 1.0)"),
        ("('a', 1.0)", "('a', 1.5)"),
        ("('a', NULL)", "('a', 0.0)"),
    ],
    ids=["value-moved-between-rows", "aa-vs-bb", "duplicated-row", "changed-value", "null-vs-zero"],
)
def test_a_change_to_the_rows_moves_the_table_fingerprint(one: str, other: str) -> None:
    assert _table(one) != _table(other)


def test_the_table_fingerprint_ends_with_the_row_count() -> None:
    assert _table("('a', 1.0), ('b', 2.0), ('c', 3.0)").endswith(":3")


def test_an_empty_table_fingerprints_as_zero() -> None:
    conn = duckdb.connect()
    conn.execute("CREATE SCHEMA db; CREATE SCHEMA db.s; CREATE TABLE db.s.t (k VARCHAR)")
    binding = sl.DataflowBinding.from_dsd(DSD, dataflow=Ref.to(FLOW), schema="s", table="t", database_role="db")
    assert table_fingerprint(conn, binding) == "0:0"


# ── the recorded values ───────────────────────────────────────────────────

#: One row per column type a lake table holds, with its text and fingerprint
#: in the fixture: a DuckDB release that renders one differently changes it.
_TYPED_ROW = """
    SELECT 'text'::VARCHAR AS s, 42::BIGINT AS i, 7::INTEGER AS i32, 1.5::DOUBLE AS d, 0.1::DOUBLE AS d_inexact,
           12.34::DECIMAL(10, 2) AS dec, true AS b, DATE '2021-05-01' AS dt,
           TIMESTAMP '2021-05-01 12:34:56.789' AS ts, NULL::VARCHAR AS n, 'é"\\' AS escaped
"""


def _recorded() -> dict[str, str]:
    recorded: dict[str, str] = json.loads(_FIXTURE.read_text())
    return recorded


def test_the_row_text_of_every_column_type_is_the_recorded_one() -> None:
    conn = duckdb.connect()
    conn.execute("CREATE SCHEMA db; CREATE SCHEMA db.s")
    conn.execute(f"CREATE TABLE db.s.t AS {_TYPED_ROW}")
    (text,) = conn.execute("SELECT to_json(t) FROM db.s.t AS t").fetchone() or ("",)
    binding = sl.DataflowBinding.from_dsd(DSD, dataflow=Ref.to(FLOW), schema="s", table="t", database_role="db")
    recorded = _recorded()
    assert (text, table_fingerprint(conn, binding)) == (recorded["typed_row_text"], recorded["typed_row_fingerprint"])
    assert recorded["canonical_form"] == CANONICAL_FORM


def test_a_synthetic_bundle_fingerprints_as_recorded(tmp_path: Path) -> None:
    """One bundle, built from a seed: its fingerprints on this DuckDB equal the
    ones recorded on the DuckDB named in the fixture."""
    manifest = synthetic_edition(
        tmp_path / "b",
        Shape(dimensions=(Dim(codes=7, depth=2, fanout=(2, 3)), Dim(codes=3)), observations=40, periods=2),
    )
    recorded = _recorded()
    ((binding,),) = (manifest.bindings,)
    assert (manifest.content_fingerprint, binding.fingerprint, binding.structure_fingerprint) == (
        recorded["synthetic_content_fingerprint"],
        recorded["synthetic_table_fingerprint"],
        recorded["synthetic_structure_fingerprint"],
    ), f"recorded on DuckDB {recorded['duckdb_version']}, running {duckdb.__version__}"


# ── artefact and bundle ───────────────────────────────────────────────────


def test_two_builds_from_the_same_inputs_have_two_tokens_and_one_content(tmp_path: Path) -> None:
    one, two = _published(tmp_path / "one", rows=200_000), _published(tmp_path / "two", rows=200_000)
    assert one.edition_id != two.edition_id
    assert [f.digests["sha256"] for f in one.files if f.path == "metadata.duckdb"] != [
        f.digests["sha256"] for f in two.files if f.path == "metadata.duckdb"
    ], "the files differ byte for byte"
    assert one.content_fingerprint == two.content_fingerprint
    assert [(a.urn, a.fingerprint) for a in one.artefacts] == [(a.urn, a.fingerprint) for a in two.artefacts]
    assert [(b.fingerprint, b.structure_fingerprint) for b in one.bindings] == [
        (b.fingerprint, b.structure_fingerprint) for b in two.bindings
    ]
    assert one.canonical_form == CANONICAL_FORM


def test_a_republish_without_changes_keeps_the_content_fingerprint(tmp_path: Path) -> None:
    first = make_bundle(tmp_path / "b", 200_000)
    one = seal(first, product_id="b", producer=_PRODUCER)
    with sl.LocalRegistry.build(first.parent, previous=first.name) as builder:
        second = builder.edition_dir
    assert seal(second, product_id="b", producer=_PRODUCER).content_fingerprint == one.content_fingerprint


def test_one_more_row_moves_the_table_and_bundle_fingerprints_and_not_the_structure(tmp_path: Path) -> None:
    one, two = _published(tmp_path / "one", rows=10), _published(tmp_path / "two", rows=11)
    assert one.bindings[0].fingerprint != two.bindings[0].fingerprint
    assert one.content_fingerprint != two.content_fingerprint
    assert one.bindings[0].structure_fingerprint == two.bindings[0].structure_fingerprint


def test_a_change_only_sdmx_json_cannot_carry_moves_the_artefact_fingerprint(tmp_path: Path) -> None:
    """`legacy_is_final` is a declared SDMX-JSON gap; the fingerprint is over the model."""
    final = attrs.evolve(_UNRELATED, legacy_is_final=True)
    one, two = _published(tmp_path / "one", extra=(_UNRELATED,)), _published(tmp_path / "two", extra=(final,))
    by_urn = [{a.urn: a.fingerprint for a in m.artefacts} for m in (one, two)]
    assert by_urn[0][str(_UNRELATED.urn)] != by_urn[1][str(_UNRELATED.urn)]
    assert {u: f for u, f in by_urn[0].items() if u != str(_UNRELATED.urn)} == {
        u: f for u, f in by_urn[1].items() if u != str(_UNRELATED.urn)
    }


# ── structure ─────────────────────────────────────────────────────────────


def test_a_constraint_on_the_dataflow_moves_its_structure_fingerprint(tmp_path: Path) -> None:
    """The constraint points at the dataflow, so only a closure that follows
    references *into* the dataflow sees it."""
    without = _published(tmp_path / "without")
    with_one = _published(tmp_path / "with", extra=(_CONSTRAINT,))
    changed = attrs.evolve(
        _CONSTRAINT, regions=(CubeRegion(keys=(KeyValue(dimension_id="GEO", values=frozenset({"G2"})),)),)
    )
    with_other = _published(tmp_path / "other", extra=(changed,))
    fingerprints = {m.bindings[0].structure_fingerprint for m in (without, with_one, with_other)}
    assert len(fingerprints) == 3


def test_a_sibling_dataflow_does_not_move_the_structure_fingerprint(tmp_path: Path) -> None:
    """Another dataflow of the same data structure refers to it, and is left out of the closure (GH-228)."""
    sibling = sl.Dataflow(id="G", maintainer="SDMX", version="1.0", name={"en": "G"}, structure=Ref.to(DSD))
    renamed = attrs.evolve(sibling, name={"en": "G, renamed"})
    without = _published(tmp_path / "without")
    with_one = _published(tmp_path / "with", extra=(sibling,))
    with_renamed = _published(tmp_path / "renamed", extra=(renamed,))
    fingerprints = {m.bindings[0].structure_fingerprint for m in (without, with_one, with_renamed)}
    assert len(fingerprints) == 1
    assert with_one.content_fingerprint != with_renamed.content_fingerprint, "the bundle holds the sibling"


def test_an_artefact_outside_the_closure_does_not_move_the_structure_fingerprint(tmp_path: Path) -> None:
    without = _published(tmp_path / "without")
    with_unrelated = _published(tmp_path / "with", extra=(_UNRELATED,))
    assert without.bindings[0].structure_fingerprint == with_unrelated.bindings[0].structure_fingerprint
    assert without.content_fingerprint != with_unrelated.content_fingerprint, "the bundle holds it, all the same"


def test_the_closure_follows_items_to_their_schemes_and_references_into_the_flow(tmp_path: Path) -> None:
    publication = make_bundle(tmp_path / "b", 10, extra=(_CONSTRAINT, _UNRELATED))
    with sl.LocalRegistry.build(publication.parent, resume=publication.name) as reg:
        present = [
            u
            for (u,) in reg.connection.execute(
                "SELECT urn FROM sdmx.artefact WHERE artefact_type <> 'DataflowBinding'"
            ).fetchall()
        ]
        closure = References(reg.connection, present).closure(str(FLOW.urn), [str(DSD.urn)])
    assert closure == {str(FLOW.urn), str(DSD.urn), str(CONCEPTS.urn), str(_CONSTRAINT.urn)}, (
        "the DSD's concepts are items of the scheme; the constraint refers to the flow; "
        "the unrelated codelist and the binding are out"
    )


def test_the_fingerprints_read_back_from_the_manifest(tmp_path: Path) -> None:
    manifest = _published(tmp_path / "b")
    assert read_manifest(tmp_path / "b" / manifest.edition_id) == manifest
    assert manifest.content_fingerprint is not None


def test_a_codelist_only_a_concept_names_is_in_the_structure_fingerprint(tmp_path: Path) -> None:
    """GH-152: a concept's core representation was missing from ``artefact_ref``,
    so the closure, which reads it, left such a codelist out, and changing it
    left the binding's structure fingerprint as it was."""

    def published(name: str, codes: tuple[str, ...]) -> Manifest:
        geo = sl.Codelist(
            id="CL_GEO",
            maintainer="T",
            version="1.0",
            name={"en": "geo"},
            codes=[sl.Code(id=c, name={"en": c}) for c in codes],
        )
        scheme = attrs.evolve(
            CONCEPTS,
            concepts=[
                attrs.evolve(c, core_representation=Ref.to(geo)) if c.id == "GEO" else c for c in CONCEPTS.concepts
            ],
        )
        root = make_bundle(tmp_path / name, 10, extra=(geo, scheme), closed=False)
        return seal(root, product_id="b", producer=_PRODUCER)

    one, two = published("one", ("G0", "G1")), published("two", ("G0", "G2"))
    assert one.bindings[0].structure_fingerprint != two.bindings[0].structure_fingerprint


# ── carried forward (GH-318) ──────────────────────────────────────────────


def _counting_fingerprints(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Count the table fingerprints `seal` computes; each entry is the table read."""
    computed: list[str] = []

    def counted(conn: duckdb.DuckDBPyConnection, binding: sl.DataflowBinding) -> str:
        computed.append(binding.table_location)
        return table_fingerprint(conn, binding)

    monkeypatch.setattr("sdmxlib.edition._write.table_fingerprint", counted)
    return computed


def _continued(first: Path, change: str | None) -> Manifest:
    """Seal an edition continuing `first` after running `change`, SQL over its lake, if any."""
    with sl.LocalRegistry.build(first.parent, previous=first.name) as builder:
        builder._data_plane()  # ATTACHes the lake
        if change is not None:
            builder.connection.execute(change)
        second = builder.edition_dir
    return seal(second, product_id="b", producer=_PRODUCER)


def test_an_unchanged_table_carries_its_fingerprint_without_reading_a_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = make_bundle(tmp_path / "b", 1_000)
    one = seal(first, product_id="b", producer=_PRODUCER)
    computed = _counting_fingerprints(monkeypatch)
    two = _continued(first, None)
    assert computed == []
    assert [(b.fingerprint, b.observations) for b in two.bindings] == [
        (b.fingerprint, b.observations) for b in one.bindings
    ]
    assert check_edition(first.parent / two.edition_id, at="publish").ok, "a full recompute agrees"


@pytest.mark.parametrize(
    "change",
    [
        pytest.param("INSERT INTO lake.d.obs VALUES ('G0', -1.0)", id="a new data file"),
        pytest.param("DELETE FROM lake.d.obs WHERE OBS_VALUE = 3", id="a deleted row"),
        pytest.param("ALTER TABLE lake.d.obs ADD COLUMN NOTE VARCHAR DEFAULT 'n'", id="a column added"),
        pytest.param("ALTER TABLE lake.d.obs RENAME COLUMN OBS_VALUE TO VALUE", id="a column renamed"),
        pytest.param(
            "CREATE OR REPLACE TABLE lake.d.obs AS SELECT * FROM lake.d.obs ORDER BY OBS_VALUE DESC",
            id="the table replaced, the same rows",
        ),
    ],
)
def test_a_changed_table_is_fingerprinted_again_as_a_full_recompute_would(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    first = make_bundle(tmp_path / "b", 1_000)
    seal(first, product_id="b", producer=_PRODUCER)
    computed = _counting_fingerprints(monkeypatch)
    two = _continued(first, change)
    assert computed == ["d.obs"]
    with sl.LocalRegistry.edition(first.parent / two.edition_id) as reg:
        (binding,) = reg.get_all(sl.DataflowBinding)
        assert two.bindings[0].fingerprint == table_fingerprint(reg.connection, reg.locate(binding))


def test_a_fingerprint_is_carried_only_from_the_edition_continued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = make_bundle(tmp_path / "b", 1_000)
    seal(first, product_id="b", producer=_PRODUCER)
    computed = _counting_fingerprints(monkeypatch)
    seal(make_bundle(tmp_path / "other", 1_000), product_id="other", producer=_PRODUCER)
    assert computed == ["d.obs"], "a first edition has nothing to carry from"
