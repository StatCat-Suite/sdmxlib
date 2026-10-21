"""Tests for sdmxlib.formats.sdmx_csv.reader — the short URN a file names its structure by.

Reading a file is `reg.scan_csv`'s, tested in `test_scan_registry.py`.
"""

import pytest

from sdmxlib.formats.sdmx_csv.reader import parse_dataflow_id

pytestmark = pytest.mark.unit


# ── parse_dataflow_id ────────────────────────────────────────────────────────


def test_parse_dataflow_id_basic():
    assert parse_dataflow_id("ESTAT:STS_INPR_M(1.0)") == ("ESTAT", "STS_INPR_M", "1.0")


def test_parse_dataflow_id_underscored_agency():
    assert parse_dataflow_id("PINAX_SUITE:CENSUS_PROFILE(2021)") == ("PINAX_SUITE", "CENSUS_PROFILE", "2021")


def test_parse_dataflow_id_rejects_missing_version():
    with pytest.raises(ValueError, match="expected 'AGENCY:ID"):
        parse_dataflow_id("ESTAT:STS_INPR_M")


def test_parse_dataflow_id_rejects_missing_agency():
    with pytest.raises(ValueError, match="expected 'AGENCY:ID"):
        parse_dataflow_id("STS_INPR_M(1.0)")


def test_parse_dataflow_id_rejects_empty():
    with pytest.raises(ValueError, match="expected 'AGENCY:ID"):
        parse_dataflow_id("")
