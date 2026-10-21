"""SDMX IDType / NestedIDType charset rules, from construction to the wire.

Background: a downstream project shipped codelists with ids like ``..``
and ``2021S05079320001.00`` (invalid ``IDType`` — ``.`` is only allowed
in ``NestedIDType`` for agency hierarchies) through reader → registry →
SDMX-ML 3.0 serialiser without complaint. Only the receiving FMR caught
it. See issue !147.

Since #492 such an id cannot be built: the model's fields refuse it as it is
set (`sdmxlib.model._rules`). What `validate()` and the write boundaries still
refuse is what needs the whole artefact, so their tests below use a duplicated
code id.
"""

from __future__ import annotations

import attrs
import pytest

from sdmxlib.model import _rules
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure
from sdmxlib.model.hierarchy import HierarchicalCode
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.validation import ValidationError, validate
from tests.support.factories import code_ref, concept_ref

pytestmark = pytest.mark.unit


# ── An invalid id is refused as it is set ─────────────────────────────────────


def test_validate_returns_none_on_a_valid_message():
    cl = Codelist(name={"en": "CL_FREQ"}, id="CL_FREQ", maintainer="SDMX", version="1.0")
    # Returns None — the API is "raise on issues", not "return issues".
    assert validate(StructureMessage(codelists=[cl])) is None


@pytest.mark.parametrize("bad", ["..", "...", "2021S05079320001.00"])
def test_the_statcan_codes_are_refused_as_they_are_built(bad: str):
    """StatCan's dotted OBS_STATUS and DGUID codes (!147), each refused by name."""
    with pytest.raises(ValidationError) as exc:
        Code(name={"en": bad}, id=bad)
    assert [i.path for i in exc.value.issues] == ["Code.id"]
    assert repr(bad) in str(exc.value), "the message names the offending id"


@pytest.mark.parametrize(
    ("build", "path"),
    [
        (lambda: Dimension(id="BAD.ID", concept=concept_ref("BAD.ID")), "Dimension.id"),
        (lambda: Measure(id="OBS VALUE", concept=concept_ref("OBS VALUE")), "Measure.id"),
        (lambda: HierarchicalCode(id="bad.id", code=code_ref("bad.id")), "HierarchicalCode.id"),
    ],
    ids=["dimension", "measure", "hierarchy-node"],
)
def test_components_and_nodes_share_the_idtype_rule(build, path: str):
    with pytest.raises(ValidationError) as exc:
        build()
    assert [i.path for i in exc.value.issues] == [path]


def test_there_is_no_assignment_to_get_past_the_rules():
    """The model is frozen (#492): `attrs.evolve`, below, is the only way to a changed id."""
    code = Code(name={"en": "ok"}, id="OK")
    field = "id"  # by name: the checker already refuses the assignment statically
    with pytest.raises(attrs.exceptions.FrozenInstanceError):
        setattr(code, field, "..")
    assert code.id == "OK"


def test_evolve_is_refused_as_construction_is():
    code = Code(name={"en": "ok"}, id="OK")
    with pytest.raises(ValidationError):
        attrs.evolve(code, id="..")


# ── A duplicated code id is refused as the codelist is built ─────────────────
# A rule no one value shows, so it was `validate`'s, and `serialize` and `add`
# ran it. It runs as the codelist is built now (#492): neither is handed one.


def test_a_duplicated_code_id_is_refused_as_the_codelist_is_built():
    with pytest.raises(ValidationError, match="duplicate code id 'A'"):
        Codelist(
            name={"en": "CL_BAD"},
            id="CL_BAD",
            maintainer="SDMX",
            version="1.0",
            codes=[Code(name={"en": "A"}, id="A"), Code(name={"en": "A"}, id="A")],
        )


def _no_dimensions() -> StructureMessage:
    """What SDMX-ML refuses and SDMX-JSON takes: a DSD with no dimensions."""
    return StructureMessage(data_structures=[DataStructure(id="DSD", maintainer="AG", name={"en": "d"})])


def test_serialize_validates_by_default():
    """What the format cannot carry cannot reach the wire without opting out."""
    from sdmxlib.formats import serialize

    with pytest.raises(ValidationError, match="requires a <Dimension>"):
        serialize(_no_dimensions(), version="3.0")


def test_serialize_validate_false_still_emits():
    """The opt-out survives — needed to round-trip a message in order to fix it."""
    from sdmxlib.formats import serialize

    assert serialize(_no_dimensions(), version="3.0", validate=False)


def test_serialize_passes_through_clean_message():
    from sdmxlib.formats import serialize

    # Sanity: clean message still serialises (validation is invisible when OK).
    msg = StructureMessage(
        codelists=[
            Codelist(
                name={"en": "CL_OK"},
                id="CL_OK",
                maintainer="SDMX",
                version="1.0",
                codes=[Code(name={"en": "A"}, id="A"), Code(name={"en": "B"}, id="B")],
            )
        ]
    )
    out = serialize(msg, version="3.0")
    assert out
    assert b"CL_OK" in out


