"""Differential properties for `q.component`, against the in-memory DSD (#280).

The oracle here is not a hand-written evaluator like `tests/support/oracle.py`
— it is **`DataStructure` itself**. A resolved DSD already answers every
question this relation answers: `dsd.dimensions`, `dsd.attributes`,
`dsd.measures` and each component's `representation`. So the property is that
persisting a DSD and querying it back agrees with the object it was persisted
from.

That is the same choice #276 made with `get_all`, and it is a good oracle for
the same reason: it is a *shipped* code path with its own users, so it cannot
quietly drift to match the thing it checks. What it costs is that both sides
share the writer — a field the writer drops is absent from both, and the
property passes. That is not hypothetical: `metadata_attribute` lacked an
`is_multilingual` column and its writer never read the field, and every
round-trip test in the suite passed for years because no fixture set it. The
mitigation is `tests/unit/test_storage_schema.py`, which compares the three
representation-bearing tables against *each other* rather than against a
round-trip — drift between copies is what a round-trip cannot see.

**A note on what these strategies do not reach.** `data_structures()` draws
component facets with `for_component=True`, which omits `is_multilingual`
because SDMX-ML 2.1 forbids it on a component's `TextFormat`. So no property
below discriminates on that field, and one that filtered by it would pass
vacuously. It is covered instead by
`test_storage_facet_relationship.py::test_a_full_facet_round_trips_from_all_three_owners`,
which sets every field on all three owners.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from sdmxlib.query.accessors import q
from tests.support.narrow import present
from tests.support.strategies import data_structures

#: The strategies draw every facet combination the formats carry, the
#: IM-discouraged ones too, which warn as they are built (#481); the query
#: properties are about what the store holds, not the warning.
pytestmark = [pytest.mark.unit, pytest.mark.filterwarnings("ignore::sdmxlib.ImRuleWarning")]

_EXAMPLES = max(5, present(settings.default).max_examples // 5)

#: `component_type` as the writer spells it, per role. Read off the writer's
#: own list rather than guessed: it also emits `TimeDimension` and
#: `MeasureDimension`, which `data_structures()` does not draw, so a property
#: keying on "Dimension" alone would silently exclude them if it did.
_ROLES = {"dimensions": "Dimension", "attributes": "Attribute", "measures": "Measure"}


def _persisted(reg: LocalRegistry, dsd: DataStructure):
    reg.add(dsd)
    return DataStructure.urn_for(agency=dsd.agency_id, id=dsd.id, version=dsd.version)


def _in_memory(dsd: DataStructure) -> dict[str, tuple[str, object]]:
    """`{component_id: (role, representation)}` straight off the object."""
    out: dict[str, tuple[str, object]] = {}
    for attr, role in _ROLES.items():
        for comp in getattr(dsd, attr):
            out[comp.id] = (role, comp.representation)
    return out


# ── The relation agrees with the object ──────────────────────────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(dsd=data_structures())
def test_the_component_set_matches_the_dsd(dsd: DataStructure):
    """Every component, and only those — the claim `dsd.components` makes."""
    with LocalRegistry(":memory:") as reg:
        urn = _persisted(reg, dsd)
        got = reg.query(q.component).filter(q.component.dsd.urn == str(urn)).ids()

    assert sorted(got) == sorted(_in_memory(dsd))


@settings(max_examples=_EXAMPLES, deadline=None)
@given(dsd=data_structures(), data=st.data())
def test_role_partitions_the_components(dsd: DataStructure, data: st.DataObject):
    """`role` selects exactly the list it names.

    Asserted as a *partition* rather than three separate filters: the three
    roles must also be disjoint and cover, which is what would break if the
    writer ever emitted a component under two rows or none.
    """
    attr = data.draw(st.sampled_from(sorted(_ROLES)), label="role")
    expected = sorted(c.id for c in getattr(dsd, attr))

    with LocalRegistry(":memory:") as reg:
        urn = _persisted(reg, dsd)
        scope = reg.query(q.component).filter(q.component.dsd.urn == str(urn))
        got = sorted(scope.filter(q.component.role == _ROLES[attr]).ids())
        by_role = {r: sorted(scope.filter(q.component.role == r).ids()) for r in _ROLES.values()}

    assert got == expected
    covered = [cid for ids in by_role.values() for cid in ids]
    assert sorted(covered) == sorted(_in_memory(dsd)), "roles must cover every component"
    assert len(covered) == len(set(covered)), "and must not overlap"


# ── The two arms of Representation ───────────────────────────────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(dsd=data_structures())
def test_the_arms_are_disjoint_and_match_the_object(dsd: DataStructure):
    """`codelist` and `facet` are the two arms of one sum type.

    A component is enumerated *or* free-text, never both, and which one the
    query reports must match what the object holds. Both directions matter:
    reporting a facet for an enumerated component would make
    `facet.max_length.is_null()` mean something different from "not free
    text".
    """
    with LocalRegistry(":memory:") as reg:
        urn = _persisted(reg, dsd)
        scope = reg.query(q.component).filter(q.component.dsd.urn == str(urn))
        enumerated = set(scope.filter(q.component.codelist.urn.is_not_null()).ids())
        typed = set(scope.filter(q.component.facet.text_type.is_not_null()).ids())

    expected_enum = {cid for cid, (_, rep) in _in_memory(dsd).items() if isinstance(rep, Ref)}
    expected_typed = {
        cid for cid, (_, rep) in _in_memory(dsd).items() if isinstance(rep, Facet) and rep.type is not None
    }

    assert enumerated == expected_enum
    assert typed == expected_typed
    assert not (enumerated & typed), "a component cannot be both arms"


@settings(max_examples=_EXAMPLES, deadline=None)
@given(dsd=data_structures(), data=st.data())
def test_every_facet_bound_projects_what_the_object_holds(dsd: DataStructure, data: st.DataObject):
    """The eleven bounds, one property, drawn rather than enumerated.

    Written as a projection compared field-by-field rather than as a filter,
    because a filter over a mostly-NULL column passes trivially when the
    strategy happens to leave it unset — and eight of these are optional.
    """
    field = data.draw(
        st.sampled_from(["min_length", "max_length", "min_value", "max_value", "decimals", "pattern"]),
        label="field",
    )
    with LocalRegistry(":memory:") as reg:
        urn = _persisted(reg, dsd)
        frame = (
            reg.query(q.component)
            .filter(q.component.dsd.urn == str(urn))
            .select(q.component.id, value=getattr(q.component.facet, field))
            .collect()
        )

    got = {r["id"]: r["value"] for r in frame.to_dicts()}
    for cid, (_, rep) in _in_memory(dsd).items():
        want = getattr(rep, field) if isinstance(rep, Facet) else None
        assert got[cid] == pytest.approx(want) if isinstance(want, float) else got[cid] == want, (
            f"{cid}.{field}: query says {got[cid]!r}, object says {want!r}"
        )


@settings(max_examples=_EXAMPLES, deadline=None)
@given(dsd=data_structures())
def test_the_facet_is_a_zero_hop_so_it_never_multiplies_rows(dsd: DataStructure):
    """The claim that makes `facet` a namespace rather than an edge.

    Its columns live on the component's own row, so projecting them cannot
    change the row count and cannot emit a join. Both halves are asserted —
    the count, because that is what a caller sees, and the absence of `JOIN`,
    because a count that happens to match would hide a join that multiplies
    only on other data.
    """
    with LocalRegistry(":memory:") as reg:
        urn = _persisted(reg, dsd)
        scope = reg.query(q.component).filter(q.component.dsd.urn == str(urn))
        plain = len(scope.ids())
        frame = scope.select(q.component.id, m=q.component.facet.max_length, p=q.component.facet.pattern)
        sql = frame.to_sql().sql
        widened = len(frame.collect())

    assert widened == plain == len(_in_memory(dsd))
    assert "JOIN" not in sql, "a facet reach must not join"


# ── Ordering ─────────────────────────────────────────────────────────────────


@settings(max_examples=_EXAMPLES, deadline=None)
@given(dsd=data_structures())
def test_position_reproduces_declaration_order(dsd: DataStructure):
    """Dimensions, then attributes, then measures — the writer's order.

    Worth pinning because a DSD's dimension order is semantic: it is the order
    of the observation key, so a component relation that lost it would make
    `position` a number with no meaning.
    """
    with LocalRegistry(":memory:") as reg:
        urn = _persisted(reg, dsd)
        got = reg.query(q.component).filter(q.component.dsd.urn == str(urn)).order_by(q.component.position).ids()

    expected = [c.id for c in dsd.dimensions] + [c.id for c in dsd.attributes] + [c.id for c in dsd.measures]
    assert got == expected
