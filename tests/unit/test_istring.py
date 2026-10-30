"""InternationalString as a mapping from language tag to `LocalisedString` (GH-309).

The stored map holds `str` texts; a lookup builds the `LocalisedString` it
returns. Errors follow `dict`: a malformed tag raises from every operation,
as an unhashable key does, and a well-formed tag that is not held is absent.
"""

import ast
import json
import os
import subprocess
import sys
from collections.abc import Callable, ItemsView, KeysView, Mapping, ValuesView
from pathlib import Path
from typing import cast

import msgspec
import polars as pl
import pytest

import sdmxlib as sl
import sdmxlib.polars as slpl
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.model import istring
from sdmxlib.model.istring import InternationalString, LocalisedString, MissingTagError
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema, is_valid_sdmx_json

pytestmark = pytest.mark.unit

_NAME = InternationalString(en="Annual", fr="Annuel")


def _ls(text: str, lang: str) -> LocalisedString:
    return LocalisedString(text, lang)


# ── lookups return LocalisedString ─────────────────────────────────────────


def test_a_lookup_returns_the_text_with_its_tag() -> None:
    assert _NAME["fr"] == _ls("Annuel", "fr")
    assert _NAME["FR"] == _ls("Annuel", "fr")
    assert _NAME["fr"].text == "Annuel"
    assert _NAME.get("en") == _ls("Annual", "en")
    assert _NAME.pick("de", "en") == _ls("Annual", "en")


def test_get_follows_mapping_get() -> None:
    assert _NAME.get("de") is None
    assert _NAME.get("de", "x") == "x"
    sentinel = object()
    assert _NAME.get("de", sentinel) is sentinel
    assert _NAME.get("en", "x") == _ls("Annual", "en")


def test_pick_falls_back_over_tags_and_needs_one() -> None:
    assert _NAME.pick("de", "fr-ca", "fr") == _ls("Annuel", "fr")
    assert _NAME.pick("de") is None
    with pytest.raises(TypeError):
        _NAME.pick()


def test_pick_stops_at_a_held_empty_text() -> None:
    s = InternationalString(fr="", en="Annual")
    assert s.pick("fr", "en") == _ls("", "fr")


# ── the D7 table: errors follow dict ───────────────────────────────────────


def _operations(s: InternationalString) -> dict[str, Callable[[object], object]]:
    keys = s.keys()
    items = s.items()
    return {
        "getitem": lambda tag: s[cast("str", tag)],
        "get": lambda tag: s.get(cast("str", tag)),
        "pick": lambda tag: s.pick(cast("str", tag)),
        "in": lambda tag: tag in s,
        "in keys": lambda tag: tag in keys,
        "in items": lambda tag: (tag, _ls("Annual", "en")) in items,
    }


@pytest.mark.parametrize("operation", list(_operations(_NAME)))
def test_a_malformed_tag_raises_invalid_tag_error(operation: str) -> None:
    call = _operations(_NAME)[operation]
    with pytest.raises(sl.InvalidTagError, match="fr_CA"):
        call("fr_CA")


@pytest.mark.parametrize("operation", list(_operations(_NAME)))
def test_a_non_str_tag_raises_type_error(operation: str) -> None:
    call = _operations(_NAME)[operation]
    with pytest.raises(TypeError):
        call(1)


def test_a_malformed_tag_is_never_read_as_absent() -> None:
    """`InvalidTagError` is not a `KeyError`, so code catching `KeyError` passes it on."""
    assert not issubclass(sl.InvalidTagError, KeyError)
    with pytest.raises(sl.InvalidTagError):
        Mapping.get(_NAME, "fr_CA")


def test_a_well_formed_absent_tag() -> None:
    with pytest.raises(MissingTagError) as raised:
        _NAME["de"]
    assert isinstance(raised.value, KeyError)
    assert raised.value.langs == ("de",)
    assert raised.value.held == ("en", "fr")
    assert str(raised.value) == "no text for 'de'; held: 'en', 'fr'"
    assert _NAME.get("de") is None
    assert _NAME.pick("de") is None
    assert "de" not in _NAME
    keys = _NAME.keys()
    assert "de" not in keys
    assert ("de", _ls("x", "de")) not in _NAME.items()


