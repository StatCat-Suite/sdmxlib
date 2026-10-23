# Building SDMX Artefacts with sdmxlib

A practical guide to creating SDMX structural metadata from scratch.
Covers the common patterns: defining codelists, building data structure
definitions, packaging for publication, and round-tripping through XML.

---

## Before You Start

```python
import sdmxlib as sl
```

That's all you need. Every type — `Codelist`, `Code`, `DataStructure`,
`Dimension`, `Ref`, `InternationalString` — is available through `sl`.

---

## 1. Your First Codelist

A codelist is a set of valid values for a dimension. Start here because
everything else references codelists.

```python
cl_freq = sl.Codelist(
    id="CL_FREQ",
    maintainer="MY_AGENCY",
    version="1.0",
    name={"en": "Frequency"},
    codes=[
        sl.Code(id="A", name={"en": "Annual"}),
        sl.Code(id="Q", name={"en": "Quarterly"}),
        sl.Code(id="M", name={"en": "Monthly"}),
    ],
)

print(cl_freq.id)  # "CL_FREQ"
print(cl_freq.name["en"])  # "Frequency"
print(cl_freq.codes.ids())  # ["A", "Q", "M"]
print(cl_freq.codes["A"].name["en"])  # "Annual"
```

Names are dicts with language keys. Codes are a list that gets
automatically wrapped in an `ItemList` — subscriptable by id.

### Multilingual names

```python
cl_freq = sl.Codelist(
    id="CL_FREQ",
    maintainer="MY_AGENCY",
    version="1.0",
    name={"en": "Frequency", "fr": "Fréquence", "de": "Häufigkeit"},
    codes=[
        sl.Code(id="A", name={"en": "Annual", "fr": "Annuel"}),
        sl.Code(id="Q", name={"en": "Quarterly", "fr": "Trimestriel"}),
        sl.Code(id="M", name={"en": "Monthly", "fr": "Mensuel"}),
    ],
)

print(cl_freq.name["fr"])  # "Fréquence"
print(cl_freq.name.langs)  # ["en", "fr", "de"]
print(cl_freq.codes["A"].name.get("de", "en"))  # falls back to "Annual"
```

### Hierarchical codelists

Geographic classifications often have parent-child relationships:

```python
cl_area = sl.Codelist(
    id="CL_AREA",
    maintainer="MY_AGENCY",
    version="1.0",
    name={"en": "Geographic Area"},
    codes=[
        sl.Code(id="WORLD", name={"en": "World"}),
        sl.Code(id="NA", name={"en": "North America"}, parent_id="WORLD"),
        sl.Code(id="CA", name={"en": "Canada"}, parent_id="NA"),
        sl.Code(id="US", name={"en": "United States"}, parent_id="NA"),
        sl.Code(id="MX", name={"en": "Mexico"}, parent_id="NA"),
        sl.Code(id="EU", name={"en": "European Union"}, parent_id="WORLD"),
        sl.Code(id="DE", name={"en": "Germany"}, parent_id="EU"),
        sl.Code(id="FR", name={"en": "France"}, parent_id="EU"),
    ],
)

# Navigate the hierarchy — SKOS-aligned vocabulary
roots = cl_area.roots()  # ItemList[Code]: [WORLD]
children = cl_area.narrower("NA")  # ItemList[Code]: [CA, US, MX]
print([c.id for c in children])  # ["CA", "US", "MX"]

# Bulk projections (one pass each, dict[str, ...] returned)
print(cl_area.depths())  # {"WORLD": 0, "NA": 1, ...}
print(cl_area.inherit_along_broader({"NA": "north-america"}))
# {"NA": "north-america", "CA": "north-america", "US": "north-america", "MX": "north-america"}
```

---

## 2. Concepts — What Your Dimensions Mean

Concepts give semantic meaning to components. A `ConceptScheme` groups
related concepts together.

