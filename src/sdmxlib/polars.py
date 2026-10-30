"""Polars integration for sdmxlib.

Schema generation, validation, and label views over polars frames — used by
`ObsQuery`'s terminals to type, label and validate observations, and
importable directly for a frame read some other way::

    import sdmxlib.polars as slpl

    schema = slpl.schema(dsd)
    df = pl.read_csv("data.csv", schema_overrides=schema)
    report = slpl.validation_report(dsd, df)
    labelled = slpl.label(df.lazy(), dsd, lang="en").collect()
    meta = slpl.structure_frame(dsd, lang="en")

A file read through a registry is an `ObsQuery` instead: `reg.scan_csv(path)`.
"""

from collections.abc import Mapping, Sequence
from typing import Literal, Protocol, runtime_checkable

import polars as pl

from sdmxlib.model.datastructure import Attribute, DataStructure, Dimension, Measure, MeasureDimension
from sdmxlib.model.istring import normalize_tag
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet

# ── Facet → Polars dtype mapping ─────────────────────────────────────────────

_TEXT_TYPE_MAP: dict[str, pl.DataType] = {
    "String": pl.String(),
    "Alpha": pl.String(),
    "AlphaNumeric": pl.String(),
    "Numeric": pl.Float64(),
    "Integer": pl.Int64(),
    "Long": pl.Int64(),
    "Short": pl.Int32(),
    "Float": pl.Float64(),
    "Double": pl.Float64(),
    "Decimal": pl.Float64(),
    "Boolean": pl.Boolean(),
    "Date": pl.Date(),
    "DateTime": pl.Datetime(),
    "Time": pl.Time(),
    # All SDMX period types are represented as strings
    "ObservationalTimePeriod": pl.String(),
    "StandardTimePeriod": pl.String(),
    "BasicTimePeriod": pl.String(),
    "GregorianTimePeriod": pl.String(),
    "GregorianYear": pl.String(),
    "GregorianYearMonth": pl.String(),
    "GregorianDay": pl.Date(),
    "ReportingTimePeriod": pl.String(),
    "ReportingYear": pl.String(),
    "ReportingSemester": pl.String(),
    "ReportingTrimester": pl.String(),
    "ReportingQuarter": pl.String(),
    "ReportingMonth": pl.String(),
    "ReportingWeek": pl.String(),
    "ReportingDay": pl.String(),
    "Year": pl.String(),
    "Month": pl.String(),
    "YearMonth": pl.String(),
    "Duration": pl.String(),
    "URI": pl.String(),
    "XHTML": pl.String(),
}


#: How `schema` types a component that has a resolved codelist.
#:
#: Two questions, two answers, and both are wanted. ``"enum"`` asks *is this
#: file usable at all* — a `pl.Enum` makes a bad code a parse error, which is
#: cheap and fails fast. ``"string"`` asks *what exactly is wrong with it* — the
#: bad values reach the frame, so `validation_report` can name every one with a
#: count rather than a `ComputeError` naming the first (#408).
#:
#: Not `strict=` or `ignore_errors=`: polars owns both names and means other
#: things by them — `ignore_errors` *nulls* unparseable values, and `strict` is
#: about casting. This names the decision it makes.
CodedAs = Literal["enum", "string"]


# Deliberately narrower than `Component`: the time dimension is Facet-only and
# is handled by its own branch in `schema()`, so it never reaches here.
def _dtype_for(
    component: Dimension | MeasureDimension | Attribute | Measure,
    coded_as: CodedAs = "enum",
) -> pl.DataType:
    """Infer the Polars dtype for a single DSD component."""
    # A MeasureDimension is enumerated by a ConceptScheme, so its category ids
    # are concept ids; every other component reads its codelist.
    ids: list[str] | None = None
    if isinstance(component, MeasureDimension):
        concepts = component.concepts
        ids = [c.id for c in concepts] if concepts is not None else None
    else:
        cl = component.resolved_enumeration
        ids = cl.code_ids() if cl is not None else None
    if ids is not None:
        return pl.String() if coded_as == "string" else pl.Enum(sorted(ids))
    match component.representation:
        case Facet(type=dtype) if dtype is not None:
            return _TEXT_TYPE_MAP.get(dtype, pl.String())
        case _:
            return pl.String()


# ── Public API ────────────────────────────────────────────────────────────────


