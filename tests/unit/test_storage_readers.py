"""Tests for sdmxlib.storage.readers — reading domain objects from DuckDB."""

import datetime as dt

import attrs
import pytest

from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import ConceptScheme
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.hierarchy import Hierarchy
from sdmxlib.model.organisation import Agency
from sdmxlib.model.registry import artefact_urn
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.query.query import Query
from sdmxlib.storage.lazy import LazyCodelist
from sdmxlib.storage.readers import (
    code_map,
    code_map_for_dsd,
    get_artefact,
    urns_of_type,
)
from sdmxlib.storage.writers import (
    put_codelist,
    put_concept_scheme,
    put_data_structure,
    put_dataflow,
    put_hierarchy,
)
from tests.support.factories import concept_ref
from tests.support.narrow import present

pytestmark = pytest.mark.unit


# URNs of the default fixture artefacts — they're stable, so capture once.
_CL_URN = str(
    SdmxUrn.make(Codelist, package="codelist", artefact_class="Codelist", agency="SDMX", id="CL_FREQ", version="1.0")
)
_DSD_URN = str(
    SdmxUrn.make(
        DataStructure,
        package="datastructure",
        artefact_class="DataStructure",
        agency="SDMX",
        id="DSD_TEST",
        version="1.0",
    )
)


# ── get_artefact ────────────────────────────────────────────────────────


def test_get_artefact_not_found(cursor):
    cur = cursor
    assert get_artefact(cur, "urn:sdmx:fake") is None


def test_get_codelist_lazy(cursor, make_codelist):
    cur = cursor
    put_codelist(cur, make_codelist())
    result = get_artefact(cur, _CL_URN)
    assert isinstance(result, LazyCodelist)
    assert result.id == "CL_FREQ"
    assert len(result) == 2


def test_get_codelist_eager(cursor, make_codelist):
    cur = cursor
    put_codelist(cur, make_codelist())
    result = get_artefact(cur, _CL_URN, eager=True)
    assert isinstance(result, Codelist)
    assert len(result.codes) == 2
    assert result.codes["A"].name["en"] == "Annual"


def test_get_concept_scheme(cursor, make_concept_scheme):
    cur = cursor
    cs = make_concept_scheme()
    put_concept_scheme(cur, cs)
    urn = str(
        SdmxUrn.make(
            ConceptScheme,
            package="conceptscheme",
            artefact_class="ConceptScheme",
            agency=cs.agency_id,
            id=cs.id,
            version=cs.version,
        )
    )
    result = get_artefact(cur, urn)
    assert result is not None
    assert result.id == "CS_COMMON"
    assert len(result.concepts) == 2


def test_get_dataflow(cursor, make_dataflow):
    cur = cursor
    put_dataflow(cur, make_dataflow())
    flow = make_dataflow()
    urn = str(
        SdmxUrn.make(
            Dataflow,
            package="datastructure",
            artefact_class="Dataflow",
            agency=flow.agency_id,
            id=flow.id,
            version=flow.version,
        )
    )
    result = get_artefact(cur, urn)
    assert result is not None
    assert result.id == "DF_TEST"


def test_get_data_structure(cursor, make_dsd):
    cur = cursor
    put_data_structure(cur, make_dsd())
    result = get_artefact(cur, _DSD_URN)
    assert result is not None
    assert result.id == "DSD_TEST"
    assert len(result.dimensions) == 1


