"""The last of #422's read-side register, modelled: annotation URLs, sentinels, time range validity.

- An annotation holds several `AnnotationURL`s, each optionally localised.
- A text format holds `SentinelValue`s — never an enumeration or coding format.
- A cube region's `TimeRange` holds its own validity period.

The censuses prove every format and the store carry each (the maximal fixture
sets all three); these pin the wire spellings, what 2.1 and SDMX-JSON 1.0
cannot hold, the refusals, and the query language's `urls` fan-out.
"""

import functools
import json
from collections.abc import Callable

import attrs
import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats.gaps import unrepresentable
from sdmxlib.formats.sdmx_json import reader as json_reader
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_json_1_0.writer import write_contentconstraint
from sdmxlib.formats.sdmx_ml21 import reader as ml21_reader
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import reader as ml30_reader
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.formats.sdmx_ml31 import reader as ml31_reader
from sdmxlib.formats.sdmx_ml31 import writer as ml31_writer
from sdmxlib.model.annotations import Annotation, Annotations, AnnotationURL
from sdmxlib.model.constraint import ConstraintAttachment, CubeRegion, DataConstraint, KeyValue, TimeBound, TimeRange
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet, SentinelValue
from sdmxlib.query.accessors import q
from tests.support.factories import concept_ref
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema
from tests.support.xsd import SchemaVersion, assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
_V3: dict[SchemaVersion, tuple[Callable[[StructureMessage], bytes], Callable[[bytes], StructureMessage]]] = {
    "3.0": (ml30_writer.write, ml30_reader.read),
    "3.1": (ml31_writer.write, ml31_reader.read),
}


@functools.cache
def _xsd(version: SchemaVersion) -> etree.XMLSchema:
    return compile_structure_schema(version)


def _json_valid(written: bytes) -> None:
    assert_valid_sdmx_json(written, compile_json_schema("structure", "2.0"))
    assert_valid_sdmx_json(written, compile_json_schema("structure", "2.1"))


# ── annotation URLs ───────────────────────────────────────────────────────

_URLS = (
    AnnotationURL("https://example.org/m.pdf"),
    AnnotationURL("https://example.org/m-en.pdf", lang="en"),
    AnnotationURL("https://example.org/m-fr.pdf", lang="fr"),
)


def _annotated() -> sl.Codelist:
    return sl.Codelist(
        id="CL",
        maintainer="AG",
        version="1.0",
        name={"en": "c"},
        annotations=Annotations([Annotation(type="METHODOLOGY", urls=_URLS)]),
        codes=[sl.Code(id="A", name={"en": "a"})],
    )


def test_a_url_may_be_given_bare() -> None:
    assert Annotation(urls="https://x").urls == (AnnotationURL("https://x"),)
    assert Annotation(urls=["https://x", AnnotationURL("https://y", "de")]).urls == (
        AnnotationURL("https://x"),
        AnnotationURL("https://y", "de"),
    )
    assert Annotation().urls == ()


@pytest.mark.parametrize("version", sorted(_V3))
def test_sdmx_ml_3x_writes_every_url_with_its_language(version: SchemaVersion) -> None:
    write, read = _V3[version]
    written = write(StructureMessage(codelists=[_annotated()]))
    assert_valid_sdmx_ml(written, _xsd(version))
    urls = [el for el in etree.fromstring(written).iter() if etree.QName(el).localname == "AnnotationURL"]
    assert [(el.text, el.get(_XML_LANG)) for el in urls] == [(u.url, u.lang) for u in _URLS]
    assert read(written).codelists["CL"].annotations[0].urls == _URLS


def test_sdmx_ml_2_1_writes_the_first_url_and_declares_the_rest() -> None:
    back = ml21_reader.read(ml21_writer.write(StructureMessage(codelists=[_annotated()])))
    assert back.codelists["CL"].annotations[0].urls == _URLS[:1]
    assert "annotations[].urls" in [gap.field for gap in unrepresentable(_annotated(), "2.1")]
    assert unrepresentable(_annotated(), "3.0") == ()


def test_an_empty_annotation_url_is_no_url() -> None:
    """Eurostat ships ~830,000 empty `AnnotationURL` elements; none is a URL."""
    xml = ml30_writer.write(StructureMessage(codelists=[_annotated()]))
    doc = etree.fromstring(xml)
    for el in doc.iter():
        if etree.QName(el).localname == "AnnotationURL" and el.get(_XML_LANG) == "fr":
            el.text = "  "
    assert ml30_reader.read(etree.tostring(doc)).codelists["CL"].annotations[0].urls == _URLS[:2]


