"""The reader ref-resolution witness (#177) is a runtime guard, not decorative.

`resolve_urn` / `resolve_ref` take a `type[T]` witness = the artefact class the
target field expects. A reference is bound to the document's index (#492) and
finds what its URN names there on access; a URN naming another class is
carried unbound, so it never resolves to a mistyped artefact (policy (a)).
This tests the policy via the canonical `_resolve.resolve_urn` helper; the ML
`resolve_ref` refuses such a URN outright and is covered by the round-trip
suite.
"""

import pytest

from sdmxlib import artefact_urn
from sdmxlib.formats.sdmx_json._resolve import resolve_any_urn
from sdmxlib.formats.sdmx_json._resolve import resolve_urn as _resolve_urn
from sdmxlib.model._binding import binding
from sdmxlib.model._resolvers import MapResolver
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.concept import Concept

pytestmark = pytest.mark.unit


def _codelist_in_index() -> tuple[str, dict[str, object]]:
    cl = Codelist(id="CL_FREQ", maintainer="SDMX", name={"en": "Frequency"})
    urn = str(artefact_urn(cl))
    return urn, {urn: cl}


def test_resolves_when_indexed_type_matches_witness() -> None:
    urn, index = _codelist_in_index()
    with binding(MapResolver(index)):
        ref = _resolve_urn(urn, Codelist)
    assert ref is not None
    assert ref.resolved
    assert isinstance(ref(), Codelist)


def test_a_reference_finds_what_the_document_defines_later() -> None:
    """Bound to the index, not looked up once: a forward reference resolves too."""
    urn, filled = _codelist_in_index()
    index: dict[str, object] = {}
    with binding(MapResolver(index)):
        ref = _resolve_urn(urn, Codelist)
    assert ref is not None
    assert not ref.resolved
    index.update(filled)
    assert ref.resolved


def test_wrong_type_in_index_falls_through_to_unresolved() -> None:
    # URN is present, but it names a Codelist — the field asked for a Concept.
    # Policy (a): do not resolve to the mistyped object.
    urn, index = _codelist_in_index()
    with binding(MapResolver(index)):
        ref = _resolve_urn(urn, Concept)
    assert ref is not None
    assert not ref.resolved
    assert not ref.bound
    assert str(ref.urn) == urn  # the URN is still carried


def test_the_any_artefact_path_accepts_any_indexed_type() -> None:
    """`resolve_any_urn` is what an `object` witness used to be (#399).

    `resolve_urn` now parses on its class (`cls.parse_urn`), which `object`
    has not got, so a reference that may name any artefact says so by name.
    """
    urn, index = _codelist_in_index()
    with binding(MapResolver(index)):
        ref = resolve_any_urn(urn)
    assert ref is not None
    assert ref.resolved


def test_a_missing_urn_on_the_any_artefact_path_stays_unresolved() -> None:
    # Sharp edge: isinstance(None, object) is True, so a *missing* URN must not
    # resolve to a None payload just because the witness is `object`.
    urn, _ = _codelist_in_index()
    with binding(MapResolver({})):  # empty index
        ref = resolve_any_urn(urn)
    assert ref is not None
    assert not ref.resolved
    assert str(ref.urn) == urn
