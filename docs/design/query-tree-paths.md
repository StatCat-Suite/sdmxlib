# Tree paths: `parent`, `children`, `ancestors`, `descendants`

Four names on every tree relation — `q.code`, `q.concept`, `q.category`,
`q.hcode` and `q.metadata_attribute`. (`q.organisation_unit` has `parent` and
`children` only: its writer bakes no closure, so it has no transitive edge to
expose — §1.) This document says what each is, why four rather than two, why there is no
`parent_id` column beside them, and how the shape compares to the Python APIs
that solved the same problem first.

Everything below was run against the code it describes. Where a claim is about
another library, it was run against that library too.

---

## 1. Four names are two edges

The four are not four relationships. They are **two, read from both ends** —
which is how every edge in this language works:

```
direct                forward='parent'      inverse='children'
                      on (('codelist_urn', 'codelist_urn'), ('parent_id', 'code_id'))

closure (membership)  forward='ancestors'   inverse='descendants'
                      on (('codelist_urn', 'codelist_urn'), ('ancestor_ids', 'code_id'))
```

(That is `q.code`'s pair, from `src/sdmxlib/query/_edges.py`. `q.concept` is
the same shape scoped by `scheme_urn`; the three nested trees join on a path
instead of an id — §6.)

That is the whole structure. `parent`/`children` is the **direct** edge — the
transitive reduction. `ancestors`/`descendants` is its **transitive closure**.
Each edge is declared once and read in both directions, so `children` cannot
drift from `parent`, and `descendants` cannot drift from `ancestors`.

So the mathematical intuition — "these feel similar" — is right, and the answer
is that they are similar in exactly the way `q.dsd.components` and
`q.component.dsd` are similar. Two of the four names are inverses, not new
concepts.

### Why keep the closure if the direct edge implies it

Because the language has no recursion, deliberately. `descendants` is not
`children` applied repeatedly; it is a separate stored fact — the writer bakes
each row's ancestor list, so the closure is a column read rather than a
recursive CTE. See §5.

And they answer differently, which is the practical reason a caller needs both:

```python
q.code.children.id == "ON"      # -> ['CA']         the direct parent of ON
q.code.descendants.id == "ON"   # -> ['CA', 'W']    everything above ON
```

---

## 2. Cardinality is the operational difference

Three of the four are to-many. **`parent` is the only to-one**, and that is not
a naming detail — it decides what you may do with the path.

```python
# parent is to-one -> it has a single value per row, so it projects
reg.query(q.code).select(code=q.code.id, parent=q.code.parent.id)
# [{'code': 'CA', 'parent': 'W'}, {'code': 'ON', 'parent': 'CA'}, {'code': 'W', 'parent': None}]

# ancestors is to-many -> there is no single value, and the DSL says so
reg.query(q.code).select(anc=q.code.ancestors.id)
# PathError: 'ancestors' crosses a to-many edge, so it has no single value per row.
#            Collect it — .list() for the values, .count() for how many, ...

reg.query(q.code).select(code=q.code.id, anc=q.code.ancestors.id.list())
# [{'code': 'CA', 'anc': ['W']}, {'code': 'ON', 'anc': ['W', 'CA']}, {'code': 'W', 'anc': []}]
```

| path | cardinality | compiles to | may be projected? |
| --- | --- | --- | --- |
| `parent` | to-one | `LEFT JOIN` | yes, as a scalar |
| `children` | to-many | correlated `EXISTS` | only via `.list()` / `.count()` |
| `ancestors` | to-many | `? = ANY(ancestor_ids)` for an equality on the key, else correlated `EXISTS` (§5) | only via `.list()` / `.count()` |
| `descendants` | to-many | correlated `EXISTS` | only via `.list()` / `.count()` |

