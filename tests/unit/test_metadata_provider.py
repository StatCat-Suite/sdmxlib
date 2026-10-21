"""`MetadataProvider` reaches the end of every leg — #315.

SDMX 3.0 has `MetadataProvider` and `MetadataProviderScheme`, and this library
modelled neither. The symptom was one column:
`metadata_provision_agreement.metadata_provider_urn` was `TEXT NOT NULL`,
populated on every write, and resolvable by nothing — a foreign key naming a
relation that did not exist. Its *data* twin was complete, which is why the gap
read as an accessor someone had forgotten rather than a missing relation.

**One test through the whole chain, not one per leg.** The issue asks for this
explicitly, citing #307: a per-leg test passes throughout such a bug's life,
because each leg is individually fine. Model → format → storage → query is
where the break lives, and only a test that walks all four sees it.

The query assertion is the one that matters most. An `Edge` declared against
the spine compiles, runs, and matches **zero rows** — a provider's urn lives in
`sdmx.metadata_provider`, never in `sdmx.artefact`. That is exactly how the
`data_provider` edge was wrong until #306 exposed the hop, and an empty result
is not an error.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from lxml import etree

import sdmxlib as sl
from sdmxlib.formats import serialize
from sdmxlib.formats.sdmx_json import reader as json_reader
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_ml30 import reader as r30
from sdmxlib.formats.sdmx_ml30 import writer as w30
from sdmxlib.formats.sdmx_ml31 import reader as r31
from sdmxlib.formats.sdmx_ml31 import writer as w31
from sdmxlib.model.collections import ArtefactList
from sdmxlib.query.accessors import q
from tests.support.xsd import SchemaVersion, compile_structure_schema

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.unit


def _scheme() -> sl.MetadataProviderScheme:
    return sl.MetadataProviderScheme(
        id="METADATA_PROVIDERS",
        maintainer="AG",
        version="1.0",
        name={"en": "providers"},
        providers=[
            sl.MetadataProvider(
                id="MP1",
                name={"en": "provider one"},
                # Contacts come along because both are `Organisation` in the IM
                # — and #309 made `organisation_contact` polymorphic on
                # `owner_urn`, so there was already somewhere to put them.
                contacts=(sl.Contact(name={"en": "desk"}, emails=("desk@example.org",)),),
            )
        ],
    )


def _message() -> sl.StructureMessage:
    scheme = _scheme()
    flow = sl.Metadataflow(
        id="MDF",
        maintainer="AG",
        version="1.0",
        name={"en": "flow"},
        structure=sl.Ref.unresolved(sl.MetadataStructure.urn_for(agency="AG", id="MSD", version="1.0")),
        # Required by the 3.0 XSD; nothing to do with #315, but a Metadataflow
        # without one does not validate and the failure would read as ours.
        targets=(str(sl.Dataflow.urn_for(agency="AG", id="DF", version="1.0")),),
    )
    mpa = sl.MetadataProvisionAgreement(
        id="MPA",
        maintainer="AG",
        version="1.0",
        name={"en": "agreement"},
        metadataflow=sl.Ref.unresolved(flow.urn),
        metadata_provider=sl.Ref.unresolved(sl.MetadataProvider.urn_for(agency="AG", id="MP1", version="1.0")),
    )
    return sl.StructureMessage(
        metadata_provider_schemes=ArtefactList([scheme]),
        metadataflows=ArtefactList([flow]),
        metadata_provision_agreements=ArtefactList([mpa]),
    )


_ML = [
    pytest.param(w30.write, r30.read, "3.0", id="3.0"),
    pytest.param(w31.write, r31.read, "3.1", id="3.1"),
]


@pytest.mark.parametrize(("write", "read", "version"), _ML)
def test_sdmx_ml_3x_round_trips_the_scheme(
    write: Callable[[sl.StructureMessage], bytes],
    read: Callable[[bytes], sl.StructureMessage],
    version: SchemaVersion,
) -> None:
    written = write(_message())
    schema = compile_structure_schema(version)
    assert schema.validate(etree.fromstring(written)), str(schema.error_log)
    scheme = read(written).metadata_provider_schemes[0]
    assert scheme.providers.ids() == ["MP1"]
    assert scheme.providers["MP1"].contacts[0].emails == ("desk@example.org",)


def test_sdmx_json_round_trips_the_scheme() -> None:
    raw = json_writer.write(_message())
    assert "metadataProviderSchemes" in json.loads(raw)["data"]
    scheme = json_reader.read(raw).metadata_provider_schemes[0]
    assert scheme.providers.ids() == ["MP1"]
    assert scheme.providers["MP1"].contacts[0].emails == ("desk@example.org",)


def test_sdmx_ml_21_writes_it_as_its_agencys_data_provider_scheme() -> None:
    """2.1 has no `<str:MetadataProviderSchemes>` at all.

    It was refused for that until #421, which writes the scheme the way 2.1
    provisioned metadata: as the agency's `DATA_PROVIDERS` scheme, marked so
    the reader turns it back (`formats/sdmx_ml21/_providers.py`).
    """
    written = serialize(_message(), version="2.1")
    assert b"<str:MetadataProviderScheme" not in written
    assert b"<str:DataProviderScheme" in written
    back = sl.parse(written)
    assert isinstance(back, sl.StructureMessage)
    assert list(back.metadata_provider_schemes) == [_scheme()]
    assert list(back.data_provider_schemes) == []


def test_the_scheme_survives_storage_with_its_contacts() -> None:
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(_scheme())
        stored = reg.get(_scheme().urn)
    assert stored is not None
    assert stored.providers.ids() == ["MP1"]
    assert stored.providers["MP1"].contacts[0].emails == ("desk@example.org",)


def test_the_provision_agreement_reaches_its_provider() -> None:
    """The column that named a relation nothing had.

    Asserts the *value* at the far end, not merely that a row came back: an
    edge pointed at the wrong table yields an empty result, and an edge pointed
    at the right table with the wrong key yields the wrong provider. Both look
    like success to a test that only counts rows.
    """
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all(list(_message().all_artefacts()))
        rows = (
            reg.query(q.metadata_provision_agreement)
            .select(
                mpa=q.metadata_provision_agreement.id,
                provider=q.metadata_provision_agreement.metadata_provider.id,
            )
            .collect()
            .to_dicts()
        )
    assert rows == [{"mpa": "MPA", "provider": "MP1"}]


def test_the_provider_reaches_its_scheme() -> None:
    """And back the other way, so the edge is not one-directional by accident."""
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(_scheme())
        rows = (
            reg.query(q.metadata_provider)
            .select(provider=q.metadata_provider.id, scheme=q.metadata_provider.scheme.id)
            .collect()
            .to_dicts()
        )
    assert rows == [{"provider": "MP1", "scheme": "METADATA_PROVIDERS"}]


def test_urn_for_builds_the_item_urn_the_storage_key_uses() -> None:
    """The helper exists because concatenation is a silent trap.

    Appending ``.MP1`` to the *scheme's* urn yields a urn whose class is
    `MetadataProviderScheme`. It is well-formed, it stores fine, and the join
    then matches nothing — which is precisely the failure mode this issue is
    about, reintroduced one level down. `DataProvider` gained the same helper
    for the same reason.
    """
    built = str(sl.MetadataProvider.urn_for(agency="AG", id="MP1", version="1.0"))
    assert built == "urn:sdmx:org.sdmx.infomodel.base.MetadataProvider=AG:METADATA_PROVIDERS(1.0).MP1"

    naive = f"{_scheme().urn}.MP1"
    assert naive != built, "the trap and the helper must not agree"

    with sl.LocalRegistry(":memory:") as reg:
        reg.add(_scheme())
        stored = reg.sql("SELECT provider_urn FROM sdmx.metadata_provider").collect().to_dicts()
    assert stored == [{"provider_urn": built}]


def test_the_scheme_id_is_pinned_like_its_data_twin() -> None:
    """The XSD fixes it to `METADATA_PROVIDERS`, as it fixes `DATA_PROVIDERS`.

    Refused as it is built (#492), so no document the schema rejects is ever
    produced from one.
    """
    with pytest.raises(sl.ValidationError, match="METADATA_PROVIDERS"):
        sl.MetadataProviderScheme(id="MDPS", maintainer="AG", version="1.0", name={"en": "n"})
