"""The real bundle, republished, checks clean (GH-67).

`data_plane_bundle` republishes its copy with `write_manifest`, so this is
the checker against 2.6 GB of real files and a real lake: every sha256, every
file DuckLake references, its orphans, the bound observation counts, and the
2.16 million stored references, which the SQL check reads in 0.15 s (GH-129).
"""

import pytest

from sdmxlib.bundle import check_bundle

pytestmark = [pytest.mark.bench, pytest.mark.bundle]


def test_the_republished_bundle_checks_clean(data_plane_bundle):
    report = check_bundle(data_plane_bundle)
    assert report.checked == ("manifest", "files", "lake", "bindings", "references")
    assert report.ok, "\n".join(map(str, report.findings))
