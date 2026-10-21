"""The store is a target too: `LocalRegistry.add` then `get`, over every field.

`test_field_coverage.py` walks the maximal fixture through the four wire
formats and fails on any field that changes without being registered. The
store was never one of its targets, so a field the store dropped had nothing
watching it: a constraint's data keys (#437) and component
`enumeration_format` (#438) were both set by the fixture, and both lost, for
as long as the store has existed. The registry sweep found them on live data.

This is the same census with the store as the target. Every field survives,
or is declared in `gaps.py` for `"storage"`, or is a line in `KNOWN_LOSSES`
against the issue that removes it — asserted both ways, so a fix fails here
until its line goes.
"""

import re

import attrs
import pytest

from sdmxlib.formats.gaps import unrepresentable
from sdmxlib.local import LocalRegistry
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.constraint import DataConstraint, DataKey, DataKeySet
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import Facet
from tests.support import maximal
from tests.support.factories import concept_ref
from tests.support.field_census import census

pytestmark = pytest.mark.unit

#: Census path (indices generalised) → the issue that fixes it. Empty since
#: #442 stored the last of them; kept, because a loss the census finds has to
#: land somewhere, and an entry here is what makes it an acknowledged one.
KNOWN_LOSSES: dict[str, str] = {}


def _general(path: str) -> str:
    return re.sub(r"\[\d+\]", "[]", path)


def _observed() -> set[str]:
    """Every field the store changed, less what `gaps.py` declares for it."""
    observed: set[str] = set()
    message = maximal.message()
    with LocalRegistry(":memory:") as registry:
        for slot in (f.name for f in attrs.fields(StructureMessage) if not f.name.startswith("_")):
            for artefact in getattr(message, slot) or []:
                assert isinstance(artefact, MaintainableArtefact)
                registry.add(artefact)
                back = registry.get(artefact.urn, eager=True)
                assert back is not None, f"{slot}: {artefact.urn} did not come back"
                declared = {gap.field for gap in unrepresentable(artefact, "storage")}
                for path in census(artefact, back, slot):
                    general = _general(path)
                    if general.split(".", 1)[1] not in declared:
                        observed.add(general)
    return observed


def test_the_store_loses_only_what_is_registered() -> None:
    observed = _observed()
    new = sorted(observed - set(KNOWN_LOSSES))
    fixed = sorted(set(KNOWN_LOSSES) - observed)
    assert not new, (
        "the store now loses fields that are neither declared in `gaps.py` nor registered:\n"
        + "\n".join(f"    {p}" for p in new)
        + "\n\nStore it, declare it a storage limit in `gaps.py`, or register it here against an issue."
    )
    assert not fixed, "the store no longer loses these, so delete them from KNOWN_LOSSES:\n" + "\n".join(
        f"    {p}  ({KNOWN_LOSSES[p]})" for p in fixed
    )


def test_the_census_sees_a_store_loss() -> None:
    """Discrimination: the data keys are what this guard exists for, so their
    absence from the register is only meaningful if the census reads them."""
    constraint = next(a for a in maximal.message().constraints if a.data_key_sets)
    stripped = attrs.evolve(constraint, data_key_sets=())
    assert "constraints.data_key_sets" in {_general(p) for p in census(constraint, stripped, "constraints")}


def test_data_keys_keep_their_order_set_and_an_empty_key() -> None:
    """The fixture's single-pair keys cannot show order, a key with no values, or sets of one kind (#479)."""
    key_sets = (
        DataKeySet(
            is_included=True,
            keys=(
                DataKey(values=(("REF_AREA", "CA"), ("FREQ", "A"))),
                DataKey(values=()),
            ),
        ),
        DataKeySet(is_included=False, keys=(DataKey(values=(("FREQ", "M"),)),)),
        DataKeySet(is_included=True, keys=(DataKey(values=(("FREQ", "Q"),)),)),
    )
    constraint = DataConstraint(id="CC", maintainer="AG", name={"en": "c"}, data_key_sets=key_sets)
    with LocalRegistry(":memory:") as registry:
        registry.add(constraint)
        back = registry.get(constraint.urn)
    assert isinstance(back, DataConstraint)
    assert back.data_key_sets == key_sets


def test_a_facet_representation_does_not_come_back_with_an_enumeration_format() -> None:
    """The enumeration row's facet columns hold `enumeration_format` (#438);
    a facet row's hold the facet, and must not read back as both."""
    codelist = Ref[Codelist].unresolved(Codelist.urn_for(agency="AG", id="CL_FREQ", version="1.0"))
    dsd = DataStructure(
        id="DSD",
        maintainer="AG",
        name={"en": "d"},
        dimensions=[
            Dimension(
                id="FREQ",
                concept=concept_ref("FREQ"),
                representation=codelist,
                enumeration_format=Facet(type="String", max_length=1),
            ),
            Dimension(
                id="REF_AREA", concept=concept_ref("REF_AREA"), representation=Facet(type="String", max_length=3)
            ),
        ],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    with LocalRegistry(":memory:") as registry:
        registry.add(dsd)
        back = registry.get(dsd.urn, eager=True)
    assert isinstance(back, DataStructure)
    freq, ref_area = back.dimensions
    assert freq.enumeration_format == Facet(type="String", max_length=1)
    assert ref_area.representation == Facet(type="String", max_length=3)
    assert ref_area.enumeration_format is None


def test_a_lazy_read_materialises_to_what_an_eager_read_returns() -> None:
    """The census reads eagerly, so a lazy proxy could lose what eager kept and
    nothing would see it — which is what `LazyHierarchy.materialize()` did to
    every node's annotations (#443). Every proxy `get()` returns must
    materialise to the eager artefact, field by field."""
    message = maximal.message()
    checked: list[str] = []
    with LocalRegistry(":memory:") as registry:
        for slot in (f.name for f in attrs.fields(StructureMessage) if not f.name.startswith("_")):
            for artefact in getattr(message, slot) or []:
                assert isinstance(artefact, MaintainableArtefact)
                registry.add(artefact)
                lazy = registry.get(artefact.urn)
                materialize = getattr(lazy, "materialize", None)
                if materialize is None:
                    continue
                eager = registry.get(artefact.urn, eager=True)
                materialised = materialize()
                # The census walks fields, so a class it lost would pass it: a
                # `GeoGridCodelist` read back as a plain `Codelist` (#422).
                assert type(materialised) is type(eager) is type(artefact)
                differences = sorted(census(eager, materialised, slot))
                assert not differences, f"{slot}: materialize() differs from get(eager=True) on {differences}"
                checked.append(slot)
    # Exact, so a proxy that stops being returned — or a new one — is seen.
    assert sorted(checked) == ["codelists", "codelists", "codelists", "codelists", "hierarchies"]
