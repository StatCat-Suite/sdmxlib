"""Two implementations of the Inventory protocol for the conformance suite (GH-252).

- `FileBackend`: the spec's file protocol over a local directory, written by
  the reference writer in `tests.support.inventory` and read by sdmxlib's
  `FullInventory`.
- `DeltaBackend`: a prototype holding each Inventory version as one commit of
  a Delta table of Inventory rows (delta-rs, put-if-absent commits). A test
  fixture that proves the spec's escape route (§6.6), not a supported backend.
"""

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

import msgspec
import pyarrow as pa
import pyarrow.parquet as pq
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import CommitFailedError, DeltaError, TableNotFoundError

from sdmxlib.inventory import (
    CATALOG_NAME,
    Event,
    EventKind,
    FullInventory,
    InventoryError,
    Leaf,
    Root,
    Tombstone,
    decode_catalog,
    decode_leaf,
    decode_root,
    encode,
    leaf_ref,
    local_fetch,
    next_root,
    root_path,
    sha256_hex,
)
from sdmxlib.inventory.conformance import ConflictError, Reader, Snapshot
from tests.support.inventory import NOW, PRODUCER, LostRaceError, create_exclusive, new_catalog, set_current


def _snapshot(inventory: FullInventory) -> Snapshot:
    return Snapshot(
        seq=inventory.seq,
        restore_epoch=inventory.restore_epoch,
        embargo=inventory.embargo,
        leaves=dict(inventory.leaves()),
        retired=inventory.retired,
    )


class _FileReader:
    def __init__(self, root: Path) -> None:
        self._inventory = FullInventory(local_fetch(root))

    def refresh(self) -> Snapshot:
        return _snapshot(self._inventory.refresh())


