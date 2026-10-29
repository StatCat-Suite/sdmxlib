# Construction Ergonomics

A ruthless comparison of building SDMX objects in sdmxlib vs
[sdmx1](https://github.com/khaeru/sdmx). Goal: identify real friction,
not theoretical purity violations.

---

## Reference: What sdmx1 does

sdmx1 uses **mutable dataclasses + direct object references**. No `Ref`
wrapper. No URNs at construction time. You build objects and wire them directly:

```python
# sdmx1 — build a DSD
dsd = DataStructureDefinition(id="MY_DSD", maintainer=Agency(id="EXAMPLE"))
for i, dim_id in enumerate(["FREQ", "REF_AREA"]):
    dsd.dimensions.append(Dimension(id=dim_id, order=i))

cl = Codelist(id="CL_FREQ", maintainer=Agency(id="SDMX"))
cl.items["A"] = Code(id="A")
dim.local_representation = Representation(enumerated=cl)
```

Simple. Incremental. No ceremony.

---

## Problem 1: `position` doesn't belong in the domain model

`position` is not a property of a `Dimension` — it is a workaround for XML's
lack of array types. SDMX-ML writes `<Dimension position="3" .../>` because
XML elements have no inherent order guarantee, so the spec encodes sequence
explicitly as an attribute.

The domain model already has ordering: `ItemList[Dimension]` is a sequence,
and the position of a `Dimension` is simply its index. Storing `position` on
the object is redundant and forces users to manually count:

```python
# Current — counting is the user's problem
sl.Dimension(id="FREQ",       position=1, ...),
sl.Dimension(id="REF_AREA",   position=2, ...),
sl.Dimension(id="TRADE_FLOW", position=3, ...),
sl.Dimension(id="COMMODITY",  position=4, ...),
```

**Fix**: Remove `position` from `Dimension` entirely. The XML writers derive
it from the list index at serialization time:

```python
for i, dim in enumerate(dsd.dimensions):
    elem.set("position", str(i + 1))
```

The XML readers still parse the `position=` attribute — useful for detecting
out-of-order elements from external sources — but store dimensions in `ItemList`
order, validating against list index rather than storing the value on the object.

```python
# After fix — no counting, no field
sl.Dimension(id="FREQ",       codelist=cl_freq, ...),
sl.Dimension(id="REF_AREA",   codelist=cl_area, ...),
sl.Dimension(id="TRADE_FLOW", codelist=cl_flow, ...),
sl.Dimension(id="COMMODITY",  codelist=cl_comm, ...),
```

_Files_: `model/datastructure.py` (remove field), `formats/sdmx_ml21/writer.py`,
`formats/sdmx_ml30/writer.py` (derive from index), `formats/sdmx_ml21/reader.py`,
`formats/sdmx_ml30/reader.py` (validate on read, don't store)

---

## Problem 2: No convenience method for concept item-level URNs

For **codelists**, `Codelist.urn()` already exists and is clean — keyword
args, validated, close in length to the raw string:

```python
sl.Codelist.urn(agency="SDMX", id="CL_FREQ", version="2.0")
# → urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ(2.0)
```

No problem there. The real pain is **concept refs**. A concept is an _item
inside a scheme_, so its URN carries both a scheme id and an item id:

```
urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=SDMX:CROSS_DOMAIN_CONCEPTS(1.0).FREQ
```

`MaintainableArtefact.urn()` only covers scheme-level URNs (no `item_id`).
There is no convenience method for item-level URNs, so you fall through to
`SdmxUrn.make()`, which forces the user to supply SDMX internals they should
never need to know:

```python
# Current — user must know package= and artefact_class=
sl.Ref.unresolved(
    sl.SdmxUrn.make(
        sl.Concept,
        package="conceptscheme",     # ← SDMX internal
        artefact_class="Concept",    # ← SDMX internal
        agency="SDMX",
        id="CROSS_DOMAIN_CONCEPTS",
        item_id="FREQ",
    )
)
```

**Fix**: Add `Concept.urn(agency=, scheme=, id=)` as a classmethod that hides
`package` and `artefact_class` and maps the caller's intuitive `scheme` /
`id` params to the URN's `id` / `item_id` fields:

```python
# Target
sl.Ref.unresolved(
    sl.Concept.urn(agency="SDMX", scheme="CROSS_DOMAIN_CONCEPTS", id="FREQ")
)
```

The URN string form is not simpler — it's the same length and still requires
knowing the `conceptscheme.Concept` package path. The fix is a better
classmethod, not string coercion.

_File_: `model/concept.py`

---

## Problem 3: `Ref[T].id` lies for concept references

`cs.ref_to("FREQ")` returns a `Ref[Concept]` where:

- `.id` → `"CS_TRADE"` — the **scheme** id (not what anyone expects)
- `.urn.item_id` → `"FREQ"` — the actual concept id

This is technically correct per the SDMX URN structure, but it surprises every
user. The building tutorial has to explicitly warn about it. When you pass
`concept=cs.ref_to("FREQ")` to a `Dimension`, `dim.concept.id` returns
`"CS_TRADE"`.

**Fix**: Add an `item_id: str | None` property on `Ref[T]` as a direct alias
for `urn.item_id`:

```python
freq_ref = cs.ref_to("FREQ")
freq_ref.item_id   # "FREQ" ← obvious
freq_ref.id        # "CS_TRADE" ← still works, unchanged
```

_File_: `model/ref.py`

---

## Problem 4: ConceptScheme is obligatory ceremony for a docs-only feature

Every tutorial and real-world workflow requires building a `ConceptScheme` with
N concepts before the DSD. The `concept` field on `Dimension` is optional
(`None`), but without it `column_labels()` falls back to dimension ids. This
tradeoff is not prominently documented — users who follow the tutorial feel
obligated to define a `ConceptScheme` even when they only care about
serialisation.

This is a **documentation problem, not a code problem**.

**Fix**: Add a callout to `building_tutorial.md` Section 2 stating that
concepts are optional. Add a minimal-DSD example (no `ConceptScheme`) to the
quick-reference so users don't think they're missing something.

_File_: `docs/building_tutorial.md`

---

## Problem 5: `with_added_*` naming is alien Python

sdmx1 users build incrementally with `dsd.dimensions.append(dim)`.
sdmxlib has `with_added_dimension()`, which reads like a Java fluent-builder.
List construction at construction time is already well-supported (good), but
post-construction modification is awkward:

```python
# Current
dsd = dsd.with_added_dimension(new_dim)             # alien
cl  = replace(cl_freq, codes=[*cl_freq.codes, code]) # ugly spread
```

**Fix**: Add an `append(item)` method to `DataStructure` and `Codelist` that
returns a new immutable copy. On `DataStructure`, dispatch by type so callers
don't repeat the component type:

```python
# Target
dsd = dsd.append(sl.Dimension(id="PARTNER", ...))
cl  = cl_freq.append(sl.Code(id="W", name={"en": "Weekly"}))
```

Remove the existing `with_added_dimension`, `with_dimension`,
`with_added_attribute`, `with_attribute` methods — they are superseded by
`append()` (adding) and `dataclasses.replace()` (bulk replacement). Keeping
both creates a confusing dual API.

_Files_: `model/datastructure.py`, `model/codelist.py`

---

## Problem 6: Type annotations don't match accepted constructor input

Field annotations say `codelist: Ref[Codelist] | None` but `__post_init__`
silently accepts a bare `Codelist`. Type checkers flag user code as errors
and readers of the source are misled.

**Fix**: Widen annotations to the actual accepted input union:

```python
codelist: Ref[Codelist] | Codelist | None = None
```

This makes type-checked user code work without casts and documents the
coercion explicitly.

_Files_: `model/datastructure.py`, `model/dataflow.py`

---

## Problem 7: `agency_id: str` loses agency identity — `AgencyScheme` is a serialisation artifact

`MaintainableArtefact` stores `agency_id: str`. An `Agency` class exists in
`model/organisation.py` but is never linked to any artefact — it is an orphan.
This means:

- Agency names and descriptions are unreachable from any artefact.
- The nested agency hierarchy (`OECD.SDD.NAD` = NAD unit within SDD within
  OECD) is invisible — the dots in the id string carry structure that the model
  cannot navigate.
- `sdmx1.Agency(id=, name=, parent=...)` is a full navigable object; ours is a
  dead class.

**The XML trap to avoid**: In SDMX-ML, agencies are packaged in an
`<AgencyScheme>` element because XML has no way to reference objects across
documents without first defining them. This makes `AgencyScheme` look like a
first-class domain concept — but it is the same kind of serialisation artifact
as `position` on `Dimension`. Users should never have to construct an
`AgencyScheme` explicitly.

**Fix**: Treat `Agency` as a plain navigable object in the domain model:

```python
@dataclass(frozen=True, slots=True)
class Agency:
    id: str
    name: InternationalString = ...
    description: InternationalString = ...
    parent: Agency | None = None          # hierarchy: OECD → SDD → NAD
```

Replace `agency_id: str` on `MaintainableArtefact` with
`maintainer: Agency`. No `Ref[Agency]` needed — agencies are always
constructed locally, never lazy-resolved from a remote endpoint the way
codelists are.

```python
# Target
agency = sl.Agency(id="OECD.SDD.NAD", name={"en": "National Accounts Division"})
dsd = sl.DataStructure(id="DSD_NA", maintainer=agency, ...)
print(dsd.maintainer.name["en"])   # "National Accounts Division"
print(dsd.maintainer.parent)       # Agency(id="OECD.SDD") if hierarchy modelled
```

`AgencyScheme` becomes a writer-level concern only: the XML writers collect
all unique `Agency` objects referenced in a `StructureMessage` and emit the
`<AgencyScheme>` block automatically. Users never see it.

For backwards compatibility, keep `agency_id` as a read-only property that
returns `maintainer.id`.

_Files_: `model/organisation.py`, `model/base.py`, all model files that
declare `agency_id` fields, `formats/sdmx_ml21/writer.py`,
`formats/sdmx_ml30/writer.py` (auto-emit AgencyScheme)

---

## Non-Problems

- **`Ref[T].__bool__` always `True`** — correct. `None` means no reference
  exists; `Ref[T]` means a reference exists (possibly unresolved). Use
  `.resolved` to check resolution status.
- **Frozen immutability** — a feature. The `with_*` / `append()` helpers make
  common modifications ergonomic; `dataclasses.replace()` handles the rest.
- **`__post_init__` coercion** — standard Python idiom, well-precedented.
- **URNs in general** — required by the standard. The problem is not URNs per
  se but the verbosity of constructing them without helpers.

---

## SDMX 3.0 Domain Model Gaps

The same design principle used above — _remove XML ceremony; use Python's rich
data model_ — also applies when assessing coverage of the SDMX 3.0 standard.
Each gap below is classified as either a **genuine domain concept** (must be
modelled) or an **XML artifact** (belongs only in readers/writers).

---

### Gap 1: `Representation` / `Enumeration` split is internal duplication

Every `Dimension`, `Attribute`, and `Measure` carries two fields for the same
information:

```python
codelist: Ref[Codelist] | None        # for navigation (dim.codelist())
representation: Representation         # = Enumeration(urn=...) | TextFormat | None
```

`Enumeration` exists solely because SDMX-ML's `<LocalRepresentation>` element
requires a separate `<Enumeration>` child that holds the codelist URN. In the
domain model this is pure redundancy — `Ref[Codelist]` already carries the URN
and the resolved object.

**Fix**: Collapse the split. Remove `Enumeration`. Redefine:

```python
type Representation = Ref[Codelist] | TextFormat | None
```

The `representation` field on each component then _is_ the codelist reference
for enumerated components and a `TextFormat` for free-text components.
`is_enumerated` becomes `isinstance(self.representation, Ref)`. The XML writers
synthesise the `<Enumeration urn="..."/>` child from `representation.urn`.

The separate `codelist` convenience field can stay as a property alias:

```python
@property
def codelist(self) -> Ref[Codelist] | None:
    return self.representation if isinstance(self.representation, Ref) else None
```

_Files_: `model/representation.py` (remove `Enumeration`), `model/datastructure.py`
(collapse fields), `formats/sdmx_ml21/writer.py`, `formats/sdmx_ml30/writer.py`
(synthesise `<Enumeration>` from `Ref.urn`)

---

### Gap 2: Codelist extensions (SDMX 3.0)

SDMX 3.0 introduced codelist _extensions_: a codelist can formally extend
another, inheriting its codes and adding new ones. This is a genuine domain
concept — not an XML-only notion — because it changes what codes are valid
for a given dimension at runtime.

Current model: no support. A codelist has only its own `codes: ItemList[Code]`.

**Fix**: Add an `extends: Ref[Codelist] | None` field to `Codelist`:

```python
@dataclass(frozen=True, slots=True)
class Codelist:
    ...
    extends: Ref[Codelist] | None = None
```

Add an `effective_codes` property that walks the extension chain:

```python
@property
def effective_codes(self) -> ItemList[Code]:
    if self.extends and self.extends.resolved:
        return ItemList([*self.extends().effective_codes, *self.codes])
    return self.codes
```

`Codelist.codes` stays as the _own_ codes; `effective_codes` is the full
inherited set. Validation warns when a dimension's enumeration includes a code
not in `effective_codes`.

_Files_: `model/codelist.py`

---

### Gap 3: Hierarchies and cross-codelist parent references

Current state: `Code.parent_id: str | None` supports single-level parent
references _within the same codelist_. This covers tree-shaped codelists (e.g.
CL*AREA where continents parent countries). It does not cover SDMX's
`Hierarchy` and `HierarchicalCodelist` — structures that define alternative
hierarchies \_across* codelists.

**Classification**: `HierarchicalCodelist` (SDMX 2.1) and its 3.0 successor
`Hierarchy` + `HierarchyAssociation` are genuine domain concepts. They encode
information (e.g. "country A belongs to region B for reporting purpose X") that
cannot be derived from the codelists themselves.

`HierarchyAssociation` in SDMX-ML wraps the `Hierarchy` in a linked-objects
envelope — that wrapper is the XML artifact; the `Hierarchy` itself is not.

**Fix**: Add three new classes:

```python
@dataclass(frozen=True, slots=True)
class HierarchyLevel:
    """Named level in a hierarchy (e.g. continent → country → city)."""
    id: str
    name: InternationalString = ...
    child_level: HierarchyLevel | None = None    # recursive nesting

@dataclass(frozen=True, slots=True)
class HierarchicalCode:
    """A code's placement in a hierarchy (may reference a foreign codelist)."""
    id: str                                       # local node id
    code: Ref[Code]                               # points into any codelist
    level: str | None = None                      # HierarchyLevel id
    valid_from: date | None = None
    valid_to: date | None = None
    children: ItemList[HierarchicalCode] = ...    # recursive

@dataclass(frozen=True, slots=True)
class Hierarchy:
    id: str
    agency_id: str
    version: str = "1.0"
    name: InternationalString = ...
    has_formal_levels: bool = False
    levels: ItemList[HierarchyLevel] = ...
    codes: ItemList[HierarchicalCode] = ...
```

`HierarchyAssociation` (linking a `Hierarchy` to a `DataStructure` or
`Dataflow`) is a separate maintainable artefact — include it in
`StructureMessage` when present. Users never construct the XML envelope; the
writer derives it.

_Files_: new `model/hierarchy.py`, `model/message.py` (add to `StructureMessage`)

---

### Gap 4: Geographic codelists (SDMX 3.0)

SDMX 3.0 introduced `GeoCodelist` and `GeoGridCodelist` — codelists whose
codes carry geographic values (WKT geometries for polygons, point grids, etc.).
These are needed by NSAs publishing regional statistics.

`GeoCodelist` is not an XML artifact — the geographic values are genuine data.

**Fix**: Subtype `Code` with a `GeoCode` and subtype `Codelist` with `GeoCodelist`:

```python
@dataclass(frozen=True, slots=True)
class GeoCode:
    id: str
    name: InternationalString = ...
    value: str | None = None          # WKT geometry string
    parent_id: str | None = None
    annotations: Annotations = ...

@dataclass(frozen=True, slots=True)
class GeoCodelist:
    """Codelist whose codes carry geographic geometry values."""
    id: str
    agency_id: str
    version: str = "1.0"
    name: InternationalString = ...
    geo_type: str | None = None       # e.g. "MultiPolygon"
    codes: ItemList[GeoCode] = ...
    extends: Ref[GeoCodelist] | None = None
```

`GeoGridCodelist` (for regular geographic grids) follows the same pattern with
additional `start`, `end`, and `step` fields.

_Files_: `model/codelist.py` (add subtypes), `model/message.py`

---

### Gap 5: Value lists (SDMX 3.0)

`ValueList` is a new SDMX 3.0 artefact for non-enumerated fixed lists — similar
to a codelist but without code ids. Items are plain strings or numbers
(e.g. a list of valid ISO currency codes that are not maintained as an SDMX
codelist). A `ValueList` can be the representation of a `Measure` or `Attribute`.

**Fix**: Add `ValueList` and `EnumeratedValue`:

```python
@dataclass(frozen=True, slots=True)
class EnumeratedValue:
    id: str                           # the value itself (e.g. "USD")
    name: InternationalString = ...
    description: InternationalString = ...

@dataclass(frozen=True, slots=True)
class ValueList:
    id: str
    agency_id: str
    version: str = "1.0"
    name: InternationalString = ...
    values: ItemList[EnumeratedValue] = ...
```

Extend `Representation` to accept a `ValueList` reference:

```python
type Representation = Ref[Codelist] | Ref[ValueList] | TextFormat | None
```

_Files_: new `model/valuelist.py`, `model/representation.py`, `model/message.py`

---

### Gap 6: Constraint framework

Constraints are the most valuable missing feature for NSA users. A
`DataConstraint` declares which combinations of dimension values actually exist
in a dataflow — enabling data catalog tools to tell users "there is no data for
country X in frequency M" without making a real data query.

Current state: no constraint classes at all.

**Classification**: Constraints are genuine domain concepts. The XML
representation (`<CubeRegion>`, `<KeySet>`) is verbose but the underlying
concept — _a set of valid key combinations_ — maps cleanly to Python sets and
dicts.

**Fix**: Add two constraint classes:

```python
@dataclass(frozen=True, slots=True)
class CubeRegion:
    """
    Selects a hyper-cube of valid observations.
    Each entry: dimension_id → frozenset of valid codes (empty = all).
    """
    included: bool = True
    keys: dict[str, frozenset[str]] = field(default_factory=dict)

    def allows(self, key: dict[str, str]) -> bool:
        for dim_id, codes in self.keys.items():
            if codes and key.get(dim_id) not in codes:
                return not self.included
        return self.included

@dataclass(frozen=True, slots=True)
class DataConstraint:
    id: str
    agency_id: str
    version: str = "1.0"
    name: InternationalString = ...
    data_structure: Ref[DataStructure] | None = None
    dataflow: Ref[Dataflow] | None = None
    regions: list[CubeRegion] = field(default_factory=list)

    def allows(self, key: dict[str, str]) -> bool:
        """True if no region excludes the key (or some region includes it)."""
        ...
```

The XML readers parse `<CubeRegion>` / `<KeySet>` and populate `regions`. The
writers emit the appropriate XML from `regions`. Users construct constraints
with plain dicts — no XML ceremony.

`MetadataConstraint` follows the same pattern but applies to metadata rather
than observation data.

_Files_: new `model/constraint.py`, `model/message.py`

---

### Gap 7: Contact information on organisations

`Agency` and `DataProvider` have `id`, `name`, `description`, and
`annotations` — but no contact details. The SDMX standard defines a `Contact`
structure with name, role, phone, fax, email, and URI.

This is a genuine domain concept. NSA users who build data catalogs surface
contact information for data providers.

**Fix**: Add `Contact` and wire it into organisations:

```python
@dataclass(frozen=True, slots=True)
class Contact:
    name: InternationalString = ...
    role: InternationalString = ...
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    uris: list[str] = field(default_factory=list)

@dataclass(frozen=True, slots=True)
class Agency:
    id: str
    name: InternationalString = ...
    description: InternationalString = ...
    contacts: list[Contact] = field(default_factory=list)
    parent: Agency | None = None      # hierarchy (see Problem 7 above)
    annotations: Annotations = ...
```

_Files_: new `model/contact.py`, `model/organisation.py`

---

### Gap 8: Temporal validity on versionable artefacts

SDMX allows any `VersionableArtefact` to carry `valid_from` / `valid_to` dates,
signalling which time periods a given version covers. Codelists in particular
may retire codes over time — `valid_to` on a `Code` records when a code
stopped being valid.

Current state: no date fields anywhere in the model.

**Fix**: Add optional date fields to the artefacts where they matter most:

```python
# On Code
valid_from: date | None = None
valid_to: date | None = None

# On Codelist (and other MaintainableArtefacts)
valid_from: date | None = None
valid_to: date | None = None
```

These are plain `datetime.date` values — no XML ceremony. The readers parse
ISO 8601 date strings; the writers emit them. Validation can warn when a
dimension references a codelist whose `valid_to` has passed.

_Files_: `model/codelist.py`, `model/concept.py`, `model/datastructure.py`

---

### Gap 9: Concept parent references

`Code` has `parent_id: str | None` for hierarchy within a codelist.
`Concept` has no equivalent. The SDMX standard allows concepts to extend or
specialise other concepts (e.g. a national concept `GDP_CURRENT` extends the
cross-domain concept `VALUE`).

**Fix**: Add `parent_id: str | None` to `Concept`, mirroring `Code`:

```python
@dataclass(frozen=True, slots=True)
class Concept:
    id: str
    name: InternationalString = ...
    description: InternationalString = ...
    parent_id: str | None = None          # concept within same scheme
    core_representation: Representation = None
    annotations: Annotations = ...
```

_File_: `model/concept.py`

---

### Gap 10: Metadata structures (out of scope for v1)

`MetadataStructureDefinition` (MSD) and `MetadataflowDefinition` define the
shape of reference metadata — footnotes, quality indicators, and methodology
documents attached to data. They are a parallel universe to DSDs and Dataflows.

MSDs are genuine domain concepts but complex and rarely needed for ETL
pipelines. Treat them as out of scope until there is concrete user demand. Add
stubs to `StructureMessage` so that metadata returned by an endpoint does not
cause parse failures, but do not expose them through the `Registry` API.

---

## Non-Problems

- **`Ref[T].__bool__` always `True`** — correct. `None` means no reference
  exists; `Ref[T]` means a reference exists (possibly unresolved). Use
  `.resolved` to check resolution status.
- **Frozen immutability** — a feature. The `with_*` / `append()` helpers make
  common modifications ergonomic; `dataclasses.replace()` handles the rest.
- **`__post_init__` coercion** — standard Python idiom, well-precedented.
- **URNs in general** — required by the standard. The problem is not URNs per
  se but the verbosity of constructing them without helpers.

---

## Prioritised Change Summary

### Ergonomics fixes

| #   | Problem                                                                            | Effort | Impact |
| --- | ---------------------------------------------------------------------------------- | ------ | ------ |
| 1   | Remove `position` from `Dimension` — derive from list index in writers             | Small  | High   |
| 2   | Add `Concept.urn(agency=, scheme=, id=)` classmethod                               | Tiny   | High   |
| 3   | `Ref.item_id` property                                                             | Tiny   | Medium |
| 4   | Docs: concepts are optional, add minimal-DSD example                               | Tiny   | Medium |
| 5   | Replace `with_added_*` with `append()` on DataStructure + Codelist                 | Small  | Medium |
| 6   | Widen type annotations to match accepted input types                               | Small  | Low    |
| 7   | `maintainer: Agency` replacing `agency_id: str`; auto-emit AgencyScheme in writers | Medium | High   |

### SDMX 3.0 coverage gaps

| Gap | Problem                                                              | Effort | Impact |
| --- | -------------------------------------------------------------------- | ------ | ------ |
| 1   | Collapse `Enumeration`/`codelist` split; unify into `Representation` | Small  | High   |
| 2   | Codelist extensions (`extends: Ref[Codelist] \| None`)               | Small  | Medium |
| 3   | `Hierarchy` + `HierarchicalCode` + `HierarchyAssociation`            | Medium | High   |
| 4   | `GeoCodelist` + `GeoCode` (geographic codelists)                     | Small  | Medium |
| 5   | `ValueList` + `EnumeratedValue`                                      | Small  | Medium |
| 6   | `DataConstraint` + `CubeRegion` (constraint framework)               | Medium | High   |
| 7   | `Contact` on `Agency` / `DataProvider`                               | Tiny   | Medium |
| 8   | `valid_from` / `valid_to` on codes and artefacts                     | Small  | Medium |
| 9   | `Concept.parent_id`                                                  | Tiny   | Low    |
| 10  | `MetadataStructureDefinition` stubs                                  | Medium | Low    |
