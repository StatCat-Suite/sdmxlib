"""Static probe: `LocalRegistry.get` declares the class it actually returns (#397).

`get` is generic over the URN's phantom, so a `SdmxUrn[Hierarchy]` gave
`Hierarchy | None` while a `LazyHierarchy` came back. `LazyHierarchy` has no
`codes`, `tree` or `descendants` at all, so this type-checked and raised:

    reg.get(h_urn).codes

Three overloads fix it. The first two name the roots that come back lazy; the
third is the generic form every other artefact still takes. `eager=` selects
between them, which is the reason overloads were needed rather than a single
changed return type — the flag decides the class, and one declaration cannot
describe both branches.

The runtime half is `tests/unit/test_lazy_return_types.py`.
"""

from typing import assert_type

import sdmxlib as sl
from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.hierarchy import Hierarchy
from sdmxlib.model.ref import Ref
from sdmxlib.storage.lazy import LazyCodelist, LazyHierarchy


def _lazy_by_default(reg: LocalRegistry) -> None:
    assert_type(reg.get(sl.Codelist.urn_for(agency="AG", id="CL")), LazyCodelist | None)
    assert_type(reg.get(sl.Hierarchy.urn_for(agency="AG", id="H")), LazyHierarchy | None)


def _eager_is_the_real_class(reg: LocalRegistry) -> None:
    assert_type(reg.get(sl.Codelist.urn_for(agency="AG", id="CL"), eager=True), Codelist | None)
    assert_type(reg.get(sl.Hierarchy.urn_for(agency="AG", id="H"), eager=True), Hierarchy | None)


def _every_other_root_is_unchanged(reg: LocalRegistry) -> None:
    """The generic overload still answers for artefacts with no lazy counterpart."""
    assert_type(reg.get(sl.DataStructure.urn_for(agency="AG", id="DSD")), DataStructure | None)
    assert_type(reg.get(sl.Dataflow.urn_for(agency="AG", id="DF")), sl.Dataflow | None)


def _a_ref_takes_the_same_overload(reg: LocalRegistry, ref: Ref[Codelist]) -> None:
    """#370 made `Ref` an accepted target; it has to reach the lazy overload too,
    or traversal would be typed differently from a URN lookup of the same thing."""
    assert_type(reg.get(ref), LazyCodelist | None)


# and these must not type-check, which is what the change is for:
#
#   reg.get(sl.Hierarchy.urn_for(agency="AG", id="H")).codes         # no `codes`
#   reg.get(sl.Hierarchy.urn_for(agency="AG", id="H")).tree          # no `tree`
#   len(reg.get(sl.Codelist.urn_for(agency="AG", id="CL")).codes)    # a Query has no len()
