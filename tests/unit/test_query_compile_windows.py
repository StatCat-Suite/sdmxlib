"""What `_window` emits that nothing pinned (#324, installment 4).

Two of `_window`'s fifteen survivors were gaps, and six were dead code: windowed
`n_unique` returns early as two windowed aggregates (#326), so its entries in
the `DISTINCT` set and the name map were never read. They are gone. These pin
the gaps:
- `mean` compiles to `avg`, which is what a second backend reads (§4);
- a windowed `n_unique` over a label passes on the language its reference
  binds.
"""

from collections.abc import Iterator

import pytest

import sdmxlib as sl
from sdmxlib.query.accessors import q

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Two agencies; AG's two codelists share an English name, so a distinct count of it is 1, not 2."""
    lists = [
        sl.Codelist(id="A1", maintainer="AG", name={"en": "same", "fr": "un"}),
        sl.Codelist(id="A2", maintainer="AG", name={"en": "same", "fr": "deux"}),
        sl.Codelist(id="B1", maintainer="OTHER", name={"en": "other", "fr": "trois"}),
    ]
    with sl.LocalRegistry(":memory:") as registry:
        registry.add_all(lists)
        yield registry


def test_a_windowed_mean_compiles_to_avg(reg: sl.LocalRegistry) -> None:
    """DuckDB answers `mean()` too, so only the emitted SQL tells the two apart."""
    compiled = reg.query(q.code).select(m=q.code.position.mean().over(q.code.codelist.id)).to_sql()
    assert "avg(" in compiled.sql
    assert "mean(" not in compiled.sql


def test_a_windowed_n_unique_over_a_label_binds_its_language(reg: sl.LocalRegistry) -> None:
    """The label's reference binds `en`. Dropping the binds was a parameter-count error, and untested."""
    distinct = q.codelist.name["en"].n_unique().over(q.codelist.agency)
    rows = reg.query(q.codelist).select(id=q.codelist.id, n=distinct).collect().sort("id").to_dicts()
    assert rows == [{"id": "A1", "n": 1}, {"id": "A2", "n": 1}, {"id": "B1", "n": 1}]


def test_a_windowed_n_unique_still_counts_distinct_values(reg: sl.LocalRegistry) -> None:
    """The discrimination check for the deletion: the French names differ, so their distinct count is 2."""
    distinct = q.codelist.name["fr"].n_unique().over(q.codelist.agency)
    rows = reg.query(q.codelist).select(id=q.codelist.id, n=distinct).collect().sort("id").to_dicts()
    assert rows == [{"id": "A1", "n": 2}, {"id": "A2", "n": 2}, {"id": "B1", "n": 1}]
