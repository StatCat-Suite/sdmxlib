"""`SdmxUrn.parse_item` must agree with `SdmxUrn.parse`, always (#154).

The readers parse item URNs in batches of hundreds of thousands — 499,138 on
the census bundle's `H_CP_GEO_FED`, one per node — and every URN in one scheme
shares everything but its `item_id`. `parse_item` splits at `")."`, caches the
scheme half, and builds the rest directly: 0.85 us to 0.55 us over 500,000
URNs.

That makes it a *second* implementation of the URN grammar's item case, which
is the thing worth testing. `parse` is the oracle — not by convention but
literally: these compare the two on the same input, including the shapes
`parse_item` is supposed to hand back.

Hypothesis draws the URNs, because the interesting inputs are structural (a
dot in the item id, a versionless URN, an id containing a bracket) and a
hand-written list is a list of the shapes its author thought of. The explicit
cases are the ones found by hand and kept as regressions.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sdmxlib.model.urn import SdmxUrn

pytestmark = pytest.mark.unit

_IDENT = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789", min_size=1, max_size=12)
#: Item ids are the loose part of the grammar — real ones contain dots, dashes
#: and digits, and a dot is exactly what the `").."` split could get wrong.
_ITEM = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-", min_size=1, max_size=20)
_VERSION = st.sampled_from(["1.0", "1.0.0", "2.1", "latest", "1.0.0-draft"])
_PACKAGE = st.sampled_from(["codelist", "conceptscheme", "categoryscheme"])
_CLASS = st.sampled_from(["Code", "Concept", "Category"])


@st.composite
def item_urns(draw: st.DrawFn) -> str:
    package = draw(_PACKAGE)
    artefact_class = draw(_CLASS)
    return (
        f"urn:sdmx:org.sdmx.infomodel.{package}.{artefact_class}="
        f"{draw(_IDENT)}:{draw(_IDENT)}({draw(_VERSION)}).{draw(_ITEM)}"
    )


@given(item_urns())
def test_parse_item_agrees_with_parse(urn):
    fast = SdmxUrn.parse_item(urn)
    slow = SdmxUrn.parse(urn)
    assert fast == slow
    # Equality on `SdmxUrn` could in principle ignore a field, so compare the
    # round-trip too: if any part were wrong, the string would differ.
    assert str(fast) == str(slow)
    assert fast.item_id == slow.item_id


@pytest.mark.parametrize(
    "urn",
    [
        "urn:sdmx:org.sdmx.infomodel.codelist.Code=STC:CL_G(1.0).2021A0001",
        "urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=STC:CS(1.0).FREQ",
        # A dot inside the item id — the separator this splits on appears twice.
        "urn:sdmx:org.sdmx.infomodel.codelist.Code=STC:CL_G(1.0).A.B.C",
        # No version.
        "urn:sdmx:org.sdmx.infomodel.codelist.Code=STC:CL_G.X",
        # Not an item URN at all — must fall through to `parse`.
        "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=STC:CL_G(1.0)",
        "urn:sdmx:org.sdmx.infomodel.datastructure.DataStructure=STC:DSD(2.0)",
    ],
)
def test_named_shapes_agree(urn):
    assert SdmxUrn.parse_item(urn) == SdmxUrn.parse(urn)
    assert str(SdmxUrn.parse_item(urn)) == str(SdmxUrn.parse(urn))


def test_a_non_item_urn_falls_through_to_parse():
    """No `").."` separator means the fast path cannot apply and must not try."""
    urn = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=STC:CL_G(1.0)"
    parsed = SdmxUrn.parse_item(urn)
    assert parsed.item_id is None
    assert parsed.artefact_class == "Codelist"


def test_the_scheme_cache_does_not_leak_between_schemes():
    """The cache is keyed on the scheme prefix — the one way this could go wrong.

    Two schemes differing only in agency, parsed adjacently. A cache keyed on
    anything coarser hands the second one the first one's agency, and every
    URN it produces is silently wrong while remaining well-formed.
    """
    first = SdmxUrn.parse_item("urn:sdmx:org.sdmx.infomodel.codelist.Code=AAA:CL(1.0).X")
    second = SdmxUrn.parse_item("urn:sdmx:org.sdmx.infomodel.codelist.Code=BBB:CL(1.0).X")
    assert first.agency == "AAA"
    assert second.agency == "BBB"
    assert first != second


def test_versions_do_not_leak_between_schemes():
    """Same argument, on the field the split most nearly touches."""
    first = SdmxUrn.parse_item("urn:sdmx:org.sdmx.infomodel.codelist.Code=STC:CL(1.0).X")
    second = SdmxUrn.parse_item("urn:sdmx:org.sdmx.infomodel.codelist.Code=STC:CL(2.0).X")
    assert (first.version, second.version) == ("1.0", "2.0")


def test_a_malformed_urn_fails_the_same_way_as_parse():
    """The fast path must not turn a parse error into a wrong answer."""
    bad = "not a urn at all"
    with pytest.raises(ValueError, match="Invalid SDMX URN") as slow:
        SdmxUrn.parse(bad)
    with pytest.raises(ValueError, match="Invalid SDMX URN") as fast:
        SdmxUrn.parse_item(bad)
    # The same message, not merely the same type: a fast path that raised its
    # own error would still pass a bare `raises(ValueError)`.
    assert str(fast.value) == str(slow.value)
