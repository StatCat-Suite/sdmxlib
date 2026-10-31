"""Shared core for the SDMX-JSON 1.0 / 2.0 streaming data writers.

The two spec versions ship near-identical observation-map and
``structures.{dimensions,attributes}`` shapes — the difference is only
the surrounding envelope (1.0 = ``{header, structure, dataSets}``;
2.0 = ``{meta, data: {dataSets, structures}}``). This module owns the
shared bits: the lazy id→position table and the per-batch encoder.
The version-specific writers wrap these with their envelope-shape
deltas.
"""

import calendar
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal, NamedTuple, TypedDict

import msgspec
import polars as pl
import pyarrow as pa

from sdmxlib.formats._arrow import frame_of
from sdmxlib.formats._coerce import to_int
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.codelist import Code, CodelistLike, codes_for_all
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import Component, DataStructure, Dimension
from sdmxlib.model.errors import SdmxParseError
from sdmxlib.model.istring import InternationalString, normalize_tag
from sdmxlib.model.ref import Ref

from ._dto import ENCODER, _camel_rename
from .structure_values import AnnotationJson, annotation_dict

ComponentKind = Literal["dimension", "attribute"]


_PERIOD_ANNUAL = re.compile(r"^(\d{4})$")
_PERIOD_MONTHLY = re.compile(r"^(\d{4})-(\d{2})$")
_PERIOD_DAILY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_PERIOD_QUARTERLY = re.compile(r"^(\d{4})-Q([1-4])$")
_PERIOD_SEMESTER = re.compile(r"^(\d{4})-S([12])$")


def _period_bounds(period_id: str) -> tuple[str, str] | None:
    """Compute ``(start, end)`` ISO-8601 timestamps for a TIME_PERIOD id.

    Returns ``None`` for unrecognised shapes — including SDMX's
    ``<start>/<duration>`` form (``"2021-01-01/P5Y"``), which the
    downstream consumer (e.g. ``.Stat``'s ``refineTimePeriod``) parses
    itself. Output format ``YYYY-MM-DDTHH:MM:SS`` **without a zone**, which
    the schema's ``date-time`` requires — a declared deviation (#496). The
    consumer these bounds exist for, .Stat's ``refineTimePeriod``, parses
    them with ``date-fns.parseISO`` and then adds the local UTC offset back
    (``dateWithTZ``), which is right only for a zoneless value: given
    ``2021-01-01T00:00:00Z`` it shows 2020-12-31 19:00 in Toronto. The NSI web
    service writes them zoneless too. The schema tests excuse exactly this
    (``tests/support/json_schema.py:is_declared_time_bound``) and nothing else.

    Supported shapes:

    - ``YYYY``        annual            (``"2021"`` → 2021-01-01 .. 2021-12-31)
    - ``YYYY-MM``     monthly           (last day computed via calendar.monthrange)
    - ``YYYY-MM-DD``  daily
    - ``YYYY-Qn``     quarterly         (Q1=Jan-Mar, Q2=Apr-Jun, Q3=Jul-Sep, Q4=Oct-Dec)
    - ``YYYY-Sn``     semester (half)   (S1=Jan-Jun, S2=Jul-Dec)

    Multi-year periods (``A5``, ``A10``) need extra context that's not
    in the period id alone — that's a follow-up gated on a frequency
    resolver. For now ``"2021"`` always means a single calendar year.
    """
    m = _PERIOD_DAILY.match(period_id)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{mo}-{d}T00:00:00", f"{y}-{mo}-{d}T23:59:59"
    m = _PERIOD_MONTHLY.match(period_id)
    if m:
        y, mo = m.groups()
        if not 1 <= int(mo) <= 12:
            return None
        last = calendar.monthrange(int(y), int(mo))[1]
        return f"{y}-{mo}-01T00:00:00", f"{y}-{mo}-{last:02d}T23:59:59"
    m = _PERIOD_QUARTERLY.match(period_id)
    if m:
        y, q = m.groups()
        start_month = (int(q) - 1) * 3 + 1
        end_month = start_month + 2
        last = calendar.monthrange(int(y), end_month)[1]
        return (
            f"{y}-{start_month:02d}-01T00:00:00",
            f"{y}-{end_month:02d}-{last:02d}T23:59:59",
        )
    m = _PERIOD_SEMESTER.match(period_id)
    if m:
        y, s = m.groups()
        start_month = 1 if s == "1" else 7
        end_month = 6 if s == "1" else 12
        last = calendar.monthrange(int(y), end_month)[1]
        return (
            f"{y}-{start_month:02d}-01T00:00:00",
            f"{y}-{end_month:02d}-{last:02d}T23:59:59",
        )
    m = _PERIOD_ANNUAL.match(period_id)
    if m:
        y = m.group(1)
        return f"{y}-01-01T00:00:00", f"{y}-12-31T23:59:59"
    return None


# ── FREQ-driven multi-period bounds (issue #149) ──────────────────────────────
#
# A bare TIME_PERIOD id like ``"2021"`` is only a *start anchor*; the period's
# length comes from the FREQ dimension's code (``A`` = 1 year, ``A5`` = 5 years,
# ``Q`` = 1 quarter, ...). ``_period_bounds`` alone treats ``"2021"`` as a single
# calendar year, which under-reports coverage for multi-year frequencies. The
# writer resolves each FREQ code to a duration — an explicit ``duration``
# annotation on the code overrides the standard/``A{n}`` convention — and uses it
# to extend the end of the period. We only ever *extend* past the id-shape bound,
# never shrink below it, so standard single-period frequencies and inconsistent
# data stay byte-identical to the id-shape default.


class _Duration(NamedTuple):
    """A calendar duration decomposed into addable parts (weeks fold into days)."""

    years: int = 0
    months: int = 0
    days: int = 0
    hours: int = 0


