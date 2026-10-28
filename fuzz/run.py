"""Run one fuzz target under Atheris (GH-241).

    PYTHONPATH=. uv run --group fuzz python fuzz/run.py TARGET CORPUS [libFuzzer flags]

TARGET is a key of `tests.fuzz.targets.TARGETS`. CORPUS is a directory; when
it is empty, the target's seed fixtures are copied in first. The remaining
arguments go to libFuzzer (`-max_total_time=600`, `-artifact_prefix=...`).

Atheris ships Linux x86_64 wheels only, so this runs in `.github/workflows/fuzz.yml`
and not on macOS. It stays outside pyrefly's scope for that reason: `atheris`
cannot be resolved where the checker runs. The targets it drives are in
`tests/fuzz/`, which pyrefly checks, and a crash it finds is replayed by
`tests/unit/test_fuzz_crashes.py` everywhere.
"""

import shutil
import sys
from pathlib import Path

import atheris

with atheris.instrument_imports(include=["sdmxlib"]):
    from tests.fuzz import targets


def main() -> None:
    """Seed the corpus if it is empty, then fuzz the target until libFuzzer stops."""
    target, corpus = sys.argv[1], Path(sys.argv[2])
    if target not in targets.TARGETS:
        sys.exit(f"unknown target {target!r}; one of {', '.join(targets.TARGETS)}")
    corpus.mkdir(parents=True, exist_ok=True)
    if not any(corpus.iterdir()):
        for seed in targets.seeds(target):
            shutil.copy(seed, corpus / f"seed-{seed.parent.name}-{seed.name}")

    def one_input(data: bytes) -> None:
        targets.check(target, data)

    atheris.Setup([sys.argv[0], str(corpus), f"-max_len={targets.MAX_LEN}", *sys.argv[3:]], one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
