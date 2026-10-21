"""Version ordering — `1.9` before `1.10`, which text ordering gets wrong (#311).

`artefact.version` is text, so every ordering over it was lexicographic and
"the latest version" — one of the most common catalogue questions — returned
the wrong artefact for any codelist whose minor version reached double digits.

The fix is a sort key baked at write time, which is what `caching.md`
prescribes for an input immutable within an artefact version. Three sites had
to change and each is tested separately, because they are built by *different*
code and only the first is obvious:

* `order_by(version)` — `_Scope.order`
* `rank().over(...)` — the window's own `ORDER BY`, built in `_window`
* `after()` paging — `_order_terms`, which must agree with `_Scope.order` or a
  cursor compares one column while the sort used another and rows are skipped
* `min()` / `max()` — `_agg_body_from`. Missed originally (#351): the aggregate
  path took the raw column, so `agg(version.max())` answered `1.9` over `1.10`
  while `order_by(version.desc())` answered correctly, and the library
  contradicted itself about which version was latest.

Comparison is deliberately *not* affected: `version == "1.10"` reads what the
registry published. Only ordering wants the key.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.organisation import Agency
from sdmxlib.model.version import sort_key
from sdmxlib.query.accessors import CodelistRow, q

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sdmxlib.query.query import Query

pytestmark = pytest.mark.unit


# ── the key itself ────────────────────────────────────────────────────────


def test_numeric_parts_compare_numerically() -> None:
    """#311's headline. `1.10` is a perfectly ordinary version."""
    assert sorted(["1.10", "1.9", "1.2"], key=sort_key) == ["1.2", "1.9", "1.10"]


def test_legacy_and_semantic_versions_interleave() -> None:
    """The XSD permits both, so a catalogue may hold both and must still order."""
    assert sorted(["2.0", "1.10.0", "1.9", "1"], key=sort_key) == ["1", "1.9", "1.10.0", "2.0"]


def test_a_missing_part_is_zero() -> None:
    """`1`, `1.0` and `1.0.0` are the same version written three ways."""
    assert sort_key("1") == sort_key("1.0") == sort_key("1.0.0")


def test_leading_zeros_do_not_change_the_version() -> None:
    """The XSD says so outright: *"1.3 and 1.03 would be equivalent, as both
    parse to an integer value of 3"* — which is exactly why text ordering is
    wrong rather than merely unusual."""
    assert sort_key("1.03") == sort_key("1.3")


def test_more_numeric_parts_than_three_still_order() -> None:
    """2.1 permits unlimited parts and `_check_version` accepts them (#319).

    The first cut of the key truncated to three, so `1.2.3.4` and `1.2.3.9`
    produced the *same* key and sorted equal — a silent tie in the middle of
    the ordering this exists to fix, and invisible unless a fixture used four
    parts. Neither the schema pattern nor a 3.0-shaped test would have caught
    it; the corpus has only `1.0`.
    """
    assert sorted(["1.2.3.9", "1.2.3.4", "1.2.3"], key=sort_key) == ["1.2.3", "1.2.3.4", "1.2.3.9"]


def test_more_parts_than_the_key_holds_collide() -> None:
    """The stated limit. Eight parts are significant; a ninth is not.

    Pinned so the boundary is a decision rather than a surprise — 3.0 caps at
    three parts and 2.1's "unlimited" is theoretical, so eight is slack rather
    than a guess, but a silent tie past it should still be written down.
    """
    assert sort_key("1.2.3.4.5.6.7.8.9") == sort_key("1.2.3.4.5.6.7.8.0")
    assert sort_key("1.2.3.4.5.6.7.8") != sort_key("1.2.3.4.5.6.7.9")


def test_a_prerelease_sorts_before_its_release() -> None:
    """SemVer's rule, and the opposite of what text gives: `-` sorts *below*
    the end of a string, so `1.0.0-draft` would otherwise follow `1.0.0`."""
    assert sorted(["1.0.0", "1.0.0-draft"], key=sort_key) == ["1.0.0-draft", "1.0.0"]