# Implied duration of the standard single-letter SDMX frequency codes.
_FREQ_UNIT: dict[str, _Duration] = {
    "A": _Duration(years=1),
    "Y": _Duration(years=1),
    "S": _Duration(months=6),
    "Q": _Duration(months=3),
    "M": _Duration(months=1),
    "W": _Duration(days=7),
    "D": _Duration(days=1),
    "H": _Duration(hours=1),
}
# ``A5`` / ``A10`` / ``S2`` / ... — a standard unit times a positive multiple.
_FREQ_MULTIPLE = re.compile(r"^([AYSQMWDH])(\d+)$")
# ISO-8601 duration subset we accept on a ``duration`` annotation.
_ISO_DURATION = re.compile(r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?)?$")


def _parse_iso_duration(text: str) -> _Duration | None:
    """Parse the ISO-8601 duration subset (``P5Y``, ``P1M``, ``PT1H``, ...)."""
    m = _ISO_DURATION.match(text.strip())
    if not m or not any(m.groups()):
        return None
    y, mo, w, d, h = (int(g) if g else 0 for g in m.groups())
    return _Duration(years=y, months=mo, days=w * 7 + d, hours=h)


def _convention_duration(freq_id: str) -> _Duration | None:
    """Duration implied by a FREQ code id via the standard/``A{n}`` convention."""
    unit = _FREQ_UNIT.get(freq_id)
    if unit is not None:
        return unit
    m = _FREQ_MULTIPLE.match(freq_id)
    if m is None:
        return None
    base = _FREQ_UNIT[m.group(1)]
    n = int(m.group(2))
    return _Duration(years=base.years * n, months=base.months * n, days=base.days * n, hours=base.hours * n)


def _first_text(istr: InternationalString) -> str | None:
    """First localized value of an InternationalString, or None if empty."""
    return next(iter(istr._backing().values()), None)


def _annotation_duration(annotations: Annotations, lang: str) -> _Duration | None:
    """Duration from a ``duration`` annotation (case-insensitive type), if any."""
    for ann in annotations:
        if ann.type and ann.type.strip().lower() == "duration":
            text = ann.text._text(lang) or _first_text(ann.text)
            if text:
                return _parse_iso_duration(text)
    return None


def _frequency_dimension(dsd: DataStructure) -> Dimension | None:
    """The DSD's frequency dimension, by well-known id then concept role."""
    for dim in dsd.dimensions:
        if dim.id in ("FREQ", "FREQUENCY"):
            return dim
    for dim in dsd.dimensions:
        roles = getattr(dim, "concept_role", ()) or ()
        if any(str(r).upper() in ("FREQ", "FREQUENCY") for r in roles):
            return dim
    return None


def frequency_dimension_id(dsd: DataStructure) -> str | None:
    """The id of the DSD's frequency dimension, or None if it has none.

    Only the id. The FREQ codes' ``duration`` annotations are read by
    `ComponentTable` from the codes a message actually observed, in the same
    batched lookup that labels them — this used to materialise the whole FREQ
    codelist up front, one of the per-message full reads #410 removed.
    """
    freq = _frequency_dimension(dsd)
    return freq.id if freq is not None else None


def _add_duration(start: datetime, duration: _Duration) -> datetime:
    """Add a `_Duration` to *start*, clamping the day to the target month."""
    total_months = start.year * 12 + (start.month - 1) + duration.years * 12 + duration.months
    year, month0 = divmod(total_months, 12)
    month = month0 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    shifted = start.replace(year=year, month=month, day=day)
    return shifted + timedelta(days=duration.days, hours=duration.hours)


def _bounds_from_anchor(period_id: str, duration: _Duration | None) -> tuple[str, str] | None:
    """``(start, end)`` for *period_id* extended by *duration*.

    ``start`` is the id's anchor (reusing `_period_bounds`); ``end`` is
    ``start + duration - 1s``. Returns the plain id-shape bounds when *duration*
    is None, and None when the id shape itself is unrecognised (e.g. the
    ``<start>/<duration>`` form the consumer parses itself).
    """
    base = _period_bounds(period_id)
    if base is None or duration is None:
        return base
    start_dt = datetime.fromisoformat(base[0])
    end_dt = _add_duration(start_dt, duration) - timedelta(seconds=1)
    return base[0], end_dt.strftime("%Y-%m-%dT%H:%M:%S")


class AnnotationPool:
    """The message's ``structure.annotations``, and the indices into it (#366).

    SDMX-JSON does not repeat an annotation object on every value that carries
    it. The structure block holds one array of annotation *objects*, and each
    value or component references them by integer index — the schema types
    ``dimensions[].annotations`` and ``values[].annotations`` as
    ``array of integer``, and only ``structure.annotations`` as the objects.

    One pool per structure block, so it is shared by the dimension and
    attribute tables: an index means nothing without knowing which array it
    indexes, and there is only one.

    Interned on the **rendered** form rather than the `Annotation` object.
    `Annotation` is `@define`, so it has value equality but no hash, and what
    matters on the wire is whether two annotations serialise the same — which
    is what the key tests directly. `annotation_dict` builds its keys in a
    fixed order, so equal inputs give equal bytes.
    """

    __slots__ = ("_index", "_items")

    def __init__(self) -> None:
        self._index: dict[bytes, int] = {}
        self._items: list[AnnotationJson] = []

    def register(self, annotations: Annotations | None) -> list[int]:
        """Add *annotations* to the pool, returning their indices in order.

        Returns an empty list for an artefact with none, which callers use to
        decide whether to emit the key at all — an empty ``annotations: []`` is
        noise on every value in the message.
        """
        if not annotations:
            return []
        out: list[int] = []
        for ann in annotations:
            rendered = annotation_dict(ann)
            key = ENCODER.encode(rendered)
            idx = self._index.get(key)
            if idx is None:
                idx = len(self._items)
                self._index[key] = idx
                self._items.append(rendered)
            if idx not in out:
                # One artefact carrying the same annotation twice references
                # it once. Repeating the index says nothing the first does not.
                out.append(idx)
        return out

    def to_json(self) -> list[AnnotationJson]:
        """The pool, in index order. Call after every `register`."""
        return self._items

    def __bool__(self) -> bool:
        """True when anything was registered — the writers omit an empty pool."""
        return bool(self._items)


