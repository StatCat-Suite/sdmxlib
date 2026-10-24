"""`docs/bundle.md` restates the shipped schemas, so it can fall behind them (GH-98).

The schemas are generated from the model and guarded against it
(`test_the_shipped_schema_is_the_models`); this keeps the specification's
field tables, its format versions and its worked digest in step with them.
"""

import json
import re
from pathlib import Path

import msgspec
import pytest

from sdmxlib.bundle import (
    FORMAT_VERSION,
    OVERLAY_FORMAT,
    MergeHook,
    Overlay,
    Producer,
    effective_token,
    manifest_schema,
    overlay_digest,
    overlay_schema,
    write_manifest,
)
from tests.support.bundles import make_bundle
from tests.support.narrow import present
from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

_SPEC = ROOT / "docs" / "bundle.md"

#: A heading naming a schema definition, then its table's rows up to the next blank line.
_TABLE = re.compile(
    r"^#{3,4} `(\w+)`\n\n(?:(?:[^|\n].*\n)+\n)?"  # the heading, then any paragraph
    r"\| field \| presence \| meaning \|\n\| --- \| --- \| --- \|\n((?:\|.*\n)+)",  # the table
    re.MULTILINE,
)
_ROW = re.compile(r"^\| `(\w+)` \| (required|optional) \|", re.MULTILINE)


def _tables(text: str) -> dict[str, dict[str, str]]:
    """Each documented definition's fields, by name, to ``required`` or ``optional``."""
    return {name: dict(_ROW.findall(rows)) for name, rows in _TABLE.findall(text)}


def _schema_fields() -> dict[str, dict[str, str]]:
    """Each definition in the two shipped schemas, as `_tables` reads the page."""
    found: dict[str, dict[str, str]] = {}
    for raw in (manifest_schema(), overlay_schema()):
        for name, definition in json.loads(raw)["$defs"].items():
            required = set(definition.get("required", ()))
            found[name] = {f: "required" if f in required else "optional" for f in definition["properties"]}
    return found


def test_the_spec_documents_every_schema_definition_field_by_field() -> None:
    documented = _tables(_SPEC.read_text(encoding="utf-8"))
    shipped = _schema_fields()
    assert len(shipped) == 9, (
        "Manifest, Producer, BundleFile, BindingCount, ArtefactEntry, Provenance and three overlay types"
    )
    assert documented == shipped, "docs/bundle.md's field tables differ from the shipped schemas"


def test_the_table_reader_sees_a_missing_or_mislabelled_field() -> None:
    """Discrimination: a page that drops a field, or calls a required one optional, must not read as equal."""
    text = _SPEC.read_text(encoding="utf-8")
    dropped = text.replace("| `token` | required |", "| `tokn` | required |", 1)
    relabelled = text.replace("| `sha256` | required |", "| `sha256` | optional |", 1)
    assert dropped != text
    assert relabelled != text
    assert _tables(dropped) != _schema_fields()
    assert _tables(relabelled) != _schema_fields()


def test_the_spec_names_the_formats_this_build_writes() -> None:
    text = _SPEC.read_text(encoding="utf-8")
    assert f"**manifest format {FORMAT_VERSION}**" in text
    assert f"**overlay format {OVERLAY_FORMAT}**" in text
    assert f'`"{FORMAT_VERSION}"` for this page' in text
    assert f'`"{OVERLAY_FORMAT}"` for this page' in text


def test_the_worked_digest_is_what_sdmxlib_computes() -> None:
    """The example in §5.4 is what another implementation checks itself against."""
    text = _SPEC.read_text(encoding="utf-8")
    example, canonical, digest = present(
        re.search(
            r"This overlay:\n\n```json\n(.*?)```\n\n"
            r"has the canonical form\n\n```text\n(.*?)\n```\n\nand the digest `(\w+)`",
            text,
            re.DOTALL,
        )
    ).groups()
    overlay = msgspec.json.decode(example.encode(), type=Overlay)
    assert msgspec.json.encode(overlay, order="sorted").decode() == canonical
    assert overlay_digest(overlay) == digest


def test_the_worked_tokens_are_what_sdmxlib_computes(tmp_path: Path) -> None:
    """§5.4's two effective tokens, with and without a hook, from its own overlay and bundle token."""
    text = _SPEC.read_text(encoding="utf-8")
    example = present(re.search(r"This overlay:\n\n```json\n(.*?)```", text, re.DOTALL)).group(1)
    token, plain, hooked = present(
        re.search(
            r"bundle whose token is\n`([^`]+)`.*?the effective token is\n`(\w+)`.*?the token is\n`(\w+)`",
            text,
            re.DOTALL,
        )
    ).groups()
    published = write_manifest(make_bundle(tmp_path / "b", 10), bundle_id="b", producer=Producer(name="t", version="0"))
    manifest = msgspec.structs.replace(published, token=token)
    overlay = msgspec.json.decode(example.encode(), type=Overlay)
    assert effective_token(manifest, [overlay]) == plain
    hook = MergeHook(package="acme-labels", name="notes", version="1.2", fn=lambda existing, new: [new])
    assert effective_token(manifest, [overlay], merge={"NOTE": hook}) == hooked
