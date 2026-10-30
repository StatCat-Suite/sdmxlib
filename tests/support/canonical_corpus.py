"""One fully populated artefact of every class, and its canonical text on disk (GH-126).

`corpus()` takes the first artefact of each class from `maximal.message()`,
whose every field is set, and adds the three classes that message does not
carry, populated the same way. The golden files under
``tests/fixtures/canonical/`` hold each one's canonical text, indented for
review; `tests/unit/test_canonical_json.py` compares them byte for byte.

Regenerate after a deliberate change to the canonical form or to the corpus,
and bump `CANONICAL_FORM` when the form itself changed::

    uv run python -m tests.support.canonical_corpus
"""

from datetime import date
from pathlib import Path

import attrs
import msgspec

from sdmxlib.edition import canonical_json
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.category import CategoryScheme
from sdmxlib.model.collections import ArtefactList
from sdmxlib.model.constraint import AttachmentConstraint, ConstraintAttachment, DataKey, DataKeySet
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.mapping import CategorySchemeMap, ItemMap
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from tests.support import maximal

#: Where the golden files live, one per class.
GOLDEN = Path(__file__).parents[1] / "fixtures" / "canonical"


def corpus() -> dict[str, MaintainableArtefact]:
    """One populated artefact per concrete class, by class name."""
    message = maximal.message()
    found: dict[str, MaintainableArtefact] = {}
    for field in attrs.fields(type(message)):
        value: object = getattr(message, field.name)
        if not isinstance(value, ArtefactList):
            continue
        for artefact in value:
            if isinstance(artefact, MaintainableArtefact):
                found.setdefault(type(artefact).__name__, artefact)
    (scheme, *_) = [a for a in found.values() if isinstance(a, CategoryScheme)]
    (flow, *_) = [a for a in found.values() if isinstance(a, Metadataflow)]
    (dsd, *_) = [a for a in found.values() if isinstance(a, DataStructure)]
    for extra in _the_rest(scheme, flow, dsd):
        found.setdefault(type(extra).__name__, extra)
    return dict(sorted(found.items()))


def _the_rest(scheme: CategoryScheme, flow: Metadataflow, dsd: DataStructure) -> tuple[MaintainableArtefact, ...]:
    """The classes `maximal.message()` does not carry, every field set."""
    attachment = AttachmentConstraint(
        **maximal._without_links(maximal._maintainable_21("ATC")),
        attachment=ConstraintAttachment(data_structures=(Ref.to(dsd),)),
        data_key_sets=(
            DataKeySet(is_included=True, keys=[DataKey(values=(("DIM", "A"),))]),
            DataKeySet(is_included=False, keys=[DataKey(values=(("DIM", "B"),))]),
        ),
    )
    category_map = CategorySchemeMap(
        **maximal._maintainable("CSM"),
        source=Ref.to(scheme),
        target=Ref.to(scheme),
        maps=(
            ItemMap(
                source_id="A",
                target_id="B",
                annotations=maximal._annotations("CSM-MAP"),
                valid_from=date(2020, 1, 1),
                valid_to=date(2030, 12, 31),
            ),
        ),
    )
    metadata_set = MetadataSet(
        **{key: value for key, value in maximal._maintainable("MDS").items() if key != "version"},
        version="1.0.0",
        structure=Ref.to(flow),
        # What the metadataflow targets, and the MSD's attributes as it defines
        # them: `MA` presentational, so its child carries the value (GH-29).
        targets=[Ref[object].unresolved(SdmxUrn.parse(flow.targets[0]))],
        attributes=[
            ReportedAttribute(
                id="MA",
                annotations=maximal._annotations("MDS-ATTR"),
                uri="https://example.org/uri/MDS-ATTR",
                children=[ReportedAttribute(id="MA_CHILD", value={"en": "child-en", "fr": "child-fr"})],
            )
        ],
    )
    return attachment, category_map, metadata_set


def golden(artefact: MaintainableArtefact) -> bytes:
    """The golden file's content: the canonical text, indented two spaces, keys still sorted."""
    return msgspec.json.format(canonical_json(artefact), indent=2) + b"\n"


def main() -> None:
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for stale in GOLDEN.glob("*.json"):
        stale.unlink()
    for name, artefact in corpus().items():
        (GOLDEN / f"{name}.json").write_bytes(golden(artefact))


if __name__ == "__main__":
    main()
