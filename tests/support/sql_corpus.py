"""A fixed corpus of queries and the SQL they compile to (#267, rebuilt #304).

For a refactor that must not change behaviour, comparing *emitted SQL* is
sharper than timing it: if the SQL is byte-identical then execution cannot
regress, and the check is exact rather than statistical.

This is deliberately **not** the golden-file strategy rejected in %13. A golden
file used as the primary test passes when the semantics are wrong, which is why
the differential properties exist. Used as a *migration equivalence check* it
answers a different and narrower question — "did this refactor change
anything?" — for which byte-identity is precisely right.

Usage across a refactor::

    git stash && python -m tests.support.sql_corpus > /tmp/before.txt
    git stash pop && python -m tests.support.sql_corpus > /tmp/after.txt
    diff /tmp/before.txt /tmp/after.txt

An empty diff means every predicate kind, every column kind and every hop shape
still produces the same statement and the same binds.

## Why it was rebuilt

The original was written for the four hand-written DSLs and imported
`FrameQuery` / `CodeQuery` / `HierarchyNodeQuery`, so #306 deleted it with
them. That left the compiler about to be restructured into an IR (#312) with
**no** equivalence check — the one tool that would show the restructure changed
nothing. This version covers the path language instead.

## It is guarded, not just written

The original hand-listed its cases, and a hand-listed corpus goes stale exactly
when it matters: a predicate kind added later is absent, the diff stays empty,
and the empty diff reads as proof. `tests/unit/test_sql_corpus.py` derives the
concrete `Pred` subclasses off `_expr` and fails when one never appears here —
the same shape as the oracles' `test_every_predicate_kind_is_covered`.

Test-only; not packaged with sdmxlib.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Protocol

import duckdb

from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ItemList
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import AttachmentLevel, Attribute, DataStructure, Dimension, Measure
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, Level
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import DataType, Facet
from sdmxlib.query.accessors import q
from sdmxlib.storage.schema import create_schema
from sdmxlib.storage.writers import (
    put_codelist,
    put_concept_scheme,
    put_data_structure,
    put_dataflow,
    put_hierarchy,
)
from tests.support.factories import code_ref, concept_ref

if TYPE_CHECKING:
    from sdmxlib.query._compile import CompiledQuery
    from sdmxlib.query._expr import Cols, RowKind
    from sdmxlib.query.accessors import CodeRow, HCodeRow
    from sdmxlib.query.query import Query


class _Compiles(Protocol):
    """Anything the corpus emits SQL for — a `Query`, or a projection over one."""

    def to_sql(self) -> CompiledQuery: ...


_LEVEL = Level(id="Region", name={"en": "Region"})
_ANNS = Annotations([Annotation(type="note", title="x", text={"en": "y"})])
_CL_URN = str(Codelist.urn_for(agency="AG", id="CL", version="1.0"))
_DSD_URN = DataStructure.urn_for(agency="AG", id="DSD", version="1.0")


def _codelist() -> Codelist:
    return Codelist(
        id="CL",
        maintainer="AG",
        version="1.0",
        name={"en": "n"},
        codes=ItemList(
            [
                Code(id="A", name={"en": "Alpha"}, annotations=_ANNS),
                Code(id="B", name={"en": "Beta"}, parent_id="A"),
            ]
        ),
    )


def _hierarchy() -> Hierarchy:
    return Hierarchy(
        id="H",
        maintainer="AG",
        version="1.0",
        name={"en": "n"},
        has_formal_levels=True,
        levels=ItemList([_LEVEL]),
        tree=ItemList(
            [
                HierarchicalCode(
                    id="N1",
                    level=_LEVEL,
                    annotations=_ANNS,
                    children=ItemList([HierarchicalCode(id="N2", code=code_ref("N2"))]),
                    code=code_ref("N1"),
                )
            ]
        ),
    )


def _concept_scheme() -> ConceptScheme:
    return ConceptScheme(
        id="CS",
        maintainer="AG",
        version="1.0",
        name={"en": "n"},
        concepts=ItemList([Concept(id="REF_AREA", name={"en": "Area"})]),
    )


def _dsd() -> DataStructure:
    return DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name={"en": "n"},
        dimensions=ItemList(
            [
                Dimension(
                    id="REF_AREA",
                    representation=Ref[Codelist].unresolved(Codelist.urn_for(agency="AG", id="CL", version="1.0")),
                    concept=concept_ref("REF_AREA"),
                ),
                Dimension(
                    id="TITLE", representation=Facet(type=DataType.String, max_length=200), concept=concept_ref("TITLE")
                ),
            ]
        ),
        attributes=ItemList(
            [Attribute(attachment=AttachmentLevel.DATASET, id="OBS_STATUS", concept=concept_ref("OBS_STATUS"))]
        ),
        measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
    )


def _dataflow() -> Dataflow:
    return Dataflow(
        id="DF",
        maintainer="AG",
        version="1.0",
        name={"en": "n"},
        structure=Ref[DataStructure].unresolved(_DSD_URN),
    )


# ── the cases ─────────────────────────────────────────────────────────────
#
# Grouped by what they exercise rather than by root, because the thing a
# refactor breaks is an *emitter*, and one emitter serves every root.


def scalar_cases() -> Iterator[tuple[str, _Compiles]]:
    """Every leaf predicate kind, over the columns each one applies to."""
    c = q.code
    cases: list[tuple[str, Callable[[], _Compiles]]] = [
        ("eq/id", lambda: _codes().filter(c.id == "A")),
        ("ne/id", lambda: _codes().filter(c.id != "A")),
        ("eq/depth", lambda: _codes().filter(c.depth == 1)),
        ("eq/label", lambda: _codes().filter(c.name["en"] == "Alpha")),
        ("eq/side-label", lambda: _q(q.dsd).filter(q.dsd.name["en"] == "n")),
        ("eq/bool", lambda: _q(q.dsd).filter(q.dsd.is_final == False)),  # noqa: E712
        ("cmp/lt", lambda: _codes().filter(c.depth < 2)),
        ("cmp/le", lambda: _codes().filter(c.depth <= 2)),
        ("cmp/gt", lambda: _codes().filter(c.depth > 2)),
        ("cmp/ge", lambda: _codes().filter(c.depth >= 2)),
        ("isnull", lambda: _codes().filter(c.parent.id.is_null())),
        ("isnull/not", lambda: _codes().filter(c.parent.id.is_not_null())),
        ("match/contains", lambda: _codes().filter(c.id.str.contains("A_%"))),
        ("match/starts", lambda: _codes().filter(c.id.str.starts_with("A"))),
        ("match/ends", lambda: _codes().filter(c.id.str.ends_with("A"))),
        ("match/caseless", lambda: _codes().filter(c.id.str.caseless.contains("A_%"))),
        ("eq/caseless", lambda: _codes().filter(c.id.str.caseless == "A")),
        ("eq/caseless-not", lambda: _codes().filter(c.id.str.caseless != "A")),
        ("match/unaccented", lambda: _codes().filter(c.name["en"].str.unaccented.contains("Al"))),
        ("match/folded", lambda: _codes().filter(c.name["en"].str.folded.contains("Al"))),
        ("inset/empty", lambda: _codes().filter(c.id.is_in([]))),
        ("inset/one", lambda: _codes().filter(c.id.is_in(["A"]))),
        ("inset/many", lambda: _codes().filter(c.id.is_in(["A", "B", "C"]))),
        ("arrayhas", lambda: _q(q.component).filter(q.component.concept_roles.has("REF_AREA"))),
        ("expr/num", lambda: _codes().filter(c.depth + 1 == 2)),
        ("expr/str", lambda: _codes().filter(c.id + ":" == "A:")),
    ]
    for name, build in cases:
        yield name, build()


def hop_cases() -> Iterator[tuple[str, _Compiles]]:
    """Every hop shape: to-one, to-many, tree, compound, extension."""
    c, n = q.code, q.hcode
    cases: list[tuple[str, Callable[[], _Compiles]]] = [
        ("to-one/trimmed", lambda: _codes().filter(c.parent.id == "A")),
        ("to-one/joined", lambda: _codes().filter(c.codelist.agency == "AG")),
        ("to-many/dot", lambda: _codes().filter(c.children.id == "B")),
        ("to-many/any", lambda: _codes().filter(c.children.any(c.id == "B", c.depth == 1))),
        ("to-many/every", lambda: _codes().filter(c.children.every(c.id != "Z"))),
        ("to-many/count", lambda: _codes().filter(c.children.count() == 0)),
        ("tree/ancestors", lambda: _codes().filter(c.ancestors.id == "A")),
        ("tree/descendants", lambda: _codes().filter(c.descendants.id == "B")),
        ("annotations/type", lambda: _codes().filter(c.annotations.type == "note")),
        (
            "annotations/any",
            lambda: _codes().filter(c.annotations.any(q.annotation.type == "note", q.annotation.title == "x")),
        ),
        ("annotations/text", lambda: _codes().filter(c.annotations.any(q.annotation.text["en"].is_not_null()))),
        ("hcode/level", lambda: _nodes().filter(n.level.id == "Region")),
        ("hcode/ancestors", lambda: _nodes().filter(n.ancestors.id == "N1")),
        ("compound/dataflow-dsd", lambda: _q(q.dataflow).filter(q.dataflow.dsd.id == "DSD")),
        ("compound/deep", lambda: _q(q.dataflow).filter(q.dataflow.dsd.components.concept.id == "REF_AREA")),
        ("component/facet", lambda: _q(q.component).filter(q.component.facet.max_length > 100)),
        ("component/codelist", lambda: _q(q.component).filter(q.component.codelist.id == "CL")),
    ]
    for name, build in cases:
        yield name, build()


def combinator_cases() -> Iterator[tuple[str, _Compiles]]:
    """`&`, `|`, `~`, and the flattening."""
    c = q.code
    a, b, d = c.id == "A", c.id == "B", c.depth == 1
    cases: list[tuple[str, Callable[[], _Compiles]]] = [
        ("bool/and", lambda: _codes().filter(a & b)),
        ("bool/or", lambda: _codes().filter(a | b)),
        ("bool/flatten-and", lambda: _codes().filter(a & b & d)),
        ("bool/flatten-or", lambda: _codes().filter(a | b | d)),
        ("bool/mixed", lambda: _codes().filter((a & b) | d)),
        ("bool/not", lambda: _codes().filter(~a)),
        ("bool/not-exists", lambda: _codes().filter(~(c.annotations.type == "note"))),
        ("filter/several", lambda: _codes().filter(a, d)),
        ("filter/chained", lambda: _codes().filter(a).filter(d)),
    ]
    for name, build in cases:
        yield name, build()


def clause_cases() -> Iterator[tuple[str, _Compiles]]:
    """Query-level clauses: order, slice, sets, cursor, windows."""
    c = q.code
    cases: list[tuple[str, Callable[[], _Compiles]]] = [
        ("sort/id", lambda: _codes().order_by(c.id)),
        ("sort/desc", lambda: _codes().order_by(c.depth.desc())),
        ("sort/label", lambda: _codes().order_by(c.name["en"])),
        ("sort/several", lambda: _codes().order_by(c.depth, c.id.desc())),
        ("limit", lambda: _codes().limit(5)),
        ("offset", lambda: _codes().offset(2)),
        ("slice", lambda: _codes().offset(2).limit(3)),
        ("intersect", lambda: _codes().intersect(_codes().filter(c.depth == 0))),
        ("difference", lambda: _codes().difference(_codes().filter(c.depth == 0))),
        ("inset/relation", lambda: _codes().filter(c.id.is_in(_codes().select(c.id)))),
        (
            "qualify/rank",
            lambda: _q(q.dataflow).qualify(q.dataflow.version.rank(descending=True).over(q.dataflow.id) == 1),
        ),
        ("qualify/row-number", lambda: _codes().qualify(c.id.row_number().over(c.depth) == 1)),
        ("chain", lambda: _codes().filter(c.id.str.starts_with("A")).filter(c.depth <= 2).order_by(c.id).limit(3)),
    ]
    for name, build in cases:
        yield name, build()


def frame_cases() -> Iterator[tuple[str, _Compiles]]:
    """Projection and grouping — the second SQL path over the same rows."""
    c = q.code
    n = c.id.count()
    cases: list[tuple[str, Callable[[], _Compiles]]] = [
        ("select/one", lambda: _codes().select(c.id)),
        # `.alias` because `c.id` and `c.parent.id` both project as `id`
        # since #363 — the corpus carries the escape as well as the shape.
        ("select/several", lambda: _codes().select(c.id, c.depth, c.parent.id.alias("parent"))),
        ("select/named", lambda: _codes().select(geo=c.id, label=c.name["en"])),
        ("select/two-langs", lambda: _codes().select(c.name["en"], c.name["fr"])),
        ("select/filtered", lambda: _codes().filter(c.depth == 1).select(c.id)),
        ("select/sliced", lambda: _codes().limit(3).select(c.id)),
        ("select/unique", lambda: _codes().select(c.depth).unique()),
        ("select/agg", lambda: _codes().select(c.id, kids=c.children.id.list())),
        ("select/agg-count", lambda: _codes().select(c.id, n=c.children.count())),
        ("select/agg-structs", lambda: _codes().select(c.id, kids=c.children.list(c.children.id, c.children.depth))),
        ("select/order", lambda: _codes().select(c.id, c.depth).order_by(c.depth)),
        ("group/len", lambda: _codes().group_by(c.depth).len()),
        ("group/agg", lambda: _codes().group_by(c.parent.id).agg(n=n)),
        ("group/label-key", lambda: _codes().group_by(c.name["en"]).len()),
        ("group/sort-agg", lambda: _codes().group_by(c.parent.id).agg(n=n).order_by(n.desc())),
        ("group/top-n", lambda: _codes().group_by(c.parent.id).agg(n=n).order_by(n.desc()).limit(5)),
        (
            "component/frame",
            lambda: _q(q.component).select(q.component.id, dsd=q.component.dsd.id, cl=q.component.codelist.id),
        ),
    ]
    for name, build in cases:
        yield name, build()


# ── wiring ────────────────────────────────────────────────────────────────

_CONN: duckdb.DuckDBPyConnection | None = None


def _q[R: RowKind, A](root: Cols[R, A]) -> Query[R, A]:
    from sdmxlib.query.query import build

    assert _CONN is not None
    return build(root, _CONN, None)


def _codes() -> Query[CodeRow, Code]:
    return _q(q.code).filter(q.code.codelist.urn == _CL_URN)


def _nodes() -> Query[HCodeRow, HierarchicalCode]:
    return _q(q.hcode)


def _sections() -> Iterator[tuple[str, Iterator[tuple[str, _Compiles]]]]:
    yield "scalar", scalar_cases()
    yield "hop", hop_cases()
    yield "bool", combinator_cases()
    yield "clause", clause_cases()
    yield "frame", frame_cases()


def dump() -> str:
    """Every case's emitted ``(sql, params)``, normalised for diffing.

    One line moved in #311: `qualify/rank` orders by `version_sort` rather
    than `version`, because ranking text puts `1.9` after `1.10`. That the
    diff was exactly one line is the evidence the change was scoped — an
    earlier cut also dropped the language bind from every label's ORDER BY,
    and this is what caught it.
    """
    global _CONN  # noqa: PLW0603
    _CONN = create_schema(duckdb.connect(":memory:"))
    put_codelist(_CONN, _codelist())
    put_hierarchy(_CONN, _hierarchy())
    put_concept_scheme(_CONN, _concept_scheme())
    put_data_structure(_CONN, _dsd())
    put_dataflow(_CONN, _dataflow())

    lines: list[str] = []
    for section, cases in _sections():
        for label, query in cases:
            compiled = query.to_sql()
            sql, params = compiled.sql, compiled.params
            lines.append(f"{section:7} {label:26} | {' '.join(sql.split())}")
            lines.append(f"{section:7} {label:26} | params={list(params)}")
    _CONN.close()
    _CONN = None
    return "\n".join(lines)


if __name__ == "__main__":
    print(dump())  # noqa: T201
