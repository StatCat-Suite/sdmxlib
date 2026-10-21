"""Every item class declares the URN pair, and the pairs are SDMX's (#404).

`validate_references` builds an item URN from the `sdmx_package` /
`sdmx_class` pair the item class declares, and `_refshape.item_fields` finds
the item-bearing fields by looking for classes that carry it. Both of those
make the declarations *load-bearing*: an item class without the pair is an item
whose references all report as broken, silently and only for that class — which
is the shape of the defect the pair replaced, where a hand-written table listed
`ConceptScheme` and nothing else.

So there are two questions here, and neither is answerable from the model
alone:

1. **Does every item class declare a pair?** Asked against a fresh reflective
   walk of `sdmxlib.model`, with an exclusion set that has to name its reasons.
2. **Is the declared pair the one SDMX gives that class?** Asked against the
   vendored XSDs, which enumerate the item URN classes in 3.0 and the object
   types in 2.1. A pair invented here — right-looking, wrong package — would
   build a URN no producer ever writes, and the differential answer is in the
   standard rather than in a second copy of our own table.
"""

import importlib
import pkgutil
import re
from pathlib import Path

import attrs
import pytest

import sdmxlib.model as model_package
from sdmxlib.model._items import CLASS_ALIAS_GROUPS
from sdmxlib.model.base import MaintainableArtefact

pytestmark = pytest.mark.unit

_SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"

#: Identifiable classes that are deliberately **not** SDMX items, with the
#: reason. Each name must still exist — `test_every_exclusion_is_a_real_class`
#: is what stops this becoming a place where a renamed class hides.
_NOT_AN_ITEM: dict[str, str] = {
    "Annotation": "an annotation is not identifiable in SDMX; neither URN vocabulary lists it",
    "DataSetReference": "2.1's reference to a data set — a provider and the set's id — not an artefact (#422)",
    "Contact": "a contact's `@id` is a user id for it, not an identity: `ContactType` is not identifiable (#422)",
    "MetadataAttributeUsage": "the 3.0 schema prohibits its id; it is known by the MSD attribute it uses (#422)",
    "ReportedAttribute": "a *populated* metadata value inside a MetadataSet, not a structural item of a scheme",
    "SdmxRef": "a reference, not an artefact",
    "Party": "a message header's party (#480): `PartyType` has an id and no URN",
    "Sender": "a message header's sender (#480): a `Party` with a time zone",
    "Header": "a message's envelope (#480), not an artefact",
    "MetadataHeader": "a metadata message's envelope (#480), not an artefact",
    "ValueItem": "a value list's value is not identifiable in SDMX: no URN, and its id is any string (#422)",
    "SdmxUrn": "an identity, not an artefact",
    "TargetObject": (
        "2.1's URN class is its kind's — five classes for one model class, so no one pair — and nothing in "
        "the model references one by URN: report structures name a MetadataTarget, by id (#422)"
    ),
}


def _model_classes() -> dict[str, type]:
    """Every attrs class defined under `sdmxlib.model`, by name."""
    found: dict[str, type] = {}
    for module_info in pkgutil.iter_modules(model_package.__path__):
        module = importlib.import_module(f"sdmxlib.model.{module_info.name}")
        for obj in vars(module).values():
            if isinstance(obj, type) and attrs.has(obj) and obj.__module__ == module.__name__:
                found[obj.__name__] = obj
    return found


def _candidate_items() -> dict[str, type]:
    """Classes that *look* like items: identifiable, and not maintainable."""
    return {
        name: cls
        for name, cls in _model_classes().items()
        if not issubclass(cls, MaintainableArtefact) and any(f.name == "id" for f in attrs.fields(cls))
    }


def _urn_classes_30() -> set[tuple[str, str]]:
    """`(package, class)` for every URN the 3.0 schemas name a pattern for."""
    pattern = re.compile(r"\.([a-z]+)\\\.([A-Za-z]+)=")
    found: set[tuple[str, str]] = set()
    for xsd in (_SCHEMAS / "sdmx_ml30").glob("*.xsd"):
        found.update(pattern.findall(xsd.read_text(encoding="utf-8")))
    return found