class ComponentTable:
    """Lazy id → position table for one component kind (dim or attr).

    Built incrementally as observation rows are encountered. Positions
    are assigned strictly in arrival order — codelists supplied by the
    DSD seed only a *label cache* (code id → localized name) so the
    writer can emit nice names for observed codes; they do NOT
    pre-populate ``_positions``. That keeps the emitted
    ``structures.dimensions.observation[*].values`` array scoped to
    codes that actually appear in the streamed observations (the
    SDMX-JSON spec explicitly allows this) rather than the full
    enumeration. The StatCan census geography codelist (579k codes)
    would otherwise blow up a ``limit=2`` data response to >30 MB.

    **The cost follows the response too, not just its size.** A value's
    name, ``parent`` and annotations are filled in at `to_json`, by one
    `CodelistLike.codes_for` per component over exactly the ids observed.
    Calling `materialize` here instead read every code of every enumerated
    component on every message once the DSD came from a `LocalRegistry` —
    1.4 s to write one observation against a 200k-code geography, where the
    same DSD with eager codelists took 3 ms (#410). Looking each code up as it
    arrived would be one round trip per distinct value, which is the same
    mistake at the other end of the scale.
    """

    def __init__(
        self,
        component_ids: list[str],
        dsd: DataStructure,
        *,
        kind: ComponentKind,
        lang: str,
        annotations: AnnotationPool,
        time_dim_id: str | None = None,
        freq_dim_id: str | None = None,
    ) -> None:
        self._ids = component_ids
        # Shared with the sibling table — see `AnnotationPool`. Required rather
        # than defaulted: a table quietly building its own pool would emit
        # indices into an array that never reaches the message.
        self._annotations = annotations
        # component id -> its own annotation indices, resolved once here since
        # the component set is fixed at construction.
        self._component_annotations: dict[str, list[int]] = {}
        self._lang = normalize_tag(lang)
        self._kind = kind
        self._time_dim_id = time_dim_id
        # FREQ-driven period bounds (issue #149): the frequency dimension id,
        # its observed codes' annotation-derived duration overrides (filled
        # by `_resolve_codes`, alongside their labels), and the accumulated
        # TIME_PERIOD id → set of co-occurring FREQ ids seen while streaming.
        # Bounds are resolved at to_json() time from these.
        self._freq_dim_id = freq_dim_id
        self._freq_overrides: dict[str, _Duration] = {}
        self._time_freqs: dict[str, set[str]] = {}
        # ``dsd.dimensions`` is ItemList[Dimension] and does NOT include
        # the time dimension (which lives at ``dsd.time_dimension``).
        # Use ``key_dimensions`` so TIME_PERIOD's concept-derived name
        # is picked up like every other dim. Without this, TIME_PERIOD's
        # name falls back to the id (see CPT envelope-names report).
        components: dict[str, Component]
        if kind == "dimension":
            components = {d.id: d for d in dsd.key_dimensions}
        else:
            components = {a.id: a for a in dsd.attributes}
        self._names: dict[str, str] = {}
        # per-component position table: code_id → integer position
        # (purely arrival-order, never pre-seeded from the codelist)
        self._positions: dict[str, dict[str, int]] = {cid: {} for cid in component_ids}
        # mirror of ``_positions`` with values pre-stringified — saves a
        # ``str(int)`` call per cell in the writer's hot loop, which at
        # 100k rows by N dims is the biggest measurable Python overhead.
        self._position_str: dict[str, dict[str, str]] = {cid: {} for cid in component_ids}
        # observed-code metadata: same insertion order as ``_positions``,
        # so position N maps to the Nth-inserted entry. ``to_json``
        # walks this directly.
        self._codes: dict[str, dict[str, dict[str, object]]] = {cid: {} for cid in component_ids}
        # Per-component resolved codelists, held as they came — eager or lazy
        # — and never walked: `_resolve_codes` asks each for the observed ids
        # only. For census-scale codelists (CL_GEOGRAPHY: 579k codes) the walk
        # dominated wall time on small data responses, first as a label cache
        # and again as a per-message `materialize()` (#410).
        self._codelists: dict[str, CodelistLike] = {}

        for cid in component_ids:
            comp = components.get(cid)
            if comp is None:
                continue
            name = cid
            if isinstance(comp.concept, Ref) and comp.concept.resolved:
                cname = comp.concept().name._text(self._lang)
                if cname:
                    name = cname
            self._names[cid] = name
            # TimeDimension carries a Facet representation, not a codelist
            # ref, so it has no ``resolved_codelist`` property — skip the
            # codelist registration for components without one.
            # getattr returns Any; the annotation kills it on this line.
            cl: CodelistLike | None = getattr(comp, "resolved_codelist", None)
            if cl is not None:
                self._codelists[cid] = cl
            comp_anns = self._annotations.register(comp.annotations)
            if comp_anns:
                self._component_annotations[cid] = comp_anns

    def position(self, component_id: str, code_id: str) -> int:
        """Look up ``code_id`` under ``component_id``, assigning on first sight."""
        table = self._positions[component_id]
        pos = table.get(code_id)
        if pos is not None:
            return pos
        pos = len(table)
        table[code_id] = pos
        self._position_str[component_id][code_id] = str(pos)
        # Both SDMX-JSON 1.0 and 2.0 schemas require every value entry to carry
        # id + name. The id stands in for the name until `_resolve_codes`
        # replaces it, and stays for a value the codelist does not hold (or a
        # component with no codelist).
        self._codes[component_id][code_id] = {"id": code_id, "name": code_id}
        return pos

    def _resolve_codes(self) -> None:
        """Fill each observed value's name, ``parent`` and annotations.

        Every enumerated component's codes in one `codes_for_all`, over the ids
        this message observed, which is why it is deferred to here (#410). For
        a DSD from a store that is one read, not three statements per component
        (#441). Annotations are registered in position order, so the pool's
        indices are deterministic for a given input.
        """
        requests = {cid: (cl, self._codes[cid].keys()) for cid, cl in self._codelists.items() if self._codes[cid]}
        for component_id, found in codes_for_all(requests).items():
            entries = self._codes[component_id]
            for code_id, entry in entries.items():
                code = found.get(code_id)
                if code is None:
                    continue
                if component_id == self._freq_dim_id:
                    duration = _annotation_duration(code.annotations, self._lang)
                    if duration is not None:
                        self._freq_overrides[code_id] = duration
                self._fill_entry(entry, code)

    def _fill_entry(self, entry: dict[str, object], code: Code) -> None:
        """Write one resolved *code*'s name, ``parent`` and annotations into *entry*."""
        entry["name"] = code.name._text(self._lang) or code.id
        # A value's hierarchy edge (#365). `parent` is a first-class property of
        # this object in the 1.0, 2.0 and 2.1 data schemas, and omitting it made
        # every hierarchical codelist arrive flat: the .Stat Data Explorer groups
        # row headers with
        #     groupBy(v => v.parent && has(v.parent, indexed) ? v.parent : '#ROOT')
        # so with no `parent` every value lands at `#ROOT` and the table renders
        # one flat list even when the codelist is a real tree.
        #
        # Omitted rather than emitted as null when the code is a root, which is
        # what the sibling *structure* writer already does — see
        # `structure_values.serialize_code`. The two writers held the same `Code`
        # and disagreed about it until now.
        #
        # Emitted unconditionally, without checking the schema's
        # `^[A-Za-z0-9_@$-]+$`: that pattern is character-for-character SDMX-ML's
        # `IDType`, so an id it rejects was already invalid SDMX upstream. Adding
        # a filter here would turn someone else's malformed codelist into a
        # clean-looking document with a silently missing hierarchy.
        if code.parent_id is not None:
            entry["parent"] = code.parent_id
        # The code's annotations, as indices into the shared pool (#366). This
        # is how a consumer gets a value's ORDER, NOT_DISPLAYED and the rest —
        # the *type* vocabulary is the annotation creator's, not SDMX's ("the
        # types are not enumerated, as these can be specified by the user or
        # creator", per the AnnotationType documentation in every SDMX-ML
        # version), so the writer emits all of them and filters none.
        value_anns = self._annotations.register(code.annotations)
        if value_anns:
            entry["annotations"] = value_anns

    def tracks_freq(self) -> bool:
        """True if both a time dimension and a frequency dimension are known."""
        return self._time_dim_id is not None and self._freq_dim_id is not None

    def observe_batch_time_freq(self, df: pl.DataFrame, dim_cols: list[str], dim_phys: list[str]) -> None:
        """Record the distinct ``(TIME_PERIOD, FREQ)`` pairs in *df*.

        Cheap per-batch vectorised ``unique()`` — accumulated across batches so
        `_resolve_time_bounds` can pick each period's duration from the
        frequencies it actually co-occurs with.
        """
        # Bind to locals so the None-guard narrows `str | None` → `str` for the
        # `dim_cols.index(...)` calls below (equivalent to `tracks_freq()`, but
        # the method call can't narrow the attributes).
        time_id, freq_id = self._time_dim_id, self._freq_dim_id
        if time_id is None or freq_id is None or time_id not in dim_cols or freq_id not in dim_cols:
            return
        time_col = dim_phys[dim_cols.index(time_id)]
        freq_col = dim_phys[dim_cols.index(freq_id)]
        pairs = df.select(
            pl.col(time_col).cast(pl.String).fill_null("").alias("__t"),
            pl.col(freq_col).cast(pl.String).fill_null("").alias("__f"),
        ).unique()
        times = pairs.get_column("__t").to_list()
        freqs = pairs.get_column("__f").to_list()
        for time_id, freq_id in zip(times, freqs, strict=True):
            self._time_freqs.setdefault(time_id, set()).add(freq_id)

    def _duration_for_freq(self, freq_id: str) -> _Duration | None:
        """Duration for a FREQ code: annotation override else convention."""
        return self._freq_overrides.get(freq_id) or _convention_duration(freq_id)

    def _resolve_time_bounds(self, code_id: str) -> tuple[str, str] | None:
        """Period bounds for a TIME_PERIOD id, extended by FREQ where unambiguous.

        Only extends past the plain id-shape bound — never shrinks below it — so
        standard single-period frequencies and contradictory data stay identical
        to the id-shape default. A period that co-occurs with conflicting
        durations (mixed FREQ) falls back to the id-shape default.
        """
        base = _period_bounds(code_id)
        freqs = self._time_freqs.get(code_id)
        if base is None or not freqs:
            return base
        durations = {self._duration_for_freq(f) for f in freqs}
        durations.discard(None)
        if len(durations) != 1:
            return base
        extended = _bounds_from_anchor(code_id, next(iter(durations)))
        if extended is None:
            return base
        return extended if extended[1] > base[1] else base

    def register_many(self, component_id: str, code_ids: list[str]) -> dict[str, str]:
        """Ensure every *code_id* has a position; return a focused str-map.

        Returns a fresh dict scoped to exactly the supplied *code_ids*,
        not the global per-component position table. That matters when
        the component's codelist is large (StatCan census geography:
        ~78k codes) but each batch only references a few thousand —
        Polars ``replace_strict`` runs against a small mapping rather
        than the full enumeration.
        """
        positions = self._positions[component_id]
        pos_str = self._position_str[component_id]
        for cid in code_ids:
            if cid not in positions:
                self.position(component_id, cid)
        return {cid: pos_str[cid] for cid in code_ids}

    def to_json(self) -> list[dict[str, object]]:
        """Render the per-position metadata array for the structures block.

        Call once, after the last batch: this is where observed values are
        resolved against their codelists.
        """
        self._resolve_codes()
        out: list[dict[str, object]] = []
        for key_pos, cid in enumerate(self._ids):
            code_entries = self._codes[cid]
            if cid == self._time_dim_id:
                for code_id, value_entry in code_entries.items():
                    bounds = self._resolve_time_bounds(code_id)
                    if bounds is not None:
                        value_entry["start"], value_entry["end"] = bounds
            entry: dict[str, object] = {
                "id": cid,
                "name": self._names.get(cid, cid),
                "values": list(code_entries.values()),
            }
            comp_anns = self._component_annotations.get(cid)
            if comp_anns:
                entry["annotations"] = comp_anns
            if self._kind == "dimension":
                entry["keyPosition"] = key_pos
            else:
                # Attribute schema (both 1.0 and 2.0) requires a relationship.
                # We currently only emit observation-level attributes, so the
                # relationship is the empty observation marker.
                observation_marker: dict[str, object] = {}
                entry["relationship"] = {"observation": observation_marker}
            out.append(entry)
        return out


