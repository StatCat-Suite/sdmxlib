"""What SDMX REST can evaluate over observations (data-plane pushdown, step 2).

The structural capability is declared over column names — every maintainable
has `agency_id`, so `PredicateShape(column="agency_id")` means something. An
observation's columns come from a DSD, so `column="REF_AREA"` is a fact about
one dataflow rather than about a backend. `shapes_for` rewrites a predicate's
columns into **roles** first, and the data capabilities are declared over those.

Two things here are deliberately *under*-declared, and both are asserted rather
than left to a comment: strict `>` / `<` on time, because `startPeriod` and
`endPeriod` are inclusive bounds and pushing a strict comparison would return
one period too many; and any filter on a measure, because that is a filter on a
value and provider support is not something this declaration can vouch for.

The asymmetry worth noticing: `/data` pushes **more** than `/structure` does.
The structural surface has no predicate slot beyond identity, while the data
surface has a positional key and period parameters. The two planes are usually
assumed to run the other way round.
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
    ComponentRole,
    DataStructure,
    Dimension,
    Measure,
    TimeDimension,
)
from sdmxlib.model.ref import Ref
from sdmxlib.query._expr import Pred
from sdmxlib.query._pushdown import (
    ROLE_SIGIL,
    SDMX_REST,
    SDMX_REST_DATA_21,
    SDMX_REST_DATA_30,
    Capability,
    shapes_for,
    shapes_of,
)
from sdmxlib.query.accessors import q
from sdmxlib.query.observations import Observations, ObsRow

pytestmark = pytest.mark.unit


def _concept(id_: str) -> Ref[Concept]:
    return Ref[Concept].unresolved(Concept.urn_for(agency="AG", scheme="CS", id=id_, version="1.0"))


@pytest.fixture(scope="module")
def dsd() -> DataStructure:
    return DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name={"en": "structure"},
        dimensions=[
            Dimension(id="FREQ", concept=_concept("FREQ")),
            Dimension(id="REF_AREA", concept=_concept("REF_AREA")),
        ],
        time_dimension=TimeDimension(id="TIME_PERIOD", concept=_concept("TIME_PERIOD")),
        measures=[Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"))],
        attributes=[Attribute(attachment=AttachmentLevel.DATASET, id="OBS_STATUS", concept=_concept("OBS_STATUS"))],
    )


@pytest.fixture(scope="module")
def binding() -> DataflowBinding:
    """Every column renamed, so nothing can pass by `id == column`."""
    return DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(Dataflow.urn_for(agency="AG", id="DF", version="1.0")),
        database_role="memory",
        table_location="data.obs",
        dimension_column_map={"FREQ": "f", "REF_AREA": "geo", "TIME_PERIOD": "t"},
        measure_column_map={"OBS_VALUE": "v"},
        attribute_column_map={"OBS_STATUS": "st"},
        structural_version="AG:DSD(1.0)",
        source_registry="local",
        materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
        bound_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture(scope="module")
def obs(binding: DataflowBinding, dsd: DataStructure) -> Observations:
    return Observations(binding, dsd)


# ── the verdicts ──────────────────────────────────────────────────────────


def _verdicts(obs: Observations, predicate: Pred[ObsRow]) -> tuple[bool, bool]:
    shapes = shapes_for(predicate, obs)
    return SDMX_REST_DATA_21.accepts((), shapes), SDMX_REST_DATA_30.accepts((), shapes)


def test_a_key_dimension_equality_pushes_on_both(obs: Observations) -> None:
    assert _verdicts(obs, obs["REF_AREA"] == "ON") == (True, True)


def test_a_key_dimension_membership_pushes_on_both(obs: Observations) -> None:
    """`is_in` is what the `+` in a key segment means — `CA+US`."""
    assert _verdicts(obs, obs["FREQ"].is_in(["A", "M"])) == (True, True)


def test_time_membership_separates_the_versions_too(obs: Observations) -> None:
    """The version difference nobody expects (#337).

    SDMX-REST 1.5 puts the time dimension *outside* the dotted key — sdmx1
    skips it when serialising a key for the same reason — so there is no
    `+`-joined segment to hold values, and `startPeriod`/`endPeriod` is one
    **contiguous** range. Pushing `is_in(["2019", "2021"])` there would
    silently acquire 2020.

    2.2.2 made time a key dimension, so it has a segment and the segment takes
    `+`-joined values.
    """
    assert _verdicts(obs, obs["TIME_PERIOD"].is_in(["2019", "2021"])) == (False, True)


def test_time_equality_pushes_on_both_by_different_means(obs: Observations) -> None:
    """A key segment on 2.2.2; `startPeriod == endPeriod` on 1.5.

    Same shape, two lowerings — which is exactly what a capability is for: it
    says *whether*, and leaves *how* to the request builder.
    """
    assert _verdicts(obs, obs["TIME_PERIOD"] == "2020") == (True, True)


@pytest.mark.parametrize("bound", [">=", "<="])
def test_an_inclusive_time_bound_pushes_on_both(obs: Observations, bound: str) -> None:
    """`startPeriod` / `endPeriod`, the one range the wire can express."""
    predicate = obs["TIME_PERIOD"] >= "2019" if bound == ">=" else obs["TIME_PERIOD"] <= "2024"
    assert _verdicts(obs, predicate) == (True, True)


@pytest.mark.parametrize("bound", [">", "<"])
def test_a_strict_time_bound_does_not_push(obs: Observations, bound: str) -> None:
    """Under-declared on purpose.

    `startPeriod` and `endPeriod` are *inclusive*. There is no exclusive form,
    so pushing `> "2019"` as `startPeriod=2019` would return 2019 as well —
    a wrong answer rather than a slow one. Under-declaring costs a local
    filter; over-declaring costs correctness.
    """
    predicate = obs["TIME_PERIOD"] > "2019" if bound == ">" else obs["TIME_PERIOD"] < "2024"
    assert _verdicts(obs, predicate) == (False, False)


def test_an_attribute_filter_separates_the_two_api_versions(obs: Observations) -> None:
    """The whole reason there are two capabilities rather than one.

    3.0's `c[<id>]=<values>` reaches components outside the key;
    2.1 has no equivalent, so the same predicate is local work there.
    """
    assert _verdicts(obs, obs["OBS_STATUS"] == "A") == (False, True)


def test_a_measure_filter_pushes_on_neither(obs: Observations) -> None:
    """Also under-declared, and for a different reason than the strict bounds.

    `c[…]` is defined over any component, so a measure filter is *expressible*.
    What is not established is that providers implement a filter on an
    observation's value, and a declaration is a promise about the answer.
    """
    assert _verdicts(obs, obs["OBS_VALUE"] > 100) == (False, False)


# ── the normalisation ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("component", "role"),
    [
        ("FREQ", ComponentRole.KEY_DIMENSION),
        ("TIME_PERIOD", ComponentRole.TIME_DIMENSION),
        ("OBS_VALUE", ComponentRole.MEASURE),
        ("OBS_STATUS", ComponentRole.ATTRIBUTE),
    ],
)
def test_a_physical_column_is_rewritten_to_its_role(obs: Observations, component: str, role: ComponentRole) -> None:
    shapes = shapes_for(obs[component] == "x", obs)
    assert [s.column for s in shapes] == [f"{ROLE_SIGIL}{role.value}"]


def test_a_structural_root_is_left_alone() -> None:
    """`agency_id` means the same on every maintainable, so it is declared by
    name and must survive `shapes_for` untouched."""
    predicate = q.dataflow.agency == "ESTAT"
    assert shapes_for(predicate, q.dataflow) == shapes_of(predicate)
    assert SDMX_REST.accepts((), shapes_for(predicate, q.dataflow))


def test_without_a_dsd_the_column_keeps_its_physical_name_and_pushes_nothing(
    binding: DataflowBinding,
) -> None:
    """The fail-closed path, end to end.

    A root with no DSD reports no roles, so the shape keeps `geo` — which no
    data capability declares, because a capability cannot enumerate one
    dataflow's column names. The predicate becomes a residual and is evaluated
    locally. Slower, and right.
    """
    blind = Observations(binding)
    shapes = shapes_for(blind["REF_AREA"] == "ON", blind)
    assert [s.column for s in shapes] == ["geo"], "not rewritten"
    assert not SDMX_REST_DATA_30.accepts((), shapes)


def test_the_sigil_stops_an_unrolled_column_named_after_a_role(dsd: DataStructure) -> None:
    """The collision the sigil exists for, which needs an *unrolled* column.

    A column that has a role is rewritten to it, so its name never matters. The
    hazard is a column that keeps its physical name — no DSD, or a component
    the DSD does not define — and happens to be called `key_dimension`. Without
    the sigil that string is exactly what the capability declares, so a
    predicate on an unknown column would be pushed as though it were a key.

    Asserted through `accepts` rather than by comparing against `ROLE_SIGIL`,
    because an assertion written in terms of the constant passes for any value
    of it — including the empty string, which is the mutation this is here to
    catch.
    """
    binding = DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(Dataflow.urn_for(agency="AG", id="DF", version="1.0")),
        database_role="memory",
        table_location="data.obs",
        # `GHOST` is not in the DSD, so `role_of` returns None and the column
        # keeps the name below.
        dimension_column_map={"GHOST": "key_dimension"},
        measure_column_map={},
        attribute_column_map={},
        structural_version="AG:DSD(1.0)",
        source_registry="local",
        materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
        bound_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    root = Observations(binding, dsd)
    shapes = shapes_for(root["GHOST"] == "x", root)

    assert [s.column for s in shapes] == ["key_dimension"], "unrolled, so the raw name survives"
    assert not SDMX_REST_DATA_21.accepts((), shapes), "and a raw name must never satisfy a role"
    assert not SDMX_REST_DATA_30.accepts((), shapes)


# ── the declarations themselves ───────────────────────────────────────────


def test_30_adds_exactly_what_2_2_2_changed() -> None:
    """Two changes, and the pair should differ by those and nothing else:
    `c[…]` reaching components outside the key, and the time dimension
    becoming a key dimension (#337)."""
    added = SDMX_REST_DATA_30.pushes[()] - SDMX_REST_DATA_21.pushes[()]
    assert {(s.kind, s.column) for s in added} == {
        ("Compare", f"{ROLE_SIGIL}attribute"),
        ("InSet", f"{ROLE_SIGIL}attribute"),
        ("InSet", f"{ROLE_SIGIL}time_dimension"),
    }
    assert not SDMX_REST_DATA_21.pushes[()] - SDMX_REST_DATA_30.pushes[()], "3.0 loses nothing"


@pytest.mark.parametrize("capability", [SDMX_REST_DATA_21, SDMX_REST_DATA_30])
def test_neither_claims_order_or_limit(capability: Capability) -> None:
    """There is no `ORDER BY` and no `LIMIT` in a data URL.

    `firstNObservations` looks like one and is not — it slices *per series*,
    so pushing a `limit()` onto it would return a different set of rows.
    """
    assert not capability.orders
    assert not capability.slices


@pytest.mark.parametrize("capability", [SDMX_REST_DATA_21, SDMX_REST_DATA_30])
def test_every_declared_shape_is_role_keyed(capability: Capability) -> None:
    """A column name in a data declaration is a fact about one dataflow that
    has been mistaken for a fact about the backend."""
    for shapes in capability.pushes.values():
        for shape in shapes:
            assert shape.column.startswith(ROLE_SIGIL), f"{shape.column} is not a role"
