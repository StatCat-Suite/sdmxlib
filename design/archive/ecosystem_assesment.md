# sdmxlib API Ergonomics Audit — Comparison with sdmx1 and pysdmx

## Executive Summary

sdmxlib offers the strongest **type safety and navigation ergonomics** of the three libraries. It trades observation-data support and async for a tighter, more discoverable API built on generics and frozen dataclasses. The gaps are real but narrow and well-scoped for future work.

---

## 1. Registry / Client Setup

| Task             | sdmx1                         | pysdmx                               | sdmxlib                            |
| ---------------- | ----------------------------- | ------------------------------------ | ---------------------------------- |
| Connect          | `sdmx.Client("ECB")`          | `RegistryClient(sdmx_endpoint, ...)` | `sl.RestRegistry(sl.Provider.ECB)` |
| Built-in sources | 25+ via `sdmx.list_sources()` | None (raw URLs)                      | 6 via `sl.Provider`                |
| Custom endpoint  | `Client("ECB", url=...)`      | `RegistryClient(url)`                | `RestRegistry(url)`                |
| Context manager  | No                            | Yes (async `async with`)             | Yes (`with`)                       |
| Async support    | No                            | Yes (native async/await)             | No                                 |

**Verdict:** sdmx1 wins on source count. pysdmx wins on async. sdmxlib's `Provider` class is a clean middle ground — typed constants with both attribute (`Provider.ECB`) and subscript (`Provider["ECB"]`) access. Adding more providers is trivial.

**Gap:** No async client. This matters for apps fetching from multiple registries concurrently.

---

## 2. Fetching Artefacts

| Task               | sdmx1                                                   | pysdmx                                       | sdmxlib                                             |
| ------------------ | ------------------------------------------------------- | -------------------------------------------- | --------------------------------------------------- |
| Get a DSD          | `client.datastructure("AGENCY", "ID")`                  | `await client.get_schema("dataflow", ...)`   | `reg.get(sl.DataStructure, agency="X", id="Y")`     |
| Get a codelist     | `client.codelist("AGENCY", "CL_FREQ")`                  | `await client.get_codes("dataflow", "comp")` | `reg.get(sl.Codelist, agency="X", id="CL_FREQ")`    |
| List dataflows     | `client.dataflow()`                                     | `await client.get_dataflows(...)`            | `reg.query(sl.Dataflow, agency="X").fetch()`        |
| Resolve references | Manual: `client.get(..., params={"references": "all"})` | Implicit (high-level API)                    | `.resolve()` on any `Ref[T]` — automatic            |
| Caching            | None built-in                                           | None built-in                                | Session identity map — same URN returns same object |

**Verdict:** sdmxlib's `reg.get(Type, ...)` is more verbose than sdmx1's `client.datastructure(...)` but dramatically safer — the return type is `Type`, not `Any`. The `Query[T]` fluent API for collection searches has no equivalent in either competitor.

**Trade-off:** sdmx1's dynamic methods (`client.dataflow()`, `client.codelist()`) are terser but produce untyped results. pysdmx's high-level API hides resolution but loses granularity.

---

## 3. Navigating the Object Graph

This is where sdmxlib pulls ahead significantly.

| Task               | sdmx1                                           | pysdmx                       | sdmxlib                                                       |
| ------------------ | ----------------------------------------------- | ---------------------------- | ------------------------------------------------------------- |
| Get dimension      | `dsd.dimensions["FREQ"]` or `dsd.dimensions[0]` | `schema.components[i]`       | `dsd.dimensions["FREQ"]`                                      |
| Get codelist       | Manual: find codelist in message                | `comp.codes` (flat list)     | `dim.codelist()` — typed `Ref[Codelist]` unwrap               |
| Get codes          | Separate fetch                                  | `comp.codes`                 | `dim.codes` (convenience) or `dim.codelist().codes`           |
| Filter dimensions  | Manual list comprehension                       | Manual                       | `dsd.dimensions.where(lambda d: d.is_enumerated)`             |
| Pattern match repr | Not possible                                    | Not applicable               | `match dim.representation: case Ref(): ... case Facet(): ...` |
| Code map           | Manual construction                             | `get_code_map()` (on client) | `dsd.code_map("en")` — returns `dict[str, dict[str, str]]`    |
| Column labels      | Manual                                          | Not available                | `dsd.column_labels("en")`                                     |
| All codelists      | Manual aggregation                              | Not available                | `dsd.all_codelists`                                           |

**Verdict:** sdmxlib is the only library where you can go from DSD → dimension → codelist → codes in a single chain of attribute accesses. `Ref[T]` makes every edge in the SDMX graph navigable and type-safe. The convenience methods (`code_map`, `column_labels`, `all_codelists`) have no equivalent in either competitor.

---

## 4. Collection Types

