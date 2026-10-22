"""Closed vocabularies, and the empty result they used to return silently.

`q.artefact.type` and `q.component.role` hold one of a short fixed list the
library itself writes. Compared against anything else the answer was always an
empty set — and indistinguishable from "this catalogue contains none of those",
which is a perfectly ordinary answer:

    q.artefact.type == "Codlist"       # typo -> []
    q.component.role == "dimension"    # case -> []

Both look like they work. The check found one of each in this repo's own test
suite the moment it went in, which is the argument for it.
"""

from __future__ import annotations

import pytest

import sdmxlib as sl
from sdmxlib.model.ref import Ref
from sdmxlib.query.accessors import q
from sdmxlib.storage._kinds import ARTEFACT_TYPES, CLASS_TO_NAME, COMPONENT_TYPES
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("build", "bad"),
    [
        pytest.param(lambda v: q.artefact.type == v, "Codlist", id="artefact-type-typo"),
        pytest.param(lambda v: q.artefact.type != v, "Codlist", id="artefact-type-negated"),
        pytest.param(lambda v: q.component.role == v, "dimension", id="component-role-case"),
        pytest.param(lambda v: q.artefact.type.is_in(["Codelist", v]), "Dataflw", id="in-a-set"),
    ],
)
def test_a_value_the_column_cannot_hold_is_refused(build, bad: str) -> None:
    """Including inside a set — which is where a typo hides best, because the
    other members still match and the result looks plausible."""
    with pytest.raises(ValueError, match=bad):
        build(bad)


def test_the_refusal_names_the_language_spelling_not_the_column() -> None:
    """`q.artefact.type`, not `artefact_type`. The storage column is what the
    language exists to hide, and naming it sends someone to the wrong schema."""
    with pytest.raises(ValueError, match="never holds") as caught:
        _ = q.artefact.type == "Codlist"

    message = str(caught.value)
    assert "q.artefact.type" in message
    assert "artefact_type" not in message


def test_the_refusal_lists_the_legal_values() -> None:
    with pytest.raises(ValueError, match="never holds") as caught:
        _ = q.component.role == "nope"

    assert "Dimension" in str(caught.value)
    assert "TimeDimension" in str(caught.value)


def test_a_case_mistake_gets_a_did_you_mean() -> None:
    """The commonest form of this — the value is right, the case is not."""
    with pytest.raises(ValueError, match="Did you mean 'Dimension'"):
        _ = q.component.role == "dimension"


def test_negation_says_it_matches_everything_not_nothing() -> None:
    """Both spellings are mistakes, but they fail differently, and telling
    someone their filter is empty when it is a no-op sends them looking in the
    wrong place."""
    with pytest.raises(ValueError, match="matches every row"):
        _ = q.artefact.type != "Codlist"
    with pytest.raises(ValueError, match="matches nothing"):
        _ = q.artefact.type == "Codlist"


def test_every_legal_value_is_accepted() -> None:
    """The other direction. A check that refused everything would pass every
    test above."""
    # `is not None` rather than a bare assert: `==` builds a predicate and
    # `bool(pred)` is refused on purpose, so asserting one is a TypeError.
    for value in ARTEFACT_TYPES:
        assert (q.artefact.type == value) is not None
    for value in COMPONENT_TYPES:
        assert (q.component.role == value) is not None


def test_a_non_string_still_gets_the_type_message() -> None:
    """`Str`'s own literal check is better about the *type*, and must not be
    shadowed by the membership one."""
    with pytest.raises(TypeError):
        _ = q.artefact.type == 3


def test_the_vocabulary_survives_being_read_through_a_path() -> None:
    """`q.artefact.type` is read off a root; `q.dataflow.dsd` and friends
    rebuild columns as they bind the path. A column class carrying extra state
    has to carry it through that rebuild or the check silently stops
    happening."""
    with pytest.raises(ValueError, match="never holds"):
        _ = q.categorisation.artefact.type == "Codlist"


# ── the vocabulary is the writer's, not a second list ────────────────────────


