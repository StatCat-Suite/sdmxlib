"""A MetadataSet's reported attributes read back from the store in the order they were written (GH-197).

The store keyed a value by its path and its index among repeats of one id, and
rebuilt the tree sorting paths as text. Attributes of different ids came back
sorted by id, and a repeated container came back as ``[0]``, ``[1]``,
``[10]``, ``[11]``, ..., ``[2]``. Each value now carries its position in the
set, in document order.
"""

import pytest

import sdmxlib as sl
from sdmxlib.local import LocalRegistry
from sdmxlib.model.registry import artefact_urn
from tests.support.factories import concept_ref

pytestmark = pytest.mark.unit

#: Not in id order, as cpt-builder's `MS_CP_DATAFLOW_INFO` declares them.
_TOP = ["TIME_COVERAGE_NOTE", "TIME_COMPARISON_CHARS", "UNIVERSE_NOTE", "LICENCE_NOTE"]
#: Twelve repeats, so a text sort of their paths puts `[10]` and `[11]` before `[2]`.
_REPEATS = 12


def _text(attribute_id: str, max_occurs: int | None = 1) -> sl.MetadataAttribute:
    return sl.MetadataAttribute(
        id=attribute_id,
        concept=concept_ref(attribute_id),
        representation=sl.Facet(type=sl.DataType.String),
        max_occurs=max_occurs,
    )


def _msd() -> sl.MetadataStructure:
    lineage = sl.MetadataAttribute(
        id="LINEAGE",
        concept=concept_ref("LINEAGE"),
        is_presentational=True,
        children=[
            sl.MetadataAttribute(
                id="RELATED_DATA_PRODUCT",
                concept=concept_ref("RELATED_DATA_PRODUCT"),
                is_presentational=True,
                max_occurs=None,
                children=[_text("PRODUCT_URL"), _text("PRODUCT_LABEL")],
            )
        ],
    )
    return sl.MetadataStructure(
        id="MSD_ORDER",
        maintainer="AG",
        version="1.0",
        name={"en": "Order"},
        attributes=[*(_text(a) for a in _TOP), lineage],
    )


def _set(msd: sl.MetadataStructure) -> tuple[sl.Metadataflow, sl.MetadataSet]:
    flow = sl.Metadataflow(id="MDF_ORDER", maintainer="AG", version="1.0", name={"en": "Order"}, structure=msd)
    # Each product reports its URL before its label, against the MSD's order,
    # so children keep their written order too.
    products = [
        sl.ReportedAttribute(
            id="RELATED_DATA_PRODUCT",
            children=[
                sl.ReportedAttribute(id="PRODUCT_URL", value=f"https://example.org/{n}"),
                sl.ReportedAttribute(id="PRODUCT_LABEL", value=f"product {n}"),
            ],
        )
        for n in range(_REPEATS)
    ]
    attributes = [
        *(sl.ReportedAttribute(id=a, value=a.lower()) for a in _TOP),
        sl.ReportedAttribute(id="LINEAGE", children=products),
    ]
    metadata_set = sl.MetadataSet(
        id="MS_ORDER",
        maintainer="AG",
        version="1.0",
        structure=flow,
        name={"en": "Order"},
        attributes=attributes,
    )
    return flow, metadata_set


@pytest.fixture
def stored():
    msd = _msd()
    flow, metadata_set = _set(msd)
    with LocalRegistry(":memory:") as reg:
        reg.add_all([msd, flow, metadata_set])
        yield reg, metadata_set


def test_a_set_reads_back_equal_to_what_was_written(stored) -> None:
    reg, metadata_set = stored
    assert reg.get(artefact_urn(metadata_set)) == metadata_set


def test_attributes_of_different_ids_keep_their_order(stored) -> None:
    reg, metadata_set = stored
    read = reg.get(artefact_urn(metadata_set))
    assert isinstance(read, sl.MetadataSet)
    assert [a.id for a in read.attributes] == [*_TOP, "LINEAGE"]
    assert sorted(_TOP) != _TOP, "the written order is not id order, so a sort would show"


def test_repeated_containers_keep_their_order(stored) -> None:
    reg, metadata_set = stored
    read = reg.get(artefact_urn(metadata_set))
    assert isinstance(read, sl.MetadataSet)
    (lineage,) = (a for a in read.attributes if a.id == "LINEAGE")
    urls = [product.children[0].value for product in lineage.children]
    assert urls == [f"https://example.org/{n}" for n in range(_REPEATS)]
    assert [[c.id for c in product.children] for product in lineage.children] == [
        ["PRODUCT_URL", "PRODUCT_LABEL"]
    ] * _REPEATS


def test_the_report_is_in_document_order(stored) -> None:
    reg, metadata_set = stored
    paths = [section.path for section in reg.metadata_report(artefact_urn(metadata_set))]
    products = [
        path
        for n in range(_REPEATS)
        for product in [f"LINEAGE.RELATED_DATA_PRODUCT[{n}]" if n else "LINEAGE.RELATED_DATA_PRODUCT"]
        for path in (product, f"{product}.PRODUCT_URL", f"{product}.PRODUCT_LABEL")
    ]
    assert paths == [*_TOP, "LINEAGE", *products]
