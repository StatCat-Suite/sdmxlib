"""Batch resolvers — key rows to domain objects, for the path language.

Separate from `readers` for a plain reason: `readers` imports `storage.lazy`
to build the lazy facades, and `storage.lazy` needs these to make `cl.codes`
return a `Query`. One of the two directions had to move, and this is the half
with no dependency on either.

Everything here resolves *many* keys at once and registers the key set rather
than binding it — see `session.value_match` for the measurement, and
`tests/unit/test_query_resolve_binds.py` for what it was worth (1.24 s to
34 ms over 10k codes).
"""

from collections.abc import Collection, Mapping, Sequence
from contextlib import ExitStack
from typing import Any, Final

import duckdb
import msgspec

from sdmxlib._duckdb import first
from sdmxlib.model.annotations import Annotation, Annotations, AnnotationURL
from sdmxlib.model.codelist import (
    CascadeSelection,
    Code,
    Codelist,
    CodelistExtension,
    CodeSelection,
    InclusionMode,
    MemberValue,
    code_of_kind,
)
from sdmxlib.model.hierarchy import HierarchicalCode, Level
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet, Representation, SentinelValue, enumeration_ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.session import cursor_for, value_match
from sdmxlib.storage._parsed import parsed
from sdmxlib.storage.schema import FACET_COLUMN_NAMES

#: Decodes what `istring_sql` selects. Built once — a `msgspec.json.Decoder`
#: bound to its type is markedly faster than `msgspec.json.decode(b, type=…)`
#: per call, and this runs once per label per row.
_ISTRING_DECODER: Final = msgspec.json.Decoder(dict[str, str])


def istring_sql(column: str) -> str:
    """SQL selecting a `MAP(VARCHAR, VARCHAR)` label for `json_to_istring`.

    DuckDB's MAP → Python `dict` conversion happens inside `fetchall`, before
    any of our code runs, and it is expensive: on the 2021 Census Profile's `CL_GEOGRAPHY`
    the two label columns are **750 ms of a 916 ms fetch** for 579,951 rows
    (#380). Serialising them to JSON in the engine and decoding with msgspec is
    2.3x faster end to end — 1358 ms to 580 ms, for byte-identical
    `InternationalString`s — because it moves one dict-per-row-per-label out of
    DuckDB's row materialiser and into a decoder written for exactly this.

    **Not** a general "use Arrow / use JSON" result, and worth stating so it is
    not over-applied: `to_arrow_table()` fetches the same rows in 71 ms, but
    `to_arrow_table().to_pylist()` costs 2430 ms — *slower* than `fetchall`.
    Columnar wins only while the data stays columnar, which is why `.frame()`
    is 161 ms and why this path, which must produce a Python object per row,
    cannot use it.

    Only the four readers that materialise rows in bulk use this. The rest read
    tens of rows, where the difference is unmeasurable and the wider diff is
    not worth it.

    The column name is interpolated, never a value — see CLAUDE.md "SQL style".
    Callers pass literals from the schema.
    """
    return f"to_json({column})"


def json_to_istring(value: str | None) -> InternationalString:
    """What `istring_sql` selected, as an `InternationalString`.

    NULL arrives as `None` and an empty map as `"{}"`; both are the shared
    empty (#377). Every awkward label value — embedded quotes, backslashes,
    newlines, non-ASCII, empty strings — round-trips identically to the MAP
    path, which `tests/unit/test_istring_sql_agrees_with_maps.py` holds.
    """
    if not value or value == "{}":
        return InternationalString.EMPTY
    return InternationalString.of(_ISTRING_DECODER.decode(value))


def map_to_istring(value: dict[str, str] | None) -> InternationalString:
    """A DuckDB `MAP` result as an `InternationalString`.

    `of` returns the shared empty for an empty map, so the `None` check is the
    only branch left here (#377).
    """
    return InternationalString.of(value) if value else InternationalString.EMPTY


