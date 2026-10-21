"""`sdmx_ml30` and `sdmx_ml31` share one implementation; this guards that — #346.

The 3.1 package began as a copy of the 3.0 one — its reader's own header said
*"Forked from the 3.0 reader at parity"* — and the cost was that **every fix to
a shared code path had to be applied twice, by someone who knew to look.** When
nobody knew, the result was #345: two branches present in one reader and absent
in the other, with the round-trip guard passing because the fixture had been
trimmed to match.

This file used to compare the two packages function by function and require
each difference to be a declared wire-format divergence. That guard (option 2,
!434) did its job: under it the pair converged to 30/30 identical readers and
47/50 identical writers, which is the evidence that made option 1 worth doing.

Both halves are now merged:

| | was | now |
| --- | --- | --- |
| reader | 1,166 lines each, 30/30 identical | `sdmx_ml/_v3_reader.V3Reader`, 41-line bindings |
| writer | 1,138 lines each, 47/50 identical | `sdmx_ml/_v3_writer.V3Writer` + `V3Generation`, 49-line bindings |

So the body comparison is gone — it would compare two modules that each hold
one forwarding function and pass while saying nothing, which is worse than
absent. What replaces it asserts the *shape*: neither package reimplements
anything, the shared modules actually carry the implementation, and the three
surviving divergences live in one flag set that agrees with `formats/gaps.py`.

Re-forking either half is a small edit that would otherwise silently restore
~2,300 duplicated lines.
"""

from __future__ import annotations

import ast
from pathlib import Path

import attrs
import pytest

from sdmxlib.formats.gaps import declared_gaps
from sdmxlib.formats.sdmx_ml._v3_writer import V3_0, V3_1, V3Generation
from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

_ML30 = ROOT / "src" / "sdmxlib" / "formats" / "sdmx_ml30"
_ML31 = _ML30.parent / "sdmx_ml31"
_SHARED = _ML30.parent / "sdmx_ml"

#: Module-level constants that legitimately differ, and why.
#:
#: Only `namespaces.py`, and only the URIs — which is the point: the fork's one
#: *real* difference is meant to live there and nowhere else. An entry
#: appearing for `reader.py` or `writer.py` means a generation difference
#: leaked out of the module built to hold it.
_DIVERGENT_CONSTANTS: dict[str, dict[str, str]] = {
    "namespaces.py": {
        "COM": "the SDMX 3.1 common namespace URI",
        "MD": "the SDMX 3.1 metadata namespace URI",
        "MES": "the SDMX 3.1 message namespace URI",
        "STR": "the SDMX 3.1 structure namespace URI",
    },
    "reader.py": {},
    "writer.py": {
        "_WRITER": (
            "the binding itself — `generation=V3_0` against `V3_1`, which is "
            "the one line that is *supposed* to differ now that the "
            "implementation is shared"
        )
    },
}

#: Each generation flag, and the `formats/gaps.py` row saying the same thing to
#: a caller. `dimension_constraint` is the one that runs the other way — 3.1
#: *gains* it — so its row is against the versions that cannot carry it.
_FLAG_GAPS: dict[str, tuple[str, str]] = {
    "categorisation_version": ("Categorisation", "version"),
    # 3.1 writes an Actual constraint as an AvailabilityConstraint (#421), so
    # what it cannot carry is the constraint's kind, not its role.
    "data_constraint_role": ("DataConstraint", "sdmx_class"),
    "release_calendar": ("DataConstraint", "release_calendar"),
    "dimension_constraint": ("Dataflow", "dimension_constraint"),
    "partial_language": ("MaintainableArtefact", "is_partial_language"),
    "evolving_structure": ("DataStructure", "evolving_structure"),
    # Another 3.1 gain: only it has an AvailabilityConstraint (#421).
    "availability_constraint": ("AvailabilityConstraint", "sdmx_class"),
}


def _defined_functions(path: Path) -> list[str]:
    return [n.name for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef)]


def _module_constants(path: Path) -> dict[str, str]:
    """Every module-level `UPPER_CASE` binding, as a normalised value.

    Module-level data is outside any function comparison, so a namespace map
    edited on one side is exactly the shape of #345 and would otherwise pass
    every assertion here.
    """
    tree = ast.parse(path.read_text())
    out: dict[str, str] = {}
    for node in tree.body:
        targets: tuple[ast.expr, ...] = ()
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = tuple(node.targets), node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = (node.target,), node.value
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id.isupper():
                out[target.id] = ast.unparse(value)
    return out


# ── neither half is a fork any more ───────────────────────────────────────


@pytest.mark.parametrize(("module", "entries"), [("reader.py", ["read"]), ("writer.py", ["write", "write_kept"])])
def test_neither_package_reimplements_the_shared_module(module: str, entries: list[str]) -> None:
    """Each package defines only its entry points and binds the shared class.

    The assertion the body comparison cannot make. Once each module holds
    forwarding functions only, the two are trivially identical, so a re-fork
    would pass every comparison while restoring a thousand duplicated lines.
    The writer has two entry points: `write`, and `write_kept` for `serialize`,
    which has already left out what the generation cannot hold (#464).
    """
    shared = "V3Reader" if module == "reader.py" else "V3Writer"
    for package in (_ML30, _ML31):
        path = package / module
        source = path.read_text()
        assert _defined_functions(path) == entries, (
            f"{package.name}/{module} defines {_defined_functions(path)}; the implementation "
            f"belongs in sdmx_ml/_v3_{module[:-3]}.{shared}, shared by both generations (#346)"
        )
        assert shared in source, f"{package.name}/{module} no longer uses {shared}"
        assert "from .namespaces import" in source, (
            f"{package.name}/{module} must bind the shared class to its own namespace URIs"
        )


