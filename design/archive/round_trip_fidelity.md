# Round-Trip Fidelity

## The Problem

"Round-trip fidelity" means: parse an SDMX-ML document into the domain model,
serialise it back to XML, and get the same information out. We do not need
byte-identical XML — element ordering and whitespace can differ — but every
artefact, every name, every constraint, every mapping must survive the cycle.

**We do not have this.** The current readers are _whitelists_: they only parse
element types they have domain classes for. Any XML element they do not
recognise is silently dropped — no error, no warning. When the writer runs
over the resulting `StructureMessage`, the dropped elements are simply gone.

This is not a theoretical concern. It affects every real SDMX 2.1 response we
have tested against. Parsing a live ECB dataflow response and re-serialising
it produces XML that is missing at minimum three entire top-level sections that
were present in the original.

---

## Evidence from Real Data

The cassettes and ECB fixture files (`ecb_dataflow.xml`, `ecb_data_structure.xml`)
cover responses from the ECB SDMX REST API and the SDMX Global Registry. The
following sections appear in those responses but are **silently dropped**:

### OrganisationSchemes — dropped in every rich response

Present in: `ecb_data_structure.xml`, `ecb_dataflow.xml`, and every integration
cassette that returns a DSD or Dataflow (`test_registry_data_structure.yaml`,
`test_registry_dataflow.yaml`, `test_ecb_dsd_with_resolution.yaml`,
`test_ecb_code_map.yaml`, and more).

Sample from `ecb_dataflow.xml`:

```xml
<str:OrganisationSchemes>
  <str:AgencyScheme agencyID="SDMX" id="AGENCIES" version="1.0" isFinal="false">
    <com:Name xml:lang="en">SDMX Agency Scheme</com:Name>
    <str:Agency id="SDMX">
      <com:Name xml:lang="en">SDMX</com:Name>
    </str:Agency>
    <str:Agency id="ECB">
      <com:Name xml:lang="en">European Central Bank</com:Name>
    </str:Agency>
    <str:Agency id="BIS">
      <com:Name xml:lang="en">Bank for International Settlements</com:Name>
    </str:Agency>
    <!-- ... more agencies ... -->
  </str:AgencyScheme>
</str:OrganisationSchemes>
```

The reader has no `_read_organisation_schemes()` call. The entire block —
including the full names of every agency referenced in the message — is
discarded on read and never written back.

### Constraints — dropped in dataflow responses

Present in: `ecb_dataflow.xml` and `test_registry_dataflow.yaml`.

This is arguably the highest-value loss. Constraints declare which dimension
value combinations are valid for a dataflow — enabling catalog tools to tell
users "there is no data for currency USD at frequency A" without making a
live data query.

Sample from `ecb_dataflow.xml`:

```xml
<str:Constraints>
  <str:ContentConstraint agencyID="ECB" id="EXR_CONSTRAINTS" version="1.0"
      type="Allowed" isFinal="false">
    <com:Name xml:lang="en">Constraints for the EXR dataflow.</com:Name>
    <str:ConstraintAttachment>
      <str:Dataflow>
        <Ref package="datastructure" agencyID="ECB" id="EXR" version="1.0"
             class="Dataflow"/>
      </str:Dataflow>
    </str:ConstraintAttachment>
    <str:CubeRegion include="true">
      <com:KeyValue id="FREQ">
        <com:Value>A</com:Value>
        <com:Value>D</com:Value>
        <com:Value>M</com:Value>
        <com:Value>Q</com:Value>
        <com:Value>H</com:Value>
      </com:KeyValue>
      <com:KeyValue id="CURRENCY">
        <com:Value>ZAR</com:Value>
        <com:Value>USD</com:Value>
        <!-- 60+ currency codes -->
      </com:KeyValue>
      <!-- ... more dimensions ... -->
    </str:CubeRegion>
  </str:ContentConstraint>
</str:Constraints>
```

The reader has no `_read_constraints()` call. Constraints are gone.

### StructureSets — dropped in codelist responses

Present in: `test_registry_codelist.yaml` and `test_registry_resolve_urn.yaml`.

StructureSets contain `CodelistMap` entries — code-level mappings from one
codelist to another (e.g. CL_CURRENCY → CL_AREA_EE, mapping currency codes to
country codes).

