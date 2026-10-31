"""What an SDMX-JSON read fills in is recorded, and what it cannot fill in refuses (#474).

The SDMX-JSON half of `test_read_assumptions.py`. The schema decides every
case here: each key of the maximal message is removed in turn, and the
document is validated before it is read.

* **Removing it breaks the schema** — the key is required. The artefact is
  refused, unless the read can hold the absence: `name` beside `names`, the
  header, and the constructs `ASSUMPTIONS` declares, kept and recorded.
* **The schema still holds** — the key is optional. The artefact is read,
  unless the model requires what SDMX-JSON 2.0 leaves out: an identifiable's
  id and an item map's target, which the information model makes mandatory,
  a text's language, which a `name` left without its `names` no longer
  states (GH-280), and a component's id, which #477 derives from its concept.

The walk visits each key shape once, so a new DTO field or schema rule is a
new case, not a silent pass.
"""

import functools
import json
from collections.abc import Callable, Iterable

import pytest
from jsonschema.protocols import Validator

import sdmxlib as sl
from sdmxlib.formats.sdmx_json import writer
from sdmxlib.formats.sdmx_json._losses import ASSUMPTIONS, Assumption
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.metadatastructure import MetadataAttribute
from sdmxlib.model.urn import SdmxUrn
from tests.support import maximal
from tests.support.json_schema import compile_json_schema, is_valid_sdmx_json

pytestmark = pytest.mark.unit

type Path = tuple[str | int, ...]


@functools.cache
def _maximal_bytes() -> bytes:
    return writer.write(maximal.message())


def _maximal() -> dict[str, object]:
    doc = json.loads(_maximal_bytes())
    assert isinstance(doc, dict)
    return doc


@functools.cache
def _validator() -> Validator:
    return compile_json_schema("structure", "2.0")


def _walk(node: object, shape: tuple[str, ...], path: Path, out: dict[str, Path]) -> dict[str, Path]:
    """Each key's first occurrence, by its shape: the keys above it, list positions as ``[]``."""
    if isinstance(node, dict):
        for key, value in node.items():
            out.setdefault("/".join((*shape, key)), (*path, key))
            _walk(value, (*shape, key), (*path, key), out)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _walk(item, (*shape, "[]"), (*path, i), out)
    return out


_SHAPES = _walk(_maximal(), (), (), {})


def _child(node: object, step: str | int) -> object:
    if isinstance(node, dict) and isinstance(step, str):
        value: object = node[step]
        return value
    if isinstance(node, list) and isinstance(step, int):
        item: object = node[step]
        return item
    msg = f"no {step!r} under {type(node).__name__}"
    raise AssertionError(msg)


def _without(path: Path) -> bytes:
    """The maximal message without the key at `path`, which is always an object's key."""
    doc = _maximal()
    parent: object = doc
    for step in path[:-1]:
        parent = _child(parent, step)
    assert isinstance(parent, dict)
    del parent[path[-1]]
    return json.dumps(doc).encode()


def _required(shape: str) -> bool:
    """Whether the schema requires the key: the document is invalid without it."""
    return not is_valid_sdmx_json(_without(_SHAPES[shape]), _validator())


# ── the expectations, exact ───────────────────────────────────────────────

_DSD = "data/dataStructures/[]/dataStructureComponents"
_MSD = "data/metadataStructures/[]/metadataStructureComponents/metadataAttributeList"

#: Each `ASSUMPTIONS` row and the key whose absence it records.
_ASSUMED_AT: dict[str, str] = {
    "attributes[].attributeRelationship": f"{_DSD}/attributeList/attributes/[]/attributeRelationship",
    "metadataAttributeUsages[].attributeRelationship": (
        f"{_DSD}/attributeList/metadataAttributeUsages/[]/attributeRelationship"
    ),
    "epochMaps[].epochPeriod": "data/structureMaps/[]/epochMaps/[]/epochPeriod",
    "version": "data/codelists/[]/version",
    "dataStructureComponents.dimensionList.timeDimension.localRepresentation.format": (
        f"{_DSD}/dimensionList/timeDimension/localRepresentation"
    ),
}

