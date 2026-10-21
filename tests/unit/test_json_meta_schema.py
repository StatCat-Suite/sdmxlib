"""The structure JSON writer names its schema, so other readers recognise it (#465).

`meta.schema` is optional in SDMX-JSON 2.0, and the writer left it out. pysdmx
recognises an SDMX-JSON message by that URL alone, so it refused every
structure message sdmxlib wrote ("Cannot parse input as SDMX-JSON"), while
reading BIS's, which carries it.
"""

import json

import pytest

from sdmxlib import formats
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.message import StructureMessage
from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

#: pysdmx reads this much of a document to recognise it (`pysdmx.io.input_processor`).
_PYSDMX_WINDOW = 350


def _written() -> bytes:
    codelist = Codelist(
        id="CL",
        maintainer="AG",
        version="1.0",
        name={"en": "Codes"},
        codes=[Code(id="A", name={"en": "A"}), Code(id="B", name={"en": "B"}, parent_id="A")],
    )
    return formats.serialize(StructureMessage(codelists=[codelist]), version="json")


def test_meta_names_the_structure_schema_where_sdmx_publishes_it() -> None:
    """json.sdmx.org's copy, as FMR writes: the schema's own `$id` no longer resolves.

    The copy there declares that `$id` unchanged, so it is the same schema as
    the one vendored here.
    """
    schema = json.loads((ROOT / "tests/schemas/sdmx_json/2.0/sdmx-json-structure-schema.json").read_text())
    assert json.loads(_written())["meta"]["schema"] == "https://json.sdmx.org/2.0.0/sdmx-json-structure-schema.json"
    assert schema["$id"].endswith("/2.0.0/sdmx-json-structure-schema.json")


def test_the_schema_comes_first_enough_for_a_reader_that_looks_only_at_the_start() -> None:
    assert b"2.0.0/sdmx-json-structure-schema.json" in _written()[:_PYSDMX_WINDOW]


def test_pysdmx_reads_what_sdmxlib_writes() -> None:
    """Interop, when pysdmx is installed (the ``bench-compete`` group)."""
    pysdmx_io = pytest.importorskip("pysdmx.io", reason="install via uv sync --group bench-compete")
    message = pysdmx_io.read_sdmx(_written().decode())
    (codelist,) = message.get_codelists()
    assert [code.id for code in codelist.items] == ["A", "B"]
