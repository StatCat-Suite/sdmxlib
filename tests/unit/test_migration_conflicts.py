"""`sl.migration.conflicts`: final versions the target holds with other content (GH-399)."""

import attrs
import pytest

import sdmxlib as sl

pytestmark = pytest.mark.unit


def _codelist(version: str, *codes: str) -> sl.Codelist:
    return sl.Codelist(
        id="CL",
        maintainer="AG",
        version=version,
        name={"en": "c"},
        codes=[sl.Code(id=c, name={"en": c}) for c in codes],
    )


def _msg(*codelists: sl.Codelist) -> sl.StructureMessage:
    return sl.StructureMessage(codelists=list(codelists))


def test_a_final_version_held_with_other_content_conflicts():
    (conflict,) = sl.migration.conflicts(_msg(_codelist("1.0.0", "X", "Y")), _msg(_codelist("1.0.0", "A", "B")))
    assert conflict.urn.endswith("Codelist=AG:CL(1.0.0)")
    assert set(conflict.differences) == {"Codelist.codes[] (only left)", "Codelist.codes[] (only right)"}


def test_the_same_content_does_not_conflict():
    assert sl.migration.conflicts(_msg(_codelist("1.0.0", "A")), _msg(_codelist("1.0.0", "A"))) == ()


def test_a_draft_may_be_replaced():
    assert sl.migration.conflicts(_msg(_codelist("1.0.0-draft", "X")), _msg(_codelist("1.0.0-draft", "A"))) == ()


def test_a_version_the_target_lacks_does_not_conflict():
    assert sl.migration.conflicts(_msg(_codelist("1.1.0", "X")), _msg(_codelist("1.0.0", "A"))) == ()


def test_the_check_can_fail_on_a_name_alone():
    """Discrimination: a difference anywhere in the artefact counts, not only in its items."""
    held = _codelist("1.0.0", "A")
    (conflict,) = sl.migration.conflicts(_msg(attrs.evolve(held, name={"en": "renamed"})), _msg(held))
    assert list(conflict.differences) == ["Codelist.name (text)"]
