"""Every type in a public signature must be nameable (#265).

The complaint was that the filter-DSL chain is typed in private names, and that
one of them — ``sort_by(key: _Ref)`` — appeared in the published API reference.

The published-signature half **resolved itself** while the milestone ran:
#267 moved the ref hierarchy into `sdmxlib.model._query_common` with plain
names (CLAUDE.md rule 4: a private *module* holding plainly-named
package-internal API), and #294/#295 replaced `sort_by(key: _Ref)` with
``order_by(*keys: ColumnRef[...] | Descending[ColumnRef[...]])``. Verified
below by walking everything ``docs/api.md`` renders.

What was left is narrower than the issue's framing, and is the part that
actually bites: the types appearing in our public signatures could not be
*imported* from anywhere public. A user writing

    def sort_key() -> ColumnRef[CodeRow]: ...

had to reach into `_query_common`. Eleven types were in that state. Exporting
them is the whole fix; no code moved.

## What stays private, and why that is not the same problem

- **Leaf refs** — `_IdRef`, `_AgencyRef`, `_PathRef`, `_LevelRef`. A user never
  names one: they name the base, `ColumnRef[CodeRow]`, which the leaf
  satisfies. Ten more of these per DSL would add ten unusable names.
- **Accessors** — `_CodeAccessor` and friends. Reached through the singleton
  (`q.code`), never constructed.
- **Predicate classes** — `Eq`, `StringMatch`, `InSet`, `And` … Plainly named
  inside a private module, exactly as rule 4 prescribes, and not exported.
  `Predicate` is the boundary, it *is* exported, and it is what `filter()`
  takes. The issue is explicit that these should stay non-public, and the
  roadmap agrees: #254, #262 and #292 each changed this set, and every one of
  those was free because no name was published.

The ecosystem check the issue asks for lands the same way: polars exposes
`pl.Expr` and SQLAlchemy `ColumnElement` — the *base* a user annotates — not
the hundred leaf classes underneath.
"""

import annotationlib
import builtins
import importlib
import inspect
import re

import pytest

import sdmxlib as sl
from sdmxlib.query.accessors import q
from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

_API_DOC = ROOT / "docs/api.md"

#: Classes whose public methods form the documented surface. Read from
#: `docs/api.md` rather than hand-listed, so a class added to the reference is
#: covered the day it is added.
_RENDERED = re.findall(r"^::: ([\w.]+)$", _API_DOC.read_text(encoding="utf-8"), re.MULTILINE)

#: The filter-DSL chain, which is what this issue is about. Scoped
#: deliberately: run over every rendered class, the same check flags `Callable`
#: and `Optional` (stdlib), `Expr` and `LazyFrame` (polars), and a handful of
#: our own types that live outside this milestone — `SupportsUrn`,
#: `AncestorMap`. Those are a real question about whole-library export hygiene
#: and a separate one (#299); folding them in here would produce a guard that
#: is 90% allow-list, which is a guard nobody maintains.
#: The four the #265 guard was written for. Kept only so
#: `test_no_private_name_appears_in_a_rendered_signature` still names what it
#: was built to check.
_DSL_CLASSES = [
    "sdmxlib.query.query.Query",
    "sdmxlib.query.query.Select",
    "sdmxlib.query.query.Grouped",
    "sdmxlib.query.query.Page",
]


def _exported_classes() -> list[str]:
    """Every class in `sdmxlib.__all__`, as a dotted path.

    Derived, because the hand-listed alternative is what let #299 happen: the
    importability guard below was written for the query DSL and walked four
    classes out of the hundred-and-fifty-odd `__all__` carries. `DataStructure`,
    `Session`, `LazyHierarchy`, `InMemoryRegistry` and `DuckDBDataStore` were
    never looked at, and each of them named a type a caller could not import.

    The docstring on that guard already claimed to be derived rather than
    hand-listed. It was — of the *types inside* a signature. The *classes whose
    signatures get read* were the list nobody revisited.
    """
    out: list[str] = []
    for name in sl.__all__:
        obj = getattr(sl, name, None)
        if inspect.isclass(obj):
            out.append(f"{obj.__module__}.{obj.__qualname__}")
    return out


