"""`canonical_json`: the defined text of a model object that a fingerprint hashes (GH-126).

Equal content must give equal bytes however it was built, and any change to
any field must change them. The goldens pin the text per artefact class; the
walk below proves every field of every object reaches the text, which no
golden comparison can, because a golden regenerated from a lossy encoder is
equally lossy.
"""

import enum
import json
import math
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import TypeIs

import attrs
import msgspec
import pytest

import sdmxlib as sl
from sdmxlib.bundle import CANONICAL_FORM, canonical_json
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.collections import ItemList
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from tests.support.canonical_corpus import GOLDEN, corpus, golden

pytestmark = pytest.mark.unit


def _concrete_artefact_classes() -> set[str]:
    def below(cls: type) -> set[type]:
        found: set[type] = set()
        for sub in cls.__subclasses__():
            found |= {sub, *below(sub)}
        return found

    return {c.__name__ for c in below(MaintainableArtefact) if attrs.has(c) and not c.__name__.startswith("_")}


# ── goldens ───────────────────────────────────────────────────────────────


def test_the_corpus_holds_one_of_every_artefact_class() -> None:
    assert set(corpus()) == _concrete_artefact_classes()
    assert len(corpus()) == 30


def test_every_golden_belongs_to_a_class_and_every_class_has_one() -> None:
    assert sorted(p.stem for p in GOLDEN.glob("*.json")) == sorted(corpus())


@pytest.mark.parametrize("name", sorted(corpus()))
def test_the_text_is_the_golden(name: str) -> None:
    """Regenerate with ``uv run python -m tests.support.canonical_corpus`` after a
    deliberate change, and bump `CANONICAL_FORM` if the form itself changed."""
    assert golden(corpus()[name]) == (GOLDEN / f"{name}.json").read_bytes()


def test_the_form_is_versioned() -> None:
    assert CANONICAL_FORM == "1"


# ── every field reaches the text ──────────────────────────────────────────


def _is_attrs(value: object) -> TypeIs[attrs.AttrsInstance]:
    return not isinstance(value, type) and attrs.has(type(value))


def _complete(obj: object, plain: object, path: str) -> int:
    """Assert `plain` carries every field of `obj`, recursively; return the attrs objects seen."""
    match obj:
        case Ref():
            assert plain == str(obj.urn), path
            return 0
        case InternationalString():
            assert plain == obj.as_dict(), path
            return 0
        case Annotations() | ItemList() | list() | tuple():
            assert isinstance(plain, list), path
            items = list(obj)
            assert len(plain) == len(items), path
            return sum(_complete(item, p, f"{path}[{i}]") for i, (item, p) in enumerate(zip(items, plain, strict=True)))
        case set() | frozenset():
            assert isinstance(plain, list), path
            assert len(plain) == len(obj), path
            return sum(_complete_member(item, plain, f"{path}{{}}") for item in obj)
        case Mapping():
            assert isinstance(plain, dict), path
            assert set(plain) == set(obj), path
            return sum(_complete(obj[k], plain[k], f"{path}[{k!r}]") for k in obj)
        case _ if _is_attrs(obj):
            return _complete_object(obj, plain, path)
        case enum.Enum():
            assert plain == obj.name, path
            return 0
        case bool() | int() | float() | str() | None:
            assert (type(plain), plain) == (type(obj), obj), path
            return 0
        case datetime() | date():
            assert isinstance(plain, str), path
            assert plain[:10] == obj.isoformat()[:10] or isinstance(obj, datetime), path
            return 0
        case _:
            msg = f"{path}: the walk does not know {type(obj).__name__}"
            raise AssertionError(msg)


def _complete_member(item: object, plain: list[object], path: str) -> int:
    """`item` against whichever element of a set's array it encodes to, matched without the encoder."""
    for candidate in plain:
        try:
            return _complete(item, candidate, path)
        except AssertionError:
            continue
    msg = f"{path}: {item!r} matches no element of {plain!r}"
    raise AssertionError(msg)


