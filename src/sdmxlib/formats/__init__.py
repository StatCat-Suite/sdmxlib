"""sdmxlib.formats — SDMX wire format readers and writers.

Two distinct message envelopes:

- ``StructureMessage`` — definitions (Codelists, DSDs, MSDs, Metadataflows…)
  carried in ``<mes:Structure>`` (XML) or the structure ``$schema`` (JSON).
- ``MetadataMessage`` — populated reference metadata carried in
  ``<mes:GenericMetadata>`` (XML) or the metadata ``$schema`` (JSON).

The top-level ``parse`` and ``serialize`` entry points auto-discriminate
inbound messages and dispatch outbound writes on the message type.

Example:
    from sdmxlib.formats import read, parse, to_xml, to_json

    msg = read("path/to/file.xml")  # StructureMessage or MetadataMessage
    msg = read(json_bytes)

    xml_bytes = to_xml(msg)  # version="2.1" (default) or "3.0"
    json_bytes = to_json(msg)
"""

from io import BytesIO
from pathlib import Path

from contextlib import nullcontext
import msgspec
from lxml import etree

from sdmxlib.formats.sdmx_json.metadata import (
    METADATA_SCHEMA_URL as _METADATA_JSON_SCHEMA,
)
from sdmxlib.formats.sdmx_json.metadata import (
    read as _read_metadata_json,
)
from sdmxlib.formats.sdmx_json.metadata import (
    write as _write_metadata_json,
)
from sdmxlib.formats.gaps import without_dropped
from sdmxlib.formats.sdmx_json._dto import _camel_rename
from sdmxlib.formats.sdmx_json.reader import read as _read_json
from sdmxlib.formats.sdmx_json.writer import write_kept as _write_json_kept
from sdmxlib.formats.sdmx_ml21.namespaces import MES as _MES_21
from sdmxlib.formats.sdmx_ml21.reader import read as _read_21
from sdmxlib.formats.sdmx_ml21.writer import write_kept as _write_21_kept
from sdmxlib.formats.sdmx_ml30.metadata import read as _read_metadata_30
from sdmxlib.formats.sdmx_ml30.metadata import write as _write_metadata_30
from sdmxlib.formats.sdmx_ml30.namespaces import MES as _MES_30
from sdmxlib.formats.sdmx_ml30.reader import read as _read_30
from sdmxlib.formats.sdmx_ml30.writer import write_kept as _write_30_kept
from sdmxlib.formats.sdmx_ml31.namespaces import MES as _MES_31
from sdmxlib.formats.sdmx_ml31.reader import read as _read_31
from sdmxlib.formats.sdmx_ml31.writer import write_kept as _write_31_kept
from sdmxlib.model._binding import binding
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.message import MetadataMessage, StructureMessage
from sdmxlib.model.registry import SupportsAddAll
from sdmxlib.model.validation import (
    LossyReadError,
    SdmxConformanceError,
    SdmxErrorMessage,
    SdmxParseError,
    ValidationError,
    check_metadata_serialisable,
    check_serialisable,
)

#: What a malformed payload throws from inside the untyped shell, before
#: `parse` converts it. lxml raises a `SyntaxError` subclass; msgspec's errors
#: and `UnicodeDecodeError` are already `ValueError`s, which is why
#: `SdmxParseError` is one too.
_SHELL_FAILURES = (etree.LxmlError, msgspec.MsgspecError, UnicodeDecodeError)


def read(
    source: str | Path | bytes,
    *,
    version: str | None = None,
    registry: SupportsAddAll | None = None,
    strict: bool = True,
    lossless: bool = False,
) -> StructureMessage | MetadataMessage:
    """Read an SDMX file (or raw bytes) into a Structure or Metadata message.

    Format and message type are auto-detected. The result is a
    ``StructureMessage`` for definitions or a ``MetadataMessage`` for
    populated reference-metadata payloads.

    Args:
        source: File path (``str`` or ``Path``) or raw bytes.
        version: Optional format hint: ``"json"``, ``"2.1"``, ``"3.0"`` or ``"3.1"``.
            Auto-detected if omitted.
        registry: Optional registry to add the artefacts to after parsing.
            Only honoured for StructureMessages — MetadataMessages do not
            interact with the registry today.
        strict: Raise if the reader refused any artefact. See `parse`.
        lossless: Raise if the read could not hold everything. See `parse`.

    Returns:
        The parsed message.
    """
    data = Path(source).read_bytes() if isinstance(source, (str, Path)) else source
    return parse(data, version=version, registry=registry, strict=strict, lossless=lossless)


