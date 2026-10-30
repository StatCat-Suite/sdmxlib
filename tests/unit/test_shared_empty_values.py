"""`InternationalString.EMPTY` / `Annotations.EMPTY` — and why sharing is safe (#377).

Both classes are documented immutable, and the storage readers now hand out a
single instance of each rather than building one per row. On the census bundle
bundle that removed 1,159,902 `InternationalString` constructions from one
codelist read — ~98% of its 585,675 annotations carry no text at all.

"Documented immutable" is what these tests turn into something checked. The
risk is specific and worth naming rather than gesturing at: a shared instance
makes any escape of the backing store a *process-wide* corruption instead of a
local one. `InternationalString` has two deliberate escapes —
`_backing()` and `_emit_pair()` — both documented read-only for the
caller and both on serialiser hot paths. So the contract is now load-bearing,
and the tests below are where it is stated.
"""

import pytest

from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.istring import InternationalString

pytestmark = pytest.mark.unit


# ── the sharing itself ────────────────────────────────────────────────────────


def test_of_an_empty_mapping_is_the_shared_empty():
    assert InternationalString.of({}) is InternationalString.EMPTY
    assert InternationalString.of({}) is InternationalString.of({})


def test_a_non_empty_mapping_is_never_the_shared_empty():
    assert InternationalString.of({"en": "x"}) is not InternationalString.EMPTY


def test_the_shared_empty_equals_a_freshly_built_one():
    """Sharing must not be observable through `==`, `bool` or `len`."""
    assert InternationalString() == InternationalString.EMPTY
    assert not InternationalString.EMPTY
    assert len(InternationalString.EMPTY) == 0
    assert InternationalString.EMPTY.langs == []
    assert hash(InternationalString.EMPTY) == hash(InternationalString())


def test_empty_annotations_is_shared_and_equals_a_fresh_one():
    assert Annotations() == Annotations.EMPTY
    assert not Annotations.EMPTY
    assert len(Annotations.EMPTY) == 0
    assert list(Annotations.EMPTY) == []


def test_a_subclass_does_not_receive_the_base_singleton():
    """`of` returns `EMPTY` only for this exact class.

    `EMPTY` is an `InternationalString`, so handing it to a subclass caller
    would be a type error no annotation can express — the return type says
    `InternationalString` and would be telling the truth while being wrong.
    """

    class Localised(InternationalString):
        __slots__ = ()

    assert Localised.of({}) is not InternationalString.EMPTY
    assert isinstance(Localised.of({}), Localised)


# ── the contract that makes sharing safe ──────────────────────────────────────


def test_istring_exposes_no_mutator():
    """A public mutator would make the shared instance a global variable.

    Checked by name rather than by attempting mutation, so a *newly added*
    `__setitem__` / `update` / `clear` fails here instead of silently making
    every empty istring in the process aliasable.
    """
    forbidden = {"__setitem__", "__delitem__", "update", "clear", "pop", "popitem", "setdefault"}
    assert not (forbidden & set(dir(InternationalString)))


def test_annotations_exposes_no_mutator():
    forbidden = {"__setitem__", "__delitem__", "append", "extend", "insert", "remove", "clear", "pop"}
    assert not (forbidden & set(dir(Annotations)))


def test_istring_has_no_dict_so_attributes_cannot_be_added():
    """`__slots__` with no `__dict__` — otherwise a caller could stash state on
    the shared instance and every other holder would see it."""
    assert not hasattr(InternationalString.EMPTY, "__dict__")
    with pytest.raises(AttributeError):
        InternationalString.EMPTY.whatever = 1  # pyrefly: ignore[missing-attribute]


def test_annotations_has_no_dict_so_attributes_cannot_be_added():
    assert not hasattr(Annotations.EMPTY, "__dict__")
    with pytest.raises(AttributeError):
        Annotations.EMPTY.whatever = 1  # pyrefly: ignore[missing-attribute]


def test_slicing_annotations_returns_a_new_object():
    """The one operation that looks like mutation is already a copy."""
    anns = Annotations([Annotation(id="a"), Annotation(id="b")])
    assert anns[0:1] is not anns
    assert len(Annotations.EMPTY[0:1]) == 0


def test_as_dict_copies_by_default():
    """The default must not hand out the backing store.

    `copy=False` deliberately does, for the serialise hot path — that is the
    documented escape this whole file exists to bound.
    """
    istr = InternationalString(en="x")
    assert istr.as_dict() is not istr.as_dict()
    assert istr._backing() is istr._backing()


def test_the_shared_empty_hands_out_nothing_worth_mutating():
    """Both escapes yield an empty map or None, so the blast radius is nil.

    This is the reassuring half of the risk above: `_emit_pair()` on the empty
    istring returns `(None, None)` rather than the backing dict, and
    `_backing()` returns a dict with nothing in it to corrupt in
    place. A caller would have to *add* a key to do damage, which no serialiser
    does — but if one ever did, `test_istring_exposes_no_mutator` is not the
    test that catches it, so it is written down here instead of assumed.
    """
    assert InternationalString.EMPTY._emit_pair() == (None, None)
    assert InternationalString.EMPTY._backing() == {}
