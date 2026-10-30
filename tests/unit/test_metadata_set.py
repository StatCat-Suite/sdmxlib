"""Tests for MetadataSet — the new SDMX 3.0-aligned shape.

MetadataSet now carries a list of targets and a single attribute tree
that applies to all of them. The v0.11.0 MetadataReport layer is gone.

The footnote shape (one set per unique footnote, listing every target
characteristic that shares it) is the canonical example.
"""

from typing import Any

import pytest

from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.metadatastructure import MetadataStructure
from sdmxlib.model.ref import Ref
from sdmxlib.storage.readers import get_artefact
from sdmxlib.storage.writers import put_artefact
from tests.support.factories import code_urn, footnote_metadataflow, footnote_msd, memory_cursor

pytestmark = pytest.mark.unit


# ── Fixtures ─────────────────────────────────────────────────────────────


def _make_mpa(mdf: Metadataflow) -> MetadataProvisionAgreement:
    return MetadataProvisionAgreement(
        name={"en": "MPA_FOOTNOTES"},
        id="MPA_FOOTNOTES",
        maintainer="STATCAN",
        version="1.0",
        metadataflow=mdf,
    )


_SET_URN = "urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataSet=STATCAN:FOOTNOTE_A(1.0)"
#: For sets built without an explicit version. `MetadataSet.version` defaults
#: to semver — SDMX-JSON's metadata schema pins VersionType to x.y.z, unlike
#: every other maintainable (#234) — so the lookup URN differs from `_SET_URN`.
_DEFAULTED_SET_URN = "urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataSet=STATCAN:FOOTNOTE_A(1.0.0)"


# ── Model: ReportedAttribute ────────────────────────────────────────────


def test_reported_attribute_defaults() -> None:
    a = ReportedAttribute(id="X")
    assert a.id == "X"
    assert a.value is None
    assert len(a.children) == 0


def test_reported_attribute_value_scalar_passes_through() -> None:
    assert ReportedAttribute(id="X", value="A").value == "A"
    assert ReportedAttribute(id="X", value=42).value == 42
    assert ReportedAttribute(id="X", value=3.14).value == 3.14
    assert ReportedAttribute(id="X", value=True).value is True


def test_reported_attribute_value_dict_coerces_to_international_string() -> None:
    a = ReportedAttribute(id="X", value={"en": "Hello", "fr": "Bonjour"})
    assert isinstance(a.value, InternationalString)
    assert a.value["en"].text == "Hello"
    assert a.value["fr"].text == "Bonjour"


def test_reported_attribute_recursive_children() -> None:
    a = ReportedAttribute(
        id="QUALITY",
        children=[
            ReportedAttribute(id="COVERAGE", value="full"),
            ReportedAttribute(id="ACCURACY", value=0.99),
        ],
    )
    assert {c.id for c in a.children} == {"COVERAGE", "ACCURACY"}
    assert a.children["COVERAGE"].value == "full"


# ── Model: MetadataSet ──────────────────────────────────────────────────


def test_metadata_set_rejects_msd_direct() -> None:
    # SDMX 3.0 has no direct-MSD structure ref — only Metadataflow / MPA.
    msd = footnote_msd()
    with pytest.raises(TypeError, match="cannot reference a MetadataStructure directly"):
        # The refusal is the subject, so the argument has to be written wrong.
        MetadataSet(name={"en": "FOOTNOTE_A"}, id="FOOTNOTE_A", maintainer="STATCAN", structure=msd)  # pyrefly: ignore[bad-argument-type]


def test_metadata_set_construct_with_mpa() -> None:
    mpa = _make_mpa(footnote_metadataflow(footnote_msd(), name=None))
    ms = MetadataSet(
        name={"en": "FOOTNOTE_A"},
        id="FOOTNOTE_A",
        maintainer="STATCAN",
        structure=mpa,
        targets=[code_urn("CL_C", "1", agency="STATCAN")],
        attributes=[ReportedAttribute(id="FOOTNOTE_TEXT", value={"en": "..."})],
    )
    assert ms.structure.urn.id == "MPA_FOOTNOTES"
    assert ms.structure.urn.artefact_class == "MetadataProvisionAgreement"
    assert ms.structure.resolved
    assert ms.structure() is mpa
    assert len(ms.targets) == 1
    assert isinstance(ms.targets[0], Ref)


def test_metadata_set_construct_with_metadataflow() -> None:
    msd = footnote_msd()
    mdf = footnote_metadataflow(msd, name=None)
    ms = MetadataSet(
        name={"en": "MS"},
        id="MS",
        maintainer="STATCAN",
        structure=mdf,
        targets=[code_urn("CL_C", "1", agency="STATCAN")],
    )
    assert ms.structure.urn.artefact_class == "Metadataflow"
    assert ms.structure() is mdf


def test_metadata_set_target_urns_coerce_to_refs() -> None:
    msd = footnote_msd()
    ms = MetadataSet(
        name={"en": "MS"},
        id="MS",
        maintainer="STATCAN",
        structure=footnote_metadataflow(msd, name=None),
        targets=[code_urn("CL_C", "1", agency="STATCAN"), code_urn("CL_C", "2", agency="STATCAN")],
    )
    assert all(isinstance(t, Ref) for t in ms.targets)
    assert {t.urn.item_id for t in ms.targets} == {"1", "2"}


