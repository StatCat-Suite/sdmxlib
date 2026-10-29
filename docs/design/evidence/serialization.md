# Serialization: evidence

> Records the measurements behind the SDMX-JSON layer's move from
> hand-built dicts + orjson to typed msgspec Structs (%8, opened 2026-07-22;
> the data-plane writers in #183, closed 2026-08-06), the cost of coupling
> validation to `serialize()` (#205, 2026-08-05), and the profile against
> pysdmx that followed. Figures are as measured then. The current design is
> [JSON and parsing](../json-and-parsing.md) and the serialization summary
> in [Performance](../performance.md#serialization).

## The move to msgspec Structs

%8 moved the SDMX-JSON layer to typed `msgspec.Struct` DTOs used both
directions — `to_*` converters (domain → DTO) encoded via a shared
`msgspec.json.Encoder`, and typed `msgspec.convert` / `msgspec.json.decode`
on read. This replaced the hand-built `dict[str, Any]` + orjson design, and
orjson was removed as a dependency.

**Validation coupled to serialize (#205).** `serialize()` took
`validate=False` by default, on the reasoning that validation cost the
majority of JSON serialize time. #205 found five distinct ways to build a
message the SDMX schema rejects, and measured the cost at 4–9% of
serialisation. `serialize(validate=True)` became the default.

**The switch to msgspec.** The first decision settled on dict + orjson and
explicitly deferred msgspec. It was revisited because the
motivation was never purely raw speed:

- **Typing.** `dict[str, Any]` is a viral `Any` hole through the whole
  format layer; typed Structs give the checker a real shape both directions
  and localise the untyped boundary to one `msgspec.json.decode` call.
- **Design fit.** A Struct-shaped wire layer separate from the domain model
  is the same split `pysdmx` uses (`from_model`), and it made the read side
  a clean inverse of the write side.
- **Footprint.** msgspec is a small, single-purpose compiled C extension — and it
  *replaced* orjson rather than adding to it, so the dependency count didn't
  grow.

The "1000 codes" measurement that informed the first decision also
quantifies the alternatives rejected:

| approach | time | vs the old dict path |
|---|---|---|
| hand-built dict + orjson (old) | 127 µs | — |
| cattrs unstructure + orjson | 272 µs | 2.1× *slower* |
| dataclass DTO + orjson-native | 292 µs | 2.3× *slower* |
| **msgspec Struct + encode (chosen)** | **91 µs** | **1.4× faster** |

So the chosen path (msgspec Structs) is also the *fastest* of the four — but
the case for it was typing + design cleanliness, not the ~30% write win.
cattrs and orjson's native-dataclass path remain measurably *slower* than a
hand-written emit and were correctly rejected. Data-plane byte streaming was
migrated separately (#183) and benchmarked flat-to-faster on the large case.

## JSON serialisation after the migration, against pysdmx

A profile of the migrated writer settled a question the table above left open.
Both libraries have a domain-model → wire-Struct conversion (`to_*` for us,
`from_model` for pysdmx) — the encode itself is only ~6% of serialize time in
each; the conversion is ~90%+. The residual gap to pysdmx came from two
things:

1. **They are lossy, we are lossless.** pysdmx's model stores one best-match
   `name: str`; its JSON emits only `name`. We model a full
   `InternationalString` and emit `name` + the `names` lang-map (and
   `text`/`texts` on annotations). Their `from_model` is `name=code.name` (a
   pointer copy); our `to_*` must split every istring into `(best, full-map)`.
   That is real work they skip by *discarding languages* — a price we choose to
   pay for a lossless round-trip.

2. **We had two msgspec knobs still on the table**, both fixed in the perf pass
   that followed:
   - `InternationalString.emit_pair()` — collapse the istring split
     (`__bool__` + `as_dict` + `iter`/`next`) into one method with direct
     backing-map access (~6%).
   - **`gc=False` on the wire-Struct base (`WireStruct`)** — the big one
     (~40% of build+encode). Wire DTOs are transient acyclic trees, so the
     cyclic collector scanning millions of them is pure waste; refcounting
     still frees them promptly. pysdmx doesn't use this.

Net on the 3.8M-obs ESTAT HICP fixture (SDMX-JSON 2.1 serialize, median):
**sdmxlib ~4.0 ms vs pysdmx ~4.6 ms — ~16% faster, while emitting every
language pysdmx drops.** A `gc.is_tracked` guard test (`test_dto.py`) locks the
`gc=False` invariant so a stray `msgspec.Struct` base can't silently re-track a
DTO and undo it.
