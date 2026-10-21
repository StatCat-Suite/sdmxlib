"""Arrow record batch → polars frame, on the fast path (#427).

The data writers convert every batch they encode, so the conversion is on the
hot path of every bulk download. `pl.from_arrow(batch)` is the zero-copy
conversion; the `pl.DataFrame(batch)` constructor takes a slower general path —
2.4x slower to build the frame on polars 1.39, and 1.33x slower for SDMX-CSV's
whole per-batch encode. 4f65fd7 switched to the constructor for its return type
and cost a consumer ~17% on large CSV downloads.

`from_arrow` is typed `DataFrame | Series`, and since polars 2.0 really returns
a `Series` for stream-only inputs. A `cast` would pass that through unchecked,
so the result is narrowed at runtime instead: a record batch takes the fast
branch, and anything that ever comes back as a `Series` falls back to the
constructor rather than being mislabelled.

Shared by the SDMX-CSV and SDMX-JSON data writers, hence `sdmxlib.formats`.
"""

import polars as pl
import pyarrow as pa


def frame_of(batch: pa.RecordBatch) -> pl.DataFrame:
    """The batch as a polars frame, zero-copy where polars allows it."""
    df = pl.from_arrow(batch)
    if isinstance(df, pl.DataFrame):
        return df
    return pl.DataFrame(batch)
