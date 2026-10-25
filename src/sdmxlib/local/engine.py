"""One DuckDB engine that many published bundles attach into (GH-133).

A server holding several bundles open keeps one buffer pool, one thread pool
and one ``memory_limit`` for all of them: one `BundleEngine`, passed to
``LocalRegistry.bundle(path, engine=engine)``. Without one, each bundle gets
an engine of its own, so a bundle always opens the same way.

Each publication attaches under a prefix, ``<bundle id>__<token>``: its
``meta.duckdb`` as ``<prefix>__meta`` and its DuckLake catalog, when
something first reads observations, as ``<prefix>__lake``. A registry's
handles select its own ``<prefix>__meta`` catalog with ``USE``, so the
``sdmx.<table>`` names every query spells read that bundle and no other. The
same publication opened twice shares one attachment, counted, and is detached
when the last registry over it closes.

A publication whose data files are in object storage reads them through the
engine (GH-180): `secrets` holds the stores' credentials, and the extension a
store's scheme needs is loaded when a bundle first names it.
"""

import os
import re
import tempfile
import threading
from collections.abc import Iterable
from pathlib import Path

import attrs
import duckdb

from sdmxlib.local.object_store import SECRET_EXTENSIONS, StoreSecret, extension_for, load_for, sql_literal
from sdmxlib.storage.schema import use_sdmx

#: What a bundle id keeps in an alias; anything else becomes ``_``.
_UNSAFE = re.compile(r"[^A-Za-z0-9_]")
#: A DuckDB memory setting: a number and a unit, or a percentage.
_MEMORY = re.compile(r"^\d+(\.\d+)?\s*(B|KB|KiB|MB|MiB|GB|GiB|TB|TiB|%)$", re.IGNORECASE)


def spill_directory() -> Path:
    """Where a read-only database spills: under the system temp, one directory per process."""
    return Path(tempfile.gettempdir()) / f"sdmxlib-spill-{os.getpid()}"


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


@attrs.define
class _Attached:
    meta: Path
    holders: int


class BundleEngine:
    """A DuckDB engine shared by the bundles a process holds open.

    Thread-safe: attaching and releasing hold a lock, and each registry reads
    on handles of its own.
    """

    def __init__(
        self,
        *,
        memory_limit: str | None = None,
        threads: int | None = None,
        secrets: Iterable[StoreSecret] = (),
    ) -> None:
        """Start an engine with no bundle attached.

        Args:
            memory_limit: DuckDB's ``memory_limit`` for every bundle together,
                such as ``"8GB"`` or ``"70%"``; DuckDB's default when None.
            threads: DuckDB's thread pool size; DuckDB's default when None.
            secrets: The credentials for the object stores the engine's
                bundles read their data files from, created as DuckDB secrets
                with the extension each needs.

        Raises:
            ValueError: If `memory_limit` is not a size or `threads` is below 1.
        """
        if memory_limit is not None and not _MEMORY.match(memory_limit):
            msg = f"memory_limit {memory_limit!r} is not a size such as '8GB' or '70%'"
            raise ValueError(msg)
        if threads is not None and threads < 1:
            msg = f"threads must be at least 1, got {threads}"
            raise ValueError(msg)
        self._conn = duckdb.connect()
        # Published bundles are never written, so the engine spills under the
        # system temp, one directory per process, as a read-only store does.
        self._conn.execute(f"SET temp_directory = {sql_literal(str(spill_directory()))}")
        if memory_limit is not None:
            self._conn.execute(f"SET memory_limit = {sql_literal(memory_limit)}")
        if threads is not None:
            self._conn.execute(f"SET threads = {threads}")
        self._lock = threading.Lock()
        self._attached: dict[str, _Attached] = {}
        #: The extensions loaded on the engine's database, so each loads once.
        self._loaded: set[str | None] = {None}
        for secret in secrets:
            self._load(SECRET_EXTENSIONS[secret.type])
            self._conn.execute(secret.statement())

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """The engine's base connection, which every bundle's handles derive from."""
        return self._conn

    @property
    def attached(self) -> tuple[str, ...]:
        """The prefixes of the publications attached now, sorted."""
        with self._lock:
            return tuple(sorted(self._attached))

    def attach(self, root: Path, *, bundle_id: str, token: str) -> str:
        """Attach the publication at `root`, or count one more holder of it, and return its prefix.

        Args:
            root: The publication directory.
            bundle_id: Its manifest's ``bundle_id``.
            token: Its manifest's ``token``.

        Returns:
            ``<bundle_id>__<token>``, with every character an alias cannot hold
            made ``_``: the prefix of its catalogs' aliases.

        Raises:
            ValueError: If the prefix is attached to another publication's files.
        """
        prefix = f"{_UNSAFE.sub('_', bundle_id)}__{_UNSAFE.sub('', token)}"
        meta = (root / "meta.duckdb").resolve()
        with self._lock:
            held = self._attached.get(prefix)
            if held is not None:
                if held.meta != meta:
                    msg = f"{prefix} is attached from {held.meta}; this registry wants {meta}"
                    raise ValueError(msg)
                held.holders += 1
                return prefix
            self._conn.execute(f"ATTACH {sql_literal(str(meta))} AS {_identifier(prefix + '__meta')} (READ_ONLY)")
            self._attached[prefix] = _Attached(meta=meta, holders=1)
        return prefix

    def load_for_uri(self, uri: str) -> None:
        """Load the DuckDB extension that reads `uri`'s scheme, once per engine.

        Raises:
            ValueError: If `uri` has no scheme, or one DuckDB does not read.
        """
        extension = extension_for(uri)
        with self._lock:
            self._load(extension)

    def _load(self, extension: str | None) -> None:
        if extension not in self._loaded:
            load_for(self._conn, extension)
            self._loaded.add(extension)

    def cursor(self, prefix: str) -> duckdb.DuckDBPyConnection:
        """A new handle reading the ``sdmx`` schema of the publication attached as `prefix`."""
        return use_sdmx(self._conn.cursor(), prefix + "__meta")

    def release(self, prefix: str) -> None:
        """Count one holder of `prefix` gone, and detach its catalogs when none is left."""
        with self._lock:
            held = self._attached.get(prefix)
            if held is None:
                return
            held.holders -= 1
            if held.holders > 0:
                return
            del self._attached[prefix]
            self._conn.execute(f"DETACH DATABASE IF EXISTS {_identifier(prefix + '__lake')}")
            self._conn.execute(f"DETACH DATABASE IF EXISTS {_identifier(prefix + '__meta')}")

    def close(self) -> None:
        """Close the engine and every handle on it."""
        self._conn.close()
