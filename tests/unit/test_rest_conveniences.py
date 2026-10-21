"""Tests for parse_rest_ref + StructureMessage.containing (issue #60)."""

from __future__ import annotations

import pytest

import sdmxlib as sl
from sdmxlib.model.category import Categorisation, CategoryScheme
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.concept import ConceptScheme
from sdmxlib.model.constraint import DataConstraint
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.organisation import AgencyScheme, DataProviderScheme
from sdmxlib.model.provision import ProvisionAgreement

pytestmark = pytest.mark.unit


# ── parse_rest_ref ──────────────────────────────────────────────────────


def test_parse_rest_ref_dataflow():
    urn = Dataflow.parse_rest_ref("ESTAT,STS_INPR_M,1.0")
    assert urn.agency == "ESTAT"
    assert urn.id == "STS_INPR_M"
    assert urn.version == "1.0"
    assert urn.artefact_class == "Dataflow"


def test_parse_rest_ref_codelist():
    urn = Codelist.parse_rest_ref("SDMX,CL_FREQ,1.0")
    assert urn.agency == "SDMX"
    assert urn.id == "CL_FREQ"
    assert urn.version == "1.0"
    assert urn.artefact_class == "Codelist"


def test_parse_rest_ref_too_few_parts_raises():
    with pytest.raises(ValueError, match="2 parts"):
        Dataflow.parse_rest_ref("ESTAT,STS_INPR_M")


def test_parse_rest_ref_too_many_parts_raises():
    with pytest.raises(ValueError, match="4 parts"):
        Dataflow.parse_rest_ref("ESTAT,STS_INPR_M,1.0,extra")


def test_parse_rest_ref_round_trips_urn():
    urn = Dataflow.parse_rest_ref("ESTAT,STS_INPR_M,1.0")
    same = Dataflow.urn_for(agency="ESTAT", id="STS_INPR_M", version="1.0")
    assert urn == same


# ── StructureMessage.containing ─────────────────────────────────────────


def _name(text: str) -> InternationalString:
    return InternationalString(en=text)


def test_containing_codelist():
    cl = Codelist(id="CL_FREQ", maintainer="SDMX", name=_name("FREQ"))
    msg = StructureMessage.containing(cl)
    assert list(msg.codelists) == [cl]
    assert list(msg.dataflows) == []


def test_containing_dataflow():
    df = Dataflow(id="DF", maintainer="ESTAT", name=_name("Flow"))
    msg = StructureMessage.containing(df)
    assert list(msg.dataflows) == [df]


def test_containing_data_structure():
    dsd = DataStructure(id="DSD", maintainer="ESTAT", name=_name("DSD"))
    msg = StructureMessage.containing(dsd)
    assert list(msg.data_structures) == [dsd]


def test_containing_concept_scheme():
    cs = ConceptScheme(id="CS", maintainer="SDMX", name=_name("CS"))
    msg = StructureMessage.containing(cs)
    assert list(msg.concept_schemes) == [cs]


def test_containing_category_scheme():
    cs = CategoryScheme(id="CS", maintainer="SDMX", name=_name("CS"))
    msg = StructureMessage.containing(cs)
    assert list(msg.category_schemes) == [cs]


def test_containing_categorisation():
    from sdmxlib.model.ref import Ref

    cat = Categorisation(
        id="CAT",
        maintainer="SDMX",
        name=_name("CAT"),
        source=Ref[Dataflow].unresolved(Dataflow.urn_for(agency="X", id="Y")),
        target=Ref[CategoryScheme].unresolved(CategoryScheme.urn_for(agency="X", id="CS")),
    )
    msg = StructureMessage.containing(cat)
    assert list(msg.categorisations) == [cat]


def test_containing_provision_agreement():
    from sdmxlib.model.ref import Ref

    pa_ = ProvisionAgreement(
        id="PA",
        maintainer="SDMX",
        name=_name("PA"),
        dataflow=Ref[Dataflow].unresolved(Dataflow.urn_for(agency="X", id="Y")),
    )
    msg = StructureMessage.containing(pa_)
    assert list(msg.provision_agreements) == [pa_]


def test_containing_agency_scheme():
    asch = AgencyScheme(id="AGENCIES", maintainer="SDMX", name=_name("Agencies"))
    msg = StructureMessage.containing(asch)
    assert list(msg.agency_schemes) == [asch]


def test_containing_data_provider_scheme():
    dpsch = DataProviderScheme(id="DATA_PROVIDERS", maintainer="SDMX", name=_name("Providers"))
    msg = StructureMessage.containing(dpsch)
    assert list(msg.data_provider_schemes) == [dpsch]


def test_containing_constraint():
    c = DataConstraint(id="C", maintainer="SDMX", name=_name("C"))
    msg = StructureMessage.containing(c)
    assert list(msg.constraints) == [c]


def test_containing_metadataflow():
    mdf = Metadataflow(id="MDF", maintainer="SDMX", name=_name("MDF"))
    msg = StructureMessage.containing(mdf)
    assert list(msg.metadataflows) == [mdf]


def test_containing_metadata_structure():
    msd = MetadataStructure(id="MSD", maintainer="SDMX", name=_name("MSD"))
    msg = StructureMessage.containing(msd)
    assert list(msg.metadata_structures) == [msd]


def test_containing_unknown_type_raises():
    with pytest.raises(TypeError, match="no msg_field"):
        StructureMessage.containing("not an artefact")


def test_containing_artefact_with_unknown_slot_raises():
    """An object whose msg_field doesn't match a StructureMessage slot is rejected."""

    class FakeArtefact:
        msg_field = "metadata_sets"  # belongs to MetadataMessage

    with pytest.raises(TypeError, match="not a StructureMessage field"):
        StructureMessage.containing(FakeArtefact())


def test_containing_materialises_a_lazy_artefact() -> None:
    """Found by executing the REST-facade page's own example.

    That page's `/structure` handler is `LocalRegistry.get(urn)` handed to
    `containing` — and `LocalRegistry.get` returns a `LazyCodelist`, which has
    no `msg_field`. So the documented pattern raised `TypeError` for the most
    common artefact type there is.

    Materialising rather than accepting the lazy object: the registry closes
    when the handler returns, and a message still holding a `LazyCodelist`
    would raise at serialisation time instead — later, and somewhere else.
    """
    codelist = sl.Codelist(
        id="CL_FREQ",
        maintainer="AG",
        version="1.0",
        name={"en": "Frequency"},
        codes=[sl.Code(id="A", name={"en": "Annual"})],
    )
    with sl.LocalRegistry(":memory:") as local:
        local.add(codelist)
        lazy = local.get(codelist.urn)
        assert type(lazy).__name__ == "LazyCodelist", "otherwise this test proves nothing"
        message = sl.StructureMessage.containing(lazy)

    # Read *after* the registry closed — the point of materialising.
    assert [c.id for c in message.codelists[0].codes] == ["A"]
