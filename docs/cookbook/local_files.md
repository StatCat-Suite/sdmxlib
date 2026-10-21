# Working with Local Files

Recipes for reading SDMX structural metadata from local files — cached
downloads, vendor-supplied DSDs, or files produced by your own tooling.

---

## Reading a file into a StructureMessage

`sl.read` accepts a file path (or raw bytes) and returns a
`StructureMessage` — or a `MetadataMessage`, when the file carries populated
reference metadata rather than definitions. Format and message type are
auto-detected — SDMX-ML 2.1, 3.0 and 3.1, and SDMX-JSON, are all supported.

```python
import sdmxlib as sl

msg = sl.read("path/to/datastructure.xml")

print(f"DSDs:      {len(msg.data_structures)}")
print(f"Codelists: {len(msg.codelists)}")
```

```
DSDs:      1
Codelists: 11
```

Use a `Path` object or a string — both work:

```python
from pathlib import Path

msg = sl.read(Path("path/to/datastructure.xml"))
```

---

## Navigating a file with a registry

For more than a quick inspection you usually want to navigate artefacts the
same way you would when fetching from a REST endpoint. Pass `registry=` and
`read` adds what it parsed: the registry stores a copy of each artefact with
its references bound to it, so a `Ref` finds its target there when you call it.

```python
import sdmxlib as sl

reg = sl.InMemoryRegistry()
sl.read("datastructure.xml", registry=reg)

urn = sl.DataStructure.urn_for(agency="ESTAT", id="NAMAIN_10_GDP", version="1.0")
dsd = reg.get(urn)

if dsd is not None:
    for dim in dsd.dimensions:
        if dim.is_enumerated:
            cl = dim.representation()
            print(f"  {dim.id:<20}  {cl.id}")
```

```
  FREQ                  CL_FREQ
  REF_AREA              CL_GEO
  ...
```

---

## Loading multiple files — cross-file Ref resolution

An SDMX file may reference codelists that live in a separate file. Load both
files into the same registry and its references resolve cross-file,
regardless of load order: each finds its target in the registry when used.

```python
import sdmxlib as sl

reg = sl.InMemoryRegistry()
sl.read("codelists.xml", registry=reg)  # load codelists first
sl.read("datastructure.xml", registry=reg)  # the DSD's Refs are bound to reg

urn = sl.DataStructure.urn_for(agency="ESTAT", id="CPI", version="1.0")
dsd = reg.get(urn)

if dsd is not None:
    for dim in dsd.dimensions:
        if dim.is_enumerated:
            cl = dim.representation()  # found in reg on access
            print(f"  {dim.id:<20}  {cl.id}  ({len(cl.codes)} codes)")
```

Load order does not matter — loading the DSD first and the codelists second
produces the same result:

```python
reg = sl.InMemoryRegistry()
sl.read("datastructure.xml", registry=reg)  # Refs bound, targets not there yet
sl.read("codelists.xml", registry=reg)  # now each Ref finds its codelist
```

---

## Pre-populating a REST registry from a local file

When you have local constraint files or pre-fetched codelists, load them into
a `RestRegistry` before making network requests. The registry will not fetch
artefacts that are already present.

```python
import sdmxlib as sl

with sl.RestRegistry("https://stats.example.nsa.org/sdmx/v2") as reg:
    reg.load("local_codelists.xml")  # pre-populate from disk
    dsd = reg.get(sl.DataStructure.urn_for(agency="NSA", id="DSD_CPI"), resolve=True)
    # Codelists from the file resolve dimension Refs without extra HTTP calls
```

`RestRegistry.load` accepts a path string, a `Path` object, or raw bytes,
and returns the parsed `StructureMessage`.

---

## Reading from bytes

Both `sl.read` and `reg.load` accept raw bytes — useful when the file
content came from a network response, a database BLOB, or an in-memory
buffer.

```python
import sdmxlib as sl

with open("datastructure.xml", "rb") as f:
    data = f.read()

msg = sl.read(data)  # from bytes — no path needed

reg = sl.InMemoryRegistry()
sl.read(data, registry=reg)  # populate a registry from bytes
```
