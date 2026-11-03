"""`fill_edition`, `fill_structures` and `check_edition` over several structure editions (GH-347).

A caller opens the builder and seals it: statcatlib's `WorkingCopy` does both
around its ingest. The source is `test_edition_build`'s: a dataflow `F` and a
sibling `G` over one data structure whose `GEO` dimension a hierarchical
codelist enumerates.
"""

from collections.abc import Callable
from pathlib import Path

import attrs
import duckdb
import httpx2
import polars as pl
import pytest

import sdmxlib as sl
from sdmxlib.edition import (
    ArtefactEntry,
    Producer,
    Report,
    build_edition,
    check_edition,
    fill_edition,
    fill_structures,
    read_manifest,
    seal,
)
from sdmxlib.formats.sdmx_ml21.writer import write as write21
from sdmxlib.local.registry import EditionRegistry
from sdmxlib.model.codelist import GeographicCodelist
from sdmxlib.storage._references import held_targets
from sdmxlib.storage.writers import drop_artefact
from tests.unit.test_edition_build import CONCEPTS, DSD, FLOW, FLOW_URN, GEO, GEO_URN, OTHER, SIBLING, _source

pytestmark = pytest.mark.unit

_PRODUCER = Producer(name="test-builder", version="1.0")
SIBLING_URN = str(SIBLING.urn)


def _frame(geo: list[str]) -> pl.DataFrame:
    return pl.DataFrame({"GEO": geo, "OBS_VALUE": [1.0] * len(geo)})


def _codes(edition: Path) -> list[str]:
    with sl.LocalRegistry.edition(edition) as reg:
        codelist = reg.get(GEO.urn, eager=True)
        assert isinstance(codelist, sl.Codelist)
        return sorted(code.id for code in codelist.codes)


def _entry(edition: Path, urn: str) -> ArtefactEntry:
    (entry,) = (a for a in read_manifest(edition).artefacts if a.urn == urn)
    return entry


def _partials(report: Report) -> list[tuple[str, str]]:
    return [(f.subject, f.detail) for f in report.findings if f.check == "partials"]


def _clean(report: Report) -> None:
    assert report.ok, "\n".join(map(str, report.findings))


# ── fill_edition ──────────────────────────────────────────────────────────


def test_fill_edition_fills_a_builder_the_caller_opened_and_seals(tmp_path: Path) -> None:
    source = _source(tmp_path / "s")
    product = tmp_path / "p"
    product.mkdir()
    with sl.LocalRegistry.edition(source) as reg, sl.LocalRegistry.build(product) as builder:
        cut = fill_edition(builder, reg, [FLOW.urn], observations={FLOW_URN: _frame(["A1", "B3"])})
        # The builder is still open: the caller writes more before its seal.
        builder.add(OTHER)
        edition = builder.edition_dir
    assert not (edition / "manifest.json").exists(), "fill_edition leaves the seal to its caller"

    seal(edition, product_id="p", producer=_PRODUCER, partial_of=cut)

    assert cut == {GEO_URN: _entry(source, GEO_URN).fingerprint}
    assert _codes(edition) == ["A", "A1", "B", "B3"]
    assert str(OTHER.urn) in {a.urn for a in read_manifest(edition).artefacts}
    _clean(check_edition(edition, at="publish", structures=source))


def test_fill_edition_without_partials_returns_nothing_to_record(tmp_path: Path) -> None:
    source = _source(tmp_path / "s")
    (tmp_path / "p").mkdir()
    with sl.LocalRegistry.edition(source) as reg, sl.LocalRegistry.build(tmp_path / "p") as builder:
        cut = fill_edition(builder, reg, [FLOW.urn], observations={FLOW_URN: _frame(["A1"])}, partials=False)
        edition = builder.edition_dir
    seal(edition, product_id="p", producer=_PRODUCER, partial_of=cut)

    assert cut == {}
    assert len(_codes(edition)) == len(GEO.codes)


def _two_flows(source: Path, product: Path) -> Path:
    """A first edition of `F` and `G`, whose shared codelist is cut to A1 and B2."""
    with sl.LocalRegistry.edition(source) as reg:
        manifest = build_edition(
            reg,
            [FLOW.urn, SIBLING.urn],
            product,
            product_id="p",
            producer=_PRODUCER,
            observations={FLOW_URN: _frame(["A1"]), SIBLING_URN: _frame(["B2"])},
        )
    return product / manifest.edition_id


