"""A date-time with no zone is SDMX-ML's, and SDMX-JSON writes it only as a time range's bound (#496).

A constraint's periods are held as written: a `TimeBound.period` and every
selection's ``valid_from`` / ``valid_to`` are SDMX's time-period text, which in
SDMX-ML may be an ``xs:dateTime``, and an ``xs:dateTime``'s zone is optional.
SDMX-JSON's period types admit a date-time only as JSON Schema's ``date-time``
(RFC 3339), which requires one. Appending ``Z`` would state a zone the source
never gave, so the JSON writers refuse a zoneless validity date-time, as they
refuse an unwritable version.

A `TimeRange`'s start and end are the declared exception, written zoneless
and schema-invalid, as NSI writes them (GH-392). The .Stat Suite Data Explorer
turns each bound into a JavaScript ``Date`` with ``new Date(p.toString())``
(dotstatsuite-data-explorer ``src/web/lib/sdmx/index.js``, line 158 at
``b85ec0a4``). A bare date or a bound with ``Z`` parses as UTC, so west of UTC
``2021-01-01`` shows as the previous day, and an annual range's first year as
the year before; a zoneless date-time parses as local time and shows as
written. `sdmxlib.builders` writes availability bounds this way
(`sdmxlib.model.period`), and the data messages' time bounds carry the same
deviation.
"""

from collections.abc import Iterator
from datetime import datetime

from sdmxlib.model.constraint import (
    AttachmentConstraint,
    ComponentValueSet,
    DataConstraint,
    DataKeySet,
    KeyValue,
    TimeRange,
)
from sdmxlib.model.validation import ValidationIssue


def is_zoneless_date_time(text: str) -> bool:
    """Whether *text* is a date-time with no zone: a date alone, or a reporting period, is not."""
    if "T" not in text:
        return False
    try:
        return datetime.fromisoformat(text).tzinfo is None
    except ValueError:
        return False


def _range(path: str, time_range: TimeRange | None) -> Iterator[tuple[str, str | None]]:
    """A time range's validity; its start and end are written as given, zoneless or not (the module says why)."""
    if time_range is None:
        return
    yield f"{path}.valid_from", time_range.valid_from
    yield f"{path}.valid_to", time_range.valid_to


def _selection(path: str, selection: KeyValue | ComponentValueSet) -> Iterator[tuple[str, str | None]]:
    yield from _range(f"{path}.time_range", selection.time_range)
    for value in sorted(selection.values, key=lambda v: v.value):
        yield f"{path}.values[{value.value}].valid_from", value.valid_from
        yield f"{path}.values[{value.value}].valid_to", value.valid_to
    if isinstance(selection, KeyValue):
        yield f"{path}.valid_from", selection.valid_from
        yield f"{path}.valid_to", selection.valid_to


def _key_sets(path: str, key_sets: tuple[DataKeySet, ...]) -> Iterator[tuple[str, str | None]]:
    for s, key_set in enumerate(key_sets):
        for k, key in enumerate(key_set.keys):
            at = f"{path}.data_key_sets[{s}].keys[{k}]"
            yield f"{at}.valid_from", key.valid_from
            yield f"{at}.valid_to", key.valid_to
            for component in key.components:
                yield from _selection(f"{at}.components[{component.component_id}]", component)


def _periods(constraint: DataConstraint | AttachmentConstraint) -> Iterator[tuple[str, str | None]]:
    path = f"{type(constraint).__name__}:{constraint.id}"
    if isinstance(constraint, DataConstraint):
        for r, region in enumerate(constraint.regions):
            for kv in region.keys:
                yield from _selection(f"{path}.regions[{r}].keys[{kv.dimension_id}]", kv)
            for component in region.components:
                yield from _selection(f"{path}.regions[{r}].components[{component.component_id}]", component)
    yield from _key_sets(path, constraint.data_key_sets)


def zoneless_periods(constraint: DataConstraint | AttachmentConstraint) -> list[ValidationIssue]:
    """One issue per validity date-time in *constraint* with no zone; a time range's bounds are exempt."""
    return [
        ValidationIssue(
            path=path,
            message=(
                f"{text!r} is a date-time with no time zone, which SDMX-JSON cannot write; "
                "give it a zone, or write SDMX-ML"
            ),
        )
        for path, text in _periods(constraint)
        if text is not None and is_zoneless_date_time(text)
    ]
