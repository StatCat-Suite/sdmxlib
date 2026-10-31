"""An SDMX-CSV file names its own structure, so `scan_csv` can use it (#409).

Every SDMX-CSV 2.0 row carries `STRUCTURE` and `STRUCTURE_ID`. The reader has
always parsed them — it pulled the id off the first row and
built an unresolved `Ref[Dataflow]` — and then required the `DataStructure` as a
positional argument anyway, so the file's own answer was available only *after*
you had supplied it. A consumer holding a structure message and a directory of
CSVs had to restate the join by hand:

    slpl.scan_csv(out / f"{flow_id.lower()}.csv", message.dataflows[flow_id].structure())

#409 fixed that by resolving whatever the file names against a registry, and
#516 made that the only way: `reg.scan_csv(path)` looks the file's structure up
in the registry it is called on, and returns an `ObsQuery` over its rows.

All three `STRUCTURE` kinds are honoured. The spec's field guide says exactly
``dataflow``, ``datastructure``, ``dataprovision`` — quoted, because two of our
own docstrings said ``provisionagreement``, which is SDMX-JSON's RFC 5988 link
relation and not this column's vocabulary at all.
"""

from pathlib import Path
from typing import TypedDict

import polars as pl
import pytest

import sdmxlib as sl
from sdmxlib.formats.sdmx_csv.reader import STRUCTURE_KINDS

pytestmark = pytest.mark.unit

A, V = "AGENCY", "1.0.0"


class _Structures(TypedDict):
    concepts: sl.ConceptScheme
    codelist: sl.Codelist
    dsd: sl.DataStructure
    flow: sl.Dataflow
    agreement: sl.ProvisionAgreement


@pytest.fixture
def structures() -> _Structures:
    concepts = sl.ConceptScheme(
        id="CS",
        maintainer=A,
        version=V,
        name={"en": "C"},
        concepts=[sl.Concept(id=i, name={"en": i}) for i in ("AREA", "TIME_PERIOD", "OBS_VALUE")],
    )
    codelist = sl.Codelist(
        id="CL_AREA", maintainer=A, version=V, name={"en": "Area"}, codes=[sl.Code(id="CA", name={"en": "Canada"})]
    )
    dsd = sl.DataStructure(
        id="DSD",
        maintainer=A,
        version=V,
        name={"en": "DSD"},
        dimensions=[sl.Dimension(id="AREA", concept=concepts.ref_to("AREA"), representation=sl.Ref.to(codelist))],
        time_dimension=sl.TimeDimension(concept=concepts.ref_to("TIME_PERIOD")),
        measures=[sl.Measure(id="OBS_VALUE", concept=concepts.ref_to("OBS_VALUE"))],
    )
    flow = sl.Dataflow(id="DF", maintainer=A, version=V, name={"en": "f"}, structure=sl.Ref.to(dsd))
    agreement = sl.ProvisionAgreement(id="PA", maintainer=A, version=V, name={"en": "pa"}, dataflow=sl.Ref.to(flow))
    return {"concepts": concepts, "codelist": codelist, "dsd": dsd, "flow": flow, "agreement": agreement}


@pytest.fixture
def registry(structures: _Structures):
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all(
            [
                structures["concepts"],
                structures["codelist"],
                structures["dsd"],
                structures["flow"],
                structures["agreement"],
            ]
        )
        yield reg


def _csv(tmp_path: Path, kind: str, identifier: str, name: str = "data") -> Path:
    path = tmp_path / f"{name}.csv"
    _ = path.write_text(
        f"STRUCTURE,STRUCTURE_ID,ACTION,AREA,TIME_PERIOD,OBS_VALUE\n{kind},{identifier},I,CA,2020-01,1.0\n",
        encoding="utf-8",
    )
    return path


# ── resolving from the file ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kind", "identifier"), [("dataflow", "AGENCY:DF(1.0.0)"), ("dataprovision", "AGENCY:PA(1.0.0)")]
)
def test_the_registry_resolves_a_dataflow_or_its_agreement(
    registry: sl.LocalRegistry, tmp_path: Path, kind: str, identifier: str
) -> None:
    """A provision agreement resolves through its dataflow, the same second hop
    a dataflow makes to its structure."""
    obs = registry.scan_csv(_csv(tmp_path, kind, identifier))
    assert obs.structure is not None
    assert obs.structure.id == "DSD"
    assert obs.frame().height == 1


