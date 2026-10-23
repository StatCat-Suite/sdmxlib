"""The real bundle, republished, checks clean (GH-67).

`data_plane_bundle` republishes its copy with `write_manifest`, so this is
the checker against 2.6 GB of real files and a real lake: every sha256, every
file DuckLake references, its orphans, and the bound observation counts.

The structural closure is left out (`references=False`): it loads every
artefact, about 30 s and 5 GB here, and finds nothing on this bundle (measured
when GH-67 was written). A closure regression shows up in the unit tests'
`test_a_reference_outside_the_bundle_is_named_where_it_sits` far faster.
"""

import pytest

from sdmxlib.bundle import check_bundle

pytestmark = [pytest.mark.bench, pytest.mark.bundle]


def test_the_republished_bundle_checks_clean(data_plane_bundle):
    report = check_bundle(data_plane_bundle, references=False)
    assert report.checked == ("manifest", "files", "lake", "bindings")
    assert report.ok, "\n".join(map(str, report.findings))
