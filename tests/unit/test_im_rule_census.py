"""Every normative sentence of the information model has an owner, and every check an IM rule (#456).

`tests.support.im_rules` holds the census; this checks it both ways against
its two sources:

- **the IM text** — each quote is verbatim on its page, and every *must*,
  *cannot*, *mandatory*, *may not* and *shall* in the model's chapters sits
  inside a quote, so the sentences in the census are the text's, not a
  selection;
- **the checks** — the checks rules cite and the checks `NON_IM_CHECKS` sets
  aside are, between them, exactly the `_check_*` / `_validate_*` functions of
  `model.validation`, and each cited check has a test that passes with it and
  fails with it disabled.

Each guard has a sabotage beside it, because each starts green.
"""

import annotationlib
import ast
import functools
import importlib
import inspect
import re
import types
from collections.abc import Callable

import pytest

from sdmxlib.model import validation
from tests.support import im_rules
from tests.support.im_rules import (
    NON_IM_CHECKS,
    RULES,
    UNSURVEYED_MULTIPLICITIES,
    NotChecked,
    Rule,
    Structural,
    Validated,
    Warned,
)
from tests.support.repo import find_root

pytestmark = pytest.mark.unit

_VALIDATION = find_root() / "src" / "sdmxlib" / "model" / "validation.py"


def _checks() -> set[str]:
    tree = ast.parse(_VALIDATION.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith(("_check_", "_validate_"))
    }


def _cited(rules: tuple[Rule, ...]) -> set[str]:
    return {check.name for rule in rules if isinstance(rule.owner, Validated | Warned) for check in rule.owner.checks}


def _uncovered(rules: tuple[Rule, ...]) -> list[tuple[int, str]]:
    """Each strong marker in the model's chapters that no rule's quote contains, as (page, context)."""
    spans: dict[int, list[tuple[int, int]]] = {}
    for rule in rules:
        span = im_rules.locate(rule)
        if span is not None:
            spans.setdefault(rule.page, []).append(span)
    return [
        (page, text[max(0, marker.start() - 100) : marker.end() + 40])
        for page in range(im_rules.FIRST_PAGE, im_rules.LAST_PAGE + 1)
        for text in (im_rules.page_text(page),)
        for marker in im_rules.MARKERS.finditer(text)
        if not any(start <= marker.start() and marker.end() <= end for start, end in spans.get(page, ()))
    ]


def _disabled(*_args: object, **_kwargs: object) -> None:
    return None


#: What `pytest.param` returns; pytest exports the function, not the class.
_PARAM = type(pytest.param())


def _case_id(value: object) -> str | None:
    """A case's id where pytest's is knowable: a `pytest.param`'s own, or a string value."""
    if isinstance(value, _PARAM):
        # Not only `str | None`: a hidden id is pytest's sentinel, and no citation can name it.
        value = value.id
    return value if isinstance(value, str) else None


def _test_function(test: str) -> Callable[[], object]:
    """The cited test as a call taking nothing: a plain test, or one case of a parametrized one.

    A case is named as pytest names it, ``test_x[id]``, and found from the
    test's one ``parametrize`` mark — its ``ids``, a ``pytest.param``'s id, or the value itself when
    there is one argument. Fixtures are not supplied, so a cited test takes
    none.
    """
    path, _, rest = test.partition("::")
    name, _, case = rest.partition("[")
    module = importlib.import_module(path.removesuffix(".py").replace("/", "."))
    function = getattr(module, name)
    assert isinstance(function, types.FunctionType), test
    parameters = list(inspect.signature(function, annotation_format=annotationlib.Format.FORWARDREF).parameters)
    if not case:
        assert not parameters, f"{test} takes arguments; cite a plain test, or one case of it"
        return function
    marks: list[pytest.Mark] = [mark for mark in getattr(function, "pytestmark", []) if mark.name == "parametrize"]
    assert len(marks) == 1, f"{test}: cite a test parametrized once"
    argnames, argvalues = marks[0].args
    names = [part.strip() for part in argnames.split(",")] if isinstance(argnames, str) else list(argnames)
    assert names == parameters, f"{test} takes fixtures beside its parameters; cite a plain test"
    ids = marks[0].kwargs.get("ids") or [_case_id(value) for value in argvalues]
    for ident, value in zip(ids, argvalues, strict=True):
        if ident == case.removesuffix("]"):
            if isinstance(value, _PARAM):
                values = tuple(value.values)
            else:
                values = (value,) if len(names) == 1 else tuple(value)
            return functools.partial(function, **dict(zip(names, values, strict=True)))
    msg = f"{test}: no case with that id among {ids}"
    raise AssertionError(msg)


def _fails_without(check: str, test: str) -> bool:
    """Whether `test` fails while `check` does nothing.

    Swaps the function's code rather than its module attribute, so a
    reference held elsewhere — `_DISPATCH` holds the `_validate_*` functions —
    is disabled too.

    A test fails by assertion, by a `pytest.raises` that saw nothing, or by
    ``(message,) = issues`` meeting no issue — a ValueError. Anything else
    propagates, so the census test errors rather than counting it either way.
    """
    target = getattr(validation, check)
    assert isinstance(target, types.FunctionType), check
    saved = target.__code__
    target.__code__ = _disabled.__code__
    try:
        _test_function(test)()
    except AssertionError, ValueError, pytest.fail.Exception:
        return True
    finally:
        target.__code__ = saved
    return False


