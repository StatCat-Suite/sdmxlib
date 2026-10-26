"""A codelist cut down to some codes and their ancestors (GH-12).

SDMX-REST returns one for ``detail=referencepartial``, keeping the codes a
dataflow's constraints name, and for ``/availability`` references, keeping the
codes with data and their parent codes. Both flag it ``isPartial``. Every test
runs on an eager `Codelist` and on the `LazyCodelist` a store returns for the
same codelist, and the two must agree.
"""

from collections.abc import Collection, Iterator

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code
from sdmxlib.storage import resolve
from sdmxlib.storage.lazy import LazyCodelist

pytestmark = pytest.mark.unit

#: Canada > Ontario > Toronto, Canada > Quebec > Montreal, and a second root.
#: Declared children-first in places, so codelist order is not tree order.
_CODES = [
    ("TOR", "ON"),
    ("CA", None),
    ("ON", "CA"),
    ("QC", "CA"),
    ("MTL", "QC"),
    ("US", None),
]


def _codelist() -> sl.Codelist:
    return sl.Codelist(
        id="CL_GEO",
        maintainer="AG",
        version="1.0",
        name={"en": "Geography"},
        codes=[sl.Code(id=code_id, name={"en": code_id}, parent_id=parent) for code_id, parent in _CODES],
    )


@pytest.fixture(params=["eager", "lazy"])
def codelist(request) -> Iterator[sl.Codelist | LazyCodelist]:
    eager = _codelist()
    if request.param == "eager":
        yield eager
        return
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(eager)
        lazy = reg.get(eager.urn)
        assert isinstance(lazy, LazyCodelist)
        yield lazy


def test_a_code_brings_its_ancestors(codelist) -> None:
    assert codelist.partial(["TOR"]).code_ids() == ["TOR", "CA", "ON"]


def test_codes_keep_the_codelists_order(codelist) -> None:
    assert codelist.partial(["MTL", "TOR"]).code_ids() == ["TOR", "CA", "ON", "QC", "MTL"]


def test_the_result_is_flagged_partial_and_otherwise_the_same(codelist) -> None:
    partial = codelist.partial(["US"])
    assert partial.is_partial
    assert (partial.urn, partial.name) == (codelist.urn, codelist.name)
    assert partial.code_ids() == ["US"]


def test_every_parent_is_a_code_of_the_result(codelist) -> None:
    partial = codelist.partial(["TOR", "MTL"])
    held = set(partial.code_ids())
    assert {code.parent_id for code in partial.codes if code.parent_id is not None} <= held


def test_eager_and_lazy_agree() -> None:
    eager = _codelist()
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(eager)
        lazy = reg.get(eager.urn)
        assert isinstance(lazy, LazyCodelist)
        asks: list[list[str]] = [["TOR"], ["MTL", "US"], ["CA"], [], [code_id for code_id, _ in _CODES]]
        for ids in asks:
            assert lazy.partial(ids) == eager.partial(ids), ids


def test_an_id_the_codelist_does_not_hold_is_refused_by_name(codelist) -> None:
    with pytest.raises(KeyError, match="NOWHERE"):
        codelist.partial(["TOR", "NOWHERE"])


def test_a_bare_string_is_refused(codelist) -> None:
    """``partial("TOR")`` would otherwise ask for codes T, O and R."""
    with pytest.raises(TypeError, match=r"partial\(\['TOR'\]\)"):
        codelist.partial("TOR")


def test_extensions_are_not_carried() -> None:
    """The partial lists its codes; an extension would add the base codelist's back."""
    base = sl.Codelist(
        id="CL_BASE", maintainer="AG", version="1.0", name={"en": "B"}, codes=[sl.Code(id="X", name={"en": "X"})]
    )
    extended = sl.Codelist(
        id="CL_EXT",
        maintainer="AG",
        version="1.0",
        name={"en": "E"},
        codes=[sl.Code(id="Y", name={"en": "Y"})],
        extensions=[sl.CodelistExtension(codelist=sl.Ref.to(base))],
    )
    assert extended.partial(["Y"]).extensions == ()
    with pytest.raises(KeyError, match="apply them first"):
        extended.partial(["X"])


def test_a_lazy_partial_builds_only_what_it_keeps(monkeypatch) -> None:
    """The cost follows the result: one census geography is a handful of codes of 579,951."""
    asked: list[set[str]] = []
    original = resolve.codes_by_id

    def spy(
        conn: duckdb.DuckDBPyConnection, codelist_urn: str, ids: Collection[str], *, annotations: bool = True
    ) -> dict[str, Code]:
        asked.append(set(ids))
        return original(conn, codelist_urn, ids, annotations=annotations)

    eager = _codelist()
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(eager)
        lazy = reg.get(eager.urn)
        assert isinstance(lazy, LazyCodelist)
        monkeypatch.setattr(resolve, "codes_by_id", spy)
        lazy.partial(["TOR"])
    assert asked == [{"TOR", "ON", "CA"}]
