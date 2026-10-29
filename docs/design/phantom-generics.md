# Phantom Type Parameters and the Witness Pattern

`SdmxUrn.parse("urn:…") -> SdmxUrn[object]` can look untyped beside a
pydantic or attrs constructor, which also turns an untyped string into a typed
object.

The output **is** fully typed. This page explains why the
parameter is nonetheless `object` *there*, where the real type comes back, and
the general rule so you recognise the shape next time. It's the
type-**parameter** companion to `json-and-parsing.md` (which is about how
*deep* to parse); this one is about how a generic type's *parameter* is
recovered — or can't be.

Until #399 the answer on this page was `SdmxUrn[Any]`, and the page argued
*against* `object`. #399 reversed that, for reasons recorded in
[`object` over `Any`](#object-over-any-the-399-reversal) below; the rest of the page
describes the design as it stands.

## The output is typed. The `object` is on a phantom parameter.

`parse` returns a fully-typed `SdmxUrn`. The *class* is concrete; no field is
`Any`; nothing untyped floats around. In the sense people mean by "typed
output," parse is **identical** to `User.model_validate(json) -> User`.

The `object` sits only on the **type argument** — and `SdmxUrn`'s `T` is a
**phantom**: it is never stored in a field and never comes from the payload.
It is a *label* threaded through purely to type **downstream consumers**:

```python
urn: SdmxUrn[Codelist] = sl.Codelist.urn_for(agency="SDMX", id="CL_FREQ")
Ref.unresolved(urn)  # -> Ref[Codelist]
rest.get(urn)        # -> Codelist | None
```

The URN carries a "what do I point at" tag that exists only for the
type-checker. Nothing in the runtime object depends on it.

## Plain models and generic models

A plain `User` model has **no type parameter**, so there is nothing to infer:
`model_validate(json) -> User` is trivially complete. The wall appears the
moment a model becomes *generic*:

```python
class Box[T](BaseModel):
    value: T


Box.model_validate(some_json)  # T = ???  pydantic can't infer it either
Box[int].model_validate(some_json)  # you must SUPPLY T
```

Pydantic *requires* you to parameterise a generic model before validating —
it validates *against the `T` you handed it*, it does not discover `T` from
the JSON. That `Box[int]` is exactly a `type[T]` **witness**. So this is not
an sdmxlib quirk; it is a universal property of *any generic type whose
parameter the payload does not determine*.

## The class name in the URN string

The class name is in the string **at runtime**
(`urn:…codelist.Codelist=SDMX:CL_FREQ(1.0)`). But a
function's return type is **static**: one signature, one return type. It
cannot be "`SdmxUrn[Codelist]` when the string says Codelist,
`SdmxUrn[Concept]` when it says Concept." The only mechanisms that let an
output type track an input are:

- a generic bound to an **argument type** — `def f[T](cls: type[T]) -> T`
  (the witness), or
- `@overload` on **literal** argument values.

An arbitrary runtime string is neither. So the governing law is:

> **Runtime-present ≠ statically-recoverable.** The class is in the string,
> but the type system cannot reach it without being *told* the expected type.

## Two construction paths, split by whether `T` is known

We *chose* to make `SdmxUrn` generic. If it were a plain non-generic
`SdmxUrn`, `parse` would return a fully-typed value with **no parameter at all** and
this question would never arise — but we'd lose the `URN → Ref[T] → get[T]`
threading that makes navigation typed. The price of that choice is that the
two ways to build a URN split by whether the type is statically known:

```python
SdmxUrn.make(Codelist, agency="SDMX", id="CL_FREQ", …)  # -> SdmxUrn[Codelist]
SdmxUrn.parse("urn:…Codelist=SDMX:CL_FREQ(1.0)")         # -> SdmxUrn[object] (#399; was SdmxUrn[Any])
Codelist.parse_urn("urn:…Codelist=SDMX:CL_FREQ(1.0)")    # -> SdmxUrn[Codelist] — the class is the witness
```

`make` (and `<Class>.urn_for(...)`) is the **typed constructor** — you
already hand it the class, so `T` is bound. `parse` is the **from-serialized**
path where no witness is present, so `object` is the honest answer *at that
point*: a URN string can name anything. `<Class>.parse_urn` is the same parse
with the witness supplied — the class it is called on.

## The rule: restore the type where the expectation lives

`parse` is genuinely polymorphic — it feeds heterogeneous indexes,
round-trips, logging, comparison, where no single `T` exists — so it states
what it knows, `SdmxUrn[object]`, and **nothing downstream may assume more**.
`get(SdmxUrn.parse(text))` answers `object | None`, and the caller narrows
with `isinstance` or names the class with `expect=`:

```python
local.get(SdmxUrn.parse(text), expect=MetadataSet)  # -> MetadataSet | None
```

Where the expected type *is* known — a parsed URN flowing into a **typed
slot**, because *the slot's type is the expectation* — the caller parses on the
class instead. In the format readers that slot is a domain field —
`Dimension.concept: Ref[Concept]` — so the reader's ref helper takes a
`type[T]` witness and the call site supplies the class the field expects:

```python
# formats/sdmx_ml/_common.py — a method on the reader
def resolve_ref[T](self, container_el, cls: type[T]) -> Ref[T] | None: ...

concept_ref = self.resolve_ref(ci_el, Concept)  # -> Ref[Concept] | None

# formats/sdmx_json/_resolve.py — the witness parses the URN itself
def resolve_urn[T: ParsesUrn](urn_val: object, cls: type[T]) -> Ref[T] | None:
    urn = cls.parse_urn(urn_val)  # SdmxUrn[T]
    ...
```

`cls` is both witness and guard: a URN naming a different class from the slot
is refused (SDMX-ML raises `SdmxConformanceError`; SDMX-JSON leaves the ref
unbound), so a mistyped artefact never lands in a typed field. Genuinely
heterogeneous slots (a metadata set's targets, a categorisation's source can
point at *any* artefact) pass `object` and land as `Ref[object]` — which is
the *correct* type there, not a cast.

**Why a class method, not a `kind=` parameter on `parse`.** `ParsesUrn`
(`model/urn.py`) is a mixin giving every class a reader needs
`parse_urn(text) -> SdmxUrn[Self]`. A `parse(text, kind=Concept)` parameter
would be a type witness the function body never reads — an unused argument to
ruff. On the class, `cls` *is* the witness and is never unused. (Items share no
base class, so each one a reader needs inherits `ParsesUrn` alongside
`MaintainableArtefact`.) `SdmxUrn.make[U](artefact_type, …)` predates this and
keeps its explicit `type[U]` argument, with the `ARG003` suppression that
shape costs.

## `object` over `Any` (the #399 reversal)

This page used to say `parse -> SdmxUrn[Any]` and warn against `object`:
`Ref.unresolved(SdmxUrn[object])` is a `Ref[object]`, not a `Ref[Codelist]`,
so "the friction reappears at every caller" — while `Any` flows into `Ref[T]`
silently. Both halves were true, and the conclusion drawn from them was wrong:

- **"Silently" was the defect.** The same `Any` that flowed quietly into a
  reader's `Ref[Concept]` also flowed into `LocalRegistry.get`'s overloads,
  where `get(SdmxUrn.parse(text))` matched the *first* one: basedpyright
  answered a confident, wrong `LazyCodelist | None`, and pyrefly an unchecked
  `Any | None`. A phantom `Any` is only harmless until something dispatches on
  the phantom. `tests/typing/probe_urn_any.py` pins what is declared now.
- **Only the callers that know the type need to recover it.** It only reappeared "at every caller" if every
  caller had to narrow. The callers that *know* the type are the readers, and
  `<Class>.parse_urn` gives them `SdmxUrn[T]` with no narrowing at all. The
  callers that don't know it should be told so — which is what `object` does.

This is the CLAUDE.md typing rule applied to a phantom: `object` by default,
and when the caller knows the type, **recover it on the class** rather than
through `Any`. `SdmxUrn.parse`, `parse_item` and `artefact_urn` all return
`SdmxUrn[object]`.

## The heuristic

> **A generic type's parameter is only as recoverable as its input makes it.
> Construct-from-a-known-type (`make`, `urn_for`, a class witness) → typed.
> Recover-from-serialized (`parse`, a string) → `object` at the boundary;
> where the expectation is known, parse on the class (`Cls.parse_urn`) so the
> witness supplies `T`.** Never use `Any` on the phantom to make it "flow" —
> it flows into every overload that dispatches on it, too.

`src/` has two sites that break this rule, both in the SDMX-ML reader
(`formats/sdmx_ml/_common.py`). `ref_el_to_urn` returns `SdmxUrn[Any] | None`
because its result feeds `Ref[T].unresolved` in `read_representation`, and a
comment there cites #286 for it. The `_checked` helper inside `resolve_ref`
takes `SdmxUrn[Any]` and hands it to `Ref.unresolved` under the `Ref[T]`
that `resolve_ref`'s `cls` witness names. They are the only `SdmxUrn[Any]`
annotations in `src/`; new code follows the heuristic.