def annotation_rows(cur: duckdb.DuckDBPyConnection, where: str, params: Sequence[object]) -> dict[str, Annotations]:
    """The annotations `where` selects, keyed by owner, read in one statement and their URLs in another.

    `where` is a predicate on ``sdmx.annotation`` as ``a``. The URLs (#422)
    were a correlated subquery in the first statement, one per annotation,
    which DuckDB planned so badly for a point read that it cost 320 µs with no
    annotations at all. Now they are read only when there are annotations,
    under the same predicate, so the same one bound value.

    Raises:
        duckdb.Error: If `where` is not a predicate on ``a``.
    """
    rows = cur.execute(
        parsed(
            cur,
            f"""
        SELECT a.owner_urn, a.id, a.annotation_id, a.title, a.annotation_type, a.value, atxt.lang, atxt.text
        FROM sdmx.annotation a
        LEFT JOIN sdmx.annotation_text atxt ON atxt.annotation_id = a.id
        WHERE {where}
        -- ``atxt.lang`` is part of the ordering because the LEFT JOIN otherwise
        -- returns an annotation's languages in whatever order the plan happens
        -- to produce. That was latent until #368 added a column and the plan
        -- shifted, flipping ``en, fr`` to ``fr, en`` and failing the #316
        -- store/load guard, which compares `repr`. Nothing records insertion
        -- order, so a canonical order is the only reproducible answer.
        ORDER BY a.owner_urn, a.ordinal, a.id, atxt.lang
        """,
        ),
        params,
    ).fetchall()
    if not rows:
        return {}
    url_rows = cur.execute(
        parsed(
            cur,
            f"""
        SELECT u.annotation_id, u.url, u.lang, u.link_urn, u.link_type
        FROM sdmx.annotation_url u
        JOIN sdmx.annotation a ON a.id = u.annotation_id
        WHERE {where}
        ORDER BY u.annotation_id, u.position
        """,
        ),
        params,
    ).fetchall()
    urls: dict[object, list[AnnotationURL]] = {}
    for annotation_id, url, lang, urn, link_type in url_rows:
        urls.setdefault(annotation_id, []).append(AnnotationURL(str(url), lang, urn, link_type))
    return group_annotations_by_owner(rows, urls)


def annotations_from_joined_rows(
    rows: list[tuple[Any, ...]], urls: Mapping[object, Sequence[AnnotationURL]]
) -> Annotations:
    """Build `Annotations` from joined annotation + annotation_text rows and their URLs.

    Each row is ``(db_id, annotation_id, title, annotation_type, value, lang,
    text)``, and `urls` holds each annotation's URLs by ``db_id``. Rows for one
    annotation are contiguous; LEFT-JOIN nulls in ``(lang, text)`` mean an
    annotation with no text.
    """
    result: list[Annotation] = []
    current_db_id: object = object()
    texts: dict[str, str] = {}
    current: tuple[Any, ...] | None = None

    def flush() -> None:
        if current is None:
            return
        db_id, ann_id, title, ann_type, value = current
        # `of` yields the shared empty when there is no text, which is ~98% of
        # annotations on a real bundle — 585,675 of them against 10,064 text
        # rows on the 2021 Census Profile bundle (#377).
        text = InternationalString.of(texts)
        result.append(
            Annotation(id=ann_id, title=title, type=ann_type, urls=tuple(urls.get(db_id, ())), text=text, value=value)
        )

    for row in rows:
        db_id, ann_id, title, ann_type, value, lang, text_val = row
        if db_id != current_db_id:
            flush()
            current_db_id = db_id
            current = (db_id, ann_id, title, ann_type, value)
            texts = {}
        if lang is not None:
            texts[str(lang)] = str(text_val)
    flush()
    return Annotations(result)


def part_owner(urn: str, field: str, index: int) -> str:
    """The annotation owner of an artefact's unidentifiable part: ``<urn>#<field>[<index>]``.

    A value mapping, a cube region or a data key has no URN, and the annotation
    table is keyed by owner (#422). The field and position name it uniquely
    within its artefact, the way the model reaches it — ``maps[3]``,
    ``regions[0]`` — and ``#`` cannot occur in a URN, so no owner key collides.
    """
    return f"{urn}#{field}[{index}]"


def group_annotations_by_owner(
    rows: list[tuple[Any, ...]], urls: Mapping[object, Sequence[AnnotationURL]]
) -> dict[str, Annotations]:
    """Group joined annotation rows by ``owner_urn`` (the first column)."""
    if not rows:
        return {}
    by_owner: dict[str, list[tuple[Any, ...]]] = {}
    for row in rows:
        by_owner.setdefault(str(row[0]), []).append(row[1:])
    return {owner: annotations_from_joined_rows(rs, urls) for owner, rs in by_owner.items()}


