"""`unrepresentable()` — what a format would silently drop, asked in advance.

The model is a union of the 2.1 and 3.0 information models, so writing an
artefact to a format can refuse, drop, or downgrade. A refusal is visible; a
**drop is not**, and until now the only record of the drops was a table in this
project's test suite. This is that table made answerable by a caller.

Two properties carry the file. It must report a gap only when *this* artefact
actually carries the field — a check that fires on every codelist is noise, and
noise is not read. And a store that declares a target must **raise**, because
declaring one is a statement of intent and a silent loss at write time is the
thing the declaration was made to prevent.
"""

from __future__ import annotations

import re

import pytest

import sdmxlib as sl
from sdmxlib.formats.gaps import TARGETS, declared_gaps, unrepresentable
from sdmxlib.model.codelist import Codelist
from sdmxlib.model.metadatastructure import MetadataStructure, ReportStructure
from sdmxlib.model.organisation import Agency

pytestmark = pytest.mark.unit


def _codelist(
    *, legacy_is_final: bool | None = None, version: str = "1.0", codes: list[sl.Code] | None = None
) -> Codelist:
    return Codelist(
        id="CL",
        maintainer=Agency(id="AG"),
        version=version,
        name={"en": "CL"},
        legacy_is_final=legacy_is_final,
        codes=[] if codes is None else codes,
    )


# ── the check ─────────────────────────────────────────────────────────────


def test_a_field_the_target_cannot_carry_is_reported() -> None:
    (gap,) = unrepresentable(_codelist(legacy_is_final=True), "3.0")
    assert gap.field == "legacy_is_final"
    assert "3.0 has no isFinal" in gap.reason


def test_a_field_the_target_can_carry_is_not() -> None:
    assert unrepresentable(_codelist(legacy_is_final=True), "2.1") == ()


def test_an_unset_field_is_not_a_gap() -> None:
    """A 2.1 `isFinal` the version agrees with is carried by the version (#420).

    Reporting every artefact of a type as lossy would make the check noise,
    and a noisy check is one nobody runs.
    """
    assert unrepresentable(_codelist(), "3.0") == ()
    assert unrepresentable(_codelist(legacy_is_final=False), "3.0") == ()
    assert unrepresentable(_codelist(legacy_is_final=True, version="1.0.0"), "3.0") == ()


def test_a_disagreeing_false_is_a_gap_too() -> None:
    """`1.0.0` is final in 3.0; a 2.1 document that said otherwise is lost there."""
    (gap,) = unrepresentable(_codelist(legacy_is_final=False, version="1.0.0"), "3.0")
    assert gap.field == "legacy_is_final"


def test_an_unknown_target_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown target"):
        unrepresentable(_codelist(), "4.0")


def test_the_gap_reads_as_a_sentence() -> None:
    """It ends up in a traceback and in a report; both want prose."""
    (gap,) = unrepresentable(_codelist(legacy_is_final=True), "3.0")
    assert str(gap) == (
        "legacy_is_final is not representable in SDMX-ML 3.0: "
        "SDMX 3.0 has no isFinal; finality is read from the version, and this one says otherwise"
    )


def test_every_declared_row_names_a_real_target() -> None:
    """The table is data, so it can be wrong in ways a type cannot catch."""
    for target, type_name, field, reason in declared_gaps():
        assert target in TARGETS, (target, type_name)
        assert field, (target, type_name)
        assert reason, (target, type_name, field)


def test_a_gap_declared_on_a_base_class_applies_to_its_subclasses() -> None:
    """`legacy_is_final` is declared on every maintainable, not on `Codelist`.

    The round-trip guard keys by test case, so this gap was recorded once
    under whichever case happened to exercise it. Declared against the base
    here, it applies where it actually applies.
    """
    for artefact in (
        _codelist(legacy_is_final=True),
        sl.Dataflow(id="DF", maintainer=Agency(id="AG"), name={"en": "DF"}, legacy_is_final=True),
    ):
        assert [gap.field for gap in unrepresentable(artefact, "3.0")] == ["legacy_is_final"]


# ── a declared target is a statement of intent ────────────────────────────


def test_a_store_with_a_target_refuses_what_that_target_cannot_carry() -> None:
    with sl.LocalRegistry(":memory:", target="3.0") as registry, pytest.raises(ValueError, match="cannot represent"):
        registry.add(_codelist(legacy_is_final=True))


def test_a_store_with_a_target_accepts_what_it_can() -> None:
    with sl.LocalRegistry(":memory:", target="3.0") as registry:
        registry.add(_codelist())
        assert registry.query(sl.q.codelist).count() == 1