def schema(dsd: DataStructure, *, coded_as: CodedAs = "enum") -> dict[str, pl.DataType]:
    """Return a Polars schema dict for all DSD components.

    Enumerated components become ``pl.Enum``; text components are mapped
    from their ``Facet.type``; everything else defaults to
    ``pl.String``.

    Args:
        dsd: The resolved `DataStructure` the schema describes.
        coded_as: How to type components with a resolved codelist.
            ``"enum"`` (default) makes each a ``pl.Enum`` over its code ids,
            so a value outside the codelist is a **parse error on read** —
            fail-fast, and the right default for a pipeline.
            ``"string"`` types them ``pl.String`` instead, which is what makes
            `validation_report` able to answer at all: a bad code is then a
            value in the frame rather than an exception, so the report can name
            the column, the value and the count of every one. The DSD
            still drives every non-enumerated dtype either way.

    Example:
        df = pl.read_csv("data.csv", schema_overrides=slpl.schema(dsd))

        # "what exactly is wrong with this file?"
        lf = pl.scan_csv(path, schema_overrides=slpl.schema(dsd, coded_as="string"))
        slpl.validation_report(dsd, lf)
    """
    result: dict[str, pl.DataType] = {}
    md = [dsd.measure_dimension] if dsd.measure_dimension is not None else []
    for comp in [*dsd.dimensions, *md, *dsd.attributes, *dsd.measures]:
        result[comp.id] = _dtype_for(comp, coded_as)
    if dsd.time_dimension is not None:
        td = dsd.time_dimension
        match td.representation:
            case Facet(type=tt) if tt is not None:
                result[td.id] = _TEXT_TYPE_MAP.get(tt, pl.String())
            case _:
                result[td.id] = pl.String()
    return result


def validation_report(
    dsd: DataStructure,
    df: pl.LazyFrame | pl.DataFrame,
) -> pl.DataFrame:
    """Validate a DataFrame against enumerated DSD components.

    Returns a DataFrame of violations::

        ┌──────────┬───────────────┬───────┐
        │ column   │ invalid_value │ count │
        └──────────┴───────────────┴───────┘

    Only columns present in ``df`` are checked. Non-enumerated components
    and unresolved codelists are skipped.

    Example:
        report = slpl.validation_report(dsd, df)
        if report.is_empty():
            print("All values valid")
    """
    lf = df.lazy() if isinstance(df, pl.DataFrame) else df
    schema_cols = set(lf.collect_schema().names())

    violations: list[pl.DataFrame] = []
    md = [dsd.measure_dimension] if dsd.measure_dimension is not None else []
    for comp in [*dsd.dimensions, *md, *dsd.attributes, *dsd.measures]:
        if comp.id not in schema_cols:
            continue
        # MeasureDimension enumerates over concepts, not codes (see its docstring).
        if isinstance(comp, MeasureDimension):
            concepts = comp.concepts
            if concepts is None:
                continue
            valid_codes = {c.id for c in concepts}
        else:
            cl = comp.resolved_enumeration
            if cl is None:
                continue
            valid_codes = set(cl.code_ids())
        bad = (
            lf.select(pl.col(comp.id).cast(pl.String))
            .filter(~pl.col(comp.id).is_in(valid_codes) & pl.col(comp.id).is_not_null())
            .group_by(comp.id)
            .agg(pl.len().alias("count"))
            .rename({comp.id: "invalid_value"})
            .with_columns(pl.lit(comp.id).alias("column"))
            .select("column", "invalid_value", "count")
            # `group_by` promises no order, and at a few thousand values the
            # order it gives differs run to run — on polars 1.x and 2.0 alike.
            # Components stay in DSD order (the loop); values within one sort.
            .sort("invalid_value")
            .collect()
        )
        if not bad.is_empty():
            violations.append(bad)

    if violations:
        return pl.concat(violations)
    return pl.DataFrame(
        {
            "column": pl.Series([], dtype=pl.String),
            "invalid_value": pl.Series([], dtype=pl.String),
            "count": pl.Series([], dtype=pl.UInt32),
        },
    )


# ── Label-map cache ─────────────────────────────────────────────────────
#
# slpl.label() is a hot path. Building the id→label
# dict by iterating ``codelist.codes`` materialises a Code object per
# row, which dominates on large codelists (CL_GEOGRAPHY: 78k codes →
# ~16s per call).
#
# Caching lives at the *source*, not here (#155): `LazyCodelist.code_map`
# memoises per instance per lang with a single-flight build lock, so every
# code_map caller — not just label() — dedups, and concurrent first-time
# callers share one build instead of stampeding the DuckDB connection.
# Plain in-memory `Codelist` has no code_map and is rebuilt each call, but
# that path is pure-Python dict construction (no SQL) — cheap enough not to
# warrant a second cache. See docs/design/caching.md.


@runtime_checkable
class SupportsCodeMap(Protocol):
    """Anything exposing a SQL-backed ``code_map`` — e.g. `LazyCodelist`.

    Declares the return type so the fast path is checked, not cast. The
    ``isinstance`` cost is paid per-component (a handful of times per labelling
    call), never per-row.
    """

    def code_map(self, *, lang: str) -> dict[str, str]:
        """Return ``{code_id: label}`` for ``lang`` via direct SQL projection."""
        ...


