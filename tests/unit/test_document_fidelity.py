"""Reading a document and writing it back must not lose information (#237).

The generative round-trip properties start from a *model artefact* and check
``model -> D -> model``. That direction is structurally blind to a whole class
of bug: a field the reader never populates and the writer never emits was never
in the generated artefact, so nothing looks for it. This file runs the other
way — ``D -> model -> D`` over real documents — which is what makes such a loss
observable.

Every difference it has reported so far was one of three things, and telling
them apart was most of the work:

- **A library defect.** `EnumerationFormat` dropped, the TimeDimension
  `position` never emitted, `NCNameIDType` applied to maintainables the schema
  leaves permissive — that last one made sdmxlib refuse the ECB's own dataflow.
- **A comparator artefact.** Positional keys turning a reordering into 139
  differences; `urn` restatements; `maxOccurs="1"` where the XSD default is 1.
  Fixing these in `tests.support.fidelity` was correcting the *measurement*.
- **Legitimately regenerated.** The message `Header` describes the
  transmission — "an identification for the message, assigned by the sender" —
  not the artefacts, so a new message getting a new header is correct.

The equivalences are documented in `tests.support.fidelity`; each is a claim
about what the format says two documents mean, not a convenience.
"""

import pathlib

import pytest

from sdmxlib.formats import parse, serialize
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.validation import ValidationError
from tests.support.fidelity import compare
from tests.support.roundtrip import as_structure

pytestmark = pytest.mark.unit

_FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"

#: Namespace marker -> the `serialize` version that reproduces it.
_VERSION_OF = {b"schemas/v2_1/": "2.1", b"schemas/v3_0/": "3.0", b"schemas/v3_1/": "3.1"}

#: Documents that do not yet round-trip, with the reason. The burn-down for
#: #237, in the same shape as the conformance gate's `KNOWN_INVALID`: entries
#: are asserted *still* lossy, so fixing one fails here until it is removed.
#: Documents the library reads but declines to write back, with the reason.
#:
#: This is the *acceptable* failure under #237's rule: a refusal names what
#: cannot be represented, where a silent drop does not. It is not a free pass —
#: an entry here is a standing admission that the model cannot hold something
#: the format carries, and each names the issue tracking that.
_REFUSED_ON_WRITE: dict[str, str] = {
    # Empty since #245 modelled the 2.1 reporting layer. Kept because the
    # next construct SDMX cannot carry both ways will want somewhere to be
    # recorded, and the guards below assert the table stays honest.
}

_KNOWN_LOSSY: dict[str, str] = {
    # Empty since #238 carried `uri` through every identifiable artefact. The
    # two entries here were the ECB fixtures, whose DSD links to the bank's
    # statistics vocabulary — the last non-structural difference in the corpus.
    # Kept because the next such loss wants somewhere to be recorded, and the
    # guards below assert the table stays honest.
}


def _sdmx_ml_documents() -> list[str]:
    """Every checked-in SDMX-ML fixture, as a path relative to `tests/fixtures`."""
    found = []
    for path in sorted(_FIXTURES.rglob("*.xml")):
        blob = path.read_bytes()
        if any(marker in blob for marker in _VERSION_OF):
            found.append(str(path.relative_to(_FIXTURES)))
    return found


def _version_for(blob: bytes) -> str:
    return next(version for marker, version in _VERSION_OF.items() if marker in blob)


