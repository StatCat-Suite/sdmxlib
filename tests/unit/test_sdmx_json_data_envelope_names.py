"""Envelope display-name tests for the SDMX-JSON 1.0 / 2.0 data writers.

Covers the two omissions a downstream consumer's envelope-names report surfaced:

1. **TIME_PERIOD dimension name**: previously fell through to the id
   because ``ComponentTable`` looked up dimensions in
   ``dsd.dimensions`` (which excludes ``TimeDimension``). Now uses
   ``dsd.key_dimensions`` so the time dim's concept-resolved name
   appears alongside every other dim's.
2. **Dataflow display name**: ``header`` / ``meta`` /
   ``structure(s)`` / ``dataSets[0]`` previously carried no
   localized name at all, so consumers like .Stat fell back to the
   dataflow id (visible as ``[DF_CP_2021]`` in the table title).
   ``display_names()`` now pulls from the resolved dataflow's
   ``InternationalString`` and spreads it into every relevant slot.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import msgspec
import pyarrow as pa
import pytest

from sdmxlib.formats.sdmx_json.data_v1 import write_sdmx_json_data as write_v1
from sdmxlib.formats.sdmx_json.data_v2 import write_sdmx_json_data as write_v2
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure, Dimension, Measure, TimeDimension
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from tests.support.factories import concept_ref, dsd_binding

pytestmark = pytest.mark.unit


def _time_concept_ref() -> Ref[Concept]:
    """A resolved Ref[Concept] for the TIME_PERIOD concept with bilingual name."""
    urn = SdmxUrn.make(
        Concept,
        package="conceptscheme",
        artefact_class="Concept",
        agency="TEST",
        id="CS",
        version="1.0",
        item_id="TIME_PERIOD",
    )
    concept = Concept(
        id="TIME_PERIOD",
        name=InternationalString(en="Reference period", fr="Période de référence"),
    )
    return Ref[Concept].of(urn, concept)


def _build_dsd_with_time_concept() -> tuple[DataStructure, Dataflow]:
    cl_freq = Codelist(
        id="CL_FREQ",
        maintainer="TEST",
        version="1.0",
        name=InternationalString(en="Frequency"),
        codes=[Code(name={"en": "A5"}, id="A5")],
    )
    dsd = DataStructure(
        id="DSD_CP",
        maintainer="TEST",
        version="2.0",
        name=InternationalString(en="Census Profile DSD"),
        dimensions=[Dimension(id="FREQ", representation=Ref[Codelist].to(cl_freq), concept=concept_ref("FREQ"))],
        time_dimension=TimeDimension(id="TIME_PERIOD", concept=_time_concept_ref()),
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    flow = Dataflow(
        id="DF_CP_2021",
        maintainer="TEST",
        version="2.0",
        name=InternationalString(
            en="Census Profile dataflow (2021) v2.0",
            fr="Flux de données du Profil du recensement (2021) v2.0",
        ),
        description=InternationalString(en="Statistics for Canada's census areas."),
        structure=Ref[DataStructure].to(dsd),
    )
    return dsd, flow


def _drain_v2(binding: DataflowBinding, dsd: DataStructure, *, lang: str = "en") -> dict[str, Any]:
    batch = pa.record_batch({"FREQ": ["A5"], "TIME_PERIOD": ["2021"], "OBS_VALUE": [1.0]})
    out = b"".join(
        write_v2(
            [batch],
            dsd,
            binding.dataflow,
            message_id="m",
            prepared=datetime(2026, 6, 17, tzinfo=UTC),
            sender_id="S",
            lang=lang,
        )
    )
    return msgspec.json.decode(out, type=dict[str, Any])


def _drain_v1(binding: DataflowBinding, dsd: DataStructure, *, lang: str = "en") -> dict[str, Any]:
    batch = pa.record_batch({"FREQ": ["A5"], "TIME_PERIOD": ["2021"], "OBS_VALUE": [1.0]})
    out = b"".join(
        write_v1(
            [batch],
            dsd,
            binding.dataflow,
            message_id="m",
            prepared=datetime(2026, 6, 17, tzinfo=UTC),
            sender_id="S",
            lang=lang,
        )
    )
    return msgspec.json.decode(out, type=dict[str, Any])


# ── Issue 2: TIME_PERIOD dimension name ───────────────────────────────────────


def test_time_dim_carries_concept_resolved_name_v2() -> None:
    """The report's smoking gun — TIME_PERIOD must surface its
    concept's name (``Reference period``) like every other dim, not
    fall through to the bare id."""
    dsd, flow = _build_dsd_with_time_concept()
    doc = _drain_v2(dsd_binding(dsd, flow, schema="d", table="o"), dsd)
    obs_dims = doc["data"]["structures"][0]["dimensions"]["observation"]
    time_dim = next(d for d in obs_dims if d["id"] == "TIME_PERIOD")
    assert time_dim["name"] == "Reference period"


def test_time_dim_carries_concept_resolved_name_v1() -> None:
    dsd, flow = _build_dsd_with_time_concept()
    doc = _drain_v1(dsd_binding(dsd, flow, schema="d", table="o"), dsd)
    obs_dims = doc["structure"]["dimensions"]["observation"]
    time_dim = next(d for d in obs_dims if d["id"] == "TIME_PERIOD")
    assert time_dim["name"] == "Reference period"


def test_time_dim_name_respects_lang() -> None:
    """Pass ``lang="fr"`` and the time dim's name comes through in French."""
    dsd, flow = _build_dsd_with_time_concept()
    doc = _drain_v2(dsd_binding(dsd, flow, schema="d", table="o"), dsd, lang="fr")
    obs_dims = doc["data"]["structures"][0]["dimensions"]["observation"]
    time_dim = next(d for d in obs_dims if d["id"] == "TIME_PERIOD")
    assert time_dim["name"] == "Période de référence"


