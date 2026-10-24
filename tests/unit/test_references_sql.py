"""The references check in SQL agrees with the loading check (GH-129).

The loading check (`sdmxlib.model.validation`'s resolution, over every
artefact built from the store) is the oracle. It shares no code with
`sdmxlib.bundle._references`, which reads stored rows and builds item URNs in
SQL. Both are compared on ``(source artefact URN, target URN)`` pairs, the
loading check's path standing for the artefact it starts at.

The corpus is every SDMX-ML fixture the store holds and the canonical corpus
(`tests/support/canonical_corpus.py`), each stored as it is, then with each
artefact dropped in turn, then with one leaf item of each stored kind deleted.
A drop leaves every reference to what it removed dangling, so the comparison
has findings to disagree on: `test_the_corpus_has_findings_to_disagree_on`
counts them for artefacts, and an item deletion compares only the references
to the deleted item, which is always the finding it makes.
"""

import shutil
import warnings
from collections.abc import Callable, Iterator, Sequence
from functools import cache
from pathlib import Path
from typing import Any

import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.model import AttachmentConstraint
from sdmxlib.model._items import item_fields
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import AttachmentLevel, Attribute, DataStructure, Dimension, Measure
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, HierarchyAssociation
from sdmxlib.model.metadataset import MetadataSet
from sdmxlib.model.ref import Ref, artefact_urn
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import _collect_known_urns, _collect_refs, _resolves, _versions_present
from sdmxlib.storage import readers
from sdmxlib.storage._references import (
    NO_OWNER,
    NOT_REFERENCES,
    STORED_ITEMS,
    UNDECLARED_REFERENCES,
    StoredItems,
    reference_columns,
    unresolved_references,
)
from tests.support.canonical_corpus import corpus
from tests.support.factories import concept_ref
from tests.support.repo import ROOT

pytestmark = pytest.mark.unit

type Pairs = set[tuple[str, str]]


def _oracle(reg: sl.LocalRegistry) -> Pairs:
    """What the loading check finds: each artefact's references that resolve to nothing loaded."""
    loaded = [a for a in reg.to_registry()._store.values() if isinstance(a, MaintainableArtefact)]
    structural = [a for a in loaded if not isinstance(a, MetadataSet)]
    known, versions = _collect_known_urns(structural), _versions_present(structural)
    return {
        (str(artefact_urn(a)), str(ref.urn))
        for a in loaded
        for _, ref in _collect_refs(a, "")
        if not _resolves(ref.urn, known, versions)
    }


def _sql(reg: sl.LocalRegistry) -> Pairs:
    return {(u.source, u.target) for u in unresolved_references(reg.connection)}


# ── the corpus ──────────────────────────────────────────────────────────


@cache
def _corpus() -> dict[str, tuple[MaintainableArtefact, ...]]:
    """Every artefact set the store holds, by name. An AttachmentConstraint is
    SDMX 2.1's alone, and the store refuses it."""
    sets: dict[str, tuple[MaintainableArtefact, ...]] = {}
    for path in sorted((ROOT / "tests" / "fixtures").rglob("*.xml")):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                message = sl.read(path.read_bytes())
            except sl.SdmxParseError, ValueError:
                continue
        if isinstance(message, sl.StructureMessage):
            name = path.relative_to(ROOT / "tests" / "fixtures").as_posix()
            sets[name] = tuple(a for a in message.all_artefacts() if not isinstance(a, AttachmentConstraint))
    sets["canonical"] = tuple(a for a in corpus().values() if not isinstance(a, AttachmentConstraint))
    return sets


def _stored(directory: Path, artefacts: Sequence[MaintainableArtefact]) -> Path:
    path = directory / "store.duckdb"
    with sl.LocalRegistry(path) as reg:
        reg.add_all(artefacts)
    return path


_STORED: dict[str, Path] = {}


@pytest.fixture
def stored(tmp_path_factory: pytest.TempPathFactory) -> Callable[[str], Path]:
    """A corpus set's store, written once per worker and shared by its cases."""

    def get(name: str) -> Path:
        if name not in _STORED:
            _STORED[name] = _stored(tmp_path_factory.mktemp("corpus"), _corpus()[name])
        return _STORED[name]

    return get


