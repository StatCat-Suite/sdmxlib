"""sdmxlib.builders — synthesise SDMX artefacts from a registry + lake.

Builders are pure, sync, framework-free functions that derive SDMX
artefacts from already-open resources (a `Registry`, a DuckDB
connection). They are the shared core that lets a producer pre-bake an
artefact and a consumer compute the same artefact live, with byte-identical
results.
"""

from .contentconstraint import build_component_availability, build_dataflow_constraint
from .metadata_annotation import (
    DsdMetadataLink,
    annotate_structure_message,
    build_dsd_metadata_map,
    build_metadata_target_links,
)

__all__ = [
    "DsdMetadataLink",
    "annotate_structure_message",
    "build_component_availability",
    "build_dataflow_constraint",
    "build_dsd_metadata_map",
    "build_metadata_target_links",
]