def _component_label_map(component: object, lang: str) -> dict[str, str] | None:
    """Return ``{code_id: label}`` for a resolved enumerated component.

    Fast path: when ``component.representation()`` is a
    `LazyCodelist` (or anything with a
    ``code_map(*, lang)`` method), use direct-SQL projection — single
    DuckDB query (cached + single-flight at the source), no
    Code/InternationalString construction. Falls back to in-memory
    iteration for plain `Codelist` instances.

    Returns ``None`` when the component is not enumerated or its
    representation Ref is unresolved.
    """
    rep = getattr(component, "representation", None)
    if not (isinstance(rep, Ref) and rep.resolved):
        return None
    cl: object = rep()
    # One branch since #398. This used to try `SupportsCodeMap` and then fall
    # back to iterating a `Codelist`, because only the lazy class had
    # `code_map`. Both do now, each on its own cheapest path — a SQL
    # projection there, a dict comprehension here — so the fallback would be
    # dead code rather than a safety net.
    if isinstance(cl, SupportsCodeMap):
        return cl.code_map(lang=lang)
    return None


def label(
    df: pl.LazyFrame,
    dsd: DataStructure,
    *,
    lang: str | Sequence[str],
    suffix: str = "_label",
    columns: Mapping[str, str] | None = None,
) -> pl.LazyFrame:
    """Add human-readable label columns for every enumerated component.

    For each enumerated dimension/attribute/measure that exists in ``df``,
    appends a ``{col}{suffix}_{lang}`` column with the code label. Codes with
    no label fall back to the code id.

    **The one labelling path**: `Query.frame(labels=)` and
    `Select.collect(labels=)` both come here, so a result is
    labelled the same way whichever terminal produced it.

    `columns` maps a component id to the column carrying it, for a frame whose
    columns are named something else — a projection under an alias. The label
    then takes the *frame's* name: `{"REF_AREA": "area"}` labels `area` as
    `area_label_en`, because the name a caller reads is the one they chose.
    Without it, ids and column names are assumed to match.

    **The language is always in the name**, including for a single one:
    ``REF_AREA_label_en``, never ``REF_AREA_label``. Otherwise adding a second
    language later *renames* the first column, and a purely additive change
    breaks whatever read it. `lang` accepts one or several.

    Label maps are cached per (codelist instance, lang) at the source
    (`LazyCodelist.code_map`) with a single-flight build lock, so the
    first caller builds the map and concurrent callers share it rather
    than each re-querying. A fresh ingest produces a new codelist
    instance with a fresh cache, so re-ingestion is automatically correct.

    **Columns are matched by SDMX component id**, so the frame's columns have
    to be named the way the DSD names them. A frame carrying *physical* column
    names — `GEO` where the DSD says `REF_AREA` — matches nothing.

    That used to happen silently and return the frame unchanged, which is how
    `catalog.query(with_labels=True)` (since deleted) came to be a no-op for
    any binding whose
    columns are not already SDMX ids. Nobody noticed because nothing failed.
    So a call that matches **no** component at all now raises: labelling
    nothing is never what a caller meant, and a frame with *some* matching
    components is a different case — extra columns are ordinary, and those are
    still passed through untouched.

    Raises:
        ValueError: If not one of the DSD's components names a column of `df`,
            or a language is not a well-formed tag.

    Example:
        labelled = slpl.label(pl.scan_csv("data.csv"), dsd, lang="en").collect()
    """
    schema_cols = set(df.collect_schema().names())
    named = dict(columns or {})
    components = _components(dsd)
    languages = [normalize_tag(each) for each in ([lang] if isinstance(lang, str) else lang)]
    if not languages:
        msg = "label() needs at least one language."
        raise ValueError(msg)
    matched: list[str] = []
    added = 0
    for comp in components:
        column = named.get(comp.id, comp.id)
        if column not in schema_cols:
            continue
        matched.append(comp.id)
        for one in languages:
            mapping = _component_label_map(comp, one)
            if mapping is None:
                continue
            df = df.with_columns(
                pl.col(column)
                .cast(pl.String)
                .replace_strict(old=list(mapping.keys()), new=list(mapping.values()), default=None)
                .alias(f"{column}{suffix}_{one}")
            )
            added += 1
    if not added:
        raise ValueError(_nothing_to_label(dsd, schema_cols, matched))
    return df


