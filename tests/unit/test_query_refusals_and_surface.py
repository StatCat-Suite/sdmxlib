"""Refusals in this language's own words, and two names the docs promised.

Four unrelated-looking defects with one shape: the library did something other
than what it said, and said it in someone else's voice.

* a predicate from another root reached DuckDB and came back as a binder error
  naming an alias the caller never wrote;
* `frame(labels=)` on a structural query died inside the label builder,
  because a `hasattr` duck-test meant to spot an observation root was
  satisfied by a *declared edge* of the same name;
* `valid_from` / `valid_to` are stored, and named in the design of record, and
  were not in the language;
* `sl.union` is specified twice in `docs/design/` and did not exist.
"""

from __future__ import annotations

import annotationlib
import datetime as dt

import pytest

import sdmxlib as sl
from sdmxlib.model.ref import Ref
from sdmxlib.query._expr import AnyCols
from sdmxlib.query.accessors import q
from tests.support.factories import concept_ref
from tests.support.narrow import present

pytestmark = pytest.mark.unit


@pytest.fixture
def registry():
    dsd = sl.DataStructure(
        id="D",
        maintainer="AG",
        version="1.0",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="F", concept=concept_ref("F"))],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    flow = sl.Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "f"}, structure=Ref.to(dsd))
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all([dsd, flow])
        yield reg


# ── a predicate built for another relation ───────────────────────────────────


def test_a_predicate_from_another_root_is_refused_here(registry) -> None:
    """It used to reach DuckDB, which answered:

        BinderException: Table "s0" does not have a column named "id"
          Candidate bindings: : "fetched_at"

    An alias the caller never wrote, a column they never wrote, and a
    suggestion of `fetched_at`. Statically this is caught — so a typed
    codebase never sees it — but a notebook is exactly the audience a query
    DSL has.
    """
    with pytest.raises(TypeError) as caught:
        registry.query(q.dataflow).filter(q.code.id == "A")

    message = str(caught.value)
    assert "q.code" in message, "names the relation the predicate came from"
    assert "q.dataflow" in message, "and the one the query is over"
    assert "BinderException" not in message


def test_a_predicate_reached_by_a_declared_hop_is_still_fine(registry) -> None:
    """The check is reachability, not table equality — `q.dataflow.dsd.id`
    reads `artefact` through a hop and is perfectly valid. A rule that
    compared tables would refuse the commonest traversal in SDMX."""
    assert registry.query(q.dataflow).filter(q.dataflow.dsd.id == "D").ids() == ["DF"]


def test_a_sibling_root_over_the_same_table_is_refused(registry) -> None:
    """`q.dataflow` and `q.codelist` both root at `artefact`, so a table check
    passed a predicate from one into a query over the other (#510).

    For a direct column that answered for the wrong relation without saying
    so; through a hop it was worse — `q.dsd.dimensions.id` on a query over
    dataflows planned the DSD's path from the dataflow and returned `[]`. A
    predicate now belongs to the root it was read from, which is what its row
    kind already says statically.
    """
    with pytest.raises(TypeError, match=r"q\.codelist.*q\.dataflow"):
        registry.query(q.dataflow).filter(q.codelist.agency == "AG")
    with pytest.raises(TypeError, match=r"q\.dsd.*q\.dataflow"):
        registry.query(q.dataflow).filter(q.dsd.dimensions.id == "GEO")


def test_the_generic_artefact_root_fits_any_maintainable(registry) -> None:
    """`q.artefact`'s columns are every artefact's, so they fit a narrower root,
    and a narrower root's fit `reg.artefacts()` — the discrimination check:
    what the rule refuses is a *different* artefact type, not a related one."""
    assert registry.query(q.dataflow).filter(q.artefact.agency == "AG").ids() == ["DF"]
    assert "DF" in registry.artefacts().filter(q.dataflow.agency == "AG").ids()


def test_the_refusal_survives_a_boolean_wrapper(registry) -> None:
    """A check reading only the outermost predicate would miss the shape a
    caller actually writes."""
    with pytest.raises(TypeError, match=r"q\.code\b"):
        # Mixing roots is what this asserts is refused; the row kind catches
        # most of it statically, and the runtime message is the backstop.
        registry.query(q.dataflow).filter((q.dataflow.id == "DF") & (q.code.id == "A"))  # pyrefly: ignore[unsupported-operation]


# ── labels are an observation thing ──────────────────────────────────────────


def test_labels_on_a_structural_query_are_refused_in_our_words(registry) -> None:
    """The guard was `getattr(root, "dsd", None)`, and `_Dataflow.dsd` is a
    declared *edge* — so the duck-test passed, an accessor object went to the
    label builder, and it died on `'_Dsd' object has no attribute
    'measure_dimension'`. The intended refusal never fired.

    This is the `hasattr`-cannot-narrow hazard CLAUDE.md names, found in the
    wild rather than in a checker run.
    """
    with pytest.raises(TypeError) as caught:
        registry.query(q.dataflow).frame(labels="en")

    message = str(caught.value)
    assert "measure_dimension" not in message
    assert ".obs(" in message, "names where labels do apply"
    assert "select(name=" in message, "and what to do instead here"


# ── validity, which was stored but unreachable ───────────────────────────────


