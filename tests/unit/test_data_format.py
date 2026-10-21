"""The data-format vocabulary, checked against the two reference libraries.

`DataFormat` is a list of strings copied from a spec, which is the kind of
thing that is either exactly right or quietly wrong — a typo'd media type
produces a 406 from a live provider and nothing at all from a test suite. So
the member list is compared against pysdmx's `DataFormat` rather than against
itself, and the derived properties are re-parsed from the media type by hand
rather than read back off the table that produced them.
"""

from __future__ import annotations

import pytest

from sdmxlib.formats.data_format import DataFormat
from tests.support.repo import ROOT

pytestmark = pytest.mark.unit


def test_every_member_matches_pysdmx_exactly() -> None:
    """The list is the spec's, and pysdmx read the same spec.

    Compared as a *set of media types*, not by name: the names are ours
    (`SDMX_ML_2_1_GENERIC` where pysdmx says `SDMX_ML_2_1_GEN`), the media
    types are the standard's and must agree character for character.
    """
    pysdmx_qb = pytest.importorskip("pysdmx.api.qb.data", reason="pysdmx is in the bench-compete group")
    theirs = {f.value for f in pysdmx_qb.DataFormat}
    ours = {f.value for f in DataFormat}
    assert ours == theirs


def test_the_two_csv_versions_sdmx1_omits_are_present() -> None:
    """sdmx1's `MEDIA_TYPES` stops at SDMX-CSV 1.0.0.

    2.0.0 is what this library's own writer emits, so a vocabulary missing it
    could not ask a provider for the format we can already produce. Pinned
    because the omission is easy to inherit when cross-checking against sdmx1.
    """
    assert DataFormat.SDMX_CSV_2_0_0.version == "2.0.0"
    assert DataFormat.SDMX_CSV_2_1_0.version == "2.1.0"


@pytest.mark.parametrize("fmt", list(DataFormat))
def test_derived_properties_re_parse_the_media_type(fmt: DataFormat) -> None:
    """`base` / `version` / `suffix` restated independently of the properties.

    The point of deriving them from the media type was that a member cannot
    be described inconsistently. That only holds if the derivation is right,
    so this parses the same string a second way.
    """
    head, _, params = fmt.value.partition(";")
    assert fmt.base == head.split("+")[-1]
    assert fmt.version == params.removeprefix("version=")
    assert fmt.suffix == "." + fmt.base
    assert fmt.media_type == fmt.value


def test_xml_at_2_1_names_four_formats_which_is_why_this_is_not_a_pair() -> None:
    """The evidence against the design's ``(media type, version)`` tuple.

    SDMX-ML 2.1 carries data under four media types, so ``("xml", "2.1")``
    names no single format; ``data+xml`` only appears at 3.0, so
    ``("xml", "2.1")`` cannot even be normalised into one. Delete this test
    and the enum starts looking like ceremony over a pair.
    """
    xml_2_1 = {f for f in DataFormat if f.base == "xml" and f.version == "2.1"}
    assert len(xml_2_1) == 4
    assert all(f.value.count("+xml") == 1 for f in xml_2_1)
    assert "vnd.sdmx.data+xml" not in " ".join(f.value for f in xml_2_1)


def test_no_two_members_share_a_media_type() -> None:
    """A duplicate value in a `StrEnum` silently becomes an alias — the second
    name would resolve to the first member and the format would be
    unrequestable rather than wrong-looking."""
    assert len(list(DataFormat)) == len({f.value for f in DataFormat}) == 11


def test_a_member_is_its_media_type() -> None:
    """`StrEnum`, deliberately: it goes straight into a headers dict."""
    assert DataFormat.SDMX_CSV_2_0_0 == "application/vnd.sdmx.data+csv;version=2.0.0"
    assert {"Accept": DataFormat.SDMX_JSON_2_0_0}["Accept"].startswith("application/")


def test_the_cookbook_table_lists_every_member() -> None:
    """The docs restate the vocabulary, so they can fall behind it.

    A member added without a row would be undiscoverable — the enum is the
    kind of thing users read the docs for rather than `dir()`. Checked by
    name *and* media type, since a stale row is worse than a missing one.
    """
    text = (ROOT / "docs" / "cookbook" / "data.md").read_text(encoding="utf-8")
    missing = [f.name for f in DataFormat if f"| `{f.name}` | `{f.value}` |" not in text]
    assert not missing, f"docs/cookbook/data.md is missing rows for {missing}"
