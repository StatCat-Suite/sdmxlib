# LocalisedString migration record

The suite design "Text model: add LocalisedString per the SDMX Information
Model" lives in the StatCat Suite hub, at `docs/design/localised-string.md`,
with its EGM reviews, IM audit and benchmark scripts in
`docs/design/egm/localised-string/` there (StatCat-Suite/statcat-suite.github.io#13).
This directory holds what sdmxlib's Stage 2,
[GH-309](https://github.com/StatCat-Suite/sdmxlib/issues/309), adds to that
record:

- [`call-sites.md`](call-sites.md): every sdmxlib site the change reached,
  with its old and new expression and what caught it.

The hub's `bench_final_split.py` became the gated test
`tests/bench/test_istring_lookup_speed.py`. The other scripts stay in the hub.
