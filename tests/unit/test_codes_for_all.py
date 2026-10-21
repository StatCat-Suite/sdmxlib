"""Several codelists' codes in one read: `codes_for_all` and `resolve.codes_by_ids` (#441).

The SDMX-JSON data writer asks every enumerated component's codelist for the
ids a message observed. Asked one codelist at a time from a store that was
three statements per component: 54 statements and ~79 ms for a census DSD's
1-observation message. These read them all at once and must answer exactly
what `codes_for` would, codelist by codelist.
"""

from collections.abc import Collection, Iterator, Mapping

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import codes_for_all
from sdmxlib.session import _REGISTER_ABOVE
from sdmxlib.storage import resolve
from sdmxlib.storage.lazy import LazyCodelist

pytestmark = pytest.mark.unit

_SMALL = sl.Codelist(
    id="CL_SMALL",
    maintainer="AG",
    version="1.0",
    name={"en": "Small"},
    codes=[sl.Code(id="A", name={"en": "Alpha"}), sl.Code(id="B", name={"en": "Beta"}, parent_id="A")],
)
#: More codes than `value_match` binds, so its branch registers a relation.
_LARGE = sl.Codelist(
    id="CL_LARGE",
    maintainer="AG",
    version="1.0",
    name={"en": "Large"},
    codes=[sl.Code(id=f"L{i}", name={"en": f"Large {i}"}) for i in range(3 * _REGISTER_ABOVE)],
)


@pytest.fixture
def lazy() -> Iterator[tuple[LazyCodelist, LazyCodelist]]:
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all([_SMALL, _LARGE])
        small, large = reg.get(_SMALL.urn), reg.get(_LARGE.urn)
        assert isinstance(small, LazyCodelist)
        assert isinstance(large, LazyCodelist)
        yield small, large


def test_each_request_gets_what_codes_for_would(lazy: tuple[LazyCodelist, LazyCodelist]) -> None:
    small, large = lazy
    many = [f"L{i}" for i in range(2 * _REGISTER_ABOVE)]
    requests = {
        "s": (small, ["A", "B", "nope"]),
        "l": (large, [*many, "nope"]),
        "empty": (small, []),
    }
    found = codes_for_all(requests)
    assert found == {key: codelist.codes_for(ids) for key, (codelist, ids) in requests.items()}
    assert found["s"]["B"].parent_id == "A"
    assert sorted(found["l"]) == sorted(many)
    assert found["empty"] == {}


def test_two_requests_for_one_codelist_each_get_their_own_ids(lazy: tuple[LazyCodelist, LazyCodelist]) -> None:
    """Two components can share a codelist; the read merges them, the answers must not."""
    small, _ = lazy
    found = codes_for_all({"first": (small, ["A"]), "second": (small, ["B"])})
    assert sorted(found["first"]) == ["A"]
    assert sorted(found["second"]) == ["B"]


def test_eager_and_lazy_codelists_are_each_answered_by_their_own_kind(lazy: tuple[LazyCodelist, LazyCodelist]) -> None:
    small, _ = lazy
    found = codes_for_all({"lazy": (small, ["A"]), "eager": (_LARGE, ["L1", "nope"])})
    assert sorted(found["lazy"]) == ["A"]
    assert sorted(found["eager"]) == ["L1"]


def test_every_lazy_codelist_is_read_in_one_statement_set(
    lazy: tuple[LazyCodelist, LazyCodelist], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One `codes_by_ids` for both codelists, however many there are."""
    small, large = lazy
    calls: list[set[str]] = []
    original = resolve.codes_by_ids

    def spy(conn: duckdb.DuckDBPyConnection, wanted: Mapping[str, Collection[str]]) -> dict[str, dict[str, sl.Code]]:
        calls.append(set(wanted))
        return original(conn, wanted)

    monkeypatch.setattr(resolve, "codes_by_ids", spy)
    codes_for_all({"s": (small, ["A"]), "l": (large, ["L1"])})
    assert calls == [{str(small.urn), str(large.urn)}]
