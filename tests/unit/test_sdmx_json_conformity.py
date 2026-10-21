"""Conformity: emitted SDMX-JSON structure output validates against the official
SDMX TWG JSON Schema (``tests/schemas/sdmx_json/2.1/``, ``$id`` under
``json.sdmx.org``).

This is the JSON counterpart to the XSD round-trip validation on the XML side
(`tests/support/xsd.py`): it asserts that what our writer *emits* actually
conforms to the wire spec — a class of bug pure round-trip can't catch. It is
also the gate the msgspec DTO migration must not break: the Struct-based writer
(milestone: SDMX-JSON dict+orjson → msgspec) must keep emitting schema-valid
JSON, so these tests carry over unchanged.
"""

import functools
from typing import Any

import pytest

from sdmxlib.formats import serialize
from sdmxlib.formats.sdmx_json.metadata import write as write_metadata_json
from sdmxlib.model.annotations import Annotation
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ArtefactList
from sdmxlib.model.message import MetadataMessage, StructureMessage
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.urn import SdmxUrn
from tests.bench.fixtures import make_message
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema

pytestmark = pytest.mark.unit


@functools.cache
def _structure_schema():
    return compile_json_schema("structure")


def test_multi_artefact_message_conforms() -> None:
    # Rich message: dataflows + DSDs + codelists + a concept scheme.
    msg = make_message(n_dataflows=3, n_dims=3, codes_per_dim=5)
    assert_valid_sdmx_json(serialize(msg, version="json"), _structure_schema())


def test_codelist_with_annotations_parents_descriptions_conforms() -> None:
    # Exercises the shapes the DTO foundation owns: multilingual name/description
    # (name+names), parent codes, and annotations (text/texts + links[rel=self]).
    cl = Codelist(
        id="CL_FREQ",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Frequency", "fr": "Fréquence"},
        description={"en": "Frequency codes"},
        codes=[
            Code(
                id="A",
                name={"en": "Annual"},
                annotations=[Annotation(id="a1", title="T", type="NOTE", urls="https://example/y", text={"en": "n"})],
            ),
            Code(id="Q", name={"en": "Quarterly"}, description={"en": "three months"}, parent_id="A"),
        ],
    )
    assert_valid_sdmx_json(serialize(StructureMessage(codelists=[cl]), version="json"), _structure_schema())


def test_codelist_extension_conforms() -> None:
    # The last dict island typed in #186: CodelistExtension with a member
    # selection exercising both anyOf shapes (bare string + object) and the
    # bool/"excluderoot" cascade — must validate against the structure schema.
    from sdmxlib.model.codelist import (
        CascadeSelection,
        Code,
        Codelist,
        CodelistExtension,
        CodeSelection,
        InclusionMode,
        MemberValue,
    )
    from sdmxlib.model.ref import Ref

    base = Codelist.parse_urn("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_BASE(1.0)")
    cl = Codelist(
        id="CL_EXT",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Extending"},
        codes=[Code(id="X", name={"en": "X"})],
        extensions=(
            CodelistExtension(
                codelist=Ref[Codelist].unresolved(base),
                prefix="P_",
                selection=CodeSelection(
                    mode=InclusionMode.INCLUDE,
                    members=(
                        MemberValue(value="A"),
                        MemberValue(value="B", cascade=CascadeSelection.INCLUDE_DESCENDANTS),
                        MemberValue(value="D", cascade=CascadeSelection.EXCLUDE_ROOT),
                    ),
                ),
            ),
        ),
    )
    assert_valid_sdmx_json(serialize(StructureMessage(codelists=[cl]), version="json"), _structure_schema())


# ── Metadata message ─────────────────────────────────────────────────────────
# The metadata writer is a sibling msgspec DTO layer (#182). It must emit output
# valid against the metadata schema, whose $schema differs from structure. Note
# the metadata schema's VersionType is strict SemVer (3-part), unlike the
# lenient structure VersionType — so these fixtures use "1.0.0".


@functools.cache
def _metadata_schema():
    return compile_json_schema("metadata")


def _mdf_urn(id_: str) -> SdmxUrn[Any]:
    return SdmxUrn.parse(f"urn:sdmx:org.sdmx.infomodel.metadatastructure.Metadataflow=ESTAT:{id_}(1.0.0)")


def test_metadata_set_recursive_attributes_conform() -> None:
    # Recursive attribute tree with every value kind: string scalar, integer
    # scalar, and a localisedText (multilingual) leaf — plus name/names.
    ms = MetadataSet(
        id="MS1",
        maintainer="ESTAT",
        version="1.0.0",
        structure=_mdf_urn("ESMS"),
        name={"en": "Set One", "fr": "Ensemble Un"},
        targets=[SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.categoryscheme.Category=ESTAT:CS(1.0.0).CAT")],
        attributes=[
            ReportedAttribute(
                id="CONTACT",
                children=[
                    ReportedAttribute(id="EMAIL", value="a@b.c"),
                    ReportedAttribute(id="POSTAL_CODE", value=2721),
                    ReportedAttribute(id="NOTE", value={"en": "hi", "fr": "salut"}),
                ],
            )
        ],
    )
    msg = MetadataMessage(metadata_sets=ArtefactList([ms]))
    assert_valid_sdmx_json(write_metadata_json(msg), _metadata_schema())


def test_metadata_set_with_mpa_ref_conforms() -> None:
    # A set reported against a MetadataProvisionAgreement (#187) must emit the
    # conformant `metadataProvisionAgreement` key and validate.
    from sdmxlib.model.metadata_provision import MetadataProvisionAgreement

    mpa_urn = MetadataProvisionAgreement.parse_urn(
        "urn:sdmx:org.sdmx.infomodel.registry.MetadataProvisionAgreement=ESTAT:MPA(1.0.0)"
    )
    ms = MetadataSet(
        id="MS2",
        maintainer="ESTAT",
        version="1.0.0",
        structure=mpa_urn,
        name={"en": "Via MPA"},
        targets=[SdmxUrn.parse("urn:sdmx:org.sdmx.infomodel.categoryscheme.Category=ESTAT:CS(1.0.0).CAT")],
        attributes=[ReportedAttribute(id="NOTE", value="hi")],
    )
    raw = write_metadata_json(MetadataMessage(metadata_sets=ArtefactList([ms])))
    assert b'"metadataProvisionAgreement"' in raw
    assert_valid_sdmx_json(raw, _metadata_schema())