def parse(
    data: bytes,
    *,
    version: str | None = None,
    registry: SupportsAddAll | None = None,
    strict: bool = True,
    lossless: bool = False,
) -> StructureMessage | MetadataMessage:
    """Parse SDMX bytes into a StructureMessage or MetadataMessage.

    Auto-detects the format and message type from the payload:

    - JSON envelopes are routed by ``$schema`` URL.
    - XML envelopes are routed by root element (``<mes:Structure>`` vs
      ``<mes:GenericMetadata>``).

    A structure document is read artefact by artefact: one that
    contradicts the schema is *refused* — recorded in
    `StructureMessage.refused`, never partly built — and the others are read.
    So is one that breaks a rule `Registry.add` enforces, and a
    reference another artefact holds into it is left unresolved.
    By default a refusal still fails the call, so nothing wrong is accepted
    silently; ``strict=False`` returns what was read, refusals attached.

    Content inside an artefact that the model has no field for yet — SDMX-ML
    2.1's `RepresentationMapping` in a component map — is a different case: the
    artefact is built without it and the loss recorded in
    `StructureMessage.lost`. The read succeeds unless ``lossless=True``.
    SDMX-ML only, so far; the SDMX-JSON reader does not report losses yet.

    Args:
        data: Raw bytes — JSON or XML.
        version: Optional hint: ``"json"``, ``"2.1"``, ``"3.0"`` or ``"3.1"``.
            Auto-detected if omitted.
        registry: Optional registry to add the artefacts to. Honoured only
            for StructureMessages. Nothing is interned if the call raises.
        strict: When True (the default), any refusal raises
            `SdmxConformanceError`. When False, the message is returned with
            `StructureMessage.refused` set, and what was read is interned.
        lossless: When True, any loss, or any value the reader had to supply
            for required content the document omitted, raises
            `LossyReadError`. When False (the default), they are recorded in
            `StructureMessage.lost` and `StructureMessage.assumed`.
            For SDMX-JSON they are found when `lost` is first read, and the
            message holds the document's bytes until then. True finds them
            during the read instead, so the message keeps nothing extra.

    Returns:
        The parsed message.

    Raises:
        SdmxParseError: If the payload cannot be read as SDMX — the format
            cannot be detected, the bytes are not well-formed XML or JSON, or
            the document contradicts the schema (`SdmxConformanceError`, a
            subclass). It is a ``ValueError``, and the underlying lxml or
            msgspec failure is preserved as ``__cause__``. When ``strict``
            and an artefact was refused, the `SdmxConformanceError` names the
            refusals and carries them as ``.refused``, with everything that
            was read as ``.message``. When ``lossless`` and anything was
            lost, `LossyReadError`, carrying ``.lost`` and ``.message``.
            When the document is an SDMX error message (``<mes:Error>``,
            ``{"errors": [...]}``), `SdmxErrorMessage`, carrying each
            error's code and text as ``.errors``.
    """
    try:
        # Read straight into an in-memory registry: its references are bound
        # to it as they are built, so `add_all` below copies nothing (#492).
        with binding(registry._resolver) if isinstance(registry, InMemoryRegistry) else nullcontext():
            msg = _dispatch(data, version=version)
    except SdmxParseError:
        raise
    except _SHELL_FAILURES as exc:
        # The untyped shell — lxml, msgspec, a bad UTF-8 sequence — surfacing
        # its own exception types through a public entry point. Converted here
        # so a caller handling a malformed provider response catches one thing
        # rather than importing two third-party libraries to name theirs.
        err = f"could not read {len(data)} bytes as SDMX: {exc}"
        raise SdmxParseError(err) from exc
    except ValidationError as exc:
        # A value the model refuses as it is set (#492), built where no
        # per-artefact refusal catches it — a metadata set, say. The structure
        # readers refuse such an artefact alone (`build_each`); this keeps
        # `parse`'s one exception type for whatever else holds one.
        issues = "; ".join(f"{issue.path} — {issue.message}" for issue in exc.issues)
        err = f"the document holds a value no SDMX artefact can: {issues}"
        raise SdmxParseError(err) from exc
    if isinstance(msg, StructureMessage):
        if strict and msg.refused:
            raise SdmxConformanceError.refusing(msg)
        if lossless and (msg.lost or msg.assumed):
            raise LossyReadError(msg)
        # Interned here rather than by the reader, so a strict call that
        # raises has interned nothing (#435).
        if registry is not None:
            registry.add_all(msg.all_artefacts())
    return msg


