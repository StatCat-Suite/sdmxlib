"""`q.agency`, `q.data_provider`, `q.provision_agreement` — who supplies what.

The cluster exists to answer one catalogue question — *which provider is
cleared to supply this dataflow* — and it is the first place two relations of
different **kinds** meet on one accessor: `provision_agreement.dataflow`
reaches a maintainable on the spine, `provision_agreement.data_provider`
reaches an *item* of a scheme.

That distinction was a live bug. The `data_provider` edge pointed at
`artefact.urn`, and a DataProvider's urn never appears there — the *scheme* is
the artefact, the provider is an item of it. The join matched zero rows where
the corrected one matches every row. Nothing had caught it because no accessor
reached the edge, so it had never been run, and an empty result is not an
error. `test_a_provider_is_an_item_not_an_artefact` is the regression.

`agency` is the odd tree: `parent_id` is populated from `Agency.parent`, but
the writer bakes no `depth` / `ancestor_ids`, so it carries `parent` /
`children` and no transitive pair. Ancestry in this language is a stored fact
rather than a recursive query, so that absence is the honest shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.organisation import Agency, AgencyScheme, DataProvider, DataProviderScheme
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.query._edges import EDGES
from sdmxlib.query.accessors import q

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


def _provider_urn(scheme: str, item_id: str) -> SdmxUrn[DataProvider]:
    return SdmxUrn.make(
        DataProvider,
        package="base",
        artefact_class="DataProvider",
        agency="SDMX",
        id=scheme,
        version="1.0",
        item_id=item_id,
    )


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """Two providers, a nested agency scheme, and two agreements over one flow.

    Two agreements because a fixture with one cannot tell a correlated lookup
    from an unconditional one, and the second flow has none so a projection can
    show the difference.
    """
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            AgencyScheme(
                id="AGENCIES",
                maintainer=Agency(id="SDMX"),
                name={"en": "Agencies"},
                agencies=[
                    Agency(id="ESTAT", name={"en": "Eurostat"}),
                    Agency(id="ESTAT_D1", name={"en": "Unit D1"}),
                    Agency(id="OECD", name={"en": "OECD"}),
                ],
            )
        )
        registry.add(
            DataProviderScheme(
                id="DATA_PROVIDERS",
                maintainer=Agency(id="SDMX"),
                name={"en": "Providers"},
                providers=[
                    DataProvider(id="ECB", name={"en": "European Central Bank"}),
                    DataProvider(id="BIS", name={"en": "Bank for International Settlements"}),
                ],
            )
        )
        dsd = DataStructure(id="CPI", maintainer=Agency(id="ESTAT"), name={"en": "CPI"})
        registry.add(dsd)
        registry.add(Dataflow(id="DF_CPI", maintainer=Agency(id="ESTAT"), name={"en": "CPI flow"}, structure=dsd))
        registry.add(Dataflow(id="DF_BARE", maintainer=Agency(id="ESTAT"), name={"en": "No agreements"}, structure=dsd))
        for pa_id, provider in (("PA_ECB", "ECB"), ("PA_BIS", "BIS")):
            registry.add(
                ProvisionAgreement(
                    id=pa_id,
                    maintainer=Agency(id="ESTAT"),
                    name={"en": pa_id},
                    dataflow=Ref[object].unresolved(Dataflow.urn_for(agency="ESTAT", id="DF_CPI", version="1.0")),
                    data_provider=Ref[object].unresolved(_provider_urn("DATA_PROVIDERS", provider)),
                )
            )
        yield registry


# ── the roots ─────────────────────────────────────────────────────────────


def test_the_roots_discriminate(reg: sl.LocalRegistry) -> None:
    assert sorted(reg.query(q.agency).ids()) == ["ESTAT", "ESTAT_D1", "OECD"]
    assert sorted(reg.query(q.data_provider).ids()) == ["BIS", "ECB"]
    assert sorted(reg.query(q.provision_agreement).ids()) == ["PA_BIS", "PA_ECB"]
    assert reg.query(q.agency_scheme).ids() == ["AGENCIES"]
    assert reg.query(q.data_provider_scheme).ids() == ["DATA_PROVIDERS"]


def test_an_agency_is_not_the_maintainer_column(reg: sl.LocalRegistry) -> None:
    """`artefact.agency` is a bare maintainer id with no row to reach; `q.agency`
    is the organisation a scheme declares. The fixture makes them differ —
    `SDMX` maintains the scheme, `ESTAT` is an item in it."""
    assert reg.query(q.agency_scheme).select(m=q.agency_scheme.agency).collect()["m"].to_list() == ["SDMX"]
    assert "SDMX" not in reg.query(q.agency).ids()
    assert type(q.agency).__table__ == "agency"


# ── the bug this cluster surfaced ─────────────────────────────────────────


def test_a_provider_is_an_item_not_an_artefact(reg: sl.LocalRegistry) -> None:
    """The regression. `data_provider_urn` names an *item* of a scheme, so the
    edge must land on `sdmx.data_provider` — pointed at `artefact.urn` it
    matched nothing, silently."""
    edge = next(e for e in EDGES if e.name == "data_provider" and e.source == "provision_agreement")
    assert edge.target == "data_provider"
    assert edge.target_column == "provider_urn"
    found = reg.query(q.provision_agreement).filter(q.provision_agreement.data_provider.id == "ECB")
    assert found.ids() == ["PA_ECB"]


def test_the_provider_urn_is_absent_from_the_artefact_spine(reg: sl.LocalRegistry) -> None:
    """Why the old edge could not work, stated against the data rather than
    against the declaration."""
    rows = reg.sql(
        "SELECT count(*) AS n FROM sdmx.artefact a JOIN sdmx.provision_agreement p ON a.urn = p.data_provider_urn"
    ).collect()
    assert rows["n"].to_list() == [0]
    rows = reg.sql(
        """
        SELECT count(*) AS n FROM sdmx.data_provider d
        JOIN sdmx.provision_agreement p ON d.provider_urn = p.data_provider_urn
        """
    ).collect()
    assert rows["n"].to_list() == [2]


# ── the agreement's two ends ──────────────────────────────────────────────


def test_an_agreement_names_both_ends(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.provision_agreement)
        .order_by(q.provision_agreement.id)
        .select(
            pa=q.provision_agreement.id,
            who=q.provision_agreement.data_provider.name["en"],
            what=q.provision_agreement.dataflow.id,
        )
        .collect()
    )
    assert frame.to_dicts() == [
        {"pa": "PA_BIS", "who": "Bank for International Settlements", "what": "DF_CPI"},
        {"pa": "PA_ECB", "who": "European Central Bank", "what": "DF_CPI"},
    ]


def test_both_ends_are_compound_hops(reg: sl.LocalRegistry) -> None:
    """Both foreign keys sit on the `provision_agreement` extension, so each
    crosses the identity split before following its own FK."""
    assert q.provision_agreement.dataflow.id.path == ("provision_agreement", "dataflow")
    assert q.provision_agreement.data_provider.id.path == ("provision_agreement", "data_provider")


def test_the_agreement_reaches_the_dsd_behind_the_flow(reg: sl.LocalRegistry) -> None:
    """Four hops — agreement, flow, dsd — the traversal the cluster exists for."""
    found = reg.query(q.provision_agreement).filter(q.provision_agreement.dataflow.dsd.id == "CPI")
    assert sorted(found.ids()) == ["PA_BIS", "PA_ECB"]


def test_who_may_supply_this_flow(reg: sl.LocalRegistry) -> None:
    """The catalogue question, read from the flow's side."""
    found = reg.query(q.dataflow).filter(q.dataflow.provision_agreements.data_provider.id == "ECB")
    assert found.ids() == ["DF_CPI"]
    assert reg.query(q.dataflow).filter(q.dataflow.provision_agreements.data_provider.id == "NOPE").ids() == []


