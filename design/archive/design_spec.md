# sdmxlib — Design Specification

This is the implementation specification for sdmxlib. It synthesizes
every design decision into a single document. Use it as the primary
reference when building with Claude Code.

---

## Table of Contents

1. [Principles](#1-principles)
2. [Architecture](#2-architecture)
3. [Package Layout](#3-package-layout)
4. [The User Experience](#4-the-user-experience)
5. [Registry — The Graph Boundary](#5-registry--the-graph-boundary)
6. [Ref[T] — The Reference Abstraction](#6-reft--the-reference-abstraction)
7. [SdmxUrn[T] — Typed Artefact Identity](#7-sdmxurnt--typed-artefact-identity)
8. [Domain Model](#8-domain-model)
9. [Collection Types](#9-collection-types)
10. [Query[T] — Collection Search](#10-queryt--collection-search)
11. [Session API Summary](#11-session-api-summary)
12. [Format Layer](#12-format-layer)
13. [Polars Integration](#13-polars-integration)
14. [FastAPI Integration](#14-fastapi-integration)
15. [SDMX Version Reconciliation](#15-sdmx-version-reconciliation)
16. [Future Work — HTTP Optimization](#16-future-work--http-optimization)
17. [Implementation Order](#17-implementation-order)
18. [CLAUDE.md](#18-claudemd)

---

## 1. Principles

1. **The domain model is the source of truth.** Zero external dependencies.
   Just stdlib + typing. No framework owns it.

2. **Frozen means frozen.** All domain objects are immutable frozen slotted
   dataclasses. No post-hoc mutation. Resolution state is determined at
   construction time and never changes.

3. **The registry presents the remote graph as if it were local.** Within a
   `with sl.RestRegistry(...) as reg:` block, the user navigates the SDMX
   artefact graph through plain attribute access. No URN chasing, no manual
   resolution, no XML structure leaking through.

4. **Fully resolved first.** The initial implementation downloads artefacts
   with `references=all` and constructs fully-resolved domain objects. HTTP
   optimization (caching, lazy loading, partial resolution) comes later.

5. **Ref[T] for outgoing edges.** Every reference to another artefact is a
   `Ref[T]`. It always carries the URN. After resolution, it also carries
   the object. `None` means "no reference exists in the model" — never
   "exists but unfetched."

6. **No XML leaks.** Thirteen XML container wrappers are collapsed by the
   format reader. The user writes `dim.codelist`, not
   `dim.local_representation.enumeration.urn`.

7. **Comprehensions + collection methods.** `ItemList` and `ArtefactList`
   provide `where`, `sort_by`, `partition`, `first_where`, `to_dict` for
   filtering and organizing. Transformation and aggregation use standard
   Python comprehensions and builtins.

8. **Polars is an integration, not a dependency.** `sdmxlib.polars` provides
   the bridge. The core library never imports polars.

9. **Format is a transport detail.** SDMX 2.1 vs 3.0 is a parsing concern.
   The domain model is version-neutral. The version belongs on the registry.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────┐
│                   User code                          │
│  import sdmxlib as sl                                │
│  with sl.RestRegistry(...) as reg:                       │
│      dsd = reg.DataStructure(agency=, id=)           │
│      for dim in dsd.dimensions:                      │
│          if dim.codelist: ...                         │
└───────────────────────┬──────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────┐
│              Registry / Session                       │
│  Graph boundary: identity map, fetch, resolve, query │
│  Constructs fully-resolved domain objects             │
│  Depends on: model, httpx                            │
└───────────────────────┬──────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────┐
│              Domain Model (sdmxlib.model)             │
│  Frozen slotted dataclasses                          │
│  Ref[T], SdmxUrn[T], SdmxRef                        │
│  ItemList[T], ArtefactList[T]                        │
│  InternationalString, Annotations                    │
│  Depends on: nothing (stdlib only)                   │
└───────────────────────┬──────────────────────────────┘
                        │
         ┌──────────────┼──────────────┬───────────────┐
         │              │              │               │
┌────────▼───────┐ ┌────▼─────┐ ┌─────▼──────┐ ┌─────▼──────┐
│ formats/       │ │ formats/ │ │ polars.py  │ │ contrib/   │
│ sdmx_ml21/     │ │ sdmx_ml30│ │ bridge     │ │ fastapi/   │
│ lxml           │ │ lxml     │ │ polars     │ │ pydantic   │
└────────────────┘ └──────────┘ └────────────┘ └────────────┘
```

### Dependency rules

| Module                      | May depend on            |
| --------------------------- | ------------------------ |
| `sdmxlib.model`             | stdlib only              |
| `sdmxlib.api`               | model, httpx             |
| `sdmxlib.formats.sdmx_ml*`  | model, lxml              |
| `sdmxlib.formats.sdmx_json` | model, msgspec           |
| `sdmxlib.polars`            | model, polars            |
| `sdmxlib.contrib.fastapi`   | model, pydantic, fastapi |

---

## 3. Package Layout

```
sdmxlib/
├── __init__.py              # Re-exports everything the user needs
├── model/
│   ├── __init__.py          # Re-exports all domain types
│   ├── base.py              # MaintainableArtefact mixin
│   ├── ref.py               # Ref[T], UnresolvedRef, SdmxRef
│   ├── urn.py               # SdmxUrn[T]
│   ├── collections.py       # ItemList[T], ArtefactList[T], Maintainable protocol
│   ├── istring.py           # InternationalString
│   ├── annotations.py       # Annotation, Annotations
│   ├── representation.py    # TextFormat, Enumeration, Representation alias
│   ├── codelist.py          # Codelist, Code
│   ├── concept.py           # ConceptScheme, Concept
│   ├── datastructure.py     # DataStructure, Dimension, TimeDimension, Attribute, Measure, Group
│   ├── dataflow.py          # Dataflow
│   ├── provision.py         # ProvisionAgreement
│   ├── category.py          # CategoryScheme, Category, Categorisation
│   ├── organisation.py      # Agency, DataProvider, etc.
│   └── message.py           # StructureMessage
├── api/
│   ├── __init__.py          # Re-exports: Registry, FmrRegistry, Session, Query
│   ├── client.py            # SdmxClient (thin HTTP + parse)
│   ├── session.py           # Session: identity map, fetch, resolve, query
│   ├── registry.py          # Registry, FmrRegistry
│   ├── query.py             # Query[T]
│   └── auth.py              # BearerToken
├── formats/
│   ├── __init__.py          # parse(), to_xml(), to_json()
│   ├── registry.py          # Wire mapping registry
│   ├── sdmx_ml21/
│   │   ├── mappings.py      # XML mapping declarations for 2.1
│   │   ├── reader.py        # lxml → domain model
│   │   └── writer.py        # domain model → lxml → bytes
│   ├── sdmx_ml30/
│   │   └── ...
│   └── sdmx_json/
│       ├── schema.py        # msgspec.Struct wire types
│       ├── reader.py        # decode → domain model
│       └── writer.py        # domain model → encode
├── polars.py                # Polars integration
└── contrib/
    └── fastapi/
        ├── schemas.py       # Pydantic models
        └── router.py        # Example router
```

No underscore prefixes on module names. Public API is controlled by
`__all__` on each package `__init__.py`.

---

## 4. The User Experience

These examples show what the user writes. The registry, HTTP, parsing,
identity map, and resolution are all behind the scenes.

### Explore a DSD

```python
import sdmxlib as sl

with sl.RestRegistry("https://registry.sdmx.org/sdmx/v2/") as reg:
    dsd = reg.DataStructure(agency="ESTAT", id="CPI")

    for dim in dsd.dimensions:
        if dim.codelist:
            print(dim.id, dim.codelist.name["en"], len(dim.codelist.codes))
        else:
            print(dim.id, dim.text_type)
```

### Navigate from ProvisionAgreement to all codelists

```python
with sl.RestRegistry("https://registry.sdmx.org/sdmx/v2/") as reg:
    pa = reg.ProvisionAgreement(agency="ESTAT", id="PA_BOP")

    for dim in pa.dataflow.structure.dimensions:
        if dim.codelist:
            print(dim.id, dim.codelist.id, len(dim.codelist.codes))
```

### Build a code map

```python
with sl.RestRegistry("https://registry.sdmx.org/sdmx/v2/") as reg:
    dsd = reg.DataStructure(agency="ESTAT", id="CPI")

    code_maps = {
        dim.id: {code.id: code.name["en"] for code in dim.codelist.codes}
        for dim in dsd.dimensions
        if dim.codelist
    }
```

### Build a Polars schema

```python
import polars as pl
import sdmxlib as sl
import sdmxlib.polars as slpl

with sl.RestRegistry("https://registry.sdmx.org/sdmx/v2/") as reg:
    dsd = reg.DataStructure(agency="ESTAT", id="CPI")
    df = pl.read_csv("cpi.csv", schema_overrides=slpl.schema(dsd))
```

### Browse an agency's dataflows

```python
with sl.RestRegistry("https://registry.sdmx.org/sdmx/v2/") as reg:
    for df in reg.dataflows(agency="OECD.SDD.NAD"):
        print(f"{df.id}: {df.name['en']}")
        print(f"  dimensions: {df.structure.dimensions.ids()}")
```

### Compare DSD versions

```python
with sl.RestRegistry("https://registry.sdmx.org/sdmx/v2/") as reg:
    old = reg.DataStructure(agency="ESTAT", id="CPI", version="1.0")
    new = reg.DataStructure(agency="ESTAT", id="CPI", version="2.0")

    old_dims = old.dimensions.to_dict()
    new_dims = new.dimensions.to_dict()

    for dim_id in set(old_dims) & set(new_dims):
        old_codes = set(old_dims[dim_id].codelist.codes.ids())
        new_codes = set(new_dims[dim_id].codelist.codes.ids())
        if added := new_codes - old_codes:
            print(f"{dim_id}: +{len(added)} codes")
```

### Pattern matching

```python
with sl.RestRegistry("https://registry.sdmx.org/sdmx/v2/") as reg:
    dsd = reg.DataStructure(agency="BIS", id="WS_CBPOL")

    for dim in dsd.dimensions:
        match dim:
            case sl.Dimension(codelist=cl) if cl is not None:
                print(f"{dim.id}: {cl.id} ({len(cl.codes)} codes)")
            case sl.Dimension(text_type=tt) if tt is not None:
                print(f"{dim.id}: text ({tt})")
```

### Filter and sort with collection methods

```python
with sl.RestRegistry("https://registry.sdmx.org/sdmx/v2/") as reg:
    dsd = reg.DataStructure(agency="ESTAT", id="CPI")

    coded, uncoded = dsd.dimensions.partition(lambda d: d.codelist is not None)
    biggest = coded.sort_by(lambda d: len(d.codelist.codes), descending=True)

    for dim in biggest:
        print(f"{dim.id}: {len(dim.codelist.codes)} codes")
```

### Audit annotations

```python
with sl.RestRegistry("https://registry.sdmx.org/sdmx/v2/") as reg:
    for pa in reg.provision_agreements(agency="NSA"):
        for dim in pa.dataflow.structure.dimensions:
            if cl := dim.codelist:
                if "DEPRECATED" in cl.annotations:
                    print(f"{pa.id} → {dim.id}: {cl.id} deprecated")
```

---

## 5. Registry — The Graph Boundary

The Registry is the user's entry point to the SDMX artefact graph. It
manages the HTTP client, identity map, and object construction. Within
a `with` block, the graph is navigable through attribute access.

```python
class Registry:
    """Connection to an SDMX REST endpoint.

    Use as a context manager. All artefact access happens within the scope.

        with sl.RestRegistry("https://registry.sdmx.org/sdmx/v2/") as reg:
            dsd = reg.DataStructure(agency="ESTAT", id="CPI")

    The registry:
      - Manages the HTTP client lifecycle
      - Maintains a session-scoped identity map (same URN → same object)
      - Constructs fully-resolved domain objects (references=all)
      - Presents the remote graph as local attribute access
    """

    def __init__(self, endpoint: str,
                 version: str = "2.1",
                 timeout: float = 30.0,
                 headers: dict[str, str] | None = None,
                 **httpx_kwargs) -> None: ...

    def __enter__(self) -> Registry: ...
    def __exit__(self, *args) -> None: ...
    def close(self) -> None: ...

    # ── Lookup: single artefact by identity ───────────

    def DataStructure(self, *, agency: str, id: str,
                      version: str = "latest") -> DataStructure: ...
    def Codelist(self, *, agency: str, id: str,
                 version: str = "latest") -> Codelist: ...
    def Dataflow(self, *, agency: str, id: str,
                 version: str = "latest") -> Dataflow: ...
    def ConceptScheme(self, *, agency: str, id: str,
                      version: str = "latest") -> ConceptScheme: ...
    def ProvisionAgreement(self, *, agency: str, id: str,
                           version: str = "latest") -> ProvisionAgreement: ...
    def CategoryScheme(self, *, agency: str, id: str,
                       version: str = "latest") -> CategoryScheme: ...

    # ── Lookup: by URN (from a Ref or parsed string) ──

    def fetch(self, urn: SdmxUrn[T]) -> T: ...
    def resolve(self, urn: SdmxUrn[T]) -> T: ...

    # ── Search: collection queries ────────────────────

    def query(self, artefact_type: type[T], *,
              agency: str | None = None) -> Query[T]: ...

    # ── Convenience: common collection queries ────────

    def dataflows(self, *, agency: str | None = None) -> ArtefactList[Dataflow]: ...
    def codelists(self, *, agency: str | None = None) -> ArtefactList[Codelist]: ...
    def data_structures(self, *, agency: str | None = None) -> ArtefactList[DataStructure]: ...
    def provision_agreements(self, *, agency: str | None = None) -> ArtefactList[ProvisionAgreement]: ...
    def concept_schemes(self, *, agency: str | None = None) -> ArtefactList[ConceptScheme]: ...
```

### FmrRegistry

```python
class FmrRegistry(RestRegistry):
    """Registry configured for Fusion Metadata Registry endpoints.

    Defaults to the public SDMX Global Registry with SDMX 3.0.

        with sl.FmrRegistry() as reg: ...                          # public
        with sl.FmrRegistry(auth=("user", "pass")) as reg: ...     # basic
        with sl.FmrRegistry(auth=sl.BearerToken("eyJ...")) as reg: ...
    """

    def __init__(self, endpoint: str = SDMX_GLOBAL_REGISTRY,
                 auth: tuple[str, str] | BearerToken | None = None,
                 timeout: float = 30.0, **kwargs) -> None: ...
```

### Named lookup methods vs generic fetch/resolve

The registry has two paths to get a single artefact:

**Named methods** (`reg.DataStructure(agency=, id=)`) — for when the user
knows what they want and types it directly. The method name IS the type.
Keyword-only args ensure readability. Returns the fully-resolved artefact.

**URN methods** (`reg.fetch(urn)`, `reg.resolve(urn)`) — for when the user
has a `SdmxUrn[T]` from a `Ref`, parsed XML, or programmatic construction.
`fetch` returns with unresolved Refs. `resolve` returns with resolved Refs.

The named methods always resolve (consistent with the "fully resolved first"
principle). `fetch` exists for the future when the user wants to control
resolution, and for the rare case where they only need URN metadata from
Refs without resolving them.

### Identity map

The registry maintains a session-scoped identity map keyed by full URN
string. The same artefact is never fetched twice within a session. When
constructing resolved objects, referenced artefacts are looked up in the
map first.

---

## 6. Ref[T] — The Reference Abstraction

Every outgoing URN edge in the SDMX graph is a `Ref[T]`. It always
carries the URN. After resolution it also carries the resolved object.

```python
@dataclass(frozen=True, slots=True)
class Ref[T]:
    """A reference to an SDMX artefact.

    Always carries the URN. May also carry the resolved object.
    None on the parent field means "no reference exists."
    Ref[T] means "a reference exists."

    Three access tiers:
      ref.id, ref.agency, ref.urn     — URN metadata, always available
      ref.resolved                    — bool check
      ref()                           — unwrap to T, raises if unresolved
    """
    urn: SdmxUrn[T]
    _resolved: T | None = field(default=None, repr=False)

    @property
    def resolved(self) -> bool:
        return self._resolved is not None

    def __call__(self) -> T:
        if self._resolved is None:
            raise UnresolvedRef(self.urn)
        return self._resolved

    @property
    def id(self) -> str:
        return self.urn.id

    @property
    def agency(self) -> str:
        return self.urn.agency

    @property
    def version(self) -> str:
        return self.urn.version

    @classmethod
    def unresolved(cls, urn: SdmxUrn[T]) -> Ref[T]:
        """Create an unresolved Ref (URN only)."""
        return cls(urn=urn)

    @classmethod
    def of(cls, urn: SdmxUrn[T], obj: T) -> Ref[T]:
        """Create a resolved Ref (URN + object). Useful in tests."""
        return cls(urn=urn, _resolved=obj)

    def __bool__(self) -> bool:
        return True

    def __repr__(self) -> str:
        if self._resolved is not None:
            return f"Ref({self.urn.id} → resolved)"
        return f"Ref({self.urn.id} [unresolved])"


class UnresolvedRef(Exception):
    def __init__(self, urn: SdmxUrn) -> None:
        self.urn = urn
        super().__init__(
            f"{urn} has not been resolved. "
            f"Use reg.resolve(urn) or the named registry methods."
        )
```

### Semantic contract

| Expression              | Type                     | Fails?                             |
| ----------------------- | ------------------------ | ---------------------------------- |
| `dim.codelist`          | `Ref[Codelist] \| None`  | Never                              |
| `dim.codelist.id`       | `str`                    | Never (if Ref exists)              |
| `dim.codelist.urn`      | `SdmxUrn[Codelist]`      | Never (if Ref exists)              |
| `dim.codelist.resolved` | `bool`                   | Never (if Ref exists)              |
| `dim.codelist()`        | `Codelist`               | Raises UnresolvedRef if unresolved |
| `dim.codes`             | `ItemList[Code] \| None` | Never — None if unresolved         |
| `None`                  | —                        | No codelist reference exists       |

### Resolved vs unresolved objects

Resolution state is set at construction time and never changes:

- **reg.DataStructure(agency=, id=)** — always returns fully resolved objects.
  `dim.codelist` is `Ref[Codelist]` with the `Codelist` inside. `dim.codelist()`
  returns the `Codelist`. `dim.codes` returns the codes.

- **reg.fetch(urn)** — returns objects with unresolved Refs. `dim.codelist`
  is `Ref[Codelist]` with URN only. `dim.codelist()` raises. `dim.codes`
  returns None.

Both return frozen immutable objects. Neither mutates after construction.

---

## 7. SdmxUrn[T] — Typed Artefact Identity

`T` is a phantom type parameter — no field stores it. It exists for the
type checker to carry the target type through `reg.fetch(urn)` → `T`.

```python
@dataclass(frozen=True, slots=True)
class SdmxUrn[T]:
    package: str
    artefact_class: str
    agency: str
    id: str
    version: str = "latest"
    item_id: str | None = None

    @classmethod
    def parse(cls, urn: str) -> SdmxUrn:
        """Parse a full URN string. Returns unparameterized SdmxUrn."""
        ...

    @classmethod
    def make(cls, *, package: str, artefact_class: str,
             agency: str, id: str, version: str = "latest",
             item_id: str | None = None) -> SdmxUrn:
        """Low-level factory."""
        ...

    def __str__(self) -> str:
        """Round-trip to full URN string."""
        ...

    @property
    def is_latest(self) -> bool:
        return self.version == "latest"

    @property
    def is_item_urn(self) -> bool:
        return self.item_id is not None
```

### .urn() classmethod on model types

Each maintainable model class has a `.urn()` classmethod that returns
`SdmxUrn[Self]`, carrying the type through:

```python
class DataStructure:
    sdmx_package: ClassVar[str] = "datastructure"
    sdmx_class: ClassVar[str] = "DataStructure"

    @classmethod
    def urn(cls, *, agency: str, id: str, version: str = "latest") -> SdmxUrn[DataStructure]:
        return SdmxUrn.make(
            package=cls.sdmx_package, artefact_class=cls.sdmx_class,
            agency=agency, id=id, version=version,
        )
```

This lives in the `MaintainableArtefact` mixin so it doesn't repeat.

Usage:

```python
sl.DataStructure.urn(agency="ESTAT", id="CPI")     # SdmxUrn[DataStructure]
sl.Codelist.urn(agency="BIS", id="CL_FREQ")        # SdmxUrn[Codelist]

# Used with registry fetch/resolve
cl = reg.fetch(dim.codelist.urn)                    # SdmxUrn[Codelist] → Codelist
```

### "latest" semantics

`"latest"` is an SDMX REST concept meaning "the version currently in
production." The server resolves it to a concrete version. Artefacts in
memory always have concrete versions. When used as an `ArtefactList` key,
`version="latest"` matches any version.

### SdmxRef — partial identity for lookup and Query

```python
@dataclass(frozen=True, slots=True)
class SdmxRef:
    """Partial artefact identity for filtering and lookup.

    All fields optional. None means "match any."
    Used as ArtefactList key and in Query.
    """
    agency: str | None = None
    id: str | None = None
    version: str | None = None
```

---

## 8. Domain Model

### 8.1 XML wrapper collapse

The format reader collapses thirteen XML container wrappers during parsing.
The domain model exposes the SDMX information model, not the XML structure.

| XML path                                         | Python path                      | Wrappers collapsed     |
| ------------------------------------------------ | -------------------------------- | ---------------------- |
| `dsd.components.dimension_list.dimensions`       | `dsd.dimensions`                 | 3                      |
| `dsd.components.attribute_list.attributes`       | `dsd.attributes`                 | 3                      |
| `dsd.components.measure_list.primary_measure`    | `dsd.measures`                   | 3 + v2/v3 unification  |
| `dim.concept_identity.ref`                       | `dim.concept`                    | 1 + resolved to object |
| `dim.local_representation.enumeration.ref`       | `dim.codelist`                   | 2 + resolved to object |
| `dim.local_representation.text_format.text_type` | `dim.text_type`                  | 2 + property           |
| `attr.attribute_relationship.*`                  | `attr.attachment` + typed fields | 1 + simplified         |
| `df.structure.ref`                               | `df.structure`                   | 1 + resolved           |
| `pa.structure_usage.ref`                         | `pa.dataflow`                    | 1 + renamed + resolved |
| `code.parent.ref.id`                             | `code.parent_id`                 | 1                      |

### 8.2 InternationalString

```python
class InternationalString:
    """Dict-like multilingual string. Language access is always explicit.

    s["en"]              → str (KeyError if absent)
    s.get("en")          → str | None
    s.get("de", "en")    → first available in priority order
    "fr" in s            → bool
    s.langs              → list[str]
    len(s), bool(s), repr(s)
    """
```

### 8.3 Annotations

```python
@dataclass(frozen=True, slots=True)
class Annotation:
    id: str | None = None
    title: str | None = None
    annotation_type: str | None = None
    url: str | None = None
    text: InternationalString = field(default_factory=InternationalString)


class Annotations(Sequence[Annotation]):
    """Collection with type-based querying.

    "DEPRECATED" in annotations     → bool (checks annotation_type)
    annotations.by_type("CONTACT")  → list[Annotation]
    annotations.first("CONTACT")    → Annotation | None
    annotations.types               → list[str]
    """
```

### 8.4 Representation types

```python
@dataclass(frozen=True, slots=True)
class TextFormat:
    """Non-enumerated representation with constraints."""
    text_type: str | None = None
    max_length: int | None = None
    min_length: int | None = None
    max_value: float | None = None
    min_value: float | None = None
    decimals: int | None = None
    pattern: str | None = None
    start_value: float | None = None
    end_value: float | None = None
    is_sequence: bool | None = None
    is_multi_lingual: bool | None = None


@dataclass(frozen=True, slots=True)
class Enumeration:
    """Enumerated representation: references a codelist."""
    urn: SdmxUrn[Codelist]

    @property
    def codelist_id(self) -> str:
        return self.urn.id

    @property
    def agency(self) -> str:
        return self.urn.agency


type Representation = TextFormat | Enumeration | None
```

### 8.5 Codelist & Code

```python
@dataclass(frozen=True, slots=True)
class Code:
    id: str
    name: InternationalString = field(default_factory=InternationalString)
    description: InternationalString = field(default_factory=InternationalString)
    parent_id: str | None = None
    annotations: Annotations = field(default_factory=Annotations)


@dataclass(frozen=True, slots=True)
class Codelist:  # MaintainableArtefact
    id: str
    agency_id: str
    version: str = "1.0"
    name: InternationalString = field(default_factory=InternationalString)
    description: InternationalString = field(default_factory=InternationalString)
    is_final: bool = False
    annotations: Annotations = field(default_factory=Annotations)
    codes: ItemList[Code] = field(default_factory=ItemList)

    # Hierarchy helpers
    def roots(self) -> ItemList[Code]:
        """Top-level codes (no parent)."""
        return self.codes.where(lambda c: c.parent_id is None)

    def children_of(self, code_id: str) -> ItemList[Code]:
        """Direct children of a code."""
        return self.codes.where(lambda c: c.parent_id == code_id)
```

### 8.6 Concept & ConceptScheme

```python
@dataclass(frozen=True, slots=True)
class Concept:
    id: str
    name: InternationalString = field(default_factory=InternationalString)
    description: InternationalString = field(default_factory=InternationalString)
    core_representation: Representation = None
    annotations: Annotations = field(default_factory=Annotations)


@dataclass(frozen=True, slots=True)
class ConceptScheme:  # MaintainableArtefact
    id: str
    agency_id: str
    version: str = "1.0"
    name: InternationalString = field(default_factory=InternationalString)
    description: InternationalString = field(default_factory=InternationalString)
    concepts: ItemList[Concept] = field(default_factory=ItemList)
    annotations: Annotations = field(default_factory=Annotations)
```

### 8.7 DataStructure & Components

```python
class AttachmentLevel(Enum):
    DATASET = "dataset"
    OBSERVATION = "observation"
    SERIES = "series"
    GROUP = "group"


@dataclass(frozen=True, slots=True)
class Dimension:
    id: str
    position: int | None = None
    codelist: Ref[Codelist] | None = None
    concept: Ref[Concept] | None = None
    representation: Representation = None
    annotations: Annotations = field(default_factory=Annotations)

    @property
    def enumeration_urn(self) -> SdmxUrn[Codelist] | None:
        return self.codelist.urn if self.codelist else None

    @property
    def is_enumerated(self) -> bool:
        return self.codelist is not None

    @property
    def text_type(self) -> str | None:
        match self.representation:
            case TextFormat(text_type=tt): return tt
            case _: return None

    @property
    def codes(self) -> ItemList[Code] | None:
        """Convenience: codelist codes if resolved, else None."""
        if self.codelist and self.codelist.resolved:
            return self.codelist().codes
        return None


@dataclass(frozen=True, slots=True)
class TimeDimension:
    id: str = "TIME_PERIOD"
    concept: Ref[Concept] | None = None
    representation: TextFormat | None = None
    annotations: Annotations = field(default_factory=Annotations)

    @property
    def text_type(self) -> str | None:
        return self.representation.text_type if self.representation else None


@dataclass(frozen=True, slots=True)
class Attribute:
    id: str
    usage: str | None = None
    attachment: AttachmentLevel | None = None
    related_dimensions: list[str] | None = None
    related_measure: str | None = None
    codelist: Ref[Codelist] | None = None
    concept: Ref[Concept] | None = None
    representation: Representation = None
    annotations: Annotations = field(default_factory=Annotations)

    @property
    def is_enumerated(self) -> bool:
        return self.codelist is not None

    @property
    def codes(self) -> ItemList[Code] | None:
        if self.codelist and self.codelist.resolved:
            return self.codelist().codes
        return None


@dataclass(frozen=True, slots=True)
class Measure:
    id: str
    usage: str | None = None
    codelist: Ref[Codelist] | None = None
    concept: Ref[Concept] | None = None
    representation: Representation = None
    annotations: Annotations = field(default_factory=Annotations)

    @property
    def codes(self) -> ItemList[Code] | None:
        if self.codelist and self.codelist.resolved:
            return self.codelist().codes
        return None


@dataclass(frozen=True, slots=True)
class Group:
    """Dimension grouping (v2.1). Optional, None for v3.0 sources."""
    id: str
    dimension_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DataStructure:  # MaintainableArtefact
    id: str
    agency_id: str
    version: str = "1.0"
    name: InternationalString = field(default_factory=InternationalString)
    description: InternationalString = field(default_factory=InternationalString)
    is_final: bool = False
    annotations: Annotations = field(default_factory=Annotations)

    dimensions: ItemList[Dimension] = field(default_factory=ItemList)
    time_dimension: TimeDimension | None = None
    attributes: ItemList[Attribute] = field(default_factory=ItemList)
    measures: ItemList[Measure] = field(default_factory=ItemList)
    groups: ItemList[Group] | None = None

    # ── Convenience ───────────────────────────────────

    @property
    def components(self) -> ItemList[Dimension | Attribute | Measure]:
        """All components: dimensions + attributes + measures."""
        return ItemList([*self.dimensions, *self.attributes, *self.measures])

    @property
    def enumerated_dimensions(self) -> ItemList[Dimension]:
        return self.dimensions.where(lambda d: d.is_enumerated)

    @property
    def free_text_dimensions(self) -> ItemList[Dimension]:
        return self.dimensions.where(lambda d: not d.is_enumerated)

    @property
    def all_codelists(self) -> dict[str, Codelist]:
        """All resolved codelists keyed by component id."""
        result = {}
        for comp in [*self.dimensions, *self.attributes, *self.measures]:
            if comp.codelist and comp.codelist.resolved:
                result[comp.id] = comp.codelist()
        return result

    def code_map(self, lang: str = "en") -> dict[str, dict[str, str]]:
        """Build {component_id: {code_id: label}}. Plain dict, no Polars."""
        result = {}
        for comp_id, cl in self.all_codelists.items():
            result[comp_id] = {
                code.id: code.name.get(lang) or code.id
                for code in cl.codes
            }
        return result

    def column_labels(self, lang: str = "en") -> dict[str, str]:
        """Map component ids to human labels from concepts. Plain dict."""
        result = {}
        for comp in [*self.dimensions, *self.attributes, *self.measures]:
            if comp.concept and comp.concept.resolved:
                result[comp.id] = comp.concept().name.get(lang) or comp.id
            else:
                result[comp.id] = comp.id
        return result
```

### 8.8 Dataflow & ProvisionAgreement

```python
@dataclass(frozen=True, slots=True)
class Dataflow:  # MaintainableArtefact
    id: str
    agency_id: str
    version: str = "1.0"
    name: InternationalString = field(default_factory=InternationalString)
    description: InternationalString = field(default_factory=InternationalString)
    annotations: Annotations = field(default_factory=Annotations)
    structure: Ref[DataStructure] | None = None


@dataclass(frozen=True, slots=True)
class ProvisionAgreement:  # MaintainableArtefact
    id: str
    agency_id: str
    version: str = "1.0"
    name: InternationalString = field(default_factory=InternationalString)
    description: InternationalString = field(default_factory=InternationalString)
    annotations: Annotations = field(default_factory=Annotations)
    dataflow: Ref[Dataflow] | None = None
    data_provider: Ref[Any] | None = None
```

### 8.9 StructureMessage

```python
@dataclass(frozen=True, slots=True)
class StructureMessage:
    """Version-neutral envelope for SDMX structural artefacts."""
    codelists: ArtefactList[Codelist] = field(default_factory=ArtefactList)
    concept_schemes: ArtefactList[ConceptScheme] = field(default_factory=ArtefactList)
    data_structures: ArtefactList[DataStructure] = field(default_factory=ArtefactList)
    dataflows: ArtefactList[Dataflow] = field(default_factory=ArtefactList)
    category_schemes: ArtefactList[CategoryScheme] = field(default_factory=ArtefactList)
    categorisations: ArtefactList[Categorisation] = field(default_factory=ArtefactList)
    provision_agreements: ArtefactList[ProvisionAgreement] = field(default_factory=ArtefactList)

    def all_artefacts(self) -> list[Any]:
        return [
            *self.codelists, *self.concept_schemes, *self.data_structures,
            *self.dataflows, *self.category_schemes, *self.categorisations,
            *self.provision_agreements,
        ]
```

---

## 9. Collection Types

Two types that map onto the SDMX information model's distinction between
child artefacts and maintainable artefacts.

### ItemList[T] — child artefacts

For codes within a codelist, dimensions within a DSD, concepts within a
scheme. Items don't have `agency_id` — they belong to their parent.
Lookup by bare `id` only.

```python
class ItemList[T](Sequence[T]):
    # ── Lookup ────────────────────────────────────────
    def __getitem__(self, key: int | str) -> T: ...
    def get(self, id: str) -> T | None: ...
    def __contains__(self, key: object) -> bool: ...
    def ids(self) -> list[str]: ...

    # ── Filtering (returns new ItemList — chainable) ──
    def where(self, predicate: Callable[[T], bool]) -> ItemList[T]: ...
    def first_where(self, predicate: Callable[[T], bool]) -> T | None: ...
    def partition(self, predicate: Callable[[T], bool]) -> tuple[ItemList[T], ItemList[T]]: ...

    # ── Ordering (returns new ItemList — chainable) ───
    def sort_by(self, key: Callable[[T], Any], *, descending: bool = False) -> ItemList[T]: ...

    # ── Conversion ────────────────────────────────────
    def to_dict(self, key: str | Callable[[T], Any] = "id") -> dict[Any, T]: ...

    # ── Standard sequence ─────────────────────────────
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[T]: ...
    def __bool__(self) -> bool: ...
    def __repr__(self) -> str: ...
```

### ArtefactList[T] — maintainable artefacts

For top-level collections in StructureMessage and query results. Items
have `agency_id` and `version`. Supports qualified key lookup.

```python
@runtime_checkable
class Maintainable(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def agency_id(self) -> str: ...
    @property
    def version(self) -> str: ...


class ArtefactList[T: Maintainable](Sequence[T]):
    # Everything from ItemList plus:

    # Key formats:
    #   "CL_FREQ"                  — bare id (must be unambiguous)
    #   "SDMX:CL_FREQ"            — agency-qualified
    #   "SDMX:CL_FREQ(1.0)"       — fully qualified
    #   SdmxUrn[T]                 — typed URN (version="latest" matches any)
    #   SdmxRef(agency=, id=)      — partial identity

    def __getitem__(self, key: int | str | SdmxUrn | SdmxRef) -> T: ...
    def get(self, key: str | SdmxUrn | SdmxRef) -> T | None: ...
    def __contains__(self, key: object) -> bool: ...
    def ids(self, *, qualified: bool = False) -> list[str]: ...

    # Same filtering/ordering/conversion as ItemList
    def where(self, predicate: Callable[[T], bool]) -> ArtefactList[T]: ...
    def sort_by(self, key: Callable[[T], Any], *, descending: bool = False) -> ArtefactList[T]: ...
    def partition(self, predicate: Callable[[T], bool]) -> tuple[ArtefactList[T], ArtefactList[T]]: ...
    def first_where(self, predicate: Callable[[T], bool]) -> T | None: ...
    def to_dict(self, key: str | Callable[[T], Any] = "id") -> dict[Any, T]: ...
```

### Ambiguity handling on ArtefactList

```python
msg.codelists["CL_FREQ"]
# KeyError: Ambiguous id 'CL_FREQ' matches 2 artefacts:
#   SDMX:CL_FREQ(1.0), ESTAT:CL_FREQ(2.0). Use a qualified key.

msg.codelists["SDMX:CL_FREQ"]         # unambiguous
msg.codelists[dim.codelist.urn]        # SdmxUrn — unambiguous
msg.codelists.get("CL_FREQ")          # None on ambiguity
```

---

## 10. Query[T] — Collection Search

For browsing/searching when you don't know a specific artefact's identity.
Created by `reg.query(type)`. Server-side constraint (agency) is set at
construction. Client-side refinement chains.

```python
class Query[T]:
    # ── Client-side refinement (return Self) ──────────
    def filter(self, predicate: Callable[[T], bool]) -> Self: ...
    def order_by(self, key: str | Callable[[T], Any]) -> Self: ...
    def limit(self, n: int) -> Self: ...

    # ── Terminals ─────────────────────────────────────
    def fetch(self) -> list[T]: ...
    def fetch_one(self) -> T: ...
    def fetch_first(self) -> T: ...
    def fetch_optional(self) -> T | None: ...

    def resolve(self) -> list[T]: ...
    def resolve_one(self) -> T: ...
    def resolve_first(self) -> T: ...
    def resolve_optional(self) -> T | None: ...

    def count(self) -> int: ...
```

Usage:

```python
# Browse
all_cls = reg.query(sl.Codelist, agency="ESTAT").fetch()

# Filter and sort
big = (
    reg.query(sl.Codelist, agency="ESTAT")
    .filter(lambda cl: len(cl.codes) > 100)
    .order_by("id")
    .limit(10)
    .fetch()
)

# The convenience methods on Registry are sugar for common queries
reg.dataflows(agency="ESTAT")
# is equivalent to:
reg.query(sl.Dataflow, agency="ESTAT").resolve()
```

---

## 11. Session API Summary

Three operations on Registry, each with a clear purpose:

| Operation                             | Input                       | Output         | Use case                  |
| ------------------------------------- | --------------------------- | -------------- | ------------------------- |
| `reg.DataStructure(agency=, id=)`     | keyword identity            | `T` (resolved) | "I know what I want"      |
| `reg.fetch(urn)` / `reg.resolve(urn)` | `SdmxUrn[T]`                | `T`            | "I have a URN from a Ref" |
| `reg.query(type, agency=)`            | `type[T]` + optional agency | `Query[T]`     | "I want to browse/search" |

No `.where()` on Query. Server-side constraint (agency) is set at
construction. Everything on Query is post-fetch client-side refinement.
If you know the id, use the named lookup or construct a URN — that's a
lookup, not a query.

---

## 12. Format Layer

### parse()

```python
def parse(data: bytes, *, version: str | None = None) -> StructureMessage:
    """Parse SDMX bytes into a StructureMessage.
    Auto-detects version from XML namespace if not specified.
    Cross-references within the message are auto-resolved."""
```

### Wire mapping pattern

Each domain type gets a mapping that describes how to find its fields in
the XML. The reader uses these mappings generically. Adding a new resource
type means adding a mapping, not writing parser code.

```python
register_xml_mapping(
    Dimension,
    xpath="str:Dimension",
    fields={
        "id": "@id",
        "position": ("@position", int),
        "concept_identity_urn": XmlConceptRef("str:ConceptIdentity/Ref"),
        "representation": XmlRepresentation(
            enumeration="str:LocalRepresentation/str:Enumeration/Ref",
            text_format="str:LocalRepresentation/str:TextFormat",
        ),
    },
)
```

### Parsed XML with local resolution

```python
msg = parse(Path("structures.xml").read_bytes())

dsd = msg.data_structures[0]
for dim in dsd.dimensions:
    if ref := dim.codelist:
        if ref.resolved:
            print(f"{dim.id} → {ref().id}")     # resolved from same message
        else:
            print(f"{dim.id} → {ref.id} (not in this message)")
```

---

## 13. Polars Integration

`sdmxlib/polars.py` — depends on model + polars. Never imported by core.

```python
"""Polars integration for sdmxlib.

    import sdmxlib.polars as slpl
"""

def schema(dsd: DataStructure) -> dict[str, pl.DataType]:
    """Polars schema with Enum types for enumerated components.

        df = pl.read_csv("data.csv", schema_overrides=slpl.schema(dsd))
    """

def validation_report(dsd: DataStructure,
                      df: pl.LazyFrame | pl.DataFrame) -> pl.DataFrame:
    """Validate a DataFrame against a resolved DSD.

    Returns DataFrame of violations:
        ┌──────────┬───────────────┬────────┐
        │ column   │ invalid_value │ count  │
        └──────────┴───────────────┴────────┘
    """

def label(df: pl.LazyFrame, dsd: DataStructure,
          lang: str = "en", suffix: str = "_label") -> pl.LazyFrame:
    """Add label columns for enumerated components.

        df = slpl.label(pl.scan_csv("data.csv"), dsd).collect()
    """

def structure_frame(dsd: DataStructure, lang: str = "en") -> pl.DataFrame:
    """Export DSD structure as a DataFrame.

        ┌────────────┬───────────┬──────────┬───────────┬──────────┬─────────┐
        │ component  │ role      │ position │ concept   │ codelist │ n_codes │
        └────────────┴───────────┴──────────┴───────────┴──────────┴─────────┘
    """
```

---

## 14. FastAPI Integration

`sdmxlib/contrib/fastapi/` — depends on model + pydantic + fastapi.

```python
# schemas.py — Pydantic models with from_attributes=True
class CodelistSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    agency_id: str
    version: str
    # ...
```

---

## 15. SDMX Version Reconciliation

_Separate design document. Principle established, details deferred._

The domain model uses v3.x semantics as the canonical form. The v2.1
format reader maps into v3 types; the v2.1 format writer maps back.

| v2.1                          | v3.0                 | Domain model                        |
| ----------------------------- | -------------------- | ----------------------------------- |
| `PrimaryMeasure` (single)     | `Measure` (multiple) | `measures: ItemList[Measure]`       |
| `DimensionDescriptor` wrapper | flat dimensions      | `dimensions: ItemList[Dimension]`   |
| `GroupDimensionDescriptor`    | dropped              | `groups: ItemList[Group] \| None`   |
| nested `LocalRepresentation`  | same                 | flattened to `representation` field |
| no `Measure.usage`            | `Measure.usage`      | included, None for v2.1             |

Version is on the registry, not the model:

```python
with sl.RestRegistry(endpoint, version="2.1") as reg: ...
with sl.FmrRegistry() as reg: ...  # defaults to 3.0
```

---

## 16. Future Work — HTTP Optimization

The initial implementation fetches with `references=all` for every
request and constructs fully-resolved objects. This is simple and correct.

Future optimizations (not in scope for initial implementation):

- **Identity map cache hits**: if an artefact is already in the map from
  a previous request, don't fetch again.
- **Lazy resolution**: `reg.fetch(urn)` returns unresolved objects.
  `reg.resolve(urn)` returns resolved. The user chooses.
- **Partial resolution**: fetch only the reference types needed
  (`references=codelist` instead of `references=all`).
- **Request batching**: detect N+1 patterns and batch into fewer requests.
- **TTL / cache invalidation**: expire identity map entries after a
  configurable duration.

The domain model already supports this future through `Ref[T]` —
unresolved Refs carry URN only, resolved Refs carry the object. The
`fetch` vs `resolve` distinction on the registry is designed for this
split. But the initial implementation always resolves.

---

## 17. Implementation Order

Each phase is independently testable. Build bottom-up.

### Phase 1: Foundation types (no I/O)

1. `model/urn.py` — SdmxUrn[T], regex parser, .make()
2. `model/istring.py` — InternationalString
3. `model/annotations.py` — Annotation, Annotations
4. `model/ref.py` — Ref[T], UnresolvedRef, SdmxRef
5. `model/collections.py` — ItemList[T], ArtefactList[T], Maintainable protocol
6. `model/representation.py` — TextFormat, Enumeration, Representation alias

**Test:** Unit tests for each type. Construction, equality, hashing,
pattern matching, collection operations (where, sort_by, partition,
to_dict, qualified key lookup, ambiguity handling).

### Phase 2: Domain model types

7. `model/codelist.py` — Codelist, Code
8. `model/concept.py` — ConceptScheme, Concept
9. `model/datastructure.py` — DataStructure, Dimension, TimeDimension,
   Attribute, Measure, Group + all convenience properties
10. `model/dataflow.py` — Dataflow
11. `model/provision.py` — ProvisionAgreement
12. `model/category.py` — CategoryScheme, Category, Categorisation
13. `model/organisation.py` — Agency, DataProvider
14. `model/message.py` — StructureMessage
15. `model/base.py` — MaintainableArtefact mixin with .urn() classmethod

**Test:** Construction with Ref.of() and Ref.unresolved(). Convenience
properties (dim.codes, dsd.code_map(), dsd.all_codelists). Pattern
matching on Representation. Navigation chains in tests.

### Phase 3: Format layer (lxml)

16. `formats/registry.py` — wire mapping registry
17. `formats/sdmx_ml21/mappings.py` — mapping declarations
18. `formats/sdmx_ml21/reader.py` — lxml iterparse → domain model
19. `formats/__init__.py` — parse() entry point

**Test:** Parse real SDMX-ML 2.1 messages from OECD, ECB, ESTAT.
Verify cross-references within a message are auto-resolved. Verify
all XML wrappers are collapsed.

### Phase 4: API layer

20. `api/client.py` — SdmxClient (thin HTTP + parse)
21. `api/query.py` — Query[T]
22. `api/session.py` — Session with identity map
23. `api/registry.py` — Registry, FmrRegistry
24. `api/auth.py` — BearerToken

**Test:** Integration tests against live endpoints. Named lookup
returns resolved objects. Navigation chains work end-to-end.
Identity map prevents duplicate fetches.

### Phase 5: Polars bridge

25. `polars.py` — schema(), validation_report(), label(), structure_frame()

**Test:** Generate schema from resolved DSD. Validate sample CSV.

### Phase 6: Extras

26. SDMX-ML 3.0 reader
27. SDMX-JSON reader
28. SDMX-ML writer
29. FastAPI contrib
30. `__init__.py` re-exports

---

## 18. CLAUDE.md

Place at repository root.

````markdown
# sdmxlib — SDMX structural metadata library for Python

## What this library does

sdmxlib fetches and models SDMX structural metadata (DataStructures,
Codelists, Dataflows, etc.) from SDMX REST APIs. Users navigate a typed
graph of artefacts through plain attribute access. Observation data is
handled by Polars, not by this library.

## SDMX glossary

- **DSD** (DataStructureDefinition): defines the shape of a dataset —
  dimensions, attributes, measures
- **Dimension**: component that identifies an observation. Usually
  enumerated by a Codelist.
- **Codelist**: finite set of valid codes (e.g. CL_FREQ: A=Annual, Q=Quarterly)
- **Concept**: semantic meaning of a component (e.g. "Frequency")
- **Dataflow**: published data product backed by a DSD
- **ProvisionAgreement**: binds a data provider to a dataflow
- **URN**: globally unique identifier for any SDMX artefact
- **Ref[T]**: reference to another artefact. Always carries URN. May
  carry resolved object. ref() to unwrap.

## Architecture rules

- `sdmxlib.model` has ZERO external dependencies. Just stdlib.
- All domain objects are frozen slotted dataclasses. Truly immutable.
- `Ref[T]` for every outgoing URN edge. None = "doesn't exist."
- Registry presents the remote graph as local attribute access.
- Format layer in `sdmxlib/formats/` — domain model knows nothing
  about XML/JSON.
- Polars bridge in `sdmxlib/polars.py` — core never imports polars.

## Common patterns

```python
import sdmxlib as sl

with sl.RestRegistry("https://registry.sdmx.org/sdmx/v2/") as reg:
    dsd = reg.DataStructure(agency="ESTAT", id="CPI")

    # Navigate — pure attribute access
    dsd.dimensions                     # ItemList[Dimension]
    dsd.dimensions["FREQ"]             # Dimension
    dsd.dimensions["FREQ"].codelist    # Ref[Codelist] (resolved)
    dsd.dimensions["FREQ"].codelist()  # Codelist (unwrap)
    dsd.dimensions["FREQ"].codes       # ItemList[Code] (convenience)

    # Aggregate
    dsd.code_map("en")                # dict[str, dict[str, str]]
    dsd.all_codelists                  # dict[str, Codelist]
    dsd.column_labels("en")            # dict[str, str]

    # Filter
    dsd.dimensions.where(lambda d: d.is_enumerated)
    dsd.dimensions.partition(lambda d: d.is_enumerated)

    # Pattern match
    match dim.representation:
        case sl.Enumeration(): ...
        case sl.TextFormat(text_type=tt): ...

    # Browse
    for df in reg.dataflows(agency="ESTAT"):
        print(df.id, df.structure.dimensions.ids())
```

## Key files

| File                     | Purpose                               |
| ------------------------ | ------------------------------------- |
| `model/ref.py`           | Ref[T], SdmxRef, UnresolvedRef        |
| `model/urn.py`           | SdmxUrn[T] — typed artefact identity  |
| `model/collections.py`   | ItemList[T], ArtefactList[T]          |
| `model/istring.py`       | InternationalString                   |
| `model/datastructure.py` | DSD, Dimension, Attribute, Measure    |
| `api/session.py`         | Session — identity map + construction |
| `api/registry.py`        | Registry — user-facing entry point    |
| `api/query.py`           | Query[T] — collection search          |
| `formats/sdmx_ml21/`     | lxml-based XML reader/writer          |
| `polars.py`              | Polars bridge                         |

## Testing

```bash
uv run pytest -m unit          # unit tests
uv run pytest -m integration   # live endpoint tests
uv run basedpyright            # type checking
uv run ruff check              # linting
```

## Dependencies

- Core: stdlib only (model), httpx (api)
- Formats: lxml (XML), msgspec (JSON)
- Optional: polars (polars.py), pydantic + fastapi (contrib)
````
