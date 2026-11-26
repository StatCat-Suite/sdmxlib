"""`sl.diff`: normalised field paths where two model objects disagree (GH-401).

Each test is one rule from `sdmxlib.model.diff`'s docstring.
"""

import importlib
import pkgutil

import attrs
import pytest

import sdmxlib as sl
import sdmxlib.model
from sdmxlib.model._refshape import UNORDERED
from tests.support import repo

pytestmark = pytest.mark.unit


def _codelist() -> sl.Codelist:
    return sl.Codelist(
        id="CL",
        maintainer="AG",
        version="1.0.0",
        name={"en": "codes"},
        codes=[sl.Code(id="A", name={"en": "a"}), sl.Code(id="B", name={"en": "b"})],
    )


def test_equal_objects_have_no_differences():
    assert sl.diff(_codelist(), _codelist()) == {}


def test_a_path_names_fields_not_items():
    renamed = attrs.evolve(_codelist(), codes=[sl.Code(id="A", name={"en": "x"}), sl.Code(id="B", name={"en": "y"})])
    assert sl.diff(_codelist(), renamed) == {"Codelist.codes[].name (text)": "'a' → 'x'"}


def test_items_only_one_side_holds_are_named_by_side():
    fewer = attrs.evolve(_codelist(), codes=[sl.Code(id="A", name={"en": "a"})])
    assert sl.diff(_codelist(), fewer) == {"Codelist.codes[] (only left)": "['B'] → 1"}


def test_a_language_difference_is_reported_as_languages():
    assert sl.diff(_codelist(), attrs.evolve(_codelist(), name={"en": "codes", "fr": "codes"})) == {
        "Codelist.name (languages)": "['en'] → ['en', 'fr']"
    }


def test_a_ref_is_equal_by_urn():
    urn = sl.Codelist.urn_for(agency="AG", id="CL", version="1.0.0")
    one = sl.Dimension(id="D", representation=sl.Ref[sl.Codelist].unresolved(urn))
    other = sl.Dimension(id="D", representation=sl.Ref.to(_codelist()))
    assert sl.diff(one, other) == {}
    moved = sl.Dimension(id="D", representation=sl.Ref[sl.Codelist].unresolved(attrs.evolve(urn, version="2.0.0")))
    assert list(sl.diff(one, moved)) == ["Dimension.representation"]


def test_a_type_difference_is_reported_as_type():
    assert sl.diff(attrs.evolve(_codelist(), uri=None), attrs.evolve(_codelist(), uri="https://example.org")) == {
        "Codelist.uri (type)": "'NoneType' → 'str'"
    }


def _attribute(related: list[str] | None) -> sl.Attribute:
    return sl.Attribute(id="A", attachment=sl.AttachmentLevel.SERIES, related_dimensions=related)


def test_related_dimensions_are_compared_as_a_set():
    assert sl.diff(_attribute(["D2", "D1"]), _attribute(["D1", "D2"])) == {}
    assert sl.diff(_attribute(["D1"]), _attribute(["D1", "D2"])) == {
        "Attribute.related_dimensions (set)": "['D1'] → ['D1', 'D2']"
    }


def test_an_ordered_sequence_is_compared_in_order():
    """Discrimination: order still counts outside a field marked unordered."""
    one = sl.Hierarchy(id="H", maintainer="AG", name={"en": "h"})
    assert sl.diff([1, 2], [2, 1]) == {"list[]": "1 → 2"}
    assert sl.diff(one, one) == {}


def test_the_unordered_fields_are_the_two_attribute_relationships():
    """Exact, so the marker cannot spread to a field whose order matters without review."""
    marked = set()
    for info in pkgutil.walk_packages(sdmxlib.model.__path__, "sdmxlib.model."):
        module = importlib.import_module(info.name)
        for value in vars(module).values():
            if isinstance(value, type) and attrs.has(value) and value.__module__ == info.name:
                marked |= {f"{value.__name__}.{f.name}" for f in attrs.fields(value) if f.metadata.get(UNORDERED)}
    assert marked == {"Attribute.related_dimensions", "MetadataAttributeUsage.related_dimensions"}


def test_fmr_serving_related_dimensions_in_dsd_order_is_no_difference():
    """GH-401's case: FMR 12.5.0's read-back of a DSD whose attribute listed D2 before D1."""
    case = repo.ROOT / "tests" / "integration" / "fmr" / "12.5.0" / "registry-rules"
    sent = sl.parse((case / "base.xml").read_bytes())
    served = sl.parse((case / "back" / "AG-ml30.xml").read_bytes())
    assert isinstance(sent, sl.StructureMessage)
    assert isinstance(served, sl.StructureMessage)
    one, other = sent.data_structures["AG:DSD"], served.data_structures["AG:DSD"]
    assert one.attributes["A1"].related_dimensions != other.attributes["A1"].related_dimensions
    assert not any("related_dimensions" in path for path in sl.diff(one, other))


def test_the_example_is_the_first_disagreement_in_left_order():
    """Not a set's order: the same inputs give the same example under any hash seed."""
    renamed = attrs.evolve(_codelist(), codes=[sl.Code(id="B", name={"en": "y"}), sl.Code(id="A", name={"en": "x"})])
    assert sl.diff(_codelist(), renamed)["Codelist.codes[].name (text)"] == "'a' → 'x'"
