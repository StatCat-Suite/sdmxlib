"""Refusing `filter()` / `order_by()` on an already-sliced query (#296, #293).

Every clause compiles into one ``SELECT``, so a `filter` added after a `limit`
lands in the ``WHERE`` and runs *before* it. That is not a refinement of the
query it was added to — it is a different query:

    q.limit(1)                    ['0']
    q.limit(1).filter(id != '0')  ['1']    <- a row the first did not contain

Which makes it a hard violation of the refinement principle rather than merely
a surprise, and it was found by the property that states the principle, not by
reading the code. #296 was filed as follow-up work and pulled forward into #293
once the property proved the principle was false without it.

Both readings are defensible — "filter the whole set, then cap" and "of the
first n, the ones matching" — so the DSL refuses instead of picking one
silently. Django does the same (`Cannot filter a query once a slice has been
taken`); unlike Django we can name the other reading, because `is_in` already
expresses it.

The properties live in `test_properties_query_laws`. This file pins what a
generator cannot: the message content, and that the spelling it recommends
actually runs.
"""

import pytest

from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.query.accessors import q
from sdmxlib.query.query import SliceError
from tests.support.factories import code_urn, concept_ref
from tests.support.narrow import present

pytestmark = pytest.mark.unit


@pytest.fixture
def codes():
    cl = Codelist(
        id="CL",
        maintainer="AG",
        version="1.0",
        name={"en": "Codes"},
        codes=[
            Code(id="A", name={"en": "a"}),
            Code(id="B", name={"en": "b"}, parent_id="A"),
            Code(id="C", name={"en": "c"}, parent_id="A"),
        ],
    )
    with LocalRegistry(":memory:") as reg:
        reg.add(cl)
        yield present(reg.get(Codelist.urn_for(agency="AG", id="CL", version="1.0"))).codes


# ── What is refused ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "slicer",
    [lambda query: query.limit(2), lambda query: query.offset(1), lambda query: query.offset(1).limit(2)],
    ids=["limit", "offset", "offset+limit"],
)
@pytest.mark.parametrize(
    "clause",
    [
        lambda query: query.filter(q.code.id == "A"),
        lambda query: query.order_by(q.code.id),
        lambda query: query.qualify(q.code.id.rank().over(q.code.depth) == 1),
    ],
    ids=["filter", "order_by", "qualify"],
)
def test_a_row_clause_after_a_slice_is_refused(codes, slicer, clause) -> None:
    """All three clauses, after all three ways of slicing.

    `qualify` is included because it is the clause added since: a guard that
    covers only the clauses present when it was written is the shape this
    milestone kept finding.
    through the same guard, and a future one-shot shorthand that bypassed
    `filter` would fail here.
    """
    with pytest.raises(SliceError, match="has been sliced"):
        clause(slicer(codes))


def test_the_legal_orders_are_untouched(codes) -> None:
    """The guard is narrow: everything before the slice still composes.

    Worth asserting because a guard that over-fires is worse than the ambiguity
    it removes — this is the ordering people actually write.
    """
    assert codes.filter(q.code.parent.id == "A").limit(1).ids() == ["B"]
    assert codes.order_by(q.code.id.desc()).limit(1).ids() == ["C"]
    assert codes.filter(q.code.ancestors.id == "A").offset(1).ids() == ["C"]
    assert codes.filter(q.code.parent.id == "A").order_by(q.code.id).offset(1).limit(1).ids() == ["C"]


def test_slicing_a_sliced_query_is_still_allowed(codes) -> None:
    """`limit` and `offset` after a slice are refinements and stay legal.

    They are what paging is made of, so refusing them would defeat #264.
    """
    assert codes.limit(2).limit(1).ids() == ["A"]
    assert codes.offset(1).offset(2).ids() == ["C"]
    assert codes.offset(1).limit(1).ids() == ["B"]


# `test_annotation_hydration_after_a_slice_is_allowed` went with
# `with_annotations` (#306). Hydration is no longer a query clause at all —
# `collect()` always returns whole objects — so there is nothing left to
# mistake for a row clause.


# ── The message ──────────────────────────────────────────────────────────────


