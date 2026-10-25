"""`sl.check_serialisable`: a builder checks its structures write before publishing (GH-193).

The check itself is `serialize`'s and has its own tests; these pin that it is
public, and that it answers per version as a builder calls it.
"""

import pytest

import sdmxlib as sl
from tests.support.bundles import CONCEPTS, DSD, FLOW

pytestmark = pytest.mark.unit


def test_it_is_exported() -> None:
    assert "check_serialisable" in sl.__all__
    assert sl.check_serialisable.__module__ == "sdmxlib.model.validation"


@pytest.mark.parametrize("version", ["2.1", "3.0", None])
def test_a_writable_message_passes_for_each_version(version: str | None) -> None:
    sl.check_serialisable(
        sl.StructureMessage(concept_schemes=[CONCEPTS], data_structures=[DSD], dataflows=[FLOW]), version=version
    )


def test_a_message_repeating_an_artefact_is_refused() -> None:
    """Every SDMX-ML container is unique over id, agency and version, so a message cannot hold one twice."""
    with pytest.raises(sl.ValidationError):
        sl.check_serialisable(sl.StructureMessage(dataflows=[FLOW, FLOW]), version="3.0")
