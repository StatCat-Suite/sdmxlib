# Constraints

Recipes for working with `DataConstraint` — artefacts that define the
valid key combinations and time coverage for a dataflow.

A `DataConstraint` answers the question: _for this dataflow, which
combinations of dimension values actually have data?_ They are used to
validate observation keys before loading data and to discover time coverage.

---

## Fetching constraints for a dataflow

Constraints are published alongside dataflows in the SDMX registry, but they
point *at* the dataflow, not the other way round. `reg.get(flow_urn,
resolve=True)` follows the dataflow's own references (`references=descendants`
— its DSD, codelists, concept schemes), so it never brings a constraint back.
Ask for the constraints themselves, and name the dataflow they attach to:

```python
import sdmxlib as sl

with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    constraints = (
        reg.query(sl.q.data_constraint)
        .filter(
            sl.q.data_constraint.agency == "ECB",
            sl.q.data_constraint.dataflows.any(sl.q.dataflow.id == "ECB_EXR1"),
        )
        .collect()
    )
    for cc in constraints:
        print(f"{cc.id}: {len(cc.regions)} region(s), type={cc.constraint_type}")
```

---

## Validating observation keys

Use `constraint.allows(key)` to check whether a dimension combination is
valid before sending a data request or inserting a row.

```python
with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    # Fetch a constraint directly from the registry
    cc = reg.get(sl.DataConstraint.urn_for(agency="ECB", id="ECB_EXR1_CONSTRAINTS"))

    # Check individual observation keys
    valid_key = {"FREQ": "D", "CURRENCY": "USD", "CURRENCY_DENOM": "EUR"}
    invalid_key = {"FREQ": "D", "CURRENCY": "XYZ", "CURRENCY_DENOM": "EUR"}

    print(cc.allows(valid_key))  # True
    print(cc.allows(invalid_key))  # False — XYZ not in allowed currency codes

    # Filter a list of candidate keys
    candidate_keys = [
        {"FREQ": "D", "CURRENCY": "USD", "CURRENCY_DENOM": "EUR"},
        {"FREQ": "D", "CURRENCY": "GBP", "CURRENCY_DENOM": "EUR"},
        {"FREQ": "D", "CURRENCY": "XYZ", "CURRENCY_DENOM": "EUR"},
    ]
    valid_keys = [k for k in candidate_keys if cc.allows(k)]
    print(f"{len(valid_keys)} of {len(candidate_keys)} keys are valid")
```

---

## Inspecting allowed values per dimension

Drill into `CubeRegion.keys` to see exactly which codes are listed for
each dimension. Two flags decide what a listing means: the region's own
`include`, and each value set's `KeyValue.include` — an included region may
still *exclude* some of one dimension's values, so read both:

```python
with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    cc = reg.get(sl.DataConstraint.urn_for(agency="ECB", id="ECB_EXR1_CONSTRAINTS"))

    for region in cc.regions:
        kind = "included" if region.include else "excluded"
        print(f"Region ({kind}):")
        for kv in region.keys:
            codes = sorted(kv.codes())[:5]
            truncated = "..." if len(kv.values) > 5 else ""
            listed = "in" if kv.include else "not in"
            print(f"  {kv.dimension_id} {listed}: {codes}{truncated}")
```

```
Region (included):
  FREQ in: ['D', 'M']
  CURRENCY in: ['AUD', 'BGN', 'BRL', 'CAD', 'CHF']...
  CURRENCY_DENOM in: ['EUR']
```

`allows()` reads both flags for you; walk them by hand only when you need the
listing itself.

---

## Reading time coverage

`DataConstraint.reference_period` describes the time span of available
data. Use it to avoid requesting data outside the covered range:

```python
with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    cc = reg.get(sl.DataConstraint.urn_for(agency="ECB", id="ECB_EXR1_CONSTRAINTS"))

    if cc.reference_period:
        rp = cc.reference_period
        print(f"Data available from {rp.start_period.date()} to {rp.end_period.date()}")
    else:
        print("No reference period declared")
```

---

## Synthesising availability from the data

Everything above reads a constraint someone **published**. The other question
is what the data itself says, and for a local lake you can synthesise the
answer rather than wait for a provider to declare it.

`build_dataflow_constraint` scans the bound table and returns a real
`DataConstraint` of type `ACTUAL` — one inclusive `CubeRegion` whose
`KeyValue`s are the distinct values present per dimension:

It reads the lake, so it takes a `LocalRegistry` — the one holding both the
binding and the rows:

```python
from sdmxlib.builders.contentconstraint import build_dataflow_constraint

with sl.LocalRegistry("lake.duckdb") as local:
    cc = build_dataflow_constraint(local, flow_urn, name={"en": "Availability"})

cc.constraint_type                       # ConstraintType.ACTUAL
{kv.dimension_id: sorted(kv.codes()) for kv in cc.regions[0].keys}
# {"FREQ": ["A", "M", "Q"], "REF_AREA": ["CA", "US"]}
```

The dimension set and the physical column names come from the dataflow's
`DataflowBinding`, which is the authoritative record of what columns the
materialised table actually has — the right source for a statement about
actual availability.

