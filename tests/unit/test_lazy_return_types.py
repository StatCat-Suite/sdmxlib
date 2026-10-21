"""What `LocalRegistry.get` returns for the two lazy roots (#397).

`get` is declared `get[T](target: SdmxUrn[T] | Ref[T]) -> T | None`, so a
`SdmxUrn[Hierarchy]` used to give `Hierarchy | None` while a `LazyHierarchy`
came back — and that class has no ``codes``, ``tree`` or ``descendants`` at
all. So this type-checked and raised:

    reg.get(h_urn).codes      # AttributeError: no attribute 'codes'

Two halves, because a wrong declaration and a useless error are different
problems and neither fixes the other:

* Overloads on `get` name the class each URN actually produces, in both
  `eager=` branches. `tests/typing/probe_local_get.py` is the static half.
* `__getattr__` on both lazy classes refuses a materialised-only member by
  name, for a caller with no checker running. That mirrors what a `Query`
  already does for ``len()`` — the codelist half of this divergence has always
  had a good message, and the hierarchy half had none.

The runtime map is asserted here rather than assumed: every member the real
class has and the lazy one does not is either refused by name or listed as
deliberately absent, so a new one cannot appear unnoticed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

import sdmxlib as sl
from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.storage.lazy import LazyCodelist, LazyHierarchy
from tests.support.factories import code_urn

pytestmark = pytest.mark.unit

#: Members the real classes carry that the lazy ones do not, and that are *not*
#: refused by name. All four are class-level (a classmethod builder or a
#: message-slot marker), so no instance a caller holds can reach them — which
#: is why they need no message rather than having been overlooked.
#: `parse_urn` (#399) is `urn_for`'s parsing half: a constructor on the class,
#: typed `SdmxUrn[Self]`. A lazy facade copying it would answer
#: `SdmxUrn[LazyCodelist]`, naming a class no URN denotes.
CLASS_LEVEL_ONLY = {"msg_field", "parse_rest_ref", "parse_urn", "urn_for"}


@pytest.fixture
def registry() -> Iterator[LocalRegistry]:
    with LocalRegistry(":memory:") as reg:
        reg.add(
            sl.Codelist(
                id="CL",
                maintainer="AG",
                version="1.0",
                name={"en": "Codes"},
                codes=[sl.Code(id="A", name={"en": "a"})],
            )
        )
        reg.add(
            sl.Hierarchy(
                id="H",
                maintainer="AG",
                version="1.0",
                name={"en": "Tree"},
                tree=[HierarchicalCode(id="N1", code=code_urn("CL", "N1"))],
            )
        )
        yield reg


def _codelist(registry: LocalRegistry) -> LazyCodelist:
    got = registry.get(sl.Codelist.urn_for(agency="AG", id="CL", version="1.0"))
    assert isinstance(got, LazyCodelist)
    return got


def _hierarchy(registry: LocalRegistry) -> LazyHierarchy:
    got = registry.get(sl.Hierarchy.urn_for(agency="AG", id="H", version="1.0"))
    assert isinstance(got, LazyHierarchy)
    return got


# ── what actually comes back ──────────────────────────────────────────────


def test_get_returns_the_lazy_classes_by_default(registry: LocalRegistry) -> None:
    """The behaviour the overloads now declare. Asserted so the two cannot drift."""
    assert isinstance(_codelist(registry), LazyCodelist)
    assert isinstance(_hierarchy(registry), LazyHierarchy)


def test_eager_returns_the_real_classes(registry: LocalRegistry) -> None:
    """`eager=` changes the class, which is why one declaration could not cover both."""
    eager_codelist = registry.get(sl.Codelist.urn_for(agency="AG", id="CL", version="1.0"), eager=True)
    assert isinstance(eager_codelist, Codelist)
    assert not isinstance(eager_codelist, LazyCodelist)
    eager_hierarchy = registry.get(sl.Hierarchy.urn_for(agency="AG", id="H", version="1.0"), eager=True)
    assert isinstance(eager_hierarchy, Hierarchy)


# ── the refusals name what to use ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("member", "names"),
    [("codes", ".nodes"), ("tree", ".roots()"), ("descendants", ".narrower_transitive")],
)
def test_a_hierarchy_refuses_a_materialised_member_by_name(registry: LocalRegistry, member: str, names: str) -> None:
    """The defect this issue was filed for: these three did not exist at all."""
    hierarchy = _hierarchy(registry)
    with pytest.raises(AttributeError, match=r"a lazy hierarchy has no") as excinfo:
        getattr(hierarchy, member)
    assert names in str(excinfo.value), "a refusal that names no alternative is barely better than a miss"
    assert "materialize()" in str(excinfo.value)


@pytest.mark.parametrize("member", ["add", "drop"])
def test_a_codelist_refuses_a_materialised_member_by_name(registry: LocalRegistry, member: str) -> None:
    codelist = _codelist(registry)
    with pytest.raises(AttributeError, match=r"a lazy codelist has no") as excinfo:
        getattr(codelist, member)
    assert f".materialize().{member}(" in str(excinfo.value)


@pytest.mark.parametrize("lazy_of", [_codelist, _hierarchy])
def test_a_genuine_typo_still_gets_the_ordinary_message(
    registry: LocalRegistry, lazy_of: Callable[[LocalRegistry], object]
) -> None:
    """`__getattr__` must not turn every miss into a lecture about laziness."""
    obj = lazy_of(registry)
    # Through `getattr` rather than as an attribute: the name is deliberately
    # absent, so writing it as one is a type error in a file whose whole point
    # is that these classes now type-check.
    missing = "no_such_member"
    with pytest.raises(AttributeError, match=r"has no attribute 'no_such_member'"):
        getattr(obj, missing)


# ── nothing new goes missing quietly ──────────────────────────────────────


@pytest.mark.parametrize(
    ("real", "lazy", "label"),
    [(Codelist, LazyCodelist, "codelist"), (Hierarchy, LazyHierarchy, "hierarchy")],
)
def test_every_missing_member_is_refused_or_declared_class_level(real: type, lazy: type, label: str) -> None:
    """The inventory. A member added to `Codelist` / `Hierarchy` and not to the
    lazy class fails here rather than becoming the next bare `AttributeError`.

    This is what makes the two halves stay in step: the refusal table is data,
    and this compares it against the classes themselves.
    """
    missing = {name for name in dir(real) if not name.startswith("_") and not hasattr(lazy, name)}
    unhandled = missing - set(lazy._MATERIALISED_ONLY) - CLASS_LEVEL_ONLY
    assert not unhandled, (
        f"{lazy.__name__} is missing {sorted(unhandled)} from {real.__name__} with no "
        f"message naming a replacement — add them to _MATERIALISED_ONLY, or to "
        f"CLASS_LEVEL_ONLY if no instance can reach them"
    )


@pytest.mark.parametrize("lazy", [LazyCodelist, LazyHierarchy])
def test_the_refusal_is_live_at_runtime(lazy: type) -> None:
    """`__getattr__` sits under `if not TYPE_CHECKING`, so it is easy to read as
    dead code and delete. It is not: the guard hides it from the *checker* —
    a visible `__getattr__` makes every attribute resolve and would cancel the
    overloads — while leaving it in place for anyone running no checker.

    The static half of that trade cannot be asserted here (it is the absence of
    a type error); `tests/typing/probe_local_get.py` carries it as the
    must-not-type-check comment block this repo uses for negatives.
    """
    assert "__getattr__" in vars(lazy), (
        f"{lazy.__name__}.__getattr__ is gone — the TYPE_CHECKING guard hides it "
        f"from the checker on purpose, it is not dead code"
    )


@pytest.mark.parametrize("lazy", [LazyCodelist, LazyHierarchy])
def test_the_refusal_table_only_names_members_that_are_really_absent(lazy: type) -> None:
    """The other direction: an entry that stops being missing is dead weight,
    and worse, would shadow a member that now exists."""
    for name in lazy._MATERIALISED_ONLY:
        assert not hasattr(lazy, name), (
            f"{lazy.__name__}.{name} exists, so its _MATERIALISED_ONLY entry is dead "
            f"— `__getattr__` never fires for an attribute that resolves"
        )
