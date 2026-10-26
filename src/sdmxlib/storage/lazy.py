"""LazyCodelist + LazyHierarchy — DuckDB-backed lazy artefact access.

Codes and hierarchy placements are exposed as `Query` — the path language —
pre-filtered to this artefact.
Hierarchy navigation (``roots``, ``leaves``, ``narrower``,
``narrower_transitive``, ``broader``, ``broader_transitive``,
``depth_of``) compiles to a single SQL statement against the
appropriate persisted table (``sdmx.code`` or ``sdmx.hierarchy_node``).
"""

import threading
from collections.abc import Collection, Iterable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, Final, Self

import duckdb

from sdmxlib._duckdb import count
from sdmxlib.model._tree import refuse_bare_id
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.codelist import (
    Code,
    Codelist,
    CodelistExtension,
    codelist_of_kind,
    partial_ids,
    unknown_codes_message,
)
from sdmxlib.model.collections import ItemList
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, Level
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from sdmxlib.model.rollup import AncestorMap
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.version import is_final
from sdmxlib.query import accessors as q_accessors
from sdmxlib.query._expr import OrderKey
from sdmxlib.query.accessors import CodeRow, HCodeRow
from sdmxlib.query.query import Query, build
from sdmxlib.session import cursor_for as _cur
from sdmxlib.storage import resolve
from sdmxlib.storage._checked import checked
from sdmxlib.storage._projections import code_map


def _json_to_istring(value: str | None) -> InternationalString:
    """Re-exported from `resolve` — see `resolve.istring_sql` for why (#380)."""
    return resolve.json_to_istring(value)


def _artefacts[T](rows: Sequence[object], kind: type[T]) -> list[T]:
    """Narrow a resolver's rows to the artefact type this relation builds.

    `Query[R]` is generic over the *row kind* and not over what its resolver
    builds, so `collect()` is `list[object]`. Which resolver a relation gets is
    wired one line from where the relation is named — `q.code` with
    `resolve_code_keys` — so this is a runtime check of what the wiring already
    guarantees, not a cast. If the two ever disagree it fails here, naming both,
    rather than as an `AttributeError` in a caller's code.
    """
    for row in rows:
        if not isinstance(row, kind):
            msg = f"resolver returned {type(row).__name__}, expected {kind.__name__}"
            raise TypeError(msg)
    return [row for row in rows if isinstance(row, kind)]


