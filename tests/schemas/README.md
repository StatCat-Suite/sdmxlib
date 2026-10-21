# SDMX schema vendoring

This directory vendors the official SDMX-ML XML Schema files and the
SDMX-JSON 2.1 JSON Schema files for **test-only use**. They are not
included in the published `sdmxlib` wheel — see `pyproject.toml`'s
build configuration.

## What's here

```
sdmx_ml21/    SDMX-ML 2.1     (58 .xsd files, ~1.2 MB)
sdmx_ml30/    SDMX-ML 3.0.0   (30 .xsd files, ~700 KB)
sdmx_ml31/    SDMX-ML 3.1.0   (30 .xsd files, ~680 KB)
sdmx_json/2.1 SDMX-JSON 2.1.0 (3 .json schemas, ~340 KB; plus samples/)
sdmx_json/2.0 SDMX-JSON 2.0.0 (3 .json schemas; plus samples/) — the
              version `formats.sdmx_json` emits, so the one its output is
              validated against. Structure + metadata added in #364; before
              that the only structure schema here was 2.1's, which describes
              SDMX 3.1 and closes objects that 2.0 leaves open.
sdmx_json/1.0 SDMX-JSON 1.0   data + structure schemas (plus samples/). The
              structure schema (tag v1.0) is what the `/availability`
              ContentConstraint writer emits against; added with #433.
sdmx_im/      SDMX 3.1.0 Section 2, the Information Model, as text (~350 KB):
              what `tests/support/im_rules.py` quotes (#456)
VERSION       upstream tags + retrieval dates
```

The flat-per-version layout matches upstream exactly. The XSDs reference each
other via `<xs:include schemaLocation="..."/>` relative to each file's own
directory, so lxml's `etree.XMLSchema` resolves the import graph without any
catalog or path manipulation.

## Attribution

The XSD files are © SDMX initiative — `Copyright SDMX 2021 - http://www.sdmx.org`
(as carried in each file's XML comment header). They were retrieved from the
official SDMX Technical Working Group repository at
<https://github.com/sdmx-twg/sdmx-ml> at tags `v2.1`, `v3.0.0`, and `v3.1.0`.

The upstream repository does not ship an explicit LICENSE file or a written
redistribution clause. The SDMX standards (of which the XSDs are a normative
part) are published by the SDMX sponsoring institutions — BIS, ECB, Eurostat,
IMF, OECD, the UN, and the World Bank — as official open standards intended
for use by implementers. This is the practical convention every SDMX-ML
implementer relies on; sdmxlib vendors them here on the same understanding,
for test-only conformance validation of our writer output.

If the SDMX initiative publishes formal redistribution terms, this notice
will be updated to match.

The Information Model text is © SDMX initiative likewise: Section 2 of the
SDMX 3.1.0 standard, retrieved from <https://sdmx.org> and vendored on the
same understanding, as text extracted from the published PDF, for test-only
use — the IM rule census quotes it, and checks each quote against it.

## Refreshing — SDMX-ML

When a new SDMX-ML version is published upstream:

1. Pick the new tag from <https://github.com/sdmx-twg/sdmx-ml/tags>.
2. Download the tagged tarball:
   ```bash
   curl -L -o /tmp/sdmx-ml-X.Y.Z.tar.gz \
     https://github.com/sdmx-twg/sdmx-ml/archive/refs/tags/vX.Y.Z.tar.gz
   ```
3. Replace the matching directory:
   ```bash
   tar xzf /tmp/sdmx-ml-X.Y.Z.tar.gz -C /tmp
   rm tests/schemas/sdmx_ml30/*.xsd
   cp /tmp/sdmx-ml-X.Y.Z/schemas/*.xsd tests/schemas/sdmx_ml30/
   ```
4. Update `tests/schemas/VERSION` with the new tag + date.
5. Run `just ci`. The schema-validated writer tests will surface any
   conformance regressions in the new schema set.

## Refreshing — SDMX-JSON

The SDMX-JSON 2.1 schemas are single self-contained JSON Schema documents
(draft 2019-09):

```bash
TAG=v2.1.0
for kind in structure metadata data; do
  curl -sL "https://raw.githubusercontent.com/sdmx-twg/sdmx-json/${TAG}/${kind}-message/tools/schemas/sdmx-json-${kind}-schema.json" \
    -o "tests/schemas/sdmx_json/2.1/sdmx-json-${kind}-schema.json"
done
# Refresh the pinned-baseline sample too:
curl -sL "https://raw.githubusercontent.com/sdmx-twg/sdmx-json/${TAG}/structure-message/samples/constructed-sample.json" \
  -o tests/schemas/sdmx_json/2.1/samples/structure-constructed-sample.json
```

Then bump the `sdmx-json` line in `tests/schemas/VERSION`.

## Refreshing — the Information Model

`sdmx_im/` holds the text of SDMX 3.1.0 Section 2, extracted with poppler's
`pdftotext` (26.10.0) in layout mode, which keeps the PDF's page breaks as form
feeds — the census locates each quote by page:

```bash
curl -sL -o /tmp/im.pdf https://sdmx.org/wp-content/uploads/SDMX_3-1-0_SECTION_2_FINAL.pdf
pdftotext -layout /tmp/im.pdf tests/schemas/sdmx_im/SDMX_3-1-0_SECTION_2_FINAL.txt
```

Then bump the `sdmx_im` lines in `tests/schemas/VERSION`. A new edition of the
IM renumbers pages and sections: `tests/unit/test_im_rule_census.py` fails on
every quote that moved, and on every new *must* no rule quotes yet.