def _complete_object(obj: attrs.AttrsInstance, plain: object, path: str) -> int:
    assert isinstance(plain, dict), path
    fields = [f for f in attrs.fields(type(obj)) if f.init]
    assert set(plain) == {"$class", *(f.name for f in fields)}, path
    assert plain["$class"] == type(obj).__name__, path
    seen = 1
    for f in fields:
        value: object = getattr(obj, f.name)
        if f.name == "maintainer" and isinstance(value, Agency):
            assert plain[f.name] == value.id, path
        else:
            seen += _complete(value, plain[f.name], f"{path}.{f.name}")
    return seen


@pytest.mark.parametrize("name", sorted(corpus()))
def test_every_field_of_every_object_reaches_the_text(name: str) -> None:
    """Every init field of every attrs object, `None`s included, appears under
    its own name: the 28 fields SDMX-JSON cannot carry among them. The object
    count is checked against the text, so the walk cannot skip a subtree."""
    artefact = corpus()[name]
    text = canonical_json(artefact)
    seen = _complete(artefact, json.loads(text), name)
    assert seen == text.count(b'"$class"')
    assert seen >= 3, "an artefact, its annotation and its annotation's URL at least"


# ── equal content, equal bytes ────────────────────────────────────────────


def test_two_builds_of_one_artefact_give_one_text() -> None:
    assert {n: canonical_json(a) for n, a in corpus().items()} == {n: canonical_json(a) for n, a in corpus().items()}


_BASE = sl.Codelist(id="CL", maintainer="AG", version="1.0", name={"en": "cl"})


def test_language_order_does_not_change_the_text() -> None:
    assert canonical_json(attrs.evolve(_BASE, name={"en": "a", "fr": "b"})) == canonical_json(
        attrs.evolve(_BASE, name={"fr": "b", "en": "a"})
    )


def test_one_instant_in_two_zones_gives_one_text() -> None:
    utc = datetime(2021, 5, 1, 12, tzinfo=UTC)
    east = utc.astimezone(timezone(timedelta(hours=5)))
    assert canonical_json(attrs.evolve(_BASE, valid_from=utc)) == canonical_json(attrs.evolve(_BASE, valid_from=east))
    assert b'"valid_from":"2021-05-01T12:00:00.000000Z"' in canonical_json(attrs.evolve(_BASE, valid_from=east))
    # The model's converter already holds it in UTC; the encoder converts what it is handed.
    assert canonical_json(_A(east)) == canonical_json(_A(utc)) == b'{"$class":"_A","x":"2021-05-01T12:00:00.000000Z"}'


def test_the_maintainer_is_its_id_however_much_of_the_agency_was_read() -> None:
    bare = attrs.evolve(_BASE, maintainer=Agency(id="AG"))
    described = attrs.evolve(_BASE, maintainer=Agency(id="AG", name={"en": "An agency"}, description={"en": "long"}))
    assert canonical_json(bare) == canonical_json(described)
    assert b'"maintainer":"AG"' in canonical_json(bare)


def test_a_ref_is_its_urn_resolved_or_not() -> None:
    resolved = sl.Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "f"}, structure=Ref.to(_dsd()))
    unresolved = sl.Dataflow(
        id="DF",
        maintainer="AG",
        version="1.0",
        name={"en": "f"},
        structure=Ref[sl.DataStructure].unresolved(_dsd().urn),
    )
    assert canonical_json(resolved) == canonical_json(unresolved)
    assert f'"structure":"{_dsd().urn}"'.encode() in canonical_json(resolved)
    assert canonical_json(resolved).count(b"DataStructure") == 1, "the URN, never the structure"


def _dsd() -> sl.DataStructure:
    return sl.DataStructure(id="DSD", maintainer="AG", version="1.0", name={"en": "d"})


