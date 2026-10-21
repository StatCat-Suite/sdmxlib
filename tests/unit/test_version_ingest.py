"""Versions only SDMX 2.1 accepts are refused at ingest, unless rewritten (#420, #424).

Both registries: the rule is "the model holds 3.x", which a check on one of
them would make true of the store and false of the graph `parse` fills.
"""

import re
from collections.abc import Iterator

import pytest

import sdmxlib as sl
from sdmxlib.formats.sdmx_ml21 import writer as ml21_writer
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref
from sdmxlib.model.version_rewrite import RewrittenVersion, strip_leading_zeros
from tests.support.narrow import present

pytestmark = pytest.mark.unit

type Registry = InMemoryRegistry | sl.LocalRegistry


@pytest.fixture(params=["memory", "store"])
def reg(request: pytest.FixtureRequest) -> Iterator[Registry]:
    if request.param == "memory":
        yield InMemoryRegistry()
        return
    with sl.LocalRegistry(":memory:") as local:
        yield local


def _dsd(version: str) -> DataStructure:
    return DataStructure(id="DSD", maintainer="AG", version=version, name={"en": "DSD"})


def _flow(dsd_version: str, *, id_: str = "DF", version: str = "1.0") -> Dataflow:
    urn = DataStructure.urn_for(agency="AG", id="DSD", version=dsd_version)
    return Dataflow(
        id=id_, maintainer="AG", version=version, name={"en": id_}, structure=Ref[DataStructure].unresolved(urn)
    )


def _held(reg: Registry, cls: type, id_: str, version: str) -> object | None:
    return reg.get(
        sl.SdmxUrn.make(
            cls, package=cls.sdmx_package, artefact_class=cls.sdmx_class, agency="AG", id=id_, version=version
        )
    )


# ── the rewrite ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("version", "expected"),
    [("1.03", "1.3"), ("01.0", "1.0"), ("1.0", "1.0"), ("1.2.3.4", "1.2.3.4"), ("1.0.0-draft", "1.0.0-draft")],
)
def test_strip_leading_zeros_changes_the_spelling_and_nothing_else(version: str, expected: str) -> None:
    assert strip_leading_zeros(version) == expected
    assert sl.versions.strip_leading_zeros is strip_leading_zeros


# ── refused by default ────────────────────────────────────────────────────


def test_an_artefact_at_a_2_1_only_version_is_refused(reg: Registry) -> None:
    with pytest.raises(sl.ValidationError, match=re.escape("strip_leading_zeros makes it '1.3'")):
        reg.add(_dsd("1.03"))
    assert _held(reg, DataStructure, "DSD", "1.03") is None


def test_a_version_with_no_3_x_equal_names_the_mapping(reg: Registry) -> None:
    with pytest.raises(sl.ValidationError, match=r'rewrite_version=\{"1\.2\.3\.4": \.\.\.\}'):
        reg.add(_dsd("1.2.3.4"))


def test_a_reference_to_a_2_1_only_version_is_refused(reg: Registry) -> None:
    with pytest.raises(sl.ValidationError) as caught:
        reg.add(_flow("1.03"))
    (issue,) = caught.value.issues
    assert issue.path == "Dataflow:AG:DF.structure"
    assert "the reference to AG:DSD" in issue.message


def test_a_refused_batch_adds_nothing_and_names_every_problem(reg: Registry) -> None:
    with pytest.raises(sl.ValidationError) as caught:
        reg.add_all([_dsd("1.0"), _dsd("1.03"), _flow("1.2.3.4")])
    assert len(caught.value.issues) == 2
    assert _held(reg, DataStructure, "DSD", "1.0") is None, "checked whole before anything is added"


def test_parsing_into_a_registry_refuses_too(reg: Registry) -> None:
    codelist = sl.Codelist(
        id="CL", maintainer="AG", version="1.03", name={"en": "CL"}, codes=[sl.Code(id="A", name={"en": "a"})]
    )
    xml = ml21_writer.write(StructureMessage(codelists=[codelist]))
    with pytest.raises(sl.ValidationError, match=re.escape("valid only in SDMX 2.1")):
        sl.parse(xml, registry=reg)


# ── admitted by a rewrite ─────────────────────────────────────────────────


