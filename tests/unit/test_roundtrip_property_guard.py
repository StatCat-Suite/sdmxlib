"""Generic per-serializer round-trip property guard (#159).

For each serializer (SDMX-JSON, SDMX-ML 2.1/3.0, DuckDB storage) this
builds a *fully populated* artefact, round-trips it, and asserts each
wire-representable field survives.

Fields that are known to be dropped — the "Table B" symmetric gaps from
the #159 audit, where neither side of a pair handles the field — are
marked `xfail(strict=True)`. That does double duty: it documents the
exact gap, and the moment a gap is closed the case XPASSes, which under
`strict=True` fails the suite and tells us to delete the marker. New,
unintended drops surface as ordinary failures.

Extend by adding a `Check` to a case (structure formats) or to
`STORAGE_CHECKS`. Add `(serializer, case_id, field)` to `KNOWN_GAPS`
when the gap is real and tracked; never to silence a genuine regression.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, NamedTuple

import duckdb
import pytest

from sdmxlib.formats.sdmx_json import reader as json_reader
from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_ml21 import reader as ml21_reader
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import reader as ml30_reader
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ItemList
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.datastructure import AttachmentLevel, Attribute, DataStructure, Dimension, Measure
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, Level
from sdmxlib.model.mapping import RepresentationMap, RepresentationMapping
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.organisation import Agency, AgencyScheme, Contact
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.storage.readers import get_artefact
from sdmxlib.storage.schema import create_schema
from sdmxlib.storage.writers import (
    put_agency_scheme,
    put_codelist,
    put_concept_scheme,
    put_data_structure,
    put_hierarchy,
)
from tests.support.factories import concept_ref
from tests.support.roundtrip import structure_schema
from tests.support.xsd import assert_valid_sdmx_ml

pytestmark = pytest.mark.unit


# ── Round-trip drivers ────────────────────────────────────────────────────────

STRUCTURE_FORMATS: dict[str, tuple[Callable[[StructureMessage], bytes], Callable[[bytes], StructureMessage]]] = {
    "json": (json_writer.write, json_reader.read),
    "ml21": (ml21_writer.write, ml21_reader.read),
    "ml30": (ml30_writer.write, ml30_reader.read),
}


# XML formats validate the emitted bytes before re-reading. Without this the
# guard could only catch "the reader undoes the writer" — it stayed green while
# the hierarchy case emitted a `<Code>` reference carrying a *codelist* URN,
# which the XSD forbids and our own reader accepted (found via !190).
_XSD_VERSION: dict[str, str] = {"ml21": "2.1", "ml30": "3.0"}


def _roundtrip_structure(fmt: str, msg: StructureMessage) -> StructureMessage:
    write, read = STRUCTURE_FORMATS[fmt]
    written = write(msg)
    if (version := _XSD_VERSION.get(fmt)) is not None:
        assert_valid_sdmx_ml(written, structure_schema(version))
    return read(written)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _cl_ref(id_: str) -> Ref[Codelist]:
    return Ref[Codelist].unresolved(
        SdmxUrn(package="codelist", artefact_class="Codelist", agency="T", id=id_, version="1.0")
    )


def _code_ref(cl_id: str, code_id: str) -> Ref[Code]:
    """A reference to a *Code* — the parent codelist's URN plus an item id.

    The XSD types a ``<Code>`` reference ``CodeReferenceType`` (pattern
    ``.+\\.codelist\\.Code=.+``), so a bare codelist URN is not valid there.
    """
    return Ref[Code].unresolved(
        SdmxUrn(package="codelist", artefact_class="Code", agency="T", id=cl_id, version="1.0", item_id=code_id)
    )


def _ann_types(a: Annotations) -> set[str]:
    return {x.type for x in a if x.type}


class Check(NamedTuple):
    field: str
    # get(round_tripped_artefact) -> comparable value
    get: Callable[[Any], Any]
    want: Any


class Case(NamedTuple):
    id: str
    build: Callable[[], StructureMessage]
    # extract(maintainable) -> the object the checks inspect (the maintainable
    # itself, or an item/component beneath it). Runs identically on a parsed
    # structure message and on a storage-reconstructed maintainable.
    extract: Callable[[Any], Any]
    checks: list[Check]


_MAINT_SLOTS = (
    "codelists",
    "concept_schemes",
    "data_structures",
    "agency_schemes",
    "hierarchies",
    "representation_maps",
)


def _one_maintainable(msg: StructureMessage) -> Any:
    for slot in _MAINT_SLOTS:
        items = getattr(msg, slot)
        if items:
            return next(iter(items))
    msg_err = "case message has no maintainable"
    raise AssertionError(msg_err)


# ── Cases (structure formats) ─────────────────────────────────────────────────


def _codelist_case() -> Case:
    def build() -> StructureMessage:
        cl = Codelist(
            id="CL",
            maintainer="T",
            version="1.0",
            name={"en": "Freq", "fr": "Fréq"},
            description={"en": "desc"},
            legacy_is_final=True,
            uri="https://example.org/vocabulary/freq",
            valid_from=datetime(2020, 1, 1),
            valid_to=datetime(2021, 1, 1),
            codes=[
                Code(
                    id="A",
                    name={"en": "Annual"},
                    uri="https://example.org/vocabulary/freq/A",
                    annotations=Annotations([Annotation(type="NOTE", title="t")]),
                ),
                Code(id="A1", name={"en": "Annual, sub"}, parent_id="A"),
            ],
        )
        return StructureMessage(codelists=[cl])

    def extract(cl: Any) -> Any:
        return cl

    return Case(
        "codelist",
        build,
        extract,
        [
            Check("name", lambda a: a.name.get("en"), "Freq"),
            Check("name.fr", lambda a: a.name.get("fr"), "Fréq"),
            Check("legacy_is_final", lambda a: a.legacy_is_final, True),
            Check("valid_from", lambda a: a.valid_from, datetime(2020, 1, 1, tzinfo=UTC)),
            Check("valid_to", lambda a: a.valid_to, datetime(2021, 1, 1, tzinfo=UTC)),
            Check("uri", lambda a: a.uri, "https://example.org/vocabulary/freq"),
            Check("code.uri", lambda a: a.codes["A"].uri, "https://example.org/vocabulary/freq/A"),
            Check("code.annotations", lambda a: _ann_types(a.codes["A"].annotations), {"NOTE"}),
            Check("code.parent_id", lambda a: a.codes["A1"].parent_id, "A"),
        ],
    )


def _concept_case() -> Case:
    def build() -> StructureMessage:
        facet = Facet(type="Integer", min_value=1.0, max_value=99.0, decimals=2, pattern="[0-9]+", is_multilingual=True)
        cs = ConceptScheme(
            id="CS",
            maintainer="T",
            version="1.0",
            name={"en": "cs"},
            concepts=[Concept(id="C1", name={"en": "c1"}, core_representation=facet)],
        )
        return StructureMessage(concept_schemes=[cs])

    def extract(cs: Any) -> Any:
        return cs.concepts["C1"]

    def facet(a: Any) -> Facet | None:
        return a.core_representation if isinstance(a.core_representation, Facet) else None

    return Case(
        "concept",
        build,
        extract,
        [
            Check("core.type", lambda a: getattr(facet(a), "type", None), "Integer"),
            Check("core.min_value", lambda a: getattr(facet(a), "min_value", None), 1.0),
            Check("core.decimals", lambda a: getattr(facet(a), "decimals", None), 2),
            Check("core.pattern", lambda a: getattr(facet(a), "pattern", None), "[0-9]+"),
            Check("core.is_multilingual", lambda a: getattr(facet(a), "is_multilingual", None), True),
        ],
    )


def _dsd_case() -> Case:
    def build() -> StructureMessage:
        dsd = DataStructure(
            id="DSD",
            maintainer="T",
            version="1.0",
            name={"en": "dsd"},
            dimensions=ItemList(
                [Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=_cl_ref("CL"), concept_role=("REF",))]
            ),
            attributes=ItemList(
                [
                    Attribute(
                        id="OBS_STATUS",
                        concept=concept_ref("OBS_STATUS"),
                        attachment=AttachmentLevel.SERIES,
                        related_dimensions=["FREQ"],
                    )
                ]
            ),
            # SDMX-ML 2.1 requires <MeasureList> containing a <PrimaryMeasure>
            # (`MeasureListType`: "Only a primary may be defined", minOccurs 1),
            # so a measure-less DSD cannot be expressed in 2.1 at all.
            measures=ItemList([Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))]),
        )
        return StructureMessage(data_structures=[dsd])

    def extract(dsd: Any) -> Any:
        return dsd

    return Case(
        "dsd",
        build,
        extract,
        [
            Check("dim.enumeration", lambda a: a.dimensions["FREQ"].representation.urn.id, "CL"),
            Check("dim.concept_role", lambda a: tuple(a.dimensions["FREQ"].concept_role), ("REF",)),
            Check(
                "attr.related_dimensions",
                lambda a: tuple(a.attributes["OBS_STATUS"].related_dimensions or ()),
                ("FREQ",),
            ),
        ],
    )


def _hierarchy_case() -> Case:
    def build() -> StructureMessage:
        lvl = Level(id="L1", name={"en": "l1"}, coding_format=Facet(type="AlphaNumeric", max_length=3))
        h = Hierarchy(
            id="H",
            maintainer="T",
            version="1.0",
            name={"en": "h"},
            has_formal_levels=True,
            levels=[lvl],
            tree=[
                HierarchicalCode(
                    id="R",
                    code=_code_ref("CL", "R"),
                    level=lvl,
                    valid_from=datetime(2020, 6, 1),
                    valid_to=datetime(2021, 6, 1),
                )
            ],
        )
        return StructureMessage(hierarchies=[h])

    def extract(h: Any) -> Any:
        return h

    return Case(
        "hierarchy",
        build,
        extract,
        [
            Check("has_formal_levels", lambda a: a.has_formal_levels, True),
            Check("level.coding_format", lambda a: getattr(a.levels["L1"].coding_format, "type", None), "AlphaNumeric"),
            Check("hcode.valid_from", lambda a: a.tree["R"].valid_from, datetime(2020, 6, 1, tzinfo=UTC)),
        ],
    )


def _repmap_case() -> Case:
    def build() -> StructureMessage:
        rm = RepresentationMap(
            id="RM",
            maintainer="T",
            version="1.0",
            name={"en": "rm"},
            sources=(_cl_ref("S"),),
            targets=(_cl_ref("D"),),
            mappings=(RepresentationMapping(sources=("A",), targets=("1",), valid_from=date(2020, 1, 1)),),
        )
        return StructureMessage(representation_maps=[rm])

    def extract(rm: Any) -> Any:
        return rm

    return Case(
        "repmap",
        build,
        extract,
        [
            Check("map.target", lambda a: a.mappings[0].targets, ("1",)),
            Check("map.valid_from", lambda a: a.mappings[0].valid_from, date(2020, 1, 1)),
        ],
    )


def _agencyscheme_case() -> Case:
    def build() -> StructureMessage:
        contact = Contact(name={"en": "Sup"}, phones=("111",), faxes=("222",), x400s=("/C=US",), emails=("a@b.c",))
        ags = AgencyScheme(
            id="AGENCIES",
            maintainer="T",
            version="1.0",
            name={"en": "a"},
            agencies=ItemList([Agency(id="T", name={"en": "T"}, contacts=(contact,))]),
        )
        return StructureMessage(agency_schemes=[ags])

    def extract(ags: Any) -> Any:
        return ags.agencies["T"]

    def contact(a: Any) -> Contact | None:
        return a.contacts[0] if a.contacts else None

    return Case(
        "agency",
        build,
        extract,
        [
            Check("contact.phones", lambda a: tuple(getattr(contact(a), "phones", ())), ("111",)),
            Check("contact.faxes", lambda a: tuple(getattr(contact(a), "faxes", ())), ("222",)),
            Check("contact.x400s", lambda a: tuple(getattr(contact(a), "x400s", ())), ("/C=US",)),
        ],
    )


STRUCTURE_CASES: list[Case] = [
    _codelist_case(),
    _concept_case(),
    _dsd_case(),
    _hierarchy_case(),
    _repmap_case(),
    _agencyscheme_case(),
]


# ── Known Table-B gaps: (serializer, case_id, field) → tracked in #159 ─────────
# Each entry is a field a serializer round-trip is known to drop. xfail(strict)
# means: fix the gap → the case XPASSes → suite fails → delete the entry.

KNOWN_GAPS: dict[tuple[str, str, str], str] = {
    # Remaining entries are BY DESIGN — the wire format cannot carry the field,
    # so these xfails are permanent (not a TODO):
    #   * SDMX-ML 2.1 CodeMapType allows only Annotations/Source/Target — no
    #     per-mapping validity dates.
    #   * SDMX 3.0 dropped isFinal from every maintainable artefact.
    ("ml21", "repmap", "map.valid_from"): "by design: 2.1 CodeMapType has no validity dates",
    ("ml30", "codelist", "legacy_is_final"): "by design: SDMX 3.0 dropped isFinal from maintainables",
    #   * SDMX-JSON encodes that same 3.0 model, so no version of its schema
    #     defines isFinal. 2.0.0 tolerates the extra key; 2.1.0 closes the
    #     object model and rejects it, which is why it is gone (#358).
    ("json", "codelist", "legacy_is_final"): "by design: SDMX-JSON encodes the 3.0 model, which has no isFinal",
    #   * SDMX-ML 3.1 marks CategorisationBaseType/@version use="prohibited",
    #     so a categorisation's version cannot survive a 3.1 round-trip. The
    #     writer drops it deliberately and the reader supplies the default.
    ("ml31", "categorisation", "version"): "by design: 3.1 prohibits Categorisation/@version",
    #   * SDMX-ML 3.0 generic metadata carries a reported value as element
    #     text with no type marker — the type comes from the MSD, which the
    #     message does not carry — so int/float/bool all read back as str.
    #     SDMX-JSON has native types and keeps all five.
    ("ml30", "metadataset", "attribute.value_type"): "by design: GenericMetadata values are untyped text",
    #   * SDMX-ML 2.1's MetadataflowType is a plain StructureUsage with no
    #     <Target> element; 3.0 added Target and made it mandatory. So a
    #     metadataflow round-trips through 2.1 with its targets dropped.
    ("ml21", "metadataflow", "targets"): "by design: 2.1 MetadataflowType has no Target element",
    # DuckDB storage — level codingFormat still not persisted (#159 B). Concept
    # facet detail, is_multilingual, attribute relationships and contact
    # faxes/x400s are now persisted (this MR); their entries were removed.
    # `code.uri` was here as a by-design spine-only boundary (#238) until #442
    # gave every item and component table a `uri` column, so the case passes.
}


def _param_id(fmt: str, case: Case, chk: Check) -> str:
    return f"{fmt}-{case.id}-{chk.field}"


def _structure_params() -> list[Any]:
    out: list[Any] = []
    for fmt in STRUCTURE_FORMATS:
        for case in STRUCTURE_CASES:
            for chk in case.checks:
                marks: list[pytest.MarkDecorator] = []
                reason = KNOWN_GAPS.get((fmt, case.id, chk.field))
                if reason is not None:
                    marks = [pytest.mark.xfail(strict=True, reason=reason)]
                out.append(pytest.param(fmt, case, chk, id=_param_id(fmt, case, chk), marks=marks))
    return out


@functools.cache
def _rt_cached(fmt: str, case_id: str) -> Any:
    case = next(c for c in STRUCTURE_CASES if c.id == case_id)
    parsed = _roundtrip_structure(fmt, case.build())
    return case.extract(_one_maintainable(parsed))


@pytest.mark.parametrize(("fmt", "case", "chk"), _structure_params())
def test_structure_field_round_trips(fmt: str, case: Case, chk: Check) -> None:
    artefact = _rt_cached(fmt, case.id)
    assert chk.get(artefact) == chk.want


# ── Storage variant ───────────────────────────────────────────────────────────

_STORAGE_PUT: dict[str, Any] = {
    "codelist": put_codelist,
    "concept": put_concept_scheme,
    "dsd": put_data_structure,
    "agency": put_agency_scheme,
    # Added in the #214 audit. Its absence made the
    # ("storage", "hierarchy", "level.coding_format") KNOWN_GAPS entry dead:
    # `_storage_params` skips any case not listed here, so the gap was
    # documented but never asserted. A strict xfail that generates no test is
    # invisible — it neither fails when the gap closes nor when it widens.
    # It closed in #160, and this line is why the suite noticed: the xfail
    # turned into an XPASS rather than the entry quietly outliving the bug.
    "hierarchy": put_hierarchy,
}


def _storage_artefact(case: Case) -> Any:
    # The message wraps exactly one maintainable — pull it out to persist.
    return _one_maintainable(case.build())


def _storage_params() -> list[Any]:
    out: list[Any] = []
    for case in STRUCTURE_CASES:
        if case.id not in _STORAGE_PUT:
            continue
        for chk in case.checks:
            marks: list[pytest.MarkDecorator] = []
            reason = KNOWN_GAPS.get(("storage", case.id, chk.field))
            if reason is not None:
                marks = [pytest.mark.xfail(strict=True, reason=reason)]
            out.append(pytest.param(case, chk, id=f"storage-{case.id}-{chk.field}", marks=marks))
    return out


def _urn_str(art: Any) -> str:
    return str(type(art).urn_for(agency=art.agency_id, id=art.id, version=art.version))


@pytest.mark.parametrize(("case", "chk"), _storage_params())
def test_storage_field_round_trips(case: Case, chk: Check) -> None:
    art = _storage_artefact(case)
    conn = create_schema(duckdb.connect(":memory:"))
    _STORAGE_PUT[case.id](conn, art)
    back = get_artefact(conn, _urn_str(art), eager=True)
    got, want = chk.get(case.extract(back)), chk.want
    if isinstance(got, datetime) and isinstance(want, datetime):
        # Compare the *instant*, not the representation. Storage stores UTC in
        # a zoneless column and re-tags on read (#321), so a naive value comes
        # home labelled UTC; the wire formats keep it naive, and these `Check`
        # objects are shared with them. One `want` cannot be both, and the
        # instant is what "the field survived" means for a datetime.
        #
        # This still catches the bug #321 fixed: an aware value used to come
        # back converted to the reader's local time, which is a *different*
        # instant and fails here.
        got, want = _as_utc(got), _as_utc(want)
    assert got == want


def _as_utc(value: datetime) -> datetime:
    """A datetime as an instant, reading naive as UTC — the storage convention."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# ── The declaration and the proof must agree ─────────────────────────────────


