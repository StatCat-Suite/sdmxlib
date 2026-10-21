# Competitor benchmark

`just bench-compete` (`python -m bench.compete`) compares sdmxlib with the two
other Python SDMX libraries, [pysdmx](https://github.com/bis-med-it/pysdmx)
and [sdmx1](https://github.com/khaeru/sdmx). It answers four questions over
one corpus held in every format:

1. **Speed.** How long each library takes to read every file, and to write
   every format from its own read of the 2.1 file. Every value is the median
   of `--rounds` interleaved rounds (default 7). Within a round, each library
   runs once in turn, so machine drift lands on all of them.
2. **Agreement.** Whether the libraries read the same content. It compares
   them across libraries on the same file, and within each library across
   formats.
3. **Fidelity.** What each library keeps of what the codes hold: languages,
   parents, annotations and descriptions. Each read is counted against the
   document's own text, not against another library.
4. **Output.** Whether each library's output validates against the vendored
   schema, and whether every library reads it back to what it read from the
   source.

It needs the optional `bench-compete` dependency group, which the recipe
selects. It is a report for humans, never part of `just ci`. The
pytest-benchmark parse and serialise timings for 2.1 XML stay in
`tests/bench/test_compete.py`.

## Corpus

The corpus is BIS's codelists (105 at the time of writing, with 9,716
codes) and data structures (27). The first run fetches it into `cache/`. That
directory is gitignored, because the repository does not redistribute BIS's
data. Later runs read the cache, so they are offline and compare the same
snapshot. `--refresh` (`just bench-compete --refresh`) fetches a new snapshot.

All six documents are fetched together whenever any is missing, so the three
formats always hold one day's content. `cache/fetched` records when, and the
report prints it.

| cached file | request |
| --- | --- |
| `bis-*-2.1.xml` | `https://stats.bis.org/api/v1/{codelist,datastructure}/BIS/all/latest`, SDMX-ML 2.1 |
| `bis-*-3.0.xml` | `https://stats.bis.org/api/v2/structure/{codelist,datastructure}/BIS/*/~`, SDMX-ML 3.0.0 |
| `bis-*-2.0.json` | the same v2 request, `application/vnd.sdmx.structure+json;version=2.0.0` |

BIS was chosen because one registry serves all three formats for the same
artefacts, and all three libraries read at least its 2.1 files. A newer
snapshot can differ from the one the findings below were measured on.

## What is compared, and what is not

The content comparison uses what all three libraries hold:

- **Codelists:** code ids and English names, plus code parents. pysdmx has
  no code parent, so parents are compared only between sdmxlib and sdmx1.
- **DSDs:** each component's role, id, concept id and enumeration.

Annotations, descriptions, other languages, facets and links are not
compared there. The fidelity section counts them instead.

**Fidelity** is counted twice for every codelist document:

- once from the document's text (`source_fidelity`, lxml or msgspec, with no
  library's model in between);
- once from each library's read (`fidelity`).

Each read's losses are printed. The count is independent of every library on
purpose: compared with sdmxlib's read, a loss would look like a disagreement
between two libraries. `tests/unit/test_compete_report.py` checks that the two
counts agree on sdmxlib's own output, and that a smaller read is reported item
by item.

BIS is English-only, so it cannot show what happens to a second language. A
small **probe codelist** (`probe()`) can: two languages, parents, an annotation
and a description, and one code (`B`) named in French only. Each library reads
it in every format, writes it back with its own writer, and sdmxlib reads the
result, so a language that is lost, or relabelled, shows on one line.

pysdmx validates its input against the schema by default, and sdmxlib does
not. So `pysdmx` is timed with validation off, and `pysdmx-validating` is its
default. Writers run unvalidated, so the timing measures the writer alone.

sdmxlib's default `serialize` also validates, and refuses what the target
format cannot carry. When it would refuse a write, the write table marks the
timing `(refused by default)`, and the Output section says `refused by
default: <reason>` instead of checking the file against the schema (#500).
The file is the unchecked writer's, not output sdmxlib ships. `INVALID` is
kept for output the default *would* write that still fails the schema, which
is a real writer bug.

## Scale: single large codelists

`just bench-compete-scale` (`python -m bench.compete.scale`) asks how sdmxlib
and pysdmx scale with codelist size. That's the question for census
geographies, where one codelist holds hundreds of thousands of codes and only
the per-code cost matters. It reports each read and write in milliseconds and
per thousand codes.

| document | codes | formats |
| --- | --- | --- |
| UNSD `CL_COMMODITY`, from the SDMX global registry | 68,191 | 2.1, 3.0, JSON |
| ECB `CL_PROVIDER_FM_ID` | 103,712 | 2.1 (ECB serves no JSON) |
| synthetic, two languages and a four-level hierarchy | 1,000 to 500,000 (`--sizes`) | 2.1, 3.0, JSON, as sdmxlib writes them |
| a 2021 Census Profile bundle's `CL_GEOGRAPHY`, when a bundle is present | 579,951 | 2.1, JSON (sdmxlib refuses 3.0, #432) |

The real codelists are fetched on first run into `cache/scale/`. The SDMX
registry takes about two minutes per format. Whatever sdmxlib writes, the
synthetic codelists and `CL_GEOGRAPHY`, is written afresh on every run. A
cached copy would measure whichever writer made it. The bundle is opened
read-only.
A format sdmxlib refuses to write is left out, with the reason printed.

Two things to keep in mind when reading the numbers:

- **pysdmx often does less.** It holds one name per code and no code parent.
  Every read in this tier prints what it dropped of the document's own count,
  so the like-for-like rows are marked rather than assumed. UNSD and ECB are
  English-only, with no parents and no annotations: there, pysdmx `keeps
  everything counted`, and the timings compare equal work. On the synthetic
  codelists and `CL_GEOGRAPHY` it drops the French and the hierarchy, so it
  is timed doing less, on both read and write.
- **sdmx1 is not in this tier.** Its 2.1 writer is quadratic: at 500,000 codes it
  took over four minutes a round, most of the run's length. The figures below
  are from the one run that included it, and `just bench-compete` still
  compares all three libraries on the BIS corpus.

### What the scale tier found on 2026-10-19

This is `main` after #492 (cb0a40e), on Python 3.14, against pysdmx 1.21.0. The figures are milliseconds per thousand codes, medians of three rounds; the 2026-10-15 column is sdmxlib on the same documents before milestone 22's last fixes, #492 and the move to 3.14. Both runs were at reduced priority, so read differences under about 10% as noise.

| | sdmxlib | pysdmx | sdmxlib, 2026-10-15 |
| --- | --- | --- | --- |
| read 2.1, UNSD / ECB (equal content) | **7.5 / 7.3** | 8.4 / 7.7 | 7.0 / 7.2 |
| read JSON, UNSD (equal content) | 3.4 | **1.2** | 5.2 |
| write 2.1, UNSD / ECB (equal content) | 2.2 / 2.2 | 2.6 / 2.0 | 2.2 / 2.2 |
| write JSON, UNSD (equal content) | 0.37 | 0.39 | — |
| read 2.1, synthetic 1k → 500k | **9.5 → 10.6** | 13.7 → 23.6 (does less) | 9.1 → 9.5 |
| read JSON, synthetic 1k → 500k | 2.1 → 3.9 | 0.6 → 0.8 (does less) | 2.2 → 3.3 |
| write 2.1, synthetic 1k → 500k | 4.3 → 4.1 | 1.4 → 1.3 (does less) | 4.4 → 4.1 |

- **On equal content, SDMX-ML is sdmxlib's and SDMX-JSON is pysdmx's.** UNSD and ECB hold one language, no parents and no annotations, so pysdmx keeps everything counted and does the same work. There, sdmxlib reads 2.1 faster and writes level with pysdmx. pysdmx reads UNSD's JSON 2.8× faster.

  That JSON gap is not what sdmxlib holds beyond pysdmx, because on UNSD there is nothing extra. It is sdmxlib's per-code construction and checks. #493 measured the cyclic GC at 35–58% of a large read, and %24 tracks that.
- **Per-code cost stays roughly flat** from 1,000 to 500,000 codes in every sdmxlib read and write. pysdmx's 2.1 read cost grows 1.7× over the same range.
- **Since 2026-10-15:**
  - **faster:** JSON reads of real documents (UNSD 5.2 → 3.4) and JSON writes, from milestone 22;
  - **slower:** large synthetic reads, 2.1 by 5–11% and JSON at 500k by 18%.
  
  Two causes are likely, neither measured apart here: Python 3.14's larger GC share (#493), and #492's construction-time checks, estimated at about 3%.
- **`CL_GEOGRAPHY`** (579,951 codes, the bundle rebuilt for #492):
  - sdmxlib now reads its own 2.1 file: 10.7 s, against pysdmx's 27.3 s. On 2026-10-15 it refused it, because of blank names (#452).
  - JSON: 4.9 s against 2.2 s.
  - 3.0 is still refused, as designed: 564,684 codes have digit-leading parents that 3.0.0 cannot name (#432).
  - pysdmx drops the French names and the whole hierarchy here, so its times are for less work.

## What it found on 2026-10-19

On the 2026-10-15 snapshot, with sdmxlib `main` (cb0a40e), pysdmx 1.21.0 and sdmx1 2.28.0.

- **Speed:**
  - sdmxlib reads SDMX-ML fastest: codelists 92 ms, against pysdmx 119 and sdmx1 173. It writes codelists level with pysdmx. Milestone 20's read regression (30–130% on 2026-10-15) is gone.
  - pysdmx reads SDMX-JSON about twice as fast, and writes SDMX-ML DSDs 1.5–2.3× faster. JSON DSD writes are level.
- **Agreement:** the three libraries agree on everything all of them hold, in every file each can read.
- **Fidelity: pysdmx drops content without saying so.** Neither a warning nor `validate=True` changes it.
  - BIS: it drops all 2,370 code parents in every format, and all 2,104 annotations when reading SDMX-JSON. It keeps them from SDMX-ML. sdmxlib and sdmx1 keep everything counted.
  - Probe codelist: pysdmx keeps one name per code and drops the second language.
  - **It relabels.** After a pysdmx SDMX-ML write, the French-only name reads back as `{'en': 'Seulement en français'}`, because its writer stamps `xml:lang="en"` on every name. After its JSON write the language is lost entirely (`_default`): it writes `name` without the `names` map.
- **Output:**
  - Every sdmxlib output the default writes is schema-valid, and every library that reads that format reads it back identically. sdmx1 reads no JSON and no 3.0 DSDs.
  - Its SDMX-ML 3.0 codelist line says `refused by default`: BIS's `_T` (SDMX's total) as a parent, which 3.0.0 cannot name (#432). Until #500 the report called that file `INVALID`.
  - pysdmx's DSD JSON fails the schema, and none of the three libraries can read it.
  - sdmx1's 2.1 DSD output fails the XSD (`TextFormat` placement). It reads no 3.0 DSDs and no structure JSON, and writes only 2.1.
- **BIS's JSON:** it still spells the time dimension `timeDimensions`, so sdmxlib's and pysdmx's JSON reads of every DSD lack it. sdmxlib reports this among its read losses.
