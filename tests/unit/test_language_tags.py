"""Language tags have one spelling, set where they enter (GH-280).

`normalize_tag` lowercases a tag and accepts it when it is well-formed, by the
SDMX-JSON 2.1 schema's RFC 5646 pattern. Every way a tag enters normalises it:
`InternationalString` and the other model types, both readers, the overlays
and every public `lang` / `langs` parameter. The readers refuse an artefact
with a tag that is not well-formed, and the store readers trust what the store
holds, which `check_bundle` checks.
"""

import contextlib
import importlib
import inspect
import json
import pkgutil
import re
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import msgspec
import polars as pl
import pytest

import sdmxlib as sl
import sdmxlib.polars as slpl
from sdmxlib import q
from sdmxlib.bundle._overlay import OverlayError, OverlayText, read_overlay
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_json._data_common import AnnotationPool, ComponentTable, display_names
from sdmxlib.formats.sdmx_json._dto import Link as JsonLink
from sdmxlib.formats.sdmx_json._structure_dto import read_links
from sdmxlib.formats.sdmx_json.data_v1 import write_sdmx_json_data as write_v1
from sdmxlib.formats.sdmx_json.data_v2 import write_sdmx_json_data as write_v2
from sdmxlib.formats.sdmx_json.metadata import _ingest_meta_name
from sdmxlib.formats.sdmx_ml30.writer import write as write30
from sdmxlib.model.istring import _SEEN_TAGS, _SEEN_TAGS_MAX, MAX_TAG_LENGTH, TAG_PATTERN
from sdmxlib.model.validation import SdmxConformanceError, SdmxParseError
from sdmxlib.storage import readers
from sdmxlib.storage._projections import code_map
from sdmxlib.storage._tags import stale_tags
from sdmxlib.storage.lazy import LazyCodelist
from tests.support.repo import find_root

pytestmark = pytest.mark.unit

_BAD = "fr_CA"

# ── normalize_tag ──────────────────────────────────────────────────────────


def test_the_pattern_is_the_sdmx_json_schemas() -> None:
    schema = json.loads((find_root() / "tests/schemas/sdmx_json/2.1/sdmx-json-structure-schema.json").read_text())
    defs = schema.get("$defs", schema.get("definitions"))
    (pattern,) = defs["localisedText"]["patternProperties"]
    assert pattern == f"^{TAG_PATTERN}$"


@pytest.mark.parametrize(
    ("tag", "stored"),
    [
        ("en", "en"),
        ("EN", "en"),
        ("fr-CA", "fr-ca"),
        ("zh-Hant-TW", "zh-hant-tw"),
        ("es-419", "es-419"),
        ("en-t-fr", "en-t-fr"),
        ("und", "und"),
        ("i-klingon", "i-klingon"),
        ("zh-min-nan", "zh-min-nan"),
        ("x-private", "x-private"),
    ],
)
def test_a_well_formed_tag_is_stored_lowercase(tag: str, stored: str) -> None:
    assert sl.normalize_tag(tag) == stored


@pytest.mark.parametrize(
    "tag",
    [
        "",
        "fr_CA",
        " en",
        "en ",
        "en US",
        "e",
        "en-a",
        "abc-12345678x",
        # The grandfathered tags RFC 5646 spells in mixed case: lowercased, they match no branch.
        "en-GB-oed",
        "sgn-BE-FR",
        "sgn-BE-NL",
        "sgn-CH-DE",
        # The Kelvin sign lowercases to an ASCII "k".
        chr(0x212A) + "a",
        "én",
    ],
)
def test_a_malformed_tag_is_refused(tag: str) -> None:
    with pytest.raises(ValueError, match="not a well-formed language tag"):
        sl.normalize_tag(tag)


def test_the_two_letter_path_agrees_with_the_pattern_on_every_pair() -> None:
    """Every two-character string from a set that includes non-letters and non-ASCII: same answer either way."""
    alphabet = [chr(c) for c in range(0x20, 0x7F)] + [chr(0xE9), chr(0x212A), chr(0x131)]
    pattern = re.compile(TAG_PATTERN)
    pairs = [a + b for a in alphabet for b in alphabet]
    letters = 0
    for pair in pairs:
        expected = pair.lower() if pair.isascii() and pattern.fullmatch(pair.lower()) else None
        try:
            got: str | None = sl.normalize_tag(pair)
        except ValueError:
            got = None
        assert got == expected, pair
        letters += got is not None
    assert letters == 52 * 52, "the discrimination check: every pair of ASCII letters is accepted"