def test_prerelease_ordering_is_lexical_not_semver() -> None:
    """The stated limit, pinned so it is a decision rather than a surprise.

    Full SemVer precedence compares dot-separated identifiers, numeric ones
    numerically — so `alpha.9` precedes `alpha.10`. This sorts them as text.
    Left that way deliberately: the prerelease field is rare in published
    SDMX, and the fix is a second parser for a case no fixture uses.
    """
    assert sorted(["1.0.0-alpha.9", "1.0.0-alpha.10"], key=sort_key) == ["1.0.0-alpha.10", "1.0.0-alpha.9"]


def test_an_unparseable_version_sorts_before_every_real_one() -> None:
    """A registry sending something the XSD forbids should look *old*.

    Sorting it highest would make "the latest version" silently pick the
    malformed artefact — the failure this module exists to prevent, arriving
    by another route.
    """
    assert min(["1.0", "not-a-version", "0.1"], key=sort_key) == "not-a-version"


# ── the three query sites ─────────────────────────────────────────────────


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """One codelist at three versions, spanning the double-digit boundary.

    No `2.0`, deliberately: it is the largest both numerically *and*
    lexicographically, so a rank test including it passes against the bug.
    Every assertion below has to distinguish the two orderings.
    """
    with sl.LocalRegistry(":memory:") as registry:
        for version in ("1.2", "1.9", "1.10"):
            registry.add(Codelist(id="CL", maintainer=Agency(id="AG"), name={"en": "CL"}, version=version))
        yield registry


def _versions[A](query: Query[CodelistRow, A]) -> list[str]:
    rows = query.select(q.codelist.version).collect()
    return [str(value) for value in rows.to_series()]


def test_order_by_version_is_numeric(reg: sl.LocalRegistry) -> None:
    assert _versions(reg.query(q.codelist).order_by(q.codelist.version)) == ["1.2", "1.9", "1.10"]
    assert _versions(reg.query(q.codelist).order_by(q.codelist.version.desc())) == ["1.10", "1.9", "1.2"]


def test_rank_picks_the_latest_version(reg: sl.LocalRegistry) -> None:
    """The window's `ORDER BY` is built by `_window`, not `_Scope.order`.

    Two separate code paths, so fixing one left this returning `1.9` — which
    is the exact query #311 names and the headline example in
    `cookbook/discovery.md`.
    """
    latest = q.codelist.version.rank(descending=True).over(q.codelist.agency, q.codelist.id) == 1
    assert _versions(reg.query(q.codelist).qualify(latest)) == ["1.10"]


def test_paging_by_version_visits_every_row_once(reg: sl.LocalRegistry) -> None:
    """`after()` compares the columns the sort ordered by.

    If `ORDER BY` read `version_sort` while the cursor carried `version`, the
    comparison would be against a different column and rows would be skipped —
    silently, and only on catalogues that reach double digits.
    """
    seen: list[str] = []
    cursor = None
    for _ in range(3):
        page = reg.query(q.codelist).order_by(q.codelist.version).after(cursor).page(2)
        seen.extend(page.urns)
        cursor = page.cursor
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 3


# ── what must *not* change ────────────────────────────────────────────────


def test_comparison_still_reads_the_published_version(reg: sl.LocalRegistry) -> None:
    """Only ordering wants the key. A filter must match what a registry sent,
    and a caller types `"1.10"` rather than a padded key."""
    assert reg.query(q.codelist).filter(q.codelist.version == "1.10").count() == 1
    assert reg.query(q.codelist).filter(q.codelist.version.str.starts_with("1.")).count() == 3


def test_the_projected_value_is_the_published_version(reg: sl.LocalRegistry) -> None:
    """Sorting by the key must not leak it into the result."""
    assert set(_versions(reg.query(q.codelist))) == {"1.2", "1.9", "1.10"}


