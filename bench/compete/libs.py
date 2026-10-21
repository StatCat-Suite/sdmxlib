"""sdmxlib, pysdmx and sdmx1 behind one interface, for the competitor benchmarks.

Each library is read and written through `read` and `write`, and what it
read is reduced by `summary` to what all three hold, so `differences` can
compare them. The competitors are imported only when called: they are the
optional `bench-compete` group, and `just ci` type-checks this module without
them.
"""

from __future__ import annotations

import gc
import io
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from importlib import import_module
from types import ModuleType
from typing import Protocol, runtime_checkable

import msgspec
from lxml import etree

from sdmxlib.formats import parse, serialize
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import ValidationError
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema
from tests.support.xsd import assert_valid_sdmx_ml, compile_structure_schema

LIBS = ("sdmxlib", "sdmx1", "pysdmx")
#: pysdmx validates its input against the schema by default and sdmxlib does
#: not, so `pysdmx` is timed with validation off and this is its default.
PYSDMX_DEFAULT = "pysdmx-validating"

type Key = tuple[str, str, str]
type Row = tuple[str, str, str | None, str | None]
#: (maintainer, id, version) -> code id -> (English name, parent id).
type Codes = dict[Key, dict[str, tuple[str | None, str | None]]]
#: (maintainer, id, version) -> (role, component id, concept id, enumeration).
type Components = dict[Key, list[Row]]
type Summary = tuple[Codes, Components]
type Times = dict[tuple[str, str, str, str], list[float]]


# ── the competitors' models, as far as this benchmark reads them ─────────────


class _HasId(Protocol):
    id: str


class _Sdmx1Localized(Protocol):
    localizations: Mapping[str, str]


class _Sdmx1Code(Protocol):
    id: str
    name: _Sdmx1Localized
    description: _Sdmx1Localized
    annotations: Sequence[object]
    parent: object


class _Sdmx1Maintainable(Protocol):
    id: str
    version: object
    maintainer: _HasId


class _Sdmx1Codelist(_Sdmx1Maintainable, Protocol):
    is_external_reference: bool | None
    items: Mapping[str, _Sdmx1Code]


class _Sdmx1Representation(Protocol):
    enumerated: _Sdmx1Maintainable | None


class _Sdmx1Component(Protocol):
    id: str
    local_representation: _Sdmx1Representation | None
    concept_identity: _HasId | None


class _Sdmx1ComponentList(Protocol):
    components: Sequence[_Sdmx1Component]


class _Sdmx1Structure(_Sdmx1Maintainable, Protocol):
    dimensions: _Sdmx1ComponentList
    attributes: _Sdmx1ComponentList
    measures: _Sdmx1ComponentList


@runtime_checkable
class Sdmx1Message(Protocol):
    codelist: Mapping[str, _Sdmx1Codelist]
    structure: Mapping[str, _Sdmx1Structure]


class _PysdmxMaintainable(Protocol):
    agency: str
    id: str
    version: str


class _PysdmxCode(Protocol):
    id: str
    name: str | None
    description: str | None
    annotations: Sequence[object] | None


class _PysdmxCodelist(_PysdmxMaintainable, Protocol):
    items: Sequence[_PysdmxCode]


class _PysdmxRole(Protocol):
    name: str


class _PysdmxComponent(Protocol):
    id: str
    role: _PysdmxRole
    concept: object
    local_enum_ref: str | None
    local_codes: _PysdmxMaintainable | None


class _PysdmxStructure(_PysdmxMaintainable, Protocol):
    components: Iterable[_PysdmxComponent]


@runtime_checkable
class PysdmxMessage(Protocol):
    def get_codelists(self) -> Sequence[_PysdmxCodelist]: ...
    def get_data_structure_definitions(self) -> Sequence[_PysdmxStructure]: ...


type Message = StructureMessage | Sdmx1Message | PysdmxMessage


def _module(name: str) -> ModuleType:
    """A competitor, imported only when the benchmark runs: `bench-compete` is an optional group."""
    return import_module(name)


# ── reading and writing ──────────────────────────────────────────────────────


def read(lib: str, data: bytes) -> Message:
    if lib == "sdmxlib":
        parsed = parse(data)
        assert isinstance(parsed, StructureMessage)
        return parsed
    if lib == "sdmx1":
        # sdmx1 prints its parser state when it fails; keep the report readable.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            sdmx1_message: Sdmx1Message = _module("sdmx").read_sdmx(io.BytesIO(data))
        return sdmx1_message
    pysdmx_message: PysdmxMessage = _module("pysdmx.io").read_sdmx(data.decode(), validate=lib == PYSDMX_DEFAULT)
    return pysdmx_message


