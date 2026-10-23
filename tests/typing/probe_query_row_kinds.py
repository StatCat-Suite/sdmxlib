"""Static probe: the row-kind phantom keeps roots apart.

Not a pytest module — it asserts nothing at runtime. It exists to be **type
checked**, and is wired into `just typecheck` so the property is enforced by
the gate rather than by review.

The property under test is the only reason the phantom exists. Without it,
`reg.query(q.component).filter(q.codelist.id == "X")` type-checks and then
emits a `WHERE` against a table absent from the `FROM` — a runtime failure at
best and a wrong answer at worst.

Two rules are checked, and the second is the one a first implementation gets
backwards:

1. A predicate carries the row kind of the query's **root**.
2. `edge()` threads the **root's** row kind, not the target's. Typed the other
   way, `component.dsd.agency` comes out as `Str[ArtefactRow]` and a component
   query rejects its own path.
"""

from collections.abc import Sequence
from typing import Never, assert_type

import duckdb

from sdmxlib.model.codelist import Code
from sdmxlib.model.collections import Maintainable
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.query._expr import Agg, Label, Num, Pred, Str, Win, WinPred
from sdmxlib.query.accessors import CodelistRow, CodeRow, ComponentRow, DataflowRow, DsdRow, HCodeRow, HierarchyRow, q
from sdmxlib.query.query import Grouped, Query, Select, build, union
from sdmxlib.storage.lazy import LazyCodelist, LazyHierarchy

# ── a root column carries its own root ────────────────────────────────────
assert_type(q.component.id, Str[ComponentRow])
assert_type(q.component.position, Num[ComponentRow])
assert_type(q.dataflow.agency, Str[DataflowRow])
assert_type(q.code.id, Str[CodeRow])

# ── a path carries the ROOT's row kind, not the target's ──────────────────
assert_type(q.component.dsd.agency, Str[ComponentRow])
assert_type(q.component.concept.id, Str[ComponentRow])
assert_type(q.component.concept.codelist.id, Str[ComponentRow])
assert_type(q.dataflow.dsd.id, Str[DataflowRow])
assert_type(q.code.codelist.agency, Str[CodeRow])

# ── predicates too ────────────────────────────────────────────────────────
assert_type(q.component.dsd.agency == "ESTAT", Pred[ComponentRow])
assert_type(q.component.position > 1, Pred[ComponentRow])
assert_type(q.dataflow.agency == "ESTAT", Pred[DataflowRow])


def _takes_component(_: Pred[ComponentRow]) -> None: ...


def _takes_dataflow(_: Pred[DataflowRow]) -> None: ...


# these are the calls that must keep working
_takes_component(q.component.dsd.agency == "ESTAT")
_takes_component(q.component.concept.codelist.id == "CL_AREA")
_takes_dataflow(q.dataflow.dsd.id == "CPI")

# and these must not — each is a cross-root predicate the checker rejects.
# Kept as commented cases rather than suppressions, because a suppression here
# would silently start passing if the property broke.
#
#   _takes_component(q.dataflow.agency == "ESTAT")   # Pred[DataflowRow]
#   _takes_dataflow(q.code.id == "CA")               # Pred[CodeRow]
#   _takes_component(q.codelist.agency == "ESTAT")   # Pred[CodelistRow]
#
# `tests/unit/test_query_accessors.py` covers the runtime half.


# ── fan-out ───────────────────────────────────────────────────────────────
#
# Dotting through a fan-out keeps the PARENT's row kind, so the predicate is
# still about a DSD row.
assert_type(q.dsd.components.concept.id, Str[DsdRow])
assert_type(q.dsd.components.id == "FREQ", Pred[DsdRow])
assert_type(q.codelist.codes.id, Str[CodelistRow])

# `.any()` takes CHILD-rooted predicates and yields a PARENT-rooted one. That
# asymmetry makes the grouping expressible.
assert_type(q.dsd.components.any(q.component.id == "FREQ"), Pred[DsdRow])
assert_type(
    q.dsd.components.any(
        q.component.concept.id == "REF_AREA",
        q.component.codelist.id == "CL_GEO",
    ),
    Pred[DsdRow],
)
assert_type(q.dsd.components.every(q.component.codelist.id == "CL_GEO"), Pred[DsdRow])


def _takes_dsd(_: Pred[DsdRow]) -> None: ...