def test_a_republish_of_one_dataflow_keeps_the_other_and_cuts_for_both(tmp_path: Path) -> None:
    source = _source(tmp_path / "s")
    first = _two_flows(source, tmp_path / "p")
    assert _codes(first) == ["A", "A1", "B", "B2"]

    with sl.LocalRegistry.edition(source) as reg, sl.LocalRegistry.build(tmp_path / "p", previous=first.name) as b:
        cut = fill_edition(b, reg, [FLOW.urn], observations={FLOW_URN: _frame(["A2"])})
        edition = b.edition_dir
    seal(edition, product_id="p", producer=_PRODUCER, partial_of=cut)

    # F's A1 is gone with its rows; G's B2 stays, since G's rows hold it.
    assert _codes(edition) == ["A", "A2", "B", "B2"]
    assert {b.dataflow for b in read_manifest(edition).bindings} == {FLOW_URN, SIBLING_URN}
    assert _entry(edition, GEO_URN).partial_of == _entry(source, GEO_URN).fingerprint
    _clean(check_edition(edition, at="publish", structures=source))


def test_a_republish_that_cuts_nothing_keeps_each_partials_partial_of(tmp_path: Path) -> None:
    """`seal` takes `partial_of` from the previous manifest for a partial it is not told about."""
    source = _source(tmp_path / "s")
    first = _two_flows(source, tmp_path / "p")
    with sl.LocalRegistry.build(tmp_path / "p", previous=first.name) as builder:
        builder.add(OTHER)
        edition = builder.edition_dir

    seal(edition, product_id="p", producer=_PRODUCER)

    assert _entry(edition, GEO_URN).partial_of == _entry(first, GEO_URN).partial_of is not None
    _clean(check_edition(edition, at="publish", structures=source))


def test_a_partial_of_given_to_seal_wins_over_the_previous_one(tmp_path: Path) -> None:
    source = _source(tmp_path / "s")
    first = _two_flows(source, tmp_path / "p")
    with sl.LocalRegistry.build(tmp_path / "p", previous=first.name) as builder:
        edition = builder.edition_dir

    seal(edition, product_id="p", producer=_PRODUCER, partial_of={GEO_URN: "0" * 64})

    assert _entry(edition, GEO_URN).partial_of == "0" * 64


def _provider() -> httpx2.MockTransport:
    """A provider serving `F`'s structures, `G` and CL_OTHER for any request, as a bulk query would."""
    message = sl.StructureMessage(
        codelists=[GEO, OTHER], concept_schemes=[CONCEPTS], data_structures=[DSD], dataflows=[FLOW, SIBLING]
    )
    body = write21(message)
    return httpx2.MockTransport(lambda _request: httpx2.Response(200, content=body))


def test_a_republish_from_a_rest_source_keeps_what_the_builder_held(tmp_path: Path) -> None:
    """Fetched artefacts outside the closure come out only when this fill added them."""
    product = tmp_path / "p"
    with sl.RestRegistry("https://example.test/service", id="T", transport=_provider()) as rest:
        first = build_edition(
            rest,
            [FLOW.urn, SIBLING.urn],
            product,
            product_id="p",
            producer=_PRODUCER,
            observations={FLOW_URN: _frame(["A1"]), SIBLING_URN: _frame(["B2"])},
        )
    first_dir = product / first.edition_id
    held = {a.urn for a in first.artefacts}
    assert str(OTHER.urn) not in held
    assert SIBLING_URN in held

    with (
        sl.RestRegistry("https://example.test/service", id="T", transport=_provider()) as rest,
        sl.LocalRegistry.build(product, previous=first.edition_id) as builder,
    ):
        cut = fill_edition(builder, rest, [FLOW.urn], observations={FLOW_URN: _frame(["A2"])})
        edition = builder.edition_dir
    seal(edition, product_id="p", producer=_PRODUCER, partial_of=cut)

    manifest = read_manifest(edition)
    assert {a.urn for a in manifest.artefacts} == held, "G stays; CL_OTHER, fetched again, stays out"
    assert {b.dataflow for b in manifest.bindings} == {FLOW_URN, SIBLING_URN}
    assert _codes(edition) == ["A", "A2", "B", "B2"]
    assert _entry(edition, GEO_URN).partial_of == _entry(first_dir, GEO_URN).partial_of
    _clean(check_edition(edition, at="publish"))


# ── fill_structures ───────────────────────────────────────────────────────


