"""Alias allocation — that nested scopes never collide (#267, #306).

`CompileContext.next_alias` was the counter under the four retired DSLs; the
path language's is `_Namespace`, which hands out a *pair* — a root alias and a
prefix for the joins hanging off it — because a scope now owns a join tree
rather than a flat list of joins.

The claim is unchanged and is the only one worth pinning here: two scopes
compiled in the same statement must not name the same alias, or a correlated
subquery silently reads the outer row.
"""

import pytest

from sdmxlib.query._compile import _Namespace
from tests.support.narrow import present

pytestmark = pytest.mark.unit


def test_each_scope_gets_a_distinct_alias_and_prefix() -> None:
    names = _Namespace()

    assert names.next() == ("s0", "s0_")
    assert names.next() == ("s1", "s1_")
    assert names.next() == ("s2", "s2_")


def test_counters_are_per_namespace_not_global() -> None:
    """Otherwise aliases would depend on how many queries the process had
    already compiled, and `to_sql()` would not be reproducible."""
    first, second = _Namespace(), _Namespace()

    assert first.next() == ("s0", "s0_")
    assert first.next() == ("s1", "s1_")
    assert second.next() == ("s0", "s0_")


def test_a_nested_scope_never_reuses_the_outer_alias() -> None:
    """The property the counter exists for, stated over a real compile.

    A fan-out compiles to a correlated `EXISTS` whose inner scope draws from
    the same namespace as the outer one — if it drew `s0` again, the
    correlation condition would compare the inner row to itself and the
    subquery would match every row.
    """
    import sdmxlib as sl
    from sdmxlib.model.codelist import Code, Codelist
    from sdmxlib.query.accessors import q

    with sl.LocalRegistry(":memory:") as reg:
        reg.add(
            Codelist(
                id="CL",
                maintainer="AG",
                version="1.0",
                name={"en": "n"},
                codes=[Code(id="A", name={"en": "a"}), Code(id="B", name={"en": "b"}, parent_id="A")],
            )
        )
        codes = present(reg.get(Codelist.urn_for(agency="AG", id="CL", version="1.0"))).codes
        sql, _ = codes.filter(q.code.children.id == "B").to_sql()

    inner = sql[sql.index("EXISTS (") :]
    assert "FROM sdmx.code s1" in inner, "the inner scope drew its own alias"
    assert "FROM sdmx.code s0" not in inner, "the inner scope reused the outer's"
    # And the correlation names both, which is only meaningful if they differ.
    assert "s0.code_id = s1.parent_id" in inner
