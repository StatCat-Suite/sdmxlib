# sdmxlib — Construction, Coercion, Validation & Serialization Spec

How to build SDMX artefacts programmatically, validate them, and
serialize to/from multiple formats. Replaces the earlier construction spec.

---

## Table of Contents

1. [Construction Ergonomics](#1-construction-ergonomics)
2. [Coercion Rules](#2-coercion-rules)
3. [Construction Helpers](#3-construction-helpers)
4. [Construction Examples](#4-construction-examples)
5. [Validation](#5-validation)
6. [The Four Corners — Format Symmetry](#6-the-four-corners--format-symmetry)
7. [Dict Format](#7-dict-format)
8. [Writer Design](#8-writer-design)
9. [Modification Pattern](#9-modification-pattern)
10. [Building from Databases](#10-building-from-databases)
11. [Polars Construction Helpers](#11-polars-construction-helpers)
12. [Implementation Summary](#12-implementation-summary)

---

## 1. Construction Ergonomics

The design goal: constructing SDMX artefacts should feel as natural as
building Pydantic models. The stored types remain strict (frozen dataclasses
with `InternationalString`, `ItemList`, `Ref[T]`), but the constructor
accepts convenient Python types and coerces them.

### Before coercion

```python
sl.Codelist(
    id="CL_FREQ", maintainer="MY_AGENCY", version="1.0",
    name=sl.InternationalString(en="Frequency"),
    codes=sl.ItemList([
        sl.Code(id="A", name=sl.InternationalString(en="Annual")),
        sl.Code(id="Q", name=sl.InternationalString(en="Quarterly")),
    ]),
)
```

### After coercion

```python
sl.Codelist(
    id="CL_FREQ", maintainer="MY_AGENCY", version="1.0",
    name={"en": "Frequency"},
    codes=[
        sl.Code(id="A", name={"en": "Annual"}),
        sl.Code(id="Q", name={"en": "Quarterly"}),
    ],
)
```

Same stored types. Less ceremony.

---

## 2. Coercion Rules

All coercion happens in `__post_init__` on frozen dataclasses via
`object.__setattr__`. The stored type after construction is always the
strict type. Coercion is one-way: input → strict type.

### InternationalString fields

| Input                 | Stored as                     | Example                       |
| --------------------- | ----------------------------- | ----------------------------- |
| `InternationalString` | unchanged                     | `InternationalString(en="X")` |
| `dict[str, str]`      | `InternationalString(**dict)` | `{"en": "X", "fr": "Y"}`      |

No implicit default language. A bare `str` is NOT accepted — the user
must always specify the language key. This is explicit and correct for a
multilingual standard.

```python
# In __post_init__
if isinstance(self.name, dict):
    object.__setattr__(self, "name", InternationalString(**self.name))
```

### ItemList / ArtefactList fields

| Input                             | Stored as                               |
| --------------------------------- | --------------------------------------- |
| `ItemList[T]` / `ArtefactList[T]` | unchanged                               |
| `list[T]`                         | `ItemList(list)` / `ArtefactList(list)` |

```python
# In __post_init__
if isinstance(self.codes, list):
    object.__setattr__(self, "codes", ItemList(self.codes))
```

### Ref fields (maintainable artefacts only)

| Input                                  | Stored as          |
| -------------------------------------- | ------------------ |
| `Ref[T]`                               | unchanged          |
| `T` instance (where T is Maintainable) | `Ref.to(instance)` |

```python
# In __post_init__ — representation accepts Codelist directly
if isinstance(self.representation, Codelist):
    object.__setattr__(self, "representation", Ref.to(self.representation))
```

This coercion only works for maintainable artefacts (Codelist, DataStructure,
Dataflow, etc.) because they have `.urn()`. Item-level Refs (Concepts within
a ConceptScheme) cannot be auto-coerced — they require `cs.ref_to("FREQ")`
because the Concept doesn't know its parent scheme.

### Annotations field

| Input              | Stored as           |
| ------------------ | ------------------- |
| `Annotations`      | unchanged           |
| `list[Annotation]` | `Annotations(list)` |

### Coercion summary table

| Field type                 | Accepts            | Coerces to                    |
| -------------------------- | ------------------ | ----------------------------- |
| `InternationalString`      | `dict[str, str]`   | `InternationalString(**dict)` |
| `ItemList[T]`              | `list[T]`          | `ItemList(list)`              |
| `ArtefactList[T]`          | `list[T]`          | `ArtefactList(list)`          |
| `Ref[T]` (T: Maintainable) | `T` instance       | `Ref.to(instance)`            |
| `Annotations`              | `list[Annotation]` | `Annotations(list)`           |
| `maintainer`               | `str`              | `Agency(id=str)`              |

### Type annotation widening

Constructor signatures accept the union of strict and coercible types:

```python
@dataclass(frozen=True, slots=True)
class Codelist(MaintainableArtefact):
    id: str
    maintainer: Agency | str
    version: str = "1.0"
    name: InternationalString | dict[str, str] = field(default_factory=InternationalString)
    description: InternationalString | dict[str, str] = field(default_factory=InternationalString)
    codes: ItemList[Code] | list[Code] = field(default_factory=ItemList)
    annotations: Annotations | list[Annotation] = field(default_factory=Annotations)
```

After `__post_init__`, the stored type is always the strict type. Document
this in the class docstring.

---

## 3. Construction Helpers

### 3.1 `Ref.to(artefact)` — resolved Ref from a MaintainableArtefact

```python
class Ref[T]:
    @classmethod
    def to(cls, obj: T) -> Ref[T]:
        """Create a resolved Ref from an artefact that has .urn().

            codelist = Ref.to(cl_freq)
            structure = Ref.to(dsd)
        """
        urn = obj.urn()
        return cls(urn=urn, _resolved=obj)
```

With coercion, `Ref.to()` is rarely needed explicitly — passing the
bare artefact works for maintainable Ref fields. `Ref.to()` remains
available for explicit construction and non-field contexts.

### 3.2 `ConceptScheme.ref_to(concept_id)` — Ref to an item

```python
class ConceptScheme(MaintainableArtefact):
    def ref_to(self, concept_id: str) -> Ref[Concept]:
        """Build a resolved Ref to a concept within this scheme.

            concept = cs.ref_to("FREQ")

        Raises KeyError if concept_id is not in this scheme.
        """
        concept = self.concepts[concept_id]
        urn = SdmxUrn.make(
            Concept,
            package="conceptscheme",
            artefact_class="Concept",
            agency=self.agency_id,
            id=self.id,
            version=self.version,
            item_id=concept_id,
        )
        return Ref.of(urn, concept)
```

### 3.3 `Ref.of(urn, obj)` and `Ref.unresolved(urn)` — existing factories

These remain unchanged. `Ref.of` is used by the format reader and
`ConceptScheme.ref_to`. `Ref.unresolved` is used when referencing
external artefacts without fetching them.

---

## 4. Construction Examples

### 4.1 Build a Codelist

```python
import sdmxlib as sl

cl_freq = sl.Codelist(
    id="CL_FREQ", maintainer="MY_AGENCY", version="1.0",
    name={"en": "Frequency"},
    codes=[
        sl.Code(id="A", name={"en": "Annual"}),
        sl.Code(id="Q", name={"en": "Quarterly"}),
        sl.Code(id="M", name={"en": "Monthly"}),
    ],
)
```

### 4.2 Build a multilingual Codelist

```python
cl_freq = sl.Codelist(
    id="CL_FREQ", maintainer="MY_AGENCY", version="1.0",
    name={"en": "Frequency", "fr": "Fréquence"},
    codes=[
        sl.Code(id="A", name={"en": "Annual", "fr": "Annuel"}),
        sl.Code(id="Q", name={"en": "Quarterly", "fr": "Trimestriel"}),
        sl.Code(id="M", name={"en": "Monthly", "fr": "Mensuel"}),
    ],
)
```

### 4.3 Build a ConceptScheme

```python
cs = sl.ConceptScheme(
    id="CS_INCOME", maintainer="MY_AGENCY", version="1.0",
    name={"en": "Income concepts"},
    concepts=[
        sl.Concept(id="FREQ", name={"en": "Frequency"}),
        sl.Concept(id="REF_AREA", name={"en": "Reference Area"}),
        sl.Concept(id="TIME_PERIOD", name={"en": "Time Period"}),
        sl.Concept(id="OBS_VALUE", name={"en": "Observation Value"}),
        sl.Concept(id="OBS_STATUS", name={"en": "Observation Status"}),
    ],
)
```

### 4.4 Build a DataStructure

```python
dsd = sl.DataStructure(
    id="DSD_INCOME", maintainer="MY_AGENCY", version="1.0",
    name={"en": "Income Data Structure"},
    dimensions=[
        sl.Dimension(
            id="FREQ", position=1,
            representation=cl_freq,         # bare Codelist → coerced to Ref
            concept=cs.ref_to("FREQ"),      # item Ref — explicit
        ),
        sl.Dimension(
            id="REF_AREA", position=2,
            representation=cl_area,
            concept=cs.ref_to("REF_AREA"),
        ),
    ],
    time_dimension=sl.TimeDimension(
        concept=cs.ref_to("TIME_PERIOD"),
        representation=sl.TextFormat(text_type="ObservationalTimePeriod"),
    ),
    measures=[
        sl.Measure(id="OBS_VALUE", concept=cs.ref_to("OBS_VALUE")),
    ],
    attributes=[
        sl.Attribute(
            id="OBS_STATUS", usage="Conditional",
            attachment=sl.AttachmentLevel.OBSERVATION,
            representation=cl_obs_status,   # bare Codelist → coerced to Ref
            concept=cs.ref_to("OBS_STATUS"),
        ),
    ],
)
```

### 4.5 Build with unresolved Refs (external artefacts)

```python
# Reference cross-domain CL_FREQ without having the object
dim_freq = sl.Dimension(
    id="FREQ", position=1,
    representation=sl.Ref.unresolved(sl.Codelist.urn(agency="SDMX", id="CL_FREQ")),
)
# Serializes fine — writer only needs the URN
```

### 4.6 Build a Dataflow and ProvisionAgreement

```python
df = sl.Dataflow(
    id="DF_INCOME", maintainer="MY_AGENCY", version="1.0",
    name={"en": "Income Dataflow"},
    structure=dsd,                          # coerced to Ref
)

pa = sl.ProvisionAgreement(
    id="PA_INCOME", maintainer="MY_AGENCY", version="1.0",
    name={"en": "Income Provision"},
    dataflow=df,                            # coerced to Ref
)
```

### 4.7 Package into a StructureMessage

```python
msg = sl.StructureMessage(
    codelists=[cl_freq, cl_area, cl_obs_status],       # list coerced to ArtefactList
    concept_schemes=[cs],
    data_structures=[dsd],
    dataflows=[df],
    provision_agreements=[pa],
)
```

---

## 5. Validation

Validation is separate from construction. Domain objects can be created
in any state — validation is explicit, when you're ready.

### Why not validate at construction time

- Building iteratively (adding dimensions one at a time) means intermediate
  states would fail validation.
- Format readers may encounter non-compliant data from real registries —
  validation failures shouldn't prevent parsing.
- Testing benefits from constructing invalid objects deliberately.

### Per-artefact validation

```python
def validate(artefact: Any, *, strict: bool = False) -> list[ValidationIssue]:
    """Validate an artefact against SDMX structural rules.

    Checks:
      - id format ([A-Za-z0-9_-]+)
      - agency_id format ([A-Za-z0-9._-]+)
      - version format (semver-like)
      - Required fields present (id, agency_id for maintainables)
      - Code id uniqueness within codelists
      - Dimension position continuity (1, 2, 3, ...)
      - Ref URN format validity

    Returns a list of issues. If strict=True, raises on the first issue.

        issues = sl.validate(dsd)
        for issue in issues:
            print(f"{issue.path}: {issue.message}")
    """
```

### ValidationIssue

```python
@dataclass(frozen=True)
class ValidationIssue:
    path: str           # e.g. "dimensions[FREQ].codelist.urn"
    message: str        # human-readable description
    severity: str       # "error" | "warning"
```

### Cross-artefact reference validation

```python
def validate_references(msg: StructureMessage) -> list[ReferenceIssue]:
    """Check that all Refs within a message resolve to artefacts in that message.

    For each unresolved Ref, reports what it points to and what's missing.

        issues = sl.validate_references(msg)
        for issue in issues:
            print(f"{issue.source} → {issue.target_urn}: {issue.message}")
    """
```

### ReferenceIssue

```python
@dataclass(frozen=True)
class ReferenceIssue:
    source: str         # "DataStructure:MY_AGENCY:DSD_INCOME.dimensions[FREQ].codelist"
    target_urn: str     # "urn:sdmx:...Codelist=MY_AGENCY:CL_FREQ(1.0)"
    message: str        # "Referenced codelist not found in message"
```

### Usage

```python
# Build a DSD
dsd = sl.DataStructure(...)

# Validate the DSD itself
issues = sl.validate(dsd)
if issues:
    for issue in issues:
        print(f"  {issue.severity}: {issue.path} — {issue.message}")

# Package and validate cross-references
msg = sl.StructureMessage(data_structures=[dsd], codelists=[cl_freq, cl_area])
ref_issues = sl.validate_references(msg)
if ref_issues:
    for issue in ref_issues:
        print(f"  BROKEN: {issue.source} → {issue.target_urn}")

# Validate before serializing (recommended)
assert not sl.validate(dsd), "DSD has validation errors"
assert not sl.validate_references(msg), "Message has broken references"
xml_bytes = sl.to_xml(msg, version="2.1")
```

---

## 6. The Four Corners — Format Symmetry

```
                  bytes                dict
                  ─────                ────
  read:           parse()              from_dict()
  write:          to_xml() / to_json() to_dict()
```

All four live in `sdmxlib.formats` and are re-exported from `sdmxlib`:

```python
# sdmxlib/formats/__init__.py

def parse(data: bytes, *, version: str | None = None) -> StructureMessage:
    """Parse SDMX-ML or SDMX-JSON bytes. Auto-detects version."""

def to_xml(artefact: StructureMessage | Any, *, version: str = "2.1") -> bytes:
    """Serialize to SDMX-ML bytes."""

def to_json(artefact: StructureMessage | Any) -> bytes:
    """Serialize to SDMX-JSON bytes."""

def from_dict(cls: type[T], data: dict) -> T:
    """Construct a domain object from a nested dict."""

def to_dict(artefact: Any) -> dict:
    """Convert a domain object to a nested dict."""
```

### Round-trip guarantees

```python
# bytes round-trip
msg1 = sl.parse(xml_bytes)
msg2 = sl.parse(sl.to_xml(msg1, version="2.1"))
# msg1 and msg2 are semantically equal

# dict round-trip
d = sl.to_dict(dsd)
dsd2 = sl.from_dict(sl.DataStructure, d)
# dsd and dsd2 are semantically equal

# cross-format
dsd = sl.from_dict(sl.DataStructure, some_config_dict)
xml_bytes = sl.to_xml(dsd, version="2.1")
```

---

## 7. Dict Format

The dict format mirrors the domain model structure, not the XML structure.
It uses the same field names as the dataclass fields.

### Codelist

```python
{
    "id": "CL_FREQ",
    "agency_id": "MY_AGENCY",
    "version": "1.0",
    "name": {"en": "Frequency"},
    "codes": [
        {"id": "A", "name": {"en": "Annual"}},
        {"id": "Q", "name": {"en": "Quarterly"}},
    ]
}
```

### Dimension

```python
{
    "id": "FREQ",
    "position": 1,
    "concept": "urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=MY_AGENCY:CS_COMMON(1.0).FREQ",
    "representation": {"type": "Enumeration", "urn": "urn:sdmx:...Codelist=MY_AGENCY:CL_FREQ(1.0)"}
}
```

### Ref serialization in dicts

`representation` serializes as a typed dict. The `from_dict` reader
reconstructs the appropriate type:

```python
# to_dict: Ref[Codelist] → {"type": "Enumeration", "urn": "..."}
# to_dict: TextFormat → {"type": "TextFormat", "text_type": "..."}

# from_dict: dict → Ref.unresolved or TextFormat
```

Other Refs (concept, structure, dataflow) serialize as plain URN strings.

### `from_dict` implementation

```python
def from_dict[T](cls: type[T], data: dict) -> T:
    """Recursively construct a domain object from a nested dict.

    Handles:
      - dict → InternationalString for name/description fields
      - list[dict] → ItemList[T] with recursive conversion
      - URN string → Ref.unresolved(SdmxUrn.parse(...))
      - Nested dicts → recursive from_dict for known child types

    Usage:
        cl = sl.from_dict(sl.Codelist, {...})
        dsd = sl.from_dict(sl.DataStructure, {...})

    The resulting objects have unresolved Refs (URN only). To resolve
    them against a StructureMessage, use the parse() entry point or
    fetch via a Registry.
    """
```

### `to_dict` implementation

```python
def to_dict(artefact: Any) -> dict:
    """Convert a domain object to a nested dict.

    Handles:
      - InternationalString → dict[str, str]
      - ItemList/ArtefactList → list[dict]
      - Ref[T] → URN string
      - Nested domain objects → recursive to_dict
      - Enums → string value

    Usage:
        d = sl.to_dict(dsd)
        json.dumps(d, indent=2)   # human-readable JSON
    """
```

---

## 8. Writer Design

### 8.1 Principle: writers read `ref.urn`, never `ref()`

The writer serializes URN references, not resolved objects. This means:

- Unresolved Refs serialize fine (the URN is always present)
- The writer doesn't need referenced artefacts to be in memory
- Building a DSD referencing external codelists and serializing it
  works without fetching those codelists

### 8.2 XML writer — expanding collapsed wrappers

The reader collapsed thirteen XML wrappers. The writer expands them:

| Domain model                      | XML output                                             |
| --------------------------------- | ------------------------------------------------------ |
| `dsd.dimensions`                  | `<str:DataStructureComponents><str:DimensionList>`     |
| `dim.representation.urn`          | `<str:LocalRepresentation><str:Enumeration><Ref .../>` |
| `dim.concept.urn`                 | `<str:ConceptIdentity><Ref .../>`                      |
| `dim.representation` (TextFormat) | `<str:LocalRepresentation><str:TextFormat .../>`       |
| `attr.attachment`                 | `<str:AttributeRelationship>...`                       |
| `df.structure.urn`                | `<str:Structure><Ref .../>`                            |
| `pa.dataflow.urn`                 | `<str:StructureUsage><Ref .../>`                       |
| `code.parent_id`                  | `<str:Parent><Ref id="..."/>`                          |

### 8.3 URN to Ref element mapping

```python
def _write_ref(parent: etree.Element, urn: SdmxUrn) -> None:
    """Write a Ref child element from an SdmxUrn."""
    ref = etree.SubElement(parent, "Ref")
    ref.set("agencyID", urn.agency)
    ref.set("id", urn.item_id if urn.item_id else urn.id)
    ref.set("package", urn.package)
    ref.set("class", urn.artefact_class)
    if not urn.is_latest:
        ref.set("version", urn.version)
    if urn.item_id:
        ref.set("maintainableParentID", urn.id)
        if not urn.is_latest:
            ref.set("maintainableParentVersion", urn.version)
```

### 8.4 Version-specific differences

| Domain model        | v2.1 output                   | v3.0 output              |
| ------------------- | ----------------------------- | ------------------------ |
| `measures` (list)   | Single `<str:PrimaryMeasure>` | Multiple `<str:Measure>` |
| `dimensions` (list) | `<str:DimensionList>` wrapper | Flat                     |
| `Measure.usage`     | Ignored                       | Emitted                  |
| Groups              | Emitted if present            | Ignored                  |

### 8.5 Single artefact wrapping

When `to_xml` receives a single artefact instead of a StructureMessage,
it wraps it in a minimal message envelope:

```python
def to_xml(artefact: StructureMessage | Any, *, version: str = "2.1") -> bytes:
    if not isinstance(artefact, StructureMessage):
        msg = _wrap_in_message(artefact)
        return _serialize_message(msg, version)
    return _serialize_message(artefact, version)
```

---

## 9. Modification Pattern

Frozen dataclasses use `dataclasses.replace()` for modification:

```python
from dataclasses import replace

# Add a code
updated_cl = replace(cl_freq, codes=[*cl_freq.codes, sl.Code(id="W", name={"en": "Weekly"})])

# Change version
new_version = replace(dsd, version="2.0")

# Replace a dimension's representation
old_dim = dsd.dimensions["FREQ"]
new_dim = replace(old_dim, representation=new_cl_freq)  # Codelist coerced to Ref
```

### Convenience methods on DataStructure

```python
class DataStructure:
    def with_dimension(self, dim: Dimension) -> DataStructure:
        """Return a copy with one dimension replaced (matched by id)."""
        new_dims = [dim if d.id == dim.id else d for d in self.dimensions]
        return replace(self, dimensions=new_dims)

    def with_added_dimension(self, dim: Dimension) -> DataStructure:
        """Return a copy with a dimension appended."""
        return replace(self, dimensions=[*self.dimensions, dim])

    def with_attribute(self, attr: Attribute) -> DataStructure:
        """Return a copy with one attribute replaced (matched by id)."""
        new_attrs = [attr if a.id == attr.id else a for a in self.attributes]
        return replace(self, attributes=new_attrs)

    def with_added_attribute(self, attr: Attribute) -> DataStructure:
        """Return a copy with an attribute appended."""
        return replace(self, attributes=[*self.attributes, attr])
```

Usage:

```python
updated_dsd = dsd.with_dimension(
    replace(dsd.dimensions["FREQ"], representation=new_cl_freq)
)
```

---

## 10. Building from Databases

### 10.1 Full workflow: database catalog → DSD → XML

```python
import polars as pl
import sdmxlib as sl

AGENCY = "MY_AGENCY"

# ── Step 1: Query database metadata ──────────────────

columns = pl.DataFrame({
    "column": ["freq", "country", "indicator", "time_period", "value", "status"],
    "role": ["dimension", "dimension", "dimension", "time", "measure", "attribute"],
    "has_codelist": [True, True, True, False, False, True],
})

code_frames = {
    "freq": pl.DataFrame({"id": ["A", "Q", "M"], "label": ["Annual", "Quarterly", "Monthly"]}),
    "country": pl.DataFrame({"id": ["CA", "US", "MX"], "label": ["Canada", "United States", "Mexico"]}),
    "indicator": pl.DataFrame({"id": ["GDP", "CPI"], "label": ["GDP", "Consumer Price Index"]}),
    "status": pl.DataFrame({"id": ["A", "E", "P"], "label": ["Normal", "Estimated", "Provisional"]}),
}

# ── Step 2: Build codelists from DataFrames ──────────

def codelist_from_df(cl_id: str, df: pl.DataFrame, lang: str = "en") -> sl.Codelist:
    return sl.Codelist(
        id=cl_id, maintainer=AGENCY, version="1.0",
        name={lang: cl_id},
        codes=[
            sl.Code(id=row["id"], name={lang: row["label"]})
            for row in df.iter_rows(named=True)
        ],
    )

codelists = {col: codelist_from_df(f"CL_{col.upper()}", df) for col, df in code_frames.items()}

# ── Step 3: Build concept scheme ─────────────────────

cs = sl.ConceptScheme(
    id="CS_MY_DATA", maintainer=AGENCY, version="1.0",
    name={"en": "My Data Concepts"},
    concepts=[
        sl.Concept(id=row["column"].upper(), name={"en": row["column"].title()})
        for row in columns.iter_rows(named=True)
    ],
)

# ── Step 4: Build DSD ───────────────────────────────

dims, attrs, measures = [], [], []
td = None

for row in columns.iter_rows(named=True):
    col_id = row["column"].upper()
    cl = codelists.get(row["column"])

    match row["role"]:
        case "dimension":
            dims.append(sl.Dimension(
                id=col_id, position=len(dims) + 1,
                representation=cl,                  # Codelist coerced to Ref
                concept=cs.ref_to(col_id),
            ))
        case "time":
            td = sl.TimeDimension(
                concept=cs.ref_to(col_id),
                representation=sl.TextFormat(text_type="ObservationalTimePeriod"),
            )
        case "measure":
            measures.append(sl.Measure(id=col_id, concept=cs.ref_to(col_id)))
        case "attribute":
            attrs.append(sl.Attribute(
                id=col_id, usage="Conditional",
                attachment=sl.AttachmentLevel.OBSERVATION,
                representation=cl,
                concept=cs.ref_to(col_id),
            ))

dsd = sl.DataStructure(
    id="DSD_MY_DATA", maintainer=AGENCY, version="1.0",
    name={"en": "My Data Structure"},
    dimensions=dims, time_dimension=td,
    measures=measures, attributes=attrs,
)

# ── Step 5: Validate ────────────────────────────────

msg = sl.StructureMessage(
    codelists=list(codelists.values()),
    concept_schemes=[cs],
    data_structures=[dsd],
)

issues = sl.validate(dsd)
ref_issues = sl.validate_references(msg)
assert not issues, f"Validation errors: {issues}"
assert not ref_issues, f"Broken references: {ref_issues}"

# ── Step 6: Serialize ────────────────────────────────

from pathlib import Path
Path("my_structures.xml").write_bytes(sl.to_xml(msg, version="2.1"))
```

### 10.2 From a config dict

```python
import json

config = json.loads(Path("dsd_config.json").read_text())
dsd = sl.from_dict(sl.DataStructure, config)

# Validate and serialize
issues = sl.validate(dsd)
if not issues:
    Path("output.xml").write_bytes(sl.to_xml(dsd, version="2.1"))
```

---

## 11. Polars Construction Helpers

These live in `sdmxlib/polars.py` — not in the core.

```python
# sdmxlib/polars.py

def codelist_from_frame(
    id: str,
    agency: str,
    df: pl.DataFrame,
    *,
    id_col: str = "id",
    label_col: str = "label",
    parent_col: str | None = None,
    lang: str = "en",
    version: str = "1.0",
) -> Codelist:
    """Build a Codelist from a DataFrame.

        cl = slpl.codelist_from_frame("CL_AREA", "MY_AGENCY", areas_df)
    """
    ...

def codelist_to_frame(
    cl: Codelist,
    *,
    langs: list[str] | None = None,
) -> pl.DataFrame:
    """Convert a Codelist to a DataFrame.

        df = slpl.codelist_to_frame(cl)
        # ┌──────┬─────────┬───────────┐
        # │ id   │ name_en │ parent_id │
        # └──────┴─────────┴───────────┘
    """
    ...
```

---

## 12. Implementation Summary

### New methods on existing types

| Type                      | Addition                                     | Purpose                                                   |
| ------------------------- | -------------------------------------------- | --------------------------------------------------------- |
| `Ref[T]`                  | `Ref.to(obj)`                                | Resolved Ref from MaintainableArtefact                    |
| `ConceptScheme`           | `ref_to(concept_id)`                         | Resolved Ref to an item                                   |
| `Dimension`               | `__post_init__` coercion + inference         | dict→IString, list→ItemList, Codelist→Ref, repr inference |
| `Attribute`               | `__post_init__` coercion + inference         | Same                                                      |
| `Measure`                 | `__post_init__` coercion + inference         | Same                                                      |
| All MaintainableArtefacts | `__post_init__` coercion                     | dict→IString, list→ItemList/ArtefactList                  |
| `DataStructure`           | `with_dimension()`, `with_added_dimension()` | Immutable modification helpers                            |
| `DataStructure`           | `with_attribute()`, `with_added_attribute()` | Same                                                      |

### New files

| File                          | Purpose                                                                    |
| ----------------------------- | -------------------------------------------------------------------------- |
| `model/convert.py`            | `from_dict()`, `to_dict()` implementations                                 |
| `model/validation.py`         | `validate()`, `validate_references()`, `ValidationIssue`, `ReferenceIssue` |
| `formats/sdmx_ml21/writer.py` | Domain model → SDMX-ML 2.1 bytes                                           |
| `formats/sdmx_json/writer.py` | Domain model → SDMX-JSON bytes                                             |

### Format entry points

```python
# sdmxlib/formats/__init__.py
def parse(data: bytes, *, version: str | None = None) -> StructureMessage: ...
def to_xml(artefact, *, version: str = "2.1") -> bytes: ...
def to_json(artefact) -> bytes: ...

# sdmxlib/model/convert.py (re-exported from sdmxlib)
def from_dict(cls: type[T], data: dict) -> T: ...
def to_dict(artefact: Any) -> dict: ...

# sdmxlib/model/validation.py (re-exported from sdmxlib)
def validate(artefact: Any, *, strict: bool = False) -> list[ValidationIssue]: ...
def validate_references(msg: StructureMessage) -> list[ReferenceIssue]: ...
```

### Re-exports from `sdmxlib.__init__`

Add to the existing `__all__`:

```python
from sdmxlib.model.convert import from_dict, to_dict
from sdmxlib.model.validation import (
    validate, validate_references,
    ValidationIssue, ReferenceIssue,
)
from sdmxlib.formats import to_xml, to_json
```