#: Optional in SDMX-JSON 2.0, and still refused: the model requires them.
_REFUSED_THOUGH_OPTIONAL: dict[str, str] = {
    "data/conceptSchemeMaps/[]/itemMaps/[]/targetValue": "an item map's target is mandatory in the model",
    "data/organisationSchemeMaps/[]/itemMaps/[]/targetValue": "an item map's target is mandatory in the model",
    "data/structureMaps/[]/datePatternMaps/[]/id": "an identifiable's id is mandatory in the model",
    "data/structureMaps/[]/epochMaps/[]/id": "an identifiable's id is mandatory in the model",
    "data/structureMaps/[]/frequencyFormatMappings/[]/id": "an identifiable's id is mandatory in the model",
    f"{_DSD}/measureList": "the maximal DSD's attribute then names a measure the DSD lacks",
    f"{_DSD}/measureList/measures": "the maximal DSD's attribute then names a measure the DSD lacks",
    # Read under its concept's id (#477), the measure is renamed: the same consequence.
    f"{_DSD}/measureList/measures/[]/id": "the maximal DSD's attribute then names a measure the DSD lacks",
    # And a dimension or group the series and group attributes name (#454).
    f"{_DSD}/dimensionList/dimensions": "the maximal DSD's attributes then name dimensions the DSD lacks",
    f"{_DSD}/dimensionList/dimensions/[]/id": "renamed to its concept's id, the dimension leaves attributes naming it",
    f"{_DSD}/groups": "the maximal DSD's group attribute then names a group the DSD lacks",
}

#: The language maps an SDMX-JSON text carries beside its singular field.
_LANGUAGE_MAPS = frozenset({"names", "descriptions", "texts", "departments", "roles"})


def _leaves_a_lone_text(shape: str) -> bool:
    """Optional, and still refused: without its map, the singular text states no language (GH-280).

    The maximal message is in two languages, so its ``contentLanguages`` cannot
    give the text one. In the header that is a loss, as any header the model
    cannot hold is.
    """
    return shape.rsplit("/", 1)[-1] in _LANGUAGE_MAPS and shape[:-1] in _SHAPES and not shape.startswith("meta/")


#: A component's id is optional, and defaults to its concept's (#477): read under it.
_COMPONENT_ID: frozenset[str] = frozenset(
    {
        f"{_DSD}/attributeList/attributes/[]/id",
        f"{_DSD}/dimensionList/dimensions/[]/id",
        f"{_MSD}/metadataAttributes/[]/id",
        f"{_MSD}/metadataAttributes/[]/metadataAttributes/[]/id",
    }
)


def _kept_though_required(shape: str) -> bool:
    """Required, and still read: the header, a ``name`` beside its ``names``, and what is assumed."""
    return (
        shape.startswith("meta/")
        or (shape.endswith("/name") and f"{shape}s" in _SHAPES)
        or shape in _ASSUMED_AT.values()
    )


_CASES = [pytest.param(shape, id=shape) for shape in _SHAPES]


def test_the_walk_reaches_every_shape() -> None:
    """The discrimination check: items, components, maps, links and the header are all visited."""
    assert len(_SHAPES) == 2360
    assert sum(_leaves_a_lone_text(shape) for shape in _SHAPES) == 160
    for shape in (*_ASSUMED_AT.values(), *_REFUSED_THOUGH_OPTIONAL, *_COMPONENT_ID, "meta/sender/id"):
        assert shape in _SHAPES, f"{shape} is no longer in the maximal message"
    assert any(shape.endswith("/links/[]/href") for shape in _SHAPES)


def test_the_maximal_message_reads_back_whole() -> None:
    msg = sl.parse(_maximal_bytes(), strict=False)
    assert isinstance(msg, StructureMessage)
    assert (msg.refused, msg.assumed, msg.lost) == ((), (), ())


# Removing one key of the maximal facet's sequence leaves the rest without it,
# which warns as it is built (#481); what a removal reads as is the question.
@pytest.mark.filterwarnings("ignore::sdmxlib.ImRuleWarning")
@pytest.mark.parametrize("shape", _CASES)
def test_removing_a_key_reads_as_the_schema_says(shape: str) -> None:
    required = _required(shape)

    msg = sl.parse(_without(_SHAPES[shape]), strict=False)

    assert isinstance(msg, StructureMessage)
    refuse = (
        (required and not _kept_though_required(shape))
        or shape in _REFUSED_THOUGH_OPTIONAL
        or _leaves_a_lone_text(shape)
    )
    if refuse:
        assert len(msg.refused) == 1, f"{shape} is {'required' if required else 'optional'} and not refused"
    else:
        assert msg.refused == (), f"{shape} is {'required' if required else 'optional'}, read, and refused"
    if shape in _COMPONENT_ID and shape not in _REFUSED_THOUGH_OPTIONAL:
        stated, concept = _component_and_concept_id(_SHAPES[shape][:-1])
        assert concept != stated, "the derived id must differ to be seen"
        assert concept in _component_ids(msg), "read under its concept's id"


