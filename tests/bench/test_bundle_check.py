"""The real bundle, republished, checks clean at every profile (GH-67, GH-130).

`data_plane_bundle` republishes its copy with `write_manifest`, so this is
the checker against 2.6 GB of real files and a real lake. At ``publish``:
every sha256, every file DuckLake references, the bound observation counts,
the 2.16 million stored references (0.15 s, GH-129) and every fingerprint
recomputed, which repeats publishing's 6.5 minutes. ``receive`` and ``open``
hash less, and must still find nothing on a clean bundle.
"""

import pytest

from sdmxlib.bundle import check_bundle

pytestmark = [pytest.mark.bench, pytest.mark.bundle]


@pytest.mark.parametrize(
    ("at", "checked"),
    [
        ("publish", ("manifest", "files", "lake", "bindings", "references", "fingerprints")),
        ("receive", ("manifest", "files", "lake", "bindings", "references")),
        ("open", ("manifest", "files")),
    ],
)
def test_the_republished_bundle_checks_clean(data_plane_bundle, at, checked):
    report = check_bundle(data_plane_bundle, at=at)
    assert report.checked == checked
    assert report.ok, "\n".join(map(str, report.findings))
