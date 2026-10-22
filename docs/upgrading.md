# Upgrading

This page covers every **breaking change** from 0.48.0, which is where the
current shape of the library starts, through the current release.
Each section states what the API is now. Where a later change superseded an
earlier one, the section gives the result, not the step in between. There are
no deprecation shims: sdmxlib is pre-stability and breaks cleanly rather than
carrying two spellings of the same idea. That means an upgrade is a
find-and-replace pass, and this page is the list.

If you only read one section, read [Registry naming](#registry-naming) and
[One `get`](#one-get) — between them they cover most call sites.

Names that left the public API entirely: `Registry`, `FederatedCatalog`,
`FailurePolicy`, `CodeQuery`, `HierarchyNodeQuery`, `Predicate`,
`CompileContext`, `AnyRef`, `sl.code`, `sl.node`, `Dataset`, `DataQuery`,
`DimFilter`, `sl.dim`, and the four alias spellings `PathQuery`, `QueryCols`,
`ArtefactQueryRow`, `QueryRowKind`.

---

## A store on another schema version is refused, not emptied

Opening a `LocalRegistry` (or `LocalRegistry.bundle`) whose store holds
artefacts on a different store schema version now raises `sl.StoreSchemaError`.
From 0.68 to 0.70.1 it dropped and rebuilt the schema instead, which emptied a
bundle built by 0.67 the moment a newer sdmxlib opened it, even only to read
(GH-57). An empty store, or a new file, is still created or migrated silently.

The store schema moved from 2.19 to 2.48 in **0.68.0**: a store or bundle
written by 0.67 or earlier has to be rebuilt from its source to be read by
0.68 or later. A cache that can be fetched again opts back into the rebuild:

```python
with (
    sl.LocalRegistry("cache.duckdb", rebuild=True) as cache,
    sl.RestRegistry(sl.Provider.ECB, store=cache) as ecb,
):
    ...
```

A bundle is never rebuilt: `bundle()` has no `rebuild=`, because its
`meta.duckdb` is the published record.

---

## Registry naming

`sl.Registry` is now `sl.LocalRegistry`, and `materialise_to=` is `store=`.

```text
# 0.48
local = sl.Registry("store.duckdb")
reg = sl.RestRegistry(sl.Provider.ECB, materialise_to=local)
```

```python
# now
with sl.LocalRegistry("store.duckdb") as local:
    sl.RestRegistry(sl.Provider.ECB, store=local)
```

`sl.Registry` sat beside `sl.RestRegistry` and implied the first was the
general case. The three types are peers — `LocalRegistry`, `RestRegistry`,
`FederatedRegistry` — and the names now say so. `store=` says *position in a
source chain* rather than a direction of travel.

---

## One `get`

`fetch()`, `resolve()` and the `GetQuery` returned by
`get(Type, agency=…, id=…)` are one method. Resolution depth is a parameter.

```text
# 0.48
dsd = reg.get(sl.DataStructure, agency="ECB", id="ECB_EXR1").fetch()
dsd = reg.get(sl.DataStructure, agency="ECB", id="ECB_EXR1").resolve()
dsd = reg.get(sl.DataStructure, agency="ECB", id="ECB_EXR1").resolve(partial=False)
```

```python
# now
urn = sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1")
dsd = reg.get(urn)  # skeleton; refs unresolved
dsd = reg.get(urn, resolve=True)  # every reference followed
dsd = reg.get(urn, resolve=True, partial=False)  # and untrimmed
dsd = reg.get(urn, resolve=[sl.Codelist])  # only refs of these types
```

`get` takes **identity** — a URN or a `Ref` — and returns the artefact or
`None`. It no longer takes a type plus keyword parts.

!!! note "`LocalRegistry.get` takes no `resolve=`; `FederatedRegistry.get` does"

    `LocalRegistry.get(urn)` returns an artefact whose references are bound
    to the store, so each one finds its target when you use it (#492; see
    [A `Ref` is frozen and finds its target on
    access](#a-ref-is-frozen-and-finds-its-target-on-access)). Depth is a
    choice about how many *requests* to make, and a local store makes none,
    so there is nothing to choose. Copying a REST call onto it raises
    `TypeError` rather than quietly ignoring the argument.

    `FederatedRegistry.get` takes both `resolve=` and `partial=` and
    **translates** them per routed source: `resolve=` is dropped for a local
    one (whose answer already satisfies it), while `partial=False` is refused
    there, because a store cannot promise untrimmed codelists. See
    [Federation](cookbook/federation.md#resolve-and-partial-through-a-federation).

### A `str` is not identity

```text
# 0.48 — worked on LocalRegistry and InMemoryRegistry, nowhere else
geo = local.get("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:CL_GEO(1.0)")
```

```python
# now — the three spellings that name an artefact
geo_urn = sl.Codelist.urn_for(agency="AG", id="CL_GEO", version="1.0")
geo = reg.get(sl.SdmxUrn.parse(str(geo_urn)))  # from a string you were handed
geo = reg.get(geo_urn)  # from a URN you built
geo = reg.get(sl.Ref[sl.Codelist].unresolved(geo_urn))  # from a Ref you traversed to
```

The two local registries end their lookup in `self._store.get(str(urn))`, and
`str` of a `str` is itself — so a bare string worked there by accident, was
never declared, and failed against `RestRegistry`. Passing one now raises
`TypeError` naming both replacements.

The same accident ran the other way: a `Ref` stringifies to
`Ref(CL [unresolved])`, so it *missed* and returned `None` — silently, on the
two registries where a string worked. Traversal code that ran against a
`RestRegistry` found nothing against a `LocalRegistry`. All four `get`s now
take a URN or a `Ref`, positional-only, and refuse everything else.

### `.urn` is a property; `urn_for` builds one

```text
# 0.48
urn = sl.Codelist.urn(agency="ECB", id="CL_FREQ")  # a classmethod builder
```

```python
# now
urn = sl.Codelist.urn_for(agency="ECB", id="CL_FREQ")
cl.urn  # the instance's own URN
```

An artefact having a `.urn` and a class building one are different operations,
and one name could not carry both. Note items are identified within their
scheme: `Code("A").urn` does not exist, because a code has no independent
identity.

---

## `None` means absent; unreachable raises

`get` returns `None` for exactly one reason: every source answered "not here".
A source that could not be reached raises `RegistryUnavailable`, naming itself
and what was being read.

```text
# 0.48 — with FailurePolicy.RETURN_NONE, these were the same outcome
result = fed.get(urn)
if result is None:
    ...  # down, or missing? unrecoverable
```

```python
# now
try:
    artefact = fed.get(urn)
except sl.RegistryUnavailable as exc:
    exc.source_id, exc.tried, exc.cause
else:
    if artefact is None:
        ...  # genuinely absent, and only that
```

**`sl.FailurePolicy` is gone.** Its stated job was deciding whether a
federation continues past a dead source, and `FederatedRegistry` routes each
URN to exactly one source by agency — so there was no next source to continue
to, and the knob only converted an error into a silence. Continuing past a dead
source is now the loop you write, which cannot mislabel anything.

A `404` is the server answering "not here", so it comes back as `None`. A
`500` is the server's own error and is raised.

---

## `latest` is a pointer

A pinned URN names an immutable artefact and caches indefinitely. `latest`
names a *pointer*, and how long it may go un-re-asked is the provider's call,
read from `Cache-Control` and `ETag`.

```python
reg.get(pinned_urn)  # cached forever; no header shortens it
reg.get(latest_urn)  # re-asked, within whatever window the provider granted
```

Nothing to change at a call site, but the behaviour differs: 0.48 served a
cached `latest` for the life of the process, so a long-running server returned
a superseded artefact indefinitely. If a provider's `no-store` is boilerplate
and you want a window anyway, `sl.RestRegistry(..., latest_ttl=300)` is a
**floor** over what the provider allows — it cannot shorten a generous one.

### A 2.1 reference without a version names 1.0

An SDMX-ML 2.1 `<Ref>` with no `version` (or, for an item, no
`maintainableParentVersion`) was read as `latest`: a pointer to whatever was
newest. Every concrete reference type in the 2.1 schema defaults both to
`1.0`, so the reference names `1.0`, and it is now read that way (#476). The
writers already agreed: `serialize` refuses to write a `latest` reference to
2.1 because a schema-following reader takes the omission for `1.0`.

No live registry is affected: none of the 279,000 non-local references in the
BIS, ECB, ESTAT, OECD and SDMX 2.1 documents the sweep downloads omits a
version.

---

## Queries

### `reg.query(T)` is retired; `sl.q` is the entry

```text
# 0.48
flows = reg.query(sl.Dataflow, agency="ECB").browse()
```

```python
# now
flows = reg.query(sl.q.dataflow).filter(sl.q.dataflow.agency == "ECB").collect()
```

`sl.q` is the accessor — `sl.q.code`, `sl.q.dsd`, `sl.q.dataflow`, `sl.q.hcode`
— and one path language runs over both a `LocalRegistry` and a `RestRegistry`.
Against REST it plans what the endpoint can answer, fetches exactly that,
materialises it, and runs your original query locally.

### The four DSLs are gone

`ArtefactQuery`, `CodeQuery`, `HierarchyNodeQuery` and `MetadataValueQuery`
have no replacement each — they have *one* replacement between them.

```text
# 0.48
cl.codes.query().where(sl.code.id.starts_with("CA"))
```

```python
# now
cl.codes.filter(sl.q.code.id.str.starts_with("CA"))
```

`sl.code` and `sl.node` (the old predicate namespaces) are gone with them.

### Projected columns are named the way you wrote them

```text
# 0.48 — and every earlier build of the path language
reg.query(sl.q.codelist).select(sl.q.codelist.id, sl.q.codelist.agency).collect()
# columns: resource_id, agency_id
```

```python
# now
reg.query(sl.q.codelist).select(sl.q.codelist.id, sl.q.codelist.agency).collect()
# columns: id, agency
```

The storage names (`resource_id`, `agency_id`, `code_id`, `artefact_type`) were
leaking out of the schema the path language exists to hide, so `df["id"]` raised
`KeyError` on a frame produced by `q.codelist.id`. They now appear only where
they should: `reg.sql()` and `sl.schema`.

**If you index frames by column name, this is the change that will bite.** It
fails at runtime rather than at type-check, because column access is
string-keyed.

Two columns projecting to one name are refused rather than renamed. Name either
side — as a keyword, or with the new `alias` on the expression:

```python
reg.query(sl.q.dataflow).select(flow=sl.q.dataflow.id, dsd=sl.q.dataflow.dsd.id)
reg.query(sl.q.dataflow).select(sl.q.dataflow.id, sl.q.dataflow.dsd.id.alias("dsd"))
```

`alias` is the one that reaches inside `list()`, whose struct fields take their
names from the columns given to it.

### An empty fan-out collects to `[]`, not `None`

```text
# 0.48 — a leaf's collected children
frame["kids"][0]    # None
```

```python
# now
frame = cl.codes.select(id=sl.q.code.id, kids=sl.q.code.children.id.list()).collect()
leaves = [row for row in frame.to_dicts() if row["kids"] == []]
# a leaf collects to [], never None
```

`len(unique()) == n_unique()` now holds without `or []` at the call site. A
row whose children all carry a null still collects to `[None]`, so "no rows"
and "rows, all null" stay distinguishable.

### A to-many projection is refused when you write it

```text
# 0.48 — returned a Select; the error arrived at collect()
frame = cl.codes.select(sl.q.code.annotations.title)
frame.collect()     # PathError, here
```

<!-- test: raises -->

```python
# now — PathError at the select() call, nothing has run
cl.codes.select(sl.q.code.annotations.title)
```

Nothing about it needed the database: the cardinality is a property of the
declared edge graph. `select()` still executes no SQL, refusing or not.

### A query is a plan, not a collection

<!-- test: raises -->

```python
# these now raise, rather than executing silently
len(cl.codes)
```

```python
# ask for what you want
cl.codes.count()
cl.codes.ids()
cl.codes.collect()
cl.codes.filter(sl.q.code.parent.id.is_null()).collect()
```

This bites where a codelist came from a `LocalRegistry`, which returns a
`LazyCodelist`. A codelist fetched over REST is eager and still supports
`len()`.

`LocalRegistry.get` now *says* so, which it did not before:

```text
reg.get(cl_urn)              # LazyCodelist | None
reg.get(cl_urn, eager=True)  # Codelist | None
reg.get(h_urn)               # LazyHierarchy | None
```

The hierarchy line is the one that was a bug rather than a nuisance.
`LazyHierarchy` has no `codes`, `tree` or `descendants` at all — it has
`nodes`, `roots()` and `narrower_transitive()` — so `reg.get(h_urn).codes`
type-checked against the declared `Hierarchy` and raised `AttributeError` at
runtime. Both halves are fixed: the declaration is now true, and the three
missing members refuse by name.

```text
>>> reg.get(h_urn).codes
AttributeError: a lazy hierarchy has no `codes` — it is a plan, not a
materialised tree. Use `.nodes` for the query over every placement, or
`.materialize().codes` for the ItemList.
```

### `n_unique` counts a null as a value

```text
# column values: [None, "W", "W"]
col.unique()  # ['W', None]
col.n_unique()  # 2   — was 1
```

`len(unique()) == n_unique()` now holds, as it does in polars, whose names
both of these are.

---

## Observations

`FederatedCatalog` is deleted. A registry answers for observations as well as
artefacts.

```text
# 0.48
catalog = sl.FederatedCatalog.from_parts(structural=reg, bindings=reg, data=store)
rows = catalog.obs(flow_urn)
```

```python
# now
rows = reg.obs(flow_urn)
```

`obs`, `dsd`, `binding_for` and `browse` are methods on the registry.
`FederatedCatalog`'s three constructor arguments were a structural reader,
artefacts the registry already held, and a store the registry already owned —
every call site in the suite passed the same object for all three.

### Results are named by component id

An observation query's columns are SDMX component ids, not the binding's
physical column names.

```python
# a binding mapping REF_AREA -> geo
reg.obs(flow_urn).frame().columns  # ['REF_AREA', 'OBS_VALUE'] — not ['geo', 'v']
```

Filters were always written in component ids; results now agree with them.

### The data plane takes no SQL text

Filtering is a predicate over named components. If you need SQL, `reg.sql()` is
the explicit escape hatch and returns a frame.

### A relation from another database is refused

```text
obs = a.obs(flow)
obs.filter(obs.col("REF_AREA").is_in(b.query(sl.q.code)...))   # CrossSourceError
```

In 0.48 this composed silently and returned **the wrong registry's answer** —
the other registry's SQL was spliced into this connection, where the same table
names resolve to this database's rows. Give both sides one database with
`store=`, or pass `.ids()` as a list.

---

## Finality is read from the version

SDMX 3.0 removed `isFinal` from every maintainable and moved the fact into the
version: `1.0.0` is final, and `1.0.0-draft` and a legacy `1.0` are not. The
model follows it (#420), so `is_final` is a property derived from `version`,
not a field you set:

```text
# 0.48
Codelist(id="CL", maintainer="AG", version="1.0", is_final=True, ...)

# now
Codelist(id="CL", maintainer="AG", version="1.0.0", ...).is_final        # True
Codelist(id="CL", maintainer="AG", version="1.0", ...).is_final          # False
```

- A 2.1 document can say something the version does not — `1.0` declared final.
  That word is kept on **`legacy_is_final`**, set by the SDMX-ML 2.1 reader only
  where it disagrees with the version (otherwise `None`), and read by the 2.1
  writer. It is what `isFinal` a 2.1 document gets; without it, the version's
  answer. The 3.0 formats and SDMX-JSON cannot carry it — ask
  `sl.unrepresentable(artefact, "3.0")`.
- The classes 3.0 introduced or made maintainable (`Hierarchy`, the maps,
  `HierarchyAssociation`, `MetadataProvisionAgreement`,
  `MetadataProviderScheme`, `MetadataSet`) and the organisation schemes, where
  2.1 fixes `isFinal` to `false`, have no `legacy_is_final`.
- `q.artefact.is_final` filters on the derived answer, so a 2.1 `1.0` declared
  final is not in it. Storage 2.25 bakes `is_final` from the version and adds a
  nullable `legacy_is_final` column.
- No format writes `isFinal` outside 2.1. SDMX-JSON had stopped already, and
  the SDMX-ML 3.0 metadata writer, which still emitted one, has too.

---

## Versions only SDMX 2.1 accepts are refused at ingest

The model holds SDMX 3.x, which has no spelling for `1.03` or `1.2.3.4`. A
registry's `add` and `add_all` now refuse an artefact at one of those versions,
or a reference naming one, with a `ValidationError` (#420). Reading and writing
such a document as 2.1, without a registry, is unchanged.

`add_all` takes a rewrite to admit them. It applies to every artefact in the
batch and every reference inside one, and returns what it changed:

```text
reg.add_all(msg.all_artefacts(), rewrite_version=sl.versions.strip_leading_zeros)  # 1.03 → 1.3
reg.add_all(msg.all_artefacts(), rewrite_version={"1.2.3.4": "1.2.4"})
# (RewrittenVersion(urn='urn:…(1.3)', original='1.03', version='1.3'),)
```

- `strip_leading_zeros` is exact: the 2.1 schema says `1.03` equals `1.3`.
  `1.2.3.4` has no equal, so only a mapping admits it.
- A rewrite that makes two artefacts one, or lands on an artefact the registry
  holds from elsewhere, is refused. So is one that leaves a reference already
  held pointing at a version that will never arrive. Ingesting the same batch
  with the same rewrite again is fine.
- `LocalRegistry` records each rewrite in `version_rewrite` (storage 2.26), for
  as long as the artefact is held.
- `parse(data, registry=reg)` takes no rewrite: parse first, then `add_all`.

---

## Drafts are left out of SDMX-ML 2.1

The other direction. 2.1 has no form for a version extension, so a draft
(`1.0.0-draft`) cannot be written there. Writing it raised; now the artefact is
left out of the 2.1 document, and so is everything in the message that would
otherwise point at it (#420). Stripping the suffix would make a draft look final,
and could collide with a real `1.0.0`.

```text
msg = StructureMessage(data_structures=[dsd_draft], dataflows=[flow], provision_agreements=[pa])
sl.serialize(msg, version="2.1")   # none of the three is written
for drop in sl.dropped(msg, "2.1"):
    print(drop)   # urn, the draft that caused it (drop.cause), and why
```

- What cascades: an artefact referencing a draft, whose reference 2.1 cannot
  name, and then anything referencing an artefact left out, repeated until
  nothing changes. A reference to an artefact never in the message is left
  alone.
- `serialize`, `to_xml` and the 2.1 writer called directly all leave out the
  same artefacts, whether or not `validate` is set. The 3.x formats and
  SDMX-JSON write drafts as before.
- `sl.unrepresentable(artefact, "2.1")` reports a draft (`version`), and a
  reference to one (`references`), for an artefact asked about on its own.
- A server answering a request for one artefact by name can find it in
  `dropped()` and refuse it, rather than send an empty document.
- A late-binding reference (`1.0+.0`) is still refused: it is not a draft. The
  reader keeps it as written; only the formats that cannot name it refuse it
  (#429).

---

## An annotation holds several URLs

SDMX 3.x lets an annotation carry several `AnnotationURL`s, each optionally
in a language, and SDMX-JSON several `links` with `hreflang` (#422). The model
held one, so the rest were reported lost on read. `Annotation.url` is replaced
by `urls: tuple[AnnotationURL, ...]`, `sl.AnnotationURL(url, lang=None)`.

**Breaking:**

```text
Annotation(url="https://x")      # before
Annotation(urls="https://x")     # after — a bare string is one unlocalised URL
ann.url                          # before
ann.urls[0].url                  # after
sl.q.annotation.url              # before: a column
sl.q.annotation.urls.url         # after: a fan-out, with .lang and .position
```

SDMX-ML 2.1 has one unlocalised URL: a 2.1 write keeps the first, and
`sl.unrepresentable(artefact, "2.1")` reports `annotations[].urls` when there
is more to lose. The store keeps them in `sdmx.annotation_url`.

SDMX-JSON spells each URL as a link, which may also carry a `urn` and a
`type`: `sl.AnnotationURL(url, lang=None, urn=None, type=None)` holds both,
and `sl.q.annotation_url` reads them as `.link_urn` and `.type`. SDMX-ML's
`AnnotationURL` has neither, so every SDMX-ML write drops them and
`sl.unrepresentable` reports `annotations[].urls[].urn` / `.type`.

---

## Text formats carry sentinel values; a time range its validity

Two more SDMX 3.x constructs the readers used to report lost (#422):

- `Facet.sentinel_values` — `sl.SentinelValue(value, name, description)`, a
  value with a special meaning (`-1` for "not applicable"). Only a text
  format may hold one: a DSD, MSD or hierarchy with one on an
  `enumeration_format` or a level's `coding_format` is refused as it is
  built (#508), as the schemas refuse it. 2.1 has none.
- `TimeRange.valid_from` / `valid_to` — a cube region time range's own
  validity, apart from its value set's. 2.1 and SDMX-JSON 1.0 have none.

The read-side register was down to two constructs — links on identifiable
artefacts, and N:M component maps — until an element census (#422) compared
every element pair the SDMX-ML 3.x schemas allow against what the writer
emits, and found more that the readers dropped *without* a row: a
StructureMap's `EpochMap`, `DatePatternMap`, `FrequencyFormatMapping` and
`FixedValueMap`, a ComponentMap's `RepresentationMap`, annotations on a
component list, group, map or region, a concept's `ISOConceptReference` and
its core representation's `EnumerationFormat`, a data key's
`Component`, and (3.0) a constraint attachment's data sources. Each now has a
row, so `msg.lost` reports it and `parse(..., lossless=True)` raises on it,
where both used to say nothing. (A nested `Level` was among them: SDMX-ML 3.x
chains a hierarchy's levels, and sdmxlib wrote and read them as siblings —
XSD-invalid output past one level. It now writes and reads the chain.)

The same census over SDMX-ML 2.1 found three readings that were wrong, not
merely incomplete, and fixes them:

- **A reference spelled `<URN>`** — 2.1 lets a reference be a `<URN>` child
  instead of a `<Ref>`, and the reader looked only for `<Ref>`. A schema-valid
  document spelling its references that way lost all of them, unreported. It
  is now read as the `<Ref>` form is.
- **A constraint on metadata** — 2.1 has one `ContentConstraint` (and one
  `AttachmentConstraint`) for data and metadata alike. One attached to an MSD
  or metadataflow, or holding a metadata key set or region, was read as a data
  constraint with its attachment emptied. It is now refused, as 3.x's
  `MetadataConstraint` is.
- **`StructureSet.related_structures`** was neither written nor read in 2.1,
  and its `isFinal` was not read.

And these 2.1 constructs now have rows: a constraint attachment's `DataSet`,
`SimpleDataSource` and `QueryableDataSource`; an attribute's `AttachmentGroup`;
a group's `AttachmentConstraint`; a measure dimension's `ConceptRole`;
`ReportingYearStartDay`; a component map's `RepresentationMapping`; a structure
set's `HybridCodelistMap`, `OrganisationSchemeMap` and `ReportingTaxonomyMap`;
a metadata target holding more than one target object; and annotations on a
component list, group, item map or target object. `ISOConceptReference` and
`EnumerationFormat` are now reported in 2.1 too, and in every version a
constraint attached to more than one artefact reports the rest.

Text with no `xml:lang` is now read as English, in every SDMX-ML version: each
schema declares `xml:lang` with `default="en"` on its text types, so
`<com:Name>Foo</com:Name>` is a valid English name. The readers dropped it, and
an artefact named only that way came back without its name. This is the
schema's own default, applied where the document is parsed. It is not a
library default language: every API that takes `lang` still requires one.

Attributes were outside both censuses until an attribute census (#422)
compared every attribute the SDMX-ML schemas allow with what the writer
emits. The readers dropped these without a row, and each now has one:

- an item scheme's `isPartial`, and a maintainable's `isExternalReference`,
  `serviceURL` and `structureURL`;
- a text, enumeration or coding format's `interval`, `timeInterval`,
  `startTime` and `endTime`;
- a contact's `id`; a 2.1 hierarchical code's `version`; a 2.1 structure map's
  `isExtension`; and `@uri` on a component list or a 2.1 target object;
- in 3.x, a measure's `usage`, a data key's and an item map's validity, a
  concept core representation's `minOccurs`/`maxOccurs`, and a metadata
  attribute usage's `uri`;
- in 3.1, `isPartialLanguage` and a data structure's `evolvingStructure`.

A flag is reported only where it says something its default does not —
`isPartial="true"`, not `"false"`.

SDMX-JSON goes further, because its reader decodes through typed structs that
skip any key they do not declare. It now walks each artefact's raw JSON beside
its structs and reports every key they skipped, at any depth, as
`<Struct>.<key>` — `CodelistJson.isPartial`, `Link.type`, a vendor's
`x-extension` — without a row having to name it. The SDMX-TWG's own 2.1
sample, which reported one lost construct, reports thirty-eight — fifty-one
before `isPartial` and `isExternalReference` were held. A census over the 2.0 and 2.1
schemas (`tests/unit/test_json_census.py`) keeps that complete: a property a
struct declares and nothing writes must be held, registered, or explained.

---

## External references and partial item schemes

Two flags every SDMX version allows, which the readers dropped, are fields
now (#422):

| SDMX | the model |
| --- | --- |
| `@isExternalReference`, `@serviceURL`, `@structureURL` on any maintainable | `is_external_reference: bool = False`, `service_url: str \| None`, `structure_url: str \| None` — as sdmx1 has them |
| `@isPartial` on an item scheme | `is_partial: bool = False` on `Codelist` (and its geographic kinds), `ConceptScheme`, `CategoryScheme`, `AgencyScheme`, `DataProviderScheme`, `MetadataProviderScheme` |

An external reference is a stub: the full artefact is at `structure_url` (an
SDMX-ML document) or `service_url` (a registry). sdmxlib keeps the flag and
the URLs and does not fetch from them.

- SDMX-ML 2.1, 3.0 and 3.1 write and read all four, as does the 3.0
  metadata message on a `MetadataSet` — whose reader now reads `uri` too.
- SDMX-JSON carries `isExternalReference` and `isPartial`, and has no
  `serviceURL` or `structureURL`; `sl.unrepresentable` reports the URLs.
- SDMX-ML 2.1 writes a map inside a `StructureSet` and a hierarchy inside a
  `HierarchicalCodelist`; each reads back with its container's (#421).
- Storage 2.33: `artefact.service_url`, `structure_url` and `is_partial`; the
  `is_external_ref` column, declared since the first schema and never
  written, is written now.

---

## A format's sequence and time facets; a concept's enumeration format and ISO reference

Four facets every SDMX format allows on a text format, which the readers
dropped, are `Facet` fields now (#422):

| SDMX | `Facet` |
| --- | --- |
| `@interval` | `interval: float \| None` — a sequence's step |
| `@timeInterval` | `time_interval: str \| None` — a time sequence's step, an `xs:duration` such as `P1D` |
| `@startTime`, `@endTime` | `start_time`, `end_time: str \| None` — SDMX time periods, kept as written |

A level's `CodingFormat` takes `interval` and none of the three time facets,
which `sl.unrepresentable` reports.

A `Concept` gains two fields:

- `enumeration_format: Facet | None` — the `<CoreRepresentation>`'s
  `<EnumerationFormat>`, constraining the codes of an enumerated core
  representation, as a component's `enumeration_format` does its local one.
  Written only beside an enumeration, the one place the schema allows it.
- `iso_concept_reference: ISOConceptReference | None` — `sl.ISOConceptReference(agency,
  concept_scheme_id, concept_id)`, the concept's ISO 11179 counterpart. Three
  strings, not a `Ref`: it points out of SDMX.

Every SDMX-ML version and SDMX-JSON write and read all of it. Storage 2.34
adds the four facet columns to every facet group (`concept`, `dsd_component`,
`metadata_attribute`, `hierarchy_level`) and its enumeration twin, and to
`concept` an enumeration-format group and `iso_agency` /
`iso_concept_scheme_id` / `iso_concept_id`.

---

## Smaller fields the attribute census found

More of what the attribute census (#422) found dropped are fields now:

| SDMX | the model | where SDMX lacks it |
| --- | --- | --- |
| `Measure/@usage` (3.x) | `Measure.usage: AttributeUsage \| None`, as an attribute's | 2.1 |
| `Key/@validFrom`, `@validTo` (3.x) | `DataKey.valid_from`, `valid_to: str \| None` | 2.1 |
| `ItemMap/@validFrom`, `@validTo` (3.x) | `ItemMap.valid_from`, `valid_to: date \| None` | 2.1 |
| `@isPartialLanguage` (3.1) | `is_partial_language: bool = False` on every maintainable | 2.1, 3.0 |
| `DataStructure/@evolvingStructure` (3.1) | `DataStructure.evolving_structure: bool = False` | 2.1, 3.0 |
| `@uri` on a component list | `DataStructure.dimension_descriptor_uri` (and `attribute_`, `measure_`), `MetadataStructure.attribute_list_uri` | 2.1 (the MSD's) |
| `@uri` on a 2.1 metadata target's kind element | `TargetObject.uri` | 3.x |
| `Contact/@id` | `Contact.id: str \| None` — a user id for the contact | — |
| `MetadataAttributeUsage/@uri` (3.x) | `MetadataAttributeUsage.uri` | 2.1 |
| `CoreRepresentation/@minOccurs`, `@maxOccurs` (3.x) | `Concept.min_occurs: int = 1`, `max_occurs: int \| None = 1` — None is unbounded | 2.1 |
| `StructureMap/@isExtension` (2.1) | `StructureMap.is_extension: bool = False` | 3.x, SDMX-JSON |
| `HierarchicalCode/@version` (2.1, informational) | `HierarchicalCode.version: str \| None` | 3.x, SDMX-JSON |

`sl.unrepresentable` reports each where the last column says. SDMX-JSON
carries all of them, the lists' uris as `describedby` links. Storage 2.35:
`artefact.is_partial_language`, a new `data_structure` table for a DSD's list
uris and `evolving_structure`, `metadata_structure.attribute_list_uri`, and
validity on `data_key` and `item_map`; `dsd_component.measure_usage`, declared
and never written, is written now. Storage 2.36 adds the second group:
`organisation_contact.contact_id`, `dsd_metadata_attribute.uri`,
`concept.min_occurs` / `max_occurs`, `structure_map.is_extension` and
`hierarchy_node.version`.

The attribute census now finds no attribute the readers drop.

A data key's `<Component>` (SDMX 3.x) is held too, as
`DataKey.components: tuple[ComponentValueSet, ...]` — the attribute or
measure values the key's data has. It is the schema type a cube region's
component uses, so it is the same class, read and written by the same code in
every format. SDMX-ML 2.1's key has none, which `sl.unrepresentable` reports.
Storage 2.37 keeps them in `data_key_component` and
`data_key_component_value`, the region selection tables' twins.

---

## A structure map's date and fixed-value maps

An SDMX 3.x `StructureMap` used to be read as its component maps alone. Now
`StructureMap` holds the other children too (#422):

- `epoch_maps: tuple[EpochMap, ...]` — maps a source component's epoch count
  (`base_period`, counted in `epoch_period: sl.EpochPeriod`) to a target
  period.
- `date_pattern_maps: tuple[DatePatternMap, ...]` — maps a source date string,
  read with `source_pattern` in `locale`, to a target period.
- `frequency_format_mappings: tuple[FrequencyFormatMapping, ...]` — the
  `frequency_id` → `date_pattern` table that a date-pattern map's
  `mapped_frequencies` can name.
- `fixed_value_maps: tuple[FixedValueMap, ...]` — a constant value given to a
  `source_id` or a `target_id` component.

The two date maps share their fields:

- `component_maps` holds the source → target id pairs;
- `frequency_dimension` with `mapped_frequencies`, or else a
  `target_frequency_id`, says what the output frequency is;
- `resolve_period: sl.ResolvePeriod | None` says which point of the period is
  used.

A `ComponentMap` gains `representation_map: Ref | None`, the representation
map that translates its values.

SDMX-ML 2.1 has none of these, and `sl.unrepresentable` reports them. SDMX-ML
3.x and SDMX-JSON read and write all of them. Storage 2.38 adds the
`epoch_map`, `date_pattern_map`, `frequency_format_mapping` and
`fixed_value_map` tables and `component_map.representation_map_urn`.

---

## Maps are N:M

SDMX 3.x lets a map have several sources and several targets, and the readers
kept only the first of each (#422). For a representation map they did this
without reporting it, and a mapping with no target value was dropped
altogether. The model now holds every one, and the names follow SDMX-ML 3.x's
elements:

```text
# before
sl.ComponentMap(source_id="FREQ", target_id="FREQ")
sl.RepresentationMap(
    source=cl_ref, target=sl.MapDataType("Integer"),
    maps=(sl.LiteralValueMap(source_value="A", target_value="1"),
          sl.RegexValueMap(source_value="^M", target_value="2")),
)
```

```python
# now
cl_ref = sl.Ref.unresolved(sl.Codelist.urn_for(agency="ESTAT", id="CL_FREQ", version="1.0"))
sl.ComponentMap(sources=("FREQ",), targets=("FREQ",))
sl.RepresentationMap(
    id="RM",
    maintainer="ESTAT",
    name={"en": "RM"},
    sources=(cl_ref,),
    targets=(sl.MapDataType("Integer"),),
    mappings=(
        sl.RepresentationMapping(sources=("A",), targets=("1",)),
        sl.RepresentationMapping(sources=(sl.SourceValue("^M", is_regex=True),), targets=("2",)),
    ),
)
```

- `ComponentMap.source_id` / `target_id` are `sources` / `targets`, tuples of
  component ids.
- `RepresentationMap.source` / `target` are `sources` / `targets`, tuples of
  endpoints — each a codelist (or value list) `Ref` or a `MapDataType`.
  `maps` is `mappings`.
- `LiteralValueMap`, `RegexValueMap` and `IndexValueMap` are one class,
  `RepresentationMapping`: `sources` (one `SourceValue` per source endpoint),
  `targets` (one value per target endpoint, possibly none), validity and
  annotations. A plain string in `sources` is a literal.
- `SourceValue(value, is_regex=False, start_index=None, end_index=None)` says
  how one source value matches. The flags sit on the value rather than the
  mapping because SDMX-ML puts them there, so one mapping can match a literal
  on one source and a pattern on the next. `is_literal` says whether neither
  flag is set.
- A `str` passed where a tuple is expected is refused rather than iterated.

SDMX-ML 2.1 maps one codelist to one codelist, code to code. `check_writable`
refuses a map it would have to cut to one pair, just as it refused a regex map
(#242): several endpoints, or an N:M component map or mapping.

The query language follows. `q.representation_map.sources` and `.targets` are
fan-outs over the endpoints. Each endpoint has a `codelist` hop, null for a
data type, plus `data_type` and `position`. They replace `source_codelist` and
`target_codelist`. `q.value_map` is `q.representation_mapping`, reached as
`q.representation_map.mappings`. Its `sources` and `targets` are stored lists,
like `q.structure_map.component_maps.sources`, so a value test is `.has()`:

```python
sl.q.representation_map.sources.codelist.id == "CL_FREQ"
sl.q.representation_map.mappings.sources.has("A")
sl.q.structure_map.component_maps.sources.has("FREQ")
```

Storage 2.39:

- `component_map.source_id` / `target_id` become the lists `sources` /
  `targets`;
- a representation map's endpoints move to `representation_map_source` and
  `representation_map_target`;
- `value_map` becomes `representation_mapping`.

The store used to drop a data-type endpoint and every regex and index mapping.
It also returned mappings in scan order, which could attach a mapping's
annotations to the wrong mapping. All of that is kept now, in order.

---

## SDMX-ML 2.1's representation mappings are held

A 2.1 `<ComponentMap>` says how its values map with a `<RepresentationMapping>`,
which every 2.1 read dropped and reported (#422). It is one of three forms, and
each is held in the 3.x shape where 3.x has one:

- **`<CodelistMap>`** names a codelist map of the same structure set. It is
  read as `ComponentMap.representation_map`, the URN of the `RepresentationMap`
  that codelist map becomes. A 2.1 write names the map again when it goes into
  the same structure set.
- **`<ValueMap>`** lists value pairs inline. 3.x keeps value pairs only in a
  maintainable map, so the reader builds one: a data-type `RepresentationMap`
  (`String` to `String`) named `<structure map id>_VALUE_MAP_<n>` after the
  component map's position, with the structure map's agency, version and name,
  and an annotation of type `"sdmxlib:value-map"`
  (`sdmxlib.model.mapping.BUILT_VALUE_MAP_TYPE`). It is in `msg.representation_maps`,
  not in the structure set's `maps`. A 2.1 write puts it back inline and does
  not write it as an artefact, so a 2.1 round trip is exact. A 3.x write
  carries it as the data-type map it is.
- **`<ToTextFormat>` and `<ToValueType>`** have no 3.x form and are held as
  `ComponentMap.to_text_format` (a `Facet`) and `to_value_type`
  (`sl.ToValueType.VALUE`, `NAME` or `DESCRIPTION`). An SDMX-ML 3.x or
  SDMX-JSON write drops them; `sl.unrepresentable(structure_map, "3.0")` says
  so. A 2.1 write refuses one without the other, or either beside a
  `representation_map`, as its schema does.

A 2.1 write still cannot name an epoch or date-pattern map, or a map under
another agency or version, and drops the reference, as `sl.unrepresentable`
reports. A data-type map without the annotation is still refused in 2.1:
only a map the reader built goes back inline.

Storage 2.44: `component_map` gains a `to_*` facet group and `to_value_type`.

---

## A rule every format shares is checked as the artefact is built

A registry's `add` and `add_all` used to store an artefact that every format
refuses, and the refusal came at its first write — for a server built on a
`LocalRegistry`, as a failed request for an artefact it was allowed to
register (#419). Every rule on which SDMX-ML 2.1, 3.0, 3.1 and SDMX-JSON agree
is now checked as the artefact is built (#492, see [Every rule runs as an
artefact is built](#every-rule-runs-as-an-artefact-is-built)), so there is
nothing of that kind left for `add` to refuse:

- a DSD component or MSD metadata attribute names a concept;
- a hierarchical code names the code it places;
- an `enumeration_format` sits beside an enumeration, has integer bounds, and
  no `decimals` or `is_multilingual` — codes are strings;
- a Level's `coding_format` declares `Alpha`, `AlphaNumeric` or `Numeric`, and
  no `decimals`, `is_multilingual` or time facets.

A rule whose answer depends on the format stays with the writer: a DSD with no
dimensions, a hierarchy with no codes, a map with no source or target, and a
provision agreement without its dataflow or provider are valid SDMX-JSON, so
they can be built and stored, and are refused when written as SDMX-ML.

**Breaking:**

```text
sl.DataStructure(..., dimensions=[sl.Dimension(id="FREQ")])      # before: built and stored
# now: ValidationError as it is built — DataStructure.dimensions[FREQ].concept
sl.DataStructure(..., dimensions=[sl.Dimension(id="FREQ", concept=cs.ref_to("FREQ"))])
```

The error names every problem in the artefact. `sl.unrepresentable` does not
report the facet fields above, because no artefact that breaks them can exist
to be written.

**Readers refuse the same artefacts.** A reader that meets one cannot build
it, so it refuses it into `StructureMessage.refused`, as it does an element
that contradicts the schema. A reference another artefact holds to it, or to
one of its items, is left unresolved, as a reference to anything the document
does not carry is. `parse` raises on a refusal unless `strict=False`.

```text
sl.parse(doc)                 # before: a DSD with no concept was read; add_all(...) then raised for the batch
# now: SdmxConformanceError (an sl.SdmxParseError) — 1 artefact refused; the rest were read
msg = sl.parse(doc, strict=False)
msg.refused                   # (Refusal(artefact_class='DataStructure', urn=..., reason='...concept — ...'),)
```

A `RestRegistry` reads on: the rest of the response is ingested, the refusals
are on `reg.refused`, and `reg.get` of a refused artefact raises
`SdmxConformanceError` with its reason. It used to fail the whole fetch — 54 of
IMF's 240 SDMX 3.0 DSDs give a time dimension a `String` format, and any
response holding one raised.

---

## Fields no SDMX format carries are gone

Two fields could be set but no SDMX format could write them, so they were
dropped on every write (#419):

- **`HierarchicalCode.name` / `.description`.** A hierarchical code is
  *identifiable*, not nameable, in SDMX-ML 2.1, 3.0, 3.1 and SDMX-JSON. The
  label a node shows is its code's. No reader ever filled these fields, so
  `q.hcode.name` was always empty on a parsed hierarchy.
- **`Agency.parent`.** `AgencyType` restricts `<Parent>` away in every format,
  so an agency scheme is flat.

**Breaking:**

```text
sl.HierarchicalCode(id="ON", name={"en": "Ontario"})   # before
sl.HierarchicalCode(id="ON", code=cl.ref_to("ON"))     # after — the name is the code's
node.name["en"]                                        # before
node.code().name["en"]                                 # after
sl.q.hcode.name["en"]                                  # before: always empty when parsed
sl.q.hcode.code.name["en"]                             # after
sl.Agency(id="D1", parent=...)                         # before
sl.q.agency.parent / .children                         # before: gone with the field
```

Storage 2.45 drops `hierarchy_node.name` / `.description` and
`agency.parent_id`.

---

## A 2.1 metadata target holds all its target objects

A 2.1 `<str:MetadataTarget>` holds one or more target objects, so a report can
describe, for example, an identifiable object within a data set. The model kept
the first and reported the rest lost (#422). `MetadataTarget` now holds them
all, in order, as `target_objects`, and each is a `TargetObject`.

**Breaking:** the six fields that flattened the one object onto the target
are gone.

```text
target.component_id            # before
target.target_objects[0].id    # after
target.kind / .object_type / .representation   # before
target.target_objects[0].kind / ...            # after
target.component_annotations / .component_uri  # before
target.target_objects[0].annotations / .uri    # after
```

A 2.1 write refuses a target with no target object, since the schema requires
one. A `ReportPeriodTarget`'s representation is now checked as the time type
2.1 gives it, where before it was refused for not being one of the four
reference types the other kinds take. The store still holds the 3.x MSD
without this 2.1 layer, as before.

---

## A constraint's keys are held in key sets

`DataConstraint.data_keys` and `AttachmentConstraint.data_keys` are now
`data_key_sets`: a tuple of `sl.DataKeySet`, each holding `is_included` and
its `keys` (#479).

```text
# before
cc = sl.DataConstraint(..., data_keys=[sl.DataKey(values=[("FREQ", "A")])])
for key in cc.data_keys: ...
```

```python
# now
keyed = sl.DataConstraint(
    id="CC",
    maintainer="AG",
    name={"en": "keyed"},
    data_key_sets=[sl.DataKeySet(is_included=False, keys=[sl.DataKey(values=[("FREQ", "A")])])],
)
ruled_out = [k.values for s in keyed.data_key_sets if not s.is_included for k in s.keys]
assert ruled_out == [(("FREQ", "A"),)]
```

Every format requires `isIncluded` on a key set, and `false` means the set's
keys are *ruled out*. The readers flattened every set into one tuple and the
writers wrote each one back as `isIncluded="true"`, so a constraint excluding
keys read as one allowing them: the opposite of the document, with nothing
lost or refused. The shape follows the information model, and pysdmx's
`KeySet` and sdmx1's `DataKeySet` hold it the same way.

- A key set with no `isIncluded` now refuses its constraint, as #474 refuses
  any other missing required attribute.
- A key set with no keys is refused by `add` and on read, because every
  format requires at least one.
- Storage is on schema 2.47: a `data_key_set` table, plus `data_key.set_idx`.

---

## A constraint attaches to several artefacts

SDMX attaches a constraint to one data provider, or to data sets, or to simple
data sources, or to one or more artefacts of a single kind — data structures,
dataflows or provision agreements — with queryable data sources beside them.
`ConstraintAttachment` held one artefact per kind, so the readers kept the
first dataflow and dropped the rest, along with every data source and SDMX-ML
2.1's data sets (#422). It now holds each alternative:

```text
# before
sl.ConstraintAttachment(dataflow=flow_ref)
```

```python
# now
flow_ref = sl.Ref.unresolved(sl.Dataflow.urn_for(agency="ESTAT", id="CPI", version="1.0"))
sl.ConstraintAttachment(
    dataflows=(flow_ref,),
    queryable_data_sources=(
        sl.QueryableDataSource(
            data_url="https://example.org/rest/data",
            is_rest_datasource=True,
            is_web_service_datasource=False,
        ),
    ),
)
```

- `dataflow` / `data_structure` / `provision_agreement` are `dataflows` /
  `data_structures` / `provision_agreements`, tuples of `Ref`s in document
  order. `data_provider` stays one `Ref`.
- `simple_data_sources` (URLs), `queryable_data_sources`
  (`sl.QueryableDataSource`) and `data_sets` (`sl.DataSetReference`, a
  provider and an id) are new.
- `alternatives` names the alternatives an attachment sets. A constraint
  whose attachment sets more than one, or queryable data sources beside no
  artefacts, is refused with `ValidationError` as it is built.
- Writing refuses what the target has no element for: data sets outside
  SDMX-ML 2.1; data sources in SDMX-ML 3.1 and SDMX-JSON, whose output must
  validate against SDMX-JSON 2.1 too; and more than one data set or simple
  data source on a 2.1 `ContentConstraint` or in SDMX-JSON 1.0. A 2.1
  `AttachmentConstraint` has no provider and no queryable sources.
- SDMX-JSON 1.0 wrote a provider as `dataProviders`, a key 1.0 does not have.
  It is `dataProvider` now.

In the query language `q.data_constraint.dataflow`, `.dsd` and
`.provision_agreement` are the fan-outs `.dataflows`, `.dsds` and
`.provision_agreements`, each reaching the artefact whole. `.data_provider`
stays a to-one hop, and `q.dataflow.constraints` still reaches a flow's
constraints:

```python
sl.q.data_constraint.dataflows.id == "CPI"
sl.q.data_constraint.dataflows.dsd.id == "DSD_CPI"
```

Storage 2.40 moves the three kinds out of `data_constraint`'s columns into
`data_constraint_dataflow`, `data_constraint_dsd` and
`data_constraint_provision`, keyed by position. The data sets and queryable
sources get tables of their own, and `data_constraint` gains
`simple_data_sources`.

---

## SDMX-ML 2.1's own DSD constructs are held

Four things only a 2.1 DSD can say were dropped on read, each reported as a
loss (#422). They are held now:

- **An attribute's attachment groups.** `Attribute.attachment_groups`, a
  tuple of group ids beside `related_dimensions`.
- **A group defined by an attachment constraint.** `Group.attachment_constraint`,
  a `Ref`, used instead of `dimension_ids`. A DSD holding a group with both
  is refused as it is built.
- **A measure dimension's concept roles.** `MeasureDimension.concept_role`,
  as the other components have.
- **The reporting year's start day.** It is an `Attribute` by the id 2.1
  reserves for it, `REPORTING_YEAR_START_DAY`. The 2.1 writer writes that
  attribute as `<ReportingYearStartDay>`, and refuses one whose representation
  is not a `MonthDay` text format or which has concept roles. SDMX 3.x has no
  such element, so there it is an ordinary attribute by the same id.

```python
from sdmxlib.model.datastructure import REPORTING_YEAR_START_DAY

sl.Attribute(id=REPORTING_YEAR_START_DAY, representation=sl.Facet(type="MonthDay"), attachment=sl.AttachmentLevel.DATASET)
sl.Attribute(id="UNIT", attachment=sl.AttachmentLevel.SERIES, related_dimensions=["FREQ"], attachment_groups=("SIBLING",))
```

Writing attachment groups or a constraint-defined group to SDMX-ML 3.x or
SDMX-JSON is refused, since neither has a form for them. Storage 2.41 adds
`dsd_component.attachment_groups` and `dsd_group.attachment_constraint_urn`.

---

## Links are held

SDMX 3.x lets every identifiable carry `<com:Link>` elements — "a link to the
metadata report can be dynamically inserted in the structure metadata" — and
every reader dropped them, reported as a loss (#422). They are `links` now,
beside each `uri`: a tuple of `sl.Link(rel, url, urn=None, type=None)` on
every maintainable, item and component 3.x lets carry one, and on a DSD's and
an MSD's component lists (`dimension_descriptor_links`, …,
`attribute_list_links`).

```python
sl.Codelist(
    id="CL_FREQ", maintainer="AG", name={"en": "Frequency"},
    links=(
        sl.Link(
            rel="metadata",
            url="https://host/sdmx/v2/metadata/metadataset/AG/MS1/1.0",
            urn="urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataSet=AG:MS1(1.0)",
        ),
    ),
)
```

- **`urn` is text, not a `Ref`.** The schema types it `anyURI`, "an SDMX
  registry urn of the object being linked (if applicable)", so it is a
  pointer and not an edge of the graph.
- **SDMX-ML 2.1 has no links**, so a 2.1 write drops them;
  `sl.unrepresentable(artefact, "2.1")` says so first.
- **SDMX-JSON** spells `url` as `href`. It already uses `links` for the
  artefact's own URN (`rel="self"`) and its `uri` (the first
  `rel="describedby"`), so writing a `self` link, or a `describedby` one with
  no `uri` ahead of it, is refused — it would read back as something else. So
  is a link `urn` that is not an SDMX URN, which its schema requires. A
  `title` has no field and is reported as a read loss.
- **A link's language** is SDMX-JSON's `hreflang`, held as `Link.hreflang`.
  SDMX-ML's link has no language, so an SDMX-ML 3.x write drops it, and
  `sl.unrepresentable(artefact, "3.0")` says so.
- **A uri's own link can say more.** When the `describedby` link that is the
  `uri` also carries a `urn`, `type` or `hreflang`, it is held twice: as the
  `uri`, and as a `Link` in its place among `links`. An SDMX-JSON write puts
  it back once; an SDMX-ML write gives `@uri` and a `<com:Link>`.
- **A metadata attribute usage** may carry one link in SDMX-ML 3.x, which the
  writer checks.
- **`metadata_links=`** on the SDMX-ML 3.0 and SDMX-JSON writers appends its
  `rel="metadata"` back-links after the links an artefact holds, and skips a
  report one of them already points at.
- **A MetadataSet's links** go through both metadata formats, and SDMX-JSON
  metadata now carries its `uri` too.

Storage 2.42 adds a `link` table, keyed by owner URN as `annotation` is. A
`get()` reads an artefact's own links with its spine row, and its items' only
when it has some, so an artefact without links costs what it did.

---

## A component may be enumerated by a ValueList

SDMX 3.x's `ValueList` is modelled (#422) as `sl.ValueList`, a maintainable of
`sl.ValueItem` values — a codelist without hierarchy or item identity, whose
values may be any string. It lives in `msg.value_lists`, and a component's
representation may reference one wherever it may a codelist, so the
representation is now `Ref[Codelist | ValueList] | Facet | None`.

**Breaking:** `component.representation()` can return a `ValueList`, so code
that unwrapped it as a codelist must narrow or use the accessors:

```text
dim.representation().codes      # before: always a Codelist
dim.codelist.codes               # after: TypeError naming value_list if it is one
```

- `dim.code_ids()`, `dim.resolved_enumeration` and `dsd.code_map(lang=)`
  answer for either kind, so labels and the polars bridge's value checks
  work on a value-list component. `sl.EnumerationLike` is the protocol the
  two share.
- `dim.value_list` is the resolved `ValueList`; `dim.codelist` and
  `dim.resolved_codelist` stay codelist-only (TypeError and None for a value
  list), as does `dim.codes`.
- SDMX-ML 2.1 has no value list: a 2.1 write leaves it out, with everything
  that references it, and `sl.dropped(msg, "2.1")` names each and why.
- SDMX-JSON writes a value list without its `rel="self"` link — the 2.0
  schema misspells the class in its generic URN pattern — and refuses a
  reference to one anywhere but an enumeration, for the same reason.
- `Concept.core_representation` now converts an `SdmxUrn` or an artefact to
  a `Ref`, and refuses URN text, as every other reference field does.

---

## Geographic codelists are read, not refused

SDMX 3.x's `GeographicCodelist` and `GeoGridCodelist` are modelled (#422) —
as `sl.GeographicCodelist` and `sl.GeoGridCodelist`, subclasses of
`sl.Codelist`. Their URN class is `Codelist`, so they land in `msg.codelists`
beside the plain ones and any `Ref[Codelist]` can name one; their codes are
`sl.GeoFeatureSetCode` (with its `value`) and `sl.GeoGridCode` (with its
`geo_cell`), and a grid codelist has its `grid_definition`. A document holding
one used to raise; it now reads.

```text
for cl in msg.codelists:
    if isinstance(cl, sl.GeoGridCodelist):
        print(cl.grid_definition, {c.id: c.geo_cell for c in cl.codes if isinstance(c, sl.GeoGridCode)})
```

`cl.codes` stays `ItemList[Code]`: a geographic codelist checks that each code
is of its class as it is built (and on `attrs.evolve`), so narrow with
`isinstance` to read a code's geographic field. SDMX-ML 2.1 has neither class
and writes each as a plain codelist; `sl.unrepresentable(cl, "2.1")` lists
what that loses.

---

## What a read cannot hold is reported

SDMX 3.x carries constructs the model has no field for yet, and the SDMX-ML
readers used to drop them silently (#422). Now:

- **Inside an artefact** (a 2.1 `HybridCodelistMap`, a 2.1 metadata
  target's second target object, …):
  the artefact
  is built without it and the loss is recorded on `msg.lost`, one
  `sl.ReadGap` per artefact and construct. The read succeeds;
  `parse(..., lossless=True)` raises `sl.LossyReadError` instead.
- **A whole artefact the model has no class for** (`MetadataConstraint`,
  `Process`, the VTL schemes, …)
  is a refusal, like an artefact that contradicts the schema. So by default
  `parse` raises `SdmxConformanceError` naming it; `strict=False` reads the
  rest, with the refusal in `msg.refused`. Before, it was skipped.
- **An element the schema forbids** is refused the same way, and that now
  includes one a restriction prohibits with `maxOccurs="0"`. In SDMX-ML 3.x,
  a DSD's `<str:MetadataAttributeUsage>` may not carry a `ConceptIdentity` or
  `LocalRepresentation`; one that does was read past and is now refused.
- **An attribute the schema forbids** on an element is refused the same way
  (#446). A misspelt optional attribute, such as `validfrom` for `validFrom`,
  was read past and its value lost. So was one a version prohibits, such as
  `version` and `isFinal` on a 3.0 organisation scheme. `xsi:` attributes are
  always allowed. In the cached registry documents the only such attribute is
  on Eurostat's 3.0 `AgencyScheme`, which its `<str:Name>` already refused. The
  check adds about 5% to the parse of a large codelist document.
- **The frame around the artefacts is checked too** (#447): the message
  header, and the containers between `mes:Structures` and the artefacts. A
  stray element there is inside no artefact, so it is refused by its place,
  e.g. `mes:Structures/str:Codelists/str:Bogus[1]`. An unknown container, or
  one carrying an attribute it does not allow, is refused whole. Before, these
  vanished, or were refused with the wrong reason: a `ConceptScheme` misplaced
  among the codelists was blamed as "a reader defect". As with any refusal,
  `parse` raises and `strict=False` reads the rest. None of the 167 cached
  registry documents is affected. The header is read too, into
  `msg.header` (#480; see [A message's header is read and
  written](#a-messages-header-is-read-and-written)).

```text
msg = sl.parse(data)
for gap in msg.lost:
    print(gap)   # urn: str:StructureSet/str:HybridCodelistMap x1 lost — the model has no HybridCodelistMap; …
```

- The account is of the document: every artefact in it comes back built or
  refused, so a reader that skips one is caught too.
- **SDMX-JSON works its losses out when you first read `msg.lost`**, not
  during the read, because finding them walks the whole document and most
  callers never ask (#461). Until then the message holds the document's
  bytes, at most one copy of what you parsed (#473). Read `msg.lost`, or
  parse with `lossless=True`, to release them; a process that keeps many
  parsed JSON messages alive and never reads `lost` keeps their documents
  too.
- SDMX-ML 3.0/3.1 and SDMX-JSON report losses (2.1 only a region value's
  `cascadeValues`, the one it can carry), and every format refuses unmodelled
  artefacts. SDMX-JSON finds those as sections of `data` it has no reader
  for, so an artefact kind nobody listed is refused too.
- A cube region value set saying `include="false"` (exclude these values)
  used to read as *included* — the opposite of the document. It is held now:
  `KeyValue.include` and `ComponentValueSet.include`, default true, read and
  written by every SDMX-ML version and SDMX-JSON 2.0, and honoured by
  `CubeRegion.allows`. SDMX-JSON 1.0 has no form for it and refuses one.
  Storage 2.28 keeps it on `region_selection` and on each value row, so
  `q.key_value.is_included` tells an excluded value from an allowed one —
  `key_values.value == "EU"` alone also finds EU listed as an exclusion.
- SDMX-JSON's object form of a region value (`{"value": "A", …}`), which the
  schema allows beside a bare string, failed the whole document's decode.

### A region value is a `RegionValue`

`KeyValue.values` and `ComponentValueSet.values` hold `sl.RegionValue`s, not
strings (#422): the value, and its `cascade` (`CascadeSelection`, default
`SELF_ONLY`, the code alone; never `None`), `lang`, `valid_from` and
`valid_to`. A bare string still builds one, unqualified.

```text
# 0.48
"EU" in kv.values

# now
"EU" in kv.codes()
KeyValue(dimension_id="REF_AREA", values=["EU", RegionValue("XX", cascade=CascadeSelection.INCLUDE_DESCENDANTS)])
```

- A value set also carries `remove_prefix`, and a dimension's set
  `valid_from` / `valid_to` (SDMX-ML 3.x and SDMX-JSON). Periods stay text,
  as `StandardTimePeriodType` allows `2020-Q1`.
- What SDMX 3.x prohibits is refused with `ValidationError` as the
  constraint is built: a language on a dimension's value, validity on a
  component's value, a value listed twice.
- SDMX-ML 2.1 has a boolean `cascadeValues` and nothing else. It refuses
  `EXCLUDE_ROOT`, and `sl.unrepresentable(constraint, "2.1")` names the rest.
  SDMX-JSON 1.0 refuses any qualifier.
- `CubeRegion.allows` does not expand a cascade: that needs the codelist.
- Storage 2.29; `q.key_value.cascade` reads a value's cascade.

### A DSD holds SDMX 3.0's component additions

Four of those losses are now fields (#422):

| SDMX 3.0 | the model |
| --- | --- |
| `Attribute/MeasureRelationship` | `Attribute.related_measures: tuple[str, ...] \| None` — None is all measures |
| `AttributeRelationship/Dimension/@optional` | `optional_dimensions: frozenset[str]`, on `Attribute` and `MetadataAttributeUsage` |
| `LocalRepresentation/@minOccurs`, `@maxOccurs` | `min_occurs: int = 1`, `max_occurs: int \| None = 1` on `Attribute` and `Measure` — None is unbounded, as on `MetadataAttribute` |
| `<str:Metadata>`, `MetadataAttributeUsage` | `DataStructure.metadata: Ref[MetadataStructure] \| None`, `DataStructure.metadata_attributes: ItemList[MetadataAttributeUsage]` |

- `dsd.attributes` stays data attributes only. A usage has no id of its own
  (the schema prohibits one); its `id` is the MSD attribute's.
- A dimension's `@position` is not stored: order is list order. Positions
  that rise with it — counted from 1 or from 0, with or without a number
  skipped — read as before; one that contradicts it (a dimension saying it
  comes before one it follows, or two claiming one place) refuses the DSD,
  since the schema requires consistency. A gap was refused too until #449,
  which cost 802 of Eurostat's SDMX 3.0 DSDs. The
  SDMX-JSON writer now counts from 0, as that schema says; it counted from 1.
- SDMX-ML 3.0/3.1 and SDMX-JSON read and write all of it. SDMX-ML 2.1 has
  no form for any of it: `sl.unrepresentable(dsd, "2.1")` names what it drops.
- Storage 2.27: four `dsd_component` columns, a `dsd_metadata_attribute`
  table, and the MSD as an `artefact_ref` with role `metadata`.

The annotations of a DSD's component lists, its groups and an MSD's attribute
list are fields now too (#422). The lists are not modelled — `dsd.dimensions`
stays a plain list — so their annotations sit on the structure, named for
the SDMX class each list is:

| SDMX | the model |
| --- | --- |
| `DimensionList` (DimensionDescriptor) | `DataStructure.dimension_descriptor_annotations` |
| `AttributeList` (AttributeDescriptor) | `DataStructure.attribute_descriptor_annotations` |
| `MeasureList` (MeasureDescriptor) | `DataStructure.measure_descriptor_annotations` |
| `Group` | `Group.annotations` |
| `MetadataAttributeList` | `MetadataStructure.attribute_list_annotations` |
| a 2.1 target object (`IdentifiableObjectTarget`, …) | `TargetObject.annotations`, apart from the target's own |

Every format writes and reads them, and the store keeps them under each
list's own URN — `…DimensionDescriptor=AG:DSD(1.0).DimensionDescriptor` —
with no schema change. SDMX-ML 2.1 has no MSD attribute list, which
`sl.unrepresentable(msd, "2.1")` reports.

So are the annotations of what has no URN at all: `ItemMap.annotations`,
`ComponentMap.annotations`, and `annotations` on each `RepresentationMapping`,
on `CubeRegion` and on `DataKey`. The store keys each by its artefact's URN and
its place in it — `…RepresentationMap=AG:RM(1.0)#mappings[0]` — in the same
annotation table. SDMX-ML
2.1 cannot annotate a cube region or a data key, which `sl.unrepresentable`
reports. No annotation is reported lost any more.

---

## What a read assumes is reported; what it cannot assume is refused

The SDMX-ML readers filled in a schema-required construct that a document
omitted, and the artefact then held that value as though the document had
stated it (#474). There are now two outcomes.

- **A value worth assuming** is kept and recorded on `msg.assumed`, one
  `sl.ReadAssumption` per artefact and construct. Examples: an attribute with
  no `AttributeRelationship` (read as attached to the dataset, the tolerance
  FMR and IMF documents need), a 3.0 `DataConstraint` with no `role`, an
  epoch map with no `epochPeriod`, a hierarchy with no `hasFormalLevels`, and
  a 3.x artefact with no `version`, which 3.x reads as un-versioned and the
  model holds as `1.0`. `parse(..., lossless=True)` raises `sl.LossyReadError`
  on assumptions as well as losses, and the error carries both.
- **A value not worth assuming** refuses the artefact, so `parse` raises
  `SdmxConformanceError` by default and `strict=False` keeps the rest. Before,
  the artefact was built with `""`. This covers a link with no `rel` or `url`,
  a sentinel or geographic code with no `value`, a 2.1 reference period with
  no start or end, an epoch or date-pattern map missing its required
  attributes, and a 2.1 value mapping with no source or target. It also
  covers every required `@id`: on an item, a level, a group, a map, a cube
  region value, or a 2.1 metadata target.
- **A maintainable with no `@id` or `@agencyID`** used to fail the whole
  document. Now only that artefact is refused.
- **A 2.1 `<Ref>` with no `@id` or `@agencyID`** used to vanish: the
  reference was read as absent, so a dataflow came back with no structure.
  The artefact holding it is now refused.

```text
msg = sl.parse(data)
for assumption in msg.assumed:
    print(assumption)   # urn: str:Attribute/str:AttributeRelationship x3 missing, assumed attachment DATASET — …
```

A value the schema itself defaults or fixes, such as `isFinal="false"` or
2.1's `version="1.0"`, is the standard's own answer and is never reported.

### SDMX-JSON

The SDMX-JSON reader now follows the same rules, and the 2.0 schema decides
every case:

- **One malformed artefact no longer fails the document.** An entry missing
  a key the schema requires, or carrying one of the wrong type, used to stop
  the whole read with msgspec's error. Now that entry alone is refused,
  named by the id, agency and version it states, and the rest is read.
  A document with nothing wrong decodes in one pass, as before.
- **A schema rule the reader skipped now refuses the artefact.** Examples:
  a link with no `rel`, or with neither `href` nor `urn`; a representation
  with neither an enumeration nor a format; an attribute relationship, a
  constraint attachment, a time range, a date map's frequency, a fixed-value
  map or a representation map's source that names none of its alternatives;
  a data key set with no `keys`; a DSD with no `dimensionList`.
- **What is assumed is recorded**, as in SDMX-ML: an attribute or metadata
  attribute usage with no `attributeRelationship`, an epoch map with no
  `epochPeriod`, and an artefact with no `version`. An organisation scheme
  without a version assumes nothing, because the SDMX 3.0 model does not
  version it.
- **Conformant documents the reader refused are now read.** It required a
  `version` on every artefact, a `position` on every dimension, and a
  `metadataAttributeList` on every MSD; the schema makes all three optional.
  It still refuses a date map or frequency-format mapping with no `id`, and
  an item map with no `targetValue`: SDMX-JSON 2.0 leaves them optional, but
  the information model makes them mandatory.

### A component without an id takes its concept's

A component's id is optional in SDMX-ML 2.1, 3.0, 3.1 and SDMX-JSON. The
schema documents the default: "if this identifier is not supplied, then it is
assumed to be the same as the identifier of the concept referenced from the
concept identity". The readers read the omission as `""`, so a conformant DSD
or MSD was refused for an empty id, or kept with one (2.1's measure dimension).
Now a dimension, attribute, measure, measure dimension or metadata attribute
without an `id` takes the item id of its `ConceptIdentity` (#477). This is the
standard's answer, not an assumption, so nothing is recorded on
`msg.assumed`. One with neither an id nor a concept refuses its artefact.

A 3.x measure without an id used to read as `OBS_VALUE`, which is 2.1's fixed
id for the primary measure. 3.x fixes no measure id, so the measure now takes
its concept's id too.

---

## Writers

The SDMX-CSV and SDMX-JSON writers take the structure, not a binding.

```text
# 0.48
write_sdmx_csv(batches, binding, structure_id=...)
write_sdmx_json_data(batches, binding, dsd, ...)

# now
write_sdmx_csv(batches, dsd, structure_id=...)
write_sdmx_json_data(batches, dsd, dataflow_ref, ...)
```

They read column order off the binding's *physical* names while an observation
query projects by component id, so any renamed binding raised `KeyError`. The
structure is now the single source of both.

### A single measure is written to 2.1 as `OBS_VALUE`

SDMX-ML 2.1 pins the primary measure id to `OBS_VALUE`; 3.x lets a measure be
named for what it measures. A 3.x DSD with one measure named anything else was
refused by the 2.1 writer, and is now written with that measure renamed
`OBS_VALUE`, keeping its concept and representation (#421). Read back, the id
is `OBS_VALUE`; `sl.unrepresentable(dsd, "2.1")` names `measures[].id` as the
loss. A DSD with several measures is still refused: 2.1 has no form for it.

### An extending codelist is written to 2.1 with what it inherits

SDMX-ML 2.1 has no codelist extension, and the 2.1 writer used to write an
extending codelist with only its own codes. It now writes the codes it
inherits too (#421), and refuses one whose extended codelists it cannot find
— a reference must be resolved, or the codelist must be in the same message.
`sl.unrepresentable(codelist, "2.1")` names `extensions` as what is lost.

The same is available on the model: `codelist.apply_extensions(available)`
returns the codelist the 3.x document means — its own codes first, then each
extension's selection (ids, `%` wildcards, cascade) behind its prefix, a
later extension winning a clash — with no extensions left.
`unresolved_extensions(codelist, available)` names what cannot be found.

### A 2.1 hierarchy is named for its codelist

SDMX 3.0 made `Hierarchy` maintainable and removed the `HierarchicalCodelist`
around it. A 2.1 hierarchy's id is unique only inside its codelist, and kept
bare it collided: OECD names 210 hierarchies `H`, one per codelist, and they
read as one URN, so the store kept one and a 3.0 write was schema-invalid.

The 2.1 reader now names each hierarchy as the Fusion Metadata Registry does,
`<hierarchy>@<codelist>` — `H` in `HCL_AGE` is `H@HCL_AGE`, which is the form
ECB and BIS already serve (#421). A hierarchy whose id is its codelist's keeps
it. Each hierarchy also takes its codelist's validity and external-reference
attributes, which 3.0 made its own.

- A codelist that only wraps one hierarchy — the same id, name, description
  and annotations, and nothing else — is read as the hierarchy alone. That is
  what every 2.1 writer puts around a standalone hierarchy, so one written to
  2.1 now reads back as itself, without a `HierarchicalCodelist` beside it.
- SDMX-ML 3.x and SDMX-JSON write a message's hierarchies and leave its
  `HierarchicalCodelist`s out, where they used to refuse the message.
  SDMX-ML 2.1 leaves a `HierarchyAssociation` out the same way.
  `sl.dropped(msg, version)` names what was left out.
- The 2.1 writer writes `H@HCL_AGE` as `H` inside `HCL_AGE`. It refuses a
  hierarchy that a 2.1 reader would give back different: one whose id is not
  `<id>@<codelist>`, or whose agency, version, validity or external reference
  is not its codelist's.

### A constraint's release calendar is a gap in 3.1, not a refusal

SDMX 3.1 removed `<ReleaseCalendar>` from `DataConstraint`. A 3.1 write used to
refuse a constraint carrying one; it now writes the constraint without it, and
`sl.unrepresentable(constraint, "3.1")` names `release_calendar` — as SDMX-JSON
always did (#421). An `Actual` constraint is written to 3.1 as an
`AvailabilityConstraint` when it fits that shape, and refused when it does
not: without its role it would read back as `Allowed`. See [An Actual
constraint is written to 3.1 as an
AvailabilityConstraint](#an-actual-constraint-is-written-to-31-as-an-availabilityconstraint).

An `AttachmentConstraint`, which SDMX 3.0 removed, is still written to 3.x and
SDMX-JSON as the `Allowed` `DataConstraint` it equals, and reads back as one.
`sl.unrepresentable(ac, "3.0")` now says so.

### A 3.x metadata structure is written to 2.1

SDMX-ML 2.1 requires every metadata structure to carry a `MetadataTarget` and a
`ReportStructure`, the reporting layer SDMX 3.0 removed. A 3.x MSD has neither,
and a 2.1 write refused it (#245). It now writes one of each: a target of any
object (`IdentifiableObjectTarget objectType="Any"`) and a report of every
attribute, both carrying an `sdmxlib:synthetic-report-layer` annotation. The
2.1 reader drops them again, so 3.x → 2.1 → 3.x returns the MSD (#421).

An MSD with its own layer is written with it, as before. One with half a layer
— targets and no report, or the reverse — is still refused, as is one with no
attributes, which a report cannot hold.

### A 2.1 metadataflow is given the targets 3.x requires

SDMX 3.x requires a metadataflow to name at least one target: what the metadata
reported against it may describe. SDMX-ML 2.1 puts that in the metadata
structure instead, so a flow read from 2.1 has none, and a 3.x or SDMX-JSON
write refused it. The writers now derive the targets from the structure's
`IdentifiableObjectTarget`s, one wildcard URN per class: `Dataflow` gives
`urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=*:*(*)`, and an item class
such as `Code` gives `…codelist.Code=*:*(*).*` (#421).

- `mdf.with_derived_targets(msds)` does the same by hand; the structure comes
  from the resolved reference, or else from `msds`. `mdf.target_misfits(msds)`
  says why it cannot.
- `sl.unrepresentable(mdf, "3.0")` reports the derivation, since the flow reads
  back with targets it did not have.
- Still refused: a flow whose structure is neither resolved nor in the message,
  one whose structure has no `IdentifiableObjectTarget`, and one whose target
  type no 3.x URN can name, such as `Any`.
- SDMX-JSON now writes and reads a metadataflow's `targets`. They were lost
  silently before.

### Metadata providers and their agreements are written to 2.1

SDMX-ML 2.1 has no `MetadataProviderScheme` and no
`MetadataProvisionAgreement`, and a 2.1 write refused a message holding either.
2.1 provisioned reference metadata with a plain `ProvisionAgreement` over a
metadataflow that names a data provider, so that is how both are now written
(#421):

- An agency's metadata providers go into its `DATA_PROVIDERS` scheme, the only
  id and version 2.1 allows a provider scheme. If the agency has no data
  providers, the whole scheme is the metadata provider scheme and is marked
  `sdmxlib:metadata-provider-scheme`. Otherwise each metadata provider is added
  to it, marked `sdmxlib:metadata-provider`, and the scheme keeps the metadata
  scheme's name in an `sdmxlib:metadata-providers-merged` annotation.
- Each `MetadataProvisionAgreement` becomes a `ProvisionAgreement` over its
  metadataflow, naming its provider as the data provider with the same id. Its
  `targets` are lost, since 2.1 has nowhere for them; `sl.unrepresentable(mpa,
  "2.1")` says so.
- The 2.1 reader undoes both, so 3.x → 2.1 → 3.x returns the same schemes and
  agreements.
- Still refused: a metadata provider whose id the agency's data provider scheme
  already uses; a metadata provider scheme whose description, annotations,
  validity, uri or external-reference attributes differ from that data scheme's
  (they share one 2.1 element); and an agreement with the same agency, id and
  version as a `ProvisionAgreement` in the message.

A `ProvisionAgreement` read from any 2.1 document whose `StructureUsage` is a
metadataflow is now a `MetadataProvisionAgreement`, with its provider read as
the `MetadataProvider` of the same agency and id. It used to be a
`ProvisionAgreement` whose `dataflow` held a metadataflow's URN.

### `uri` is a query path

Every relation whose table keeps a `uri` now has a `uri` path (#444): every
maintainable (`q.codelist.uri`, `q.dsd.uri`, …) and the items and components
`q.code`, `q.concept`, `q.category`, `q.agency`, `q.data_provider`,
`q.metadata_provider`, `q.component`, `q.group`, `q.hcode` (and its
`level`), and `q.metadata_attribute`. It filters and selects like any other
string column. For example, `reg.query(sl.q.code).filter(sl.q.code.uri ==
"https://…")` replaces collecting the codes and filtering in Python.

### Organisation scheme maps are modelled

`sl.OrganisationSchemeMap` maps one organisation scheme's organisations to
another's, as `ConceptSchemeMap` does for concepts: `source` and `target`
references and `maps` of `ItemMap` pairs (#448). Its ends may be schemes of
different kinds, for example agencies onto data providers.

- **Read and written** in SDMX-ML 2.1 (inside a `StructureSet`, named
  `<map>@<set>` like the other maps), 3.0, 3.1 and SDMX-JSON, in the new
  `StructureMessage.organisation_scheme_maps`.
- **Before:** the 3.x and JSON readers refused one, and 2.1 reported it lost.
- **Stored** in the new `organisation_scheme_map` table (schema 2.46). Its pairs
  share `item_map` with the other scheme maps.
- **Queried** with `sl.q.organisation_scheme_map`. Its `source` and `target`
  reach the generic maintainable, since an end may be any scheme kind:
  `sl.q.organisation_scheme_map.source.id == "AGENCIES"`.

### A wildcarded extension member is spelled out in SDMX-JSON

In SDMX-ML 3.x a codelist extension may select codes by pattern (`A%`). SDMX-JSON's
member value is a plain id, and the JSON writer used to write the pattern anyway,
which the schema rejects. It now writes the codes the pattern matches, each with
the member's cascade (#451). `cl.expand_wildcards(codelists)` does the same by
hand.

- The extended codelist must be resolved or in the message, and the pattern must
  match at least one code. Otherwise the write is refused, and
  `wildcard_misfits(cl, codelists)` says why.
- A code added to the extended codelist later is not picked up by the written
  selection. `sl.unrepresentable(cl, "json")` reports this.

### An attribute's usage and relationship

Three changes make every attribute convert across SDMX-ML 2.1, 3.0, 3.1 and
SDMX-JSON (#421):

- `Attribute.usage` and `Measure.usage` default to `AttributeUsage.OPTIONAL`
  and are never `None`, as SDMX 3.x's schema defaults `@usage`. Passing
  `usage=None` gives OPTIONAL, and a 3.x document that omits the attribute
  reads as OPTIONAL. Because that is the schema default, the writers leave
  it out (see [Writers leave out what a schema
  defaults](#writers-leave-out-what-a-schema-defaults-and-the-model-holds-what-it-means)).
  Code checking `usage is None` should check for `AttributeUsage.OPTIONAL`.
- An attribute's `attachment` (and a DSD's metadata attribute usage's) is
  required: there is no default, and `None` is refused as the attribute is
  built (#508), since every format requires a relationship. Every writer used
  to write a missing one as attached to the data set; then it was refused on
  write, after a store had accepted it. Set `AttachmentLevel.DATASET` for that
  meaning. A store row written without one is reported in `refused`.
- Every reader now reads an attribute whose `AttributeRelationship` is missing
  as `AttachmentLevel.DATASET`, an attribute that varies with nothing. The
  schemas require the element, but FMR omits it: IMF serves 16 attributes and
  3,047 metadata attribute usages without one.
- A 2.1 attribute's attachment groups are left out of SDMX-ML 3.x and SDMX-JSON
  as a declared gap, which `sl.unrepresentable(dsd, "3.0")` reports. The
  attribute keeps its dimensions. Before, the whole message was refused.

### An Actual constraint is written to 3.1 as an AvailabilityConstraint

SDMX 3.1 removed the role from `DataConstraint` and moved Actual content to a
type of its own, `AvailabilityConstraint`. It is not a maintainable artefact —
no id, agency, version or name — but one attachment, one included cube region
and optional series and observation counts: the answer to an availability
query (#421).

- `sl.AvailabilityConstraint` models it, in the new
  `StructureMessage.availability_constraints`. It has no URN, so it is in no
  artefact list and not in `all_artefacts()`; the SDMX-ML 3.1 reader fills it.
- `DataConstraint` keeps its role, as `constraint_type`
  (`sl.ConstraintType.ACTUAL` or `ALLOWED`). A 3.1 write turns an Actual
  constraint with one attached structure, flow or agreement, one included
  region and no data keys into an `AvailabilityConstraint`
  (`dc.to_availability_constraint()`); its identity is lost, and
  `sl.unrepresentable(dc, "3.1")` says so. One that does not fit is refused,
  and the refusal names why (`dc.availability_misfits()`).
- Every other target — SDMX-ML 2.1 and 3.0, SDMX-JSON, the store — leaves an
  `AvailabilityConstraint` out: none can give it the identity an Actual
  `DataConstraint` needs. `sl.dropped(msg, version)` names each by its target.

### A 2.1 map is named for its structure set

The same conversion, for 2.1's other container (#421). A map read from a
`StructureSet` is named `<map>@<set>` — `CLM_FREQ` in `SS_A` is `CLM_FREQ@SS_A`
— and takes the set's validity and external-reference attributes. Inside a set
the 2.1 writer invented (it carries an annotation saying so) a map keeps its
own id.

- SDMX-ML 3.x and SDMX-JSON write a message's maps and leave its
  `StructureSet`s out, where they used to refuse the message.
- A map no declared set claims is written in an invented set carrying its
  validity and external reference, one per combination (`SS_SYNTHETIC`,
  `SS_SYNTHETIC_2`, …), so they come back. They were declared lost.
- The 2.1 writer refuses a map in a declared set that a reader would give back
  different, as it does a hierarchy.

---

## Downloads

`download()` fetches a provider's bytes unparsed, and the format is explicit.

```python
body = reg.download(flow_urn, fmt=sl.DataFormat.SDMX_CSV_2_0_0)
path = reg.download(flow_urn, fmt=sl.DataFormat.SDMX_ML_3_0, to="exr.xml")
```

`Accept` was hardcoded to `text/csv` before, so SDMX-ML could not be requested
at all. `fmt` is required; a destination extension that contradicts it is
refused, and a response whose `Content-Type` is not what was asked for is
refused too.

---

## Keys and API versions

SDMX-REST 1.5/2.1 puts time *outside* the dotted key; 2.2.2/3.0 makes it a key
dimension. That is a property of the API version, so it is a parameter.

```python
dsd.key_slot_ids("3.0")  # was: dsd.key_dimension_ids
sl.widen_key(key, dsd, "3.0")
```

---

## Validation is stricter

The first four rules below raise `ValidationError` as the artefact is built
(#492), so no artefact you hold breaks one, and a reader refuses a
document's artefact that does.

- Every `NameableArtefact` requires a name, and its text must not be blank
  (empty or whitespace only) in any language it gives (#452). A strict
  registry treats `{"en": ""}` as no name: FMR refused a codelist whose
  498,786 codes were named that way. Before, a write produced a file the 2.1
  reader then refused, and SDMX-JSON accepted what SDMX-ML did not. The
  SDMX-ML reader reads an empty `<com:Name>` or `<com:Description>` as `""`
  rather than dropping it, so a blank description, which is optional and
  allowed, survives a round trip.
- A code's or concept's `parent_id` must name another item of the same
  scheme, and no chain of parents may loop (#453). A dangling parent, a
  self-parent or a cycle used to validate cleanly and serialise, and then
  `roots` / `children_of` gave a forest with orphans, or none at all. A
  codelist that extends others may name a parent it inherits, so only its own
  self-parents and cycles are checked. No cached registry document has any of
  these (0 of about 192,000 parent links).
- An attribute or metadata attribute usage attached to a series must name at
  least one dimension, and every name must be a dimension of the DSD (the
  time dimension, and 2.1's measure dimension, count). One attached to a
  group must name a group the DSD declares (#454). A series attachment with
  no dimensions was written as an empty `<str:AttributeRelationship/>`, which
  every schema rejects, and FMR refused the DSD. Eurostat's 3.0 DSDs name
  dimensions in the wrong case (`FREQ` for `freq`): 2,426 references across
  266 DSD versions. A reference that names exactly one dimension ignoring
  case is read as that dimension and recorded on `msg.assumed`, so those DSDs
  still read, and `lossless=True` refuses them.
- A hierarchical code that names its level must name one of the hierarchy's
  levels, at its own depth or deeper, never shallower (#455). SDMX writes a
  hierarchy's levels as a chain, broadest first, and a code's level reference
  exists only to skip *down* from its depth. A code naming a level above its
  depth describes a structure the chain cannot hold. The model holds the
  levels as that chain too — `Hierarchy.levels` in order, a level's depth
  its position — so two levels side by side at one depth cannot be stated.
- Serialisation checks what the target can carry by default
  (`validate=True`), and refuses a measure-less DSD for 2.1.
- References whose class does not match are rejected at the read boundary.
- `Concept.parent` is gone — the column existed, the feature never did.
- Contacts belong to an organisation, not an agency.

If you build artefacts programmatically, the name requirement is the one most
likely to surface.

---

## Collections refuse to hash

`ItemList` and `ArtefactList` are unhashable, and so are `StructureMessage`
and `MetadataMessage`.

```text
# before — worked for an empty list, raised for every populated one
{dsd.dimensions: "..."}   # TypeError: unhashable type: 'Dimension'
```

<!-- test: raises -->

```python
# now — the collection says so itself
{dsd.dimensions: "..."}   # TypeError: unhashable type: 'ItemList'
```

Use the ids, which are stable and hashable:

```python
by_shape = {tuple(dsd.dimensions.ids()): "..."}
```

The old `__hash__` was `hash(self._items)`, which only ever succeeded on the
empty case, because no domain item is hashable. Items are attrs `@frozen`
now (#492), but deliberately still unhashable: they compare by value, and
hashing a large codelist would walk every code. Key by id, or by `urn` for an
artefact.

Equality is untouched; only `hash()`, `set` membership and dict-key use are
affected.

---

## `Attribute.related_measure` is gone

```text
# before
attr.related_measure   # "OBS_VALUE", always — invented, not read
```

```python
# now
attr = sl.Attribute(id="OBS_STATUS", attachment=sl.AttachmentLevel.OBSERVATION)
print(attr.attachment)  # AttachmentLevel.OBSERVATION
```

The field never carried information. SDMX 3.0's relationship type has no
measure branch at all, so every 3.0/3.1/JSON reader hard-coded `"OBS_VALUE"`
and presented it as though it came off the wire — a DSD whose measure is named
`MEAS` round-tripped to an attribute referencing a measure it does not
contain. 2.1 does write the ref, but pins it to exactly `OBS_VALUE` and
permits one measure, so there it restates what the attachment level already
says. `attachment` carries the whole of what any format encodes.

---

## Everything in a signature is importable

Additive, not breaking, but it removes a class of workaround: twelve types
that appeared in public signatures could not be imported from `sdmxlib`, so
callers reached into private modules to name them.

```python
# no longer needs a private import path
from sdmxlib import ObsRow, ObsQuery, ApiVersion, ComponentRole, ValueSet
```

If you have `from sdmxlib.<something>._private import X` lines carrying a
comment about the type not being exported, check them — most can now come
from the package root.

---

## An absent reference is NULL, not the empty string

Storage only, and visible in the query language: asking for absence now finds
it.

```text
# before — an unscoped HierarchyAssociation was unfindable
ha = sl.q.hierarchy_association
reg.query(ha).filter(ha.context_object.urn.is_null()).ids()
# []  — and yet an association with no context object was sitting there
```

```python
# now — the same query finds it
ha = sl.q.hierarchy_association
unscoped = reg.query(ha).filter(ha.context_object.urn.is_null())
```

Eight columns were `TEXT NOT NULL` while every model field behind them is
optional, and the writer squared that by storing `''` — so the constraint
enforced nothing and `is_null()` compared against a NULL nobody had written.
The same hole covered a `Dataflow` with no structure, both ends of a
`Categorisation` and of a `ProvisionAgreement`, a `Metadataflow`'s MSD, and
both ends of a `MetadataProvisionAgreement`.

If you read `sdmx.*` tables directly, drop any `= ''` comparisons. `IS NULL`
was never matching before, so this is a simplification rather than a port.

---

## Aggregates take `where=`

New, not breaking, and the answer to a question that had no spelling: which
children an aggregate collects.

```text
# before — `filter` chooses which DSDs come back, never which components collect
c = sl.q.dsd.components
reg.query(sl.q.dsd).filter(c.any(sl.q.component.role == "Dimension")).select(ids=c.list(c.id))
# ['FREQ', 'GEO', 'OBS_STATUS', 'OBS_VALUE'] — the filter changed nothing here
```

```python
# now
c = sl.q.dsd.components
dims = reg.query(sl.q.dsd).select(ids=c.list(c.id, where=sl.q.component.role == "Dimension"))
measures = c.count(where=sl.q.component.role == "Measure")
```

Every aggregate takes it — `list`, `unique`, `count`, `n_unique`, `sum` — in
both spellings. One predicate; combine with `&` and `|`. Nothing matching
collects `[]` or `0`, never NULL.

`AnyPred` is exported for it: the column form (`fan.col.list(where=…)`) cannot
name the child's row kind, so it takes the non-generic base, and every type in
a public signature has to be importable.

---

## Which hierarchy applies, as a relation

Also new. `q.hierarchy_applicability` answers "which hierarchy applies to this
component, in this dataflow's context" — the rule stated once in the schema,
which `applicable_hierarchies` and `unique_hierarchy` now read.

```python
a = sl.q.hierarchy_applicability
applies = reg.query(a).filter(a.rank == 1).select(flow=a.dataflow.id, hier=a.hierarchy.id)
```

A scoped association — one naming this dataflow — outranks an unscoped one.
`rank == 1` is the winner; the losers are rows too, so an ambiguous component
can be seen rather than silently resolved. A `HierarchyAssociation` also
reaches the component it binds now, via `sl.q.hierarchy_association.component`.

---

## Nested items are named by path

A category, a hierarchy node and a metadata attribute nest, and SDMX names a
nested one by its dotted path — `ECON.GDP`, not `GDP` — because its id may
repeat in another branch. Eurostat files one category id under four parents;
OECD places `MR` at a root and under `TYPO_METRO`. Keyed by leaf id, the repeat
was refused or overwrote its namesake (#439).

Every method on `CategoryScheme`, `Hierarchy`, `MetadataStructure` and
`LazyHierarchy` that names an item now takes a **path**, and a bare id names a
root — pysdmx's rule:

```text
# 0.48
h.codes["OTTAWA"]
h.narrower("ON")
cs.ref_to("GDP")           # built ...CS(1.0).GDP, which names nothing
h.depths()                 # {"ON": 1, ...}

# now
h["CA.ON.OTTAWA"]          # by path; h.by_id("OTTAWA") for every node with that id
h.narrower("CA.ON")
cs.ref_to("ECON.GDP")      # ...CS(1.0).ECON.GDP, the URN SDMX gives it
h.depths()                 # {"CA.ON": 1, ...} — every map is keyed by path
```

- `Hierarchy.codes` is a tuple of every node, so a stray `codes["X"]` is a type
  error rather than whichever same-id node was flattened last.
- A reference naming a nested item by its leaf no longer resolves — every
  registry swept writes the path.
- In the query language the path is `nested_id`: `q.hcode.parent.nested_id ==
  "CA"`. `ancestor_at(...)` is keyed by path and carries the ids beside it.
- Storage 2.24: `path`, `parent_path` and `ancestor_paths` on `category`,
  `hierarchy_node` and `metadata_attribute`; `parent_id`, `parent_node_id` and
  `parent_attribute_id` are gone. Raw SQL over them needs the new names.
- SDMX-ML 3.0 and 3.1 require a node id once per *hierarchy*, so a hierarchy
  repeating one is refused for those two and written as 2.1.

---

## An empty measure relationship is refused as it is set

`Attribute.related_measures=()` was stored as given and written as an empty
measure relationship, which SDMX-ML 3.0, 3.1 and SDMX-JSON all reject (#497).
The IM relates an attribute "to one or more Measures", and an unset one to
all of them, so an empty one has no meaning: it now raises `ValidationError`
as it is set. Use `None` (the default) for every measure, or name them.

## An invalid id, version, name, facet bound or time range is refused as it is set

A value SDMX rules out by itself used to be accepted until something
called `validate()` — `add`, `serialize` — and a store read never did (#492).
Now the model's own fields refuse it: construction and `attrs.evolve` raise
`ValidationError`, naming the class and field. (Assigning a field is no
longer possible at all; see [Every model class is
frozen](#every-model-class-is-frozen).)

```text
# before: built, refused later (or never)
Codelist(id="bad id", maintainer="AG", name={"en": "x"})
Code(id="A")                                        # no name
Code(id="A", name={"en": ""})                       # blank name
Facet(max_length=0)
TimeRange()                                         # neither bound

# now
Codelist(id="bad id", ...)        # ValidationError: Codelist.id — id 'bad id' is not a valid NCName ...
Code(id="A")                      # TypeError: missing required keyword-only argument: 'name'
Code(id="A", name={"en": ""})     # ValidationError: Code.name[en] — name is blank in this language
Facet(max_length=0)               # ValidationError: Facet.max_length — max_length must be a positive integer ...
TimeRange()                       # ValidationError: TimeRange — a time range needs a start, an end, or both
```

- **What is checked as it is set:** every maintainable's `id`, its
  maintainer's agency id, `version` (either grammar) and `name`; and the
  `id` and `name` of codes, concepts, categories and data providers, and the
  `id` of hierarchical codes, a DSD's dimensions, attributes and measures, and
  metadata attributes. An `Agency`'s id and name are checked when the
  `AgencyScheme` holding it is built, not on the `Agency` alone: the same
  class is the maintainer an artefact names.
- **Facets and time ranges:** a `Facet`'s `decimals`, `max_length` and
  `min_length` must be at least 1 (every format types them a positive
  integer) — on every representation, where the old `validate()` checked
  only a DSD's dimensions and attributes; and a `TimeRange` needs a `start`,
  an `end`, or both.
- **`name` is required and keyword-only** on every maintainable and those
  items, with no default; an empty one is refused as above.
- **No more empty defaults:** `DataConstraint`, `AttachmentConstraint`,
  `Hierarchy`, `HierarchyAssociation`, the four scheme maps and `StructureMap`
  no longer default `id` to `""` or `maintainer` to an empty agency.
- **The availability builders take `name`:** `build_dataflow_constraint` and
  `build_component_availability` require a `name=` for the constraint they
  build, in whichever languages you serve.
- **Readers:** a reader refuses an artefact holding such a value, as it
  refuses one that contradicts the schema; `parse` raises only
  `SdmxParseError`.
- **Building one on purpose,** to test what refuses it downstream: inside
  `attrs.validators.disabled()`.

## Every rule runs as an artefact is built

What one artefact must be as a whole — unique item ids, parents that exist and
do not loop, a concept on every component and a code on every hierarchical
code, attachments naming the DSD's own dimensions, an organisation scheme's
fixed id and version, a level's coding type — used to run in `validate()`,
which bound only where something called it (#492). With the model frozen, an
artefact is only ever built whole, so these rules run then: the constructor
and `attrs.evolve` raise `ValidationError`, and no artefact you hold breaks
one.

```text
# before                                     # now
dsd = DataStructure(..., dimensions=[        DataStructure(..., dimensions=[
    Dimension(id="FREQ")])                       Dimension(id="FREQ")])
sl.validate(dsd)   # ValidationError         # ValidationError: ...dimensions[FREQ].concept
```

- **`validate()` takes a message only** and checks what only a message can
  know: its references resolve, and no artefact is in it twice. Given an
  artefact it raises `TypeError`. `validate_message` is gone.
- **`add`, `add_all` and `serialize` no longer re-check an artefact.** They
  take what was built; `serialize` still checks what the target format can
  carry (`check_serialisable`), and `add` a version 3.x cannot hold.
- **Readers refuse such an artefact as they build it**, per artefact, as
  before — `parse`'s after-the-fact refusal pass is gone, and a refused
  artefact is never built, so a reference to it is simply unresolved.
- **A 2.1 metadata provider scheme refused on read is named as the element
  is** — a `DataProviderScheme`, which is how 2.1 carries one.
- **Building one on purpose,** to test what refuses it downstream: inside
  `attrs.validators.disabled()`, which turns these rules off with the field
  validators.

## Every model class is frozen

Artefacts, items and the model's value classes were `@define`: mutable, with
nothing recording why (#492). The mutation that needed it is gone — a `Ref`
finds its target through its registry, and readers build a tree from its
leaves — so all 103 model classes are now `@frozen`. An artefact a registry
hands out cannot change under another holder, and a rule its constructor
checks stays true.

```text
# before                                  # now
code.annotations = anns                   code = attrs.evolve(code, annotations=anns)
dsd.dimensions["FREQ"].annotations = a    dsd = attrs.evolve(dsd, dimensions=[...])
```

- **Assigning a field raises** `attrs.exceptions.FrozenInstanceError`. Build
  the value you want, or copy with `attrs.evolve`, which runs every converter
  and validator construction does.
- **Artefacts, items and value classes stay unhashable**, as they were:
  hashing a large codelist would walk every code. Key by `urn`.
- **Nothing to invalidate:** `invalidate_codelist_cache()` went with the
  `Ref` change above.

## A `Ref` is frozen and finds its target on access

A `Ref` used to be filled in: a registry wrote the artefact into it, and
swapped one canonical `Ref` per URN into every artefact it was given (#492).
Nothing writes to a `Ref` now. It holds its URN and, at most, what bound it,
and looks its target up there each time it is used:

- **An `InMemoryRegistry` or a `RestRegistry`** binds a `Ref` to its store, so
  a target added later is found, and a fuller fetch is what the next use sees.
- **A `LocalRegistry`** binds it to what the read that built it loaded. A
  target added after that read is not found through it; `get` the artefact
  again to see it.

- **`add` stores a copy** whose references the registry binds, and leaves
  your object as you built it. Read the stored one back with `get`:

  ```text
  # before                                  # now
  reg.add(dsd)                              reg.add(dsd)
  dsd.dimensions["FREQ"].representation()  reg.get(dsd.urn).dimensions["FREQ"].representation()
  ```

  `parse(registry=reg)`, a `RestRegistry` fetch and `LocalRegistry.to_registry()`
  build bound to the registry already, so they copy nothing.
- **`reg.ref(urn)` is not unique per URN.** Two `Ref`s with one URN are equal
  and hash alike — `Ref` is hashable now — and find the same artefact; `is`
  between them is no longer meaningful.
- **`Ref.of` / `Ref.to` keep their target** as a fallback: the registry's copy
  wins once it holds one.
- **A `Ref` into a closed `LocalRegistry`** is unresolved: `resolved` is False,
  and unwrapping raises `UnresolvedRef` saying the registry is closed.
- **`ref.bound`** says whether a registry binds the reference at all.
- **Components no longer cache their codelist**, and
  `invalidate_codelist_cache()` is gone: there is nothing to invalidate.
- **A document's references** find what it defines anywhere in it, not only
  earlier; a refused artefact is removed from the document instead of its
  references being rewritten.
- **`sdmxlib.api.session.Session.ingest`** takes the artefacts, not the
  message.

## A store read refuses an artefact `add` would refuse

A `LocalRegistry` read used to trust its rows, so a store written past `add`
— straight into DuckDB, as a bundle builder does — handed back artefacts no
SDMX format can write (#492). Every artefact the store builds is now checked
as `add` checks it:

- **`get` raises** `SdmxConformanceError` naming it: `... is stored and
  refused: <reason>`, carrying the `Refusal`. `None` still means only absent.
- **Every other read leaves it out and reads on** — `get_all`, a query's
  `collect()`, resolving a reference (which stays unresolved), `to_registry`
  — and records it in the new `LocalRegistry.refused`, as `parse(strict=False)`
  records a document's refusals. `add`, `drop` and `replace` clear it.
- **A lazy codelist or hierarchy** is checked when it builds the eager one:
  `materialize()` raises as `get(eager=True)` does. Opening one, or reading
  its codes, checks only what each code checks as it is built.
- **`readers.get_all` is gone**; `readers.urns_of_type` lists the URNs, and
  `LocalRegistry.get_all` reads them.
- **An undeclared level** on a stored hierarchy node used to be dropped by
  `get(eager=True)` and invented by `materialize()`; both now keep it, and the
  hierarchy is refused for it.

---

## Data consumer and organisation unit schemes are read, not refused

SDMX's two remaining organisation schemes are modelled (#469):

- `sl.DataConsumerScheme`, holding `sl.DataConsumer` items in `consumers`.
  It is the receiving counterpart of `DataProviderScheme`, with its id fixed to
  `DATA_CONSUMERS` and its version to `1.0`.
- `sl.OrganisationUnitScheme`, holding `sl.OrganisationUnit` items in
  `organisation_units`. A unit's `parent_id` names another unit of the same
  scheme, as a code's names a code, and the same rule holds: a parent is in
  the scheme, and no chain loops.

They land in `msg.data_consumer_schemes` and `msg.organisation_unit_schemes`,
and are read and written in every format. A document holding one used to have
it refused, by name; it now reads.

```text
for scheme in msg.organisation_unit_schemes:
    print(scheme.id, [(u.id, u.parent_id) for u in scheme.organisation_units])
```

- **An organisation unit scheme's version** is free in SDMX-ML 2.1 alone, and
  the model holds it. SDMX-ML 3.x prohibits a version on every organisation
  scheme, and SDMX-JSON fixes it to `1.0`. So `serialize` refuses a unit
  scheme at another version for 3.0, 3.1 and `json`, and the SDMX-JSON reader
  refuses one, as the schema does.
- **Storage is on schema 2.48:** `data_consumer` and `organisation_unit`
  tables. A store on 2.47 needs them before this version reads it. Both are
  additive, so running `create_schema` on the store adds them and stamps the
  version.
- **The query language** has `q.data_consumer_scheme`, `q.data_consumer`,
  `q.organisation_unit_scheme` and `q.organisation_unit`. An organisation unit
  has `parent` and `children`, and both items reach their `contacts`.

---

## A later codelist extension wins a clash

When two extended codelists contribute a code with the same id,
`Codelist.apply_extensions` now keeps the **later** extension's code (#486). It
kept the earlier one. That also changes what an SDMX-ML 2.1 write of an
extending codelist holds. The IM says so (§4.3.3.2: "the latest Codelist used
overrides any previous Codelist"), and FMR 12.4.2 flattens the same way. The
codelist's own codes still win over inherited ones, and an inherited code keeps
the place it first appeared in.

Nothing changes for a codelist whose extensions share no code id. To keep both
codes, give an extension a `prefix`.

## IM rules no format enforces warn, and `severity` is gone

Some rules the information model states are enforced by no SDMX schema. An
artefact that breaks one is now built with an `sl.ImRuleWarning`, one per
artefact (#481):

- a facet's sequence attributes go together (IM §3.6.3.3, #482): `interval`,
  `start_value` and `end_value` need `is_sequence=True` and each other, and
  likewise `time_interval`, `start_time` and `end_time`. A time dimension's
  `start_time`/`end_time` are bounds, and do not warn;
- a data constraint holds cube regions or data key sets, not both
  (§12.3.3.1, #491).

Nothing that built before is refused. Across every registry we sweep, three
facets of about 1.05 million warn, and no constraint does. A
test suite running with `-W error` will see the warnings as errors. Filter
`sl.ImRuleWarning`, or its base `sl.SdmxWarning`. (One warning sdmxlib emits
is a plain `RuntimeWarning`: a `LocalRegistry` overwriting an artefact first
stored from a different source registry.)

`ValidationIssue.severity` is removed: it was always `"error"`, and a warning
is now a warning, never an issue. `str(issue)` no longer starts `error:`.

## A message's header is read and written

A message's header was ignored on read, and every write invented one (#480).
It is now modelled: `StructureMessage.header` is an `sl.Header` and
`MetadataMessage.header` an `sl.MetadataHeader`, each `None` when the
document has none. Both are set with `header=`.

```python
from datetime import UTC, datetime

header = sl.Header(id="REQ_1", prepared=datetime(2026, 10, 20, tzinfo=UTC), sender=sl.Sender(id="ESTAT"))
msg = sl.StructureMessage(header=header)
assert sl.parse(sl.serialize(msg, version="3.0")).header == header
```

A header holds everything a structure message's header allows: id, test,
prepared, sender (`sl.Sender`, with its time zone), receivers
(`sl.Party`), names and source. A metadata header adds the provider, the
action (`sl.ActionType`), data set ids and the extraction time. SDMX-JSON's
`meta` has no source, no time zone and none of the metadata four, and
`sl.unrepresentable(header, "json")` says so. A header the model cannot hold,
such as a party with no id, reads as `None` and is recorded on `msg.lost`, so
`lossless=True` refuses it.

**What changes in output.** Without a header, every format writes the same
fixed placeholders: id `SDMX_MSG`, sender `sdmxlib`, and prepared
`2000-01-01T00:00:00` — in SDMX-JSON `2000-01-01T00:00:00Z`, since its schema
requires a zone. SDMX-ML did already. SDMX-JSON wrote id and sender `SDMXLIB`
and the current time, so its output differed on every write; it is now
reproducible. Code that read a JSON message's `meta.id` or `meta.prepared`
to mean anything gets the placeholders. Set a header to say otherwise. Every
reader takes the placeholders back as no header, so `msg.header` is `None`
and a header-less message round-trips as one.

## Writers leave out what a schema defaults, and the model holds what it means

Output has one spelling per meaning, the minimal one (#503). An optional
attribute at the default its format and type declare is left out:
`usage="optional"`, `include="true"`, `isInclusive="true"`,
`cascadeValues="false"`, a component's `textType="String"` (a time
dimension's `ObservationalTimePeriod`), `isMultiLingual` at its version's
default, SDMX-JSON's `minOccurs: 1`, and 2.1's fixed metadata-target types.
Identity is always written (an artefact's and a reference's id, agency,
version, parent version, class and package), and so is anything a format
requires, such as 2.1's `assignmentStatus`. There is no option to spell
defaults out. The bytes change; the meaning does not.

For that to be safe, the model holds what an unstated value *means* rather
than `None`, and a reader fills it in:

- `Facet.is_multilingual` is a `bool`, `False` unless stated. An absent
  `isMultiLingual` reads as its version's default where its type allows one:
  **true in SDMX-ML 2.1 and 3.0, false in 3.1**. So a 3.0 attribute with no
  `isMultiLingual` is multilingual, and converting it to 3.1 now writes
  `isMultiLingual="true"`, where before the flag silently flipped to false.
  2.1 cannot say it of an attribute at all, which `sl.unrepresentable` now
  declares rather than `serialize` refusing. A dimension's format can never
  carry it, and a multilingual dimension is refused as it is built.
- A text format's `type` is its context's default when unstated: `String` for
  a component, concept, metadata attribute or map target,
  `ObservationalTimePeriod` for a time dimension. A facet built with
  `type=None` holds it, so it compares equal to itself read back.
- `RegionValue.cascade` is `CascadeSelection.SELF_ONLY` (the code alone)
  unless stated, never `None`.

One SDMX-JSON fix rides along: a metadata attribute's `minOccurs` was read as
0 when absent and left out at 0, where the schema says absent means 1. It now
reads and writes as SDMX-ML does.

## SDMX-JSON's dates carry a time zone

Every SDMX-JSON schema types `prepared`, and a map's validity, as a JSON
Schema `date-time`, which requires a zone. Nothing checked that, and some of
what sdmxlib wrote had none (#496).

**Breaking:** `prepared` is a timezone-aware `datetime`, not a string, on
`write_sdmx_json_data` (SDMX-JSON 1.0 and 2.0) and `ObsQuery.sink_json`. A
naive one raises `ValueError` rather than having a zone guessed for it.
Leaving it out of `sink_json` still means now, in UTC.

```text
# before
ds.sink_json("exr.json", prepared="2026-10-20T09:30:00")
```

```python
from datetime import UTC, datetime

obs = reg.obs(flow_urn)
obs.sink_json("exr.json", lang="en", prepared=datetime(2026, 10, 20, 9, 30, tzinfo=UTC))
```

<!-- test: raises -->

```python
# a naive datetime: ValueError, prepared must be timezone-aware
obs.sink_json("exr.json", lang="en", prepared=datetime(2026, 10, 20, 9, 30))
```

Two structure-message changes come with it:

- **A map's validity** (`ItemMap.valid_from` / `valid_to`,
  `RepresentationMapping`'s) is a `date` in the model, as SDMX-ML 3.x's
  `xs:date` is. SDMX-JSON writes it as midnight UTC
  (`2026-01-01T00:00:00Z`) and reads midnight UTC back as that date. Any other
  instant, a bare date, or a date-time with no zone refuses the artefact
  rather than being truncated.
- **A constraint period that is a date-time with no zone**, which SDMX-ML's
  `xs:dateTime` allows, is refused by the SDMX-JSON 2.0 structure writer and
  the SDMX-JSON 1.0 constraint writer, naming its path. Give it a zone, or
  write SDMX-ML.

A time dimension value's start and end in an SDMX-JSON data message are still
written without a zone: the consumer they exist for, .Stat, reads them as
local time.

## A predicate belongs to the relation it was built from

A query refuses a predicate built from a sibling root — another relation over
the same table — where it used to plan it from the wrong relation (#510).
Every maintainable roots at `sdmx.artefact`, so a check on the table let these
through:

<!-- test: raises -->

```python
with sl.LocalRegistry(":memory:") as local:
    local.add(sl.Dataflow(id="DF", maintainer="AG", name={"en": "f"}))
    # TypeError, naming q.codelist and q.dataflow
    local.query(sl.q.dataflow).filter(sl.q.codelist.agency == "AG")
```

Through a hop it was a silently wrong answer, not just a loose spelling:
`reg.query(sl.q.dataflow).filter(sl.q.dsd.dimensions.id == "UNIT")` planned
the DSD's path from the dataflow and returned `[]`. Write the predicate from
the query's own root, and hop from there:

```python
with sl.LocalRegistry(":memory:") as local:
    local.add(sl.Dataflow(id="DF", maintainer="AG", name={"en": "f"}))
    print(local.query(sl.q.dataflow).filter(sl.q.dataflow.agency == "AG").ids())  # ['DF']
    local.query(sl.q.dataflow).filter(sl.q.dataflow.dsd.dimensions.id == "UNIT")
```

`sl.q.artefact`'s columns are every artefact's, so they still fit any
maintainable root. The type checker already refused these (`Pred[R]`); the
runtime now agrees with it.
## A time dimension always has a format

`TimeDimension.representation` is never `None` (#507). With no format stated it
is `Facet(type=ObservationalTimePeriod)`, the default every SDMX format gives a
time dimension:

```python
td = sl.TimeDimension()
print(td.representation.type)  # ObservationalTimePeriod
```

Every format requires the format — SDMX-ML's `TimeDimensionType` has a
mandatory `LocalRepresentation`, SDMX-JSON 2.0 a mandatory
`localRepresentation.format` — and `None` was written as a document each
schema rejected; SDMX-JSON's own reader then refused what its writer had just
written. SDMX-ML now writes the minimal `<str:TextFormat/>`, SDMX-JSON states
the type, and a document that omits the format reads as the default with the
assumption in `msg.assumed`. Code testing `td.representation is None` should
test the facet's type instead.

---

## A time or measure dimension needs a concept

Every component needs a concept, and the rule now covers the two that are
not in a list (#506): a `TimeDimension` or `MeasureDimension` without one is
refused as the DSD is built, as a dimension, attribute or measure already was.
It used to build, store, and be written as SDMX-ML its XSD rejects.

<!-- test: raises -->

```python
sl.DataStructure(
    id="DSD",
    maintainer="AG",
    name={"en": "d"},
    time_dimension=sl.TimeDimension(),  # ValidationError: ConceptIdentity is required
)
```

---

## SDMX-JSON data names its language

`lang` has no default on `write_sdmx_json_data` (1.0 and 2.0) and
`ObsQuery.sink_json` (#509). It picks the
names a data message carries — each dimension's, value's and the dataflow's —
and fills `contentLanguages`. It defaulted to `"en"`, the one place the
library chose a language for the caller; `sink_json` did not take one at all.

```text
# before
ds.sink_json("exr.json")
```

```python
obs.sink_json("exr.json", lang="en")
```

---

## `Frame` is `Select`

What `select(...)`, `group_by(...).agg(...)` and `agg(...)` return — a
projection plan, run by `.collect()` — is `sl.Select`, not `sl.Frame` (#516).
The old name clashed with `.frame()`, which *runs* a query into a polars
`DataFrame`; `Select` is SQL's word for the statement, as in SQLAlchemy.
Nothing changes at a call site that does not name the type.

```text
# before
f: sl.Frame[sl.CodeRow] = cl.codes.select(sl.q.code.id)
```

```python
with sl.LocalRegistry(":memory:") as local:
    local.add(sl.Codelist(id="CL", maintainer="AG", name={"en": "c"}, codes=[sl.Code(id="A", name={"en": "a"})]))
    codes = local.get(sl.Codelist.urn_for(agency="AG", id="CL", version="1.0")).codes
    f: sl.Select[sl.CodeRow] = codes.select(sl.q.code.id)
    print(f.collect().rows())  # [('A',)]
```

---

## An observation result is typed from its DSD

`frame()` and `select(...).collect()` on an observation query type each
component from the DSD (#516): a code as its codelist's `pl.Enum`, a value as
its `Facet` declares — where they returned whatever the table held, usually
text. A component the DSD gives no representation keeps its stored type. A
value that does not fit — a code the codelist does not list — raises
`ValueError`; `.stream()` still gives the raw rows, and `.validate()` names
every bad value.

---

## Observation data is an `ObsQuery`

`reg.data()`, `DataQuery`, `sl.dim`, `DimFilter` and `Dataset` are gone
(#516). There were two ways to read a dataflow's observations — `reg.data(...)
.fetch()` into a `Dataset`, and `reg.obs(flow)` into an `ObsQuery` —
and they answered different questions about the same rows. `ObsQuery` is the
one left, and it took over what only `Dataset` had.

```text
# before
ds = reg.data(sl.Dataflow, agency="ECB", id="EXR").slice(sl.dim("REF_AREA") == "ON").fetch()
ds.collect(labels="en")
ds.pivot()                          # measure_dim= on reg.data for a regular dimension
ds.validate()
ds.sink_parquet("exr.parquet")
ds.structure.dimensions
```

```python
obs = reg.obs(flow_urn)
on = obs.filter(obs.col("REF_AREA") == "ON")   # pushed into the REST key
print(on.frame(labels="en").columns)  # ['REF_AREA', 'OBS_VALUE', 'REF_AREA_label_en']
on.pivot(on="REF_AREA")              # on= replaces measure_dim=; the default is the MeasureDimension
on.validate()                        # every bad value, with a count, from the raw rows
on.sink_parquet("exr.parquet")       # also sink_csv, sink_json(lang=); typed batch by batch
print(on.structure.dimensions.ids())  # ['REF_AREA']
```

The rest of the old surface, and where it went:

| Before | Now |
| --- | --- |
| `reg.data(..., key=, start_period=, end_period=)` | `obs.filter(...)` on the dimensions and `TIME_PERIOD` — pushed into the key and period |
| `.slice(sl.dim(...).isin([...]))`, `.measures([...])` | `obs.filter(obs.col(...).is_in([...]))` |
| `.fetch_to(path)` | a `RestRegistry(..., store="file.duckdb")`, then `obs.sink_parquet(path)` |
| `ds.collect()` / `ds.lazy()` | `obs.frame()`; `.select(...)` / `.group_by(...).agg(...)` for a projection |
| `ds.code("X")`, `ds.label("X", lang=)` | `obs.col("X")`; `obs.frame(labels=)` |
| `slpl.scan_csv(path, dsd)`, `reg.scan_csv(path)` → `Dataset` | `reg.scan_csv(path)` → `ObsQuery`, on `LocalRegistry` too |
| `read_sdmx_json_data(source, dsd)` | `reg.scan_json(source)`; `formats.sdmx_json.data.read_observations` for the frame alone |
| `sdmxlib.polars.write_data_json(ds, path)` | `obs.sink_json(path, lang=)` |

A file read through a registry needs the dataflow and its structure in that
registry, and names them itself — SDMX-CSV by `STRUCTURE_ID` (or 1.0's
`DATAFLOW`), SDMX-JSON by its `rel="dataflow"` link. An SDMX-CSV file whose
`STRUCTURE` is `datastructure` is refused: an observation query is over a
dataflow, and a structure alone does not say which. `pivot()` returns a
`pl.DataFrame`, where `Dataset.pivot()` returned a `LazyFrame`.

---

## `observations` is `obs`

`LocalRegistry.observations`, `RestRegistry.observations` and
`DuckDBDataStore.observations` are `obs` — the spec term's transparent
abbreviation, matching the `ObsQuery` / `ObsRow` it returns and the `obs`
every example already bound it to. Same arguments, same result.

```text
# before
obs = reg.observations(flow)
```

```python
obs = reg.obs(flow_urn)
print(obs.structure.dimensions.ids())  # ['REF_AREA']
```

---

## A node's id where its path belongs is refused

A path-keyed tree — `Hierarchy`, `CategoryScheme`, `MetadataStructure`,
`LazyHierarchy` — raises `KeyError` when a walk is given a node's own id
instead of its path, naming the path (#514). `h.broader("ON")` answered `None`
and `h.narrower("ON")` an empty list, as if `ON` were a root or a leaf, which
is how code written before the trees were keyed by path kept running wrong. A
key that is nobody's id still answers absent; `get` still answers `None`.

```text
# before
h.broader("ON")      # None, silently
```

```python
h = sl.Hierarchy(
    id="H",
    maintainer="AG",
    name={"en": "Geo"},
    tree=[sl.HierarchicalCode(id="CA", code=cl.ref_to("CA"), children=[sl.HierarchicalCode(id="ON", code=cl.ref_to("ON"))])],
)
print(h.broader("CA.ON").id)  # CA
```

<!-- test: raises -->

```python
h.broader("ON")  # KeyError: 'ON' is a node id, not a path in Hierarchy ... It is at 'CA.ON'
```

---

## A REST filter nothing narrows is refused

`RestRegistry.obs(flow)` raises `PushdownError` when a filter cannot go into
the data request — one on a measure, say — and nothing else narrows it (#515).
It fetched `/data/{flow}/all`, every observation the provider holds, to filter
them locally. Beside a key or time filter the same predicate still runs, after
a fetch those narrowed; an unfiltered query still fetches everything, as asked.

```text
# now refused: OBS_VALUE cannot be sent, and nothing else narrows the fetch
obs.filter(obs.col("OBS_VALUE") > 1.0).frame()

# runs: the key filter narrows the fetch, OBS_VALUE is applied to what came back
obs.filter(obs.col("REF_AREA") == "ON", obs.col("OBS_VALUE") > 1.0).frame()
```

---

## An observation column refuses a literal of the wrong kind

`obs.col(id)` checks what it is compared with as the filter is built (#511). A
code, a time period or a declared string is text; a numeric `Facet` is a
number; a component the DSD leaves untyped takes its stored column's type.
`obs.col("REF_AREA") == 1` matched nothing, and `obs.col("OBS_VALUE") > 1.5`
over a text column was a DuckDB binder error; both now raise `TypeError`
naming the fix. `obs.num(id)` and `obs.text(id)` are new: exact `Num` / `Str`
static types, checked the same way, and `sl.Str` / `sl.Num` are exported to
annotate them.

```text
# now refused
obs.col("REF_AREA") == 1
```

```python
obs = reg.obs(flow_urn)
print(obs.filter(obs.text("REF_AREA") == "ON").frame().height)  # 1
```

---

## An untyped measure is read as its values say

A measure the DSD gives no type — no `Facet`, no codelist — is no longer
forced to text when a REST fetch or `reg.scan_csv` stores it (#518). DuckDB
infers it from the values, so an `OBS_VALUE` of numbers is a number:
`obs.num("OBS_VALUE") > 1.5` runs, and `frame()` gives it as a float rather
than a string. A measure the DSD does type is forced as before.

---

## What did not change

`sl.read`, `sl.parse`, `to_xml`, `to_json`, `Ref`/`ref()`,
`InternationalString`, the polars bridge, and attribute navigation
(`dsd.dimensions["FREQ"].codes`) are as they were — though `.codes` on a
registry-backed DSD only *became* what it always declared in #398, and
`code_ids()` is now the cheap way to ask for ids alone. `ItemList` keeps its API
apart from hashing, covered above. If your code only reads artefacts and never
touched a registry constructor or a query, it may need nothing beyond the
`get` change.
