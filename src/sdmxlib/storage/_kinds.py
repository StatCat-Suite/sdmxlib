"""Single source of truth for the artefact-kind registry.

Each entry pairs a Python class with the storage ``artefact_type``
column value. The class-to-name and name-to-class dicts are derived
from one list so the two halves can't drift.

The remaining function-dispatch maps (class → writer in writers.py,
name → reader in readers.py) live next to the functions they
dispatch to — they can't be hoisted here without a circular import.
But they no longer carry their own class/name entries; they look the
key up from this module.
"""

from attrs import frozen

from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.category import Categorisation, CategoryScheme
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.concept import ConceptScheme
from sdmxlib.model.constraint import DataConstraint
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.hierarchy import HierarchicalCodelist, Hierarchy, HierarchyAssociation
from sdmxlib.model.mapping import (
    CategorySchemeMap,
    ConceptSchemeMap,
    OrganisationSchemeMap,
    RepresentationMap,
    StructureMap,
    StructureSet,
)
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.organisation import (
    AgencyScheme,
    DataConsumerScheme,
    DataProviderScheme,
    MetadataProviderScheme,
    OrganisationUnitScheme,
)
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.valuelist import ValueList


@frozen
class ArtefactKind:
    """Pair of (Python class, storage type_name) for one artefact kind."""

    cls: type
    name: str


ARTEFACT_KINDS: tuple[ArtefactKind, ...] = (
    ArtefactKind(Codelist, "Codelist"),
    ArtefactKind(ValueList, "ValueList"),
    ArtefactKind(ConceptScheme, "ConceptScheme"),
    ArtefactKind(DataStructure, "DataStructure"),
    ArtefactKind(Dataflow, "Dataflow"),
    ArtefactKind(Hierarchy, "Hierarchy"),
    ArtefactKind(HierarchyAssociation, "HierarchyAssociation"),
    ArtefactKind(DataConstraint, "DataConstraint"),
    ArtefactKind(CategoryScheme, "CategoryScheme"),
    ArtefactKind(Categorisation, "Categorisation"),
    ArtefactKind(ProvisionAgreement, "ProvisionAgreement"),
    ArtefactKind(AgencyScheme, "AgencyScheme"),
    ArtefactKind(DataProviderScheme, "DataProviderScheme"),
    ArtefactKind(MetadataProviderScheme, "MetadataProviderScheme"),
    ArtefactKind(DataConsumerScheme, "DataConsumerScheme"),
    ArtefactKind(OrganisationUnitScheme, "OrganisationUnitScheme"),
    ArtefactKind(RepresentationMap, "RepresentationMap"),
    ArtefactKind(ConceptSchemeMap, "ConceptSchemeMap"),
    ArtefactKind(CategorySchemeMap, "CategorySchemeMap"),
    ArtefactKind(OrganisationSchemeMap, "OrganisationSchemeMap"),
    ArtefactKind(StructureMap, "StructureMap"),
    ArtefactKind(MetadataStructure, "MetadataStructure"),
    ArtefactKind(Metadataflow, "Metadataflow"),
    ArtefactKind(MetadataProvisionAgreement, "MetadataProvisionAgreement"),
    ArtefactKind(MetadataSet, "MetadataSet"),
    ArtefactKind(DataflowBinding, "DataflowBinding"),
    # The two SDMX 2.1 grouping containers. They have no table: each is a
    # maintainable plus a list of refs, which `artefact` and `artefact_ref`
    # already hold — see `writers.put_container` (#395).
    ArtefactKind(HierarchicalCodelist, "HierarchicalCodelist"),
    ArtefactKind(StructureSet, "StructureSet"),
)

CLASS_TO_NAME: dict[type, str] = {k.cls: k.name for k in ARTEFACT_KINDS}
NAME_TO_CLASS: dict[str, type] = {k.name: k.cls for k in ARTEFACT_KINDS}

#: Every value the `artefact.artefact_type` column can hold, derived from the
#: registry above so the query language cannot disagree with the writer.
#:
#: **Closed, not merely current.** These are SDMX Information Model class
#: names, and the IM is a fixed specification — a value outside this set is a
#: bug or a corrupt row, not a variant to accommodate. That is what licenses
#: `q.artefact.type` to *refuse* an unknown literal rather than pass it through
#: to an empty result. Nor can a store carry a stale one: the schema is dropped
#: and rebuilt whenever `SCHEMA_VERSION` moves
#: (`LocalRegistry._initialise_schema`), so a store never outlives the build
#: that wrote it.
#:
#: The one way this can wrongly refuse is a writer landing without an entry
#: here — `test_query_vocabularies` compares the two against
#: `writers._WRITER_DISPATCH` in both directions.
#:
#: `DataflowBinding` is the single non-IM member: sdmxlib's own artefact,
#: stored on the same spine.
ARTEFACT_TYPES: frozenset[str] = frozenset(NAME_TO_CLASS)

#: Every value `dsd_component.component_type` can hold. Listed rather than
#: derived because the writer builds them from the DSD's *fields* — there is no
#: registry to read — but listed **here**, beside the artefact kinds, so the one
#: place to look for "what may this column contain" is the same for both.
#: `storage.writers._put_data_structure` is the producer; a test holds them
#: together.
COMPONENT_TYPES: frozenset[str] = frozenset({"Dimension", "TimeDimension", "MeasureDimension", "Attribute", "Measure"})