def _component_and_concept_id(path: Path) -> tuple[object, str]:
    """The maximal message's component at `path`: its stated id, and the item id its conceptIdentity names."""
    component: object = _maximal()
    for step in path:
        component = _child(component, step)
    assert isinstance(component, dict)
    item = SdmxUrn.parse(str(component["conceptIdentity"])).item_id
    assert item is not None
    return component["id"], item


def _component_ids(msg: StructureMessage) -> set[str]:
    ids = {c.id for dsd in msg.data_structures for c in (*dsd.dimensions, *dsd.attributes, *dsd.measures)}

    def walk(attributes: Iterable[MetadataAttribute]) -> None:
        for attribute in attributes:
            ids.add(attribute.id)
            walk(attribute.children)

    for msd in msg.metadata_structures:
        walk(msd.attributes)
    return ids


def test_the_exceptions_are_each_what_they_say() -> None:
    """Each listed shape really is required, or optional, as its table claims: no entry is stale."""
    for shape in _REFUSED_THOUGH_OPTIONAL:
        assert not _required(shape), f"{shape} is required now"
    for shape in _ASSUMED_AT.values():
        if shape != _ASSUMED_AT["version"]:
            assert _required(shape), f"{shape} is optional now"


# ── what is assumed is recorded, once ────────────────────────────────────


@pytest.mark.parametrize("row", ASSUMPTIONS, ids=lambda row: row.construct)
def test_each_assumption_is_reported_once_and_the_artefact_kept(row: Assumption) -> None:
    msg = sl.parse(_triggered(row), strict=False)

    assert isinstance(msg, StructureMessage)
    (found,) = msg.assumed
    assert (found.construct, found.assumed, found.count, found.reason) == (row.construct, row.assumed, 1, row.reason)
    assert found.urn in {str(sl.artefact_urn(a)) for a in msg.all_artefacts()}, "the artefact is kept"
    assert msg.refused == ()


def _recased() -> bytes:
    """The maximal message with one series attribute's first related dimension in lower case (#454)."""
    doc = _maximal()
    relationship: object = doc
    for step in _SHAPES[f"{_DSD}/attributeList/attributes/[]/attributeRelationship/dimensions"]:
        relationship = _child(relationship, step)
    assert isinstance(relationship, list)
    relationship[0] = str(relationship[0]).lower()
    return json.dumps(doc).encode()


#: Rows a document triggers by spelling something differently, not by omitting it.
_RECASED: dict[str, Callable[[], bytes]] = {"attributeRelationship.dimensions[]": _recased}


def _triggered(row: Assumption) -> bytes:
    recase = _RECASED.get(row.construct)
    return recase() if recase is not None else _without(_SHAPES[_ASSUMED_AT[row.construct]])


def test_every_row_has_a_removal() -> None:
    assert sorted([*_ASSUMED_AT, *_RECASED]) == sorted(row.construct for row in ASSUMPTIONS)


def test_an_organisation_scheme_without_a_version_assumes_nothing() -> None:
    """Versionless in the SDMX 3.0 model: its absence is the standard's, not an omission."""
    msg = sl.parse(_without(_SHAPES["data/agencySchemes/[]/version"]), strict=False)
    assert isinstance(msg, StructureMessage)
    assert (msg.refused, msg.assumed) == ((), ())


def test_lossless_parse_raises_on_a_json_assumption() -> None:
    data = _without(_SHAPES[_ASSUMED_AT["version"]])
    with pytest.raises(sl.LossyReadError, match=r"^1 assumption reading") as caught:
        sl.parse(data, lossless=True)
    assert [a.construct for a in caught.value.assumed] == ["version"]


def test_losses_are_worked_out_past_a_refused_entry() -> None:
    """`lost` decodes entry by entry, so the one the read refused does not fail it (#461, #474)."""
    msg = sl.parse(_without(_SHAPES["data/codelists/[]/id"]), strict=False)

    assert isinstance(msg, StructureMessage)
    assert len(msg.refused) == 1
    assert msg.lost == ()


def test_a_component_with_neither_id_nor_concept_refuses_its_artefact() -> None:
    """Nothing to take the id from: the DSD is refused (#477)."""
    doc = _maximal()
    dimension: object = doc
    for step in _SHAPES[f"{_DSD}/dimensionList/dimensions/[]/id"][:-1]:
        dimension = _child(dimension, step)
    assert isinstance(dimension, dict)
    del dimension["id"], dimension["conceptIdentity"]

    msg = sl.parse(json.dumps(doc).encode(), strict=False)

    assert isinstance(msg, StructureMessage)
    (refusal,) = msg.refused
    assert refusal.artefact_class == "DataStructure"
    assert "conceptIdentity" in refusal.reason
