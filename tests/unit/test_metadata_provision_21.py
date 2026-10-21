"""3.x metadata providers and provision agreements, written as SDMX-ML 2.1 provisions metadata (#421).

2.1 has neither class. It provisioned reference metadata with a plain
ProvisionAgreement over a metadataflow, naming a data provider, so that is
what is written: an agency's metadata providers in its DATA_PROVIDERS scheme,
marked, and each agreement over its metadataflow. The reader undoes both.
"""

import attrs
import pytest

from sdmxlib.formats import parse, serialize
from sdmxlib.formats.gaps import unrepresentable
from sdmxlib.formats.sdmx_ml21._synthetic import METADATA_PROVIDER_TYPE, METADATA_PROVIDERS_MERGED_TYPE
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.organisation import DataProvider, DataProviderScheme, MetadataProvider, MetadataProviderScheme
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.validation import ValidationError
from tests.support.narrow import present
from tests.support.roundtrip import as_structure, structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit

_META = MetadataProviderScheme(
    id="METADATA_PROVIDERS",
    maintainer="AG",
    name={"en": "Metadata providers"},
    providers=[MetadataProvider(id="MP", name={"en": "mp"}), MetadataProvider(id="MQ", name={"en": "mq"})],
)
_DATA = DataProviderScheme(
    id="DATA_PROVIDERS",
    maintainer="AG",
    name={"en": "Data providers"},
    providers=[DataProvider(id="DP", name={"en": "dp"})],
)
_MDF = Metadataflow(
    id="MDF",
    maintainer="AG",
    name={"en": "mdf"},
    targets=("urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=*:*(*)",),
)
_MPA = MetadataProvisionAgreement(
    id="MPA",
    maintainer="AG",
    name={"en": "mpa"},
    metadataflow=_MDF.urn,
    metadata_provider=MetadataProvider.urn_for(agency="AG", id="MP", version="1.0"),
)


def _through_21(msg: StructureMessage) -> StructureMessage:
    written = serialize(msg, version="2.1")
    assert_valid_sdmx_ml(written, structure_schema("2.1"))
    back = as_structure(parse(written))
    assert back.refused == ()
    assert back.lost == ()
    return back


@pytest.mark.parametrize("data", [[], [_DATA]], ids=["alone", "beside-data-providers"])
def test_metadata_providers_and_their_agreement_come_back_from_2_1(data: list[DataProviderScheme]) -> None:
    msg = StructureMessage(
        metadata_provider_schemes=[_META],
        data_provider_schemes=data,
        metadataflows=[_MDF],
        metadata_provision_agreements=[_MPA],
    )
    back = _through_21(msg)
    assert list(back.metadata_provider_schemes) == [_META]
    assert list(back.data_provider_schemes) == data
    assert list(back.provision_agreements) == []
    (mpa,) = back.metadata_provision_agreements
    assert present(mpa.metadataflow).urn == _MDF.urn
    assert present(mpa.metadata_provider).urn == present(_MPA.metadata_provider).urn
    assert present(mpa.metadata_provider)() == _META.providers["MP"]
    assert attrs.evolve(mpa, metadataflow=_MPA.metadataflow, metadata_provider=_MPA.metadata_provider) == _MPA


def test_beside_data_providers_each_metadata_provider_is_marked() -> None:
    written = serialize(
        StructureMessage(metadata_provider_schemes=[_META], data_provider_schemes=[_DATA]), version="2.1"
    )
    assert written.count(METADATA_PROVIDER_TYPE.encode() + b"<") == len(_META.providers)
    assert METADATA_PROVIDERS_MERGED_TYPE.encode() in written


def test_2_1_loses_only_the_agreements_targets() -> None:
    assert [gap.field for gap in unrepresentable(attrs.evolve(_MPA, targets=_MDF.targets), "2.1")] == ["targets"]
    assert unrepresentable(_MPA, "2.1") == ()
    assert unrepresentable(_META, "2.1") == ()


_FOREIGN = b"""<?xml version="1.0" encoding="UTF-8"?>
<mes:Structure xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
    xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
    xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">
  <mes:Header><mes:ID>X</mes:ID><mes:Test>false</mes:Test><mes:Prepared>2000-01-01T00:00:00</mes:Prepared>
    <mes:Sender id="S"/></mes:Header>
  <mes:Structures>
    <str:ProvisionAgreements>
      <str:ProvisionAgreement id="PA_META" agencyID="AG" version="1.0">
        <com:Name xml:lang="en">metadata</com:Name>
        <str:StructureUsage><Ref id="MDF" agencyID="AG" version="1.0" class="Metadataflow"
            package="metadatastructure"/></str:StructureUsage>
        <str:DataProvider><Ref id="DP" agencyID="AG" maintainableParentID="DATA_PROVIDERS"
            maintainableParentVersion="1.0" class="DataProvider" package="base"/></str:DataProvider>
      </str:ProvisionAgreement>
      <str:ProvisionAgreement id="PA_DATA" agencyID="AG" version="1.0">
        <com:Name xml:lang="en">data</com:Name>
        <str:StructureUsage><Ref id="DF" agencyID="AG" version="1.0" class="Dataflow"
            package="datastructure"/></str:StructureUsage>
        <str:DataProvider><Ref id="DP" agencyID="AG" maintainableParentID="DATA_PROVIDERS"
            maintainableParentVersion="1.0" class="DataProvider" package="base"/></str:DataProvider>
      </str:ProvisionAgreement>
    </str:ProvisionAgreements>
  </mes:Structures>
</mes:Structure>
"""


def test_a_2_1_agreement_over_a_metadataflow_is_read_as_a_metadata_provision_agreement() -> None:
    assert_valid_sdmx_ml(_FOREIGN, structure_schema("2.1"))
    msg = as_structure(parse(_FOREIGN))
    assert msg.refused == ()
    (pa,) = msg.provision_agreements
    assert isinstance(pa, ProvisionAgreement)
    assert pa.id == "PA_DATA"
    (mpa,) = msg.metadata_provision_agreements
    assert mpa.id == "PA_META"
    assert present(mpa.metadataflow).urn == _MDF.urn
    assert present(mpa.metadata_provider).urn == MetadataProvider.urn_for(agency="AG", id="DP", version="1.0")
    written = serialize(msg, version="3.0")
    assert_valid_sdmx_ml(written, structure_schema("3.0"))


@pytest.mark.parametrize(
    ("msg", "message"),
    [
        pytest.param(
            StructureMessage(
                metadata_provider_schemes=[_META],
                data_provider_schemes=[
                    attrs.evolve(_DATA, providers=[DataProvider(id="MP", name={"en": "clash"})]),
                ],
            ),
            "already has MP",
            id="provider-id-collision",
        ),
        pytest.param(
            StructureMessage(
                metadata_provider_schemes=[attrs.evolve(_META, description={"en": "own"})],
                data_provider_schemes=[_DATA],
            ),
            "its description differs",
            id="scheme-disagrees",
        ),
        pytest.param(
            StructureMessage(
                metadataflows=[_MDF],
                metadata_provision_agreements=[_MPA],
                provision_agreements=[ProvisionAgreement(id="MPA", maintainer="AG", name={"en": "pa"})],
            ),
            "already has one with agency AG, id MPA",
            id="agreement-urn-collision",
        ),
        pytest.param(
            StructureMessage(metadata_provision_agreements=[attrs.evolve(_MPA, metadata_provider=None)]),
            "requires a metadata provider",
            id="no-provider",
        ),
    ],
)
def test_2_1_refuses_what_would_not_come_back(msg: StructureMessage, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        serialize(msg, version="2.1")
