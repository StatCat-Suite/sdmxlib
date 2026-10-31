# Tier-3 reads from object storage

What a server's reads of an edition's data files cost in requests, bytes and
time when the files are in object storage, under each DuckDB cache setting
(GH-185, GH-231). Not part of `just ci`: it reads a whole edition's data
files, 2.1 GB for the census edition.

```bash
just bench-tier3 <edition>          # Azurite: print the table
just bench-tier3-update <edition>   # Azurite: also overwrite baselines.json
just bench-tier3 <edition> --remote az://<container>/<product>/<edition id>/ \
    --account <storage account> --region <region>   # Azure Blob Storage
```

Without `--remote`, the edition's files go to Azurite, pinned by digest as
the object-store tier pins it (`tests/stores/_stores.py`); a pod's copy of tiers 1 and 2 opens
them with `LocalRegistry.edition(path, remote=...)`. Each setting runs in a
fresh process that reads one geography, one characteristic and the count twice,
then in a second fresh process, which is a pod restart. DuckDB's HTTP log gives
the GET count and bytes of each read; `duckdb_external_file_cache()` the bytes
cached and spilled.

The counts are what an object store would see. The timings are the emulator's
on one machine, which has no per-request latency, so a setting that trades
bytes for fewer requests shows no gain here; `--remote` measures that in a
cloud region (below).

## Census, 2.1 GB, 4 threads (`baselines.json`)

| setting | geography: cold, again | characteristic: cold, again | after a restart |
|---|---|---|---|
| cache off | 940 GETs, 84 MB; the same | 1,971 GETs, 232 MB; the same | the same |
| in-memory cache (DuckDB's default) | 938 GETs, 85 MB; none | 1,922 GETs, 224 MB; none | cold again |
| in-memory, `memory_limit` 400 MB | 938; none | 1,949; 1,967 (evicted) | cold again |
| spill, `memory_limit` 400 MB | 938; none | 1,939; 1,604 (121 MB) | cold again |
| in-memory, 1 MB minimum block | 133 GETs, 150 MB; none | 354 GETs, 383 MB; none | cold again |
| in-memory, `GRID` request sizing | 82 GETs, 169 MB; none | 207 GETs, 411 MB; none | cold again |

`count()` makes no request in any setting: DuckLake's catalog answers it.

## Azure Blob Storage (`--remote`)

The bench reads an edition someone has already uploaded, and signs in through
DuckDB's credential chain: `az login` on the machine, or its managed identity.
No key passes through the bench. Run it from a machine in the storage
account's region, since the timings are the point; the GET counts should match
Azurite's.

1. Create a storage account and a container in one region, and give the
   identity the bench runs as the *Storage Blob Data Reader* role on it
   (*Contributor* to upload).
2. Upload the product directory as it is on disk, its `data/` beside the
   edition directory, so the manifest's `data_path` of `../data/` resolves:

   ```bash
   az storage blob upload-batch --auth-mode login --account-name <account> \
       --destination <container> --destination-path <product> --source <product directory>
   ```

3. On a VM in the same region with 4 vCPUs (the engine runs 4 threads),
   check out sdmxlib, copy the edition directory there, run `az login`, then
   the `--remote` command above. `--update` writes `baselines_azure.json`.
