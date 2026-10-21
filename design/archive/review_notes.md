# sdmxlib — Code Review Notes

Issues found during architecture review of the codebase against
the design spec. Ordered by severity.

---

## 1. BLOCKING: Identity map returns unresolved objects for resolve() calls

**File:** `api/registry.py` lines 187-198, `api/session.py`

**Problem:** `Registry.resolve(urn)` checks the identity map first and
returns whatever is cached — even if the cached object has unresolved Refs
from a prior `fetch()` call:

```python
def resolve[T](self, urn: SdmxUrn[T]) -> T:
    cached = self._session.get(urn)
    if cached is not None:
        return cached  # ← BUG: may be unresolved from a prior fetch()
```

The same bug exists in the named methods (`reg.DataStructure(...)`) via
`_fetch_resolved`, which also checks the cache first.

**Fix:** Two options:

**Option A (simple):** Named methods and `resolve()` always skip the cache
and fetch with `references=all`. The cache only serves `fetch()` calls.
This means duplicate HTTP requests if the user calls both `fetch` and
`resolve` for the same URN, but it's correct.

**Option B (better):** Track resolution state in the identity map. Store
both resolved and unresolved versions keyed differently, or store only
resolved versions (since resolved is a superset of unresolved data).

**Recommended:** Option B — the identity map stores resolved objects.
`fetch()` also stores into the map (the object just has unresolved Refs).
`resolve()` checks if the cached entry has resolved Refs; if not, re-fetches
with `references=all` and replaces the cache entry. Since our principle is
"fully resolved first" and the named methods always resolve, the common
path always populates the cache with resolved objects.

Simplest implementation: two maps, or a flag per entry, or just always
fetch with `references=all` for named methods and `resolve()`, and let
`fetch()` use `references=none` without caching (or caching separately).

---

## 2. Type annotation: `Enumeration.urn` should be `SdmxUrn[Codelist]`

**File:** `model/representation.py` line 46

**Current:**

```python
urn: SdmxUrn[Any]
```

**Should be:**

```python
from sdmxlib.model.codelist import Codelist  # in TYPE_CHECKING block
...
urn: SdmxUrn[Codelist]
```

Enumerations always reference codelists. Using `Any` loses the type
information that lets the checker verify `dim.codelist.urn` flows correctly
through `reg.resolve()` → `Codelist`.

---

## 3. Type annotation: `Dimension.enumeration_urn` returns `object | None`

**File:** `model/datastructure.py` line 42

**Current:**

```python
@property
def enumeration_urn(self) -> object | None:
    return self.codelist.urn if self.codelist else None
```

**Should be:**

```python
@property
def enumeration_urn(self) -> SdmxUrn[Codelist] | None:
    return self.codelist.urn if self.codelist else None
```

This requires importing `SdmxUrn` (already available via `TYPE_CHECKING`)
and `Codelist` (already in `TYPE_CHECKING` block). The `object` return
type discards all type information.

---

## 4. `ArtefactList.ids(qualified=True)` omits version

**File:** `model/collections.py` line 313

**Current:**

```python
def ids(self, *, qualified: bool = False) -> list[str]:
    if qualified:
        return [f"{item.agency_id}:{item.id}" for item in self._items]
```

**Should be:**

```python
def ids(self, *, qualified: bool = False) -> list[str]:
    if qualified:
        return [f"{item.agency_id}:{item.id}({item.version})" for item in self._items]
```

The spec and the ambiguity error messages both use `agency:id(version)` format.
The `__repr__` method (line 368) has the same issue — should include version.

Also update `__repr__`:

```python
def __repr__(self) -> str:
    ids = [f"{item.agency_id}:{item.id}({item.version})" for item in self._items]
    return f"ArtefactList([{', '.join(repr(i) for i in ids)}])"
```

---

## 5. Package string duplication — three sources of truth

**Files:** `api/registry.py` (`_PACKAGE` dict), model classes (`sdmx_package`
class vars), `formats/sdmx_ml21/reader.py` (hardcoded strings in
`_index_artefact` calls)

**Problem:** The mapping from artefact class name to SDMX package string
is defined in three places:

