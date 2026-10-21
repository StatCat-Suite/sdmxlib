"""Lowering an observation query onto a `/data` URL (data-plane pushdown, step 3).

Each predicate either becomes part of the request or stays behind as a
residual, evaluated locally after the fetch. There is no third outcome and
nothing is dropped — which is the property most of this file asserts, because a
predicate that vanished would return *more* rows with no error.

The two API versions produce different URLs from the same query, and that is
#337 arriving in the data plane: the key has one fewer slot on 2.1, and `c[…]`
does not exist there at all.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.concept import Concept
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import (
    AttachmentLevel,
    Attribute,
    DataStructure,
    Dimension,
    Measure,
    TimeDimension,
)
from sdmxlib.model.ref import Ref
from sdmxlib.query._rest import data_request_for
from sdmxlib.query.observations import Observations

pytestmark = pytest.mark.unit


def _concept(id_: str) -> Ref[Concept]:
    return Ref[Concept].unresolved(Concept.urn_for(agency="ECB", scheme="CS", id=id_, version="1.0"))


@pytest.fixture(scope="module")
def dsd() -> DataStructure:
    return DataStructure(
        id="EXR",
        maintainer="ECB",
        version="1.0",
        name={"en": "exchange rates"},
        dimensions=[
            Dimension(id="FREQ", concept=_concept("FREQ")),
            Dimension(id="CURRENCY", concept=_concept("CURRENCY")),
        ],
        time_dimension=TimeDimension(id="TIME_PERIOD", concept=_concept("TIME_PERIOD")),
        measures=[Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"))],
        attributes=[Attribute(attachment=AttachmentLevel.DATASET, id="OBS_STATUS", concept=_concept("OBS_STATUS"))],
    )


@pytest.fixture(scope="module")
def binding() -> DataflowBinding:
    """Columns renamed, so a lowering that skipped the binding finds nothing."""
    return DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(Dataflow.urn_for(agency="ECB", id="EXR", version="1.0")),
        database_role="memory",
        table_location="data.obs",
        dimension_column_map={"FREQ": "f", "CURRENCY": "cur", "TIME_PERIOD": "t"},
        measure_column_map={"OBS_VALUE": "v"},
        attribute_column_map={"OBS_STATUS": "st"},
        structural_version="ECB:EXR(1.0)",
        source_registry="local",
        materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
        bound_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture(scope="module")
def obs(binding: DataflowBinding, dsd: DataStructure) -> Observations:
    return Observations(binding, dsd)


def _lower(preds, binding, dsd, api):
    return data_request_for(preds, binding=binding, dsd=dsd, api=api)


# ── the key ───────────────────────────────────────────────────────────────


def test_an_equality_takes_its_slot(obs, binding, dsd) -> None:
    request, residual = _lower([obs["FREQ"] == "M"], binding, dsd, "2.1")
    assert request.key == "M."
    assert not residual


def test_membership_is_plus_joined_and_sorted(obs, binding, dsd) -> None:
    """Sorted for a stable URL — the same question must not produce two cache
    keys depending on the order a caller happened to write."""
    request, _ = _lower([obs["CURRENCY"].is_in(["USD", "GBP"])], binding, dsd, "2.1")
    assert request.key == ".GBP+USD"


def test_an_unconstrained_query_asks_for_all_rather_than_dots(obs, binding, dsd) -> None:
    request, residual = _lower([], binding, dsd, "2.1")
    assert request.key == "all"
    assert not residual


def test_the_key_is_one_slot_shorter_on_2_1(obs, binding, dsd) -> None:
    """#337, arriving in the data plane.

    The same query, two URLs. On 3.0 the time dimension has a slot — empty
    here, because a *range* cannot go in a slot and belongs in the periods.
    """
    predicates = [obs["FREQ"] == "M", obs["CURRENCY"] == "USD"]
    assert _lower(predicates, binding, dsd, "2.1")[0].key == "M.USD"
    assert _lower(predicates, binding, dsd, "3.0")[0].key == "M.USD."


# ── time ──────────────────────────────────────────────────────────────────


def test_an_inclusive_range_becomes_the_two_periods(obs, binding, dsd) -> None:
    request, residual = _lower([obs["TIME_PERIOD"] >= "2019", obs["TIME_PERIOD"] <= "2024"], binding, dsd, "2.1")
    assert request.params == {"startPeriod": "2019", "endPeriod": "2024"}
    assert not residual


def test_time_equality_sets_both_bounds(obs, binding, dsd) -> None:
    """`startPeriod` and `endPeriod` are inclusive, so one period is [v, v]."""
    request, residual = _lower([obs["TIME_PERIOD"] == "2020"], binding, dsd, "2.1")
    assert request.params == {"startPeriod": "2020", "endPeriod": "2020"}
    assert not residual


def test_a_strict_bound_stays_residual(obs, binding, dsd) -> None:
    """There is no exclusive period parameter, so `>` cannot be expressed —
    and expressing it approximately would return a period too many."""
    request, residual = _lower([obs["TIME_PERIOD"] > "2019"], binding, dsd, "2.1")
    assert request.params == {}
    assert len(residual) == 1


@pytest.mark.parametrize("api", ["2.1", "3.0"])
def test_an_ordering_comparison_never_reaches_a_key_slot(obs, binding, dsd, api) -> None:
    """A slot holds values, not a comparison.

    Two guards refuse it, and they cover *different* operators — which is why
    both are here. `>` never leaves `_lowerable_values`, because the URL has no
    exclusive form of anything. `>=` does leave it, since the period parameters
    are inclusive, and is then refused **at the slot**: a range is meaningful
    on time and meaningless on a code.

    Without the second guard, `CURRENCY >= "USD"` would put `USD` in the
    currency slot and drop the comparison — silently querying for exactly USD.
    """
    for predicate in (obs["CURRENCY"] > "USD", obs["CURRENCY"] >= "USD"):
        request, residual = _lower([predicate], binding, dsd, api)
        assert request.key == "all"
        assert len(residual) == 1


def test_time_membership_pushes_only_where_time_has_a_slot(obs, binding, dsd) -> None:
    predicate = [obs["TIME_PERIOD"].is_in(["2019", "2021"])]
    assert _lower(predicate, binding, dsd, "3.0")[0].key == "..2019+2021"
    request, residual = _lower(predicate, binding, dsd, "2.1")
    assert request.key == "all", "nothing to put it in"
    assert len(residual) == 1, "so it is evaluated locally"


# ── components outside the key ────────────────────────────────────────────


def test_an_attribute_becomes_a_c_parameter_on_3_0_only(obs, binding, dsd) -> None:
    predicate = [obs["OBS_STATUS"] == "A"]
    request, residual = _lower(predicate, binding, dsd, "3.0")
    assert request.params == {"c[OBS_STATUS]": "A"}
    assert not residual

    request, residual = _lower(predicate, binding, dsd, "2.1")
    assert request.params == {}, "2.1 has no such parameter"
    assert len(residual) == 1


def test_a_measure_predicate_is_never_pushed(obs, binding, dsd) -> None:
    for api in ("2.1", "3.0"):
        request, residual = _lower([obs["OBS_VALUE"] > 100], binding, dsd, api)
        assert request.params == {}
        assert request.key == "all"
        assert len(residual) == 1


# ── nothing is dropped ────────────────────────────────────────────────────


def test_every_predicate_is_either_pushed_or_returned(obs, binding, dsd) -> None:
    """The invariant that matters most, asserted per predicate.

    A predicate that was neither pushed nor returned would silently widen the
    result — more rows, no error, no way to notice. Lowering each one alone
    makes "pushed" observable: the request changes, or the residual holds it.
    """
    empty, _ = _lower([], binding, dsd, "2.1")
    for api in ("2.1", "3.0"):
        for predicate in (
            obs["FREQ"] == "M",
            obs["CURRENCY"].is_in(["USD"]),
            obs["TIME_PERIOD"] >= "2019",
            obs["OBS_STATUS"] == "A",
            obs["OBS_VALUE"] > 100,
            obs["TIME_PERIOD"] > "2000",
        ):
            request, residual = _lower([predicate], binding, dsd, api)
            changed = (request.key, request.params) != (empty.key, empty.params)
            assert changed != bool(residual), f"{api}: predicate was both pushed and residual, or neither"


def test_residuals_are_the_originals_untouched(obs, binding, dsd) -> None:
    """Identity, not equality — comparing two predicates raises by design, and
    a residual that had been rebuilt would be a different question."""
    predicates = [obs["OBS_VALUE"] > 100, obs["FREQ"] == "M", obs["TIME_PERIOD"] > "2000"]
    _, residual = _lower(predicates, binding, dsd, "2.1")
    assert len(residual) == 2
    assert all(any(r is p for p in predicates) for r in residual)


def test_a_second_predicate_on_one_slot_stays_residual(obs, binding, dsd) -> None:
    """Pushing a superset and filtering locally is always safe; pushing an
    intersection would depend on both predicates meaning what we think.

    So the first wins the slot and the second is evaluated locally — the server
    returns `A+M` and the local filter narrows it to `M`.
    """
    request, residual = _lower([obs["FREQ"].is_in(["A", "M"]), obs["FREQ"] == "M"], binding, dsd, "2.1")
    assert request.key == "A+M."
    assert len(residual) == 1


def test_a_column_the_binding_does_not_map_stays_residual(obs, binding, dsd) -> None:
    """No component, no role, no push — the same fail-closed path `role_of`
    takes, arriving one layer up."""
    from sdmxlib.query._expr import Untyped

    ghost = Untyped((), "data.obs", "not_a_column", "GHOST")
    request, residual = _lower([ghost == "x"], binding, dsd, "2.1")
    assert request.key == "all"
    assert len(residual) == 1


# ── the URL ───────────────────────────────────────────────────────────────


def test_the_two_path_shapes(obs, binding, dsd) -> None:
    assert _lower([], binding, dsd, "2.1")[0].path == "data/ECB,EXR,1.0/all"
    assert _lower([], binding, dsd, "3.0")[0].path == "data/dataflow/ECB/EXR/1.0/all"


def test_latest_becomes_a_tilde_on_3_0_only(dsd) -> None:
    """The 3.0 wildcard for a version, which 2.1 spells literally."""
    binding = DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(Dataflow.urn_for(agency="ECB", id="EXR")),
        database_role="memory",
        table_location="data.obs",
        dimension_column_map={"FREQ": "f"},
        measure_column_map={},
        attribute_column_map={},
        structural_version="ECB:EXR(1.0)",
        source_registry="local",
        materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
        bound_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert _lower([], binding, dsd, "3.0")[0].path.endswith("/EXR/~/all")
    assert "latest" in _lower([], binding, dsd, "2.1")[0].path
