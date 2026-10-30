# Connecting & Fetching

Practical recipes for connecting to SDMX REST endpoints and fetching
artefacts.

---

## Connecting to a registry

<!-- prettier-ignore -->
!!! tip "FMR vs SDMX Global Registry"
    Use `sl.RestRegistry` for any SDMX 2.1 or 3.0 REST endpoint. If your
    agency runs Fusion Metadata Registry (FMR), `sl.FmrRegistry` is a
    `RestRegistry` preset for it: it defaults to the SDMX Global Registry
    (itself an FMR), speaks the 2.1 API, and takes `auth=` as either a
    `(user, password)` pair or an `sl.BearerToken`. For an FMR serving the 3.0
    API, use `sl.RestRegistry(url, api="3.0")` instead.

sdmxlib works with any SDMX 2.1 or 3.0 REST endpoint. Use it as a context
manager so the HTTP connection is cleaned up automatically.

`sl.Provider` holds URLs for well-known public registries:

```python
import sdmxlib as sl

# Using a built-in provider shortcut
with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="ESTAT", id="NAMAIN_10_GDP"), resolve=True)
    print(dsd.id, dsd.name["en"].text)
```

```
NAMAIN_10_GDP  National Accounts Main Aggregates — GDP
```

You can also pass the endpoint URL directly — useful for internal registries or
any endpoint not in `sl.Provider`:

```python
# Raw URL — works for any SDMX REST endpoint
with sl.RestRegistry("https://registry.sdmx.org/sdmx/v2") as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="ESTAT", id="NAMAIN_10_GDP"), resolve=True)

# Internal or custom endpoint, with explicit SDMX version
with sl.RestRegistry("https://stats.example.nsa.org/sdmx/", api="3.0") as reg:
    dsd = reg.get(sl.DataStructure.urn_for(agency="NSA", id="DSD_CPI"), resolve=True)
```

Available built-in providers: `ECB`, `BIS`, `ESTAT`, `IMF`, `OECD`,
`SDMX_GLOBAL`. Both attribute and subscript access work:

```python
sl.Provider.ECB  # ProviderInfo(url="https://data-api.ecb.europa.eu/service", api="2.1", id="ECB")
sl.Provider["ECB"]  # same
sl.Provider.ECB.url  # "https://data-api.ecb.europa.eu/service"
sl.Provider.ECB.api  # "2.1"
```

---

## Fetching a single codelist

Fetch a codelist directly when you only need the code table — for instance,
to populate a database lookup table or validate a reference file.

```python
with sl.RestRegistry(sl.Provider.SDMX_GLOBAL) as reg:
    cl = reg.get(sl.Codelist.urn_for(agency="SDMX", id="CL_AREA"), resolve=True)

    print(f"Codelist: {cl.agency_id}:{cl.id} v{cl.version}")
    print(f"Total codes: {len(cl.codes)}")
    print()
    for code in cl.codes[:6]:
        print(f"  {code.id:<8}  {code.name.as_dict().get('en', '')}")
    print("  ...")
```

```
Codelist: SDMX:CL_AREA v1.1
Total codes: 249

  AD        Andorra
  AE        United Arab Emirates
  AF        Afghanistan
  AG        Antigua and Barbuda
  AL        Albania
  AM        Armenia
  ...
```

---

## Connecting to a Fusion Metadata Registry

`sl.FmrRegistry` takes the same arguments as `RestRegistry` apart from `api`,
which it fixes at 2.1, and turns a `BearerToken` into an `Authorization`
header:

```python
with sl.FmrRegistry() as reg:  # the SDMX Global Registry
    cl = reg.get(sl.Codelist.urn_for(agency="SDMX", id="CL_AREA"), resolve=True)

with sl.FmrRegistry("https://my-fmr.example.com/sdmxapi/rest", auth=sl.BearerToken("eyJ...")) as reg:
    ...
```
