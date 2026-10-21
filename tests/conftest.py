"""Hypothesis profiles (#215).

Three profiles. Note what does *not* distinguish them: example count is not
rationed to save CI time. Measured on the heaviest property here, 25 → 100
examples costs 0.28 s, against a pipeline that spends about a minute on
``uv sync`` before it runs anything. Trading exploration for that would be
false economy.

- ``dev`` — the local default, and Hypothesis's own default example count.
  Tuned for inner-loop feedback, not for thoroughness.
- ``ci`` — same exploration as ``dev``, plus ``derandomize=True``. That is the
  one genuine difference: ``.hypothesis/`` is gitignored and absent in CI, so a
  failure there has to reproduce from the report alone rather than from a
  database that does not exist.
- ``deep`` — for deliberately hunting. Runs the same properties with 20× the
  examples; use it when a property is new, or when a mutation run (#213) says a
  path is weakly pinned. ``HYPOTHESIS_PROFILE=deep just test``.

``deadline=None`` everywhere: several strategies build artefacts that touch
DuckDB, and a per-example wall-clock deadline turns an unlucky first example
into a spurious failure.
"""

import os

from hypothesis import HealthCheck, settings

# ── Fork safety on macOS (#252) ──────────────────────────────────────────────
#
# `just mutate` forks a child per mutant. On Darwin, `urllib.request.getproxies`
# is `getproxies_environment() or getproxies_macosx_sysconf()`, and the second
# reaches into the SystemConfiguration framework, which is **not fork-safe**.
# Constructing an `httpx.Client` calls it, so `RestRegistry(...)` in a forked
# child crashes the interpreter outright:
#
#     Fatal Python error: Segmentation fault
#       urllib/request.py in getproxies_macosx_sysconf
#       httpx/_client.py  in _get_proxy_map
#       sdmxlib/api/client.py:156 in __init__
#       tests/unit/test_rest_registry.py in test_store_writes_through...
#
# mutmut books the dead child as a *timeout*, which is why the first clean
# mutation run reported 356 of them with no plausible mutant behind any of the
# ones sampled — the crash is in the harness, not in the mutated code.
#
# Setting any `*_proxy` variable makes `getproxies_environment()` return
# non-empty, so `getproxies()` returns before touching the framework at all.
# `no_proxy="*"` is the right one to set regardless of forking: no test in this
# suite should reach the network, and none should consult a system proxy to
# decide not to. Measured: with it set, `getproxies_macosx_sysconf` is never
# called.
os.environ.setdefault("no_proxy", "*")

_EXAMPLES = 100

settings.register_profile("dev", max_examples=_EXAMPLES, deadline=None)
settings.register_profile(
    "ci",
    max_examples=_EXAMPLES,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "deep",
    max_examples=_EXAMPLES * 20,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE") or ("ci" if os.environ.get("CI") else "dev"))