def _dispatch(data: bytes, *, version: str | None) -> StructureMessage | MetadataMessage:
    """Detect format and message kind, then hand off to the matching reader."""
    detected = version or _detect_version(data)
    _refuse_error_message(data, detected)
    kind = _detect_message_kind(data, detected)

    if kind == "metadata":
        if detected == "json":
            return _read_metadata_json(data)
        if detected == "3.0":
            return _read_metadata_30(data)
        msg = f"Metadata messages are not supported for {detected!r}"
        raise SdmxParseError(msg)

    # Structure message
    if detected == "json":
        return _read_json(data)
    if detected == "2.1":
        return _read_21(data)
    if detected == "3.0":
        return _read_30(data)
    if detected == "3.1":
        return _read_31(data)
    msg = f"Unsupported format: {detected!r}"
    raise SdmxParseError(msg)


def serialize(
    msg: StructureMessage | MetadataMessage,
    *,
    version: str = "2.1",
    pretty_print: bool = False,
    validate: bool = True,
) -> bytes:
    """Serialise a Structure or Metadata message to bytes.

    Dispatches on the message type:

    - ``StructureMessage`` → ``<mes:Structure>`` envelope (XML) or structure
      ``$schema`` (JSON).
    - ``MetadataMessage`` → ``<mes:GenericMetadata>`` envelope (XML) or
      metadata ``$schema`` (JSON).

    Validates before emitting. `check_serialisable` runs first and raises
    ``ValidationError`` — with every issue accumulated, not just the first —
    so a message the target format cannot carry never reaches the wire. What
    every format requires of an artefact alone is refused as it is built, so
    there is nothing of that left to check here.

    This used to default to ``False``, reasoning that "messages built through
    the typed model or read back by the parser are already well-formed". That
    proved untrue often enough to matter — five distinct ways to build a
    message the SDMX schema rejects were found — and the cost argument does not
    hold either: validation measures 4-9% of serialisation, the *lower* figure
    being the larger payload, because the item-id checks vectorise above 64
    items.

    Pass ``validate=False`` to skip deliberately — e.g. round-tripping a
    message you know is non-conformant in order to repair it.

    Args:
        msg: The message to serialise.
        version: Wire format — ``"2.1"`` (default), ``"3.0"`` or ``"3.1"``
            (SDMX-ML XML), or ``"json"`` (SDMX-JSON 2.0). MetadataMessage requires ``"3.0"``
            or ``"json"`` (SDMX-ML 2.1 GenericMetadata is out of scope).
        pretty_print: If True, indent the output.
        validate: Run `check_serialisable` first, raising ``ValidationError``
            (accumulated across all artefacts) before any bytes are produced.
            Default True; pass False to emit without checking.
    """
    if isinstance(msg, MetadataMessage):
        if validate:
            # The metadata side used to return here unchecked, so a payload
            # with no targets — which neither 3.0 nor SDMX-JSON accepts —
            # serialised silently to an invalid document (#234).
            check_metadata_serialisable(msg, version=version)
        if version == "json":
            return _write_metadata_json(msg, pretty_print=pretty_print)
        if version == "3.0":
            return _write_metadata_30(msg, pretty_print=pretty_print)
        err = f"MetadataMessage cannot be serialised as {version!r} (use '3.0' or 'json')"
        raise ValueError(err)

    writers = {"json": _write_json_kept, "2.1": _write_21_kept, "3.0": _write_30_kept, "3.1": _write_31_kept}
    if version not in writers:
        # Before validating: the writability checks are per format, and there
        # is no answer to them for a format that does not exist.
        err = f"Unsupported format for writing: {version!r}"
        raise ValueError(err)

    # Before validating: a draft is left out of 2.1 rather than refused, with
    # what references it (#424), as is an artefact of a class the format
    # lacks (#421), and what is left out is not written and so not checked.
    # `dropped(msg, version)` says what that was.
    msg = without_dropped(msg, version)

    if validate:
        # What the message and the target format require, which `add` does
        # not ask: a DSD with no dimensions is valid SDMX-JSON and not
        # SDMX-ML, so it is storable and refused here for SDMX-ML (#419).
        check_serialisable(msg, version=version)

    # The writer's step after the drop, which happened above: through its
    # `write`, the drop would run again (#464).
    return writers[version](msg, pretty_print=pretty_print)


