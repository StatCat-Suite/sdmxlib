"""The render version of each wire format sdmxlib writes, for cache keys (GH-132).

A served response's bytes depend on the bundle and on the code that writes
them. The effective token covers the bundle and its overlays; the render
version covers the writer. A server that caches what it writes keys on both,
so a writer fix gives new keys and the old entries age out, and a release
whose writers write the same bytes leaves every key as it was.

Each version is the first 16 hex characters of the sha256 of that format's
golden file: one pinned input, written. `tests/unit/test_render_version.py`
fails when a writer's output leaves its golden file, and again when the
golden file leaves its version here, so a writer change cannot ship under an
old version. `tests/support/render_corpus.py` rewrites the golden files and
prints this mapping.

Example:
    from sdmxlib.formats.data_format import DataFormat
    from sdmxlib.formats.render import render_version

    key = (effective_token, render_version(DataFormat.SDMX_CSV_2_0_0), query, lang)
"""

from collections.abc import Mapping
from types import MappingProxyType

#: Each written format's render version, by media type.
RENDER_VERSION: Mapping[str, str] = MappingProxyType(
    {
        "application/vnd.sdmx.structure+xml;version=2.1": "49cc074f171b9c27",
        "application/vnd.sdmx.structure+xml;version=3.0.0": "59035aefb9e011fd",
        "application/vnd.sdmx.structure+xml;version=3.1.0": "45c504a2b2672e79",
        "application/vnd.sdmx.structure+json;version=2.0.0": "4e69e1656ec0608b",
        "application/vnd.sdmx.structure+json;version=1.0.0": "46f8af53f63ed11f",
        "application/vnd.sdmx.metadata+xml;version=3.0.0": "c5441ae467a8ea9c",
        "application/vnd.sdmx.metadata+json;version=2.0.0": "8ac8bc1dcc3b5310",
        "application/vnd.sdmx.data+csv;version=2.0.0": "9d1378142ddac704",
        "application/vnd.sdmx.data+json;version=1.0.0": "30a8ba5971051a86",
        "application/vnd.sdmx.data+json;version=2.0.0": "34b11da33b3eff61",
    }
)


def render_version(media_type: str) -> str:
    """The render version of the format sdmxlib writes as `media_type`.

    Args:
        media_type: The media type, as `RENDER_VERSION` keys it; a
            `DataFormat` member is its media type.

    Returns:
        16 hex characters, which change exactly when the format's writer
        writes other bytes.

    Raises:
        KeyError: If sdmxlib writes no format of that media type.
    """
    try:
        return RENDER_VERSION[media_type]
    except KeyError:
        msg = f"sdmxlib writes no format as {media_type!r}; it writes {', '.join(RENDER_VERSION)}"
        raise KeyError(msg) from None
