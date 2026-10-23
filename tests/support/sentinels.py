"""Build an artefact with *every* field set to a distinguishable value.

The material for the derived round-trip guard (#316). Three bugs of one shape
have shipped — `Facet.is_multilingual`, `Concept.parent_id`,
`DataProvider.contacts` — where the field existed in the model and storage
silently dropped it. None was caught by a test, because **an absence does not
fail a test**: every existing round-trip asserts that what it put in came back,
over a fixture that did not happen to set the missing field.

So the field list is derived from `attrs.fields()` rather than written out. A
field added tomorrow is covered the day it lands; a
hand-listed version is what already exists and is what missed all three.

**Values are distinguishable, not merely valid.** `is_multilingual=False`
round-trips through a writer that never reads it, because the default is also
`False`. Every sentinel here differs from the field's default, so "came back
equal" cannot be satisfied by a writer that does nothing — which is precisely
the failure mode being hunted.

Where a field's type is one this module cannot build, it says so rather than
skipping quietly: `unsupported()` reports them, and the guard asserts the list
is one a reader can hold. A silent skip would reintroduce the hole.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, get_args, get_origin

import attrs

from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.codelist import (
    CascadeSelection,
    Codelist,
    CodelistExtension,
    CodeSelection,
    InclusionMode,
    MemberValue,
)
from sdmxlib.model.constraint import ReferencePeriod, ReleaseCalendar
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn

if TYPE_CHECKING:
    from collections.abc import Callable

#: A fixed instant, not `now()`: a guard whose expected value changes per run
#: cannot be compared against a stored fixture, and a failure that only
#: reproduces at one time of day is worse than no failure.
INSTANT = dt.datetime(2021, 2, 3, 4, 5, 6, tzinfo=dt.UTC)


class Unsupported:
    """No sentinel could be built for this field's declared type."""

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason


def _istring(name: str) -> InternationalString:
    # Two languages, because a writer that keeps only the first is a real bug
    # shape and one language cannot show it.
    return InternationalString(en=f"en {name}", fr=f"fr {name}")


def _annotations(name: str) -> Annotations:
    return Annotations(
        [
            Annotation(
                id=f"A_{name}"[:40],
                title=f"title {name}",
                type="SENTINEL",
                urls="http://example.test/a",
                text=_istring(name),
            )
        ]
    )


def _ref(name: str) -> Ref[object]:
    return Ref[object].unresolved(
        SdmxUrn(package="codelist", artefact_class="Codelist", agency="SENT", id=f"CL_{name}"[:40], version="1.0")
    )


def _extensions(_name: str) -> tuple[CodelistExtension, ...]:
    """A codelist extension carrying every part of one.

    Prefix, selection mode, member value and cascade are all distinct, because
    the storage tables keep them in two tables joined on `(codelist_urn,
    position)` — a writer that stored the extension and dropped its members
    would round-trip a plausible-looking artefact (#160).
    """
    return (
        CodelistExtension(
            codelist=Ref[Codelist].unresolved(
                SdmxUrn(package="codelist", artefact_class="Codelist", agency="SENT", id="CL_EXTENDED", version="1.0")
            ),
            prefix="SENT_",
            selection=CodeSelection(
                mode=InclusionMode.EXCLUDE,
                members=(MemberValue(value="SENTINEL_MEMBER", cascade=CascadeSelection.EXCLUDE_ROOT),),
            ),
        ),
    )


def _reference_period(_name: str) -> ReferencePeriod:
    # Distinct start and end: one column read into both fields is a live
    # failure mode and equal values could not show it.
    return ReferencePeriod(start_period=INSTANT, end_period=INSTANT + dt.timedelta(days=365))


def _release_calendar(_name: str) -> ReleaseCalendar:
    return ReleaseCalendar(periodicity="P1M", offset="P2D", tolerance="P3D")


#: Sentinel per declared type, keyed by the type's `str()` as attrs reports it.
#: Matching on the string rather than the object because attrs stores the
#: annotation as written, and `from __future__ import annotations` means many
#: are strings already.
_BY_TYPE: dict[str, Callable[[str], object]] = {
    "<class 'str'>": lambda name: f"S{name}"[:40],
    "str": lambda name: f"S{name}"[:40],
    "str | None": lambda name: f"S{name}"[:40],
    "typing.Optional[str]": lambda name: f"S{name}"[:40],
    "<class 'bool'>": lambda _name: True,
    "bool": lambda _name: True,
    "<class 'int'>": lambda _name: 7,
    "int | None": lambda _name: 7,
    "<class 'datetime.datetime'>": lambda _name: INSTANT,
    "datetime.datetime | None": lambda _name: INSTANT,
    "<class 'sdmxlib.model.istring.InternationalString'>": _istring,
    "<class 'sdmxlib.model.annotations.Annotations'>": _annotations,
    "<class 'sdmxlib.model.organisation.Agency'>": lambda _name: Agency(id="SENT"),
    "tuple[str, ...]": lambda name: (f"T{name}"[:40],),
    "dict[str, str]": lambda name: {"k": f"V{name}"[:40]},
    "dict[str, str | None]": lambda name: {"k": f"V{name}"[:40]},
    "tuple['CodelistExtension', ...]": _extensions,
    "sdmxlib.model.constraint.ReferencePeriod | None": _reference_period,
    "sdmxlib.model.constraint.ReleaseCalendar | None": _release_calendar,
}


def sentinel_for(field: attrs.Attribute[object]) -> object:
    """A distinguishable value for `field`, or `Unsupported`.

    `id` fields are special-cased: SDMX restricts them to
    `[A-Za-z0-9_@$-]+`, so the generic string sentinel would be rejected by
    validation before it ever reached storage — and a guard that cannot build
    a valid artefact reports a validation error rather than a storage gap.
    """
    declared = str(field.type)
    if field.name in {"id", "agency_id"}:
        return "SENTINEL_ID"
    if field.name == "version":
        return "9.9"
    builder = _BY_TYPE.get(declared)
    if builder is not None:
        return builder(field.name)
    if declared.startswith(("typing.Optional[sdmxlib.model.ref.Ref", "sdmxlib.model.ref.Ref")):
        return _ref(field.name)
    origin = get_origin(field.type)
    if origin is not None and get_args(field.type):
        return Unsupported(f"generic {declared}")
    return Unsupported(f"no sentinel for {declared}")


def build(cls: type) -> tuple[object, dict[str, object], list[str]]:
    """An instance of `cls` with every buildable field set.

    Returns:
        The instance, the sentinel values keyed by field name, and the names
        of fields no sentinel could be built for — reported rather than
        skipped, so the coverage this guard actually has stays visible.
    """
    values: dict[str, object] = {}
    skipped: list[str] = []
    for field in attrs.fields(cls):
        if not field.init:
            continue
        value = sentinel_for(field)
        if isinstance(value, Unsupported):
            skipped.append(field.name)
            continue
        values[field.name] = value
    init_names = {f.name.lstrip("_"): f.name for f in attrs.fields(cls) if f.init}
    kwargs = {init_names.get(name, name).lstrip("_"): value for name, value in values.items()}
    return cls(**kwargs), values, skipped
