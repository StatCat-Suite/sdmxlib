"""SDMX 3.x's ``<com:Link>``, held on every identifiable that may carry one (#422).

``IdentifiableType`` allows any number of links in SDMX-ML 3.x, and every
reader dropped them, reported as one `LOSSES` row. They are `links` now, beside
each `uri`: written by SDMX-ML 3.x and SDMX-JSON, stored, and dropped — as a
declared gap — by SDMX-ML 2.1, which has no such element.

`tests/unit/test_field_coverage.py` carries the breadth: the maximal fixture
sets links on every class that has them and proves each survives every format
and the store. This file holds what that cannot say — the refusals, the order,
the back-link dedupe and what a read costs.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, cast

import attrs
import duckdb
import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats import parse, serialize
from sdmxlib.formats.gaps import unrepresentable
from sdmxlib.formats.sdmx_json import metadata as json_md
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_ml30 import metadata as ml30_md
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.model._refshape import link_fields, walk_fields
from sdmxlib.model.message import MetadataMessage, StructureMessage
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import ValidationError, check_serialisable
from tests.support import maximal
from tests.support.roundtrip import as_structure

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

pytestmark = pytest.mark.unit

_OTHER = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:CL_OTHER(1.0)"
_REPORT = sl.Link(rel="metadata", url="https://example.org/md/1", urn=_OTHER, type="text")
_ALTERNATE = sl.Link(rel="alternate", url="https://example.org/alt")


def _codelist(links: tuple[sl.Link, ...] = (_REPORT, _ALTERNATE), uri: str | None = None) -> sl.Codelist:
    code = sl.Code(id="A", name={"en": "a"}, links=(_ALTERNATE,))
    return sl.Codelist(id="CL", maintainer="AG", version="1.0", name={"en": "cl"}, codes=[code], links=links, uri=uri)


def _round_trip(codelist: sl.Codelist, version: str) -> sl.Codelist:
    (back,) = as_structure(parse(serialize(StructureMessage(codelists=[codelist]), version=version))).codelists
    return back


# ── the model ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["https://example.org", _REPORT], ids=["str", "link"])
def test_a_bare_string_or_link_is_not_links(value: object) -> None:
    with pytest.raises(TypeError, match="iterable of Link"):
        sl.Code(name={"en": "A"}, id="A", links=cast("tuple[sl.Link, ...]", value))


def test_a_code_is_still_never_walked_for_refs() -> None:
    """`links` is a tuple of attrs objects that hold no `Ref`, so #376's guarantee stands."""
    assert walk_fields(sl.Code) == ()
    assert link_fields(sl.Code) == ("links",)


# ── SDMX-ML 3.x and SDMX-JSON ─────────────────────────────────────────────


@pytest.mark.parametrize("version", ["3.0", "3.1", "json"])
def test_links_round_trip_in_order(version: str) -> None:
    back = _round_trip(_codelist(), version)
    assert back.links == (_REPORT, _ALTERNATE)
    assert back.codes["A"].links == (_ALTERNATE,)


@pytest.mark.parametrize("version", ["3.0", "3.1"])
def test_sdmx_ml_writes_links_after_the_annotations(version: str) -> None:
    codelist = attrs.evolve(_codelist(), annotations=sl.Annotations([sl.Annotation(type="NOTE")]))
    doc = etree.fromstring(serialize(StructureMessage(codelists=[codelist]), version=version))
    (el,) = doc.iter("{*}Codelist")
    assert [etree.QName(child).localname for child in el][:4] == ["Annotations", "Link", "Link", "Name"]


def test_sdmx_json_keeps_a_uri_and_a_describedby_link_apart() -> None:
    """The first `describedby` is the uri; one after it is a link."""
    described = sl.Link(rel="describedby", url="https://example.org/more")
    back = _round_trip(_codelist(links=(described,), uri="https://example.org/uri"), "json")
    assert back.uri == "https://example.org/uri"
    assert back.links == (described,)


_URI = "https://example.org/uri"


