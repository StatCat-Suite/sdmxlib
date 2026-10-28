"""SDMX-CSV data reader: what names a file's structure.

A file names its own structure in its first row, and these read it without
reading on — `LocalRegistry.scan_csv` / `RestRegistry.scan_csv` resolve it and
read the file as an `ObsQuery` (`sdmxlib.local._scan`).

SDMX-CSV format::

    DATAFLOW,FREQ,REF_AREA,INDICATOR,TIME_PERIOD,OBS_VALUE,OBS_STATUS
    ESTAT:STS_INPR_M(1.0),M,AT,INDPRO,2024-01,98.5,A

The ``DATAFLOW`` column (1.0), or ``STRUCTURE`` / ``STRUCTURE_ID`` (2.0),
names the structure and is not a DSD component: an observation query reads
components only, so the envelope is never a column of its result.
"""

import csv
import io
import re
from pathlib import Path
from typing import Final

from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.errors import SdmxParseError
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref
from sdmxlib.model.registry import SupportsGet
from sdmxlib.model.urn import SdmxUrn

_DATAFLOW_COL = "DATAFLOW"
# SDMX-CSV 2.0.0 envelope columns that name the structure.
_STRUCTURE_COL = "STRUCTURE"
_STRUCTURE_ID_COL = "STRUCTURE_ID"
_DATAFLOW_RE = re.compile(r"^([^:]+):([^(]+)\(([^)]+)\)$")


def parse_dataflow_id(dataflow_id: str) -> tuple[str, str, str]:
    """Parse an SDMX-CSV DATAFLOW value into ``(agency, id, version)``.

    Example:
        parse_dataflow_id("ESTAT:STS_INPR_M(1.0)")  # ("ESTAT", "STS_INPR_M", "1.0")
    """
    m = _DATAFLOW_RE.match(dataflow_id)
    if m is None:
        msg = f"Cannot parse DATAFLOW value {dataflow_id!r} — expected 'AGENCY:ID(VERSION)'"
        raise ValueError(msg)
    return m.group(1), m.group(2), m.group(3)


#: The values SDMX-CSV 2.0's ``STRUCTURE`` column may carry.
#:
#: Verbatim from the field guide: "The first column contains: ``dataflow``,
#: ``datastructure`` or ``dataprovision``". Worth stating because two of our own
#: docstrings said ``provisionagreement`` — which is the RFC 5988 *link
#: relation* SDMX-JSON uses, a different vocabulary — and a caller following
#: them wrote a non-conformant file. sdmx1's CSV reader enumerates the same
#: three (#409).
STRUCTURE_KINDS: Final[tuple[str, ...]] = ("dataflow", "datastructure", "dataprovision")


def _peek_dataflow_id(source: str | Path | bytes) -> str | None:
    """Extract the DATAFLOW value from the first data row without reading the whole file."""
    if isinstance(source, (str, Path)):
        with Path(source).open(newline="", encoding="utf-8") as f:
            head = f.read(4096)
    else:
        head = source[:4096].decode("utf-8", errors="replace")

    reader = csv.reader(io.StringIO(head))
    try:
        header = next(reader)
    except StopIteration:
        return None
    df_idx: int | None = None
    for col in (_DATAFLOW_COL, _STRUCTURE_ID_COL):
        if col in header:
            df_idx = header.index(col)
            break
    if df_idx is None:
        return None
    try:
        first_row = next(reader)
    except StopIteration:
        return None
    return first_row[df_idx] if df_idx < len(first_row) else None


def _peek_structure(source: str | Path | bytes) -> tuple[str | None, str | None]:
    """``(STRUCTURE, STRUCTURE_ID)`` from the first data row, without reading on.

    The kind matters: an SDMX-CSV file may name a dataflow, a data structure or
    a provision agreement, and resolving all three is the difference between
    honouring the column and assuming what it says (#409). SDMX-CSV 1.0 has no
    ``STRUCTURE`` column at all — its ``DATAFLOW`` implies the kind — so the
    first element is None there and the caller treats it as a dataflow.
    """
    try:
        if isinstance(source, (str, Path)):
            with Path(source).open(newline="", encoding="utf-8") as f:
                head = f.read(4096)
        else:
            head = source[:4096].decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(head))
        header = next(reader)
        first_row = next(reader)
    except StopIteration:
        return None, None
    except (csv.Error, UnicodeDecodeError) as exc:
        msg = f"the SDMX-CSV header could not be read: {exc}"
        raise SdmxParseError(msg) from exc

    def value(column: str) -> str | None:
        if column not in header:
            return None
        index = header.index(column)
        return first_row[index] if index < len(first_row) else None

    kind = value(_STRUCTURE_COL)
    identifier = value(_STRUCTURE_ID_COL) or value(_DATAFLOW_COL)
    return kind, identifier


