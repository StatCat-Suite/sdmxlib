"""Read a real provider's structure message, write it back, and lose nothing.

`tests/unit/test_field_coverage.py` asks the same question of a *synthetic*
message: one artefact per slot, every field populated. That is the right shape
for finding gaps in what the library can express, and the wrong shape for
finding what real publishers actually put on the wire. Providers use the
information model differently from a fixture author — deep code hierarchies,
partial concept schemes, agencies with contacts, constraints with hundreds of
regions, ids and versions no test would think to type.

So this runs the same census over messages fetched from the OECD, ECB, BIS,
IMF and the SDMX Global Registry, replayed from cassettes.

**The comparison is model-to-model, not byte-to-byte.** `parse(original)`
against `parse(write(parse(original)))`. A byte diff would report attribute
ordering, namespace prefix choice and whitespace — none of which is information
the round-trip lost — and would report nothing about the thing that matters.
Emitted bytes are XSD-validated separately, which is what catches a writer
producing non-conformant XML that our own reader happens to tolerate.

**What this can and cannot catch.** It catches a regression in any field the
corpus actually populates — deleting the writer's `Code.description` fails three
cases here. It cannot catch one in a field no provider fills: the BIS hierarchy
carries `is_final=False` and unnamed nodes, so breaking either is invisible to
this file. That is the division of labour with the unit census, which populates
everything and catches exactly those. Neither replaces the other.

**The corpus is chosen for information-model coverage, not size.** A 37 MB
dataflow listing is breadth of *dataflows*; a 7 KB hierarchy is breadth of the
*model*. `test_the_corpus_covers_the_artefact_kinds_providers_publish` holds
that line, so a source going dark shows up as a coverage failure rather than as
quietly fewer assertions.
"""

from __future__ import annotations

import re
import urllib.request
import warnings
from typing import NamedTuple

import pytest

from sdmxlib.formats import parse
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.formats.sdmx_ml30 import writer as ml30_writer
from sdmxlib.model.errors import ImRuleWarning
from sdmxlib.model.message import StructureMessage
from tests.support.field_census import differences, generalise
from tests.support.xsd import SchemaVersion, assert_valid_sdmx_ml, compile_structure_schema

pytestmark = pytest.mark.integration

_ACCEPT = {
    "2.1": "application/vnd.sdmx.structure+xml;version=2.1",
    "3.0": "application/vnd.sdmx.structure+xml;version=3.0.0",
}
_WRITER = {"2.1": ml21_writer, "3.0": ml30_writer}


class Source(NamedTuple):
    """One real message: where it comes from and what it is there to exercise."""

    id: str
    #: `SchemaVersion`, not `str`: every value here is one of the compiled
    #: schema sets, and typing it as such is what lets `compile_structure_schema`
    #: take it directly instead of the call site asserting a literal it already
    #: knows (#347).
    version: SchemaVersion
    url: str
    #: Message slots this source is expected to fill. Asserted, so a provider
    #: quietly returning an empty document fails instead of passing vacuously.
    covers: frozenset[str]


