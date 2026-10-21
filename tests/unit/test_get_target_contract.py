"""The four `get`s take the same two things, and refuse the same third (#370).

Before this, three entry points declared three different contracts and none of
them described what actually happened:

| entry point              | declared               | `SdmxUrn` | `str`            | `Ref`      |
| ------------------------ | ---------------------- | --------- | ---------------- | ---------- |
| `InMemoryRegistry.get`   | `SdmxUrn[T]`           | yes       | yes, silently    | **`None`** |
| `LocalRegistry.get`      | `SdmxUrn[T]`           | yes       | yes, silently    | **`None`** |
| `RestRegistry.get`       | `SdmxUrn[T] \\| Ref[T]` | yes      | `AttributeError` | yes        |
| `FederatedRegistry.get`  | `SdmxUrn[T]`           | yes       | `AttributeError` | yes        |

Both local registries end their lookup in ``self._store.get(str(urn))``. That
is why a `str` worked there and nowhere else — ``str`` of a ``str`` is itself —
and it is the same accident that made a `Ref` fail: it stringifies to
``Ref(CL [unresolved])``, misses, and returns ``None``. So traversal code that
worked against a `RestRegistry` found nothing against a `LocalRegistry`, with
no error to say so. That is the defect these tests are mostly about; the `str`
question is the one the issue was filed under.

The inventory test is the durable half. Behaviour tests catch the four `get`s
that exist; `test_every_get_declares_the_same_target_contract` catches the
fifth one somebody writes later.
"""

from __future__ import annotations

import annotationlib
import inspect
import typing
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest

import sdmxlib as sl
from sdmxlib.api.federated import FederatedRegistry
from sdmxlib.api.registry import RestRegistry
from sdmxlib.local import LocalRegistry
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.in_memory_registry import InMemoryRegistry
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.ref import Ref, artefact_urn, target_urn
from sdmxlib.model.registry import RegistryReader, SupportsGet

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

#: Every `get` in the library. The inventory test reads its shape off these,
#: so a new registry is added here rather than to each behavioural test.
GETS = (
    ("InMemoryRegistry", InMemoryRegistry.get),
    ("LocalRegistry", LocalRegistry.get),
    ("RestRegistry", RestRegistry.get),
    ("FederatedRegistry", FederatedRegistry.get),
    ("SupportsGet", SupportsGet.get),
)

#: What all five must say. Written out rather than derived from one of them,
#: so agreeing on the *wrong* thing still fails.
TARGET_ANNOTATION = "SdmxUrn[T] | Ref[T]"

#: What an implementation may say *instead*, once its class carries overloads.
#:
#: `expect=` (#406) binds `T` to its own argument rather than to the URN's
#: phantom, so the implementation behind the overloads accepts a target whose
#: phantom is anything: both halves are `object`, because the URN is a pure
#: consumer there and `Ref` carries an explicitly covariant TypeVar, so
#: `Ref[object]` accepts every typed `Ref` a caller holds.
#:
#: It said `Ref[Any]` until #406's correction, on the belief that `Ref` was
#: invariant — eight unnecessary `Any` in `src/`, and this constant is where
#: the wrong spelling was written down as though it were a rule.
#:
#: This is a widening of the *implementation* signature only. The contract
#: #370 is about is what callers see, and that is the overloads — which is why
#: the test below checks them rather than accepting this string and stopping.
IMPL_TARGET_ANNOTATION = "SdmxUrn[object] | Ref[object]"

#: The URN string every refusal test hands over. A real one, so the refusal is
#: about the *type* rather than about the text being unparseable.
A_URN_STRING = "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=SDMX:CL_FREQ(1.0)"


def _codelist() -> Codelist:
    return Codelist(
        id="CL_FREQ",
        maintainer="SDMX",
        version="1.0",
        name=InternationalString(en="Frequency"),
        codes=[Code(id="A", name=InternationalString(en="Annual"))],
    )


