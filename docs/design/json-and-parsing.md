# JSON, Parsing, and the Typedness Spectrum

sdmxlib's job is to turn wire formats (SDMX-ML, SDMX-JSON) into a typed
domain graph. But not every byte that arrives should become a domain
object, and not every `dict` should stay a `dict[str, Any]`. Between "raw
`Any`" and "fully-parsed domain object" there is a spectrum, and choosing
the wrong point on it is a real design error in *both* directions.

This page is the rubric for that choice. Read it before deciding how to
type a new reader, a new payload, or a pass-through path. It is the JSON
companion to the typing conventions in `CLAUDE.md` (`object`-by-default,
"typed core, untyped shell") — this is that principle one level up: a
**structured** shell instead of a raw-`Any` one.

## The spectrum

```
bytes  →  Any  →  object  →  JSONRO / JSON  →  TypedDict  →  domain object
        (edge)   (narrow)   (open tree)      (known tree)   (the model)
```

| Point | Use when | Navigation cost |
| --- | --- | --- |
| `Any` | a genuine escape hatch at an I/O boundary | none — and unchecked |
| `object` | heterogeneous, shallow, you'll narrow before use | `isinstance` per step |
| recursive `JSONRO` | the JSON shape is **open / not yours** | `isinstance` per step, but *typed* |
| `TypedDict` (+ `NotRequired`) | the shape is **known** (a spec) | typed key access, **no ceremony** |
| domain object | core model, rich navigation | none — plus methods + invariants |

`JSONRO` is **notional** — a name for this point on the spectrum, not an
alias the library defines. Nothing in `src/` declares it; a caller who wants
it writes it themselves. It would be a PEP 695 recursive alias over the
*read-only* collections, so it stays covariant and accepts a plain
`dict[str, str]`:

```python
type JSONRO = str | int | float | bool | None | Sequence[JSONRO] | Mapping[str, JSONRO]
```

## Rule zero: you can't have a precise *buildable* JSON type

There is a soundness law here: **mutable ⟹ invariant**. A container that is
both writable and covariant is unsound. So:

- `JSONRO` (covariant `Mapping`/`Sequence`) is great for *reading* JSON — but
  you **cannot build it** (`Mapping` has no `__setitem__`).
- `dict[str, JSON]` is buildable but **invariant**: a `dict[str, str]` is *not*
  a `dict[str, JSON]`, so every heterogeneous assignment fights the union and
  the value must be born `JSON`-typed (viral).

This reasoning drove the *original* SDMX-JSON writers, which built a
heterogeneous `dict[str, Any]` incrementally and then dumped it — `Any` there
was the honest gradual-typing escape at a *build* boundary, not laziness.
**Milestone #8 superseded that**: the writers now build typed `msgspec.Struct`
DTOs (a spec'd shape, checked both directions) encoded via the shared
`_dto.ENCODER`. The structure surface has **no `dict[str, Any]` wire islands
left** — even the awkward cases got typed: the codelist-extension /
`memberValues` union (bare-string-or-object + `bool | "excluderoot"` cascade +
the inclusive/exclusive xs:choice) is plain msgspec unions, no `dec_hook` (#186),
and the `{observation:{}}` / `{dataflow:{}}` presence markers became an empty
`EmptyObject` Struct. The only remaining build-boundary dict is the data-plane
per-batch `{key: row}` map that msgspec encodes directly on the hot path.

The **read/parse** side was the last place an untyped decode survived, and it
is now closed too. Every `msgspec.json.decode` in `src/` carries `type=`: the
structure, metadata and data messages each decode their envelope into a Struct
in one pass, so there is no `-> Any` to navigate and no per-section
`msgspec.convert` fan-out (#200).

Two conclusions from that work are worth keeping, because the obvious answer
was wrong both times:

- **A `TypedDict` per section would have been a downgrade.** That was the
  original proposal (#176), made when the seam was `orjson.loads -> Any`. Once
  the sections were Structs, a `TypedDict` would only *annotate* a shape that
  typed decode already *validates* — strictly less checking for the same
  syntax. #176 was closed on that basis rather than implemented.
- **Typing the envelope changes error behaviour, so it needs its own
  verification.** Hand-navigation surfaces a malformed message as whatever the
  first bad subscript raises — in practice `AttributeError: 'list' object has
  no attribute 'get'`, twice, one of them violating a documented
  `Raises: ValueError`. Round-tripping valid fixtures cannot see any of that;
  only a diff of *malformed* inputs can.

`JSONRO` stays reserved for the genuinely-open bits, which is what the rest of
this document is about.

## The core decision: typed tree vs domain object

**Parse all the way to a domain object when:**

- The shape is **yours and stable**, central to the domain (DSD, Codelist,
  Hierarchy, Concept).
- Consumers navigate richly — `.attr.attr`, methods, computed properties,
  cross-references (`Ref` resolution, interning).
- You want **validation + normalisation** at the boundary (defaults, canonical
  forms).
- It *is* the product. For structural metadata this is never in question.

**Stop at a strongly-typed tree (`TypedDict` / `JSONRO`) when:**

- **Pass-through / conduit** — you read, tweak one field, and re-emit. A full
  parse is wasted work *and* a lossy round-trip risk. (A downstream service
  splicing one annotation into a message should not rebuild the whole model.)
- **Open / schemaless** — arbitrary annotation payloads, vendor extensions,
  reference-metadata blobs whose shape *you do not own*. A domain class cannot
  model "arbitrary."
- **Peek, don't model** — you extract one value; a full model isn't justified.
- **Fidelity > modelling** — a domain object silently **drops fields it does
  not know**; a tree preserves everything. If faithful re-emit matters and the
  model is incomplete, the tree is *safer*.

## The two failure modes

- **Over-parsing** — forcing open/vendor payloads into rigid classes: lossy,
  brittle, endless "add-a-field" churn. Issues #159 / #160 (serializer and
  storage round-trip gaps) are exactly this: fields on the wire that the model
  didn't carry, dropped on round-trip.
- **Under-parsing** — leaving core structural data as trees: pushes `["k"]["k"]`
  navigation and per-step narrowing onto every consumer, and throws away the
  library's entire value proposition.

## Where sdmxlib sits today

- **Structural metadata** → domain objects. Settled, correct.
- **The parse boundary itself** → a typed Struct decode (`decode(type=…) →
  construct`). It used to be a transient `loads → navigate → construct` tree
  whose `Any` was defended as short-lived; there is no tree and no `Any` there
  now (#200).
- **The resolution `index`** (`URN → parsed-object`) is **not** JSON — it's a
  heterogeneous domain-object map, typed `dict[str, object]` (issue #169). A
  JSON type does not apply.
- **Reference metadata is the genuinely murky zone.** `ReportedAttribute`
  already models values as `scalar | InternationalString + recursive children`
  — a **domain shell around a flexible payload**, i.e. a deliberate hybrid. The
  open question, decided per payload, is whether *arbitrary* reported values and
  vendor annotation payloads get modelled further or exposed as a typed-tree
  accessor.

## The heuristic

> **Is the shape mine and stable, and will people navigate it richly?
> → domain object. Is it someone else's / open, or am I just a conduit?
> → typed tree.**

Parse the core; keep a typed tree at the open edges.
