"""Unit tests for sdmxlib.polars bridge functions."""

from pathlib import Path

import polars as pl
import pytest

import sdmxlib.polars as slpl
from sdmxlib.formats import parse
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ItemList
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure, MeasureDimension
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from sdmxlib.model.urn import SdmxUrn
from tests.support.factories import concept_ref
from tests.support.roundtrip import as_structure

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "sdmx_ml21"


def _msg():
    return as_structure(parse((_FIXTURES / "minimal.xml").read_bytes()))


def _dsd() -> DataStructure:
    return _msg().data_structures[0]


# ── helpers ───────────────────────────────────────────────────────────────────


def _codelist(*code_ids: str) -> Codelist:
    codes = ItemList([Code(id=cid, name=InternationalString.of({"en": f"Label {cid}"})) for cid in code_ids])
    return Codelist(name={"en": "CL_TEST"}, id="CL_TEST", maintainer="TEST", codes=codes)


def _resolved_ref(cl: Codelist) -> Ref[Codelist]:
    urn = SdmxUrn.make(
        Codelist, package="codelist", artefact_class="Codelist", agency=cl.agency_id, id=cl.id, version=cl.version
    )
    return Ref[Codelist].of(urn, cl)


def _concept_scheme(*concept_ids: str) -> ConceptScheme:
    """A ConceptScheme for enumerating a MeasureDimension (SDMX 2.1)."""
    return ConceptScheme(
        id="CS_TEST",
        maintainer="TEST",
        version="1.0",
        name=InternationalString.of({"en": "Measures"}),
        concepts=ItemList(
            [Concept(id=cid, name=InternationalString.of({"en": f"Label {cid}"})) for cid in concept_ids]
        ),
    )


def _resolved_cs_ref(cs: ConceptScheme) -> Ref[ConceptScheme]:
    return Ref.to(cs)


def _dim_enumerated(id: str, *code_ids: str) -> Dimension:
    cl = _codelist(*code_ids)
    return Dimension(id=id, representation=_resolved_ref(cl), concept=concept_ref("X"))


def _dim_text(id: str, text_type: str | None = None) -> Dimension:
    return Dimension(id=id, representation=Facet(type=text_type), concept=concept_ref("X"))


# ── schema ────────────────────────────────────────────────────────────────────


def test_schema_enumerated_dimension():
    dsd = _dsd()
    s = slpl.schema(dsd)
    assert "FREQ" in s
    assert isinstance(s["FREQ"], pl.Enum)


def test_schema_enum_codes_sorted():
    dim = _dim_enumerated("X", "C", "A", "B")
    dsd = DataStructure(
        name={"en": "D"},
        id="D",
        maintainer="T",
        dimensions=ItemList([dim]),
        measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
    )
    s = slpl.schema(dsd)
    assert s["X"].categories.to_list() == ["A", "B", "C"]  # type: ignore[union-attr]


def test_schema_text_string():
    s = slpl.schema(
        DataStructure(
            name={"en": "D"},
            id="D",
            maintainer="T",
            dimensions=ItemList([_dim_text("DIM", "String")]),
            measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
        )
    )
    assert s["DIM"] == pl.String()


def test_schema_text_integer():
    s = slpl.schema(
        DataStructure(
            name={"en": "D"},
            id="D",
            maintainer="T",
            dimensions=ItemList([_dim_text("DIM", "Integer")]),
            measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
        )
    )
    assert s["DIM"] == pl.Int64()


def test_schema_text_none_defaults_to_string():
    s = slpl.schema(
        DataStructure(
            name={"en": "D"},
            id="D",
            maintainer="T",
            dimensions=ItemList([_dim_text("DIM")]),
            measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
        )
    )
    assert s["DIM"] == pl.String()


def test_schema_time_dimension():
    s = slpl.schema(_dsd())
    assert "TIME_PERIOD" in s
    assert s["TIME_PERIOD"] == pl.String()


def test_schema_from_fixture_has_obs_status():
    s = slpl.schema(_dsd())
    assert "OBS_STATUS" in s
    assert s["OBS_STATUS"] == pl.String()


def test_schema_measure_defaults_to_string():
    s = slpl.schema(_dsd())
    assert "OBS_VALUE" in s
    assert s["OBS_VALUE"] == pl.String()


# ── validation_report ─────────────────────────────────────────────────────────


def test_validation_report_clean_data():
    dsd = _dsd()
    df = pl.DataFrame({"FREQ": ["A", "Q", "M"], "OBS_VALUE": ["1.0", "2.0", "3.0"]})
    report = slpl.validation_report(dsd, df)
    assert report.is_empty()


def test_validation_report_finds_invalid_value():
    dim = _dim_enumerated("FREQ", "A", "Q", "M")
    dsd = DataStructure(
        name={"en": "D"},
        id="D",
        maintainer="T",
        dimensions=ItemList([dim]),
        measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
    )
    df = pl.DataFrame({"FREQ": ["A", "INVALID", "INVALID", "Q"], "OBS_VALUE": ["1", "2", "3", "4"]})
    report = slpl.validation_report(dsd, df)
    assert not report.is_empty()
    assert "INVALID" in report["invalid_value"].to_list()
    row = report.filter(pl.col("invalid_value") == "INVALID")
    assert row["count"][0] == 2
    assert row["column"][0] == "FREQ"