def _target(func: Callable[..., object]) -> inspect.Parameter:
    """The target parameter of a `get`, skipping `self`."""
    params = list(inspect.signature(func, annotation_format=annotationlib.Format.STRING).parameters.values())
    return params[1] if params[0].name == "self" else params[0]


# ── The inventory ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(("name", "func"), GETS, ids=[n for n, _ in GETS])
def test_every_get_declares_the_same_target_contract(name: str, func: Callable[..., object]) -> None:
    """One annotation, positional-only, across every `get` in the library.

    Positional-only is load-bearing rather than tidiness: it is what makes the
    four interchangeable without also agreeing on a keyword. They did not agree
    on one before — three said ``urn`` and `RestRegistry` said ``target``.
    """
    declared = _target(func)
    overloads = typing.get_overloads(func)

    assert str(declared.annotation).strip("'\"") in {TARGET_ANNOTATION, IMPL_TARGET_ANNOTATION}, (
        f"{name}.get declares {declared.annotation!r} for its target"
    )
    assert declared.kind is inspect.Parameter.POSITIONAL_ONLY, (
        f"{name}.get takes its target as {declared.kind.description}, so a keyword call is not portable across the four"
    )

    if str(declared.annotation).strip("'\"") == IMPL_TARGET_ANNOTATION:
        # The widened form is only allowed behind overloads, and one of them
        # must still be the plain contract — otherwise a `get` could widen its
        # target and take the exemption without offering callers the narrow
        # form at all.
        assert overloads, f"{name}.get widened its target annotation without declaring overloads"
        narrow = [o for o in overloads if str(_target(o).annotation).strip("'\"") == TARGET_ANNOTATION]
        assert narrow, (
            f"{name}.get has no overload declaring {TARGET_ANNOTATION!r} — the contract #370 is about is the "
            f"one callers see, and none of its {len(overloads)} overloads offers it"
        )

    for overload_fn in overloads:
        assert _target(overload_fn).kind is inspect.Parameter.POSITIONAL_ONLY, (
            f"{name}.get has an overload taking its target by keyword"
        )


# ── A Ref is not a miss ───────────────────────────────────────────────────


@pytest.fixture
def in_memory() -> InMemoryRegistry:
    reg = InMemoryRegistry()
    reg.add(_codelist())
    return reg


@pytest.fixture
def local() -> Iterator[LocalRegistry]:
    with LocalRegistry(":memory:") as reg:
        reg.add(_codelist())
        yield reg


def test_a_ref_finds_what_its_urn_finds_in_memory(in_memory: InMemoryRegistry) -> None:
    """The regression. This returned `None` — no error, no warning."""
    urn = artefact_urn(_codelist())
    ref = Ref[object].unresolved(urn)
    assert in_memory.get(ref) is in_memory.get(urn)
    assert in_memory.get(ref) is not None


def test_a_ref_finds_what_its_urn_finds_locally(local: LocalRegistry) -> None:
    urn = _codelist().urn
    ref = Ref[sl.Codelist].unresolved(urn)
    by_ref = local.get(ref)
    assert by_ref is not None, "a Ref used to miss and answer None here"
    assert by_ref.id == "CL_FREQ"


def test_a_ref_answers_contains(in_memory: InMemoryRegistry) -> None:
    """``ref in reg`` said `False` for a *present* artefact, for the same reason."""
    ref = Ref[object].unresolved(artefact_urn(_codelist()))
    assert ref in in_memory


def test_a_ref_answers_contains_locally(local: LocalRegistry) -> None:
    ref = Ref[object].unresolved(artefact_urn(_codelist()))
    assert ref in local


def test_contains_still_answers_no_for_an_unrelated_object(in_memory: InMemoryRegistry) -> None:
    """``in`` is a question, so the answer for a bare object is "no", not an error."""
    assert object() not in in_memory
    assert A_URN_STRING not in in_memory


def test_contains_still_answers_no_for_an_unrelated_object_locally(local: LocalRegistry) -> None:
    assert object() not in local
    assert A_URN_STRING not in local