def test_only_version_diverges(reg: sl.LocalRegistry) -> None:
    """Every other column sorts by itself.

    A `sort_column` defaulting to the private slot rather than the public
    `column` made every *label* diverge too, and the compiler emitted
    `ORDER BY name` without its language bind. The SQL corpus caught it; this
    states the rule the corpus enforced.
    """
    assert q.codelist.version.sort_column == "version_sort"
    for column in (q.codelist.id, q.codelist.agency, q.codelist.urn, q.codelist.name["en"]):
        assert column.sort_column == column.column, column


def test_the_schema_default_matches_the_key_function() -> None:
    """The DDL carries `sort_key("1.0")` as a literal, because it is DDL.

    A literal that drifts from the function is the worst kind of stale: rows
    inserted without an explicit `version_sort` would sort somewhere arbitrary
    and nothing would say so. Changing `_PARTS` from 3 to 8 moved this, which
    is exactly the case the assertion is here for.
    """
    from sdmxlib.storage import schema

    tail = schema._DDL.split("version_sort    TEXT NOT NULL DEFAULT", 1)[1]
    literal = tail.split("'")[1]
    assert literal == sort_key("1.0")


# ── aggregates sort the same way ordering does (#351) ────────────────────────


def _versioned(*versions: str) -> sl.LocalRegistry:
    """One codelist id at several versions."""
    reg = sl.LocalRegistry(":memory:")
    reg.open()
    reg.add_all([sl.Codelist(id="CL_X", maintainer="AG", version=v, name={"en": v}) for v in versions])
    return reg


@pytest.mark.parametrize(
    "versions",
    [("1.9", "1.10"), ("1.0", "1.0.1", "1.2"), ("2.0", "10.0", "9.0"), ("1.0", "1.0.0-draft")],
    ids=["9-vs-10", "three-part", "major-10", "prerelease"],
)
def test_max_agrees_with_order_by(versions: tuple[str, ...]) -> None:
    """The defect, stated as the law it broke.

    `max()` compiled to `max(version)` over raw text while `order_by` routed
    through the baked `version_sort`, so the two disagreed about which version
    is latest — `max()` answered `1.9` over `1.10`. They are each other's
    oracle here: two independent implementations of "which is largest", and the
    only useful assertion is that they converge.
    """
    with _versioned(*versions) as reg:
        by_order = reg.query(q.codelist).order_by(q.codelist.version.desc()).select(v=q.codelist.version)
        newest = by_order.collect()["v"].to_list()[0]
        aggregated = reg.query(q.codelist).group_by(q.codelist.id).agg(v=q.codelist.version.max()).collect()

        assert aggregated["v"].to_list() == [newest], f"max() and order_by disagree for {versions}"


@pytest.mark.parametrize(
    "versions",
    [("1.9", "1.10"), ("1.0", "1.0.1", "1.2"), ("2.0", "10.0", "9.0")],
    ids=["9-vs-10", "three-part", "major-10"],
)
def test_min_agrees_with_order_by(versions: tuple[str, ...]) -> None:
    """The other end, because `arg_min` is a separate call and a fix that only
    reached `max` would leave `min` wrong in a way nothing else looks at."""
    with _versioned(*versions) as reg:
        oldest = reg.query(q.codelist).order_by(q.codelist.version).select(v=q.codelist.version).collect()
        aggregated = reg.query(q.codelist).group_by(q.codelist.id).agg(v=q.codelist.version.min()).collect()

        assert aggregated["v"].to_list() == [oldest["v"].to_list()[0]]


def test_the_aggregate_returns_the_version_not_its_sort_key() -> None:
    """`arg_max(version, version_sort)`, not `max(version_sort)`.

    The caller asked for the version. Returning the padded sort key would be
    just as ordered and completely unusable — and is the obvious wrong fix.
    """
    with _versioned("1.9", "1.10") as reg:
        value = reg.query(q.codelist).group_by(q.codelist.id).agg(v=q.codelist.version.max()).collect()["v"][0]
        assert value == "1.10", f"got {value!r}, which looks like the sort key rather than the version"


