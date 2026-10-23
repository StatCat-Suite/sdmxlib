"""Every written maintainable states its own URN (#406).

The SDMX-JSON structure writer emitted neither form of an artefact's identity:
no ``urn`` property and no ``links`` entry with ``rel="self"``. A consumer
could not follow a cross-reference inside a response the writer had just
produced — a downstream REST service carried a whole module to splice the identity back
in, plus a hand-kept table of 17 collection-key → (package, class) pairs, which
is a copy of what the model already knows.

**One form, not two.** #406 asked for the ``urn`` property as well, reading the
spec as accepting either. Neither vendored schema defines one. Both put the
self-URN in ``links`` and say so on `IdentifiableType`: *"Also used to specify
the URI or the URN to itself."* 2.0 tolerates an extra property because its
objects are open; 2.1 closes them with ``unevaluatedProperties`` and rejects it
— which is what `test_wire_conformance_gate` reported, as 72 invalid documents,
when the property was emitted. The tests below assert the property's *absence*
for that reason.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import sdmxlib as sl
from sdmxlib.formats.sdmx_json import writer as wjson
from sdmxlib.model.mapping import RepresentationMap, StructureMap
from sdmxlib.model.ref import artefact_urn
from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema

pytestmark = pytest.mark.unit


def _message() -> sl.StructureMessage:
    """One message carrying several artefact kinds, including the maps.

    The maps are here on purpose: their declared package was ``structuremap``,
    a string in no SDMX vocabulary, and writing a self-URN is what first put it
    somewhere a schema could read it.
    """
    dsd = sl.DataStructure(id="DSD", maintainer="AG", version="1.0", name={"en": "dsd"})
    return sl.StructureMessage(
        codelists=[sl.Codelist(id="CL", maintainer="AG", version="1.0", name={"en": "cl"})],
        concept_schemes=[sl.ConceptScheme(id="CS", maintainer="AG", version="1.0", name={"en": "cs"})],
        data_structures=[dsd],
        dataflows=[sl.Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "df"}, structure=dsd.urn)],
        structure_maps=[StructureMap(id="SM", maintainer="AG", version="1.0", name={"en": "sm"})],
        representation_maps=[RepresentationMap(id="RM", maintainer="AG", version="1.0", name={"en": "rm"})],
    )


def _written(msg: sl.StructureMessage) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(wjson.write(msg))["data"]
    return data


def _self_link(artefact: dict[str, Any]) -> dict[str, Any] | None:
    return next((link for link in artefact.get("links", []) if link.get("rel") == "self"), None)


def test_every_written_maintainable_carries_its_own_urn() -> None:
    """Every written maintainable carries its own URN, in every collection.

    A count, not a spot check: a writer that emitted the link for codelists and
    not for maps would pass any single-artefact test, and the maps are exactly
    where this went wrong.
    """
    data = _written(_message())
    collections = {key: value for key, value in data.items() if isinstance(value, list)}
    assert len(collections) == 6, f"expected six populated collections, got {sorted(collections)}"

    for key, artefacts in collections.items():
        for artefact in artefacts:
            link = _self_link(artefact)
            assert link is not None, f"{key}: no rel=self link on {artefact.get('id')}"
            assert link["urn"].endswith(f"={artefact['agencyID']}:{artefact['id']}({artefact['version']})"), (
                f"{key}: self link does not name the artefact it sits on: {link}"
            )


def test_the_self_link_carries_the_urn_and_no_href() -> None:
    """Identity, not a pointer. A link item needs no ``href`` — the schema
    requires none — and inventing one would assert a resolvable address this
    library does not know."""
    codelist = sl.Codelist(id="CL", maintainer="AG", version="1.0", name={"en": "cl"})
    written = _written(sl.StructureMessage(codelists=[codelist]))["codelists"][0]
    assert written["links"] == [{"rel": "self", "urn": str(artefact_urn(codelist))}]


def test_no_top_level_urn_property_is_emitted() -> None:
    """The half of #406 that had to be declined, and why.

    2.1 closes every object with ``unevaluatedProperties``, so a ``urn``
    property makes the document invalid. This asserts the absence directly
    rather than leaving it to the conformance gate, because the gate would
    report it as "72 documents rejected" without naming the cause.
    """
    data = _written(_message())
    offenders = [
        f"{key}[{artefact.get('id')}]"
        for key, artefacts in data.items()
        if isinstance(artefacts, list)
        for artefact in artefacts
        if "urn" in artefact
    ]
    assert offenders == [], f"a top-level urn property is not schema-valid in 2.1: {offenders}"


def test_the_uri_link_still_round_trips_beside_it() -> None:
    """`read_uri` ignores every ``rel`` but ``describedby``, so the identity
    link cannot be mistaken for the artefact's ``@uri`` (#238)."""
    codelist = sl.Codelist(id="CL", maintainer="AG", version="1.0", name={"en": "cl"}, uri="https://example.org/about")
    written = wjson.write(sl.StructureMessage(codelists=[codelist]))

    rels = [link["rel"] for link in json.loads(written)["data"]["codelists"][0]["links"]]
    assert rels == ["self", "describedby"]

    parsed = sl.parse(written)
    assert isinstance(parsed, sl.StructureMessage)
    assert parsed.codelists[0].uri == "https://example.org/about"


def test_the_written_message_validates_against_the_strict_schema() -> None:
    """2.1 rather than 2.0: it is the one that closes its objects, so it is the
    one that decides whether a new field is conformant. The same choice
    `test_wire_conformance_gate` makes."""
    assert_valid_sdmx_json(wjson.write(_message()), compile_json_schema("structure"))