```python
cs = sl.ConceptScheme(
    id="CS_TRADE",
    maintainer="MY_AGENCY",
    version="1.0",
    name={"en": "Trade Statistics Concepts"},
    concepts=[
        sl.Concept(id="FREQ", name={"en": "Frequency"}, description={"en": "The frequency of the observation"}),
        sl.Concept(
            id="REF_AREA", name={"en": "Reference Area"}, description={"en": "The geographic area being measured"}
        ),
        sl.Concept(id="TRADE_FLOW", name={"en": "Trade Flow"}, description={"en": "Import or export"}),
        sl.Concept(id="COMMODITY", name={"en": "Commodity"}, description={"en": "The good being traded"}),
        sl.Concept(id="TIME_PERIOD", name={"en": "Time Period"}),
        sl.Concept(id="OBS_VALUE", name={"en": "Observation Value"}),
        sl.Concept(id="OBS_STATUS", name={"en": "Observation Status"}),
        sl.Concept(id="UNIT_MEASURE", name={"en": "Unit of Measure"}),
    ],
)

# Look up a concept
print(cs.concepts["FREQ"].name["en"])  # "Frequency"
print(cs.concepts["FREQ"].description.get("en"))  # "The frequency of..."

# Build a Ref to a concept (needed for DSD construction)
freq_ref = cs.ref_to("FREQ")
print(freq_ref.urn.item_id)  # "FREQ"   — the concept id lives in item_id
print(freq_ref.id)  # "CS_TRADE" — the scheme id
```

---

## 3. Building a Data Structure Definition

A DSD defines the shape of a dataset. It declares which dimensions
identify observations, which attributes describe them, and which
measures carry the values.

### A simple DSD — trade statistics

```python
# First, the codelists
cl_freq = sl.Codelist(
    id="CL_FREQ",
    maintainer="MY_AGENCY",
    version="1.0",
    name={"en": "Frequency"},
    codes=[
        sl.Code(id="A", name={"en": "Annual"}),
        sl.Code(id="Q", name={"en": "Quarterly"}),
        sl.Code(id="M", name={"en": "Monthly"}),
    ],
)

cl_area = sl.Codelist(
    id="CL_AREA",
    maintainer="MY_AGENCY",
    version="1.0",
    name={"en": "Area"},
    codes=[
        sl.Code(id="CA", name={"en": "Canada"}),
        sl.Code(id="US", name={"en": "United States"}),
        sl.Code(id="MX", name={"en": "Mexico"}),
    ],
)

cl_flow = sl.Codelist(
    id="CL_TRADE_FLOW",
    maintainer="MY_AGENCY",
    version="1.0",
    name={"en": "Trade Flow"},
    codes=[
        sl.Code(id="I", name={"en": "Imports"}),
        sl.Code(id="X", name={"en": "Exports"}),
    ],
)

cl_commodity = sl.Codelist(
    id="CL_COMMODITY",
    maintainer="MY_AGENCY",
    version="1.0",
    name={"en": "Commodity"},
    codes=[
        sl.Code(id="TOTAL", name={"en": "Total merchandise"}),
        sl.Code(id="AG", name={"en": "Agriculture"}),
        sl.Code(id="MFG", name={"en": "Manufacturing"}),
        sl.Code(id="FUEL", name={"en": "Fuels and mining"}),
    ],
)

cl_obs_status = sl.Codelist(
    id="CL_OBS_STATUS",
    maintainer="MY_AGENCY",
    version="1.0",
    name={"en": "Observation Status"},
    codes=[
        sl.Code(id="A", name={"en": "Normal value"}),
        sl.Code(id="E", name={"en": "Estimated"}),
        sl.Code(id="P", name={"en": "Provisional"}),
        sl.Code(id="B", name={"en": "Break in series"}),
    ],
)

cl_unit = sl.Codelist(
    id="CL_UNIT_MEASURE",
    maintainer="MY_AGENCY",
    version="1.0",
    name={"en": "Unit of Measure"},
    codes=[
        sl.Code(id="USD", name={"en": "US Dollars"}),
        sl.Code(id="USD_M", name={"en": "Millions of US Dollars"}),
        sl.Code(id="PCT", name={"en": "Percent"}),
    ],
)

# The concept scheme
cs = sl.ConceptScheme(
    id="CS_TRADE",
    maintainer="MY_AGENCY",
    version="1.0",
    name={"en": "Trade Statistics Concepts"},
    concepts=[
        sl.Concept(id="FREQ", name={"en": "Frequency"}),
        sl.Concept(id="REF_AREA", name={"en": "Reference Area"}),
        sl.Concept(id="TRADE_FLOW", name={"en": "Trade Flow"}),
        sl.Concept(id="COMMODITY", name={"en": "Commodity"}),
        sl.Concept(id="TIME_PERIOD", name={"en": "Time Period"}),
        sl.Concept(id="OBS_VALUE", name={"en": "Observation Value"}),
        sl.Concept(id="OBS_STATUS", name={"en": "Observation Status"}),
        sl.Concept(id="UNIT_MEASURE", name={"en": "Unit of Measure"}),
    ],
)

# The DSD
dsd = sl.DataStructure(
    id="DSD_TRADE",
    maintainer="MY_AGENCY",
    version="1.0",
    name={"en": "International Trade Statistics"},
    description={"en": "Merchandise trade by commodity and partner country"},
    dimensions=[
        sl.Dimension(id="FREQ", representation=cl_freq, concept=cs.ref_to("FREQ")),
        sl.Dimension(id="REF_AREA", representation=cl_area, concept=cs.ref_to("REF_AREA")),
        sl.Dimension(id="TRADE_FLOW", representation=cl_flow, concept=cs.ref_to("TRADE_FLOW")),
        sl.Dimension(id="COMMODITY", representation=cl_commodity, concept=cs.ref_to("COMMODITY")),
    ],
    time_dimension=sl.TimeDimension(
        concept=cs.ref_to("TIME_PERIOD"),
        representation=sl.Facet(type=sl.DataType.ObservationalTimePeriod),
    ),
    measures=[
        sl.Measure(id="OBS_VALUE", concept=cs.ref_to("OBS_VALUE")),
    ],
    attributes=[
        sl.Attribute(
            id="OBS_STATUS",
            usage="Conditional",
            attachment=sl.AttachmentLevel.OBSERVATION,
            representation=cl_obs_status,
            concept=cs.ref_to("OBS_STATUS"),
        ),
        sl.Attribute(
            id="UNIT_MEASURE",
            usage="Mandatory",
            # A series attribute names the dimensions whose values identify its series.
            attachment=sl.AttachmentLevel.SERIES,
            related_dimensions=["FREQ", "REF_AREA", "TRADE_FLOW", "COMMODITY"],
            representation=cl_unit,
            concept=cs.ref_to("UNIT_MEASURE"),
        ),
    ],
)
```

