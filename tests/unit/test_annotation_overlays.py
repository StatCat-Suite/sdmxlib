"""Annotation overlays and the effective token (GH-70).

An overlay changes display annotations, and nothing else, without a rebuild.
These pin what a server and a cache rely on: which entry wins, what each
operation does, that items and nested items are reachable, that what nothing
selects is returned untouched, that the result is the same in every wire
format, and that the effective token moves with the overlays and never with
the bundle's own token.
"""

import hashlib
import json
from pathlib import Path

import msgspec
import pytest

import sdmxlib as sl
from sdmxlib.bundle import (
    Manifest,
    MergeHook,
    Overlay,
    OverlayAnnotation,
    OverlayEntry,
    OverlayError,
    OverlayText,
    Producer,
    applied,
    apply_overlays,
    check_bundle,
    effective_token,
    fold_overlays,
    overlay_schema,
    read_manifest,
    read_overlay,
    write_manifest,
)
from sdmxlib.bundle import _overlay as _overlay_module
from sdmxlib.bundle._overlay import generate_overlay_schema
from sdmxlib.model.annotations import Annotation
from sdmxlib.model.urn import SdmxUrn
from tests.support.bundles import DSD, make_bundle

pytestmark = pytest.mark.unit

_FREQ = sl.Codelist(
    id="CL_FREQ",
    maintainer="AG",
    version="1.0",
    name={"en": "Frequency"},
    codes=[sl.Code(id="A", name={"en": "Annual"}), sl.Code(id="M", name={"en": "Monthly"})],
    annotations=[Annotation(type="ORDER", title="kept"), Annotation(type="NOTE", title="other")],
)
_AREA = sl.Codelist(
    id="CL_AREA", maintainer="AG", version="1.0", name={"en": "Area"}, codes=[sl.Code(id="CA", name={"en": "Canada"})]
)
_CATS = sl.CategoryScheme(
    id="CS",
    maintainer="AG",
    version="1.0",
    name={"en": "Topics"},
    categories=[sl.Category(id="ECON", name={"en": "Economy"}, categories=[sl.Category(id="GDP", name={"en": "GDP"})])],
)


def _entry(
    selector: str, op: str = "set", kind: str = "LABEL", title: str | None = "x", scope: str | None = None
) -> OverlayEntry:
    annotation = None if op == "remove" else OverlayAnnotation(title=title)
    return msgspec.convert(
        {
            "selector": selector,
            "op": op,
            "type": kind,
            "annotation": annotation and msgspec.to_builtins(annotation),
            "scope": scope,
        },
        OverlayEntry,
    )


def _overlay(*entries: OverlayEntry, overlay_id: str = "o") -> Overlay:
    return Overlay(format_version="1.0", id=overlay_id, entries=entries)


def _titles(artefact: object, kind: str) -> list[str | None]:
    return [a.title for a in getattr(artefact, "annotations", ()) if a.type == kind]


def test_the_most_specific_selector_wins_whatever_the_document_order() -> None:
    overlay = _overlay(
        _entry(str(_FREQ.urn), title="urn"),
        _entry("AG:CL_FREQ", title="agency-id"),
        _entry("Codelist", title="type"),
        _entry("*", title="any"),
    )
    freq, area = apply_overlays([_FREQ, _AREA], [overlay])
    assert _titles(freq, "LABEL") == ["urn"]
    assert _titles(area, "LABEL") == ["type"], "a class name beats *"


def test_set_replaces_its_type_add_appends_remove_drops() -> None:
    (set_,) = apply_overlays([_FREQ], [_overlay(_entry("AG:CL_FREQ", "set", "ORDER", "new"))])
    assert _titles(set_, "ORDER") == ["new"]
    assert _titles(set_, "NOTE") == ["other"], "other types untouched"
    (added,) = apply_overlays([_FREQ], [_overlay(_entry("AG:CL_FREQ", "add", "ORDER", "more"))])
    assert _titles(added, "ORDER") == ["kept", "more"]
    (removed,) = apply_overlays([_FREQ], [_overlay(_entry("AG:CL_FREQ", "remove", "ORDER"))])
    assert _titles(removed, "ORDER") == []
    assert _titles(removed, "NOTE") == ["other"]


