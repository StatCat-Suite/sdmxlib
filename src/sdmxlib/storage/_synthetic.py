"""The SQL behind `sdmxlib.testing`'s synthetic bundles and profiles (GH-69).

Here rather than in `sdmxlib.testing` because SQL is composed in the storage
layer: every value interpolated below is an integer this module computed or an
identifier a `DataflowBinding` validated, never caller text.
"""

import duckdb

from sdmxlib._duckdb import first
from sdmxlib.model.binding import DataflowBinding


def observations_sql(
    radices: list[int], widths: list[int], *, periods: int, first_year: int, attributes: int, attribute_codes: int
) -> str:
    """The template for synthetic observation rows, before `observations_from` fills in the key walk.

    One column per key dimension (``DIM_1``…, its digit of the key, zero
    padded to its codes' width, prefixed ``D<n>_``), ``TIME_PERIOD`` (a year),
    ``OBS_VALUE`` (a number derived from the key) and ``ATTR_1``….
    """
    stride = 1
    columns: list[str] = []
    for n, (radix, width) in enumerate(zip(radices, widths, strict=True), start=1):
        columns.append(f"'D{n}_' || lpad(((k // {stride}) % {radix})::VARCHAR, {width}, '0') AS DIM_{n}")
        stride *= radix
    columns.append(f"({first_year} + (k // {stride}) % {periods})::VARCHAR AS TIME_PERIOD")
    columns.append("((k * 2654435761 + 97) % 1000003) / 100.0 AS OBS_VALUE")
    columns += [f"'V' || ((k + {n}) % {attribute_codes})::VARCHAR AS ATTR_{n}" for n in range(1, attributes + 1)]
    return f"SELECT {', '.join(columns)} FROM ("


def observations_from(template: str, *, multiplier: int, offset: int, space: int, rows: int) -> str:
    """`template` over ``rows`` keys, row ``i`` taking ``(multiplier·i + offset) mod space``.

    With ``multiplier`` coprime to ``space`` that is a bijection, so no two rows
    share a key.
    """
    return f"{template}SELECT ({multiplier}::HUGEINT * range + {offset}) % {space} AS k FROM range({rows}))"


def distinct_periods(conn: duckdb.DuckDBPyConnection, binding: DataflowBinding, column: str) -> int:
    """About how many distinct values `column` holds in the binding's table (HyperLogLog)."""
    location = f"{binding.database_role}.{binding.table_location}"
    row = first(conn.execute(f'SELECT approx_count_distinct("{column}") FROM {location}'))
    return int(row[0]) if row is not None else 0