### Explore what you built

```python
print(dsd.id)  # "DSD_TRADE"
print(dsd.dimensions.ids())  # ["FREQ", "REF_AREA", "TRADE_FLOW", "COMMODITY"]
print(dsd.time_dimension.text_type)  # ObservationalTimePeriod
print(dsd.measures.ids())  # ["OBS_VALUE"]
print(dsd.attributes.ids())  # ["OBS_STATUS", "UNIT_MEASURE"]

# The codelist is right there — no resolution needed
freq_dim = dsd.dimensions["FREQ"]
print(freq_dim.representation().codes.ids())  # ["A", "Q", "M"]

# Convenience properties work immediately
print(dsd.enumerated_dimensions.ids())  # ["FREQ", "REF_AREA", "TRADE_FLOW", "COMMODITY"]
print(dsd.code_map(lang="en"))
# {"FREQ": {"A": "Annual", ...}, "REF_AREA": {"CA": "Canada", ...}, ...}
```

---

## 4. Publishing — Dataflow and StructureMessage

A `Dataflow` makes a DSD's data available to consumers. A
`StructureMessage` packages everything for transmission.

```python
from datetime import UTC, datetime

# Create the dataflow
df = sl.Dataflow(
    id="DF_TRADE",
    maintainer="MY_AGENCY",
    version="1.0",
    name={"en": "International Trade Data"},
    structure=dsd,  # bare DataStructure → coerced to Ref
)

# Package everything into a message
msg = sl.StructureMessage(
    codelists=[cl_freq, cl_area, cl_flow, cl_commodity, cl_obs_status, cl_unit],
    concept_schemes=[cs],
    data_structures=[dsd],
    dataflows=[df],
    # Optional. Without one, a write emits fixed placeholders, so the same
    # message always serialises to the same bytes.
    header=sl.Header(
        id="TRADE_2026",
        prepared=datetime(2026, 10, 20, tzinfo=UTC),
        sender=sl.Sender(id="MY_AGENCY", name={"en": "My Agency"}),
    ),
)

# Every artefact above was checked as it was built: a bad id, a missing
# concept or two codes sharing an id raise `ValidationError` at the
# constructor. What only the whole message can know — that every reference
# resolves, and nothing is in it twice — `validate` checks. It returns None
# on success and raises with the full list of issues attached as `e.issues`.
try:
    sl.validate(msg)
except sl.ReferenceValidationError as e:
    print("Broken references:")
    for issue in e.issues:
        print(f"  {issue.source} → {issue.target_urn}")

# Serialize to SDMX-ML 2.1
from pathlib import Path

Path("trade_structures.xml").write_bytes(sl.to_xml(msg, version="2.1"))
```