def _resolve(dotted: str) -> object | None:
    module, _, name = dotted.rpartition(".")
    try:
        found: object = getattr(importlib.import_module(module), name)
    except ImportError, AttributeError:
        return None
    return found


def _public_signatures(dotted: str):
    obj = _resolve(dotted)
    if obj is None or not inspect.isclass(obj):
        return
    for name, member in vars(obj).items():
        if name.startswith("_") or not callable(member):
            continue
        try:
            # As written: since PEP 649 a plain `signature` evaluates annotations, and
            # a name imported for type checking alone would raise rather than render.
            rendered = str(inspect.signature(member, annotation_format=annotationlib.Format.STRING))
        except ValueError, TypeError:
            continue
        # A PEP 695 bound is part of the signature a caller has to satisfy, and
        # `inspect.signature` does not render it: `def query[R: RowKind](self,
        # root: Cols[R])` prints as `(self, root: Cols[R])`, so `RowKind` was
        # invisible to this guard while being exactly the name a caller writing
        # their own `def helper[R: RowKind](...)` needs to import. Appended
        # rather than parsed — the caller regex only wants the words.
        bounds = " ".join(
            str(getattr(param, "__bound__", "") or getattr(param, "__constraints__", ""))
            for param in getattr(member, "__type_params__", ())
        )
        yield name, f"{rendered} {bounds}"


def test_no_private_name_appears_in_a_rendered_signature() -> None:
    """The issue's headline gate: ``docs/api.md`` must not publish a `_Name`.

    mkdocstrings has nothing to link such a name to, and the underscore tells
    the reader not to use the very thing the signature requires.

    Scoped to *our* private names. ``pathlib._local.Path`` shows up on Python
    3.13+ because that is where the stdlib now defines a thoroughly public
    class, and flagging it would make the guard noisy — which is how guards get
    deleted.
    """
    ours = re.compile(r"\bsdmxlib[\w.]*\._\w+|\b_[A-Z]\w*")
    offenders: list[str] = []
    for dotted in _RENDERED:
        for name, sig in _public_signatures(dotted):
            hits = [h for h in ours.findall(sig) if "pathlib" not in h]
            if hits:
                offenders.append(f"{dotted}.{name}{sig} -> {hits}")
    assert not offenders, "private names in the published API reference:\n  " + "\n  ".join(offenders)


def test_every_type_in_a_public_signature_is_importable_from_sdmxlib() -> None:
    """The gap that remained, and the one a user actually hits.

    A signature naming a type the caller cannot import is a signature the
    caller cannot satisfy in an annotation. Eleven types were in that state
    before #265 — `ColumnRef`, `Descending`, `Projection`, `AggregateRef`,
    `AggPredicate`, `FrameQuery`, `GroupBy` and the four ``RowKind`` types.

    Derived from the signatures rather than hand-listed, so a new parameter
    type has to be exported or explicitly allowed, on the day it lands.
    """
    #: Names that appear in signatures and are legitimately not ours to export,
    #: each mapped to the module it really comes from.
    #
    # The mapping is not decoration: it is *checked* below, so an entry that is
    # misspelled, or that names a type the owning library has since renamed,
    # fails instead of silently widening the allow-list. A flat set of bare
    # names — which is what this was — also cannot tell "polars owns DataFrame"
    # from "one of ours happens to be called DataFrame", so a real export
    # regression could hide behind an allow-list entry meant for someone else.
    #
    # Every module here is a baseline runtime dependency or the standard
    # library, so naming one of these in a signature commits a caller to
    # nothing they do not already have. That — not "is it ours" — is the test
    # this mapping encodes.
    foreign = {
        "DataFrame": "polars",
        "LazyFrame": "polars",
        "Expr": "polars",
        "RecordBatchReader": "pyarrow",
        "DuckDBPyConnection": "duckdb",
        "Path": "pathlib",
        "Callable": "collections.abc",
        "Collection": "collections.abc",
        "Generator": "collections.abc",
        "Iterable": "collections.abc",
        "Iterator": "collections.abc",
        "ItemsView": "collections.abc",
        "KeysView": "collections.abc",
        "ValuesView": "collections.abc",
        "Mapping": "collections.abc",
        "Sequence": "collections.abc",
        "Any": "typing",
        "Literal": "typing",
        "Never": "typing",
        "Self": "typing",
    }
    for candidate, module in sorted(foreign.items()):
        assert hasattr(importlib.import_module(module), candidate), (
            f"`foreign` claims {candidate!r} comes from {module!r}, but it is not there. "
            f"Either the entry is stale, or {candidate!r} is ours and needs exporting."
        )
    #: Rendered signatures contain keywords the capitalised-word regex picks up
    #: as if they were types. They are not names at all, so they get no module.
    literals = {"None", "True", "False"}

    # `__all__`, not `dir(sl)`. `dir` sees every name the package happens to
    # import, so a type could be reachable by accident and the guard would call
    # that a pass — measured: dropping `ColumnRef` from `__all__` did not fail
    # this test until it was changed. `__all__` is the declared contract, and
    # it is what `import *` and the doc tooling read.
    exported = set(sl.__all__)
    missing: dict[str, set[str]] = {}
    for dotted in _exported_classes():
        for name, sig in _public_signatures(dotted):
            for candidate in re.findall(r"\b[A-Z]\w+", sig):
                if candidate in foreign or candidate in literals or candidate in exported:
                    continue
                missing.setdefault(f"{dotted}.{name}", set()).add(candidate)
    assert not missing, (
        "types named in a public signature but not importable from `sdmxlib`:\n  "
        + "\n  ".join(f"{k}: {sorted(v)}" for k, v in sorted(missing.items()))
        + "\nExport it, or add it to `foreign` if it belongs to another library."
    )