def _object_types_21() -> set[str]:
    """Every class name 2.1's reference schema enumerates."""
    text = (_SCHEMAS / "sdmx_ml21" / "SDMXCommonReferences.xsd").read_text(encoding="utf-8")
    return set(re.findall(r'<xs:enumeration value="([A-Za-z]+)"\s*/>', text))


def test_the_vendored_vocabularies_are_readable() -> None:
    """The discrimination check for the two tests that read them.

    Both oracles below are "is this name in that set", which a missing or
    renamed schema file turns into a vacuous pass — or, worse, a pass that
    reports every declaration as wrong for one reason nobody reads. Assert the
    sets are populated and contain a name we know, so a bad refresh fails here
    with a clear cause rather than there with an obscure one.
    """
    urns_30 = _urn_classes_30()
    assert ("codelist", "Code") in urns_30
    assert ("datastructure", "DataAttribute") in urns_30
    assert len(urns_30) > 50, f"only {len(urns_30)} URN patterns found — did the 3.0 schema layout change?"

    types_21 = _object_types_21()
    assert {"Code", "Attribute", "PrimaryMeasure"} <= types_21
    assert len(types_21) > 50, f"only {len(types_21)} object types found — did the 2.1 schema layout change?"


def test_every_item_class_declares_its_urn_pair() -> None:
    """An item class without the pair is invisible to `item_fields`.

    Which means every reference to one of its items reports as broken — for
    that class only, so the suite stays green and the symptom shows up in a
    user's message. #404 is what that looks like: `Code`, `Category`,
    `Dimension` and `MetadataAttribute` were all in this position, and a real
    3.0 message produced 1,388 issues of which none were real.
    """
    missing = [
        name
        for name, cls in _candidate_items().items()
        if name not in _NOT_AN_ITEM
        and (getattr(cls, "sdmx_class", None) is None or getattr(cls, "sdmx_package", None) is None)
    ]
    assert missing == [], (
        f"item classes with no sdmx_package/sdmx_class: {missing}. "
        "Declare the pair SDMX gives the class, or add it to _NOT_AN_ITEM with the reason"
    )


def test_every_exclusion_is_a_real_class() -> None:
    """`_NOT_AN_ITEM` names classes, so a rename must not quietly widen it.

    An entry that matches nothing excuses nothing, and the test above would go
    on passing while the class it was meant to cover grew an id and slipped
    through — the guard would still be green and no longer be about anything.
    """
    unknown = sorted(set(_NOT_AN_ITEM) - set(_candidate_items()))
    assert unknown == [], f"_NOT_AN_ITEM names classes that are no longer identifiable non-maintainables: {unknown}"


def test_declared_pairs_are_the_ones_sdmx_gives() -> None:
    """The pair must be SDMX's, not a plausible-looking guess.

    `Group` is why this is worth a test rather than a reading: SDMX names a
    group's URN class for the descriptor that holds it —
    ``datastructure.GroupDimensionDescriptor`` — so the pair cannot be derived
    from the class name, and a wrong one would build URNs nothing writes.

    A class 3.0 dropped is checked against 2.1's object types instead, by name:
    2.1 enumerates classes without packages, so that half of the oracle is
    weaker, and it covers `MeasureDimension`, `MetadataTarget` and
    `ReportStructure`.
    """
    urns_30 = _urn_classes_30()
    classes_30 = {cls for _, cls in urns_30}
    types_21 = _object_types_21()

    unknown: list[str] = []
    for name, cls in _candidate_items().items():
        if name in _NOT_AN_ITEM:
            continue
        pair = (getattr(cls, "sdmx_package", None), getattr(cls, "sdmx_class", None))
        package, artefact_class = pair
        if artefact_class is None or package is None:
            continue  # reported by test_every_item_class_declares_its_urn_pair
        if pair in urns_30:
            continue
        if artefact_class in types_21 and artefact_class not in classes_30:
            continue  # a 2.1-only class: 3.0 dropped it, so only the name can be checked
        unknown.append(f"{name} declares {package}.{artefact_class}")
    assert unknown == [], f"pairs that appear in neither vendored URN vocabulary: {unknown}"