def test_the_vocabulary_matches_what_the_writer_can_emit() -> None:
    """The invariant the whole check rests on, stated exactly.

    `artefact_type` holds SDMX IM class names, and the IM is fixed — so a value
    outside the vocabulary is a bug, not a variant to accommodate. The store
    cannot hold a stale one either: `LocalRegistry._initialise_schema` refuses a
    populated store on another `SCHEMA_VERSION` unless asked to rebuild it
    (GH-57), so no build reads a store another build wrote.

    That leaves exactly one way for this check to refuse a *legitimate* query:
    sdmxlib grows a writer for an artefact and `ARTEFACT_KINDS` does not learn
    about it. Then the rows are real, the query is right, and the language says
    no. Compared against the writer's own dispatch table rather than against a
    fixture, because a fixture only covers the artefacts someone remembered to
    put in it — the same mistake one layer up.
    """
    from sdmxlib.storage.writers import _WRITER_DISPATCH

    unregistered = sorted(cls.__name__ for cls in _WRITER_DISPATCH if cls not in CLASS_TO_NAME)
    assert not unregistered, (
        "these have a writer but no ARTEFACT_KINDS entry, so `q.artefact.type == ...` would "
        f"refuse a query for rows that exist: {unregistered}"
    )

    writable = {CLASS_TO_NAME[cls] for cls in _WRITER_DISPATCH if cls in CLASS_TO_NAME}
    assert writable <= ARTEFACT_TYPES, f"writable but not queryable: {sorted(writable - ARTEFACT_TYPES)}"


def test_the_vocabulary_has_nothing_the_writer_cannot_emit() -> None:
    """The other direction, milder but still worth knowing.

    An entry nothing writes is not a wrong answer — the query is legal and
    correctly returns nothing. It does mean a refusal advertises a value that
    is unreachable, which sends a reader looking for a feature that is not
    there.
    """
    from sdmxlib.storage.writers import _WRITER_DISPATCH

    writable = {CLASS_TO_NAME[cls] for cls in _WRITER_DISPATCH if cls in CLASS_TO_NAME}
    assert not ARTEFACT_TYPES - writable, (
        f"the refusal advertises types nothing can write: {sorted(ARTEFACT_TYPES - writable)}"
    )


def test_the_artefact_types_are_what_the_store_actually_holds() -> None:
    """And end to end, so the two static tables above are checked against a real
    write rather than only against each other."""
    codelist = sl.Codelist(id="C", maintainer="AG", version="1.0", name={"en": "c"})
    dsd = sl.DataStructure(
        id="D",
        maintainer="AG",
        version="1.0",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="F", concept=concept_ref("F"))],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all([codelist, dsd])
        written = {row[0] for row in reg.execute("SELECT DISTINCT artefact_type FROM sdmx.artefact")}

    assert written <= ARTEFACT_TYPES, f"the writer produces types the language refuses: {written - ARTEFACT_TYPES}"


def test_the_component_types_are_what_the_writer_produces() -> None:
    """`COMPONENT_TYPES` is listed rather than derived — the writer builds them
    from the DSD's *fields*, so there is no registry to read. This is what
    holds the two together.
    """
    codelist = sl.Codelist(
        id="C", maintainer="AG", version="1.0", name={"en": "c"}, codes=[sl.Code(id="A", name={"en": "a"})]
    )
    dsd = sl.DataStructure(
        id="D",
        maintainer="AG",
        version="1.0",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="F", concept=concept_ref("F"), representation=Ref.to(codelist))],
        attributes=[sl.Attribute(attachment=sl.AttachmentLevel.DATASET, id="S", concept=concept_ref("S"))],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
        time_dimension=sl.TimeDimension(concept=concept_ref("TIME_PERIOD")),
    )
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all([codelist, dsd])
        written = {row[0] for row in reg.execute("SELECT DISTINCT component_type FROM sdmx.dsd_component")}

    assert written <= COMPONENT_TYPES, f"the writer produces roles the language refuses: {written - COMPONENT_TYPES}"
    assert {"Dimension", "TimeDimension", "Attribute", "Measure"} <= written, "the fixture should exercise four of five"


def test_a_refused_value_would_really_have_returned_nothing() -> None:
    """The premise, checked rather than assumed: these are refused *because*
    the query would be empty, not because the value looks unusual."""
    codelist = sl.Codelist(id="C", maintainer="AG", version="1.0", name={"en": "c"})
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(codelist)
        assert reg.query(q.artefact).filter(q.artefact.type == "Codelist").ids() == ["C"]
        rows = reg.execute("SELECT count(*) FROM sdmx.artefact WHERE artefact_type = 'Codlist'")
        assert rows[0][0] == 0
