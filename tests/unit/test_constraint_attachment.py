"""A constraint's attachment, held whole (#422).

SDMX makes `ConstraintAttachment` a choice in every format: one data provider,
or data sets, or simple data sources, or one or more artefacts of a single
kind — data structures, dataflows or provision agreements — with queryable data
sources beside them. The model held one artefact per kind, so every reader kept
the first dataflow and dropped the rest, and the data sources and 2.1's data
sets were dropped outright. Each was a `LOSSES` row; now each is held, and
`validate` refuses what a format has no element for.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

import sdmxlib as sl
from sdmxlib.formats import parse, serialize
from sdmxlib.formats.sdmx_json import reader as json_reader
from sdmxlib.formats.sdmx_json_1_0.writer import write_contentconstraint
from sdmxlib.model.constraint import ConstraintAttachment, DataConstraint, DataSetReference, QueryableDataSource
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import ValidationError
from tests.support import shapes
from tests.support.field_census import differences
from tests.support.narrow import present
from tests.support.roundtrip import as_structure

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.unit

_PROVIDER = "urn:sdmx:org.sdmx.infomodel.base.DataProvider=AG:DATA_PROVIDERS(1.0).P"
_FLOW = "urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=AG:{}(1.0)"
_QUERYABLE = QueryableDataSource(
    data_url="https://example.org/rest/data",
    is_rest_datasource=True,
    is_web_service_datasource=False,
    wadl_url="https://example.org/rest?wadl",
)


def _ref(urn: str) -> Ref[object]:
    return Ref[object].unresolved(SdmxUrn.parse(urn))


def _constraint(attachment: ConstraintAttachment, cid: str = "DC") -> DataConstraint:
    return DataConstraint(id=cid, maintainer="AG", name={"en": cid}, attachment=attachment)


def _attachment(msg: StructureMessage, cid: str) -> ConstraintAttachment:
    return next(c for c in msg.constraints if c.id == cid).attachment


_SEVERAL_FLOWS = ConstraintAttachment(
    dataflows=(_ref(_FLOW.format("DF_B")), _ref(_FLOW.format("DF_A"))), queryable_data_sources=(_QUERYABLE,)
)
_SIMPLE = ConstraintAttachment(simple_data_sources=("https://example.org/a.xml", "https://example.org/b.xml"))


# ── the model ─────────────────────────────────────────────────────────────


def test_the_alternatives_name_what_is_set() -> None:
    assert _SEVERAL_FLOWS.alternatives == ("dataflows",), "queryable sources sit beside a kind, not apart"
    assert _SIMPLE.alternatives == ("simple_data_sources",)
    assert ConstraintAttachment().alternatives == ()
    assert ConstraintAttachment().is_empty
    assert not _SIMPLE.is_empty


def test_a_bare_string_is_not_a_tuple_of_urls() -> None:
    """A `str` is an iterable of one-character strings — the #261 defect."""
    untyped = cast("object", "https://example.org/a.xml")
    with pytest.raises(TypeError, match="tuple of URLs"):
        ConstraintAttachment(simple_data_sources=cast("tuple[str, ...]", untyped))


def test_urn_text_is_refused_in_a_tuple_of_refs() -> None:
    untyped = cast("object", (_FLOW.format("DF_A"),))
    with pytest.raises(TypeError, match="URN string"):
        ConstraintAttachment(dataflows=cast("tuple[Ref[object], ...]", untyped))


# ── well-formedness: one alternative, everywhere ──────────────────────────


@pytest.mark.parametrize(
    ("attachment", "path"),
    [
        pytest.param(
            ConstraintAttachment(dataflows=(_ref(_FLOW.format("DF_A")),), data_provider=_ref(_PROVIDER)),
            "DataConstraint.attachment",
            id="a-provider-and-flows",
        ),
        pytest.param(
            ConstraintAttachment(
                simple_data_sources=("https://example.org/a.xml",), queryable_data_sources=(_QUERYABLE,)
            ),
            "DataConstraint.attachment.queryable_data_sources",
            id="queryable-sources-beside-no-kind",
        ),
    ],
)
def test_an_attachment_with_no_form_anywhere_is_refused(attachment: ConstraintAttachment, path: str) -> None:
    with pytest.raises(ValidationError) as caught:
        _constraint(attachment)
    assert [issue.path for issue in caught.value.issues] == [path]


