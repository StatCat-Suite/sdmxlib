"""Static probe: `text_type` declares what it returns (the return-widening audit).

`Dimension.text_type` and `TimeDimension.text_type` return `Facet.type`, which is
declared `DataType | str | None`, and themselves declared `str | None`. True —
`DataType` is a `StrEnum` — but it hid the enum from a checker, the same
declaration-wider-than-value shape as `ObsQuery.col()` (#413) and `Agg`'s
comparisons (#414). `tests/unit/test_return_widening.py` guards the shape
generally; this pins the two public instances the audit found.
"""

from typing import assert_type

from sdmxlib.model.datastructure import Dimension, TimeDimension
from sdmxlib.model.representation import DataType


def _the_declarations(dim: Dimension, time: TimeDimension) -> None:
    assert_type(dim.text_type, DataType | str | None)
    assert_type(time.text_type, DataType | str | None)
