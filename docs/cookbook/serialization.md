# Serialization

Recipes for reading, writing and converting SDMX structural metadata, and for
finding out what a conversion cannot carry before it costs you anything.

sdmxlib reads and writes SDMX-ML 2.1, 3.0 and 3.1 and SDMX-JSON 2.0 structure
messages. The model in between is SDMX 3.x, so each format is a view of it,
and the narrower ones (2.1 most of all) cannot hold everything it can.

---

## Read a file, write any version

`sl.read` takes a path (or bytes) and detects the format; `sl.parse` does the
same for bytes you already hold. `sl.to_xml` writes SDMX-ML (2.1 unless you
say otherwise), and `sl.to_json` writes SDMX-JSON.

```python
msg = sl.read("dsd_cpi.xml")
print(msg.data_structures.ids())  # ['DSD_X']
print(msg.codelists.ids())  # ['CL_FREQ', 'CL_GEO']

xml_21 = sl.to_xml(msg)  # SDMX-ML 2.1, the default
xml_30 = sl.to_xml(msg, version="3.0", pretty_print=True)
xml_31 = sl.to_xml(msg, version="3.1")

# Each reads back to the same artefacts
for xml in (xml_21, xml_30, xml_31):
    assert sl.parse(xml).data_structures[0] == msg.data_structures[0]
```

`sl.serialize(msg, version=...)` is the same writer with one switch for
every target: `"2.1"`, `"3.0"`, `"3.1"` or `"json"`.

```python
codelists = sl.StructureMessage(codelists=msg.codelists)
json_bytes = sl.serialize(codelists, version="json")
assert json_bytes == sl.to_json(codelists)
assert sl.parse(json_bytes).codelists == msg.codelists
```

Every writer checks the message first (`validate=True`), and raises
`sl.ValidationError` naming every problem before writing a byte. What a single
artefact must be is already checked when it is built, so what is left is
what the message and the target can carry: a sentinel value on an
enumeration format, or two artefacts with one URN.

---

## Set the message header

A message's header is `msg.header`: an `sl.Header` for a structure message,
`None` when there is none. Set it to say who sent the message, and when.

```python
from datetime import UTC, datetime

header = sl.Header(
    id="REQ_1",
    prepared=datetime(2026, 10, 20, 9, 30, tzinfo=UTC),
    sender=sl.Sender(id="ESTAT"),
    receivers=[sl.Party(id="OECD")],
)
with_header = sl.StructureMessage(header=header, codelists=msg.codelists)

for version in ("2.1", "3.0", "3.1", "json"):
    assert sl.parse(sl.serialize(with_header, version=version)).header == header
```

The model holds every datetime in UTC, so a naive `prepared` is taken as UTC.

A message without a header is written with fixed placeholders so the same
message always serialises to the same bytes: id `SDMX_MSG`, prepared
`2000-01-01T00:00:00` (`2000-01-01T00:00:00Z` in SDMX-JSON, whose schema
requires a zone), and sender `sdmxlib`. Reading those placeholders gives back
`None`, so a header-less message round-trips as header-less:

```python
plain = sl.to_xml(codelists, version="3.0")
assert b"<mes:ID>SDMX_MSG</mes:ID>" in plain
print(sl.parse(plain).header)  # None
```

SDMX-JSON's `meta` has no source and no sender time zone, and
`sl.unrepresentable` (next recipe) says so for a header that uses them.

---

## Ask what a target format cannot carry

Converting to an older or narrower format can lose information. Ask before
you write, rather than diffing the output.

**`sl.unrepresentable(artefact, target)`** lists what one artefact (or a
header) would lose. Each `sl.Gap` names the target, the field and the reason:

```python
linked = sl.Codelist(
    id="CL_FREQ",
    maintainer="AG",
    version="1.0",
    name={"en": "Frequency"},
    codes=[sl.Code(id="A", name={"en": "Annual"})],
    links=(sl.Link(rel="metadata", url="https://example.org/metadata/FREQ"),),
)
for gap in sl.unrepresentable(linked, "2.1"):
    print(gap.field)  # 'links'

print(sl.unrepresentable(linked, "3.0"))  # ()
```

**`sl.dropped(msg, target)`** lists the artefacts a write leaves out
entirely. SDMX-ML 2.1 has no form for a draft version, for example, so a
draft is left out of a 2.1 document, along with anything that references it:

```python
draft = sl.Codelist(
    id="CL_DRAFT",
    maintainer="AG",
    version="1.0.0-draft",
    name={"en": "Work in progress"},
    codes=[sl.Code(id="A", name={"en": "Annual"})],
)
both = sl.StructureMessage(codelists=[linked, draft])

for drop in sl.dropped(both, "2.1"):
    print(drop.urn)  # 'urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:CL_DRAFT(1.0.0-draft)'

assert b"CL_DRAFT" not in sl.to_xml(both, version="2.1")
assert sl.dropped(both, "3.0") == ()
```

A `DroppedArtefact` also carries its `cause` (the artefact whose problem
cascaded to it) and a `reason`. The targets both functions take are `"2.1"`,
`"3.0"`, `"3.1"`, `"json"` and `"storage"`.

---

## Read strictly, or keep what can be read

A document can hold an artefact that breaks the SDMX schema, or something the
model has no field for. `parse` and `read` report both. Two keywords set how
strict they are:

| | default | what it does |
| --- | --- | --- |
| `strict=` | `True` | raise when any artefact is refused; `False` reads the rest and records the refusals |
| `lossless=` | `False` | `True` raises `sl.LossyReadError` if anything was lost or assumed |

**Refused.** By default one bad artefact raises, naming it. Here the second
codelist's code has a blank name, so it cannot be built:

```python
good = sl.Codelist(id="CL_FREQ", maintainer="AG", version="1.0", name={"en": "Frequency"},
                   codes=[sl.Code(id="A", name={"en": "Annual"})])
area = sl.Codelist(id="CL_AREA", maintainer="AG", version="1.0", name={"en": "Area"},
                   codes=[sl.Code(id="CA", name={"en": "Canada"})])
document = sl.to_xml(sl.StructureMessage(codelists=[good, area]), version="3.0")
broken = document.replace(b">Canada<", b"><")

try:
    sl.parse(broken)
except sl.SdmxParseError as exc:
    print(type(exc).__name__)  # 'SdmxConformanceError'

kept = sl.parse(broken, strict=False)
print(kept.codelists.ids())  # ['CL_FREQ']
for refusal in kept.refused:
    print(refusal.urn)  # 'urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:CL_AREA(1.0)'
```

`SdmxConformanceError` is an `sl.SdmxParseError`, so catch that. Each
`sl.Refusal` carries the `artefact_class`, `urn` and `reason`.

**Lost.** Something the model has no field for is left out of the artefact
and recorded on `msg.lost`, one `sl.ReadGap` per artefact and construct. The
read still succeeds:

```python
import json

payload = json.loads(sl.to_json(sl.StructureMessage(codelists=[good])))
payload["data"]["codelists"][0]["x-note"] = "a vendor extension"
vendor = json.dumps(payload).encode()

for gap in sl.parse(vendor).lost:
    print(gap.construct)  # 'CodelistJson.x-note'
```

**Assumed.** When a document omits something its schema requires and the
reader can fill in a sensible value, it does, and records it on
`msg.assumed` as an `sl.ReadAssumption`. An SDMX-ML 3.0 attribute with no
`AttributeRelationship` is read as attached to the data set:

```python
import re

concepts = sl.ConceptScheme(id="CS", maintainer="AG", version="1.0", name={"en": "Concepts"},
                            concepts=[sl.Concept(id=c, name={"en": c}) for c in ("FREQ", "OBS_STATUS", "OBS_VALUE")])
structure = sl.DataStructure(
    id="DSD",
    maintainer="AG",
    version="1.0",
    name={"en": "Example"},
    dimensions=[sl.Dimension(id="FREQ", concept=concepts.ref_to("FREQ"))],
    attributes=[sl.Attribute(id="OBS_STATUS", concept=concepts.ref_to("OBS_STATUS"),
                             attachment=sl.AttachmentLevel.OBSERVATION)],
    measures=[sl.Measure(id="OBS_VALUE", concept=concepts.ref_to("OBS_VALUE"))],
)
written = sl.to_xml(sl.StructureMessage(concept_schemes=[concepts], data_structures=[structure]), version="3.0")
no_relationship = re.sub(rb"<str:AttributeRelationship>.*?</str:AttributeRelationship>", b"", written)

assumed = sl.parse(no_relationship)
for assumption in assumed.assumed:
    print(assumption.assumed)  # 'attachment DATASET'
```

When you need the artefacts to be exactly what the document said,
`lossless=True` turns both into an error, which carries them as `.lost` and
`.assumed`:

```python
try:
    sl.parse(no_relationship, lossless=True)
except sl.LossyReadError as exc:
    print((len(exc.assumed), len(exc.lost)))  # (1, 0)
```

`sl.read(path, strict=..., lossless=...)` takes the same keywords.

---

## What the writers leave out

Output is minimal: an optional attribute at the default its schema declares
is not written, and the model holds the meaning rather than `None`. An
attribute's `usage` defaults to optional, so it is never spelled out as
`usage="optional"`, and it reads back as `AttributeUsage.OPTIONAL`:

```python
print(structure.attributes["OBS_STATUS"].usage)  # 'AttributeUsage.OPTIONAL'
assert b"usage=" not in written
assert sl.parse(written).data_structures[0] == structure
```

The same default can be spelled differently in different versions, so a
conversion writes what the target needs. `isMultiLingual` defaults to true in
SDMX-ML 3.0 and to false in 3.1. An attribute that is not multilingual has to
say so in 3.0 and needs nothing in 3.1:

```python
import attrs

textual = sl.Attribute(id="OBS_STATUS", concept=concepts.ref_to("OBS_STATUS"),
                       attachment=sl.AttachmentLevel.OBSERVATION, representation=sl.Facet(type="String"))
with_text = attrs.evolve(structure, attributes=[textual])
plain_text = sl.StructureMessage(concept_schemes=[concepts], data_structures=[with_text])
assert b'isMultiLingual="false"' in sl.to_xml(plain_text, version="3.0")
assert b"isMultiLingual" not in sl.to_xml(plain_text, version="3.1")
```

Identity (an artefact's or reference's id, agency, version and class) and
anything a format requires are always written. There is no option to spell
defaults out.