---

## 5. Referencing External Artefacts

You don't need to have a codelist in memory to reference it. If you're
building a DSD that uses the cross-domain SDMX codelists (CL_FREQ,
CL_CONF_STATUS, etc.), just create an unresolved Ref. Give it a version: a
reference is written into the document, and one without a version is refused
in every format — 3.0 has nowhere to put "latest", and 2.1 reads a missing
version as `1.0`.

```python
# Reference the SDMX cross-domain frequency codelist
dim_freq = sl.Dimension(
    id="FREQ",
    representation=sl.Ref.unresolved(sl.Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="2.0")),
    concept=sl.Ref.unresolved(sl.Concept.urn_for(agency="SDMX", scheme="CROSS_DOMAIN_CONCEPTS", id="FREQ", version="2.0")),
)

# Build a minimal DSD and message — the writer only needs the URNs,
# not the actual codelists from external agencies
dsd_minimal = sl.DataStructure(
    id="DSD_EXAMPLE",
    maintainer="MY_AGENCY",
    version="1.0",
    name={"en": "Example DSD"},
    dimensions=[dim_freq],
    # Every component needs a ConceptIdentity — SDMX requires it, so a DSD
    # without one cannot be serialised in any format.
    measures=[
        sl.Measure(
            id="OBS_VALUE",
            concept=sl.Ref.unresolved(sl.Concept.urn_for(agency="SDMX", scheme="CROSS_DOMAIN_CONCEPTS", id="OBS_VALUE", version="2.0")),
        )
    ],
)
msg_minimal = sl.StructureMessage(data_structures=[dsd_minimal])

xml = sl.to_xml(msg_minimal, version="2.1")
print(b"CL_FREQ" in xml)  # True — the URN is serialized
```

This is common when your agency publishes data using dimensions
defined by international standards. You reference them by URN without
needing to bundle copies of the cross-domain codelists.

---

## 6. Modifying Existing Artefacts

Use `evolve()` from attrs to create a modified copy without changing the original:

```python
from attrs import evolve

# Add a code to an existing codelist
updated_cl = evolve(
    cl_freq,
    codes=[
        *cl_freq.codes,
        sl.Code(id="W", name={"en": "Weekly"}),
        sl.Code(id="D", name={"en": "Daily"}),
    ],
)
print(updated_cl.codes.ids())  # ["A", "Q", "M", "W", "D"]
print(cl_freq.codes.ids())  # ["A", "Q", "M"] — original unchanged

# Bump the version
v2 = evolve(updated_cl, version="2.0")

# Update a dimension's codelist in a DSD
updated_dsd = evolve(
    dsd,
    dimensions=[evolve(dim, representation=v2) if dim.id == "FREQ" else dim for dim in dsd.dimensions],
)
```

### Version a complete DSD