#: Chosen by probing each provider and measuring which artefact kinds a
#: response actually fills, against its size. Each entry earns its place by
#: covering something no other entry does.
SOURCES = (
    Source(
        "bis-dsd",
        "2.1",
        "https://stats.bis.org/api/v1/datastructure/BIS/BIS_CBPOL/latest?references=all&detail=referencepartial",
        frozenset({"codelists", "concept_schemes", "data_structures", "dataflows", "agency_schemes"}),
    ),
    Source(
        "ecb-categories",
        "2.1",
        "https://data-api.ecb.europa.eu/service/categoryscheme/ECB/all/latest?references=all",
        frozenset({"category_schemes", "categorisations", "dataflows", "agency_schemes"}),
    ),
    Source(
        # Every BIS codelist only wraps its one hierarchy, so it is read as
        # that hierarchy (#421); `oecd-hierarchies` is the HCL source.
        "bis-hierarchies",
        "2.1",
        "https://stats.bis.org/api/v1/hierarchicalcodelist/BIS/all/latest?references=all",
        frozenset({"hierarchies", "codelists"}),
    ),
    Source(
        # A HierarchicalCodelist holding three hierarchies, H, H_EN and H_FR —
        # OECD's shape, whose 210 `H`s collapsed onto one URN before #421.
        "oecd-hierarchies",
        "2.1",
        "https://sdmx.oecd.org/public/rest/hierarchicalcodelist/OECD.EDU.IMEP/HCL_LF_STATUS/1.0",
        frozenset({"hierarchies", "hierarchical_codelists"}),
    ),
    Source(
        "bis-provision-agreements",
        "2.1",
        "https://stats.bis.org/api/v1/provisionagreement/all/all/latest",
        frozenset({"provision_agreements"}),
    ),
    Source(
        # Added by the #359 hunt: the only source found that attaches a
        # constraint to something other than a dataflow, and that carries
        # maintainable validity dates. Both were registered as losses on
        # fixture evidence; this is what disproved them.
        "bis-constraints",
        "2.1",
        "https://stats.bis.org/api/v1/contentconstraint/BIS/all/latest",
        frozenset({"constraints"}),
    ),
    Source(
        # Attributes related to a measure and to a group — the two halves of
        # the `AttributeRelationship` choice the synthetic fixture could not
        # express, because it set both at once.
        "bis-all-dsds",
        "2.1",
        "https://stats.bis.org/api/v1/datastructure/BIS/all/latest?references=all&detail=referencepartial",
        frozenset({"codelists", "concept_schemes", "data_structures", "dataflows", "agency_schemes"}),
    ),
    Source(
        "imf-msd",
        "2.1",
        "https://sdmxcentral.imf.org/ws/public/sdmxapi/rest/metadatastructure/all/all/latest?references=all",
        frozenset({"metadata_structures", "concept_schemes"}),
    ),
    Source(
        "oecd-dsd",
        "2.1",
        "https://sdmx.oecd.org/public/rest/datastructure/ESTAT/SEEAAIR/latest?references=all&detail=referencepartial",
        frozenset({"codelists", "concept_schemes", "data_structures"}),
    ),
    # ── SDMX 3.0, from the Global Registry ───────────────────────────────────
    Source(
        "registry-hierarchies-30",
        "3.0",
        "https://registry.sdmx.org/sdmx/v2/structure/hierarchy/*/*/*",
        frozenset({"hierarchies"}),
    ),
    Source(
        "registry-representation-maps-30",
        "3.0",
        "https://registry.sdmx.org/sdmx/v2/structure/representationmap/*/*/*",
        frozenset({"representation_maps"}),
    ),
    Source(
        "registry-provider-schemes-30",
        "3.0",
        "https://registry.sdmx.org/sdmx/v2/structure/dataproviderscheme/*/*/*",
        frozenset({"data_provider_schemes"}),
    ),
    Source(
        "registry-msd-30",
        "3.0",
        "https://registry.sdmx.org/sdmx/v2/structure/metadatastructure/*/*/*",
        frozenset({"metadata_structures"}),
    ),
    Source(
        "registry-categories-30",
        "3.0",
        "https://registry.sdmx.org/sdmx/v2/structure/categoryscheme/*/*/*",
        frozenset({"category_schemes"}),
    ),
)

#: Losses this test tolerates. **Empty, and that is the measured result** —
#: every message in the corpus round-trips with zero differences.
#:
#: It did not start empty. It started as a defensive copy of the register in
#: `tests/unit/test_field_coverage.py`, forty entries carried over on the
#: assumption that real data would hit the same bugs the synthetic fixture
#: does. Measuring said otherwise: real publishers do not populate the fields
#: those bugs are in. The BIS hierarchy carries `is_final=False` and unnamed
#: nodes; nothing in the corpus set a component's `related_measure` back when
#: that field existed (#360 removed it).
#:
#: A tolerance list that tolerates nothing is worse than none. It reads as
#: "forty known losses here" when the truth is "none", and — the part that
#: matters — it would have swallowed a regression that introduced any of the
#: forty. So it is empty and the assertion is strict: **any** difference fails.
#:
#: Adding an entry is legitimate when a new source genuinely exercises a bug
#: tracked in #355. It must be justified by a measurement, not by symmetry with
#: the unit register.
KNOWN_LOSSES: frozenset[str] = frozenset()