_takes_dsd(q.dsd.components.concept.id == "REF_AREA")
_takes_dsd(q.dsd.components.any(q.component.id == "FREQ"))

# and these must not type-check:
#
#   q.dsd.components.any(q.dsd.id == "CPI")        # Pred[DsdRow] into a child slot
#   _takes_dsd(q.component.id == "FREQ")           # child predicate at the top level


# ── facets are fan-outs, and take the same binder ─────────────────────────
assert_type(q.dsd.dimensions.concept.id, Str[DsdRow])
assert_type(q.dsd.dimensions.any(q.component.concept.id == "REF_AREA"), Pred[DsdRow])
assert_type(q.dsd.attributes.id == "OBS_STATUS", Pred[DsdRow])
_takes_dsd(q.dsd.dimensions.every(q.component.codelist.id == "CL_GEO"))


# Placeholders with the types `build` declares. The probe is only checked, never
# run, so neither is ever queried — they exist so the calls below are well-typed
# and any error reported on them is about the row kind.
_CONN = duckdb.connect(":memory:")


def _resolve(keys: Sequence[tuple[object, ...]], *, annotations: bool = True) -> Sequence[object]:
    del annotations
    return keys


# ── the Query object keeps the row kind ───────────────────────────────────
_query: Query[DsdRow, DataStructure] = build(q.dsd, _CONN, _resolve)
assert_type(_query.filter(q.dsd.agency == "ESTAT"), Query[DsdRow, DataStructure])
assert_type(_query[0:10], Query[DsdRow, DataStructure])
assert_type(_query.limit(5).offset(1), Query[DsdRow, DataStructure])

# a predicate from another root must not reach it
#   _query.filter(q.component.id == "FREQ")     # Pred[ComponentRow]


# ── set operations are typed on the row kind ──────────────────────────────
_codes: Query[CodeRow, Code] = build(q.code, _CONN, _resolve)
assert_type(_query.intersect(_query), Query[DsdRow, DataStructure])
assert_type(_query.difference(_query), Query[DsdRow, DataStructure])
assert_type(union(_query, _query), Query[DsdRow, DataStructure])

# and these must not type-check — the runtime refusal in `_require_same_root`
# is the backstop for `Query[ArtefactRow]` pairs the checker cannot separate:
#
#   _query.intersect(_codes)      # Query[CodeRow] into a Query[DsdRow] slot
#   union(_query, _codes)         # same, at the free function

# ── unique lives on the projection, and takes nothing ─────────────────────
assert_type(_query.select(q.dsd.agency).unique(), Select[DsdRow, DataStructure])

# and this must not type-check:
#
#   _query.select(q.dsd.agency).unique(q.dsd.agency)   # Expected 0 positional
#   _query.unique()                                    # not on Query


# ── windows: projectable, but not filterable ──────────────────────────────
_rank = q.dsd.version.rank(descending=True).over(q.dsd.agency, q.dsd.id)
assert_type(_rank, Win[DsdRow])
assert_type(_rank == 1, WinPred[DsdRow])
assert_type(_rank.le(3), WinPred[DsdRow])
assert_type(_query.qualify(_rank == 1), Query[DsdRow, DataStructure])
# a `Win` is a `Col`, so only the filter position is restricted
assert_type(_query.select(n=_rank), Select[DsdRow, DataStructure])
assert_type(q.dsd.id.count().over(q.dsd.agency), Win[DsdRow])

# and these must not type-check, which is what the split is for:
#
#   _query.filter(_rank == 1)              # WinPred into filter -> use qualify
#   _query.qualify(q.dsd.id == "CPI")      # Pred into qualify   -> use filter
#   _query.qualify(q.codelist.version.rank().over(q.codelist.agency) == 1)
#   q.dsd.version.rank().path              # a WinFn has no column until .over()

# ── aggregates carry the row kind too (they did not, before #306's §4) ────
assert_type(q.dsd.dimensions.id.list(), Agg[DsdRow])
assert_type(q.dsd.dimensions.count(), Agg[DsdRow])
#
#   _query.select(x=q.codelist.codes.id.list())   # Agg[CodelistRow]


