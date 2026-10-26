"""`LocalRegistry.write_observations`: the public way a builder writes its rows, partitioned as asked (GH-198).

DuckLake inlines a small insert into its catalog rather than writing a data
file, so these write enough rows (10,000) for files to exist and be counted.
"""

from pathlib import Path

import polars as pl
import pytest

import sdmxlib as sl

pytestmark = pytest.mark.unit

_ROWS = pl.DataFrame({"LEVEL": ["A", "B"] * 5000, "V": [float(i) for i in range(10000)]})


def _files(registry: sl.LocalRegistry) -> list[str]:
    rows = registry.connection.execute(
        "SELECT data_file FROM ducklake_list_files($lake, 'obs', schema => 'd') ORDER BY 1",
        {"lake": registry.lake_alias},
    ).fetchall()
    return [str(path).rsplit("/obs/", 1)[1] for (path,) in rows]


def test_a_builder_writes_rows_into_its_publications_lake(tmp_path: Path) -> None:
    with sl.LocalRegistry.build(tmp_path / "b") as registry:
        registry.write_observations(schema="d", table="obs", source=_ROWS)
        count = registry.connection.execute(
            "SELECT count(*) FROM query_table($table)", {"table": f"{registry.lake_alias}.d.obs"}
        ).fetchall()
    assert count == [(10000,)]


def test_partition_by_writes_one_set_of_files_per_value(tmp_path: Path) -> None:
    with sl.LocalRegistry.build(tmp_path / "b") as registry:
        registry.write_observations(schema="d", table="obs", source=_ROWS, partition_by=("LEVEL",))
        partitioned = _files(registry)
    assert [path.split("/", 1)[0] for path in partitioned] == ["LEVEL=A", "LEVEL=B"]


def test_without_partition_by_the_rows_share_files(tmp_path: Path) -> None:
    """The discrimination check: the same rows unpartitioned land in no partition directory."""
    with sl.LocalRegistry.build(tmp_path / "b") as registry:
        registry.write_observations(schema="d", table="obs", source=_ROWS)
        unpartitioned = _files(registry)
    assert unpartitioned
    assert not any("=" in path for path in unpartitioned)


def test_a_partition_column_must_be_a_column_of_the_source(tmp_path: Path) -> None:
    with (
        sl.LocalRegistry.build(tmp_path / "b") as registry,
        pytest.raises(ValueError, match=r"cannot partition the write by GEO_LEVEL: not a column of the source"),
    ):
        registry.write_observations(schema="d", table="obs", source=_ROWS, partition_by=("GEO_LEVEL",))


def test_an_append_keeps_the_partitioning_and_refuses_partition_by(tmp_path: Path) -> None:
    with sl.LocalRegistry.build(tmp_path / "b") as registry:
        registry.write_observations(schema="d", table="obs", source=_ROWS, partition_by=("LEVEL",))
        with pytest.raises(ValueError, match="an append keeps"):
            registry.write_observations(schema="d", table="obs", source=_ROWS, partition_by=("LEVEL",), mode="append")
        registry.write_observations(schema="d", table="obs", source=_ROWS, mode="append")
        appended = _files(registry)
    assert {path.split("/", 1)[0] for path in appended} == {"LEVEL=A", "LEVEL=B"}
    assert len(appended) == 4
