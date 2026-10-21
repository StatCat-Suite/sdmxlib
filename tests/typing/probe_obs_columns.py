"""Static probe: an observation column offers what it really supports (#413).

Not a pytest module — it is type checked, by `just typecheck`.

`ObsQuery.col()` returned an `Untyped` and declared a `Col`. `Col` carries
`__eq__` and little else, so the string predicates `Untyped` inherits through
`.str` — `contains`, `starts_with`, `ends_with`, the `caseless` rung — were
invisible to every checker and every reader. They compiled and ran correctly;
#413 was filed because nothing showed they existed. It is the same lie #347
corrected one level down, on `Observations.__getitem__`.

These are exactly SDMX-REST 2.2.2's component-filter operators: `co`, `nc`,
`sw`, `ew` (and `ne`/`lt`/`le`/`gt`/`ge`, which the comparisons cover).
"""

from typing import assert_type

import sdmxlib as sl
from sdmxlib.query._expr import AggCompare, Pred, Untyped
from sdmxlib.query.observations import ObsQuery, ObsRow
from sdmxlib.query.query import Select


def _the_declaration(obs: ObsQuery) -> None:
    assert_type(obs.col("GEO"), Untyped[ObsRow])


def _sdmx_rest_string_operators(obs: ObsQuery) -> None:
    geo = obs.col("GEO")
    assert_type(geo.str.contains("Census"), Pred[ObsRow])  # co:
    assert_type(~geo.str.contains("Census"), Pred[ObsRow])  # nc:
    assert_type(geo.str.starts_with("Census"), Pred[ObsRow])  # sw:
    assert_type(geo.str.ends_with("Division"), Pred[ObsRow])  # ew:
    assert_type(geo.str.caseless.contains("census"), Pred[ObsRow])


def _comparisons_too(obs: ObsQuery) -> None:
    """`ne`/`lt`/`le`/`gt`/`ge` — lexical on text, which suits ISO periods."""
    period = obs.col("TIME_PERIOD")
    assert_type(period != "2020", Pred[ObsRow])
    assert_type(period >= "2020", Pred[ObsRow])
    assert_type(period < "2021", Pred[ObsRow])


def _root_aggregate(obs: ObsQuery) -> None:
    """`Query.agg` + `sl.len()` (#414): the row kind is taken from the query."""
    assert_type(obs.agg(SEX=obs.col("SEX").unique(), n=sl.len()), Select[ObsRow, object])
    assert_type(obs.agg(n=sl.len()), Select[ObsRow, object])


def _having_takes_an_aggregate_comparison(obs: ObsQuery) -> None:
    """`Agg`'s comparisons declare `AggCompare`, which `having` takes (#414)."""
    over_one = obs.col("AGE").count() > 1
    assert_type(over_one, AggCompare[ObsRow])
    obs.group_by(obs.col("SEX")).agg(n=sl.len()).having(over_one)
