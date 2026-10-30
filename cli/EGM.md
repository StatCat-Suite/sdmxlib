# cli

## Overview

`sdmxlib-cli`, a uv workspace member (`[tool.uv.workspace] members = ["cli"]`
in the root `pyproject.toml`) built with `uv_build`. It installs the `sdmxlib`
console command (`uv tool install sdmxlib-cli`, or `pip install sdmxlib-cli`)
and depends on `sdmxlib==<same version>` and `typer>=0.27.3,<0.28`. It is
released with sdmxlib at the same version (GH-67); `README.md` is its PyPI
description and `LICENSE` is a copy of sdmxlib's.

## Subdirectories & Boundaries

- src: [./src/EGM.md](./src/EGM.md) - source root holding the `sdmxlib_cli`
  package ([./src/sdmxlib_cli/EGM.md](./src/sdmxlib_cli/EGM.md)), the
  `sdmxlib edition` commands over `sdmxlib.edition`.
