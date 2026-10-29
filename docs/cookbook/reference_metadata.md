# Reference Metadata

Recipes for attaching structured metadata (footnotes, methodology notes,
quality reports, ESMS-style content) to SDMX artefacts.

The reference-metadata model has two halves:

| Layer | Maps to (XML / JSON) | What it carries |
| --- | --- | --- |
| `MetadataStructure` (MSD) + `Metadataflow` | `<mes:Structure>` / structure schema | The schema — definitions of attribute slots |
| `MetadataSet` | `<mes:GenericMetadata>` / metadata schema | The populated payload — values attached to target artefacts |

Definitions ride in a `StructureMessage`; populated payloads ride in a
`MetadataMessage`. The top-level `sl.parse()` auto-discriminates;
`sl.to_xml()` and `sl.to_json()` dispatch on input type.

---

## Defining the schema

Build a `MetadataStructure` (MSD) describing the shape of your metadata,
optionally wrapped in a `Metadataflow`:

```python
import sdmxlib as sl

msd = sl.MetadataStructure(
    id="MSD_FOOTNOTES",
    maintainer="STATCAN",
    version="1.0",
    name={"en": "Census footnote MSD"},
    attributes=[
        sl.MetadataAttribute(
            id="FOOTNOTE_TEXT",
            # Required: every SDMX format demands a ConceptIdentity on a
            # metadata attribute, exactly as on a dimension. The attribute
            # alone can be built without one, but the MetadataStructure
            # holding it is refused as it is built.
            #
            # A concept is an *item*, so it has no independent URN and
            # `Ref.to` refuses it — name its position within the scheme.
            concept=sl.Ref[sl.Concept].unresolved(
                sl.Concept.urn_for(agency="STATCAN", scheme="CS_CENSUS", id="FOOTNOTE_TEXT", version="1.0")
            ),
            representation=sl.Facet(type=sl.DataType.String),
            min_occurs=0,
            max_occurs=1,
        ),
    ],
)

mdf = sl.Metadataflow(
    id="MDF_FOOTNOTES",
    maintainer="STATCAN",
    version="1.0",
    name={"en": "Census footnote flow"},
    structure=msd,
    # At least one target is required — SDMX-ML 3.0 declares <Target> with
    # minOccurs=1. The targets say what the reported metadata may be *about*:
    # a metadata set's targets must be among them, and a `*` stands for any
    # part of a URN. These footnotes describe STATCAN codes.
    targets=["urn:sdmx:org.sdmx.infomodel.codelist.Code=STATCAN:*(1.0).*"],
)
```

Attributes can nest — use `is_presentational=True` for container nodes
that don't carry a value themselves:

```python
def concept(concept_id: str) -> sl.Ref[sl.Concept]:
    urn = sl.Concept.urn_for(agency="STATCAN", scheme="CS_CENSUS", id=concept_id, version="1.0")
    return sl.Ref[sl.Concept].unresolved(urn)


quality_msd = sl.MetadataStructure(
    id="MSD_QUALITY",
    maintainer="STATCAN",
    name={"en": "Quality"},
    attributes=[
        sl.MetadataAttribute(
            id="QUALITY",
            concept=concept("QUALITY"),
            is_presentational=True,
            children=[
                sl.MetadataAttribute(
                    id="COVERAGE", concept=concept("COVERAGE"), representation=sl.Facet(type=sl.DataType.String)
                ),
                sl.MetadataAttribute(
                    id="ACCURACY", concept=concept("ACCURACY"), representation=sl.Facet(type=sl.DataType.Decimal)
                ),
            ],
        ),
    ],
)
```

`max_occurs=None` means unbounded; repetition is expressed by sibling
`ReportedAttribute` instances with the same id, not a values list.

---

## Populating reference metadata

`MetadataSet` carries a list of targets and a single attribute tree that
applies to all of them. The SDMX 3.0 standard dropped the v2.1
`MetadataReport` layer; sdmxlib follows the standard.

The natural shape for many-to-many footnote relationships (e.g. 204
unique footnotes shared across 2,631 census characteristics in the
StatCan 2021 Census Profile) is **one `MetadataSet` per unique
footnote**, listing every target that shares it:

