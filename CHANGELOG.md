# Changelog

All notable changes to this project will be documented in this file.

## [0.72.0] - 2026-10-23

The bundle as a published, checkable artefact (GH-56). See `docs/upgrading.md`.

### Breaking Changes

- **bundle**: sdmxlib owns `bundle.manifest.json`. `LocalRegistry.bundle` reads it when present, and a file there that is not sdmxlib's (a builder's own, without `format_version`) is refused with `sl.ManifestError`: republish the bundle with `sdmxlib.bundle.write_manifest`. A manifest on another store schema is refused with `sl.StoreSchemaError` before `meta.duckdb` is opened

### Features

- **bundle**: `write_manifest` publishes a bundle: a fresh `token` per publication (what caches key on), a sha256 per file, observation counts per dataflow, the store schema version; `bundle(require_manifest=)` and `LocalRegistry.manifest` (GH-66)
- **bundle**: `check_bundle` checks a bundle against its manifest and itself: files, the DuckLake catalog, bindings, and the structural closure (GH-67)
- **bundle**: `fetch_bundle` lands one dataflow from an SDMX REST source as a published bundle, narrowed by SDMX key and period, its provenance in the manifest (format 1.1) (GH-68)
- **testing**: `sdmxlib.testing.synthetic_bundle` builds conforming bundles of any shape from a seed; `profile_bundle` reads the shape of a real one (GH-69)
- **bundle**: annotation overlays: `apply_overlays`, `effective_token` and `fold_overlays` change display annotations without a rebuild (GH-70)
- **cli**: `sdmxlib-cli`, a separate package released with sdmxlib at the same version: `sdmxlib bundle check` and `sdmxlib bundle fetch` (`uv tool install sdmxlib-cli`) (GH-67, GH-68)

### Bug Fixes

- **local**: A bundle opened twice in one process attaches its lake once, instead of failing with "database with name "lake" already exists"

### Documentation

- **upgrading**: sdmxlib owns `bundle.manifest.json`

## [0.71.0] - 2026-10-22

### Breaking Changes

- **storage**: Opening a store that holds artefacts on another store schema version raises the new `sl.StoreSchemaError` instead of emptying it (GH-57). From 0.68 to 0.70.1, `LocalRegistry` and `LocalRegistry.bundle()` dropped and rebuilt the schema on a version mismatch, so a bundle built by 0.67 (schema 2.19) lost every artefact the moment a newer sdmxlib (schema 2.48) opened it, even to read, and the sdmxlib that built it then read it empty too. A cache that can be fetched again opts back in with `LocalRegistry(path, rebuild=True)`; `bundle()` never rebuilds; an empty store or a new file is created or migrated as before. `ReadOnlySchemaError` is now a `StoreSchemaError`
- **storage**: Stores and bundles written by sdmxlib 0.67 or earlier must be rebuilt from their source to be read by 0.68 or later; the store schema moved from 2.19 to 2.48 in 0.68.0, which its changelog did not say. See `docs/upgrading.md`

## [0.70.1] - 2026-10-22

### Bug Fixes