def test_metadata_set_add_target_immutable() -> None:
    msd = footnote_msd()
    ms = MetadataSet(
        name={"en": "MS"},
        id="MS",
        maintainer="STATCAN",
        structure=footnote_metadataflow(msd, name=None),
        targets=[code_urn("CL_C", "1", agency="STATCAN")],
    )
    ms2 = ms.add_target(code_urn("CL_C", "2", agency="STATCAN"))
    assert len(ms.targets) == 1
    assert len(ms2.targets) == 2


def test_metadata_set_add_attribute_immutable() -> None:
    msd = footnote_msd()
    ms = MetadataSet(name={"en": "MS"}, id="MS", maintainer="STATCAN", structure=footnote_metadataflow(msd, name=None))
    ms2 = ms.add_attribute(ReportedAttribute(id="X", value="v"))
    assert len(ms.attributes) == 0
    assert len(ms2.attributes) == 1


# ── The footnote shape: one set per unique footnote ────────────────────


def _build_footnote_sets(msd: MetadataStructure, footnotes: dict[str, dict[str, Any]]) -> list[MetadataSet]:
    """Build one MetadataSet per (footnote_id, target_ids) entry.

    The dict maps footnote_id -> {"text": istring_dict, "targets": [code_id, ...]}.
    """
    return [
        MetadataSet(
            name={"en": "x"},
            id=f"FOOTNOTE_{fnote_id}",
            maintainer="STATCAN",
            structure=footnote_metadataflow(msd, name=None),
            targets=[code_urn("CL_CHARACTERISTIC", c, agency="STATCAN") for c in spec["targets"]],
            attributes=[ReportedAttribute(id="FOOTNOTE_TEXT", value=spec["text"])],
        )
        for fnote_id, spec in footnotes.items()
    ]


def test_footnote_shape_one_set_per_footnote() -> None:
    """Standard 3.0 model: 204 unique footnotes → 204 sets, each with N targets."""
    msd = footnote_msd()
    footnotes = {
        "A": {"text": {"en": "Statistics Canada is …"}, "targets": ["1", "5", "12"]},
        "B": {"text": {"en": "Excludes religious …"}, "targets": ["3"]},
        "C": {"text": {"en": "See note on …"}, "targets": ["7", "9"]},
    }
    sets = _build_footnote_sets(msd, footnotes)

    assert len(sets) == 3
    set_a = sets[0]
    assert set_a.id == "FOOTNOTE_A"
    assert {t.urn.item_id for t in set_a.targets} == {"1", "5", "12"}
    val = set_a.attributes["FOOTNOTE_TEXT"].value
    assert isinstance(val, InternationalString)
    assert val["en"].text == "Statistics Canada is …"


# ── Storage round-trip ──────────────────────────────────────────────────


def test_metadata_set_storage_round_trip_with_targets() -> None:
    cur = memory_cursor()
    msd = footnote_msd()
    ms = MetadataSet(
        id="FOOTNOTE_A",
        maintainer="STATCAN",
        structure=footnote_metadataflow(msd, name=None),
        version="1.0",
        name={"en": "Footnote A"},
        targets=[code_urn("CL_C", "1", agency="STATCAN"), code_urn("CL_C", "5", agency="STATCAN")],
        attributes=[ReportedAttribute(id="FOOTNOTE_TEXT", value={"en": "Hello"})],
    )
    put_artefact(cur, msd)
    put_artefact(cur, ms)

    out = get_artefact(cur, _SET_URN)
    assert isinstance(out, MetadataSet)
    assert {t.urn.item_id for t in out.targets} == {"1", "5"}
    val = out.attributes["FOOTNOTE_TEXT"].value
    assert isinstance(val, InternationalString)
    assert val["en"].text == "Hello"


def test_metadata_set_storage_round_trip_preserves_structure_kind_mpa() -> None:
    cur = memory_cursor()
    mpa = _make_mpa(footnote_metadataflow(footnote_msd(), name=None))
    ms = MetadataSet(name={"en": "MS_VIA_MPA"}, id="MS_VIA_MPA", maintainer="STATCAN", structure=mpa)
    put_artefact(cur, ms)
    out = get_artefact(cur, "urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataSet=STATCAN:MS_VIA_MPA(1.0.0)")
    assert isinstance(out, MetadataSet)
    assert out.structure.urn.artefact_class == "MetadataProvisionAgreement"


def test_metadata_provision_agreement_storage_round_trip() -> None:
    # MPA persists + reconstructs its metadataflow ref (needed for the
    # set → MPA → flow → MSD resolution chain).
    cur = memory_cursor()
    mpa = _make_mpa(footnote_metadataflow(footnote_msd(), name=None))
    put_artefact(cur, mpa)
    out = get_artefact(
        cur, "urn:sdmx:org.sdmx.infomodel.registry.MetadataProvisionAgreement=STATCAN:MPA_FOOTNOTES(1.0)"
    )
    assert isinstance(out, MetadataProvisionAgreement)
    assert out.metadataflow is not None
    assert out.metadataflow.urn.id == "MDF_FOOTNOTES"


