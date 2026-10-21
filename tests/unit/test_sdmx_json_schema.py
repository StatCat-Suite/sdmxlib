"""Smoke tests for the SDMX-JSON 2.1 schema validation infrastructure.

The compile helper + validator are exercised against the upstream
SDMX-TWG sample. Wiring of validation across the writer test matrix
is deferred until the JSON writer is brought into conformance — see
the milestone tracking issue.
"""

import json
from pathlib import Path

import pytest

from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema

pytestmark = pytest.mark.unit

_SAMPLES_DIR = Path(__file__).parent.parent / "schemas" / "sdmx_json" / "2.1" / "samples"


def test_structure_schema_compiles() -> None:
    """The vendored SDMX-JSON 2.1 structure schema parses and is itself valid
    against its meta-schema (draft 2019-09)."""
    validator = compile_json_schema("structure")
    assert validator is not None


def test_metadata_schema_compiles() -> None:
    validator = compile_json_schema("metadata")
    assert validator is not None


def test_data_schema_compiles() -> None:
    validator = compile_json_schema("data")
    assert validator is not None


@pytest.mark.parametrize("version", ["1.0", "2.0"])
def test_legacy_data_schema_compiles(version: str) -> None:
    """SDMX-JSON 1.0 and 2.0 data schemas — older drafts, vendored for the
    streaming data-message writer. Only ``data`` is vendored at these
    versions; ``structure`` / ``metadata`` raise ``ValueError``."""
    validator = compile_json_schema("data", version=version)
    assert validator is not None


#: What the upstream samples get wrong against their own schema (#496): the
#: 2.0 EXR samples' ``prepared`` has no zone, which the schema requires. Seen
#: only once formats were checked; exact, so a refreshed sample shows up here.
_UPSTREAM_SAMPLE_DEFECTS: dict[tuple[str, str], list[str]] = {
    ("2.0", "exr-flat.json"): ["/meta/prepared: '2018-03-11T14:30:47' is not a 'date-time'"],
    ("2.0", "exr-time-series.json"): ["/meta/prepared: '2018-03-11T14:30:47' is not a 'date-time'"],
}


@pytest.mark.parametrize(
    ("version", "sample"),
    [
        ("1.0", "exr-flat.json"),
        ("1.0", "exr-time-series.json"),
        ("2.0", "exr-flat.json"),
        ("2.0", "exr-time-series.json"),
    ],
)
def test_vendored_sample_validates(version: str, sample: str) -> None:
    """Upstream samples validate against their own schema, but for their recorded defects.

    Sanity-checks that the vendor + validator pipeline is wired.
    """
    validator = compile_json_schema("data", version=version)
    path = Path(__file__).parent.parent / "schemas" / "sdmx_json" / version / "samples" / sample
    errors = sorted(validator.iter_errors(json.loads(path.read_text())), key=lambda e: list(e.absolute_path))
    found = [f"/{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in errors]
    assert found == _UPSTREAM_SAMPLE_DEFECTS.get((version, sample), [])


def test_unavailable_kind_for_legacy_version() -> None:
    # 1.0 vendors data and (since #433) structure, not metadata.
    with pytest.raises(ValueError, match="does not vendor"):
        compile_json_schema("metadata", version="1.0")


_XML30_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "sdmx_ml30"

_CONFORMANT_XML30_FIXTURES = [
    "minimal",
    "with_groups",
    "with_categories",
    "complete",
    "with_codelist_extension",
    "with_hierarchy_association",
    "with_multiple_hierarchies",
    "with_metadata",
    "with_representation_map_variants",
]


@pytest.mark.parametrize("fixture", _CONFORMANT_XML30_FIXTURES)
def test_writer_output_validates_against_v2_1_schema(fixture: str) -> None:
    """Round-trip: parse XML 3.0 fixture → write SDMX-JSON → schema-validate.

    Covers every artefact type modelled today. Brought into conformance
    in MR !94 (simple maintainables, name/names split, items → named
    arrays) and this MR closing #129 (DSD components, DataConstraint,
    StructureSets distribution, RepresentationMap shape, MSD maxOccurs).
    """
    from sdmxlib.formats.sdmx_json.writer import write as write_json
    from sdmxlib.formats.sdmx_ml30.reader import read as read30

    src = _XML30_FIXTURES_DIR / f"{fixture}.xml"
    out = write_json(read30(src.read_bytes()))
    assert_valid_sdmx_json(out, compile_json_schema("structure"))


def test_hierarchy_with_formal_levels_json_validates() -> None:
    """3.1 formal Levels aren't representable in the SDMX-ML 3.0 XSD, so
    none of the on-disk 3.0 fixtures cover this branch. Build a Hierarchy
    with Levels in code, serialise to JSON, and validate against the
    SDMX-JSON 2.1 schema's HierarchyType.levels variant.

    Each HierarchicalCode must carry an underlying ``code`` Ref — the
    JSON schema requires it (whereas our model treats the ref as
    optional).
    """
    from sdmxlib.formats.sdmx_json.writer import write as write_json
    from sdmxlib.model.codelist import Code
    from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, Level
    from sdmxlib.model.message import StructureMessage
    from sdmxlib.model.ref import Ref
    from sdmxlib.model.urn import SdmxUrn

    def _code_ref(item_id: str) -> Ref[Code]:
        urn: SdmxUrn[Code] = SdmxUrn(
            package="codelist",
            artefact_class="Code",
            agency="TEST",
            id="CL_GEO",
            version="1.0",
            item_id=item_id,
        )
        return Ref[Code].unresolved(urn)

    continent = Level(id="Continent", name={"en": "Continent"})
    country = Level(id="Country", name={"en": "Country"})
    h = Hierarchy(
        id="H_GEO_LEVELS",
        maintainer="TEST",
        version="1.0",
        name={"en": "Levels-bearing geography"},
        has_formal_levels=True,
        levels=[continent, country],
        tree=[
            HierarchicalCode(
                id="EUROPE",
                code=_code_ref("EUROPE"),
                level=continent,
                children=[
                    HierarchicalCode(
                        id="FR",
                        code=_code_ref("FR"),
                        level=country,
                    ),
                ],
            ),
        ],
    )
    out = write_json(StructureMessage(hierarchies=[h]))
    assert_valid_sdmx_json(out, compile_json_schema("structure"))


def test_official_sample_validates() -> None:
    """The upstream SDMX-TWG constructed sample
    (https://github.com/sdmx-twg/sdmx-json/blob/v2.1.0/structure-message/samples/constructed-sample.json)
    validates cleanly. Pins the validator to a known-good baseline so any
    regression in the support helper is caught."""
    sample_path = _SAMPLES_DIR / "structure-constructed-sample.json"
    if not sample_path.exists():
        pytest.skip(f"upstream sample not vendored at {sample_path}")
    validator = compile_json_schema("structure")
    assert_valid_sdmx_json(json.loads(sample_path.read_text()), validator)