# ── LocalRegistry.add ────────────────────────────────────────────────────────


def test_registry_add_accepts_clean_codelist(tmp_path):
    from sdmxlib.local import LocalRegistry

    cl = Codelist(
        name={"en": "CL_OK"}, id="CL_OK", maintainer="SDMX", version="1.0", codes=[Code(name={"en": "A"}, id="A")]
    )
    with LocalRegistry(tmp_path / "r.db") as reg:
        reg.add(cl)  # no raise


# ── Coverage regression guard ────────────────────────────────────────────────
# Every MaintainableArtefact subclass exported by sdmxlib.model must refuse an
# invalid id, agency id and version as they are set — so a new artefact type
# cannot silently skip them.


def _maintainable_subclasses() -> list[type]:
    import sdmxlib.model as m

    out: list[type] = []
    for name in m.__all__:
        obj = getattr(m, name, None)
        if isinstance(obj, type) and issubclass(obj, MaintainableArtefact) and obj is not MaintainableArtefact:
            out.append(obj)
    return out


_NCNAME_IDS = frozenset({"Codelist", "ConceptScheme", "CategoryScheme", "GeographicCodelist", "GeoGridCodelist"})


@pytest.mark.parametrize("cls", _maintainable_subclasses(), ids=lambda c: c.__name__)
def test_every_maintainable_field_refuses_as_it_is_set(cls: type):
    """Read off the class, so a field declared without its rule fails here."""
    fields = attrs.fields_dict(cls)
    expected_id = _rules.ncname_id_type if cls.__name__ in _NCNAME_IDS else _rules.id_type
    assert (fields["id"].validator, fields["maintainer"].validator, fields["version"].validator) == (
        expected_id,
        _rules.maintained_by,
        _rules.version_type,
    )


def test_the_sweep_sees_every_maintainable():
    """Exact: 30 classes; a new one changes this and is then swept above."""
    assert len(_maintainable_subclasses()) == 30


# Classes whose constructor needs extra required fields beyond (id, maintainer).
# They have dedicated tests below; the guard fails (not skips) for any new
# MaintainableArtefact subclass that lands here without one — silent skip
# would defeat the regression-guard purpose.
_NEEDS_DEDICATED_TEST: frozenset[str] = frozenset({"GeoGridCodelist", "MetadataSet"})


@pytest.mark.parametrize("cls", _maintainable_subclasses(), ids=lambda c: c.__name__)
def test_walker_covers_every_maintainable_subclass(cls: type):
    # ".." is invalid IDType (dot-only) *and* invalid NCNameIDType — a
    # reliable trigger for either rule.
    if cls.__name__ in _NEEDS_DEDICATED_TEST:
        pytest.skip(f"{cls.__name__} has a dedicated test (extra constructor fields)")
    paths: list[str] | None = None
    try:
        cls(id="..", maintainer="SDMX", name={"en": "x"})
    except ValidationError as exc:
        paths = [i.path for i in exc.issues]
    except TypeError as e:
        pytest.fail(
            f"{cls.__name__} can't be constructed with (id, maintainer) — "
            f"add it to _NEEDS_DEDICATED_TEST and write a dedicated test. "
            f"Construction error: {e}"
        )
    assert paths == [f"{cls.__name__}.id"], f"{cls.__name__} accepted the id '..'"


# ── Dedicated tests for artefacts that need extra constructor fields ─────────


def test_walker_covers_geo_grid_codelist():
    from sdmxlib.model.codelist import GeoGridCodelist

    # The XSD requires a grid definition, so the constructor does too.
    with pytest.raises(ValidationError) as exc:
        GeoGridCodelist(name={"en": ".."}, id="..", maintainer="SDMX", grid_definition=".*")
    assert [i.path for i in exc.value.issues] == ["GeoGridCodelist.id"]


def test_walker_covers_metadata_set():
    from sdmxlib.model.metadataset import MetadataSet
    from sdmxlib.model.metadatastructure import MetadataStructure
    from sdmxlib.model.ref import Ref

    # An MSD ref is not one of the two `MetadataSetType` offers — feeding it
    # one is the point, and an unresolved ref off the wire carries no known
    # target type, which is why the refusal happens later.
    structure_ref = Ref[MetadataStructure].unresolved(MetadataStructure.urn_for(agency="TEST", id="MSD_1"))
    with pytest.raises(ValidationError) as exc:
        MetadataSet(name={"en": ".."}, id="..", maintainer="SDMX", structure=structure_ref)  # pyrefly: ignore[bad-argument-type]
    assert [i.path for i in exc.value.issues] == ["MetadataSet.id"]