| Feature          | sdmx1                           | pysdmx          | sdmxlib                                       |
| ---------------- | ------------------------------- | --------------- | --------------------------------------------- |
| Type             | `dict`-like (custom `DictLike`) | plain `list`    | `ItemList[T]` / `ArtefactList[T]`             |
| Lookup by id     | `msg.codelist["CL_FREQ"]`       | index iteration | `msg.codelists["CL_FREQ"]`                    |
| Lookup by URN    | Not supported                   | Not supported   | `msg.codelists[urn]`                          |
| Lookup by ref    | Not supported                   | Not supported   | `msg.codelists[ref]`                          |
| Qualified lookup | Not supported                   | Not supported   | `msg.codelists["SDMX:CL_FREQ(1.0)"]`          |
| Filter           | Manual                          | Manual          | `.where()`, `.partition()`, `.first_where()`  |
| Sort             | Manual                          | Manual          | `.sort_by(key)`                               |
| Ambiguity        | Silent (returns first)          | N/A             | `KeyError` with qualified alternatives listed |

**Verdict:** `ArtefactList[T]` is a significant differentiator. Multi-format lookup (bare id, agency-qualified, fully-qualified, URN, Ref) with O(1) indices and explicit ambiguity errors is unique to sdmxlib.

---

## 5. Type Safety

| Aspect       | sdmx1                   | pysdmx                 | sdmxlib                                   |
| ------------ | ----------------------- | ---------------------- | ----------------------------------------- |
| Objects      | Mutable attrs classes   | Pydantic/dataclass mix | Frozen slotted dataclasses                |
| References   | Bare strings or objects | Bare strings           | `Ref[T]` — generic, typed, URN-carrying   |
| URNs         | Strings                 | Strings                | `SdmxUrn[T]` — parsed, validated, generic |
| Return types | `Any` / `Message`       | Typed but narrow       | Generic `T` flows through                 |
| IDE support  | Minimal (dynamic attrs) | Good                   | Excellent (generics + frozen)             |

**Verdict:** sdmxlib is the only library where `pyright --strict` passes meaningfully. `Ref[T]` and `SdmxUrn[T]` propagate type information through the entire graph. This is a major ergonomic win for any project using a type checker.

---

## 6. Validation

| Feature              | sdmx1 | pysdmx                         | sdmxlib                       |
| -------------------- | ----- | ------------------------------ | ----------------------------- |
| Structural           | None  | `get_schema()` → validate data | `sl.validate(artefact)`       |
| Reference validation | None  | None                           | `sl.validate_references(msg)` |
| Data validation      | None  | Schema-based (codes, format)   | Not yet                       |

**Verdict:** sdmxlib and pysdmx approach validation differently — sdmxlib validates artefact structure, pysdmx validates data against schemas. Both are useful. sdmxlib's reference validation is unique.

**Gap:** No data-level validation (e.g. "is this code valid for this dimension?"). pysdmx's `get_schema()` approach is worth studying.

---

## 7. Data / Observation Support

| Feature          | sdmx1                 | pysdmx                       | sdmxlib                                    |
| ---------------- | --------------------- | ---------------------------- | ------------------------------------------ |
| Fetch obs        | `client.data(...)`    | `await client.get_data(...)` | Not supported (by design)                  |
| DataFrame output | `sdmx.to_pandas(msg)` | Returns pandas/list          | Polars bridge for structural metadata only |
| Data formats     | SDMX-ML, SDMX-JSON    | SDMX-JSON, Fusion-JSON       | N/A                                        |

**Verdict:** This is sdmxlib's biggest functional gap. Both competitors fetch and parse observation data. sdmxlib deliberately delegates data to Polars, but the bridge for actually fetching data doesn't exist yet.

---

## 8. Format Support

| Format      | sdmx1       | pysdmx  | sdmxlib        |
| ----------- | ----------- | ------- | -------------- |
| SDMX-ML 2.1 | Full (lxml) | Partial | Full (lxml)    |
| SDMX-ML 3.0 | No          | No      | Reader only    |
| SDMX-JSON   | Partial     | Full    | Full (msgspec) |
| Fusion-JSON | No          | Yes     | No             |

**Verdict:** Roughly at parity. sdmxlib's SDMX-ML 3.0 reader is a differentiator. Fusion-JSON support in pysdmx matters for BIS workflows.

---

## Scorecard

| Category               | sdmx1 | pysdmx | sdmxlib |
| ---------------------- | ----- | ------ | ------- |
| Setup & sources        | ★★★★  | ★★★    | ★★★½    |
| Type safety            | ★★    | ★★★    | ★★★★★   |
| Graph navigation       | ★★    | ★★½    | ★★★★★   |
| Collection ergonomics  | ★★½   | ★★     | ★★★★★   |
| Validation             | ★     | ★★★½   | ★★★★    |
| Data/observations      | ★★★★  | ★★★★   | ☆       |
| Async                  | ☆     | ★★★★★  | ☆       |
| Format coverage        | ★★★½  | ★★★½   | ★★★★    |
| Docs & discoverability | ★★★   | ★★½    | ★★★★    |

---

## Recommended Next Steps (priority order)

1. **Data fetching** — Even a thin `reg.data(dataflow, key=...) → polars.DataFrame` would close the biggest gap. The DSD is already resolved; the hard part is done.
2. **Async client** — An `AsyncRestRegistry` sharing the same Session/model layer. httpx already supports async.
3. **Schema-based validation** — A `validate_data(dsd, row)` that checks codes against codelists. Borrows pysdmx's idea but uses sdmxlib's typed graph.
4. **More providers** — Expand `Provider` toward sdmx1's 25+ sources. Low effort, high usability.
5. **Fusion-JSON reader** — Needed if BIS is a target audience.