def _json_links(codelist: sl.Codelist) -> list[dict[str, object]]:
    body = json.loads(json_writer.write(StructureMessage(codelists=[codelist])))
    links: list[dict[str, object]] = body["data"]["codelists"][0]["links"]
    return [link for link in links if link["rel"] != "self"]


def test_sdmx_json_holds_the_uri_link_s_urn_as_a_link_too() -> None:
    """A uri's link that says more than its ``href`` is a `Link` as well, written once (#422)."""
    described = sl.Link(rel="describedby", url=_URI, urn=_OTHER, type="PDF", hreflang="en")
    codelist = _codelist(links=(_ALTERNATE, described), uri=_URI)
    assert _json_links(codelist) == [
        {"href": _ALTERNATE.url, "rel": "alternate"},
        {"href": _URI, "rel": "describedby", "urn": _OTHER, "hreflang": "en", "type": "PDF"},
    ]
    back = _round_trip(codelist, "json")
    assert (back.uri, back.links) == (_URI, (_ALTERNATE, described))


def test_sdmx_json_reads_a_bare_uri_link_as_the_uri_alone() -> None:
    back = _round_trip(_codelist(links=(), uri=_URI), "json")
    assert (back.uri, back.links) == (_URI, ())


@pytest.mark.parametrize(
    "described",
    [
        pytest.param(sl.Link(rel="describedby", url=_URI), id="bare, the uri's url"),
        pytest.param(sl.Link(rel="describedby", url="https://example.org/more", urn=_OTHER), id="another url"),
    ],
)
def test_sdmx_json_writes_the_uri_first_when_no_link_stands_for_it(described: sl.Link) -> None:
    """Only a link with more than the uri's ``href`` can stand for it; otherwise the uri leads."""
    codelist = _codelist(links=(described,), uri=_URI)
    assert [link["href"] for link in _json_links(codelist)] == [_URI, described.url]
    back = _round_trip(codelist, "json")
    assert (back.uri, back.links) == (_URI, (described,))


def test_sdmx_json_keeps_a_link_s_language() -> None:
    localised = sl.Link(rel="alternate", url="https://example.org/fr", hreflang="fr")
    assert _round_trip(_codelist(links=(localised,)), "json").links == (localised,)


@pytest.mark.parametrize("version", ["3.0", "3.1"])
def test_sdmx_ml_drops_a_link_s_language_and_says_so(version: str) -> None:
    localised = sl.Link(rel="alternate", url="https://example.org/fr", hreflang="fr")
    codelist = _codelist(links=(localised,))
    assert [gap.field for gap in unrepresentable(codelist, version)] == ["links[].hreflang"]
    assert _round_trip(codelist, version).links == (attrs.evolve(localised, hreflang=None),)


_DETAILED_URL = sl.AnnotationURL("https://example.org/doc", lang="en", urn=_OTHER, type="PDF")


def _annotated() -> sl.Codelist:
    return sl.Codelist(
        id="CL",
        maintainer="AG",
        version="1.0",
        name={"en": "cl"},
        codes=[sl.Code(id="A", name={"en": "a"})],
        annotations=sl.Annotations([sl.Annotation(urls=[_DETAILED_URL])]),
    )


def test_sdmx_json_keeps_an_annotation_url_s_urn_and_type() -> None:
    (annotation,) = _round_trip(_annotated(), "json").annotations
    assert annotation.urls == (_DETAILED_URL,)


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1"])
def test_sdmx_ml_drops_an_annotation_url_s_urn_and_type_and_says_so(version: str) -> None:
    assert {gap.field for gap in unrepresentable(_annotated(), version)} == {
        "annotations[].urls[].urn",
        "annotations[].urls[].type",
    } | ({"annotations[].urls"} if version == "2.1" else set())
    (annotation,) = _round_trip(_annotated(), version).annotations
    held = _DETAILED_URL.lang if version != "2.1" else None
    assert annotation.urls == (sl.AnnotationURL(_DETAILED_URL.url, lang=held),)