def annotations_for_urns(cur: duckdb.DuckDBPyConnection, urns: Sequence[str]) -> dict[str, Annotations]:
    """Annotations for a set of owners, with the set registered not bound."""
    if not urns:
        return {}
    with value_match(cur, list(urns)) as (match, params):
        return annotation_rows(cur, f"a.owner_urn {match}", params)


def annotations_of_artefact(cur: duckdb.DuckDBPyConnection, artefact_urn: str) -> dict[str, Annotations]:
    """Every annotation an artefact and its items own, keyed by owner (#443).

    By ``owner_artefact_urn``, the indexed column #145 added for the delete
    cascade: one equality lookup however many items there are, where listing
    the item URNs for `annotations_for_urns` would register half a million of
    them for the FED hierarchy.
    """
    return annotation_rows(cur, "a.owner_artefact_urn = ?", [artefact_urn])


def _group_links_by_owner(
    rows: list[tuple[str, str, str, str | None, str | None, str | None]],
) -> dict[str, tuple[Link, ...]]:
    by_owner: dict[str, list[Link]] = {}
    for owner, rel, url, urn, link_type, hreflang in rows:
        by_owner.setdefault(owner, []).append(Link(rel=rel, url=url, urn=urn, type=link_type, hreflang=hreflang))
    return {owner: tuple(links) for owner, links in by_owner.items()}


def links_for_urns(cur: duckdb.DuckDBPyConnection, urns: Sequence[str]) -> dict[str, tuple[Link, ...]]:
    """`annotations_for_urns` for links (#422): each owner's, in order; an owner with none is absent."""
    if not urns:
        return {}
    with value_match(cur, list(urns)) as (match, params):
        rows = cur.execute(
            f"SELECT owner_urn, rel, url, urn, link_type, hreflang FROM sdmx.link WHERE owner_urn {match} "
            "ORDER BY owner_urn, position",
            params,
        ).fetchall()
    return _group_links_by_owner(rows)


def links_of_artefact(cur: duckdb.DuckDBPyConnection, artefact_urn: str) -> dict[str, tuple[Link, ...]]:
    """`annotations_of_artefact` for links (#422): the artefact's and every item's, by owner."""
    rows = cur.execute(
        "SELECT owner_urn, rel, url, urn, link_type, hreflang FROM sdmx.link WHERE owner_artefact_urn = ? "
        "ORDER BY owner_urn, position",
        [artefact_urn],
    ).fetchall()
    return _group_links_by_owner(rows)


def codes_by_urn(conn: duckdb.DuckDBPyConnection, urns: Sequence[str], *, annotations: bool = True) -> dict[str, Code]:
    """`code_urn` → `Code`, for exactly the URNs asked for.

    The targeted counterpart to loading a whole `Codelist`. Resolving an item
    `Ref` used to load the item's entire parent scheme — correct, and the #72
    fix that made it load *once* rather than per-ref, but still every code in
    it. On the 2021 Census Profile bundle that made a 28-node hierarchy cost 2.09 s because its
    nodes reference `CL_GEOGRAPHY`, which has 579,951 codes (#378).

    Reading only what was asked for is **never slower** and up to 178x faster.
    Measured, one batched read against loading the scheme:

    | distinct codes wanted | load the scheme | this |
    | --- | --- | --- |
    | 28 | 2254 ms | 13 ms |
    | 1,660 | 2176 ms | 16 ms |
    | 46,027 | 2187 ms | 198 ms |
    | 63,404 | 2201 ms | 262 ms |
    | 499,138 (86% of the codelist) | 2241 ms | 2271 ms |

    They converge only when the caller wants nearly the whole scheme, which is
    why there is no threshold here: a branch that buys nothing at the crossover
    and 178x nowhere else is a knob with no setting worth choosing.
    """
    urns = list(urns)
    if not urns:
        return {}
    cur = cursor_for(conn)
    with value_match(cur, urns) as (match, params):
        rows = cur.execute(f"SELECT {_CODE_COLUMNS} FROM sdmx.code WHERE code_urn {match}", params).fetchall()
    return dict(_build_codes(cur, rows, annotations=annotations))


#: What `_build_codes` reads, in its order; the URN is last so a caller keying
#: by something else still has it for the annotation read.
_CODE_COLUMNS: Final = (
    f"code_id, parent_id, {istring_sql('name')}, {istring_sql('description')}, uri, geo_value, geo_cell, code_urn"
)

