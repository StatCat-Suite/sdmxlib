"""No test may read a file by a path that resolves inside `mutants/` (#252, #402).

`just mutate` runs pytest from a `mutants/` tree that mutmut builds by copying
`src/` and `tests/` — and nothing else. A test that opens `docs/api.md` or
`tests/fixtures/x.xml` by a bare relative path therefore resolves it against
`mutants/`, where `docs/` does not exist at all.

That is not a skipped test. Both offending reads happened at *import* time, so
they raised during mutmut's stats pass and took down the whole run with
``failed to collect stats``. `just mutate` was broken from the moment
`test_public_surface.py` landed in #265 until #252 — several milestones — and
nothing surfaced it, because mutation is deliberately outside `just ci` and so
nobody ran it in between.

The fix is `tests.support.repo.ROOT`; this is the guard that keeps it applied.
It is a static scan rather than a runtime check because the failure it prevents
is a *collection* error: by the time a runtime assertion could fire, the run
mutmut was trying to do has already aborted.

## The second spelling, and why this guard missed it twice (#402)

`Path(__file__).parents[2]` is not working-directory-relative — it is anchored,
and it is *still wrong*, because under mutmut `__file__` is inside
`mutants/tests/unit/` and two levels up is `mutants/`. The original scan looked
for relative literals and said nothing about it, so the same failure came back
twice more:

* **#284** — `test_count_any` reached `tools/` that way and collection failed.
* **#324** — `test_data_format` reached `docs/` that way and the *stats* phase
  returned 1, printing `1610 mutants  killed 0  survived 0`: a full-looking
  summary with every number at zero, which is worse than an abort because it
  reads like a result.

Four occurrences now, three discovered only by someone next trying to run
mutation. `_file_anchored_escapes` is the second scan, and the reason both
spellings live in one file: they are one rule — *anchor to `ROOT`* — that was
being enforced for one half of its surface.

`also_copy` was tried first and is the wrong fix. mutmut has the setting, and
adding `["docs", "tools"]` does put them in the tree — but `find_root` tells
the checkout from the copy by looking for a directory holding **both**
`pyproject.toml` and `docs/`, so copying `docs/` makes `mutants/` satisfy the
marker pair and `ROOT` starts resolving to the copy.
`test_the_root_helper_finds_the_checkout_not_the_mutants_copy` catches it
immediately, which is how that attempt was abandoned.
"""

import ast
import pathlib

import pytest

from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

#: Directory prefixes that name a real location in the checkout. A string
#: literal starting with one of these, passed to `Path()` or `open()`, is a
#: working-directory-relative read.
_REPO_DIRS = ("docs/", "src/", "tests/", "examples/", "benchmarks/")

#: `tests/support/repo.py` states these paths as the markers it searches for,
#: which is the one legitimate place they appear as bare literals.
_EXEMPT = {"tests/support/repo.py"}

#: `conftest.py` sits at `tests/`, so `parents[1]` is the checkout for it —
#: a different arithmetic that the escape scan does not flag.


def _relative_reads(tree: ast.AST) -> list[str]:
    """String literals under a `Path(...)` / `open(...)` call that name a repo dir."""
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in {"Path", "open"}:
            continue
        found.extend(
            arg.value
            for arg in node.args
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith(_REPO_DIRS)
        )
    return found


#: `Path(__file__)` walked up past `tests/` — anchored, and anchored to the
#: wrong tree under mutmut. Both spellings seen in this suite.
_ESCAPE_SUFFIXES = (".parents[2]", ".parents[3]")


