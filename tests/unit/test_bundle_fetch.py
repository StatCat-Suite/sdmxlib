"""`fetch_bundle`'s refusals that need no network (GH-68).

The fetch itself is replayed from recorded ECB responses in
`tests/integration/test_bundle_fetch_live.py`.
"""

from pathlib import Path

import pytest

import sdmxlib as sl
from sdmxlib.bundle import fetch_bundle

pytestmark = pytest.mark.unit


def test_a_directory_that_is_not_empty_is_refused_before_anything_is_fetched(tmp_path: Path) -> None:
    """Refused first, so nothing is asked of the provider: a provider URL that
    cannot resolve would raise something else if it were reached."""
    (tmp_path / "notes.txt").write_text("x")
    with pytest.raises(FileExistsError, match="not empty"):
        fetch_bundle("https://example.invalid/rest", sl.Dataflow.urn_for(agency="A", id="F"), tmp_path, source_id="X")
    assert [p.name for p in tmp_path.iterdir()] == ["notes.txt"]