def to_xml(
    msg: StructureMessage | MetadataMessage,
    *,
    version: str = "2.1",
    pretty_print: bool = False,
    validate: bool = True,
) -> bytes:
    """Serialise a Structure or Metadata message to SDMX-ML XML bytes.

    MetadataMessage requires ``version="3.0"`` (SDMX-ML 2.1 GenericMetadata
    is intentionally not supported). See `serialize` for ``validate``.
    """
    if isinstance(msg, MetadataMessage) and version != "3.0":
        version = "3.0"
    return serialize(msg, version=version, pretty_print=pretty_print, validate=validate)


def to_json(msg: StructureMessage | MetadataMessage, *, pretty_print: bool = False, validate: bool = True) -> bytes:
    """Serialise a Structure or Metadata message to SDMX-JSON 2.0 bytes.

    See `serialize` for ``validate``.
    """
    return serialize(msg, version="json", pretty_print=pretty_print, validate=validate)


_MESSAGE_NS_VERSION = {_MES_21: "2.1", _MES_30: "3.0", _MES_31: "3.1"}

#: Local name of the SDMX-ML root element that carries reported metadata.
_METADATA_ROOT = "GenericMetadata"

#: Local name of the SDMX-ML root element of an error message, and of each error in it.
_ERROR_ROOT = "Error"
_ERROR_ENTRY = "ErrorMessage"


class _ErrorProbeEntry(msgspec.Struct):
    """One SDMX-JSON error. ``message`` is not in the schema; FMR 12.4 writes it."""

    code: int | float | str
    title: str | None = None
    detail: str | None = None
    message: str | None = None


class _ErrorProbe(msgspec.Struct):
    """Just enough of an SDMX-JSON document to tell an error message from the rest."""

    errors: list[_ErrorProbeEntry] | None = None
    data: msgspec.Raw | None = None


def _refuse_error_message(data: bytes, version: str) -> None:
    """Raise `SdmxErrorMessage` if *data* is an SDMX error message (GH-31).

    SDMX-ML: the root is ``<mes:Error>``. SDMX-JSON: ``errors`` is non-empty
    and there is no ``data``, since a message may also carry errors beside
    what it answers. A JSON payload this probe cannot decode is left to the
    reader, so the reader's own error is the one raised.

    Raises:
        SdmxErrorMessage: Carrying each error's code and text.
    """
    if version == "json":
        try:
            probe = msgspec.json.decode(data, type=_ErrorProbe)
        except msgspec.MsgspecError:
            return
        if probe.errors and probe.data is None:
            raise SdmxErrorMessage(tuple((_code(e.code), e.title or e.detail or e.message or "") for e in probe.errors))
        return
    root = _xml_root(data)
    if root is None or root.localname != _ERROR_ROOT or root.namespace not in _MESSAGE_NS_VERSION:
        return
    entries = [
        el for el in etree.fromstring(data) if isinstance(el.tag, str) and etree.QName(el).localname == _ERROR_ENTRY
    ]
    raise SdmxErrorMessage(tuple((el.get("code", ""), _first_text(el)) for el in entries))


def _code(code: float | str) -> str:
    """An SDMX-JSON error code as SDMX-ML writes it: ``404``, not ``404.0``."""
    if isinstance(code, float) and code.is_integer():
        return str(int(code))
    return str(code)


def _first_text(entry: etree._Element) -> str:
    """The first ``<com:Text>`` of an ``<ErrorMessage>``; a message may give several languages."""
    for child in entry:
        if isinstance(child.tag, str) and etree.QName(child).localname == "Text":
            return (child.text or "").strip()
    return ""


