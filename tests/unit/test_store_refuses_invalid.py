"""A store read refuses what `add` would, and reads on (#492).

A store read used to trust its rows. The census bundle was written straight
into DuckDB by its builder, bypassing `add`, and 498,786 blank code names sat
in it unchecked until a write found them. Each artefact the store builds is now
checked as `add` checks it: `get` raises for one it refuses, and every other
read leaves it out, records it in `LocalRegistry.refused`, and reads on.

The invalid artefacts here are written with `storage.writers`, which do not
validate, exactly as the builder wrote the bundle. Each refusal sits beside a
valid twin read from the same store, so "left out" is checked against "read".
"""

from collections.abc import Iterator

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.model.validation import SdmxConformanceError
from sdmxlib.storage.schema import create_schema
from sdmxlib.storage.writers import put_artefact
from tests.support.factories import concept_ref
from tests.support.narrow import present
from tests.support.unchecked import unchecked

pytestmark = pytest.mark.unit

_GOOD = sl.DataStructure.urn_for(agency="T", id="DSD_OK", version="1.0")
_BAD = sl.DataStructure.urn_for(agency="T", id="DSD_BAD", version="1.0")
_CL = sl.Codelist.urn_for(agency="T", id="CL_BLANK", version="1.0")
_NO_CONCEPT = r"DataStructure\.dimensions\[FREQ\]\.concept — ConceptIdentity is required"


def _dsd(id_: str, *, concept: bool) -> sl.DataStructure:
    """A DSD; without a concept on `FREQ`, one `add` refuses."""
    return sl.DataStructure(
        id=id_,
        maintainer="T",
        name={"en": id_},
        dimensions=[sl.Dimension(id="FREQ", concept=concept_ref("FREQ") if concept else None)],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )


def _invalid_dsd(id_: str) -> sl.DataStructure:
    """Built unchecked: the model refuses a component without a concept as it is built (#492)."""
    with unchecked():
        return _dsd(id_, concept=False)


def _flow(id_: str, structure: sl.SdmxUrn[sl.DataStructure]) -> sl.Dataflow:
    return sl.Dataflow(
        id=id_, maintainer="T", name={"en": id_}, structure=sl.Ref[sl.DataStructure].unresolved(structure)
    )


def _blank_codelist() -> sl.Codelist:
    """A codelist one of whose codes is named ``""``, as the bundle's blocks were."""
    with unchecked():
        blank = sl.Code(id="B", name={"en": ""})
    return sl.Codelist(
        id="CL_BLANK", maintainer="T", name={"en": "c"}, codes=[sl.Code(id="A", name={"en": "a"}), blank]
    )


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    conn = create_schema(duckdb.connect(":memory:"))
    for artefact in (
        _dsd("DSD_OK", concept=True),
        _invalid_dsd("DSD_BAD"),
        _flow("DF_OK", _GOOD),
        _flow("DF_BAD", _BAD),
        _blank_codelist(),
    ):
        put_artefact(conn, artefact)
    with sl.LocalRegistry.from_connection(conn) as registry:
        yield registry
    conn.close()


# ── a single read raises ──────────────────────────────────────────────────────


def test_getting_a_refused_artefact_raises_with_its_reason(reg: sl.LocalRegistry):
    with pytest.raises(SdmxConformanceError, match=rf"DSD_BAD\(1\.0\) is stored and refused: {_NO_CONCEPT}") as info:
        reg.get(_BAD)
    assert [r.urn for r in info.value.refused] == [str(_BAD)]
    assert [r.urn for r in reg.refused] == [str(_BAD)]


def test_its_valid_twin_reads(reg: sl.LocalRegistry):
    dsd = reg.get(_GOOD)
    assert dsd is not None
    assert dsd.id == "DSD_OK"
    assert reg.refused == ()


def test_a_value_the_model_refuses_is_refused_as_the_store_builds_it(reg: sl.LocalRegistry):
    """The blank name meets `named` while the code is built, before any whole-artefact rule."""
    refusal = r"CL_BLANK\(1\.0\) is stored and refused: Code\.name\[en\] — name is blank"
    with pytest.raises(SdmxConformanceError, match=refusal):
        reg.get(_CL, eager=True)


def test_a_lazy_codelist_is_refused_as_it_materialises(reg: sl.LocalRegistry):
    """Opening the facade builds no code, so nothing is refused until one is built."""
    lazy = present(reg.get(_CL))
    assert lazy.code_ids() == ["A", "B"]
    with pytest.raises(SdmxConformanceError, match=r"CL_BLANK\(1\.0\) is stored and refused: Code\.name\[en\]"):
        lazy.materialize()


# ── a bulk read leaves it out and reads on ────────────────────────────────────


def test_get_all_leaves_a_refused_artefact_out_and_records_it(reg: sl.LocalRegistry):
    assert [d.id for d in reg.get_all(sl.DataStructure)] == ["DSD_OK"]
    assert [r.urn for r in reg.refused] == [str(_BAD)]
    assert "ConceptIdentity is required" in reg.refused[0].reason


def test_a_query_leaves_a_refused_artefact_out_and_records_it(reg: sl.LocalRegistry):
    assert sorted(d.id for d in reg.query(sl.q.dsd).collect()) == ["DSD_OK"]
    assert [r.urn for r in reg.refused] == [str(_BAD)]


def test_a_reference_to_a_refused_artefact_stays_unresolved(reg: sl.LocalRegistry):
    good_flow = reg.get(sl.Dataflow.urn_for(agency="T", id="DF_OK", version="1.0"))
    bad_flow = reg.get(sl.Dataflow.urn_for(agency="T", id="DF_BAD", version="1.0"))
    assert good_flow is not None
    assert bad_flow is not None
    good, bad = present(good_flow.structure), present(bad_flow.structure)

    assert good.resolved
    assert not bad.resolved
    assert bad.urn == _BAD
    assert [r.urn for r in reg.refused] == [str(_BAD)]


def test_to_registry_leaves_a_refused_artefact_out(reg: sl.LocalRegistry):
    copied = reg.to_registry()
    assert _GOOD in copied
    assert _BAD not in copied
    assert {r.urn for r in reg.refused} == {str(_BAD), str(_CL)}


# ── what clears it ────────────────────────────────────────────────────────────


def test_a_change_to_the_store_forgets_what_was_refused(reg: sl.LocalRegistry):
    """What changed may be what was refused, so the next read decides again."""
    reg.get_all(sl.DataStructure)
    assert reg.refused
    reg.drop(_BAD)
    assert reg.refused == ()
    assert [d.id for d in reg.get_all(sl.DataStructure)] == ["DSD_OK"]
    assert reg.refused == ()
