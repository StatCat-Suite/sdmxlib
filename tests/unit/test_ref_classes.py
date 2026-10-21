"""A reference must name a class the target format can reference (#430).

A reference is written as its URN's package and class, unchecked. BIS
categorises Fusion Metadata Registry artefacts SDMX does not define
(`publicationtable.PublicationTable`, `pdq.PreDefinedQuery`); the reader keeps
them faithfully, and writing them produced 209 categorisations every format
rejected. They are refused at the write now.
"""

import pytest

from sdmxlib.formats import serialize
from sdmxlib.model import _referenceable
from sdmxlib.model.category import Categorisation, Category
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import ValidationError
from tests.support.referenceable import from_schemas

pytestmark = pytest.mark.unit


def test_the_tables_are_what_the_schemas_say() -> None:
    """Drift guard: regenerate `model/_referenceable.py` when a schema refresh moves this."""
    schemas = from_schemas()
    assert schemas["2.1_classes"] == _referenceable.CLASSES_21
    assert schemas["2.1_packages"] == _referenceable.PACKAGES_21
    assert schemas["3.0"] == _referenceable.PAIRS_30
    assert schemas["3.1"] == _referenceable.PAIRS_31
    assert schemas["json"] == _referenceable.PAIRS_JSON


def test_the_tables_can_disagree() -> None:
    """Discrimination: the targets really differ, so one table would not do."""
    assert ("base", "Any") in _referenceable.PAIRS_30
    assert ("base", "Any") not in _referenceable.PAIRS_JSON
    assert "ContentConstraint" in _referenceable.CLASSES_21
    assert ("registry", "ContentConstraint") not in _referenceable.PAIRS_30


def _categorisation(source: str) -> StructureMessage:
    target = SdmxUrn.make(
        Category, package="categoryscheme", artefact_class="Category", agency="BIS", id="CS", version="1.0", item_id="C"
    )
    return StructureMessage(
        categorisations=[
            Categorisation(
                id="CAT",
                maintainer="BIS",
                name={"en": "c"},
                source=Ref[object].unresolved(SdmxUrn.parse(source)),
                target=Ref[Category].unresolved(target),
            )
        ]
    )


_PUBLICATION_TABLE = "urn:sdmx:org.sdmx.infomodel.publicationtable.PublicationTable=BIS:CPMI_CT13B(1.0)"


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1", "json"])
def test_a_reference_to_a_registry_proprietary_class_is_refused(version: str) -> None:
    with pytest.raises(ValidationError, match=r"publicationtable\.PublicationTable is not a class"):
        serialize(_categorisation(_PUBLICATION_TABLE), version=version)


@pytest.mark.parametrize("version", ["2.1", "3.0", "3.1", "json"])
def test_a_reference_to_an_sdmx_class_is_written(version: str) -> None:
    """Discrimination: the refusal is about the class, not about categorisations."""
    dataflow = str(Dataflow.urn_for(agency="BIS", id="DF", version="1.0"))
    assert serialize(_categorisation(dataflow), version=version)
