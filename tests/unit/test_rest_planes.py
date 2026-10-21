"""A request goes to the plane that serves its resource (#334).

`Request.path` said ``structure/`` for every resource. SDMX-REST routes three
ways — `/structure`, `/data`, `/metadata` — and a `metadataset` is served from
the third.

**#334 as filed was wider than the defect.** It listed seven `sl.q` relations
as "metadata-plane" and unfetchable. Six were already correct:

* `msd`, `metadataflow` and `metadata_provision_agreement` are *definitions*,
  which SDMX-REST serves under `/structure` — confirmed against pysdmx's
  `StructureType`, which lists `metadatastructure`, `metadataflow` and
  `metadataprovisionagreement` among its structure resources. They were
  already routed correctly, and "metadata" appearing in a resource name says
  nothing about its plane.
* `metadata_attribute`, `reported_attribute` and `metadata_target` are *items*
  within an MSD or a MetadataSet. The planner already refuses them as roots,
  exactly as it refuses `q.code` — items come back inside the artefact that
  owns them, which is behaviour rather than a gap.

That left one: `metadataset`. And it was worse than "no request to issue" —
it issued a **wrong** one. Since #338 a 404 comes back as "not here" rather
than raising, so against a real provider the query returned an empty result
and said nothing.
"""

from __future__ import annotations

import pytest

import sdmxlib as sl
from sdmxlib.query._plan import Plan
from sdmxlib.query._pushdown import SDMX_REST, PushdownError, plan_program
from sdmxlib.query._rest import requests_for

pytestmark = pytest.mark.unit


def _path(root) -> str:
    requests = requests_for(plan_program(Plan(root=root), SDMX_REST))
    return requests[0].path


def test_a_metadataset_is_served_from_the_metadata_plane() -> None:
    """The defect. pysdmx renders ``/metadata/metadataset/{p}/{i}/{v}``, and
    this now agrees segment for segment."""
    assert _path(sl.q.metadata_set) == "metadata/metadataset/all/all/latest"


@pytest.mark.parametrize(
    ("root", "resource"),
    [
        ("msd", "metadatastructure"),
        ("metadataflow", "metadataflow"),
        ("metadata_provision_agreement", "metadataprovisionagreement"),
    ],
)
def test_a_metadata_definition_stays_on_the_structure_plane(root: str, resource: str) -> None:
    """The half of #334 that was already right, pinned so the fix cannot
    over-reach. These are definitions, and SDMX-REST serves definitions from
    `/structure` however much "metadata" is in the name."""
    assert _path(getattr(sl.q, root)) == f"structure/{resource}/all/all/latest"


@pytest.mark.parametrize("root", ["codelist", "dataflow", "dsd", "concept_scheme"])
def test_structural_resources_are_unaffected(root: str) -> None:
    assert _path(getattr(sl.q, root)).startswith("structure/")


@pytest.mark.parametrize("root", ["metadata_attribute", "reported_attribute", "metadata_target"])
def test_an_item_root_is_refused_rather_than_routed(root: str) -> None:
    """The other half #334 counted as unfetchable. An item has no endpoint on
    *either* plane — it is returned inside its owner — so the refusal is the
    same one `q.code` gets, and naming a plane for it would be inventing an
    endpoint."""
    with pytest.raises(PushdownError, match="no endpoint for that relation"):
        _path(getattr(sl.q, root))


def test_every_scannable_root_names_a_plane_deliberately() -> None:
    """A new maintainable arrives on `/structure` by default, which is right
    for eleven of twelve — so the one exception has to be *listed*, and this
    fails if the list stops matching what is actually routed.

    Written as an inventory rather than a rule because there is no rule: the
    plane is a property of the SDMX-REST spec, not something derivable from
    the resource name, as `metadataflow` and `metadataset` demonstrate by
    differing.
    """
    planes: dict[str, str] = {}
    for name in dir(sl.q):
        if name.startswith("_"):
            continue
        try:
            path = _path(getattr(sl.q, name))
        except PushdownError, TypeError, AttributeError:
            continue
        plane, _, rest = path.partition("/")
        planes[rest.split("/")[0]] = plane

    assert planes, "no scannable roots found — the probe is broken, not the code"
    metadata = {resource for resource, plane in planes.items() if plane == "metadata"}
    assert metadata == {"metadataset"}, f"the metadata plane serves {metadata}, not just metadataset"
