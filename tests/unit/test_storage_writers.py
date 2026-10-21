"""Tests for sdmxlib.storage.writers — writing domain objects to DuckDB."""

import pytest

from sdmxlib.model.annotations import Annotation
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, HierarchyAssociation, Level
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.storage.writers import (
    _split_item_urn,
    drop_artefact,
    put_artefact,
    put_codelist,
    put_concept_scheme,
    put_data_structure,
    put_dataflow,
    put_hierarchy,
    put_hierarchy_association,
)
from tests.support.factories import code_ref, concept_ref
from tests.support.narrow import present

pytestmark = pytest.mark.unit


# ── put_codelist ────────────────────────────────────────────────────────


def test_put_codelist_artefact_row(cursor, make_codelist):
    cur = cursor
    put_codelist(cur, make_codelist())
    row = cur.execute("SELECT artefact_type, agency_id, resource_id, version FROM artefact").fetchone()
    assert row is not None
    assert row == ("Codelist", "SDMX", "CL_FREQ", "1.0")


def test_put_codelist_source_registry_default_null(cursor, make_codelist):
    """No explicit source_registry → spine column stays NULL."""
    cur = cursor
    put_codelist(cur, make_codelist())
    row = cur.execute("SELECT source_registry FROM artefact").fetchone()
    assert row == (None,)


def test_put_codelist_source_registry_tagged(cursor, make_codelist):
    """Explicit source_registry is persisted on the spine."""
    cur = cursor
    put_codelist(cur, make_codelist(), source_registry="ESTAT")
    row = cur.execute("SELECT source_registry FROM artefact").fetchone()
    assert row == ("ESTAT",)


def test_local_registry_default_source_registry_applied(make_codelist):
    """Constructor-level source_registry tags every write."""
    from sdmxlib.local import LocalRegistry

    with LocalRegistry(":memory:", source_registry="local") as local:
        local.add(make_codelist())
        cur = local._cursor  # internal cursor
        row = present(cur).execute("SELECT source_registry FROM artefact").fetchone()
        assert row == ("local",)


def test_local_registry_per_call_source_registry_overrides_default(make_codelist):
    """Per-call source_registry takes precedence over constructor default."""
    from sdmxlib.local import LocalRegistry

    with LocalRegistry(":memory:", source_registry="local") as local:
        local.add(make_codelist(), source_registry="ESTAT")
        cur = local._cursor
        row = present(cur).execute("SELECT source_registry FROM artefact").fetchone()
        assert row == ("ESTAT",)


def test_cross_source_overwrite_emits_warning(cursor, make_codelist):
    """Writing the same URN with a different source_registry warns."""
    cur = cursor
    put_codelist(cur, make_codelist(), source_registry="ECB")
    with pytest.warns(RuntimeWarning, match="Spine row.*overwritten"):
        put_codelist(cur, make_codelist(), source_registry="ESTAT")
    # Last-write-wins
    row = cur.execute("SELECT source_registry FROM artefact").fetchone()
    assert row == ("ESTAT",)


def test_same_source_overwrite_no_warning(cursor, make_codelist):
    """Repeated writes with the same source don't warn."""
    cur = cursor
    put_codelist(cur, make_codelist(), source_registry="ECB")
    # Should not emit any warning
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        put_codelist(cur, make_codelist(), source_registry="ECB")


def test_put_codelist_localized_text(cursor, make_codelist):
    cur = cursor
    put_codelist(cur, make_codelist())
    row = cur.execute("SELECT text FROM localized_text WHERE field = 'name' AND lang = 'en'").fetchone()
    assert row is not None
    assert row[0] == "Frequency"


def test_put_codelist_codes(cursor, make_codelist):
    cur = cursor
    put_codelist(cur, make_codelist())
    rows = cur.execute("SELECT code_id, name['en'], position FROM code ORDER BY position").fetchall()
    assert len(rows) == 2
    assert rows[0] == ("A", "Annual", 0)
    assert rows[1] == ("Q", "Quarterly", 1)