#: One `_CODE_COLUMNS` row, as the `sdmx.code` DDL types it.
type _CodeRow = tuple[str, str | None, str | None, str | None, str | None, str | None, str | None, str]


def _build_codes(cur: duckdb.DuckDBPyConnection, rows: list[_CodeRow], *, annotations: bool) -> list[tuple[str, Code]]:
    """`(code_urn, Code)` per row of `_CODE_COLUMNS`, annotations read in one batch."""
    anns = annotations_for_urns(cur, [row[-1] for row in rows]) if annotations else {}
    # Links are read with the annotations, and skipped with them (#422).
    links: dict[str, tuple[Link, ...]] = links_for_urns(cur, [row[-1] for row in rows]) if annotations else {}
    return [
        (
            code_urn,
            code_of_kind(
                id=code_id,
                parent_id=parent_id,
                name=json_to_istring(name),
                description=json_to_istring(description),
                annotations=anns.get(code_urn, Annotations.EMPTY),
                uri=uri,
                links=links.get(code_urn, ()) if links else (),
                value=geo_value,
                geo_cell=geo_cell,
            ),
        )
        for code_id, parent_id, name, description, uri, geo_value, geo_cell, code_urn in rows
    ]


def geo_kind(conn: duckdb.DuckDBPyConnection, codelist_urn: str) -> tuple[str | None, str | None]:
    """A codelist's geographic kind and grid definition; ``(None, None)`` for a plain one (#422)."""
    row: tuple[str, str | None] | None = first(
        conn.execute("SELECT geo_type, grid_definition FROM sdmx.geo_codelist WHERE codelist_urn = ?", [codelist_urn])
    )
    return (None, None) if row is None else row


def codes_by_id(
    conn: duckdb.DuckDBPyConnection, codelist_urn: str, ids: Collection[str], *, annotations: bool = True
) -> dict[str, Code]:
    """`code_id` → `Code` within one codelist, for exactly the ids asked for.

    `codes_by_urn` keyed by what a caller holding *values* has: an id, not a
    URN — matched on ``(codelist_urn, code_id)`` directly, so no code URN is
    derived and this cannot disagree with however the writer spelled one. Ids
    the codelist does not hold are absent.

    One read plus the annotation read, whatever the codelist's size — the
    SDMX-JSON data writer's per-component lookup (#410).
    """
    if not ids:
        return {}
    cur = cursor_for(conn)
    with value_match(cur, list(ids)) as (match, params):
        rows = cur.execute(
            f"SELECT {_CODE_COLUMNS} FROM sdmx.code WHERE codelist_urn = ? AND code_id {match}",
            [codelist_urn, *params],
        ).fetchall()
    return {code.id: code for _, code in _build_codes(cur, rows, annotations=annotations)}


def partial_closure(
    conn: duckdb.DuckDBPyConnection, codelist_urn: str, ids: Collection[str]
) -> tuple[set[str], list[str]]:
    """Which of *ids* the codelist holds, and those codes with their ancestors, in codelist order.

    The ancestors come from the ``ancestor_ids`` the writer bakes, so this is
    two reads whatever the codelist's size: no code is built, and no parent
    chain is walked in Python.
    """
    cur = cursor_for(conn)
    with value_match(cur, list(ids)) as (match, params):
        found = {
            str(code_id)
            for (code_id,) in cur.execute(
                f"SELECT code_id FROM sdmx.code WHERE codelist_urn = ? AND code_id {match}",
                [codelist_urn, *params],
            ).fetchall()
        }
        kept = cur.execute(
            f"""
            WITH asked AS (
                SELECT code_id, ancestor_ids FROM sdmx.code WHERE codelist_urn = ? AND code_id {match}
            )
            SELECT code_id FROM sdmx.code
            WHERE codelist_urn = ?
              AND code_id IN (SELECT code_id FROM asked UNION SELECT unnest(ancestor_ids) FROM asked)
            ORDER BY position, code_id
            """,
            [codelist_urn, *params, codelist_urn],
        ).fetchall()
    return found, [str(code_id) for (code_id,) in kept]


