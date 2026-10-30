# Hierarchies

Recipes for navigating `Hierarchy` artefacts — cross-codelist hierarchical
views used for geographic, industry, and other classification structures.

A `Hierarchy` differs from a hierarchical `Codelist` in that it can span
codes from **multiple** codelists (e.g. country codes from one list, region
codes from another) and exists as a standalone maintainable artefact.

`Hierarchy`, `Codelist`, `CategoryScheme`, and `MetadataStructure` all
expose the **same SKOS-aligned tree API** — once you learn it on one,
you have it on all four. The methods on this page work identically (with
their respective node types) across those artefacts.

---

## Nodes are named by path

A node in a hierarchy, a category, a metadata attribute: SDMX identifies each
by its **path** from a root, `"A.01"`, because its id may repeat in another
branch. OECD places `MR` at a root *and* under `TYPO_METRO`; Eurostat files
one category id under four parents. So every method below that names a node
takes a dotted path, and a bare id names a root:

```python
import sdmxlib as sl

regions = sl.Codelist(
    id="CL_REGIONS",
    maintainer="AG",
    name={"en": "Regions"},
    codes=[sl.Code(id="MR", name={"en": "Metro region"}), sl.Code(id="TYPO_METRO", name={"en": "Metro typology"})],
)
h = sl.Hierarchy(
    id="H",
    maintainer="AG",
    name={"en": "h"},
    tree=[
        sl.HierarchicalCode(id="MR", code=regions.ref_to("MR")),
        sl.HierarchicalCode(
            id="TYPO_METRO",
            code=regions.ref_to("TYPO_METRO"),
            children=[sl.HierarchicalCode(id="MR", code=regions.ref_to("MR"))],
        ),
    ],
)
h["TYPO_METRO.MR"]  # one node, by path
"MR" in h  # True: a root, by its bare id
[h.path_of(n) for n in h.by_id("MR")]  # ['MR', 'TYPO_METRO.MR'] -- every node with that id
```

`by_id` is the way in when you have only the leaf. It answers with every node
that carries it, because one leaf may name several. pysdmx draws the same
line: `hierarchy["1.11.111"]` and `hierarchy.by_id("111")`.

---

## SKOS-aligned tree access

Every tree-shaped artefact has the same vocabulary:

```python
import sdmxlib as sl

with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    h = reg.get(sl.Hierarchy.urn_for(agency="ESTAT", id="NACE2_H"), resolve=True)

    # Multi-node walks (return ItemList[HierarchicalCode] for in-memory)
    h.roots()  # top-level nodes (= h.tree)
    h.leaves()  # nodes with no children
    h.narrower("A")  # direct children of the root "A"
    h.narrower_transitive("A")  # all descendants
    h.narrower_transitive("A", depth=1)  # bounded
    h.broader_transitive("A.01")  # ancestor chain, root-first

    # Single-result accessors
    h.broader("A.01")  # parent node (HierarchicalCode | None)
    h.depth_of("A.01")  # int (root = 0)

    # Bulk projections, keyed by path
    h.depths()  # {path: depth}
    h.broader_map()  # {path: parent_path | None}
    h.narrower_map()  # {path: tuple[child_path, ...]}
    h.ancestors_map()  # root-first chain per node
    h.descendants_map()  # pre-order DFS per node
```

Underlying tree access is still available:

```python
h.tree  # ItemList[HierarchicalCode] — root nodes
h.codes  # every node, pre-order, as a tuple
h["CA.ON.OTTAWA"]  # HierarchicalCode lookup by path
h["CA.ON.OTTAWA"].code()  # Ref[Code] → Code
```

---

## Printing a hierarchy tree

```python
def print_tree(h: sl.Hierarchy, path: str, indent: int = 0) -> None:
    node = h[path]
    # A node shows its code's name: a hierarchical code has none of its own.
    label = node.code().name.as_dict().get("en", node.id)
    print("  " * indent + f"[{node.id}] {label}")
    for child in h.narrower(path):
        print_tree(h, h.path_of(child), indent + 1)


for root in h.roots():
    print_tree(h, root.id)
```