```python
# Add a new codelist for partner countries
cl_partner = sl.Codelist(
    id="CL_PARTNER",
    maintainer="MY_AGENCY",
    version="1.0",
    name={"en": "Partner Country"},
    codes=[
        sl.Code(id="CA", name={"en": "Canada"}),
        sl.Code(id="US", name={"en": "United States"}),
        sl.Code(id="EU", name={"en": "European Union"}),
    ],
)

# Extend the concept scheme to include the new concept
cs_v2 = evolve(
    cs,
    concepts=[
        *cs.concepts,
        sl.Concept(id="PARTNER", name={"en": "Partner Country"}),
    ],
)

# Clone-and-modify for a new version — adds a PARTNER dimension
dsd_v2 = evolve(
    dsd,
    version="2.0",
    name={"en": "International Trade Statistics v2"},
    dimensions=[
        *dsd.dimensions,
        sl.Dimension(
            id="PARTNER",
            representation=cl_partner,
            concept=cs_v2.ref_to("PARTNER"),
        ),
    ],
)

print(dsd_v2.version)  # "2.0"
print(dsd_v2.dimensions.ids())  # ["FREQ", "REF_AREA", "TRADE_FLOW", "COMMODITY", "PARTNER"]
```

---

## 7. From a Config File

If your DSD structure is defined in JSON or YAML, load it with `from_dict`:

```json
{
  "id": "DSD_PRICES",
  "agency_id": "MY_AGENCY",
  "version": "1.0",
  "name": { "en": "Consumer Prices" },
  "dimensions": [
    {
      "id": "FREQ",
      "concept": "urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=MY_AGENCY:CS_PRICES(1.0).FREQ",
      "representation": {
        "kind": "Enumeration",
        "urn": "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=MY_AGENCY:CL_FREQ(1.0)"
      }
    },
    {
      "id": "REF_AREA",
      "concept": "urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=MY_AGENCY:CS_PRICES(1.0).REF_AREA",
      "representation": {
        "kind": "Enumeration",
        "urn": "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=MY_AGENCY:CL_AREA(1.0)"
      }
    }
  ],
  "time_dimension": {
    "concept": "urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=MY_AGENCY:CS_PRICES(1.0).TIME_PERIOD",
    "representation": { "type": "ObservationalTimePeriod" }
  },
  "measures": [
    {
      "id": "OBS_VALUE",
      "concept": "urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=MY_AGENCY:CS_PRICES(1.0).OBS_VALUE"
    }
  ]
}
```

Every component names its concept: a DSD whose component has none is refused
as it is built, since no SDMX format can write it.

```python
import json

config = json.loads(Path("dsd_config.json").read_text())
dsd = sl.from_dict(sl.DataStructure, config)

print(dsd.id)  # "DSD_PRICES"
print(dsd.dimensions.ids())  # ["FREQ", "REF_AREA"]

# Refs from the config are unresolved (URN only)
print(dsd.dimensions["FREQ"].representation.id)  # "CL_FREQ"
print(dsd.dimensions["FREQ"].representation.resolved)  # False
```

### Round-trip through dict

```python
# Domain object → dict → domain object
d = sl.to_dict(dsd)
dsd2 = sl.from_dict(sl.DataStructure, d)
assert dsd.id == dsd2.id
assert dsd.dimensions.ids() == dsd2.dimensions.ids()
```

---

## 8. From a Database

The most common real-world scenario: generate SDMX structural metadata
from an existing data warehouse.

