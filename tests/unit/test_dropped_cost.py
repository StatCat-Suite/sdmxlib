"""What leaving out a format's unholdable artefacts costs a write (#464).

`gaps.dropped` walks every reference in a message to find what the target
format cannot hold. On BIS's DSDs written as SDMX-ML 2.1 it was 9.5 of 16 ms,
for two reasons, each guarded here by a count:

- it ran twice per `serialize`, once before validating and again in the
  writer, on a message the first run had already narrowed;
- each reference it looked up walked the class tree of every
  `MaintainableArtefact` again, 1,116 times per write.
"""

import gc

import pytest

from sdmxlib import formats
from sdmxlib.formats import gaps
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.model import _items
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.message import StructureMessage
from tests.support import maximal

pytestmark = pytest.mark.unit


@pytest.fixture
def drops(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The target of each `gaps.dropped` call, as it is made."""
    targets: list[str] = []
    original = gaps.dropped

    def counting(msg: StructureMessage, target: str) -> tuple[gaps.DroppedArtefact, ...]:
        targets.append(target)
        return original(msg, target)

    monkeypatch.setattr(gaps, "dropped", counting)
    return targets


@pytest.mark.parametrize("target", ["2.1", "3.0", "3.1", "json"])
def test_serialize_works_out_what_to_leave_out_once(drops: list[str], target: str) -> None:
    formats.serialize(maximal.message(), version=target, validate=False)
    assert drops == [target]


def test_a_writer_called_directly_still_leaves_out_what_it_cannot_hold(drops: list[str]) -> None:
    """The discrimination check: `write` drops, so a second drop under `serialize` would be counted."""
    ml21_writer.write(maximal.message())
    assert drops == ["2.1"]


def test_a_write_walks_the_artefact_classes_once() -> None:
    _items._subclasses.cache_clear()
    formats.serialize(maximal.message(), version="2.1", validate=False)
    info = _items._subclasses.cache_info()
    assert info.misses == 1, f"the class tree was walked {info.misses} times"
    assert info.hits > 0, "the lookups must have asked, or the count above says nothing"


def test_a_class_defined_later_is_seen() -> None:
    """The cache must not freeze a partial answer, which was #405.

    An import that defines a new artefact class has to reach the next lookup.
    The probe class is dropped again afterwards, with the cache that holds it,
    so no other test sees it: deleting a class defines nothing, so the cache
    cannot tell (see `_maintainable_classes`).
    """
    before = _items._maintainable_classes()

    class LaterArtefact(MaintainableArtefact):
        pass

    try:
        assert LaterArtefact in _items._maintainable_classes()
        assert LaterArtefact not in before
    finally:
        del LaterArtefact
        _items._subclasses.cache_clear()
        gc.collect()
    assert all(cls.__name__ != "LaterArtefact" for cls in _items._maintainable_classes())
