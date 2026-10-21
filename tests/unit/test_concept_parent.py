"""`Concept.parent` survives every path it can travel (#307).

SDMX-ML 2.1 and 3.0 both allow `<str:Parent>` on a concept — the XSD calls it
"the semantic relationships between concepts which occur within a single
concept scheme" — and this library parsed the element and threw it away. A
nested scheme came back flat, on read *and* on round-trip write.

**Why it went unnoticed for so long, which is the part worth guarding.**
`sdmx.concept` had a `parent_id` column from the start. The writer put a
constant `NULL` in it, nothing read it back, and `Concept` had no parent field
at all. So the capability was implied by the schema and implemented nowhere,
and *an un-populated column is an absence* — absences do not fail tests. It was
found by inventory while sweeping for un-wired traversals (%306), not by
anything going red.

So this file tests the whole path rather than one hop: model → each writer →
each reader → back, plus storage, plus the query edges that were blocked on it.
A test of any single leg would have passed throughout the bug's life.
"""

from collections.abc import Sequence
from typing import cast

import pytest

import sdmxlib as sl
from sdmxlib.local import LocalRegistry
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.representation import DataType, Facet
from tests.support.roundtrip import as_structure
from tests.support.xsd import SchemaVersion

pytestmark = pytest.mark.unit


def _scheme() -> ConceptScheme:
    """ECON → PRICES → CPI, plus an unparented DEMO.

    Three deep, because a two-level fixture cannot tell `parent` from
    `ancestors`; and one root with no children, so "is a root" and "is a leaf"
    select different sets.
    """
    return ConceptScheme(
        id="CS",
        maintainer="AG",
        version="1.0",
        name=InternationalString(en="Scheme"),
        concepts=[
            Concept(id="ECON", name=InternationalString(en="Economy")),
            Concept(id="PRICES", name=InternationalString(en="Prices"), parent_id="ECON"),
            Concept(id="CPI", name=InternationalString(en="CPI"), parent_id="PRICES"),
            Concept(id="DEMO", name=InternationalString(en="Demography")),
        ],
    )


def _parents(scheme: ConceptScheme) -> dict[str, str | None]:
    return {c.id: c.parent_id for c in scheme.concepts}


_EXPECTED = {"ECON": None, "PRICES": "ECON", "CPI": "PRICES", "DEMO": None}


# ── the wire, both versions and both syntaxes ────────────────────────────────


@pytest.mark.parametrize("version", ["2.1", "3.0"])
def test_the_parent_survives_an_sdmx_ml_round_trip(version: SchemaVersion) -> None:
    """2.1 wraps the id in a `<Ref>`; 3.0 ships it as element text.

    Both spellings are the same fact, and `Code.Parent` has the identical
    split — which is why the reader for it is now shared rather than copied.
    """
    blob = sl.serialize(StructureMessage(concept_schemes=[_scheme()]), version=version)
    back = as_structure(sl.parse(blob))

    assert _parents(back.concept_schemes[0]) == _EXPECTED


def test_the_two_wire_syntaxes_are_what_the_xsd_asks_for() -> None:
    """Asserted on the bytes, because a reader that accepts both would hide a
    writer that emits the wrong one."""
    v21 = sl.serialize(StructureMessage(concept_schemes=[_scheme()]), version="2.1")
    v30 = sl.serialize(StructureMessage(concept_schemes=[_scheme()]), version="3.0")

    assert b'<str:Parent><Ref id="ECON"/></str:Parent>' in v21
    assert b"<str:Parent>ECON</str:Parent>" in v30