def test_every_by_design_gap_is_declared_in_the_library() -> None:
    """`KNOWN_GAPS` records what a round-trip *observes*; `formats.gaps`
    declares what a format *cannot carry*. They are two views of one fact and
    must not drift.

    The direction that matters: a by-design entry here with no declared row
    means the library tells an author the write is lossless while this suite
    knows it is not. `unrepresentable()` would then be quietly incomplete —
    the failure mode of any table nobody cross-checks.

    Not asserted in the other direction. `formats.gaps` may declare a gap this
    guard has no case for (`legacy_is_final` applies to every maintainable; only
    codelist is exercised), and a missing *case* is a coverage gap rather than
    a contradiction. What would catch a *wrong* declaration is the
    `xfail(strict=True)` on the cases that do exist: fix the gap and the case
    XPASSes.
    """
    from sdmxlib.formats.gaps import declared_gaps

    # `KNOWN_GAPS` keys are (serializer, test case, field path as the checks
    # spell it); the declaration keys are (target, type, field path as a model
    # reader would write it). Mapping them is this test's whole job, and it is
    # written out rather than guessed so a mismatch is a failure and not a
    # silently empty intersection.
    serializers = {"ml21": "2.1", "ml30": "3.0", "ml31": "3.1", "json": "json", "storage": "storage"}
    fields = {
        ("ml30", "codelist", "legacy_is_final"): ("3.0", "MaintainableArtefact", "legacy_is_final"),
        ("json", "codelist", "legacy_is_final"): ("json", "MaintainableArtefact", "legacy_is_final"),
        ("ml21", "metadataflow", "targets"): ("2.1", "Metadataflow", "targets"),
        ("ml21", "repmap", "map.valid_from"): ("2.1", "RepresentationMap", "mappings[].valid_from"),
        ("ml31", "categorisation", "version"): ("3.1", "Categorisation", "version"),
        ("ml30", "metadataset", "attribute.value_type"): ("3.0", "MetadataSet", "attributes[].value_type"),
    }
    declared = {(target, type_name, field) for target, type_name, field, _ in declared_gaps()}

    for key, reason in KNOWN_GAPS.items():
        serializer = key[0]
        if serializer not in serializers or not reason.startswith("by design"):
            # A tracked defect is not a by-design gap: #159 B says the store
            # *should* persist a level's coding format and does not, so
            # declaring it would tell an author the loss is permanent.
            continue
        row = fields.get(key)
        assert row is not None, f"{key} is by-design but this test has no mapping for it"
        assert row in declared, f"{key} is by-design here but `formats.gaps` does not declare {row}"
