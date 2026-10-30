# sdmxlib-cli

[sdmxlib](https://github.com/StatCat-Suite/sdmxlib) from the command line. A
separate install, so the library itself carries no CLI dependency:

```sh
uv tool install sdmxlib-cli
# or: pip install sdmxlib-cli
sdmxlib edition check path/to/edition --at publish
```

`sdmxlib edition check` runs `sdmxlib.edition.check_edition` at the profile
`--at` names. `publish` checks the manifest, every file's checksum, the
DuckLake catalog, the bindings' observation counts, the structural closure
and the fingerprints. `receive` checks data files by size and leaves the
fingerprints out; `open` checks file sizes only. It prints each finding and
exits 1 if there are any.

Released with sdmxlib, at the same version.
