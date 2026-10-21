# Construction Ergonomics Audit: sdmxlib vs sdmx1

A fresh-eyes comparison of building SDMX artefacts in sdmxlib against
[khaeru/sdmx](https://github.com/khaeru/sdmx) (sdmx1). Written after
completing the round-trip fidelity work so the API surface is stable.

---

## Reference: sdmx1 construction style

sdmx1 uses `attrs`-based mutable classes with direct Python references.
No URN wrappers at construction time. You mutate in-place:

```python
import sdmx
import sdmx.model.v21 as m

# Codelists
cl_freq = m.Codelist(id="CL_FREQ",
                     maintainer=m.Agency(id="SDMX"),
                     version="1.0")
cl_freq["A"] = m.Code(id="A")
cl_freq["A"].name["en"] = "Annual"
cl_freq["Q"] = m.Code(id="Q")
cl_freq["Q"].name["en"] = "Quarterly"

# Concept scheme
cs = m.ConceptScheme(id="CROSS_DOMAIN",
                     maintainer=m.Agency(id="SDMX"))
cs["FREQ"] = m.Concept(id="FREQ", name={"en": "Frequency"})

# DataStructure
dsd = m.DataStructureDefinition(id="DSD_TRADE",
                                maintainer=m.Agency(id="MY_AGENCY"))
dsd.dimensions.append(
    m.Dimension(
        id="FREQ",
        local_representation=m.Representation(enumerated=cl_freq),
        concept_identity=cs["FREQ"],
    )
)

# Dataflow
df = m.DataflowDefinition(id="DF_TRADE",
                          maintainer=m.Agency(id="MY_AGENCY"),
                          structure=dsd)
```

Core characteristics:

- Everything is mutable. Add items by mutation, not reconstruction.
- References are direct Python object pointers. No URN wrapper at all.
- `maintainer` field takes an `Agency` object, not a bare string.
- Names are set by subscript assignment or `name["en"] = "..."`.
- Items added to collections via `[key] = value` or `.append()`.
- No concept scheme required — you can wire concepts to dimensions directly.

---

## sdmxlib: the same DSD

```python
import sdmxlib as sl

cl_freq = sl.Codelist(
    id="CL_FREQ", maintainer="SDMX", version="1.0",
    name={"en": "Frequency"},
    codes=[
        sl.Code(id="A", name={"en": "Annual"}),
        sl.Code(id="Q", name={"en": "Quarterly"}),
        sl.Code(id="M", name={"en": "Monthly"}),
    ],
)

cs = sl.ConceptScheme(
    id="CROSS_DOMAIN", maintainer="SDMX", version="1.0",
    concepts=[sl.Concept(id="FREQ", name={"en": "Frequency"})],
)

dsd = sl.DataStructure(
    id="DSD_TRADE", maintainer="MY_AGENCY", version="1.0",
    name={"en": "Trade Statistics"},
    dimensions=[
        sl.Dimension(id="FREQ", representation=cl_freq, concept=cs.ref_to("FREQ")),
    ],
    time_dimension=sl.TimeDimension(
        representation=sl.TextFormat(text_type="ObservationalTimePeriod"),
    ),
    measures=[sl.Measure(id="OBS_VALUE")],
)

df = sl.Dataflow(
    id="DF_TRADE", maintainer="MY_AGENCY", version="1.0",
    structure=dsd,  # bare DataStructure → coerced to Ref
)
```

---

## Side-by-side assessment

### 1. Codelist construction

**sdmx1** — two-step: create, then populate by mutation:

```python
cl = m.Codelist(id="CL_FREQ", maintainer=m.Agency(id="SDMX"))
cl["A"] = m.Code(id="A")
cl["A"].name["en"] = "Annual"
cl["Q"] = m.Code(id="Q")
cl["Q"].name["en"] = "Quarterly"
```

**sdmxlib** — single constructor call with inline codes:

```python
cl = sl.Codelist(
    id="CL_FREQ", maintainer="SDMX",
    codes=[
        sl.Code(id="A", name={"en": "Annual"}),
        sl.Code(id="Q", name={"en": "Quarterly"}),
    ],
)
```

**Verdict: sdmxlib wins.** The dict coercion for names and the inline codes
list make a single readable expression. sdmx1 requires N mutation lines per
code.

---

### 2. Concept scheme and concept references

**sdmx1:**

```python
cs = m.ConceptScheme(id="CS", maintainer=m.Agency(id="SDMX"))
cs["FREQ"] = m.Concept(id="FREQ")
# Wire to a dimension — direct object reference
dim = m.Dimension(id="FREQ", concept_identity=cs["FREQ"], ...)
```

**sdmxlib:**

```python
cs = sl.ConceptScheme(id="CS", maintainer="SDMX",
                      concepts=[sl.Concept(id="FREQ", name={"en": "Frequency"})])
# Wire to a dimension — Ref via cs.ref_to
dim = sl.Dimension(id="FREQ", concept=cs.ref_to("FREQ"), ...)
```

**Verdict: even.** sdmxlib's `cs.ref_to()` is clean. The naming is
intuitive. One rough edge: `cs.ref_to("FREQ").id` returns `"CS"` — the
scheme's id, not the concept's id. The concept id lives in `.item_id`:

```python
ref = cs.ref_to("FREQ")
ref.id       # "CS"      ← the scheme (surprising)
ref.item_id  # "FREQ"    ← the concept (what you want)
```

This is SDMX-correct (URN structure encodes item id separately) but violates
the principle of least surprise. The `item_id` property exists precisely to
address this — but it's not discoverable. The tutorial even had a warning
comment about it. Consider naming it `concept_id` or adding a `__repr__`
that shows `item_id` prominently.

---

### 3. Dimension construction — local codelists

**sdmx1:**

```python
m.Dimension(
    id="FREQ",
    local_representation=m.Representation(enumerated=cl_freq),
    concept_identity=cs["FREQ"],
)
```

**sdmxlib:**

```python
sl.Dimension(id="FREQ", representation=cl_freq, concept=cs.ref_to("FREQ"))
```

**Verdict: sdmxlib wins clearly.** `representation=cl` (bare Codelist) is coerced
to a `Ref` automatically.
`local_representation=m.Representation(enumerated=...)` is a mouthful
compared to `representation=cl`.

---

### 4. Dimension construction — external codelists

This is the most important real-world case. An NSA builds a DSD that uses
SDMX cross-domain codelists (CL_FREQ, CL_CONF_STATUS, etc.) from a different
agency. The codelist object is not in memory.

**sdmx1** — no ceremony. External refs are just Python references; if you
don't have the object, you can reference by id directly or fetch on demand:

```python
# sdmx1 can fetch lazily — no upfront ref construction
cl_freq_ref = sdmx.urn.URN(str=
    "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ(1.0)")
dim = m.Dimension(id="FREQ",
                  local_representation=m.Representation(enumerated=cl_freq_ref))
```

**sdmxlib** — requires explicit `Ref.unresolved()` construction:

```python
dim = sl.Dimension(
    id="FREQ",
    representation=sl.Ref.unresolved(
        sl.Codelist.urn(agency="SDMX", id="CL_FREQ", version="2.0")
    ),
    concept=sl.Ref.unresolved(
        sl.Concept.urn(agency="SDMX", scheme="CROSS_DOMAIN_CONCEPTS", id="FREQ")
    ),
)
```

**Verdict: sdmxlib loses on verbosity but wins on explicitness.** The
`Ref.unresolved()` wrapper makes the semantics clear — "I know this exists
somewhere but don't have it yet." That's better than sdmx1's implicit
laziness. But `Ref.unresolved(sl.Codelist.urn(...))` is still 3 levels of
nesting for what is conceptually a simple operation.

The `Codelist.urn()` and `Concept.urn()` helpers do a good job of hiding
the `package=`/`artefact_class=` internals. The remaining friction is the
`Ref.unresolved()` wrapper itself. It adds a wrapping call that feels like
ceremony when the common case is "I'm referencing a well-known codelist."

**Implemented:** `representation=` accepts a bare `SdmxUrn` directly:

```python
# Current — SdmxUrn coercion is implemented
sl.Dimension(
    id="FREQ",
    representation=sl.Codelist.urn(agency="SDMX", id="CL_FREQ", version="2.0"),
    concept=sl.Concept.urn(agency="SDMX", scheme="CROSS_DOMAIN_CONCEPTS", id="FREQ"),
)
```

---

### 5. Incremental construction — adding to an existing DSD

**sdmx1** — direct mutation:

```python
dsd.dimensions.append(new_dim)
dsd.attributes.append(new_attr)
```

**sdmxlib** — `append()` returns a new object (immutable):

```python
dsd = dsd.append(new_dim)    # Dimension dispatched automatically
dsd = dsd.append(new_attr)   # Attribute dispatched automatically
```

**Verdict: comparable.** sdmx1's mutation is more familiar. sdmxlib's
`append()` is immutable-idiomatic and the reassignment pattern is standard
Python for immutable objects (same as `str`, `tuple`, `frozenset`). The
dispatch-by-type means you don't repeat the component name.

The main friction: it's easy to forget to reassign (`dsd.append(dim)` with
no reassignment silently does nothing). A linter can catch this
(`reportUnusedCallResult`), and sdmxlib's type config does suppress it for
test files — but for user code it should surface as a warning.