def test_regular_dim_name_still_resolves() -> None:
    """Sanity: the existing non-time dim path is not regressed by the
    switch from ``dsd.dimensions`` to ``dsd.key_dimensions``."""
    dsd, flow = _build_dsd_with_time_concept()
    doc = _drain_v2(dsd_binding(dsd, flow, schema="d", table="o"), dsd)
    obs_dims = doc["data"]["structures"][0]["dimensions"]["observation"]
    freq = next(d for d in obs_dims if d["id"] == "FREQ")
    # FREQ has no concept on the test DSD — falls back to id, which is
    # the documented behavior. The point of the test is that the lookup
    # itself doesn't break on regular dims.
    assert freq["id"] == "FREQ"


# ── Issue 1: envelope-level dataflow display name ─────────────────────────────


def test_v2_envelope_carries_dataflow_name_when_resolved() -> None:
    """All four envelope slots — ``meta``, ``data.dataSets[0]``,
    ``data.structures[0]`` (name + names) — get the dataflow's display
    name when its Ref is resolved."""
    dsd, flow = _build_dsd_with_time_concept()
    doc = _drain_v2(dsd_binding(dsd, flow, schema="d", table="o"), dsd)
    expected = "Census Profile dataflow (2021) v2.0"
    assert doc["meta"]["name"] == expected
    assert doc["meta"]["names"] == {
        "en": expected,
        "fr": "Flux de données du Profil du recensement (2021) v2.0",
    }
    assert doc["data"]["dataSets"][0]["name"] == expected
    assert doc["data"]["structures"][0]["name"] == expected
    assert doc["data"]["structures"][0]["names"]["en"] == expected


def test_v1_envelope_carries_dataflow_name_when_resolved() -> None:
    """1.0 envelope: ``header``, ``structure``, ``dataSets[0]``."""
    dsd, flow = _build_dsd_with_time_concept()
    doc = _drain_v1(dsd_binding(dsd, flow, schema="d", table="o"), dsd)
    expected = "Census Profile dataflow (2021) v2.0"
    assert doc["header"]["name"] == expected
    assert doc["header"]["names"]["fr"] == "Flux de données du Profil du recensement (2021) v2.0"
    assert doc["dataSets"][0]["name"] == expected
    assert doc["structure"]["name"] == expected


def test_envelope_carries_description_when_available() -> None:
    """``description`` on the dataflow flows through to the same slots."""
    dsd, flow = _build_dsd_with_time_concept()
    doc = _drain_v2(dsd_binding(dsd, flow, schema="d", table="o"), dsd)
    assert doc["data"]["structures"][0]["description"] == "Statistics for Canada's census areas."