def test_put_codelist_bilingual(cursor):
    cur = cursor
    cl = Codelist(
        id="CL_FREQ",
        maintainer="SDMX",
        name={"en": "Frequency", "fr": "Fréquence"},
        codes=[
            Code(id="A", name={"en": "Annual", "fr": "Annuel"}),
        ],
    )
    put_codelist(cur, cl)

    # Artefact-level text in localized_text
    rows = cur.execute("SELECT lang, text FROM localized_text WHERE field = 'name' ORDER BY lang").fetchall()
    assert ("en", "Frequency") in rows
    assert ("fr", "Fréquence") in rows

    # Code-level labels in MAP columns
    row = cur.execute("SELECT name['en'], name['fr'] FROM code").fetchone()
    assert row == ("Annual", "Annuel")


def test_put_codelist_upsert(cursor, make_codelist):
    cur = cursor
    put_codelist(cur, make_codelist())
    cl2 = Codelist(
        id="CL_FREQ",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Frequency v2"},
        codes=[Code(id="M", name={"en": "Monthly"})],
    )
    put_codelist(cur, cl2)

    rows = cur.execute("SELECT code_id FROM code").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "M"

    name = cur.execute("SELECT text FROM localized_text WHERE field = 'name' AND lang = 'en'").fetchone()
    assert name is not None
    assert name[0] == "Frequency v2"


def test_put_codelist_with_parent_id(cursor):
    cur = cursor
    cl = Codelist(
        name={"en": "CL_GEO"},
        id="CL_GEO",
        maintainer="SDMX",
        codes=[
            Code(id="CA", name={"en": "Canada"}),
            Code(id="ON", name={"en": "Ontario"}, parent_id="CA"),
        ],
    )
    put_codelist(cur, cl)
    row = cur.execute("SELECT parent_id FROM code WHERE code_id = 'ON'").fetchone()
    assert row is not None
    assert row[0] == "CA"


def test_put_codelist_hierarchy_precomputation(cursor):
    cur = cursor
    cl = Codelist(
        name={"en": "CL_GEO"},
        id="CL_GEO",
        maintainer="SDMX",
        codes=[
            Code(id="CA", name={"en": "Canada"}),
            Code(id="ON", name={"en": "Ontario"}, parent_id="CA"),
            Code(id="OTT", name={"en": "Ottawa"}, parent_id="ON"),
        ],
    )
    put_codelist(cur, cl)

    rows = cur.execute("SELECT code_id, depth, ancestor_ids FROM code ORDER BY code_id").fetchall()
    ca = next(r for r in rows if r[0] == "CA")
    on = next(r for r in rows if r[0] == "ON")
    ott = next(r for r in rows if r[0] == "OTT")

    assert ca[1] == 0
    assert ca[2] == []
    assert on[1] == 1
    assert on[2] == ["CA"]
    assert ott[1] == 2
    assert ott[2] == ["CA", "ON"]


def test_put_codelist_code_urn(cursor, make_codelist):
    cur = cursor
    put_codelist(cur, make_codelist())
    row = cur.execute("SELECT code_urn FROM code WHERE code_id = 'A'").fetchone()
    assert row is not None
    assert "Code" in row[0]
    assert "CL_FREQ" in row[0]
    assert row[0].endswith(".A")


# ── put_data_structure ──────────────────────────────────────────────────


def test_put_data_structure_artefact(cursor, make_dsd):
    cur = cursor
    put_data_structure(cur, make_dsd())
    row = cur.execute("SELECT artefact_type, resource_id FROM artefact").fetchone()
    assert row is not None
    assert row == ("DataStructure", "DSD_TEST")


def test_put_data_structure_components(cursor, make_dsd):
    cur = cursor
    put_data_structure(cur, make_dsd())
    rows = cur.execute(
        "SELECT component_id, component_type, codelist_urn FROM dsd_component ORDER BY position"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] == "FREQ"
    assert rows[0][1] == "Dimension"
    assert rows[0][2] is not None
    assert rows[1][0] == "OBS_VALUE"
    assert rows[1][1] == "Measure"