```python
# registry.py
_PACKAGE = {"DataStructure": "datastructure", "Dataflow": "datastructure", ...}

# codelist.py
sdmx_package = "codelist"

# reader.py
_index_artefact(index, cl, "codelist", "Codelist")  # hardcoded
```

If any of these disagree, URN construction breaks silently.

**Fix:** The model classes are the source of truth. The registry and reader
should read `sdmx_package` and `sdmx_class` from the model class:

```python
# registry.py — replace _PACKAGE dict
def _fetch_resolved[T](self, artefact_type: type[T], agency, id, version):
    resource = artefact_type.sdmx_class      # from model class
    package = artefact_type.sdmx_package      # from model class
    urn = SdmxUrn.make(artefact_type, package=package, ...)
    ...

# reader.py — replace hardcoded strings
_index_artefact(index, cl, Codelist.sdmx_package, Codelist.sdmx_class)
```

Remove the `_PACKAGE` dict from `registry.py` entirely.

---

## 6. MaintainableArtefact field repetition — needs documentation

**Files:** All maintainable model classes (codelist.py, concept.py,
datastructure.py, dataflow.py, provision.py, category.py)

**Problem:** Every maintainable type redeclares `id`, `agency_id`, `version`,
`name`, `description`, `is_final`, `annotations`. This is ~7 lines repeated
across 7+ classes.

**Reason:** Frozen slotted dataclass inheritance requires that if a parent
has slots, children can't add new slots without issues. The `MaintainableArtefact`
mixin doesn't use `@dataclass` (correctly), so subclasses must declare
all fields.

**Fix:** Add a comment in `base.py` explaining why, so future contributors
don't try to "fix" the repetition:

```python
class MaintainableArtefact:
    """Mixin for top-level SDMX artefacts.

    NOTE: Subclasses must redeclare the standard fields (id, agency_id,
    version, name, description, is_final, annotations) because frozen
    slotted dataclasses cannot inherit slots from a non-dataclass parent.
    This is intentional — see Python docs on __slots__ and inheritance.

    This mixin provides only the .urn() classmethod and the class-level
    sdmx_package / sdmx_class declarations.
    """
```

Optionally, explore a `@maintainable` decorator that injects the fields
via `dataclasses.field` manipulation before `@dataclass` runs. But this
is a later optimization — the comment is sufficient for now.

---

## 7. `Query.order_by` missing `descending` parameter

**File:** `api/query.py` line 44

**Current:**

```python
def order_by(self, key: str | Callable[[T], Any]) -> Self:
    self._order_key = key
    return self
```

**Should be:**

```python
def order_by(self, key: str | Callable[[T], Any], *, descending: bool = False) -> Self:
    self._order_key = key
    self._descending = descending
    return self
```

And in `_apply`:

```python
if self._order_key is not None:
    ...
    items = sorted(items, key=key_fn, reverse=self._descending)
```

Also add `self._descending: bool = False` to `__init__`.

This aligns with `ItemList.sort_by(key, *, descending=False)` which
already has the parameter.

---

## 8. `FmrRegistry` hardcodes `version="2.1"`

**File:** `api/registry.py` line 273

**Current:**

```python
super().__init__(endpoint, version="2.1", timeout=timeout, ...)
```

**Problem:** FMR is a 3.0 registry. The spec says FmrRegistry should
default to `version="3.0"`. However, the SDMX Global Registry serves
2.1 format responses by default at its current endpoint.

**Fix:** Either:

- Change to `version="3.0"` if we have a 3.0 reader (we don't yet)
- Keep as `version="2.1"` but add a comment explaining why:

```python
# The SDMX Global Registry currently serves 2.1 format responses.
# This will change to "3.0" once the sdmx_ml30 reader is implemented.
super().__init__(endpoint, version="2.1", timeout=timeout, ...)
```

---

## 9. Reader uses `object` as phantom type witness everywhere

**File:** `formats/sdmx_ml21/reader.py` — multiple locations

**Current:**

```python
_index_artefact(index, cl, "codelist", "Codelist")  # which calls:
SdmxUrn.make(object, package=..., ...)  # object as type witness
```

**Should use actual types where known:**

```python
SdmxUrn.make(Codelist, package=..., ...)
SdmxUrn.make(ConceptScheme, package=..., ...)
SdmxUrn.make(DataStructure, package=..., ...)
```

This makes the index values carry the correct phantom type, which
improves type safety even though the index is `dict[str, Any]`.

The `_index_artefact` and `_index_item` helpers should take the
artefact type as a parameter:

```python
def _index_artefact[T](index: dict[str, Any], obj: T, artefact_type: type[T]) -> None:
    package = artefact_type.sdmx_package
    cls = artefact_type.sdmx_class
    urn = SdmxUrn.make(artefact_type, package=package, artefact_class=cls, ...)
    ...
```

This also addresses issue #5 (package string duplication) by reading
from the model class.

---

## 10. Session `_latest` index uses `setdefault` — first write wins

**File:** `api/session.py` line 54

**Current:**

```python
self._latest.setdefault(latest_key, artefact)
```

**Problem:** If a response contains `CL_FREQ(1.0)` and then a later
request returns `CL_FREQ(2.0)`, the `_latest` index still points to
`1.0` because `setdefault` doesn't overwrite.

**Fix:** Always overwrite, or compare versions and keep the higher one:

```python
# Simple — always overwrite (last seen = latest)
self._latest[latest_key] = artefact

# Better — compare versions if we want highest
existing = self._latest.get(latest_key)
if existing is None or artefact.version > existing.version:
    self._latest[latest_key] = artefact
```

The simple overwrite is probably correct for the "fully resolved first"
approach where a single `references=all` request populates everything.
Version comparison is better for future multi-request scenarios.

---

## 11. `Query.count()` does a full fetch

**File:** `api/query.py` line 122

**Current:**

```python
def count(self) -> int:
    return len(self.fetch())
```

**Fix for now:** Add a comment noting this is intentional:

```python
def count(self) -> int:
    """Return the number of matching artefacts.

    NOTE: Currently fetches all results to count. A future optimization
    could use server-side count or HEAD request.
    """
    return len(self.fetch())
```

---

## 12. Naming inconsistency: `order_by` vs `sort_by`

**Files:** `api/query.py` uses `order_by`, `model/collections.py` uses `sort_by`

**Problem:** Two different names for the same operation (sort results by
a key function). `ItemList` and `ArtefactList` use `sort_by`, `Query`
uses `order_by`.

**Fix:** Pick one. The spec uses `sort_by` on collections and `order_by`
on Query, which follows the precedent of SQL (`ORDER BY`) vs Python
(`sorted`). This is arguably intentional — the Query is closer to SQL
semantics. But for consistency within the library, consider using
`sort_by` on Query as well:

```python
# Query
def sort_by(self, key: str | Callable[[T], Any], *, descending: bool = False) -> Self:
```

Or keep both and document the naming rationale. Either way, add
`descending` (see issue #7).

---

## Summary

| #   | Severity     | File(s)                         | Issue                                                 |
| --- | ------------ | ------------------------------- | ----------------------------------------------------- |
| 1   | **Blocking** | api/registry.py, api/session.py | Identity map returns unresolved objects for resolve() |
| 2   | Medium       | model/representation.py         | `Enumeration.urn` should be `SdmxUrn[Codelist]`       |
| 3   | Medium       | model/datastructure.py          | `enumeration_urn` returns `object \| None`            |
| 4   | Medium       | model/collections.py            | `ids(qualified=True)` omits version                   |
| 5   | Medium       | multiple                        | Package string duplication                            |
| 6   | Low          | model/base.py                   | Missing comment explaining field repetition           |
| 7   | Low          | api/query.py                    | `order_by` missing `descending` parameter             |
| 8   | Low          | api/registry.py                 | FmrRegistry version comment                           |
| 9   | Low          | formats/sdmx_ml21/reader.py     | `object` as phantom type witness                      |
| 10  | Low          | api/session.py                  | `_latest` first-write-wins behavior                   |
| 11  | Low          | api/query.py                    | `count()` full fetch — needs comment                  |
| 12  | Low          | api/query.py                    | `order_by` vs `sort_by` naming inconsistency          |