def test_a_rewrite_follows_the_artefact_into_every_reference(reg: Registry) -> None:
    dsd, flow = _dsd("1.03"), _flow("1.03")
    report = reg.add_all([dsd, flow], rewrite_version=strip_leading_zeros)

    assert report == (
        RewrittenVersion(
            urn=str(DataStructure.urn_for(agency="AG", id="DSD", version="1.3")), original="1.03", version="1.3"
        ),
    )
    stored = present(_held(reg, Dataflow, "DF", "1.0"))
    assert isinstance(stored, Dataflow)
    assert present(stored.structure).urn.version == "1.3"
    assert _held(reg, DataStructure, "DSD", "1.3") is not None
    # Copy-on-write: what the caller handed in is what it still holds.
    assert dsd.version == "1.03"
    assert present(flow.structure).urn.version == "1.03"


def test_a_mapping_names_the_version_for_one_with_no_3_x_equal(reg: Registry) -> None:
    (rewrite,) = reg.add_all([_dsd("1.2.3.4")], rewrite_version={"1.2.3.4": "1.2.4"})
    assert (rewrite.original, rewrite.version) == ("1.2.3.4", "1.2.4")
    assert _held(reg, DataStructure, "DSD", "1.2.4") is not None


def test_no_rewrite_reports_nothing(reg: Registry) -> None:
    assert reg.add_all([_dsd("1.0")]) == ()


def test_the_same_batch_ingested_twice_is_no_collision(reg: Registry) -> None:
    reg.add_all([_dsd("1.03")], rewrite_version=strip_leading_zeros)
    reg.add_all([_dsd("1.03")], rewrite_version=strip_leading_zeros)
    assert _held(reg, DataStructure, "DSD", "1.3") is not None


# ── collisions ────────────────────────────────────────────────────────────


def test_two_versions_rewritten_to_one_are_refused(reg: Registry) -> None:
    with pytest.raises(sl.ValidationError, match=re.escape("'1.3' and '1.03' are both rewritten to '1.3'")):
        reg.add_all([_dsd("1.3"), _dsd("1.03")], rewrite_version=strip_leading_zeros)


def test_a_rewrite_onto_an_artefact_held_from_elsewhere_is_refused(reg: Registry) -> None:
    reg.add(_dsd("1.3"))
    with pytest.raises(sl.ValidationError, match="collides with"):
        reg.add_all([_dsd("1.03")], rewrite_version=strip_leading_zeros)


def test_a_reference_the_rewrite_cannot_reach_is_refused(reg: Registry) -> None:
    """Not only 2.1-only versions: a mapping may rewrite any, and orphan a holder.

    The flow is held ahead of its DSD; ingesting the DSD at another version
    would leave the flow pointing at one that never arrives.
    """
    reg.add(_flow("1.0", version="2.0"))
    with pytest.raises(sl.ValidationError) as caught:
        reg.add_all([_dsd("1.0")], rewrite_version={"1.0": "1.0.0"})
    (issue,) = caught.value.issues
    assert issue.path.startswith("urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=AG:DF(2.0)")
    assert "would dangle" in issue.message


def test_a_holder_in_the_same_batch_follows_the_rewrite(reg: Registry) -> None:
    reg.add(_flow("1.0", version="2.0"))
    reg.add_all([_dsd("1.0"), _flow("1.0", version="2.0")], rewrite_version={"1.0": "1.0.0"})
    stored = present(_held(reg, Dataflow, "DF", "2.0"))
    assert isinstance(stored, Dataflow)
    assert present(stored.structure).urn.version == "1.0.0"


def test_a_reference_the_old_version_still_answers_does_not_dangle(reg: Registry) -> None:
    reg.add_all([_dsd("1.0"), _flow("1.0", version="2.0")])
    reg.add_all([_dsd("1.0")], rewrite_version={"1.0": "1.0.0"})
    assert _held(reg, DataStructure, "DSD", "1.0") is not None
    assert _held(reg, DataStructure, "DSD", "1.0.0") is not None


# ── provenance in the store ───────────────────────────────────────────────


def _rewrites(local: sl.LocalRegistry) -> list[tuple[str, str]]:
    return local.connection.execute("SELECT urn, original_version FROM version_rewrite").fetchall()


def test_the_store_records_each_rewrite_for_as_long_as_the_artefact() -> None:
    urn = DataStructure.urn_for(agency="AG", id="DSD", version="1.3")
    with sl.LocalRegistry(":memory:") as local:
        local.add_all([_dsd("1.03")], rewrite_version=strip_leading_zeros)
        assert _rewrites(local) == [(str(urn), "1.03")]

        local.add(_dsd("1.3"))  # published at 1.3 now: no longer a rewrite
        assert _rewrites(local) == []

        local.drop(urn)
        local.add_all([_dsd("1.03")], rewrite_version=strip_leading_zeros)
        assert _rewrites(local) == [(str(urn), "1.03")]
        local.drop(urn)
        assert _rewrites(local) == []
