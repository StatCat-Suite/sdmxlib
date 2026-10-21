"""Scheme items must not evict the schemes from the resolver cache (#378).

`docs/design/performance.md` §Layer 3 describes a bounded cache of resolved
targets shared across `get` calls, so that many artefacts referencing one heavy
parent load it once. It holds 256 entries.

It was also being filled with every *item* resolved along the way. Measured on
the census bundle before this changed: reading a 1,660-node hierarchy left the cache
holding **256 `Code` entries and no `Codelist`** — the parent scheme it had
just loaded, evicted by the items it had just resolved. Every subsequent eager
read paid the full 2.1 s again, and the 256 codes left behind were near-useless
because the next hierarchy wants different ones.

So Layer 3 was being defeated by its own bookkeeping for any artefact with more
than 256 items, which is every artefact the cache exists for.

These tests hold the fix from both sides: schemes survive, items never enter.
"""

from collections.abc import Iterator

import pytest

import sdmxlib as sl
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.model.organisation import Agency
from sdmxlib.model.ref import Ref
from tests.support.factories import code_urn, concept_ref

pytestmark = pytest.mark.unit

#: Comfortably past the 256-entry default, so the eviction this guards against
#: would actually happen. At 100 items nothing is evicted and every assertion
#: below passes against the broken code — the vacuity trap CLAUDE.md's rule 4
#: is about.
_N_CODES = 400


def _code_urn(code_id: str) -> str:
    return f"urn:sdmx:org.sdmx.infomodel.codelist.Code=SDMX:CL_BIG(1.0).{code_id}"


@pytest.fixture
def reg() -> Iterator[sl.LocalRegistry]:
    """A codelist of 400 codes and a hierarchy placing every one of them."""
    codes = [Code(id=f"C{i:04d}", name={"en": f"Code {i}"}) for i in range(_N_CODES)]
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            Codelist(
                id="CL_BIG",
                maintainer=Agency(id="SDMX"),
                version="1.0",
                name={"en": "Big"},
                codes=codes,
            )
        )
        registry.add(
            Hierarchy(
                id="H_BIG",
                maintainer=Agency(id="SDMX"),
                version="1.0",
                name={"en": "Big hierarchy"},
                tree=[
                    HierarchicalCode(
                        id="ROOT",
                        code=code_urn("CL", "ROOT"),
                        children=[
                            HierarchicalCode(
                                id=f"N{i:04d}",
                                code=Ref[Code].unresolved(Code.parse_urn(_code_urn(f"C{i:04d}"))),
                            )
                            for i in range(_N_CODES)
                        ],
                    )
                ],
            )
        )
        registry.add(
            DataStructure(
                id="DSD_BIG",
                maintainer=Agency(id="SDMX"),
                version="1.0",
                name={"en": "Uses the big codelist"},
                dimensions=[
                    Dimension(
                        id="GEO",
                        concept=concept_ref("GEO"),
                        representation=Ref[Codelist].unresolved(
                            Codelist.parse_urn("urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_BIG(1.0)")
                        ),
                    )
                ],
                measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
            )
        )
        yield registry


def _cached(registry: sl.LocalRegistry) -> list[str]:
    return list(registry._resolve_cache)


def test_the_fixture_is_big_enough_to_evict(reg):
    """Without this the rest of the file passes against the broken code."""
    assert _N_CODES > 256


def test_resolving_items_puts_no_items_in_the_shared_cache(reg):
    """400 codes resolved; none of them belongs in a 256-entry cache."""
    reg.get(Hierarchy.urn_for(agency="SDMX", id="H_BIG", version="1.0"), eager=True)
    items = [k for k in _cached(reg) if ".Code=" in k]
    assert not items, f"{len(items)} item entries reached the shared cache"


def test_the_cache_does_not_overflow_on_one_read(reg):
    """The whole point: one read must not churn the cache from end to end."""
    reg.get(Hierarchy.urn_for(agency="SDMX", id="H_BIG", version="1.0"), eager=True)
    assert len(_cached(reg)) < 256


def test_a_scheme_survives_reading_a_hierarchy_over_it(reg):
    """A cached `Codelist` is still there after a hierarchy over it is read.

    The failing case in the module docstring. The codelist is put in the cache
    the only way anything gets there — as the *target of a Ref*, here a DSD
    dimension's representation. Reading the artefact directly does not cache
    it, which is worth knowing: the cache holds resolution targets, not
    everything `get` has returned.
    """
    codelist_urn = str(Codelist.urn_for(agency="SDMX", id="CL_BIG", version="1.0"))
    reg.get(DataStructure.urn_for(agency="SDMX", id="DSD_BIG", version="1.0"), eager=True)
    assert codelist_urn in _cached(reg), "precondition: resolving the DSD's ref caches the codelist"

    reg.get(Hierarchy.urn_for(agency="SDMX", id="H_BIG", version="1.0"), eager=True)
    assert codelist_urn in _cached(reg), "reading a hierarchy over it evicted the codelist"


def test_the_refs_still_resolve(reg):
    """None of the above is worth anything if resolution stopped working.

    Stated separately rather than folded into the assertions above, because a
    resolver that resolved nothing would satisfy every one of them.
    """
    hierarchy = reg.get(Hierarchy.urn_for(agency="SDMX", id="H_BIG", version="1.0"), eager=True)
    leaves = list(hierarchy.tree[0].children)
    assert len(leaves) == _N_CODES
    assert all(node.code.resolved for node in leaves)
    assert {node.code().id for node in leaves} == {f"C{i:04d}" for i in range(_N_CODES)}
    assert all(node.code().name["en"].startswith("Code ") for node in leaves)
