"""A document must survive a hop through another SDMX-ML version (#246).

`test_document_fidelity` asserts the *same-version* rule: `D -> model -> D`.
This asserts the harder one — `D -> other version -> D` — where the
intermediate document is the bottleneck rather than the model. Anything the
middle version cannot express is gone before the return trip, so no amount of
model-level conversion helps.

Three outcomes, all asserted rather than tolerated:

- **converts** — the document comes back unchanged;
- **blocked** — one leg raises `ValidationError`, which is the correct answer
  where SDMX genuinely cannot carry the construct;
- **lossy** — it survives with recorded differences, each pointing at the
  issue that tracks them.

Entries in the last two are asserted *still* blocked or *still* lossy, so
removing a bottleneck fails the test until its entry goes. That is what makes
the table a burn-down rather than documentation.

The distinction the milestone turns on is which blocks are **fundamental** and
which are **ours**. SDMX 3.0 mostly relocated or unified what it dropped —
`AttachmentConstraint` folded into `DataConstraint` (#244), MSD targets moved
to `Metadataflow` — and those conversions are ours to implement. The grouping
containers are the genuine wall: `StructureSet` and `HierarchicalCodelist`
assert "these artefacts belong together", and 3.x has no way to say it.
"""

import pathlib

import pytest

from sdmxlib.formats import parse, serialize
from sdmxlib.model.validation import ValidationError
from tests.support.fidelity import compare

pytestmark = pytest.mark.unit

_FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"

_VERSION_OF = {b"schemas/v2_1/": "2.1", b"schemas/v3_0/": "3.0", b"schemas/v3_1/": "3.1"}
_VERSIONS = ("2.1", "3.0", "3.1")

#: (home version, via version, fixture) -> why the hop cannot complete.
#:
#: A block is the *right* answer when SDMX cannot carry the construct; it is a
#: bug when the limitation is ours. Each entry says which, so the difference
#: stays visible rather than being flattened into "unsupported".
_BLOCKED: dict[tuple[str, str, str], str] = {
    # ── Fundamental: 2.1 is narrower than 3.x ────────────────────────────
    ("3.0", "2.1", "sdmx_ml30/complete.xml"): "2.1 allows one PrimaryMeasure; 3.0 allows many",
    ("3.0", "2.1", "sdmx_ml30/with_groups.xml"): "2.1 allows one PrimaryMeasure; 3.0 allows many",
    ("3.1", "2.1", "sdmx_ml31/with_groups.xml"): "2.1 allows one PrimaryMeasure; 3.0 allows many",
    # ── Fundamental: a metadataflow's targets ────────────────────────────
    # 3.x requires a Metadataflow to name its targets and 2.1 has nowhere to
    # put them, so the trip back from 2.1 has none. Since #421 the 3.x writer
    # derives them from the MSD's 2.1 targets — but a 3.x MSD has none, and
    # what 2.1 writes for it (a target of `Any`) is dropped on read and names
    # no class anyway. The MSD itself crosses since #421: 2.1 writes a 3.x one
    # with a marked reporting layer. It was blocked on that layer until then
    # (#245).
    ("3.0", "2.1", "sdmx_ml30/with_metadata.xml"): "2.1 has no Metadataflow targets; 3.x requires one",
    ("3.1", "2.1", "sdmx_ml31/with_metadata.xml"): "2.1 has no Metadataflow targets; 3.x requires one",
    # The 3.0 -> 3.1 -> 3.0 hop used to be blocked here, reading "a
    # datatype-target map has no 3.0 form; 3.0 requires TargetCodelist". That
    # blamed the wrong version: 3.0 emits `<TargetDataType>` and always did,
    # and it was the *3.1* leg dropping it that left the returning map with no
    # target at all. Fixed in #345, so the hop survives and the entry is gone —
    # which this test is what told us.
    ("3.0", "2.1", "sdmx_ml30/with_representation_map_variants.xml"): (
        "a datatype-target map has no 3.0 form; 3.0 requires TargetCodelist"
    ),
    # ── Fundamental: 2.1 maps codes to codes and nothing else ────────────
    # These used to read "StructureSet grouping has no 3.x form", which was
    # about the synthetic wrapper rather than the map, and stopped being true
    # when the wrapper stopped being reified. What actually cannot make the
    # trip is the map body: a regex or index mapping has no ``<CodeMap>``
    # form. The writer used to emit them as literal pairs — restating the
    # pattern text as a code id — so the hop "succeeded" while changing what
    # the document said. Now refused (#242).
    ("3.1", "2.1", "sdmx_ml31/with_representation_map_variants.xml"): (
        "2.1 <CodeMap> cannot express a regex or index mapping"
    ),
}