def test_the_message_names_the_clause_and_the_slice(codes) -> None:
    with pytest.raises(SliceError, match="has been sliced") as exc:
        codes.limit(2).filter(q.code.id == "A")
    text = str(exc.value)
    assert "filter()" in text

    with pytest.raises(SliceError, match="has been sliced") as exc:
        codes.offset(1).order_by(q.code.id)
    text = str(exc.value)
    assert "order_by()" in text


def test_the_message_recommends_a_spelling_that_actually_runs(codes) -> None:
    """The nested form the error suggests must work, not merely read plausibly.

    This is the difference between an error that unblocks someone and one that
    just says no. Django's can only say no; ours points at `is_in`, so the
    pointer has to be verified rather than string-matched.
    """
    with pytest.raises(SliceError, match="has been sliced") as exc:
        codes.limit(2).filter(q.code.id == "A")
    assert "is_in" in str(exc.value)

    # The recommended nesting, run for real: "of the first two, those under A".
    first_two = codes.limit(2)
    nested = codes.filter(q.code.id.is_in(first_two.select(q.code.id))).filter(q.code.parent.id == "A")
    assert nested.ids() == ["B"]

    # And it differs from the refused spelling's would-be answer, which is the
    # whole reason the two cannot be conflated.
    assert codes.filter(q.code.parent.id == "A").limit(2).ids() == ["B", "C"]


# ── Every DSL, because the guard is per-class ────────────────────────────────


def test_the_hierarchy_dsl_is_guarded() -> None:
    h = Hierarchy(
        id="H",
        maintainer="AG",
        version="1.0",
        name={"en": "H"},
        tree=[
            HierarchicalCode(id="N1", code=code_urn("CL", "N1")),
            HierarchicalCode(id="N2", code=code_urn("CL", "N2")),
        ],
    )
    with LocalRegistry(":memory:") as reg:
        reg.add(h)
        nodes = present(reg.get(Hierarchy.urn_for(agency="AG", id="H", version="1.0"))).nodes
        with pytest.raises(SliceError, match="has been sliced"):
            nodes.limit(1).filter(q.hcode.id == "N1")
        with pytest.raises(SliceError, match="has been sliced"):
            nodes.offset(1).order_by(q.hcode.id)
        assert nodes.filter(q.hcode.id == "N1").limit(1).ids() == ["N1"]


# `test_the_artefact_spine_is_guarded` went with `ArtefactQuery` (#306).
# The path language refuses filter-after-slice in `Query` itself, checked
# by the other tests in this file.
def test_the_metadata_dsl_is_guarded() -> None:
    from sdmxlib.model.metadataflow import Metadataflow
    from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
    from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
    from sdmxlib.model.ref import artefact_urn

    described = Codelist(
        id="CL_D", maintainer="AG", version="1.0", name={"en": "d"}, codes=[Code(id="A", name={"en": "a"})]
    )
    msd = MetadataStructure(
        id="MSD",
        maintainer="AG",
        name={"en": "MSD"},
        attributes=[
            MetadataAttribute(id="A", concept=concept_ref("A")),
            MetadataAttribute(id="B", concept=concept_ref("B")),
        ],
    )
    mdf = Metadataflow(id="MDF", maintainer="AG", name={"en": "MDF"}, structure=msd)
    mds = MetadataSet(
        id="MDS",
        maintainer="AG",
        name={"en": "Set"},
        structure=mdf,
        targets=[artefact_urn(described)],
        attributes=[ReportedAttribute(id="A", value="1"), ReportedAttribute(id="B", value="2")],
    )
    with LocalRegistry(":memory:") as reg:
        for artefact in (described, msd, mdf, mds):
            reg.add(artefact)
        # The generic query since #306. Its refusal is `SliceError`, a
        # `ValueError` — the same guarantee spelled by a different class, and
        # the message still names the fix.
        values = reg.reported_attributes()
        path = q.reported_attribute.attribute_path
        with pytest.raises(ValueError, match="cannot filter"):
            values.limit(1).filter(path == "A")
        with pytest.raises(ValueError, match="cannot order_by"):
            values.offset(1).order_by(path)
        assert values.filter(path == "A").limit(1).ids() == ["A"]