```python
def code_urn(codelist_id: str, code_id: str) -> sl.SdmxUrn:
    return sl.SdmxUrn.make(
        sl.Code,
        package="codelist",
        artefact_class="Code",
        agency="STATCAN",
        id=codelist_id,
        version="1.0",
        item_id=code_id,
    )


footnote_a = sl.MetadataSet(
    id="FOOTNOTE_A",
    maintainer="STATCAN",
    version="1.0.0",
    structure=mdf,
    name={"en": "Footnote A"},
    targets=[code_urn("CL_CHARACTERISTIC", c) for c in ["1", "5", "12"]],
    attributes=[
        sl.ReportedAttribute(
            id="FOOTNOTE_TEXT",
            value={"en": "2021 and 2016 population: Statistics Canada is …"},
        ),
    ],
)

footnote_b = sl.MetadataSet(
    id="FOOTNOTE_B",
    maintainer="STATCAN",
    version="1.0.0",
    name={"en": "Footnote B"},
    structure=mdf,
    targets=[code_urn("CL_CHARACTERISTIC", "3")],
    attributes=[
        sl.ReportedAttribute(
            id="FOOTNOTE_TEXT",
            value={"en": "Excludes religious institutions …"},
        ),
    ],
)
```

The wire format does the deduplication structurally — each footnote's
text appears exactly once per set, regardless of how many targets share
it.

---

## Reported values

`ReportedAttribute.value` is a primitive union — `str | int | float |
bool | InternationalString | None` — and the MSD declares whether a string
scalar is a code id, a date, free text, etc.

```python
sl.ReportedAttribute(id="FREQ", value="A")  # code id (scalar str)
sl.ReportedAttribute(id="POSTAL_CODE", value=2721)  # number
sl.ReportedAttribute(id="IS_PROV", value=True)  # boolean
sl.ReportedAttribute(id="NAME", value={"en": "x"})  # multilingual
sl.ReportedAttribute(
    id="QUALITY",
    children=[  # presentational container
        sl.ReportedAttribute(id="COVERAGE", value="full"),
    ],
)
```

Repetition uses sibling attributes with the same id, never a values
list — matching the SDMX 3.0 wire formats:

```python
ms = sl.MetadataSet(
    id="MS_TAGS",
    maintainer="STATCAN",
    name={"en": "Tag metadata"},
    structure=mdf,
    targets=[code_urn("CL_T", "1")],
    attributes=[
        sl.ReportedAttribute(id="TAG", value="alpha"),
        sl.ReportedAttribute(id="TAG", value="beta"),
        sl.ReportedAttribute(id="TAG", value="gamma"),
    ],
)
```

---

## Persisting to a LocalRegistry

`LocalRegistry` (DuckDB-backed) persists both kinds of artefacts. MSDs
and Metadataflows live in the structures-side tables; MetadataSets live
in dedicated `metadata_set` / `metadata_target` / `metadata_value`
tables.

```python
with sl.LocalRegistry("census_footnotes.duckdb") as local:
    local.add(msd)
    local.add(mdf)
    local.add(footnote_a)
    local.add(footnote_b)

# Round-trip
with sl.LocalRegistry("census_footnotes.duckdb") as local:
    set_urn = sl.MetadataSet.urn_for(agency="STATCAN", id="FOOTNOTE_A", version="1.0.0")
    out = local.get(set_urn)
    assert {t.urn.item_id for t in out.targets} == {"1", "5", "12"}
```

---

## Serialising to wire formats

Wrap your sets in a `MetadataMessage` and use `sl.to_xml()` /
`sl.to_json()`. Each dispatches on the input type and emits the proper
envelope (`<mes:GenericMetadata>` for XML; metadata `$schema` for JSON).

```python
msg = sl.MetadataMessage(metadata_sets=[footnote_a, footnote_b])

xml_bytes = sl.to_xml(msg)  # <mes:GenericMetadata>
json_bytes = sl.to_json(msg)  # SDMX-JSON metadata schema
```

For the structures-side definitions, use a `StructureMessage`:

```python
defs = sl.StructureMessage(
    metadata_structures=[msd],
    metadataflows=[mdf],
)
defs_xml = sl.to_xml(defs, version="3.0")
defs_json = sl.to_json(defs)
```

---

## Parsing — auto-discrimination

`sl.parse()` returns a `StructureMessage | MetadataMessage` based on the
envelope. Discriminate with `isinstance`:

```python
parsed = sl.parse(xml_bytes)

if isinstance(parsed, sl.MetadataMessage):
    for ms in parsed.metadata_sets:
        for target in ms.targets:
            footnote = ms.attributes["FOOTNOTE_TEXT"].value
            print(target.urn, footnote)
```

JSON works the same way — auto-detected by `$schema`:

```python
parsed = sl.parse(json_bytes)
assert isinstance(parsed, sl.MetadataMessage)
```

---

## Metadataflow vs MetadataProvisionAgreement

`MetadataSet.structure` accepts a Metadataflow (typical) or a
MetadataProvisionAgreement — the two options the SDMX 3.0
`MetadataSetType` `xs:choice` allows. The wire format records which kind
was used and the round-trip preserves it:

```python
mpa = sl.MetadataProvisionAgreement(
    id="MPA_FOOTNOTES",
    maintainer="STATCAN",
    version="1.0",
    name={"en": "Census footnote provision"},
    metadataflow=mdf,
)

ms_via_flow = sl.MetadataSet(id="MS1", maintainer="STATCAN", name={"en": "Set via flow"}, structure=mdf)
ms_via_provision = sl.MetadataSet(id="MS2", maintainer="STATCAN", name={"en": "Set via provision"}, structure=mpa)

assert ms_via_flow.structure.urn.artefact_class == "Metadataflow"
assert ms_via_provision.structure.urn.artefact_class == "MetadataProvisionAgreement"
```

The model reaches an MSD only through a Metadataflow or a provision
agreement, so passing a `MetadataStructure` is rejected — wrap it in a
Metadataflow:

<!-- test: raises -->

```python
sl.MetadataSet(id="MS3", maintainer="STATCAN", name={"en": "Set via MSD"}, structure=msd)
# TypeError: MetadataSet.structure cannot reference a MetadataStructure
# directly — ... Wrap the MSD in a Metadataflow:
# Metadataflow(id=..., maintainer=..., structure=msd).
```

---

## Advertising which MSD describes a dataflow

Tools built against OECD / `.Stat` (notably the Data Explorer's Overview
tab) discover *which* reference-metadata schema applies to a dataflow by
reading the association off the **structure response** — not by walking the
metadata graph themselves. sdmxlib derives that association from the lake
(populated `MetadataSet.targets`, plus declared `Metadataflow` /
`MetadataProvisionAgreement` targets including wildcards) and emits it in
either of two forms. Both derive from the same source of truth; neither is
persisted onto the DSD.

The builders live in `sdmxlib.builders`:

```python
from sdmxlib.builders import (
    build_dsd_metadata_map,  # derive dsd_urn -> [DsdMetadataLink]
    annotate_structure_message,  # .Stat compat annotation (pure projection)
    build_metadata_target_links,  # derive target_urn -> [metadataset_urn]
)
```

### `.Stat` compatibility — the `type="METADATA"` annotation

The legacy `.Stat` convention: a `type="METADATA"` annotation on the DSD
whose `title` is the MSD URN. `annotate_structure_message` splices it on,
idempotently, returning a **new** message (models are frozen, so the DSDs
you passed in are untouched). Because every structure writer already serialises DSD
annotations, this works for SDMX-JSON *and* SDMX-ML 2.1/3.0:

```python
with sl.LocalRegistry("census_footnotes.duckdb") as store:
    md_map = build_dsd_metadata_map(store)  # {dsd_urn: [DsdMetadataLink, ...]}
    message = annotate_structure_message(message, md_map)

json_bytes = sl.to_json(message)  # DSD now carries the METADATA annotation
```

`build_dsd_metadata_map` resolves the MSD through the full
`MetadataProvisionAgreement → Metadataflow → MetadataStructure` chain and
orders multiple MSDs `*_DATAFLOW_INFO` first (the Explorer picks the
first). This annotation is a **compatibility shim** — deprecated the moment
consumers move to the native link below.

### Native SDMX 3.0 — the `rel="metadata"` link

The forward-looking form (what FMR emits): a link on the targeted artefact
pointing at the metadata *report*. Opt in on the **SDMX-JSON** or
**SDMX-ML 3.0** structure writer with the derived target→reports map and
the REST `base_url` used to build absolute URLs:

```python
from sdmxlib.formats.sdmx_json.writer import write as write_json
from sdmxlib.formats.sdmx_ml30.writer import write as write_ml30

with sl.LocalRegistry("census_footnotes.duckdb") as store:
    target_links = build_metadata_target_links(store)  # {target_urn: [metadataset_urn, ...]}

json_bytes = write_json(message, metadata_links=target_links, base_url="https://host/FusionRegistry/sdmx/v2")
xml_bytes = write_ml30(message, metadata_links=target_links, base_url="https://host/FusionRegistry/sdmx/v2")
```

Each targeted artefact gains a link — SDMX-JSON `links[*]`:

```json
{"rel": "metadata",
 "href": "https://host/FusionRegistry/sdmx/v2/metadata/metadataset/AG:ID(VER)",
 "urn": "urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataSet=AG:ID(VER)"}
```

…and the equivalent SDMX-ML 3.0 `<com:Link>` (after `<com:Annotations>`,
before `<com:Name>`, per `MaintainableBaseType`):

```xml
<com:Link rel="metadata"
          url="https://host/FusionRegistry/sdmx/v2/metadata/metadataset/AG/ID/VER"
          urn="urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataSet=AG:ID(VER)"/>
```

Both shapes were verified byte-for-byte against a live FMR instance.

Note the two forms differ: the annotation sits on the **DSD** and points at
the **MSD**; the native link sits on the **target** (e.g. the dataflow) and
points at the **MetadataSet report** (follow it → its `metadataflow` →
MSD). Output is unchanged when `metadata_links` is omitted.