def test_a_merge_hook_decides_how_set_combines() -> None:
    def keep_both(existing: list[Annotation], new: Annotation) -> list[Annotation]:
        return [*existing, new]

    hook = MergeHook(package="tests", name="keep_both", version="1", fn=keep_both)
    (merged,) = apply_overlays([_FREQ], [_overlay(_entry("AG:CL_FREQ", "set", "ORDER", "new"))], merge={"ORDER": hook})
    assert _titles(merged, "ORDER") == ["kept", "new"]


def test_an_item_and_a_nested_item_are_reached_by_urn() -> None:
    code_urn = "urn:sdmx:org.sdmx.infomodel.codelist.Code=AG:CL_FREQ(1.0).M"
    nested_urn = "urn:sdmx:org.sdmx.infomodel.categoryscheme.Category=AG:CS(1.0).ECON.GDP"
    freq, cats = apply_overlays(
        [_FREQ, _CATS], [_overlay(_entry(code_urn, title="m"), _entry(nested_urn, title="gdp"))]
    )
    assert isinstance(freq, sl.Codelist)
    assert isinstance(cats, sl.CategoryScheme)
    assert _titles(freq.codes["M"], "LABEL") == ["m"]
    assert _titles(freq.codes["A"], "LABEL") == []
    assert _titles(freq, "LABEL") == [], "an item URN selects the item, not its scheme"
    assert _titles(cats.categories["ECON"].categories["GDP"], "LABEL") == ["gdp"]
    assert _titles(cats.categories["ECON"], "LABEL") == []


def test_a_class_name_reaches_every_item_of_that_class() -> None:
    (freq,) = apply_overlays([_FREQ], [_overlay(_entry("Code", title="c"))])
    assert [_titles(code, "LABEL") for code in freq.codes] == [["c"], ["c"]]
    assert _titles(freq, "LABEL") == []


def test_a_dsds_time_dimension_is_an_item_too() -> None:
    urn = "urn:sdmx:org.sdmx.infomodel.datastructure.TimeDimension=SDMX:DSD(1.0).TIME_PERIOD"
    dsd = sl.DataStructure(
        id="DSD",
        maintainer="SDMX",
        version="1.0",
        name={"en": "d"},
        dimensions=DSD.dimensions,
        time_dimension=sl.TimeDimension(id="TIME_PERIOD", concept=DSD.dimensions[0].concept),
        measures=DSD.measures,
    )
    (overlaid,) = apply_overlays([dsd], [_overlay(_entry(urn, title="t"))])
    assert overlaid.time_dimension is not None
    assert _titles(overlaid.time_dimension, "LABEL") == ["t"]


def test_what_nothing_selects_is_returned_as_it_was() -> None:
    freq, area = apply_overlays([_FREQ, _AREA], [_overlay(_entry("AG:CL_FREQ"))])
    assert area is _AREA
    assert freq.codes["A"] is _FREQ.codes["A"], "unselected items are not rebuilt either"
    assert freq is not _FREQ


def test_the_result_is_the_same_in_sdmx_ml_and_sdmx_json() -> None:
    """Applied to the model, so every writer carries it alike."""
    code_urn = "urn:sdmx:org.sdmx.infomodel.codelist.Code=AG:CL_FREQ(1.0).A"
    message = apply_overlays(sl.StructureMessage(codelists=[_FREQ]), [_overlay(_entry(code_urn, "add", "LABEL", "a"))])
    for written in (sl.to_xml(message, version="3.0"), sl.to_json(message)):
        read = sl.parse(written)
        assert isinstance(read, sl.StructureMessage)
        assert _titles(read.codelists["CL_FREQ"].codes["A"], "LABEL") == ["a"]