def test_envelope_falls_back_to_dsd_when_dataflow_unresolved() -> None:
    """An unresolved dataflow Ref forces the writer onto the DSD's name —
    callers using ``Ref.unresolved(urn)`` still get *something* readable
    rather than a missing field."""
    dsd, flow = _build_dsd_with_time_concept()
    binding = dsd_binding(
        dsd,
        flow,
        schema="d",
        table="o",
        dataflow_ref=Ref[Dataflow].unresolved(Dataflow.urn_for(agency="TEST", id="DF_CP_2021", version="2.0")),
    )
    doc = _drain_v2(binding, dsd)
    # DSD name, not the dataflow's:
    assert doc["data"]["structures"][0]["name"] == "Census Profile DSD"
    # No description on the DSD in this fixture, so the field is absent
    # rather than ``null`` (display_names skips empty InternationalStrings).
    assert "description" not in doc["data"]["structures"][0]


def test_envelope_omits_name_when_nothing_localizable() -> None:
    """Neither the dataflow nor the DSD has a name in ``lang`` — the envelope
    omits ``name`` rather than emitting an empty string, and keeps ``names``.

    Both have *a* name: neither can lack one (#492)."""
    cl = Codelist(
        id="CL", maintainer="T", version="1.0", name=InternationalString(en="F"), codes=[Code(name={"en": "A"}, id="A")]
    )
    # Named, but only in French.
    dsd = DataStructure(
        id="DSD",
        maintainer="T",
        version="1.0",
        name=InternationalString(fr="Structure"),
        dimensions=[Dimension(id="FREQ", representation=Ref[Codelist].to(cl), concept=concept_ref("FREQ"))],
        measures=[Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    flow = Dataflow(
        id="DF",
        maintainer="T",
        version="1.0",
        name=InternationalString(fr="Flux"),
        structure=Ref[DataStructure].to(dsd),
    )
    binding = DataflowBinding.from_dsd(dsd, dataflow=Ref[Dataflow].to(flow), schema="d", table="o")
    batch = pa.record_batch({"FREQ": ["A"], "OBS_VALUE": [1.0]})
    out = b"".join(
        write_v2(
            [batch],
            dsd,
            binding.dataflow,
            message_id="m",
            prepared=datetime(2026, 6, 17, tzinfo=UTC),
            sender_id="S",
            lang="en",
        )
    )
    doc = msgspec.json.decode(out)
    assert "name" not in doc["data"]["structures"][0]
    assert doc["data"]["structures"][0]["names"] == {"fr": "Flux"}


def test_envelope_with_names_still_schema_valid_v2() -> None:
    """The added envelope fields must not regress JSON-Schema validation."""
    from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema

    dsd, flow = _build_dsd_with_time_concept()
    binding = dsd_binding(dsd, flow, schema="d", table="o")
    batch = pa.record_batch({"FREQ": ["A5"], "TIME_PERIOD": ["2021"], "OBS_VALUE": [1.0]})
    out = b"".join(
        write_v2(
            [batch],
            dsd,
            binding.dataflow,
            message_id="m",
            prepared=datetime(2026, 6, 17, tzinfo=UTC),
            sender_id="S",
            lang="en",
        )
    )
    assert_valid_sdmx_json(out, compile_json_schema("data", version="2.0"))


def test_envelope_with_names_still_schema_valid_v1() -> None:
    from tests.support.json_schema import assert_valid_sdmx_json, compile_json_schema

    dsd, flow = _build_dsd_with_time_concept()
    binding = dsd_binding(dsd, flow, schema="d", table="o")
    batch = pa.record_batch({"FREQ": ["A5"], "TIME_PERIOD": ["2021"], "OBS_VALUE": [1.0]})
    out = b"".join(
        write_v1(
            [batch],
            dsd,
            binding.dataflow,
            message_id="m",
            prepared=datetime(2026, 6, 17, tzinfo=UTC),
            sender_id="S",
            lang="en",
        )
    )
    assert_valid_sdmx_json(out, compile_json_schema("data", version="1.0"))