def test_put_data_structure_artefact_ref(cursor, make_dsd):
    cur = cursor
    put_data_structure(cur, make_dsd())
    refs = cur.execute("SELECT target_urn, role FROM artefact_ref ORDER BY role").fetchall()
    roles = {r[1] for r in refs}
    assert "enumeration" in roles
    assert "concept_identity" in roles


# ── _put_refs Arrow rewrite (#146) ──────────────────────────────────────────


def test_put_refs_dedups_intra_call_duplicates(cursor):
    """A DSD with two dims pointing at the same Codelist yields ONE enumeration ref.

    Same set-semantics the old ON CONFLICT clause provided; covered here
    explicitly now that ON CONFLICT was dropped in favour of Arrow bulk
    insert with Python-side dedup.
    """
    cur = cursor
    cl_urn = SdmxUrn.make(
        Codelist,
        package="codelist",
        artefact_class="Codelist",
        agency="SDMX",
        id="CL_SHARED",
        version="1.0",
    )
    dsd = DataStructure(
        id="DSD_DUP",
        maintainer="SDMX",
        version="1.0",
        name={"en": "DSD with two dims sharing one codelist"},
        dimensions=[
            Dimension(id="D1", representation=Ref[Codelist].unresolved(cl_urn), concept=concept_ref("D1")),
            Dimension(id="D2", representation=Ref[Codelist].unresolved(cl_urn), concept=concept_ref("D2")),
        ],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    put_data_structure(cur, dsd)
    rows = cur.execute(
        "SELECT COUNT(*) FROM artefact_ref WHERE role = 'enumeration' AND target_urn = ?",
        [str(cl_urn)],
    ).fetchone()
    assert rows[0] == 1, "intra-call dup must be collapsed to one row (first ordinal wins)"


def test_put_refs_issues_one_bulk_insert_per_call(cursor, monkeypatch):
    """Statement count is O(1), not O(N), regardless of refs batch size.

    Guards against future regression to the per-row loop. We spy on the
    Arrow bulk-insert helper and verify exactly one call lands for the
    ``artefact_ref`` table, no matter how many tuples were collected.
    """
    from sdmxlib.storage import writers as writers_mod

    calls: list[tuple[str, int]] = []
    original = writers_mod._execute_bulk_insert

    def _spy(conn, table, cols, rows):
        if table == "artefact_ref":
            calls.append((table, len(rows)))
        return original(conn, table, cols, rows)

    monkeypatch.setattr(writers_mod, "_execute_bulk_insert", _spy)
    put_data_structure(cursor, make_dsd_with_many_refs())

    assert len(calls) == 1, f"expected one bulk insert into artefact_ref, got {len(calls)}: {calls}"
    # Five distinct enumeration edges, one per dimension. The point of
    # the test is the bulk-insert call count, not the row count.
    assert calls[0][1] >= 5, f"expected ≥5 rows in the bulk insert, got {calls[0][1]}"


def make_dsd_with_many_refs() -> DataStructure:
    """A DSD with multiple distinct enumeration + concept_identity refs."""
    cls = [
        SdmxUrn.make(
            Codelist,
            package="codelist",
            artefact_class="Codelist",
            agency="SDMX",
            id=f"CL_{i:02d}",
            version="1.0",
        )
        for i in range(5)
    ]
    dims = [
        Dimension(id=f"D{i:02d}", representation=Ref[Codelist].unresolved(cls[i]), concept=concept_ref("X"))
        for i in range(5)
    ]
    return DataStructure(
        id="DSD_MANY",
        maintainer="SDMX",
        version="1.0",
        name={"en": "DSD many refs"},
        dimensions=dims,
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )


def test_put_data_structure_upsert(cursor, make_dsd):
    cur = cursor
    put_data_structure(cur, make_dsd())
    put_data_structure(cur, make_dsd())
    count = cur.execute("SELECT count(*) FROM dsd_component").fetchone()
    assert count[0] == 2


def _annotated_dsd() -> DataStructure:
    return DataStructure(
        id="DSD_ANN",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Annotated DSD"},
        dimensions=[
            Dimension(
                id="FREQ",
                annotations=[Annotation(id="DIM_ANN", title="dimension note")],
                concept=concept_ref("FREQ"),
            ),
        ],
        measures=[
            Measure(
                id="OBS_VALUE",
                annotations=[
                    Annotation(id="UNIT_MEASURE", title="Persons (thousands)", type="UNIT_MEASURE"),
                    Annotation(id="MEASURE_DIM", title="DIM2", type="MEASURE_DIM"),
                ],
                concept=concept_ref("OBS_VALUE"),
            ),
        ],
    )


def test_put_data_structure_component_annotations(cursor):
    cur = cursor
    put_data_structure(cur, _annotated_dsd())
    rows = cur.execute(
        """
        SELECT owner_urn, annotation_id, title, annotation_type
        FROM annotation
        WHERE annotation_id IN ('DIM_ANN', 'UNIT_MEASURE', 'MEASURE_DIM')
        ORDER BY annotation_id
        """
    ).fetchall()
    assert len(rows) == 3
    by_id = {r[1]: r for r in rows}
    assert by_id["DIM_ANN"][0].endswith(".FREQ")
    assert "Dimension=" in by_id["DIM_ANN"][0]
    assert by_id["UNIT_MEASURE"][0].endswith(".OBS_VALUE")
    assert "Measure=" in by_id["UNIT_MEASURE"][0]
    assert by_id["UNIT_MEASURE"][2] == "Persons (thousands)"
    assert by_id["UNIT_MEASURE"][3] == "UNIT_MEASURE"


def test_put_data_structure_component_annotations_upsert_no_orphans(cursor):
    cur = cursor
    put_data_structure(cur, _annotated_dsd())
    put_data_structure(cur, _annotated_dsd())
    count = cur.execute(
        "SELECT count(*) FROM annotation WHERE annotation_id IN ('DIM_ANN', 'UNIT_MEASURE', 'MEASURE_DIM')"
    ).fetchone()
    assert count[0] == 3


# ── put_dataflow ────────────────────────────────────────────────────────


def test_put_dataflow(cursor, make_dataflow):
    cur = cursor
    put_dataflow(cur, make_dataflow())
    row = cur.execute("SELECT dsd_urn FROM dataflow").fetchone()
    assert row is not None
    assert "DSD_TEST" in row[0]


def test_put_dataflow_localized_text(cursor, make_dataflow):
    cur = cursor
    put_dataflow(cur, make_dataflow())
    name = cur.execute("SELECT text FROM localized_text WHERE field = 'name' AND lang = 'en'").fetchone()
    desc = cur.execute("SELECT text FROM localized_text WHERE field = 'description' AND lang = 'en'").fetchone()
    assert name[0] == "Test Dataflow"
    assert desc[0] == "A test dataflow"


def test_put_dataflow_artefact_ref(cursor, make_dataflow):
    cur = cursor
    put_dataflow(cur, make_dataflow())
    refs = cur.execute("SELECT role FROM artefact_ref").fetchall()
    assert any(r[0] == "structure" for r in refs)


# ── put_concept_scheme ──────────────────────────────────────────────────


def test_put_concept_scheme_concepts(cursor, make_concept_scheme):
    cur = cursor
    put_concept_scheme(cur, make_concept_scheme())
    rows = cur.execute("SELECT concept_id, name['en'], description['en'] FROM concept ORDER BY concept_id").fetchall()
    assert len(rows) == 2
    assert rows[0] == ("FREQ", "Frequency", "Observation frequency")
    assert rows[1][0] == "REF_AREA"
    assert rows[1][1] == "Reference Area"


def test_put_concept_scheme_upsert(cursor, make_concept_scheme):
    cur = cursor
    put_concept_scheme(cur, make_concept_scheme())
    put_concept_scheme(cur, make_concept_scheme())
    count = cur.execute("SELECT count(*) FROM concept").fetchone()
    assert count[0] == 2


# ── put_hierarchy ───────────────────────────────────────────────────────


def test_put_hierarchy_artefact(cursor, make_hierarchy):
    cur = cursor
    put_hierarchy(cur, make_hierarchy())
    row = cur.execute("SELECT artefact_type, resource_id FROM artefact").fetchone()
    assert row == ("Hierarchy", "H_GEO")


def test_put_hierarchy_nodes(cursor, make_hierarchy):
    cur = cursor
    put_hierarchy(cur, make_hierarchy())
    rows = cur.execute(
        "SELECT node_id, path, parent_path, position, node_urn FROM hierarchy_node ORDER BY position"
    ).fetchall()
    assert [(r[0], r[1], r[2], r[3]) for r in rows] == [
        ("CANADA", "CANADA", None, 0),
        ("ON", "CANADA.ON", "CANADA", 1),
        ("QC", "CANADA.QC", "CANADA", 2),
    ]
    assert rows[1][4].endswith(".CANADA.ON"), "the item URN carries the path (#439)"


def test_put_hierarchy_upsert(cursor, make_hierarchy):
    cur = cursor
    put_hierarchy(cur, make_hierarchy())
    put_hierarchy(cur, make_hierarchy())
    count = cur.execute("SELECT count(*) FROM hierarchy_node").fetchone()
    assert count[0] == 3


def test_put_hierarchy_upsert_with_levels(cursor):
    """Re-adding a Hierarchy with Levels must not collide on hierarchy_level PK (#67)."""
    lvl = Level(id="Universe", name={"en": "Universe"})
    h = Hierarchy(
        id="H_LEVELED",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Leveled"},
        levels=[lvl],
        tree=[HierarchicalCode(id="ROOT", level=lvl, code=code_ref("ROOT"))],
    )
    cur = cursor
    put_hierarchy(cur, h)
    put_hierarchy(cur, h)
    count = cur.execute("SELECT count(*) FROM hierarchy_level").fetchone()
    assert count[0] == 1


# ── put_artefact (type dispatch) ────────────────────────────────────────


def test_put_artefact_codelist(cursor, make_codelist):
    cur = cursor
    put_artefact(cur, make_codelist())
    assert cur.execute("SELECT count(*) FROM code").fetchone()[0] == 2


def test_put_artefact_dsd(cursor, make_dsd):
    cur = cursor
    put_artefact(cur, make_dsd())
    assert cur.execute("SELECT count(*) FROM dsd_component").fetchone()[0] == 2


def test_put_artefact_dataflow(cursor, make_dataflow):
    cur = cursor
    put_artefact(cur, make_dataflow())
    assert cur.execute("SELECT count(*) FROM dataflow").fetchone()[0] == 1


def test_put_artefact_concept_scheme(cursor, make_concept_scheme):
    cur = cursor
    put_artefact(cur, make_concept_scheme())
    assert cur.execute("SELECT count(*) FROM concept").fetchone()[0] == 2


def test_put_artefact_hierarchy(cursor, make_hierarchy):
    cur = cursor
    put_artefact(cur, make_hierarchy())
    assert cur.execute("SELECT count(*) FROM hierarchy_node").fetchone()[0] == 3


def test_put_artefact_rejects_non_hasurn(cursor):
    cur = cursor
    with pytest.raises(TypeError, match="SupportsUrn"):
        # The refusal is the subject, so the argument has to be written wrong.
        put_artefact(cur, "not an artefact")  # pyrefly: ignore[bad-argument-type]


# ── drop_artefact ───────────────────────────────────────────────────────


def test_drop_codelist(cursor, make_codelist):
    cur = cursor
    put_codelist(cur, make_codelist())
    urn = cur.execute("SELECT urn FROM artefact").fetchone()[0]
    assert drop_artefact(cur, urn)
    assert cur.execute("SELECT count(*) FROM artefact").fetchone()[0] == 0
    assert cur.execute("SELECT count(*) FROM code").fetchone()[0] == 0


def test_drop_nonexistent(cursor):
    cur = cursor
    assert not drop_artefact(cur, "urn:sdmx:fake")


def test_drop_hierarchy(cursor, make_hierarchy):
    cur = cursor
    put_hierarchy(cur, make_hierarchy())
    urn = cur.execute("SELECT urn FROM artefact").fetchone()[0]
    assert drop_artefact(cur, urn)
    assert cur.execute("SELECT count(*) FROM hierarchy_node").fetchone()[0] == 0


# ── the linked-object split (#372) ──────────────────────────────────────


def test_split_item_urn_returns_the_owner_and_the_item() -> None:
    """A Dimension URN names the DSD that owns it plus the component id.

    The inverse of `_make_item_urn`, and the reason the pair is baked: no
    table keys on a Dimension URN, while `dsd_component` keys on
    `(dsd_urn, component_id)`.
    """
    dim = "urn:sdmx:org.sdmx.infomodel.datastructure.Dimension=A:DSD(1.0).REF_AREA"
    assert _split_item_urn(dim) == (
        "urn:sdmx:org.sdmx.infomodel.datastructure.DataStructure=A:DSD(1.0)",
        "REF_AREA",
    )


@pytest.mark.parametrize(
    ("urn", "owner"),
    [
        (
            "urn:sdmx:org.sdmx.infomodel.codelist.Code=A:CL(1.0).ON",
            "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=A:CL(1.0)",
        ),
        (
            "urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=A:CS(1.0).FREQ",
            "urn:sdmx:org.sdmx.infomodel.conceptscheme.ConceptScheme=A:CS(1.0)",
        ),
    ],
)
def test_split_item_urn_places_the_other_item_classes(urn: str, owner: str) -> None:
    split = _split_item_urn(urn)
    assert split is not None
    assert split[0] == owner


@pytest.mark.parametrize(
    "urn",
    [
        # A maintainable is not an item — there is nothing to split.
        "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=A:DF(1.0)",
        # An item class no scheme in the model holds. None, never a guess: a
        # wrong owner would join an association to the wrong component
        # silently. `HierarchicalCode` was this case until the ten-row table
        # became `model._items.owner_classes` — see the test below — so the
        # boundary is now "nothing declares it" rather than "nobody added a
        # row", and only a fabricated class is on the far side of it.
        "urn:sdmx:org.sdmx.infomodel.codelist.NotAnItemClass=A:X(1.0).Y",
        # Not a URN at all.
        "REF_AREA",
    ],
)
def test_split_item_urn_declines_rather_than_guesses(urn: str) -> None:
    assert _split_item_urn(urn) is None


@pytest.mark.parametrize(
    ("urn", "owner"),
    [
        (
            "urn:sdmx:org.sdmx.infomodel.codelist.HierarchicalCode=A:H(1.0).ON",
            "urn:sdmx:org.sdmx.infomodel.codelist.Hierarchy=A:H(1.0)",
        ),
        (
            "urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataAttribute=A:MSD(1.0).CONTACT",
            "urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataStructure=A:MSD(1.0)",
        ),
    ],
)
def test_split_item_urn_places_what_the_table_used_to_decline(urn: str, owner: str) -> None:
    """The ten-row table's boundary was "nobody added a row", not a judgement.

    `owner_classes` derives the answer from the item class's own declaration,
    so these place correctly now. Both were `None` before, which is why the
    `declines` case above had to change: the only inputs left on that side are
    a maintainable, a fabricated class and a non-URN.
    """
    split = _split_item_urn(urn)
    assert split is not None
    assert split[0] == owner


def test_put_hierarchy_association_bakes_the_component_key(cursor) -> None:
    """The columns the `component` edge joins on."""
    dim_urn = SdmxUrn.make(
        Dimension,
        package="datastructure",
        artefact_class="Dimension",
        agency="A",
        id="DSD",
        version="1.0",
        item_id="REF_AREA",
    )
    put_hierarchy_association(
        cursor,
        HierarchyAssociation(
            id="HA",
            maintainer="A",
            version="1.0",
            name={"en": "GEO on REF_AREA"},
            linked_hierarchy=Ref[object].unresolved(Hierarchy.urn_for(agency="A", id="GEO", version="1.0")),
            linked_object=Ref[object].unresolved(dim_urn),
        ),
    )
    row = cursor.execute("SELECT linked_object_owner_urn, linked_object_item_id FROM hierarchy_association").fetchone()
    assert row == ("urn:sdmx:org.sdmx.infomodel.datastructure.DataStructure=A:DSD(1.0)", "REF_AREA")


def test_a_widened_owner_cannot_join_a_dsd_component(cursor, make_dsd) -> None:
    """The safety argument for splitting more classes than the table did,
    asserted rather than reasoned about.

    `hierarchy_applicability` joins these columns to
    ``dsd_component(dsd_urn, component_id)``. A `HierarchicalCode` link now
    bakes an owner where it used to bake NULL, and that owner is a `Hierarchy`
    URN — which cannot equal a ``dsd_urn``, because the class differs inside the
    URN. So the rows the widening adds are inert for the one consumer.

    The dimension link is the discrimination check: same view, same shape, and
    it *does* produce a row. Without it this would pass on the view being empty
    for some unrelated reason.
    """
    dsd = make_dsd()
    put_data_structure(cursor, dsd)
    put_dataflow(
        cursor,
        Dataflow(id="DF", maintainer="A", version="1.0", name={"en": "df"}, structure=dsd.urn),
    )

    def link(item_urn: SdmxUrn[object], ha_id: str) -> None:
        put_hierarchy_association(
            cursor,
            HierarchyAssociation(
                id=ha_id,
                maintainer="A",
                version="1.0",
                name={"en": ha_id},
                linked_hierarchy=Ref[object].unresolved(Hierarchy.urn_for(agency="A", id="GEO", version="1.0")),
                linked_object=Ref[object].unresolved(item_urn),
            ),
        )

    link(
        SdmxUrn.make(
            Dimension,
            package="datastructure",
            artefact_class="Dimension",
            agency=dsd.agency_id,
            id=dsd.id,
            version=dsd.version,
            item_id=dsd.dimensions[0].id,
        ),
        "HA_DIM",
    )
    link(
        SdmxUrn.make(
            HierarchicalCode,
            package="codelist",
            artefact_class="HierarchicalCode",
            agency="A",
            id="GEO",
            version="1.0",
            item_id="ON",
        ),
        "HA_NODE",
    )

    baked = cursor.execute("SELECT urn, linked_object_owner_urn FROM hierarchy_association").fetchall()
    node_owners = [owner for urn, owner in baked if "HA_NODE" in urn]
    assert node_owners == ["urn:sdmx:org.sdmx.infomodel.codelist.Hierarchy=A:GEO(1.0)"], (
        f"the node link should bake its hierarchy as owner, not NULL: {node_owners}"
    )

    matched = [urn for (urn,) in cursor.execute("SELECT association_urn FROM hierarchy_applicability").fetchall()]
    assert any("HA_DIM" in urn for urn in matched), f"the dimension link must join: {matched}"
    assert not any("HA_NODE" in urn for urn in matched), f"the node link must not join: {matched}"


def test_put_hierarchy_association_leaves_a_maintainable_link_unsplit(cursor) -> None:
    """A Dataflow link has no item half, and must not invent one.

    This is the case every earlier test used, which is why the missing join
    went unnoticed: linking a maintainable works through the spine.
    """
    put_hierarchy_association(
        cursor,
        HierarchyAssociation(
            id="HA",
            maintainer="A",
            version="1.0",
            name={"en": "GEO on a flow"},
            linked_hierarchy=Ref[object].unresolved(Hierarchy.urn_for(agency="A", id="GEO", version="1.0")),
            linked_object=Ref[object].unresolved(Dataflow.urn_for(agency="A", id="DF", version="1.0")),
        ),
    )
    row = cursor.execute("SELECT linked_object_owner_urn, linked_object_item_id FROM hierarchy_association").fetchone()
    assert row == (None, None)
