"""What an artefact as a whole must be, checked as it is built (#492, stage 2).

Stage 1 made the rules a value decides by itself attrs field validators. The
rest — unique item ids, parents that exist and do not loop, a concept on every
component, attachments naming the DSD's own dimensions — depend on the
artefact as a whole, and ran only in `validate()`. With the model frozen, an
artefact is only ever built whole (`__init__` or `attrs.evolve`), so they can
run then and stay true: no path holds an artefact that breaks one.

The rules live in `sdmxlib.model.validation`, which imports every model
class, so a class cannot import them. They are registered here instead, and a
class's `__attrs_post_init__` calls `enforce`. `sdmxlib.model` imports
`validation` before any class can be used, so the registry is full by then.

`attrs.validators.disabled()` turns these off with the field validators: a
test builds an artefact a rule refuses there, to check what refuses it
downstream (`tests/support/unchecked.py`).

A class can also be built under *advisories* (#481): rules the information
model states and no wire schema enforces. They run once its rules pass, and
breaking one builds the artefact anyway with an `ImRuleWarning`, one per
artefact, pointing at the caller's line rather than at sdmxlib.
"""

import warnings
from collections.abc import Callable
from pathlib import Path

import attrs

from sdmxlib.model.errors import ImRuleWarning, ValidationError, ValidationIssue

type _Check = Callable[[object, str, list[ValidationIssue]], None]

#: Each artefact class's rules, by class; a subclass takes its nearest base's.
_CHECKS: dict[type, _Check] = {}

#: Each artefact class's advisories, likewise.
_ADVISORIES: dict[type, _Check] = {}

#: Frames a warning skips to reach the caller: sdmxlib's own, and the
#: ``__init__`` attrs generates, whose file is ``<attrs generated methods …>``.
_INTERNAL = (str(Path(__file__).resolve().parents[1]), "<attrs generated")


def _wrapped[T](cls: type[T], check: Callable[[T, str, list[ValidationIssue]], None]) -> _Check:
    def run(artefact: object, prefix: str, issues: list[ValidationIssue]) -> None:
        if isinstance(artefact, cls):
            check(artefact, prefix, issues)

    return run


def register[T](cls: type[T], check: Callable[[T, str, list[ValidationIssue]], None]) -> None:
    """Make *check* the rules every *cls* is built under.

    Args:
        cls: The artefact class.
        check: Appends an issue per broken rule, its path under the prefix given.
    """
    _CHECKS[cls] = _wrapped(cls, check)


def register_advisory[T](cls: type[T], check: Callable[[T, str, list[ValidationIssue]], None]) -> None:
    """Make *check* the advisories every *cls* is built under: broken, they warn rather than refuse.

    Args:
        cls: The artefact class.
        check: Appends an issue per broken rule, its path under the prefix given.
    """
    _ADVISORIES[cls] = _wrapped(cls, check)


def _run(registry: dict[type, _Check], artefact: object, prefix: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for cls in type(artefact).__mro__:
        check = registry.get(cls)
        if check is not None:
            check(artefact, prefix, issues)
            break
    return issues


def issues_of(artefact: object) -> list[ValidationIssue]:
    """Every rule *artefact* breaks, its paths under its class name; empty if none apply."""
    return _run(_CHECKS, artefact, type(artefact).__name__)


def advisories_of(artefact: object) -> list[ValidationIssue]:
    """Every advisory *artefact* breaks, its paths under ``Class:id``; empty if none apply.

    The id, unlike `issues_of`: a refusal raises at the line that built the
    artefact, but a warning may come from a read of hundreds, and has to say
    which one.
    """
    artefact_id: object = getattr(artefact, "id", None)
    prefix = type(artefact).__name__ if artefact_id is None else f"{type(artefact).__name__}:{artefact_id}"
    return _run(_ADVISORIES, artefact, prefix)


def enforce(artefact: object) -> None:
    """Raise if *artefact*, as just built, breaks a rule of its class; warn if it breaks an advisory.

    Raises:
        ValidationError: Carrying every broken rule at once.
    """
    if attrs.validators.get_disabled():
        return
    issues = issues_of(artefact)
    if issues:
        raise ValidationError(issues)
    advise(advisories_of(artefact))


def advise(advisories: list[ValidationIssue]) -> None:
    """Warn with one `ImRuleWarning` carrying `advisories`, pointed at the caller's line; nothing if empty."""
    if advisories:
        warnings.warn(ImRuleWarning(advisories), skip_file_prefixes=_INTERNAL)