def test_a_datastructure_file_is_refused(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    """The wart the kind column made visible, now a refusal.

    A `datastructure` file used to come back claiming a dataflow whose id was
    really a DSD's; then claiming none (#409). An observation query is over a
    dataflow, and a structure alone does not say which (#516).
    """
    with pytest.raises(ValueError, match="names the data structure DSD, not a dataflow"):
        registry.scan_csv(_csv(tmp_path, "datastructure", "AGENCY:DSD(1.0.0)"))


# ── the distinguishable failures #409 asked for ──────────────────────────


def test_a_file_that_names_nothing(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    bare = tmp_path / "bare.csv"
    _ = bare.write_text("AREA,TIME_PERIOD,OBS_VALUE\nCA,2020-01,1.0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="names no structure"):
        registry.scan_csv(bare)


def test_an_id_the_registry_lacks(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not in this registry"):
        registry.scan_csv(_csv(tmp_path, "dataflow", "AGENCY:GONE(1.0.0)"))


def test_an_unknown_structure_kind(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown STRUCTURE value"):
        registry.scan_csv(_csv(tmp_path, "nonsense", "AGENCY:DF(1.0.0)"))


def test_a_dataflow_with_no_structure(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    registry.add(sl.Dataflow(id="X", maintainer=A, version=V, name={"en": "x"}))
    with pytest.raises(ValueError, match="has no structure"):
        registry.scan_csv(_csv(tmp_path, "dataflow", "AGENCY:X(1.0.0)"))


def test_an_agreement_with_no_dataflow(registry: sl.LocalRegistry, tmp_path: Path) -> None:
    registry.add(sl.ProvisionAgreement(id="PA2", maintainer=A, version=V, name={"en": "pa"}))
    with pytest.raises(ValueError, match="has no dataflow"):
        registry.scan_csv(_csv(tmp_path, "dataprovision", "AGENCY:PA2(1.0.0)"))


# ── short_urn, and the writer that needed it ─────────────────────────────


def test_short_urn_is_the_form_the_column_carries(structures: _Structures) -> None:
    """The read side has always owned this format (`parse_dataflow_id`); the
    write side made every caller produce it by hand. The two must agree, which
    is what this asserts rather than restating the f-string."""
    from sdmxlib.formats.sdmx_csv.reader import parse_dataflow_id

    flow = structures["flow"]
    text = sl.short_urn(flow)
    assert text == "AGENCY:DF(1.0.0)"
    assert parse_dataflow_id(text) == (A, "DF", V)
    assert sl.short_urn(flow.urn) == text


def test_the_writer_takes_the_artefact(structures: _Structures) -> None:
    """Reading the kind off the artefact as well as the id, so the two cannot
    disagree — a `DataStructure` passed with ``structure="dataflow"`` was the
    available mistake."""
    import pyarrow as pa

    from sdmxlib.formats.sdmx_csv.writer import write_sdmx_csv

    dsd = structures["dsd"]
    batch = pa.RecordBatch.from_pydict({"AREA": ["CA"], "TIME_PERIOD": ["2020-01"], "OBS_VALUE": ["1.0"]})

    def first_row(structure_id: str | sl.Dataflow | sl.DataStructure | sl.ProvisionAgreement) -> str:
        written = b"".join(write_sdmx_csv([batch], dsd, structure_id=structure_id))
        return written.decode().splitlines()[1]

    assert first_row(structure_id=structures["flow"]).startswith("dataflow,AGENCY:DF(1.0.0),")
    assert first_row(structure_id=dsd).startswith("datastructure,AGENCY:DSD(1.0.0),")
    assert first_row(structure_id=structures["agreement"]).startswith("dataprovision,AGENCY:PA(1.0.0),")
    assert first_row(structure_id="AGENCY:DF(1.0.0)").startswith("dataflow,AGENCY:DF(1.0.0),"), "string form unchanged"


def test_the_writer_refuses_the_wrong_kind_vocabulary(structures: _Structures) -> None:
    """`provisionagreement` is what this library's own docstring told callers to
    pass. It is SDMX-JSON's link relation, not SDMX-CSV's `STRUCTURE` value, so
    a file written with it is one no conformant reader accepts — and nothing
    refused it."""
    import pyarrow as pa

    from sdmxlib.formats.sdmx_csv.writer import write_sdmx_csv

    batch = pa.RecordBatch.from_pydict({"AREA": ["CA"], "TIME_PERIOD": ["2020-01"], "OBS_VALUE": ["1.0"]})
    with pytest.raises(ValueError, match="STRUCTURE must be one of"):
        list(
            write_sdmx_csv(
                [batch],
                structures["dsd"],
                structure_id="AGENCY:PA(1.0.0)",
                structure="provisionagreement",
            )
        )


def test_the_kinds_are_the_specs_three() -> None:
    """Quoted from the SDMX-CSV field guide, and the reason the writer's old
    docstring was wrong. A fourth value appearing here should be a deliberate
    edit against the spec, not a guess."""
    assert STRUCTURE_KINDS == ("dataflow", "datastructure", "dataprovision")


# ── the round trip the two halves now share ──────────────────────────────


def test_written_then_read_back_through_the_registry(
    structures: _Structures, registry: sl.LocalRegistry, tmp_path: Path
) -> None:
    """End to end: write with the artefact, read with the registry, and
    nobody formats or parses the short URN by hand."""
    import pyarrow as pa

    from sdmxlib.formats.sdmx_csv.writer import write_sdmx_csv

    batch = pa.RecordBatch.from_pydict({"AREA": ["CA"], "TIME_PERIOD": ["2020-01"], "OBS_VALUE": ["1.0"]})
    path = tmp_path / "round.csv"
    _ = path.write_bytes(b"".join(write_sdmx_csv([batch], structures["dsd"], structure_id=structures["flow"])))

    obs = registry.scan_csv(path)
    assert obs.structure is not None
    assert obs.structure.id == "DSD"
    # `OBS_VALUE` declares no facet, so it is read as its values say — a number
    # (#518). The round trip is about identity, not dtypes; asserting the real
    # one keeps it honest.
    assert obs.frame().with_columns(pl.col("AREA").cast(pl.String)).to_dicts() == [
        {"AREA": "CA", "TIME_PERIOD": "2020-01", "OBS_VALUE": 1.0}
    ]