Sample from `test_registry_codelist.yaml`:

```xml
<str:StructureSets>
  <str:StructureSet agencyID="ECB" id="GLOBAL_MAPPINGS" version="1.0">
    <com:Name xml:lang="en">Generic mappings</com:Name>
    <str:CodelistMap id="MAP_CL_CURRENCY2AREA">
      <com:Name xml:lang="en">CL_CURRENCY to CL_AREA_EE map</com:Name>
      <str:Source>
        <Ref package="codelist" agencyID="ECB" id="CL_CURRENCY" version="1.0"
             class="Codelist"/>
      </str:Source>
      <str:Target>
        <Ref package="codelist" agencyID="ECB" id="CL_AREA_EE" version="1.0"
             class="Codelist"/>
      </str:Target>
      <str:CodeMap>
        <str:Source><Ref id="ATS"/></str:Source>
        <str:Target><Ref id="AT"/></str:Target>
      </str:CodeMap>
      <!-- hundreds of individual code mappings -->
    </str:CodelistMap>
  </str:StructureSet>
</str:StructureSets>
```

---

## What "Fidelity" Means for Each Gap

Not every missing element requires deep domain modelling. The principle from
the ergonomics audit applies: only model what is a genuine domain concept.
XML ceremony belongs in readers and writers, not in the domain model.

| Element type                       | Domain concept?                         | Drop consequence                                                            |
| ---------------------------------- | --------------------------------------- | --------------------------------------------------------------------------- |
| AgencyScheme / Agency names        | Yes — agencies have names, not just ids | Agency names irretrievable; `maintainer.name` forever empty                 |
| ContentConstraint + CubeRegion     | Yes — which data exists                 | Constraints silently lost; catalog tools cannot answer availability queries |
| StructureSet + CodelistMap         | Yes — code equivalence across domains   | Mapping tables lost; ETL code cannot use registry-provided mappings         |
| `isExternalReference` on artefacts | No — reader artefact                    | Informational flag; safe to discard                                         |
| `urn` attribute on artefacts       | Derived — reconstruct from fields       | Always re-derivable; not a loss                                             |

---

## Plan

Each section below describes the full stack of changes needed: domain model,
reader (2.1 and 3.0), writer (2.1 and 3.0), `StructureMessage`, and test
fixtures.

---

### Change 1: Agency names via AgencyScheme

**What changes**

Add `AgencyScheme` to the domain model as a maintainable artefact containing
`Agency` items. Extend `Agency` to carry `name`, `description`, and `contact`
(the latter SDMX 3.0 only). Add `Contact` as a value class.

The `maintainer: Agency` change from Problem 7 of `ergonomics.md` is the
companion to this: `MaintainableArtefact` currently stores only `agency_id: str`.
These two changes must land together.

**Domain model** (`model/organisation.py`):

```python
@dataclass(frozen=True, slots=True)
class Contact:
    name: InternationalString = field(default_factory=InternationalString)
    department: InternationalString = field(default_factory=InternationalString)
    role: InternationalString = field(default_factory=InternationalString)
    phones: tuple[str, ...] = ()
    uris: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class Agency:
    id: str
    name: InternationalString = field(default_factory=InternationalString)
    description: InternationalString = field(default_factory=InternationalString)
    contacts: tuple[Contact, ...] = ()
    parent: "Agency | None" = None      # from ergonomics.md Problem 7
    annotations: Annotations = field(default_factory=Annotations)

@dataclass(frozen=True, slots=True)
class DataProvider:
    id: str
    name: InternationalString = field(default_factory=InternationalString)
    description: InternationalString = field(default_factory=InternationalString)
    contacts: tuple[Contact, ...] = ()
    annotations: Annotations = field(default_factory=Annotations)

@dataclass(frozen=True, slots=True)
class AgencyScheme:
    """Maintainable container for Agency items."""
    sdmx_package: ClassVar[str] = "base"
    sdmx_class: ClassVar[str] = "AgencyScheme"
    id: str
    agency_id: str
    version: str = "1.0"
    name: InternationalString = field(default_factory=InternationalString)
    description: InternationalString = field(default_factory=InternationalString)
    is_final: bool = False
    agencies: ItemList[Agency] = field(default_factory=ItemList)
    annotations: Annotations = field(default_factory=Annotations)

@dataclass(frozen=True, slots=True)
class DataProviderScheme:
    """Maintainable container for DataProvider items."""
    sdmx_package: ClassVar[str] = "base"
    sdmx_class: ClassVar[str] = "DataProviderScheme"
    id: str
    agency_id: str
    version: str = "1.0"
    name: InternationalString = field(default_factory=InternationalString)
    is_final: bool = False
    providers: ItemList[DataProvider] = field(default_factory=ItemList)
    annotations: Annotations = field(default_factory=Annotations)
```

