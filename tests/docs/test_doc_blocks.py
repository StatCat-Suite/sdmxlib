"""Execute the code that is in the documentation.

The other files here assert what a page *claims*, in Python written beside it
— which is a second copy of the example and can drift from it. This runs the
fenced blocks themselves, so an example that stops working fails at the page
it is on.

See `_extract.py` for how a page's namespace is supplied and how a block opts
out.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib

import pytest

from tests.docs._expected import Recorder, claims_in
from tests.docs._extract import Block, blocks_of, page_key, user_facing_pages
from tests.docs.pages import EXECUTED, setup_for
from tests.support.repo import ROOT

pytestmark = pytest.mark.docs

_PAGES = user_facing_pages()
_ROOT = ROOT


def _runnable(block: Block) -> bool:
    return block.directive != "skip"


@pytest.mark.parametrize("page", _PAGES, ids=lambda p: p.name)
def test_every_block_on_the_page_runs(page) -> None:
    """One test per page, not per block, because the blocks are a *sequence*:
    later ones use names earlier ones bound, which is how the page reads.

    A page joins `EXECUTED` when someone writes its setup. Until then it is
    skipped *by name* rather than silently: `test_the_backlog_only_shrinks`
    counts what is left, so the work is visible and cannot quietly stall.

    Running is half of it. Where a block states what it prints —
    `print(cl.codes.ids())  # ["A", "Q", "M"]` — the claim is checked against
    what it actually prints, because a wrong answer runs exactly as well as a
    right one and the comment is what a reader believes. See `_expected`.
    """
    blocks = blocks_of(page)
    if not blocks:
        pytest.skip("no python blocks")
    if page_key(page) not in EXECUTED:
        pytest.skip(f"no setup yet — see tests/docs/pages/, {len(_PAGES) - len(EXECUTED)} pages left")

    with setup_for(page_key(page))() as namespace:
        for block in blocks:
            if not _runnable(block):
                continue
            # A fresh recorder per block: line numbers restart with each
            # `compile`, so one shared across a page would collide them.
            recorder = Recorder()
            namespace["print"] = recorder.print
            try:
                exec(compile(block.code, f"<{block}>", "exec"), namespace)  # noqa: S102
            except Exception as exc:  # noqa: BLE001
                if block.directive == "raises":
                    continue
                pytest.fail(f"{block} raised {type(exc).__name__}: {exc}\n\n{block.code}")
            if block.directive == "raises":
                # A `raises` block that runs clean is a claim the page makes
                # and the code no longer keeps — and, until this, a guard
                # that could not fail.
                pytest.fail(f"{block} is marked `test: raises` and raised nothing:\n\n{block.code}")
            wrong = recorder.mismatches(claims_in(block.code))
            if wrong:
                detail = "\n".join(wrong)
                pytest.fail(f"{block} prints something other than what it says:\n{detail}\n\n{block.code}")


def test_output_claims_are_checked_not_drifting() -> None:
    """How many `print(...)  # …` comments are prose rather than a value.

    The value/prose split is a heuristic (`_expected._as_literal`), and a
    heuristic that fails open silently is a check that decays: a comment
    reworded from `# ["A", "B"]` to `# the two codes` stops being verified and
    nothing says so. Counting them makes that a diff.

    Lower it when a page states a claim more precisely; raising it needs the
    same kind of reason as `_SKIP_BUDGET`.
    """
    prose = [
        f"{block}:{claim.line}  {claim.text}"
        for page in _PAGES
        for block in blocks_of(page)
        for claim in claims_in(block.code)
        if not claim.checkable
    ]
    assert len(prose) <= _PROSE_BUDGET, (
        f"{len(prose)} output comments are unverifiable, budget {_PROSE_BUDGET}:\n  " + "\n  ".join(prose)
    )


def test_the_output_check_is_actually_reaching_the_pages() -> None:
    """A guard on the guard.

    Every mechanism here is opt-in-by-shape, so a refactor that broke claim
    extraction would leave the suite green and checking nothing. This fails if
    the number of *verified* claims collapses.
    """
    checked = sum(claim.checkable for page in _PAGES for block in blocks_of(page) for claim in claims_in(block.code))
    assert checked >= _CHECKED_CLAIMS, f"only {checked} output claims are verified, expected at least {_CHECKED_CLAIMS}"


def test_the_backlog_only_shrinks() -> None:
    """How many pages still have no setup.

    A number in a test rather than a note in a file, because a note does not
    fail. Lower it when you add a setup; raising it means a page was added
    without one, which is the moment to notice.
    """
    remaining = [page_key(page) for page in _PAGES if page_key(page) not in EXECUTED and blocks_of(page)]
    assert len(remaining) <= _PAGES_WITHOUT_SETUP, (
        f"{len(remaining)} pages have no doc-block setup, budget {_PAGES_WITHOUT_SETUP}: {remaining}"
    )


def test_every_executed_page_exists() -> None:
    """`EXECUTED` names pages by path, so a rename would silently stop running
    one. This is what turns that into a failure."""
    known = {page_key(page) for page in _PAGES}
    assert known >= EXECUTED, f"EXECUTED names pages that are gone: {sorted(EXECUTED - known)}"


def test_skips_are_declared_not_drifting() -> None:
    """A `<!-- test: skip -->` is debt, and debt with a number beside it.

    Without this the easy fix for a failing block is a skip comment, and the
    suite quietly stops running the documentation it was written to run.
    """
    skipped = [f"{block}" for page in _PAGES for block in blocks_of(page) if block.directive == "skip"]
    assert len(skipped) <= _SKIP_BUDGET, (
        f"{len(skipped)} skipped blocks, budget {_SKIP_BUDGET}: {skipped}. "
        "Lower the budget when you remove one; raise it only with a reason."
    )


#: How many blocks are allowed to opt out. **Zero**, and it got there: the
#: last six were a fragment naming an unbound `latest`, two placeholder
#: predicates, a polars claim nothing checked, a bundle path only the reader
#: had, and a FastAPI handler with two undefined names in it. Each was a
#: defect the skip was hiding, which is the argument for keeping this at 0 —
#: raising it is a visible diff and needs a reason in the MR.
_SKIP_BUDGET = 0

#: `print(...)  # …` comments that are prose, not a value to compare against.
#: Legitimate — `# falls back to "Annual"`, `# 250+` — but counted, because the
#: value/prose split is a heuristic and an uncounted heuristic decays.
_PROSE_BUDGET = 10

#: A floor under the number of output claims actually verified, so a refactor
#: that quietly stopped extracting them fails instead of passing green.
_CHECKED_CLAIMS = 148

#: Pages whose blocks are not executed yet. Every user-facing page now has a
#: setup; the check stays so a *new* page arrives as a failure rather than as
#: silently-unrun documentation.
_PAGES_WITHOUT_SETUP = 0


def test_the_web_framework_is_test_only() -> None:
    """`fastapi` is here for one documentation block and must stay there.

    `docs/cookbook/rest_facade.md` ends on the FastAPI handler the whole page
    builds toward, and that block is where the kit finally composes — so it is
    worth importing to check. It is *not* worth imposing on every consumer:
    sdmxlib has no web framework, and the facade page's whole premise is that
    the framework is yours.

    Same boundary as `test_sqlglot_is_test_only`, and same reason for asserting
    it: a dependency added for the suite drifts into the runtime set by nobody
    deciding to.
    """
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = pyproject["project"]["dependencies"]
    assert not [d for d in runtime if "fastapi" in d], f"fastapi leaked into runtime deps: {runtime}"
    assert [d for d in pyproject["dependency-groups"]["dev"] if "fastapi" in d], "expected in the dev group"


def test_importing_sdmxlib_pulls_in_no_web_framework() -> None:
    """The declaration above is a promise; this is the observation."""
    result = subprocess.run(
        [sys.executable, "-c", "import sys, sdmxlib; print('fastapi' in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
        cwd=_ROOT,
    )
    assert result.stdout.strip() == "False", result.stdout
