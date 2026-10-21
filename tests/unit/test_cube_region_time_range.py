"""A cube region's time ranges and component value sets (#433).

OECD's constraints carry `KeyValue/TimeRange` (1,453) and `CubeRegion/Attribute`
(758). The model had a field for neither, so a key with a time range came back
with no values: SDMX-ML wrote it as an empty `<KeyValue>` every version
rejects, `allows()` read the empty set as "nothing allowed", and the store then
dropped the key altogether.
"""

import attrs
import pytest

from sdmxlib.formats import parse, serialize
from sdmxlib.local import LocalRegistry
from sdmxlib.model.constraint import ComponentValueSet, CubeRegion, DataConstraint, KeyValue, TimeBound, TimeRange
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import ValidationError
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema
from tests.support.roundtrip import structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_BETWEEN = TimeRange(start=TimeBound("2000-01-01"), end=TimeBound("2010-12-31", inclusive=False))


def _constraint(*keys: KeyValue, components: tuple[ComponentValueSet, ...] = ()) -> DataConstraint:
    return DataConstraint(
        id="CC",
        maintainer="AG",
        name={"en": "c"},
        regions=[CubeRegion(keys=keys, components=components)],
    )


# ── allows() ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("period", "allowed"),
    [
        ("1999-12-31", False),
        ("2000-01-01", True),  # inclusive start
        ("2005-06-15", True),
        ("2010-12-31", False),  # exclusive end
        ("2011-01-01", False),
    ],
)
def test_allows_compares_iso_periods_with_their_inclusivity(period: str, allowed: bool) -> None:
    region = CubeRegion(keys=(KeyValue(dimension_id="TIME_PERIOD", time_range=_BETWEEN),))
    assert region.allows({"TIME_PERIOD": period}) is allowed


def test_allows_does_not_exclude_a_period_it_cannot_read() -> None:
    """`2020-Q1` is a valid observational period with no ISO reading; the
    range says nothing about it rather than forbidding it."""
    region = CubeRegion(keys=(KeyValue(dimension_id="TIME_PERIOD", time_range=_BETWEEN),))
    assert region.allows({"TIME_PERIOD": "2020-Q1"})


def test_a_time_range_no_longer_forbids_every_key() -> None:
    """The defect: a lost range left an empty value set, so every key in an
    include region read as forbidden."""
    region = CubeRegion(
        keys=(KeyValue(dimension_id="FREQ", values={"A"}), KeyValue(dimension_id="TIME_PERIOD", time_range=_BETWEEN))
    )
    assert region.allows({"FREQ": "A", "TIME_PERIOD": "2005-01-01"})
    assert not region.allows({"FREQ": "Q", "TIME_PERIOD": "2005-01-01"})


def test_an_exclude_region_inverts_a_time_range() -> None:
    region = CubeRegion(include=False, keys=(KeyValue(dimension_id="TIME_PERIOD", time_range=_BETWEEN),))
    assert not region.allows({"TIME_PERIOD": "2005-01-01"})
    assert region.allows({"TIME_PERIOD": "1990-01-01"})


# ── validation: what no format can write, and what only SDMX-ML cannot ──────


def test_a_selection_with_values_and_a_time_range_is_invalid_everywhere() -> None:
    with pytest.raises(ValidationError, match="values or a time range, not both"):
        _constraint(KeyValue(dimension_id="TIME_PERIOD", values={"2000"}, time_range=_BETWEEN))


def test_a_time_range_with_no_bound_is_refused_as_it_is_built() -> None:
    """No format can write one, so it cannot be built (#492)."""
    with pytest.raises(ValidationError, match=r"TimeRange — a time range needs a start, an end, or both"):
        TimeRange()


def test_dropping_a_time_range_s_last_bound_is_refused() -> None:
    """`attrs.evolve` builds anew, so it runs the same check."""
    with pytest.raises(ValidationError, match="needs a start, an end, or both"):
        attrs.evolve(TimeRange(start=TimeBound("2000")), start=None)


