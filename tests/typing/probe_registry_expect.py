"""Static probe: `expect=` names the result, whatever the URN's phantom (#406).

This is the half of `expect=` that a runtime test cannot show. The feature
exists because `get(SdmxUrn.parse(text))` has no usable static type — #399
measured basedpyright taking the first overload and answering
`LazyCodelist | None` for every URN, and pyrefly answering `Any | None`.
Neither is what the caller knows.

`expect=` sidesteps that instead of fighting it: `T` binds to the *argument*,
so the ambiguous-overload case never arises and both checkers agree. The
assertions below are what "agree" means, and they are checked by basedpyright
on every `just check`.

Compare with `probe_urn_any.py`, which pins the ambiguous form. The two files
are the before and after of the same call.

The runtime half is `tests/unit/test_registry_expect.py`.
"""

from typing import assert_type

import sdmxlib as sl
from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Codelist, CodelistLike
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.metadataset import MetadataSet
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.storage.lazy import LazyCodelist


def _the_case_the_issue_named(local: LocalRegistry, text: str) -> None:
    """A downstream SDMX REST service's two call sites, typed.

    Both read a URN out of a store as a string and need a `MetadataSet`. What
    they had to write was a walrus inside an `isinstance` inside a
    comprehension, with a four-line comment explaining the union.
    """
    assert_type(local.get(SdmxUrn.parse(text), expect=MetadataSet), MetadataSet | None)


def _it_beats_the_ambiguous_form(local: LocalRegistry, text: str) -> None:
    """Side by side: the same URN, with and without the expectation.

    Without `expect=`, an untyped URN answers `object | None` (#399 — it was a
    confident, wrong `LazyCodelist`). With it, the caller's class. Keeping
    both here is what makes the improvement checkable rather than asserted.
    """
    assert_type(local.get(SdmxUrn.parse(text)), object | None)
    assert_type(local.get(SdmxUrn.parse(text), expect=DataStructure), DataStructure | None)


def _the_lazy_roots(local: LocalRegistry, text: str) -> None:
    """`expect=` declares what it is given, including when that is a facade.

    A stored codelist comes back lazy, so the honest expectation is
    `LazyCodelist` or the shared `CodelistLike`; `Codelist` with ``eager=True``
    is the third spelling. Each declares exactly what it returns.
    """
    urn = SdmxUrn.parse(text)
    assert_type(local.get(urn, expect=LazyCodelist), LazyCodelist | None)
    assert_type(local.get(urn, expect=CodelistLike), CodelistLike | None)
    assert_type(local.get(urn, expect=Codelist, eager=True), Codelist | None)


def _the_other_registries(memory: InMemoryRegistry, rest: sl.RestRegistry) -> None:
    """The keyword means the same thing on each registry that takes a URN.

    Not on the `Registry` protocol, deliberately: it documents itself as the
    narrowest read seam and says the implementations need not agree on a
    keyword, so widening it would charge every implementer for a convenience.
    """
    text = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=AG:CL(1.0)"
    assert_type(memory.get(SdmxUrn.parse(text), expect=Codelist), Codelist | None)
    assert_type(rest.get(SdmxUrn.parse(text), expect=Codelist), Codelist | None)
    assert_type(rest.get(SdmxUrn.parse(text), expect=Codelist, resolve=True), Codelist | None)


def _any_ref_is_accepted(local: LocalRegistry, target: Ref[object], typed: Ref[Codelist]) -> None:
    """Both spellings reach the `expect=` overload, which is why its parameter
    is `Ref[object]` and not `Ref[Any]`.

    `Ref[object]` is what the model gives for a categorisation target or a
    metadata target — the refs that genuinely need an expectation. A typed
    `Ref[Codelist]` also has to be accepted, and it is: `Ref` carries an
    explicitly covariant TypeVar for exactly this assignment. An earlier
    version of this parameter was `Ref[Any]` on the belief that `Ref` was
    invariant, which cost eight unnecessary `Any` in `src/`; the second
    argument here is what would have caught it.
    """
    assert_type(local.get(target, expect=DataStructure), DataStructure | None)
    assert_type(local.get(typed, expect=Codelist), Codelist | None)


def _a_typed_urn_still_answers_without_it(local: LocalRegistry) -> None:
    """The discrimination check: `expect=` must not have changed what the
    existing overloads say, or this probe would pass against a `get` that
    answered the same thing for everything."""
    assert_type(local.get(Codelist.urn_for(agency="AG", id="CL")), LazyCodelist | None)
    assert_type(local.get(DataStructure.urn_for(agency="AG", id="DSD")), DataStructure | None)
