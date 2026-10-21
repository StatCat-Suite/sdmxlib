"""Static probe: `resolved_codelist` declares what it returns (#398).

`Dimension.resolved_codelist` declared `Codelist | None` while a DSD read from
a `LocalRegistry` handed back a `LazyCodelist`, so `len(dim.codes)` and
`[c.id for c in dim.codes]` type-checked and raised. `CodelistLike` is what the
two classes share; this asserts the declaration and that both satisfy it.

The runtime half is `tests/unit/test_codelist_like.py`.
"""

from typing import assert_type

import sdmxlib as sl
from sdmxlib.model.codelist import Codelist, CodelistLike
from sdmxlib.model.collections import ItemList
from sdmxlib.model.datastructure import Dimension
from sdmxlib.storage.lazy import LazyCodelist


def _the_declaration(dim: Dimension) -> None:
    assert_type(dim.resolved_codelist, CodelistLike | None)
    assert_type(dim.code_ids(), list[str] | None)
    assert_type(dim.codes, ItemList[sl.Code] | None)


def _both_classes_satisfy_it(eager: Codelist, lazy: LazyCodelist) -> None:
    """Structural, so this is the check rather than a restatement of one."""
    accepts: list[CodelistLike] = [eager, lazy]
    assert len(accepts) == 2


def _the_shared_surface(cl: CodelistLike) -> None:
    assert_type(cl.code_ids(), list[str])
    assert_type(cl.code_map(lang="en"), dict[str, str])
    assert_type(cl.materialize(), Codelist)
    assert_type(cl.codes_for(["A"]), dict[str, sl.Code])
    assert_type(len(cl), int)
    assert_type(cl.id, str)
    assert_type(cl.version, str)


def _materialize_reaches_the_full_surface(cl: CodelistLike) -> None:
    """The escape hatch is typed, so a caller needing `Code` objects gets them."""
    assert_type(cl.materialize().codes, ItemList[sl.Code])


# and these must not type-check — the point of the change:
#
#   cl.codes                     # CodelistLike declares no `codes`; see below
#   cl.name                      # nor `name`
#
# Both members exist on *both* classes. They are absent from the protocol
# because basedpyright reads `Codelist.name` as `InternationalString` for
# attribute access and as `__converterDescriptor_name` for protocol matching,
# so a protocol naming either does not check. `tests/unit/test_codelist_like.py
# ::test_name_and_description_are_absent_for_a_reason` fails the day that is
# fixed, so the workaround cannot outlive its cause.
