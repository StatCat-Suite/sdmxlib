"""Annotations: one hub column, three typed edges.

`sdmx.annotation.owner_urn` points at artefacts, codes *and* concepts. Modelled
as one polymorphic edge it would make everything reachable from everything —
the measurement that killed bare column references in the first place. Three
typed edges keep the graph honest, and the guard here is
`test_the_owners_do_not_bleed_into_each_other`: without the type discrimination
a codelist would match its own codes' annotations.

Nothing else about them is special. An annotation is a to-many side table, so
it takes the ordinary fan-out surface — dotting through, `.any()`, `.list()` —
which is the payoff for declaring it as an edge rather than as a bespoke
predicate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.annotations import Annotation
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.organisation import Agency
from sdmxlib.query.accessors import q

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Every owner kind annotated, and the *types* deliberately collide.

    `CL_AREA` and its code `CA` both carry a `NOTE`, and the scheme carries one
    too. A fixture where each owner used a distinct type would pass even with
    the owner correlation dropped entirely.
    """
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            Codelist(
                id="CL_AREA",
                maintainer=Agency(id="ESTAT"),
                name={"en": "Areas"},
                annotations=[Annotation(id="cl1", type="NOTE", title="on the codelist")],
                codes=[
                    Code(
                        id="CA",
                        name={"en": "Canada"},
                        annotations=[
                            Annotation(id="c1", type="NOTE", title="on the code"),
                            Annotation(id="c2", type="SOURCE", title="statcan", urls="https://x"),
                        ],
                    ),
                    Code(id="US", name={"en": "United States"}),
                ],
            )
        )
        registry.add(
            Codelist(
                id="CL_FREQ",
                maintainer=Agency(id="OECD"),
                name={"en": "Frequency"},
                codes=[Code(id="A", name={"en": "Annual"})],
            )
        )
        registry.add(
            ConceptScheme(
                id="CROSS",
                maintainer=Agency(id="ESTAT"),
                name={"en": "Cross"},
                concepts=[
                    Concept(
                        id="FREQ",
                        name={"en": "Frequency"},
                        annotations=[Annotation(id="k1", type="NOTE", title="on the concept")],
                    )
                ],
            )
        )
        yield registry


# ── the ordinary fan-out surface, with nothing added ──────────────────────


def test_dotting_through_means_some_annotation(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.code).filter(q.code.annotations.type == "SOURCE").ids() == ["CA"]


def test_an_unannotated_row_does_not_match(reg: sl.LocalRegistry) -> None:
    """A correlated `EXISTS`, so `US` is absent rather than null-matched."""
    assert "US" not in reg.query(q.code).filter(q.code.annotations.type == "NOTE").ids()


def test_the_binder_groups_onto_one_annotation(reg: sl.LocalRegistry) -> None:
    """`CA` has a `NOTE` and a `SOURCE`, but no *single* annotation is both."""
    same = q.code.annotations.any(
        q.annotation.type == "NOTE",
        q.annotation.title.str.contains("statcan"),
    )
    assert reg.query(q.code).filter(same).ids() == []


def test_independent_predicates_match_across_annotations(reg: sl.LocalRegistry) -> None:
    """The contrast that makes the previous test mean something."""
    found = reg.query(q.code).filter(
        q.code.annotations.type == "NOTE",
        q.code.annotations.title.str.contains("statcan"),
    )
    assert found.ids() == ["CA"]


def test_the_binder_matches_when_one_annotation_satisfies_all(reg: sl.LocalRegistry) -> None:
    same = q.code.annotations.any(
        q.annotation.type == "SOURCE",
        q.annotation.title.str.contains("statcan"),
    )
    assert reg.query(q.code).filter(same).ids() == ["CA"]


def test_annotations_collect(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.code).select(code=q.code.id, types=q.code.annotations.type.list()).collect()
    types = dict(zip(frame["code"].to_list(), frame["types"].to_list(), strict=True))
    assert sorted(types["CA"]) == ["NOTE", "SOURCE"]
    assert types["US"] in ([], None)


def test_annotations_count(reg: sl.LocalRegistry) -> None:
    frame = reg.query(q.code).select(code=q.code.id, n=q.code.annotations.count()).collect()
    assert dict(zip(frame["code"].to_list(), frame["n"].to_list(), strict=True)) == {
        "CA": 2,
        "US": 0,
        "A": 0,
    }


def test_every_declared_column_reads(reg: sl.LocalRegistry) -> None:
    """`urls` and `position` are otherwise unexercised, and a column that names
    nothing real fails only when something reads it."""
    frame = (
        reg.query(q.code)
        .filter(q.code.annotations.id == "c2")
        .select(
            code=q.code.id,
            ids=q.code.annotations.id.list(),
            at=q.code.annotations.position.list(),
        )
        .collect()
    )
    assert frame["code"].to_list() == ["CA"]
    assert sorted(frame["ids"].to_list()[0]) == ["c1", "c2"]
    assert len(frame["at"].to_list()[0]) == 2
    # An annotation's URLs are a fan-out of their own (#422): listed from the
    # annotation, and filtered through from a code.
    urls = reg.query(q.annotation).filter(q.annotation.id == "c2").select(urls=q.annotation.urls.url.list()).collect()
    assert urls["urls"].to_list() == [["https://x"]]
    assert reg.query(q.code).filter(q.code.annotations.urls.url == "https://x").ids() == ["CA"]


# ── three typed edges, not one polymorphic one ────────────────────────────


def test_each_owner_kind_reaches_its_own_annotations(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.codelist).filter(q.codelist.annotations.title == "on the codelist").ids() == ["CL_AREA"]
    assert reg.query(q.code).filter(q.code.annotations.title == "on the code").ids() == ["CA"]
    assert reg.query(q.concept).filter(q.concept.annotations.title == "on the concept").ids() == ["FREQ"]


def test_the_owners_do_not_bleed_into_each_other(reg: sl.LocalRegistry) -> None:
    """The whole reason the edges are typed.

    All three owners carry a `NOTE`, so a query that reached the annotation
    table without discriminating on owner would match all three. Each must see
    only its own — asserted by *title*, since the types deliberately collide.
    """
    assert reg.query(q.codelist).filter(q.codelist.annotations.title == "on the code").ids() == []
    assert reg.query(q.code).filter(q.code.annotations.title == "on the codelist").ids() == []
    assert reg.query(q.concept).filter(q.concept.annotations.title == "on the codelist").ids() == []


def test_a_codelists_annotations_are_not_its_codes(reg: sl.LocalRegistry) -> None:
    """One `NOTE` on the codelist, one on a code it contains. Counting them
    apart is what a polymorphic edge could not do."""
    frame = reg.query(q.codelist).select(cl=q.codelist.id, n=q.codelist.annotations.count()).collect()
    assert dict(zip(frame["cl"].to_list(), frame["n"].to_list(), strict=True)) == {
        "CL_AREA": 1,
        "CL_FREQ": 0,
    }


def test_reaching_annotations_through_a_fan_out(reg: sl.LocalRegistry) -> None:
    """`codelist -> codes -> annotations`: two to-many hops in one path."""
    found = reg.query(q.codelist).filter(q.codelist.codes.annotations.type == "SOURCE")
    assert found.ids() == ["CL_AREA"]


def test_annotations_fold_like_any_other_string(reg: sl.LocalRegistry) -> None:
    assert reg.query(q.code).filter(q.code.annotations.title.str.caseless == "STATCAN").ids() == ["CA"]
