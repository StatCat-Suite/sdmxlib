"""Which classes each target format can reference, read out of the vendored schemas (#430).

The source of truth `model/_referenceable.py`'s constants are checked against.
Test-only, like the schemas it reads.
"""

import functools
import json
import re

from lxml import etree

from tests.support import repo

_XSD = "{http://www.w3.org/2001/XMLSchema}"
_SCHEMAS = repo.ROOT / "tests" / "schemas"


def _simple_type(folder: str, name: str) -> etree._Element:
    for path in sorted((_SCHEMAS / folder).glob("*.xsd")):
        found = etree.parse(str(path)).find(f".//{_XSD}simpleType[@name='{name}']")
        if found is not None:
            return found
    msg = f"{name} is gone from {folder}: has the schema been refreshed?"
    raise LookupError(msg)


def _enumeration(folder: str, name: str) -> frozenset[str]:
    return frozenset(str(e.get("value")) for e in _simple_type(folder, name).iter(f"{_XSD}enumeration"))


def _xsd_pairs(folder: str) -> frozenset[tuple[str, str]]:
    """`UrnClassesPart`: one ``.+\\.<package>\\.<Class>=.+`` pattern per class."""
    pairs = set()
    for pattern in _simple_type(folder, "UrnClassesPart").iter(f"{_XSD}pattern"):
        found = re.search(r"\\\.(\w+)\\\.(\w+)=", str(pattern.get("value")))
        if found:
            pairs.add((found.group(1), found.group(2)))
    return frozenset(pairs)


def _json_pairs(version: str) -> frozenset[tuple[str, str]]:
    """The generic ``urn`` pattern: ``(package\\.((A)|(B)|…))`` alternations."""
    schema = json.loads((_SCHEMAS / "sdmx_json" / version / "sdmx-json-structure-schema.json").read_text())
    pattern = str((schema.get("$defs") or schema["definitions"])["urn"]["pattern"])
    pairs = set()
    for package, body in re.findall(r"\((\w+)\\\.\(((?:\(\w+\)\|?)+)\)\)", pattern):
        pairs.update((package, cls) for cls in re.findall(r"\((\w+)\)", body))
    return frozenset(pairs)


@functools.cache
def from_schemas() -> dict[str, object]:
    """Per target: 2.1's class and package enumerations, and the URN pairs for the rest."""
    return {
        "2.1_classes": _enumeration("sdmx_ml21", "ObjectTypeCodelistType"),
        "2.1_packages": _enumeration("sdmx_ml21", "PackageTypeCodelistType"),
        "3.0": _xsd_pairs("sdmx_ml30"),
        "3.1": _xsd_pairs("sdmx_ml31"),
        # Both JSON versions, intersected: the writer's output is validated
        # against either, so a class must be legal in both.
        "json": _json_pairs("2.0") & _json_pairs("2.1"),
    }