def test_get_data_structure_component_annotations_round_trip(cursor, dsd):
    cur = cursor
    dsd = DataStructure(
        id="DSD_ANN",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Annotated DSD"},
        dimensions=[
            Dimension(
                id="FREQ",
                concept=concept_ref("FREQ"),
                annotations=[Annotation(id="DIM_ANN", title="dim note")],
            ),
        ],
        measures=[
            Measure(
                id="OBS_VALUE",
                concept=concept_ref("OBS_VALUE"),
                annotations=[
                    Annotation(id="UNIT_MEASURE", title="Persons", type="UNIT_MEASURE"),
                ],
            ),
        ],
    )
    put_data_structure(cur, dsd)
    urn = str(
        SdmxUrn.make(
            DataStructure,
            package="datastructure",
            artefact_class="DataStructure",
            agency="SDMX",
            id="DSD_ANN",
            version="1.0",
        )
    )
    result = get_artefact(cur, urn)
    assert isinstance(result, DataStructure)
    dim = result.dimensions["FREQ"]
    assert len(dim.annotations) == 1
    assert dim.annotations[0].id == "DIM_ANN"
    measure = result.measures["OBS_VALUE"]
    assert len(measure.annotations) == 1
    assert measure.annotations[0].id == "UNIT_MEASURE"
    assert measure.annotations[0].title == "Persons"
    assert measure.annotations[0].type == "UNIT_MEASURE"


def test_get_hierarchy_eager(cursor, make_hierarchy):
    cur = cursor
    h = make_hierarchy()
    put_hierarchy(cur, h)
    urn = str(
        SdmxUrn.make(
            Hierarchy,
            package="codelist",
            artefact_class="Hierarchy",
            agency=h.agency_id,
            id=h.id,
            version=h.version,
        )
    )
    result = get_artefact(cur, urn, eager=True)
    assert result is not None
    assert isinstance(result, Hierarchy)
    assert len(result.tree) == 1
    assert result.tree["CANADA"].children["ON"].id == "ON"


def test_get_hierarchy_lazy_by_default(cursor, make_hierarchy):
    """get_artefact returns a LazyHierarchy by default; no full tree materialisation."""
    from sdmxlib.storage.lazy import LazyHierarchy

    cur = cursor
    h = make_hierarchy()
    put_hierarchy(cur, h)
    urn = str(
        SdmxUrn.make(
            Hierarchy,
            package="codelist",
            artefact_class="Hierarchy",
            agency=h.agency_id,
            id=h.id,
            version=h.version,
        )
    )
    result = get_artefact(cur, urn)
    assert result is not None
    assert isinstance(result, LazyHierarchy)
    assert "CANADA" in result
    assert set(result.roots().ids()) == {"CANADA"}


# ── urns_of_type ────────────────────────────────────────────────────


def test_urns_of_type_lists_each_stored_artefact_of_the_type(cursor, make_codelist):
    """What `LocalRegistry.get_all` reads one by one, through `get_artefact`."""
    cur = cursor
    codelist = make_codelist()
    put_codelist(cur, codelist)
    assert urns_of_type(cur, Codelist) == [str(codelist.urn)]


def test_urns_of_type_is_empty_for_none_stored(cursor):
    assert urns_of_type(cursor, Codelist) == []


# ── LazyCodelist ────────────────────────────────────────────────────────


def test_lazy_codelist_codes(cursor, make_codelist):
    cur = cursor
    put_codelist(cur, make_codelist())
    lazy = get_artefact(cur, _CL_URN)
    assert isinstance(present(lazy).codes, Query)
    assert present(lazy).codes["A"].id == "A"
    assert present(lazy).codes["A"].name["en"] == "Annual"


def test_lazy_codelist_code_map(cursor, make_codelist):
    cur = cursor
    put_codelist(cur, make_codelist())
    lazy = get_artefact(cur, _CL_URN)
    cm = present(lazy).code_map(lang="en")
    assert cm == {"A": "Annual", "Q": "Quarterly"}


def test_lazy_codelist_materialize(cursor, make_codelist):
    cur = cursor
    put_codelist(cur, make_codelist())
    lazy = get_artefact(cur, _CL_URN)
    cl = present(lazy).materialize()
    assert isinstance(cl, Codelist)
    assert len(cl.codes) == 2