# ── computed columns: still columns, and the two types do not mix ─────────
assert_type(q.code.position + 1, Num[CodeRow])
assert_type(1 + q.code.position, Num[CodeRow])
assert_type(-q.code.position, Num[CodeRow])
assert_type(q.code.id + ":", Str[CodeRow])
assert_type("x:" + q.code.id, Str[CodeRow])
assert_type(q.code.id.len(), Num[CodeRow])
# a computed column composes, so arithmetic on one is arithmetic
assert_type((q.code.position + 1) * 2, Num[CodeRow])
assert_type((q.code.id + "-").len() + 1, Num[CodeRow])
# ...and keeps the surface of the type it produced
assert_type(q.code.id.len() > 2, Pred[CodeRow])
assert_type((q.code.id + "-").str.contains("x"), Pred[CodeRow])

# and these must not type-check — the reason Num and Str are separate types:
#
#   q.code.position + "x"          # Num + str
#   q.code.id + 1                  # Str + int
#   q.code.position + q.code.id    # Num + Str
#   q.code.position.len()          # len() is text-only
#   len(q.code.id)                 # __len__ must return a real int, so absent


# ── group_by is a stage, and keeps the row kind ───────────────────────────
assert_type(_query.group_by(q.dsd.agency), Grouped[DsdRow, DataStructure])
assert_type(_query.group_by(q.dsd.agency).len(), Select[DsdRow, DataStructure])
assert_type(_query.group_by(q.dsd.agency).agg(n=q.dsd.id.count()), Select[DsdRow, DataStructure])

# and these must not type-check:
#
#   _query.group_by(q.codelist.agency)                   # Col[CodelistRow]
#   _query.group_by(q.dsd.agency).agg(n=q.codelist.id.count())   # Agg[CodelistRow]
#   _query.group_by(q.dsd.agency).agg(q.dsd.id.count())  # positional; aggregates are named
#   _query.group_by(q.dsd.agency).filter(...)            # Grouped is not a query


# ── a placement is not a code, and the row kind is what says so ───────────
assert_type(q.hcode.id, Str[HCodeRow])
assert_type(q.hcode.code.id, Str[HCodeRow])
assert_type(q.hcode.code.codelist.agency, Str[HCodeRow])
assert_type(q.hierarchy.hcodes.level.id, Str[HierarchyRow])
# The hop keeps the *root's* row kind while landing on another relation, which
# is the whole reason `R` is the query's root rather than the column's table.
assert_type(q.hierarchy.hcodes.level.name["en"], Label[HierarchyRow])
assert_type(q.hierarchy.hcodes.level.index, Num[HierarchyRow])
assert_type(q.hierarchy.hcodes.any(q.hcode.level.id == "PROVINCE"), Pred[HierarchyRow])

# and these must not type-check — both relations carry `id`, `parent_id` and
# `depth`, and they mean different things:
#
#   _codes.filter(q.hcode.id == "ONT")          # Pred[HCodeRow] into a code query
#   q.hierarchy.hcodes.any(q.code.id == "ON")   # code predicate in a node binder


# ── the artefact phantom: what a root collects, per #396 ──────────────────
# The row kind keeps roots apart; `A` says what each one *builds*. Both are
# phantoms, and this is where the second one is pinned down statically — the
# runtime half is `tests/unit/test_query_collect_types.py`.
assert_type(_query.collect(), list[DataStructure])
assert_type(_codes.collect(), list[Code])
assert_type(_codes.first(), Code | None)
assert_type(_codes.one(), Code)
assert_type(_codes.filter(q.code.id == "A").limit(2).collect(), list[Code])
assert_type(_codes.page(5).collect(), list[Code])

# The lazy roots say what a LocalRegistry actually hands back, not the nominal
# domain type — the same declared-vs-returned rule #370 settled for `get`.
_lazy_codelists = build(q.codelist, _CONN, _resolve)
assert_type(_lazy_codelists.collect(), list[LazyCodelist])
_lazy_hierarchies = build(q.hierarchy, _CONN, _resolve)
assert_type(_lazy_hierarchies.collect(), list[LazyHierarchy])

# `q.artefact` is heterogeneous, so it collects the structural protocol that
# every one of them satisfies — including the two lazy classes, which subclass
# no artefact at all.
_any_artefact = build(q.artefact, _CONN, _resolve)
assert_type(_any_artefact.collect(), list[Maintainable])

# A root with no resolver collects `Never`. That does not make the *call* an
# error — `list[Never]` is still a list — so the runtime `TypeError` remains
# the refusal a caller actually meets. What it does is poison any use of the
# result, which is why the two must agree and are checked together.
_components = build(q.component, _CONN, _resolve)
assert_type(_components.collect(), list[Never])