def _codelist(cl_id: str, agency: str = "T") -> sl.Codelist:
    return sl.Codelist(
        id=cl_id, maintainer=agency, version="1.0", name={"en": cl_id}, codes=[sl.Code(id="X", name={"en": "X"})]
    )


SGC_2021 = _codelist("SGC_2021")
SGC_2016 = _codelist("SGC_2016")
NAICS = _codelist("NAICS")
ELSEWHERE = _codelist("SGC_2021", agency="U")


def _fmr(root: Path) -> Path:
    """A sealed edition standing for an FMR's structures: two agencies, codelists and a concept scheme."""
    return _source(root, SGC_2021, SGC_2016, NAICS, ELSEWHERE)


Fill = Callable[[sl.LocalRegistry, EditionRegistry], tuple[str, ...]]


def _structure_product(source: Path, product: Path, fill: Fill) -> tuple[tuple[str, ...], Path]:
    product.mkdir(parents=True)
    with sl.LocalRegistry.edition(source) as reg, sl.LocalRegistry.build(product) as builder:
        copied = fill(builder, reg)
        edition = builder.edition_dir
    seal(edition, product_id=product.name, producer=_PRODUCER)
    return copied, edition


def test_families_agency_and_an_id_pattern_select_the_geography(tmp_path: Path) -> None:
    copied, edition = _structure_product(
        _fmr(tmp_path / "s"),
        tmp_path / "structure-geo",
        lambda b, reg: fill_structures(b, reg, families=[sl.Codelist], agency="T", ids=["SGC*"]),
    )

    assert copied == tuple(sorted([str(SGC_2016.urn), str(SGC_2021.urn)]))
    manifest = read_manifest(edition)
    assert sorted(a.urn for a in manifest.artefacts) == list(copied)
    assert manifest.bindings == ()
    _clean(check_edition(edition, at="publish"))


def test_a_family_alone_selects_every_artefact_of_it(tmp_path: Path) -> None:
    copied, _ = _structure_product(
        _fmr(tmp_path / "s"),
        tmp_path / "structure-concepts",
        lambda b, reg: fill_structures(b, reg, families=[sl.ConceptScheme]),
    )

    assert copied == (str(CONCEPTS.urn),)


def test_explicit_urns_add_to_a_family_selection(tmp_path: Path) -> None:
    copied, _ = _structure_product(
        _fmr(tmp_path / "s"),
        tmp_path / "structure-classifications",
        lambda b, reg: fill_structures(
            b, reg, families=[sl.Codelist], agency="T", ids=["NAICS"], artefacts=[str(OTHER.urn), ELSEWHERE.urn]
        ),
    )

    assert copied == tuple(sorted(str(c.urn) for c in (NAICS, OTHER, ELSEWHERE)))


def test_a_family_skips_partials_and_a_named_partial_is_refused(tmp_path: Path) -> None:
    source = _source(tmp_path / "s")
    data = _two_flows(source, tmp_path / "p")
    assert _entry(data, GEO_URN).partial
    (tmp_path / "q").mkdir()
    with sl.LocalRegistry.edition(data) as reg, sl.LocalRegistry.build(tmp_path / "q") as builder:
        assert fill_structures(builder, reg, families=[sl.ConceptScheme]) == (str(CONCEPTS.urn),)
        with pytest.raises(ValueError, match=r"nothing at .* matches families=\['Codelist'\]"):
            fill_structures(builder, reg, families=[sl.Codelist])
        with pytest.raises(ValueError, match="is a partial artefact"):
            fill_structures(builder, reg, artefacts=[GEO_URN])


_REFUSALS: list[tuple[Fill, type[Exception], str]] = [
    (lambda b, reg: fill_structures(b, reg, families=[sl.Dataflow]), TypeError, "holds no Dataflow"),
    (
        lambda b, reg: fill_structures(b, reg, families=[GeographicCodelist]),
        TypeError,
        "GeographicCodelist is not an artefact class the store holds; it is stored as a Codelist",
    ),
    (lambda b, reg: fill_structures(b, reg, artefacts=[FLOW_URN]), ValueError, "is a Dataflow, and a structure"),
    (
        lambda b, reg: fill_structures(b, reg, artefacts=["urn:sdmx:org.sdmx.infomodel.codelist.Codelist=T:NO(1.0)"]),
        ValueError,
        "is not held",
    ),
    (
        lambda b, reg: fill_structures(b, reg, families=[sl.Codelist], agency="NOBODY"),
        ValueError,
        "nothing at .* matches",
    ),
]


