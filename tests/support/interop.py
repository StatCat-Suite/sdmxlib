"""Comparing what an independent implementation made of our documents with what we sent (#457).

The external oracles -- FMR (`tests.support.fmr_oracle`) and pysdmx
(`tests.support.pysdmx_oracle`) -- each take a document of ours and give back
their own copy of it. Both sides are read *through our reader* and diffed field
by field (`bench.sweep.modeldiff`), so a difference is one the model can see,
not one of serialisation. Each difference is then classified by one `Rule`.

An artefact is matched across the two by its URN with the id upper-cased,
because FMR 12.4.2 upper-cases some ids (`CC_data_provider` comes back as
`CC_DATA_PROVIDER`); that is recorded as a difference of its own rather than
left to make the artefact look missing.
"""

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

import attrs
import pytest
from attrs import field, frozen

from sdmxlib.formats import parse
from sdmxlib.formats.gaps import unrepresentable
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.collections import ArtefactList
from tests.support.roundtrip import as_structure

# `bench/` is outside what mutmut copies into `mutants/`, so it is imported
# as `test_sweep.py` imports it: skipped there rather than aborting the run.
diff = pytest.importorskip("bench.sweep.modeldiff").diff


def load_json(path: Path) -> object:
    """A recorded JSON file."""
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def key(urn: object) -> str:
    """How an artefact is matched across sent and served: its URN, upper-cased."""
    return str(urn).upper()


def artefacts(document: bytes) -> dict[str, object]:
    """Every maintainable artefact our reader finds in `document`, by `key`."""
    message = as_structure(parse(document, strict=False))
    found: dict[str, object] = {}
    for attribute in attrs.fields(type(message)):
        value: object = getattr(message, attribute.name)
        if isinstance(value, ArtefactList):
            for artefact in value:
                assert isinstance(artefact, MaintainableArtefact)
                found[key(artefact.urn)] = artefact
    return found


def refusals(document: bytes) -> dict[str, str]:
    """The reason our reader gave for each artefact it refused in `document`, by `identity`.

    By identity, not URN: a refusal of a class the reader does not model names
    it `unknown.HierarchicalCodelist`, not the class sent.
    """
    message = as_structure(parse(document, strict=False))
    return {identity(key(refusal.urn)): refusal.reason for refusal in message.refused}


def _bare(path: str) -> str:
    """A modeldiff path as `formats.gaps` writes a field: no class prefix, no ``(type)`` / ``(length)``."""
    path = re.sub(r" \((type|length)\)$", "", path)
    return path.split(".", 1)[1] if "." in path else ""


def declared(artefact: object, target: str, path: str) -> bool:
    """Whether `formats.gaps` already says `target` cannot carry what `path` names for `artefact`."""
    name = _bare(path)
    return any(
        name == gap.field or name.startswith((f"{gap.field}.", f"{gap.field}[]"))
        for gap in unrepresentable(artefact, target)
    )


#: The parts of a refusal reason that name a place or a URN, which vary per artefact.
_WHERE = re.compile(r"`[^`]*`|\([^)]*\)")


def identity(urn: str) -> str:
    """An upper-cased URN key as ``Class AGENCY:ID(VERSION)``."""
    return urn.split("=", 1)[1] if "=" in urn else urn


def compare(
    ours: Mapping[str, object], theirs: Mapping[str, object], refused: Mapping[str, str], target: str
) -> set[str]:
    """What `theirs` does not carry of `ours`, beyond what `formats.gaps` declares for `target`.

    Each entry is one of:

    - a `modeldiff` path, for a field of an artefact both sides hold;
    - ``REFUSED <class>: <reason>``, when they hold it and our reader refused their copy;
    - ``MISSING <class> <identity>``, when they do not hold it at all;
    - ``EXTRA <class> <identity>``, when they hold one that was not sent.

    Differences `formats.gaps` already declares for `target` are left out --
    a field, or a whole artefact the format has no element for (its
    ``sdmx_class`` gap): they are ours to know, and the round-trip guard
    proves them.
    """
    found: set[str] = set()
    for urn, artefact in ours.items():
        name = type(artefact).__name__
        if urn in theirs:
            found |= {path for path in diff(artefact, theirs[urn]) if not declared(artefact, target, path)}
        elif identity(urn) in refused:
            found.add(f"REFUSED {name}: {_WHERE.sub('…', refused[identity(urn)])}")
        elif not declared(artefact, target, f"{name}.sdmx_class"):
            found.add(f"MISSING {name} {identity(urn)}")
    found |= {f"EXTRA {type(theirs[urn]).__name__} {identity(urn)}" for urn in set(theirs) - set(ours)}
    return found


def _pattern(text: str) -> re.Pattern[str]:
    return re.compile(text)


@frozen
class Rule[K: str]:
    """A class of difference, where it occurs, and whose it is."""

    #: Matched in full against a `compare` entry.
    entry: re.Pattern[str] = field(converter=_pattern)
    #: Matched in full against ``case:format``.
    where: re.Pattern[str] = field(converter=_pattern)
    kind: K
    reason: str

    def matches(self, case: str, fmt: str, entry: str) -> bool:
        """Whether this rule covers `entry` in `case`, served as `fmt`."""
        return bool(self.entry.fullmatch(entry) and self.where.fullmatch(f"{case}:{fmt}"))


type Difference = tuple[str, str, str]


@frozen
class Classification:
    """How a set of rules classified a set of differences."""

    #: The differences no rule covers.
    unclassified: list[Difference]
    #: The differences more than one rule covers.
    ambiguous: list[Difference]
    #: ``case:format`` for every difference each rule covered, by the rule's index.
    covered: dict[int, set[str]]


def classify[K: str](rules: tuple[Rule[K], ...], differences: Iterable[Difference]) -> Classification:
    """Match every difference against every rule."""
    unclassified: list[Difference] = []
    ambiguous: list[Difference] = []
    covered: dict[int, set[str]] = {i: set() for i in range(len(rules))}
    for case, fmt, entry in differences:
        matching = [i for i, rule in enumerate(rules) if rule.matches(case, fmt, entry)]
        if not matching:
            unclassified.append((case, fmt, entry))
        elif len(matching) > 1:
            ambiguous.append((case, fmt, entry))
        for index in matching:
            covered[index].add(f"{case}:{fmt}")
    return Classification(unclassified, ambiguous, covered)