**`StructureMessage`** — add two new fields:

```python
agency_schemes: ArtefactList[AgencyScheme] = field(default_factory=ArtefactList)
data_provider_schemes: ArtefactList[DataProviderScheme] = field(default_factory=ArtefactList)
```

**Reader (2.1)** — add `_read_organisation_schemes(structures, index)` that
finds `str:OrganisationSchemes`, iterates `str:AgencyScheme` and
`str:DataProviderScheme`, reads agencies/providers as items with
`_read_istring`, and indexes each `Agency` so that `ProvisionAgreement`
`data_provider` refs can resolve:

```python
def _read_organisation_schemes(
    structures: etree._Element, index: dict[str, Any]
) -> tuple[list[AgencyScheme], list[DataProviderScheme]]:
    container = structures.find(_s("OrganisationSchemes"))
    if container is None:
        return [], []
    agency_schemes: list[AgencyScheme] = []
    provider_schemes: list[DataProviderScheme] = []
    for as_el in container.findall(_s("AgencyScheme")):
        agencies = [
            Agency(
                id=a.get("id", ""),
                name=_read_istring(a),
                description=_read_istring(a, "Description"),
                annotations=_read_annotations(a),
            )
            for a in as_el.findall(_s("Agency"))
        ]
        scheme = AgencyScheme(
            id=as_el.get("id", ""),
            agency_id=as_el.get("agencyID", ""),
            version=as_el.get("version", "1.0"),
            name=_read_istring(as_el),
            is_final=as_el.get("isFinal", "false").lower() == "true",
            agencies=ItemList(agencies),
        )
        agency_schemes.append(scheme)
        _index_artefact(index, scheme, AgencyScheme)
        for agency in agencies:
            _index_item(index, agency, scheme, "base", "Agency")
    # same pattern for DataProviderScheme...
    return agency_schemes, provider_schemes
```

Call this in Phase 1 of `read()` (before index construction), since agencies
are leaf artefacts that nothing references.

**Reader (3.0)** — same, plus parse `str:Contact` children on each `Agency`:

```python
def _read_contact(c_el: etree._Element) -> Contact:
    return Contact(
        name=_read_istring(c_el),
        department=_read_istring(c_el, "Department"),
        role=_read_istring(c_el, "Role"),
        phones=tuple(el.text for el in c_el.findall(_s("Telephone")) if el.text),
        uris=tuple(el.text for el in c_el.findall(_s("URI")) if el.text),
        emails=tuple(el.text for el in c_el.findall(_s("Email")) if el.text),
    )
```

**Writer (2.1)** — add `_write_organisation_schemes(msg, structures)` that
emits `<str:OrganisationSchemes>` with all `AgencyScheme` items in
`msg.agency_schemes`. If `msg.agency_schemes` is empty but the message
references agencies (via `maintainer` once Problem 7 lands), auto-collect
unique `Agency` objects from all artefacts and emit a synthetic scheme.

**Writer (3.0)** — same plus emit `<str:Contact>` children for each agency.

**Test fixtures** — add `<str:OrganisationSchemes>` block to `minimal.xml`
(2.1 and 3.0) with at least one `AgencyScheme` containing the `TEST` agency.
Add round-trip assertions for `msg.agency_schemes.ids()` and
`msg.agency_schemes["TEST:AGENCIES"].agencies["TEST"].name["en"]`.

---

### Change 2: ContentConstraint + CubeRegion

**What changes**

Add `ContentConstraint`, `ConstraintAttachment`, and `CubeRegion` to the
domain model. Add reader and writer support. Expose constraints on `Dataflow`
and `DataStructure` via a convenience property.

**Domain model** (`model/constraint.py`, new file):

