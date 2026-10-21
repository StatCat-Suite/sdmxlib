"""Every property SDMX-JSON allows is read, reported, or out of reach (#422).

The SDMX-JSON twin of `test_element_census.py`. A struct ignores every key it
does not declare, so the reader now walks each artefact's raw JSON beside its
structs and reports what they skipped (`_losses.undeclared`). This census is
what keeps that claim true: per schema version, every ``(definition,
property)`` pair the structure schema allows inside a section the reader reads
is compared with what the writer emits for the maximal fixture and
`tests.support.shapes`, and each pair the writer never emits is one of:

- **detected** — no struct that decodes that definition declares the
  property, so the walk reports it. Derived, not listed: the structs are
  followed statically from `StructureDataJson` along their field types.
- **covered** — the definition sits under a detected property, which is
  reported whole.
- **listed** — a struct declares it and nothing writes it, so whether it is
  held is not derivable: `_DECLARED` says why no document here carries it.
  There is no "lost" kind: a key a struct declares is one the model holds
  (#422), so it belongs in the fixtures.

Checked in both directions, so a pair that starts being written, or stops
being declared, must leave `_DECLARED`.
"""

import functools
import json
import re
import typing
from pathlib import Path

import msgspec
import pytest

from sdmxlib.formats.sdmx_json import writer as json_writer
from sdmxlib.formats.sdmx_json._structure_dto import StructureDataJson
from tests.support import maximal, shapes

pytestmark = pytest.mark.unit

_SCHEMAS = Path(__file__).resolve().parents[1] / "schemas" / "sdmx_json"
_VERSIONS = ("2.0", "2.1")

type _Node = dict[str, object]
type _Pair = tuple[str, str]


@functools.cache
def _schema(version: str) -> _Node:
    loaded: _Node = json.loads((_SCHEMAS / version / "sdmx-json-structure-schema.json").read_text())
    return loaded


def _definitions(version: str) -> dict[str, _Node]:
    schema = _schema(version)
    found = schema.get("definitions") or schema.get("$defs")
    assert isinstance(found, dict)
    return found


def _ref(node: _Node) -> str | None:
    ref = node.get("$ref")
    return ref.rsplit("/", 1)[-1] if isinstance(ref, str) else None


def _properties(
    version: str, node: _Node, owner: str, seen: frozenset[str] = frozenset()
) -> list[tuple[str, str, _Node]]:
    """``(owner, property, schema)`` for every property `node` may hold, through refs and combinators.

    The owner is the definition that declares the property, so a property of
    `MaintainableType` is one pair however many artefacts inherit it.
    """
    found: list[tuple[str, str, _Node]] = []
    name = _ref(node)
    if name is not None and name not in seen:
        found += _properties(version, _definitions(version)[name], name, seen | {name})
    properties = node.get("properties")
    if isinstance(properties, dict):
        found += [(owner, key, value) for key, value in properties.items() if isinstance(value, dict)]
    for combinator in ("allOf", "anyOf", "oneOf"):
        subs = node.get(combinator)
        for sub in subs if isinstance(subs, list) else []:
            if isinstance(sub, dict):
                found += _properties(version, sub, owner, seen)
    return found


def _inner(version: str, node: _Node, owner: str) -> tuple[_Node, str]:
    """The schema a property's value is checked against — an array's elements — and its owner name.

    A node reached through `$ref` is named by its definition, so a recursive
    definition is visited once rather than under an ever-longer inline name.
    """
    items = node.get("items")
    if node.get("type") == "array" and isinstance(items, dict):
        node = items
    name = _ref(node)
    if name is not None and not node.get("properties"):
        target = _definitions(version)[name]
        if target.get("type") == "array":
            return _inner(version, target, owner)
        return target, name
    return node, owner


def _structs_in(annotation: object) -> set[type[msgspec.Struct]]:
    """The struct classes a field's type admits, through `Optional`, `list` and unions."""
    if isinstance(annotation, typing.TypeAliasType):
        return _structs_in(annotation.__value__)
    if isinstance(annotation, type) and issubclass(annotation, msgspec.Struct):
        return {annotation}
    found: set[type[msgspec.Struct]] = set()
    for arg in typing.get_args(annotation):
        found |= _structs_in(arg)
    return found


