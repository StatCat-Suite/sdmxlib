"""A constraint's key sets keep whether they include or exclude their keys (#479).

``DataKeySet/@isIncluded`` is required in every format, and ``false`` rules
its keys out. Read into one flat tuple, an excluded set came back as allowed:
the opposite of the document, with nothing lost, refused or assumed.
"""

import pytest
from lxml import etree

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.sdmx_ml._allowed_children import NAMESPACES
from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model.constraint import DataConstraint, DataKey, DataKeySet
from sdmxlib.model.message import StructureMessage
from tests.support.unchecked import refusal

pytestmark = pytest.mark.unit

_SETS = (
    DataKeySet(is_included=True, keys=(DataKey(values=(("FREQ", "A"),)),)),
    DataKeySet(is_included=False, keys=(DataKey(values=(("FREQ", "M"),)), DataKey(values=(("FREQ", "Q"),)))),
)
_CONSTRAINT = DataConstraint(id="CC", maintainer="AG", name={"en": "c"}, data_key_sets=_SETS)


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1", "json"])
def test_an_excluded_key_set_reads_back_excluded(version: str) -> None:
    written = serialize(StructureMessage(constraints=[_CONSTRAINT]), version=version)

    back = parse(written)

    assert isinstance(back, StructureMessage)
    (constraint,) = back.constraints
    assert [s.is_included for s in constraint.data_key_sets] == [True, False]
    assert constraint.data_key_sets == _SETS


def test_an_excluded_key_set_is_stored_excluded() -> None:
    with LocalRegistry(":memory:") as registry:
        registry.add(_CONSTRAINT)
        back = registry.get(_CONSTRAINT.urn)
    assert isinstance(back, DataConstraint)
    assert back.data_key_sets == _SETS


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1"])
def test_a_key_set_without_is_included_refuses_its_constraint(version: str) -> None:
    """Required by the schema, with no default to fall back on (#474)."""
    doc = etree.fromstring(serialize(StructureMessage(constraints=[_CONSTRAINT]), version=version))
    (first, *_) = doc.iterfind(".//str:DataKeySet", namespaces=NAMESPACES[version])
    del first.attrib["isIncluded"]

    back = parse(etree.tostring(doc), strict=False)

    assert isinstance(back, StructureMessage)
    (refusal,) = back.refused
    assert "@isIncluded" in refusal.reason


def test_a_key_set_with_no_keys_is_refused() -> None:
    """Every format requires a key in a set, so an empty one has no form anywhere."""
    (issue,) = refusal(
        lambda: DataConstraint(
            id="CC", maintainer="AG", name={"en": "c"}, data_key_sets=(DataKeySet(is_included=False),)
        )
    )
    assert issue.path.endswith("data_key_sets[0].keys")