Cardinality is **derived**, never declared — from key constraints, with one
exception. A membership match is to-many in *both* directions however unique
the target key is: `(codelist_urn, code_id)` is unique, so key constraints
alone would call `code -> ancestors` to-one, and be wrong. That is the single
cardinality fact read off the declaration, and
`test_a_membership_edge_is_to_many_in_both_directions` asserts both halves that
keep it honest. Which accessors may declare which names is guarded by
`test_a_tree_accessor_declares_the_relationships_its_bake_supports`:
`parent`/`children` always come together, and `ancestors`/`descendants` come
as a pair and only where the writer bakes the closure.

---

## 3. There is no `parent_id` — the hop is free

`q.code.parent.id` is the only spelling, and it emits no join:

```python
q.code.parent.id == "W"    # SELECT s0.* FROM sdmx.code s0 WHERE s0.parent_id = ?
```

(On the flat trees, `q.code` and `q.concept`, where the parent reference *is*
the parent's id. The nested trees reference a parent by path, so there the
free hop is `parent.nested_id`, and `parent.id` — a column of the parent row —
keeps its join. `test_a_nested_trees_parent_id_is_a_join` is the
discrimination check.)

Two decisions produce that, and they are worth keeping apart because only the
first is about performance.

**The compiler trims the hop.** A foreign key *is* the target's key, so
resolving the row to read the key back is work with no answer in it: the path
`("parent",)` with column `code_id` is rewritten to the empty path with column
`parent_id` at the moment the column is built, before the planner, the SQL, the
cache key or `explain()` ever see it. `_joins.trim` is the rule and
`tests/unit/test_query_trimming.py` is the guard.

**And the duplicate column is not exposed.** Trimming leaves `parent_id` and
`parent.id` identical in SQL, binds and `canonical_key` — so a `parent_id`
accessor would be a second name for one thing, one autocomplete from the other,
with nothing to say about when to prefer it. Django exposes the pair; the
reason does not carry over, because Django's `parent_id` is there to be
*assigned* (`obj.parent_id = 5` saves fetching the related object on write) and
this is a read-only query language.
`test_the_key_has_exactly_one_spelling` in `tests/unit/test_query_trimming.py`
holds the line — no `parent_id` on `q.code`, `q.category` or `q.hcode`, and
no `level_id` on `q.hcode`.

A reference column with **no** edge stays exposed, because then the column is
the only way to ask — `q.artefact.agency` (the stored `agency_id`) names an
agency this schema usually has no row for. See §7.

### This was not always true, and the gap was invisible

Before trimming, the second spelling emitted a `LEFT JOIN` back to `sdmx.code`.
On referentially-intact data both answered identically — which is why the
divergence survived: every row-comparing test passed. What did not agree:

- **A dangling reference.** `parent_id = 'GHOST'` found the row that stores the
  reference; `parent.id = 'GHOST'` found nothing, because no row resolved.
- **`canonical_key()`.** The same question cached under two keys — a
  correctness bug in anything that memoises, and silent.
- **Cost.** A join to compute a column already sitting on the row.

Trimming takes the *stored-reference* answer, and with it the position that
these are not two questions. A dangling reference is corrupt data — the writer
maintains the invariant, and only `reg.sql()` can break it behind its back.

Removing the column then settled what trimming had only made harmless. The two
spellings were identical in behaviour, so the remaining question — "which do I
write?" — had no answer that referred to anything the query *did*. A choice
with no consequence attached is not an affordance.

### It generalises past trees

Nothing in the rule is about ancestry. Any **forward, to-one, non-membership**
hop whose terminal column is the key it was reached by is redundant:

```python
q.code.codelist.urn == u    # -> WHERE s0.codelist_urn = ?      no join
q.component.dsd.urn == u    # -> WHERE s0.dsd_urn = ?           no join
q.code.parent.parent.id     # -> one join, not two
```

Resolution walks the path from the query's root rather than matching the edge
by name, because a forward name is not unique on its own — `codelist` is
declared on `code`, `dsd_component` *and* `concept`, all reaching
`artefact.urn`. A first implementation matched by name, declined when ambiguous,
and so trimmed trees and nothing else: it passed every tree test while doing
nothing for the common case.

