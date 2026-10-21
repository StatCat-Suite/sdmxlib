"""Finality is read from the version; 2.1's `isFinal` is kept beside it (#420, #424).

SDMX 3.0 removed `isFinal` from every maintainable: a semantic version with no
extension is final, and anything else — a `-draft`, a legacy `1.0` — is not.
The model follows it, so `is_final` is derived and every format carries it
wherever it carries the version.

2.1 still says `isFinal` out loud, and can say something the version does not
(`1.0` declared final). That disagreement, and only that, is kept on
`legacy_is_final`, set by the 2.1 reader and read by the 2.1 writer.
"""

import functools

import attrs
import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats.sdmx_ml21 import reader as ml21_reader
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import metadata as ml30_md
from sdmxlib.formats.sdmx_ml30 import reader as ml30_reader
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.hierarchy import HierarchyAssociation
from sdmxlib.model.message import MetadataMessage, StructureMessage
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.version import is_final
from tests.support.xsd import SchemaVersion, assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.unit

_STR21 = "{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure}"


@functools.cache
def _schema(version: SchemaVersion) -> etree.XMLSchema:
    return compile_structure_schema(version)


def _codelist(version: str, legacy_is_final: bool | None = None) -> Codelist:
    return Codelist(
        id="CL",
        maintainer="AG",
        version=version,
        name={"en": "CL"},
        legacy_is_final=legacy_is_final,
        codes=[Code(id="A", name={"en": "a"})],
    )


def _read_21_with(version: str, is_final_attr: str | None) -> Codelist:
    """A 2.1 document whose codelist says `isFinal=is_final_attr`, or nothing."""
    root = etree.fromstring(ml21_writer.write(StructureMessage(codelists=[_codelist(version)])))
    (el,) = root.iter(f"{_STR21}Codelist")
    el.attrib.pop("isFinal", None)
    if is_final_attr is not None:
        el.set("isFinal", is_final_attr)
    return ml21_reader.read(etree.tostring(root)).codelists["CL"]


# ── the derivation ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("version", "final"),
    [
        ("1.0.0", True),
        ("2.3.10", True),
        ("1.0", False),  # legacy: "considered draft or unstable"
        ("1", False),
        ("1.0.0-draft", False),
        ("1.0.0-rc.1", False),
        ("0.1.0", False),  # semver: major zero is initial development
        ("1.03", False),  # 2.1 only
        ("1.2.3.4", False),  # 2.1 only
        ("01.0.0", False),
    ],
)
def test_finality_is_read_from_the_version(version: str, final: bool) -> None:
    assert is_final(version) is final
    assert _codelist(version).is_final is final


def test_every_maintainable_answers_it_including_the_3_0_only_ones() -> None:
    """3.0 artefacts have no 2.1 flag, and still a finality: their version's."""
    ha = HierarchyAssociation(id="HA", maintainer="AG", version="1.0.0", name={"en": "HA"})
    assert ha.is_final is True
    assert not hasattr(ha, "legacy_is_final")


def test_finality_cannot_be_set_apart_from_the_version() -> None:
    assert "is_final" not in attrs.fields_dict(Codelist)


# ── 2.1 in ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("version", "attr", "legacy", "final"),
    [
        ("1.0", "true", True, False),  # 2.1 final, 3.0 draft: kept
        ("1.0.0", None, False, True),  # absent is false, the version says final: kept
        ("1.0.0", "false", False, True),
        ("1.0.0", "true", None, True),  # agrees: nothing to keep
        ("1.0", None, None, False),
        ("1.0", "false", None, False),
    ],
)
def test_the_2_1_reader_keeps_isfinal_only_where_it_disagrees(
    version: str, attr: str | None, legacy: bool | None, final: bool
) -> None:
    cl = _read_21_with(version, attr)
    assert cl.legacy_is_final is legacy
    assert cl.is_final is final


def test_one_artefact_read_from_2_1_and_from_3_0_is_equal() -> None:
    """Why the reader keeps `None` when the two agree, rather than the bool."""
    from_21 = _read_21_with("1.0.0", "true")
    from_30 = ml30_reader.read(ml30_writer.write(StructureMessage(codelists=[_codelist("1.0.0")]))).codelists["CL"]
    assert from_21 == from_30