def codes_by_ids(conn: duckdb.DuckDBPyConnection, wanted: Mapping[str, Collection[str]]) -> dict[str, dict[str, Code]]:
    """`codes_by_id` for several codelists at once: one code read, one annotation read, one link read (#441).

    The SDMX-JSON data writer asks one codelist per enumerated component, and a
    census DSD has 18: one at a time that was 54 statements and ~79 ms per
    message, where these three take ~11 ms. Each codelist is its own
    ``UNION ALL`` branch, so each keeps the ``codelist_urn = ?`` predicate the
    code table is pruned by; one ``OR``-joined predicate measured 3x slower.

    Args:
        conn: The store.
        wanted: Codelist URN → the code ids wanted from it.

    Returns:
        Codelist URN → ``{code_id: Code}``, for every codelist in *wanted*.
        Ids a codelist does not hold are absent.
    """
    asked = {urn: list(ids) for urn, ids in wanted.items() if ids}
    found: dict[str, dict[str, Code]] = {urn: {} for urn in wanted}
    if not asked:
        return found
    cur = cursor_for(conn)
    with ExitStack() as matches:
        branches: list[str] = []
        params: list[object] = []
        for urn, ids in asked.items():
            match, match_params = matches.enter_context(value_match(cur, ids))
            branches.append(
                f"SELECT {_CODE_COLUMNS}, codelist_urn FROM sdmx.code WHERE codelist_urn = ? AND code_id {match}"
            )
            params += [urn, *match_params]
        rows = cur.execute(" UNION ALL ".join(branches), params).fetchall()
    built = _build_codes(cur, [row[:-1] for row in rows], annotations=True)
    for row, (_, code) in zip(rows, built, strict=True):
        found[row[-1]][code.id] = code
    return found


def resolve_code_keys(
    conn: duckdb.DuckDBPyConnection, keys: Sequence[tuple[object, ...]], *, annotations: bool = True
) -> list[Code]:
    """`(code_urn,)` key rows to `Code` objects, in the order asked for.

    Lives here rather than on `Registry` because `LazyCodelist` needs it too
    and holds a connection, not a registry.

    `annotations=False` skips the join and leaves every `.annotations` empty.
    It defaults to True and should stay that way: a *silently* empty
    `.annotations` is the trade `CodeQuery`'s `with_annotations` offered and
    that this reader deliberately stopped offering, because a caller who did
    not know to ask got wrong data rather than slow data.

    What changed is the size, not the argument. That docstring justified the
    eager read with "they were expensive, and they are not any more" — measured,
    and true at the 10k-code fixture it was measured against. On the 2021 Census Profile's
    579,951-code `CL_GEOGRAPHY` the annotation join plus its grouping is
    **1109 ms of a 3.3 s collect, 32%** — more than building the `Code` objects
    themselves (642 ms) — for 585,675 annotations of which 98% carry no text
    (#381). So the flag is back, at the *call site* rather than as a default:
    the caller states that it does not need them, and an empty `.annotations`
    is then their answer rather than our silence.
    """
    urns = [str(key[0]) for key in keys]
    by_urn = codes_by_urn(conn, urns, annotations=annotations)
    # Re-ordered to the query's order, which a set match does not preserve.
    return [by_urn[urn] for urn in urns if urn in by_urn]


def resolve_hcode_keys(
    conn: duckdb.DuckDBPyConnection, keys: Sequence[tuple[object, ...]], *, annotations: bool = True
) -> list[HierarchicalCode]:
    """`(node_urn,)` key rows to `HierarchicalCode` objects.

    The path language had no resolver for this relation at all, so
    `q.hcode.collect()` refused while `h.nodes.collect()` worked — the gap that
    blocked repointing `LazyHierarchy.nodes`.

    `children` is left empty deliberately. A placement's children are a
    *query* here (`q.hcode.children`), and materialising the subtree per row
    would make `collect()` quadratic on the shape it is most used for.
    `LazyHierarchy.materialize()` is what builds the linked tree.
    """
    urns = [str(key[0]) for key in keys]
    by_urn = hcodes_by_urn(conn, urns, annotations=annotations)
    return [by_urn[urn] for urn in urns if urn in by_urn]