class FileBackend:
    """The spec's file protocol over a local directory."""

    def __init__(self, root: Path) -> None:
        """Create an empty catalog at `root`."""
        self.root = root
        new_catalog(root)

    def head(self) -> Snapshot | None:
        try:
            return _snapshot(FullInventory(local_fetch(self.root)).refresh())
        except InventoryError:
            return None

    def _root(self, seq: int) -> Root:
        return decode_root((self.root / root_path(seq)).read_bytes())

    def _next(
        self,
        base: Snapshot | None,
        leaves: Mapping[str, Leaf],
        *,
        retired: Sequence[Tombstone] | None,
        embargo: bool | None,
        kind: EventKind,
        restore_epoch: int | None = None,
    ) -> Root:
        previous = None if base is None else self._root(base.seq)
        products = dict(previous.products) if previous is not None else {}
        for product, leaf in leaves.items():
            data, ref = leaf_ref(leaf)
            path = self.root / ref.leaf
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(data)
            products[product] = ref
        return next_root(
            previous,
            products=products,
            event=Event(actor="conformance", reason="test", kind=kind),
            producer=PRODUCER,
            now=NOW,
            retired=retired,
            embargo=embargo,
            restore_epoch=restore_epoch,
        )

    def commit(
        self,
        base: Snapshot | None,
        leaves: Mapping[str, Leaf],
        *,
        retired: Sequence[Tombstone] | None = None,
        embargo: bool | None = None,
        kind: EventKind = "seal",
    ) -> Snapshot:
        root = self._next(base, leaves, retired=retired, embargo=embargo, kind=kind)
        return self._publish(root)

    def _publish(self, root: Root) -> Snapshot:
        data = encode(root)
        try:
            create_exclusive(self.root / root_path(root.seq), data)
        except LostRaceError as exc:
            raise ConflictError(str(exc)) from exc
        set_current(self.root, root.seq, sha256_hex(data), root.restore_epoch)
        head = self.head()
        if head is None:
            msg = "the version just written does not read"
            raise AssertionError(msg)
        return head

    def commit_interrupted(self, base: Snapshot | None, leaves: Mapping[str, Leaf]) -> None:
        root = self._next(base, leaves, retired=None, embargo=None, kind="seal")
        data = encode(root)
        target = self.root / root_path(root.seq)
        # Killed after writing part of the staged file, before the link that publishes it.
        staged = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        staged.write_bytes(data[: len(data) // 2])

    def reader(self) -> Reader:
        return _FileReader(self.root)

    def hint_behind(self) -> None:
        head = self.head()
        if head is None or head.seq < 2:
            return
        older = self.root / root_path(head.seq - 1)
        set_current(self.root, head.seq - 1, sha256_hex(older.read_bytes()), head.restore_epoch)

    def restore_to(self, seq: int) -> None:
        later = seq + 1
        while (self.root / root_path(later)).exists():
            (self.root / root_path(later)).unlink()
            later += 1
        kept = self._root(seq)
        set_current(self.root, seq, sha256_hex((self.root / root_path(seq)).read_bytes()), kept.restore_epoch)

    def restore_with_new_epoch(self, seq: int) -> None:
        self.restore_to(seq)
        catalog = decode_catalog((self.root / CATALOG_NAME).read_bytes())
        epoch = catalog.restore_epoch + 1
        (self.root / CATALOG_NAME).write_bytes(encode(msgspec.structs.replace(catalog, restore_epoch=epoch)))
        base = self.head()
        root = self._next(base, {}, retired=None, embargo=None, kind="restore", restore_epoch=epoch)
        self._publish(root)


#: The row carrying a version's root fields; every other row is one product's leaf.
_META = ""


class _DeltaReader:
    def __init__(self, backend: DeltaBackend) -> None:
        self._backend = backend
        self._held: Snapshot | None = None

    def refresh(self) -> Snapshot:
        found = self._backend.head()
        held = self._held
        if found is None or (held is not None and found.restore_epoch == held.restore_epoch and found.seq < held.seq):
            if held is None:
                msg = "no Inventory version"
                raise InventoryError(msg)
            return held
        self._held = found
        return found


class DeltaBackend:
    """Inventory versions as commits of a Delta table of rows: one row per product's leaf, one for the root."""

    def __init__(self, path: Path) -> None:
        """A table at `path`, created by the first commit."""
        self.path = path

    def _table(self, version: int | None = None) -> DeltaTable | None:
        try:
            return DeltaTable(str(self.path), version=version)
        except TableNotFoundError:
            return None

    def head(self) -> Snapshot | None:
        table = self._table()
        if table is None:
            return None
        rows = table.to_pyarrow_table().to_pylist()
        meta = json.loads(next(r["leaf"] for r in rows if r["product_id"] == _META))
        return Snapshot(
            seq=table.version() + 1,
            restore_epoch=meta["restore_epoch"],
            embargo=meta["embargo"],
            leaves={r["product_id"]: decode_leaf(r["leaf"].encode()) for r in rows if r["product_id"] != _META},
            retired=msgspec.convert(meta["retired"], tuple[Tombstone, ...]),
        )

    def _rows(self, snapshot: Snapshot) -> pa.Table:
        meta = {
            "embargo": snapshot.embargo,
            "restore_epoch": snapshot.restore_epoch,
            "retired": msgspec.to_builtins(snapshot.retired),
        }
        products = [_META, *sorted(snapshot.leaves)]
        leaves = [json.dumps(meta), *(encode(snapshot.leaves[p]).decode() for p in sorted(snapshot.leaves))]
        return pa.table({"product_id": products, "leaf": leaves})

    def _write(self, base: Snapshot | None, state: Snapshot) -> Snapshot:
        try:
            if base is None:
                write_deltalake(str(self.path), self._rows(state), mode="error")
            else:
                table = self._table(base.seq - 1)
                if table is None:
                    raise ConflictError(str(self.path))
                write_deltalake(table, self._rows(state), mode="overwrite")
        except (CommitFailedError, DeltaError) as exc:
            raise ConflictError(str(exc)) from exc
        head = self.head()
        if head is None:
            msg = "the commit just written does not read"
            raise AssertionError(msg)
        return head

    def commit(
        self,
        base: Snapshot | None,
        leaves: Mapping[str, Leaf],
        *,
        retired: Sequence[Tombstone] | None = None,
        embargo: bool | None = None,
        kind: EventKind = "seal",
    ) -> Snapshot:
        del kind  # a Delta commit records its own operation
        state = Snapshot(
            seq=1 if base is None else base.seq + 1,
            restore_epoch=0 if base is None else base.restore_epoch,
            embargo=embargo if embargo is not None else (base.embargo if base is not None else False),
            leaves={**(base.leaves if base is not None else {}), **leaves},
            retired=tuple(retired) if retired is not None else (base.retired if base is not None else ()),
        )
        return self._write(base, state)

    def commit_interrupted(self, base: Snapshot | None, leaves: Mapping[str, Leaf]) -> None:
        del base
        # Killed after writing a data file, before the log entry that commits it.
        rows = pa.table({"product_id": list(leaves), "leaf": [encode(leaf).decode() for leaf in leaves.values()]})
        self.path.mkdir(parents=True, exist_ok=True)
        pq.write_table(rows, self.path / "part-00000-interrupted.parquet")

    def reader(self) -> _DeltaReader:
        return _DeltaReader(self)

    def hint_behind(self) -> None:
        """A Delta reader lists its log rather than trusting a hint, so there is no hint to lag."""

    def restore_to(self, seq: int) -> None:
        log = self.path / "_delta_log"
        for entry in log.iterdir():
            if entry.suffix == ".json" and entry.stem.isdigit() and int(entry.stem) >= seq:
                entry.unlink()

    def restore_with_new_epoch(self, seq: int) -> None:
        self.restore_to(seq)
        base = self.head()
        if base is None:
            msg = "nothing left after the restore"
            raise AssertionError(msg)
        state = msgspec.structs.replace(base, seq=base.seq + 1, restore_epoch=base.restore_epoch + 1)
        self._write(base, state)
