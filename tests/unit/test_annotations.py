"""Tests for Annotation and Annotations."""

import pytest

from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.istring import InternationalString

pytestmark = pytest.mark.unit


def _make_annotations() -> Annotations:
    return Annotations(
        [
            Annotation(id="a1", type="DEPRECATED", title="Old"),
            Annotation(id="a2", type="CONTACT", title="John"),
            Annotation(id="a3", type="CONTACT", title="Jane"),
            Annotation(id="a4", title="No type"),
        ]
    )


def test_empty_annotations():
    a = Annotations()
    assert len(a) == 0
    assert not a


def test_len():
    a = _make_annotations()
    assert len(a) == 4


def test_bool_nonempty():
    assert _make_annotations()


def test_iteration():
    a = _make_annotations()
    ids = [ann.id for ann in a]
    assert ids == ["a1", "a2", "a3", "a4"]


def test_getitem():
    a = _make_annotations()
    assert a[0].id == "a1"
    assert a[-1].id == "a4"


def test_contains_by_type():
    a = _make_annotations()
    assert "DEPRECATED" in a
    assert "CONTACT" in a
    assert "MISSING" not in a


def test_by_type():
    a = _make_annotations()
    contacts = a.by_type("CONTACT")
    assert len(contacts) == 2
    assert all(c.type == "CONTACT" for c in contacts)


def test_by_type_empty():
    a = _make_annotations()
    assert a.by_type("NONEXISTENT") == []


def test_first():
    a = _make_annotations()
    first = a.first("CONTACT")
    assert first is not None
    assert first.title == "John"


def test_first_none():
    a = _make_annotations()
    assert a.first("MISSING") is None


def test_types():
    a = _make_annotations()
    types = a.types
    assert "DEPRECATED" in types
    assert "CONTACT" in types
    # deduplicated — CONTACT appears once
    assert types.count("CONTACT") == 1


def test_annotation_with_text():
    ann = Annotation(
        type="NOTE",
        text=InternationalString(en="Some note"),
    )
    assert ann.text["en"].text == "Some note"


def test_equality():
    a = Annotations([Annotation(id="x")])
    b = Annotations([Annotation(id="x")])
    assert a == b