def hcodes_by_urn(
    conn: duckdb.DuckDBPyConnection, urns: Sequence[str], *, annotations: bool = True
) -> dict[str, HierarchicalCode]:
    """The hierarchy nodes with these item URNs, read in one batched query.

    `codes_by_urn`'s counterpart. A node's URN is its hierarchy's item prefix
    plus its *path* (#439), which is also what a reference to it carries, so
    the stored `node_urn` matches a reference directly and resolving one node
    does not load a 499,143-node tree. URNs with no node are absent.
    """
    if not urns:
        return {}
    cur = cursor_for(conn)
    with value_match(cur, urns) as (match, params):
        rows = cur.execute(
            "SELECT node_urn, node_id, code_urn, level_id, valid_from, valid_to, hierarchy_urn, uri, version "
            f"FROM sdmx.hierarchy_node WHERE node_urn {match}",
            params,
        ).fetchall()
    anns = annotations_for_urns(cur, urns) if annotations else {}
    links: dict[str, tuple[Link, ...]] = links_for_urns(cur, urns) if annotations else {}
    levels = levels_by_hierarchy(cur, {str(row[6]) for row in rows if row[3] is not None})
    return {
        str(row[0]): HierarchicalCode(
            id=str(row[1]),
            code=Ref[Code].unresolved(Code.parse_urn(str(row[2]))) if row[2] is not None else None,
            annotations=anns.get(str(row[0]), Annotations.EMPTY),
            valid_from=row[4],
            valid_to=row[5],
            level=_level_for(levels, str(row[6]), row[3]),
            uri=row[7],
            links=links.get(str(row[0]), ()) if links else (),
            version=row[8],
        )
        for row in rows
    }


def _level_for(levels: dict[str, dict[str, Level]], hierarchy_urn: str, level_id: object) -> Level | None:
    """The hierarchy's declared `Level`, or a bare one for an undeclared id.

    This site used to write `Level(id=str(level_id))` unconditionally, which
    was two defects at once. The cost — 499,138 constructions on the 2021 Census Profile's
    largest hierarchy, 28% of a `collect()` — is the same one `ee10df9` removed
    from `LazyHierarchy.materialize()`. The other is worse and invisible in a
    timing: the `Level` it built carried **only an id**, so the same node
    reached through `collect()` had no level name, description or coding
    format, while through `materialize()` it had all three. On the 2021 Census Profile bundle
    that disagreed on every node of every hierarchy.

    The fallback is kept because a node may sit at a level the hierarchy never
    declared, and `materialize()` synthesises one for that case too. It is
    written as an `if` rather than as `dict.get`'s default argument, which
    Python evaluates eagerly — the mistake this function exists to undo.
    """
    if level_id is None:
        return None
    key = str(level_id)
    declared = levels.get(hierarchy_urn)
    if declared is not None and (level := declared.get(key)) is not None:
        return level
    return Level(id=key)


def codelist_extensions(conn: duckdb.DuckDBPyConnection, urn: str) -> tuple[CodelistExtension, ...]:
    """A Codelist's SDMX 3.0 extensions, with their code selections.

    Two queries rather than a join: an extension with no selection must still
    appear, and the members are ordered within their extension, so grouping
    them client-side keeps both without a LEFT JOIN whose NULL row means two
    different things (#160).
    """
    extension_rows = conn.execute(
        """
        SELECT position, extends_urn, prefix, selection_mode
        FROM sdmx.codelist_extension WHERE codelist_urn = ?
        ORDER BY position
        """,
        [urn],
    ).fetchall()
    if not extension_rows:
        return ()

    member_rows = conn.execute(
        """
        SELECT position, value, cascade
        FROM sdmx.code_selection_member WHERE codelist_urn = ?
        ORDER BY position, member_idx
        """,
        [urn],
    ).fetchall()
    members_by_position: dict[int, list[MemberValue]] = {}
    for position, value, cascade in member_rows:
        members_by_position.setdefault(position, []).append(
            MemberValue(value=value, cascade=CascadeSelection(cascade) if cascade else None)
        )

    return tuple(
        CodelistExtension(
            codelist=Ref[Codelist].unresolved(Codelist.parse_urn(extends_urn)),
            prefix=prefix,
            selection=(
                CodeSelection(
                    mode=InclusionMode(selection_mode),
                    members=tuple(members_by_position.get(position, [])),
                )
                if selection_mode
                else None
            ),
        )
        for position, extends_urn, prefix, selection_mode in extension_rows
    )


