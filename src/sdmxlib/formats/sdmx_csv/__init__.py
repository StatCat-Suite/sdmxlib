"""SDMX-CSV: the data message writer, and what a reader needs to name a file's structure.

Reading a file is a registry's: `reg.scan_csv(source)` returns an `ObsQuery`.
"""

from sdmxlib.formats.sdmx_csv.writer import write_sdmx_csv

__all__ = ["write_sdmx_csv"]