@functools.cache
def _field_types(struct: type[msgspec.Struct]) -> dict[str, set[type[msgspec.Struct]]]:
    """Wire name → the struct classes that field decodes into (empty for a scalar or a map)."""
    hints = typing.get_type_hints(struct)
    return {
        encoded: _structs_in(hints[name])
        for encoded, name in zip(struct.__struct_encode_fields__, struct.__struct_fields__, strict=True)
    }


@functools.cache
def _graph(version: str) -> tuple[frozenset[_Pair], dict[str, frozenset[type[msgspec.Struct]]], frozenset[str]]:
    """The allowed pairs; the structs decoding each owner; and the owners reached only through a skipped key.

    Followed from `StructureDataJson`, through the sections it declares only —
    the others are refused whole.
    """
    data = _schema(version)["properties"]
    assert isinstance(data, dict)
    allowed: set[_Pair] = set()
    structs: dict[str, set[type[msgspec.Struct]]] = {}
    under_skipped: set[str] = set()
    todo: list[tuple[_Node, str, frozenset[type[msgspec.Struct]]]] = [
        (data["data"], "data", frozenset({StructureDataJson}))
    ]
    seen: set[tuple[str, frozenset[type[msgspec.Struct]]]] = set()
    while todo:
        node, owner, decoding = todo.pop()
        if (owner, decoding) in seen:
            continue
        seen.add((owner, decoding))
        for declarer, key, value in _properties(version, node, owner):
            if declarer == "data" and key not in StructureDataJson.__struct_encode_fields__:
                continue
            allowed.add((declarer, key))
            if not decoding and owner in under_skipped:
                # An inherited property of a definition below a reported key.
                under_skipped.add(declarer)
            structs.setdefault(declarer, set()).update(decoding)
            inner, inner_owner = _inner(version, value, f"{declarer}.{key}")
            fields = [_field_types(struct) for struct in decoding]
            # Below a key no struct declares — reported whole by the walk —
            # and so, transitively, is everything beneath it.
            if (decoding and not any(key in field for field in fields)) or (not decoding and owner in under_skipped):
                under_skipped.add(inner_owner)
            below = frozenset(struct for field in fields for struct in field.get(key, ()))
            todo.append((inner, inner_owner, below))
    return frozenset(allowed), {k: frozenset(v) for k, v in structs.items()}, frozenset(under_skipped)


def _written(version: str) -> set[_Pair]:
    """The pairs the writer emits for the maximal fixture and the shapes."""
    pairs: set[_Pair] = set()

    def walk(value: object, node: _Node, owner: str) -> None:
        if not isinstance(value, dict):
            return
        declared: dict[str, tuple[str, _Node]] = {}
        for declarer, key, sub in _properties(version, node, owner):
            declared.setdefault(key, (declarer, sub))
        for key, item in value.items():
            if key not in declared:
                continue  # a map's language keys, or a key this version leaves open
            declarer, sub = declared[key]
            pairs.add((declarer, key))
            inner, inner_owner = _inner(version, sub, f"{declarer}.{key}")
            for element in item if isinstance(item, list) else [item]:
                walk(element, inner, inner_owner)

    data = _schema(version)["properties"]
    assert isinstance(data, dict)
    for message in (maximal.message(), shapes.message_31()):
        walk(json.loads(json_writer.write(message))["data"], data["data"], "data")
    return pairs


_EITHER_REPRESENTATION = (
    "absent: a representation is an enumeration or a format, never both; this component's "
    "is enumerated in every message here, and its format is written for the shapes' free-text ones"
)
_REPORTING_YEAR = (
    "absent: `ReportingYearStartOrEndDayType` repeats `AttributeType`'s properties, and the census credits "
    "the first definition declaring a key — an attribute is read the same either way"
)

