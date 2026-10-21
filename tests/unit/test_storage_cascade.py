"""`_delete_artefact_cascade` — the re-add contract, and the skip that rests on it.

The cascade clears an artefact's child rows before it is rewritten. #317 made
it return immediately when the artefact is not stored, which on a fresh
materialisation is every artefact and took the write from 8.5 ms to 3.1 ms per
artefact.

That skip is only sound because of an ordering invariant: every writer
cascades *before* `_put_spine`, so child rows never exist behind an absent
spine row. `test_the_cascade_precondition_holds_for_every_writer` asserts the
ordering off the source rather than trusting the docstring that claims it — a
writer added later with the two calls transposed makes the skip silently wrong,
and the symptom is stale rows nobody looks for.

The rest of this file is the re-add contract itself, which the skip must not
weaken. #309 is why it is written per artefact kind: contacts were deleted
*after* the agencies that reached them, so the subquery found nothing, and a
re-added scheme accumulated one duplicate contact set per add — silently,
because nothing counted them.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

import attrs
import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.model.category import Category, CategoryScheme
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.organisation import Agency, AgencyScheme, Contact
from sdmxlib.storage import writers
from tests.support import maximal
from tests.support.factories import concept_ref

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sdmxlib.model.ref import SupportsUrn

pytestmark = pytest.mark.unit

ESTAT = Agency(id="ESTAT")


def _artefacts() -> dict[str, SupportsUrn]:
    """One artefact per kind that owns child rows in a distinct table.

    Not every kind in `_WRITER_DISPATCH` — the ones whose children live in a
    table the cascade names, which is where a missed delete shows up.
    """
    dsd = DataStructure(
        id="DSD",
        maintainer=ESTAT,
        name={"en": "DSD"},
        dimensions=[
            Dimension(id="FREQ", concept=concept_ref("FREQ")),
            Dimension(id="REF_AREA", concept=concept_ref("REF_AREA")),
        ],
    )
    return {
        "codelist": Codelist(
            id="CL",
            maintainer=ESTAT,
            name={"en": "CL", "fr": "CL"},
            codes=[Code(id="A", name={"en": "Annual"}), Code(id="Q", name={"en": "Quarterly"})],
        ),
        "concept_scheme": ConceptScheme(
            id="CS",
            maintainer=ESTAT,
            name={"en": "CS"},
            concepts=[Concept(id="FREQ", name={"en": "Frequency"})],
        ),
        "category_scheme": CategoryScheme(
            id="CATS",
            maintainer=ESTAT,
            name={"en": "Cats"},
            categories=[Category(id="ECON", name={"en": "Economy"})],
        ),
        "data_structure": dsd,
        "dataflow": Dataflow(id="FLOW", maintainer=ESTAT, name={"en": "Flow"}, structure=dsd),
        # #309's shape: contacts are reached *through* their agency, so the
        # delete order is load-bearing and re-adding is what exposes it.
        "agency_scheme": AgencyScheme(
            id="AGENCIES",
            maintainer=ESTAT,
            name={"en": "Agencies"},
            agencies=[
                Agency(
                    id="ESTAT",
                    name={"en": "Eurostat"},
                    contacts=(Contact(name={"en": "Support"}, emails=("a@b.c",)),),
                )
            ],
        ),
    }


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    with sl.LocalRegistry(":memory:") as registry:
        yield registry


def _counts(registry: sl.LocalRegistry) -> dict[str, int]:
    """Row counts for every populated table — the shape a re-add must preserve."""
    conn = registry._c
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'sdmx'"
        ).fetchall()
    ]
    counts: dict[str, int] = {}
    for table in sorted(str(t) for t in tables):
        n = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
        if n and n[0]:
            counts[table] = int(n[0])
    return counts


@pytest.mark.parametrize("kind", list(_artefacts()))
def test_re_adding_an_artefact_leaves_the_row_counts_unchanged(reg: sl.LocalRegistry, kind: str) -> None:
    """Add, add again, add a third time — nothing accumulates.

    Three rather than two: #309 grew by one set per add, so a single re-add
    already shows it, but a *third* distinguishes "duplicates once" from
    "duplicates every time", and only the second is the real bug.
    """
    artefact = _artefacts()[kind]
    reg.add(artefact)
    after_first = _counts(reg)
    reg.add(artefact)
    after_second = _counts(reg)
    reg.add(artefact)
    assert after_second == after_first, f"{kind} accumulated rows on re-add"
    assert _counts(reg) == after_first, f"{kind} accumulated rows on the third add"


def test_replacing_an_artefact_removes_the_rows_it_no_longer_has(reg: sl.LocalRegistry) -> None:
    """The cascade's actual job: a narrower artefact must not keep its old children.

    The complement of the test above. #317's skip returns early when the
    artefact is *absent*; here it is present, the deletes must run, and
    without them the dropped code survives and every query over it is wrong.
    """
    reg.add(_artefacts()["codelist"])
    assert _counts(reg)["code"] == 2

    reg.add(Codelist(id="CL", maintainer=ESTAT, name={"en": "CL"}, codes=[Code(id="A", name={"en": "Annual"})]))
    assert _counts(reg)["code"] == 1, "the dropped code survived the replacement"


def test_the_cascade_precondition_holds_for_every_writer() -> None:
    """Cascade before spine, in every writer — what makes the skip sound.

    Derived from the source rather than asserted about behaviour, because the
    failure it guards is invisible at runtime: transpose the two calls in one
    writer and that artefact's child rows outlive their spine row, so a later
    write skips the cascade and the stale rows stay forever.
    """
    for artefact_type, writer in writers._WRITER_DISPATCH.items():
        source = inspect.getsource(writer)
        cascade = source.find("_delete_artefact_cascade")
        spine = source.find("_put_spine")
        assert cascade != -1, f"{artefact_type.__name__} does not cascade before writing"
        assert spine != -1, f"{artefact_type.__name__} writes no spine row, so the skip cannot see it"
        assert cascade < spine, f"{artefact_type.__name__} writes its spine row before clearing old rows"


def test_the_cascade_is_skipped_for_an_artefact_that_is_not_stored(reg: sl.LocalRegistry) -> None:
    """The #317 saving, asserted as a statement count rather than a duration.

    Wall-clock would make this a flaky benchmark; the count is the thing that
    actually changed, and `caching.md` asks for exactly this shape of guard.
    """
    conn = reg._c
    real = type(conn).execute
    seen: list[str] = []

    def counting(
        self: duckdb.DuckDBPyConnection, query: duckdb.Statement | str, parameters: object = None
    ) -> duckdb.DuckDBPyConnection:
        text = " ".join(str(query).split())
        if text.upper().startswith("DELETE"):
            seen.append(text)
        return real(self, query, parameters)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(type(conn), "execute", counting)
        reg.add(_artefacts()["dataflow"])
        first = list(seen)
        seen.clear()
        reg.add(_artefacts()["dataflow"])
        again = list(seen)

    # One survives, and it is not the cascade's: `_put_spine` deletes its own
    # row before inserting rather than using INSERT OR REPLACE, which sidesteps
    # the DuckDB ART-index desync of #70. Named here so a future reader does
    # not "fix" the count by deleting the guard.
    assert first == ["DELETE FROM artefact WHERE urn = ?"], f"a new artefact issued {len(first)} deletes"
    # Was `len(again) > 20`, a proxy for "the cascade ran" that counted the
    # *undispatched* statement list. #144 dispatches on the artefact type, so a
    # Dataflow now issues six — and the count stopped meaning anything. What
    # the test was always about is which tables get cleared, so it says that.
    tables = {statement.split()[2] for statement in again}
    assert "dataflow" in tables, f"a re-add must clear the Dataflow's own extension row, saw {sorted(tables)}"
    assert {"localized_text", "annotation", "artefact_ref"} <= tables, (
        f"and the universal ones every artefact has, saw {sorted(tables)}"
    )
    assert "code" not in tables, "but not tables a Dataflow can never have rows in (#144)"


# ── Every kind, every field (#428) ─────────────────────────────────────────
#
# The re-add test above covers six hand-picked kinds. Metadataflow and
# MetadataProvisionAgreement were not among them, and both writers insert
# declared targets that neither cascade cleared — so a second add of either
# raised a primary-key violation on `metadata_declared_target`. It stayed
# hidden because no producer set targets until 0.62's validate-on-serialise
# required one. `maximal.message()` populates every field of every kind it
# holds, so a re-add over it reaches every child table a writer can fill.

#: Writer kinds `maximal.message()` does not hold, each with why — exact, so a
#: new writer kind fails here until it is either covered or listed.
_NOT_IN_MAXIMAL: dict[str, str] = {
    "MetadataSet": "a MetadataMessage artefact, not StructureMessage content",
    "DataflowBinding": "sdmxlib's own binding, not SDMX message content",
    "CategorySchemeMap": "absent from the maximal fixture — a gap in it, not a choice",
}


def _maximal_artefacts() -> list[SupportsUrn]:
    return list(maximal.message().all_artefacts())


def test_the_maximal_fixture_reaches_every_writer_kind_but_the_listed_ones() -> None:
    covered = {type(a).__name__ for a in _maximal_artefacts()}
    written = {t.__name__ for t in writers._WRITER_DISPATCH}
    assert written - covered == set(_NOT_IN_MAXIMAL)


@pytest.mark.parametrize("index", range(len(_maximal_artefacts())))
def test_re_adding_any_maximal_artefact_leaves_the_row_counts_unchanged(reg: sl.LocalRegistry, index: int) -> None:
    """The whole message stored, then one artefact re-added: nothing collides or accumulates."""
    artefacts = _maximal_artefacts()
    reg.add_all(artefacts)
    before = _counts(reg)
    reg.add(artefacts[index])
    assert _counts(reg) == before, f"{type(artefacts[index]).__name__} accumulated rows on re-add"


@pytest.mark.parametrize("kind", [Metadataflow, MetadataProvisionAgreement])
def test_re_adding_with_other_targets_replaces_them(
    reg: sl.LocalRegistry, kind: type[Metadataflow | MetadataProvisionAgreement]
) -> None:
    """The cascade's job for declared targets: the new list, not the old one beside it."""
    artefacts = _maximal_artefacts()
    reg.add_all(artefacts)
    original = next(a for a in artefacts if isinstance(a, kind))
    assert len(original.targets) >= 1, "the fixture must carry targets, or this proves nothing"
    replacement = attrs.evolve(original, targets=("urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=AG:OTHER(1.0)",))
    reg.add(replacement)
    stored = reg._c.execute(
        "SELECT target_urn FROM metadata_declared_target WHERE source_urn = ? ORDER BY ordinal",
        [str(original.urn)],
    ).fetchall()
    assert stored == [("urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=AG:OTHER(1.0)",)]