def test_the_exported_row_kinds_actually_annotate_the_chain() -> None:
    """Exporting a name is only useful if it types what a user holds.

    Rewritten for #306. The four DSLs exported *ref* bases — `ColumnRef`,
    `AggregateRef`, `Projection`, `Predicate` — because the leaf a caller held
    (`q.code.id` was an `_IdRef`) had to satisfy something importable. The path
    language exports **row kinds** instead: the expression types are private
    and generic over the row kind, so the name a user writes in an annotation
    is `Query[sl.CodeRow]`, not the class of the expression.

    That is a smaller published surface for the same expressiveness, and it is
    the reason the expression hierarchy could be reshaped four times during
    this milestone without a compatibility argument.
    """
    from sdmxlib.query.accessors import CodeRow
    from sdmxlib.query.query import Query

    assert sl.CodeRow is CodeRow
    assert isinstance(q.code.id.desc(), sl.Descending)

    # The annotation a user would actually write, resolved rather than assumed.
    def rows(reg: sl.LocalRegistry) -> Query[sl.CodeRow, sl.Code]:
        return reg.query(q.code)

    with sl.LocalRegistry(":memory:") as reg:
        assert isinstance(rows(reg), Query)


def test_the_expression_types_stay_private() -> None:
    """The other half of the decision, so "export more" does not creep.

    Predicate and column classes are compiler detail. #254, #262, #292 and
    #306 each reshaped this set, and each was free because no name was
    published. What is exported is the *row kinds*, which name relations rather
    than machinery and so do not move when the compiler does.
    """
    from sdmxlib.query import _expr

    exported = set(sl.__all__)
    # The *abstract* bases are the annotation vocabulary and are exported —
    # `Pred[R]`, `Col[R]`, `Agg[R]`, `Cols[R]` are what a helper taking a
    # predicate has to name. What stays private is every concrete node: a
    # caller builds those through `q`, never by name, which is what let the
    # expression hierarchy be reshaped four times in this milestone.
    # The window chain is typed by stage — `Col` → `WinFn` → `Win` → `WinPred`
    # — and all four stages are vocabulary. #299 found the first and last
    # exported and the two middles not, which was not a principle: `WinFn` is
    # what `.rank()` hands back and `Win` is what `.over()` hands back, so a
    # caller holding a mid-chain value in an annotated variable, or writing a
    # helper that returns one, had no name for it. Polars settles nothing here
    # — every stage there is `Expr`, so it has no intermediates to publish.
    # `AnyPred` joined them in #373. A filtered aggregate takes a *child*-rooted
    # predicate, and the column form — `fan.col.list(where=…)` — has no
    # parameter for the child's row kind to name: `Col[R]` carries the row the
    # aggregate is projected onto, not the row being restricted. The fan-out
    # form is typed `Pred[C]` and is the one to reach for; the column form
    # takes the non-generic base, which then has to be nameable. The same
    # argument that exported `AggPred`.
    # `Untyped` joined in #413, on #299's argument exactly. It is what
    # `ObsQuery.col()` hands back — every observation column — and it was
    # declared as `Col`, which hid the comparisons and `.str` it carries; the
    # declaration is now honest, so a consumer writing a helper that takes an
    # observation column and applies SDMX-REST's `co:`/`sw:`/`ew:` to it needs
    # a name for it. Still built only through `col()`, never by name.
    # `Str` and `Num` joined in #511, on the same argument: `ObsQuery.text()` /
    # `.num()` hand them back, with exact static types — the point of those
    # accessors — so a helper taking one has to be able to name it. The
    # component-projected subclasses that implement them stay private.
    vocabulary = {
        "Pred",
        "AnyPred",
        "Col",
        "Cols",
        "Agg",
        "AggPred",
        "Win",
        "WinFn",
        "WinPred",
        "Cursor",
        "Untyped",
        "Str",
        "Num",
    }
    machinery = {
        c.__name__
        for c in vars(_expr).values()
        if inspect.isclass(c)
        and issubclass(c, _expr.AnyPred | _expr.AnyCol | _expr.AnyAgg | _expr.AnyCols)
        and c.__name__ not in vocabulary
    }
    leaked = machinery & exported
    assert not leaked, (
        f"expression types exported: {sorted(leaked)}. They are compiler detail, "
        "and publishing one makes the next change to it a compatibility argument."
    )
    assert {"CodeRow", "HCodeRow", "ArtefactRow"} <= exported, "the row kinds are the published names"


