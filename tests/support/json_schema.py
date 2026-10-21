"""JSON-Schema validation helpers for SDMX-JSON writer tests.

The vendored schemas live under ``tests/schemas/sdmx_json/<version>/`` — one
self-contained ``$schema`` document per message kind
(structure / metadata / data). Mirrors the SDMX-ML XSD support module
``tests/support/xsd.py``.

**Pass the version the writer emits.** ``formats.sdmx_json`` writes SDMX-JSON
**2.0**; the 2.1 schemas describe SDMX 3.1 and disagree about far more than a
version number — 2.1 closes every object with ``unevaluatedProperties: false``
where 2.0 leaves them open. Validating 2.0 output against 2.1 reports
violations that are version differences rather than defects, and #358 was
nearly decided on a conclusion drawn that way.

Schema compilation is the slow part (~100 ms — large schema, draft
2019-09 keyword support). Tests should pull the compiled validator via
the session-scoped fixtures in ``tests/unit/conftest.py``; this module
exposes the compile primitive plus a validation helper that raises a
readable ``AssertionError`` with the full error list.
"""

from __future__ import annotations

import functools
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import jsonschema
import jsonschema.validators
from jsonschema.exceptions import ValidationError
from jsonschema.protocols import Validator

SchemaKind = Literal["structure", "metadata", "data"]
SchemaVersion = Literal["1.0", "2.0", "2.1"]

_SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas" / "sdmx_json"

_FILENAMES: dict[SchemaKind, str] = {
    "structure": "sdmx-json-structure-schema.json",
    "metadata": "sdmx-json-metadata-schema.json",
    "data": "sdmx-json-data-schema.json",
}

# 1.0 vendors only the data message. 2.0 gained structure and metadata in
# #364 — before that the only *structure* schema in the tree was 2.1's, which
# is not the version this library emits.
_KINDS_BY_VERSION: dict[SchemaVersion, set[SchemaKind]] = {
    "1.0": {"structure", "data"},
    "2.0": {"structure", "metadata", "data"},
    "2.1": {"structure", "metadata", "data"},
}


@functools.cache
def compile_json_schema(kind: SchemaKind, version: SchemaVersion = "2.1") -> Validator:
    """Compile an SDMX-JSON schema for the given message *kind* and *version*.

    Args:
        kind: ``"structure"`` / ``"metadata"`` / ``"data"``. SDMX-JSON
            ``"1.0"`` vendors no metadata schema.
        version: SDMX-JSON spec version. Defaults to ``"2.1"`` to preserve
            existing callers.

    Returns:
        A compiled `jsonschema` ``Validator`` — draft 2019-09 for ``2.1``,
        draft 7 for ``1.0`` / ``2.0`` (those schemas predate the draft
        identifier and are draft-04/07-compatible).

    Raises:
        FileNotFoundError: If the vendored schema is missing — points
            the caller at ``tests/schemas/README.md``.
        ValueError: If *kind* is not available under *version*.
    """
    if kind not in _KINDS_BY_VERSION[version]:
        msg = f"SDMX-JSON {version} does not vendor a {kind} schema"
        raise ValueError(msg)
    filename = _FILENAMES[kind]
    path = _SCHEMAS_DIR / version / filename
    if not path.exists():
        msg = f"SDMX-JSON {version} {kind} schema not found at {path}; see tests/schemas/README.md"
        raise FileNotFoundError(msg)
    schema = json.loads(path.read_text())
    if version in ("1.0", "2.0"):
        # The legacy schemas declare ``http://json-schema.org/schema#`` with
        # no draft pinned. ``validator_for`` falls back to "latest" and warns
        # about future breakage; pin to draft 7 explicitly — that's the
        # newest draft compatible with the keywords those schemas actually
        # use (no ``unevaluatedProperties`` etc.).
        validator_cls: type[Validator] = jsonschema.Draft7Validator
    else:
        validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    # Enable format-checking so ``oneOf`` over {format: date, format: date-time}
    # actually discriminates by format. Without this, a date-time string
    # satisfies both subschemas and oneOf semantics fire spuriously.
    return validator_cls(schema, format_checker=validator_cls.FORMAT_CHECKER)


def is_declared_time_bound(err: ValidationError) -> bool:
    """Whether *err* is a data message's TIME_PERIOD ``start``/``end`` with no zone.

    A declared deviation (#496): the schema requires a zone, but .Stat's
    ``refineTimePeriod`` reads these with ``parseISO`` and then shifts by the
    local offset (``dateWithTZ``), which is right only for a zoneless value —
    a ``Z`` would show 2021-01-01 as 2020-12-31 west of UTC. The data writers'
    ``_period_bounds`` says why. Only that: a well-formed date-time at a time
    dimension value's ``start``/``end``, lacking only its zone.
    """
    path = list(err.absolute_path)
    if err.validator != "format" or err.validator_value != "date-time" or len(path) < 6:
        return False
    *_, dimensions, _level, _position, values, _index, bound = path
    if (dimensions, values) != ("dimensions", "values") or bound not in ("start", "end"):
        return False
    if not isinstance(err.instance, str):
        return False
    try:
        return datetime.fromisoformat(err.instance).tzinfo is None
    except ValueError:
        return False


def assert_valid_sdmx_json(data: bytes | str | dict[str, Any], validator: Validator) -> None:
    """Validate *data* against *validator*. Raises a readable AssertionError.

    Accepts raw bytes (decoded as UTF-8), a JSON string, or an already-
    parsed dict. The error list is sorted by JSON path so failures
    surface in document order. A zoneless TIME_PERIOD bound is excused,
    and nothing else: see `is_declared_time_bound`.
    """
    if isinstance(data, bytes):
        parsed: Any = json.loads(data.decode())
    elif isinstance(data, str):
        parsed = json.loads(data)
    else:
        parsed = data

    errors = sorted(
        (e for e in validator.iter_errors(parsed) if not is_declared_time_bound(e)),
        key=lambda e: list(e.absolute_path),
    )
    if not errors:
        return

    lines = ["JSON failed SDMX-JSON schema validation:"]
    for err in errors:
        path = "/" + "/".join(str(p) for p in err.absolute_path) if err.absolute_path else "<root>"
        lines.append(f"  {path}: {err.message}")
    raise AssertionError("\n".join(lines))


def is_valid_sdmx_json(data: bytes, validator: Validator) -> bool:
    """Whether *data* validates against *validator*: for a test whose expectation the schema decides."""
    return validator.is_valid(json.loads(data))