def test_pick_checks_every_tag_before_it_looks_any_up() -> None:
    with pytest.raises(sl.InvalidTagError):
        _NAME.pick("en", "fr_CA")


def test_missing_tag_error_names_nothing_held_for_an_empty_string() -> None:
    assert str(MissingTagError(("de",), ())) == "no text for 'de'; held: nothing"


# ── the Mapping protocol ───────────────────────────────────────────────────


def test_it_is_a_mapping_with_views() -> None:
    assert isinstance(_NAME, Mapping)
    assert isinstance(_NAME.keys(), KeysView)
    assert isinstance(_NAME.values(), ValuesView)
    assert isinstance(_NAME.items(), ItemsView)
    assert list(_NAME) == ["en", "fr"]
    assert list(_NAME.keys()) == ["en", "fr"]
    assert list(_NAME.values()) == [_ls("Annual", "en"), _ls("Annuel", "fr")]
    assert dict(_NAME.items()) == {"en": _ls("Annual", "en"), "fr": _ls("Annuel", "fr")}
    assert _ls("Annual", "en") in _NAME.values()
    assert _ls("Annual", "fr") not in _NAME.values()
    assert ("en", _ls("Annual", "en")) in _NAME.items()
    assert len(_NAME) == 2


def test_mapping_protocols_follow_from_the_base_class() -> None:
    assert _NAME.items() == {"en": _ls("Annual", "en"), "fr": _ls("Annuel", "fr")}.items()
    match _NAME:
        case {"fr": found}:
            assert found == _ls("Annuel", "fr")
        case _:
            pytest.fail("a mapping pattern did not match")
    assert dict(_NAME.items()) == {**_NAME}


def test_equal_only_to_an_international_string() -> None:
    assert InternationalString(en="Annual", fr="Annuel") == _NAME
    assert _NAME != {"en": "Annual", "fr": "Annuel"}
    assert {"en": _ls("Annual", "en"), "fr": _ls("Annuel", "fr")} != _NAME
    assert hash(InternationalString(en="Annual", fr="Annuel")) == hash(_NAME)
    assert len({_NAME, InternationalString(fr="Annuel", en="Annual")}) == 1