#: (home, via, fixture) -> what the hop loses, with the issue tracking it.
_ATTRIBUTE_MULTILINGUAL = "2.1 prohibits isMultiLingual on an attribute; 3.0 defaults it true"

_LOSSY: dict[tuple[str, str, str], str] = {
    # The four ECB entries here were `@uri`, carried since #238. What is left
    # is a real format gap rather than a library one.
    ("3.0", "2.1", "sdmx_ml30/with_codelist_extension.xml"): "2.1 has no CodelistExtension",
    # A 3.0 attribute's text format is multilingual unless it says otherwise;
    # 2.1 gives an attribute the simple format, which cannot say so (#503).
    # 3.1 defaults it false, so a 3.1 document loses nothing.
    ("3.0", "2.1", "sdmx_ml30/minimal.xml"): _ATTRIBUTE_MULTILINGUAL,
    ("3.0", "2.1", "sdmx_ml30/with_categories.xml"): _ATTRIBUTE_MULTILINGUAL,
    ("3.1", "2.1", "sdmx_ml31/with_codelist_extension.xml"): "2.1 has no CodelistExtension",
    # 3.1 removed <ReleaseCalendar> from DataConstraint. Refused with the role
    # (#246) until #421 made it a declared gap: a calendar left out asserts
    # nothing false.
    ("3.0", "3.1", "sdmx_ml30/minimal.xml"): "3.1 removed DataConstraint <ReleaseCalendar>",
    ("2.1", "3.1", "sdmx_ml21/complete.xml"): "3.1 removed DataConstraint <ReleaseCalendar>",
    ("2.1", "3.1", "sdmx_ml21/minimal.xml"): "3.1 removed DataConstraint <ReleaseCalendar>",
    # 3.1 removed DataConstraint/@role. Dropping it turned Actual into Allowed,
    # so it was refused (#246); since #421 an Actual constraint that fits is
    # written as 3.1's AvailabilityConstraint, which has no identity and which
    # the trip back to 3.0 leaves out.
    ("3.0", "3.1", "sdmx_ml30/complete.xml"): "3.1 states Actual content as an identity-less AvailabilityConstraint",
    # SDMX 3.0 deleted the MSD reporting layer, so a 2.1 MSD crossing 3.x
    # comes back with the marked layer 2.1 writes for an MSD with none
    # (#245, #421) — written, but not the document's own.
    ("2.1", "3.0", "sdmx_ml21/with_metadata_structure.xml"): "3.x cannot carry the 2.1 reporting layer",
    ("2.1", "3.1", "sdmx_ml21/with_metadata_structure.xml"): "3.x cannot carry the 2.1 reporting layer",
    # Refused until #421, which leaves each out of the format lacking it: the
    # hierarchies cross, the grouping or the binding does not.
    ("2.1", "3.0", "sdmx_ml21/with_hierarchy.xml"): "3.x has no HierarchicalCodelist",
    ("2.1", "3.1", "sdmx_ml21/with_hierarchy.xml"): "3.x has no HierarchicalCodelist",
    ("3.0", "2.1", "sdmx_ml30/with_hierarchy_association.xml"): "2.1 has no HierarchyAssociation",
    ("3.1", "2.1", "sdmx_ml31/with_hierarchy_association.xml"): "2.1 has no HierarchyAssociation",
    ("3.0", "2.1", "sdmx_ml30/with_multiple_hierarchies.xml"): "2.1 has no HierarchyAssociation",
    # The maps cross since #242; the declared set around them is the grouping
    # SDMX 3.0 deleted, refused until #421 left it out. Contrast the 3.0
    # fixture, whose map has no declared set: it survives 3.0 -> 2.1 -> 3.0
    # intact, because the wrapper the 2.1 leg invents is marked synthetic and
    # dropped again on read.
    ("2.1", "3.0", "sdmx_ml21/complete.xml"): "3.x has no StructureSet",
    ("2.1", "3.0", "sdmx_ml21/minimal.xml"): "3.x has no StructureSet",
    ("2.1", "3.0", "sdmx_ml21/with_structure_map.xml"): "3.x has no StructureSet",
    ("2.1", "3.1", "sdmx_ml21/with_structure_map.xml"): "3.x has no StructureSet",
}