def write(lib: str, message: Message, fmt: str) -> bytes:
    """*message* as *fmt*, unvalidated, so the writer alone is timed; validity is checked separately."""
    if isinstance(message, StructureMessage):
        return serialize(message, version=fmt, validate=False)
    if lib == "sdmx1":
        if fmt != "2.1":
            msg = "sdmx1 writes SDMX-ML 2.1 only"
            raise NotImplementedError(msg)
        written: bytes = _module("sdmx").to_xml(message)
        return written
    assert isinstance(message, PysdmxMessage)
    names = {"2.1": "STRUCTURE_SDMX_ML_2_1", "3.0": "STRUCTURE_SDMX_ML_3_0", "json": "STRUCTURE_SDMX_JSON_2_0_0"}
    pysdmx_format: object = _module("pysdmx.io.format").Format[names[fmt]]
    objects = [*message.get_codelists(), *message.get_data_structure_definitions()]
    out: str = _module("pysdmx.io.writer").write_sdmx(objects, pysdmx_format)
    return out.encode()


# ── content, on what all three hold ──────────────────────────────────────────
# pysdmx holds one name per item and no code parent, so names are compared in
# English and parents only between sdmxlib and sdmx1.


def _after_class(urn: str) -> str:
    """`urn:…Codelist=BIS:CL_FREQ(1.0)` -> `BIS:CL_FREQ(1.0)`."""
    return urn.split("=", 1)[1] if "=" in urn else urn


def _summary_sdmxlib(message: StructureMessage) -> Summary:
    codes: Codes = {}
    for codelist in message.codelists:
        # A name read without a language is held language-unknown, never
        # guessed as English: there is no default language.
        codes[(codelist.maintainer.id, codelist.id, codelist.version)] = {
            code.id: (code.name.get("en", "_default"), code.parent_id) for code in codelist.codes
        }
    components: Components = {}
    for dsd in message.data_structures:
        rows: list[Row] = []
        dimensions = [*dsd.dimensions, *([dsd.time_dimension] if dsd.time_dimension is not None else [])]
        for role, items in (("D", dimensions), ("A", list(dsd.attributes)), ("M", list(dsd.measures))):
            for component in items:
                urn: object = getattr(component.representation, "urn", None)
                concept = component.concept
                concept_id = str(concept.urn).rsplit(".", 1)[-1] if concept is not None else None
                rows.append((role, component.id, concept_id, _after_class(str(urn)) if urn is not None else None))
        components[(dsd.maintainer.id, dsd.id, dsd.version)] = rows
    return codes, components


def _sdmx1_key(artefact: _Sdmx1Maintainable) -> Key:
    return (artefact.maintainer.id, artefact.id, str(artefact.version))


def _summary_sdmx1(message: Sdmx1Message) -> Summary:
    codes: Codes = {}
    for codelist in message.codelist.values():
        # sdmx1 makes a one-code external-reference codelist for some <Parent>
        # references; those are bookkeeping, not content.
        if codelist.is_external_reference:
            continue
        entries: dict[str, tuple[str | None, str | None]] = {}
        for code in codelist.items.values():
            # A code's parent is the codelist itself when it has none.
            parent_id: str | None = getattr(code.parent, "id", None) if type(code.parent).__name__ == "Code" else None
            entries[code.id] = (code.name.localizations.get("en"), parent_id)
        codes[_sdmx1_key(codelist)] = entries
    components: Components = {}
    for dsd in message.structure.values():
        rows: list[Row] = []
        for role, part in (("D", dsd.dimensions), ("A", dsd.attributes), ("M", dsd.measures)):
            for component in part.components:
                representation = component.local_representation
                enumerated = representation.enumerated if representation is not None else None
                identity = component.concept_identity
                enumeration = None if enumerated is None else "{}:{}({})".format(*_sdmx1_key(enumerated))
                rows.append((role, component.id, identity.id if identity is not None else None, enumeration))
        components[_sdmx1_key(dsd)] = rows
    return codes, components