def resolve_columns(
    dsd: DataStructure,
) -> tuple[list[str], list[str], list[str], list[str], str, str]:
    """The columns both writers read, in SDMX order, from the structure.

    Took a `DataflowBinding` and mapped every component id to the producer's
    physical column. It reads the **DSD** now, because an observation query
    projects by component id — so the physical names never reach a writer, and
    asking the binding for them produced a `KeyError` naming columns the batch
    correctly did not have.

    Returns ``(dim_cols, dim_phys, attr_cols, attr_phys, measure_id, measure_col)``.
    The `_phys` pair is retained, equal to its id counterpart: the distinction
    is real for a writer fed from somewhere other than a query, and collapsing
    the signature would hide that it exists.

    Raises:
        ValueError: If the structure declares anything other than exactly one
            measure. Multi-measure DSDs require a different value-array
            layout and are deferred to a follow-up.
    """
    dim_cols = [d.id for d in dsd.key_dimensions]
    attr_cols = [a.id for a in dsd.attributes]
    measure_cols = [m.id for m in dsd.measures]
    if len(measure_cols) != 1:
        msg = f"SDMX-JSON data writer currently requires exactly one measure, got {len(measure_cols)}: {measure_cols!r}"
        raise ValueError(msg)
    measure_id = measure_cols[0]
    # Component id *is* the column name: an observation query projects by id,
    # so a batch never carries the binding's physical names. The two lists stay
    # separate because the id and the column were once different things — and
    # a writer fed from somewhere other than a query would want the mapping
    # back.
    return dim_cols, list(dim_cols), attr_cols, list(attr_cols), measure_id, measure_id


