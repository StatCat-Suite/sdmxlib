"""SDMX-ML 3.0 Hierarchy Level ``CodingFormat`` round-trip (#159 Table B).

The 3.0 writer emitted Level id/name/description but dropped
``coding_format``; the reader never parsed ``<str:CodingFormat>``. Both
now round-trip. CodingTextFormatType's ``textType`` is restricted to
{Alpha, AlphaNumeric, Numeric} and prohibits decimals / isMultiLingual.
"""

import pytest

from sdmxlib.formats.sdmx_ml30 import reader as r30
from sdmxlib.formats.sdmx_ml30 import writer as w30
from sdmxlib.model.codelist import Code
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, Level
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from sdmxlib.model.urn import SdmxUrn
from tests.support.roundtrip import structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit


def test_ml30_level_coding_format_round_trips_and_is_xsd_valid() -> None:
    code_ref = Ref[Code].unresolved(
        SdmxUrn(package="codelist", artefact_class="Code", agency="T", id="CL", version="1.0", item_id="A")
    )
    lvl = Level(
        id="L1",
        name={"en": "Level 1"},
        coding_format=Facet(type="AlphaNumeric", min_length=1, max_length=3, pattern="[A-Z0-9]+"),
    )
    h = Hierarchy(
        id="H",
        maintainer="T",
        version="1.0",
        name={"en": "h"},
        has_formal_levels=True,
        levels=[lvl],
        tree=[HierarchicalCode(id="R", code=code_ref, level=lvl)],
    )
    written = w30.write(StructureMessage(hierarchies=[h]))
    assert_valid_sdmx_ml(written, structure_schema("3.0"))
    cf = r30.read(written).hierarchies["H"].levels["L1"].coding_format
    assert isinstance(cf, Facet)
    assert cf.type == "AlphaNumeric"
    assert cf.min_length == 1
    assert cf.max_length == 3
    assert cf.pattern == "[A-Z0-9]+"