class LazyCodelist:
    """Codelist facade backed by DuckDB.

    Duck-types as ``Codelist`` for its scalar read surface — ``id``, ``name``,
    ``agency``, ``version`` and the rest.

    **``codes`` is where it deliberately differs.** It returns a `Query`,
    a lazy plan for SQL-pushed filtering and hierarchy navigation, where a
    plain `Codelist` returns a materialised `ItemList`. That plan is
    not iterable, has no ``len()`` and answers no ``in``: those ran SQL without
    saying so, which is what stops a planner pushing anything down.

    So ``for c in cl.codes`` is ``for c in cl.codes.collect()`` here, and
    ``len(cl.codes)`` is ``cl.codes.count()`` — the second being the point
    rather than the cost, since a length used to build every `Code` in order to
    throw them all away. On CL_GEOGRAPHY that is 78k objects for one integer.
    """

    __slots__ = (
        "__weakref__",
        "_agency",
        "_code_map_cache",
        "_code_map_lock",
        "_conn",
        "_description",
        "_id",
        "_is_external_reference",
        "_is_partial",
        "_is_partial_language",
        "_legacy_is_final",
        "_name",
        "_service_url",
        "_structure_url",
        "_uri",
        "_urn",
        "_valid_from",
        "_valid_to",
        "_version",
    )

    def __init__(
        self,
        *,
        urn: str,
        conn: duckdb.DuckDBPyConnection,
        id: str,
        maintainer: Agency,
        version: str,
        name: InternationalString,
        description: InternationalString,
        legacy_is_final: bool | None,
        uri: str | None = None,
        is_external_reference: bool = False,
        is_partial_language: bool = False,
        service_url: str | None = None,
        structure_url: str | None = None,
        is_partial: bool = False,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
    ) -> None:
        self._urn = urn
        self._conn = conn
        self._id = id
        self._agency = maintainer
        self._version = version
        self._name = name
        self._description = description
        self._legacy_is_final = legacy_is_final
        self._uri = uri
        self._is_external_reference = is_external_reference
        self._is_partial_language = is_partial_language
        self._service_url = service_url
        self._structure_url = structure_url
        self._is_partial = is_partial
        self._valid_from = valid_from
        self._valid_to = valid_to
        # Per-instance, per-language label-map cache + single-flight build
        # lock (#155). The lock collapses the thundering herd: N threads
        # asking the same instance for the same lang run the SQL once, not
        # N times. Cache lifetime is the instance's — a fresh ingest yields
        # a new LazyCodelist, so re-ingestion is automatically correct.
        self._code_map_cache: dict[str, dict[str, str]] = {}
        self._code_map_lock = threading.Lock()

    # ── MaintainableArtefact-compatible properties ────────────────────────

    @property
    def urn(self) -> SdmxUrn[Codelist]:
        """This artefact's URN — its identity, as on an eager `Codelist`.

        Parsed rather than held typed because the row stores it as text. Added
        when the upgrade guide's own `cl.urn` example — on the artefact a
        `LocalRegistry` hands back — turned out to raise `AttributeError`.
        """
        return Codelist.parse_urn(self._urn)

    @property
    def id(self) -> str:
        """The artefact's identifier, unique within its agency and version."""
        return self._id

    @property
    def maintainer(self) -> Agency:
        """The `Agency` responsible for maintaining this artefact."""
        return self._agency

    @property
    def agency_id(self) -> str:
        """Identifier of the maintaining agency. Shorthand for ``maintainer.id``."""
        return self._agency.id

    @property
    def version(self) -> str:
        """Semantic version string, e.g. ``"1.0"``."""
        return self._version

    @property
    def name(self) -> InternationalString:
        """Human-readable name, keyed by language code."""
        return self._name

    @property
    def description(self) -> InternationalString:
        """Longer human-readable description, keyed by language code."""
        return self._description

    @property
    def is_final(self) -> bool:
        """Whether the version names a final artefact. See `MaintainableArtefact.is_final`."""
        return is_final(self._version)

    @property
    def legacy_is_final(self) -> bool | None:
        """SDMX 2.1's `isFinal` where it disagrees with the version. See `Codelist.legacy_is_final`."""
        return self._legacy_is_final

    @property
    def uri(self) -> str | None:
        """External link describing this artefact, or None. See `MaintainableArtefact.uri`."""
        return self._uri

    @property
    def is_external_reference(self) -> bool:
        """Whether this is a stub for an artefact held elsewhere. See `MaintainableArtefact`."""
        return self._is_external_reference

    @property
    def is_partial_language(self) -> bool:
        """Whether some text lacks a language the rest carries. See `MaintainableArtefact`."""
        return self._is_partial_language

    @property
    def service_url(self) -> str | None:
        """Where a stub's full artefact can be queried. See `MaintainableArtefact`."""
        return self._service_url

    @property
    def structure_url(self) -> str | None:
        """Where a stub's full artefact is written out. See `MaintainableArtefact`."""
        return self._structure_url

    @property
    def is_partial(self) -> bool:
        """Whether this codelist holds a subset of its codes. See `Codelist.is_partial`."""
        return self._is_partial

    @property
    def valid_from(self) -> datetime | None:
        """Start of the artefact's validity period, or None if unbounded."""
        return self._valid_from

    @property
    def valid_to(self) -> datetime | None:
        """End of the artefact's validity period, or None if unbounded."""
        return self._valid_to

    # ── SDMX class vars ──────────────────────────────────────────────────

    # `ClassVar`, like every other artefact class declares them: a plain
    # assignment reads as an instance attribute and so does not satisfy
    # `SupportsUrn`, whose members are ClassVars (#407).
    sdmx_package: ClassVar[str] = "codelist"
    sdmx_class: ClassVar[str] = "Codelist"

    # ── Code access (Layer 2: Query-returning) ───────────────────────────

    @property
    def codes(self) -> Query[CodeRow, Code]:
        """This codelist's codes, as a lazy query.

        Ordered by declaration position, as the *default* order rather than as
        a caller's `order_by`. The path language's own default is its `__key__`
        tiebreak — `code_urn`, under which `C10` precedes `C2` — and a codelist
        has a stated order a caller reading `cl.codes` is entitled to. Setting
        it as the default keeps `cl.codes.group_by(...)` legal, which a real
        `order_by` would refuse, and the first `order_by` replaces it.
        """
        return build(
            q_accessors.q.code,
            self._conn,
            lambda keys, *, annotations=True: resolve.resolve_code_keys(self._conn, keys, annotations=annotations),
            default_order=(OrderKey(q_accessors.q.code.position, ascending=True),),
        ).filter(q_accessors.q.code.codelist.urn == self._urn)

    def __len__(self) -> int:
        row = _cur(self._conn).execute(
            "SELECT COUNT(*) FROM sdmx.code WHERE codelist_urn = ?",
            [self._urn],
        )
        return count(row)

    def __contains__(self, key: object) -> bool:
        """Whether this codelist holds a code with that id.

        Its own SQL rather than `self.codes.__contains__`, which #306 removed:
        on a *query* `in` ignored the query's own filters. On the artefact
        there are no filters to ignore, so the question is well posed — this is
        the one place the old behaviour was correct.

        Note plain `Codelist` has no `__contains__` at all, so this is an
        addition of the lazy facade rather than part of the duck-typing.
        Left as it was; narrowing that asymmetry is its own change.
        """
        if isinstance(key, Code):
            key = key.id
        if not isinstance(key, str):
            return False
        row = _cur(self._conn).execute(
            "SELECT 1 FROM sdmx.code WHERE codelist_urn = ? AND code_id = ?",
            [self._urn, key],
        )
        return row.fetchone() is not None

    def ref_to(self, code_id: str) -> Ref[Code]:
        """Build a resolved `Ref` to a code in this codelist — see `Codelist.ref_to`.

        Works without materialising: the existence check goes through this
        class's own subscript, which is one targeted query.

        Raises:
            KeyError: If no code with this id is in the codelist.
        """
        from sdmxlib.model._items import item_ref  # noqa: PLC0415  # lazy: model imports storage nowhere

        return item_ref(self, code_id, Code)

    def __getitem__(self, key: str | int) -> Code:
        """One code, by id or by position in declaration order.

        Its own lookup rather than `self.codes[key]`, which #306 changed under
        it: on a `Query` the subscript is a *slice*, because a query is not a
        sequence. `CodeQuery` accepted an id here and a `Query` cannot, without
        `q[0:10]` and `q["FREQ"]` meaning two different things.
        """
        if isinstance(key, str):
            found = _artefacts(self.codes.filter(q_accessors.q.code.id == key).collect(), Code)
            if not found:
                raise KeyError(key)
            return found[0]
        return _artefacts(self.codes.collect(), Code)[key]

    # ── SKOS/XKOS hierarchy navigation ──────────────────────────────────

    def roots(self) -> Query[CodeRow, Code]:
        """Top-level codes (``broader IS NULL``)."""
        return self.codes.filter(q_accessors.q.code.parent.id.is_null())

    def leaves(self) -> Query[CodeRow, Code]:
        """Codes that no other code names as parent."""
        return self.codes.filter(q_accessors.q.code.children.count() == 0)

    def narrower(self, code_id: str) -> Query[CodeRow, Code]:
        """Direct children of ``code_id``."""
        return self.codes.filter(q_accessors.q.code.parent.id == code_id)

    def broader(self, code_id: str) -> Code | None:
        """Direct parent of ``code_id`` — at most one. ``None`` for roots.

        Read as "the code whose children include this one", which is the
        `children` edge rather than the raw self-join this used to spell.
        """
        found = _artefacts(self.codes.filter(q_accessors.q.code.children.id == code_id).collect(), Code)
        return found[0] if found else None

    def narrower_transitive(
        self, code_id: str, *, depth: int | None = None, include_self: bool = False
    ) -> Query[CodeRow, Code]:
        """All descendants of ``code_id``.

        Ancestry is a stored fact — the writer bakes `ancestor_ids` — so this
        is a containment test rather than recursion, and `depth` is an ordinary
        conjunction on the baked `depth` column rather than anything the
        compiler has to know about. The starting code's depth is fetched once
        so the comparison is against a literal.
        """
        node = q_accessors.q.code
        found = node.ancestors.id == code_id
        if include_self:
            found = found | (node.id == code_id)
        if depth is not None:
            found = found & (node.depth <= self.depth_of(code_id) + depth)
        return self.codes.filter(found)

    def broader_transitive(
        self, code_id: str, *, depth: int | None = None, include_self: bool = False
    ) -> Query[CodeRow, Code]:
        """All ancestors of ``code_id``. ``depth`` bounds hops."""
        node = q_accessors.q.code
        found = node.descendants.id == code_id
        if include_self:
            found = found | (node.id == code_id)
        if depth is not None:
            found = found & (node.depth >= self.depth_of(code_id) - depth)
        return self.codes.filter(found)

    def depth_of(self, code_id: str) -> int:
        """Depth of ``code_id`` in the codelist hierarchy (root = 0)."""
        row = (
            _cur(self._conn)
            .execute(
                "SELECT depth FROM sdmx.code WHERE codelist_urn = ? AND code_id = ?",
                [self._urn, code_id],
            )
            .fetchone()
        )
        if row is None:
            msg = f"Code {code_id!r} not found in codelist {self._urn!r}"
            raise KeyError(msg)
        return int(row[0]) if row[0] is not None else 0

    # ── Bulk projections (one SQL each) ──────────────────────────────────

    def depths(self) -> dict[str, int]:
        """``{code_id: depth}`` for every code (root = 0). One SQL query."""
        rows = (
            _cur(self._conn)
            .execute(
                """
            SELECT code_id, depth
            FROM sdmx.code
            WHERE codelist_urn = ?
            """,
                [self._urn],
            )
            .fetchall()
        )
        return {str(r[0]): int(r[1]) if r[1] is not None else 0 for r in rows}

    def broader_map(self) -> dict[str, str | None]:
        """``{code_id: parent_id}`` for every code. One SQL query."""
        rows = (
            _cur(self._conn)
            .execute(
                """
            SELECT code_id, parent_id
            FROM sdmx.code
            WHERE codelist_urn = ?
            """,
                [self._urn],
            )
            .fetchall()
        )
        return {str(r[0]): (str(r[1]) if r[1] is not None else None) for r in rows}

    def narrower_map(self) -> dict[str, tuple[str, ...]]:
        """``{code_id: (child_id, ...)}`` for every code, ordered by position."""
        rows = (
            _cur(self._conn)
            .execute(
                """
            SELECT code_id, parent_id
            FROM sdmx.code
            WHERE codelist_urn = ?
            ORDER BY position NULLS LAST, code_id
            """,
                [self._urn],
            )
            .fetchall()
        )
        children: dict[str, list[str]] = {str(r[0]): [] for r in rows}
        for r in rows:
            cid = str(r[0])
            pid = str(r[1]) if r[1] is not None else None
            if pid is not None and pid in children:
                children[pid].append(cid)
        return {k: tuple(v) for k, v in children.items()}

    def ancestors_map(self) -> dict[str, tuple[str, ...]]:
        """``{code_id: (ancestor_id, ...)}`` root-first. One SQL query.

        Reads the precomputed ``ancestor_ids`` column.
        """
        rows = (
            _cur(self._conn)
            .execute(
                """
            SELECT code_id, ancestor_ids
            FROM sdmx.code
            WHERE codelist_urn = ?
            """,
                [self._urn],
            )
            .fetchall()
        )
        result: dict[str, tuple[str, ...]] = {}
        for r in rows:
            cid = str(r[0])
            ancestors = r[1]
            result[cid] = tuple(str(a) for a in ancestors) if ancestors else ()
        return result

    def descendants_map(self) -> dict[str, tuple[str, ...]]:
        """``{code_id: (descendant_id, ...)}`` in pre-order DFS, excluding self."""
        narrower = self.narrower_map()
        cache: dict[str, tuple[str, ...]] = {}

        def _desc(cid: str) -> tuple[str, ...]:
            if cid in cache:
                return cache[cid]
            out: list[str] = []
            for child_id in narrower.get(cid, ()):
                out.append(child_id)
                out.extend(_desc(child_id))
            cache[cid] = tuple(out)
            return cache[cid]

        return {cid: _desc(cid) for cid in narrower}

    def inherit_along_broader[T](self, values: dict[str, T]) -> dict[str, T]:
        """Propagate ``values`` down the parent chain. One SQL query.

        For each code, walk the broader chain (using ``ancestor_ids``) and
        assign the first explicit value encountered. Codes with no explicit
        value anywhere in their chain are omitted from the result.
        """
        rows = (
            _cur(self._conn)
            .execute(
                """
            SELECT code_id, ancestor_ids
            FROM sdmx.code
            WHERE codelist_urn = ?
            """,
                [self._urn],
            )
            .fetchall()
        )
        result: dict[str, T] = {}
        for r in rows:
            cid = str(r[0])
            if cid in values:
                result[cid] = values[cid]
                continue
            ancestors = r[1] or ()
            # ancestor_ids is stored root-first; walk from immediate parent
            # (last entry) back toward the root for SKOS-correct inheritance.
            for ancestor in reversed(list(ancestors)):
                a = str(ancestor)
                if a in values:
                    result[cid] = values[a]
                    break
        return result

    # ── Direct SQL helpers ───────────────────────────────────────────────

    def code_ids(self) -> list[str]:
        """Direct SQL: every code id, without creating `Code` objects.

        The lazy half of `CodelistLike.code_ids` — 92 ms against 2,854 ms to
        materialise, on a 579,951-code codelist.
        """
        return self.codes.ids()

    def codes_for(self, ids: Iterable[str]) -> dict[str, Code]:
        """Direct SQL: ``{code_id: Code}`` for exactly *ids*; unknown ids are absent.

        The lazy half of `CodelistLike.codes_for` — one batched read whose cost
        follows ``len(ids)``, where `materialize` builds every code.
        Not through `self.codes.filter(...is_in(ids))`: that binds the ids,
        converted one at a time — linear, and ~80 us each without pandas
        installed — where `resolve.codes_by_id` matches against a
        registered Arrow buffer.
        """
        return resolve.codes_by_id(self._conn, self._urn, set(ids))

    @classmethod
    def codes_for_each(cls, requests: Sequence[tuple[Self, Collection[str]]]) -> list[dict[str, Code]]:
        """Direct SQL: every request's codes in one batched read per store.

        The lazy half of `CodelistLike.codes_for_each`. Requests are grouped by
        connection, and two asking the same codelist share its branch of
        `resolve.codes_by_ids`.
        """
        asked = [(codelist, set(ids)) for codelist, ids in requests]
        stores: dict[int, tuple[duckdb.DuckDBPyConnection, dict[str, set[str]]]] = {}
        for codelist, ids in asked:
            _, wanted = stores.setdefault(id(codelist._conn), (codelist._conn, {}))
            wanted.setdefault(codelist._urn, set()).update(ids)
        found = {
            (store, urn): codes
            for store, (conn, wanted) in stores.items()
            for urn, codes in resolve.codes_by_ids(conn, wanted).items()
        }
        return [
            {
                code_id: code
                for code_id in ids
                if (code := found[id(codelist._conn), codelist._urn].get(code_id)) is not None
            }
            for codelist, ids in asked
        ]

    def code_map(self, *, lang: str) -> dict[str, str]:
        """Direct SQL: ``{code_id: label}`` without creating Code objects.

        Memoised per instance per ``lang`` with a single-flight build lock: the
        first caller runs the SQL projection, concurrent
        callers for the same ``lang`` block on the lock and then return the
        hot cache rather than each re-running the query. The returned dict
        is the cached instance — treat it as read-only.
        """
        cached = self._code_map_cache.get(lang)
        if cached is not None:
            return cached
        with self._code_map_lock:
            # Double-checked: a prior holder may have built it while we waited.
            cached = self._code_map_cache.get(lang)
            if cached is not None:
                return cached
            mapping = code_map(self._conn, self._urn, lang=lang)
            self._code_map_cache[lang] = mapping
            return mapping

    @property
    def annotations(self) -> Annotations:
        """This artefact's annotations, read on demand.

        A property rather than a constructor argument because the proxy exists
        to avoid loading what nobody asked for, and most callers never ask.
        Uncached for the reason `code_map` is cached and this is not: one small
        query, and a cache would need invalidating on every write.
        """
        loaded = resolve.annotations_for_urns(_cur(self._conn), [self._urn])
        return loaded.get(self._urn, Annotations.EMPTY)

    @property
    def links(self) -> tuple[Link, ...]:
        """This artefact's links, read on demand for the reason `annotations` is."""
        return resolve.links_for_urns(_cur(self._conn), [self._urn]).get(self._urn, ())

    @property
    def extensions(self) -> tuple[CodelistExtension, ...]:
        """The codelists this one extends, read on demand.

        On the proxy for the same reason `annotations` is: if only the
        eager reader carried them, `get()` and `get(eager=True)` would answer
        differently about what codes this list is composed of.
        """
        return resolve.codelist_extensions(_cur(self._conn), self._urn)

    #: `Codelist` members this class does not have, and what to use instead
    #: (#397). Each rebuilds the codelist from its codes, which is the one thing
    #: a lazy view has not loaded.
    _MATERIALISED_ONLY: ClassVar[dict[str, str]] = {
        "add": "`.materialize().add(...)`",
        "drop": "`.materialize().drop(...)`",
        "apply_extensions": "`.materialize().apply_extensions(...)`",
        "expand_wildcards": "`.materialize().expand_wildcards(...)`",
    }

    # Runtime-only, and that is load-bearing rather than tidiness. A
    # `__getattr__` a type checker can see makes *every* attribute resolve to
    # its return type, so `h.codes` would type-check again and the #397
    # overloads would buy nothing — the two halves would cancel. Hidden here,
    # the checker sees the real attribute set and the message below is what a
    # caller running no checker gets instead.
    if not TYPE_CHECKING:

        def __getattr__(self, name: str) -> object:
            """Refuse a materialised-only member by name, not with a bare miss.

            The same reasoning as `LazyHierarchy.__getattr__`, for a smaller gap:
            `codes` exists here and differs only in *type*, so the loud refusal a
            caller meets for it comes from `Query` itself. These two do not exist
            at all, and used to say so without saying what to do about it.

            Raises:
                AttributeError: Always — naming the counterpart, or the ordinary
                    message for a genuine typo.
            """
            instead = self._MATERIALISED_ONLY.get(name)
            if instead is None:
                msg = f"{type(self).__name__!r} object has no attribute {name!r}"
                raise AttributeError(msg)
            msg = f"a lazy codelist has no `{name}` — it has not loaded its codes to copy. Use {instead}."
            raise AttributeError(msg)

    def materialize(self) -> Codelist:
        """Load all codes and return a real `Codelist`.

        **Every field the proxy holds is passed on.** Omitting one made the two
        read paths disagree — `lazy.uri` returned the stored value and
        `lazy.materialize().uri` returned `None` — so materialising for an
        unrelated reason silently changed the answer. `valid_from`,
        `valid_to`, `uri` and `annotations` were all missing.

        Checked as `get(eager=True)` is: a codelist that breaks a rule
        `Registry.add` enforces raises `SdmxConformanceError`.
        """
        return checked("Codelist", self._urn, self._build)

    def partial(self, code_ids: Iterable[str]) -> Codelist:
        """Direct SQL: this codelist cut down to *code_ids* and their ancestors, flagged ``is_partial``.

        The lazy half of `CodelistLike.partial`. The ancestors come from the
        store's baked ``ancestor_ids``, and only the kept codes are built, so
        the cost follows the result, not the codelist: a handful of the
        579,951 geographies costs a handful of codes.

        Args:
            code_ids: The codes to keep.

        Returns:
            A `Codelist` of the kept codes, in codelist order, with
            ``is_partial`` set and no extensions.

        Raises:
            KeyError: If an id is not a code of this codelist, naming it.
        """
        asked = partial_ids(code_ids)
        found, kept = resolve.partial_closure(self._conn, self._urn, asked)
        unknown = sorted(asked - found)
        if unknown:
            raise KeyError(unknown_codes_message(self._urn, unknown, extended=bool(self.extensions)))
        built = resolve.codes_by_id(self._conn, self._urn, kept)
        return checked(
            "Codelist",
            self._urn,
            lambda: self._with([built[code_id] for code_id in kept], is_partial=True, extensions=()),
        )

    def _build(self) -> Codelist:
        codes = _artefacts(self.codes.collect(), Code)
        return self._with(codes, is_partial=self._is_partial, extensions=self.extensions)

    def _with(self, codes: list[Code], *, is_partial: bool, extensions: Sequence[CodelistExtension]) -> Codelist:
        """A `Codelist` carrying every field this proxy holds, with *codes*."""
        return codelist_of_kind(
            *resolve.geo_kind(self._conn, self._urn),
            id=self._id,
            maintainer=self._agency,
            version=self._version,
            name=self._name,
            description=self._description,
            legacy_is_final=self._legacy_is_final,
            valid_from=self._valid_from,
            valid_to=self._valid_to,
            uri=self._uri,
            is_external_reference=self._is_external_reference,
            is_partial_language=self._is_partial_language,
            service_url=self._service_url,
            structure_url=self._structure_url,
            is_partial=is_partial,
            annotations=self.annotations,
            links=self.links,
            codes=ItemList(codes),
            extensions=tuple(extensions),
        )

    def __repr__(self) -> str:
        return f"LazyCodelist(id={self._id!r}, agency={self.agency_id!r}, codes=~{len(self)})"


