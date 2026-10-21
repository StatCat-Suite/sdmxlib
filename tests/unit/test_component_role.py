"""What part of a dataflow's shape a component plays (data-plane pushdown, step 1).

A REST data capability cannot be declared over column names, because an
observation's columns come from a DSD — `column="REF_AREA"` is a fact about one
dataflow, not about a backend. It is declared over *roles* instead, and these
are the tests that the role a column reports is the one the DSD says.

The case worth reading first is `test_no_dsd_means_no_role`. A binding alone can
say a column is *a dimension*; only a DSD can say whether it is in the key,
where, or whether it is time. Guessing builds a positional SDMX key with a wrong
slot, which returns a different slice with no error — one of the three failures
`docs/design/data-plane-pushdown.md` §6 names as falsifying the design. So the
absence of a DSD has to fail closed, and that is asserted rather than assumed.
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
from sdmxlib.query.observations import Observations

pytestmark = pytest.mark.unit


def _concept(id_: str) -> Ref[Concept]:
    return Ref[Concept].unresolved(Concept.urn_for(agency="AG", scheme="CS", id=id_, version="1.0"))


def _dsd() -> DataStructure:
    """A DSD with one of each role, so no assertion passes by there being one kind."""
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


def _binding(dimension_column_map: dict[str, str] | None = None) -> DataflowBinding:
    """A binding that *renames* every column, so nothing can pass by id == column."""
    return DataflowBinding(
        dataflow=Ref[Dataflow].unresolved(Dataflow.urn_for(agency="AG", id="DF", version="1.0")),
        database_role="memory",
        table_location="data.obs",
        dimension_column_map=(
            {"FREQ": "f", "REF_AREA": "geo", "TIME_PERIOD": "t"}
            if dimension_column_map is None
            else dimension_column_map
        ),
        measure_column_map={"OBS_VALUE": "v"},
        attribute_column_map={"OBS_STATUS": "st"},
        structural_version="AG:DSD(1.0)",
        source_registry="local",
        materialized_at=datetime(2026, 1, 1, tzinfo=UTC),
        bound_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


# ── the DSD's own answer ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("component_id", "expected"),
    [
        ("FREQ", ComponentRole.KEY_DIMENSION),
        ("REF_AREA", ComponentRole.KEY_DIMENSION),
        ("TIME_PERIOD", ComponentRole.TIME_DIMENSION),
        ("OBS_VALUE", ComponentRole.MEASURE),
        ("OBS_STATUS", ComponentRole.ATTRIBUTE),
        ("NOPE", None),
    ],
)
def test_the_dsd_names_each_component_s_role(component_id: str, expected: ComponentRole | None) -> None:
    assert _dsd().role_of(component_id) == expected


def test_time_is_not_reported_as_an_ordinary_key_dimension() -> None:
    """The split the enum exists for, and it holds on both API versions.

    Whether time occupies a key slot is a *version* question (#337) — it does
    on 3.0 and does not on 2.1. Its **role** is neither answer: it is the one
    component `startPeriod`/`endPeriod` reaches, and a capability that called
    it a key dimension would be claiming a backend can serve a range on
    `REF_AREA` too.
    """
    dsd = _dsd()
    assert "TIME_PERIOD" in dsd.key_slot_ids("3.0"), "a key slot there…"
    assert "TIME_PERIOD" not in dsd.key_slot_ids("2.1"), "…and not there…"
    assert dsd.role_of("TIME_PERIOD") is ComponentRole.TIME_DIMENSION, "…and its role is neither"


def test_every_role_is_reachable() -> None:
    """A role no component can ever report is a role a capability cannot use."""
    dsd = _dsd()
    reported = {dsd.role_of(c) for c in ("FREQ", "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS")}
    assert reported == set(ComponentRole)


# ── the root's answer, which goes through the binding ─────────────────────


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        ("f", ComponentRole.KEY_DIMENSION),
        ("geo", ComponentRole.KEY_DIMENSION),
        ("t", ComponentRole.TIME_DIMENSION),
        ("v", ComponentRole.MEASURE),
        ("st", ComponentRole.ATTRIBUTE),
    ],
)
def test_a_physical_column_reports_its_component_s_role(column: str, expected: ComponentRole) -> None:
    """Keyed by *column*, because that is what a compiled predicate carries.

    Every column here is renamed away from its component id, so a lookup that
    skipped the binding would return `None` for all five.
    """
    assert Observations(_binding(), _dsd()).role_of(column) is expected


def test_a_column_the_binding_does_not_map_has_no_role() -> None:
    assert Observations(_binding(), _dsd()).role_of("not_a_column") is None


def test_a_component_id_is_not_a_column() -> None:
    """The inverse of the test above, and the likelier mistake.

    `REF_AREA` is a perfectly good component id and is not a column of this
    binding. Accepting it would mean the role lookup silently worked for
    identity-mapped bindings and failed for renamed ones.
    """
    assert Observations(_binding(), _dsd()).role_of("REF_AREA") is None


def test_no_dsd_means_no_role() -> None:
    """Fail closed, and the reason is worth more than the assertion.

    A binding knows `geo` is *a dimension* — it is in `dimension_column_map`.
    It cannot know whether that dimension is in the key, at which position, or
    whether it is time. Reporting `KEY_DIMENSION` on the binding alone would let
    a caller build a positional key with a wrong slot, which returns a
    *different slice of data* and raises nothing.

    So a root without a DSD reports no role, nothing pushes, and every predicate
    is evaluated locally — slower, and right.
    """
    root = Observations(_binding())
    assert root.dsd is None
    assert all(root.role_of(c) is None for c in ("f", "geo", "t", "v", "st"))


def test_a_binding_mapping_a_component_the_dsd_lacks_has_no_role() -> None:
    """Binding drift, arriving here rather than at the database.

    The binding says `x` is the column for `GHOST`; the DSD has never heard of
    `GHOST`. That is exactly what `structural_version` and
    `check_binding_drift` exist to detect, and until someone runs that check
    this is where it surfaces — as unpushable, not as a guess.
    """
    binding = _binding(dimension_column_map={"FREQ": "f", "GHOST": "x"})
    assert Observations(binding, _dsd()).role_of("x") is None


def test_the_first_mapping_wins_when_two_ids_share_a_column() -> None:
    """A malformed binding, pinned so the behaviour is not accidental.

    Two components cannot share a physical column in any meaningful sense. The
    inverse map takes the first and does not raise, because refusing to build a
    root is a worse failure than answering one lookup oddly — and
    `check_binding_drift` is where a caller should learn about it.
    """
    binding = _binding(dimension_column_map={"FREQ": "same", "REF_AREA": "same", "TIME_PERIOD": "t"})
    assert Observations(binding, _dsd()).role_of("same") is ComponentRole.KEY_DIMENSION