def test_the_views_normalise_no_held_tag_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Iterating 1,000 strings over 3 tags checks each tag once, through the seen set."""
    strings = [
        InternationalString._from_normalized({"en": f"e{n}", "fr": f"f{n}", "fr-ca": f"c{n}"}) for n in range(1000)
    ]
    calls: list[str] = []
    checked = istring.normalize_tag

    def counting(tag: str) -> str:
        calls.append(tag)
        return checked(tag)

    istring._SEEN_TAGS.clear()
    monkeypatch.setattr(istring, "normalize_tag", counting)
    for s in strings:
        list(s.keys())
        list(s.values())
        list(s.items())
    assert len(calls) == 3


# ── raw views and construction ─────────────────────────────────────────────


def test_as_dict_is_a_new_dict_of_texts() -> None:
    texts = _NAME.as_dict()
    assert texts == {"en": "Annual", "fr": "Annuel"}
    texts["de"] = "x"
    assert "de" not in _NAME


def test_of_copies_an_international_string() -> None:
    copy = InternationalString.of(_NAME)
    assert copy == _NAME
    assert copy._backing() is not _NAME._backing()


def test_of_refuses_a_text_that_is_not_a_str() -> None:
    untyped = cast("Callable[[object], InternationalString]", InternationalString.of)
    with pytest.raises(TypeError):
        untyped(dict(_NAME.items()))


def test_to_istring_returns_an_international_string_unchanged() -> None:
    assert istring.to_istring(_NAME) is _NAME
    assert istring.to_istring({"EN": "a"}) == InternationalString(en="a")


def test_repr_is_valid_python_for_every_tag() -> None:
    s = InternationalString.of({"en-t-fr": "Annual"})
    assert repr(s) == "InternationalString.of({'en-t-fr': 'Annual'})"
    literal = repr(s).removeprefix("InternationalString.of(").removesuffix(")")
    assert InternationalString.of(ast.literal_eval(literal)) == s


def test_empty_is_shared_and_falsy() -> None:
    assert InternationalString.of({}) is InternationalString.EMPTY
    assert not InternationalString()
    assert InternationalString(en="A")


# ── LocalisedString is read through .text ──────────────────────────────────


def test_a_lookup_is_read_through_text() -> None:
    value = _NAME["fr"]
    assert f"{value}" == "LocalisedString(text='Annuel', lang='fr')"
    assert value != "Annuel"
    assert value.text == "Annuel"
    assert msgspec.json.encode(value) == b'{"text":"Annuel","lang":"fr"}'


# ── tag_alias ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("tag", "alias"), [("en", "en"), ("FR-CA", "fr_ca"), ("en-t-fr", "en_t_fr")])
def test_tag_alias_writes_a_tag_for_a_generated_name(tag: str, alias: str) -> None:
    assert sl.tag_alias(tag) == alias


def test_tag_alias_refuses_a_malformed_tag() -> None:
    with pytest.raises(sl.InvalidTagError, match="fr_CA"):
        sl.tag_alias("fr_CA")


# ── serialisers write text ─────────────────────────────────────────────────


def test_an_sdmx_json_name_is_text_and_the_schema_refuses_a_localised_string() -> None:
    """The schema checks each writer test's output; it refuses a `LocalisedString` where text belongs.

    msgspec encodes a `LocalisedString` as an object, so a writer that passed
    one on would write valid JSON with the wrong shape rather than raise.
    """
    validator = compile_json_schema("structure")
    codelist = sl.Codelist(id="CL", maintainer="AG", name=_NAME, codes=[sl.Code(id="A", name={"en": "a"})])
    written = json_writer.write(sl.StructureMessage(codelists=[codelist]))
    assert_valid_sdmx_json(written, validator)
    doc = json.loads(written)
    names = doc["data"]["codelists"][0]["names"]
    assert names == {"en": "Annual", "fr": "Annuel"}
    names["fr"] = {"text": "Annuel", "lang": "fr"}
    assert not is_valid_sdmx_json(json.dumps(doc).encode(), validator)


def test_structure_frame_concept_labels_are_text() -> None:
    concept = sl.Concept(id="FREQ", name={"en": "Frequency", "fr": "Fréquence"})
    urn = sl.Concept.parse_urn("urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=AG:CS(1.0).FREQ")
    dsd = sl.DataStructure(
        id="DSD",
        maintainer="AG",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="FREQ", concept=sl.Ref[sl.Concept].of(urn, concept))],
    )
    frame = slpl.structure_frame(dsd, lang="fr")
    assert frame["concept"].dtype == pl.String
    assert frame["concept"].to_list() == ["Fréquence"]


# ── the migration audit ────────────────────────────────────────────────────


def _run_audited(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run a script reading one string by each audited method, the first twice from one line, under `env`."""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sdmxlib as sl
s = sl.InternationalString(en="a")
for _ in range(2):
    s["en"]
s.get("en")
s.pick("en")
list(s.values())
list(s.items())
""",
        ],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_audit_logs_each_reading_site_once(tmp_path: Path) -> None:
    out = tmp_path / "audit.txt"
    done = _run_audited({"SDMXLIB_ISTRING_AUDIT": "1", "SDMXLIB_ISTRING_AUDIT_OUT": str(out)})
    assert done.returncode == 0, done.stderr
    assert out.read_text().splitlines() == [
        "<string>:5\t__getitem__",
        "<string>:6\tget",
        "<string>:7\tpick",
        "<string>:8\tvalues",
        "<string>:9\titems",
    ]


def test_the_audit_needs_a_file_to_write(tmp_path: Path) -> None:
    done = _run_audited({"SDMXLIB_ISTRING_AUDIT": "1", "SDMXLIB_ISTRING_AUDIT_OUT": ""})
    assert done.returncode != 0
    assert "SDMXLIB_ISTRING_AUDIT_OUT" in done.stderr
    assert not list(tmp_path.iterdir())