```
[A] Agriculture, forestry and fishing
  [01] Crop and animal production, hunting
  [02] Forestry and logging
  [03] Fishing and aquaculture
[B] Mining and quarrying
  [05] Mining of coal and lignite
  ...
```

---

## Inheriting values down the tree

A common analytical task: assign explicit values to a few anchor nodes
and have all descendants inherit the closest ancestor's value.

```python
# Tag each top-level NACE section with its "broad sector"
sectors = {
    "A": "primary",
    "B": "primary",
    "C": "manufacturing",
    "G": "services",
    "K": "services",
    # ...
}

broad_sectors = h.inherit_along_broader(sectors)
# → every descendant node now carries its nearest ancestor's value
print(broad_sectors["A.01"])  # "primary"   — 01 sits under A
print(broad_sectors["C.29"])  # "manufacturing"  — 29 sits under C
```

`inherit_along_broader` returns only the nodes whose chain has at least
one explicit value — nodes outside the labelled subtrees are omitted.

---

## Mapping every node to its ancestor at a level

The central query in hierarchical analysis: to total observations by
province you need every geography mapped to the province containing it.

`ancestor_at` answers that as a **relation**, not a dictionary -- the mapping
stays in the database and composes into a join, so it costs a handful of bind
parameters whatever the size of the hierarchy.

It is not called `rollup` because SQL's `GROUP BY ROLLUP(...)` means grouping
sets with subtotals -- a different thing that DuckDB supports and this library
may expose later.

It is a `LazyHierarchy` method for that reason -- a relation needs the
database it runs on -- so this section works from a store rather than from the
in-memory `h` above.

```python
with sl.LocalRegistry("classifications.duckdb") as local:
    h = local.get(h_urn)  # LazyHierarchy

    rollup = h.ancestor_at("PR")  # a Level, or its id

    rollup.columns  # ("path", "node_id", "rollup_path", "rollup_id")
    rollup.to_dict()  # {"CA.ON.ON-01": "CA.ON", "CA.ON": "CA.ON", "CA.QC": "CA.QC"}
    rollup.collect()  # pl.DataFrame, for joining in Polars
```

Keyed by path, like every map on this page: a node id may sit in two
branches, and an id-keyed mapping would give that node two images. The ids
ride along because they are what you join observations on.

Three rules:

- a node **at** the level maps to itself (`"ON" -> "ON"`),
- a node below maps to its **nearest** ancestor at that level,
- a node with **no** ancestor at that level is **absent** -- not mapped to
  `None`. `"CAN"` sits above `PR`, so it does not appear. Were it mapped to
  null it would quietly become a null group in every rollup aggregate.

These are the semantics of `inherit_along_broader` above, seeded with each
level node mapped to itself. The two are property-tested against each other.

### Self-inclusion is explicit everywhere

`ancestor_at` includes the identity by **default**, because it is a *mapping*:
dropping it would leave every node at the level with no image at all.

The transitive walks default the other way -- they are *closures*, and SKOS
specifies `broaderTransitive` as non-reflexive:

```python
cl.narrower_transitive("A")  # ["B", "C"]      -- excludes A
cl.narrower_transitive("A", include_self=True)  # ["A", "B", "C"]
cl.broader_transitive("C", include_self=True)  # ["A", "B", "C"]

with sl.LocalRegistry("classifications.duckdb") as local:
    lazy_cl = local.get(cl.urn)
    lazy_cl.codes.filter(sl.q.code.ancestors.id == "A")  # strict descendants of A

    h = local.get(h_urn)
    h.ancestor_at("PR")  # identity included
    h.ancestor_at("PR", include_self=False)  # strict ancestors only
```

Same flag, same name, opposite defaults -- because a closure is a set of
*other* nodes and a mapping is a total function. Each site documents which it
is.

### Using it to aggregate

The mapping is a relation, so it is a *join* input. For the membership
case -- "only geographies that roll up to some province" -- project it to
one column with `ancestors()`:

```python
with sl.LocalRegistry("classifications.duckdb") as local:
    h = local.get(h_urn)

    # every province in the hierarchy
    provinces = h.ancestor_at("PR").ancestors()

    # nodes that have a province
    h.nodes.filter(sl.q.hcode.id.is_in(provinces)).ids()
```

Passing the whole rollup to `is_in` is rejected, with a message
pointing at `ancestors()` -- otherwise the mismatch surfaces from DuckDB as
a column-count error a long way from the call that caused it.

<!-- test: raises -->

```python
with sl.LocalRegistry("classifications.duckdb") as local:
    h = local.get(h_urn)
    h.nodes.filter(sl.q.hcode.id.is_in(h.ancestor_at("PR")))
# TypeError: is_in() needs a one-column relation, got 4 (path, node_id, rollup_path, rollup_id).
#            Narrow it with .ancestors().
```

---

## Filtering by level

When a `Hierarchy` carries SDMX 3.1 `Level` definitions, you can filter
nodes by their structural level. SDMX reads levels as a chain by depth: the
hierarchy's top codes sit at its first level, their children at the second,
and so on. A code names its level only to skip *down* from its depth, so the
broadest level comes first. Here that is a `Country` level for `CA`, then
province and census division. A code that names a level above its own depth
is refused. On the in-memory `Hierarchy`, walk `h.codes`:

```python
eager = sl.read("classifications.xml").hierarchies["H_GEO_STAT"]
provinces = [n for n in eager.codes if n.level is not None and n.level.id == "PR"]
```

On `LazyHierarchy` (returned from `LocalRegistry.get`), use the predicate
DSL — pushed to SQL:

```python
with sl.LocalRegistry("classifications.duckdb") as local:
    h = local.get(h_urn)  # LazyHierarchy
    provinces = h.nodes.filter(sl.q.hcode.level.id == "PR").ids()
```

---

## LazyHierarchy: SQL-backed access

`LocalRegistry.get(hierarchy_urn)` returns a `LazyHierarchy` by default
— same SKOS API as in-memory `Hierarchy`, but tree walks compile to a
single SQL query against `sdmx.hierarchy_node`. For large hierarchies
(60k+ nodes) this avoids materialising the full tree in Python.

```python
import sdmxlib as sl

with sl.LocalRegistry("classifications.duckdb") as local:
    h_urn = sl.Hierarchy.urn_for(agency="STATCAN", id="H_GEO_STAT", version="1.0")
    h = local.get(h_urn)  # LazyHierarchy
    assert isinstance(h, sl.LazyHierarchy)

    # Multi-node walks return a lazy `Query`
    h.roots().ids()  # one SQL → list[str]
    h.narrower("CA").collect()  # list[HierarchicalCode]
    h.narrower_transitive("CA", depth=2).ids()

    # Bulk projections — one SQL each, keyed by path as on `Hierarchy`
    h.depths()  # {path: depth}
    h.inherit_along_broader({"CA": "country"})  # SKOS propagation
```

To get a fully hydrated `Hierarchy` (with `.tree` populated and `.children`
wired), call `h.materialize()` or pass `eager=True` to `local.get(...)`.

### Filter DSL with `sl.q.hcode`

A hierarchy's placements are a relation like any other, reached through
`sl.q.hcode`:

```python
with sl.LocalRegistry("classifications.duckdb") as local:
    h = local.get(h_urn)

    # All nodes at depth 2
    h.nodes.filter(sl.q.hcode.depth == 2).ids()

    # Nodes whose code's name contains "Vancouver" — a node has no name of its own
    h.nodes.filter(sl.q.hcode.code.name["en"].str.contains("Vancouver")).collect()

    # Compose with SKOS walks
    h.narrower_transitive("CA").filter(sl.q.hcode.level.id == "PR").ids()

    # Boolean combinators — & | ~
    h.nodes.filter((sl.q.hcode.level.id == "PR") | (sl.q.hcode.level.id == "CD")).ids()
    h.nodes.filter((sl.q.hcode.depth == 1) & ~sl.q.hcode.level.id.is_null()).ids()
```

### Intersecting two subtrees — keep it in the engine

Two hierarchies over the same codelist, and you want the nodes in both
subtrees. The obvious line pulls both operands into Python:

```python
with sl.LocalRegistry("classifications.duckdb") as local:
    by_admin = local.get(h_urn)
    by_pop = local.get(other_urn)

    # Costs what the operands weigh, whatever the answer weighs.
    slow = set(by_admin.narrower_transitive("CA").ids()) & set(by_pop.narrower_transitive("CA").ids())
```

`is_in` takes a **relation**, and a `select()` projection is one — so the same
question compiles to a single statement with a subquery, and no id list
crosses into Python:

```python
with sl.LocalRegistry("classifications.duckdb") as local:
    by_admin = local.get(h_urn)
    by_pop = local.get(other_urn)

    other = by_pop.narrower_transitive("CA").order_by(None).select(sl.q.hcode.id)
    both = by_admin.narrower_transitive("CA").filter(sl.q.hcode.id.is_in(other))

    print(both.count())
    print(sorted(both.ids()))
```

```
2
['ON', 'QC']
```

`count()` brings back one row and `ids()` brings back the answer — neither
fetches an operand.

Measured on a real bundle — 499,137 and 266,965 nodes in, 265,939 out:

| | wall | peak Python heap |
| --- | --- | --- |
| two id lists, intersected in Python | 235.6 ms | **+80.7 MB** |
| one statement, `count()` | 54.7 ms | **+0.0 MB** |

The wall time matters less than the heap. That 80.7 MB is **outside DuckDB's
`memory_limit`** — a container caps the engine and not the interpreter — and
it is paid whether the answer is 265,939 rows or zero.

!!! tip "`order_by(None)` on the operand"

    A relation used for *membership* has no use for an order, and a bare
    `select()` carries the root-key tiebreak into the subquery. `is_in`
    receives compiled SQL and cannot strip it, so decline it at the source.
    It saves ~12% here at no cost.

The same shape answers "descendants at a level in both trees" — add the level
filter to either side. `Query.intersect` answers a different question: it is
a semi-join on the row key, and two hierarchies place the *same code* under
*different node URNs*.

Annotations are a to-many edge, so a reach through them asks *whether*:
`~(hcode.annotations.type == "t")` is "has no `t` annotation", and it is a true
complement because `NOT EXISTS` is total. Asking about the annotation's *title*
is a different question and is existence-scoped —
`hcode.annotations.any(annotation.type == "t", annotation.title.is_null())`
means "has a `t` annotation whose title is missing". See the discovery page for
the full rule — it is the same on every relation.

The columns are `hcode.id`, `hcode.nested_id` (the node's path, its identity
when its id repeats), `hcode.depth` and `hcode.position`; a node's label is its code's,
`hcode.code.name[lang]`, since SDMX gives a hierarchical code no name of its own. The edges
are `hcode.parent`, `hcode.children`, `hcode.ancestors`, `hcode.descendants`,
`hcode.level`, `hcode.code` and `hcode.annotations`. A tree edge is a hop like
any other, so the transitive walks are `ancestors` / `descendants` rather than
their own methods.

Combine any of them with `&`, `|` and `~`:

- **Parenthesise each comparison.** Python binds `&` and `|` more tightly than
  `==`, so `sl.q.hcode.id == "A" | sl.q.hcode.id == "B"` is a `TypeError`, not
  a query. The same hazard exists in polars and pandas.
- **`~` is SQL negation, not set complement.** These compile to
  `NOT (...)`, and under SQL's three-valued logic a row whose column is `NULL`
  satisfies neither `p` nor `~p`. So `filter(p)` and `filter(~p)` together
  return every row only when the column cannot be `NULL` — `hcode.level.id`
  is optional; `hcode.id`, `hcode.depth` and `hcode.code.id` are not (every
  hierarchical code must reference the code it places). Double
  negation still holds: `~~p` is `p`.

---

## Reading from a local file

Hierarchies are included in `StructureMessage` alongside codelists and DSDs:

```python
msg = sl.read("classifications.xml")

for h in msg.hierarchies:
    print(f"{h.id}: {len(h.codes)} total nodes, {len(h.tree)} roots")
```