@pytest.mark.parametrize("version", ["2.1", "3.0"])
def test_parent_and_core_representation_together_stay_xsd_valid(version: SchemaVersion) -> None:
    """The ordering trap. `Parent` is on `ConceptBaseType` and
    `CoreRepresentation` on the extension, so the XSD sequences Parent *first*.

    Emitting them the other way round produces a document this library's own
    reader accepts and every validator rejects — exactly the writer bug pure
    round-trip testing cannot see, which is why the XSD check exists.
    """
    from tests.support.xsd import assert_valid_sdmx_ml, compile_structure_schema

    scheme = ConceptScheme(
        id="CS",
        maintainer="AG",
        version="1.0",
        name=InternationalString(en="Scheme"),
        concepts=[
            Concept(id="ECON", name=InternationalString(en="Economy")),
            Concept(
                id="PRICES",
                name=InternationalString(en="Prices"),
                parent_id="ECON",
                core_representation=Facet(type=DataType.String, max_length=50),
            ),
        ],
    )
    blob = sl.serialize(StructureMessage(concept_schemes=[scheme]), version=version)

    assert_valid_sdmx_ml(blob, compile_structure_schema(version))


def test_the_parent_survives_an_sdmx_json_round_trip() -> None:
    back = as_structure(sl.parse(sl.to_json(StructureMessage(concept_schemes=[_scheme()]))))

    assert _parents(back.concept_schemes[0]) == _EXPECTED


# ── storage, where the constant NULL lived ───────────────────────────────────


def test_the_parent_survives_a_storage_round_trip() -> None:
    with LocalRegistry(":memory:") as reg:
        reg.add(_scheme())
        back = reg.get(ConceptScheme.urn_for(agency="AG", id="CS", version="1.0"))

    assert back is not None
    assert _parents(back) == _EXPECTED


def test_the_writer_bakes_the_closure_like_every_other_tree() -> None:
    """`depth` and `ancestor_ids`, so ancestry is containment not recursion.

    The bake is what makes `ancestors` a declared *edge* rather than a verb —
    `docs/design/query-tree-paths.md` §5. Without it the accessor could only
    offer `parent`, and the path language does not do recursive CTEs.
    """
    with LocalRegistry(":memory:") as reg:
        reg.add(_scheme())
        rows = reg.execute("SELECT concept_id, parent_id, depth, ancestor_ids FROM concept ORDER BY concept_id")

    # `ancestor_ids` is a DuckDB LIST column, so the row value is untyped.
    baked = {str(r[0]): (r[1], r[2], list(cast("Sequence[str]", r[3] or ()))) for r in rows}
    assert baked["ECON"] == (None, 0, [])
    assert baked["PRICES"] == ("ECON", 1, ["ECON"])
    assert baked["CPI"] == ("PRICES", 2, ["ECON", "PRICES"]), "root-first, and transitive"
    assert baked["DEMO"] == (None, 0, [])


# ── the query edges this unblocked ───────────────────────────────────────────


@pytest.fixture
def reg():
    with LocalRegistry(":memory:") as registry:
        registry.add(_scheme())
        yield registry


def test_the_tree_edges_answer_like_every_other_tree(reg) -> None:
    q = sl.q

    assert sorted(reg.query(q.concept).filter(q.concept.parent.id.is_null()).ids()) == ["DEMO", "ECON"]
    assert reg.query(q.concept).filter(q.concept.parent.id == "ECON").ids() == ["PRICES"]
    assert sorted(reg.query(q.concept).filter(q.concept.ancestors.id == "ECON").ids()) == ["CPI", "PRICES"]
    assert reg.query(q.concept).filter(q.concept.descendants.id == "CPI").ids() == ["ECON", "PRICES"]
    assert sorted(reg.query(q.concept).filter(q.concept.children.count() == 0).ids()) == ["CPI", "DEMO"]


def test_parent_trims_to_the_local_column(reg) -> None:
    """`concept.parent.id` is `parent_id` on the same row — no join.

    The trimming that makes `parent_id` unnecessary as an accessor, and the
    reason there is exactly one spelling of this question.
    """
    sql, _ = reg.query(sl.q.concept).filter(sl.q.concept.parent.id == "ECON").to_sql()

    assert "JOIN" not in sql
    assert "parent_id" in sql


def test_depth_is_projectable(reg) -> None:
    frame = reg.query(sl.q.concept).select(sl.q.concept.id, sl.q.concept.depth).collect()
    depths = {r["id"]: r["depth"] for r in frame.to_dicts()}

    assert depths == {"ECON": 0, "PRICES": 1, "CPI": 2, "DEMO": 0}