def test_sdmx_json_writes_each_url_as_a_link_with_its_hreflang() -> None:
    written = json_writer.write(StructureMessage(codelists=[_annotated()]))
    _json_valid(written)
    (annotation,) = json.loads(written)["data"]["codelists"][0]["annotations"]
    assert [(link["href"], link.get("hreflang")) for link in annotation["links"]] == [(u.url, u.lang) for u in _URLS]
    assert json_reader.read(written).codelists["CL"].annotations[0].urls == _URLS


def test_the_store_keeps_every_url_in_order_and_queries_them() -> None:
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(_annotated())
        assert _stored_urls(registry) == _URLS
        french = registry.query(q.codelist).filter(q.codelist.annotations.urls.lang == "fr").ids()
        assert french == ["CL"]
        listed = registry.query(q.annotation).select(urls=q.annotation.urls.url.list()).collect()
        assert sorted(listed["urls"].to_list()[0]) == sorted(u.url for u in _URLS)
        # Re-adding replaces the URLs rather than piling them up — counted in
        # the table, since an orphaned row would never reach a read.
        registry.add(_annotated())
        assert _stored_urls(registry) == _URLS
        assert registry.sql("SELECT count(*) AS n FROM sdmx.annotation_url").collect().to_dicts() == [{"n": len(_URLS)}]


def _stored_urls(registry: sl.LocalRegistry) -> tuple[AnnotationURL, ...]:
    codelist = registry.get(_annotated().urn, eager=True)
    assert isinstance(codelist, sl.Codelist)
    return codelist.annotations[0].urls


# ── sentinel values ───────────────────────────────────────────────────────

_SENTINELS = (
    SentinelValue("-1", name={"en": "Not applicable"}, description={"en": "No value can exist"}),
    SentinelValue("-2", name={"en": "Confidential", "fr": "Confidentiel"}),
)


def _concepts(facet: Facet) -> sl.ConceptScheme:
    return sl.ConceptScheme(
        id="CS",
        maintainer="AG",
        version="1.0",
        name={"en": "cs"},
        concepts=[sl.Concept(id="OBS_VALUE", name={"en": "o"}, core_representation=facet)],
    )


def _with_sentinels() -> Facet:
    return Facet(type="Integer", sentinel_values=_SENTINELS)


@pytest.mark.parametrize("version", sorted(_V3))
def test_sdmx_ml_3x_writes_sentinels_in_the_text_format(version: SchemaVersion) -> None:
    write, read = _V3[version]
    written = write(StructureMessage(concept_schemes=[_concepts(_with_sentinels())]))
    assert_valid_sdmx_ml(written, _xsd(version))
    sentinels = [el for el in etree.fromstring(written).iter() if etree.QName(el).localname == "SentinelValue"]
    assert [el.get("value") for el in sentinels] == ["-1", "-2"]
    assert read(written).concept_schemes["CS"].concepts["OBS_VALUE"].core_representation == _with_sentinels()


def test_sdmx_json_carries_sentinels_and_reads_a_numeric_one_as_its_text() -> None:
    written = json_writer.write(StructureMessage(concept_schemes=[_concepts(_with_sentinels())]))
    _json_valid(written)
    assert json_reader.read(written).concept_schemes["CS"].concepts["OBS_VALUE"].core_representation == (
        _with_sentinels()
    )
    body = json.loads(written)
    sentinels = body["data"]["conceptSchemes"][0]["concepts"][0]["coreRepresentation"]["format"]["sentinelValues"]
    sentinels[0]["value"] = -1  # a number, which the schema allows
    facet = json_reader.read(json.dumps(body).encode()).concept_schemes["CS"].concepts["OBS_VALUE"].core_representation
    assert isinstance(facet, Facet)
    assert facet.sentinel_values[0].value == "-1", "not -1.0"


def test_sdmx_ml_2_1_declares_the_sentinels_it_cannot_write() -> None:
    back = ml21_reader.read(ml21_writer.write(StructureMessage(concept_schemes=[_concepts(_with_sentinels())])))
    facet = back.concept_schemes["CS"].concepts["OBS_VALUE"].core_representation
    assert isinstance(facet, Facet)
    assert facet.sentinel_values == ()
    assert [gap.field for gap in unrepresentable(_concepts(_with_sentinels()), "2.1")] == [
        "concepts[].core_representation.sentinel_values"
    ]


