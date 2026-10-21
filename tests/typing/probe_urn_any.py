"""Static probe: what a URN parsed from text declares, and what `get` answers for it (#399).

`SdmxUrn.parse(text)` and `artefact_urn(x)` are `SdmxUrn[object]`: a URN string,
or a URN taken off any artefact, can name anything, so nothing downstream may
assume what. `get()` on one answers `object | None`, and the caller narrows or
passes `expect=`. A caller that knows the class parses on it —
`Concept.parse_urn(text)` is `SdmxUrn[Concept]` — which is how the readers
rebuild typed references from stored strings.

## What this replaced

Both returned `SdmxUrn[Any]`, so the readers' `Ref.unresolved(...)` could become
whatever `Ref[T]` it was assigned to. The same `Any` made `get(parse(text))`
ambiguous across `get`'s overloads, and the checkers split on the typing spec's
rule for that: basedpyright took the first overload and answered a confident,
wrong `LazyCodelist | None`; pyrefly answered an unchecked `Any | None`.
Neither was acceptable. `tests/unit/test_overload_any_ambiguity.py` recorded
that checker disagreement until basedpyright was retired (#425); it is in git
history.

Narrowing `parse` cost 61 findings when measured (28 in `src/`), all readers
filling typed `Ref` fields from URN text — which is why the typed path is
`<Class>.parse_urn` (`model/urn.py:ParsesUrn`) rather than a `kind=` argument on
`parse`, whose witness would have been an unused parameter.
"""

from typing import assert_type

import sdmxlib as sl
from sdmxlib.local import LocalRegistry
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.ref import Ref, artefact_urn
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.storage.lazy import LazyCodelist, LazyHierarchy


def _a_parsed_urn_names_no_particular_type(text: str) -> None:
    assert_type(SdmxUrn.parse(text), SdmxUrn[object])
    assert_type(artefact_urn(sl.Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "f"})), SdmxUrn[object])


def _get_on_an_untyped_urn_answers_object(reg: LocalRegistry, text: str) -> None:
    """Honest, and the caller narrows — no confident wrong class, no unchecked `Any`."""
    assert_type(reg.get(SdmxUrn.parse(text)), object | None)
    assert_type(
        reg.get(artefact_urn(sl.Dataflow(id="DF", maintainer="AG", version="1.0", name={"en": "f"}))),
        object | None,
    )


def _parsing_on_the_class_carries_it(reg: LocalRegistry, text: str) -> None:
    """The typed path the readers use: the class is the witness."""
    assert_type(sl.Concept.parse_urn(text), SdmxUrn[sl.Concept])
    assert_type(sl.Code.parse_urn(text), SdmxUrn[sl.Code])
    assert_type(sl.Codelist.parse_urn(text), SdmxUrn[sl.Codelist])
    assert_type(Ref.unresolved(sl.Concept.parse_urn(text)), Ref[sl.Concept])
    assert_type(reg.get(sl.Codelist.parse_urn(text)), LazyCodelist | None)
    assert_type(reg.get(DataStructure.parse_urn(text)), DataStructure | None)


def _a_typed_urn_is_unaffected(reg: LocalRegistry) -> None:
    """The discrimination check for the ones above.

    Without these, the probe would pass just as well against a `get` that
    answered `LazyCodelist | None` for *every* URN — which is the shape of the
    bug #397 fixed, and the reason the overloads are ordered the way they are.
    """
    assert_type(reg.get(sl.Codelist.urn_for(agency="AG", id="CL")), LazyCodelist | None)
    assert_type(reg.get(sl.Hierarchy.urn_for(agency="AG", id="H")), LazyHierarchy | None)
    assert_type(reg.get(DataStructure.urn_for(agency="AG", id="DSD")), DataStructure | None)
    assert_type(reg.get(sl.Codelist.urn_for(agency="AG", id="CL"), eager=True), sl.Codelist | None)
