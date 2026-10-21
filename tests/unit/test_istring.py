"""Tests for InternationalString."""

import pytest

from sdmxlib.model.istring import InternationalString

pytestmark = pytest.mark.unit


def test_construction_and_access():
    s = InternationalString(en="Annual", fr="Annuel")
    assert s["en"] == "Annual"
    assert s["fr"] == "Annuel"


def test_getitem_missing_raises():
    s = InternationalString(en="Annual")
    with pytest.raises(KeyError):
        _ = s["de"]


def test_get_single_lang():
    s = InternationalString(en="Annual")
    assert s.get("en") == "Annual"
    assert s.get("de") is None


def test_get_priority_fallback():
    s = InternationalString(en="Annual", fr="Annuel")
    assert s.get("de", "fr", "en") == "Annuel"


def test_get_all_missing_returns_none():
    s = InternationalString(en="Annual")
    assert s.get("de", "fr") is None


def test_contains():
    s = InternationalString(en="Annual", fr="Annuel")
    assert "en" in s
    assert "de" not in s


def test_len():
    s = InternationalString(en="A", fr="B", de="C")
    assert len(s) == 3


def test_bool_empty():
    s = InternationalString()
    assert not s


def test_bool_nonempty():
    s = InternationalString(en="A")
    assert s


def test_langs():
    s = InternationalString(en="A", fr="B")
    assert set(s.langs) == {"en", "fr"}


def test_of_factory():
    s = InternationalString.of({"en": "Annual", "fr": "Annuel"})
    assert s["en"] == "Annual"


def test_equality():
    a = InternationalString(en="Annual")
    b = InternationalString(en="Annual")
    assert a == b


def test_inequality():
    a = InternationalString(en="Annual")
    b = InternationalString(en="Quarterly")
    assert a != b


def test_hash_equal_objects():
    a = InternationalString(en="Annual")
    b = InternationalString(en="Annual")
    assert hash(a) == hash(b)


def test_repr():
    s = InternationalString(en="Annual")
    assert "en" in repr(s)
    assert "Annual" in repr(s)


def test_iter():
    s = InternationalString(en="A", fr="B")
    assert set(s) == {"en", "fr"}
