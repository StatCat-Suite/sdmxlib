# Call sites of the InternationalString change (GH-309)

`InternationalString` became a `Mapping[str, LocalisedString]`: `[]`, `get`,
`pick`, `values()` and `items()` return `LocalisedString`, `get` takes one tag
and a default, `as_dict()` lost `copy`, and `emit_pair()` became the private
`_emit_pair()`. This table lists every site in `src/` and `bench/` the change
reached, as of the commit that made it.

Three things found them:

- **pyrefly**, where a `LocalisedString` reached a parameter typed `str`
  (lxml's `.text`, a `TypedDict` field, `str.strip`) or a removed name.
- **a test**, where the value flowed on untyped: a Polars cell, a truthiness
  test, an `or` fallback. The test named is the one that failed.
- **the audit**: `SDMXLIB_ISTRING_AUDIT=1 SDMXLIB_ISTRING_AUDIT_OUT=<file>
  uv run pytest -m "unit or integration"`, run after the change. It logs every
  site that still reads through a public lookup. It found one site in `src/`,
  `model/istring.py`'s own `_Items.__contains__`, which reads `get` on purpose,
  and 190 in `tests/`, each in a passing test.

Serialisers and validators read the stored texts through `_backing()` (the
stored map, uncopied) or `_text(tag)` (one text, for a tag already
normalised): they need the text, and building a `LocalisedString` per text
would cost an allocation on the write path.

| file:line | old | new | caught by |
|---|---|---|---|
| `bundle/_canonical.py:82` | `dict(value.items())` | `dict(value._backing())` | pyrefly; `test_canonical_json.py` |
| `bundle/_overlay.py:502` | `(value or None)` for `description` | the map as built | `test_annotation_overlays.py::test_a_description_language_is_removed_and_a_names_last_one_is_refused`: `to_istring(None)` now raises, where it passed `None` into a field typed `InternationalString` |
| `formats/sdmx_json/_data_common.py:173` | `next(iter(istr.items()))[1]` | `next(iter(istr._backing().values()), None)` | pyrefly |
| `formats/sdmx_json/_data_common.py:180` | `ann.text.get(lang)` | `ann.text._text(lang)` | pyrefly |
| `formats/sdmx_json/_data_common.py:385` | `name.get(lang)` | `name._text(self._lang)` | pyrefly |
| `formats/sdmx_json/_data_common.py:775, 784` | `name_attr.get(lang)`, `desc_attr.get(lang)` | `._text(lang)` | pyrefly |
| `formats/sdmx_json/_data_common.py:778, 787` | `dict(name_attr.items())` | `name_attr.as_dict()` | pyrefly |
| `formats/sdmx_json/_dto.py:99` | `v.emit_pair()` | `v._emit_pair()` | pyrefly |
| `formats/sdmx_json/metadata.py:139` | `v.as_dict(copy=False)` | `v._backing()` | pyrefly |
| `formats/sdmx_ml/_common.py:1792` | `istring.items()` | `istring._backing().items()` for an `InternationalString` | pyrefly |
| `formats/sdmx_ml/_common.py:1840` | `ann.text.items()` | `ann.text._backing().items()` | pyrefly |
| `formats/sdmx_ml/_header.py:104` | `texts.items()` | `texts._backing().items()` | pyrefly |
| `formats/sdmx_ml/_v3_writer.py:772, 776` | `contact.department.items()`, `contact.role.items()` | `._backing().items()` | pyrefly |
| `formats/sdmx_ml21/writer.py:155, 159` | `contact.department.items()`, `contact.role.items()` | `._backing().items()` | pyrefly |
| `formats/sdmx_ml30/metadata.py:87` | `s.items()` | `s._backing().items()` | pyrefly |
| `model/_rules.py:148` | `name.items()` | `name._backing().items()` | pyrefly |
| `model/_rules.py:213` | `value.as_dict(copy=False)` | `value._backing()` | pyrefly; every model construction raised `TypeError` |
| `storage/writers.py:291, 347` | `istring.items()`, `ann.text.items()` | `._backing().items()` | pyrefly |
| `polars.py:416, 434, 450, 468, 487` | `concept().name.get(lang) or …` | `concept().name._text(lang) or …` | `test_polars_bridge.py` (`structure_frame`: "Cannot convert LocalisedString"); `test_istring.py::test_structure_frame_concept_labels_are_text` |
| `polars.py:354` | `f"{column}{suffix}_{one}"` | `f"{column}{suffix}_{tag_alias(one)}"` | the design (D8); `test_language_tags.py::test_a_generated_name_writes_its_tag_through_tag_alias` |
| `query/_expr.py:1240` | `f"{self._column}_{self._lang}"` | `f"{self._column}_{tag_alias(self._lang)}"` | the design (D8); the same test |
| `bench/sweep/modeldiff.py:91, 139` | `as_dict(copy=False)` | `_backing()` | pyrefly; `test_sweep.py` |
| `bench/compete/libs.py:199` | `code.name.get("en", "_default")` | `name.text if (name := code.name.get("en")) else None` | pyrefly |

Four `isinstance(x, Mapping)` sites now match an `InternationalString`.
None needed a change: `query/_expr.py:1064`, `1558` and `2388` refuse a
`Mapping` as an `is_in` value set, which now covers an `InternationalString`
too, and `model/version_rewrite.py:235` receives a `VersionRewrite`, never a
string.

The test uses of `emit_pair` and `as_dict(copy=False)` became `_emit_pair`
and `_backing` in `tests/support/strategies.py` (a comment),
`test_properties_core.py`, `test_shared_empty_values.py` and `test_dto.py`.
Test assertions comparing a lookup with a `str` read `.text`; the user docs'
examples changed the same way, and their `get(tag, default)` fallbacks became
`as_dict().get(tag, default)` or `pick`.
