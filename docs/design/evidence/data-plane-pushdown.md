# Streaming observations over REST: evidence

> Records the choice between two ways for `.stream()` to read an SDMX REST
> data response — materialise into the query store, or parse the wire stream —
> and the 2M-row measurements that decided it. Written in August 2026 as step 1 of the
> registry design's landing plan; the measurements are from !359 (merged 2026-08-18), which made
> the fetch go through a file DuckDB reads. Corrected on 2026-10-28 (GH-267)
> where the outcome called that fetch bounded. The current design is
> [`data-plane-pushdown.md`](../data-plane-pushdown.md), whose §4 summarises
> this; section numbers below are that document's.

## 4. Streaming over REST: two readings

**The question is whether `.stream()` streams when the backend is REST.**

Two readings, both defensible, and they produce different libraries.

### A. Fetch, materialise, then stream from DuckDB

The structural design, applied unchanged: issue the request, write rows into the
query store, run the original query, stream the result out of DuckDB.

*For:* one code path with the local case; every terminal works identically; the
`WHERE` that could not be pushed runs in SQL, so semantics are the compiler's
everywhere.
*Against:* peak memory is the whole slice, not `chunk_rows`. The measured claim
in `Query.stream`'s docstring — 5 MB at 4M rows — is a property of DuckDB
streaming a local table. Over REST the fetch materialises first, so
`stream(chunk_rows=…)` becomes a chunking of an already-resident result, and
the docstring's promise is false for this backend.

### B. Stream the wire response through the parser

`httpx2`'s streaming response into the SDMX-CSV reader, yielding batches as
bytes arrive — which `client.stream_data_at` already does for the file case.

*For:* actually bounded, which is what a caller reaching for `stream()` wants,
and what a downstream SDMX REST service's `StreamingResponse` needs to be more than decorative.
*Against:* only correct when the query is **fully pushed**. A residual `WHERE`
cannot be applied to a stream without an evaluator — which the design does not
have and §1.1 of the [registry design](../registry-and-catalog.md) refuses on
purpose.

### Recommendation: B when fully pushed, A otherwise, and say which

> **Outcome: neither, and the measurement is why.** A is what shipped — the
> fetch always materialises — but through a file DuckDB reads itself rather
> than through a `bytes` object, which roughly halved its peak memory. Measured
> on 2M rows: reading the body into polars was 0.30 s / 660 MB, scanning to
> Arrow IPC and back 0.27 s / 608 MB, and letting DuckDB read the file 0.21 s
> / 307 MB.
>
> *Corrected 2026-10-28:* this note first said the file route "made it
> bounded", and that B's premise — that bounded memory needed a second,
> wire-level path — was false. The file route is cheaper and still loads the
> whole slice: `DuckDBDataStore.ingest_csv` runs `CREATE OR REPLACE TABLE … AS
> SELECT * FROM read_csv(…)` into the query store, which is an in-memory
> DuckDB unless the registry was given a file-backed `store=`. `Query.stream`'s
> docstring states this: a fetched stream is materialised in RAM with the
> default store, and bounded after a full download with a store on disk.
>
> B was not built, so no second SDMX-CSV reader exists. What survives is the
> disclosure half: `explain()` says when a query fetches, and `stream()`'s
> measured figures are scoped to the configurations that hold them.

The two are not alternatives; they are the two halves of one rule already
implicit in the pushdown machinery:

> **A fully-pushed data query streams from the wire. A query with a residual
> materialises first, and then streams from the store.**

That is `Program`'s existing distinction — `Push` with no residual versus
`Push` plus local work — surfacing as a memory property instead of only as a
correctness one. It needs `stream()` to state which it did, because the
difference is 5 MB against the whole slice and a caller cannot see it from the
call site. An `explain()` line is the cheapest honest answer.

*(This rule was not built: every REST data query materialises first.)*

**What this rules out:** silently choosing A while documenting B's memory
figures. `Query.stream`'s docstring currently makes a measured promise that
would be false for a REST-backed stream, and the fix is not to delete the
measurement but to scope it. *(Done: the docstring's figures are scoped by
where the rows are.)*
