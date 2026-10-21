"""Item URNs are built once per artefact, not once per item (#139).

`_make_item_urn` parsed the owner's URN and constructed an `SdmxUrn` for every
item written. Both are constant across one artefact's items — only `item_id`
varies — so a 499,143-node hierarchy paid 499,143 parses to produce 499,143
strings that share a prefix.

**These are count assertions, deliberately.** The wall clock cannot see this
change: interleaved A/B/A runs against the real 2021 Census Profile bundle came out
6.60 s / 6.48 s / 5.32 s for the same write, where the first and third are
*identical code*. DuckDB insert time dominates and the machine drifts more
than the effect being measured. `docs/design/caching.md` asks for exactly this
— guard the fix with the count of expensive calls, not a duration — and it is
the only claim here that survives being re-run on a loaded laptop.

The component writer is the one caller left on the per-item path: its
`comp_type` varies per iteration, so there is no single prefix, and a DSD has
tens of components rather than hundreds of thousands.
"""

from __future__ import annotations

from typing import Any

import pytest

import sdmxlib as sl
from sdmxlib.model import urn as urn_module
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy
from sdmxlib.storage.writers import _item_urn_prefix, _make_item_urn
from tests.support.factories import code_urn

pytestmark = pytest.mark.unit

#: Enough items that a per-item parse is unmistakable in the count, and few
#: enough to stay a unit test.
ITEMS = 500


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Counts of `SdmxUrn.parse` / `.make` over the block."""
    calls = {"parse": 0, "make": 0}
    real_parse = urn_module.SdmxUrn.parse
    real_make = urn_module.SdmxUrn.make

    def counting_parse(text: str) -> object:
        calls["parse"] += 1
        return real_parse(text)

    def counting_make(cls: type, **kwargs: Any) -> object:
        # `Any` because this forwards `SdmxUrn.make`'s six keyword parameters
        # verbatim; `object` would need each one narrowed to pass them on.
        calls["make"] += 1
        return real_make(cls, **kwargs)

    monkeypatch.setattr(urn_module.SdmxUrn, "parse", counting_parse)
    monkeypatch.setattr(urn_module.SdmxUrn, "make", counting_make)
    return calls


def _codelist() -> sl.Codelist:
    return sl.Codelist(
        id="CL_BIG",
        maintainer="AG",
        version="1.0",
        name={"en": "Big"},
        codes=[sl.Code(id=f"C{i}", name={"en": f"c{i}"}) for i in range(ITEMS)],
    )


def _hierarchy() -> Hierarchy:
    return Hierarchy(
        id="H_BIG",
        maintainer="AG",
        version="1.0",
        name={"en": "Big"},
        tree=[HierarchicalCode(id=f"N{i}", code=code_urn("CL", f"N{i}")) for i in range(ITEMS)],
    )


@pytest.mark.parametrize("artefact", [_codelist(), _hierarchy()], ids=["codelist", "hierarchy"])
def test_the_owner_urn_is_parsed_once_not_once_per_item(
    counted: dict[str, int], artefact: sl.Codelist | Hierarchy
) -> None:
    """The regression this guards: one parse per *item* rather than per artefact.

    A handful of parses is expected — the writer resolves its own URN and any
    outgoing refs — so the bound is "well under the item count", not one.
    Before the fix it was `ITEMS` exactly, plus those few.
    """
    with sl.LocalRegistry(":memory:") as reg:
        reg.add(artefact)

    assert counted["parse"] < ITEMS / 10, (
        f"{counted['parse']} parses for {ITEMS} items — the owner URN is being "
        f"re-parsed per item, which is what #139 removed"
    )
    assert counted["make"] < ITEMS / 10, f"{counted['make']} SdmxUrn constructions for {ITEMS} items"


def test_the_hoisted_prefix_builds_the_same_urn_as_the_per_item_form() -> None:
    """The two spellings must agree, or the hoist silently rewrites every URN.

    `_make_item_urn` is still the per-item form and still used by the component
    writer, so this is a live comparison rather than a museum piece.
    """
    codelist = _codelist()
    urn = str(sl.artefact_urn(codelist))
    prefix = _item_urn_prefix(codelist, urn, "Code")
    for code in list(codelist.codes)[:20]:
        assert prefix + code.id == _make_item_urn(codelist, urn, "Code", code.id)


def test_the_prefix_ends_in_a_separator() -> None:
    """Concatenation is only faithful if the prefix carries the dot.

    `SdmxUrn.__str__` renders an item URN as ``base + "." + item_id``; this
    builds the base without an `item_id` and appends the separator, so a prefix
    that lost it would silently produce `…CL_BIG(1.0)C0`.
    """
    codelist = _codelist()
    prefix = _item_urn_prefix(codelist, str(sl.artefact_urn(codelist)), "Code")
    assert prefix.endswith("."), prefix
    assert ".Code=" in prefix, "and the item class replaces the owner's"
