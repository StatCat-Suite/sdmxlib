"""`tools/mkdocs_issue_links.py`: the docs site links the tracker references the source keeps bare (GH-85).

Each case is an exact before and after, so a reference linked twice, linked
inside code, or linked inside a link's target fails as surely as one missed.
"""

import importlib.util
import re

import pytest

from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

_spec = importlib.util.spec_from_file_location("mkdocs_issue_links", ROOT / "tools" / "mkdocs_issue_links.py")
assert _spec is not None
assert _spec.loader is not None
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)

_REPO = "https://github.com/StatCat-Suite/sdmxlib"
_ISSUE = f"{_REPO}/blob/main/history/gitlab/issues"


def _link(text: str) -> str:
    return str(hook.link_references(text, _REPO))


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("Measured in #271.", f"Measured in [#271]({_ISSUE}/271.md)."),
        (
            "rebuilt for this (!304).",
            f"rebuilt for this ([!304]({_REPO}/blob/main/history/gitlab/merge_requests/304.md)).",
        ),
        ("Milestone %15's rule", f"Milestone [%15]({_REPO}/blob/main/history/gitlab/milestones/15.md)'s rule"),
        ("the bundle spec (GH-98)", f"the bundle spec ([GH-98]({_REPO}/issues/98))"),
        ("## The intermediate representation (#273)", f"## The intermediate representation ([#273]({_ISSUE}/273.md))"),
        ("| replace (#294) |", f"| replace ([#294]({_ISSUE}/294.md)) |"),
        ("#255, #261 and #292", f"[#255]({_ISSUE}/255.md), [#261]({_ISSUE}/261.md) and [#292]({_ISSUE}/292.md)"),
    ],
)
def test_a_reference_becomes_a_link(before: str, after: str) -> None:
    assert _link(before) == after


@pytest.mark.parametrize(
    "text",
    [
        "## A heading",
        "the `is_in` path, `#411` in code",
        "See [the example](#9-full-example) and [#422](https://example.org/422).",
        "<https://example.org/#12> and https://example.org/page#12",
        "see page.md#12, item#3, v1.2#4 and 2#5",
        "about 50% of rows, 50%, and a != b",
        'an HTML <span id="#12"> tag',
        "![image](pic.png) and [GH-19](https://github.com/StatCat-Suite/sdmxlib/issues/19)",
        "#12-anchor-slug",
    ],
)
def test_code_links_urls_paths_and_headings_are_left_alone(text: str) -> None:
    assert _link(text) == text


def test_a_fenced_block_is_left_alone_and_the_text_after_it_is_not() -> None:
    before = "```python\nx = 1  # see #411\n```\nAfter the fence, #411."
    after = f"```python\nx = 1  # see #411\n```\nAfter the fence, [#411]({_ISSUE}/411.md)."
    assert _link(before) == after


def test_the_hook_reads_repo_url_from_the_config() -> None:
    class Config:
        repo_url = _REPO + "/"

    assert hook.on_page_markdown("see #1", page=None, config=Config(), files=None) == f"see [#1]({_ISSUE}/1.md)"


def test_the_design_docs_are_where_the_links_land() -> None:
    """Reach: the design pages carry the references this hook exists for.

    A floor, not an exact count, since the pages change: 297 links when this
    landed. Every link targets a file the export holds, so a reference to an
    item the archive lacks would fail here rather than 404 on the site.
    """
    targets: list[str] = []
    for page in sorted((ROOT / "docs" / "design").rglob("*.md")):
        targets += re.findall(
            r"\]\(" + re.escape(_REPO) + r"/blob/main/(history/gitlab/[a-z_]+/\d+\.md)\)",
            _link(page.read_text(encoding="utf-8")),
        )
    assert len(targets) >= 280, "the design docs cite the tracker a few hundred times"
    missing = sorted({t for t in targets if not (ROOT / t).is_file()})
    assert missing == [], "a reference to an item history/gitlab/ does not hold"