def test_lazy_codelist_roots(cursor):
    cur = cursor
    cl = Codelist(
        name={"en": "CL_GEO"},
        id="CL_GEO",
        maintainer="SDMX",
        codes=[
            Code(id="CA", name={"en": "Canada"}),
            Code(id="ON", name={"en": "Ontario"}, parent_id="CA"),
        ],
    )
    put_codelist(cur, cl)
    urn = str(
        SdmxUrn.make(
            Codelist,
            package="codelist",
            artefact_class="Codelist",
            agency="SDMX",
            id="CL_GEO",
            version="1.0",
        )
    )
    lazy = present(get_artefact(cur, urn))
    roots = lazy.roots().collect()
    assert len(roots) == 1
    assert roots[0].id == "CA"


def test_lazy_codelist_children(cursor):
    cur = cursor
    cl = Codelist(
        name={"en": "CL_GEO"},
        id="CL_GEO",
        maintainer="SDMX",
        codes=[
            Code(id="CA", name={"en": "Canada"}),
            Code(id="ON", name={"en": "Ontario"}, parent_id="CA"),
            Code(id="QC", name={"en": "Quebec"}, parent_id="CA"),
        ],
    )
    put_codelist(cur, cl)
    urn = str(
        SdmxUrn.make(
            Codelist,
            package="codelist",
            artefact_class="Codelist",
            agency="SDMX",
            id="CL_GEO",
            version="1.0",
        )
    )
    lazy = present(get_artefact(cur, urn))
    children = lazy.narrower("CA").collect()
    assert len(children) == 2
    assert {c.id for c in children} == {"ON", "QC"}


def test_lazy_codelist_ids(cursor, make_codelist):
    cur = cursor
    put_codelist(cur, make_codelist())
    lazy = get_artefact(cur, _CL_URN)
    assert present(lazy).codes.ids() == ["A", "Q"]


def test_lazy_codelist_contains(cursor, make_codelist):
    cur = cursor
    put_codelist(cur, make_codelist())
    lazy = get_artefact(cur, _CL_URN)
    assert isinstance(lazy, LazyCodelist)
    assert "A" in lazy
    assert "Z" not in lazy


def test_lazy_codelist_iter(cursor, make_codelist):
    cur = cursor
    put_codelist(cur, make_codelist())
    lazy = get_artefact(cur, _CL_URN)
    codes = present(lazy).codes.collect()
    assert len(codes) == 2
    assert codes[0].id == "A"


def test_lazy_codelist_repr(cursor, make_codelist):
    cur = cursor
    put_codelist(cur, make_codelist())
    lazy = get_artefact(cur, _CL_URN)
    assert "CL_FREQ" in repr(lazy)


# ── code_map ────────────────────────────────────────────────────────────


def test_code_map(cursor, make_codelist):
    cur = cursor
    put_codelist(cur, make_codelist())
    cm = code_map(cur, _CL_URN, lang="en")
    assert cm == {"A": "Annual", "Q": "Quarterly"}


def test_code_map_missing_lang(cursor, make_codelist):
    """A code with no label in `lang` is labelled by its id, not dropped.

    This asserted `{}` until #398. The eager `Codelist.code_map` had always
    fallen back to the id — as did `polars._component_label_map`'s in-memory
    branch — so the same call meant two things depending on which class the
    caller held, which is the family of bug #398 is about.

    The fallback is the direction that had to win: `Dataset.label` builds a
    `replace_strict` from this map, so a dropped code became a **null column
    value** rather than a visible failure. A caller wanting to know whether a
    codelist carries labels in a language is asking a different question and
    should get its own answer, not infer it from a map that quietly shrank.
    """
    cur = cursor
    put_codelist(cur, make_codelist())
    cm = code_map(cur, _CL_URN, lang="fr")
    assert cm, "every code appears"
    assert all(code_id == label for code_id, label in cm.items()), (
        f"and each is labelled by its own id when French is absent: {cm}"
    )


def test_code_map_bilingual(cursor):
    cur = cursor
    cl = Codelist(
        id="CL_FREQ",
        maintainer="SDMX",
        name={"en": "Frequency", "fr": "Fréquence"},
        codes=[
            Code(id="A", name={"en": "Annual", "fr": "Annuel"}),
            Code(id="Q", name={"en": "Quarterly", "fr": "Trimestriel"}),
        ],
    )
    put_codelist(cur, cl)
    cm_en = code_map(cur, _CL_URN, lang="en")
    cm_fr = code_map(cur, _CL_URN, lang="fr")
    assert cm_en == {"A": "Annual", "Q": "Quarterly"}
    assert cm_fr == {"A": "Annuel", "Q": "Trimestriel"}