def _nothing_to_label(dsd: DataStructure, schema_cols: set[str], matched: list[str]) -> str:
    """Why a labelling call produced no column — the two cases are different.

    Worth telling apart. A frame naming no component at all is usually
    *physical* column names where SDMX ids were wanted, and the fix is a
    rename or a `columns=` map. A frame whose components are all
    non-enumerated has nothing to label by construction, and the fix is to
    project something enumerated or to stop asking.
    """
    if matched:
        return (
            f"none of {', '.join(matched)} is enumerated, so there is nothing to label. "
            f"A label comes from the codelist a component is enumerated by; a component with a facet "
            f"representation, or one whose codelist did not resolve, has none."
        )
    wanted = ", ".join(sorted(comp.id for comp in _components(dsd))) or "(none — the DSD has no components)"
    present = ", ".join(sorted(schema_cols)) or "(no columns)"
    return (
        f"no component of {dsd.id} names a column of this frame, so there is nothing to label. "
        f"The DSD has {wanted}; the frame has {present}. Columns are matched by SDMX component id — "
        f"a frame of physical column names needs renaming, or a `columns=` map."
    )


def _components(dsd: DataStructure) -> list[Dimension | Attribute | Measure | MeasureDimension]:
    """Every component a label could hang off, in the order `label` walks them."""
    mds = [dsd.measure_dimension] if dsd.measure_dimension is not None else []
    return [*dsd.dimensions, *mds, *dsd.attributes, *dsd.measures]


def structure_frame(dsd: DataStructure, *, lang: str) -> pl.DataFrame:
    """Export the DSD structure as a tidy DataFrame.

    Returns one row per component::

        ┌────────────┬───────────┬──────────┬───────────┬──────────┬─────────┐
        │ component  │ role      │ position │ concept   │ codelist │ n_codes │
        └────────────┴───────────┴──────────┴───────────┴──────────┴─────────┘

    ``role`` is one of ``"dimension"``, ``"time_dimension"``,
    ``"attribute"``, ``"measure"``.

    Raises:
        ValueError: If `lang` is not a well-formed language tag.

    Example:
        meta = slpl.structure_frame(dsd, lang="en")
    """
    lang = normalize_tag(lang)
    rows: list[dict[str, object]] = []

    for i, dim in enumerate(dsd.dimensions, start=1):
        concept_label = (
            dim.concept().name.get(lang) or dim.id if isinstance(dim.concept, Ref) and dim.concept.resolved else dim.id
        )
        cl = dim.resolved_enumeration
        cl_id = cl.id if cl is not None else None
        n_codes = len(cl) if cl is not None else 0
        rows.append(
            {
                "component": dim.id,
                "role": "dimension",
                "position": i,
                "concept": concept_label,
                "codelist": cl_id,
                "n_codes": n_codes,
            }
        )

    if dsd.time_dimension is not None:
        td = dsd.time_dimension
        concept_label = (
            td.concept().name.get(lang) or td.id if isinstance(td.concept, Ref) and td.concept.resolved else td.id
        )
        rows.append(
            {
                "component": td.id,
                "role": "time_dimension",
                "position": None,
                "concept": concept_label,
                "codelist": None,
                "n_codes": 0,
            }
        )

    if dsd.measure_dimension is not None:
        md = dsd.measure_dimension
        concept_label = (
            md.concept().name.get(lang) or md.id if isinstance(md.concept, Ref) and md.concept.resolved else md.id
        )
        cs = md.resolved_concept_scheme
        cl_id = cs.id if cs is not None else None
        n_codes = len(cs.concepts) if cs is not None else 0
        rows.append(
            {
                "component": md.id,
                "role": "measure_dimension",
                "position": None,
                "concept": concept_label,
                "codelist": cl_id,
                "n_codes": n_codes,
            }
        )

    for attr in dsd.attributes:
        if isinstance(attr.concept, Ref) and attr.concept.resolved:
            concept_label = attr.concept().name.get(lang) or attr.id
        else:
            concept_label = attr.id
        cl = attr.resolved_enumeration
        cl_id = cl.id if cl is not None else None
        n_codes = len(cl) if cl is not None else 0
        rows.append(
            {
                "component": attr.id,
                "role": "attribute",
                "position": None,
                "concept": concept_label,
                "codelist": cl_id,
                "n_codes": n_codes,
            }
        )

    for meas in dsd.measures:
        if isinstance(meas.concept, Ref) and meas.concept.resolved:
            concept_label = meas.concept().name.get(lang) or meas.id
        else:
            concept_label = meas.id
        rows.append(
            {
                "component": meas.id,
                "role": "measure",
                "position": None,
                "concept": concept_label,
                "codelist": None,
                "n_codes": 0,
            }
        )

    return pl.DataFrame(
        {
            "component": [r["component"] for r in rows],
            "role": [r["role"] for r in rows],
            "position": [r["position"] for r in rows],
            "concept": [r["concept"] for r in rows],
            "codelist": [r["codelist"] for r in rows],
            "n_codes": [r["n_codes"] for r in rows],
        },
        schema={
            "component": pl.String,
            "role": pl.String,
            "position": pl.Int32,
            "concept": pl.String,
            "codelist": pl.String,
            "n_codes": pl.Int32,
        },
    )