def _raised(doc: str | None) -> list[str]:
    """The exception names a Google docstring's ``Raises:`` section lists."""
    if not doc:
        return []
    section = re.search(r"\n\s*Raises:\n((?:[ \t]{4,}.*\n?)+)", doc)
    return [] if section is None else re.findall(r"^\s+([A-Za-z_][\w.]*)\s*:", section.group(1), re.MULTILINE)


def test_every_exception_a_public_docstring_raises_is_importable() -> None:
    """A documented `Raises:` the caller cannot import is one they cannot catch (#512).

    `PushdownError`, `SliceError`, `CrossSourceError`, `SdmxConformanceError`
    and `SidecarNotUniqueError` were each named under ``Raises:`` and none was
    importable from `sdmxlib`: `except sl.SliceError` was an `AttributeError`.
    Derived from the docstrings, so a new one fails on the day it is written.
    """
    exported = set(sl.__all__)
    missing: dict[str, list[str]] = {}
    for name in sl.__all__:
        obj = getattr(sl, name)
        members = [(name, obj)]
        if inspect.isclass(obj):
            members += [
                (f"{name}.{attr}", getattr(value, "fget", None) or getattr(value, "__func__", value))
                for attr, value in vars(obj).items()
                if not attr.startswith("_") or attr == "__init__"
            ]
        for where, member in members:
            if not (inspect.isfunction(member) or inspect.isclass(member)):
                continue
            for raised in _raised(inspect.getdoc(member)):
                short = raised.rsplit(".", 1)[-1]
                if not hasattr(builtins, short) and short not in exported:
                    missing.setdefault(short, []).append(where)
    assert missing == {}, f"raised but not importable from sdmxlib: {missing}"


def test_the_raises_reader_sees_the_section() -> None:
    """The sabotage check: a section's names are found, prose and other sections are not."""
    doc = (
        "Do a thing.\n\nArgs:\n    x: An x.\n\n"
        "Raises:\n    ValueError: If bad.\n    sl.SliceError: If\n        sliced.\n"
    )
    assert _raised(doc) == ["ValueError", "sl.SliceError"]
