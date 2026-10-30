"""The Inventory protocol's conformance suite, against the file protocol and a Delta prototype (GH-252)."""

import itertools
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import override

import pytest

from sdmxlib.inventory import EventKind, Leaf, Tombstone
from sdmxlib.inventory.conformance import CHECKS, Backend, Snapshot, run
from tests.support.inventory_backends import DeltaBackend, FileBackend

pytestmark = pytest.mark.unit

_KINDS: dict[str, Callable[[Path], Backend]] = {"file": FileBackend, "delta": DeltaBackend}


@pytest.mark.parametrize("kind", list(_KINDS))
@pytest.mark.parametrize("check", CHECKS, ids=[c.__name__.removeprefix("check_") for c in CHECKS])
def test_the_backend_conforms(kind: str, check: Callable[[Callable[[], Backend]], None], tmp_path: Path) -> None:
    counter = itertools.count()
    check(lambda: _KINDS[kind](tmp_path / f"catalog-{next(counter)}"))


def test_run_runs_every_check(tmp_path: Path) -> None:
    counter = itertools.count()
    assert run(lambda: FileBackend(tmp_path / f"c{next(counter)}")) == [c.__name__ for c in CHECKS]


class _NoConflict(FileBackend):
    """A sabotaged backend whose commits never conflict: the last writer silently wins."""

    @override
    def commit(
        self,
        base: Snapshot | None,
        leaves: Mapping[str, Leaf],
        *,
        retired: Sequence[Tombstone] | None = None,
        embargo: bool | None = None,
        kind: EventKind = "seal",
    ) -> Snapshot:
        return super().commit(self.head(), leaves, retired=retired, embargo=embargo, kind=kind)


class _HeadReader:
    def __init__(self, backend: FileBackend) -> None:
        self._backend = backend

    def refresh(self) -> Snapshot:
        found = self._backend.head()
        assert found is not None
        return found


class _GoesBack(FileBackend):
    """A sabotaged backend whose reader takes whatever is newest on storage, lower or not."""

    @override
    def reader(self) -> _HeadReader:
        return _HeadReader(self)


@pytest.mark.parametrize(
    ("sabotaged", "failing"),
    [
        (_NoConflict, "check_a_stale_commit_is_refused"),
        (_GoesBack, "check_a_reader_finds_newer_versions_and_never_goes_back"),
    ],
)
def test_a_backend_breaking_the_protocol_fails_its_check(
    sabotaged: type[FileBackend], failing: str, tmp_path: Path
) -> None:
    """Discrimination: each check can fail."""
    check = next(c for c in CHECKS if c.__name__ == failing)
    counter = itertools.count()
    with pytest.raises(AssertionError):
        check(lambda: sabotaged(tmp_path / f"c{next(counter)}"))