@pytest.mark.parametrize(
    ("link", "uri", "field", "says"),
    [
        pytest.param(sl.Link(rel="self", url="https://e.org"), None, "rel", "URN", id="self"),
        pytest.param(sl.Link(rel="describedby", url="https://e.org"), None, "rel", "uri", id="describedby-no-uri"),
        pytest.param(
            sl.Link(rel="alternate", url="https://e.org", urn="urn:isbn:0"), None, "urn", "SDMX URN", id="urn"
        ),
    ],
)
def test_sdmx_json_refuses_a_link_it_would_read_back_as_something_else(
    link: sl.Link, uri: str | None, field: str, says: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        serialize(StructureMessage(codelists=[_codelist(links=(link,), uri=uri)]), version="json")
    (issue,) = caught.value.issues
    assert issue.path == f"Codelist:CL.links[0].{field}"
    assert says in issue.message


def test_sdmx_ml_takes_what_sdmx_json_refuses() -> None:
    """The refusals are SDMX-JSON's: SDMX-ML has no identity link and types a urn `anyURI`."""
    links = (sl.Link(rel="self", url="https://e.org"), sl.Link(rel="alternate", url="https://e.org", urn="urn:isbn:0"))
    assert _round_trip(_codelist(links=links), "3.0").links == links


@pytest.mark.parametrize("version", ["3.0", "3.1"])
def test_sdmx_ml_3x_allows_a_metadata_attribute_usage_one_link(version: str) -> None:
    msg = maximal.message()
    (dsd,) = msg.data_structures
    usage, *rest = dsd.metadata_attributes
    two = attrs.evolve(usage, links=(_REPORT, _ALTERNATE))
    msg = attrs.evolve(msg, data_structures=[attrs.evolve(dsd, metadata_attributes=[two, *rest])])
    with pytest.raises(ValidationError) as caught:
        check_serialisable(msg, version=version)
    assert [i.path for i in caught.value.issues if i.path.endswith(".links")] == [
        f"DataStructure:{dsd.id}.metadata_attributes[{usage.id}].links"
    ]


# ── SDMX-ML 2.1 ───────────────────────────────────────────────────────────


def test_sdmx_ml_2_1_drops_links_and_says_so_first() -> None:
    codelist = _codelist()
    assert {gap.field for gap in unrepresentable(codelist, "2.1")} >= {"links", "codes[].links"}
    back = _round_trip(codelist, "2.1")
    assert back.links == ()
    assert back.codes["A"].links == ()


# ── the metadata back-links ───────────────────────────────────────────────


def _flow_with_a_report_link() -> sl.Dataflow:
    held = sl.Link(rel="metadata", url="https://h/sdmx/v2/metadata/metadataset/AG/MS1/1.0", urn=_set_urn("MS1"))
    return sl.Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "df"}, links=(held,))


def _set_urn(set_id: str) -> str:
    return f"urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataSet=AG:{set_id}(1.0)"


def test_sdmx_ml_does_not_link_a_held_report_twice() -> None:
    flow = _flow_with_a_report_link()
    out = ml30_writer.write(
        StructureMessage(dataflows=[flow]),
        metadata_links={str(flow.urn): [_set_urn("MS1"), _set_urn("MS2")]},
        base_url="https://h/sdmx/v2",
    )
    (el,) = etree.fromstring(out).iter("{*}Dataflow")
    assert [link.get("urn") for link in el.iter("{*}Link")] == [_set_urn("MS1"), _set_urn("MS2")]


def test_sdmx_json_does_not_link_a_held_report_twice() -> None:
    flow = _flow_with_a_report_link()
    out = json_writer.write(
        StructureMessage(dataflows=[flow]),
        metadata_links={str(flow.urn): [_set_urn("MS1"), _set_urn("MS2")]},
        base_url="https://h/sdmx/v2",
    )
    (back,) = as_structure(parse(out)).dataflows
    assert [link.urn for link in back.links] == [_set_urn("MS1"), _set_urn("MS2")]