def encode_batch(
    batch: pa.RecordBatch,
    *,
    dim_cols: list[str],
    dim_phys: list[str],
    attr_cols: list[str],
    attr_phys: list[str],
    measure_col: str,
    dim_lookup: ComponentTable,
    attr_lookup: ComponentTable,
    first_obs: bool,
) -> bytes:
    """Encode one Arrow batch into a chunk of ``"key": [...]`` observation entries.

    Hot path: column-oriented Polars work for the things that vectorise
    (key string construction, attribute position lookups) and one
    msgspec encode over the whole batch's ``{key: row}`` dict. The
    surrounding ``{}`` braces are stripped because the caller is
    splicing this chunk into a streamed object whose braces it owns.

    Raises:
        KeyError: If a batch is missing a component the structure declares.
    """
    missing = [c for c in (*dim_phys, *attr_phys, measure_col) if c not in batch.schema.names]
    if missing:
        msg = (
            f"Record batch is missing column(s) {missing!r} declared by "
            f"the structure. Available columns: {batch.schema.names}"
        )
        raise KeyError(msg)

    # `frame_of`, not the `pl.DataFrame` constructor: 2.4x on the conversion (#427).
    df = frame_of(batch)
    n = df.height
    if n == 0:
        return b""

    # ── Step 1: register every observed code with the lazy lookup ────
    # Done once per batch on unique values (cheap) so step 2 can use a
    # complete value→position-string map for a vectorised replace.
    dim_str_tables: list[dict[str, str]] = []
    for col_idx, cid in enumerate(dim_cols):
        uniq = df.get_column(dim_phys[col_idx]).cast(pl.String).fill_null("").unique(maintain_order=True).to_list()
        dim_str_tables.append(dim_lookup.register_many(cid, uniq))

    # Accumulate (TIME_PERIOD, FREQ) co-occurrence for duration-aware bounds.
    dim_lookup.observe_batch_time_freq(df, dim_cols, dim_phys)

    attr_int_tables: list[dict[str, int]] = []
    for col_idx, cid in enumerate(attr_cols):
        uniq = df.get_column(attr_phys[col_idx]).cast(pl.String).drop_nulls().unique(maintain_order=True).to_list()
        attr_lookup.register_many(cid, uniq)
        positions = attr_lookup._positions[cid]
        attr_int_tables.append({code_id: positions[code_id] for code_id in uniq})

    # ── Step 2: vectorised colon-joined key column via Polars ────────
    key_exprs = [
        pl.col(dim_phys[i]).cast(pl.String).fill_null("").replace_strict(dim_str_tables[i])
        for i in range(len(dim_cols))
    ]
    keys = df.select(pl.concat_str(key_exprs, separator=":").alias("__key")).get_column("__key").to_list()

    # ── Step 3: value arrays per row (Python loop bounded by n_rows times n_attrs)
    measure_vals = df.get_column(measure_col).to_list()
    if not attr_cols:
        rows: list[list[object]] = [[m] for m in measure_vals]
    else:
        attr_value_lists = [df.get_column(attr_phys[i]).to_list() for i in range(len(attr_cols))]
        rows = []
        for i in range(n):
            row: list[object] = [measure_vals[i]]
            for col_idx in range(len(attr_cols)):
                raw = attr_value_lists[col_idx][i]
                row.append(None if raw is None else attr_int_tables[col_idx][str(raw)])
            rows.append(row)

    # ── Step 4: one msgspec encode over the whole batch, strip braces ──
    # ``{key1:[...], key2:[...], ...}`` with the leading ``{`` and
    # trailing ``}`` removed becomes a valid chunk to splice into the
    # caller's streamed observations map. Polars ``.to_list()`` already
    # yields Python-native scalars (int/float/str/None), so no numpy
    # encode hook is needed (#179).
    payload = ENCODER.encode(dict(zip(keys, rows, strict=True)))
    body = payload[1:-1]  # strip outer { and }
    if first_obs or not body:
        return body
    return b"," + body


