"""Run the code that is *in* the documentation, not a copy of it.

The doc tests beside this file assert what each page **claims**. They are
hand-written, which means they are a second copy of the example and can drift
from it: edit a fenced block and nothing fails.

This runs the blocks themselves. A page's Python is extracted in order and
executed in one namespace, so block *n* sees what block *n-1* bound — which is
how the pages read, and why they are full of names like `reg` and `ds` that no
single block defines.

Those names come from a **setup** in `tests/docs/pages/<page>.py`, not from the
page: a fenced `import` block that existed only to satisfy a test would be
noise for the reader, and hiding one would be worse. The setup supplies the
conventional vocabulary — `sl`, `q`, `pl`, `reg`, `cl`, `ds` — against local
fixtures and stubbed HTTP, so a page about a live provider still runs.

Directives are HTML comments, invisible in the rendered page:

    <!-- test: skip -->      the next block is a fragment, or is illustrative
    <!-- test: raises -->    the next block is *expected* to raise

`skip` is a debt marker, not an escape: `test_skips_are_declared_not_drifting`
holds the count, so adding one is a visible diff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tests.support.repo import ROOT

DOCS = ROOT / "docs"

_BLOCK = re.compile(r"(?:<!--\s*test:\s*(?P<directive>[a-z]+)\s*-->\s*\n)?```python\n(?P<code>.*?)```", re.DOTALL)


@dataclass(frozen=True)
class Block:
    """One fenced block, with where it came from and what to do with it."""

    page: str
    index: int
    code: str
    directive: str | None

    def __str__(self) -> str:
        return f"{self.page}#{self.index}"


def blocks_of(page: Path) -> list[Block]:
    """Every python block in `page`, in document order."""
    text = page.read_text(encoding="utf-8")
    return [
        Block(page=page_key(page), index=i, code=m.group("code"), directive=m.group("directive"))
        for i, m in enumerate(_BLOCK.finditer(text))
    ]


def page_key(page: Path) -> str:
    """How a page is named in `SETUPS` — its path under `docs/`, or the repo root's."""
    return page.relative_to(DOCS if page.is_relative_to(DOCS) else ROOT).as_posix()


#: Pages outside `docs/` that teach the API: the README, and the two files an
#: LLM agent reads instead of the site. Nothing ran the llms files until they
#: were found teaching a `get` and a `query` that no longer existed.
ROOT_PAGES = ("README.md", "llms.txt", "llms-dsl.txt")


def user_facing_pages() -> list[Path]:
    """Tutorials, cookbook, the front page, and `ROOT_PAGES`.

    `design/` is excluded deliberately: those pages argue about the library
    with snippets that are often *counter*-examples — the shape that was
    rejected, or a rewrite shown mid-way — and running them would assert the
    wrong thing.
    """
    docs = [page for page in DOCS.rglob("*.md") if "design/" not in page.relative_to(DOCS).as_posix()]
    return sorted([*docs, *(ROOT / name for name in ROOT_PAGES)])