# ── reference metadata ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("read", "write"), [(ml30_md.read, ml30_md.write), (json_md.read, json_md.write)], ids=["ml30", "json"]
)
def test_a_metadata_set_keeps_its_links(
    read: Callable[[bytes], MetadataMessage], write: Callable[[MetadataMessage], bytes]
) -> None:
    flow = SdmxUrn(package="metadatastructure", artefact_class="Metadataflow", agency="T", id="MDF", version="1.0")
    ms = MetadataSet(
        name={"en": "MS"},
        id="MS",
        maintainer="T",
        version="1.0.0",
        structure=Ref[Metadataflow].unresolved(flow),
        valid_from=datetime(2021, 3, 1),
        links=(_REPORT, _ALTERNATE),
    )
    (back,) = read(write(MetadataMessage(metadata_sets=[ms]))).metadata_sets
    assert back.links == (_REPORT, _ALTERNATE)


# ── the store ─────────────────────────────────────────────────────────────


def test_the_store_keeps_what_only_sdmx_json_says() -> None:
    """A link's language, and an annotation URL's URN and type (#422)."""
    localised = sl.Link(rel="alternate", url="https://example.org/fr", hreflang="fr")
    code = sl.Code(
        id="A",
        name={"en": "a"},
        links=(localised,),
        annotations=sl.Annotations([sl.Annotation(urls=[_DETAILED_URL])]),
    )
    codelist = attrs.evolve(_annotated(), codes=[code], links=(localised,))
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(codelist)
        lazy = reg.get(sl.Codelist.parse_urn(str(codelist.urn)))
        assert lazy is not None
        back = lazy.materialize()
    assert back.links == (localised,)
    assert back.annotations[0].urls == (_DETAILED_URL,)
    assert back.codes["A"].links == (localised,)
    assert back.codes["A"].annotations[0].urls == (_DETAILED_URL,)


def test_the_store_keeps_links_on_the_artefact_and_its_items() -> None:
    codelist = _codelist()
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(codelist)
        lazy = reg.get(sl.Codelist.parse_urn(str(codelist.urn)))
        assert lazy is not None
        assert lazy.links == (_REPORT, _ALTERNATE)
        materialised = lazy.materialize()
        assert materialised.links == (_REPORT, _ALTERNATE)
        assert materialised.codes["A"].links == (_ALTERNATE,)


def _statements(reg: sl.LocalRegistry, urn: SdmxUrn[object]) -> Iterator[str]:
    seen: list[str] = []
    real = duckdb.DuckDBPyConnection.execute

    def counting(
        self: duckdb.DuckDBPyConnection, query: duckdb.Statement | str, parameters: object = None
    ) -> duckdb.DuckDBPyConnection:
        seen.append(" ".join(str(query).split()))
        return real(self, query, parameters)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(duckdb.DuckDBPyConnection, "execute", counting)
        assert reg.get(urn) is not None
    return iter(seen)


def _concept_scheme(*, own: tuple[sl.Link, ...], item: tuple[sl.Link, ...]) -> sl.ConceptScheme:
    concept = sl.Concept(id="C", name={"en": "c"}, links=item)
    return sl.ConceptScheme(id="CS", maintainer="AG", version="1.0", name={"en": "cs"}, concepts=[concept], links=own)


def test_a_get_pays_for_item_links_only_when_there_are_some() -> None:
    """The spine row carries the artefact's own links and a count of its items' (#426's budget)."""
    counts: dict[str, int] = {}
    for label, scheme in (
        ("none", _concept_scheme(own=(), item=())),
        ("own", _concept_scheme(own=(_REPORT,), item=())),
        ("items", _concept_scheme(own=(), item=(_ALTERNATE,))),
    ):
        with sl.LocalRegistry(":memory:") as reg:
            reg.add(scheme)
            urn = sl.ConceptScheme.parse_urn(str(scheme.urn))
            _ = reg.get(urn)  # the resolver cache warm, as the #426 guard measures
            counts[label] = len(list(_statements(reg, urn)))
            back = reg.get(urn)
            assert isinstance(back, sl.ConceptScheme)
            assert back.links == scheme.links
            assert back.concepts["C"].links == scheme.concepts["C"].links
    assert counts["own"] == counts["none"]
    assert counts["items"] == counts["none"] + 1
