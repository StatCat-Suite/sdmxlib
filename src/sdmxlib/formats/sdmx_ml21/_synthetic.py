"""The mark sdmxlib puts on a StructureSet it invented rather than read.

SDMX-ML 2.1 has nowhere to put a map except inside a ``<str:StructureSet>``,
so writing a message whose maps belong to no declared set means inventing one.
SDMX 3.0 then refuses to carry the container back, because the grouping it
asserts has no 3.x form — and a container sdmxlib invented on the way down
would block the way back up, over a grouping nobody ever stated.

So the writer marks what it invents and the reader unmarks it: a set carrying
this annotation type is dropped on read, leaving its maps top-level, which is
where they were before the downgrade. A *declared* 2.1 structure set carries
no such mark and is still reified — losing that one would be losing a real
artefact (#239).

The mark is a plain SDMX annotation, so a document carrying it stays valid and
readable for every other consumer; they simply see an annotated structure set
and can ignore the annotation as they would any other (#242).
"""

#: ``<com:AnnotationType>`` identifying a wrapper sdmxlib created on write.
SYNTHETIC_STRUCTURE_SET_TYPE = "sdmxlib:synthetic-structure-set"

#: The id given to such a wrapper. Not the discriminator — the annotation is,
#: since an id is something a real document is free to reuse.
SYNTHETIC_STRUCTURE_SET_ID = "SS_SYNTHETIC"


#: ``<com:AnnotationType>`` marking a 2.1 reporting layer sdmxlib invented for
#: an MSD that has none (#421). SDMX 3.0 removed the layer — a MetadataTarget
#: and a ReportStructure — and 2.1 requires both, so a 3.x MSD is written with
#: one target of any object and one report of every attribute, both carrying
#: this mark, and the reader drops them again: 3.x -> 2.1 -> 3.x returns its
#: input, and any other 2.1 reader sees a valid MSD reporting on any object.
SYNTHETIC_REPORT_LAYER_TYPE = "sdmxlib:synthetic-report-layer"

#: The invented layer's ids. Not the discriminator — the annotation is.
SYNTHETIC_TARGET_ID = "SYNTHETIC_TARGET"
#: The invented target object's id, and its type: 2.1's
#: ``ObjectTypeCodelistType`` value for any object at all.
SYNTHETIC_TARGET_OBJECT = ("OBJECT", "Any")
SYNTHETIC_REPORT_ID = "SYNTHETIC_REPORT"


#: ``<com:AnnotationType>`` marking a 2.1 ``DataProviderScheme`` that is an
#: SDMX 3.x ``MetadataProviderScheme`` (#421). 2.1 has no metadata provider —
#: it provisioned metadata through a data provider — so an agency's metadata
#: providers are written as its ``DATA_PROVIDERS`` scheme, the only id 2.1
#: allows one, and the reader turns the scheme back.
METADATA_PROVIDER_SCHEME_TYPE = "sdmxlib:metadata-provider-scheme"

#: The mark on an agency's 2.1 ``DataProviderScheme`` when it holds data
#: *and* metadata providers: its text is the metadata provider scheme's name,
#: and each provider merged in carries `METADATA_PROVIDER_TYPE`. The reader
#: splits the two again (#421).
METADATA_PROVIDERS_MERGED_TYPE = "sdmxlib:metadata-providers-merged"

#: The mark on a metadata provider merged into a 2.1 data provider scheme.
METADATA_PROVIDER_TYPE = "sdmxlib:metadata-provider"