# ── any change, other bytes ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "changed",
    [
        attrs.evolve(_BASE, legacy_is_final=True),  # SDMX 3.0 and SDMX-JSON drop it
        attrs.evolve(_BASE, uri="https://example.org/x"),
        attrs.evolve(_BASE, description={"en": "d"}),
        attrs.evolve(_BASE, version="1.0.0"),
        attrs.evolve(_BASE, is_partial=True),
        attrs.evolve(_BASE, links=(sl.Link(rel="alternate", url="https://example.org/a"),)),
        attrs.evolve(_BASE, codes=[sl.Code(id="A", name={"en": "a"})]),
    ],
    ids=["legacy_is_final", "uri", "description", "version", "is_partial", "links", "codes"],
)
def test_a_change_to_one_field_changes_the_text(changed: sl.Codelist) -> None:
    assert canonical_json(changed) != canonical_json(_BASE)


@attrs.frozen
class _A:
    x: object


@attrs.frozen
class _B:
    x: object


def test_two_classes_with_equal_fields_differ() -> None:
    assert canonical_json(_A(1)) == b'{"$class":"_A","x":1}'
    assert canonical_json(_A(1)) != canonical_json(_B(1))


@pytest.mark.parametrize(
    ("one", "other"),
    [(1, 1.0), (None, ""), (None, []), ("", []), (True, 1), ([1, 2], [2, 1]), ("1", 1)],
    ids=["int-float", "null-empty-string", "null-empty-list", "empty-string-list", "bool-int", "order", "str-int"],
)
def test_values_json_could_confuse_stay_apart(one: object, other: object) -> None:
    assert canonical_json(_A(one)) != canonical_json(_A(other))


def test_an_enum_is_its_member_name() -> None:
    assert canonical_json(_A(sl.ConstraintType.ALLOWED)) == b'{"$class":"_A","x":"ALLOWED"}'


class _Spelled(enum.StrEnum):
    LONG_NAME = "short"


def test_a_str_enum_is_its_member_name_too() -> None:
    """A `StrEnum` member is also a `str`, so a `str` case reached first would
    spell it by value. The model's one, `DataType`, has equal names and values,
    which is why this needs its own."""
    assert canonical_json(_A(_Spelled.LONG_NAME)) == b'{"$class":"_A","x":"LONG_NAME"}'


def test_a_set_is_sorted_by_canonical_text() -> None:
    assert canonical_json(_A(frozenset({"b", "a", "c"}))) == b'{"$class":"_A","x":["a","b","c"]}'


def test_a_date_and_a_mapping() -> None:
    assert canonical_json(_A({"b": date(2021, 1, 2), "a": None})) == b'{"$class":"_A","x":{"a":null,"b":"2021-01-02"}}'


# ── refusals ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_non_finite_float_is_refused(value: float) -> None:
    with pytest.raises(ValueError, match=r"_A\.x is .*canonical JSON cannot spell"):
        canonical_json(_A(value))


def test_a_naive_datetime_is_refused() -> None:
    with pytest.raises(ValueError, match=r"_A\.x is a naive datetime"):
        canonical_json(_A(datetime.fromisoformat("2021-01-01T00:00:00")))


@pytest.mark.parametrize("value", [b"bytes", object(), 1 + 2j])
def test_a_value_the_form_does_not_define_is_refused_naming_it(value: object) -> None:
    with pytest.raises(TypeError, match=rf"_A\.x holds a {type(value).__name__}, which the canonical form"):
        canonical_json(_A(value))


def test_a_mapping_with_a_key_that_is_not_text_is_refused() -> None:
    with pytest.raises(TypeError, match=r"_A\.x has a int key"):
        canonical_json(_A({1: "a"}))


def test_a_store_backed_codelist_is_refused(tmp_path: Path) -> None:
    with sl.LocalRegistry(tmp_path / "s.duckdb") as reg:
        reg.add(attrs.evolve(_BASE, codes=[sl.Code(id="A", name={"en": "a"})]))
        lazy = reg.get(_BASE.urn)
        assert lazy is not None
        with pytest.raises(TypeError, match="holds a LazyCodelist"):
            canonical_json(lazy)
        eager = reg.get(_BASE.urn, eager=True)
        assert eager is not None
        assert msgspec.json.decode(canonical_json(eager))["$class"] == "Codelist"