---

### 6. Versioning and cloning

**sdmx1** — copy then mutate:

```python
import copy
dsd_v2 = copy.deepcopy(dsd)
dsd_v2.version = "2.0"
dsd_v2.dimensions.append(new_dim)
```

**sdmxlib** — `replace()` from `dataclasses`:

```python
from dataclasses import replace

dsd_v2 = replace(dsd, version="2.0", dimensions=[*dsd.dimensions, new_dim])
```

**Verdict: even.** Both are one-liners for the common case. sdmxlib's
`replace()` is more precise (no risk of accidentally mutating shared state
via deepcopy's reference graph). The `[*dsd.dimensions, new_dim]` spread
is idiomatic Python.

For simple single-field changes:

```python
dsd_v2 = replace(dsd, version="2.0")      # clear
dsd_final = replace(dsd, is_final=True)   # clear
```

---

### 7. Building a full StructureMessage

**sdmx1** — no StructureMessage concept at construction time; serialization
handles packaging:

```python
msg = sdmx.message.StructureMessage()
msg.codelist[cl.urn] = cl
msg.dataflow[df.urn] = df
# etc.
```

**sdmxlib:**

```python
msg = sl.StructureMessage(
    codelists=[cl_freq, cl_area, cl_flow],
    concept_schemes=[cs],
    data_structures=[dsd],
    dataflows=[df],
)
xml = sl.to_xml(msg, version="2.1")
```

**Verdict: sdmxlib wins.** Keyword-argument construction with list coercion
is readable. Every field is named. sdmx1's mutable dict-of-URNs feels like
infrastructure rather than a domain object.

---

### 8. Concept scheme: required or optional?

**sdmx1** — concepts can be wired or omitted freely. You pay only for what
you need.

**sdmxlib** — concepts are genuinely optional. `Dimension(id="FREQ",
representation=cl)` works fine. The `concept=` field defaults to `None`. When
absent, `column_labels()` falls back to the dimension id. This is documented
in the Quick Reference but not prominently enough — many users follow the
tutorial through Section 2 (Concepts) and feel obligated to build a
ConceptScheme for every DSD.

**Verdict: no API problem, but a documentation problem.** The tutorial's
"From a Database" example (Section 8) builds a ConceptScheme in the loop
alongside the codelists, which models good practice. But there is no
"minimal DSD — no concepts" example showing that the ConceptScheme is
purely opt-in.

---

## Bugs and Stale Patterns Found in the Tutorial

The `building_tutorial.md` has three stale patterns that were left when the
API was updated:

**1. `with_added_attribute` in Section 9 — removed, replaced by `append()`:**

```python
# Section 9 still says (wrong):
new_dsd = dsd.with_added_attribute(sl.Attribute(...))

# Should be:
new_dsd = dsd.append(sl.Attribute(...))
```

**2. `with_dimension` in Section 6 — removed, replaced by `replace()`:**

```python
# Section 6 still says (wrong):
updated_dsd = dsd.with_dimension(replace(dsd.dimensions["FREQ"], representation=v2))

# Should be (if replacing a component in-place):
updated_dsd = replace(
    dsd,
    dimensions=[
        replace(dim, representation=v2) if dim.id == "FREQ" else dim
        for dim in dsd.dimensions
    ],
)
# Or if just adding back a modified version, use append()
```

**3. `SdmxUrn.make(sl.Concept, package=..., artefact_class=..., ...)` in
Section 5 — should use `Concept.urn()`:**

```python
# Section 5 still says (verbose):
concept=sl.Ref.unresolved(
    sl.SdmxUrn.make(
        sl.Concept,
        package="conceptscheme",
        artefact_class="Concept",
        agency="SDMX",
        id="CROSS_DOMAIN_CONCEPTS",
        item_id="FREQ",
    )
)

# Should use the helper:
concept=sl.Ref.unresolved(
    sl.Concept.urn(agency="SDMX", scheme="CROSS_DOMAIN_CONCEPTS", id="FREQ")
)
```

**4. `"position": 1` in Section 7's JSON example — removed from the model:**

```json
// Section 7's JSON example still has (stale):
{ "id": "FREQ", "position": 1, "codelist": "..." }

// Should just be:
{ "id": "FREQ", "representation": "..." }
```

---

## Summary Scorecard

| Task                       | sdmxlib                            | sdmx1                           | Notes                     |
| -------------------------- | ---------------------------------- | ------------------------------- | ------------------------- |
| Single-language name       | `name={"en": "X"}`                 | `name={"en": "X"}` or mutate    | Tie                       |
| Multi-code codelist        | ✅ Single constructor              | ❌ N mutation lines             | sdmxlib wins              |
| Dimension with local CL    | ✅ `representation=cl`             | `Representation(enumerated=cl)` | sdmxlib wins              |
| Dimension with external CL | `Ref.unresolved(CL.urn(...))`      | Direct ref or URN string        | sdmx1 wins on brevity     |
| External concept ref       | `Ref.unresolved(Concept.urn(...))` | `cs["FREQ"]` direct             | sdmx1 wins on brevity     |
| Add component to DSD       | `dsd = dsd.append(dim)`            | `dsd.dimensions.append(dim)`    | Tie (sdmx1 more familiar) |
| Version a DSD              | `replace(dsd, version="2.0")`      | `dsd.version = "2.0"`           | Tie                       |
| Validate before publish    | `sl.validate(dsd)`                 | ad hoc                          | sdmxlib wins              |
| Serialize                  | `sl.to_xml(msg)`                   | `sdmx.to_xml(msg)`              | Tie                       |
| Type safety                | ✅ Frozen, typed, match-safe       | ❌ Mutable, less typed          | sdmxlib wins              |

---

## Priority Actions

### Fix immediately: tutorial staleness

The tutorial has four stale patterns (documented above) that will break for
any user who copies from it. These should be fixed before any other
documentation work.

### URN coercion (implemented)

`SdmxUrn` is accepted directly on `representation`/`concept` fields. The
external-reference case is clean and flat:

```python
sl.Dimension(
    id="FREQ",
    representation=sl.Codelist.urn(agency="SDMX", id="CL_FREQ", version="2.0"),
    concept=sl.Concept.urn(agency="SDMX", scheme="CROSS_DOMAIN_CONCEPTS", id="FREQ"),
)
```

### Fix soon: add minimal-DSD example to tutorial

Add a "No concepts needed" callout in Section 2 or 3 showing the shortest
valid DSD:

```python
# Minimal valid DSD — no concepts, no time dimension, no attributes
dsd = sl.DataStructure(
    id="DSD_SIMPLE", maintainer="MY_AGENCY",
    dimensions=[sl.Dimension(id="COUNTRY", representation=cl_area)],
    measures=[sl.Measure(id="OBS_VALUE")],
)
```

### Fix later: `ref_to().id` naming

`cs.ref_to("FREQ").id` returns the scheme id, not the concept id. This is
technically correct per URN semantics but surprises every new user.
Options:

- Document it more visibly (a warning box in the tutorial, not just a comment)
- Consider adding `.concept_id` as an alias for `.item_id` on `Ref[Concept]`
  specifically, though this requires protocol narrowing
- At minimum, make the `__repr__` of `Ref` show the item_id when present:
  `Ref(CS_TRADE → FREQ [resolved])` instead of `Ref(CS_TRADE → resolved)`