def test_every_maintainable_pair_is_in_the_sdmx_vocabulary() -> None:
    """The same oracle, one level up — and the hole that let `structuremap` in.

    Four map classes declared ``sdmx_package = "structuremap"``, a string in no
    SDMX vocabulary: 2.1 spells the package ``mapping``, 3.0 and 3.1 spell it
    ``structuremapping``. Nothing caught it because nothing put a map's URN
    anywhere a schema could read it until the SDMX-JSON writer emitted a
    self-URN (#406) — at which point it invalidated every document containing
    one.

    This checks the maintainables the way `test_declared_pairs_are_the_ones_sdmx_gives`
    checks the items, against the same two vendored vocabularies.

    `DataflowBinding` is absent by construction rather than by exemption: it
    declares the pair but is not a `MaintainableArtefact`, because it is
    sdmxlib's own artefact and has no SDMX vocabulary entry to match.
    """
    urns_30 = _urn_classes_30()
    classes_30 = {cls for _, cls in urns_30}
    types_21 = _object_types_21()

    found: dict[str, type] = {}

    def walk(cls: type) -> None:
        for sub in cls.__subclasses__():
            found[sub.__name__] = sub
            walk(sub)

    walk(MaintainableArtefact)
    assert len(found) >= 24, (
        f"only {len(found)} maintainables found — a floor rather than an exact count because a new "
        "artefact is a normal addition, but a walk that goes blind fails here"
    )
    assert {"Codelist", "DataStructure", "StructureMap"} <= set(found), "the walk is missing known artefacts"

    unknown: list[str] = []
    for name, cls in sorted(found.items()):
        pair = (getattr(cls, "sdmx_package", None), getattr(cls, "sdmx_class", None))
        package, artefact_class = pair
        if package is None or artefact_class is None:
            unknown.append(f"{name} declares no pair")
        elif pair in urns_30:
            continue
        elif artefact_class in types_21 and artefact_class not in classes_30:
            continue  # a 2.1-only artefact: 3.0 dropped it, so only the name can be checked
        else:
            unknown.append(f"{name} declares {package}.{artefact_class}")
    assert unknown == [], f"maintainable pairs in neither vendored URN vocabulary: {unknown}"


def test_each_alias_group_holds_one_declared_spelling_and_one_the_model_lacks() -> None:
    """What `CLASS_ALIAS_GROUPS` is for, stated as an assertion.

    SDMX renamed two component classes between 2.1 and 3.0, so a URN carries
    whichever spelling produced it while the model declares exactly one. Each
    group must therefore hold **one** declared spelling — otherwise the folding
    is either unnecessary (both declared, so both already resolve) or wrong
    (neither declared, so it folds onto nothing).

    Which version each declared spelling comes from is deliberately not
    asserted: the model declares the class's own name, and that happens to be
    2.1's for `Attribute` and 3.0's for `Measure`. Every member must still be a
    name SDMX uses, or the alias would accept a reference nothing could write.
    """
    classes_30 = {cls for _, cls in _urn_classes_30()}
    types_21 = _object_types_21()
    declared = {
        artefact_class
        for cls in _candidate_items().values()
        if isinstance(artefact_class := getattr(cls, "sdmx_class", None), str)
    }
    for group in CLASS_ALIAS_GROUPS:
        for spelling in group:
            assert spelling in classes_30 | types_21, f"{spelling!r} is in neither SDMX vocabulary"
        assert len(set(group) & declared) == 1, (
            f"{group} must hold exactly one spelling the model declares — "
            f"declared here: {sorted(set(group) & declared)}"
        )