def test_one_alternative_is_well_formed() -> None:
    _constraint(_SEVERAL_FLOWS)
    _constraint(_SIMPLE)


def test_an_attachment_constraint_is_held_to_the_choice_too() -> None:
    with pytest.raises(ValidationError) as caught:
        sl.AttachmentConstraint(
            id="AC",
            maintainer="AG",
            name={"en": "ac"},
            attachment=ConstraintAttachment(dataflows=(_ref(_FLOW.format("DF_A")),), simple_data_sources=("x",)),
        )
    assert [issue.path for issue in caught.value.issues] == ["AttachmentConstraint.attachment"]


# ── writability: what each format has an element for ──────────────────────


@pytest.mark.parametrize(
    ("attachment", "version", "refused"),
    [
        pytest.param(_SIMPLE, "3.1", ["simple_data_sources"], id="3.1-has-no-simple-sources"),
        pytest.param(_SEVERAL_FLOWS, "3.1", ["queryable_data_sources"], id="3.1-has-no-queryable-sources"),
        pytest.param(_SIMPLE, "json", ["simple_data_sources"], id="json-has-no-simple-sources"),
        pytest.param(_SEVERAL_FLOWS, "json", ["queryable_data_sources"], id="json-has-no-queryable-sources"),
        pytest.param(
            ConstraintAttachment(data_sets=(DataSetReference(_ref(_PROVIDER), "DS"),)),
            "3.0",
            ["data_sets"],
            id="3.0-has-no-data-sets",
        ),
        pytest.param(_SIMPLE, "2.1", ["simple_data_sources"], id="2.1-content-takes-one-simple-source"),
        pytest.param(
            ConstraintAttachment(
                data_sets=(DataSetReference(_ref(_PROVIDER), "D1"), DataSetReference(_ref(_PROVIDER), "D2"))
            ),
            "2.1",
            ["data_sets"],
            id="2.1-content-takes-one-data-set",
        ),
    ],
)
def test_a_format_refuses_what_it_has_no_element_for(
    attachment: ConstraintAttachment, version: str, refused: list[str]
) -> None:
    msg = StructureMessage(constraints=[_constraint(attachment)])
    with pytest.raises(ValidationError) as caught:
        serialize(msg, version=version)
    assert [issue.path for issue in caught.value.issues] == [f"DataConstraint:DC.attachment.{f}" for f in refused]


@pytest.mark.parametrize(
    ("attachment", "versions"),
    [
        pytest.param(_SEVERAL_FLOWS, ("2.1", "3.0"), id="several-flows-with-a-queryable-source"),
        pytest.param(_SIMPLE, ("3.0",), id="several-simple-sources"),
        pytest.param(
            ConstraintAttachment(data_sets=(DataSetReference(_ref(_PROVIDER), "DS"),)), ("2.1",), id="a-data-set"
        ),
    ],
)
def test_what_a_format_has_an_element_for_round_trips(
    attachment: ConstraintAttachment, versions: tuple[str, ...]
) -> None:
    """In order — the second dataflow first, so a sorted read would show."""
    msg = StructureMessage(constraints=[_constraint(attachment)])
    for version in versions:
        assert _attachment(as_structure(parse(serialize(msg, version=version))), "DC") == attachment, version


def test_sdmx_ml_2_1_refuses_a_provider_on_an_attachment_constraint() -> None:
    """`AttachmentConstraintAttachmentType` has no provider and no queryable sources."""
    ac = sl.AttachmentConstraint(
        id="AC", maintainer="AG", name={"en": "ac"}, attachment=ConstraintAttachment(data_provider=_ref(_PROVIDER))
    )
    with pytest.raises(ValidationError) as caught:
        serialize(StructureMessage(attachment_constraints=[ac]), version="2.1")
    assert [issue.path for issue in caught.value.issues] == ["AttachmentConstraint:AC.attachment.data_provider"]