def test_validity_columns_are_queryable() -> None:
    """`design/query-language.md` lists `valid_from` as part of the hoisted
    maintainable surface and asserts *"they are columns, so..."* for as-of
    queries. They were columns; they were not in the language."""
    expired = sl.Codelist(id="OLD", maintainer="AG", version="1.0", name={"en": "o"}, valid_to=dt.datetime(2020, 1, 1))
    live = sl.Codelist(id="NOW", maintainer="AG", version="1.0", name={"en": "n"}, valid_from=dt.datetime(2020, 1, 1))
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all([expired, live])
        cutoff = dt.datetime(2025, 1, 1)

        assert reg.query(q.codelist).filter(q.codelist.valid_to < cutoff).ids() == ["OLD"]
        assert reg.query(q.codelist).filter(q.codelist.valid_to.is_null()).ids() == ["NOW"]
        assert reg.query(q.codelist).filter(q.codelist.valid_from >= cutoff).ids() == []


def test_validity_projects_as_a_timestamp() -> None:
    """Not as text — an as-of query that came back stringly would compare
    lexicographically, which is the `1.9`/`1.10` mistake in another suit."""
    cl = sl.Codelist(id="C", maintainer="AG", version="1.0", name={"en": "c"}, valid_from=dt.datetime(2020, 6, 1))
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(cl)
        got = reg.query(q.codelist).select(v=q.codelist.valid_from).collect()["v"].to_list()

    assert got == [dt.datetime(2020, 6, 1, tzinfo=dt.UTC)]


# ── the documented union spelling ────────────────────────────────────────────


def test_union_is_importable_from_the_top_level() -> None:
    """`docs/design/query-language.md` and `query-language-examples.md` both
    specify `sl.union(a, b)` — *"a FREE FUNCTION, never a method"* — and it was
    only reachable as `from sdmxlib.query import union`."""
    assert hasattr(sl, "union")
    assert "union" in sl.__all__

    from sdmxlib.query import union as inner

    assert sl.union is inner, "one function, not a re-implementation"


def test_union_widens(registry) -> None:
    """It is exported because it works; asserted so the export is not the only
    thing tested."""
    a = registry.query(q.dataflow).filter(q.dataflow.id == "DF")
    b = registry.query(q.dataflow).filter(q.dataflow.id == "NOPE")
    assert sl.union(a, b).ids() == ["DF"]


# ── validity comes back with a zone, as `reg.get` does ───────────────────────


def _with_validity(**kwargs) -> sl.Codelist:
    return sl.Codelist(id="C", maintainer="AG", version="1.0", name={"en": "c"}, **kwargs)


def test_a_projected_timestamp_agrees_with_the_one_on_the_artefact() -> None:
    """The same field, read two ways, must be the same value.

    The store is UTC-*naive* by convention (#321) and `readers` re-attach the
    zone, so `reg.get(urn).valid_from` is tz-aware. A projected frame handed
    back the raw column, so the two neither compared nor subtracted — the
    trap being that both look like the right answer.
    """
    cl = _with_validity(valid_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC))
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(cl)
        on_artefact = present(reg.get(cl.urn)).valid_from
        in_frame = reg.query(q.codelist).select(v=q.codelist.valid_from).collect()["v"][0]

    assert in_frame == on_artefact
    assert in_frame.tzinfo is not None, "a projected instant carries its zone"


def test_the_wall_time_is_labelled_not_shifted() -> None:
    """`replace_time_zone`, not `convert_time_zone`.

    The stored value is already UTC, so moving it would be wrong by the local
    offset — which is what `AT TIME ZONE 'UTC'` in SQL does, since DuckDB
    renders the resulting TIMESTAMPTZ in the *session's* zone. That is the
    machine-dependence #321 exists to remove, so the labelling happens in
    polars instead.
    """
    cl = _with_validity(valid_from=dt.datetime(2020, 6, 1, 12, 30, tzinfo=dt.UTC))
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(cl)
        got = reg.query(q.codelist).select(v=q.codelist.valid_from).collect()["v"][0]

    assert (got.year, got.month, got.day, got.hour, got.minute) == (2020, 6, 1, 12, 30)


def test_a_null_validity_survives_labelling() -> None:
    """Most artefacts have no stated end, so the null path is the common one —
    and a column-wide operation is where nulls get lost."""
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(_with_validity())
        assert reg.query(q.codelist).select(v=q.codelist.valid_to).collect()["v"].to_list() == [None]


def test_a_frame_with_no_timestamp_is_untouched() -> None:
    """The labelling must not fire on an ordinary projection."""
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(_with_validity())
        frame = reg.query(q.codelist).select(q.codelist.id).collect()
        assert frame["id"].to_list() == ["C"]


def test_every_pair_of_sibling_roots_is_told_apart(registry) -> None:
    """Over every two `sl.q` roots sharing a table but not a discriminator.

    A walk rather than one pair, so a root added later is covered the day it
    is written. The count is asserted exactly: it is what `sl.q` declares now,
    and a change to it is a change worth seeing.
    """
    roots = [
        getattr(q, name)
        for name in annotationlib.get_annotations(type(q), format=annotationlib.Format.STRING)
        if isinstance(getattr(q, name), AnyCols)
    ]
    pairs = [
        (mine, theirs)
        for mine in roots
        for theirs in roots
        if mine is not theirs
        and mine.__table__ == theirs.__table__
        and None not in (mine.__constraint__, theirs.__constraint__)
        and mine.__constraint__ != theirs.__constraint__
        and hasattr(theirs, "id")
    ]
    for mine, theirs in pairs:
        with pytest.raises(TypeError, match="belongs to the relation it was built from"):
            registry.query(mine).filter(theirs.id == "X")
    assert len(pairs) == _SIBLING_PAIRS


#: Ordered pairs of distinct `sl.q` maintainable roots over `sdmx.artefact`:
#: 23 discriminated roots, each against the other 22.
_SIBLING_PAIRS = 506