def _fetch(source: Source) -> bytes:
    # A User-Agent is not optional: sdmx.oecd.org answers 403 to urllib's
    # default one. The other four do not care.
    headers = {"Accept": _ACCEPT[source.version], "User-Agent": "sdmxlib-tests"}
    request = urllib.request.Request(source.url, headers=headers)  # noqa: S310
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        body: bytes = response.read()
        return body


def _filled_slots(message: StructureMessage) -> set[str]:
    return {a.name for a in StructureMessage.__attrs_attrs__ if not a.name.startswith("_") and getattr(message, a.name)}  # pyrefly: ignore[missing-attribute]


#: What each source's read warns of (#481): every IM rule a real provider breaks
#: and no schema enforces, as ``path — message``. A source not listed warns of
#: nothing. Measured over every swept registry, these are rare: BIS's three
#: sequences without an interval were the only facets of ~1.05M to warn.
WARNED: dict[str, list[str]] = {
    # A free-text title declared a sequence, with nothing to step by.
    "bis-all-dsds": [
        "DataStructure:CBTA.attributes[TITLE].representation — is_sequence=True with no interval or time_interval"
    ],
}


def _round_trip(source: Source) -> tuple[StructureMessage, StructureMessage, bytes]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ImRuleWarning)
        original = parse(_fetch(source))
    warned = sorted(
        str(issue)
        for warning in caught
        if isinstance(warning.message, ImRuleWarning)
        for issue in warning.message.issues
    )
    assert warned == WARNED.get(source.id, []), f"{source.id} warns of something new: {warned}"
    # Declared as `StructureMessage` because that is what these sources serve
    # and what every caller indexes into; `parse` cannot know that.
    assert isinstance(original, StructureMessage)
    written = _WRITER[source.version].write(original)
    # The same rules, warned again as the written message is read back.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ImRuleWarning)
        reread = parse(written)
    assert isinstance(reread, StructureMessage), "a structure message must re-read as one"
    return original, reread, written


@pytest.mark.vcr
@pytest.mark.parametrize("source", SOURCES, ids=lambda s: s.id)
def test_real_world_message_round_trips(source: Source) -> None:
    """Read a live message, write it, read it back — nothing unregistered changes."""
    original, back, _ = _round_trip(source)

    missing = source.covers - _filled_slots(original)
    assert not missing, (
        f"{source.id} was expected to exercise {sorted(missing)} and did not — the "
        "provider's response changed shape, so this case is now testing less than it "
        "claims. Re-record and update `covers`, or replace the source."
    )

    unregistered: list[str] = []
    for slot in sorted(_filled_slots(original)):
        written_artefacts = list(getattr(original, slot))
        read_artefacts = list(getattr(back, slot))
        if len(written_artefacts) != len(read_artefacts):
            unregistered.append(f"{slot}: {len(written_artefacts)} written, {len(read_artefacts)} read back")
            continue
        for before, after in zip(written_artefacts, read_artefacts, strict=True):
            for difference in differences(before, after):
                path = generalise(f"{slot}{difference.path}")
                if path not in KNOWN_LOSSES:
                    unregistered.append(f"{path}  ({difference.before!r} -> {difference.after!r})")

    assert not unregistered, (
        f"{source.id} ({source.version}) loses fields that are not registered:\n"
        + "\n".join(f"    {line}" for line in sorted(set(unregistered))[:40])
        + "\n\nA real provider populated something the synthetic fixture does not. "
        "Triage it in #355 and register it in both this file and "
        "tests/unit/test_field_coverage.py, or fix it."
    )


@pytest.mark.vcr
@pytest.mark.parametrize("source", SOURCES, ids=lambda s: s.id)
def test_what_we_write_back_is_schema_valid(source: Source) -> None:
    """The emitted bytes validate against the official XSD.

    Separate from the round-trip because it catches the opposite failure: a
    writer emitting non-conformant XML that our own reader tolerates, which
    round-trips perfectly and is still wrong on anyone else's parser.
    """
    _, _, written = _round_trip(source)
    assert_valid_sdmx_ml(written, compile_structure_schema(source.version))


