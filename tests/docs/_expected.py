"""What a page says its examples print, and whether they print it.

Running a block proves it does not raise. It does not prove the page is
*right* — `print(cl.codes.ids())  # ["A", "Q", "M"]` runs identically when the
answer is `["A"]`, and that comment is the claim a reader acts on.

The pages already write those claims down, 63 times, as a trailing comment on
a `print`. This reads them back. No new syntax: the comment is documentation
first, and the check is a consequence of it being accurate.

**How an expected value is told from prose.** By whether it parses as a Python
literal, not by a marker. `# ["A", "Q", "M"]` is checked; `# falls back to
"Annual"` is not, because there is nothing to compare. That is a heuristic, so
it fails open — and a check that fails open silently is a check that decays.
`test_output_claims_are_checked_not_drifting` therefore asserts the *count* of
unchecked comments, the same ratchet `_SKIP_BUDGET` and `any_baseline.txt` use.
Prose is allowed and counted; a value comment that stops parsing shows up as a
diff on that number.

Output is captured by binding a recording `print` into the block's namespace
rather than by redirecting stdout, because the line number is what maps an
output back to its claim, and only the calling frame has it. That also works
for a `print` inside a `for` or a `with`, which statement-splitting would not.
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from dataclasses import dataclass


@dataclass(frozen=True)
class Claim:
    """One `print(...)  # expected` on a line of a block."""

    line: int
    #: The comment as written, kept for the failure message.
    text: str
    #: `str()` of the literal it parses to, or `None` when it is prose.
    expected: str | None
    #: The page abbreviated with `...`, so the claim is a *prefix*.
    truncated: bool = False

    @property
    def checkable(self) -> bool:
        return self.expected is not None

    def holds_for(self, actual: str) -> bool:
        """Whether `actual` satisfies this claim.

        Truncated claims match as prefixes. Pages abbreviate long output on
        purpose — `# ["AE", "AR", "AU", ...]`, `# "The frequency of..."` — and
        a reader takes that as *"it starts like this"*, which is a real claim
        and worth holding the page to. Treating them as unverifiable instead
        would drop the longest and most drift-prone outputs on the site.
        """
        assert self.expected is not None
        return actual.startswith(self.expected) if self.truncated else actual == self.expected


def claims_in(code: str) -> list[Claim]:
    """Every `print(...)` in `code` that carries a trailing comment.

    Tokenised rather than regexed: a `#` inside a string literal is not a
    comment, and `print("a # b")` appears on these pages.
    """
    printed = _print_lines(code)
    found: list[Claim] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
    except tokenize.TokenError, IndentationError, SyntaxError:  # pragma: no cover - blocks compile
        return found
    for token in tokens:
        if token.type != tokenize.COMMENT or token.start[0] not in printed:
            continue
        text = token.string.lstrip("#").strip()
        expected, truncated = _as_literal(text)
        found.append(Claim(line=token.start[0], text=text, expected=expected, truncated=truncated))
    return found


def _print_lines(code: str) -> set[int]:
    """Lines holding a `print(...)` call, from the AST rather than the text."""
    try:
        tree = ast.parse(code)
    except SyntaxError:  # pragma: no cover - blocks compile before this runs
        return set()
    return {
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
    }


#: Distinguishes "parsed to None" from "did not parse". `# None` is a claim.
_NOTHING = object()

#: A single dotted identifier and nothing else.
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


def _literal_or_none(text: str) -> object:
    """`ast.literal_eval(text)`, or `_NOTHING` when it is not a literal."""
    try:
        value: object = ast.literal_eval(text)
    except ValueError, SyntaxError, MemoryError, TypeError:
        return _NOTHING
    return value


def _as_literal(text: str) -> tuple[str | None, bool]:
    """`(str() of the literal `text` denotes, whether it was abbreviated)`.

    `(None, False)` when it is not a literal at all.

    A bare word is rejected on purpose. `# the codelist` parses as nothing, but
    `# True` and `# None` do — and so would a comment reading `# id`, if names
    were allowed. `literal_eval` already refuses names; this only adds that an
    *empty* comment is not a claim.

    Two abbreviations are recognised, because the pages use both: a string
    ending `...`, and a sequence whose last element is `...`. Note the second
    is why this cannot simply trust `literal_eval` — `["A", ...]` parses
    happily, into a list containing `Ellipsis`, and comparing *that* to real
    output would fail every time.
    """
    if not text:
        return None, False
    value = _literal_or_none(text)
    if value is _NOTHING:
        # The pages' other house style is `value — why`, and the value half is
        # as much of a claim as one standing alone. Split on the *first*
        # separator only: `# ["A", "Q"] — kept, not replaced` has one, and a
        # value containing a dash is still parsed whole by the attempt above.
        for separator in (" — ", " -- ", "  # "):
            head, found, _ = text.partition(separator)
            if found and (value := _literal_or_none(head.strip())) is not _NOTHING:
                break
        else:
            # A lone identifier-shaped word: `print(type(cl).__name__)` and
            # `# LazyCodelist`. `literal_eval` refuses names on purpose — it
            # cannot know one from prose — but *one* word next to a print is a
            # claim far more often than it is a sentence, and a rare false
            # positive fails loudly rather than hiding.
            if _WORD.fullmatch(text):
                return text, False
            return None, False
    if isinstance(value, str):
        return (value.removesuffix("..."), True) if value.endswith("...") else (value, False)
    if isinstance(value, (list, tuple)) and value and value[-1] is Ellipsis:
        kept = list(value[:-1])
        if not kept:
            return None, False
        # `str([...])` of the kept head, minus the closing bracket, is the
        # prefix the full output must start with.
        return str(kept)[:-1], True
    if any(v is Ellipsis for v in (value.values() if isinstance(value, dict) else ())):
        return None, False
    return str(value), False


class Recorder:
    """A `print` that keeps what each line printed.

    Bound into the block's namespace, so it shadows the builtin for the
    documentation's code and nothing else.
    """

    def __init__(self) -> None:
        self.by_line: dict[int, list[str]] = {}

    def print(self, *args: object, sep: str = " ", end: str = "\n", **_: object) -> None:
        """Signature-compatible with `print` for the arguments pages use."""
        line = sys._getframe(1).f_lineno  # keyed by the caller's line
        self.by_line.setdefault(line, []).append(sep.join(str(a) for a in args))

    def mismatches(self, claims: list[Claim]) -> list[str]:
        """Claims whose line printed something else. Empty when the page is right.

        A claim whose `print` never ran is *not* a mismatch: a block may print
        inside a branch the fixture does not take, and demanding otherwise
        would make the fixtures drive the prose.
        """
        out: list[str] = []
        for claim in claims:
            if not claim.checkable:
                continue
            actual = self.by_line.get(claim.line)
            if actual is None or claim.holds_for(actual[0]):
                continue
            how = "does not start with" if claim.truncated else "is not"
            out.append(f"line {claim.line}: page says {claim.text!r}, which {how} what it prints: {actual[0]!r}")
        return out