def test_a_plain_string_column_still_aggregates_as_text() -> None:
    """The narrowness of the change: only a column whose `sort_column` differs
    from itself is rerouted. `id` must keep plain `max()`, or every aggregate in
    the language pays for `arg_max`."""
    with _versioned("1.0") as reg:
        reg.add(sl.Codelist(id="CL_Y", maintainer="AG", version="1.0", name={"en": "y"}))
        got = reg.query(q.codelist).group_by(q.codelist.agency).agg(m=q.codelist.id.max()).collect()
        assert got["m"].to_list() == ["CL_Y"]
        sql, _ = reg.query(q.codelist).group_by(q.codelist.agency).agg(m=q.codelist.id.max()).to_sql()
        assert "arg_max" not in sql, "a plain text column should not be rerouted"


# ── ordered comparison, which was absent entirely (#352-adjacent) ────────────


@pytest.mark.parametrize(
    ("op", "bound", "expected"),
    [
        (">=", "1.10", ["1.10", "2.0"]),
        (">", "1.10", ["2.0"]),
        ("<", "1.10", ["1.9"]),
        ("<=", "1.9", ["1.9"]),
        (">=", "2.0", ["2.0"]),
        ("<", "1.0", []),
    ],
    ids=["ge-1.10", "gt-1.10", "lt-1.10", "le-1.9", "ge-2.0", "lt-none"],
)
def test_version_compares_on_the_sort_key(op: str, bound: str, expected: list[str]) -> None:
    """`q.dsd.version >= "2.0"` — "every artefact at 2.0 or later".

    Left out originally on the sound grounds that `>=` over the raw text makes
    `1.9 >= 1.10` true. But that left the question unaskable in a domain where
    versions are first-class, and the refusal came back as a bare Python
    message about `Version` and `str`.

    `version_sort` is already baked and already what `order_by` compares, so
    the comparison is exact rather than approximate. The `1.9`/`1.10` rows are
    the point of every case here: text ordering gets that pair backwards.
    """
    import operator

    with _versioned("1.9", "1.10", "2.0") as reg:
        compare = {">=": operator.ge, ">": operator.gt, "<": operator.lt, "<=": operator.le}[op]
        found = (
            reg.query(q.codelist)
            .filter(compare(q.codelist.version, bound))
            .select(v=q.codelist.version)
            .order_by(q.codelist.version)
            .collect()["v"]
            .to_list()
        )
    assert found == expected


def test_comparison_agrees_with_ordering() -> None:
    """The two must not disagree — they are the same question asked twice, and
    `agg(max())` disagreeing with `order_by` is exactly what #351 was."""
    with _versioned("1.9", "1.10", "2.0") as reg:
        ordered = reg.query(q.codelist).order_by(q.codelist.version).select(v=q.codelist.version)
        ascending = ordered.collect()["v"].to_list()

        for boundary in ascending:
            below = set(
                reg.query(q.codelist)
                .filter(q.codelist.version < boundary)
                .select(v=q.codelist.version)
                .collect()["v"]
                .to_list()
            )
            assert below == set(ascending[: ascending.index(boundary)])


def test_equality_still_reads_the_published_text() -> None:
    """Only *ordering* diverges. `== "1.10"` must compare what the registry
    published, not a padded key nobody typed."""
    with _versioned("1.9", "1.10") as reg:
        assert reg.query(q.codelist).filter(q.codelist.version == "1.10").ids() == ["CL_X"]
        assert reg.query(q.codelist).filter(q.codelist.version == "000000001.000000010").ids() == []


def test_comparing_against_a_non_string_says_what_to_write() -> None:
    """`version >= 2.0` with a float is the obvious mistake, and the bare
    Python message it used to give named neither the type nor the fix."""
    with pytest.raises(TypeError, match=re.escape('q.dsd.version >= "2.0"')):
        _ = q.codelist.version >= 2.0
