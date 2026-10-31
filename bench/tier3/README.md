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

The bench reads an edition someone has already uploaded. It signs in through
DuckDB's credential chain (`az login` on the machine, or its managed
identity), or with a SAS token read from the environment variable
`--sas-env` names. Run it from a machine in the storage account's region,
since the timings are the point; the GET counts should match Azurite's.

A managed identity fails on this edition. A query that opens 5 or more files
stops with "Failed to get token from ChainedTokenCredential" (DuckDB 2.0.0,
azure extension `906e92458c`; 4 files read;
[duckdb-azure#171](https://github.com/duckdb/duckdb-azure/issues/171)), so
the census queries, which open up to 22, need a SAS on a VM:

```bash
az storage container generate-sas --as-user --auth-mode login --permissions rl \
    --account-name <account> --name <container> --expiry <UTC time> -o tsv > ~/.tier3_sas
TIER3_SAS=$(cat ~/.tier3_sas) uv run python -m bench.tier3.bench_tier3 <edition> \
    --remote ... --account ... --region ... --sas-env TIER3_SAS
```

Remote runs set `azure_transport_option_type = 'curl'`: on Ubuntu 24.04 the
extension's default transport fails every request with "Problem with the SSL
CA cert" ([duckdb-azure#185](https://github.com/duckdb/duckdb-azure/issues/185)),
and curl reads the system's CA bundle.

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
   check out sdmxlib, copy the edition directory there, write the SAS, then
   run the `--sas-env` command above. `--update` writes `baselines_azure.json`.

## Census in Canada Central, 4 threads (`baselines_azure.json`)

Read with a SAS from a `Standard_D4as_v7` VM (4 vCPUs, 16 GB) in the storage
account's region. Seconds are one cold read in the first process; the read
after a restart was within 0.3 s of it in every setting.

| setting | geography: cold, again | characteristic: cold, again | peak RSS |
|---|---|---|---|
| cache off | 950 GETs, 87 MB, 1.9 s; the same | 1,937 GETs, 228 MB, 5.2 s; the same, 4.0 s | 1.3 GB |
| in-memory cache (DuckDB's default) | 948 GETs, 88 MB, 1.9 s; none, 0.5 s | 1,886 GETs, 221 MB, 3.9 s; none, 0.8 s | 1.7 GB |
| in-memory, `memory_limit` 400 MB | 948, 3.4 s; none, 0.5 s | 1,910, 7.4 s; 1,889 (evicted), 7.0 s | 1.0 GB |
| spill, `memory_limit` 400 MB | 948, 3.3 s; none, 0.5 s | 1,914, 7.3 s; 1,524 (114 MB), 5.6 s | 1.1 GB |
| in-memory, 1 MB minimum block | 132 GETs, 145 MB, 2.1 s; none, 0.5 s | 373 GETs, 395 MB, 4.4 s; none, 0.8 s | 2.0 GB |
| in-memory, `GRID` request sizing | 80 GETs, 165 MB, 2.1 s; none, 0.5 s | 219 GETs, 438 MB, 4.6 s; none, 0.8 s | 2.0 GB |

The GET counts match Azurite's to within 6 %. Larger requests cut GETs by 7
to 9 times and read about twice the bytes, and in the region the cold reads
are 0.2 to 0.7 s slower, so `EditionEngine` keeps DuckDB's default request
sizing. A 400 MB `memory_limit` nearly doubles a cold read.