@pytest.mark.parametrize(("fill", "error", "match"), _REFUSALS)
def test_a_selection_a_structure_product_cannot_hold_is_refused(
    tmp_path: Path, fill: Fill, error: type[Exception], match: str
) -> None:
    source = _fmr(tmp_path / "s")
    (tmp_path / "p").mkdir()
    with sl.LocalRegistry.edition(source) as reg, sl.LocalRegistry.build(tmp_path / "p") as builder:
        with pytest.raises(error, match=match):
            fill(builder, reg)
        held = builder.connection.execute("SELECT count(*) FROM sdmx.artefact").fetchone()
    assert held == (0,), "a refusal writes nothing"


# ── check_edition over several structure editions ─────────────────────────


def _split(source: Path, root: Path) -> tuple[Path, Path]:
    """`structure-concepts` (the concept scheme and CL_OTHER) and `structure-geo` (CL), from `source`."""
    _, concepts = _structure_product(
        source,
        root / "structure-concepts",
        lambda b, reg: fill_structures(b, reg, families=[sl.ConceptScheme], artefacts=[str(OTHER.urn)]),
    )
    _, geo = _structure_product(
        source, root / "structure-geo", lambda b, reg: fill_structures(b, reg, artefacts=[GEO_URN])
    )
    return concepts, geo


def _data(source: Path, product: Path) -> Path:
    with sl.LocalRegistry.edition(source) as reg:
        manifest = build_edition(
            reg, [FLOW.urn], product, product_id="data", producer=_PRODUCER, observations={FLOW_URN: _frame(["A1"])}
        )
    return product / manifest.edition_id


def test_a_partial_checks_clean_against_the_second_structure_product(tmp_path: Path) -> None:
    source = _source(tmp_path / "s")
    concepts, geo = _split(source, tmp_path)
    data = _data(source, tmp_path / "data")

    _clean(check_edition(data, at="publish", structures=[concepts, geo]))
    # The discrimination check: the same partial, given only the product without its codelist.
    assert _partials(check_edition(data, at="publish", structures=[concepts])) == [
        (GEO_URN, f"{concepts} holds no full artefact at this URN")
    ]


def test_a_partial_with_a_code_its_full_artefact_lacks_is_a_finding(tmp_path: Path) -> None:
    source = _source(tmp_path / "s")
    concepts, geo = _split(source, tmp_path)
    extra = sl.Codelist(
        id="CL",
        maintainer="T",
        version="1.0",
        name={"en": "geo"},
        is_partial=True,
        codes=[sl.Code(id="A", name={"en": "A"}), sl.Code(id="C", name={"en": "C"})],
    )
    (tmp_path / "data").mkdir()
    with sl.LocalRegistry.build(tmp_path / "data") as builder:
        builder.add(extra)
        data = builder.edition_dir
    seal(data, product_id="data", producer=_PRODUCER, partial_of={GEO_URN: _entry(geo, GEO_URN).fingerprint})

    report = check_edition(data, at="publish", structures=[concepts, geo])

    assert _partials(report) == [(GEO_URN, "C is not in the full artefact")]


def test_a_urn_no_structure_product_holds_names_every_one(tmp_path: Path) -> None:
    source = _source(tmp_path / "s")
    concepts, _ = _split(source, tmp_path)
    _, more = _structure_product(
        source, tmp_path / "more", lambda b, reg: fill_structures(b, reg, families=[sl.ConceptScheme])
    )
    data = _data(source, tmp_path / "data")

    report = check_edition(data, at="publish", structures=[concepts, more])

    assert _partials(report) == [(GEO_URN, f"none of {concepts}, {more} holds a full artefact at this URN")]


def test_a_fingerprint_no_structure_product_bears_out_names_each(tmp_path: Path) -> None:
    source = _source(tmp_path / "s")
    data = _data(source, tmp_path / "data")
    cut_from = _entry(data, GEO_URN).partial_of
    homes: list[Path] = []
    for n in (1, 2):
        edited = sl.Codelist(id="CL", maintainer="T", version="1.0", name={"en": f"geo {n}"}, codes=list(GEO.codes))
        (tmp_path / f"h{n}").mkdir()
        with sl.LocalRegistry.build(tmp_path / f"h{n}") as builder:
            builder.add(edited)
            homes.append(builder.edition_dir)
        seal(homes[-1], product_id=f"h{n}", producer=_PRODUCER)
    first, second = (_entry(home, GEO_URN).fingerprint for home in homes)
    assert len({cut_from, first, second}) == 3

    report = check_edition(data, at="publish", structures=homes)

    detail = f"cut from {cut_from}, and the full artefact in {homes[0]} is {first}; in {homes[1]} is {second}"
    assert _partials(report) == [(GEO_URN, detail)]