def _documents() -> list[tuple[str, str]]:
    """(home version, relative path) for every checked-in SDMX-ML fixture."""
    found: list[tuple[str, str]] = []
    for path in sorted(_FIXTURES.rglob("*.xml")):
        blob = path.read_bytes()
        home = next((v for marker, v in _VERSION_OF.items() if marker in blob), None)
        if home is not None:
            found.append((home, str(path.relative_to(_FIXTURES))))
    return found


def _hops() -> list[tuple[str, str, str]]:
    """(home, via, fixture) for every version hop worth trying."""
    return [(home, via, name) for home, name in _documents() for via in _VERSIONS if via != home]


@pytest.mark.parametrize(("home", "via", "relative_path"), _hops())
def test_document_survives_a_version_hop(home: str, via: str, relative_path: str):
    """`home -> via -> home` must return the document, or be a declared block."""
    blob = (_FIXTURES / relative_path).read_bytes()
    key = (home, via, relative_path)

    if key in _BLOCKED:
        with pytest.raises(ValidationError):
            serialize(parse(serialize(parse(blob), version=via)), version=home)
        return

    intermediate = serialize(parse(blob), version=via)
    returned = serialize(parse(intermediate), version=home)
    missing, changed = compare(blob, returned)

    if key in _LOSSY:
        assert missing or changed, (
            f"{relative_path} now survives {home}->{via}->{home} — delete its _LOSSY entry ({_LOSSY[key]})"
        )
        return

    differences = [*(f"lost     {k}" for k in missing[:6]), *(f"changed  {k}" for k in changed[:6])]
    assert not differences, (
        f"{relative_path} loses information via {via}:\n  "
        + "\n  ".join(differences)
        + "\nEither fix it, or record it in _LOSSY or _BLOCKED with the reason"
    )


def test_the_hop_matrix_is_not_empty():
    """Guard against fixture discovery silently matching nothing."""
    hops = _hops()
    assert len(hops) > 30, f"only {len(hops)} hops found; discovery looks broken"
    assert {home for home, _via, _name in hops} == set(_VERSIONS)


def test_exemption_tables_only_name_real_hops():
    """A typo'd key would exempt nothing while looking like it exempted something."""
    known = set(_hops())
    for label, table in (("_BLOCKED", _BLOCKED), ("_LOSSY", _LOSSY)):
        unknown = sorted(key for key in table if key not in known)
        assert not unknown, f"{label} names {unknown}, which are not hops in the matrix"

    both = sorted(set(_BLOCKED) & set(_LOSSY))
    assert not both, f"{both} are listed as both blocked and lossy; a hop has one outcome"


def test_adjacent_versions_mostly_convert():
    """3.0 and 3.1 differ in a small, known set — most documents should survive.

    A canary rather than a precise assertion. 3.1 is a point release of 3.0,
    so a *majority* of hops between them failing would mean something
    structural had broken rather than one artefact gaining a constraint.

    The known differences are `Categorisation/@version` (prohibited in 3.1),
    `AttachmentConstraint` (absent from both, converted since #244), and
    `DataConstraint`'s `@role` and `<ReleaseCalendar>`, which 3.1 removed —
    the role written as an `AvailabilityConstraint` where it fits and refused
    where it does not (#246, #421), the calendar a declared gap (#421).
    """
    adjacent = [(h, v, n) for h, v, n in _hops() if {h, v} == {"3.0", "3.1"}]
    blocked = [key for key in adjacent if key in _BLOCKED]
    assert len(blocked) <= len(adjacent) // 4, (
        f"{len(blocked)} of {len(adjacent)} 3.0<->3.1 hops are blocked; that is too many for a point release"
    )