def build_representation(
    repr_kind: str | None,
    codelist_urn: str | None,
    *,
    text_type: str | None,
    min_length: int | None,
    max_length: int | None,
    # DOUBLE columns, and `Facet` declares these `float | None`. They were
    # annotated `str | None`, which no caller ever checked: both call sites
    # unpack a raw duckdb row, so `Any` flowed in and the mismatch was inert.
    min_value: float | None,
    max_value: float | None,
    decimals: int | None,
    pattern: str | None,
    is_sequence: bool | None,
    start_value: float | None,
    end_value: float | None,
    is_multilingual: bool | None = None,
    interval: float | None = None,
    time_interval: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    sentinel_values: object = None,
) -> Representation:
    """Build a representation (a codelist or value-list Ref, or a Facet) from component row fields.

    `sentinel_values` is the raw ``sentinel_values`` column (#422) — a list of
    ``{value, name, description}`` structs, or NULL — for a text format's.
    """
    if repr_kind == "enumeration" and codelist_urn:
        return enumeration_ref(codelist_urn)
    if repr_kind == "facet":
        return Facet(
            type=text_type,
            min_length=min_length,
            max_length=max_length,
            min_value=min_value,
            max_value=max_value,
            decimals=decimals,
            pattern=pattern,
            is_sequence=is_sequence,
            start_value=start_value,
            end_value=end_value,
            # A NULL is unstated, which is not multilingual (#503).
            is_multilingual=bool(is_multilingual),
            interval=interval,
            time_interval=time_interval,
            start_time=start_time,
            end_time=end_time,
            sentinel_values=_sentinels(sentinel_values),
        )
    return None


def _map_istring(value: object) -> InternationalString:
    """A MAP read inside a struct — a plain dict, unlike `istring_sql`'s JSON text."""
    if not isinstance(value, dict) or not value:
        return InternationalString.EMPTY
    return InternationalString.of({str(k): str(v) for k, v in value.items()})


def _sentinels(value: object) -> tuple[SentinelValue, ...]:
    """The ``sentinel_values`` column as `SentinelValue`s; ``()`` for NULL."""
    if not isinstance(value, list):
        return ()
    return tuple(
        SentinelValue(
            value=str(s["value"]),
            name=_map_istring(s["name"]),
            description=_map_istring(s["description"]),
        )
        for s in value
        if isinstance(s, dict)
    )


