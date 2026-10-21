"""The SDMX data wire formats, as media types.

One member per format a provider can actually serve, because the combinations
are not a free cross-product. `docs/design/registry-and-catalog.md` §2
described this as "an explicit ``(media type, version)`` argument", and the
vocabulary refuses that shape: SDMX-ML 2.1 carries data in **four** distinct
flavours under four distinct media types (generic / structure-specific, each
plain or time-series), while ``application/vnd.sdmx.data+xml`` did not exist
until 3.0. A ``("xml", "2.1")`` pair therefore names no single format, and a
``("csv", "3.0")`` pair names nothing at all. An enum makes exactly the real
formats expressible and nothing else.

The member list matches pysdmx's `DataFormat` — the same eleven, checked
member-for-member in `tests/unit/test_data_format.py`. Names are spelled out
where pysdmx abbreviates (``SDMX_ML_2_1_GENERIC`` for its ``SDMX_ML_2_1_GEN``);
sdmx1's `MEDIA_TYPES` covers the same ground but omits SDMX-CSV 2.0.0 and
2.1.0, which is what this library's own writer emits.

Example:
    from sdmxlib.formats.data_format import DataFormat

    DataFormat.SDMX_CSV_2_0_0.media_type
    # 'application/vnd.sdmx.data+csv;version=2.0.0'
    DataFormat.SDMX_ML_2_1_GENERIC.suffix
    # '.xml'
"""

from enum import StrEnum

__all__ = ["DataFormat"]

_SUFFIXES = {"csv": ".csv", "json": ".json", "xml": ".xml"}


class DataFormat(StrEnum):
    """A data format a provider can serve, named by its media type.

    The value is the exact ``Accept`` header to send. Attributes derived from
    it — `base`, `version`, `suffix` — are parsed from that one string, so
    there is no second place for a member to be described inconsistently.

    A `StrEnum`, so a member *is* its media type: it can go straight into a
    headers dict and compares equal to the string. That is also what keeps
    `value` typed as `str` rather than `Any`, which every property here would
    otherwise launder out through a ``-> str``.
    """

    SDMX_CSV_1_0_0 = "application/vnd.sdmx.data+csv;version=1.0.0"
    SDMX_CSV_2_0_0 = "application/vnd.sdmx.data+csv;version=2.0.0"
    SDMX_CSV_2_1_0 = "application/vnd.sdmx.data+csv;version=2.1.0"
    SDMX_JSON_1_0_0 = "application/vnd.sdmx.data+json;version=1.0.0"
    SDMX_JSON_2_0_0 = "application/vnd.sdmx.data+json;version=2.0.0"
    SDMX_ML_2_1_GENERIC = "application/vnd.sdmx.genericdata+xml;version=2.1"
    SDMX_ML_2_1_GENERIC_TIME_SERIES = "application/vnd.sdmx.generictimeseriesdata+xml;version=2.1"
    SDMX_ML_2_1_STRUCTURE_SPECIFIC = "application/vnd.sdmx.structurespecificdata+xml;version=2.1"
    SDMX_ML_2_1_STRUCTURE_SPECIFIC_TIME_SERIES = "application/vnd.sdmx.structurespecifictimeseriesdata+xml;version=2.1"
    SDMX_ML_3_0 = "application/vnd.sdmx.data+xml;version=3.0.0"
    SDMX_ML_3_1 = "application/vnd.sdmx.data+xml;version=3.1.0"

    @property
    def media_type(self) -> str:
        """The full media type, which is what goes in ``Accept``."""
        return self.value

    @property
    def base(self) -> str:
        """The underlying serialisation: ``"csv"``, ``"json"`` or ``"xml"``.

        Read out of the media type's ``+suffix``, per RFC 6839 — the same
        place a server reads it.
        """
        return self.value.split(";", 1)[0].rsplit("+", 1)[1]

    @property
    def version(self) -> str:
        """The format version, as the media type states it.

        Note this is the *format's* version, not the API's, and not always
        three parts: SDMX-ML 2.1's media types say ``version=2.1`` while
        SDMX-CSV's say ``version=2.0.0``. Reported as written rather than
        normalised, because it is echoed back in ``Accept``.
        """
        return self.value.split("version=", 1)[1]

    @property
    def suffix(self) -> str:
        """The conventional file extension, leading dot included.

        Advisory only. `RestRegistry.download` never *infers* a format from a
        path — it uses this to refuse a path that contradicts the format asked
        for, which is a different thing and the safer half of the two.
        """
        return _SUFFIXES[self.base]