@pytest.mark.parametrize(
    ("module", "cls", "entry"), [("_v3_reader.py", "V3Reader", "read"), ("_v3_writer.py", "V3Writer", "write")]
)
def test_the_shared_module_is_where_the_work_is(module: str, cls: str, entry: str) -> None:
    """The merge moved the implementation rather than deleting coverage.

    A thin pair plus an empty shared class would satisfy the test above.
    """
    tree = ast.parse((_SHARED / module).read_text())
    klass = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == cls)
    methods = [n.name for n in klass.body if isinstance(n, ast.FunctionDef)]
    assert entry in methods
    assert len(methods) >= 25, f"{cls} has only {len(methods)} methods; the forked module had ~30 functions"


# ── the divergences live in one place, and agree with `gaps` ──────────────


def test_the_two_generations_differ_in_every_flag() -> None:
    """`V3Generation` earns its existence only if the two disagree everywhere.

    A flag both generations set the same way is not a generation difference —
    it is a constant with extra steps, and it should be inlined.
    """
    flags = [field.name for field in attrs.fields(V3Generation)]
    same = [name for name in flags if getattr(V3_0, name) == getattr(V3_1, name)]
    assert not same, f"{same} are the same in 3.0 and 3.1 — a constant with extra steps, not a generation flag"
    assert set(flags) == set(_FLAG_GAPS), f"the flag set and the gap map disagree: {set(flags) ^ set(_FLAG_GAPS)}"


def test_every_generation_flag_has_a_matching_gaps_row() -> None:
    """The writer's capability set and `formats.gaps` are one fact, stated twice.

    `V3Generation` tells the *writer* what to emit; `gaps` tells a *caller*
    what will be lost. Two statements of one fact drifting apart is how #345
    happened, and this pass found exactly that: 3.1 dropped
    `DataConstraint/@role` and `gaps` said nothing, so a constraint written as
    `Actual` came back `Allowed` and `unrepresentable()` reported no loss.
    """
    for flag, (artefact, field) in _FLAG_GAPS.items():
        # The generation that *cannot* carry it is the one with a gap row.
        without = "3.1" if getattr(V3_0, flag) else "3.0"
        rows = {(name, field) for target, name, field, _ in declared_gaps() if target == without}
        assert (artefact, field) in rows, (
            f"`V3Generation.{flag}` says {without} cannot emit {artefact}.{field}, "
            f"but formats/gaps.py declares no row for it — so `unrepresentable()` "
            f"tells a caller the write is lossless (#346)"
        )


def test_the_flag_set_covers_every_declared_v3_difference() -> None:
    """The other direction: a gap that only one v3 generation has needs a flag.

    Rows applying to *both* 3.0 and 3.1 are not generation differences and are
    correctly absent here — they are things neither version can carry.
    """
    only_30 = {(name, field) for target, name, field, _ in declared_gaps() if target == "3.0"}
    only_31 = {(name, field) for target, name, field, _ in declared_gaps() if target == "3.1"}
    asymmetric = only_30 ^ only_31
    covered = set(_FLAG_GAPS.values())
    assert asymmetric <= covered, (
        f"{sorted(asymmetric - covered)} differ between 3.0 and 3.1 in `gaps` but have no "
        f"`V3Generation` flag — either the writer emits them the same way in both "
        f"(and a gap row is wrong) or it should not (and a flag is missing)"
    )


# ── module-level data ─────────────────────────────────────────────────────


@pytest.mark.parametrize("module", ["reader.py", "writer.py", "namespaces.py"])
def test_module_level_data_differs_only_where_declared(module: str) -> None:
    """A namespace map edited on one side is invisible to everything else."""
    thirty, thirty_one = _module_constants(_ML30 / module), _module_constants(_ML31 / module)
    allowed = _DIVERGENT_CONSTANTS[module]
    drifted = {
        name for name in thirty.keys() & thirty_one.keys() if thirty[name] != thirty_one[name] and name not in allowed
    }
    assert not drifted, (
        f"{module}: module-level {sorted(drifted)} differ between the two packages with no "
        f"declared reason. Generation differences belong in namespaces.py or in `V3Generation`."
    )


@pytest.mark.parametrize("module", ["reader.py", "writer.py", "namespaces.py"])
def test_a_declared_constant_divergence_still_diverges(module: str) -> None:
    """An allow-list entry that stopped being needed is one nobody re-reads."""
    thirty, thirty_one = _module_constants(_ML30 / module), _module_constants(_ML31 / module)
    stale = {
        name
        for name in _DIVERGENT_CONSTANTS[module]
        if name in thirty and name in thirty_one and thirty[name] == thirty_one[name]
    }
    assert not stale, f"{module}: {sorted(stale)} are declared as divergent and are now identical"