def test_a_flow_with_no_agreements_survives_a_projection(reg: sl.LocalRegistry) -> None:
    frame = (
        reg.query(q.dataflow)
        .order_by(q.dataflow.id)
        .select(flow=q.dataflow.id, providers=q.dataflow.provision_agreements.data_provider.id.list())
        .collect()
    )
    rows = dict(zip(frame["flow"].to_list(), frame["providers"].to_list(), strict=True))
    assert sorted(rows["DF_CPI"]) == ["BIS", "ECB"]
    # `[]`, not None — an empty fan-out collects to empty (#341).
    assert rows["DF_BARE"] == []


def test_the_binder_groups_onto_one_agreement(reg: sl.LocalRegistry) -> None:
    """No single agreement pairs `DF_CPI` with a provider that is not on it."""
    same = q.dataflow.provision_agreements.any(
        q.provision_agreement.data_provider.id == "ECB", q.provision_agreement.id == "PA_BIS"
    )
    assert reg.query(q.dataflow).filter(same).ids() == []


# ── the schemes and their items ───────────────────────────────────────────


def test_a_scheme_fans_out_to_its_items(reg: sl.LocalRegistry) -> None:
    agencies = reg.query(q.agency_scheme).select(s=q.agency_scheme.id, a=q.agency_scheme.agencies.id.list()).collect()
    assert sorted(agencies["a"].to_list()[0]) == ["ESTAT", "ESTAT_D1", "OECD"]
    providers = (
        reg.query(q.data_provider_scheme)
        .select(s=q.data_provider_scheme.id, p=q.data_provider_scheme.data_providers.id.list())
        .collect()
    )
    assert sorted(providers["p"].to_list()[0]) == ["BIS", "ECB"]


def test_an_item_reaches_its_scheme(reg: sl.LocalRegistry) -> None:
    assert sorted(reg.query(q.agency).filter(q.agency.scheme.id == "AGENCIES").ids()) == ["ESTAT", "ESTAT_D1", "OECD"]
    assert reg.query(q.data_provider).filter(q.data_provider.scheme.id == "NOPE").ids() == []


# ── not a tree ─────────────────────────────────────────────────────────────


def test_an_agency_scheme_is_flat() -> None:
    """``AgencyType`` restricts ``Parent`` away in every format, so no model field and no edge (#419)."""
    for name in ("parent", "children", "ancestors", "descendants", "depth"):
        assert not hasattr(q.agency, name), name
    assert not [e for e in EDGES if e.source == "agency" and e.target == "agency"]