# The text.


def test_every_quote_is_verbatim_on_its_page() -> None:
    misplaced = [rule.id for rule in RULES if im_rules.locate(rule) is None]
    assert not misplaced, f"not found on their pages: {misplaced}"


def test_a_quote_off_its_page_is_not_found() -> None:
    rule = RULES[0]
    assert im_rules.locate(rule) is not None
    assert im_rules.locate(Rule(rule.id, rule.section, rule.page + 1, rule.quote, rule.owner)) is None


def test_an_elision_joins_only_neighbouring_fragments() -> None:
    near = Rule("x", "3.6.3.3", 45, "a start value or time … must be supplied.", NotChecked("x"))
    far = Rule("x", "3.6.3.3", 45, "a start value or time … endTime facet is used", NotChecked("x"))
    assert im_rules.locate(near) is not None
    assert im_rules.locate(far) is None


def test_every_strong_marker_in_the_model_chapters_is_quoted() -> None:
    missing = _uncovered(RULES)
    assert not missing, "\n".join(f"p. {page}: …{context}…" for page, context in missing)


def test_a_rule_left_out_leaves_its_marker_unquoted() -> None:
    dropped = next(rule for rule in RULES if rule.id == "IM-9.1.2.1-1")
    missing = _uncovered(tuple(rule for rule in RULES if rule is not dropped))
    assert [page for page, _ in missing] == [dropped.page]
    assert "cannot mix source and target types" in missing[0][1]


def test_the_chapters_hold_the_markers_counted_when_the_census_was_taken() -> None:
    # A new edition that adds or drops a normative sentence moves this before
    # anything else: re-survey, then update the count.
    counted = sum(
        len(im_rules.MARKERS.findall(im_rules.page_text(page)))
        for page in range(im_rules.FIRST_PAGE, im_rules.LAST_PAGE + 1)
    )
    assert counted == 64


def test_rule_ids_are_unique_and_name_their_section() -> None:
    ids = [rule.id for rule in RULES]
    assert len(ids) == len(set(ids))
    for rule in RULES:
        assert re.fullmatch(rf"IM-{re.escape(rule.section)}-\d+", rule.id), rule.id


# The owners.


def test_every_rule_has_one_owner_with_its_reason() -> None:
    for rule in RULES:
        match rule.owner:
            case Validated(checks=checks) | Warned(checks=checks):
                assert checks, rule.id
            case Structural(reason=reason) | NotChecked(reason=reason):
                assert reason.strip(), rule.id


def test_every_check_is_cited_or_set_aside_and_never_both() -> None:
    checks, cited, aside = _checks(), _cited(RULES), set(NON_IM_CHECKS)
    assert not cited & aside, f"cited and set aside: {sorted(cited & aside)}"
    assert cited | aside == checks, (
        f"neither cited nor set aside: {sorted(checks - cited - aside)}; "
        f"no such check: {sorted((cited | aside) - checks)}"
    )


def test_a_new_check_is_neither_cited_nor_set_aside() -> None:
    checks = _checks() | {"_check_something_new"}
    assert checks - _cited(RULES) - set(NON_IM_CHECKS) == {"_check_something_new"}


def test_the_census_reads_the_checks_it_compares() -> None:
    # The partition above is only as good as this list: an empty or truncated
    # read would make it vacuous.
    assert len(_checks()) == 90
    assert len(_cited(RULES)) == 28
    assert len(NON_IM_CHECKS) == 62


_CITATIONS = [
    pytest.param(check.name, check.test, id=f"{rule.id}:{check.name}")
    for rule in RULES
    if isinstance(rule.owner, Validated | Warned)
    for check in rule.owner.checks
]


@pytest.mark.parametrize(("check", "test"), _CITATIONS)
def test_a_cited_test_passes_and_fails_without_its_check(check: str, test: str) -> None:
    _test_function(test)()
    assert _fails_without(check, test), f"{test} still passes with {check} disabled"


def test_a_test_that_does_not_need_the_check_survives_its_removal() -> None:
    assert not _fails_without(
        "_check_cube_region_inclusion",
        "tests/unit/test_time_text_type.py::test_a_non_time_text_type_is_invalid",
    )


def test_a_disabled_check_is_restored() -> None:
    before = validation._check_time_text_type.__code__
    assert _fails_without(
        "_check_time_text_type",
        "tests/unit/test_time_text_type.py::test_a_non_time_text_type_is_invalid",
    )
    assert validation._check_time_text_type.__code__ is before


def test_a_parametrized_test_is_cited_by_one_case() -> None:
    siblings = "tests/unit/test_nested_paths.py::test_a_repeat_among_siblings_is_refused"
    with pytest.raises(AssertionError, match="cite a plain test, or one case"):
        _test_function(siblings)
    with pytest.raises(AssertionError, match="no case with that id"):
        _test_function(f"{siblings}[nothing]")
    _test_function(f"{siblings}[hierarchy]")()


# The next phase.


def test_the_unsurveyed_chapters_are_the_models_and_are_counted() -> None:
    assert len(UNSURVEYED_MULTIPLICITIES) == 13
    for chapter, title, page in UNSURVEYED_MULTIPLICITIES:
        assert f"{title} {chapter}.1 " in im_rules.page_text(page), (chapter, title, page)
