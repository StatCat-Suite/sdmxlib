"""Every way an artefact comes to exist refuses an invalid one (#492).

`validate()` bound only where something called it. The store reader did not,
so a bundle written past `add` held 498,786 blank names that no read ever saw.
Every rule now runs as an artefact is built, so the boundary is construction:
the constructor and `attrs.evolve`, and each reader, which builds what it
reads — each document reader and the store reader. This asserts each refuses
the same invalid artefact, a DSD whose dimension has no concept.

`add` and `serialize` no longer check it: they take what was built, and an
invalid artefact is built only inside `attrs.validators.disabled()` — which is
how the readers' cases below get one to write.

The readers are found, not listed: every ``sdmxlib.formats.<pkg>.reader``
defining ``read`` is a structure reader, and a new one fails
`test_every_structure_reader_has_a_case` until it has a case here, so a new
boundary cannot skip the check unnoticed.
"""

import importlib
import pkgutil
from collections.abc import Callable

import attrs
import duckdb
import pytest

import sdmxlib as sl
import sdmxlib.formats
from sdmxlib.formats import parse
from sdmxlib.formats.sdmx_json.writer import write as write_json
from sdmxlib.formats.sdmx_ml21.writer import write as write21
from sdmxlib.formats.sdmx_ml30.writer import write as write30
from sdmxlib.formats.sdmx_ml31.writer import write as write31
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import SdmxConformanceError, ValidationError
from sdmxlib.storage.schema import create_schema
from sdmxlib.storage.writers import put_artefact
from tests.support.factories import concept_ref
from tests.support.roundtrip import as_structure
from tests.support.unchecked import unchecked

pytestmark = pytest.mark.unit

_URN = sl.DataStructure.urn_for(agency="T", id="DSD", version="1.0")
_REASON = "ConceptIdentity is required"


def _build(*, valid: bool) -> sl.DataStructure:
    return sl.DataStructure(
        id="DSD",
        maintainer="T",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="FREQ", concept=concept_ref("FREQ") if valid else None)],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )


def _dsd(*, valid: bool) -> sl.DataStructure:
    """The DSD; the invalid one built unchecked, for a document or a store to hold."""
    if valid:
        return _build(valid=True)
    with unchecked():
        return _build(valid=False)


# ── construction ──────────────────────────────────────────────────────────────


def test_construction_refuses_it():
    _build(valid=True)
    with pytest.raises(ValidationError, match=_REASON):
        _build(valid=False)


def test_evolve_refuses_it():
    valid = _build(valid=True)
    with pytest.raises(ValidationError, match=_REASON):
        attrs.evolve(valid, dimensions=[sl.Dimension(id="FREQ")])


# ── the document readers ──────────────────────────────────────────────────────

#: Each structure reader, by the module that holds it, with the writer that
#: produces its documents without validating — no validating writer will.
_READERS: dict[str, tuple[str, Callable[[StructureMessage], bytes]]] = {
    "sdmxlib.formats.sdmx_ml21.reader": ("2.1", write21),
    "sdmxlib.formats.sdmx_ml30.reader": ("3.0", write30),
    "sdmxlib.formats.sdmx_ml31.reader": ("3.1", write31),
    "sdmxlib.formats.sdmx_json.reader": ("json", write_json),
}


def _structure_readers() -> set[str]:
    found: set[str] = set()
    for info in pkgutil.iter_modules(sdmxlib.formats.__path__, "sdmxlib.formats."):
        if not info.ispkg:
            continue
        try:
            module = importlib.import_module(f"{info.name}.reader")
        except ModuleNotFoundError:
            continue
        if callable(getattr(module, "read", None)):
            found.add(module.__name__)
    return found


def test_every_structure_reader_has_a_case():
    assert _structure_readers() == set(_READERS)


@pytest.mark.parametrize("module", sorted(_READERS))
def test_each_document_reader_refuses_it(module: str):
    version, write = _READERS[module]
    for valid in (True, False):
        msg = as_structure(
            parse(write(StructureMessage(data_structures=[_dsd(valid=valid)])), version=version, strict=False)
        )
        if valid:
            assert msg.refused == (), f"{version}: the valid twin was refused"
            assert msg.data_structures.ids() == ["DSD"]
        else:
            assert [r.urn for r in msg.refused] == [str(_URN)], version
            assert _REASON in msg.refused[0].reason


# ── the store reader ──────────────────────────────────────────────────────────


def test_the_store_reader_refuses_it():
    conn = create_schema(duckdb.connect(":memory:"))
    put_artefact(conn, _dsd(valid=False))
    with sl.LocalRegistry.from_connection(conn) as reg, pytest.raises(SdmxConformanceError, match=_REASON):
        reg.get(_URN)