def dataflow_link(dataflow: Ref[Dataflow]) -> bytes:
    """Serialise the dataSet ``links[]`` entry pointing at the bound dataflow."""
    return ENCODER.encode(
        {
            "urn": str(dataflow.urn),
            "rel": "dataflow",
        }
    )


def dataflow_annotations(pool: AnnotationPool, dataflow: Ref[Dataflow]) -> list[int]:
    """Pool the bound dataflow's annotations, returning the ``dataSets[].annotations`` indices.

    A data message wraps a dataflow, and the dataflow's annotations are part of
    what a consumer needs to present it: .Stat's Data Explorer reads
    ``NOT_DISPLAYED`` (attribute values to hide in the table) from this pool,
    and the .Stat NSI web service puts the dataflow's annotations here with the
    dataSet referencing them. Call it before anything else registers so the
    indices are known when the dataSet head is written, ahead of the
    observations; an unresolved ref contributes nothing.
    """
    if not dataflow.resolved:
        return []
    return pool.register(dataflow().annotations)


class DisplayNamesJson(TypedDict, total=False):
    """The up-to-four localised name fields spliced into an envelope slot.

    Every key is optional: a missing field is simply absent rather than null,
    which is what keeps the envelope tidy.
    """

    name: str
    names: dict[str, str]
    description: str
    descriptions: dict[str, str]


def display_names(
    dataflow: Ref[Dataflow],
    dsd: DataStructure,
    lang: str,
) -> DisplayNamesJson:
    """Localized name fields to splice into envelope-level JSON objects.

    Returns a dict with up to four keys: ``name``, ``names``,
    ``description``, ``descriptions``. The caller spreads this into the
    relevant envelope slot (``header`` / ``meta`` / ``structure`` /
    ``structures[0]`` / ``dataSets[0]``); missing fields are simply
    absent rather than null, which keeps the envelope tidy.

    Source preference: the bound dataflow's ``name`` /
    ``description`` (when its Ref is resolved), falling back to the
    DSD's. SDMX-JSON data messages conceptually wrap a *dataflow*, so
    consumers like .Stat read the dataflow's display name to title
    the dataset — falling through to the DSD only matters when the
    caller passed ``Ref.unresolved(...)``.

    Raises:
        ValueError: If `lang` is not a well-formed language tag.
    """
    lang = normalize_tag(lang)
    # Either the resolved Dataflow or the DSD — both are nameable, and the
    # dataflow wins when it has a name (SDMX-JSON data messages wrap a flow).
    src: Dataflow | DataStructure = dsd
    if dataflow.resolved:
        flow = dataflow()
        if flow.name:
            src = flow

    out: DisplayNamesJson = {}
    name_attr: InternationalString | None = getattr(src, "name", None)
    if name_attr:
        localized = name_attr._text(lang)
        if localized:
            out["name"] = localized
        names_map = name_attr.as_dict()
        if names_map:
            out["names"] = names_map

    desc_attr: InternationalString | None = getattr(src, "description", None)
    if desc_attr:
        d_localized = desc_attr._text(lang)
        if d_localized:
            out["description"] = d_localized
        descs_map = desc_attr.as_dict()
        if descs_map:
            out["descriptions"] = descs_map
    return out


# ── reader-side helpers ──────────────────────────────────────────────────────

# ── data-message read shapes ─────────────────────────────────────────────────
#
# The *declaration* half of a data message has a fixed shape in both SDMX-JSON
# 1.0 and 2.0, so it decodes into Structs. The *observation* half stays
# `list[Any]`: an observation array holds a measure of arbitrary JSON type
# followed by integer attribute positions, which is heterogeneous by nature
# rather than by omission.
#
# msgspec ignores unknown keys, so the extra fields our own writer emits
# (`keyPosition`, `relationship`, the time-bound `start`/`end`) are tolerated
# without being modelled.


class ValueEntryJson(msgspec.Struct):
    """One entry of a component declaration's ``values`` array.

    Shaped ``{id, name}`` for the enumerated case, or just ``{name}`` /
    ``{value}`` for the free-text or literal case (attributes like TITLE,
    or time periods).
    """

    id: str | None = None
    name: str | None = None
    value: str | int | float | bool | None = None


class ComponentDeclJson(msgspec.Struct):
    """One dimension or attribute declaration in the structures block."""

    id: str
    values: list[ValueEntryJson] = msgspec.field(default_factory=list)


class ComponentLevelsJson(msgspec.Struct, rename=_camel_rename):
    """The dataSet / series / observation split of a component declaration."""

    data_set: list[ComponentDeclJson] = msgspec.field(default_factory=list)
    series: list[ComponentDeclJson] = msgspec.field(default_factory=list)
    observation: list[ComponentDeclJson] = msgspec.field(default_factory=list)


class LinkJson(msgspec.Struct):
    """One ``links[]`` entry. Only what names the dataflow is read."""

    rel: str | None = None
    urn: str | None = None


class StructureBlockJson(msgspec.Struct):
    """A structures block — ``structure`` in 1.0, one of ``data.structures`` in 2.0."""

    dimensions: ComponentLevelsJson = msgspec.field(default_factory=ComponentLevelsJson)
    attributes: ComponentLevelsJson = msgspec.field(default_factory=ComponentLevelsJson)
    links: list[LinkJson] = msgspec.field(default_factory=list)


