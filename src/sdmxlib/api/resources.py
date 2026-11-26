"""Every structure resource each SDMX-REST API names (GH-402).

What `RestRegistry.download_all_structures` asks a registry for, and what the
registry sweep (`bench/sweep`) measures. Unmodelled resources are listed too:
a copy of a registry holds what it serves, whether or not sdmxlib reads it.
"""

from typing import Literal

#: Every structure resource the 2.1 REST API names. The ones the model has no
#: type for (`dataconsumerscheme`, `organisationunitscheme`, `process`,
#: `reportingtaxonomy`) are asked for anyway: the read-loss census is how the
#: sweep finds out whether a registry publishes them.
RESOURCES_21: tuple[str, ...] = (
    "agencyscheme",
    "attachmentconstraint",
    "categorisation",
    "categoryscheme",
    "codelist",
    "conceptscheme",
    "contentconstraint",
    "dataconsumerscheme",
    "dataflow",
    "dataproviderscheme",
    "datastructure",
    "hierarchicalcodelist",
    "metadataflow",
    "metadatastructure",
    "organisationunitscheme",
    "process",
    "provisionagreement",
    "reportingtaxonomy",
    "structureset",
)

#: Every structure resource the 3.0 REST API names, with the same reasoning for
#: the unmodelled ones (`valuelist`, `metadataconstraint`, the geo codelists,
#: the VTL schemes) — #422 is the list of what they lose.
RESOURCES_30: tuple[str, ...] = (
    "agencyscheme",
    "categorisation",
    "categoryscheme",
    "categoryschememap",
    "codelist",
    "conceptscheme",
    "conceptschememap",
    "customtypescheme",
    "dataconstraint",
    "dataconsumerscheme",
    "dataflow",
    "dataproviderscheme",
    "datastructure",
    "geographiccodelist",
    "geogridcodelist",
    "hierarchy",
    "hierarchyassociation",
    "metadataconstraint",
    "metadataflow",
    "metadataproviderscheme",
    "metadataprovisionagreement",
    "metadatastructure",
    "namepersonalisationscheme",
    "organisationschememap",
    "organisationunitscheme",
    "process",
    "provisionagreement",
    "representationmap",
    "rulesetscheme",
    "structuremap",
    "transformationscheme",
    "userdefinedoperatorscheme",
    "valuelist",
    "vtlmappingscheme",
)


def resources(api: Literal["2.1", "3.0"]) -> tuple[str, ...]:
    """The structure resources SDMX-REST `api` names."""
    return RESOURCES_21 if api == "2.1" else RESOURCES_30
