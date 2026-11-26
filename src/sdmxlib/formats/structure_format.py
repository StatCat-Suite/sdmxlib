"""The structure formats a registry can serve, named by media type (GH-402).

`DataFormat`'s counterpart for ``/structure``: the value is the ``Accept``
header to send. A registry's ``Content-Type`` often says only
``application/xml`` or ``application/json``, so `sniff` reads the version a
body is in from its first bytes: the root element's namespace for SDMX-ML, the
``meta.schema`` URL for SDMX-JSON. FMR 12.5.0 answers a 2.1 request for some
artefacts with SDMX-ML 3.0 under ``application/xml`` (GH-400), and the check
is what catches it.
"""

import re
from enum import StrEnum

from sdmxlib.formats import _xml_root

#: SDMX-ML message namespaces, by the version they name.
_ML_NAMESPACES = {
    "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message": "2.1",
    "http://www.sdmx.org/resources/sdmxml/schemas/v3_0/message": "3.0",
    "http://www.sdmx.org/resources/sdmxml/schemas/v3_1/message": "3.1",
}

#: The schema URL SDMX-JSON names in ``meta.schema``: ``json.sdmx.org/1.0/`` or
#: ``json.sdmx.org/2.0.0/``, a version of two parts or three.
_JSON_SCHEMA = re.compile(rb"json\.sdmx\.org/(\d+\.\d+)(?:\.\d+)?/")


class StructureFormat(StrEnum):
    """A structure format, named by its media type; the value is the ``Accept`` header."""

    SDMX_ML_2_1 = "application/vnd.sdmx.structure+xml;version=2.1"
    SDMX_ML_3_0 = "application/vnd.sdmx.structure+xml;version=3.0.0"
    SDMX_ML_3_1 = "application/vnd.sdmx.structure+xml;version=3.1.0"
    SDMX_JSON_1_0_0 = "application/vnd.sdmx.structure+json;version=1.0.0"
    SDMX_JSON_2_0_0 = "application/vnd.sdmx.structure+json;version=2.0.0"

    @property
    def media_type(self) -> str:
        """The full media type, which is what goes in ``Accept``."""
        return self.value

    @property
    def base(self) -> str:
        """``"xml"`` or ``"json"``, read from the media type's ``+suffix``."""
        return self.value.split(";", 1)[0].rsplit("+", 1)[1]

    @property
    def suffix(self) -> str:
        """The file extension a body in this format takes: ``.xml`` or ``.json``."""
        return f".{self.base}"

    @property
    def sniffed(self) -> str:
        """What `sniff` reads from a body in this format.

        ``"2.1"``, ``"3.0"`` or ``"3.1"`` for SDMX-ML, ``"json-1.0"`` or
        ``"json-2.0"`` for SDMX-JSON.
        """
        version = self.value.rsplit("=", 1)[1]
        short = ".".join(version.split(".")[:2])
        return f"json-{short}" if self.base == "json" else short


def sniff(head: bytes) -> str | None:
    """The structure format a body's first bytes declare, as `StructureFormat.sniffed` spells it.

    Reads the root element's namespace for SDMX-ML and the ``meta.schema`` URL
    for SDMX-JSON, and nothing else. None when the head declares neither.
    """
    stripped = head.lstrip()
    if stripped[:1] == b"{":
        found = _JSON_SCHEMA.search(head)
        return f"json-{found.group(1).decode()}" if found else None
    root = _xml_root(head)
    if root is None:
        return None
    return _ML_NAMESPACES.get(root.namespace or "")