@pytest.mark.parametrize("relative_path", _sdmx_ml_documents())
def test_document_round_trips_without_loss(relative_path: str):
    """`serialize(as_structure(parse(D)))` must carry everything `D` carried.

    Compared by element identity rather than position, so a different section
    order is not a difference; see `tests.support.fidelity` for the full set of
    equivalences and why each holds.
    """
    blob = (_FIXTURES / relative_path).read_bytes()
    version = _version_for(blob)
    expected_lossy = relative_path in _KNOWN_LOSSY

    if relative_path in _REFUSED_ON_WRITE:
        # A declared refusal is the acceptable failure: it names what cannot be
        # represented instead of dropping it in silence. Asserted rather than
        # tolerated, so a document that starts round-tripping forces the entry
        # out — and one that starts *silently* losing fails here.
        with pytest.raises(ValidationError):
            serialize(as_structure(parse(blob)), version=version)
        return

    missing, changed = compare(blob, serialize(as_structure(parse(blob)), version=version))

    if expected_lossy:
        assert missing or changed, (
            f"{relative_path} now round-trips losslessly — delete its _KNOWN_LOSSY entry "
            f"({_KNOWN_LOSSY[relative_path]})"
        )
        return

    differences = [*(f"lost     {key}" for key in missing[:8]), *(f"changed  {key}" for key in changed[:8])]
    assert not differences, (
        f"{relative_path} loses information when written back as SDMX-ML {version}:\n  "
        + "\n  ".join(differences)
        + "\nEither fix it, or record it in _KNOWN_LOSSY with the issue that tracks it"
    )


def test_the_corpus_is_not_empty():
    """Guard against the fixture discovery silently matching nothing.

    Every assertion above is vacuously true if `_sdmx_ml_documents` stops
    finding files — a moved fixture directory, a namespace change in the
    marker table.
    """
    documents = _sdmx_ml_documents()
    assert len(documents) > 15, f"only {len(documents)} SDMX-ML fixtures found; discovery looks broken"
    assert any(d.startswith("sdmx_ml21/") for d in documents)
    assert any(d.startswith("sdmx_ml30/") for d in documents)


def test_known_lossy_only_names_real_fixtures():
    """A typo'd path would exempt nothing while looking like it exempted something."""
    documents = set(_sdmx_ml_documents())
    unknown = sorted((set(_KNOWN_LOSSY) | set(_REFUSED_ON_WRITE)) - documents)
    assert not unknown, f"{unknown} are exempted but are not SDMX-ML fixtures"

    both = sorted(set(_KNOWN_LOSSY) & set(_REFUSED_ON_WRITE))
    assert not both, f"{both} are listed as both lossy and refused; a document cannot be reached by two outcomes"


def contact_concept(msd: MetadataStructure) -> object:
    """The CONTACT attribute's concept ref, for readability in the assertion."""
    return msd.attributes["CONTACT"].concept


def test_reading_a_2_1_metadata_structure_recovers_its_attributes():
    """A 2.1 MSD must survive `parse`, flattened but intact (#236).

    Before this the 2.1 reader had no `<str:MetadataStructures>` handling at
    all: a schema-valid document containing one parsed "successfully" and
    yielded zero metadata structures — id, name and attribute tree gone, no
    exception, no warning.

    That was the only unread 2.1 section whose artefact sdmxlib models. The
    other eight are types with no model counterpart, where ignoring the
    section is defensible because there is nowhere to put the result.

    What is *not* recovered is the reporting layer: 2.1 groups attributes into
    `ReportStructure`s that name the `MetadataTarget`s they describe, and this
    model follows 3.0, which deleted it. The attributes of every report
    structure are concatenated in document order. Writing back to 2.1 then
    refuses rather than inventing the layer — asserted above via
    `_REFUSED_ON_WRITE`.
    """
    blob = (_FIXTURES / "sdmx_ml21/with_metadata_structure.xml").read_bytes()
    msg = as_structure(parse(blob))

    assert len(msg.metadata_structures) == 1, "the artefact must not vanish"
    msd = msg.metadata_structures[0]
    assert (msd.id, msd.agency_id, msd.version) == ("MSD_X", "AG", "1.0")
    assert msd.name.get("en") == "Quality report MSD"

    # Two ReportStructures, concatenated in document order.
    assert [a.id for a in msd.attributes] == ["CONTACT", "QUALITY"]

    assert contact_concept(msd) is not None, "the ConceptIdentity must survive"

    contact = msd.attributes["CONTACT"]
    assert [c.id for c in contact.children] == ["EMAIL"], "nesting must survive the flattening of report structures"

    quality = msd.attributes["QUALITY"]
    assert (quality.min_occurs, quality.max_occurs) == (0, None), "unbounded maxOccurs reads as None"