### What does not trim, and why

| | | |
| --- | --- | --- |
| `parent.name["en"]` | a non-key column | nothing local to fall back to — this is what the relationship is *for* |
| `children.id` | a reverse hop | the key lives on the far row; stays a correlated `EXISTS` |
| `ancestors.id` | a membership hop | the source column is a *list* of keys; trimming would compare a list to a scalar |

### So which do you write?

`parent.id` — there is nothing else to write. Trimming made the two spellings
compile to one statement, and once they did, keeping both would only have
asked every reader to choose between names with no difference behind them.
The hop *reads* as "the row it points at" and costs what the stored reference
costs.

---

## 4. How this compares

All claims below were run, not recalled.

### Django — creates the pair, then makes it one thing

```python
parent = models.ForeignKey("self", null=True, related_name="children", ...)
```

yields fields `['children', 'id', 'parent']`, and the field's `attname` — the
database column — is `parent_id`. Django *generates* both names from one
declaration, which is the strongest statement of the convention: the pair is
not a redundancy anyone chose, it is what a foreign key is.

And it trims. On Django 6.1, `parent__id` and `parent_id` produce byte-identical
SQL in every form, including the two where a half-done rewrite would show:

```
filter   "t_node"."parent_id" = 1                                     ← both
exclude  NOT ("t_node"."parent_id" = 1 AND "t_node"."parent_id" IS NOT NULL)
isnull   "t_node"."parent_id" IS NULL
```

That is the model we follow. Django has the same adjacency we do — two names one
autocomplete apart, generated from one declaration — and defuses it in the
compiler rather than in the documentation.

Core Django provides no ancestry; `django-mptt` and `django-treebeard` add it.

### SQLAlchemy — the same pair, explicitly

```python
class Node(Base):
    id:        Mapped[int]        = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("node.id"))
    parent:    Mapped["Node|None"] = relationship(remote_side=[id], back_populates="children")
    children:  Mapped[list["Node"]] = relationship(back_populates="parent")
```

`parent_id` is a `ColumnProperty`; `parent` is a `RelationshipProperty`. Both
exist on the mapped class, and they compile differently — exactly as ours do:

```
select(Node.id).where(Node.parent_id == 1)          ->  WHERE node.parent_id = ?
select(Node.id).where(Node.parent.has(Node.id == 1)) ->  WHERE EXISTS (SELECT 1 FROM node AS node_1 ...)
```

SQLAlchemy **does not trim** — the relationship form really does emit an
`EXISTS`. It is not a footgun there because the spellings are not adjacent:
`Node.parent_id == 1` versus `Node.parent.has(...)`. You cannot arrive at the
second by tab-completing the first. That is the other way to make the pair
safe, and it is not available to us, because our surface is Django's.

**SQLAlchemy provides no `ancestors` / `descendants`.** The documented answer is
a recursive CTE, written by hand.

### django-mptt and django-treebeard — the same four names

Both expose `get_ancestors()`, `get_descendants()`, `get_children()` and a
`parent` field. Both store `level` / `depth` as a **field**, and — verified by
inspecting their signatures — **neither takes a depth argument on any
traversal**. Bounding depth is a filter on the field.

### Where we differ, and why

| | SQLAlchemy | Django + mptt | here |
| --- | --- | --- | --- |
| `parent` / `parent_id` pair | yes | yes, generated | **no — one spelling** |
| the pair can disagree | yes — no trimming | no — trimmed | n/a |
| how the pair is kept safe | spellings kept apart | compiler trims | there is no pair |
| why the column exists | the ORM writes through it | the ORM writes through it | read-only, so it need not |
| `children` | yes | yes | yes |
| `ancestors` / `descendants` | no — hand-written CTE | yes, as methods | yes, **as paths** |
| depth bound | n/a | filter the field | filter the field |
| composition | relationship in a query | method returns a QuerySet | path in any predicate |