def _summary_pysdmx(message: PysdmxMessage) -> Summary:
    codes: Codes = {
        (codelist.agency, codelist.id, codelist.version): {code.id: (code.name, None) for code in codelist.items}
        for codelist in message.get_codelists()
    }
    roles = {"DIMENSION": "D", "ATTRIBUTE": "A", "MEASURE": "M"}
    components: Components = {}
    for dsd in message.get_data_structure_definitions():
        by_role: dict[str, list[Row]] = {"D": [], "A": [], "M": []}
        for component in dsd.components:
            role = roles[component.role.name]
            concept_id: str | None = getattr(component.concept, "item_id", None) or getattr(
                component.concept, "id", None
            )
            local = component.local_codes
            enumeration = (
                _after_class(component.local_enum_ref)
                if component.local_enum_ref
                else f"{local.agency}:{local.id}({local.version})"
                if local is not None
                else None
            )
            by_role[role].append((role, component.id, concept_id, enumeration))
        components[(dsd.agency, dsd.id, dsd.version)] = [*by_role["D"], *by_role["A"], *by_role["M"]]
    return codes, components


def summary(message: Message) -> Summary:
    if isinstance(message, StructureMessage):
        return _summary_sdmxlib(message)
    if isinstance(message, Sdmx1Message):
        return _summary_sdmx1(message)
    return _summary_pysdmx(message)


def differences(first: Summary, second: Summary, *, parents: bool) -> list[str]:
    """What differs between two summaries, one line per kind, with an example."""
    out: list[str] = []
    for label, a, b in (("codelists", first[0], second[0]), ("dsds", first[1], second[1])):
        if only := sorted(set(a) - set(b)):
            out.append(f"{label}: {len(only)} only in the first, e.g. {only[:2]}")
        if only := sorted(set(b) - set(a)):
            out.append(f"{label}: {len(only)} only in the second, e.g. {only[:2]}")
        differing = [
            k for k in sorted(set(a) & set(b)) if _strip(a[k], parents=parents) != _strip(b[k], parents=parents)
        ]
        if differing:
            first_key = differing[0]
            out.append(f"{label}: {len(differing)} differ, e.g. {_example(a[first_key], b[first_key], first_key)}")
    return out


def _strip(value: object, *, parents: bool) -> object:
    """A codelist's entries without parents, when one side cannot hold them."""
    if parents or not isinstance(value, dict):
        return value
    return {code: (entry[0], None) for code, entry in value.items()}


def _example(a: object, b: object, key: Key) -> str:
    if isinstance(a, dict) and isinstance(b, dict):
        code = next(iter(sorted(set(a) ^ set(b))), None) or next(c for c in sorted(a) if a.get(c) != b.get(c))
        return f"{key} code {code}: {a.get(code)} vs {b.get(code)}"
    if isinstance(a, list) and isinstance(b, list):
        return f"{key}: {[c for c in a if c not in b][:2]} vs {[c for c in b if c not in a][:2]}"
    return str(key)


# ── schema validity ──────────────────────────────────────────────────────────


def validity(fmt: str, data: bytes) -> str:
    try:
        if fmt == "json":
            assert_valid_sdmx_json(data, compile_json_schema("structure", "2.0"))
        else:
            assert_valid_sdmx_ml(data, compile_structure_schema("2.1" if fmt == "2.1" else "3.0"))
    except AssertionError as error:
        lines = str(error).strip().splitlines()
        return f"INVALID: {(lines[1] if len(lines) > 1 else lines[0]).strip()[:110]}"
    return "valid"


def refused_by_default(message: Message, fmt: str) -> str | None:
    """Why sdmxlib's default `serialize` refuses *message* as *fmt*, or None if it writes it.

    `write` turns validation off so the writer alone is timed. Without this,
    the report would call such output `INVALID`, as though sdmxlib wrote it.
    It doesn't: BIS's `_T` parents are the case (#432, #500).
    """
    if not isinstance(message, StructureMessage):
        return None
    try:
        serialize(message, version=fmt)
    except ValidationError as error:
        first, *rest = error.issues
        more = f" (+{len(rest)} more)" if rest else ""
        return f"{first.path}: {first.message[:110]}{more}"
    return None


def label(fmt: str, data: bytes, refusal: str | None) -> str:
    """What the Output section says about one written document.

    A refusal is reported as one, never as `INVALID`. `INVALID` stays for
    output the default would write and the schema still rejects: a writer bug.
    """
    return f"refused by default: {refusal}" if refusal is not None else validity(fmt, data)


# ── fidelity: what each library keeps of what a document holds ───────────────