# ── A str is refused, with the fix in the message ─────────────────────────


def test_a_str_is_refused_in_memory(in_memory: InMemoryRegistry) -> None:
    with pytest.raises(TypeError, match=r"takes an SdmxUrn or a Ref, not str"):
        in_memory.get(A_URN_STRING)  # pyrefly: ignore[bad-argument-type]


def test_a_str_is_refused_locally(local: LocalRegistry) -> None:
    with pytest.raises(TypeError, match=r"takes an SdmxUrn or a Ref, not str"):
        local.get(A_URN_STRING)  # pyrefly: ignore[no-matching-overload]


def test_a_str_is_refused_over_rest() -> None:
    """No network reached: the refusal happens before the lookup."""
    with (
        sl.RestRegistry("https://example.invalid/", id="TEST") as reg,
        pytest.raises(TypeError, match=r"takes an SdmxUrn or a Ref, not str"),
    ):
        reg.get(A_URN_STRING)  # pyrefly: ignore[bad-argument-type]


def test_a_str_is_refused_by_a_federation(in_memory: InMemoryRegistry) -> None:
    """Refused before routing, which is why an empty routing table still reaches it."""
    federated = FederatedRegistry(routing={}, default=in_memory)
    with pytest.raises(TypeError, match=r"takes an SdmxUrn or a Ref, not str"):
        federated.get(A_URN_STRING)  # pyrefly: ignore[bad-argument-type]


def test_the_refusal_names_both_ways_to_build_one(local: LocalRegistry) -> None:
    """A message that only says "no" costs the reader a trip to the source."""
    with pytest.raises(TypeError) as excinfo:
        local.get("nonsense")  # pyrefly: ignore[no-matching-overload]
    message = str(excinfo.value)
    assert "SdmxUrn.parse" in message
    assert "urn_for" in message


def test_every_contains_takes_its_target_positionally() -> None:
    """The inventory's other half.

    Without the positional-only marker, satisfying `RegistryReader` requires
    agreeing on the parameter *name* too — a constraint no caller of ``in``
    can benefit from, and one the three implementations failed (`RestRegistry`
    said ``urn``). basedpyright caught that as a Protocol violation the moment
    the Protocol was touched, which is the only reason it is guarded here.
    """
    contains = (
        ("InMemoryRegistry", InMemoryRegistry.__contains__),
        ("LocalRegistry", LocalRegistry.__contains__),
        ("RestRegistry", RestRegistry.__contains__),
        ("RegistryReader", RegistryReader.__contains__),
    )
    for name, func in contains:
        params = list(inspect.signature(func, annotation_format=annotationlib.Format.STRING).parameters.values())
        target = params[1] if params[0].name == "self" else params[0]
        assert target.kind is inspect.Parameter.POSITIONAL_ONLY, (
            f"{name}.__contains__ takes its target as {target.kind.description}"
        )


# ── target_urn's own narrowing ────────────────────────────────────────────


def test_target_urn_refuses_the_artefact_itself() -> None:
    """Narrower than `urn_of` on purpose — asking for what you hold is not a lookup.

    `urn_of` accepts an artefact because `LocalRegistry.data(flow)` legitimately
    takes one. `get` does not, and the two helpers exist so that difference is
    stated rather than left to whichever one a call site happened to reach.
    """
    with pytest.raises(TypeError, match=r"takes an SdmxUrn or a Ref, not Codelist"):
        target_urn(_codelist())  # pyrefly: ignore[bad-argument-type]


def test_target_urn_passes_a_urn_through_unchanged() -> None:
    """`urn_of` erases `T` to `object`; `get` returns ``T | None``, so this cannot."""
    urn = sl.Codelist.urn_for(agency="SDMX", id="CL_FREQ", version="1.0")
    assert target_urn(urn) is urn
    assert target_urn(Ref[sl.Codelist].unresolved(urn)) == urn
