"""The published census bundle checks clean at every profile (GH-67, GH-130).

The checker against 2.0 GB of real files and a real lake, read in place. At
``publish``: every digest, every file DuckLake references, the bound
observation counts, every stored reference, every fingerprint recomputed and
the availability rescanned. That repeats much of publishing's work, about
4.5 minutes, so it is marked `recompute`: `just bench-bundle` runs it and
`just ci` does not. ``receive`` and ``open`` hash less, and must still find
nothing on a clean bundle.
"""

import pytest

from sdmxlib.edition import check_edition

pytestmark = [pytest.mark.bench, pytest.mark.bundle]


@pytest.mark.parametrize(
    ("at", "checked"),
    [
        pytest.param(
            "publish",
            ("manifest", "files", "lake", "bindings", "references", "tags", "fingerprints", "availability"),
            marks=pytest.mark.recompute,
            id="publish",
        ),
        pytest.param("receive", ("manifest", "files", "lake", "bindings", "references", "tags"), id="receive"),
        pytest.param("open", ("manifest", "files"), id="open"),
    ],
)
def test_the_republished_bundle_checks_clean(data_plane_bundle, at, checked):
    report = check_edition(data_plane_bundle, at=at)
    assert report.checked == checked
    assert report.ok, "\n".join(map(str, report.findings))