def test_an_empty_key_is_json_legal_and_refused_in_sdmx_ml() -> None:
    """SDMX-JSON's `CubeRegionKeyType` allows neither values nor a time range
    — the availability builders use it for "no data" — and SDMX-ML requires
    one, so this is a writer's question, not `validate()`'s."""
    message = StructureMessage(constraints=[_constraint(KeyValue(dimension_id="DIM"))])
    assert_valid_sdmx_json(serialize(message, version="json"), compile_json_schema("structure"))
    for version in ("2.1", "3.0", "3.1"):
        with pytest.raises(ValidationError, match="no values and no time range"):
            serialize(message, version=version)


# ── every format and the store carry it ──────────────────────────────────────

_FULL = _constraint(
    KeyValue(dimension_id="FREQ", values={"A"}),
    KeyValue(dimension_id="TIME_PERIOD", time_range=_BETWEEN),
    components=(
        ComponentValueSet(component_id="UNIT_MULT", values={"0", "6"}),
        ComponentValueSet(component_id="OBS_STATUS", time_range=TimeRange(start=TimeBound("2001"))),
    ),
)


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1", "json"])
def test_each_format_writes_it_valid_and_reads_it_back(version: str) -> None:
    written = serialize(StructureMessage(constraints=[_FULL]), version=version)
    if version == "json":
        assert_valid_sdmx_json(written, compile_json_schema("structure"))
    else:
        assert_valid_sdmx_ml(written, structure_schema(version))
    back = parse(written)
    assert isinstance(back, StructureMessage)
    assert back.constraints[0].regions == _FULL.regions


def test_the_store_keeps_time_ranges_components_empty_keys_and_order() -> None:
    """`region_key_value` holds a row per *value*, so a key with a time range,
    or with no values, had no row and vanished; and nothing kept the order."""
    constraint = _constraint(
        KeyValue(dimension_id="Z_LAST_BY_NAME", values={"x"}),
        KeyValue(dimension_id="TIME_PERIOD", time_range=_BETWEEN),
        KeyValue(dimension_id="EMPTY"),
        components=_FULL.regions[0].components,
    )
    with LocalRegistry(":memory:") as registry:
        registry.add(constraint)
        back = registry.get(constraint.urn)
    assert isinstance(back, DataConstraint)
    assert back.regions == constraint.regions


# ── what OECD sends ──────────────────────────────────────────────────────────

_OECD_21 = b"""<?xml version="1.0" encoding="UTF-8"?>
<mes:Structure xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
 xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
 xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">
 <mes:Header><mes:ID>X</mes:ID><mes:Test>false</mes:Test><mes:Prepared>2026-01-01T00:00:00</mes:Prepared>
  <mes:Sender id="X"/></mes:Header>
 <mes:Structures><str:Constraints>
  <str:ContentConstraint id="CR" agencyID="ESTAT" version="1.4" type="Actual">
   <com:Name xml:lang="en">CR</com:Name>
   <str:CubeRegion include="true">
    <com:KeyValue id="FREQ"><com:Value>A</com:Value></com:KeyValue>
    <com:KeyValue id="TIME_PERIOD"><com:TimeRange>
     <com:StartPeriod isInclusive="true">1990-01-01T00:00:00</com:StartPeriod>
     <com:EndPeriod isInclusive="true">2023-12-31T00:00:00</com:EndPeriod>
    </com:TimeRange></com:KeyValue>
    <com:Attribute id="UNIT_MULT"><com:Value>0</com:Value><com:Value>6</com:Value></com:Attribute>
   </str:CubeRegion>
  </str:ContentConstraint>
 </str:Constraints></mes:Structures>
</mes:Structure>"""


def test_the_oecd_shape_reads_whole() -> None:
    message = parse(_OECD_21)
    assert isinstance(message, StructureMessage)
    (region,) = message.constraints[0].regions
    assert region.keys == (
        KeyValue(dimension_id="FREQ", values={"A"}),
        KeyValue(
            dimension_id="TIME_PERIOD",
            time_range=TimeRange(start=TimeBound("1990-01-01T00:00:00"), end=TimeBound("2023-12-31T00:00:00")),
        ),
    )
    assert region.components == (ComponentValueSet(component_id="UNIT_MULT", values={"0", "6"}),)