@dataclass(frozen=True)
class Fidelity:
    """What one read holds of a document's codes.

    `languages` is None when names are held as plain strings, with no language
    attached. That is pysdmx's model, so which language survived cannot be
    asked of it.
    """

    codes: int
    languages: frozenset[str] | None
    parents: int
    annotations: int
    descriptions: int


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def _source_xml(data: bytes) -> Fidelity:
    codes = parents = annotations = descriptions = 0
    languages: set[str] = set()
    for element in etree.fromstring(data).iter():
        container = element.getparent()
        if _local(element.tag) != "Code" or container is None or _local(container.tag) != "Codelist":
            continue
        codes += 1
        children = [(_local(child.tag), child) for child in element]
        languages |= {str(child.get(_XML_LANG)) for name, child in children if name == "Name"}
        parents += any(name == "Parent" for name, _ in children) or element.get("parentID") is not None
        descriptions += any(name == "Description" for name, _ in children)
        annotations += sum(len(child) for name, child in children if name == "Annotations")
    return Fidelity(codes, frozenset(languages), parents, annotations, descriptions)


def _source_json(data: bytes) -> Fidelity:
    document = msgspec.json.decode(data, type=dict[str, object])
    payload = document.get("data")
    codelists = payload.get("codelists", []) if isinstance(payload, dict) else []
    codes = parents = annotations = descriptions = 0
    languages: set[str] = set()
    for codelist in codelists if isinstance(codelists, list) else []:
        items = codelist.get("codes", []) if isinstance(codelist, dict) else []
        for code in items if isinstance(items, list) else []:
            if not isinstance(code, dict):
                continue
            codes += 1
            names = code.get("names")
            languages |= set(names) if isinstance(names, dict) else {"(no language)"}
            parents += code.get("parent") is not None
            descriptions += bool(code.get("descriptions") or code.get("description"))
            found = code.get("annotations")
            annotations += len(found) if isinstance(found, list) else 0
    return Fidelity(codes, frozenset(languages), parents, annotations, descriptions)


def source_fidelity(fmt: str, data: bytes) -> Fidelity:
    """What the document itself holds, counted from its text with no library's model in between.

    That independence is the point: measured against any library's read, it
    would only say where the two libraries differ, not what either dropped.
    """
    return _source_json(data) if fmt == "json" else _source_xml(data)


def _languages(names: Iterable[Iterable[str]]) -> frozenset[str]:
    return frozenset(language for name in names for language in name)


def fidelity(message: Message) -> Fidelity:
    """What a library's read holds of the codes, in the same terms as `source_fidelity`."""
    if isinstance(message, StructureMessage):
        sdmxlib_codes = [code for codelist in message.codelists for code in codelist.codes]
        return Fidelity(
            len(sdmxlib_codes),
            _languages(code.name for code in sdmxlib_codes),
            sum(code.parent_id is not None for code in sdmxlib_codes),
            sum(len(code.annotations) for code in sdmxlib_codes),
            sum(bool(code.description) for code in sdmxlib_codes),
        )
    if isinstance(message, Sdmx1Message):
        sdmx1_codes = [
            code
            for codelist in message.codelist.values()
            if not codelist.is_external_reference
            for code in codelist.items.values()
        ]
        return Fidelity(
            len(sdmx1_codes),
            _languages(code.name.localizations for code in sdmx1_codes),
            sum(type(code.parent).__name__ == "Code" for code in sdmx1_codes),
            sum(len(code.annotations) for code in sdmx1_codes),
            sum(bool(code.description.localizations) for code in sdmx1_codes),
        )
    pysdmx_codes = [code for codelist in message.get_codelists() for code in codelist.items]
    return Fidelity(
        len(pysdmx_codes),
        None,
        0,  # pysdmx's Code has no parent: the hierarchy is not in its model
        sum(len(code.annotations or ()) for code in pysdmx_codes),
        sum(code.description is not None for code in pysdmx_codes),
    )


def dropped(source: Fidelity, held: Fidelity) -> list[str]:
    """What *held* lost of *source*, one phrase each: `[]` when it kept everything counted."""
    out: list[str] = []
    if held.codes < source.codes:
        out.append(f"{source.codes - held.codes:,} codes")
    had = source.languages or frozenset()
    if held.languages is None:
        if len(had) > 1:
            out.append(f"all but one of {len(had)} languages, unlabelled")
    elif lost := sorted(had - held.languages):
        out.append(f"languages {lost}")
    for what, have, had in (
        ("parents", held.parents, source.parents),
        ("annotations", held.annotations, source.annotations),
        ("descriptions", held.descriptions, source.descriptions),
    ):
        if have < had:
            out.append(f"{had - have:,} of {had:,} {what}")
    return out


# ── timing ───────────────────────────────────────────────────────────────────


def timed(fn: Callable[[], object]) -> float:
    gc.collect()
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start