def build_enumeration_format(
    *,
    text_type: str | None,
    min_length: int | None,
    max_length: int | None,
    min_value: float | None,
    max_value: float | None,
    decimals: int | None,
    pattern: str | None,
    is_sequence: bool | None,
    start_value: float | None,
    end_value: float | None,
    is_multilingual: bool | None,
    interval: float | None = None,
    time_interval: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> Facet | None:
    """A component's `enumeration_format` from its `enum_*` columns, or None (#438).

    None when every column is NULL: no format was stored. Built through
    `build_representation`'s facet arm, so the two read one facet the same way.
    """
    fields = (
        text_type,
        min_length,
        max_length,
        min_value,
        max_value,
        decimals,
        pattern,
        is_sequence,
        start_value,
        end_value,
        is_multilingual,
        interval,
        time_interval,
        start_time,
        end_time,
    )
    if all(value is None for value in fields):
        return None
    facet = build_representation(
        "facet",
        None,
        text_type=text_type,
        min_length=min_length,
        max_length=max_length,
        min_value=min_value,
        max_value=max_value,
        decimals=decimals,
        pattern=pattern,
        is_sequence=is_sequence,
        start_value=start_value,
        end_value=end_value,
        is_multilingual=is_multilingual,
        interval=interval,
        time_interval=time_interval,
        start_time=start_time,
        end_time=end_time,
    )
    return facet if isinstance(facet, Facet) else None


def read_levels(conn: duckdb.DuckDBPyConnection, urn: str) -> tuple[Level, ...]:
    """A hierarchy's declared levels, with their coding formats.

    Read in one place because there are now three callers — the eager reader,
    the lazy proxy, and `resolve_hcode_keys` below — and the first two had
    already diverged in what they selected. A level's `coding_format` was
    dropped by both (#160); adding it to one would have left `get(eager=True)`
    and `get()` answering differently, which is the shape #322 was.

    It lives here rather than in `readers` because `resolve` is the lower
    module — `readers` imports from it, not the other way round — and the third
    caller is here. Re-reading the same rows into a second, thinner `Level` was
    the alternative, and it is precisely the divergence this docstring's first
    paragraph is about.

    The facet columns are named from `FACET_COLUMN_NAMES`, so the SELECT
    cannot list a set the table does not have.
    """
    columns = ", ".join(FACET_COLUMN_NAMES)
    rows = conn.execute(
        f"""
        SELECT level_id, name, description, {columns}, uri
        -- Schema-qualified, unlike the unqualified spelling this carried in
        -- `readers`: that worked only because every caller there holds a
        -- connection with `sdmx` on the search path, and `resolve`'s callers
        -- do not. The bundle tier caught it (#379); no unit test could, since
        -- they all use a search-path connection.
        FROM sdmx.hierarchy_level WHERE hierarchy_urn = ?
        ORDER BY level_idx
        """,
        [urn],
    ).fetchall()
    # A level's annotations are owned by its own item URN (#442) — one read
    # for the hierarchy's levels, skipped when it declares none, which is
    # nearly every hierarchy.
    anns = annotations_for_urns(conn, [level_urn(urn, str(row[0])) for row in rows]) if rows else {}
    links = links_for_urns(conn, [level_urn(urn, str(row[0])) for row in rows]) if rows else {}
    levels: list[Level] = []
    for row in rows:
        # Positional, in `FACET_COLUMN_NAMES` order — which is the order the
        # SELECT above asks for, so the two cannot disagree without the
        # round-trip failing loudly rather than filling the wrong fields.
        (
            level_id,
            name,
            description,
            repr_kind,
            codelist_urn,
            text_type,
            min_length,
            max_length,
            min_value,
            max_value,
            decimals,
            pattern,
            is_sequence,
            start_value,
            end_value,
            is_multilingual,
            interval,
            time_interval,
            start_time,
            end_time,
            uri,
        ) = row
        # A Level's coding format is a plain Facet — SDMX gives it no
        # enumerated arm — so an `enumeration` row here would be a writer bug,
        # and is dropped rather than coerced.
        representation = build_representation(
            repr_kind,
            codelist_urn,
            text_type=text_type,
            min_length=min_length,
            max_length=max_length,
            min_value=min_value,
            max_value=max_value,
            decimals=decimals,
            pattern=pattern,
            is_sequence=is_sequence,
            start_value=start_value,
            end_value=end_value,
            is_multilingual=is_multilingual,
            interval=interval,
            time_interval=time_interval,
            start_time=start_time,
            end_time=end_time,
        )
        levels.append(
            Level(
                id=str(level_id),
                name=map_to_istring(name),
                description=map_to_istring(description),
                coding_format=representation if isinstance(representation, Facet) else None,
                annotations=anns.get(level_urn(urn, str(level_id)), Annotations.EMPTY),
                uri=uri,
                links=links.get(level_urn(urn, str(level_id)), ()),
            )
        )
    return tuple(levels)


def level_urn(hierarchy_urn: str, level_id: str) -> str:
    """The item URN a hierarchy's `Level` is stored under — its annotations' owner.

    One spelling for the writer and `read_levels` both (#442): a Level had no
    owner URN at all until its annotations needed one, and two hand-written
    copies of an item-URN grammar is how `_component_urn` and `_make_item_urn`
    came to need a comment saying they mirror each other. The class pair is
    the Level's own declaration, as for every other item.
    """
    parsed = SdmxUrn.parse(hierarchy_urn)
    return str(
        SdmxUrn.make(
            Level,
            package=Level.sdmx_package,
            artefact_class=Level.sdmx_class,
            agency=parsed.agency,
            id=parsed.id,
            version=parsed.version,
            item_id=level_id,
        )
    )


def levels_by_hierarchy(conn: duckdb.DuckDBPyConnection, urns: Collection[str]) -> dict[str, dict[str, Level]]:
    """Declared levels for several hierarchies, keyed hierarchy urn then level id.

    Nested rather than flat because a level id is unique *within* a hierarchy
    and not across them — which is why `hierarchy_level` is keyed
    `(hierarchy_urn, level_id)`.

    One `read_levels` call per hierarchy rather than one query for the batch.
    That is N round-trips where a single `WHERE hierarchy_urn IN (…)` would do
    one, and it is the right trade here: `resolve_hcode_keys` is the caller,
    its batch is a set of node URNs, and those come from one hierarchy in every
    shape the query language produces — `q.hcode` filtered by hierarchy, or
    `h.nodes`. A batched form would have to unpack the sixteen-column
    `hierarchy_level` row a second time, and that duplication is exactly what
    `read_levels`'s docstring is about. Revisit if a cross-hierarchy collect
    ever becomes a real shape; the signature already takes a collection.
    """
    return {urn: {level.id: level for level in read_levels(conn, urn)} for urn in sorted(urns)}
