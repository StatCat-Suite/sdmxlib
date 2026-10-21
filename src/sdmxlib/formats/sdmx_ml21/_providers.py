"""A 3.x metadata provider and its agreements, as SDMX-ML 2.1 writes them (#421).

2.1 has no metadata provider and no metadata provision agreement: it
provisioned reference metadata with a plain ``ProvisionAgreement`` whose
``StructureUsage`` is a metadataflow, naming a data provider. SDMX 3.0 split
both out. So 2.1 is written the way 2.1 itself did it:

- an agency's metadata providers go into its ``DATA_PROVIDERS`` scheme, the
  one id and version 2.1 allows a provider scheme. With no data providers of
  its own the scheme *is* the metadata provider scheme, marked
  `METADATA_PROVIDER_SCHEME_TYPE`; beside data providers each is marked
  `METADATA_PROVIDER_TYPE` and the scheme `METADATA_PROVIDERS_MERGED_TYPE`,
  whose text keeps the metadata scheme's name;
- a metadata provision agreement is a ``ProvisionAgreement`` over its
  metadataflow, naming its provider as the data provider of the same id.

The reader undoes both, so 3.x -> 2.1 -> 3.x returns its input; what else
the metadata scheme carries must agree with the data scheme it shares a 2.1
element with, which `validate` checks. Any 2.1 agreement over a metadataflow
is read as a metadata provision agreement, its provider as the metadata
provider of the same id.
"""

import attrs

from sdmxlib.formats.sdmx_ml21._synthetic import (
    METADATA_PROVIDER_SCHEME_TYPE,
    METADATA_PROVIDER_TYPE,
    METADATA_PROVIDERS_MERGED_TYPE,
)
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.collections import ArtefactList, ItemList
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.organisation import DataProvider, DataProviderScheme, MetadataProvider, MetadataProviderScheme
from sdmxlib.model.urn import SdmxUrn

#: The id 2.1 fixes for a data provider scheme, and 3.x for a metadata one.
_DATA_PROVIDERS = "DATA_PROVIDERS"
_METADATA_PROVIDERS = "METADATA_PROVIDERS"


def data_provider_urn(urn: SdmxUrn[object]) -> SdmxUrn[DataProvider]:
    """The 2.1 data provider a metadata provider is written as: same agency, version and id."""
    return DataProvider.urn_for(agency=urn.agency, id=urn.item_id or "", version=urn.version)


def metadata_provider_urn(urn: SdmxUrn[object]) -> SdmxUrn[MetadataProvider]:
    """The metadata provider a 2.1 agreement over a metadataflow names: the inverse of `data_provider_urn`."""
    return MetadataProvider.urn_for(agency=urn.agency, id=urn.item_id or "", version=urn.version)


def with_providers_merged(msg: StructureMessage) -> StructureMessage:
    """`msg` with each metadata provider scheme written into its agency's data provider scheme.

    The same object when there is none.
    """
    if not msg.metadata_provider_schemes:
        return msg
    schemes = list(msg.data_provider_schemes)
    position = {scheme.maintainer.id: n for n, scheme in enumerate(schemes)}
    for meta in msg.metadata_provider_schemes:
        n = position.get(meta.maintainer.id)
        if n is None:
            schemes.append(_as_data_scheme(meta))
        else:
            schemes[n] = _merged(schemes[n], meta)
    return attrs.evolve(msg, data_provider_schemes=ArtefactList(schemes), metadata_provider_schemes=ArtefactList([]))


def split_provider_schemes(
    schemes: list[DataProviderScheme],
) -> tuple[list[DataProviderScheme], list[MetadataProviderScheme]]:
    """Read 2.1 data provider schemes back into data and metadata ones, undoing `with_providers_merged`."""
    data: list[DataProviderScheme] = []
    meta: list[MetadataProviderScheme] = []
    for scheme in schemes:
        types = scheme.annotations.types
        if METADATA_PROVIDER_SCHEME_TYPE in types:
            meta.append(
                _as_metadata_scheme(
                    scheme,
                    name=scheme.name,
                    annotations=_without(scheme.annotations, METADATA_PROVIDER_SCHEME_TYPE),
                    providers=list(scheme.providers),
                )
            )
        elif (mark := scheme.annotations.first(METADATA_PROVIDERS_MERGED_TYPE)) is not None:
            annotations = _without(scheme.annotations, METADATA_PROVIDERS_MERGED_TYPE)
            merged = [p for p in scheme.providers if METADATA_PROVIDER_TYPE in p.annotations.types]
            own = [p for p in scheme.providers if METADATA_PROVIDER_TYPE not in p.annotations.types]
            data.append(attrs.evolve(scheme, providers=ItemList(own), annotations=annotations))
            meta.append(_as_metadata_scheme(scheme, name=mark.text, annotations=annotations, providers=merged))
        else:
            data.append(scheme)
    return data, meta


#: What a data and a metadata provider scheme have alike, bar their items,
#: id and annotations.
_SHARED_SCHEME_FIELDS = tuple(
    field.alias
    for field in attrs.fields(MetadataProviderScheme)
    if field.init and field.name not in {"id", "providers", "annotations"}
)

#: What a data and a metadata provider have alike, bar their annotations.
_SHARED_PROVIDER_FIELDS = tuple(
    field.alias for field in attrs.fields(MetadataProvider) if field.init and field.name != "annotations"
)


def _as_data_scheme(meta: MetadataProviderScheme) -> DataProviderScheme:
    return DataProviderScheme(
        **{name: getattr(meta, name) for name in _SHARED_SCHEME_FIELDS},
        id=_DATA_PROVIDERS,
        providers=ItemList([_as_data_provider(p, marked=False) for p in meta.providers]),
        annotations=Annotations([*meta.annotations, Annotation(type=METADATA_PROVIDER_SCHEME_TYPE)]),
    )


def _merged(data: DataProviderScheme, meta: MetadataProviderScheme) -> DataProviderScheme:
    return attrs.evolve(
        data,
        providers=ItemList([*data.providers, *(_as_data_provider(p, marked=True) for p in meta.providers)]),
        annotations=Annotations([*data.annotations, Annotation(type=METADATA_PROVIDERS_MERGED_TYPE, text=meta.name)]),
    )


def _as_data_provider(provider: MetadataProvider, *, marked: bool) -> DataProvider:
    mark = [Annotation(type=METADATA_PROVIDER_TYPE)] if marked else []
    return DataProvider(
        **{name: getattr(provider, name) for name in _SHARED_PROVIDER_FIELDS},
        annotations=Annotations([*provider.annotations, *mark]),
    )


def _as_metadata_scheme(
    scheme: DataProviderScheme, *, name: InternationalString, annotations: Annotations, providers: list[DataProvider]
) -> MetadataProviderScheme:
    shared = {field: getattr(scheme, field) for field in _SHARED_SCHEME_FIELDS if field != "name"}
    return MetadataProviderScheme(
        **shared,
        id=_METADATA_PROVIDERS,
        name=name,
        annotations=annotations,
        providers=ItemList([_as_metadata_provider(p) for p in providers]),
    )


def _as_metadata_provider(provider: DataProvider) -> MetadataProvider:
    return MetadataProvider(
        **{name: getattr(provider, name) for name in _SHARED_PROVIDER_FIELDS},
        annotations=_without(provider.annotations, METADATA_PROVIDER_TYPE),
    )


def _without(annotations: Annotations, annotation_type: str) -> Annotations:
    return Annotations([a for a in annotations if a.type != annotation_type])
