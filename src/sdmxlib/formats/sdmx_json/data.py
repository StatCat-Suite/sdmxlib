"""An SDMX-JSON data message of either version, told apart by its envelope.

2.0 nests everything under ``data`` (``{meta, data: {dataSets, structures}}``);
1.0 keeps ``dataSets`` and ``structure`` at the top level. The one key decides,
so a reader that holds a file need not be told which it is.
"""

from pathlib import Path

import msgspec
import polars as pl

from sdmxlib.formats.sdmx_json import data_v1, data_v2
from sdmxlib.model.errors import SdmxParseError


class _Envelope(msgspec.Struct):
    """Only the key that tells the versions apart; its value is left undecoded."""

    #: Empty when the key is absent — `Raw | None` is not a union msgspec decodes.
    data: msgspec.Raw = msgspec.Raw()


def read_observations(source: bytes | str | Path) -> tuple[pl.DataFrame, str | None]:
    """A data message's observations, and the URN of the dataflow it names — either version.

    Args:
        source: The message's bytes, or a file path.

    Returns:
        What `data_v2.read_observations` / `data_v1.read_observations` return.

    Raises:
        SdmxParseError: If `source` is not a readable SDMX-JSON data message.
            msgspec's error, or a bad UTF-8 sequence, is the ``__cause__``.
    """
    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    try:
        envelope = msgspec.json.decode(raw, type=_Envelope)
        return (data_v2 if len(envelope.data) else data_v1).read_observations(raw)
    except (msgspec.MsgspecError, UnicodeDecodeError) as exc:
        msg = f"could not read {len(raw)} bytes as an SDMX-JSON data message: {exc}"
        raise SdmxParseError(msg) from exc