def test_a_sentinel_is_refused_on_an_enumeration_or_coding_format() -> None:
    """Refused as the artefact is built, since no format can write it (#508).

    It was a write-time check until #508; a store could hold what no format
    writes, which #419 had set out to stop.
    """
    codelist = sl.Codelist(id="CL", maintainer="AG", name={"en": "c"}, codes=[sl.Code(id="A", name={"en": "a"})])
    with pytest.raises(sl.ValidationError) as caught:
        sl.DataStructure(
            id="DSD",
            maintainer="AG",
            name={"en": "d"},
            dimensions=[
                sl.Dimension(
                    id="D",
                    representation=Ref.to(codelist),
                    enumeration_format=_with_sentinels(),
                    concept=concept_ref("D"),
                )
            ],
            measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
        )
    assert [issue.path for issue in caught.value.issues if "sentinel" in issue.message] == [
        "DataStructure.dimensions[D].enumeration_format"
    ]
    with pytest.raises(sl.ValidationError) as caught:
        sl.Hierarchy(
            id="H",
            maintainer="AG",
            name={"en": "h"},
            has_formal_levels=True,
            # Numeric: a level's coding format is a code type, refused otherwise as
            # it is built (#492) — the sentinels are this test's subject.
            levels=[sl.Level(id="L", name={"en": "l"}, coding_format=attrs.evolve(_with_sentinels(), type="Numeric"))],
        )
    assert [issue.path for issue in caught.value.issues if "sentinel" in issue.message] == [
        "Hierarchy.levels[L].coding_format"
    ]


def test_the_store_keeps_sentinels_with_an_empty_description() -> None:
    """Typed explicitly on write: inferred, an empty description read back as a null `en`."""
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(_concepts(_with_sentinels()))
        scheme = registry.get(_concepts(_with_sentinels()).urn)
        assert isinstance(scheme, sl.ConceptScheme)
        assert scheme.concepts["OBS_VALUE"].core_representation == _with_sentinels()


# ── a time range's own validity ───────────────────────────────────────────


def _constraint() -> DataConstraint:
    flow = Ref[Dataflow].unresolved(Dataflow.urn_for(agency="AG", id="DF", version="1.0"))
    return DataConstraint(
        id="DC",
        maintainer="AG",
        version="1.0",
        name={"en": "DC"},
        attachment=ConstraintAttachment(dataflows=(flow,)),
        regions=[
            CubeRegion(
                keys=(
                    KeyValue(
                        dimension_id="TIME_PERIOD",
                        time_range=TimeRange(
                            start=TimeBound("2020-01"), end=TimeBound("2024-12"), valid_from="2021", valid_to="2025"
                        ),
                    ),
                )
            )
        ],
    )


@pytest.mark.parametrize("version", sorted(_V3))
def test_sdmx_ml_3x_writes_a_time_ranges_validity(version: SchemaVersion) -> None:
    write, read = _V3[version]
    written = write(StructureMessage(constraints=[_constraint()]))
    assert_valid_sdmx_ml(written, _xsd(version))
    (range_el,) = [el for el in etree.fromstring(written).iter() if etree.QName(el).localname == "TimeRange"]
    assert (range_el.get("validFrom"), range_el.get("validTo")) == ("2021", "2025")
    assert read(written).constraints[0].regions == _constraint().regions


def test_sdmx_json_carries_a_time_ranges_validity() -> None:
    written = json_writer.write(StructureMessage(constraints=[_constraint()]))
    _json_valid(written)
    assert json_reader.read(written).constraints[0].regions == _constraint().regions


def test_sdmx_ml_2_1_and_sdmx_json_1_0_cannot_say_it() -> None:
    fields = [gap.field for gap in unrepresentable(_constraint(), "2.1")]
    assert {"regions[].keys[].time_range.valid_from", "regions[].keys[].time_range.valid_to"} <= set(fields)
    with pytest.raises(sl.ValidationError) as caught:
        write_contentconstraint(_constraint())
    assert [issue.path.rsplit(".", 1)[1] for issue in caught.value.issues] == ["time_range"]