```python
import polars as pl
import sdmxlib as sl

AGENCY = "STAT_OFFICE"

# ── Query your database ──────────────────────────────

# This might come from information_schema, a metadata table, or manual config
columns = pl.DataFrame(
    {
        "name": ["freq", "region", "indicator", "time", "value", "status", "unit"],
        "role": ["dimension", "dimension", "dimension", "time", "measure", "attribute", "attribute"],
        "coded": [True, True, True, False, False, True, True],
    }
)

# Distinct values per coded column — from SELECT DISTINCT queries
distinct_values = {
    "freq": pl.DataFrame({"id": ["A", "Q", "M"], "label": ["Annual", "Quarterly", "Monthly"]}),
    "region": pl.DataFrame(
        {
            "id": ["ON", "QC", "BC", "AB"],
            "label": ["Ontario", "Quebec", "British Columbia", "Alberta"],
        }
    ),
    "indicator": pl.DataFrame({"id": ["GDP", "CPI", "EMP"], "label": ["GDP", "CPI", "Employment"]}),
    "status": pl.DataFrame({"id": ["A", "E", "P"], "label": ["Normal", "Estimated", "Provisional"]}),
    "unit": pl.DataFrame({"id": ["CAD_M", "PCT", "IDX"], "label": ["Millions CAD", "Percent", "Index"]}),
}

# ── Build codelists ──────────────────────────────────

codelists = {}
for col_name, values_df in distinct_values.items():
    codelists[col_name] = sl.Codelist(
        id=f"CL_{col_name.upper()}",
        maintainer=AGENCY,
        version="1.0",
        name={"en": col_name.replace("_", " ").title()},
        codes=[sl.Code(id=row["id"], name={"en": row["label"]}) for row in values_df.iter_rows(named=True)],
    )

# ── Build concept scheme ─────────────────────────────

cs = sl.ConceptScheme(
    id="CS_ECON",
    maintainer=AGENCY,
    version="1.0",
    name={"en": "Economic Statistics Concepts"},
    concepts=[
        sl.Concept(id=row["name"].upper(), name={"en": row["name"].replace("_", " ").title()})
        for row in columns.iter_rows(named=True)
    ],
)

# ── Build DSD ────────────────────────────────────────

dims, attrs, measures = [], [], []
td = None

for row in columns.iter_rows(named=True):
    col_id = row["name"].upper()
    cl = codelists.get(row["name"])

    match row["role"]:
        case "dimension":
            dims.append(
                sl.Dimension(
                    id=col_id,
                    representation=cl,
                    concept=cs.ref_to(col_id),
                )
            )
        case "time":
            td = sl.TimeDimension(
                concept=cs.ref_to(col_id),
                representation=sl.Facet(type=sl.DataType.ObservationalTimePeriod),
            )
        case "measure":
            # SDMX-ML 2.1 pins the primary measure's id to OBS_VALUE:
            # `to_xml(version="2.1")` writes any other id as OBS_VALUE, so it
            # reads back renamed. Naming it OBS_VALUE here keeps it the same
            # in every format.
            measures.append(sl.Measure(id="OBS_VALUE", concept=cs.ref_to(col_id)))
        case "attribute":
            attrs.append(
                sl.Attribute(
                    id=col_id,
                    usage="Conditional",
                    attachment=sl.AttachmentLevel.OBSERVATION,
                    representation=cl,
                    concept=cs.ref_to(col_id),
                )
            )

dsd = sl.DataStructure(
    id="DSD_ECON",
    maintainer=AGENCY,
    version="1.0",
    name={"en": "Economic Statistics"},
    dimensions=dims,
    time_dimension=td,
    measures=measures,
    attributes=attrs,
)

# ── Validate and serialize ───────────────────────────

msg = sl.StructureMessage(
    codelists=list(codelists.values()),
    concept_schemes=[cs],
    data_structures=[dsd],
)

sl.validate(msg)  # every reference resolves, nothing is in it twice

Path("econ_structures.xml").write_bytes(sl.to_xml(msg, version="2.1"))
```

---

## 9. Fetch, Modify, Republish

A common workflow: pull a DSD from a registry, modify it, and publish
the updated version.

```python
import sdmxlib as sl
from attrs import evolve

# ── Fetch the current version ────────────────────────

with sl.RestRegistry(sl.Provider.ECB) as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1"), resolve=True)

# ── Inspect what's there ─────────────────────────────

print(f"Version: {dsd.version}")
print(f"Dimensions: {dsd.dimensions.ids()}")
for dim in dsd.dimensions:
    if dim.is_enumerated:
        print(f"  {dim.id}: {dim.representation.id} ({len(dim.representation().codes)} codes)")

# ── Add a new attribute ──────────────────────────────

new_dsd = dsd.add(
    sl.Attribute(
        id="DATA_SOURCE",
        usage="Conditional",
        attachment=sl.AttachmentLevel.SERIES,
        related_dimensions=dsd.dimensions.ids(),
        # No codelist — free text attribute. The *concept* is still required:
        # every SDMX format demands a ConceptIdentity on every component, so
        # `add` refuses a component without one.
        concept=sl.Ref.unresolved(
            sl.Concept.urn_for(agency="SDMX", scheme="CROSS_DOMAIN_CONCEPTS", id="DATA_SOURCE", version="2.0")
        ),
    )
)

# ── Bump the version ─────────────────────────────────

new_dsd = evolve(
    new_dsd,
    version="2.0",
    name={"en": "ECB Exchange Rates v2"},
)

# ── Serialize the modified DSD ───────────────────────

msg = sl.StructureMessage(data_structures=[new_dsd])
xml = sl.to_xml(msg, version="2.1")
Path("ecb_exr1_v2.xml").write_bytes(xml)
```

