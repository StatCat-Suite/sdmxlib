"""The registries the sweep reads, and the structure resources it asks each for.

One `Endpoint` per (registry, REST API version). A registry that serves both
APIs gets two, and the pair is what the cross-API comparison reads: the same
artefact as two documents, which is the closest thing to an outside oracle for
the 2.1 projection that exists.

`engine` names the server software, because during the move to FMR the same
symptom means different things on different engines. .Stat NSI is 2.1 inside,
so its 3.0 output is a translation and a difference between its two documents
says as much about the translation as about this library. FMR is 3.0 inside,
so for it the 3.0 document is the native one. "unverified" is a statement that
nobody has checked, not a guess.
"""

from __future__ import annotations

from typing import Literal

from attrs import frozen

type Api = Literal["2.1", "3.0"]
type Engine = Literal["nsi", "fmr", "unverified"]

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

#: The 2.1 resource and the 3.0 resource that carry the same artefacts. Only
#: pairs whose artefacts share a model type and a URN are listed: a 2.1 HCL and
#: a 3.0 Hierarchy are the same *concept* in different shapes, which is #421's
#: subject and not a field-by-field comparison.
CROSS_API_PAIRS: tuple[tuple[str, str], ...] = (
    ("agencyscheme", "agencyscheme"),
    ("categorisation", "categorisation"),
    ("categoryscheme", "categoryscheme"),
    ("codelist", "codelist"),
    ("conceptscheme", "conceptscheme"),
    ("contentconstraint", "dataconstraint"),
    ("dataflow", "dataflow"),
    ("dataproviderscheme", "dataproviderscheme"),
    ("datastructure", "datastructure"),
    ("metadataflow", "metadataflow"),
    ("metadatastructure", "metadatastructure"),
    ("provisionagreement", "provisionagreement"),
)

_ACCEPT: dict[Api, str] = {
    "2.1": "application/vnd.sdmx.structure+xml;version=2.1",
    "3.0": "application/vnd.sdmx.structure+xml;version=3.0.0",
}


@frozen
class Endpoint:
    """One REST API of one registry.

    Attributes:
        registry: Short name; the summary file is named after it.
        engine: The server software, when someone has checked.
        api: The SDMX REST API version this base URL speaks.
        base: Base URL, without a trailing slash.
        agency: The agency segment of a bulk query. `all` / `*` everywhere
            except where a registry refuses an all-agency listing.
        version: The version segment of a bulk query. `latest` / `~` except
            where a registry refuses it (Eurostat's 3.0 API only accepts `*`
            for an all-artefact listing, so it returns every version).
    """

    registry: str
    engine: Engine
    api: Api
    base: str
    agency: str
    version: str

    @property
    def key(self) -> str:
        """`<registry>-<api>`, the name of this endpoint's cache directory."""
        return f"{self.registry}-{self.api}"

    @property
    def accept(self) -> str:
        """The Accept header asking for this API's SDMX-ML."""
        return _ACCEPT[self.api]

    @property
    def resources(self) -> tuple[str, ...]:
        """Every structure resource this API names."""
        return RESOURCES_21 if self.api == "2.1" else RESOURCES_30

    def url(self, resource: str) -> str:
        """The bulk query for every artefact of `resource`."""
        if self.api == "2.1":
            return f"{self.base}/{resource}/{self.agency}/all/{self.version}"
        return f"{self.base}/structure/{resource}/{self.agency}/*/{self.version}"


def _v21(registry: str, engine: Engine, base: str, agency: str = "all") -> Endpoint:
    return Endpoint(registry=registry, engine=engine, api="2.1", base=base, agency=agency, version="latest")


def _v30(registry: str, engine: Engine, base: str, agency: str = "*", version: str = "~") -> Endpoint:
    return Endpoint(registry=registry, engine=engine, api="3.0", base=base, agency=agency, version=version)


ENDPOINTS: tuple[Endpoint, ...] = (
    _v21("OECD", "nsi", "https://sdmx.oecd.org/public/rest"),
    _v30("OECD", "nsi", "https://sdmx.oecd.org/public/rest/v2"),
    _v21("SDMX", "fmr", "https://registry.sdmx.org/ws/public/sdmxapi/rest"),
    _v30("SDMX", "fmr", "https://registry.sdmx.org/sdmx/v2"),
    _v30("IMF", "unverified", "https://api.imf.org/external/sdmx/3.0"),
    _v21("ECB", "unverified", "https://data-api.ecb.europa.eu/service"),
    _v21("BIS", "unverified", "https://stats.bis.org/api/v1"),
    _v30("BIS", "unverified", "https://stats.bis.org/api/v2"),
    _v21("ESTAT", "unverified", "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1", agency="ESTAT"),
    _v30(
        "ESTAT",
        "unverified",
        "https://ec.europa.eu/eurostat/api/dissemination/sdmx/3.0",
        agency="ESTAT",
        version="*",
    ),
)

REGISTRIES: tuple[str, ...] = tuple(dict.fromkeys(e.registry for e in ENDPOINTS))


def endpoints_of(registry: str) -> tuple[Endpoint, ...]:
    """Every endpoint of `registry`, 2.1 first.

    Raises:
        KeyError: If `registry` is not in the table.
    """
    found = tuple(e for e in ENDPOINTS if e.registry == registry)
    if not found:
        msg = f"unknown registry {registry!r}; expected one of {', '.join(REGISTRIES)}"
        raise KeyError(msg)
    return found
