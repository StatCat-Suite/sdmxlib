"""Known SDMX REST endpoint URLs and API versions.

Use as a shortcut when constructing a RestRegistry::

    with sl.RestRegistry(sl.Provider.ECB) as reg:
        ...
    with sl.RestRegistry(sl.Provider["ECB"]) as reg:
        ...
"""

from attrs import frozen


@frozen
class ProviderInfo:
    """URL and REST API version for a known SDMX endpoint.

    Attributes:
        url: Base REST endpoint URL.
        api: SDMX REST API version (``"2.1"`` or ``"3.0"``).
        id: Stable identifier used as the ``source_registry`` provenance
            tag when a `RestRegistry` materialises through to a
            local store. Required for federation; ``None`` for ad-hoc
            URLs without a known label.
        csv_media_type: The ``Accept`` an observation fetch sends, when the
            API version's SDMX-CSV media type is not what this provider
            answers (GH-50). None means that default: SDMX-CSV 1.0 for a
            2.1 API, 2.0 for a 3.0 one. Set per provider because two 2.1
            providers want opposite headers, and a header listing both gets
            SDMX-ML back from each.
    """

    url: str
    api: str = "2.1"
    id: str | None = None
    csv_media_type: str | None = None


class Provider:
    """Built-in SDMX REST endpoint URLs.

    Each entry is a `ProviderInfo` carrying the endpoint URL and its
    REST API version. Pass directly to ``RestRegistry``::

        with sl.RestRegistry(sl.Provider.ECB) as reg:
            ...
        with sl.RestRegistry(sl.Provider.BIS) as reg:
            ...

    Supports both attribute and subscript access::

        sl.Provider.ECB  # ProviderInfo(url="...", api="2.1")
        sl.Provider["ECB"]  # same
        sl.Provider.ECB.url  # "https://data-api.ecb.europa.eu/service"
        sl.Provider.ECB.api  # "2.1"
    """

    # The data `Accept` each answers, checked live 2026-10-22 (GH-50): ECB
    # serves CSV for `text/csv` alone (406 for either SDMX-CSV type); ESTAT
    # the reverse (406 for `text/csv`); BIS answers `text/csv` with SDMX-ML;
    # OECD serves all three. IMF's is unchecked, so it takes the default.

    #: European Central Bank
    ECB = ProviderInfo("https://data-api.ecb.europa.eu/service", api="2.1", id="ECB", csv_media_type="text/csv")
    #: Bank for International Settlements
    BIS = ProviderInfo("https://stats.bis.org/api/v1", api="2.1", id="BIS")
    #: Eurostat
    ESTAT = ProviderInfo("https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1", api="2.1", id="ESTAT")
    #: International Monetary Fund
    IMF = ProviderInfo("https://sdmxcentral.imf.org/ws/public/sdmxapi/rest", api="2.1", id="IMF")
    #: OECD
    OECD = ProviderInfo("https://sdmx.oecd.org/public/rest", api="2.1", id="OECD")
    #: SDMX Global Registry (FMR — SDMX 3.0 endpoint)
    SDMX_GLOBAL = ProviderInfo("https://registry.sdmx.org/sdmx/v2", api="3.0", id="SDMX_GLOBAL")

    def __class_getitem__(cls, key: str) -> ProviderInfo:
        val = getattr(cls, key, None)
        if not isinstance(val, ProviderInfo):
            available = ", ".join(
                k for k in vars(cls) if not k.startswith("_") and isinstance(getattr(cls, k), ProviderInfo)
            )
            msg = f"{key!r} — available: {available}"
            raise KeyError(msg)
        return val

    def __init_subclass__(cls, **kwargs: object) -> None:
        msg = "Provider cannot be subclassed"
        raise TypeError(msg)
