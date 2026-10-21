"""Narrowing an Optional in a test, where the test itself is the proof it is set.

`reg.get(urn).id` in a test that added `urn` three lines earlier: the `None`
is real in the type and impossible in the test. basedpyright exempted these
for `tests/` (`reportOptionalMemberAccess`); pyrefly cannot — its
`missing-attribute` covers the Optional case and genuine attribute errors
alike, and exempting it would hide the second kind too (#425). So the tests
say it, once per site:

    assert present(reg.get(urn)).id == "CL_FREQ"

A function rather than `assert x is not None`, because most sites are call
chains — `reg.get(urn).id` — that an assert would have to split into a local
first.
"""

from __future__ import annotations


def present[T](value: T | None) -> T:
    """*value*, or fail the test if it is None.

    Raises:
        AssertionError: If *value* is None — a test failure, as a bare
            `assert` would give, naming what was missing.
    """
    if value is None:
        msg = "expected a value, got None"
        raise AssertionError(msg)
    return value
