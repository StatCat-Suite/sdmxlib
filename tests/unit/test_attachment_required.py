"""An attribute says what it attaches to, as it is built (#508).

Every SDMX format requires an attribute's relationship, so one without it has
no form anywhere. It was accepted by the constructor and by `add`, then
refused by every writer — a store could hold a structure it could not write,
the shape #419 says `add` must refuse. `attachment` now has no default, and
`None` is refused by a field validator, so no path builds one.
"""

import inspect

import attrs
import duckdb
import pytest

import sdmxlib as sl
from sdmxlib.model._rules import attachment_stated
from sdmxlib.model.datastructure import Attribute, MetadataAttributeUsage
from sdmxlib.model.errors import ValidationError
from sdmxlib.model.validation import SdmxConformanceError
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("cls", [Attribute, MetadataAttributeUsage], ids=lambda c: c.__name__)
def test_attachment_has_no_default(cls: type) -> None:
    """Leaving it out is a `TypeError` at the call — and a checker finding before that."""
    assert inspect.signature(cls).parameters["attachment"].default is inspect.Parameter.empty


@pytest.mark.parametrize("cls", [Attribute, MetadataAttributeUsage], ids=lambda c: c.__name__)
def test_an_explicit_none_is_refused_as_it_is_built(cls: type[Attribute | MetadataAttributeUsage]) -> None:
    """The field runs `attachment_stated`, which refuses ``None`` — what a store row or a pass-through could carry.

    Checked as two facts rather than by passing ``None``, which the checker
    already refuses at the call: the validator is on the field, and it refuses.
    """
    field = attrs.fields_dict(cls)["attachment"]
    assert field.validator is attachment_stated
    holder = Attribute(id="ATT", concept=concept_ref("ATT"), attachment=sl.AttachmentLevel.DATASET)
    with pytest.raises(ValidationError, match=r"Attribute\.attachment — every SDMX format requires"):
        attachment_stated(holder, field, None)


@pytest.mark.parametrize("level", list(sl.AttachmentLevel))
def test_every_level_builds(level: sl.AttachmentLevel) -> None:
    """The discrimination check: the refusal is the missing level, not any level."""
    assert Attribute(id="ATT", concept=concept_ref("ATT"), attachment=level).attachment is level


def test_a_store_row_written_without_one_is_refused_on_read() -> None:
    """A store written before #508 may hold a NULL; it is reported, not built."""
    concepts = sl.ConceptScheme(
        id="CS",
        maintainer="AG",
        name={"en": "c"},
        concepts=[sl.Concept(id=c, name={"en": c}) for c in ("GEO", "OBS_VALUE", "ATT")],
    )
    dsd = sl.DataStructure(
        id="DSD",
        maintainer="AG",
        name={"en": "d"},
        dimensions=[sl.Dimension(id="GEO", concept=concepts.ref_to("GEO"))],
        attributes=[sl.Attribute(id="ATT", concept=concepts.ref_to("ATT"), attachment=sl.AttachmentLevel.DATASET)],
        measures=[sl.Measure(id="OBS_VALUE", concept=concepts.ref_to("OBS_VALUE"))],
    )
    with sl.LocalRegistry(":memory:") as registry:
        registry.add_all([concepts, dsd])
        conn: duckdb.DuckDBPyConnection = registry.connection
        conn.execute("UPDATE sdmx.dsd_component SET attachment_level = NULL WHERE component_id = 'ATT'")
        with pytest.raises(
            SdmxConformanceError, match=r"stored and refused: Attribute\.ATT\.attachment — stored with no"
        ):
            registry.get(dsd.urn)