# ── code_map_for_dsd ───────────────────────────────────────────────────


def test_code_map_for_dsd(cursor, make_codelist, make_dsd):
    cur = cursor
    put_codelist(cur, make_codelist())
    put_data_structure(cur, make_dsd())
    result = code_map_for_dsd(cur, _DSD_URN, lang="en")
    assert "FREQ" in result
    assert result["FREQ"] == {"A": "Annual", "Q": "Quarterly"}


def test_code_map_for_dsd_empty(cursor):
    cur = cursor
    assert code_map_for_dsd(cur, "urn:sdmx:fake", lang="en") == {}


# ── the proxy and its materialisation must agree (#322) ─────────────────


@pytest.mark.parametrize("kind", ["codelist", "hierarchy"])
def test_materialize_agrees_with_the_proxy_on_every_field(cursor, kind):
    """Whatever the proxy answers, the materialised artefact answers the same.

    `materialize()` built its artefact from a hand-listed subset of what the
    proxy holds, so `lazy.uri` returned the stored value and
    `lazy.materialize().uri` returned `None` — materialising for an unrelated
    reason silently changed the answer, and `Hierarchy.has_formal_levels` was
    filed as a storage gap on the strength of it (#322, #323).

    Two things make this able to fail. The field list is derived from
    `attrs.fields()`, so a field added tomorrow is covered the day it lands.
    And the artefact is built **here** rather than from `make_codelist()`:
    those fixtures leave the spine unset, and an unset field agrees on `None`
    no matter what `materialize()` does — the same vacuity CLAUDE.md warns
    about for query strategies. Written against the fixtures first, this test
    passed with all four fields deleted from the fix.
    """
    artefact = _spine_populated(kind)
    if isinstance(artefact, Codelist):
        put_codelist(cursor, artefact)
    else:
        put_hierarchy(cursor, artefact)
    proxy = get_artefact(cursor, str(artefact_urn(artefact)))
    assert hasattr(proxy, "materialize"), f"{kind} is no longer returned lazily — this guard needs updating"
    full = proxy.materialize()

    disagreements = [
        f"{field.name}: proxy {theirs!r}, materialize() {ours!r}"
        for field in attrs.fields(type(full))
        if (theirs := getattr(proxy, field.name, _ABSENT)) is not _ABSENT
        and not isinstance(theirs, Query)
        and _comparable(theirs) != _comparable(ours := getattr(full, field.name))
    ]
    assert not disagreements, f"{kind}: " + "; ".join(disagreements)


def _spine_populated(kind):
    """An artefact with every optional spine field set to a non-default value.

    `has_formal_levels=True` specifically: its default is `False`, so a
    `materialize()` that never passes it round-trips perfectly against a
    fixture that leaves it alone.
    """
    common = {
        "maintainer": Agency(id="AG"),
        "name": {"en": "spine"},
        "description": {"en": "every optional field set"},
        "version": "1.0",
        "uri": "http://example.test/spine",
        "valid_from": dt.datetime(2021, 2, 3, 4, 5, 6, tzinfo=dt.UTC),
        "valid_to": dt.datetime(2022, 3, 4, 5, 6, 7, tzinfo=dt.UTC),
        "annotations": Annotations([Annotation(id="A1", type="SPINE", title="t")]),
    }
    if kind == "codelist":
        # Only the codelist: 2.1 has no `isFinal` on a hierarchy (#420).
        return Codelist(id="CL_SPINE", codes=[Code(id="A", name={"en": "a"})], legacy_is_final=True, **common)
    return Hierarchy(id="H_SPINE", has_formal_levels=True, **common)


class _Absent:
    """A field the proxy does not expose — not a disagreement, just unloaded."""


