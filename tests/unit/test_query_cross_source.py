"""A relation from one database, spliced into a query on another (§5.9).

`SqlRelation`'s contract — *"the subquery's tables must be visible on the
consumer's connection"* — was stated in prose and checked by nothing. So this
composed silently:

    obs = a.obs(flow)
    obs.filter(obs.col("REF_AREA").is_in(b.query(q.code)...))

and produced **a wrong answer, not an error**: the other registry's SQL *text*
was spliced into this connection, where the same table names resolve to this
database's rows. Measured before the fix, with two registries whose codelists
disagreed about which codes are named "Ontario" — `b` alone answered
``['ON', 'QC']``, and the composed filter returned `a`'s answer, ``['ON']``.

The refusal is the whole point, so `test_the_disagreement_is_what_makes_this_
matter` builds registries that *disagree*: against two identical registries
every assertion here would pass for the wrong reason.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import sdmxlib as sl
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.ref import Ref
from sdmxlib.query.query import CrossSourceError
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


def _build(reg: sl.LocalRegistry, labels: dict[str, str]) -> sl.Dataflow:
    """A registry with one dataflow, two rows, and codes named by `labels`."""
    codelist = sl.Codelist(
        id="CL_GEO",
        maintainer="AG",
        version="1.0",
        name={"en": "geo"},
        codes=[sl.Code(id=code, name={"en": label}) for code, label in labels.items()],
    )
    dsd = sl.DataStructure(
        id="D",
        maintainer="AG",
        version="1.0",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="REF_AREA", concept=concept_ref("REF_AREA"), representation=Ref.to(codelist))],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    flow = sl.Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "f"}, structure=Ref.to(dsd))
    reg.add_all([codelist, dsd, flow])
    cursor = reg.connection.cursor()
    cursor.execute("CREATE SCHEMA IF NOT EXISTS data")
    cursor.execute("CREATE TABLE data.obs (REF_AREA TEXT, OBS_VALUE DOUBLE)")
    cursor.execute("INSERT INTO data.obs VALUES ('ON', 1.0), ('QC', 2.0)")
    reg.add(
        DataflowBinding(
            dataflow=Ref.to(flow),
            database_role="memory",
            table_location="data.obs",
            dimension_column_map={"REF_AREA": "REF_AREA"},
            measure_column_map={"OBS_VALUE": "OBS_VALUE"},
            attribute_column_map={},
            structural_version="AG:D(1.0)",
            source_registry="local",
            materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
            bound_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    return flow


@pytest.fixture
def two_registries():
    """Two registries that **disagree**: `a` calls only ON "Ontario", `b` calls
    both ON and QC "Ontario". So "codes named Ontario" has a different answer
    on each, and a filter that reads the wrong one is visibly wrong."""
    with sl.LocalRegistry(":memory:") as a, sl.LocalRegistry(":memory:") as b:
        flow = _build(a, {"ON": "Ontario", "QC": "Quebec"})
        _build(b, {"ON": "Ontario", "QC": "Ontario"})
        yield a, b, flow


def _ontario(reg: sl.LocalRegistry):
    return reg.query(sl.q.code).filter(sl.q.code.name["en"] == "Ontario").select(sl.q.code.id)


# ── the refusal ───────────────────────────────────────────────────────────


def test_the_disagreement_is_what_makes_this_matter(two_registries) -> None:
    """The fixture's discriminating power, asserted rather than assumed.

    If both registries answered the same, every test below would pass against
    a library that did no checking at all.
    """
    a, b, _ = two_registries
    assert a.query(sl.q.code).filter(sl.q.code.name["en"] == "Ontario").ids() == ["ON"]
    assert b.query(sl.q.code).filter(sl.q.code.name["en"] == "Ontario").ids() == ["ON", "QC"]


def test_a_foreign_relation_in_an_observation_filter_is_refused(two_registries) -> None:
    """The measured bug. Returned `['ON']` — `a`'s answer to `b`'s question."""
    a, b, flow = two_registries
    obs = a.obs(flow)
    with pytest.raises(CrossSourceError):
        obs.filter(obs.col("REF_AREA").is_in(_ontario(b)))


def test_a_foreign_relation_in_a_structural_filter_is_refused(two_registries) -> None:
    """Not only the data plane: the same splice between two structural
    queries is the same wrong answer."""
    a, b, _ = two_registries
    with pytest.raises(CrossSourceError):
        a.query(sl.q.code).filter(sl.q.code.id.is_in(_ontario(b)))


def test_the_refusal_names_both_databases(two_registries) -> None:
    """§5.9 asks the error to say *which two* could not see each other. A
    `DuckDBPyConnection` has no useful `repr`, so naming them takes a label
    carried from the registry — without it the message reads "a different
    database (DuckDBPyConnection)" twice and identifies neither."""
    a, b, flow = two_registries
    obs = a.obs(flow)
    with pytest.raises(CrossSourceError) as caught:
        obs.filter(obs.col("REF_AREA").is_in(_ontario(b)))

    message = str(caught.value)
    assert message.count(":memory:") == 2, "both sides named"
    assert f"0x{id(a):x}" in message, "and distinguishable — two in-memory stores are two databases"
    assert f"0x{id(b):x}" in message


def test_the_refusal_says_what_to_do(two_registries) -> None:
    """A refusal that does not name the fix just moves the puzzle."""
    a, b, flow = two_registries
    obs = a.obs(flow)
    with pytest.raises(CrossSourceError) as caught:
        obs.filter(obs.col("REF_AREA").is_in(_ontario(b)))

    message = str(caught.value)
    assert "store=" in message, "the chain position that puts both on one database"
    assert "wrong answer" in message, "and why this is refused rather than merely slow"