def test_sdmx_ml_2_1_writes_several_data_sets_on_an_attachment_constraint() -> None:
    """Where its ContentConstraint takes one."""
    attachment = ConstraintAttachment(
        data_sets=(DataSetReference(_ref(_PROVIDER), "D1"), DataSetReference(_ref(_PROVIDER), "D2"))
    )
    ac = sl.AttachmentConstraint(id="AC", maintainer="AG", name={"en": "ac"}, attachment=attachment)
    back = as_structure(parse(serialize(StructureMessage(attachment_constraints=[ac]), version="2.1")))
    (read_back,) = back.attachment_constraints
    assert read_back.attachment == attachment


# ── SDMX-JSON ─────────────────────────────────────────────────────────────


def test_sdmx_json_writes_several_of_a_kind() -> None:
    attachment = ConstraintAttachment(dataflows=(_ref(_FLOW.format("DF_B")), _ref(_FLOW.format("DF_A"))))
    written = serialize(StructureMessage(constraints=[_constraint(attachment)]), version="json")
    (constraint,) = json.loads(written)["data"]["dataConstraints"]
    assert constraint["constraintAttachment"] == {"dataflows": [_FLOW.format("DF_B"), _FLOW.format("DF_A")]}
    assert _attachment(json_reader.read(written), "DC") == attachment


def test_sdmx_json_2_0_data_sources_are_read() -> None:
    """SDMX-JSON 2.0 has data sources, and 2.1 does not; the reader keeps them.

    Not written: the writer's output validates against 2.1 too. Named by
    `test_json_census.py`'s `_DECLARED`, which classifies these keys as held.
    """
    msg = StructureMessage(constraints=[_constraint(_SEVERAL_FLOWS, "DC_Q"), _constraint(_SIMPLE, "DC_S")])
    doc = json.loads(serialize(msg, version="json", validate=False))
    assert "queryableDataSources" in doc["data"]["dataConstraints"][0]["constraintAttachment"]
    assert doc["data"]["dataConstraints"][1]["constraintAttachment"] == {
        "simpleDataSources": list(_SIMPLE.simple_data_sources)
    }
    back = json_reader.read(json.dumps(doc).encode())
    assert _attachment(back, "DC_Q") == _SEVERAL_FLOWS
    assert _attachment(back, "DC_S") == _SIMPLE
    assert back.lost == ()


def test_sdmx_json_1_0_writes_every_alternative_it_has() -> None:
    """1.0's attachment is 2.1's ContentConstraint's: a provider is `dataProvider`, singular.

    It was written as `dataProviders`, a key 1.0 does not define; the schema
    allows unknown keys, so nothing failed (#422).
    """
    provider = json.loads(write_contentconstraint(_constraint(ConstraintAttachment(data_provider=_ref(_PROVIDER)))))
    (cc,) = provider["data"]["contentConstraints"]
    assert cc["constraintAttachment"] == {"dataProvider": _PROVIDER}
    data_set = ConstraintAttachment(data_sets=(DataSetReference(_ref(_PROVIDER), "DS"),))
    (cc,) = json.loads(write_contentconstraint(_constraint(data_set)))["data"]["contentConstraints"]
    assert cc["constraintAttachment"] == {"dataSet": {"dataProvider": _PROVIDER, "id": "DS"}}
    (cc,) = json.loads(write_contentconstraint(_constraint(_SEVERAL_FLOWS)))["data"]["contentConstraints"]
    assert cc["constraintAttachment"]["queryableDataSources"] == [
        {
            "dataURL": "https://example.org/rest/data",
            "isRESTDatasource": True,
            "isWebServiceDatasource": False,
            "wadlURL": "https://example.org/rest?wadl",
        }
    ]


def test_sdmx_json_1_0_refuses_two_simple_sources() -> None:
    with pytest.raises(ValidationError) as caught:
        write_contentconstraint(_constraint(_SIMPLE))
    assert [issue.path for issue in caught.value.issues] == ["DataConstraint:DC.attachment.simple_data_sources"]


# ── storage ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("message", [shapes.message, shapes.message_21], ids=["3.x", "2.1"])
def test_every_alternative_survives_the_store(message: Callable[[], StructureMessage]) -> None:
    """The shapes carry several flows with a queryable source, simple sources and a data set."""
    msg = message()
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all(msg.constraints)
        for constraint in msg.constraints:
            back = present(reg.get(DataConstraint.parse_urn(str(constraint.urn))))
            assert not list(differences(constraint, back)), constraint.id