It is a `DataConstraint` like any other, so everything on this page applies to
it. `allows()` works, and it answers a different question from a published
constraint: what *is* there rather than what *may* be.

```python
cc.allows({"FREQ": "A", "REF_AREA": "CA"})   # True  — rows exist
cc.allows({"FREQ": "A", "REF_AREA": "ZZ"})   # False — no rows, though the code is valid
```

A dimension value that is `NULL` in the lake is **excluded** rather than
collected as an empty string: a missing value is not a value the cube contains.

### One component at a time

`build_component_availability` is the framework-free core of the SDMX REST
request `GET /availableconstraint/<flow>/<key>/<component>?mode=<mode>` — the
call a faceted UI makes on every click to decide which options to grey out.

```python
from sdmxlib.builders.contentconstraint import build_component_availability

with sl.LocalRegistry("lake.duckdb") as local:
    cc = build_component_availability(
        local, flow_urn, name={"en": "Availability"},
        key="A.CA", component_id="REF_AREA", mode="exact", api="2.1",
    )
```

`key` is the dotted, positional SDMX key over the DSD's key slots, and `"all"`
means unfiltered in either position. `name` is required, as on every artefact:
SDMX requires one, and which languages it is in is yours to say.

!!! warning "`api` is required, and the two versions disagree"

    How many slots the key has depends on the SDMX-REST generation you are
    serving: **2.1 leaves the time dimension out** of the dotted key and
    filters it with `startPeriod` / `endPeriod`, while **3.0 gives it a
    segment**. `dsd.key_slot_ids(api)` is the authority.

    There is no default, deliberately. A key of the other shape is usually
    caught by its length — but a structure whose measure dimension makes the
    counts coincide would be read with **every value one slot out**, which
    returns a different slice with no error at all.

The two modes differ only in the filter applied while enumerating a component:

| mode | filter while enumerating component `C` |
| --- | --- |
| `exact` | the **full** key, `C`'s own constraint included |
| `available` | the key with `C`'s own constraint **dropped** |

So with `key="A.CA"` and rows `A/CA`, `A/US`, `Q/CA`, `M/US`:

```python
with sl.LocalRegistry("lake.duckdb") as local:
    exact = build_component_availability(
        local, flow_urn, name={"en": "Availability"},
        key="A.CA", component_id="REF_AREA", mode="exact", api="2.1",
    )
    # REF_AREA -> {"CA"} — what the current selection contains

    available = build_component_availability(
        local, flow_urn, name={"en": "Availability"},
        key="A.CA", component_id="REF_AREA", mode="available", api="2.1",
    )
    # REF_AREA -> {"CA", "US"} — what you could switch to without emptying the cube
```

`available` is what greys out a dropdown correctly. `exact` would grey out
every option except the one already selected, which is a UI that cannot be
navigated.

Other components keep their constraints in both modes — only the one being
enumerated is relaxed.

### On batching the scans

`build_component_availability` runs at most **one `SELECT DISTINCT` per
component**, by design. Batching them into a single
`array_agg(DISTINCT c) FILTER (…)` per column — the shape a consumer
hand-wrote on the assumption it would be cheaper — measured **1.2× to 2.5×
slower** across every width, row count and cardinality tried, on parquet and
in memory alike.

Parquet is columnar, so N queries projecting one column each read the same
bytes as one query projecting N: there is no I/O to save, and a single-column
parallel hash-distinct beats N distinct sets built inside one operator.
`tests/bench/test_availability_scan_shape.py` keeps that answer attached to
its evidence and fails if a DuckDB release ever reverses it.

### Answering from stored availability

A sealed edition stores the values each dimension holds
(`EditionRegistry.availability`), and `build_component_availability` reads them
to skip work. A selection that lists every value a dimension holds excludes no
row, so that dimension's constraint is dropped before scanning. A dimension
left with no constraint, after that and after `available` mode drops its own,
takes its values from the stored availability and runs no scan.

So over key slots `FREQ.REF_AREA.GENDER`, a UI that opens on one area and every
gender, `key=".CA._T+1+2"`, enumerates `REF_AREA` in `available` mode without
reading an observation. The answer is
the scan's in every case. Attributes always scan, since stored availability
lists dimensions only, and a `LocalRegistry` always scans: an edition being
built may hold the availability of the one it extends.

---

## Reading from a local file

Constraints are part of `StructureMessage` and read from, and write to, every
SDMX-ML version. Not every version carries every field, though: SDMX-ML 2.1's
value sets have no validity period or `removePrefix`, 3.0 dropped
`ReferencePeriod`, and 3.1 dropped `ReleaseCalendar`. `sl.unrepresentable(cc,
"2.1")` names what a given constraint would lose before you write it — empty
means it writes losslessly.

```python
msg = sl.read("ecb_exr1_with_constraints.xml")

for cc in msg.constraints:
    print(f"{cc.id}: {cc.constraint_type}, {len(cc.regions)} region(s)")
    if cc.reference_period:
        rp = cc.reference_period
        print(f"  Coverage: {rp.start_period.date()} – {rp.end_period.date()}")
    for gap in sl.unrepresentable(cc, "2.1"):
        print(f"  lost to 2.1: {gap}")
```