def test_a_store_without_a_target_stays_format_agnostic() -> None:
    """The default, and it has to be: a cache of a 2.1 registry must accept
    what that registry publishes, and a target would refuse it."""
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(_codelist(legacy_is_final=True))
        assert registry.query(sl.q.codelist).count() == 1


def test_the_refusal_names_the_field_the_target_and_the_escape() -> None:
    """An author fixing one thing at a time learns the format one traceback at
    a time, which is the slowest way to learn anything — so the message lists
    the fields, says which target refused, and names the way out."""
    with (
        sl.LocalRegistry(":memory:", target="3.0") as registry,
        pytest.raises(ValueError, match="cannot represent") as caught,
    ):
        registry.add(_codelist(legacy_is_final=True))
    message = str(caught.value)
    assert "legacy_is_final" in message
    assert "target='3.0'" in message
    assert "without a target" in message, "the message must name the escape"


def test_an_unknown_target_is_refused_at_construction() -> None:
    """Before anything is stored, so a typo is not discovered on the first add."""
    with pytest.raises(ValueError, match="unknown target"):
        sl.LocalRegistry(":memory:", target="2.2")


def test_a_wrapped_connection_carries_the_target_too() -> None:
    """`from_connection` goes through `cls.__new__`, so it initialises every
    field itself — a new one added to `__init__` alone is simply absent, and
    surfaces as an `AttributeError` on the first `add`. Which is how this was
    found: the shared-connection cookbook test failed.
    """
    import duckdb

    conn = duckdb.connect(":memory:")
    strict = sl.LocalRegistry.from_connection(conn, target="3.0")
    with pytest.raises(ValueError, match="cannot represent"):
        strict.add(_codelist(legacy_is_final=True))

    relaxed = sl.LocalRegistry.from_connection(conn)
    relaxed.add(_codelist(legacy_is_final=True))


# ── the store is a target too ─────────────────────────────────────────────


def _msd_with_a_report_structure() -> MetadataStructure:
    return MetadataStructure(
        id="MSD",
        maintainer=Agency(id="AG"),
        name={"en": "MSD"},
        report_structures=[ReportStructure(id="REPORT")],
    )


def test_the_store_declares_the_msd_2_1_layer() -> None:
    """The store holds the 3.x MSD; the 2.1 target and report layer is
    converted on ingest by #421, so it gets no table (#442). Storing it with
    one anyway reads back as nothing, silently — the same shape as a wire
    format's drop, one layer down.

    This was `codes[].uri` until #442 gave every item table a `uri` column.
    """
    (gap,) = unrepresentable(_msd_with_a_report_structure(), "storage")
    assert gap.field == "report_structures"
    assert "#421" in gap.reason


def test_an_item_uri_is_no_longer_a_store_gap() -> None:
    """#442 stores `uri` on every item table, so a code's uri is not declared."""
    with_uri = _codelist(codes=[sl.Code(id="A", name={"en": "a"}, uri="http://example.test/a")])
    assert unrepresentable(with_uri, "storage") == ()


def test_an_msd_without_the_2_1_layer_loses_nothing_to_the_store() -> None:
    msd = MetadataStructure(id="MSD", maintainer=Agency(id="AG"), name={"en": "MSD"})
    assert unrepresentable(msd, "storage") == ()


def test_a_store_can_declare_itself_lossless() -> None:
    """`target="storage"` reads oddly at first and is the useful case: "hold
    me to what this store can actually keep"."""
    with (
        sl.LocalRegistry(":memory:", target="storage") as registry,
        pytest.raises(ValueError, match=re.escape("report_structures")),
    ):
        registry.add(_msd_with_a_report_structure())


def test_the_message_does_not_call_the_store_an_sdmx_ml_version() -> None:
    """Two sites formatted the target and both said "SDMX-ML storage", which
    is not a thing. `describe()` is the one place now, and this checks it."""
    msd = _msd_with_a_report_structure()
    (gap,) = unrepresentable(msd, "storage")
    assert "SDMX-ML storage" not in str(gap)
    assert "the DuckDB store" in str(gap)

    with (
        sl.LocalRegistry(":memory:", target="storage") as registry,
        pytest.raises(ValueError, match="cannot represent") as caught,
    ):
        registry.add(msd)
    assert "SDMX-ML storage" not in str(caught.value)
    assert "the DuckDB store" in str(caught.value)


def test_a_tracked_defect_is_not_declared_as_a_gap() -> None:
    """`level.coding_format` is not persisted (#159 B) and *should* be.

    Declaring it here would tell an author the loss is permanent and invite
    them to design around it, which is exactly wrong for a bug someone means
    to fix.
    """
    declared = {(target, field) for target, _type, field, _reason in declared_gaps()}
    assert ("storage", "levels[].coding_format") not in declared