def test_a_tag_that_is_not_a_str_is_a_type_error() -> None:
    # Called as an untyped caller would: the annotation says `str`, the runtime checks it.
    untyped = cast("Callable[[object], str]", sl.normalize_tag)
    with pytest.raises(TypeError, match="not int"):
        untyped(1)


def test_a_long_tag_is_refused_before_the_match_and_quoted_short() -> None:
    longest = "x" + "-abcdefgh" * ((MAX_TAG_LENGTH - 1) // 9)
    assert len(longest) <= MAX_TAG_LENGTH
    assert sl.normalize_tag(longest) == longest
    with pytest.raises(ValueError, match="not a well-formed") as raised:
        sl.normalize_tag(longest + "-abcdefgh")
    assert len(str(raised.value)) < 160


@pytest.mark.parametrize(
    "tag",
    [
        "a" * MAX_TAG_LENGTH,
        "-a" * (MAX_TAG_LENGTH // 2),
        "en" + "-a1" * ((MAX_TAG_LENGTH - 2) // 3),
        "en" + "-1-ab" * ((MAX_TAG_LENGTH - 2) // 5),
        "x" + "-a" * ((MAX_TAG_LENGTH - 1) // 2),
        "en-a" + "-ab" * ((MAX_TAG_LENGTH - 4) // 3) + "!",
    ],
)
def test_an_adversarial_tag_is_checked_in_bounded_time(tag: str) -> None:
    """The cap and the pattern keep a hostile tag's check short; a backtracking blow-up takes seconds."""
    start = time.perf_counter()
    for _ in range(100):
        with contextlib.suppress(ValueError):
            sl.normalize_tag(tag)
    assert time.perf_counter() - start < 0.5


# ── InternationalString and LocalisedString ────────────────────────────────


def test_keys_are_normalised_and_looked_up_normalised() -> None:
    s = sl.InternationalString(**{"FR-CA": "a", "en": "b"})
    assert s.langs == ["fr-ca", "en"]
    assert s["fr-CA"] == "a"
    assert s.get("de", "FR-ca") == "a"
    assert "Fr-Ca" in s
    assert sl.InternationalString.of({"EN": "b"}) == sl.InternationalString(en="b")


def test_two_texts_of_one_tag_are_refused() -> None:
    with pytest.raises(ValueError, match="two texts for language 'fr'"):
        sl.InternationalString(fr="a", FR="b")
    with pytest.raises(ValueError, match="two texts for language 'fr'"):
        sl.InternationalString.of({"FR": "a", "fr": "b"})


def test_a_malformed_key_is_refused() -> None:
    with pytest.raises(ValueError, match="fr_CA"):
        sl.InternationalString.of({_BAD: "a"})


def test_indexing_a_malformed_tag_is_a_key_error_from_the_value_error() -> None:
    s = sl.InternationalString(en="a")
    with pytest.raises(KeyError) as raised:
        s[_BAD]
    assert isinstance(raised.value.__cause__, ValueError)


def test_membership_of_a_malformed_tag_or_a_non_str_is_false() -> None:
    s = sl.InternationalString(en="a")
    assert _BAD not in s
    assert 1 not in s


def test_get_and_pick_refuse_a_malformed_tag() -> None:
    s = sl.InternationalString(en="a")
    with pytest.raises(ValueError, match="fr_CA"):
        s.get(_BAD)
    with pytest.raises(ValueError, match="fr_CA"):
        s.pick(_BAD)


def test_pick_says_which_tag_answered() -> None:
    s = sl.InternationalString(fr="Annuel", en="Annual")
    assert s.pick("FR-CA", "Fr", "en") == sl.LocalisedString("Annuel", "fr")
    assert s.pick("de") is None


@pytest.mark.parametrize("already_seen", [[], ["fr"]], ids=["unseen", "seen"])
def test_localised_string_normalises_its_tag_and_compares_by_both_fields(already_seen: list[str]) -> None:
    _SEEN_TAGS.clear()
    _SEEN_TAGS.update(already_seen)
    text = sl.LocalisedString("Annuel", "FR")
    assert text.lang == "fr"
    assert text == sl.LocalisedString("Annuel", "fr")
    assert text != sl.LocalisedString("Annuel", "en")
    assert text != sl.LocalisedString("Annual", "fr")
    assert len({text, sl.LocalisedString("Annuel", "fr")}) == 1


def test_localised_string_refuses_a_malformed_tag_and_a_non_str_text() -> None:
    with pytest.raises(sl.InvalidTagError, match="fr_CA"):
        sl.LocalisedString("x", _BAD)
    untyped = cast("Callable[[object, object], sl.LocalisedString]", sl.LocalisedString)
    with pytest.raises(TypeError):
        untyped(sl.LocalisedString("a", "en"), "en")
    with pytest.raises(TypeError):
        untyped("a", 1)


def test_localised_string_normalises_when_decoded_converted_or_replaced() -> None:
    decoded = msgspec.json.decode(b'{"text":"x","lang":"FR"}', type=sl.LocalisedString)
    assert decoded == sl.LocalisedString("x", "fr")
    assert decoded.lang == "fr"
    with pytest.raises(msgspec.ValidationError, match="fr_CA"):
        msgspec.json.decode(b'{"text":"x","lang":"fr_CA"}', type=sl.LocalisedString)
    assert msgspec.convert({"text": "x", "lang": "EN"}, sl.LocalisedString).lang == "en"
    assert msgspec.structs.replace(sl.LocalisedString("a", "fr"), lang="FR").lang == "fr"


def test_localised_string_is_frozen() -> None:
    value = sl.LocalisedString("a", "en")
    for name in ("text", "lang"):
        with pytest.raises(AttributeError):
            setattr(value, name, "fr")


def test_localised_string_encodes_text_then_lang() -> None:
    assert msgspec.json.encode(sl.LocalisedString("Population", "en")) == b'{"text":"Population","lang":"en"}'


def test_localised_string_is_read_through_text() -> None:
    value = sl.LocalisedString("", "fr")
    assert f"{value}" == "LocalisedString(text='', lang='fr')"
    assert bool(value)
    assert value != ""
    with pytest.raises(TypeError):
        format(value, ">10")


def test_the_seen_set_is_cleared_when_full() -> None:
    _SEEN_TAGS.clear()
    tags = [f"x-{n:04d}" for n in range(_SEEN_TAGS_MAX + 1)]
    for tag in tags:
        sl.LocalisedString("t", tag)
    assert {tags[-1]} == _SEEN_TAGS


def test_an_underscore_tag_names_its_hyphen_form() -> None:
    with pytest.raises(sl.InvalidTagError, match="did you mean 'fr-ca'") as raised:
        sl.normalize_tag("fr_CA")
    assert raised.value.lang == "fr_CA"
    with pytest.raises(sl.InvalidTagError) as raised:
        sl.normalize_tag("zz zz")
    assert "did you mean" not in str(raised.value)


def test_an_optional_tag_passes_none_and_normalises_the_rest() -> None:
    assert sl.AnnotationURL("https://x.org").lang is None
    assert sl.AnnotationURL("https://x.org", "FR").lang == "fr"
    assert sl.RegionValue("A", lang="FR").lang == "fr"
    with pytest.raises(ValueError, match="fr_CA"):
        sl.AnnotationURL("https://x.org", _BAD)
    with pytest.raises(ValueError, match="fr_CA"):
        sl.RegionValue("A", lang=_BAD)


# ── SDMX-ML ────────────────────────────────────────────────────────────────


def _codelist() -> sl.Codelist:
    return sl.Codelist(
        id="CL",
        maintainer="T",
        name={"en": "n", "fr": "f"},
        codes=[sl.Code(id="A", name={"en": "a"})],
        annotations=[sl.Annotation(type="T", urls=[sl.AnnotationURL("https://x.org", "fr")], text={"fr": "t"})],
    )


def _ml(old: str, new: str) -> bytes:
    """The codelist's SDMX-ML 3.0, with `old` replaced once."""
    written = write30(sl.StructureMessage(codelists=[_codelist()])).decode()
    assert written.count(old) == 1, old
    return written.replace(old, new).encode()


def _read(document: bytes) -> sl.StructureMessage:
    msg = sl.parse(document, strict=False)
    assert isinstance(msg, sl.StructureMessage)
    return msg


def test_the_codelist_reads_as_written() -> None:
    """The discrimination check: each document below differs from this one only in the tag it changes."""
    msg = _read(_ml('<com:Name xml:lang="fr">f', '<com:Name xml:lang="fr">f'))
    assert msg.refused == ()
    assert msg.codelists[0].name.langs == ["en", "fr"]


@pytest.mark.parametrize(
    ("old", "new", "langs"),
    [
        ('xml:lang="fr">f', 'xml:lang="FR-ca">f', ["en", "fr-ca"]),
        ('xml:lang="fr">f', 'xml:lang=" fr\t">f', ["en", "fr"]),
        ('<com:Name xml:lang="fr">f', "<com:Name>f", None),
    ],
    ids=["case", "xs-token-whitespace", "absent-is-the-schema-default"],
)
def test_an_xml_lang_is_read_normalised(old: str, new: str, langs: list[str] | None) -> None:
    msg = _read(_ml(old, new))
    if langs is None:
        assert msg.refused, "an absent xml:lang is English, which the codelist already holds"
        assert "two texts for language 'en'" in msg.refused[0].reason
        return
    assert msg.refused == ()
    assert msg.codelists[0].name.langs == langs


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    [
        ('xml:lang="fr">f', 'xml:lang="">f', "''"),
        ('xml:lang="fr">f', 'xml:lang="fr_CA">f', "fr_CA"),
        ('xml:lang="fr">f', 'xml:lang="EN">f', "two texts for language 'en'"),
        ('<com:AnnotationText xml:lang="fr">', '<com:AnnotationText xml:lang="f r">', "'f r'"),
        ('<com:AnnotationURL xml:lang="fr">', '<com:AnnotationURL xml:lang="fr_CA">', "fr_CA"),
    ],
    ids=["empty", "malformed", "two-of-one-tag", "annotation-text", "annotation-url"],
)
def test_a_bad_xml_lang_refuses_the_artefact(old: str, new: str, reason: str) -> None:
    msg = _read(_ml(old, new))
    assert len(msg.codelists) == 0
    (refusal,) = msg.refused
    assert reason in refusal.reason
    with pytest.raises(sl.SdmxConformanceError):
        sl.parse(_ml(old, new))


def test_a_bad_header_tag_reads_as_no_header_and_a_loss() -> None:
    header = sl.Header(id="ID", prepared=_NOW, sender=sl.Sender(id="S"), name={"fr": "n"})
    written = write30(sl.StructureMessage(header=header, codelists=[_codelist()])).decode()
    msg = _read(written.replace('<com:Name xml:lang="fr">n', '<com:Name xml:lang="fr_CA">n', 1).encode())
    assert msg.header is None
    assert msg.codelists[0].id == "CL"
    assert any("fr_CA" in gap.reason for gap in msg.lost)


# ── SDMX-JSON ──────────────────────────────────────────────────────────────


def _json(edit: Callable[[dict[str, object]], None]) -> bytes:
    doc = json.loads(json_writer.write(sl.StructureMessage(codelists=[_codelist()])))
    edit(doc)
    return json.dumps(doc).encode()


def _codelist_json(doc: dict[str, object]) -> dict[str, object]:
    data = cast("dict[str, list[dict[str, object]]]", doc["data"])
    return data["codelists"][0]


def _names(names: dict[str, str]) -> Callable[[dict[str, object]], None]:
    def edit(doc: dict[str, object]) -> None:
        _codelist_json(doc)["names"] = names

    return edit


def _lone_name(content_languages: list[str] | None) -> Callable[[dict[str, object]], None]:
    def edit(doc: dict[str, object]) -> None:
        del _codelist_json(doc)["names"]
        meta = cast("dict[str, object]", doc["meta"])
        if content_languages is not None:
            meta["contentLanguages"] = content_languages

    return edit


def test_json_keys_are_read_normalised() -> None:
    msg = _read(_json(_names({"en": "n", "FR-ca": "f"})))
    assert msg.refused == ()
    assert msg.codelists[0].name.langs == ["en", "fr-ca"]


@pytest.mark.parametrize(
    ("edit", "reason"),
    [
        (_names({"en": "n", _BAD: "f"}), "fr_CA"),
        (_names({"en": "n", "EN": "f"}), "two texts for language 'en'"),
        (_lone_name(None), "contentLanguages"),
        (_lone_name(["en", "fr"]), "contentLanguages"),
    ],
    ids=["malformed", "two-of-one-tag", "lone-name", "lone-name-two-languages"],
)
def test_a_bad_json_text_refuses_the_artefact(edit: Callable[[dict[str, object]], None], reason: str) -> None:
    msg = _read(_json(edit))
    assert len(msg.codelists) == 0
    (refusal,) = msg.refused
    assert reason in refusal.reason


def test_a_lone_name_takes_the_one_language_the_message_declares() -> None:
    msg = _read(_json(_lone_name(["FR"])))
    assert msg.refused == ()
    assert msg.codelists[0].name == sl.InternationalString(fr="n")


def test_a_malformed_content_language_fails_the_read() -> None:
    with pytest.raises(SdmxParseError, match="contentLanguages"):
        sl.parse(_json(_lone_name([_BAD])), strict=False)


def test_the_message_language_ends_with_the_read() -> None:
    """The context variable is reset: a second document declaring none refuses its lone name."""
    assert _read(_json(_lone_name(["en"]))).refused == ()
    assert len(_read(_json(_lone_name(None))).refused) == 1


# ── every public lang / langs parameter ────────────────────────────────────


def _public_lang_callables() -> set[str]:
    """``module:qualname`` of every public function, class or method with a `lang` or `langs` parameter.

    Protocols are skipped, and so are exceptions: `InvalidTagError(lang)`
    carries the tag it refuses.
    """
    found: set[str] = set()
    for module in _public_modules():
        for name, obj in vars(module).items():
            if name.startswith("_") or not getattr(obj, "__module__", "").startswith("sdmxlib"):
                continue
            if inspect.isclass(obj) and issubclass(obj, BaseException):
                continue
            if inspect.isclass(obj) and not getattr(obj, "_is_protocol", False):
                found |= {_named(each) for each in (obj, *_public_methods(obj)) if _takes_lang(each)}
            elif inspect.isfunction(obj) and _takes_lang(obj):
                found.add(_named(obj))
    return found


def _public_modules() -> list[object]:
    return [sl] + [
        importlib.import_module(m.name)
        for m in pkgutil.walk_packages(sl.__path__, "sdmxlib.")
        if not any(part.startswith("_") for part in m.name.split(".")[1:])
    ]


def _public_methods(cls: type) -> list[object]:
    return [
        member
        for member_name, member in inspect.getmembers(cls)
        if member_name in {"__getitem__", "__contains__"} or not member_name.startswith("_")
    ]


def _takes_lang(obj: object) -> bool:
    try:
        parameters = inspect.signature(cast("Callable[..., object]", obj)).parameters
    except TypeError, ValueError:
        return False
    return bool({"lang", "langs"} & set(parameters))


def _named(obj: object) -> str:
    return f"{getattr(obj, '__module__', '')}:{getattr(obj, '__qualname__', '')}"


_AREAS = sl.Codelist(id="CL_AREA", maintainer="AG", name={"en": "Areas"}, codes=[sl.Code(id="CA", name={"en": "C"})])
_CONCEPTS = sl.ConceptScheme(
    id="CS", maintainer="AG", name={"en": "c"}, concepts=[sl.Concept(id=c, name={"en": c}) for c in ("A", "V")]
)
_DSD = sl.DataStructure(
    id="DSD",
    maintainer="AG",
    name={"en": "d"},
    dimensions=[sl.Dimension(id="A", concept=_CONCEPTS.ref_to("A"), representation=sl.Ref.to(_AREAS))],
    measures=[sl.Measure(id="V", concept=_CONCEPTS.ref_to("V"), representation=sl.Facet(type="Double"))],
)
_FLOW = sl.Dataflow(id="F", maintainer="AG", name={"en": "f"}, structure=sl.Ref.to(_DSD))
_BINDING = sl.DataflowBinding.from_dsd(
    _DSD, dataflow=sl.Ref.to(_FLOW), schema="data", table="obs", database_role="memory"
)


@pytest.fixture(scope="module")
def store() -> Iterator[sl.LocalRegistry]:
    registry = sl.LocalRegistry(":memory:").open()
    try:
        registry.add_all([_AREAS, _CONCEPTS, _DSD, _FLOW])
        registry.connection.execute("CREATE SCHEMA data")
        registry.connection.execute("CREATE TABLE data.obs AS SELECT 'CA' AS A, 1.0 AS V")
        registry.add(_BINDING)
        yield registry
    finally:
        registry.close()


def _refuses(call: Callable[[], object]) -> None:
    with pytest.raises(sl.InvalidTagError, match="fr_CA"):
        call()


def _calls(store: sl.LocalRegistry, tmp_path: Path) -> dict[str, Callable[[], None]]:
    """For each public `lang` parameter, a call giving it a malformed tag and asserting it is refused."""
    istring = sl.InternationalString(en="a")
    lazy = store.get(_AREAS.urn)
    assert isinstance(lazy, LazyCodelist)
    flow = sl.Ref.to(_FLOW)
    no_batches: list[object] = []

    def contains() -> None:
        assert _BAD not in istring

    def getitem() -> None:
        with pytest.raises(KeyError):
            istring[_BAD]

    def table() -> None:
        _refuses(lambda: ComponentTable(["A"], _DSD, kind="dimension", lang=_BAD, annotations=AnnotationPool()))

    return {
        "sdmxlib.bundle._overlay:OverlayText": lambda: _refuses(
            lambda: OverlayText(selector="*", op="remove", field="name", lang=_BAD)
        ),
        "sdmxlib.formats.sdmx_json._data_common:ComponentTable": table,
        "sdmxlib.formats.sdmx_json._data_common:display_names": lambda: _refuses(
            lambda: display_names(flow, _DSD, _BAD)
        ),
        "sdmxlib.formats.sdmx_json.data_v1:write_sdmx_json_data": lambda: _refuses(
            lambda: next(write_v1(no_batches, _DSD, flow, message_id="m", prepared=_NOW, sender_id="s", lang=_BAD))
        ),
        "sdmxlib.formats.sdmx_json.data_v2:write_sdmx_json_data": lambda: _refuses(
            lambda: next(write_v2(no_batches, _DSD, flow, message_id="m", prepared=_NOW, sender_id="s", lang=_BAD))
        ),
        "sdmxlib.local.registry:BundleRegistry.code_map": lambda: _refuses(
            lambda: store.code_map(_AREAS.urn, lang=_BAD)
        ),
        "sdmxlib.local.registry:BundleRegistry.dsd_code_map": lambda: _refuses(
            lambda: store.dsd_code_map(_DSD.urn, lang=_BAD)
        ),
        "sdmxlib.model.annotations:AnnotationURL": lambda: _refuses(lambda: sl.AnnotationURL("u", _BAD)),
        "sdmxlib.model.codelist:Codelist.code_map": lambda: _refuses(lambda: _AREAS.code_map(lang=_BAD)),
        "sdmxlib.model.constraint:RegionValue": lambda: _refuses(lambda: sl.RegionValue("A", lang=_BAD)),
        "sdmxlib.model.datastructure:DataStructure.code_map": lambda: _refuses(lambda: _DSD.code_map(lang=_BAD)),
        "sdmxlib.model.datastructure:DataStructure.column_labels": lambda: _refuses(
            lambda: _DSD.column_labels(lang=_BAD)
        ),
        "sdmxlib.model.istring:InternationalString.__contains__": contains,
        "sdmxlib.model.istring:InternationalString.__getitem__": getitem,
        "sdmxlib.model.istring:InternationalString.get": lambda: _refuses(lambda: istring.get(_BAD)),
        "sdmxlib.model.istring:InternationalString.pick": lambda: _refuses(lambda: istring.pick(_BAD)),
        "sdmxlib.model.istring:LocalisedString": lambda: _refuses(lambda: sl.LocalisedString("x", _BAD)),
        "sdmxlib.model.valuelist:ValueList.code_map": lambda: _refuses(
            lambda: sl.ValueList(id="VL", maintainer="AG", name={"en": "v"}, items=[]).code_map(lang=_BAD)
        ),
        "sdmxlib.polars:label": lambda: _refuses(lambda: slpl.label(_FRAME, _DSD, lang=_BAD)),
        "sdmxlib.polars:structure_frame": lambda: _refuses(lambda: slpl.structure_frame(_DSD, lang=_BAD)),
        "sdmxlib.query._expr:Localised.__getitem__": lambda: _refuses(lambda: q.code.name[_BAD]),
        "sdmxlib.query._expr:SideLocalised.__getitem__": lambda: _refuses(lambda: q.dataflow.name[_BAD]),
        "sdmxlib.query.observations:ObsQuery.sink_json": lambda: _refuses(
            lambda: store.obs(_FLOW).sink_json(tmp_path / "o.json", lang=_BAD)
        ),
        "sdmxlib.storage._projections:code_map": lambda: _refuses(
            lambda: code_map(store.connection, str(_AREAS.urn), lang=_BAD)
        ),
        "sdmxlib.storage.lazy:LazyCodelist.code_map": lambda: _refuses(lambda: lazy.code_map(lang=_BAD)),
        "sdmxlib.storage.readers:code_map_for_dsd": lambda: _refuses(
            lambda: readers.code_map_for_dsd(store.connection, str(_DSD.urn), lang=_BAD)
        ),
    }


def test_every_public_lang_parameter_refuses_a_malformed_tag(store: sl.LocalRegistry, tmp_path: Path) -> None:
    calls = _calls(store, tmp_path)
    assert set(calls) == _public_lang_callables(), "a lang parameter was added or removed: list it here"
    for name, call in calls.items():
        try:
            call()
        except AssertionError as exc:
            pytest.fail(f"{name}: {exc}")


# ── overlays and the store ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "overlay",
    [
        {"texts": [{"selector": "*", "op": "set", "field": "name", "lang": _BAD, "text": "x"}]},
        {"entries": [{"selector": "*", "op": "set", "type": "T", "annotation": {"text": {_BAD: "x"}}}]},
    ],
    ids=["text", "annotation"],
)
def test_an_overlay_with_a_malformed_tag_is_refused(overlay: dict[str, object], tmp_path: Path) -> None:
    path = tmp_path / "overlay.json"
    path.write_text(json.dumps({"format_version": "1.1", "id": "o", "entries": [], **overlay}))
    with pytest.raises(OverlayError, match="fr_CA"):
        read_overlay(path)


def test_an_overlay_annotation_with_two_texts_of_one_tag_is_refused(tmp_path: Path) -> None:
    entry = {"selector": "*", "op": "set", "type": "T", "annotation": {"text": {"en": "A", "EN": "B"}}}
    path = tmp_path / "overlay.json"
    path.write_text(json.dumps({"format_version": "1.1", "id": "o", "entries": [entry]}))
    with pytest.raises(OverlayError, match="two texts for language 'en'"):
        read_overlay(path)


def test_a_link_language_is_normalised_and_a_malformed_one_refused() -> None:
    assert sl.Link(rel="r", url="u", hreflang="FR-CA").hreflang == "fr-ca"
    (link,) = read_links([JsonLink(rel="r", href="u", hreflang="FR-CA")])
    assert link.hreflang == "fr-ca"
    with pytest.raises(SdmxConformanceError, match="fr_CA"):
        read_links([JsonLink(rel="r", href="u", hreflang=_BAD)])


def test_a_metadata_set_names_map_wins_over_a_legacy_name_map() -> None:
    name = _ingest_meta_name({"en": "Old"}, {"en": "New", "fr": "Nouveau"}, "name")
    assert name.as_dict() == {"en": "New", "fr": "Nouveau"}
    assert _ingest_meta_name({"en": "Old"}, None, "name").as_dict() == {"en": "Old"}


def test_stale_tags_finds_each_kind_of_column(store: sl.LocalRegistry) -> None:
    """A sabotage check: a clean store reports nothing, and one bad tag of each kind is found."""
    assert stale_tags(store.connection) == []
    conn = store.connection
    conn.execute("BEGIN")
    try:
        conn.execute("UPDATE sdmx.code SET name = MAP {'EN': 'C'}")
        conn.execute("INSERT INTO sdmx.localized_text (owner_urn, field, lang, text) VALUES ('u', 'name', '', 'x')")
        conn.execute(
            "INSERT INTO sdmx.link (owner_urn, owner_artefact_urn, position, rel, url, hreflang) "
            "VALUES ('u', 'u', 0, 'r', 'x', 'FR')"
        )
        assert stale_tags(conn) == [("code.name", "EN"), ("link.hreflang", "FR"), ("localized_text.lang", "")]
    finally:
        conn.execute("ROLLBACK")
    assert stale_tags(conn) == []


_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_FRAME = pl.LazyFrame({"A": ["CA"]})