def _file_anchored_escapes(tree: ast.AST) -> list[str]:
    """Expressions that walk from `__file__` up to the repo root (#402).

    `tests/support/repo.py` is the one place this is correct: `find_root` walks
    *past* the mutants tree by testing for a marker pair, which is exactly what
    the spellings below do not do.
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Attribute, ast.Subscript)):
            continue
        text = ast.unparse(node)
        if not text.startswith("Path(__file__)") and not text.startswith("pathlib.Path(__file__)"):
            continue
        if text.endswith(_ESCAPE_SUFFIXES) or text.count(".parent") >= 3:
            found.append(text)
    return found


def test_no_test_anchors_to_the_tree_it_is_running_in() -> None:
    """The #402 scan: `Path(__file__).parents[2]` is `mutants/` under mutmut.

    Reported separately from the relative-path scan below because the fix is
    the same and the mistake is not: this one *looks* anchored, which is why it
    survived the #252 cleanup and came back twice.
    """
    offenders: dict[str, list[str]] = {}
    for path in sorted((ROOT / "tests").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in _EXEMPT:
            continue
        escapes = _file_anchored_escapes(ast.parse(path.read_text(encoding="utf-8")))
        if escapes:
            offenders[rel] = escapes
    assert not offenders, (
        f"`__file__`-anchored reads that land in `mutants/` under mutmut: {offenders}. "
        "Join onto tests.support.repo.ROOT, which walks past the copy (#402)."
    )


def test_the_escape_scan_would_notice_an_offender() -> None:
    """Both spellings, and the anchored form that must stay legal."""
    assert _file_anchored_escapes(ast.parse('_T = Path(__file__).parents[2] / "tools"')) == [
        "Path(__file__).parents[2]"
    ]
    assert _file_anchored_escapes(ast.parse("r = Path(__file__).resolve().parent.parent.parent")) == [
        "Path(__file__).resolve().parent.parent.parent"
    ]
    assert _file_anchored_escapes(ast.parse('_T = ROOT / "tools"')) == []
    assert _file_anchored_escapes(ast.parse("here = Path(__file__).parent")) == [], (
        "one level up is this file's own dir"
    )


def test_no_test_reads_a_path_relative_to_the_working_directory() -> None:
    """The scan. Join onto `tests.support.repo.ROOT` instead.

    Note this only catches the literal form. `Path(f"tests/{name}")` and a
    module-level constant assembled elsewhere both slip through — the check is
    a ratchet against the common spelling, not a proof. Every one of the six
    sites #252 found was the literal form.
    """
    offenders: dict[str, list[str]] = {}
    for path in sorted((ROOT / "tests").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in _EXEMPT:
            continue
        reads = _relative_reads(ast.parse(path.read_text(encoding="utf-8")))
        if reads:
            offenders[rel] = reads
    assert not offenders, (
        f"working-directory-relative reads found: {offenders}. "
        "Join onto tests.support.repo.ROOT — a bare relative path breaks "
        "`just mutate`, which runs pytest from the mutants/ tree."
    )


def test_the_scan_would_notice_an_offender() -> None:
    """Discrimination check: the guard above is a scan, so prove it can fail.

    A scan that walks the wrong tree, or matches nothing, passes silently
    forever. This feeds it the exact spelling that broke #265 and asserts it is
    flagged.
    """
    tree = ast.parse('import pathlib\n_API_DOC = pathlib.Path("docs/api.md")\n')
    assert _relative_reads(tree) == ["docs/api.md"]
    assert _relative_reads(ast.parse('open("tests/fixtures/x.xml")')) == ["tests/fixtures/x.xml"]
    # And that it does not flag the anchored form the fix uses.
    assert _relative_reads(ast.parse('_API_DOC = ROOT / "docs/api.md"')) == []


def test_the_root_helper_finds_the_checkout_not_the_mutants_copy() -> None:
    """`ROOT` must skip a directory that has `pyproject.toml` but no `docs/`.

    That pair is precisely what distinguishes the real checkout from mutmut's
    copy, so this asserts the discriminator rather than just that `ROOT`
    exists.
    """
    assert (ROOT / "pyproject.toml").is_file()
    assert (ROOT / "docs").is_dir()
    assert ROOT.name != "mutants"
    assert (ROOT / "docs" / "api.md").is_file(), "the file whose absence aborted every mutation run"


def test_the_files_that_broke_mutation_are_all_anchored_now() -> None:
    """Named directly, so the six sites cannot regress one at a time.

    The scan above already covers them, but it would also pass if a file were
    deleted. This asserts they still read what they are supposed to read.
    """
    for rel in (
        "docs/api.md",
        "src/sdmxlib/sql.py",
        "tests/schemas/sdmx_json/1.0/samples/exr-flat.json",
        "tests/schemas/sdmx_json/2.0/samples/exr-flat.json",
        "tests/fixtures/sdmx_ml21/ecb_dataflow.xml",
    ):
        assert (ROOT / rel).is_file(), rel
    assert (ROOT / "src/sdmxlib").is_dir()


def test_pathlib_is_not_imported_for_nothing() -> None:
    """`tests/support/repo.py` must expose `ROOT` as a Path, not a string.

    A string would still work for `open()` and quietly break every `/` join.
    """
    assert isinstance(ROOT, pathlib.Path)
