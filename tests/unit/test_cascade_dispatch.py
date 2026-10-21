"""The cascade clears every table its artefact's writer wrote to (#144).

`_delete_artefact_cascade` used to run all 40 `DELETE`s for every artefact,
which was slow but could not be *wrong*: whatever a writer touched, the
cascade cleared. Dispatching on the type is faster and introduces a failure
mode — a writer that starts using a table the type's entry does not name
leaves rows behind, and a re-ingest either duplicates them or trips a
constraint.

That is not hypothetical. #395 added `concept_scheme_map`,
`category_scheme_map`, `structure_map`, `item_map` and `component_map`, and
none reached the cascade. Its round-trip tests added each artefact once, so
nothing noticed until a re-ingest raised `ConstraintException: Duplicate key`.

So the guard here does not read `_CASCADE`. It **writes each artefact kind,
watches which tables gained rows, and requires the cascade to clear exactly
those** — the truth derived from the writers rather than restated beside
them. A new table reaches this test the moment a writer uses it.
"""

from __future__ import annotations

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.model.constraint import CubeRegion, DataConstraint, KeyValue
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.model.mapping import (
    CategorySchemeMap,
    ComponentMap,
    ConceptSchemeMap,
    ItemMap,
    RepresentationMap,
    RepresentationMapping,
    StructureMap,
)
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.organisation import Agency, AgencyScheme, Contact, DataProvider, DataProviderScheme
from sdmxlib.model.ref import SupportsUrn
from sdmxlib.storage.schema import ALL_TABLES
from tests.support.factories import code_urn, concept_ref

pytestmark = pytest.mark.unit

#: Tables the cascade clears for every artefact regardless of type, so a
#: per-type entry need not name them.
UNIVERSAL = {"artefact", "localized_text", "annotation", "annotation_text", "artefact_ref"}


def _cases() -> list[tuple[str, SupportsUrn]]:
    """One artefact per kind, each populated enough to reach its child tables.

    An empty artefact writes only its spine, which would make the comparison
    below vacuous — the tables it "needs cleared" would be none.
    """
    concepts = sl.ConceptScheme(
        id="CS", maintainer="AG", version="1.0", name={"en": "c"}, concepts=[sl.Concept(id="X", name={"en": "x"})]
    )
    codelist = sl.Codelist(
        id="CL", maintainer="AG", version="1.0", name={"en": "c"}, codes=[sl.Code(id="A", name={"en": "a"})]
    )
    categories = sl.CategoryScheme(
        id="CATS", maintainer="AG", version="1.0", name={"en": "k"}, categories=[sl.Category(id="C", name={"en": "c"})]
    )
    dsd = sl.DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=sl.Ref.to(codelist))],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    dataflow = sl.Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "f"}, structure=sl.Ref.to(dsd))
    msd = MetadataStructure(
        id="MSD",
        maintainer="AG",
        version="1.0",
        name={"en": "m"},
        attributes=[MetadataAttribute(id="MA", concept=concept_ref("MA"))],
    )
    return [
        ("Codelist", codelist),
        ("ConceptScheme", concepts),
        ("CategoryScheme", categories),
        ("DataStructure", dsd),
        ("Dataflow", dataflow),
        (
            "Hierarchy",
            Hierarchy(
                id="H",
                maintainer="AG",
                version="1.0",
                name={"en": "h"},
                tree=[HierarchicalCode(id="N", code=code_urn("CL", "N"))],
            ),
        ),
        (
            "AgencyScheme",
            AgencyScheme(
                id="AGENCIES",
                maintainer="AG",
                version="1.0",
                name={"en": "a"},
                agencies=[Agency(id="X", name={"en": "x"}, contacts=(Contact(name={"en": "d"}),))],
            ),
        ),
        (
            "DataProviderScheme",
            DataProviderScheme(
                id="DATA_PROVIDERS",
                maintainer="AG",
                version="1.0",
                name={"en": "p"},
                providers=[DataProvider(id="P", name={"en": "p"}, contacts=(Contact(name={"en": "d"}),))],
            ),
        ),
        (
            "DataConstraint",
            DataConstraint(
                id="DC",
                maintainer="AG",
                version="1.0",
                name={"en": "c"},
                regions=(CubeRegion(include=True, keys=(KeyValue(dimension_id="FREQ", values=frozenset({"A"})),)),),
            ),
        ),
        (
            "RepresentationMap",
            RepresentationMap(
                id="RM",
                maintainer="AG",
                version="1.0",
                name={"en": "r"},
                sources=(sl.Ref.to(codelist),),
                targets=(sl.Ref.to(codelist),),
                mappings=(RepresentationMapping(sources=("A",), targets=("B",)),),
            ),
        ),
        (
            "ConceptSchemeMap",
            ConceptSchemeMap(
                id="CSM",
                maintainer="AG",
                version="1.0",
                name={"en": "m"},
                source=sl.Ref.to(concepts),
                target=sl.Ref.to(concepts),
                maps=(ItemMap(source_id="A", target_id="B"),),
            ),
        ),
        (
            "CategorySchemeMap",
            CategorySchemeMap(
                id="CATM",
                maintainer="AG",
                version="1.0",
                name={"en": "m"},
                source=sl.Ref.to(categories),
                target=sl.Ref.to(categories),
                maps=(ItemMap(source_id="A", target_id="B"),),
            ),
        ),
        (
            "StructureMap",
            StructureMap(
                id="SM",
                maintainer="AG",
                version="1.0",
                name={"en": "s"},
                source=sl.Ref.to(dsd),
                target=sl.Ref.to(dsd),
                maps=(ComponentMap(sources=("FREQ",), targets=("FREQ",)),),
            ),
        ),
        ("MetadataStructure", msd),
        (
            "Metadataflow",
            Metadataflow(id="MDF", maintainer="AG", version="1.0", name={"en": "m"}, structure=sl.Ref.to(msd)),
        ),
    ]