def test_the_effective_token_moves_with_the_overlays_and_not_the_bundle(tmp_path: Path) -> None:
    publication = make_bundle(tmp_path / "b", 10)
    manifest = write_manifest(publication, bundle_id="b", producer=Producer(name="t", version="1"))
    one, two = _overlay(_entry("*", title="1"), overlay_id="one"), _overlay(_entry("*", title="2"), overlay_id="two")
    assert effective_token(manifest, []) == manifest.token
    tokens = {
        effective_token(manifest, [one]),
        effective_token(manifest, [two]),
        effective_token(manifest, [one, two]),
        effective_token(manifest, [two, one]),
    }
    assert len(tokens) == 4, "content and order both count"
    assert manifest.token not in tokens
    assert effective_token(manifest, [one]) == effective_token(
        manifest, [_overlay(_entry("*", title="1"), overlay_id="one")]
    )
    assert read_manifest(publication).token == manifest.token, "applying overlays never touches the bundle"


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("{not json", "not an overlay document"),
        ({"format_version": "2.0", "id": "o", "entries": []}, r"overlay format 2\.0; this sdmxlib reads 1\.x"),
        (
            {"format_version": "1.0", "id": "o", "entries": [{"selector": "*", "op": "set", "type": "T"}]},
            "gives no annotation",
        ),
        (
            {
                "format_version": "1.0",
                "id": "o",
                "entries": [{"selector": "*", "op": "remove", "type": "T", "annotation": {}}],
            },
            "removes, so gives no annotation",
        ),
        ({"format_version": "1.0", "id": "o", "entries": [], "codes": []}, "not an overlay document"),
    ],
)
def test_a_document_that_is_not_an_overlay_is_refused(tmp_path: Path, document: object, message: str) -> None:
    path = tmp_path / "o.json"
    path.write_text(document if isinstance(document, str) else json.dumps(document))
    with pytest.raises(OverlayError, match=message):
        read_overlay(path)


def test_fold_writes_overlays_into_a_new_publication(tmp_path: Path) -> None:
    root = make_bundle(tmp_path / "b", 10, extra=(_FREQ,))
    before = write_manifest(root, bundle_id="b", producer=Producer(name="t", version="1"), product={"edition": 1})
    after = fold_overlays(root, [_overlay(_entry("AG:CL_FREQ", "set", "ORDER", "folded"))])
    assert after.token != before.token
    assert after.previous_token == before.token
    assert (after.bundle_id, after.producer, after.product) == (before.bundle_id, before.producer, before.product)
    folded = root.parent / after.token
    with sl.LocalRegistry.bundle(folded) as reg:
        assert _titles(reg.get(_FREQ.urn, eager=True), "ORDER") == ["folded"]
    with sl.LocalRegistry.bundle(root) as reg:
        assert _titles(reg.get(_FREQ.urn, eager=True), "ORDER") == _titles(_FREQ, "ORDER") == ["kept"], (
            "the earlier publication is untouched"
        )
    for publication in (root, folded):
        report = check_bundle(publication, at="publish")
        assert report.ok, "\n".join(map(str, report.findings))


def test_the_shipped_schema_is_the_models() -> None:
    assert overlay_schema() == generate_overlay_schema(), "regenerate src/sdmxlib/bundle/overlay.schema.json"


def test_an_artefact_level_overlay_does_not_walk_the_items(monkeypatch: pytest.MonkeyPatch) -> None:
    """No `AGENCY:ID` or artefact-URN entry can reach an item, so items are not
    visited: on a 500,000-code codelist the walk alone was 0.28 s. Counted, as
    an unchanged item comes back as itself whether visited or not."""
    visits: list[object] = []
    original = _overlay_module._Plan._item

    def counting(
        self: _overlay_module._Plan,
        item: object,
        prefix: str,
        scheme: SdmxUrn[object],
        path: str,
        own_id: str | None = None,
    ) -> object:
        visits.append(item)
        return original(self, item, prefix, scheme, path, own_id)

    monkeypatch.setattr(_overlay_module._Plan, "_item", counting)
    apply_overlays([_FREQ], [_overlay(_entry("AG:CL_FREQ"), _entry(str(_FREQ.urn), "add", "NOTE"))])
    assert visits == []
    apply_overlays([_FREQ], [_overlay(_entry("Code"))])
    assert len(visits) == 2, "a class name can reach items, so they are walked"


# ── audiences and hooks in the effective token (GH-131) ─────────────────


def _manifest(tmp_path: Path) -> Manifest:
    return write_manifest(make_bundle(tmp_path / "b", 10), bundle_id="b", producer=Producer(name="t", version="1"))


def _replace(existing: list[Annotation], new: Annotation) -> list[Annotation]:
    return [new]


def _hook(version: str = "1", name: str = "replace") -> MergeHook:
    return MergeHook(package="tests", name=name, version=version, fn=_replace)


_SCOPED = _overlay(
    _entry("AG:CL_FREQ", kind="ALL", title="everyone"),
    _entry("AG:CL_FREQ", kind="PUB", title="public", scope="public"),
    _entry("AG:CL_FREQ", kind="INT", title="internal", scope="internal"),
)


