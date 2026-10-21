"""sdmxlib.formats.sdmx_json_1_0 — SDMX-JSON 1.0 writers.

SDMX-JSON 1.0 is the version the SDMX REST ``/availability`` endpoint
pins. This package downgrades SDMX-3-style domain objects to the 1.0
wire shape; today that is the ContentConstraint serialiser.
"""

from .writer import write_contentconstraint

__all__ = ["write_contentconstraint"]