class DataSetBlockJson(msgspec.Struct):
    """One ``dataSets[]`` entry.

    ``observations`` maps a colon-joined position key to an array whose first
    slot is the measure and whose remaining slots are attribute positions.
    ``structure`` indexes 2.0's ``data.structures``; 1.0 has one structure and
    no index. ``action`` says what a receiver does with the rows.
    """

    structure: int = 0
    action: str = "Information"
    attributes: list[object] = msgspec.field(default_factory=list)
    observations: dict[str, list[object]] = msgspec.field(default_factory=dict)
    links: list[LinkJson] = msgspec.field(default_factory=list)


#: The link relations naming what a data set follows; data sets naming two of one kind follow different structures.
_STRUCTURE_RELS = ("dataflow", "datastructure", "provisionagreement")


def _structure_links(data_set: DataSetBlockJson, structure: StructureBlockJson) -> dict[str, str]:
    """The URN each structure link relation names, the data set's link before its structure block's.

    This library's writers put the links on the data set; some providers put
    them on the structure block.
    """
    found: dict[str, str] = {}
    for link in (*data_set.links, *structure.links):
        if link.rel in _STRUCTURE_RELS and link.urn and link.rel not in found:
            found[link.rel] = link.urn
    return found


def read_data_sets(
    data_sets: Sequence[tuple[DataSetBlockJson, StructureBlockJson]],
) -> tuple[pl.DataFrame, str | None]:
    """Every data set of a message as one frame, and the dataflow they name.

    A message carries any number of data sets, each with its own structure and
    action. They read as one frame when each decodes to the same columns and
    they name at most one dataflow, data structure and provision agreement:
    two structure blocks of one data structure may list different values. A
    ``Delete`` data set is refused: its rows say what to remove. The other
    actions read as observations, and the action is dropped, as are a data
    set's ``setId``, its reporting, validity and publication dates and the
    message's ``meta``.

    Args:
        data_sets: Each data set with the structure block it follows, in
            message order; at least one.

    Returns:
        One row per observation across the data sets, ``[*dim_ids,
        OBS_VALUE, *attr_ids]``; and the ``rel="dataflow"`` link's URN, or
        `None` when no data set names one.

    Raises:
        SdmxParseError: If a data set is a ``Delete``, if two decode to
            different columns, or if they name different dataflows, data
            structures or provision agreements.
    """
    columns: tuple[list[str], list[str]] | None = None
    rows: list[list[object]] = []
    named: dict[str, set[str]] = {rel: set() for rel in _STRUCTURE_RELS}
    for position, (data_set, structure) in enumerate(data_sets):
        if data_set.action == "Delete":
            msg = f"dataSets[{position}] is a Delete data set; its rows say what to remove, not what was observed"
            raise SdmxParseError(msg)
        dim_ids, attr_ids, set_rows = decode_observations(structure, data_set)
        if columns is None:
            columns = (dim_ids, attr_ids)
        elif columns != (dim_ids, attr_ids):
            msg = (
                f"dataSets[{position}] has columns {[*dim_ids, *attr_ids]} where dataSets[0] has "
                f"{[*columns[0], *columns[1]]}; data sets of different structures do not read as one frame"
            )
            raise SdmxParseError(msg)
        rows.extend(set_rows)
        for rel, urn in _structure_links(data_set, structure).items():
            named[rel].add(urn)
    if columns is None:
        msg = "the message has no data sets"
        raise SdmxParseError(msg)
    for rel, urns in named.items():
        if len(urns) > 1:
            msg = (
                f"the data sets name {len(urns)} {rel}s ({', '.join(sorted(urns))}); "
                "data sets following different structures do not read as one frame"
            )
            raise SdmxParseError(msg)
    return rows_to_lazyframe(*columns, rows).collect(), next(iter(named["dataflow"]), None)


def _value_label(value_entry: ValueEntryJson) -> str:
    """Pick the canonical label from a structures-block value entry.

    Prefers ``id`` (the SDMX code), then ``name``, then the literal
    ``value``. The result becomes the column value in the decoded
    DataFrame.

    Args:
        value_entry: One entry of a declaration's ``values`` array.

    Returns:
        The label, or ``""`` when the entry carries none of the three.
    """
    if value_entry.id is not None:
        return value_entry.id
    if value_entry.name is not None:
        return value_entry.name
    if value_entry.value is not None:
        return str(value_entry.value)
    return ""