```python
@dataclass(frozen=True, slots=True)
class KeyValue:
    """Allowed (or excluded) values for one dimension in a CubeRegion."""
    dimension_id: str
    values: frozenset[str]      # empty = all values permitted

@dataclass(frozen=True, slots=True)
class CubeRegion:
    """
    Selects a region of the observation space.

    include=True  → only these key combinations exist.
    include=False → these key combinations are excluded.
    """
    include: bool = True
    keys: tuple[KeyValue, ...] = ()

    def allows(self, key: dict[str, str]) -> bool:
        for kv in self.keys:
            vals = kv.values
            if vals and key.get(kv.dimension_id) not in vals:
                return not self.include
        return self.include

@dataclass(frozen=True, slots=True)
class ConstraintAttachment:
    """What this constraint is attached to (dataflow, DSD, or provision)."""
    dataflow: Ref[Any] | None = None
    data_structure: Ref[Any] | None = None
    provision_agreement: Ref[Any] | None = None

@dataclass(frozen=True, slots=True)
class ContentConstraint:
    sdmx_package: ClassVar[str] = "registry"
    sdmx_class: ClassVar[str] = "ContentConstraint"
    id: str
    agency_id: str
    version: str = "1.0"
    name: InternationalString = field(default_factory=InternationalString)
    description: InternationalString = field(default_factory=InternationalString)
    constraint_type: str = "Allowed"    # "Allowed" or "Actual"
    is_final: bool = False
    attachment: ConstraintAttachment = field(default_factory=ConstraintAttachment)
    regions: tuple[CubeRegion, ...] = ()
    annotations: Annotations = field(default_factory=Annotations)

    def allows(self, key: dict[str, str]) -> bool:
        """Return False if any CubeRegion excludes this key."""
        for region in self.regions:
            if not region.allows(key):
                return False
        return True
```

**`StructureMessage`** — add:

```python
constraints: ArtefactList[ContentConstraint] = field(default_factory=ArtefactList)
```

**Reader (2.1)** — add `_read_constraints(structures, index)`:

```python
def _read_constraints(
    structures: etree._Element, index: dict[str, Any]
) -> list[ContentConstraint]:
    container = structures.find(_s("Constraints"))
    if container is None:
        return []
    result: list[ContentConstraint] = []
    for cc_el in container.findall(_s("ContentConstraint")):
        attachment = ConstraintAttachment()
        ca_el = cc_el.find(_s("ConstraintAttachment"))
        if ca_el is not None:
            df_el = ca_el.find(_s("Dataflow"))
            ds_el = ca_el.find(_s("DataStructure"))
            pa_el = ca_el.find(_s("ProvisionAgreement"))
            attachment = ConstraintAttachment(
                dataflow=_resolve_ref(df_el, index) if df_el is not None else None,
                data_structure=_resolve_ref(ds_el, index) if ds_el is not None else None,
                provision_agreement=_resolve_ref(pa_el, index) if pa_el is not None else None,
            )

        regions: list[CubeRegion] = []
        for cr_el in cc_el.findall(_s("CubeRegion")):
            include = cr_el.get("include", "true").lower() == "true"
            keys: list[KeyValue] = []
            for kv_el in cr_el.findall(_c("KeyValue")):
                dim_id = kv_el.get("id", "")
                values = frozenset(
                    v_el.text for v_el in kv_el.findall(_c("Value"))
                    if v_el.text
                )
                keys.append(KeyValue(dimension_id=dim_id, values=values))
            regions.append(CubeRegion(include=include, keys=tuple(keys)))

        result.append(ContentConstraint(
            id=cc_el.get("id", ""),
            agency_id=cc_el.get("agencyID", ""),
            version=cc_el.get("version", "1.0"),
            name=_read_istring(cc_el),
            description=_read_istring(cc_el, "Description"),
            constraint_type=cc_el.get("type", "Allowed"),
            is_final=cc_el.get("isFinal", "false").lower() == "true",
            attachment=attachment,
            regions=tuple(regions),
            annotations=_read_annotations(cc_el),
        ))
    return result
```

Call this in Phase 4 of `read()` (after DSDs and Dataflows are indexed, so
attachment refs can resolve).