An artefact read from a registry that inserts these links holds them in
`artefact.links`, and the writers put `metadata_links` after them,
skipping any report a held link already points at — so writing back what was
read does not link a report twice.

---

## Querying reported metadata

`metadata_report(set_urn)` projects one MetadataSet at a time. When the
question spans the catalogue — "everything said about this dataflow", "every
QUALITY statement anywhere" — use `reg.reported_attributes()`. It is exactly
`reg.query(sl.q.reported_attribute)`, a lazy query like any other; nothing runs
until a terminal.

```python
import sdmxlib as sl
from sdmxlib.query.accessors import q

reg.reported_attributes().filter(
    q.reported_attribute.metadata_set.targets.artefact.urn == flow_urn
).collect()
```

A value's target is not a column of the value — it lives in
`sdmx.metadata_target`, one relation away, and a set may describe many
artefacts. So the path crosses a to-many edge and cannot be *projected* as a
single cell. Collect it instead:

```python
reg.reported_attributes().select(
    path=q.reported_attribute.attribute_path,
    targets=q.reported_attribute.metadata_set.targets.artefact.id.list(),
).collect()
```

### Attribute paths

Reported attributes are stored under a dotted path, so a subtree is a prefix:

```python
reg.reported_attributes().filter(q.reported_attribute.attribute_path.str.starts_with("QUALITY.")).collect()
reg.reported_attributes().filter(q.reported_attribute.attribute_path == "CONTACT").collect()
```

### Scalar and localised values

`kind` says which column a row populated, and the two are separate
fields — the same distinction annotations make:

```python
q.reported_attribute.scalar == "national"  # plain string
q.reported_attribute.value["en"].str.contains("coverage")  # localised payload
q.reported_attribute.kind == "i18n"  # which of the two this row is
```

XHTML is reported as `i18n` with markup in it; only the MSD's declared data
type tells the two apart, which is what `collect()` surfaces as
`MetadataSection.text_type`.

### Repeated attributes

An attribute declared with `max_occurs > 1` is reported as **sibling rows**,
never as a values list. `position` (the sibling index) distinguishes them,
and it is ordered:

```python
reg.reported_attributes().filter(q.reported_attribute.attribute_path == "CONTACT").collect()  # all
reg.reported_attributes().filter(q.reported_attribute.position < 1).collect()  # first only
```

A query keyed only on `path` would silently return one row where the model
says there are two.

### Cheap versus typed

| call | returns | cost |
| --- | --- | --- |
| `ids()` | `list[str]` — the attribute paths | one statement |
| `select(...).collect()` | `polars.DataFrame` | one statement |
| `collect()` | `list[MetadataSection]` | one statement **plus** one MSD lookup per set |

`collect()` is routed through `metadata_report`, so a `MetadataSection` here
carries the same `text_type` and `is_presentational` it would there — the
typed view has exactly one implementation. Use `ids()` or `select()` when the
type information is not needed.

### Finding what has metadata at all

The other direction asks which artefacts anything has been said about. Every
artefact carries `described_by`, the metadata targets naming it:

```python
reg.query(q.dataflow).filter(q.dataflow.described_by.metadata_set.id.is_not_null()).ids()
```

Narrowing to one reported attribute is a sub-query, because a set's *values*
are reached by rooting at them:

```python
coverage = (
    reg.query(q.reported_attribute)
    .filter(q.reported_attribute.attribute_path == "QUALITY.COVERAGE")
    .select(urn=q.reported_attribute.metadata_set.urn)
)
reg.query(q.dataflow).filter(q.dataflow.described_by.metadata_set.urn.is_in(coverage)).ids()
```

For a whole subtree, match the path as a prefix — `starts_with` escapes `_`
and `%`, so `QUALITY_` does not match `QUALITY.COVERAGE` through a wildcard:

```python
under = (
    reg.query(q.reported_attribute)
    .filter(
        (q.reported_attribute.attribute_path == "QUALITY")
        | q.reported_attribute.attribute_path.str.starts_with("QUALITY.")
    )
    .select(urn=q.reported_attribute.metadata_set.urn)
)
```

---

## Limitations

- **Submit** metadata to a registry — read-only REST client.
- Fetch metadata from a live SDMX 3.0 reference-metadata REST endpoint
  via a typed builder (e.g. `reg.metadata(metadataflow).attached_to(urn)
  .fetch()`). The model and wire-format support is in place; the HTTP
  helper is deferred until validated against a real provider.
- SDMX-ML 2.1 GenericMetadata (the v2.1 envelope used a heavyweight
  `ReportStructure` apparatus that 3.0 dropped; sdmxlib targets 3.0).