def test_an_empty_list_of_structure_editions_is_refused(tmp_path: Path) -> None:
    data = _data(_source(tmp_path / "s"), tmp_path / "data")
    with pytest.raises(ValueError, match="structures= names no edition"):
        check_edition(data, at="publish", structures=[])


# ── references into another structure product (GH-351) ───────────────────


NAICS_CONCEPTS = sl.ConceptScheme(
    id="CS_NAICS",
    maintainer="T",
    version="1.0",
    name={"en": "industry concepts"},
    concepts=[sl.Concept(id="INDUSTRY", name={"en": "industry"}, core_representation=sl.Ref.to(NAICS))],
)


def _holding(product: Path, *artefacts: sl.MaintainableArtefact) -> Path:
    """A sealed edition of `product` holding `artefacts` and nothing else."""
    product.mkdir(parents=True)
    with sl.LocalRegistry.build(product) as builder:
        builder.add_all(artefacts)
        edition = builder.edition_dir
    seal(edition, product_id=product.name, producer=_PRODUCER)
    return edition


def _references(report: Report) -> list[tuple[str, str]]:
    return [(f.subject, f.detail) for f in report.findings if f.check == "references"]


def test_a_structure_products_reference_resolves_in_the_structure_edition_holding_it(tmp_path: Path) -> None:
    concepts = _holding(tmp_path / "structure-concepts", NAICS_CONCEPTS)
    classifications = _holding(tmp_path / "structure-classifications", NAICS)
    geo = _holding(tmp_path / "structure-geo", SGC_2021)
    reference = f"its core_representation reference {NAICS.urn} is not in the edition"

    _clean(check_edition(concepts, at="publish", structures=[geo, classifications]))
    _clean(check_edition(concepts, at="receive", structures=classifications))
    assert _references(check_edition(concepts, at="publish")) == [(str(NAICS_CONCEPTS.urn), reference)]
    assert _references(check_edition(concepts, at="publish", structures=[geo])) == [
        (str(NAICS_CONCEPTS.urn), f"{reference}, and no full artefact in {geo} holds it")
    ]


def test_a_partial_in_another_edition_does_not_resolve_a_reference(tmp_path: Path) -> None:
    concepts = _holding(tmp_path / "structure-concepts", NAICS_CONCEPTS)
    cut = _holding(tmp_path / "cut", attrs.evolve(NAICS, is_partial=True))

    report = check_edition(concepts, at="publish", structures=[cut])

    assert [subject for subject, _ in _references(report)] == [str(NAICS_CONCEPTS.urn)]


def test_a_data_products_references_resolve_in_its_own_edition_alone(tmp_path: Path) -> None:
    """A data product vendors its closure: `structures=` checks its partials and resolves none of its references."""
    source = _source(tmp_path / "s")
    concepts, geo = _split(source, tmp_path)
    data = _data(source, tmp_path / "data")
    with duckdb.connect(str(data / "metadata.duckdb")) as conn:
        conn.execute("SET search_path = 'sdmx'")
        assert drop_artefact(conn, str(CONCEPTS.urn))

    report = check_edition(data, at="publish", structures=[concepts, geo])

    assert _references(report), "the concepts the data structure names are held in full by structure-concepts"
    assert all(detail.endswith("is not in the edition") for _, detail in _references(report))


def test_held_targets_answers_items_and_versionless_urns_of_full_artefacts_alone(tmp_path: Path) -> None:
    full = _holding(tmp_path / "full", NAICS)
    cut = _holding(tmp_path / "cut", attrs.evolve(SGC_2021, is_partial=True))
    code = "urn:sdmx:org.sdmx.infomodel.codelist.Code=T:NAICS(1.0).X"
    versionless = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=T:NAICS"
    partial_code = "urn:sdmx:org.sdmx.infomodel.codelist.Code=T:SGC_2021(1.0).X"
    wanted = {str(NAICS.urn), code, versionless, partial_code, str(SGC_2021.urn)}
    with sl.LocalRegistry.edition(full) as reg:
        assert held_targets(reg.connection, wanted) == {str(NAICS.urn), code, versionless}
    with sl.LocalRegistry.edition(cut) as reg:
        assert held_targets(reg.connection, wanted) == set()
