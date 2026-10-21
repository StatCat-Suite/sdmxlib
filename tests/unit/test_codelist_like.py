"""`Codelist` and `LazyCodelist` answer the same questions (#398).

`Dimension.resolved_codelist` follows the component's `Ref`. When the DSD came
from a `LocalRegistry` that ref resolves to a `LazyCodelist`, not a `Codelist`,
so the property's declaration was wrong for every local caller — and not only
in the checker. Five public entry points raised `TypeError` on it, because they
iterated or took `len()` of something that had quietly become a `Query`:

    polars.schema            polars.validation_report    polars.structure_frame
    Dataset.pivot            Dataset.label

`CodelistLike` is what the two share. The gaps it exposed were closed rather
than narrowed around — `Codelist` gained `code_ids`, `code_map`, `materialize`
and `__len__`, all of which `LazyCodelist` already had in substance — so no
caller carries an `isinstance` branch and each class answers on its own cheapest
path.

**Which path is cheap is the whole reason the protocol is shaped this way.**
Measured on `CL_GEOGRAPHY`, 579,951 codes:

| | cost |
| --- | --- |
| `code_ids()` | 92 ms |
| `len()` | 22 ms |
| `materialize()` | 2,854 ms |

So "just materialise everything" was rejected at 31x and 129x — the cost #378
removed. It survives as `materialize()`, for the two callers that genuinely
need each `Code`.

The parametrisation is the point: every test runs against both classes, and a
member that only one of them can answer fails here rather than at the call site
that eventually reaches for it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import CodelistLike
from sdmxlib.model.collections import ItemList
from sdmxlib.storage.lazy import LazyCodelist
from tests.support.factories import concept_ref

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

CODES = [
    sl.Code(id="A", name={"en": "Annual", "fr": "Annuel"}),
    sl.Code(id="Q", name={"en": "Quarterly"}),
    # Named in French only: validation requires *a* name, and the English
    # lookup must still fall back to the id rather than dropping the code.
    sl.Code(id="M", name={"fr": "Mensuel"}),
]


def _codelist() -> sl.Codelist:
    return sl.Codelist(id="CL_FREQ", maintainer="AG", version="1.0", name={"en": "Freq"}, codes=CODES)


def _dsd(codelist: sl.Codelist) -> sl.DataStructure:
    return sl.DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=sl.Ref.to(codelist))],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )


@pytest.fixture
def both() -> Iterator[dict[str, CodelistLike]]:
    """The same codelist as both classes, so every assertion runs twice.

    The lazy one is reached the way a user reaches it — through a DSD read back
    out of a registry — rather than constructed directly, because that path is
    what made the divergence invisible.
    """
    eager = _codelist()
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all([eager, _dsd(eager)])
        stored = reg.get(sl.DataStructure.urn_for(agency="AG", id="DSD", version="1.0"))
        assert stored is not None
        lazy = stored.dimensions["FREQ"].resolved_codelist
        assert isinstance(lazy, LazyCodelist), "the premise of this whole file"
        yield {"eager": eager, "lazy": lazy}


@pytest.mark.parametrize("kind", ["eager", "lazy"])
def test_both_satisfy_the_protocol(both: dict[str, CodelistLike], kind: str) -> None:
    """Runtime-checkable, so this is a real check and not a restatement."""
    assert isinstance(both[kind], CodelistLike)


@pytest.mark.parametrize("kind", ["eager", "lazy"])
def test_code_ids_agree(both: dict[str, CodelistLike], kind: str) -> None:
    assert both[kind].code_ids() == ["A", "Q", "M"], "in declaration order, on both"


@pytest.mark.parametrize("kind", ["eager", "lazy"])
def test_len_is_the_code_count(both: dict[str, CodelistLike], kind: str) -> None:
    """`len()` replaced `len(cl.codes)`, which raised on the lazy side."""
    assert len(both[kind]) == 3


@pytest.mark.parametrize("kind", ["eager", "lazy"])
def test_code_map_falls_back_to_the_id(both: dict[str, CodelistLike], kind: str) -> None:
    """`M` has no English name, and must still appear.

    A label map with holes silently drops rows when a caller joins on it, so
    the fallback is part of the contract rather than a convenience — and the
    two implementations reach it by different routes, a `COALESCE` in SQL and
    an `or` in Python.
    """
    mapping = both[kind].code_map(lang="en")
    assert mapping == {"A": "Annual", "Q": "Quarterly", "M": "M"}


@pytest.mark.parametrize("kind", ["eager", "lazy"])
def test_code_map_honours_the_language(both: dict[str, CodelistLike], kind: str) -> None:
    assert both[kind].code_map(lang="fr")["A"] == "Annuel"
    assert both[kind].code_map(lang="fr")["Q"] == "Q", "and falls back per code, not per map"
    assert both[kind].code_map(lang="fr")["M"] == "Mensuel"


@pytest.mark.parametrize("kind", ["eager", "lazy"])
def test_codes_for_builds_exactly_the_ids_asked_for(both: dict[str, CodelistLike], kind: str) -> None:
    """Unknown ids are absent rather than an error, and nothing extra comes back (#410)."""
    found = both[kind].codes_for(["Q", "A", "NOPE"])
    assert sorted(found) == ["A", "Q"]
    assert found["A"] == CODES[0]
    assert found["Q"] == CODES[1]


@pytest.mark.parametrize("kind", ["eager", "lazy"])
def test_codes_for_nothing_is_empty(both: dict[str, CodelistLike], kind: str) -> None:
    assert both[kind].codes_for([]) == {}


@pytest.mark.parametrize("kind", ["eager", "lazy"])
def test_materialize_gives_the_real_codes(both: dict[str, CodelistLike], kind: str) -> None:
    """The escape hatch, for callers needing more than ids and labels."""
    real = both[kind].materialize()
    assert isinstance(real, sl.Codelist)
    assert isinstance(real.codes, ItemList)
    assert [c.id for c in real.codes] == ["A", "Q", "M"]
    assert real.codes["A"].name["fr"] == "Annuel"


def test_materialize_on_an_eager_codelist_is_free() -> None:
    """It returns `self`, not a copy.

    The protocol promises the codes are built, not that the result is fresh,
    and copying a 579,951-code codelist to honour a call that changes nothing
    would be the cost this issue is about.
    """
    eager = _codelist()
    assert eager.materialize() is eager


@pytest.mark.parametrize("kind", ["eager", "lazy"])
def test_the_scalar_identity_is_shared(both: dict[str, CodelistLike], kind: str) -> None:
    assert both[kind].id == "CL_FREQ"
    assert both[kind].version == "1.0"


def test_the_protocol_declares_nothing_the_classes_lack() -> None:
    """The inventory, so a member added to `CodelistLike` cannot go unimplemented.

    `isinstance` above already covers the runtime side; this names *which*
    member is missing, which a `False` from a protocol check does not.
    """
    declared = {name for name in vars(CodelistLike) if not name.startswith("_")}
    for cls in (sl.Codelist, LazyCodelist):
        missing = sorted(name for name in declared if not hasattr(cls, name))
        assert not missing, f"{cls.__name__} does not implement {missing} from CodelistLike"


def test_name_and_description_are_absent_for_a_reason() -> None:
    """Both classes have them; the protocol cannot declare them.

    basedpyright reads `Codelist.name` as `InternationalString` for attribute
    access and as `__converterDescriptor_name` for protocol matching, so a
    protocol naming it does not check even though every read of it does. Same
    for `description` and for `codes` — which is why `code_ids()` exists as a
    method rather than the field it wraps.

    Asserted rather than left as a comment so that the day the checker is fixed
    this fails, and whoever sees it can widen the protocol instead of
    rediscovering the limitation.
    """
    declared = set(vars(CodelistLike))
    assert "name" not in declared
    assert "description" not in declared
    assert "codes" not in declared
    for cls in (sl.Codelist, LazyCodelist):
        assert hasattr(cls, "name"), f"{cls.__name__} really does have it"
        assert hasattr(cls, "description")
        assert hasattr(cls, "codes")