def _leaf(conn: duckdb.DuckDBPyConnection, stored: StoredItems) -> tuple[str, str] | None:
    """One item of `stored`'s kind that no other item names as its parent, or None."""
    columns = {name for (name, *_) in conn.execute(f"DESCRIBE sdmx.{stored.table}").fetchall()}
    conditions = [stored.where] if stored.where else []
    conditions += [
        f"NOT EXISTS (SELECT 1 FROM sdmx.{stored.table} AS c "
        f"WHERE c.{stored.scheme} = t.{stored.scheme} AND c.{parent} = t.{stored.key})"
        for parent in ("parent_path", "parent_id")
        if parent in columns
    ]
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    row = conn.execute(f"SELECT {stored.scheme}, {stored.key} FROM sdmx.{stored.table} AS t{where} LIMIT 1").fetchone()
    return None if row is None else (str(row[0]), str(row[1]))


def _item_urn(stored: StoredItems, scheme: str, key: str) -> str:
    item = stored.item
    return f"urn:sdmx:org.sdmx.infomodel.{item.sdmx_package}.{item.sdmx_class}={scheme.split('=', 1)[1]}.{key}"


def _cases() -> Iterator[tuple[str, str]]:
    for name, artefacts in _corpus().items():
        yield name, "as stored"
        for artefact in artefacts:
            yield name, f"drop {artefact_urn(artefact)}"


def _compare(original: Path, tmp_path: Path, case: str) -> tuple[Pairs, Pairs] | None:
    """Both checks' pairs for one case, or None when the case does not apply.

    A case does not apply when the store holds no item of the kind to
    delete, or when the sabotage leaves an artefact the registry refuses to
    build, which hides its references from the loading check alone.
    """
    path = tmp_path / "store.duckdb"
    shutil.copyfile(original, path)
    target: str | None = None
    if case.startswith("drop "):
        with sl.LocalRegistry(path) as reg:
            reg.drop(SdmxUrn.parse(case.removeprefix("drop ")))
    elif case.startswith("delete a "):
        (stored,) = (s for s in STORED_ITEMS if s.item.__name__ == case.removeprefix("delete a "))
        with sl.LocalRegistry(path) as reg:
            leaf = _leaf(reg.connection, stored)
            if leaf is None:
                return None
            reg.connection.execute(
                f"DELETE FROM sdmx.{stored.table} WHERE {stored.scheme} = ? AND {stored.key} = ?", list(leaf)
            )
            # An item deleted from its table leaves the references it held in
            # `artefact_ref`, which no real store does, so only the
            # references *to* it are compared.
            target = _item_urn(stored, *leaf)
    with sl.LocalRegistry(path) as reg:
        oracle = _oracle(reg)
        if reg.refused:
            return None
        sql = _sql(reg)
    if target is not None:
        return {p for p in sql if p[1] == target}, {p for p in oracle if p[1] == target}
    return sql, oracle


@pytest.mark.parametrize(("name", "case"), list(_cases()))
def test_the_sql_check_finds_what_the_loading_check_finds(
    stored: Callable[[str], Path], tmp_path: Path, name: str, case: str
) -> None:
    compared = _compare(stored(name), tmp_path, case)
    if compared is None:
        pytest.skip("the case does not apply to this store")
    sql, oracle = compared
    assert sql == oracle, f"SQL only: {sorted(sql - oracle)}\nloading check only: {sorted(oracle - sql)}"


@pytest.mark.parametrize("name", list(_corpus()))
def test_deleting_an_item_of_each_kind_agrees(stored: Callable[[str], Path], tmp_path: Path, name: str) -> None:
    """One leaf item of each kind the store holds, deleted in turn."""
    disagreements: dict[str, tuple[Pairs, Pairs]] = {}
    for stored_items in STORED_ITEMS:
        kind = stored_items.item.__name__
        case = tmp_path / kind
        case.mkdir()
        compared = _compare(stored(name), case, f"delete a {kind}")
        if compared is not None and compared[0] != compared[1]:
            disagreements[kind] = compared
    assert disagreements == {}


def test_the_corpus_has_findings_to_disagree_on(stored: Callable[[str], Path], tmp_path: Path) -> None:
    """Discrimination for the differential: across a sample of drops the loading
    check finds dangling references, so agreement is not two empty sets."""
    found = 0
    for name in ("sdmx_ml30/complete.xml", "canonical"):
        for index, artefact in enumerate(_corpus()[name]):
            case = tmp_path / f"{len(name)}-{index}"
            case.mkdir()
            compared = _compare(stored(name), case, f"drop {artefact_urn(artefact)}")
            found += bool(compared and compared[1])
    assert found >= 10, f"{found} drops left a dangling reference; the floor is a sample size, not a count"
    dangling = set()
    for stored_items in STORED_ITEMS:
        kind = stored_items.item.__name__
        case = tmp_path / kind
        case.mkdir()
        compared = _compare(stored("canonical"), case, f"delete a {kind}")
        if compared and compared[1]:
            dangling.add(kind)
    assert dangling == {"Code", "Concept", "DataProvider", "MetadataProvider"}, "the item deletions that are findings"


