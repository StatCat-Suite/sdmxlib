"""An absent reference is stored as NULL, never as the empty string.

Eight columns were `TEXT NOT NULL` while the model field behind each one was
`Optional`, and the writer squared that by storing `''`. The constraint was
therefore enforcing nothing, and the cost landed in the query language: asking
for absence found none of it, because `is_null()` compared against a NULL that
was never written. An unscoped `HierarchyAssociation` — the *default* hierarchy
for a dimension — could not be found by asking which associations have no
context.

The sweep below is derived from `information_schema` rather than hand-listed,
so a new reference column is covered the day it is added. That matters more
than the per-artefact cases: the per-artefact tests say the writers are right
today, the sweep says nobody can quietly reintroduce the convention.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.category import Categorisation
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.hierarchy import Hierarchy, HierarchyAssociation
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref, SupportsUrn

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

_NAME = {"en": "x"}


def _artefacts() -> list[object]:
    """One of each artefact whose references are all absent."""
    return [
        Dataflow(id="DF", maintainer="SC", version="1.0", name=_NAME),
        Categorisation(id="CAT", maintainer="SC", version="1.0", name=_NAME),
        ProvisionAgreement(id="PA", maintainer="SC", version="1.0", name=_NAME),
        Metadataflow(id="MDF", maintainer="SC", version="1.0", name=_NAME),
        MetadataProvisionAgreement(id="MPA", maintainer="SC", version="1.0", name=_NAME),
        HierarchyAssociation(
            id="HA",
            maintainer="SC",
            version="1.0",
            name=_NAME,
            linked_hierarchy=Ref[Hierarchy].unresolved(Hierarchy.urn_for(agency="SC", id="GEO", version="1.0")),
        ),
    ]


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    with sl.LocalRegistry(":memory:") as registry:
        for artefact in _artefacts():
            assert isinstance(artefact, SupportsUrn)
            registry.add(artefact)
        yield registry


def _reference_columns(reg: sl.LocalRegistry) -> list[tuple[str, str]]:
    """Every `*_urn` column on the `sdmx` schema, from the live catalog."""
    rows = reg.connection.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'sdmx' AND column_name LIKE '%_urn'
        ORDER BY table_name, column_name
        """
    ).fetchall()
    return [(str(t), str(c)) for t, c in rows]


def test_no_reference_column_holds_the_empty_string(reg: sl.LocalRegistry) -> None:
    """The sweep. Derived from the catalog, so a new column is covered free."""
    offenders: list[str] = []
    for table, column in _reference_columns(reg):
        # Identifier interpolation only — both halves come from the catalog.
        count = reg.connection.execute(f'SELECT count(*) FROM sdmx."{table}" WHERE "{column}" = \'\'').fetchone()
        assert count is not None
        if count[0]:
            offenders.append(f"{table}.{column} ({count[0]} rows)")
    assert not offenders, f"absent references stored as '': {offenders}"


def test_the_sweep_would_notice(reg: sl.LocalRegistry) -> None:
    """The guard above is worth nothing if it inspects an empty store.

    Writing the empty string by hand must make it fail — otherwise a green
    result means "no rows" rather than "no empty strings".
    """
    reg.connection.execute("UPDATE sdmx.dataflow SET dsd_urn = ''")
    with pytest.raises(AssertionError, match=r"dataflow\.dsd_urn"):
        test_no_reference_column_holds_the_empty_string(reg)


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("dataflow", "dsd_urn"),
        ("categorisation", "source_urn"),
        ("categorisation", "target_urn"),
        ("provision_agreement", "dataflow_urn"),
        ("provision_agreement", "data_provider_urn"),
        ("metadataflow", "msd_urn"),
        ("metadata_provision_agreement", "metadataflow_urn"),
        ("metadata_provision_agreement", "metadata_provider_urn"),
        ("hierarchy_association", "context_object_urn"),
    ],
)
def test_an_absent_reference_reads_as_null(reg: sl.LocalRegistry, table: str, column: str) -> None:
    """Each column that carried `''`, named individually.

    The list is deliberately explicit here, unlike the sweep: these nine are
    the sites the change had to touch, and a rewrite that quietly stopped
    writing one of these rows would pass a sweep that finds nothing to check.
    """
    row = reg.connection.execute(f'SELECT "{column}" FROM sdmx."{table}"').fetchone()
    assert row is not None, f"no {table} row was written"
    assert row[0] is None


def test_asking_for_absence_finds_it(reg: sl.LocalRegistry) -> None:
    """The user-visible half, and the reason this was worth changing.

    An unscoped `HierarchyAssociation` is the *default* hierarchy for a
    dimension — the one that applies when nothing narrower does. Before this,
    `is_null()` returned nothing for it.
    """
    q = sl.q.hierarchy_association
    assert reg.query(q).filter(q.context_object.urn.is_null()).ids() == ["HA"]
    assert reg.query(q).filter(q.context_object.urn.is_not_null()).ids() == []

    flow = sl.q.dataflow
    assert reg.query(flow).filter(flow.dsd.urn.is_null()).ids() == ["DF"]


def test_a_present_reference_is_still_found(reg: sl.LocalRegistry) -> None:
    """The other half of the pair: NULL for absent must not mean NULL for all."""
    dsd = sl.DataStructure(id="DSD", maintainer="SC", version="1.0", name=_NAME)
    reg.add(dsd)
    reg.add(Dataflow(id="DF2", maintainer="SC", version="1.0", name=_NAME, structure=Ref.to(dsd)))

    flow = sl.q.dataflow
    assert reg.query(flow).filter(flow.dsd.urn.is_not_null()).ids() == ["DF2"]
    assert sorted(reg.query(flow).filter(flow.dsd.urn.is_null()).ids()) == ["DF"]
