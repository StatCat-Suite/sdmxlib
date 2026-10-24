"""The render version moves exactly when a writer's output does (GH-132).

Two links, each a test: a writer's output is its golden file, and the golden
file's digest is its render version. A writer change fails the first until
the golden file is rewritten (``uv run python -m tests.support.render_corpus``),
and then the second until `RENDER_VERSION` is updated from what that prints.
"""

import hashlib

import pytest

from sdmxlib.formats.data_format import DataFormat
from sdmxlib.formats.render import RENDER_VERSION, render_version
from tests.support import render_corpus

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("media_type", list(render_corpus.WRITERS))
def test_each_writer_writes_its_golden_file(media_type: str) -> None:
    assert render_corpus.written(media_type) == render_corpus.golden(media_type).read_bytes(), (
        f"the {media_type} writer's output changed: run `uv run python -m tests.support.render_corpus` "
        "and update RENDER_VERSION from what it prints"
    )


@pytest.mark.parametrize("media_type", list(render_corpus.WRITERS))
def test_each_render_version_is_its_golden_files_digest(media_type: str) -> None:
    expected = hashlib.sha256(render_corpus.golden(media_type).read_bytes()).hexdigest()[:16]
    assert RENDER_VERSION[media_type] == expected


def test_every_written_format_has_a_render_version_and_a_golden_file() -> None:
    assert set(RENDER_VERSION) == set(render_corpus.WRITERS)
    assert sorted(p.name for p in render_corpus.GOLDEN.iterdir()) == sorted(
        name for name, _ in render_corpus.WRITERS.values()
    ), "no stale golden file"


def test_the_versions_differ_between_formats() -> None:
    """Discrimination: a mapping of one repeated value would pass the digest test only by coincidence."""
    assert len(set(RENDER_VERSION.values())) == len(RENDER_VERSION) == 10


def test_a_data_format_member_is_its_media_type() -> None:
    assert render_version(DataFormat.SDMX_CSV_2_0_0) == RENDER_VERSION["application/vnd.sdmx.data+csv;version=2.0.0"]


def test_a_format_sdmxlib_does_not_write_is_named() -> None:
    with pytest.raises(KeyError, match="sdmxlib writes no format as"):
        render_version(DataFormat.SDMX_ML_3_0)
