# sdmxlib-cli

[sdmxlib](https://github.com/StatCat-Suite/sdmxlib) from the command line. A
separate install, so the library itself carries no CLI dependency:

```sh
uv tool install sdmxlib-cli
sdmxlib bundle check path/to/publication --at publish
```

`sdmxlib bundle check` runs `sdmxlib.bundle.check_bundle` at the profile
`--at` names. `publish` checks the manifest, every file's checksum, the
DuckLake catalog, the bindings' observation counts, the structural closure
and the fingerprints. `receive` checks data files by size and leaves the
fingerprints out; `open` checks file sizes only. It prints each finding and
exits 1 if there are any.

Released with sdmxlib, at the same version.