def test_3_0_never_sets_the_flag() -> None:
    back = ml30_reader.read(ml30_writer.write(StructureMessage(codelists=[_codelist("1.0")]))).codelists["CL"]
    assert back.legacy_is_final is None


# ── 2.1 out ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("version", "legacy", "written"),
    [
        ("1.0.0", None, "true"),  # a 3.0 artefact projected: its version's finality
        ("1.0", None, None),
        ("1.0", True, "true"),  # the 2.1 document's own word wins
        ("1.0.0", False, None),
        ("1.0.0-draft", None, "left out"),
    ],
)
def test_the_2_1_writer_says_the_flag_if_set_and_the_version_otherwise(
    version: str, legacy: bool | None, written: str | None
) -> None:
    msg = StructureMessage(codelists=[_codelist(version, legacy)])
    if written == "left out":
        # 2.1 has no grammar for an extension, so a draft is not written (#424).
        assert not ml21_reader.read(sl.serialize(msg, version="2.1")).codelists
        return
    xml = sl.serialize(msg, version="2.1")
    assert_valid_sdmx_ml(xml, _schema("2.1"))
    (el,) = etree.fromstring(xml).iter(f"{_STR21}Codelist")
    assert el.get("isFinal") == written
    assert ml21_reader.read(xml).codelists["CL"] == msg.codelists["CL"]


@pytest.mark.parametrize("target", ["3.0", "3.1"])
def test_3_x_writes_no_isfinal_for_a_final_artefact(target: SchemaVersion) -> None:
    xml = sl.serialize(StructureMessage(codelists=[_codelist("1.0.0", legacy_is_final=False)]), version=target)
    assert b"isFinal" not in xml
    assert_valid_sdmx_ml(xml, _schema(target))


def test_a_final_metadata_set_writes_valid_3_0() -> None:
    """The 3.0 metadata writer emitted `isFinal`, which 3.0 does not define.

    Latent while `is_final` defaulted to `False`; a metadata set defaults to
    `1.0.0`, so deriving finality would have made every one invalid.
    """
    flow = SdmxUrn(package="metadatastructure", artefact_class="Metadataflow", agency="AG", id="MDF", version="1.0")
    target = SdmxUrn(package="datastructure", artefact_class="Dataflow", agency="AG", id="DF", version="1.0")
    ms = MetadataSet(
        id="MS",
        maintainer="AG",
        name={"en": "MS"},
        structure=Ref[Metadataflow].unresolved(flow),
        targets=[Ref[object].unresolved(target)],
        attributes=[ReportedAttribute(id="NOTE", value="x")],
    )
    assert ms.is_final
    xml = ml30_md.write(MetadataMessage(metadata_sets=[ms]))
    assert b"isFinal" not in xml
    assert_valid_sdmx_ml(xml, _schema("3.0"))


# ── the store ─────────────────────────────────────────────────────────────


def _stored(id_: str, version: str, legacy_is_final: bool | None = None) -> Codelist:
    return Codelist(
        id=id_,
        maintainer="AG",
        version=version,
        name={"en": id_},
        legacy_is_final=legacy_is_final,
        codes=[Code(id="A", name={"en": "a"})],
    )


@pytest.fixture
def store():
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all(
            [
                _stored("CL_LEGACY", "1.0", legacy_is_final=True),
                _stored("CL_FINAL", "1.0.0"),
                _stored("CL_DRAFT", "1.0.0-draft"),
            ]
        )
        yield reg


@pytest.mark.parametrize(("final", "ids"), [(True, ["CL_FINAL"]), (False, ["CL_DRAFT", "CL_LEGACY"])])
def test_the_store_filters_on_the_derived_answer(store: sl.LocalRegistry, final: bool, ids: list[str]) -> None:
    """A 2.1 `1.0` declared final is not final here, in the store as in the model."""
    found = store.query(sl.q.codelist).filter(sl.q.codelist.is_final == final).collect()
    assert sorted(cl.id for cl in found) == ids


@pytest.mark.parametrize("eager", [False, True])
def test_the_store_keeps_the_2_1_flag(store: sl.LocalRegistry, eager: bool) -> None:
    cl = store.get(Codelist.urn_for(agency="AG", id="CL_LEGACY", version="1.0"), eager=eager)
    assert cl is not None
    assert (cl.is_final, cl.legacy_is_final) == (False, True)