def test_validation_report_skips_missing_column():
    dsd = _dsd()
    df = pl.DataFrame({"OTHER": ["x", "y"]})
    report = slpl.validation_report(dsd, df)
    assert report.is_empty()


def test_validation_report_accepts_lazyframe():
    dsd = _dsd()
    lf = pl.DataFrame({"FREQ": ["A", "Q"]}).lazy()
    report = slpl.validation_report(dsd, lf)
    assert report.is_empty()


def test_validation_report_schema():
    dsd = _dsd()
    df = pl.DataFrame({"FREQ": ["A"]})
    report = slpl.validation_report(dsd, df)
    assert report.schema["column"] == pl.String
    assert report.schema["invalid_value"] == pl.String
    assert "count" in report.schema


# ── label ─────────────────────────────────────────────────────────────────────


def test_label_adds_column():
    dsd = _dsd()
    df = pl.DataFrame({"FREQ": ["A", "Q", "M"]}).lazy()
    result = slpl.label(df, dsd, lang="en").collect()
    assert "FREQ_label_en" in result.columns


def test_label_maps_code_to_name():
    dim = _dim_enumerated("X", "A", "B")
    dsd = DataStructure(
        name={"en": "D"},
        id="D",
        maintainer="T",
        dimensions=ItemList([dim]),
        measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
    )
    df = pl.DataFrame({"X": ["A", "B", "A"]}).lazy()
    result = slpl.label(df, dsd, lang="en").collect()
    assert result["X_label_en"].to_list() == ["Label A", "Label B", "Label A"]


def test_label_custom_suffix():
    dim = _dim_enumerated("X", "A")
    dsd = DataStructure(
        name={"en": "D"},
        id="D",
        maintainer="T",
        dimensions=ItemList([dim]),
        measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
    )
    df = pl.DataFrame({"X": ["A"]}).lazy()
    result = slpl.label(df, dsd, lang="en", suffix="_name").collect()
    assert "X_name_en" in result.columns


def test_label_names_the_language_even_for_one():
    """So adding a second language does not rename the first column.

    `labels=["en"]` giving `FREQ_label` and `labels=["en", "fr"]` then giving
    `FREQ_label_en` would break every reader of the first on a change that
    only *adds*. The language is always in the name for that reason, and the
    cost is a longer column for the single-language case.
    """
    dsd = _dsd()
    df = pl.DataFrame({"FREQ": ["A"]}).lazy()
    one = slpl.label(df, dsd, lang="en").collect()
    both = slpl.label(df, dsd, lang=["en", "fr"]).collect()
    assert "FREQ_label_en" in one.columns
    assert one.columns == [c for c in both.columns if not c.endswith("_fr")]


def test_label_takes_several_languages():
    """One pass, one column each — and the maps are cached per (codelist, lang)
    at the source, so the second language does not re-query."""
    dim = _dim_enumerated("X", "A")
    dsd = DataStructure(
        name={"en": "D"},
        id="D",
        maintainer="T",
        dimensions=ItemList([dim]),
        measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
    )
    df = pl.DataFrame({"X": ["A"]}).lazy()
    result = slpl.label(df, dsd, lang=["en", "fr"]).collect()
    assert [c for c in result.columns if c != "X"] == ["X_label_en", "X_label_fr"]


def test_label_needs_a_language():
    dsd = _dsd()
    df = pl.DataFrame({"FREQ": ["A"]}).lazy()
    with pytest.raises(ValueError, match="at least one language"):
        slpl.label(df, dsd, lang=[]).collect()


def test_label_skips_non_enumerated():
    """A component with no codelist gets no label column, while its neighbours do.

    The frame carries an enumerated column too, deliberately: labelling a frame
    where *nothing* can be labelled now raises, and this test is about the
    per-component skip rather than that.
    """
    dsd = _dsd()
    df = pl.DataFrame({"FREQ": ["A"], "OBS_STATUS": ["A"]}).lazy()
    result = slpl.label(df, dsd, lang="en").collect()
    assert "FREQ_label_en" in result.columns
    assert "OBS_STATUS_label_en" not in result.columns


def test_label_refuses_when_the_matching_components_are_all_unlabelled():
    """The second half of "nothing to label", and a different message.

    A frame naming no component at all is usually physical column names where
    ids were wanted. A frame whose components are all non-enumerated has
    nothing to label by construction. Same refusal, different fix, so the
    message says which.
    """
    dsd = _dsd()
    df = pl.DataFrame({"OBS_STATUS": ["A"]}).lazy()
    with pytest.raises(ValueError, match="is enumerated"):
        slpl.label(df, dsd, lang="en").collect()


