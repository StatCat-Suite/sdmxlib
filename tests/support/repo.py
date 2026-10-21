"""Where the repository root is, for tests that read files from it.

A handful of tests read a file from the checkout rather than a fixture — the
public-surface guard walks `docs/api.md`, the compile-context inventory reads
`src/sdmxlib/sql.py`, two reader tests load vendored SDMX-JSON samples. All of
them spelled the path relative to the working directory, which is correct
exactly as long as pytest is launched from the repo root.

`just mutate` does not launch it from the repo root. mutmut copies `src/` and
`tests/` into a `mutants/` tree and runs pytest there, so a relative
`docs/api.md` resolves to `mutants/docs/api.md`, which does not exist — mutmut
copies neither `docs/` nor anything else outside those two directories.

The consequence was worse than a skipped test: the read happens at *collection*
time, so it raised during mutmut's stats pass and aborted the whole run with
``failed to collect stats``. `just mutate` was broken from the moment
`test_public_surface.py` landed in #265 until #252, and nothing surfaced it
because mutation is deliberately not part of `just ci`.

Anchoring to `__file__` alone is not enough, because under mutmut `__file__`
points inside `mutants/tests/support/`. `find_root` therefore walks up looking
for a directory that has *both* a `pyproject.toml` and a `docs/` — the marker
pair that the mutants tree fails, so the walk continues past it and lands on
the real checkout.
"""

import pathlib


def find_root() -> pathlib.Path:
    """The repository root, from anywhere including a mutmut `mutants/` copy.

    Returns:
        Path to the directory holding `pyproject.toml` and `docs/`.

    Raises:
        RuntimeError: If no ancestor has both markers — better than silently
            returning a path whose files are all missing, which is how the
            original bug presented.
    """
    for candidate in pathlib.Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file() and (candidate / "docs").is_dir():
            return candidate
    msg = (
        "Could not locate the repository root above "
        f"{pathlib.Path(__file__).resolve()} — looked for an ancestor holding "
        "both pyproject.toml and docs/."
    )
    raise RuntimeError(msg)


#: The checkout root. Join onto this rather than writing a relative path, so a
#: test does not depend on the working directory pytest happened to start in.
ROOT = find_root()
