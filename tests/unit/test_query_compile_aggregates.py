"""What `_Scope._agg_body_from` emits, and the defect its survivors found (#324, installment 3).

`version` is the one column that sorts on something other than itself
(`version_sort`, #311), so its `min`/`max` are `arg_min`/`arg_max` over the sort
key (#351). Both callers handed `_agg_body_from` their own references, but the
key was resolved from the query root. Through a fan-out, `dsd.dataflows.version`
read `dataflows.spine` as crossing that fan-out and refused:
`q.dsd.dataflows.version.max()` raised, while `.list()`, `.n_unique()`,
`.sort().list()` and `max()` over any other column answered. The four
surviving mutants of the refusal's message were the trail. Each caller now
resolves the key in its own scope.

The rest pin what nothing pinned: an ordered list of several columns, the
`avg` a `mean` compiles to, and a localised label's language bind through an
aggregate.
"""

from collections.abc import Iterator

import pytest

import sdmxlib as sl
from sdmxlib.query import _expr
from sdmxlib.query.accessors import q
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """One DSD with dataflows at 1.9 and 1.10, where text order and version order disagree."""
    dsd = sl.DataStructure(
        id="DSD",
        maintainer="AG",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="D", concept=concept_ref("D"))],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    flows = [
        sl.Dataflow(id="DF", maintainer="AG", version=version, name={"en": "f"}, structure=sl.Ref.to(dsd))
        for version in ("1.9", "1.10")
    ]
    codelist = sl.Codelist(
        id="CL",
        maintainer="AG",
        name={"en": "c"},
        codes=[
            sl.Code(id="B", name={"en": "Bravo", "fr": "Bravo-fr"}),
            sl.Code(id="A", name={"en": "Alpha", "fr": "Alpha-fr"}),
            sl.Code(id="C", name={"en": "Charlie", "fr": "Charlie-fr"}, parent_id="A"),
        ],
    )
    with sl.LocalRegistry(":memory:") as registry:
        registry.add_all([dsd, *flows, codelist])
        yield registry


def test_a_version_max_through_a_fan_out_answers_by_version_order(reg: sl.LocalRegistry) -> None:
    """It refused; and as text, `1.9` would beat `1.10`."""
    frame = reg.query(q.dsd).select(newest=q.dsd.dataflows.version.max(), oldest=q.dsd.dataflows.version.min())
    assert frame.collect().to_dicts() == [{"newest": "1.10", "oldest": "1.9"}]


def test_a_grouped_version_max_still_answers_by_version_order(reg: sl.LocalRegistry) -> None:
    """The other caller, which resolved the key in its own scope already, and still does."""
    rows = reg.query(q.dataflow).group_by(q.dataflow.id).agg(newest=q.dataflow.version.max()).collect()
    assert rows.to_dicts() == [{"id": "DF", "newest": "1.10"}]


def test_version_is_the_only_column_that_sorts_on_another_key() -> None:
    """The premise behind the `arg_` branch's binds: its key is a bare column, with none to carry.

    So dropping the binds of `arg_min`/`arg_max` changes nothing observable today.
    A new column with its own sort key fails this and makes the branch's binds
    live.
    """
    declaring = {cls.__name__ for cls in vars(_expr).values() if isinstance(cls, type) and "sort_column" in vars(cls)}
    assert declaring == {"AnyCol", "Version"}, "AnyCol has the default; any other is a new sort key"


def test_an_ordered_list_of_several_columns_keeps_its_order(reg: sl.LocalRegistry) -> None:
    """The first column's `.sort()` orders the struct list. Nothing asserted it did."""
    codes = q.codelist.codes
    collected = (
        reg.query(q.codelist)
        .select(codes=codes.list(codes.id.sort(descending=True), codes.parent.id.alias("parent")))
        .collect()["codes"]
        .to_list()[0]
    )
    assert collected == [{"id": "C", "parent": "A"}, {"id": "B", "parent": None}, {"id": "A", "parent": None}]


def test_mean_compiles_to_avg() -> None:
    """`mean` is polars' name and `avg` SQL's. DuckDB accepts both; a second backend need not (§4)."""
    with sl.LocalRegistry(":memory:") as registry:
        compiled = registry.query(q.codelist).select(m=q.codelist.codes.position.mean()).to_sql()
    assert "avg(" in compiled.sql
    assert "mean(" not in compiled.sql


@pytest.mark.parametrize(("aggregate", "expected"), [("max", "Charlie"), ("min", "Alpha")])
def test_a_label_aggregate_through_a_fan_out_binds_its_language(
    reg: sl.LocalRegistry, aggregate: str, expected: str
) -> None:
    """A localised label's reference binds the language, which the aggregate has to pass on."""
    name = q.codelist.codes.name["en"]
    agg = name.max() if aggregate == "max" else name.min()
    assert reg.query(q.codelist).select(v=agg).collect()["v"].to_list() == [expected]