- **api**: `RestRegistry.obs()` asks each provider for CSV the way it answers: Eurostat refused the old `Accept: text/csv` with 406, so no observations could be fetched from it, and BIS answered it with SDMX-ML, which was read as CSV. The media type is per provider (`ProviderInfo.csv_media_type`; ECB's is `text/csv`), else the SDMX-CSV the API version defines, and `RestRegistry(csv_media_type=)` overrides it. A response that is not CSV is refused (GH-50)
- **api**: `RestRegistry.obs()` with an unpinned dataflow URN fetches at the version the dataflow resolved to: BIS and OECD refused `latest` in a data URL with 400 (GH-51)

### Documentation

- **query**: Say why pivot returns a DataFrame
- Pinaxlib is statcatlib, and its schema is statcat.*

## [0.70.0] - 2026-10-22

### Breaking Changes

- **storage**: Move to DuckDB 2.0. A store this version writes cannot be opened by sdmxlib 0.69.x or DuckDB 1.5; a store 0.69.x wrote opens here
- **deps**: Require the latest runtime dependencies: duckdb >=2.0, polars >=2.0, pyarrow >=26, msgspec >=0.22, lxml >=6.1.3, typing-extensions >=4.16 (GH-33). msgspec <0.22 could not import sdmxlib on Python 3.14

### Bug Fixes

- **query**: An observation column whose DSD declares a `Date`, `GregorianDay`, `DateTime`, `Time` or `Boolean` facet is parsed from its text. Every typed result over one failed with "does not fit its component's type", from polars 2.0 for dates and always for the rest
- **formats**: `parse` raises the new `sl.SdmxErrorMessage`, carrying each `(code, text)`, on an SDMX error message instead of reading it as an empty message (GH-31)
- **formats**: Read SDMX-JSON 1.0's attribute and format spellings: `none`, `primaryMeasure`, `textFormat` (GH-30)

### Documentation

- GitHub issues are written GH-N; a bare #N means GitLab

### Performance

- **storage**: Parse the hottest store reads once (GH-39)
- **storage**: Parse the DSD's own reads once too (GH-39)

### Testing

- The fork probe reaps its own hung child (GH-34)

## [0.69.1] - 2026-10-21

### Bug Fixes

- SchemaInspector queries on the thread's own handle
- Cap duckdb below 2

### Miscellaneous

- Describe the lockfile's mirror generically
- Make GitHub the project's home

## [0.69.0] - 2026-10-21

### Breaking Changes

- **query**: A predicate belongs to the relation it was built from
- **model**: A time dimension always has a format
- **model**: A time or measure dimension needs a concept
- **json**: SDMX-JSON data writers take no default language
- **data**: A Dataset labels as a query does
- **query**: Frame is Select
- **query**: An observation result is typed from its DSD
- **registry**: Scan_csv reads a file as an observation query
- Retire reg.data(), DataQuery, sl.dim and Dataset
- Observations() is obs()
- **model**: A node's id where its path belongs is refused
- **registry**: A REST filter nothing narrows is refused
- **model**: An attribute's attachment is required
- **query**: An observation column knows what it holds
- **data**: A measure the DSD leaves untyped is read as its values say

### Bug Fixes

- Every public signature evaluates at runtime on 3.14
- **query**: A grouped aggregate honours where=
- **local**: A store's constructor refuses a connection by name
- **model**: A sentinel on an enumeration or coding format is refused as built
- **api**: AncestorMap.ancestors() returns the public SqlRelation
- **query**: Misuse is refused naming the fix, not by DuckDB
- **binding**: A binding from a DSD maps the measure dimension

### Documentation

- Rewrite llms.txt and llms-dsl.txt, and execute them
- Bring every page up to date with v0.68

### Features

- **api**: Export the exceptions the docs tell callers to catch
- **query**: An observation query exposes its structure
- **query**: An observation query sinks to Parquet, CSV and SDMX-JSON
- **query**: An observation query validates its values
- **query**: An observation query pivots wide
- **registry**: Scan_json reads an SDMX-JSON data message as an observation query

### Miscellaneous

- Name downstream systems by role, not by internal name

## [0.68.0] - 2026-10-20

### Breaking Changes

- A version a format cannot carry is refused with ValidationError
- A "latest" reference is refused in SDMX-ML 2.1 too (#233)
- Finality is derived from the version; 2.1's isFinal is kept as legacy_is_final (#420)
- Versions only SDMX 2.1 accepts are refused at ingest, unless rewritten (#420)
- Drafts are left out of SDMX-ML 2.1, with what references them (#420)
- What an SDMX-ML read cannot hold is recorded, never dropped silently (#422)
- A DSD holds SDMX 3.0's component additions (#422)
- SDMX-JSON records what it cannot hold; an excluding value set is refused (#422)
- A cube region value set can exclude its values (#422)
- A cube region's values and value sets carry their qualifiers (#422)
- Model SDMX 3.x ValueList; a representation may enumerate by one (#422)
- Annotation URLs, sentinel values and time range validity are held (#422)
- Maps are N:M (#422)
- A constraint attaches to several artefacts (#422)
- A 2.1 metadata target holds all its target objects (#422)
- Drop the fields no SDMX format carries (#419)
- Add refuses what no SDMX format can write (#419)
- Readers refuse what add would, and RestRegistry reads on (#419)
- **json**: Check SDMX-JSON formats, and write what they require — #496
- **model**: Refuse a bad id, agency id or version as it is set — #492 stage 1, part 1
- **model**: A name is required, and refused blank as it is set — #492 stage 1, part 2
- **model**: Refuse a zero facet bound or an unbounded time range as it is set — #492 stage 1, part 3
- **model**: Refuse an empty measure relationship as it is set — #497
- **storage**: A store read refuses an artefact add would refuse — #492 stage 1, part 4
- **model**: A Ref is frozen and finds its target on access — #492 stage 2, part 1
- **model**: Every model class is frozen — #492 stage 2, part 2
- **model**: Every whole-artefact rule runs as the artefact is built — #492 stage 2, part 3

### Bug Fixes

- A reference's version is checked against the target format (#429)
- A code whose parent SDMX-ML 3.0.0 cannot name is refused for 3.0 (#432)
- **bench**: Sweep census stops counting schema defaults and empty elements as lost
- LocalRegistry keeps a constraint's data_keys (#437)
- LocalRegistry keeps a component's enumeration_format (#438)
- Every model datetime is held tagged UTC, so a parsed artefact equals its stored self (#440)
- The store keeps every item's uri, value-map validity, level annotations and Agency.parent (#442)
- A 2.1 HierarchicalCodelist in the alias form reads whole (#434)
- LazyHierarchy.materialize() keeps every node's annotations (#443)
- A time dimension's textType must be a time type (#431)
- A reference to a class the target cannot reference is refused (#430)
- **bench**: Sweep census counts a refused write as read, not as lost
- SDMX-ML 3.x writes and reads a hierarchy's levels as a chain (#422)
- An SDMX-ML 2.1 element census, and the misreadings it found (#422)
- Closed-world check refuses a child a restriction prohibits (#422)
- A dimension position may skip a number, not contradict order (#449)
- **bench**: A counted finding's examples leave its sweep key (#450)
- SDMX-JSON writes a wildcarded extension member as the codes it matches (#451)
- **sdmx_json**: Name the structure schema in meta, so pysdmx reads our JSON (#465)
- **sdmx_json**: Cite schemas at json.sdmx.org, where SDMX publishes them
- **sdmx_ml21**: A ContentConstraint without @type is Actual, as the schema defaults (#475)
- **sdmx_ml**: Record what a read assumes, refuse what it cannot assume (#474)
- **sdmx_json**: Refuse the entry, not the document; record what a read assumes (#474)
- **model**: Hold a constraint's keys in key sets, each included or excluded (#479)
- **sdmx_ml21**: A reference without a version names 1.0, as the schema defaults (#476)
- **formats**: A component without an id takes its concept's, as the schema says (#477)
- **sdmx_ml**: Refuse an artefact holding an attribute its schema allows nowhere (#446)
- **sdmx_ml**: Refuse what the frame around the artefacts holds that the schema allows nowhere (#447)
- **validation**: A name blank in any language is refused (#452)
- **validation**: A parent_id names an item of its scheme, and no chain loops (#453)
- **validation**: Name an XML-forbidden character before calling a name blank (#452)
- **validation**: A series attachment names the DSD's dimensions, a group one its groups (#454)
- **validation**: A hierarchical code's level is one of its hierarchy's, never above its depth (#455)
- **sdmx-json**: Carry the dataflow's annotations in data messages
- **fixtures**: The N:M shape maps to codes its codelists hold, at the arity it declares (#457)
- **tools**: Credit import-time mutants to the tests that can see them (#458)
- **formats**: Read xs:boolean one way, 1 and 0 included (#458)
- **tools**: Mutate code that runs at import, and book its breakage as a kill (#458)
- **formats**: The 3.x reader reads 3.x; refuse a dangling level (#458)
- **sdmx_json**: `prepared` states its time zone
- **validation**: Validate a message by checking its artefacts — #492
- **formats**: Read a node naming an undefined level with no level, and record the loss (#501)
- **query**: A version min/max through a fan-out answers instead of refusing (#324)
- **tests**: The fold oracles keep a Hangul syllable whole, as strip_accents does (#494)
- **model**: A later codelist extension wins a code clash (#486)

### Documentation

- The building tutorial pins its external references' versions
- The hierarchies page's fixture gives every node its code
- The last three LOSSES rows are declared losses (#422)
- **bench**: The BIS corpus is 105 codelists and 9,716 codes
- **bench**: What the scale tier found on main after the M22 fixes
- Say what a JSON-read message holds until lost is read, and how to release it (#473)
- A 2.1 reference without a version names 1.0 (#476)
- **tutorial**: A series attribute names its dimensions (#454)
- A hierarchy's broadest level comes first; the root is not unlevelled (#455)
- **hierarchies**: Say how SDMX reads a level chain (#455)

### Features

- **bench**: Registry sweep — parse, store and project live registries (%20)
- **bench**: Sweep analyses large documents in chunks; absorbs Eurostat's quirks
- **bench**: Sweep runs documents in parallel, sized to what the machine has free
- **bench**: Sweep analyses chunks of one document in parallel
- Cube regions hold time ranges and component value sets (#433)
- Availability puts an attribute in the region's components (#433)
- Readers refuse per artefact, and parse() reports every refusal (#435)
- SDMX-ML readers refuse an artefact holding an element the schema allows nowhere (#436)
- Nested items are keyed by path, not leaf id (#439)
- The store resolves every item ref, 2.1 names hierarchy containers (#439)
- Model SDMX 3.x geographic codelists (#422)
- SDMX-JSON reports every key its structs skip; a JSON census (#422)
- Annotations on component lists, groups and 2.1 target objects (#422)
- Annotations on maps, value mappings, regions and keys (#422)
- External references and partial item schemes (#422)
- A format's sequence and time facets; a concept's enumeration format and ISO reference (#422)
- The smaller fields the attribute census found (#422)
- The last attribute drops the attribute census found (#422)
- A data key's components (#422)
- A structure map's date and fixed-value maps (#422)
- SDMX-ML 2.1's own DSD constructs are held (#422)
- SDMX 3.x's com:Link is held (#422)
- What SDMX-JSON's links say is held (#422)
- SDMX-ML 2.1's representation mappings are held (#422)
- A single 3.x measure is written to SDMX-ML 2.1 as OBS_VALUE (#421)
- An extending codelist is written to SDMX-ML 2.1 with what it inherits (#421)
- 2.1 hierarchies are named for their codelist; HCL and HA left out where absent (#421)
- A 2.1 map is named for its structure set; 3.x leaves the set out (#421)
- 3.1 drops a release calendar as a declared gap; AttachmentConstraint conversion declared (#421)
- Model SDMX 3.1's AvailabilityConstraint; an Actual constraint that fits is written as one (#421)
- A 3.x MSD is written to SDMX-ML 2.1 with a marked reporting layer (#421)
- A 2.1 metadataflow is given the targets 3.x requires, derived from its MSD (#421)
- Metadata providers and provision agreements are written to SDMX-ML 2.1 as 2.1 provisioned metadata (#421)
- An attribute's usage and relationship convert across every format (#421)
- Model OrganisationSchemeMap in every format, the store and the query language (#448)
- Uri is a query path on every relation that stores it (#444)
- **bench**: Record what FMR makes of our documents (#457)
- **model**: Model DataConsumerScheme and OrganisationUnitScheme (#469)
- **model**: Warn on IM rules no wire schema enforces (#481, #482, #491)
- **model**: Read and write a message's header (#480)
- **formats**: Write the minimal spelling, and hold what a default means (#503)

### Miscellaneous

- **bench**: Sweep baseline for six registries (%20)
- **bench**: Sweep baselines re-run with the corrected census
- **bench**: Sweep baselines re-run over milestone 20's fixes
- **bench**: Sweep baselines re-run over reader refusals (#419)
- **bench**: ESTAT sweep re-run over gapped dimension positions (#449)
- **bench**: Sweep summaries re-run over example-free keys (#450)
- **bench**: IMF sweep re-run over the 2.1 measure rename (#421)
- **bench**: BIS/ECB/OECD/SDMX/IMF sweep re-run over the hierarchy conversion (#421)
- **bench**: OECD/BIS sweep re-run over the AvailabilityConstraint conversion (#421)
- **bench**: IMF/BIS/OECD/SDMX sweep re-run over the 2.1 MSD layer (#421)
- **bench**: BIS/IMF/OECD/SDMX sweep re-run over metadataflow target derivation (#421)
- **bench**: BIS/ECB/IMF/OECD/SDMX sweep re-run over the attribute conversions (#421)
- Sweep-delta compares against main by default
- **tools**: Port mutmut_forksafe to mutmut 3.8's fork server
- **deps**: Widen uv_build pin to >=0.13.0,<0.14.0
- **deps**: Pyrefly 1.4, src/ and bench/ at zero
- **deps**: Ruff 0.17
- **deps**: Pydoclint 0.11
- **bench**: Install pysdmx's json extra with the compete group

### Performance

- **sdmx_ml**: Read no links from 2.1, and no QName per code (#463)
- **codelist**: Index codelists for a wildcard only when there is one (#462)
- **formats**: Work out what a format cannot hold once per write (#464)
- **sdmx_json**: Find a read's losses when they are asked for (#461)
- **sdmx_json**: Stop paying per item for work an item does not need (#466)
- **sdmx_json**: Build nothing per item that has no link to write or check (#462)
- **sdmx_json**: Read every component's codes in one batch per kind (#441)
- **storage**: Read an artefact's annotations by its indexed column (#426)
- **sdmx_ml**: Name each assumption's path rather than search every artefact for it (#474)
- **model**: Check a plain name with one regex match a language — #492
- **storage**: Read the spine and annotation URLs without correlated subqueries

### Refactoring

- **tests**: One comparison for every external oracle (#457)

### Testing

- **bench**: Sweep tests skip where mutmut has no bench/
- The wire gate over populated artefacts (%20)
- SDMX-JSON 1.0 vendors no metadata schema (structure is vendored now)
- **bench**: Q.hcode's construction budget absorbs #440's two converter calls
- An element census finds what SDMX-ML 3.x readers drop unrecorded (#422)
- An attribute census finds what SDMX-ML readers drop unrecorded (#422)
- The accent rungs of both oracles fold case with lower, as SQL does
- **schemas**: Vendor the SDMX 3.1 information model as text (#456)
- **validation**: A direct test for the provision-agreement and cube-region checks (#456)
- An information-model rule census, guarded both ways (#456)
- FMR 12.4.2 as an independent oracle, replayed in ci (#457)
- Pysdmx 1.20.0 as the SDMX-ML 3.1 oracle, replayed in ci (#457)
- Kill gaps.py's mutation survivors, triage the rest (#458)
- The field census compares text, which it never had (#458)
- Kill the 2.1 reader's mutation survivors, triage the rest (#458)
- Kill the 2.1 writer's mutation survivors, triage the rest (#458)
- Kill the 3.x writer's mutation survivors, triage the rest (#458)
- Kill sdmx_ml/_common.py's mutation survivors, triage the rest (#458)
- Halve the unit tier's cost without changing what it checks
- Kill sdmx_json/_structure_ingest.py's mutation survivors, triage the rest (#458)
- Kill sdmx_json/writer.py's mutation survivors, triage the rest (#458)
- **bench**: Find the bundle the #492 rules accept
- **query**: Pin _window's gaps and delete its dead n_unique entries (#324)

## [0.67.0] - 2026-10-08

### Bug Fixes

- **model**: Every Ref field wraps an SdmxUrn and refuses URN text — #418
- **json**: The structure writer refuses a version its schema cannot carry — #417
- **validation**: Organisation-scheme version is 1.0 in every format — #416

### Miscellaneous

- **test**: Run the unit tier in parallel with pytest-xdist

### Performance

- **storage**: Read each artefact's spine once, and its name and description together — #426

## [0.66.1] - 2026-10-07

### Bug Fixes

- **formats**: Convert data batches with pl.from_arrow again — #427
- **storage**: Clear declared targets when re-adding a Metadataflow or MPA — #428

## [0.66.0] - 2026-10-07

### Bug Fixes

- **types**: Return-widening audit — two declarations corrected, and a guard for the shape
- **types**: A URN parsed from text is SdmxUrn[object]; parse on the class to type it — #399

### Miscellaneous

- Pyrefly is the only type checker; retire basedpyright — #425 step 4

### Testing

- Narrow Optional attribute access with present() — #425 step 1
- Type the test suite for pyrefly — #425 step 2
- One erase helper for Plan roots, stub packages, last misuse lines — #425 step 3

## [0.65.0] - 2026-10-07

### Bug Fixes

- **local**: Refuse a series-attributes sidecar that is not unique on its join keys — #412
- **polars**: Give validation_report and Dataset.pivot a stable order
- **query**: ObsQuery.col() declares Untyped, so SDMX-REST's co/nc/sw/ew are visible — #413

### Features

- **query**: Query.agg and sl.len() — several aggregates over a whole query in one scan — #414

## [0.64.0] - 2026-10-06

### Bug Fixes

- **formats**: The SDMX-JSON data writer reads the codes a message observed — #410
- **query**: Time columns compare instants, whatever zone the literal or session is in — #415

### Documentation

- Binding cost is pandas' absence, not DuckDB's speed — #411
- Cast OBS_VALUE before aggregating it — polars 2 refuses a string mean

### Miscellaneous

- **deps**: Pin basedpyright to 1.39.9 — pyright 1.1.412 regression

### Performance

- **query**: Is_in binds its set as one list parameter, = ANY(?) — #411

## [0.63.0] - 2026-09-28

### Bug Fixes

- **typing**: Ref is covariant — eight Any that were never needed

### Features

- **model**: Ref_to on every item-bearing scheme — #407
- **polars**: Coded_as="string", so validate can actually report — #408
- **csv**: Scan_csv can use the structure the file names — #409

## [0.62.2] - 2026-09-28

### Bug Fixes

- **api**: A written artefact states its URN, and get() takes expect= — #406

## [0.62.1] - 2026-09-28

### Bug Fixes

- **model**: Validate_references resolves item and version-less refs — #404
- **model**: An item URN resolves to the item, at every layer — #405

### Documentation

- **typing**: #399 is a checker deviation, not an overload trade-off

### Refactoring

- **storage**: Item → owner comes off the item class, not a table — #405

## [0.62.0] - 2026-09-23

### Bug Fixes

- **local**: The data plane was unreachable on a real bundle — #387
- **validation**: Walk refs by class shape, not by a hand-listed descent — #148
- **model**: Resolve Refs held in a plain list or tuple — #392
- **model**: All_artefacts() is every slot, and storage says why it refuses — #394
- **model**: The four get()s take the same two things — #370
- **local**: Get() declares the lazy classes it returns — #397
- **local**: Refuse a store whose scans lose rows, and move to duckdb 1.5.5 — #400
- **model**: CodelistLike — one declaration for the eager and lazy codelist — #398
- **model**: Every immutable-collection field converts what it is given — #371

### Documentation

- **hierarchies**: Intersect two subtrees in the engine — #375
- **query**: Record why is_in still binds one placeholder per value — #301
- **discovery**: Document splicing a compiled query into your own SQL — #393
- A guard has to stay able to fail, and eight that had stopped — #403

### Features

- **query**: Order_by(None) declines the total order — #389
- **storage**: Store the two SDMX 2.1 grouping containers — #395
- **query**: Collect() returns the artefact, not object — #396
- **storage**: Storage kinds for the three remaining maps — #395

### Miscellaneous

- **claude**: Drop dead Write() ask rules from settings
- **storage**: Delete a dead istring converter — #154

### Performance

- **storage**: Share the empty istring and the empty Annotations — #377
- **storage**: Collect() looks a level up instead of rebuilding one — #377
- **storage**: Let DuckDB serialise the label maps, not materialise them — #380
- **query**: Collect(annotations=False), refused where it cannot be honoured — #381
- **model**: Decide once per class which fields can hold a Ref — #376
- **registry**: Resolve the item refs asked for, not their whole scheme — #378
- **storage**: Stop materialize building a Level per node to discard it — #154
- **model**: Three fixes in the ref-walk and assignment path — #383 #384 #154
- **query**: Collapse the ancestor EXISTS to a containment test — #349
- **query**: Emit the sidecar join only where a column reads it — #388
- **local**: Bound the resolver cache in items, not entries — #386
- **local**: Derive an item's parent scheme URN once per scheme — %18
- **storage**: Build item URNs once per artefact, not once per item — #139
- **storage**: The cascade runs only the deletes its type can have rows for — #144
- **model**: _by_type is keyed by URN, like _store — #138
- **storage**: The cascade finds annotations by an indexed column — #145

### Refactoring

- **formats**: One v3 reader for 3.0 and 3.1 — #346 option 1, reader half
- **formats**: One v3 writer, and the gap 3.1 was hiding — #346

### Testing

- **bench**: Benchmark against a real bundle, and make the skip audible — #379
- **bench**: Replace %18's unreachable 5x criterion — #382
- **bench**: Replace %18's 1 s / 300 MB criterion with a per-item budget
- **bench**: Read a real bundle's observations — #391
- **bench**: Put the bundle tier's assertions in `just ci` — #390
- **formats**: The fork guard now watches module-level data too — #346
- **types**: Basedpyright over integration, docs and bench — #347
- **types**: Basedpyright over tests/unit — #347
- **bench**: The bundle tier watches the write path — #140
- **bench**: The construction tax is a count, not a clock — #401
- **dev**: A docstring gate with three arms, all at zero — #374
- **query**: Mutation installment 2 — the two discriminator clauses, and a dead runner
- Anchor every test to repo.ROOT, and guard the spelling that got past #252 — #402

## [0.61.0] - 2026-08-25

### Breaking Changes

- **storage**: An absent reference is NULL, not the empty string
- **query**: Where= on every aggregate — collect some of the fan-out — #373

### Bug Fixes

- **tooling**: The index guard cannot scan its own fixtures
- **docs**: The upgrading guide's new blocks run
- **query**: Where= takes one predicate, and says why — #373

### Documentation

- **upgrading**: Bring the guide to 0.61

### Features

- **query**: An association reaches the component it binds — #372
- **query**: Q.hierarchy_applicability — the resolution rule, stated once — #372

## [0.60.0] - 2026-08-24

### Breaking Changes

- **model**: Drop Attribute.related_measure — #360
- **model**: The collections are honestly unhashable — #290

### Bug Fixes

- **formats**: Validity dates on eight maintainables per version; declare the HierarchicalCode name
- **sdmx-ml 2.1**: Annotations on hierarchies, their nodes, and the four map types
- **formats**: DimensionConstraint in 3.1, a Level's uri in JSON, MSD layers declared
- **formats**: A Group's and a Category's uri; declare that an Agency has no parent
- **sdmx-ml 2.1**: The last of #355 — four defects fixed, two declared
- **api**: Every type in a public signature is importable — #299
- **json**: Emit a value's parent in data messages — #365
- **formats**: A <Ref> without @package is read, not dropped — #240
- **bench**: The concurrency bench had rotted to an ImportError

### Documentation

- A tutorial for the query language, beginner to advanced
- A tutorial for querying observations
- **upgrading**: Bring the guide up to 0.60

### Features

- **json**: Carry annotations in data messages — #366
- **model**: Model AnnotationValue, which was read and dropped — #368
- **model**: Model MetadataProvider, so the MPA's provider urn resolves — #315

### Testing

- **formats**: Split the register into declared limits and real defects
- **fixture**: Keep the <xs:choice> sweep #359 asked for
- **tooling**: Gate dead suppressions, and type-check tests/support — #347
- **formats**: Say where the 3.0/3.1 fork differs, and close the drift — #346

## [0.50.0] - 2026-08-20

### Breaking Changes

- **reader**: Reject class-mismatched references at the read boundary
- **formats**: Validate on serialise by default; refuse measure-less 2.1 DSDs
- **validation**: Require a name on every NameableArtefact
- **storage**: Delete ingest_registry — dead since it was written
- **lint**: Turn ANN401 on everywhere; one scoped noqa replaces 3 directory ignores
- **dsl**: Label folding is opt-in, and both DSLs have it (#255)
- **dsl**: One generic predicate AST for both query DSLs (#267)
- **dsl**: Rename to ancestor_at, make self-inclusion an explicit flag
- **dsl**: Make select/group_by lazy, referenced by object
- **typing**: Delete the AnyRef alias, and teach the gate to follow aliases
- **dsl**: Limit composes as a cap, plus the three missing algebraic laws
- **query**: No implicit execution in the four DSLs
- **api**: Query[T] loses the refinement surface that never refined
- **query**: Retire MetadataValueQuery — the first DSL onto the new one
- **query**: Tree relationships, and composite edges to carry them
- **query**: Trim the hop that only recovers a key the row already stores
- **query**: The escape hatch is a terminal, not a step
- **query**: Node.level as an edge, and an inventory that finds the next one
- **query**: A foreign key an edge covers is storage, not API
- **query**: Name every accessor after its SDMX class
- **query**: Provision_agreement.data_provider pointed at the wrong relation
- **query**: Constraints and hierarchy associations — #308 by layout, not machinery
- **query**: Retire ArtefactQuery — the first of the three DSLs to go
- **query**: Count() comparisons in filter position; none() withdrawn
- **query**: Refuse every operator that would silently answer for us
- **query**: Retire CodeQuery and HierarchyNodeQuery — the DSLs are gone
- **query**: The representation surface, and the layout that made it drift
- **model**: Concept.parent — the column existed, the feature never did
- **storage**: Contacts belong to an organisation, not an agency
- **query**: The path language over REST, and reg.query(T) retired
- **data**: The data plane takes no SQL text
- **rest**: The key's shape depends on the API version, so the version is a parameter
- **model**: .urn is a property; urn_for builds one
- **query**: An observation result is named by component id
- **api**: One get(), with resolution depth as a parameter
- **design**: Step 5's alias deletions do not survive the evidence
- **local**: A registry answers for observations, not only artefacts
- Delete FederatedCatalog — a registry is the whole surface
- **formats**: Writers take (dsd, dataflow_ref), not a binding
- **api**: A `latest` URN is a pointer, not a cache key
- **api**: Sl.Registry -> sl.LocalRegistry, materialise_to= -> store=
- **query**: Refuse a relation spliced across two databases
- **api**: Unreachable raises and names itself; absent returns None
- **query**: N_unique counts a null as a value, as polars does
- **api**: The session records how completely a URN resolved, not whether
- **typing**: List[object] for decoded cells, and warnings fail the gate
- **query**: The anti-join was empty, and version.max() said 1.9 > 1.10
- **query**: Edges name their target, so file order is no longer API
- **query**: Union across two databases is refused, as intersect already was
- **query**: Project the accessor's name, and add alias() to rename one
- **query**: [] for an empty fan-out, eager to-many refusal, ordered aggregates
- **json**: SDMX-JSON no longer carries isFinal

### Bug Fixes

- **typing**: Make SdmxUrn[T] covariant; annotate empty-container fallback
- **api**: Stop HttpxOptions narrowing params/cookies below httpx
- **typing**: Close the leaky `-> Any` returns; guard URN-text ref resolution
- **formats**: Validate emitted XML in the property guard; fix 5 conformance bugs
- **docs**: Name the artefacts in tutorial and cookbook examples
- **typing**: Narrow the writer dispatch tables from Any to Callable
- Route messages on the envelope, not on what the content says
- Reject XML-incompatible text at validation and the JSON read boundary
- Escape LIKE metacharacters in query filters; add DSL differential tests
- Eager hierarchy read dropped has_formal_levels; semantic test audit
- Make unserialisable DataStructures fail loudly instead of silently
- Validate() applies the id type the schema actually uses
- Organisation scheme ids are fixed by the schema; add org + hierarchy coverage
- **conformance**: Close all 24 pairs that serialised to invalid documents
- **validation**: Apply NCNameIDType only where the schemas do
- **sdmx-ml 2.1**: Emit the TimeDimension position
- **sdmx-ml 2.1**: Read MetadataStructures instead of discarding them
- Carry enumeration_format in every format, not just 2.1
- **model**: DataConstraint carries a DataKeySet
- **dsl**: Compile annotation predicates as EXISTS, not JOIN (#254)
- **rest**: An empty OR-set matches nothing, in both directions (#256)
- **dsl**: Is_in rejects a bare string instead of iterating it (#261)
- **typing**: Close the Any enforcement gap the docs assumed was closed
- **dsl**: Reject a literal the column cannot hold (#272)
- **session**: Keep cursor_for off the public surface, and say what open_sessions counts
- **query**: Case is a rung on the fold ladder, not the floor of it (#222)
- **validation**: Refuse a versionless reference on 3.0/3.1/json (#233)
- **validation**: Audit every xs:unique, and close the one gap it found (#231)
- **metadata**: Extend conformance coverage to MetadataMessage, and fix what it found (#232)
- **validation**: A MeasureDimension is 2.1-only and must not vanish quietly (#305)
- **sdmx-ml**: A bad constraint role is a conformance error, not a ValueError
- **query**: Parenthesise union branches so a sliced operand parses
- **query**: Order versions numerically — 1.9 before 1.10 (#311)
- **model**: Eight significant version parts, not three — 1.2.3.4 tied with 1.2.3.9
- **model**: Classify version compliance, and refuse it at the writer (#319)
- **storage**: Refuse a bare item where a Ref is declared (#279)
- **storage**: Preserve the instant in a zoneless TIMESTAMP column (#321)
- **storage**: Materialize() answers what the proxy already knew (#322)
- **storage**: Read the spine from one place, not nineteen (#320)
- **storage**: Persist Dataflow.dimension_constraint (#323)
- **storage**: Persist codelist extensions, constraint schedule, level facets (#160)
- **tools**: Run mutmut's parent-side test phases in a forked child (#314)
- **query**: A windowed count counts values, not rows (#304 triage)
- **data**: Refuse a second statement in a composed data-plane query (#297)
- **query**: A column whose type nothing states, and two window bugs (#328)
- **data**: Labelling silently did nothing, and observations now project by id
- **query**: The two data capabilities differ on time, not only on attributes
- **api**: RestRegistry states a thread-safety contract, and meets it
- **tests**: Re-measure value_set; DuckDB binds 25x faster than documented
- **local**: Name the unattached catalog instead of leaking a binder error
- **query**: A REST query answers from its own fetch, not from the store
- **query**: Route a metadataset request to /metadata, not /structure
- **query**: A projected timestamp carries its zone, as the artefact's does
- **formats**: SDMX-ML 3.1 stops dropping what it never learned to carry
- **ml31**: The 3.1 writer was dropping ten kinds of annotation
- **formats**: Is_final — stop emitting it where forbidden, read it where it exists
- **model**: Wire MetadataProvisionAgreement into StructureMessage
- **formats**: Validate what the census writes — 30 conformance errors, four real

### Documentation

- **claude**: Note the just pyrefly implicit-any guard in the typing section
- **reader**: Record the ValueList caveat on the codelist-ref witnesses
- **typing**: Record the measured reason for SdmxUrn's UP046 noqa (#201)
- **storage**: Document the lazy facade properties; removes 20 D102 noqas
- **pyrefly**: Correct the unused-type-ignore rationale (setting unchanged)
- Adopt a written public/private rule; retire SLF001 (#207)
- **formats**: Explain what c/s/m qualify
- The JSON parse boundary is settled, not pending
- Reference the filed issue number (#225, not the assumed #226)
- Reference the filed issue number (#226)
- Reference the filed issue number (#234)
- **claude**: Record the differential-oracle testing discipline
- **dsl**: Correct the reason the class hierarchy is not yet shared
- **design**: Record the IR decision for the query DSLs (#273)
- **sql**: Say why each CompileContext field is dormant, and pin the live one (#268)
- **design**: Record what #280 measured about naming and joining relations
- **design**: The query model belongs in its own namespace, not on the domain classes
- **design**: The path machinery is cheap, and it collides with Ref navigation
- **design**: To-many resolves for filters and strengthens the design; projection does not
- **design**: Fan-out, the array-agg refusal, and every consumer shape checked
- **design**: Naming collides with the REST surface; fan-out N+1 is the real client shape
- **design**: One language, several backends — and supersede the no-IR decision
- **design**: Nothing is reachable by one path, so the path must be stated somewhere
- **design**: Write the query DSL out as code, and let real rows correct it
- **design**: Strict laziness retires Query[T]; the row kind keeps the entries apart
- **design**: 0-of-32 was measured over tables; over types it is 23 of 33
- **design**: Form B has no ambiguity; the row kind belongs to the root
- **design**: What a row kind is for, and why the new model may not need one
- **design**: Dropping RowKind's Protocol is free; dropping the parameter is not
- **design**: The path is the proof — why a bare reference cannot be checked
- **design**: The parent-level .any() failure, with rows — and one claim walked back
- **design**: Six ways to reach a grandchild through a to-many, three prototyped
- **design**: Strictness comes from naming every edge, not from .any()
- **design**: What binds the child accessor inside .any(), and what it cannot bind
- **design**: Prototype .every() and the HOAS binder; the self-join falls out
- **design**: Write up the polars-style branch as a worked alternative
- **design**: Split the evidence out of query-language.md into its own file
- **design**: Drop the spent corrections; move rejected branches to explorations
- **design**: A cookbook for the polars-style DSL, and rename scan -> query
- **design**: Rewrite the examples doc in the polars spelling
- **design**: Hostile review — the conjunction hole, and .any() returns as a binder
- **design**: Keep the escape hatch, and a thorough what-we-cannot-express pass
- **design**: Folding is a must-keep; cross-schema and cross-plane joins answered
- **design**: Specify the five remaining expression features
- **design**: No, we are not a pipeline — but two new clauses need naming
- **design**: Add qualify(); restrict unique() to full rows
- **query**: Why artefact.agency_id is a column, measured rather than asserted
- **query**: Record the Agg.__eq__ suppressions as approved
- **cookbook**: The availability capability, and two claims that were false
- **query**: The global-rank reading is reachable by nesting, not by call order
- **design**: `.every()` on nested fan-outs is not refused — the compiler solved it
- **design**: Pushdown — and the IR §4 asked for does not need building
- **design**: REST is not a narrower DuckDB — four places the plan must bend
- **design**: Fold §9's consequences back into the shapes they invalidate
- **design**: The pass — §1, §2, §5, §6 read against §9
- **design**: Materialise or query through — the cost is per artefact
- **design**: Re-measure §12 — the first numbers were measuring a defect
- **design**: The executor is a fetch planner, and the fetch must close
- Point Request.detail at #318
- Name the version-grammar divergence at both sites (#319)
- **cookbook**: The six undocumented terminals — and a bug writing them found
- **design**: The data plane needs the language it already has (#297)
- **design**: A second consumer, and two shapes re-rooting cannot reach (#297)
- **design**: The DSD already types the columns, and three questions settle (#297)
- **design**: Registry and catalog are one concept, and the names are inverted
- **design**: Provenance is the third dispatch, and it is the one nobody wrote down
- **design**: Cut the deliberation, keep the decisions and the examples
- **design**: Observations names the relation; download is REST-only
- **design**: Query names a relation, observations constructs one
- **design**: Get is the chain's read, not a query entry
- **design**: Retrieval versus computation is a property of the plan
- **design**: The wire's three planes are not the API's split
- **design**: Metadata on a series is already data; the vocabularies compose via subquery
- **design**: Audit the surface — 12 core entries by rule, 16 in the tail
- **design**: Consolidate — three ideas, the decisions, the audited surface, eleven open
- **design**: All eleven settled, and how it lands
- **design**: Fix what a clean-room review found — two decisions were wrong
- **design**: Pushing an observation query to SDMX REST
- **design**: The bare unbounded scan is deliberate, and §5.2 now needs re-deciding
- **design**: One refusal rule for both planes — visibility, not magnitude
- **cookbook**: An ordered stream is not bounded, and chunk_rows is not the knob
- **design**: Record what implementation taught, before the record is merged
- **design**: Restore the hand-formatting in registry-and-catalog examples
- **design**: §5.16 is filed as #338, and is sharper than it was written
- **design**: Record §5.13's decision; guard cassettes against outliving tests
- **local**: Repair four docstrings the LocalRegistry rename walked through
- An upgrade guide, and repair what 44 breaking changes left behind

### Features

- **sdmx-ml 2.1**: Write and read metadataflows
- **model**: Carry EnumerationFormat alongside an enumerated representation
- **model**: Model the 2.1 container artefacts StructureSet and HierarchicalCodelist
- Convert AttachmentConstraint to DataConstraint instead of refusing
- **model**: Model the 2.1 MSD reporting layer, making 2.1 MetadataStructures writable
- **sdmx-ml 2.1**: Write StructureMaps, refusing only what cannot be derived
- **model**: Carry artefact @uri through every format
- **dsl**: Add is_in — set membership, eager or lazy (#261)
- **dsl**: Ordered comparisons on depth, and a docstring that runs (#263)
- **dsl**: Hierarchy rollup as a relation, not a Python pipeline (#274)
- **dsl**: Project and aggregate structural queries into frames
- **dsl**: Query the artefact spine — catalogue discovery
- **dsl**: Query reported reference metadata
- **dsl**: Add &, | and ~ combinators to the query DSLs (#262)
- **dsl**: Add annotations[type].exists(), and fix predicate nesting (#292)
- **dsl**: Add offset(n), with a window that narrows (#264)
- **dsl**: Canonical_key() — a stable key for a query's meaning (#270)
- **session**: One execution handle per thread, with somewhere to put its state (#300)
- **session**: Value_set — register a large is_in set instead of binding it (#271)
- **query**: Declare the edge graph, with cardinality derived not declared
- **query**: Accessors that record typed paths over the declared edges
- **query**: Fan-out — dot-through for independent children, .any() for one
- **query**: Role facets, so dsd.dimensions matches the docs
- **query**: Resolve paths to hops, and hops to a deduplicated join tree
- **query**: Compile predicates to SQL, with EXISTS for every fan-out
- **query**: Query[R] — strictly lazy, refining, with terminals
- **query**: Reg.query() — the front door, resolving real artefacts
- **query**: Select() and projection, with fan-outs collected not multiplied
- **query**: The .str folding ladder, and localised labels
- **query**: Maintainable labels, via the localized_text side table
- **query**: Annotations, and the dataflow root they exposed as broken
- **query**: Dataflow.dsd reaches the DSD's own surface
- **query**: Order_by, and the tiebreak that makes paging mean anything
- **query**: Set operations and unique(), where the principle splits them
- **query**: Window functions and qualify — the latest-version query
- **query**: Keyset pagination — paging that does not skip or repeat
- **query**: Computed columns — arithmetic and concatenation
- **query**: The raw-SQL escape hatch — reg.sql() and sl.schema
- **query**: Group_by, and the third reading of an aggregate
- **query**: Q.hierarchy and q.node — the tree relation as an ordinary root
- **query**: The reference-metadata relations, and a footgun removed
- **query**: Resolvers are per root, and q.code collects real Codes
- **query**: Canonical_key for the new language, and the collisions it found
- **query**: Categories, categorisations, and the artefact type column
- **query**: A Query is a SqlRelation, so one query filters another
- **query**: Ancestry is a path, because it was never recursive
- **query**: Q.msd, q.metadataflow and q.metadata_attribute
- **query**: Q.group and q.dsd.groups, through the GroupDimension junction
- **query**: Q.representation_map and q.value_map
- **query**: Q.contact and q.metadata_provision_agreement
- **query**: Close the ArtefactQuery parity gap, and fix a leaf the compiler skipped
- **query**: None() — the third quantifier, and a home for leaves()
- **query**: Resolve hierarchy placements, and move the resolvers off Registry
- **query**: Capability and the program a plan becomes
- **query**: The refusal rule — boundedness, not residuals
- **query**: Rebase — and the correction that it was checkable all along
- **query**: Survey REST before building it — and a defect it found
- **query**: Projection is available on REST — and REST has a join
- **query**: The planner finds the rewrite — and the survey's number falls
- **query**: Scannable roots and a per-root bound — the message, not the count
- **query**: Compute what a fetch must close over (§13)
- **query**: A Push as an SDMX REST request
- **query**: Fetch, materialise, ask locally — with the differential that gates it
- **formats**: Ask what a format cannot carry, and declare a target that refuses
- **formats**: The store is a target too — item uri is lost, and now says so
- **query**: Derive detail= from what a projection reads (#318)
- **query**: Unique() — which distinct values, not how many
- **query**: Sum, mean, min and max (#327, #297 step 3)
- **query**: A dataflow's observations as a query root (#297 step 4)
- **query**: Reach observations by dataflow URN, and name columns off the query
- **query**: A component filter narrows a typed observation query (#329 step 2)
- **query**: Stream() — Arrow batches from a typed query (#329)
- **data**: Declare and write a sort order for observations (#330)
- **query**: An ordered stream at bounded memory, by partition (#331)
- **catalog**: Dsd(flow_urn) — the structure labelling needs
- **query**: Labels as an argument on the terminal (#329)
- **catalog**: Sql() — the raw escape hatch, and why the deletion waits (#329)
- **query**: A series attribute is a column, wherever it is stored
- **model**: ComponentRole — what a backend capability can be declared over
- **query**: SDMX REST data capabilities, declared over roles
- **query**: The data scan's bound, and the provider's own refusal
- **query**: Lower an observation query onto a /data URL
- **api**: Observations over HTTP — RestRegistry.observations
- **query**: Explain() says when a query fetches, and stream() scopes its promise
- **api**: Download() asks for a format, and checks it arrived
- **storage**: Record when an artefact was fetched, not only from where
- **query**: Wire detail=allstubs through, so a projection fetches stubs
- **api**: A freshness window for the `latest` pointer, from the provider
- **api**: FederatedRegistry.get takes resolve= and partial=, translated
- **query**: Having(), and a projection you can name its own items in
- **query**: Closed vocabularies are checked, and a colliding projection is refused
- **query**: Version ranges, validity, sl.union — and two refusals in our words
- **json**: Our SDMX-JSON is valid 2.1 as well as 2.0

### Miscellaneous

- **pyrefly**: Pin pyrefly, scope to implicit-any, add just recipe
- **checkers**: Ruff 0.16, pyrefly preset=all, pinned versions
- **lint**: Allow `id` to shadow the builtin in config, not 35 times inline
- **typing**: Migrate the last 22 `type: ignore` to per-checker syntax (#195)
- **lint**: Annotate alternate-constructor locals; 28 SLF001 noqas removed (#198)
- **api**: Drop the dead wire-parameter presets and fix a stale CLAUDE.md example

### Performance

- **dsl**: Drop runtime generic subscripts from the shared refs
- **query**: Register the key set when resolving, instead of binding it
- **storage**: Skip the cascade for an artefact that is not stored (#317)
- **storage**: Bulk-insert DSD groups instead of a statement per member
- **query**: An unordered stream is bounded — 441 MB to 5 MB (#329)
- **api**: DuckDB reads the fetched CSV itself — 30% faster, 2.1x lighter

### Refactoring

- **sdmx-json**: Type _maint_fields with a Protocol + TypedDict
- **model**: Narrow read-seam Protocols so registries stop being `object`
- **api**: Type the httpx pass-through with PEP 692 Unpack
- **typing**: Type the 9 Any params that already had a concrete type
- **typing**: Type the duck-typed writer/storage params (#203)
- **typing**: Type 7 of #204's "justified" Any sites; document the 1 real one
- **model**: Route Ref resolution through a package-internal seam (#208)
- Name the stages in _serialise_key and _resolve_refs (#199, partial)
- **formats**: Drop the legacy helper aliases; call helpers by name (#209)
- **formats**: WriterHelpers is a class, not a closure factory (#209)
- **formats**: ReaderHelpers is a class; deletes three workaround Protocols (#209)
- **formats**: Read with lxml prefixed paths, not tag-building helpers (#209)
- Retriage complexity suppressions, 5 -> 1
- **dsl**: Share the half of the two compilers that carries no types (#267)
- **typing**: Replace dict[str, Any] where the shape is known
- **typing**: Make Ref covariant — 55 of 56 Ref[Any] were variance, not gradual typing
- **typing**: SdmxUrn[Any] — 18 were pure consumers, 5 recover the parameter
- **typing**: List[Any] — type what can be, document what cannot
- **dsl**: Make the query compilers total via ColumnRef (#259)
- **dsl**: State the refinement principle and enforce it (#293, #296)
- **dsl**: Sort_by replaces, takes several keys, and clears (#294)
- **dsl**: Rename sort_by to order_by on the SQL query DSLs (#295)
- **api**: Export the types the query DSLs name in their signatures (#265)
- **storage**: Move the batch resolvers into their own module
- **query**: Extract Plan — what a query says, split from how it runs
- **query**: The root's schema is a property of the plan (#297 step 1)
- **query**: A join is ON an expression or USING columns (#297 step 2)
- **query**: Read a root's identity from the instance, not the class

### Testing

- Share the round-trip harness and fixture loaders
- Share the DSD builders, keeping the code choices visible
- Share the binding, URN, cursor and MSD builders
- Share the observation, metadataflow and catalog builders
- Repair the integration/live split and mark the slow tests
- Mutmut pilot — 807 mutants, 153 survive
- Hypothesis foundation — strategies, profiles, first properties
- Stop rationing Hypothesis examples for CI cost
- Generative round-trip properties across every serializer
- Schema-oracle coverage for DataStructure and Dataflow
- Check the model against constraints read out of the XSDs
- **matrix**: Cover five more classes, and stop 2.1 dropping artefacts silently
- **matrix**: Complete the spec-coverage matrix; six more conformance fixes
- **matrix**: Cover the metadata envelope; four conformance fixes
- **conformance**: Gate every artefact x format on valid-or-refused
- **xsd**: Extract required children and required choices from the schemas
- **xsd**: Extract required and prohibited attributes; three findings
- **support**: Document-level round-trip fidelity harness
- **conformance**: Assert every SDMX-ML version hop, and refuse two silent 3.1 losses
- **fuzz**: Mutate documents until only one exception escapes parse
- **fuzz**: Replace a guessed coverage figure with the measured one
- **dsl**: Oracle-backed properties for both query DSLs (#248, #249)
- **dsl**: Fix two oracle bugs the 20x Hypothesis run found
- **bench**: Query-DSL benchmarks and a SQL-equivalence corpus (#267)
- **bench**: Calibrate the harness for microsecond-scale benchmarks
- **dsl**: Misuse tests — assert the wrong call fails, and says how to fix it
- **dsl**: Hostile filter values across every string position
- **dsl**: Structural AST invariants over every emitted SQL
- **dsl**: Differential properties for the data plane, against a Polars oracle
- **model**: Triage collections.py's surviving mutants, and unbreak the gate
- **api**: Generative coverage for item(), Query[T] and the SQL seam
- **artefact-query**: A single-case vocabulary made every case assertion vacuous
- `just mutate` aborted at collection and had done so since #265 (#252)
- The 356 mutation "timeouts" were macOS fork-safety segfaults (#252)
- **query**: Hold the new compiler to the old one's oracle
- **query**: Rebuild the SQL corpus, and close five survivors in _plan.py
- **tools**: Mutate_union — light selections, unioned, because both single numbers lie
- **query**: Run §11's projection claim over REST instead of asserting it
- **storage**: A derived round-trip guard, and the four defects it found (#316)
- **mutation**: Answer #218's properties-vs-examples question, and widen the scope (#304)
- The time-role assertion holds on both key shapes
- **integration**: Re-record against ECB; no skipped integration tests left
- **docs**: Cover the ten user-facing pages that had no test
- **docs**: Run the code that is in the documentation, not a copy of it
- **docs**: Every page's own blocks run, and they found real defects
- **docs**: No block opts out; the six that did were hiding defects
- Close the gate holes a whole-codebase review turned up
- **docs**: One system — the pages run, and their printed claims are checked
- **bench**: The benchmark suite runs again, and guards #348's four shapes
- **query**: Pin the invariant the vocabulary check actually depends on
- **formats**: Compare every field against the model, not against a checklist
- **formats**: Round-trip real provider messages, not just a fixture
- **formats**: Hunt real examples for the missing fields — five were not missing

## [0.48.0] - 2026-07-31

### Features

- **metadata**: Emit native SDMX 3.0 rel="metadata" link in SDMX-ML 3.0 writer (#191)

### Miscellaneous

- **deps**: Widen uv_build pin to >=0.12.0,<0.13.0

## [0.47.0] - 2026-07-31

### Documentation

- **metadata**: Document DSD↔MSD association builders + native metadata link (#162, #191)

### Features

- **metadata**: Derive & splice DSD METADATA annotation for .Stat compat (#162)
- **metadata**: Derive DSD METADATA annotation from declared & wildcard targets (#189, #190)
- **metadata**: Emit native SDMX 3.0 rel="metadata" links in SDMX-JSON writer (#191)

## [0.46.0] - 2026-07-22

### Bug Fixes

- **hierarchy-query**: Reject bare Code in node code-field comparison (#174)
- **formats**: Make the reader ref-resolution witness load-bearing (#177)
- **typing**: Annotate 6 empty containers that inferred as Any
- **model**: Two real findings pyrefly caught that basedpyright missed

### Documentation

- **design**: Add JSON & Parsing note — the typedness spectrum + parse-vs-tree rubric
- **perf**: Record why JSON serialize is dict+orjson, not msgspec/cattrs

### Features

- **sdmx-json**: Msgspec migration foundation — shared DTO Structs + conventions (#178)
- **sdmx-json**: Structure write via Struct DTOs — item schemes (#180 part 1)
- **sdmx-json**: Structure write via Struct DTOs — DSD + dataflow (#180 part 2)
- **sdmx-json**: Structure write via Struct DTOs — orgs, categorisation, PA (#180 part 3)
- **sdmx-json**: Structure write via Struct DTOs — DataConstraint (#180 part 4)
- **sdmx-json**: Structure write via Struct DTOs — hierarchy (#180 part 5)
- **sdmx-json**: Structure write via Struct DTOs — MSD + metadataflow (#180 part 6)
- **sdmx-json**: Structure write via Struct DTOs — structure maps; closes #180
- **sdmx-json**: Structure read via typed decode + ingest — codelists (#181 part 1)
- **sdmx-json**: Structure read via typed decode — concept + category schemes (#181 part 2)
- **sdmx-json**: Structure read via typed decode — DSD + dataflow (#181 part 3)
- **sdmx-json**: Structure read via typed decode — orgs, categorisation, PA (#181 part 4)
- **sdmx-json**: Structure read via typed decode — DataConstraint (#181 part 5)
- **sdmx-json**: Structure read via typed decode — Hierarchy (#181 part 6)
- **sdmx-json**: Structure read via typed decode — MSD + Metadataflow (#181 part 7)
- **sdmx-json**: Structure read via typed decode — structure maps (#181 part 8, closes #181)
- **sdmx-json**: Metadata message read+write via typed msgspec DTOs (#182)
- **sdmx-json-1.0**: ContentConstraint writer via typed msgspec DTOs (#184)
- **sdmx-json**: Type the last codelist-extension dict island (#186)
- **metadata**: Model MetadataProvisionAgreement; constrain MetadataSet.structure; fix 3.0 metadata XSD conformance (#187)

### Miscellaneous

- Ignore .DS_Store
- **typing**: Add pyrefly.toml (fast editor pass) + Any/object conventions in CLAUDE.md
- **deps**: Swap lxml-stubs → types-lxml (dev)
- **deps**: Remove orjson — SDMX-JSON is fully msgspec (#185, closes milestone #8)

### Performance

- **serialize**: Decouple validation from serialize (validate=False default)
- **sdmx-json**: Dedup istring emit + skip the defensive map copy
- **sdmx-json**: Data-message writers/readers off orjson onto msgspec (#183)
- **sdmx-json**: Gc=False + emit_pair on wire DTOs — JSON serialize now beats pysdmx

### Refactoring

- **data-store**: Tighten SchemaInspector after the #163 quality audit
- **typing**: T1 model-core checked seams — drop convenience Any (#165 #166 #167 #168)
- **typing**: Apply object-by-default to clean consumer sites
- **typing**: Narrow artefact_urn to HasUrn (#165)
- **imports**: Hoist code_query's cargo-culted local imports to top-level
- **imports**: Sweep bare `# noqa: PLC0415` — hoist cargo-cult, document intentional
- **model**: Move artefact_urn to ref.py, breaking in_memory<->hierarchy cycle
- **storage**: Extract code_map to storage/_projections, breaking readers<->lazy cycle
- **data**: Move sink_json serialisation to the polars bridge, breaking model<->formats cycle
- **imports**: Hoist ref->base import; its # circular noqa was stale
- **typing**: Precise small Any narrowings (#172)
- **typing**: Narrow instead of cast in _component_label_map fallback
- **typing**: Replace the last cast in _component_label_map with a _HasCodeMap Protocol
- **typing**: Parameterize generic reader helpers (#171)
- **typing**: Type the existing-fit writer params (#170, partial)
- **typing**: Type the URN writers AnyRef, narrow the representation-map caller (#170)
- **typing**: Adopt Supports* convention for structural protocols
- **typing**: Narrow the reader resolution index to dict[str, object] (#169)
- **formats**: Witness-type reader ref resolution (#173)
- **duckdb**: Collapse scalar() helper to what it is — count()
- **sdmx-json**: Shared resolve_urn instead of the ingest callback (#181)
- **sdmx-json**: Snake_case DTO fields + rename="camel" (drop N815 noqas)

## [0.45.0] - 2026-07-22

### Features

- **data-store**: Public schema-introspection + query-planning seam for custom SQL (#163)

### Miscellaneous

- **lint**: Split multi-line docstring summaries in sdmx-ml21 reader/writer (D205)

## [0.44.1] - 2026-07-13

### Bug Fixes

- **sdmx-json**: Emit annotations on all artefacts and items (#157)
- **sdmx-ml**: Emit annotations on items and components (#158)
- **serializers**: Close asymmetric round-trip field drops (#159)
- **storage**: Persist facet detail, attribute relationships, contact fax/x400 (#159)
- **sdmx-ml21**: Emit concept_role on components and <str:Contact> on orgs (#159)
- **sdmx-json**: Component concept roles, hierarchy formal-levels/coding-format/validity, per-mapping validity (#159)
- **sdmx-ml30**: Persist Hierarchy Level codingFormat; mark remaining guard gaps by-design (#159)

### Miscellaneous

- **lint**: Replace RST :func: roles with plain markup in code_query docstrings

### Performance

- **sdmx-json**: Fast-path istring emit on the serialize hot path

### Testing

- **serializers**: Generic per-format round-trip property guard (#159)

## [0.44.0] - 2026-07-07

### Features

- **registry**: Reverse metadata lookup + typed section projection

## [0.43.0] - 2026-06-30

### Features

- **query**: Punctuation-insensitive name/title/text contains matching

## [0.42.0] - 2026-06-30

### Features

- **query**: Accent-insensitive name/title/text contains matching

## [0.41.3] - 2026-06-26

### Bug Fixes

- **deps**: Require duckdb>=1.5.3 for DuckLake catalog format 1.0
- **data_store**: Clear error on DuckLake catalog-format mismatch (#156)

### Documentation

- **design**: Add caching strategy decision framework

### Performance

- **storage**: Single-flight code_map cache to fix label thundering herd (#155)

## [0.41.2] - 2026-06-18

### Performance

- **sdmx_json**: Lazy per-code label lookup in ComponentTable

## [0.41.1] - 2026-06-17

### Bug Fixes

- **sdmx_json**: FREQ-driven multi-year TIME_PERIOD start/end bounds

## [0.41.0] - 2026-06-17

### Features

- **builders**: Per-component availability slices via build_component_availability

## [0.40.0] - 2026-06-17

### Features

- **builders**: Availability ContentConstraint builder + SDMX-JSON 1.0 writer

## [0.39.0] - 2026-06-17

### Features

- **sdmx_json**: Canonical per-Code/Concept value serializer

## [0.38.2] - 2026-06-17

### Bug Fixes

- **sdmx_json**: Emit dataflow display name + TIME_PERIOD concept name

## [0.38.1] - 2026-06-17

### Bug Fixes

- **sdmx_json**: Emit TIME_PERIOD start/end + only observed codes in values[]

## [0.38.0] - 2026-06-17

### Documentation

- Refresh examples for raising validate / split binding-drift APIs

### Features

- **validation**: Enforce SDMX IDType at every write boundary (#147)
- **validation**: Validate_references raises ReferenceValidationError
- **binding**: Split validate_binding into check_binding_drift + validate_binding
- **sdmx_json**: Streaming SDMX-JSON 2.0.0 data writer
- **sdmx_json**: Streaming SDMX-JSON 1.0 data writer
- **sdmx_json**: SDMX-JSON 1.0 / 2.0 data-message readers
- **dataset**: Sink_json — user-facing SDMX-JSON data sink

### Performance

- **sdmx_json**: Vectorise data-writer hot loop

### Testing

- **validation**: Close MetadataSet hole in walker coverage guard
- **sdmx_json**: Vendor 1.0 + 2.0 data schemas
- **sdmx_json**: SDMX-JSON 2.0 data writer conformance tests
- **bench**: SDMX-JSON data writer throughput benchmarks

## [0.35.2] - 2026-06-11

### Performance

- **storage**: _put_refs uses Arrow bulk insert (#146)

## [0.35.1] - 2026-06-11

### Performance

- **local**: Per-artefact + per-transaction DEBUG-level instrumentation

## [0.35.0] - 2026-06-11

### Documentation

- **api**: Drop stale validate_binding mkdocstrings entry

### Miscellaneous

- Satisfy lint+format on perf/139 branch

### Performance

- **storage**: Collapse _put_refs into a single executemany (#139)
- **local**: Wrap Registry.add_all in a single transaction (#139)
- **storage**: Collapse artefact name+description into one executemany
- **local**: Expose Registry.transaction() context manager

### Testing

- **bench**: Add ingest harness for issue #139

## [0.34.0] - 2026-06-09

### Breaking Changes

- **registry**: Replace default_hierarchy/validate_binding free functions with registry methods (#137)

## [0.33.0] - 2026-06-09

### Features

- **code_query**: Satisfy sl.sql.Predicate Protocol for foreign SQL embedding

## [0.32.2] - 2026-06-09

### Bug Fixes

- **ref**: Reject non-maintainable inputs to Ref/Ref.to

## [0.32.1] - 2026-06-08

### Bug Fixes

- **storage**: Persist HierarchyAssociation (#136)

## [0.32.0] - 2026-06-08

### Documentation

- Add llms-dsl.txt query-DSL reference for AI assistants

### Features

- **code-query**: Sl.code.descendants_of with optional hierarchy (#135)
- **hierarchy**: Default_hierarchy(registry, dataflow, dim_id) (#135)
- **hierarchy**: Hierarchy.descendants(root_code_id) pure-Python helper (#135)
- **sdmx-json**: HierarchyAssociation read/write (#135)

### Testing

- **hierarchy**: Multi-hierarchy + HA round-trip fixture (#135)

## [0.31.0] - 2026-06-01

### Features

- **model**: RepresentationMap source/target accept primitive dataType (#131)
- **model**: ConstraintAttachment.data_provider attachment kind (#130)
- **binding**: Require tz-aware materialized_at / bound_at (#92)

## [0.30.0] - 2026-06-01

### Bug Fixes

- **sdmx_json**: Conformance pass for simple artefacts; partial close #128
- **sdmx_json**: Conformance pass for DSD / DataConstraint / maps; close #129
- **sdmx_json**: Drop legacy-shape fallbacks; require conformant input
- **sdmx_json**: Emit codelistExtensions + concept coreRepresentation + Hierarchy levels; broaden fixture coverage

### Features

- **dsl**: Node/code.annotations[type][lang] targets text payload; close #127

### Miscellaneous

- **bench**: Add pytest-benchmark + pytest-memray harness (Phase 1)
- **bench**: Phase 2 — storage / LazyCodelist / local Registry
- **bench**: Phase 3 — navigation tax (elegance vs hand-built dict)
- **bench**: Add competitive parse/serialize bench vs sdmx1 + pysdmx

### Performance

- **model**: String-first dispatch in ItemList.__getitem__; close #118
- **model**: Slot-cache resolved codelist on Component; close #119
- **istring**: Intern language codes; close #116
- **urn**: Cache __str__ on the frozen SdmxUrn instance; close #117
- **sdmx_json**: Swap stdlib json → orjson; ~2.4× serialize speedup
- **sdmx_ml**: Precompute clark tags + empty singletons in hot read paths

### Testing

- **codelist**: Cover SKOS hierarchy methods; close #124
- **storage**: Round-trip 7 artefact types through DuckDB; close #125
- **hierarchy**: Cover LazyHierarchy + query execution DSL; close #122 #126
- **json-writer**: Cover untested artefact write paths; close #123
- **sdmx_ml31**: Port 7 fixtures from 3.0; close #120 #121
- **json-schema**: Vendor SDMX-JSON 2.1 schemas + validation helper

## [0.29.0] - 2026-05-31

### Documentation

- **claude.md**: Treat polars and duckdb as baseline runtime deps
- **claude.md**: SQL quote rule matches the community convention
- Document SDMX-ML XSD validation in CLAUDE.md (MR4)

### Features

- **model**: Expose concept_role on Dimension/Measure/Attribute (#82)
- Enum-ify usage + constraint_type; add PA.is_final (close #111, #112)
- 3.1-shaped Hierarchy + Level + HierarchicalCode (close #105, #106, #110)
- Add Dataflow.dimension_constraint (3.1 evolving-DSD support); close #113
- Add is_final to Dataflow, CategoryScheme, Categorisation, ConceptScheme; close #114
- Add Codelist.extensions (3.0 / 3.1 composition); close #109
- Discriminated-union ValueMap variants + per-mapping validity; close #107
- Add HierarchyAssociation; fix 3.0 Hierarchy XSD bugs (close #108)
- **xsd**: Attribute Group / Dataset attachment levels round-trip; close #98
- **xsd**: Contact multi-field + X400 + namespace fixes; close #99
- **format**: Add SDMX-ML 3.1 reader + writer; close #103, #104
- Add 2.1-only AttachmentConstraint artefact + serializers; close #115

### Miscellaneous

- **cleanup**: Phase 1 — small contract + dead-code fixes (#83)
- **cleanup**: Phase 2 — RST docstring sweep + public-API hygiene (#84)
- **cleanup**: Phase 3 — narrowing helpers + shared test conftest (#85)
- **cleanup**: Phase 4 — extract formats/sdmx_ml/_common.py (#86)
- **cleanup**: Phase 5 — dispatch consolidation + local/ rename (#87)
- **cleanup**: Followup phase 1 — high-level writer dedup + csv cast (#88)
- **cleanup**: Followup phase 4 — document the lint/typecheck disables (#91)
- **cleanup**: Followup phase 3 — more reader + writer dedup (#90)
- Include model: commits in CHANGELOG Features

### Refactoring

- **model**: Adopt 3.0-native mapping types; close #93

### Testing

- **xsd**: Vendor official SDMX-ML 2.1 + 3.0 XSDs (MR1 of XSD validation)
- **xsd**: Wire SDMX-ML 3.0 writer round-trips through XSD validation (MR2)
- **xsd**: Wire SDMX-ML 2.1 writer round-trips through XSD validation (MR3)
- **xsd**: Rewrite ml30/complete.xml to 3.0-conformant shape; un-xfail
- **xsd**: Exercise SDMX 3.0 MSD + Metadataflow; close #94
- **xsd**: Exercise SDMX 2.1 HierarchicalCodelist; close #95
- **xsd**: Vendor SDMX-ML v3.1.0 schemas + forward-compat audit; close #102
- **xsd**: Round-trip Annotations across all maintainable artefacts; close #96
- **xsd**: Exercise non-en/fr language tags incl. region-coded BCP-47; close #100
- **xsd**: Exercise ReferencePeriod + ReleaseCalendar; fix XSD order bugs; close #101
- **xsd**: Exercise role="Actual" + CubeRegion include=false; partial close #97

## [0.28.3] - 2026-05-28

### Bug Fixes

- **data-store**: SET GLOBAL azure curl transport + detect ducklake (#81)

## [0.28.2] - 2026-05-28

### Features

- **data-store**: Force curl transport when azure is needed

## [0.28.1] - 2026-05-28

### Documentation

- **concurrency**: Describe thread-local cursor model (#80)

### Performance

- **local**: Thread-local cursors to amortise lifecycle cost (#80)

## [0.28.0] - 2026-05-28

### Bug Fixes

- **local**: Per-call cursors + lock-free FIFO cache for concurrent reads

### Documentation

- Full audit pass — fix broken examples, add v0.27.2 surface
- **design**: Performance & caching architecture reference
- **design**: Concurrency model + deployment patterns (#79)

### Miscellaneous

- Stop committing uv.lock
- **lint**: Expand per-file-ignores for tests/bench

### Testing

- Reproduce concurrent-read corruption (#76)

## [0.27.2] - 2026-05-27

### Performance

- **local**: Bounded LRU resolver cache to amortise Ref-target reads (#74)

## [0.27.1] - 2026-05-27

### Performance

- **storage,local**: Batch annotation reads + memoize Ref resolution (#72, #73)

## [0.27.0] - 2026-05-11

### Bug Fixes

- **attach**: Split connection URL from trailing options
- **storage,tests**: Dedupe artefact_ref inserts; update lang= kwargs

### Features

- **bundle**: DataflowBinding.from_dsd, database_role, Azure ADLSv2

## [0.26.0] - 2026-05-10

### Features

- **sql**: Add CompileContext.next_alias() for predicate compilers

### Testing

- **sql**: Cover CompileContext.next_alias

## [0.25.0] - 2026-05-10

### Features

- **sql**: Lift SqlRelation + Predicate + CompileContext to public API

## [0.24.0] - 2026-05-09

### Features

- Path-style construction across Registry, DuckDBDataStore, FederatedCatalog

## [0.23.1] - 2026-05-06

### Miscellaneous

- Docs audit + tests for unified tree API; cleanup materialize

## [0.23.0] - 2026-05-06

### Features

- **hierarchy**: LazyHierarchy + HierarchyNodeQuery (SQL-backed tree API)

## [0.22.0] - 2026-05-06

### Features

- **model**: SKOS tree API on Hierarchy / CategoryScheme / MetadataStructure

## [0.21.0] - 2026-05-06

### Features

- **codelist**: Bulk tree projections — depths, broader/narrower/ancestors/descendants maps, inherit_along_broader (#63, #65)

### Miscellaneous

- **style**: Triple-quoted SQL, implicit-concat for prose

## [0.20.1] - 2026-05-05

### Bug Fixes

- **storage**: Drop INSERT OR REPLACE to avoid DuckDB index desync (#70)

## [0.20.0] - 2026-05-05

### Features

- **code-query**: Code.hierarchy(h).level(l) predicate (#68)

## [0.19.2] - 2026-05-05

### Bug Fixes

- **storage**: Cascade-delete hierarchy_level on Hierarchy upsert (#67)

## [0.19.1] - 2026-05-05

### Performance

- **storage**: Arrow-based bulk insert + batched annotation IDs (#66)

## [0.19.0] - 2026-05-02

### Features

- ComponentFilter accepts SqlRelation values for hierarchy filters (#62)

## [0.18.0] - 2026-05-02

### Breaking Changes

- ComponentFilter — uniform dim+attribute filtering, c[…]=… support (#56)

## [0.17.1] - 2026-05-01

### Bug Fixes

- **perf**: Write_sdmx_csv 50x+ faster via polars Rust CSV (#61)

## [0.17.0] - 2026-05-01

### Features

- REST facade kit — KeyFilter, streaming, SDMX-CSV writer (#55, #56, #57, #58, #59, #60)

## [0.16.2] - 2026-04-30

### Bug Fixes

- **perf**: Label/collect 60-180x faster on large codelists (#53, #54)

## [0.16.1] - 2026-04-30

### Documentation

- **cookbook**: Federation recipes — cache-mode, routing, binding, query lifecycle

## [0.16.0] - 2026-04-30

### Breaking Changes

- Federation foundation — Registry Protocol + DataflowBinding (#46, #47)

### Features

- Cache-mode federation — materialise-through + source tagging (#46 phase C)
- FederatedRegistry — agency-routed dispatch across sources (#46 phase D)
- FederatedCatalog — structural + bindings + data orchestration (#47 phase E)
- Drift validation + browse-across-federation (#46/#47 phase F)

## [0.15.0] - 2026-04-30

### Breaking Changes

- Codelist filter DSL + SKOS hierarchy nav + required lang (#48, #49, #50, #51)

## [0.14.1] - 2026-04-28

### Documentation

- **cookbook**: Add reference metadata recipe (#43)

### Miscellaneous

- **justfile**: Allow optional version override in release-2/3 recipes

### Testing

- Real-world reference-metadata coverage (#43, #45)

## [0.14.0] - 2026-04-28

### Breaking Changes

- **model**: Standards-compliant MetadataMessage envelope (#43, #44)

## [0.13.0] - 2026-04-28

### Features

- **formats**: SDMX-ML 3.0 read+write for MSD and Metadataflow (#43)

## [0.12.0] - 2026-04-28

### Features

- **model**: Add Metadataflow and broaden MetadataSet.structure (#43)

## [0.11.0] - 2026-04-28

### Features

- **model**: Add MetadataStructure (MSD) and MetadataSet (#43)

## [0.10.0] - 2026-04-27

### Bug Fixes

- **model**: Correct Measure/MeasureDimension docstrings and remove bogus Measure.usage
- **storage**: Persist DSD component annotations across round-trip (#42)

### Features

- **model**: Add Level to Hierarchy (#40) and rename ContentConstraint → DataConstraint (#39)
- **model**: Expose public artefact_urn() builder (#35)

### Miscellaneous

- Harmonize dev setup with pinax-suite conventions

## [0.9.0] - 2026-04-20

### Features

- **storage**: Lazy tree traversal API for codelists

## [0.8.1] - 2026-04-16

### Miscellaneous

- Add PyPI publishing support
- **lint**: Ignore S608 globally and remove noqa comments
- Update GitLab URLs to pinax-suite group

### Performance

- **storage**: Bulk INSERT for codelist codes (~60x faster)
- **storage**: Bulk INSERT for DSD components, concepts, and hierarchy nodes

### Refactoring

- Consolidate RepresentationMixin and fix version comparison bug
- **storage**: Fully relational DuckDB schema — no JSON blobs

## [0.8.0] - 2026-04-12

### Features

- **storage**: Shared DuckDB storage layer for LocalRegistry

## [0.7.1] - 2026-04-11

### Bug Fixes

- Handle list[str] fields in to_dict (Attribute.related_dimensions)

## [0.7.0] - 2026-04-10

### Features

- **local**: Resolve Refs automatically on LocalRegistry.get()

## [0.6.0] - 2026-04-10

### Bug Fixes

- **examples**: Derive UNIT_MEASURE from hasUom dimension members in transform_obs
- **examples**: Use memberUomCode not memberNameEn for UNIT_MEASURE

### Documentation

- Add Indicator Flows cookbook for measure_dim= pattern

### Features

- **examples**: Expand codr.py with full data pipeline
- Add LocalRegistry — SQLite-backed persistent artefact store

## [0.5.0] - 2026-04-09

### Documentation

- Update README for Provider shortcuts and resolution modes
- Add data cookbook and observation data tutorial
- Add missing pages to mkdocs nav (data tutorial, data cookbook, constraints, hierarchies)
- Add data layer design doc with shipped API and remaining work
- Update all docs to use sl.dim() API and reflect shipped data layer

### Features

- Add item() expression API and update docs/cookbook
- Implement REST query DSL with browse/fetch/resolve modes (closes #32)
- Add Polars-first data layer with Dataset, SDMX-CSV reader, and DataQuery
- Add MeasureDimension, Dataset.pivot() LazyFrame, and DataQuery.pivot/pivot_to
- Add sl.dim() / DataQuery.slice() / .measures() for named server-side filtering
- Add measure_dim= hint for pivot on regular indicator dimensions
- Make polars a required dependency and clean up import guards

### Refactoring

- Replace Dataset.label() with ds.code()/ds.label() expressions and collect(with_labels=True)

### Testing

- Add integration tests for .slice() with sl.dim() against live ECB endpoint

## [0.4.0] - 2026-04-03

### Documentation

- Add llms.txt AI-readable API reference and MSD design notes

### Features

- Add sl.read() and Registry local file loading API
- Migrate domain model from dataclasses to attrs

### Miscellaneous

- Restructure release workflow into 4 explicit phases and fix cliff.toml parse warnings

### Refactoring

- Rename Annotation.annotation_type → .type

## [0.3.0] - 2026-04-01

### Bug Fixes

- Semantic version ordering, narrower exception handling, O(1) ArtefactList lookup

### Documentation

- Update user-facing docs for TextFormat → Facet rename
- Update installation to git+https uv add (not yet published to PyPI)
- Ruff format Python code blocks in docs (remove custom alignment)
- Update all examples to current RestRegistry API

### Features

- Add add() and drop() methods to all scheme types
- Rename TextFormat → Facet, add DataType StrEnum
- Add pure-domain Registry and rename REST client to RestRegistry
- Wire Registry into format layer — parse(..., registry=reg)
- Wire RestRegistry to domain Registry — rest.registry property
- Add Provider — built-in SDMX endpoint shortcuts
- Add Hierarchy, complete ContentConstraint/StructureSet, valid_from/valid_to on all maintainables

### Miscellaneous

- Fix project description stub and remove stale TODO
- Refresh VCR cassettes and add IM coverage gap notes

### Refactoring

- Drop frozen from domain model, add HasUrn protocol, rename sdmx_ attrs
- Remove noqa/type-ignore suppressions via helper extraction and mixin annotations

### Testing

- Record VCR cassettes for new RestRegistry integration tests

## [0.2.9] - 2026-04-01

### Bug Fixes

- **model**: Correct ID/agency patterns per SDMX spec; add exhaustive regex tests
- Complete AgencyScheme/DataProviderScheme consistency across formats and model

### Documentation

- Add AI-generated code notice to README
- Update all documentation to reflect current API

### Features

- **formats**: Add SDMX-JSON 2.0 structure reader
- **formats**: Add SDMX-JSON 2.0 writer and integration tests
- **model**: Add Ref.to() classmethod
- **model**: Add ConceptScheme.ref_to(concept_id)
- **model**: __post_init__ coercion for plain-type construction (#14)
- **model**: Add DataStructure modification helpers (#15)
- **model**: Add from_dict/to_dict converters (#16)
- **model**: Add validate and validate_references (#17)
- Public API re-exports — to_xml, to_json, from_dict, to_dict, validate (#18)
- Parse and re-emit AgencyScheme / OrganisationSchemes (#20)
- ContentConstraint + CubeRegion round-trip fidelity (issue #21)
- StructureSet + CodelistMap round-trip fidelity (issue #22)
- Complete fixtures + round-trip acceptance tests (issue #23)
- CoreRepresentation on Concept with automatic inheritance (issue #24)
- Accept bare SdmxUrn on codelist/concept/structure fields (issue #25)

### Testing

- Add __post_init__ coercion tests for issue #14

## [0.2.8] - 2026-03-30

### Bug Fixes

- **formats**: Handle direct URN text content in SDMX 3.0 reader

### Documentation

- Add MkDocs site with cookbook, API reference, and GitLab Pages CI

### Features

- **formats**: Implement SDMX-ML 3.0 writer

### Miscellaneous

- Add LICENSE

### Testing

- **integration**: Add SDMX-ML 3.0 live integration tests

## [0.2.6] - 2026-03-30

### Features

- **formats**: Implement SDMX-ML 3.0 reader

## [0.2.5] - 2026-03-30

### Bug Fixes

- **session**: Implement resolved-tracking identity map for issue #1

### Features

- **formats**: Implement SDMX-ML 2.1 writer and serialize() entrypoint

## [0.2.4] - 2026-03-30

### Bug Fixes

- Apply all 12 code review fixes from v0.2.2 review

### Features

- **polars**: Phase 5 — Polars bridge

### Miscellaneous

- **dev**: Add polars to dev dependencies for type checking

## [0.2.2] - 2026-03-30

### Features

- **api**: Phase 4 — Session, Query, Registry, and typed URN generics

## [0.2.1] - 2026-03-30

### Features

- **formats**: SDMX-ML 2.1 reader with auto-resolved cross-references

## [0.2.0] - 2026-03-30

### Bug Fixes

- **justfile**: Replace @echo with echo in bash shebang recipe

### Features

- **model**: Implement Phase 2 domain model types
- **model**: Merge Phase 2 domain model types (#1)

## [0.1.0] - 2026-03-30

### Features

- **model**: Implement Phase 1 foundation types