# ── spellings ───────────────────────────────────────────────────────────


def _concepts(*ids: str) -> ConceptScheme:
    return ConceptScheme(
        id="CS", maintainer="T", version="1.0", name={"en": "cs"}, concepts=[Concept(id=i, name={"en": i}) for i in ids]
    )


def _code_ref(version: str) -> Ref[Any]:
    return Ref[Any].unresolved(
        SdmxUrn.make(
            object, package="codelist", artefact_class="Code", agency="AG", id="CL_AREA", version=version, item_id="CA"
        )
    )


def _hierarchy_naming(version: str) -> list[MaintainableArtefact]:
    codes = Codelist(
        id="CL_AREA", maintainer="AG", version="1.0", name={"en": "a"}, codes=[Code(id="CA", name={"en": "c"})]
    )
    tree = [HierarchicalCode(id="CA", code=_code_ref(version))]
    return [codes, Hierarchy(id="H", maintainer="AG", version="1.0", name={"en": "h"}, tree=tree)]


def _association_naming(artefact_class: str, item_id: str) -> list[MaintainableArtefact]:
    dsd = DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name={"en": "dsd"},
        dimensions=[Dimension(id="GEO", concept=concept_ref("GEO"))],
        attributes=[Attribute(attachment=AttachmentLevel.DATASET, id="OBS_STATUS", concept=concept_ref("OBS_STATUS"))],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    hierarchy = Hierarchy(id="H", maintainer="AG", version="1.0", name={"en": "h"})
    linked = SdmxUrn.make(
        object,
        package="datastructure",
        artefact_class=artefact_class,
        agency="AG",
        id="DSD",
        version="1.0",
        item_id=item_id,
    )
    association = HierarchyAssociation(
        id="HA",
        maintainer="AG",
        version="1.0",
        name={"en": "ha"},
        linked_hierarchy=Ref[Hierarchy].unresolved(Hierarchy.urn_for(agency="AG", id="H", version="1.0")),
        linked_object=Ref[Any].unresolved(linked),
    )
    return [_concepts("GEO", "OBS_STATUS", "OBS_VALUE"), dsd, hierarchy, association]


def _version_less_flow() -> list[MaintainableArtefact]:
    dsd = DataStructure(id="DSD1", maintainer="AG", version="1.0.0", name={"en": "dsd"})
    flow = Dataflow(
        id="DF1",
        maintainer="AG",
        version="1.0.0",
        name={"en": "df"},
        structure=Ref[DataStructure].unresolved(DataStructure.urn_for(agency="AG", id="DSD1")),
    )
    return [dsd, flow]


@pytest.mark.parametrize(
    ("artefacts", "dangling"),
    [
        (_hierarchy_naming("1.0"), 0),
        (_hierarchy_naming("latest"), 0),
        (_hierarchy_naming("2.0"), 1),
        (_version_less_flow(), 0),
        (_association_naming("Attribute", "OBS_STATUS"), 0),
        (_association_naming("DataAttribute", "OBS_STATUS"), 0),
        (_association_naming("Measure", "OBS_VALUE"), 0),
        (_association_naming("PrimaryMeasure", "OBS_VALUE"), 0),
        (_association_naming("DataAttribute", "OBS_VALUE"), 1),
        (_association_naming("Dimension", "NOPE"), 1),
    ],
    ids=[
        "item-exact",
        "item-version-less",
        "item-other-version",
        "scheme-version-less",
        "attribute-as-written",
        "attribute-respelled",
        "measure-as-written",
        "measure-respelled",
        "respelled-but-absent",
        "absent-component",
    ],
)
def test_each_spelling_the_loading_check_accepts_resolves_in_sql(
    tmp_path: Path, artefacts: list[MaintainableArtefact], dangling: int
) -> None:
    with sl.LocalRegistry(_stored(tmp_path, artefacts)) as reg:
        sql, oracle = _sql(reg), _oracle(reg)
    assert sql == oracle
    assert len(sql) == dangling


# ── what artefact_ref leaves out ────────────────────────────────────────


