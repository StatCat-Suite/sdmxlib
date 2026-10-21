"""`collect(annotations=False)` — what it skips, and what it refuses (#381).

`resolve_code_keys` loaded annotations eagerly and unconditionally, and said
why: `CodeQuery` had made them opt-in via `with_annotations` "because they were
expensive, and they are not any more."

That was measured, and it was true — against a 10k-code fixture, the largest
anything in `tests/bench/` reached before #379. On the census bundle's 579,951-code
`CL_GEOGRAPHY` the annotation join plus its grouping is **1109 ms of a 3.3 s
collect — 32%**, more than building the `Code` objects themselves, for 585,675
annotations of which 98% carry no text.

So the flag is back, with the difference that decided it: **at the call site,
not as a default.** A silently empty `.annotations` is wrong data rather than
fast data, and a caller who did not know to ask must never receive it. Passing
the flag is the caller saying they know.

The assertions here are **counts of SQL round-trips**, per CLAUDE.md — the
defect is work done, and a wall-clock assertion on a `:memory:` fixture would
measure nothing at all.
"""

from collections.abc import Iterator

import pytest

import sdmxlib as sl
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.model.organisation import Agency
from sdmxlib.query.accessors import q
from sdmxlib.storage import resolve
from tests.support.factories import code_urn

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """A codelist and a hierarchy over it, every item annotated.

    Annotated on *every* item deliberately: a fixture where only some items
    carry one would let an implementation that drops annotations at random
    pass the "they are absent when opted out" test.
    """
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            Codelist(
                id="CL_GEO",
                maintainer=Agency(id="STATCAN"),
                version="1.0",
                name={"en": "Geography"},
                codes=[
                    Code(
                        id=code_id,
                        name={"en": code_id},
                        annotations=Annotations([Annotation(title=code_id, type="alt_geo_code")]),
                    )
                    for code_id in ("CA", "ON", "QC")
                ],
            )
        )
        registry.add(
            Hierarchy(
                id="H_GEO",
                maintainer=Agency(id="STATCAN"),
                version="1.0",
                name={"en": "Geography"},
                tree=[
                    HierarchicalCode(
                        id="CA",
                        code=code_urn("CL", "CA"),
                        annotations=Annotations([Annotation(title="CA", type="alt_geo_code")]),
                        children=[
                            HierarchicalCode(
                                id=node_id,
                                code=code_urn("CL", node_id),
                                annotations=Annotations([Annotation(title=node_id, type="alt_geo_code")]),
                            )
                            for node_id in ("ON", "QC")
                        ],
                    )
                ],
            )
        )
        yield registry


def _count_annotation_reads(monkeypatch) -> list[int]:
    """Record each call to `annotations_for_urns`, and how many urns it got.

    Counting the read rather than timing it: the saving is a round-trip that
    either happens or does not, and on a three-code fixture the clock cannot
    tell. Spying on `annotations_for_urns` rather than on the connection
    because DuckDB's `execute` is a read-only attribute — and because this is
    the seam that matters: it *is* the annotation round-trip, so a refactor
    that moved the SQL elsewhere but kept the call still counts.
    """
    seen: list[int] = []
    original = resolve.annotations_for_urns

    def spy(cur, urns):
        seen.append(len(list(urns)))
        return original(cur, urns)

    monkeypatch.setattr(resolve, "annotations_for_urns", spy)
    return seen


# ── what it skips ─────────────────────────────────────────────────────────────


def test_codes_default_to_carrying_their_annotations(reg):
    """The default must not change. This is the regression guard on that."""
    codes = reg.query(q.code).filter(q.code.id == "ON").collect()
    assert len(codes) == 1
    assert [a.type for a in codes[0].annotations] == ["alt_geo_code"]


def test_opting_out_runs_no_annotation_query(reg, monkeypatch):
    seen = _count_annotation_reads(monkeypatch)
    reg.query(q.code).filter(q.code.codelist.id == "CL_GEO").collect(annotations=False)
    assert seen == [], f"expected no annotation read, got {len(seen)}"


def test_not_opting_out_runs_exactly_one_annotation_query(reg, monkeypatch):
    """One, not one per code — the batching #72/#73 established still holds."""
    seen = _count_annotation_reads(monkeypatch)
    reg.query(q.code).filter(q.code.codelist.id == "CL_GEO").collect()
    assert len(seen) == 1, f"expected one batched annotation read, got {len(seen)}"
    assert seen == [3], "the one read must cover all three codes, not one code"


def test_opting_out_leaves_annotations_empty_not_wrong(reg):
    """Empty, and empty for every code — not stale, not partial."""
    codes = reg.query(q.code).filter(q.code.codelist.id == "CL_GEO").collect(annotations=False)
    assert len(codes) == 3
    assert all(len(c.annotations) == 0 for c in codes)


def test_opting_out_changes_nothing_else_about_the_objects(reg):
    """The saving must be *only* the annotations.

    Compared field by field against the same collect with annotations on, so a
    flag that quietly dropped a name or a parent id fails here rather than
    being noticed by a consumer.
    """
    with_anns = reg.query(q.code).filter(q.code.codelist.id == "CL_GEO").collect()
    without = reg.query(q.code).filter(q.code.codelist.id == "CL_GEO").collect(annotations=False)
    assert [(c.id, c.name, c.description, c.parent_id) for c in with_anns] == [
        (c.id, c.name, c.description, c.parent_id) for c in without
    ]


def test_hierarchy_nodes_opt_out_the_same_way(reg, monkeypatch):
    seen = _count_annotation_reads(monkeypatch)
    nodes = reg.query(q.hcode).filter(q.hcode.hierarchy.id == "H_GEO").collect(annotations=False)
    assert len(nodes) == 3
    assert all(len(n.annotations) == 0 for n in nodes)
    assert seen == []


def test_hierarchy_nodes_default_to_carrying_them(reg):
    nodes = reg.query(q.hcode).filter(q.hcode.hierarchy.id == "H_GEO").collect()
    assert all(len(n.annotations) == 1 for n in nodes)


# ── what it refuses ───────────────────────────────────────────────────────────


def test_a_root_that_cannot_honour_it_raises_rather_than_ignoring_it(reg):
    """A silently ignored flag is worse than a refused one.

    The caller measures no improvement and has no way to learn why. So roots
    whose objects come from a reader that always reads annotations say so.
    """
    with pytest.raises(ValueError, match=r"not supported on q\.artefact"):
        reg.query(q.codelist).collect(annotations=False)


def test_the_refusal_names_the_roots_that_do_support_it(reg):
    """An error that only says no costs the reader a search."""
    with pytest.raises(ValueError, match=r"q\.code and q\.hcode"):
        reg.query(q.codelist).collect(annotations=False)


def test_a_refusing_root_still_collects_normally(reg):
    """The refusal is about the flag, not about the root."""
    assert len(reg.query(q.codelist).collect()) == 1
    assert len(reg.query(q.codelist).collect(annotations=True)) == 1