---

## 10. Building Annotated Artefacts

Annotations carry metadata about artefacts — contacts, deprecation
notices, data sources, processing instructions.

```python
cl = sl.Codelist(
    id="CL_AREA",
    maintainer="MY_AGENCY",
    version="1.0",
    name={"en": "Area"},
    annotations=[
        sl.Annotation(
            type="CONTACT",
            text={"en": "stats-helpdesk@example.com"},
        ),
        sl.Annotation(
            type="SOURCE",
            title="Data source",
            text={"en": "ISO 3166-1 alpha-2"},
            urls="https://www.iso.org/iso-3166-country-codes.html",
        ),
        sl.Annotation(
            type="UPDATE_SCHEDULE",
            text={"en": "Updated annually in January"},
        ),
    ],
    codes=[
        sl.Code(id="CA", name={"en": "Canada"}),
        sl.Code(
            id="CS",
            name={"en": "Czechoslovakia"},
            annotations=[
                sl.Annotation(
                    type="DEPRECATED",
                    text={"en": "Dissolved 1993. Use CZ and SK."},
                ),
            ],
        ),
        sl.Code(id="CZ", name={"en": "Czech Republic"}),
        sl.Code(id="SK", name={"en": "Slovakia"}),
    ],
)

# Query annotations
print(cl.annotations.types)  # ["CONTACT", "SOURCE", "UPDATE_SCHEDULE"]
print(cl.annotations.first("CONTACT").text["en"])  # "stats-helpdesk@example.com"
print("DEPRECATED" in cl.codes["CS"].annotations)  # True
```

---

## 11. Using Polars for Bulk Construction

For large codelists from database exports or CSV files, read the frame with
Polars and build the codes from its rows with `iter_rows(named=True)`. A
bulk-construction helper would only rename the three column arguments, so
sdmxlib has none.

```python
import polars as pl
import sdmxlib as sl

# Load a large geographic classification
areas = pl.read_csv("iso_countries.csv")
# ┌──────┬──────────────────┬───────────┐
# │ id   │ label            │ parent_id │
# ├──────┼──────────────────┼───────────┤
# │ CA   │ Canada           │ NA        │
# │ US   │ United States    │ NA        │
# │ ...  │ ...              │ ...       │
# └──────┴──────────────────┴───────────┘

cl_area = sl.Codelist(
    id="CL_AREA",
    maintainer="MY_AGENCY",
    version="1.0",
    name={"en": "Geographic Area"},
    codes=[
        sl.Code(
            id=row["id"],
            name={"en": row["label"]},
            # An empty parent cell is a root, not a code whose parent is "".
            parent_id=row["parent_id"] or None,
        )
        for row in areas.iter_rows(named=True)
    ],
)

print(len(cl_area.codes))  # 250+
print(cl_area.roots().ids()[:5])  # top-level regions
```

---

## 12. Quick Reference

### Construction cheat sheet

