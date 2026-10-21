"""Build an artefact the model refuses, to test what refuses it later (#492).

The model refuses a bad id, agency id or version as it is set
(`sdmxlib.model._rules`), and an artefact breaking a whole-artefact rule as it
is built (`sdmxlib.model._invariants`). A test of `validate()`, a writer or a reader's
refusal sometimes needs exactly such a value, so it builds one here, and the
call site says it is invalid on purpose.

attrs' own switch, so nothing in the library exists only to be bypassed. It is
process-global: not for a test that runs other threads while it is held.
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager

import attrs

from sdmxlib.model.errors import ValidationError, ValidationIssue


@contextmanager
def unchecked() -> Generator[None]:
    """Construct without running field validators, restoring them after.

    Yields:
        Nothing: the block runs with validators off.
    """
    with attrs.validators.disabled():
        yield


def refusal(build: Callable[[], object]) -> list[ValidationIssue]:
    """What construction refuses *build*'s artefact for; empty if it builds (#492).

    Takes the build, not the artefact: an artefact a rule refuses is never
    built, so a rule's test asks the constructor.

    Args:
        build: Builds the artefact, with every check on.

    Returns:
        Every issue the `ValidationError` carried, in order.
    """
    try:
        build()
    except ValidationError as exc:
        return list(exc.issues)
    return []