def decode_observations(
    structure_block: StructureBlockJson,
    dataset_block: DataSetBlockJson,
) -> tuple[list[str], list[str], list[list[object]]]:
    """Decode a flat-layout dataSet into column-oriented rows.

    Inverts the writer's encoding: observation-keyed positions are
    expanded back to ``(dim_id, code_id)`` columns plus the measure +
    attribute columns. Also handles dataSet-level dimensions and
    attributes — those carry a single value in their ``values`` array
    and are broadcast as constant columns across every observation
    (common in real-world SDMX-JSON 1.0 / 2.0 messages, where only the
    dims that vary at the obs level are encoded into the key).

    Args:
        structure_block: ``{dimensions: {dataSet|series|observation},
            attributes: {dataSet|series|observation}}``. Same shape in
            SDMX-JSON 1.0 (``structure``) and 2.0 (one of ``data.structures``).
        dataset_block: One entry from ``dataSets[]`` — provides
            ``observations`` plus any dataSet-level ``attributes`` slot.

    Returns:
        ``(dim_ids, attr_ids, rows)`` covering every dim/attr declared at
        the dataSet or observation level, in declaration order. The
        caller stitches this into a Polars frame.
    """
    dim_decl = structure_block.dimensions
    attr_decl = structure_block.attributes

    ds_dims = dim_decl.data_set
    obs_dims = dim_decl.observation
    ds_attrs = attr_decl.data_set
    obs_attrs = attr_decl.observation

    # Series-level layout is not yet exercised by our writer (flat
    # layout only). When a future series-aware writer lands, the
    # decoder will need to walk ``dataSets[].series[]`` instead of the
    # observations dict. For now, raise on series dims so consumers
    # don't see a silently truncated frame.
    if dim_decl.series or attr_decl.series:
        msg = "SDMX-JSON series layout is not supported by the reader yet — only flat layout"
        raise SdmxParseError(msg)

    ds_dim_ids = [d.id for d in ds_dims]
    obs_dim_ids = [d.id for d in obs_dims]
    ds_attr_ids = [a.id for a in ds_attrs]
    obs_attr_ids = [a.id for a in obs_attrs]

    # dataSet-level values are scalar (every dim has exactly one value).
    ds_dim_const = [_value_label(_value_at(d.values, 0)) for d in ds_dims]
    ds_attr_const = _decode_dataset_attrs(ds_attrs, dataset_block.attributes)

    obs_dim_values = [[_value_label(v) for v in d.values] for d in obs_dims]
    obs_attr_values = [[_value_label(v) for v in a.values] for a in obs_attrs]

    observations = dataset_block.observations
    rows: list[list[object]] = []
    for key, value_array in observations.items():
        positions: list[str] = key.split(":") if key else []
        if len(positions) != len(obs_dim_ids):
            msg = (
                f"Observation key {key!r} has {len(positions)} positions but "
                f"the structure declares {len(obs_dim_ids)} observation dimensions"
            )
            raise SdmxParseError(msg)
        row: list[object] = list(ds_dim_const)
        for col_idx, pos in enumerate(positions):
            row.append(_value_at(obs_dim_values[col_idx], _wire_position(pos, what="observation key position")))

        # First slot is the measure; remaining slots are attribute positions
        # into their respective code lists (or None for unset).
        measure = value_array[0] if value_array else None
        row.append(measure)
        row.extend(ds_attr_const)
        for col_idx in range(len(obs_attr_ids)):
            raw = value_array[col_idx + 1] if col_idx + 1 < len(value_array) else None
            if raw is None:
                row.append(None)
            else:
                position = _wire_position(raw, what="observation attribute position")
                row.append(_value_at(obs_attr_values[col_idx], position))
        rows.append(row)
    return (
        [*ds_dim_ids, *obs_dim_ids],
        [*ds_attr_ids, *obs_attr_ids],
        rows,
    )


def _wire_position(raw: object, *, what: str) -> int:
    """A slot's position into a declaration's ``values`` array.

    Narrowing `raw` is what typing the observation arrays as `list[object]`
    rather than `list[Any]` forces, and the narrowing is worth having on its
    own: `int(raw)` on a payload whose slot is an array raised a bare
    `TypeError` straight out of `sdmxlib.formats.parse`, which is the very
    thing `formats/_coerce` exists to stop (#247). A malformed payload is a
    parse failure, and now says so.
    """
    if isinstance(raw, str):
        return to_int(raw, what=what)
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    msg = f"{what} must be a position into the declaration's values, got {raw!r}"
    raise SdmxParseError(msg)


def _value_at[V](values: Sequence[V], position: int) -> V:
    """The declaration's value at a wire `position`, or `SdmxParseError` when there is none.

    A position comes out of the payload, so one past the declaration's end, or
    negative, is a malformed message rather than Python's ``IndexError``.
    """
    if 0 <= position < len(values):
        return values[position]
    msg = f"position {position} is outside the declaration's {len(values)} values"
    raise SdmxParseError(msg)


def _decode_dataset_attrs(
    attr_decls: list[ComponentDeclJson],
    dataset_attr_values: list[object],
) -> list[object]:
    """Decode dataSet-level attribute values into a constant-per-row list.

    The wire shape for dataSet-level attributes is a single ``values``
    array on the dataSet itself, parallel to the attribute declaration
    order. Each slot is either an integer position into the
    declaration's ``values`` array (enumerated case) or null. We
    materialise them as code-id strings, broadcast by the caller.
    """
    out: list[object] = []
    for col_idx, attr in enumerate(attr_decls):
        raw = dataset_attr_values[col_idx] if col_idx < len(dataset_attr_values) else None
        if raw is None:
            out.append(None)
            continue
        # A slot that is not int-convertible falls through to the raw value.
        # The isinstance gate is what ``int(raw)`` raising TypeError used to
        # express implicitly, now that the slot type is `object` rather than
        # `Any` — same three outcomes, just checkable.
        if isinstance(raw, (int, str, float)):
            try:
                out.append(_value_label(attr.values[int(raw)]))
                continue
            except IndexError, ValueError:
                pass
        out.append(raw)
    return out


def rows_to_lazyframe(
    dim_ids: list[str],
    attr_ids: list[str],
    rows: list[list[object]],
    *,
    measure_id: str = "OBS_VALUE",
) -> pl.LazyFrame:
    """Stitch decoded observation rows into a Polars LazyFrame.

    Column order: ``[*dim_ids, measure_id, *attr_ids]`` — matches the
    writer's column order and the natural shape SDMX consumers expect.
    Empty inputs yield an empty frame with the declared column set.
    """
    columns = [*dim_ids, measure_id, *attr_ids]
    if len(set(columns)) != len(columns):
        repeated = sorted({c for c in columns if columns.count(c) > 1})
        msg = f"the structure declares {', '.join(repeated)} more than once"
        raise SdmxParseError(msg)
    if not rows:
        return pl.LazyFrame({c: [] for c in columns})
    data: dict[str, list[object]] = {c: [] for c in columns}
    for row in rows:
        for col, val in zip(columns, row, strict=True):
            data[col].append(val)
    # Not strict: a measure of JSON numbers `1` and `-2.25` is a Float64
    # column, which the strict default refuses as an Int64 meeting a float.
    # Text among numbers makes a String column, which the DSD's types check.
    return pl.LazyFrame([pl.Series(c, data[c], strict=False) for c in columns])