| What                  | How                                                                     |
| --------------------- | ----------------------------------------------------------------------- |
| Codelist              | `sl.Codelist(id=, maintainer=, name={}, codes=[...])`                   |
| Code                  | `sl.Code(id=, name={})`                                                 |
| Hierarchical code     | `sl.Code(id=, name={}, parent_id="PARENT")`                             |
| ConceptScheme         | `sl.ConceptScheme(id=, maintainer=, concepts=[...])`                    |
| Concept               | `sl.Concept(id=, name={})`                                              |
| Concept Ref           | `cs.ref_to("CONCEPT_ID")`                                               |
| Dimension (coded)     | `sl.Dimension(id=, representation=cl, concept=cs.ref_to(...))`          |
| Dimension (free text) | `sl.Dimension(id=, representation=sl.Facet(...), concept=cs.ref_to(...))` |
| TimeDimension         | `sl.TimeDimension(representation=sl.Facet(type=sl.DataType.X), concept=cs.ref_to(...))` |
| Measure               | `sl.Measure(id=, concept=cs.ref_to(...))`                               |
| Attribute (coded)     | `sl.Attribute(id=, representation=cl, attachment=sl.AttachmentLevel.X, concept=cs.ref_to(...))` |
| Attribute (free text) | `sl.Attribute(id=, attachment=sl.AttachmentLevel.X, concept=cs.ref_to(...))` |
| DataStructure         | `sl.DataStructure(id=, maintainer=, dimensions=[...], ...)`             |
| Dataflow              | `sl.Dataflow(id=, maintainer=, structure=dsd)`                          |
| External ref          | `sl.Ref.unresolved(sl.Codelist.urn_for(agency=, id=, version=))`            |
| StructureMessage      | `sl.StructureMessage(codelists=[...], data_structures=[...])`           |

### Coercion shortcuts

| Instead of                            | Write               |
| ------------------------------------- | ------------------- |
| `name=sl.InternationalString(en="X")` | `name={"en": "X"}`  |
| `codes=sl.ItemList([...])`            | `codes=[...]`       |
| `representation=sl.Ref.to(cl)`        | `representation=cl` |
| `codelists=sl.ArtefactList([...])`    | `codelists=[...]`   |

### Serialization

| Operation | Code                                    |
| --------- | --------------------------------------- |
| To XML    | `sl.to_xml(msg, version="2.1")`         |
| To JSON   | `sl.to_json(msg)`                       |
| To dict   | `sl.to_dict(dsd)`                       |
| From XML  | `sl.parse(xml_bytes)`                   |
| From dict | `sl.from_dict(sl.DataStructure, {...})` |

### Validation

| What                     | How                                  |
| ------------------------ | ------------------------------------ |
| Per-artefact structure   | checked as it is built — the constructor raises |
| An IM rule no format enforces | built anyway, with an `sl.ImRuleWarning` |
| A message                | `sl.validate(msg)`                   |
| A target format          | `sl.serialize(msg, version=...)`     |

An artefact is checked as it is built: its constructor, and `attrs.evolve`,
enforce the SDMX information model's rules that one artefact can break on its
own, and the rules every wire format adds to them, so no artefact you hold
breaks one. `validate` checks what only a message can know — its references
resolve and nothing is in it twice — and `serialize` what the target format
can carry. The IM's rules
fall into four kinds, and the checks above are the first. Some cannot be broken at all,
because the model's types cannot hold the violation: a time dimension has no
enumeration to code it with. Others are knowingly not checked. Rules that relate one artefact to
another fall here, such as a partial codelist against the full one, or a
constraint's values against their components' representations. So does a
data set's conformance to its DSD. `tests/support/im_rules.py` lists every
normative sentence of the IM (SDMX 3.1, Section 2), quoted, and says which
of the four each one is.

The fourth is a rule the IM states and no wire schema enforces: a facet's
sequence attributes going together, or a data constraint holding cube regions
or key sets but not both. Every SDMX format can carry an artefact that breaks
one, so it is built, with one `sl.ImRuleWarning` per artefact naming each
rule it breaks. To make them errors, filter them:

```python
import warnings

warnings.simplefilter("error", sl.SdmxWarning)   # sdmxlib's own warnings
warnings.simplefilter("error", sl.ImRuleWarning)  # just these
```

`SdmxWarning` covers the warnings sdmxlib defines. One the storage layer
gives is a plain `RuntimeWarning`: a `LocalRegistry` write overwriting an
artefact stored under the same URN from a different `source_registry`.

`parse(..., lossless=True)` does not change them: it refuses what a read drops
or invents, which is a separate question from whether the content breaks an
IM rule.
