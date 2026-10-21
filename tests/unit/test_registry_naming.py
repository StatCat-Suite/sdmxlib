"""The three registry names mean three different things, and prose says which.

`sl.Registry` became `sl.LocalRegistry` in the §6 step 6c rename (!372), which
was mechanical across 124 files. Mechanical is the point of a rename and also
its hazard: the substitution ran through *prose*, and four docstrings had said
"Registry" meaning something else. They came out as

    "Copy all artefacts from an in-memory LocalRegistry into this store"
    "The domain LocalRegistry populated by every artefact fetched so far"
    "this matches the LocalRegistry Protocol semantics for membership"

each of which is wrong, and wrong in the way documentation is worst: confidently
naming the wrong type to a reader with no way to tell. Nothing failed, because
prose has no compiler.

The three:

* `LocalRegistry` — DuckDB-backed, persistent, on disk or `:memory:`.
* `InMemoryRegistry` — artefacts in a dict with interned `Ref`s. What the
  format readers intern into, and a usable federation source (#333).
* `RegistryReader` — the Protocol both satisfy.

So "in-memory LocalRegistry" names no type, and neither does "the LocalRegistry
Protocol".
"""

from __future__ import annotations

import re

import pytest

from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

_SRC = ROOT / "src" / "sdmxlib"

#: Phrases that can only be a rename that ran through prose.
_CONFUSIONS = (
    re.compile(r"in-memory\s+LocalRegistry", re.IGNORECASE),
    re.compile(r"domain\s+LocalRegistry", re.IGNORECASE),
    re.compile(r"LocalRegistry\s+Protocol"),
    re.compile(r"InMemoryRegistry\s+Protocol"),
)


def test_no_docstring_confuses_the_registry_types() -> None:
    """Cheap, and it catches the one failure mode a rename has left behind
    twice — a name substituted into a sentence that meant a different type."""
    found: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            found.extend(
                f"{path.relative_to(_SRC)}:{number}: {line.strip()}" for pattern in _CONFUSIONS if pattern.search(line)
            )
    assert not found, (
        "prose naming a type that does not exist:\n  "
        + "\n  ".join(found)
        + "\n`LocalRegistry` is the DuckDB-backed store, `InMemoryRegistry` the "
        "dict-backed one, `RegistryReader` the Protocol both satisfy."
    )


def test_the_three_names_are_three_things() -> None:
    """The premise the check rests on. If two of these ever became the same
    object the check above would be enforcing a distinction that had gone."""
    import sdmxlib as sl

    assert sl.LocalRegistry is not sl.InMemoryRegistry
    assert sl.RegistryReader is not sl.LocalRegistry
    assert not issubclass(sl.InMemoryRegistry, sl.LocalRegistry)
