"""Writers state no attribute at its schema default, except identity (#503).

Output has one spelling per meaning, the minimal one: an optional attribute
at the default its format and type declare is left out. Two exceptions are
always written: identity (an artefact's and a reference's id, agency,
version, parent version, class and package), so a consumer need not know the
schema's defaults to know what is named; and what a format requires.

SDMX-ML is checked against the vendored XSDs, the default read off each
element's type in context (`tests.support.xsd_defaults`). SDMX-JSON's
defaults are context-free but for ``usage``, so they are a table, which
`test_the_json_table_is_the_schemas` holds to the schema.
"""

import json
from collections.abc import Iterator

import pytest

from sdmxlib.formats.sdmx_json import writer as json_writer
from tests.support import maximal, shapes
from tests.support.repo import ROOT
from tests.support.xsd import SchemaVersion
from tests.support.xsd_defaults import IDENTITY, stated_defaults
from tests.unit.test_element_census import _documents

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1"])
def test_sdmx_ml_states_no_attribute_at_its_default(version: SchemaVersion) -> None:
    stated = sorted(
        {
            (path.rsplit("/", 2)[-2] + "/" + path.rsplit("/", 1)[-1], attribute, value)
            for document in _documents(version)
            for path, attribute, value in stated_defaults(document, version)
            if attribute not in IDENTITY
        }
    )
    assert not stated, f"SDMX-ML {version} states defaults: {stated}"


def test_the_sdmx_ml_check_sees_a_stated_default() -> None:
    """The discrimination check: a default put back is found."""
    (document, *_) = _documents("3.0")
    region = next(document.iter("{*}CubeRegion"))
    region.set("include", "true")
    assert ("include", "true") in {(a, v) for _, a, v in stated_defaults(document, "3.0")}


# ── SDMX-JSON ─────────────────────────────────────────────────────────────────

#: Property → its default, wherever SDMX-JSON 2.0 declares one, but `usage`.
_JSON_DEFAULTS: dict[str, object] = {
    "cascadeValues": False,
    "include": True,
    "isInclusive": True,
    "minOccurs": 1,
    "maxOccurs": 1,
}
#: `usage` defaults by the list that holds the component: optional on an
#: attribute or a measure, mandatory on a reporting-year start day.
_JSON_USAGE: dict[str, str] = {"attributes": "optional", "measures": "optional"}


def _stated_json(node: object, holder: str = "") -> Iterator[tuple[str, object]]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _JSON_DEFAULTS and value == _JSON_DEFAULTS[key] and type(value) is type(_JSON_DEFAULTS[key]):
                yield key, value
            if key == "usage" and _JSON_USAGE.get(holder) == value:
                yield f"{holder}[].usage", value
            yield from _stated_json(value, key)
    elif isinstance(node, list):
        for item in node:
            yield from _stated_json(item, holder)


def _json_documents() -> list[object]:
    return [json.loads(json_writer.write(message)) for message in (maximal.message(), shapes.message())]


def test_sdmx_json_states_no_property_at_its_default() -> None:
    stated = sorted({(key, json.dumps(value)) for doc in _json_documents() for key, value in _stated_json(doc)})
    assert not stated, f"SDMX-JSON states defaults: {stated}"


def test_the_sdmx_json_check_sees_a_stated_default() -> None:
    assert list(_stated_json({"x": [{"include": True}], "attributes": [{"usage": "optional"}]})) == [
        ("include", True),
        ("attributes[].usage", "optional"),
    ]


def test_the_json_table_is_the_schemas() -> None:
    """Every property SDMX-JSON 2.0 gives a default is one the check knows, so a new one is not missed."""
    schema = json.loads((ROOT / "tests/schemas/sdmx_json/2.0/sdmx-json-structure-schema.json").read_text())
    declared: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                declared.update(k for k, v in properties.items() if isinstance(v, dict) and "default" in v)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    assert declared == {*_JSON_DEFAULTS, "usage"}