def test_an_entry_applies_to_its_audience_and_an_unscoped_one_to_every_audience() -> None:
    def kinds(audience: str | None) -> set[str]:
        (result,) = apply_overlays([_FREQ], [_SCOPED], audience=audience)
        return {a.type for a in result.annotations if a.type in {"ALL", "PUB", "INT"}}

    assert kinds("public") == {"ALL", "PUB"}
    assert kinds("internal") == {"ALL", "INT"}
    assert kinds("anyone else") == {"ALL"}
    assert kinds(None) == {"ALL", "PUB", "INT"}, "no audience applies every entry"


def test_the_token_covers_the_overlay_as_applied(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    public = effective_token(manifest, [_SCOPED], audience="public")
    assert public != effective_token(manifest, [_SCOPED], audience="internal"), "different entries applied"
    assert effective_token(manifest, [_SCOPED], audience="a") == effective_token(manifest, [_SCOPED], audience="b"), (
        "two audiences that apply the same entries share a token"
    )
    assert public == effective_token(manifest, [applied(_SCOPED, "public")]), "what is applied is what is digested"


def test_the_token_covers_each_hook(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    overlays = [_overlay(_entry("*"))]
    base = effective_token(manifest, overlays, merge={"LABEL": _hook(), "NOTE": _hook()})
    assert base != effective_token(manifest, overlays), "a hook versus the default"
    assert base != effective_token(manifest, overlays, merge={"LABEL": _hook("2"), "NOTE": _hook()}), "a version"
    assert base != effective_token(manifest, overlays, merge={"LABEL": _hook(), "NOTE": _hook(name="other")})
    assert base != effective_token(manifest, overlays, merge={"LABEL": _hook(), "ORDER": _hook()}), "another type"
    assert base == effective_token(manifest, overlays, merge={"NOTE": _hook(), "LABEL": _hook()}), "order of the map"
    assert effective_token(manifest, [], merge={"LABEL": _hook()}) == manifest.token, "no overlays, no hooks line"


def test_the_token_is_the_documented_hash(tmp_path: Path) -> None:
    """`docs/bundle.md` §5.4, recomputed from its words."""
    manifest = _manifest(tmp_path)
    overlay = _overlay(_entry("*"))
    digest = hashlib.sha256(msgspec.json.encode(overlay, order="sorted")).hexdigest()
    hooks = "LABEL=tests:replace@1,NOTE=tests:replace@2"
    text = f"v2\n{manifest.token}\n{hooks}\n{digest}"
    merge = {"NOTE": _hook("2"), "LABEL": _hook()}
    assert effective_token(manifest, [overlay], merge=merge) == hashlib.sha256(text.encode()).hexdigest()
    plain = f"v2\n{manifest.token}\ndefault\n{digest}"
    assert effective_token(manifest, [overlay]) == hashlib.sha256(plain.encode()).hexdigest()


@pytest.mark.parametrize("bad", ["", "a,b", "a=b", "a@b", "a:b", "a\nb"])
def test_a_hook_identity_holding_a_separator_is_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="the effective token separates on"):
        MergeHook(package=bad, name="n", version="1", fn=_replace)
    with pytest.raises(ValueError, match="the effective token separates on"):
        MergeHook(package="p", name="n", version=bad, fn=_replace)


def test_an_annotation_type_with_a_separator_cannot_carry_a_hook(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="the effective token separates on"):
        effective_token(_manifest(tmp_path), [_overlay(_entry("*"))], merge={"A,B": _hook()})


def test_fold_applies_only_the_audiences_entries(tmp_path: Path) -> None:
    root = make_bundle(tmp_path / "b", 10, extra=(_FREQ,))
    write_manifest(root, bundle_id="b", producer=Producer(name="t", version="1"))
    after = fold_overlays(root, [_SCOPED], audience="public")
    with sl.LocalRegistry.bundle(root.parent / after.token) as reg:
        folded = reg.get(_FREQ.urn, eager=True)
        assert folded is not None
        kinds = {a.type for a in folded.annotations}
    assert {"ALL", "PUB"} <= kinds
    assert "INT" not in kinds


# ── names and descriptions, folded only (GH-163) ───────────────────────


def _text(
    selector: str, op: str = "set", field: str = "name", lang: str = "fr", text: str | None = "x", **kw: object
) -> OverlayText:
    return msgspec.convert(
        {"selector": selector, "op": op, "field": field, "lang": lang, "text": text, **kw}, OverlayText
    )


def _texts(*texts: OverlayText) -> Overlay:
    return Overlay(format_version="1.2", id="names", entries=(), texts=texts)


_CODE_A = "urn:sdmx:org.sdmx.infomodel.codelist.Code=AG:CL_FREQ(1.0).A"


def _folded(tmp_path: Path, *overlays: Overlay, audience: str | None = None) -> tuple[Path, Path, Manifest, Manifest]:
    root = make_bundle(tmp_path / "b", 10, extra=(_FREQ,))
    before = write_manifest(root, bundle_id="b", producer=Producer(name="t", version="1"))
    after = fold_overlays(root, list(overlays), audience=audience)
    return root, root.parent / after.token, before, after


def _code_a(publication: Path) -> sl.Code:
    with sl.LocalRegistry.bundle(publication) as reg:
        codelist = reg.get(_FREQ.urn, eager=True)
        assert codelist is not None
        return codelist.codes["A"]


def test_a_folded_french_name_reads_back_and_the_earlier_publication_keeps_its_own(tmp_path: Path) -> None:
    root, folded, before, after = _folded(tmp_path, _texts(_text(_CODE_A, text="Annuel")))
    assert _code_a(folded).name.as_dict() == {"en": "Annual", "fr": "Annuel"}
    assert _code_a(root).name.as_dict() == {"en": "Annual"}
    fingerprints = {a.urn: a.fingerprint for a in before.artefacts}, {a.urn: a.fingerprint for a in after.artefacts}
    assert fingerprints[0][str(_FREQ.urn)] != fingerprints[1][str(_FREQ.urn)], "the codelist's content changed"
    assert check_bundle(folded, at="publish").ok


def test_the_most_specific_text_wins(tmp_path: Path) -> None:
    _, folded, _, _ = _folded(tmp_path, _texts(_text(_CODE_A, text="by urn"), _text("Code", text="by class")))
    assert _code_a(folded).name["fr"].text == "by urn"


def test_a_description_language_is_removed_and_a_names_last_one_is_refused(tmp_path: Path) -> None:
    described = _texts(_text(_CODE_A, field="description", lang="en", text="yearly"))
    _, folded, _, _ = _folded(
        tmp_path, described, _texts(_text(_CODE_A, op="remove", field="description", lang="en", text=None))
    )
    assert _code_a(folded).description.as_dict() == {}, "no language left"
    with pytest.raises(OverlayError, match="last language of its name"):
        _folded(tmp_path / "again", _texts(_text(_CODE_A, op="remove", lang="en", text=None)))


def test_a_text_scoped_to_another_audience_is_not_folded(tmp_path: Path) -> None:
    _, folded, _, _ = _folded(tmp_path, _texts(_text(_CODE_A, text="interne", scope="internal")), audience="public")
    assert _code_a(folded).name.as_dict() == {"en": "Annual"}


def test_serving_refuses_a_name_change_and_names_fold(tmp_path: Path) -> None:
    overlay = _texts(_text(_CODE_A))
    with pytest.raises(OverlayError, match="fold_overlays"):
        apply_overlays([_FREQ], [overlay])
    manifest = write_manifest(make_bundle(tmp_path / "b", 10), bundle_id="b", producer=Producer(name="t", version="1"))
    with pytest.raises(OverlayError, match="fold_overlays"):
        effective_token(manifest, [overlay])
    scoped = _texts(_text(_CODE_A, scope="internal"))
    assert apply_overlays([_FREQ], [scoped], audience="public") == [_FREQ], "a text for another audience is not served"


def test_an_overlay_without_texts_encodes_as_in_format_1_1() -> None:
    overlay = _overlay(_entry("*"))
    assert b"texts" not in msgspec.json.encode(overlay, order="sorted")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ({"selector": "*", "op": "set", "field": "name", "lang": "fr"}, "gives no text"),
        ({"selector": "*", "op": "remove", "field": "name", "lang": "fr", "text": "x"}, "removes, so gives no text"),
        ({"selector": "*", "op": "set", "field": "name", "lang": "", "text": "x"}, "not a well-formed language tag"),
        ({"selector": "*", "op": "set", "field": "title", "lang": "fr", "text": "x"}, "not an overlay document"),
    ],
)
def test_a_malformed_text_is_refused(tmp_path: Path, text: dict[str, object], message: str) -> None:
    path = tmp_path / "o.json"
    path.write_text(json.dumps({"format_version": "1.2", "id": "o", "entries": [], "texts": [text]}))
    with pytest.raises(OverlayError, match=message):
        read_overlay(path)