_ABSENT = _Absent()


def _comparable(value):
    """Content, with the container type set aside.

    The proxy answers `levels` as a tuple and the artefact as an `ItemList`;
    that difference is the proxy doing its job, and comparing reprs reports it
    as a loss. A `Query` is skipped outright for the same reason one step
    further along — deferring the collection is the entire point of the proxy,
    so there is nothing to compare until someone asks.
    """
    if isinstance(value, str) or not hasattr(value, "__iter__"):
        return repr(value)
    return [repr(item) for item in value]


# ── no reader hand-writes the spine again (#320) ────────────────────────


def test_every_reader_builds_its_spine_from_the_shared_helper():
    """A maintainable is constructed from `_spine_kwargs`, never field by field.

    Nineteen readers each wrote the same seven kwargs by hand and seven of them
    omitted `is_final` and `uri` — stored correctly, read back as the default,
    on six types plus HierarchyAssociation (#320). Fixing the seven would have
    left the twentieth reader free to be written the same way.

    So this checks the *shape*, not the symptom: the round-trip guard in
    `test_roundtrip_derived` catches a field that is lost today, and this
    catches the construction that would lose the next one. Only a static read
    can do that — a reader added tomorrow with six of seven kwargs is caught
    here immediately, and there only once someone sets that field in a fixture.
    """
    import ast
    import inspect

    from sdmxlib.model.base import MaintainableArtefact
    from sdmxlib.storage import readers

    maintainables = {
        cls.__name__
        for cls in vars(readers).values()
        if inspect.isclass(cls) and issubclass(cls, MaintainableArtefact) and cls is not MaintainableArtefact
    }
    assert maintainables, "no maintainable types visible in readers — has the module been restructured?"

    tree = ast.parse(inspect.getsource(readers))
    hand_written = [
        f"{node.func.id} at readers.py:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in maintainables
        # A spine comes from the helper or not at all. `id=` is the tell: a
        # construction that names it is building the spine itself.
        and any(kw.arg == "id" for kw in node.keywords)
        and not any(
            kw.arg is None and isinstance(kw.value, ast.Call) and getattr(kw.value.func, "id", "").startswith("_spine")
            for kw in node.keywords
        )
    ]
    assert not hand_written, "these construct a maintainable without `**_spine_kwargs(meta)`:\n  " + "\n  ".join(
        hand_written
    )


def test_a_reader_reads_the_2_1_flag_back_exactly_where_the_class_has_one():
    """`_spine_kwargs_21` for a class with `legacy_is_final`, `_spine_kwargs` otherwise.

    The wrong choice fails only one way loudly: a class without the field
    given it raises `TypeError`. The other way — a 2.1 class built from
    `_spine_kwargs` — reads the flag back as `None` and says nothing, which is
    the #320 failure again for one field (#420). The store's field census
    catches it for the classes `maximal` carries; this catches the rest.
    """
    import ast
    import inspect

    from sdmxlib.model.base import MaintainableArtefact
    from sdmxlib.storage import readers

    has_flag = {
        name: "legacy_is_final" in attrs.fields_dict(cls)
        for name, cls in vars(readers).items()
        if inspect.isclass(cls) and issubclass(cls, MaintainableArtefact) and attrs.has(cls)
    }
    assert has_flag["Codelist"], "the discrimination needs a class that has the flag"
    assert not has_flag["Hierarchy"], "and one that does not"

    wrong = [
        f"{node.func.id} at readers.py:{node.lineno} uses {helper}"
        for node in ast.walk(ast.parse(inspect.getsource(readers)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in has_flag
        for kw in node.keywords
        if kw.arg is None and isinstance(kw.value, ast.Call) and isinstance(kw.value.func, ast.Name)
        for helper in [kw.value.func.id]
        if helper.startswith("_spine") and helper != ("_spine_kwargs_21" if has_flag[node.func.id] else "_spine_kwargs")
    ]
    assert not wrong, "\n  ".join(["these read the 2.1 flag back wrong:", *wrong])