def test_a_concepts_core_representation_is_checked(tmp_path: Path) -> None:
    """A concept's core representation has no ``artefact_ref`` row, so it is
    read from ``concept.codelist_urn``."""
    missing = Codelist.urn_for(agency="AG", id="CL_GONE", version="1.0")
    scheme = ConceptScheme(
        id="CS",
        maintainer="AG",
        version="1.0",
        name={"en": "cs"},
        concepts=[Concept(id="C", name={"en": "c"}, core_representation=Ref[Codelist].unresolved(missing))],
    )
    with sl.LocalRegistry(_stored(tmp_path, [scheme])) as reg:
        assert reg.connection.execute("SELECT count(*) FROM sdmx.artefact_ref").fetchone() == (0,)
        (found,) = unresolved_references(reg.connection)
    assert (found.source, found.role, found.target) == (str(artefact_urn(scheme)), "codelist", str(missing))


def test_the_check_builds_no_artefact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The check reads rows; building an artefact is what made it cost 30 s
    and 5 GB on the census bundle."""
    path = _stored(tmp_path, _corpus()["canonical"])
    built: list[str] = []

    def counting(*args: object, **kwargs: object) -> None:
        built.append("artefact")

    with sl.LocalRegistry(path) as reg:
        reg.drop(SdmxUrn.parse(str(artefact_urn(_corpus()["canonical"][0]))))
        monkeypatch.setattr(readers, "get_artefact", counting)
        assert unresolved_references(reg.connection), "a drop leaves something to find"
    assert built == []


# ── inventories ─────────────────────────────────────────────────────────


#: Item classes the loading check registers that the store does not keep,
#: so neither check can see one: a metadata structure's targets and report
#: structures have no table.
_UNSTORED = {"MetadataTarget", "ReportStructure"}


def test_the_stored_items_are_the_items_the_loading_check_registers() -> None:
    registered: set[str] = set()
    seen: set[type] = set()
    pending: list[type] = [MaintainableArtefact]
    while pending:
        cls = pending.pop()
        if cls in seen:
            continue
        seen.add(cls)
        pending += cls.__subclasses__()
        for _, item in item_fields(cls):
            registered.add(item.sdmx_class)
            pending.append(item)
    assert {s.item.sdmx_class for s in STORED_ITEMS} == registered - _UNSTORED
    assert len(STORED_ITEMS) == 20


@cache
def _urn_columns() -> dict[tuple[str, str], int]:
    """Every ``*_urn`` column of every table in a fresh store, with its position."""
    with sl.LocalRegistry(":memory:") as reg:
        rows = reg.connection.execute(
            """
            SELECT c.table_name, c.column_name, c.ordinal_position
            FROM information_schema.columns AS c
            JOIN information_schema.tables AS t USING (table_schema, table_name)
            WHERE c.table_schema = 'sdmx' AND t.table_type = 'BASE TABLE' AND c.column_name LIKE '%_urn'
            """
        ).fetchall()
    return {(table, column): position for table, column, position in rows}


#: ``*_urn`` columns past a table's first that hold no reference, with the reason.
_NEITHER: dict[tuple[str, str], str] = {
    ("annotation", "owner_artefact_urn"): "a copy of the owning artefact's URN, for the delete cascade",
    ("link", "owner_artefact_urn"): "a copy of the owning artefact's URN, for the delete cascade",
    ("annotation_url", "link_urn"): "a link's urn: text naming anything, which the model holds as a string",
    ("artefact_ref", "target_urn"): "the reference table itself, read whole",
    ("hierarchy_association", "linked_object_owner_urn"): "the scheme part of `linked_object_urn`, for a join",
    ("hierarchy_level", "codelist_urn"): "always NULL: SDMX gives a level's coding format no enumeration",
    ("metadata_declared_target", "target_urn"): "a declared target pattern, which the model keeps as text",
}


def test_every_urn_column_is_a_reference_an_owner_or_has_a_reason() -> None:
    """A new ``*_urn`` column arrives as a decision.

    A table's first column names its row's owner, or the row itself. Past it a
    ``*_urn`` column is a reference the check reads, the owning scheme of an
    item table, or is listed with the reason it is neither.
    """
    references = {(table, column) for table, _, column in reference_columns()}
    owners = {(table, owner) for table, owner, _ in reference_columns()} | {(s.table, s.scheme) for s in STORED_ITEMS}
    later = {key for key, position in _urn_columns().items() if position > 1}
    assert later - references - owners - set(_NEITHER) - set(NOT_REFERENCES) == set()
    assert references <= set(_urn_columns()), "every reference column exists"
    assert set(_NEITHER).isdisjoint(references)
    assert set(UNDECLARED_REFERENCES) <= references
    assert set(NOT_REFERENCES).isdisjoint(references)
    assert set(NO_OWNER).isdisjoint({t for t, _, _ in reference_columns()})
