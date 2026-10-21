"""What a fetch must contain, so a locally-evaluated query is not answered short.

§13. A backend that computes nothing answers by fetching artefacts and running
the original query over them, so the fetch has to close over every relation the
query's paths reach. Fetch one relation short and nothing fails — the query
evaluates against an empty table and returns *nothing*.

That silence is why this module is tested against the recorded responses rather
than only against itself: `test_the_depth_rule_matches_what_the_wire_returns`
fetches short on purpose and shows the empty answer, so the rule is pinned to a
measured fact rather than to my reading of the SDMX spec.

The distinction the whole thing turns on is **containment versus crossing**.
`dsd.dimensions` stays inside the DSD, arrives with it, and costs no fetch.
`component.concept` leaves for the concept scheme and does. Counting hops
conflates them and asks for `descendants` everywhere; counting crossings asks
for the cheapest fetch that closes.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.ref import SupportsUrn
from sdmxlib.query._closure import closure, crossings, detail_for, reached_columns, reached_paths, references_for
from sdmxlib.query._expr import OrderKey
from sdmxlib.query._plan import Plan, scanned
from sdmxlib.query.accessors import CodelistRow, DataflowRow, q
from tests.support import repo

if TYPE_CHECKING:
    from sdmxlib.query._expr import Pred

pytestmark = pytest.mark.unit


# ── crossings ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "expected", "why"),
    [
        ((), 0, "the root's own columns"),
        (("dataflow",), 0, "the artefact's extension row — the same artefact, a second table"),
        (("annotations",), 0, "reverse hop: annotations arrive with their owner"),
        (("dataflow", "dsd"), 1, "a reference to another artefact"),
        (("dataflow", "dsd", "dimensions"), 1, "components are contained by the DSD already fetched"),
        (("dataflow", "dsd", "dimensions", "concept"), 2, "the concept scheme is a second artefact out"),
    ],
)
def test_crossings_counts_artefacts_left_behind_not_hops(path: tuple[str, ...], expected: int, why: str) -> None:
    assert crossings("artefact", path) == expected, why


def test_containment_is_free_and_a_reference_is_not() -> None:
    """The pair that a hop count would get wrong, side by side.

    Both are one hop past the DSD. `dimensions` arrives inside it; `concept`
    lives in an artefact nobody has fetched.
    """
    assert crossings("artefact", ("dataflow", "dsd", "dimensions")) == crossings("artefact", ("dataflow", "dsd"))
    assert crossings("artefact", ("dataflow", "dsd", "dimensions", "concept")) == 2


def test_the_extension_hop_is_not_a_crossing() -> None:
    """`artefact.urn -> dataflow.urn` is one artefact wearing two tables.

    Counting it would make every maintainable look a crossing deeper than it
    is, and every query ask for one `references=` level more than it needs.
    """
    assert crossings("artefact", ("dataflow",)) == 0


# ── reached paths ─────────────────────────────────────────────────────────


def test_a_quantified_predicate_reports_its_parts_absolutely() -> None:
    """The reason `PredicateShape.inner` exists, stated as the consequence.

    A quantifier's parts are pathed from the fan-out. Flattened, this reports
    `("concept",)` as a path from the *dataflow* — a different relation, whose
    closure is a different fetch.
    """
    predicate: Pred[DataflowRow] = q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA")
    assert ("dataflow", "dsd", "dimensions", "concept") in reached_paths(predicate)
    assert ("concept",) not in reached_paths(predicate)


def test_the_closure_covers_sort_keys_not_only_filters() -> None:
    """A sort key crossing an edge needs those rows present just as much.

    Easy to miss, because the query "looks" unfiltered.
    """
    plan = Plan(root=scanned(q.dataflow), order_keys=(OrderKey(q.dataflow.dsd.id, ascending=True),))
    assert ("dataflow", "dsd") in closure(plan)


# ── the references value ──────────────────────────────────────────────────


def test_the_cheapest_value_that_closes_is_chosen() -> None:
    assert references_for("artefact", [()]) == "none"
    assert references_for("artefact", [("dataflow", "dsd")]) == "children"
    assert references_for("artefact", [("dataflow", "dsd", "dimensions", "concept")]) == "descendants"


def test_the_deepest_path_decides() -> None:
    """A query filtering on both a shallow and a deep path needs the deep one."""
    assert references_for("artefact", [(), ("dataflow", "dsd")]) == "children"
    assert (
        references_for("artefact", [("dataflow", "dsd"), ("dataflow", "dsd", "dimensions", "concept")]) == "descendants"
    )


def test_references_all_is_never_the_answer() -> None:
    """§11 measured 29.8 MB for one codelist under `references=all`.

    A planner that reaches for it has stopped choosing, so no input produces
    it — asserted over the paths this schema can express rather than by
    reading the implementation.
    """
    every_path = {
        *closure(Plan(root=scanned(q.dataflow))),
        *closure(Plan(root=scanned(q.dsd))),
        *closure(Plan(root=scanned(q.codelist))),
        *closure(Plan(root=scanned(q.concept_scheme))),
    } | {(), ("dataflow", "dsd"), ("dataflow", "dsd", "dimensions", "concept")}
    for path in every_path:
        assert references_for("artefact", [path]) != "all"


# ── the rule against the wire ─────────────────────────────────────────────


def _artefacts_from(fragment: str) -> list[object]:
    """Artefacts from a recorded response. No network; the cassette is a fixture."""
    import attrs
    import yaml

    from sdmxlib.formats.sdmx_ml21 import reader
    from sdmxlib.model.message import StructureMessage

    base = repo.ROOT / "tests" / "integration" / "cassettes"
    matches = [p for p in base.rglob("*.yaml") if fragment in p.read_text(errors="ignore")]
    body = yaml.safe_load(max(matches, key=lambda p: p.stat().st_size).read_text())
    message = reader.read(body["interactions"][0]["response"]["body"]["string"].encode())
    found: list[object] = []
    for field in (f.name for f in attrs.fields(StructureMessage) if not f.name.startswith("_")):
        collection = getattr(message, field, None)
        if collection is None:
            continue
        try:
            found.extend(list(collection))
        except TypeError:
            continue
    return found


def _resolvable_concept_ids(fragment: str) -> int:
    with sl.LocalRegistry(":memory:") as registry:
        for artefact in _artefacts_from(fragment):
            # A recorded catalogue contains artefacts this schema does not
            # store; skipping them is the point of using a real response.
            with contextlib.suppress(Exception):
                assert isinstance(artefact, SupportsUrn)
                registry.add(artefact)
        rows = registry.query(q.component).select(q.component.concept.id).collect()
    return len({str(v) for v in rows.to_series() if str(v) != "None"})


def test_the_depth_rule_matches_what_the_wire_returns() -> None:
    """Pin the rule to a measured response, not to a reading of the spec.

    The headline predicate crosses twice, so the rule says `descendants`. The
    recorded fetches show that is not conservatism: fetched one level short —
    the DSDs but not their concept schemes — the predicate's own column
    resolves for **nothing**, and the query would have returned an empty
    answer with no error. That silence is the whole reason this rule exists,
    so it is asserted here rather than described.
    """
    predicate: Pred[DataflowRow] = q.dataflow.dsd.dimensions.any(q.component.concept.id == "REF_AREA")
    plan = Plan(root=scanned(q.dataflow), predicates=(predicate,))
    assert references_for("artefact", closure(plan)) == "descendants"

    # Fetched at the depth a hop-count rule would have chosen: DSDs present,
    # concept schemes absent, and every concept id null.
    assert _resolvable_concept_ids("dataflow/ECB?detail=referencepartial&references=datastructure") == 0
    # Fetched at the depth this rule chooses.
    assert _resolvable_concept_ids("dataflow/ECB/EXR/latest?detail=referencepartial&references=descendants") > 0


def test_a_window_partition_reaches_across_an_edge_with_no_filter_saying_so() -> None:
    """`qualify` is the clause easiest to forget in a closure.

    `rank().over(q.dataflow.dsd.agency)` names no filter and no sort key, so a
    closure built from `predicates` and `order_keys` alone would fetch nothing
    for it and rank every row against a null partition — one answer, quietly
    wrong.
    """
    ranked = q.dataflow.version.rank(descending=True).over(q.dataflow.dsd.agency) == 1
    plan = Plan(root=scanned(q.dataflow), qualifiers=(ranked,))
    assert ("dataflow", "dsd") in closure(plan)
    assert references_for("artefact", closure(plan)) == "children"


# ── detail= (§318) ────────────────────────────────────────────────────────


def _plan(*predicates: Pred[CodelistRow]) -> Plan[CodelistRow]:
    return Plan(root=scanned(q.codelist), predicates=predicates)


def test_a_stub_answers_a_query_over_identity_and_labels() -> None:
    """The listing case the retired `browse()` served, and the reason #318
    exists: a query naming ids should not download every code."""
    plan = _plan(q.codelist.agency == "ECB")
    assert detail_for(reached_columns(plan), closure(plan)) == "allstubs"

    named = _plan(q.codelist.agency == "ECB", q.codelist.name["en"].str.contains("cur"))
    assert detail_for(reached_columns(named), closure(named)) == "allstubs"


def test_a_description_forces_full() -> None:
    """Measured, not assumed. Of 109 codelists present in both a stub listing
    and a full response, exactly one carried a description, and the stub did
    not have it — so `description` is outside the stub surface even though
    `name` is inside it."""
    plan = _plan(q.codelist.agency == "ECB", q.codelist.description["en"].str.contains("cur"))
    assert detail_for(reached_columns(plan), closure(plan)) == "full"


def test_reaching_an_item_forces_full() -> None:
    """A stub is the artefact without its items — that is what makes it a stub."""
    plan = _plan(q.codelist.agency == "ECB", q.codelist.codes.any(q.code.id == "A"))
    assert detail_for(reached_columns(plan), closure(plan)) == "full"


def test_an_unrecognised_column_forces_full() -> None:
    """The default is `full` for anything unproven.

    A wrong `allstubs` does not fail — the query evaluates against an artefact
    whose items were never fetched and answers nothing. So a column nobody has
    checked against a recorded response costs a bigger fetch rather than a
    silent empty result.
    """
    assert detail_for([("artefact", "is_final")], [()]) == "full"
    assert detail_for([("artefact", "valid_from")], [()]) == "full"


def test_reached_columns_sees_through_composites() -> None:
    plan = _plan((q.codelist.agency == "ECB") | (q.codelist.description["en"] == "x"))
    assert ("localized_text", "description_en") in reached_columns(plan)