The one place we are not following a precedent is that ancestry is a **path**
rather than a method. mptt's `node.get_descendants()` is a method on an
instance and returns a QuerySet; ours is `q.code.descendants`, usable anywhere a
path is — inside `.any()`, inside a projection, inside a `group_by` key.

That is possible only because the closure is *stored*. It is not a cleverness
we could apply to an arbitrary graph.

---

## 5. Why none of this recurses

The query language's design (`query-language.md`, on recursion) states the
corollary that the compiler never emits a recursive CTE: traversal is a
stored edge or nothing. Tree paths look
like the counter-example and are not, because the closure is baked at write
time — the "input immutable within an artefact version" case in
`docs/design/caching.md`.

So `q.code.ancestors` compiles to a correlated `EXISTS` over a stored list:

```sql
EXISTS (SELECT 1 FROM sdmx.code s1
        WHERE s1.codelist_urn = s0.codelist_urn
          AND s1.code_id = ANY(s0.ancestor_ids)
          AND s1.code_id = ?)
```

…except in the one case that turns out to be the common one. When the far side
is asked for *nothing but the key it was joined on* — `ancestors.id == X`,
which is what `narrower_transitive` is — that subquery joins a table solely to
assert that a constant appears in a list `s0` already carries. #349 collapses
it to the containment test itself:

```sql
? = ANY(s0.ancestor_ids)
```

On the 2021 Census Profile bundle (`hierarchy_node`, 1.88M rows) that is 6.8x on
`.ids()` for an 8,943-node subtree walk, and 7.5x on the 2,360-node case #348
reported. `ancestors.level.id == "PR"` keeps its join, and so does
`ancestors.id.is_in([...])`; `_member_collapse` in `query/_compile.py` states
the guards and why each one bounds the rewrite.

The collapse is not a second way to be right about ancestry — it is the *same*
assumption as the bake, made once more. It drops the requirement that the
ancestor row exist and the requirement that it share the scope column, and
both come back as one property of the writer, which
`test_ancestor_ids_are_referentially_intact` now asserts over every membership
edge rather than leaving to the second bullet below.

Two consequences worth stating:

- **The bake is load-bearing.** `tests/unit/test_properties_query_code.py`
  compares this against an oracle that walks `parent_id` freshly, so a
  disagreement means the SQL *or* the bake. Exhaustively over one fixture:
  8,420 ancestor ids, zero disagreements.
- **It is only as good as referential integrity.** Forced a dangling ancestor
  id, the stored list and the resolved rows disagree — the same fault line as
  §3. `reg.sql()` can `DELETE`; the writer maintains the invariant.

---

## 6. Every self-reference here is composite

`code.parent_id` references `code.code_id` **within one codelist**. The
reference is `(codelist_urn, parent_id) -> (codelist_urn, code_id)`, and
`concept` is the same shape scoped by `scheme_urn`.