**Reader (3.0)** — identical logic; namespace tags differ (`str:DataConstraint`
replaces `str:ContentConstraint` in SDMX 3.0 terminology, but the XML shape
is the same for our purposes).

**Writer (2.1)** — add `_write_constraints(msg, structures)`:

```python
def _write_constraints(msg: StructureMessage, structures: etree._Element) -> None:
    if not msg.constraints:
        return
    container = etree.SubElement(structures, _s("Constraints"))
    for cc in msg.constraints:
        cc_el = etree.SubElement(container, _s("ContentConstraint"),
            agencyID=cc.agency_id, id=cc.id, version=cc.version,
            type=cc.constraint_type,
            isFinal=str(cc.is_final).lower())
        _write_name(cc_el, cc.name)
        _write_description(cc_el, cc.description)

        ca_el = etree.SubElement(cc_el, _s("ConstraintAttachment"))
        if cc.attachment.dataflow:
            df_el = etree.SubElement(ca_el, _s("Dataflow"))
            etree.SubElement(df_el, "Ref",
                package="datastructure",
                agencyID=cc.attachment.dataflow.agency,
                id=cc.attachment.dataflow.id,
                version=cc.attachment.dataflow.version,
                **{"class": "Dataflow"})
        # same for data_structure, provision_agreement

        for region in cc.regions:
            cr_el = etree.SubElement(cc_el, _s("CubeRegion"),
                include=str(region.include).lower())
            for kv in region.keys:
                kv_el = etree.SubElement(cr_el, _c("KeyValue"), id=kv.dimension_id)
                for val in sorted(kv.values):
                    v_el = etree.SubElement(kv_el, _c("Value"))
                    v_el.text = val
```

**Test fixtures** — add a `ContentConstraint` to `minimal.xml` with one
`CubeRegion` restricting `FREQ` to `["A", "Q"]`. Add round-trip assertions
for constraint ids, attachment, and allowed-key logic.

**Integration test** — add a new cassette test that reads the real ECB EXR
dataflow response (`ecb_dataflow.xml`) and asserts:

- `msg.constraints` is non-empty
- The EXR constraint allows `{"FREQ": "A"}` and denies `{"FREQ": "X"}`

---

### Change 3: StructureSet + CodelistMap

**What changes**

Add `StructureSet`, `CodelistMap`, and `CodeMap` to the domain model. These
are the registry's codelist-to-codelist mapping tables.

**Domain model** (`model/mapping.py`, new file):

```python
@dataclass(frozen=True, slots=True)
class CodeMap:
    """Single code-level mapping: source code id → target code id."""
    source_id: str
    target_id: str

@dataclass(frozen=True, slots=True)
class CodelistMap:
    """Maps one codelist onto another, code by code."""
    id: str
    name: InternationalString = field(default_factory=InternationalString)
    source: Ref[Codelist] | None = None
    target: Ref[Codelist] | None = None
    maps: tuple[CodeMap, ...] = ()

    def translate(self, source_code: str) -> str | None:
        """Return the target code for a source code, or None if unmapped."""
        for m in self.maps:
            if m.source_id == source_code:
                return m.target_id
        return None

@dataclass(frozen=True, slots=True)
class StructureSet:
    sdmx_package: ClassVar[str] = "mapping"
    sdmx_class: ClassVar[str] = "StructureSet"
    id: str
    agency_id: str
    version: str = "1.0"
    name: InternationalString = field(default_factory=InternationalString)
    description: InternationalString = field(default_factory=InternationalString)
    is_final: bool = False
    codelist_maps: tuple[CodelistMap, ...] = ()
    annotations: Annotations = field(default_factory=Annotations)
```

**`StructureMessage`** — add:

```python
structure_sets: ArtefactList[StructureSet] = field(default_factory=ArtefactList)
```

**Reader (2.1)** — add `_read_structure_sets(structures, index)`:

```python
def _read_structure_sets(
    structures: etree._Element, index: dict[str, Any]
) -> list[StructureSet]:
    container = structures.find(_s("StructureSets"))
    if container is None:
        return []
    result: list[StructureSet] = []
    for ss_el in container.findall(_s("StructureSet")):
        maps: list[CodelistMap] = []
        for cm_el in ss_el.findall(_s("CodelistMap")):
            source_ref = _resolve_ref(cm_el.find(_s("Source")), index)
            target_ref = _resolve_ref(cm_el.find(_s("Target")), index)
            code_maps: list[CodeMap] = []
            for m_el in cm_el.findall(_s("CodeMap")):
                src = m_el.find(_s("Source"))
                tgt = m_el.find(_s("Target"))
                src_ref = src.find("Ref") if src is not None else None
                tgt_ref = tgt.find("Ref") if tgt is not None else None
                if src_ref is not None and tgt_ref is not None:
                    code_maps.append(CodeMap(
                        source_id=src_ref.get("id", ""),
                        target_id=tgt_ref.get("id", ""),
                    ))
            maps.append(CodelistMap(
                id=cm_el.get("id", ""),
                name=_read_istring(cm_el),
                source=source_ref,
                target=target_ref,
                maps=tuple(code_maps),
            ))
        result.append(StructureSet(
            id=ss_el.get("id", ""),
            agency_id=ss_el.get("agencyID", ""),
            version=ss_el.get("version", "1.0"),
            name=_read_istring(ss_el),
            description=_read_istring(ss_el, "Description"),
            is_final=ss_el.get("isFinal", "false").lower() == "true",
            codelist_maps=tuple(maps),
            annotations=_read_annotations(ss_el),
        ))
    return result
```

Call this in Phase 4 of `read()` (after codelists are indexed so `CodelistMap`
source/target refs can resolve).

**Reader (3.0)** — identical; SDMX 3.0 uses the same XML structure for
StructureSets.

**Writer (2.1)** — add `_write_structure_sets(msg, structures)` that emits
`<str:StructureSets>` with each `StructureSet`, its `CodelistMap` children,
and for each map its `CodeMap` children with `<str:Source>` and
`<str:Target>` `Ref` elements.

**Test fixtures** — add a small `StructureSet` to `minimal.xml` with one
`CodelistMap` mapping `CL_FREQ` codes to themselves (trivial identity mapping
is enough to exercise the round-trip). Add assertions for
`msg.structure_sets.ids()` and `msg.structure_sets[...].codelist_maps[0].translate("A") == "A"`.

---

## Testing Strategy

The current round-trip tests only assert that specific fields match between the
original and re-parsed message. They cannot detect dropped sections because
they never ask "does the re-parsed message contain everything the original did?"

### New fixture: complete_2.1.xml

Create `tests/fixtures/sdmx_ml21/complete.xml` — a hand-crafted fixture that
contains one instance of every element type we model:

- `OrganisationSchemes` with an `AgencyScheme`
- `Codelists` with at least one hierarchical codelist
- `Concepts` with a `ConceptScheme`
- `DataStructures` with a DSD (dimensions, attributes, measures, groups)
- `Dataflows`
- `CategorySchemes` and `Categorisations`
- `ProvisionAgreements`
- `Constraints` with a `ContentConstraint`
- `StructureSets` with a `CodelistMap`

This is the canonical fidelity fixture. Every round-trip test that asserts
"the message is preserved" runs against this fixture.

### New fixture: complete_3.0.xml

Same for SDMX 3.0, with additional SDMX 3.0 elements:

- `str:Contact` on Agency items
- Multiple `str:Measure` elements in `MeasureList`
- `str:ObservationValue` in attribute relationships

### New test file: `tests/unit/test_roundtrip_complete.py`

