"""The docstrings the API reference renders carry no bare issue reference (GH-85).

`docs/api.md` renders each `:::` target with mkdocstrings, which shows a
class's public members and a module's own public objects. A reader there sees
`#492` as a number on a tracker they have no reason to open, and GitHub links
a bare `#N` to the wrong issue, so CLAUDE.md's "Issue references" asks for a
full link or nothing. GH-85 took 239 out; this keeps them out.

The walk mirrors mkdocstrings' defaults (`filters: ["!^_[^_]"]`, no inherited
members) through griffe, the library mkdocstrings itself reads the source
with. The issue and link patterns are `tools/check_prose.py`'s, so the user
docs and the API reference are held to one definition.
"""

import functools
import importlib.util
import re

import griffe
import pytest

from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

_spec = importlib.util.spec_from_file_location("check_prose", ROOT / "tools" / "check_prose.py")
assert _spec is not None
assert _spec.loader is not None
check_prose = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_prose)


def _bare_references(text: str) -> list[str]:
    """The issue references in `text` outside a Markdown link."""
    return [str(ref) for ref in check_prose.ISSUE_RE.findall(check_prose.LINK_RE.sub("", text))]


@functools.cache
def _rendered() -> dict[str, str]:
    """Every docstring the API reference renders, by dotted path."""
    targets = re.findall(r"^::: ([\w.]+)", (ROOT / "docs" / "api.md").read_text(encoding="utf-8"), re.MULTILINE)
    package = griffe.load("sdmxlib", search_paths=[str(ROOT / "src")], resolve_aliases=True, resolve_external=False)
    seen: dict[str, griffe.Object] = {}

    def walk(obj: griffe.Object | griffe.Alias, *, top: bool) -> None:
        target = obj.final_target if isinstance(obj, griffe.Alias) else obj
        if target.path in seen:
            return
        seen[target.path] = target
        if target.is_class or (target.is_module and top):
            for name, member in target.members.items():
                if name.startswith("_") and name != "__init__":
                    continue
                if target.is_module and member.is_alias:
                    continue  # an import, rendered where it is defined
                walk(member, top=False)

    for path in targets:
        walk(package if path == "sdmxlib" else package[path.removeprefix("sdmxlib.")], top=True)
    return {path: obj.docstring.value for path, obj in seen.items() if obj.docstring}


def test_no_rendered_docstring_carries_a_bare_issue_reference() -> None:
    found = {path: refs for path, text in _rendered().items() if (refs := _bare_references(text))}
    assert found == {}, "state the fact, or link the issue in full (CLAUDE.md, Issue references)"


def test_the_walk_reaches_what_the_api_reference_renders() -> None:
    """The guard above passes on an empty walk, so its reach is checked by name.

    A class target, a public member of one, a module target's own function, and
    the one rendered docstring that links an issue in full. The size is a floor
    rather than an exact count, because every new public member adds to it: 706
    of the 1,996 rendered objects carried a docstring when this landed.
    """
    rendered = _rendered()
    assert "sdmxlib.local.registry.LocalRegistry" in rendered
    assert "sdmxlib.api.registry.RestRegistry.get" in rendered
    assert "sdmxlib.model.codelist.Codelist.apply_extensions" in rendered
    assert "sdmxlib.bundle._write.write_manifest" in rendered
    assert "issues/19" in rendered["sdmxlib.bundle._overlay.apply_overlays"]
    assert len(rendered) >= 650, "a discovery floor: the walk must reach the API reference, not a stub of it"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Refused as it is built (#492).", ["#492"]),
        ("Opened before the store (GH-57).", ["GH-57"]),
        ("See [GH-19](https://github.com/StatCat-Suite/sdmxlib/issues/19).", []),
        ("A relative path such as a/#12 is not one.", []),
    ],
)
def test_a_bare_reference_is_found_and_a_link_is_not(text: str, expected: list[str]) -> None:
    assert _bare_references(text) == expected