def _dataflow_ref(dataflow_str: str) -> Ref[Dataflow]:
    """Build an unresolved Ref[Dataflow] from an SDMX-CSV DATAFLOW column value."""
    agency, id_, version = parse_dataflow_id(dataflow_str)
    urn = SdmxUrn.make(
        Dataflow,
        package="datastructure",
        artefact_class="Dataflow",
        agency=agency,
        id=id_,
        version=version,
    )
    return Ref[Dataflow].unresolved(urn)


def _resolve_structure(
    source: str | Path | bytes,
    registry: SupportsGet,
) -> tuple[DataStructure, Ref[Dataflow] | None]:
    """The `DataStructure` this file names, looked up in *registry*.

    Every SDMX-CSV 2.0 row names its own structure, and this reader has always
    parsed that column and then thrown it away — `scan_csv` required the DSD as
    a positional argument, so the file's answer was only available after you had
    supplied it. #409 is that asymmetry.

    All three kinds are honoured rather than assuming `dataflow`, which is what
    "only `dataflow` appears to be considered" described. A provision agreement
    resolves through its dataflow, which is the same second hop a dataflow makes
    to its structure.

    Raises:
        ValueError: If the file names no structure (no ``STRUCTURE_ID`` and no
            ``DATAFLOW`` column), if its ``STRUCTURE`` value is not one of
            `STRUCTURE_KINDS`, if the registry cannot resolve what it names, or
            if what it resolves to has no structure to read. Four distinct
            messages, because a caller's next move differs for each.
        TypeError: If the URN resolves to something that is not a dataflow,
            provision agreement or data structure at all.
    """
    kind, identifier = _peek_structure(source)
    if identifier is None:
        msg = (
            "this file names no structure: no STRUCTURE_ID column (SDMX-CSV 2.0) "
            "and no DATAFLOW column (1.0). Pass structure=<DataStructure> instead"
        )
        raise ValueError(msg)
    if kind is not None and kind not in STRUCTURE_KINDS:
        msg = f"unknown STRUCTURE value {kind!r} — SDMX-CSV allows {', '.join(STRUCTURE_KINDS)}"
        raise ValueError(msg)

    agency, id_, version = parse_dataflow_id(identifier)
    artefact_class = {"datastructure": DataStructure, "dataprovision": ProvisionAgreement}.get(
        kind or "dataflow", Dataflow
    )
    urn = artefact_class.urn_for(agency=agency, id=id_, version=version)
    found: object = registry.get(urn)
    if found is None:
        msg = f"{kind or 'dataflow'} {identifier} is not in this registry — add it, or pass structure=<DataStructure>"
        raise ValueError(msg)

    flow_ref: Ref[Dataflow] | None = None
    if isinstance(found, ProvisionAgreement):
        if found.dataflow is None:
            msg = f"provision agreement {identifier} has no dataflow, so its structure cannot be reached"
            raise ValueError(msg)
        flow_ref = found.dataflow
        found = found.dataflow()
    if isinstance(found, Dataflow):
        flow_ref = flow_ref or _dataflow_ref(identifier)
        if found.structure is None:
            msg = f"dataflow {identifier} has no structure — it cannot say how to read this file"
            raise ValueError(msg)
        found = found.structure()
    if not isinstance(found, DataStructure):
        # A `TypeError`, per ruff's TRY004 and correctly: the registry answered
        # with the wrong *kind* of thing, which is a different fault from the
        # file naming something absent.
        msg = f"{identifier} resolved to {type(found).__name__}, which is not a DataStructure"
        raise TypeError(msg)
    return found, flow_ref
