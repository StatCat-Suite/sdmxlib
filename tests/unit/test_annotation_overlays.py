"""Annotation overlays and the effective token (GH-70).

An overlay changes display annotations, and nothing else, without a rebuild.
These pin what a server and a cache rely on: which entry wins, what each
operation does, that items and nested items are reachable, that what nothing
selects is returned untouched, that the result is the same in every wire
format, and that the effective token moves with the overlays and never with
the bundle's own token.
"""

import json
from pathlib import Path

import msgspec
import pytest

import sdmxlib as sl
from sdmxlib.bundle import (
    Overlay,
    OverlayAnnotation,
    OverlayEntry,
    OverlayError,
    Producer,
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


def _entry(selector: str, op: str = "set", kind: str = "LABEL", title: str | None = "x") -> OverlayEntry:
    annotation = None if op == "remove" else OverlayAnnotation(title=title)
    return msgspec.convert(
        {"selector": selector, "op": op, "type": kind, "annotation": annotation and msgspec.to_builtins(annotation)},
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

    (merged,) = apply_overlays(
        [_FREQ], [_overlay(_entry("AG:CL_FREQ", "set", "ORDER", "new"))], merge={"ORDER": keep_both}
    )
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
    manifest = write_manifest(make_bundle(tmp_path / "b", 10), bundle_id="b", producer=Producer(name="t", version="1"))
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
    assert read_manifest(tmp_path / "b").token == manifest.token, "applying overlays never touches the bundle"


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


def test_fold_writes_overlays_into_the_bundle_and_republishes(tmp_path: Path) -> None:
    root = make_bundle(tmp_path / "b", 10, extra=(_FREQ,))
    before = write_manifest(root, bundle_id="b", producer=Producer(name="t", version="1"), product={"edition": 1})
    after = fold_overlays(root, [_overlay(_entry("AG:CL_FREQ", "set", "ORDER", "folded"))])
    assert after.token != before.token
    assert (after.bundle_id, after.producer, after.product) == (before.bundle_id, before.producer, before.product)
    with sl.LocalRegistry.bundle(root) as reg:
        assert _titles(reg.get(_FREQ.urn, eager=True), "ORDER") == ["folded"]
    report = check_bundle(root, references=False)
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