def _xml_root(data: bytes) -> etree.QName | None:
    """The qualified name of the XML root element, or None if *data* is not XML.

    Stops at the first ``start`` event, so this reads the root tag and no more —
    it does not parse the document, and it does not depend on the root landing
    within some byte window.

    Args:
        data: Raw payload.

    Returns:
        The root element's `QName`, or None if the payload will not parse as XML.
    """
    try:
        for _event, el in etree.iterparse(BytesIO(data), events=("start",), recover=False):
            # `QName` rejects a malformed tag (``<mes:>``) with a bare
            # `ValueError` rather than an `LxmlError`, so it needs naming
            # separately — a document whose root has no usable name is simply
            # one whose format cannot be detected (#247).
            return etree.QName(el)
    except etree.XMLSyntaxError, ValueError:
        return None
    return None


def _detect_version(data: bytes) -> str:
    """Detect the wire format from the payload.

    JSON is identified by its opening brace; SDMX-ML by the *namespace of the
    root element*. Both used to be substring searches over a fixed byte prefix,
    which misrouted any document whose content happened to contain a sentinel
    token — see #220, where a codelist named "... GenericMetadata" was parsed
    as an empty MetadataMessage with no error.

    Args:
        data: Raw payload.

    Returns:
        ``"json"``, ``"2.1"``, ``"3.0"`` or ``"3.1"``.

    Raises:
        ValueError: If the payload is neither JSON nor recognised SDMX-ML.
    """
    stripped = data[:2048].lstrip()
    if stripped and stripped[0:1] == b"{":
        return "json"
    root = _xml_root(data)
    if root is not None and root.namespace in _MESSAGE_NS_VERSION:
        return _MESSAGE_NS_VERSION[root.namespace]
    msg = "Cannot detect format: not JSON and no recognised SDMX-ML namespace"
    raise SdmxParseError(msg)


class _KindProbeData(msgspec.Struct, rename=_camel_rename):
    """Just enough of ``/data`` to tell the two message kinds apart."""

    metadata_sets: list[msgspec.Raw] | None = None


class _KindProbeMeta(msgspec.Struct):
    """Just enough of ``/meta`` — some producers put the schema URL here."""

    schema: str | None = None


class _KindProbe(msgspec.Struct, rename={"schema_url": "$schema"}):
    """Envelope probe for message-kind detection."""

    schema_url: str | None = None
    meta: _KindProbeMeta | None = None
    data: _KindProbeData | None = None


def _detect_json_kind(data: bytes) -> str:
    """Return ``"structure"`` or ``"metadata"`` for an SDMX-JSON payload.

    Discriminates on the *values* of ``$schema`` / ``meta.schema`` and on the
    presence of ``data.metadataSets`` — not on whether a token appears anywhere
    in the bytes. The previous substring form read a structure message whose
    codelist was merely *named* "... metadata-schema ..." as an empty
    MetadataMessage, with no error (#220).

    A payload that does not decode falls back to ``"structure"`` so the real
    reader raises the parse error, rather than this probe masking it.

    Args:
        data: Raw JSON payload.

    Returns:
        ``"structure"`` or ``"metadata"``.
    """
    try:
        probe = msgspec.json.decode(data, type=_KindProbe)
    except msgspec.DecodeError:
        return "structure"
    # IMF and others cite the raw GitHub URL rather than the canonical one, so
    # match the distinctive path segment within a URL we actually read.
    for url in (probe.schema_url, probe.meta.schema if probe.meta else None):
        if url and ("metadata-schema" in url or url == _METADATA_JSON_SCHEMA):
            return "metadata"
    return "metadata" if probe.data is not None and probe.data.metadata_sets is not None else "structure"


def _detect_message_kind(data: bytes, version: str) -> str:
    """Return ``"structure"`` or ``"metadata"`` based on the message envelope."""
    if version == "json":
        return _detect_json_kind(data)
    # XML: the root element decides. <mes:GenericMetadata> is metadata;
    # everything else (Structure, StructureSpecificMetadata, …) is structure.
    # This used to substring-search the first 4 KB, so a *structure* message
    # merely mentioning "GenericMetadata" — in a codelist name, an annotation —
    # was read as metadata, silently returning an empty message (#220).
    root = _xml_root(data)
    return "metadata" if root is not None and root.localname == _METADATA_ROOT else "structure"