def test_label_skips_a_component_the_frame_lacks():
    """The original intent of this test, kept.

    A DSD names more components than a given frame carries — a projection, a
    CSV of two columns — and those are skipped. `OTHER` is passed through
    untouched for the same reason: labelling adds columns, it does not police
    them.
    """
    dsd = _dsd()
    df = pl.DataFrame({"FREQ": ["A"], "OTHER": ["x"]}).lazy()
    result = slpl.label(df, dsd, lang="en").collect()
    assert result.columns == ["FREQ", "OTHER", "FREQ_label_en"]


def test_label_refuses_a_frame_no_component_names():
    """Where this test used to assert the bug.

    It previously handed in a frame with no matching column at all and asserted
    the frame came back unchanged. That is the silent no-op that made
    `catalog.query(with_labels=True)` do nothing for any binding whose columns
    are physical names rather than SDMX ids — `GEO` where the DSD says
    `REF_AREA`. Nothing failed, so nobody looked.

    Labelling nothing is never what a caller meant, so it raises and names both
    sides. Skipping *some* components is a different case and still works,
    above.
    """
    dsd = _dsd()
    df = pl.DataFrame({"OTHER": ["x"]}).lazy()
    with pytest.raises(ValueError, match="nothing to label"):
        slpl.label(df, dsd, lang="en").collect()


# ── structure_frame ───────────────────────────────────────────────────────────


def test_structure_frame_columns():
    sf = slpl.structure_frame(_dsd(), lang="en")
    assert set(sf.columns) == {"component", "role", "position", "concept", "codelist", "n_codes"}


def test_structure_frame_has_dimension_row():
    sf = slpl.structure_frame(_dsd(), lang="en")
    dims = sf.filter(pl.col("role") == "dimension")
    assert "FREQ" in dims["component"].to_list()


def test_structure_frame_has_time_dimension():
    sf = slpl.structure_frame(_dsd(), lang="en")
    td = sf.filter(pl.col("role") == "time_dimension")
    assert not td.is_empty()
    assert td["component"][0] == "TIME_PERIOD"


def test_structure_frame_has_attribute_row():
    sf = slpl.structure_frame(_dsd(), lang="en")
    attrs = sf.filter(pl.col("role") == "attribute")
    assert "OBS_STATUS" in attrs["component"].to_list()


def test_structure_frame_has_measure_row():
    sf = slpl.structure_frame(_dsd(), lang="en")
    measures = sf.filter(pl.col("role") == "measure")
    assert "OBS_VALUE" in measures["component"].to_list()


def test_structure_frame_n_codes_for_enumerated():
    sf = slpl.structure_frame(_dsd(), lang="en")
    freq_row = sf.filter(pl.col("component") == "FREQ")
    assert freq_row["n_codes"][0] > 0


def test_structure_frame_codelist_id_for_enumerated():
    sf = slpl.structure_frame(_dsd(), lang="en")
    freq_row = sf.filter(pl.col("component") == "FREQ")
    assert freq_row["codelist"][0] == "CL_FREQ"


def test_structure_frame_no_codelist_for_measure():
    sf = slpl.structure_frame(_dsd(), lang="en")
    meas_row = sf.filter(pl.col("component") == "OBS_VALUE")
    assert meas_row["codelist"][0] is None


# ── measure_dimension ─────────────────────────────────────────────────────────


def _dsd_with_measure_dimension() -> DataStructure:
    cs = _concept_scheme("EMP", "UNE", "ACT")
    md = MeasureDimension(id="INDICATOR", representation=_resolved_cs_ref(cs), concept=concept_ref("INDICATOR"))
    return DataStructure(
        name={"en": "D"},
        id="D",
        maintainer="T",
        dimensions=ItemList([_dim_text("REF_AREA")]),
        measure_dimension=md,
        measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
    )


def test_schema_includes_measure_dimension():
    dsd = _dsd_with_measure_dimension()
    s = slpl.schema(dsd)
    assert "INDICATOR" in s
    assert isinstance(s["INDICATOR"], pl.Enum)


def test_schema_measure_dimension_enum_codes():
    dsd = _dsd_with_measure_dimension()
    s = slpl.schema(dsd)
    assert set(s["INDICATOR"].categories.to_list()) == {"EMP", "UNE", "ACT"}  # type: ignore[union-attr]


def test_structure_frame_has_measure_dimension_row():
    sf = slpl.structure_frame(_dsd_with_measure_dimension(), lang="en")
    md_row = sf.filter(pl.col("role") == "measure_dimension")
    assert not md_row.is_empty()
    assert md_row["component"][0] == "INDICATOR"


def test_structure_frame_measure_dimension_n_codes():
    sf = slpl.structure_frame(_dsd_with_measure_dimension(), lang="en")
    md_row = sf.filter(pl.col("role") == "measure_dimension")
    assert md_row["n_codes"][0] == 3


def test_validation_report_checks_measure_dimension():
    dsd = _dsd_with_measure_dimension()
    df = pl.DataFrame({"INDICATOR": ["EMP", "INVALID"], "OBS_VALUE": [1.0, 2.0]})
    report = slpl.validation_report(dsd, df)
    assert not report.is_empty()
    assert "INDICATOR" in report["column"].to_list()
