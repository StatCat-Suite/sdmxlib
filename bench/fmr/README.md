# FMR as an external oracle

Several constructs were only ever tested against ourselves: our writer, our
reader, the vendored XSD. That shows self-consistency, not interoperability —
if we and the XSD share a misreading, nothing can see it (#457). The Fusion
Metadata Registry is the reference SDMX 3.x implementation, so this sends it
our maximal fixture and version shapes, reads back what it serves in every
format, and classifies every difference.

```bash
just fmr-record          # needs a local FMR; writes tests/integration/fmr/<version>/
just test-integration    # replays the recordings offline; part of `just ci`
```

## A local FMR

Never a public registry: recording writes test artefacts and replaces the
`SDMX:AGENCIES` scheme. FMR is published as a container image; on macOS,
Colima is enough to run it:

```bash
brew install colima docker
colima start --cpu 4 --memory 6 --vm-type vz
docker run -d --name fmr -p 127.0.0.1:8080:8080 sdmxio/fmr-mysql:12.4.2
```

The image is pinned so the recordings say which FMR made them; the recorder
reads the version from FMR itself (`/ws/fusion/info/product`) and writes under
it. The admin account is `root` / `password` (`--url`, `FMR_USER`,
`FMR_PASSWORD` override). FMR is free to use under the BIS SDMX Community
Tools Public License; its responses to our own artefacts are what is recorded,
and nothing here suggests its makers endorse sdmxlib.

## What a recording holds

Per document (`2.1-maximal`, `2.1-shape`, `3.0-maximal`, `3.0-shape`):

| file | what |
|---|---|
| `sent.xml` | the document submitted: our writer's output with the ledger's adjustments applied |
| `accepted.json` | the URNs FMR's submission report accepted |
| `necessity.json` | FMR's answer to the document with each adjustment left out in turn |
| `back/<agency>-<format>` | what FMR then served, in SDMX-ML 2.1 and 3.0, SDMX-JSON 1.0 and 2.0 |

And once: `setup.xml` (the agencies and role concepts FMR needs first) and
`fmr.json` (its version, and what it makes of SDMX-ML 3.1 — nothing yet).

## The two ledgers

- **`ledger.py`** — what is removed before FMR will take a document. FMR
  refuses some of what the maximal fixture carries, since that fixture sets
  every field rather than meaningful combinations. Each adjustment is classed:
  `fmr-deviation` (the XSD declares it, FMR refuses it), `fmr-bug`, `fmr-gap`
  (no reader at all), `fmr-rule` (a rule in neither the XSD nor the IM), or
  `registry-rule` (the IM's rule, which FMR enforces as a registry). Where FMR
  refused something of ours, the fixture was fixed instead.
- **`tests/support/fmr_differences.py`** — every difference between what was
  sent and what FMR served, read through our reader, beyond what
  `formats.gaps` already declares. Each is `fmr-drops`, `fmr-changes`,
  `fmr-normalises`, `fmr-converts`, `fmr-invalid`, `reader-limit`, `ours` or
  `equivalent`.

Both are checked both ways by the replay test: every adjustment is needed and
every difference is classified, by exactly one rule, and every rule is used.

**Do not trust a 200.** FMR 12.4.2 reported success on a submission it had
silently truncated — it read seven of twenty-eight artefacts, crashed on a
value item with no name, and persisted what it had. The recorder records the
accepted URNs, and the replay test compares counts rather than statuses.