@pytest.mark.vcr
def test_the_corpus_covers_the_artefact_kinds_providers_publish() -> None:
    """The corpus keeps its breadth as sources are added and replaced.

    Without this, a source going dark or a URL quietly returning an empty
    document costs coverage silently — the per-source tests would still pass,
    over less. Asserting the union names what the corpus is *for*.
    """
    covered: set[str] = set()
    for source in SOURCES:
        covered |= source.covers
    expected = {
        "agency_schemes",
        "categorisations",
        "category_schemes",
        "codelists",
        "concept_schemes",
        "constraints",
        "data_provider_schemes",
        "data_structures",
        "dataflows",
        "hierarchical_codelists",
        "hierarchies",
        "metadata_structures",
        "provision_agreements",
        "representation_maps",
    }
    assert covered == expected, (
        f"corpus coverage moved: missing {sorted(expected - covered)}, unexpected {sorted(covered - expected)}"
    )


def test_the_uncovered_slots_are_named_rather_than_forgotten() -> None:
    """What no provider in the corpus publishes, and why — checked, not assumed.

    These are the message slots the corpus does *not* reach. Writing them down
    is the difference between "no provider publishes this" and "we never
    looked". Each was probed and the endpoint 404s or no source publishes it.

    `constraints` used to be here, excluded because the Global Registry's
    response is 95 MB. The #359 hunt found BIS publishes the same type in 90 KB,
    so the exclusion was about one endpoint rather than about the type — which
    is exactly the kind of thing a written-down list gets re-examined for.
    """
    covered: set[str] = set()
    for source in SOURCES:
        covered |= source.covers
    all_slots = {a.name for a in StructureMessage.__attrs_attrs__ if not a.name.startswith("_")}  # pyrefly: ignore[missing-attribute]
    assert all_slots - covered == {
        # 2.1-only container the REST APIs do not expose as a resource.
        "structure_sets",
        # No source in the corpus publishes these; the endpoints 404.
        "attachment_constraints",
        "category_scheme_maps",
        "concept_scheme_maps",
        "hierarchy_associations",
        # Gained a slot in #362. The Global Registry's endpoint 404s and no
        # other source in the corpus publishes one, so it stays synthetic-only.
        "metadata_provision_agreements",
        # Gained a slot in #315, and the same story: the metadata *provider*
        # half of the 3.0 provisioning layer is not published by any source in
        # the corpus either. Covered synthetically by the maximal-fixture
        # census instead.
        "metadata_provider_schemes",
        "metadataflows",
        "structure_maps",
        # Gained a slot in #422. **Not yet probed** against the corpus's
        # sources, unlike the rest of this list: covered synthetically by the
        # maximal-fixture census and `test_value_lists.py` until someone does.
        "value_lists",
        # Gained a slot in #421. No source publishes a 3.1 AvailabilityConstraint
        # (registry.sdmx.org's endpoint returns an empty document); covered by
        # the 3.1 shapes census and `test_availability_constraint.py`.
        "availability_constraints",
        # No swept registry serves one: every `organisationschememap` endpoint
        # answers 404 or 422 (#448). Covered by the maximal fixture and
        # `test_organisation_scheme_map.py`.
        "organisation_scheme_maps",
        # Gained slots in #469. The Global Registry publishes a DATA_CONSUMERS
        # scheme with no consumers in it, so a cassette of it would cover the
        # scheme's shell and nothing else; no swept registry serves an
        # organisation unit scheme. Both are covered by the maximal fixture.
        "data_consumer_schemes",
        "organisation_unit_schemes",
    }


def test_cassettes_are_replayed_not_fetched() -> None:
    """The corpus is real data, and the suite still runs offline.

    `just test-integration` passes `--block-network`. This asserts the URLs are
    the shape the cassettes were recorded from, so a hand-edited URL fails here
    rather than as a confusing connection error inside a replay.
    """
    for source in SOURCES:
        assert re.match(r"^https://", source.url), source.url
        assert source.version in _ACCEPT, f"{source.id} has no Accept header for {source.version}"