def _row_counts(conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in ALL_TABLES:
        # Identifier interpolation only — `table` comes from the DDL itself.
        row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
        assert row is not None
        counts[table] = int(row[0])
    return counts


@pytest.mark.parametrize(("kind", "artefact"), _cases(), ids=[k for k, _ in _cases()])
def test_the_cascade_covers_every_table_each_writer_writes(kind: str, artefact: SupportsUrn) -> None:
    """Write it, see which tables gained rows, then re-write and require zero growth.

    Re-writing is the assertion. A missed table shows up one of two ways: the
    second write trips the table's primary key, or — if it has none — the rows
    simply accumulate. Today it is always the first: all 45 tables in
    `ALL_TABLES` carry a primary key, measured rather than assumed. So the
    `except` below is the live path and the count comparison is the belt for a
    future table that arrives without one; neither is redundant, and the
    constraint error alone does not say what to do about it.
    """
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(artefact)
        after_first = _row_counts(reg.connection)
        touched = {t for t, n in after_first.items() if n > 0} - UNIVERSAL
        assert touched, f"{kind} wrote no child rows — the fixture is too empty to test anything"

        try:
            reg.add(artefact)  # the re-ingest that #395's maps could not survive
        except duckdb.ConstraintException as exc:
            pytest.fail(
                f"{kind}: re-writing tripped a key it had already written — the "
                f"cascade left those rows in place. Add the DELETE to "
                f"_CASCADE[{kind!r}] for the table named here:\n{exc}"
            )
        after_second = _row_counts(reg.connection)

    grew = {t: (after_first[t], after_second[t]) for t in touched if after_second[t] > after_first[t]}
    assert not grew, (
        f"{kind}: re-writing grew {sorted(grew)} — the cascade does not clear "
        f"{'it' if len(grew) == 1 else 'them'}, so a re-ingest accumulates rows. "
        f"Add the DELETE to _CASCADE[{kind!r}]."
    )


def test_every_cascade_entry_names_a_real_table() -> None:
    """The other direction: an entry for a table that no longer exists is dead,
    and would hide the fact that nothing clears whatever replaced it."""
    from sdmxlib.storage.writers import _CASCADE

    named = {statement.split()[2] for statements in _CASCADE.values() for statement in statements}
    unknown = sorted(named - set(ALL_TABLES))
    assert not unknown, f"_CASCADE names tables that are not in the schema: {unknown}"


def test_a_dispatched_cascade_is_much_shorter_than_the_undispatched_one() -> None:
    """The point of #144, as a count rather than a duration.

    A Codelist ran 40 `DELETE`s before, of which ~34 could not match a row.
    The bound is deliberately loose — this guards the order of magnitude, not
    an exact number that would churn whenever a table is added.
    """
    from sdmxlib.storage.writers import _CASCADE

    codelist = sl.Codelist(
        id="CL", maintainer="AG", version="1.0", name={"en": "c"}, codes=[sl.Code(id="A", name={"en": "a"})]
    )
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(codelist)
        conn = reg.connection
        real = type(conn).execute
        seen: list[str] = []

        def counting(
            self: duckdb.DuckDBPyConnection, query: duckdb.Statement | str, parameters: object = None
        ) -> duckdb.DuckDBPyConnection:
            if isinstance(query, str) and query.lstrip().upper().startswith("DELETE"):
                seen.append(query)
            return real(self, query, parameters)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(type(conn), "execute", counting)
            reg.add(codelist)

    total_statements = sum(len(v) for v in _CASCADE.values())
    # 11 today: the codelist's own four tables (`geo_codelist` the newest) and
    # seven spine ones, `annotation_url` among them (#422). A bound, not the
    # count, for the reason the docstring gives; undispatched, it is ~50.
    assert len(seen) <= 12, f"a Codelist re-write issued {len(seen)} deletes, of {total_statements} in _CASCADE"
