# Goldfish Review Report (Stage 6, Comprehension Test)

**Target:** `/Users/gabrielgellner/dev/sdmxlib/docs/design/sharded-bundle-catalog-plan.md` (Feature 1-158, Detailed 159-261)
**Reviewer lens:** `egm-comprehension` (one instance). It read the target and `docs/design/sharded-bundle-catalog.md`. It did not read `bundle.md` or `caching.md`.

**Verdict:** The reviewer could not re-implement the plan from this document. The feature overview reads clearly. About a third of the detailed work items defer signatures, formats or decisions to issues that are not yet written.

## Blockers

### RestRegistry reference-direction API deferred - Blocker
* **Affected Scope:** L181 (Phase 1 item 5)
* **Discrepancy:** The shape of the API is "decided in GH-221", so neither this item nor `fetch_bundle`'s call into it can be written.
* **Recommendation:** State the signature, or mark the item as blocked on GH-221 and name the decision it waits for.

### `_build.py` unspecified - Blocker
* **Affected Scope:** L182-184 (Phase 1 item 6)
* **Discrepancy:** The item is "an issue to file after GH-221". It has no function name, no signature, no type for "an observation source per dataflow", and no list of provenance fields. "the closure of item 5" is ambiguous.
* **Recommendation:** Give the signature, the input types, the provenance fields, and say which closure is meant.

## Major

1. **L15, L161-247:** Milestones M0-M13 are never defined in the plan. L25-29 cite "M10's/M9's tests" before any phase maps them. Add a milestone-to-phase table.
2. **L169 vs L173-186:** The plan does not say whether 0.75.0 ships before or after items 3, 5 and 6. This matters for the `<0.76` pin (L190) and for `vendor_closure`'s dependence on GH-221 (L203). It also never says what 0.74 was after 0.73.0 (L15).
3. **L47 vs L192:** Q5 leaves open whether `open_bundle(data=Auto())` survives, but Phase 2 removes `DataPolicy`/`Auto()`. One of the two must change.
4. **L172:** The benchmark does not say where the census publication is, how a cold dataflow is chosen, or where results are recorded.
5. **L173:** Item 3 has no numeric threshold for being built. The mapping of `"memory"`/`"spill"`/`None` to the two DuckDB settings is implied, not stated.
6. **L186, L49:** "FMR" is not expanded. The steps to run FMR 12.4.2 locally, create the three probe artefacts and record cassettes are missing.
7. **L192:** "folding" is undefined, the refusal's exception type and message are missing, and the source of the overlays at open time is not named.
8. **L194:** The plan does not say whether `build_index` drops tables of a `draining` or previous publication that pods may still serve. This is a correctness risk.
9. **L203:** `vendor_closure` and `structures_bundle` leave four things open: which constraint types count, the rule for a hierarchy node whose parent is missing, the type and values of `families`, and the errors either function raises.
10. **L206:** The "CODR-shaped product closure" fixture has no source, and the build-time refusal exception is not named.
11. **L211-212:** Phase 4 leaves six things undefined: `ServingEntry`, the `fetch` callable's signature, the degree of parallelism, the `--serving` file format, how the `--out` layout relates to spec §6.2, and how the `previous` manifest cache is reused.
12. **L219:** Phase 5 leaves several choices open:
    * "rename or manifest-last" is not decided.
    * The digest and the place the store keeps it are not named.
    * The storage for `staged` and the serving list is not named.
    * The parameter types for `lifecycle`, `pool`, `pinned`, `kind` and `retention` are missing.
    * The backup window (L28) has no parameter.
    * The pre-materialisation hand-off has no interface.
13. **L220:** No CLI flags are given for `statcat publish`/`release`/`gc`.
14. **L110:** "Idempotent over its inputs" is not defined per job, for example a `release` re-run over a token set that is already released.
15. **L138:** The pre-materialisation job is named as a security boundary, but no phase builds it.
16. **L227-229:** Phase 6 leaves four things undefined: the `Fetcher` protocol and `stamps=`, the `catalog/current` polling interval, the `RoutedCatalogProvider` interface, and how `get_runtime` finds the bundle for a structure query that names no dataflow.
17. **L230:** The sdmxlib-rest render version has no name, location or bump rule.
18. **L235-236:** The `extensions.statcat` record shape, the search endpoint paths and parameters, and the place where language tables are declared are all missing.
19. **L241:** The KEDA cron timing, the Prometheus thresholds, `hash_balance_factor`, the probe timeouts and the resource sizes have no values.
20. **L25, L255:** No phase builds the synthetic 7,000-bundle Zipf corpus, although Verification relies on it for Phases 5-9. This one is close to a Blocker.

## Nits

1. **L11, L57:** SDMX, CODR, .Stat Suite, "embargo" and "partials" are not expanded at first use.
2. **L19:** "check profiles", "tier" and "token directories" are used before they are defined. The linked spec defines them, which is acceptable.
3. **L96-102:** "token-named search tables" is explained only at L194. The P7 arrow is hard to read.
4. **L105:** The value of `STATCAT_SECTION_FORMAT` appears only at L204.
5. **L139, L242:** N in "1/N of keys" is not defined.
6. **L188:** Issues #46 and #47 are not mapped to items.
7. **L197:** `SEARCH_ANNOTATION_TYPES` is not defined.
8. **L213:** The `~` version syntax is not defined.
9. **L225:** The statcatlib version range is not given.
10. **L231:** "Under load" is not defined, and the plan does not say whether the bench tests run in `just ci`.
11. **L242, L255:** "kind cluster" collides with `kind: structures`. Name the tool (Kubernetes-in-Docker).
12. **L246-247:** "account-level backup" is not defined, and the hit rate has no pass threshold.
13. **Spec, outside the target:** §6.3 L312 cites §7.4 for `pool`. Pools are in §7.5.