@pytest.mark.parametrize("wrap", ["and", "or", "not"])
def test_a_foreign_relation_nested_under_a_boolean_is_refused(two_registries, wrap: str) -> None:
    """A check that only looked at the outermost predicate would pass exactly
    the composition a caller reaches for when combining two registries."""
    a, b, flow = two_registries
    obs = a.obs(flow)
    foreign = obs.col("REF_AREA").is_in(_ontario(b))
    local = obs.col("OBS_VALUE") > 0.0
    combined = {"and": lambda: foreign & local, "or": lambda: foreign | local, "not": lambda: ~foreign}[wrap]()

    with pytest.raises(CrossSourceError):
        obs.filter(combined)


# ── what must still compose ───────────────────────────────────────────────


def test_one_registry_still_crosses_its_own_planes(two_registries) -> None:
    """The composition the refusal must not break, and nearly did.

    `ObsQuery` runs on a *per-thread cursor* while a structural `Query` holds
    the base connection, so comparing the connection objects refuses the
    same-registry case. The token is the base connection for exactly this
    reason — caught by `test_a_relation_valued_filter_crosses_the_planes`
    before this file existed.
    """
    a, _, flow = two_registries
    obs = a.obs(flow)
    frame = obs.filter(obs.col("REF_AREA").is_in(_ontario(a))).frame()
    assert frame["REF_AREA"].to_list() == ["ON"]


def test_a_relation_that_cannot_name_its_source_is_trusted(two_registries) -> None:
    """Foreign producers — statcatlib's builders, `reg.sql(...)` — need not
    implement `HasSource`, and Topology B makes their composition legitimate.
    So "cannot say" means trusted, which is the behaviour before this existed.
    """
    a, _, flow = two_registries

    class _Anonymous:
        columns = ("id",)

        def to_sql(self) -> tuple[str, list[object]]:
            return "SELECT 'ON' AS id", []

    obs = a.obs(flow)
    frame = obs.filter(obs.col("REF_AREA").is_in(_Anonymous())).frame()
    assert frame["REF_AREA"].to_list() == ["ON"]


def test_a_producer_whose_source_raises_is_trusted(two_registries) -> None:
    """A closed registry, say. Its failure is not this call's to report —
    surfacing someone else's error from `is_in` would name the wrong thing."""
    a, _, flow = two_registries

    class _Broken:
        columns = ("id",)

        @property
        def source(self) -> object:
            msg = "closed"
            raise RuntimeError(msg)

        def to_sql(self) -> tuple[str, list[object]]:
            return "SELECT 'ON' AS id", []

    obs = a.obs(flow)
    assert obs.filter(obs.col("REF_AREA").is_in(_Broken())).frame()["REF_AREA"].to_list() == ["ON"]


def test_a_plain_list_is_unaffected(two_registries) -> None:
    """The non-relation form of `is_in` has no source to disagree about."""
    a, _, flow = two_registries
    obs = a.obs(flow)
    assert obs.filter(obs.col("REF_AREA").is_in(["ON"])).frame()["REF_AREA"].to_list() == ["ON"]


# ── set operations splice the same way ────────────────────────────────────


@pytest.mark.parametrize("op", ["intersect", "difference", "union"])
def test_a_set_operation_across_databases_is_refused(two_registries, op: str) -> None:
    """Every set operation compiles the other query's SQL into this one exactly
    as `is_in` does, so all three carry the same wrong answer. Cheaper to
    check, too: the argument *is* a `Query`, so there is no foreign producer
    that might legitimately be unable to name its source.

    **`union` was missing here and was wrong.** The source check lives in
    `_membership`, which `intersect` and `difference` reach and `union` — a
    free function, for the refinement reason its docstring gives — does not.
    This parametrisation named two of the three operations, so nothing looked.
    """
    a, b, _ = two_registries
    mine = a.query(sl.q.code)
    theirs = b.query(sl.q.code).filter(sl.q.code.name["en"] == "Ontario")
    combine = (lambda: sl.union(mine, theirs)) if op == "union" else (lambda: getattr(mine, op)(theirs))

    with pytest.raises(CrossSourceError, match="wrong answer"):
        combine()


def test_the_union_across_databases_really_was_wrong(two_registries) -> None:
    """What the refusal is protecting against, stated as the answer it gave.

    Two registries holding *disjoint* codes: `union` returned only the near
    side's, having dropped every row the far side contributed — because `b`'s
    SQL ran against `a`'s connection, where those rows do not exist. Not "wrong
    by a duplicate": a whole operand vanished, and the result still looked like
    a plausible set.

    Kept as a test rather than a note because the fixture that shows it is the
    thing worth preserving — two stores that disagree.
    """
    a, b, _ = two_registries
    only_in_a = a.query(sl.q.code).filter(sl.q.code.id == "QC")
    only_in_b = b.query(sl.q.code).filter(sl.q.code.name["en"] == "Ontario")

    with pytest.raises(CrossSourceError):
        sl.union(only_in_a, only_in_b)

    # Within one database the same shape is correct, which is what makes the
    # refusal above a refusal rather than a missing feature.
    both = sl.union(only_in_a, a.query(sl.q.code).filter(sl.q.code.id == "ON"))
    assert sorted(both.ids()) == ["ON", "QC"]


@pytest.mark.parametrize("op", ["intersect", "difference"])
def test_a_set_operation_within_one_database_still_works(two_registries, op: str) -> None:
    a, _, _ = two_registries
    mine = a.query(sl.q.code)
    theirs = a.query(sl.q.code).filter(sl.q.code.name["en"] == "Ontario")
    assert getattr(mine, op)(theirs).ids() == (["ON"] if op == "intersect" else ["QC"])
