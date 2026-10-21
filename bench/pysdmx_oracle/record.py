"""Record what pysdmx makes of our SDMX-ML 3.1 documents, for the replay test to check (#457).

pysdmx is the 3.1 oracle because FMR 12.4.2 reads no 3.1. Per document it
records:

- ``sent.xml``, the adjusted document pysdmx was given;
- ``necessity.json``, pysdmx's failure on the document with each adjustment
  left out in turn -- the evidence that each one is still needed;
- ``read.json``, how many artefacts of each of its classes pysdmx read, since
  its reader skips a class it does not model without saying so;
- ``back.xml``, what pysdmx's writer then makes of its reading, in SDMX-ML 3.1.

And once, ``pysdmx.json``: the pysdmx version.

pysdmx is the optional `bench-compete` group, imported only here, as
`bench.compete.libs` does.
"""

import json
from collections import Counter
from importlib import import_module
from pathlib import Path
from typing import Protocol

from bench.pysdmx_oracle import documents
from bench.pysdmx_oracle.ledger import Document
from tests.support.repo import find_root

#: Where the recordings live, one directory per pysdmx version.
RECORDINGS = find_root() / "tests" / "integration" / "pysdmx"


class _Message(Protocol):
    structures: list[object]


def roundtrip(document: bytes) -> tuple[dict[str, int], bytes]:
    """pysdmx's reading of `document`, by class, and its writer's SDMX-ML 3.1 of that reading.

    pysdmx validates its input against its own copy of the schema, as it does by default.
    """
    message: _Message = import_module("pysdmx.io").read_sdmx(document.decode())
    read = Counter(type(structure).__name__ for structure in message.structures)
    ml31: object = import_module("pysdmx.io.format").Format.STRUCTURE_SDMX_ML_3_1
    written: str = import_module("pysdmx.io").write_sdmx(message.structures, ml31)
    return dict(sorted(read.items())), written.encode()


def failure(document: bytes) -> str | None:
    """How pysdmx fails to read and write `document` back, if it does.

    Its own errors, and the two its reader raises on a key it does not expect
    (an unknown keyword to a model class, or a 2.1 shape it looks for in 3.1).
    Anything else propagates, to be looked at rather than recorded.
    """
    pysdmx_error: type[Exception] = import_module("pysdmx.errors").PysdmxError
    try:
        roundtrip(document)
    except (TypeError, KeyError, pysdmx_error) as error:
        return f"{type(error).__name__}: {error}"[:500]
    return None


def _dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record_case(out: Path, document: Document) -> None:
    """One document: necessity of each adjustment, then what pysdmx read and wrote."""
    _dump(
        out / "necessity.json",
        [
            {"xpath": a.xpath, "failure": failure(documents.adjusted(document, without=a))}
            for a in documents.adjustments(document)
        ],
    )
    sent = documents.adjusted(document)
    read, back = roundtrip(sent)
    (out / "sent.xml").write_bytes(sent)
    (out / "back.xml").write_bytes(back)
    _dump(out / "read.json", read)
    print(f"3.1 {document}: pysdmx read {sum(read.values())} artefacts, wrote {len(back)} bytes")


def record() -> Path:
    """Record every document; the directory written."""
    version: str = import_module("pysdmx").__version__
    out = RECORDINGS / version
    for document in documents.DOCUMENTS:
        record_case(out / f"3.1-{document}", document)
    _dump(out / "pysdmx.json", {"version": version})
    return out