#: One statement per column a `LazyHierarchy` bulk projection reads, keyed by
#: path in declaration order. Literal rather than built, so no identifier is
#: ever interpolated into SQL.
_BY_PATH: Final[dict[str, str]] = {
    "depth": "SELECT path, depth FROM sdmx.hierarchy_node WHERE hierarchy_urn = ? ORDER BY position",
    "parent_path": "SELECT path, parent_path FROM sdmx.hierarchy_node WHERE hierarchy_urn = ? ORDER BY position",
    "ancestor_paths": "SELECT path, ancestor_paths FROM sdmx.hierarchy_node WHERE hierarchy_urn = ? ORDER BY position",
}


def _str_tuple(value: object) -> tuple[str, ...]:
    """A stored list column as a tuple of strings; ``()`` for NULL."""
    return tuple(str(v) for v in value) if isinstance(value, (list, tuple)) else ()


class LazyHierarchy:
    """Hierarchy facade backed by DuckDB.

    Duck-types as `Hierarchy` for read operations. ``nodes`` returns
    a `Query` for SQL-pushed filtering and tree
    navigation. SKOS verbs (``roots``/``leaves``/``narrower``/``broader``/
    ``*_transitive``/``depth_of``) and bulk projections (``depths``/
    ``broader_map``/...) mirror `LazyCodelist` exactly.
    """

    __slots__ = (
        "__weakref__",
        "_agency",
        "_conn",
        "_description",
        "_has_formal_levels",
        "_id",
        "_is_external_reference",
        "_is_partial_language",
        "_levels",
        "_name",
        "_service_url",
        "_structure_url",
        "_uri",
        "_urn",
        "_valid_from",
        "_valid_to",
        "_version",
    )

    def __init__(
        self,
        *,
        urn: str,
        conn: duckdb.DuckDBPyConnection,
        id: str,
        maintainer: Agency,
        version: str,
        name: InternationalString,
        description: InternationalString,
        uri: str | None = None,
        is_external_reference: bool = False,
        is_partial_language: bool = False,
        service_url: str | None = None,
        structure_url: str | None = None,
        has_formal_levels: bool,
        levels: tuple[Level, ...],
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
    ) -> None:
        self._urn = urn
        self._conn = conn
        self._id = id
        self._agency = maintainer
        self._version = version
        self._name = name
        self._description = description
        self._uri = uri
        self._is_external_reference = is_external_reference
        self._is_partial_language = is_partial_language
        self._service_url = service_url
        self._structure_url = structure_url
        self._has_formal_levels = has_formal_levels
        self._levels = levels
        self._valid_from = valid_from
        self._valid_to = valid_to

    # ── MaintainableArtefact-compatible properties ────────────────────────

    @property
    def urn(self) -> SdmxUrn[Hierarchy]:
        """This artefact's URN — its identity, as on an eager `Hierarchy`.

        Parsed rather than held typed because the row stores it as text. Added
        when the upgrade guide's own `cl.urn` example — on the artefact a
        `LocalRegistry` hands back — turned out to raise `AttributeError`.
        """
        return Hierarchy.parse_urn(self._urn)

    @property
    def id(self) -> str:
        """The artefact's identifier, unique within its agency and version."""
        return self._id

    @property
    def maintainer(self) -> Agency:
        """The `Agency` responsible for maintaining this artefact."""
        return self._agency

    @property
    def agency_id(self) -> str:
        """Identifier of the maintaining agency. Shorthand for ``maintainer.id``."""
        return self._agency.id

    @property
    def version(self) -> str:
        """Semantic version string, e.g. ``"1.0"``."""
        return self._version

    @property
    def name(self) -> InternationalString:
        """Human-readable name, keyed by language code."""
        return self._name

    @property
    def description(self) -> InternationalString:
        """Longer human-readable description, keyed by language code."""
        return self._description

    @property
    def is_final(self) -> bool:
        """Whether the version names a final artefact. See `MaintainableArtefact.is_final`."""
        return is_final(self._version)

    @property
    def uri(self) -> str | None:
        """External link describing this artefact, or None. See `MaintainableArtefact.uri`."""
        return self._uri

    @property
    def is_external_reference(self) -> bool:
        """Whether this is a stub for an artefact held elsewhere. See `MaintainableArtefact`."""
        return self._is_external_reference

    @property
    def is_partial_language(self) -> bool:
        """Whether some text lacks a language the rest carries. See `MaintainableArtefact`."""
        return self._is_partial_language

    @property
    def service_url(self) -> str | None:
        """Where a stub's full artefact can be queried. See `MaintainableArtefact`."""
        return self._service_url

    @property
    def structure_url(self) -> str | None:
        """Where a stub's full artefact is written out. See `MaintainableArtefact`."""
        return self._structure_url

    @property
    def valid_from(self) -> datetime | None:
        """Start of the artefact's validity period, or None if unbounded."""
        return self._valid_from

    @property
    def valid_to(self) -> datetime | None:
        """End of the artefact's validity period, or None if unbounded."""
        return self._valid_to

    @property
    def has_formal_levels(self) -> bool:
        """Whether the hierarchy declares a formal level structure (SDMX ``@hasFormalLevels``)."""
        return self._has_formal_levels

    @property
    def levels(self) -> tuple[Level, ...]:
        """The `Level` definitions declared by this hierarchy, in stored order."""
        return self._levels

    # ── SDMX class vars ──────────────────────────────────────────────────

    # `ClassVar`, like every other artefact class declares them: a plain
    # assignment reads as an instance attribute and so does not satisfy
    # `SupportsUrn`, whose members are ClassVars (#407).
    sdmx_package: ClassVar[str] = "codelist"
    sdmx_class: ClassVar[str] = "Hierarchy"

    # ── Node access ──────────────────────────────────────────────────────

    @property
    def nodes(self) -> Query[HCodeRow, HierarchicalCode]:
        """This hierarchy's placements, as a lazy query.

        Ordered by the path language's `node_urn` tiebreak, which is total and
        stable across calls — what keyset paging needs — but not declaration
        order. That is stored now (`position`) and `materialize` restores
        it; order by `q.hcode.position` here to ask for it.
        """
        return build(
            q_accessors.q.hcode,
            self._conn,
            lambda keys, *, annotations=True: resolve.resolve_hcode_keys(self._conn, keys, annotations=annotations),
        ).filter(q_accessors.q.hcode.hierarchy.urn == self._urn)

    def __len__(self) -> int:
        cur = _cur(self._conn).execute(
            "SELECT COUNT(*) FROM sdmx.hierarchy_node WHERE hierarchy_urn = ?",
            [self._urn],
        )
        return count(cur)

    def __contains__(self, key: object) -> bool:
        """Whether this hierarchy has a node at path *key* — a bare id is a root.

        By path, as `Hierarchy.__contains__` is: a node id may repeat in another
        branch (#439). A node object answers False, as on the eager class — it
        does not carry the path it sits at.
        """
        if not isinstance(key, str):
            return False
        row = _cur(self._conn).execute(
            "SELECT 1 FROM sdmx.hierarchy_node WHERE hierarchy_urn = ? AND path = ?",
            [self._urn, key],
        )
        return row.fetchone() is not None

    def __getitem__(self, key: str | int) -> HierarchicalCode:
        """One placement, by path or by position. See `Hierarchy.__getitem__`."""
        if isinstance(key, str):
            self._refuse_bare_id(key)
            found = self.get(key)
            if found is None:
                raise KeyError(key)
            return found
        return _artefacts(self.nodes.collect(), HierarchicalCode)[key]

    def get(self, path: str) -> HierarchicalCode | None:
        """The placement at *path*, or ``None``. See `Hierarchy.get`."""
        found = _artefacts(self.nodes.filter(q_accessors.q.hcode.nested_id == path).collect(), HierarchicalCode)
        return found[0] if found else None

    def by_id(self, node_id: str) -> Query[HCodeRow, HierarchicalCode]:
        """Every placement whose own id is *node_id*, at any depth. See `Hierarchy.by_id`."""
        return self.nodes.filter(q_accessors.q.hcode.id == node_id)

    def _refuse_bare_id(self, key: str) -> None:
        """Raise when *key* is no path here but is some placement's own id — see `refuse_bare_id`.

        Only an undotted key is looked up: a dotted one can only be a path, so
        the walks that take one pay nothing, and a bare key is either a root
        (answered) or a node's id (refused) after one indexed lookup.
        """
        if "." in key:
            return
        # One row whatever the tree holds: the lookup's cost is constant, and
        # a guard counting rows fetched can say exactly what it adds.
        row = (
            _cur(self._conn)
            .execute(
                "SELECT list(path ORDER BY path) FROM sdmx.hierarchy_node "
                "WHERE hierarchy_urn = ? AND (path = ? OR node_id = ?)",
                [self._urn, key, key],
            )
            .fetchone()
        )
        found: object = row[0] if row is not None else None
        paths = [str(p) for p in found] if isinstance(found, list) else []
        if key not in paths:
            refuse_bare_id(key, paths, "LazyHierarchy")

    # ── SKOS/XKOS hierarchy navigation ──────────────────────────────────
    # Every *path* is a dotted path from a root, as on `Hierarchy` (#439).

    def roots(self) -> Query[HCodeRow, HierarchicalCode]:
        """Top-level placements (no parent)."""
        return self.nodes.filter(q_accessors.q.hcode.parent.nested_id.is_null())

    def leaves(self) -> Query[HCodeRow, HierarchicalCode]:
        """Placements that no other placement names as parent."""
        return self.nodes.filter(q_accessors.q.hcode.children.count() == 0)

    def narrower(self, path: str) -> Query[HCodeRow, HierarchicalCode]:
        """Direct children of *path*. A node's id rather than its path is refused."""
        self._refuse_bare_id(path)
        return self.nodes.filter(q_accessors.q.hcode.parent.nested_id == path)

    def broader(self, path: str) -> HierarchicalCode | None:
        """Direct parent of *path*. ``None`` for roots; a node's id rather than its path is refused."""
        self._refuse_bare_id(path)
        parent_of = q_accessors.q.hcode.children.nested_id == path
        found = _artefacts(self.nodes.filter(parent_of).collect(), HierarchicalCode)
        return found[0] if found else None

    def narrower_transitive(
        self, path: str, *, depth: int | None = None, include_self: bool = False
    ) -> Query[HCodeRow, HierarchicalCode]:
        """All descendants of *path*. ``depth`` bounds hops. A node's id rather than its path is refused."""
        self._refuse_bare_id(path)
        node = q_accessors.q.hcode
        found = node.ancestors.nested_id == path
        if include_self:
            found = found | (node.nested_id == path)
        if depth is not None:
            found = found & (node.depth <= self.depth_of(path) + depth)
        return self.nodes.filter(found)

    def broader_transitive(
        self, path: str, *, depth: int | None = None, include_self: bool = False
    ) -> Query[HCodeRow, HierarchicalCode]:
        """All ancestors of *path*. ``depth`` bounds hops. A node's id rather than its path is refused."""
        self._refuse_bare_id(path)
        node = q_accessors.q.hcode
        found = node.descendants.nested_id == path
        if include_self:
            found = found | (node.nested_id == path)
        if depth is not None:
            found = found & (node.depth >= self.depth_of(path) - depth)
        return self.nodes.filter(found)

    def ancestor_at(self, level: str | Level, *, include_self: bool = True) -> AncestorMap:
        """Map every node to its nearest ancestor at ``level``.

        The join input for hierarchical aggregation — totalling observations
        by province needs each geography mapped to its province. Compiled, not
        materialised: the mapping stays in the database and composes into a
        join, where applications previously traversed the tree in Python and
        registered the result as a view.

        A node at the level maps to itself; a node with no ancestor there is
        absent rather than ``NULL``. Same semantics as
        `Hierarchy.inherit_along_broader`, which the property tests use as the
        oracle.

        Args:
            level: The `Level`, or its id.
            include_self: Whether a node already *at* the level maps to
                itself. Defaults to True, unlike the transitive walks, because
                this is a mapping rather than a closure — excluding the
                identity leaves every node at the level with no image, so a
                province would have no province.

        Returns:
            A two-column ``(node_id, rollup_id)`` relation.
        """
        return AncestorMap(
            hierarchy_urn=self._urn,
            level_id=level.id if isinstance(level, Level) else level,
            conn=self._conn,
            include_self=include_self,
        )

    def depth_of(self, path: str) -> int:
        """Depth of *path* in the hierarchy (root = 0)."""
        self._refuse_bare_id(path)
        row = (
            _cur(self._conn)
            .execute(
                "SELECT depth FROM sdmx.hierarchy_node WHERE hierarchy_urn = ? AND path = ?",
                [self._urn, path],
            )
            .fetchone()
        )
        if row is None:
            msg = f"{path!r} is not a path in hierarchy {self._urn!r}"
            raise KeyError(msg)
        return int(row[0]) if row[0] is not None else 0

    # ── Bulk projections (one SQL each), keyed by path as on `Hierarchy` ─

    def _column_by_path(self, column: str) -> list[tuple[str, object]]:
        """``(path, column)`` for every node, in declaration order.

        *column* picks one of `_BY_PATH`'s literal statements, so nothing is
        interpolated into the SQL.
        """
        cur = _cur(self._conn).execute(_BY_PATH[column], [self._urn])
        return [(str(r[0]), r[1]) for r in cur.fetchall()]

    def depths(self) -> dict[str, int]:
        """``{path: depth}`` for every node (root = 0). One SQL query."""
        return {path: depth if isinstance(depth, int) else 0 for path, depth in self._column_by_path("depth")}

    def broader_map(self) -> dict[str, str | None]:
        """``{path: parent_path}`` for every node. One SQL query."""
        rows = self._column_by_path("parent_path")
        return {path: (str(parent) if parent is not None else None) for path, parent in rows}

    def narrower_map(self) -> dict[str, tuple[str, ...]]:
        """``{path: (child_path, ...)}`` for every node, in declaration order."""
        rows = self._column_by_path("parent_path")
        children: dict[str, list[str]] = {path: [] for path, _ in rows}
        for path, parent in rows:
            if parent is not None and str(parent) in children:
                children[str(parent)].append(path)
        return {k: tuple(v) for k, v in children.items()}

    def ancestors_map(self) -> dict[str, tuple[str, ...]]:
        """``{path: (ancestor_path, ...)}`` root-first. One SQL query."""
        return {path: _str_tuple(ancestors) for path, ancestors in self._column_by_path("ancestor_paths")}

    def descendants_map(self) -> dict[str, tuple[str, ...]]:
        """``{path: (descendant_path, ...)}`` in pre-order, excluding self."""
        narrower = self.narrower_map()
        cache: dict[str, tuple[str, ...]] = {}

        def _desc(path: str) -> tuple[str, ...]:
            if path in cache:
                return cache[path]
            out: list[str] = []
            for child in narrower.get(path, ()):
                out.append(child)
                out.extend(_desc(child))
            cache[path] = tuple(out)
            return cache[path]

        return {path: _desc(path) for path in narrower}

    def inherit_along_broader[T](self, values: dict[str, T]) -> dict[str, T]:
        """Propagate ``values`` (keyed by path) down the parent chain. One SQL query."""
        result: dict[str, T] = {}
        for path, ancestors in self._column_by_path("ancestor_paths"):
            if path in values:
                result[path] = values[path]
                continue
            for ancestor in reversed(_str_tuple(ancestors)):
                if ancestor in values:
                    result[path] = values[ancestor]
                    break
        return result

    @property
    def annotations(self) -> Annotations:
        """This artefact's annotations, read on demand.

        A property rather than a constructor argument because the proxy exists
        to avoid loading what nobody asked for, and most callers never ask.
        Uncached for the reason `code_map` is cached and this is not: one small
        query, and a cache would need invalidating on every write.
        """
        loaded = resolve.annotations_for_urns(_cur(self._conn), [self._urn])
        return loaded.get(self._urn, Annotations.EMPTY)

    @property
    def links(self) -> tuple[Link, ...]:
        """This artefact's links, read on demand for the reason `annotations` is."""
        return resolve.links_for_urns(_cur(self._conn), [self._urn]).get(self._urn, ())

    #: `Hierarchy` members this class deliberately does not have, and the lazy
    #: spelling to reach for instead (#397). Each is a *materialised tree*
    #: where this class offers a plan, which is the whole difference between
    #: the two — so they are refused by name rather than quietly absent.
    _MATERIALISED_ONLY: ClassVar[dict[str, str]] = {
        "codes": "`.nodes` for the query over every placement, or `.materialize().codes` for the ItemList",
        "tree": "`.roots()` for the top-level placements, or `.materialize().tree`",
        "descendants": (
            "`.narrower_transitive(path)` for the query, `.descendants_map()` "
            "for every node at once, or `.materialize().descendants(...)`"
        ),
        "path_of": (
            "`q.hcode.nested_id` in `.nodes.select(...)`, or `.materialize().path_of(node)` — a node "
            "handed out by a query is not part of a tree it could locate itself in"
        ),
    }

    # Runtime-only, and that is load-bearing rather than tidiness. A
    # `__getattr__` a type checker can see makes *every* attribute resolve to
    # its return type, so `h.codes` would type-check again and the #397
    # overloads would buy nothing — the two halves would cancel. Hidden here,
    # the checker sees the real attribute set and the message below is what a
    # caller running no checker gets instead.
    if not TYPE_CHECKING:

        def __getattr__(self, name: str) -> object:
            """Refuse a materialised-only member by name, not with a bare miss.

            `LazyHierarchy` duck-types as `Hierarchy` and is what
            `LocalRegistry.get` returns, so `h.codes` used to raise a plain
            ``AttributeError: 'LazyHierarchy' object has no attribute 'codes'`` —
            naming nothing and reading like a bug in this library rather than a
            deliberate difference. #397 declared the return type so a checker now
            catches it; this is the same answer for a caller who has no checker
            running, and it mirrors what a `Query` already does for ``len()``.

            Only reached when normal lookup fails, so it costs nothing on the
            attributes that do exist.

            Raises:
                AttributeError: Always — either naming the lazy counterpart, or
                    with the ordinary message for a genuine typo.
            """
            instead = self._MATERIALISED_ONLY.get(name)
            if instead is None:
                msg = f"{type(self).__name__!r} object has no attribute {name!r}"
                raise AttributeError(msg)
            msg = f"a lazy hierarchy has no `{name}` — it is a plan, not a materialised tree. Use {instead}."
            raise AttributeError(msg)

    def materialize(self) -> Hierarchy:
        """Load the full hierarchy tree and return a real `Hierarchy`.

        Builds the tree bottom-up: leaves first, then parents. Each
        ``HierarchicalCode`` gets its children list at construction
        time — no post-construction mutation.

        Checked as `get(eager=True)` is: a hierarchy that breaks a rule
        `Registry.add` enforces raises `SdmxConformanceError`.
        """
        return checked("Hierarchy", self._urn, self._build)

    def _build(self) -> Hierarchy:
        rows = (
            _cur(self._conn)
            .execute(
                """
            SELECT node_urn, node_id, path, parent_path, code_urn, level_id,
                   valid_from, valid_to, uri, version
            FROM sdmx.hierarchy_node
            WHERE hierarchy_urn = ?
            ORDER BY depth DESC, position
            """,
                [self._urn],
            )
            .fetchall()
        )
        levels_by_id = {lv.id: lv for lv in self._levels}
        # Every node's annotations in one indexed read (#443); they were
        # dropped here while `get(eager=True)` and `q.hcode` kept them.
        annotations_by_node = resolve.annotations_of_artefact(_cur(self._conn), self._urn)
        no_annotations = Annotations.EMPTY
        links_by_node = resolve.links_of_artefact(_cur(self._conn), self._urn)
        children_by_parent: dict[str | None, list[HierarchicalCode]] = {}
        # Hoisted out of the loop, both for the same reason: at half a million
        # nodes, per-node work that does not depend on the node is the cost.
        #
        # `Ref[Code]` re-subscripts the generic on every node, and each
        # attribute read off the resulting alias goes through `typing`'s
        # `__getattr__` — 1.5 s of pure typing machinery on the FED hierarchy.
        # Binding the constructor once keeps `T` inferred and skips all of it.
        unresolved_code = Ref[Code].unresolved
        for r in rows:
            (node_urn, node_id, path, parent_path, code_urn, level_id) = r[:6]
            valid_from, valid_to, uri, version = r[6:]
            # Children are keyed by their parent's *path* — a node id may repeat
            # in another branch (#439) — and were built first (deeper depths
            # first, siblings in declaration order).
            kids = children_by_parent.pop(str(path), [])
            code_ref: Ref[Code] | None = None
            if code_urn is not None:
                code_ref = unresolved_code(Code.parse_urn(str(code_urn)))
            level: Level | None = None
            if level_id is not None:
                # Not `.get(id, Level(id=...))`: a default argument is
                # evaluated eagerly, so that spelling built a throwaway `Level`
                # for every node whose level *was* declared — which is nearly
                # all of them. It was the single largest line in the profile,
                # above `HierarchicalCode.__init__` itself.
                level = levels_by_id.get(str(level_id))
                if level is None:
                    level = Level(id=str(level_id))
            node = HierarchicalCode(
                id=str(node_id),
                code=code_ref,
                children=kids,
                valid_from=valid_from,
                valid_to=valid_to,
                level=level,
                uri=uri,
                version=version,
                annotations=annotations_by_node.get(str(node_urn), no_annotations),
                links=links_by_node.get(str(node_urn), ()) if links_by_node else (),
            )
            pkey = str(parent_path) if parent_path is not None else None
            children_by_parent.setdefault(pkey, []).append(node)
        roots = children_by_parent.get(None, [])
        return Hierarchy(
            id=self._id,
            maintainer=self._agency,
            version=self._version,
            name=self._name,
            description=self._description,
            valid_from=self._valid_from,
            valid_to=self._valid_to,
            uri=self._uri,
            is_external_reference=self._is_external_reference,
            is_partial_language=self._is_partial_language,
            service_url=self._service_url,
            structure_url=self._structure_url,
            # Held by the proxy and dropped here, which is why #323 read as a
            # storage gap: `lazy.has_formal_levels` was right all along.
            has_formal_levels=self._has_formal_levels,
            annotations=self.annotations,
            links=self.links,
            levels=list(self._levels),
            tree=roots,
        )

    def __repr__(self) -> str:
        return f"LazyHierarchy(id={self._id!r}, agency={self.agency_id!r}, nodes=~{len(self)})"


# Late import needed by LazyHierarchy.broader (placed at module bottom to
# avoid a forward-reference cycle on Annotations).

__all__ = ["LazyCodelist", "LazyHierarchy"]
