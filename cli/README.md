# sdmxlib-cli

[sdmxlib](https://github.com/StatCat-Suite/sdmxlib) from the command line. A
separate install, so the library itself carries no CLI dependency:

```sh
uv tool install sdmxlib-cli
sdmxlib bundle check path/to/bundle
```

`sdmxlib bundle check` runs `sdmxlib.bundle.check_bundle`: the manifest, every
file's checksum, the DuckLake catalog, the bindings' observation counts, and
the structural closure (`--no-references` skips it). It prints
each finding and exits 1 if there are any.

Released with sdmxlib, at the same version.