def test_metadata_set_storage_round_trip_preserves_structure_kind_metadataflow() -> None:
    cur = memory_cursor()
    msd = footnote_msd()
    mdf = footnote_metadataflow(msd, name=None)
    ms = MetadataSet(name={"en": "MS_VIA_MDF"}, id="MS_VIA_MDF", maintainer="STATCAN", structure=mdf)
    put_artefact(cur, msd)
    put_artefact(cur, mdf)
    put_artefact(cur, ms)
    out = get_artefact(cur, "urn:sdmx:org.sdmx.infomodel.metadatastructure.MetadataSet=STATCAN:MS_VIA_MDF(1.0.0)")
    assert isinstance(out, MetadataSet)
    assert out.structure.urn.artefact_class == "Metadataflow"


def test_metadata_set_storage_round_trip_with_nested_attributes() -> None:
    cur = memory_cursor()
    msd = footnote_msd()
    ms = MetadataSet(
        name={"en": "FOOTNOTE_A"},
        id="FOOTNOTE_A",
        maintainer="STATCAN",
        structure=footnote_metadataflow(msd, name=None),
        targets=[code_urn("CL_C", "1", agency="STATCAN")],
        attributes=[
            ReportedAttribute(
                id="QUALITY",
                children=[
                    ReportedAttribute(id="COVERAGE", value="full"),
                    ReportedAttribute(id="NOTE", value={"en": "x"}),
                ],
            )
        ],
    )
    put_artefact(cur, msd)
    put_artefact(cur, ms)
    out = get_artefact(cur, _DEFAULTED_SET_URN)
    assert isinstance(out, MetadataSet)
    quality = out.attributes["QUALITY"]
    assert quality.children["COVERAGE"].value == "full"
    note_val = quality.children["NOTE"].value
    assert isinstance(note_val, InternationalString)
    assert note_val["en"].text == "x"


def test_metadata_set_storage_round_trip_with_repeated_siblings() -> None:
    """max_occurs > 1 is encoded by sibling attributes with the same id."""
    cur = memory_cursor()
    msd = footnote_msd()
    ms = MetadataSet(
        name={"en": "FOOTNOTE_A"},
        id="FOOTNOTE_A",
        maintainer="STATCAN",
        structure=footnote_metadataflow(msd, name=None),
        targets=[code_urn("CL_C", "1", agency="STATCAN")],
        attributes=[
            ReportedAttribute(id="TAG", value="alpha"),
            ReportedAttribute(id="TAG", value="beta"),
            ReportedAttribute(id="TAG", value="gamma"),
        ],
    )
    put_artefact(cur, msd)
    put_artefact(cur, ms)
    out = get_artefact(cur, _DEFAULTED_SET_URN)
    assert isinstance(out, MetadataSet)
    tags = [a.value for a in out.attributes if a.id == "TAG"]
    assert tags == ["alpha", "beta", "gamma"]


def test_metadata_set_re_put_replaces_targets_and_values() -> None:
    cur = memory_cursor()
    msd = footnote_msd()
    put_artefact(cur, msd)
    ms_v1 = MetadataSet(
        name={"en": "FOOTNOTE_A"},
        id="FOOTNOTE_A",
        maintainer="STATCAN",
        structure=footnote_metadataflow(msd, name=None),
        targets=[code_urn("CL_C", "1", agency="STATCAN"), code_urn("CL_C", "5", agency="STATCAN")],
        attributes=[ReportedAttribute(id="FOOTNOTE_TEXT", value="v1")],
    )
    put_artefact(cur, ms_v1)

    ms_v2 = MetadataSet(
        name={"en": "FOOTNOTE_A"},
        id="FOOTNOTE_A",
        maintainer="STATCAN",
        structure=footnote_metadataflow(msd, name=None),
        targets=[code_urn("CL_C", "9", agency="STATCAN")],
        attributes=[ReportedAttribute(id="FOOTNOTE_TEXT", value="v2")],
    )
    put_artefact(cur, ms_v2)

    out = get_artefact(cur, _DEFAULTED_SET_URN)
    assert isinstance(out, MetadataSet)
    assert {t.urn.item_id for t in out.targets} == {"9"}
    assert out.attributes["FOOTNOTE_TEXT"].value == "v2"


# ── Registry interop ────────────────────────────────────────────────────


def test_registry_resolves_msd_through_metadata_set() -> None:
    reg = InMemoryRegistry()
    msd = footnote_msd()
    mdf = footnote_metadataflow(msd)
    ms = MetadataSet(id="MS", maintainer="STATCAN", name={"en": "ms"}, structure=mdf)
    reg.add_all([msd, mdf, ms])
    assert ms.structure.resolved
    assert ms.structure() is mdf
    assert mdf.structure is not None
    assert mdf.structure.resolved
    assert mdf.structure() is msd