#: The pairs a struct declares that nothing here writes, classified.
_DECLARED: dict[str, dict[_Pair, str]] = {
    version: {
        **{
            ("ReportingYearStartOrEndDayType", key): _REPORTING_YEAR
            for key in (
                "attributeRelationship",
                "conceptIdentity",
                "conceptRoles",
                "id",
                "links",
                "localRepresentation",
                "measureRelationship",
                "usage",
            )
        },
        **{
            (owner, key): _REPORTING_YEAR
            for owner, keys in (
                ("ReportingYearStartOrEndDayRepresentationType", ("format", "maxOccurs", "minOccurs")),
                ("ReportingYearStartOrEndDayTextFormatType", ("dataType",)),
                ("ReportingYearStartOrEndDayType.links", ("href", "hreflang", "rel", "type", "urn")),
            )
            for key in keys
        },
        **(
            {
                ("SimpleComponentValueType", key): (
                    "held: a component value's validity, which the reader keeps and `validate` refuses to write, "
                    "as SDMX-ML 3.x prohibits it there"
                )
                for key in ("validFrom", "validTo")
            }
            if version == "2.1"
            else {}
        ),
        **(
            dict.fromkeys(
                (
                    ("DataConstraintAttachmentType", "simpleDataSources"),
                    ("DataConstraintAttachmentType", "queryableDataSources"),
                    *(
                        ("QueryableDataSourceType", key)
                        for key in ("dataURL", "isRESTDatasource", "isWebServiceDatasource", "wadlURL", "wsdlURL")
                    ),
                ),
                "held: a constraint's data sources, which the reader keeps — "
                "`test_constraint_attachment.py::test_sdmx_json_2_0_data_sources_are_read` — and `validate` "
                "refuses to write, as SDMX-JSON 2.1 removed them and the writer's output validates against both",
            )
            if version == "2.0"
            else {}
        ),
    }
    for version in _VERSIONS
}


def _classify(version: str) -> tuple[set[_Pair], set[_Pair]]:
    """The unwritten pairs a struct declares, and those no struct decodes at all."""
    allowed, structs, under_skipped = _graph(version)
    declared: set[_Pair] = set()
    undecoded: set[_Pair] = set()
    for owner, key in allowed - _written(version):
        decoding = structs.get(owner, frozenset())
        if not decoding:
            if owner not in under_skipped:
                undecoded.add((owner, key))
        elif any(key in _field_types(struct) for struct in decoding):
            declared.add((owner, key))
    return declared, undecoded


@pytest.mark.parametrize("version", _VERSIONS)
def test_every_declared_unwritten_pair_is_classified(version: str) -> None:
    declared, _ = _classify(version)
    table = set(_DECLARED[version])
    unclassified = sorted(declared - table)
    assert not unclassified, (
        f"SDMX-JSON {version}: a struct declares these and nothing writes them, so whether they are held "
        "is not known: "
        + "".join(f"\n    {owner} . {key}" for owner, key in unclassified)
        + "\n\nWrite one in `tests/support/shapes.py` if the model holds it; hold it if it does not (the "
        "reader drops a declared key without a sound); or say why no document holds it."
    )
    stale = sorted(table - declared)
    assert not stale, f"written now, or no longer declared — delete them: {stale}"


@pytest.mark.parametrize("version", _VERSIONS)
def test_every_owner_is_decoded_or_under_a_reported_key(version: str) -> None:
    """A definition no struct decodes is fine only below a key the walk reports."""
    _, undecoded = _classify(version)
    assert not undecoded, f"SDMX-JSON {version}: reached by a declared key no struct decodes: {sorted(undecoded)}"


def test_the_census_derives_a_detected_pair() -> None:
    """Discrimination: a property no struct declares is classified without a table entry.

    A component list's `id` is in the schema — `DimensionDescriptor`, a
    `const` — and on no struct, so it is neither written nor listed: the
    derivation is what accounts for it.
    """
    allowed, structs, _ = _graph("2.1")
    assert ("DimensionListType", "id") in allowed
    assert not any("id" in _field_types(struct) for struct in structs["DimensionListType"])
    assert ("DimensionListType", "id") not in _DECLARED["2.1"]


def test_the_shapes_read_back_from_sdmx_json() -> None:
    """What the shapes contribute to this census is held, not merely emitted."""
    from sdmxlib.formats.gaps import unrepresentable
    from sdmxlib.formats.sdmx_json import reader as json_reader
    from tests.support.field_census import differences
    from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema

    written = json_writer.write(shapes.message_31())
    for version in _VERSIONS:
        assert_valid_sdmx_json(written, compile_json_schema("structure", version))
    back = json_reader.read(written)
    assert back.lost == ()
    assert back.refused == ()
    read_back = {str(artefact.urn): artefact for artefact in back.all_artefacts()}
    for original in shapes.message_31().all_artefacts():
        # Less what `gaps.py` says SDMX-JSON cannot carry: a plain codelist's
        # geographic codes keep their ids and lose their geographic field.
        declared = {gap.field for gap in unrepresentable(original, "json")}
        found = [
            d
            for d in differences(original, read_back[str(original.urn)])
            if re.sub(r"\[\d+\]", "[]", d.path.removeprefix(".")) not in declared
        ]
        assert not found, f"{original.urn} lost {found}"