The three *nested* trees — `category` (scoped by `scheme_urn`),
`hierarchy_node` (by `hierarchy_urn`) and `metadata_attribute` (by `msd_urn`)
— are composite on a **path** rather than an id: an id may repeat across
branches (#439), so only `(scope, path)` is unique, and the edge is
`(scope, parent_path) -> (scope, path)`, with `ancestor_paths` for the
closure. The accessor exposes the path as `nested_id`.

A single-column edge would match across artefacts — a *wrong answer*, not a
missing feature. That is why `Edge.column` takes a tuple, and why the fixtures
in `test_query_ancestry.py` carry a second codelist repeating the same ids: a
dropped scope shows up as extra rows rather than as nothing at all.

`Edge.condition` is the one place a join or correlation is spelled. Before
composite support the three emission sites — one `JOIN ... ON` and two
correlated `EXISTS` — each spelled it themselves, which is precisely where a
composite would have drifted apart.

---

## 7. The sweep, and what it found

Once the mechanism existed, the question was whether `ancestor_ids` had been
the only one-off. An inventory of every `*_urn` / `*_id` column on a relation
the edge graph knows about — 29 columns across 13 relations — found two more,
and the inventory is now a test (`test_every_reference_shaped_column_is_an_edge_or_has_a_reason`)
so a future column arrives as a decision rather than as an absence. Absences do
not fail tests, which is why both of these survived until something counted.

**`hierarchy_node.level_id` — fixed.** A bare string column, so a query could
name a level but could not read its label or its index. It is now the `level`
edge, composite on `(hierarchy_urn, level_id)` for the same reason the tree
edges are, and `q.hcode.level.id` trims to the stored `level_id` — no join,
and, for the §3 reason, no `level_id` accessor beside it.

**`concept.parent_id` — was a half-implemented feature; now the fourth tree.**
The worst of the sweep's findings, and now fixed (#307). SDMX-ML 2.1 and 3.0
both allow `<str:Parent>` on a `Concept` — the XSD calls it "the semantic
relationships between concepts which occur within a single concept scheme" —
and this library parsed the element and threw it away. `Concept` carried no
parent field, the writer stored a constant `NULL`, and a scheme whose concepts
nest came back flat on read *and* on round-trip write. The storage column had
been added as though support existed, so the DDL implied a capability the model
never gained.

That is why it survived: **an un-populated column is an absence, and absences
do not fail tests.** Every individual leg looked fine. It took an inventory of
un-wired traversals to notice, not a red test.

`Concept` now carries `parent_id` — flat with a reference, like `Code`, which
is what the wire declares; `Category` nests instead, and the two shapes are not
interchangeable. Both format readers and writers carry it, the 2.1 `<Ref>` and
3.0 text spellings share one reader with `Code.Parent`, and the writer bakes
`depth` / `ancestor_ids` so `q.concept` has the same four names as `q.code`:
`parent`, `children`, `ancestors`, `descendants`.

One trap worth recording: `Parent` sits on `ConceptBaseType` and
`CoreRepresentation` on the extension, so the XSD sequences Parent **first**.
Emitting them the other way round produces a document our own reader accepts
and every validator rejects — the class of writer bug pure round-trip testing
cannot see, and the reason `tests/unit/test_concept_parent.py` validates
against the XSDs with both present.

The accessor had nonetheless acquired `ancestors` / `descendants` / `children`
*before* any of this, copied from `_Code` along with `_Code`'s fan-out type.
Annotations bind nothing, so `q.concept.ancestors.id == "X"` type-checked and
raised `AttributeError`. The guard that should have caught it hand-listed the
three accessors it knew were trees; it now derives them — and the annotations
it flagged as wrong then are correct now, for the first time.

### Still not here

- **A `leaves` edge.** None is needed: an aggregate over a to-many edge is a
  predicate, so "no children" is `q.code.children.count() == 0` in a filter.
  `LazyCodelist.leaves()` and the hierarchy's `leaves()` are that filter by
  name, not a separate mechanism.
- **An `agency` edge from `artefact`** — looks like a reference and is not a usable one, for
  two reasons that compound. `sdmx.agency` is keyed `(scheme_urn, agency_id)`
  and an artefact carries only the id, never which AgencyScheme defines it —
  half a key. And the rows are usually absent: adding a codelist maintained by
  ESTAT creates no agency row, so a join finds nothing.

  The trap is that declaring the edge would *look* fine. `agency.id` trims
  straight back to this column and would answer correctly, while
  `agency.name["en"]` returned NULL almost every time. An affordance that
  works on the one call that needs no join, and silently empties on the ones
  that do, is worse than a plain column. `q.artefact.agency` is therefore a
  text column, and `("artefact", "agency_id")` is listed in `NOT_EDGES`
  (`tests/unit/test_query_edges.py`) with that reason.

The relations this list once named as having no accessor at all
(`metadata_attribute`, `representation_map`, `hierarchy_association`,
`agency`, ...) all have one now — `sl.q` has 48 — and the old per-relation
query classes, `ArtefactQuery` among them, are gone.