```python
pytestmark = pytest.mark.unit

_MSG_21 = parse((_FIXTURES / "complete.xml").read_bytes())
_MSG_30 = parse((_FIXTURES_30 / "complete.xml").read_bytes())


@pytest.mark.parametrize("msg", [_MSG_21, _MSG_30], ids=["2.1", "3.0"])
def test_agency_schemes_survive(msg):
    rt = parse(serialize(msg))
    assert rt.agency_schemes.ids() == msg.agency_schemes.ids()
    for o, r in zip(msg.agency_schemes, rt.agency_schemes, strict=True):
        assert r.agencies.ids() == o.agencies.ids()
        for oa, ra in zip(o.agencies, r.agencies, strict=True):
            assert ra.name.get("en") == oa.name.get("en")


@pytest.mark.parametrize("msg", [_MSG_21, _MSG_30], ids=["2.1", "3.0"])
def test_constraints_survive(msg):
    rt = parse(serialize(msg))
    assert rt.constraints.ids() == msg.constraints.ids()
    for o, r in zip(msg.constraints, rt.constraints, strict=True):
        assert r.constraint_type == o.constraint_type
        assert len(r.regions) == len(o.regions)
        for or_, rr in zip(o.regions, r.regions, strict=True):
            assert rr.include == or_.include
            orig_keys = {kv.dimension_id: kv.values for kv in or_.keys}
            rt_keys   = {kv.dimension_id: kv.values for kv in rr.keys}
            assert rt_keys == orig_keys


@pytest.mark.parametrize("msg", [_MSG_21, _MSG_30], ids=["2.1", "3.0"])
def test_structure_sets_survive(msg):
    rt = parse(serialize(msg))
    assert rt.structure_sets.ids() == msg.structure_sets.ids()
    for o, r in zip(msg.structure_sets, rt.structure_sets, strict=True):
        o_maps = {m.id: m for m in o.codelist_maps}
        r_maps = {m.id: m for m in r.codelist_maps}
        assert set(o_maps) == set(r_maps)
        for map_id in o_maps:
            o_codes = {m.source_id: m.target_id for m in o_maps[map_id].maps}
            r_codes = {m.source_id: m.target_id for m in r_maps[map_id].maps}
            assert r_codes == o_codes
```

### Integration-level smoke test

Add a test that reads the full `ecb_dataflow.xml` fixture (which is a real ECB
response) and asserts that after one round-trip all top-level section counts
match:

```python
def test_ecb_dataflow_roundtrip_section_counts():
    orig = parse(Path("tests/fixtures/sdmx_ml21/ecb_dataflow.xml").read_bytes())
    rt   = parse(serialize(orig))
    assert len(rt.agency_schemes)    == len(orig.agency_schemes)
    assert len(rt.codelists)         == len(orig.codelists)
    assert len(rt.concept_schemes)   == len(orig.concept_schemes)
    assert len(rt.data_structures)   == len(orig.data_structures)
    assert len(rt.dataflows)         == len(orig.dataflows)
    assert len(rt.categorisations)   == len(orig.categorisations)
    assert len(rt.constraints)       == len(orig.constraints)
```

This test will currently fail (constraints and agency schemes are dropped) and
serves as the acceptance criterion for this work.

---

## Prioritised Delivery

| #   | Change                                  | New model files            | Existing files touched                   | Fixtures to add/update        | Effort |
| --- | --------------------------------------- | -------------------------- | ---------------------------------------- | ----------------------------- | ------ |
| 1   | AgencyScheme + Contact                  | `organisation.py` (extend) | both readers, both writers, `message.py` | `minimal.xml` (2.1 + 3.0)     | Medium |
| 2   | ContentConstraint + CubeRegion          | `constraint.py` (new)      | both readers, both writers, `message.py` | `minimal.xml`, `complete.xml` | Medium |
| 3   | StructureSet + CodelistMap              | `mapping.py` (new)         | both readers, both writers, `message.py` | `minimal.xml`, `complete.xml` | Medium |
| 4   | complete.xml fixtures + roundtrip tests | —                          | `test_roundtrip_complete.py` (new)       | `complete.xml` (2.1 + 3.0)    | Small  |

Changes 1–3 are independent and can proceed in parallel. Change 4 is the
acceptance gate and requires 1–3.

The companion ergonomics change (Problem 7: `maintainer: Agency`) should land
with Change 1 — the two are logically coupled. Storing `agency_id: str` while
also having a populated `AgencyScheme` in the same message creates two
parallel representations of the same information.

---

## Out of Scope for This Work

The following are genuine gaps (documented in `ergonomics.md`) but not
required for round-trip fidelity against real SDMX 2.1 responses, because
they do not appear in our current cassettes or fixtures:

- `HierarchicalCodelist` (separate from `Code.parent_id` — not present in ECB data)
- `GeoCodelist` / `GeoGridCodelist` (SDMX 3.0 only, not present in cassettes)
- `ValueList` (SDMX 3.0 only)
- `MetadataStructureDefinition` (separate endpoint, not included in structure responses)
- `ReportingTaxonomy` (not present in cassettes)

If a future cassette record reveals any of these in practice, add them then.
