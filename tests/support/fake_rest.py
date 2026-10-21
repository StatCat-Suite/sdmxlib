"""A structure service backed by a list of artefacts, for testing the executor.

Not a mock. It answers `/structure/{resource}/{agency}/{id}/{version}` with
the artefacts it holds, filtered by the path segments and expanded by
`references=` — which makes it the thing the executor's closure has to be
right *about*.

**Its `references=` semantics are derived from the SDMX spec's meaning, not
from `sdmxlib.query._closure`.** That separation is the whole value: if the
fake expanded references by asking the closure what it wanted, a query would
always receive exactly what the closure asked for and the differential could
never fail. Here `children` means "the artefacts this one references" and
`descendants` means "transitively", computed by walking `Ref` fields — so a
closure that asks for too little gets too little, and the query answers short,
which is what the test detects.

`Ref` walking is deliberately structural (any attribute that is or contains a
`Ref`) rather than a per-type list of fields: a per-type list would be a
second description of the model, free to drift from the model itself, and the
drift would show up as a test that passes while the library fetches short.
"""

from __future__ import annotations

import typing
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import attrs

from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import Ref, SupportsUrn
from sdmxlib.model.registry import artefact_urn
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.storage._kinds import CLASS_TO_NAME

if TYPE_CHECKING:
    from collections.abc import Sequence


def _field_for_element() -> dict[str, str]:
    """Element class name to the `StructureMessage` field that holds it.

    Read off the field annotations — `ArtefactList[Codelist]` — rather than
    written out, so a message that grows a collection is served by this fake
    without anyone updating a second list. The alternative fails quietly: an
    artefact kind with no home here is dropped, the query answers short, and
    the test that should catch a short fetch is itself fetching short.
    """
    mapping: dict[str, str] = {}
    for field in attrs.fields(StructureMessage):
        if field.name.startswith("_"):
            continue  # not an artefact slot: `_refused` (#435)
        args = typing.get_args(field.type)
        if len(args) == 1 and isinstance(args[0], type):
            mapping[args[0].__name__] = field.name
    return mapping


_FIELD_FOR = _field_for_element()


def _resource_of(artefact: object) -> str:
    """The REST resource name for an artefact — `Dataflow` becomes `dataflow`."""
    return CLASS_TO_NAME.get(type(artefact), type(artefact).__name__).lower()


def _refs_of(artefact: object) -> list[str]:
    """Urns this artefact references, found structurally.

    Walks attrs fields and their contents for anything carrying a urn. A
    per-type list of reference fields would be a second description of the
    model and would drift; this cannot, because it reads the model.
    """
    urns: list[str] = []

    def visit(value: object, depth: int = 0) -> None:
        if depth > 4:
            return
        if isinstance(value, Ref):
            urns.append(str(value.urn))
            return
        if isinstance(value, (str, bytes)):
            return
        # Any iterable, not just list/tuple: a DSD's dimensions live in an
        # `ItemList`, and checking for the concrete containers found nothing
        # at all — every artefact looked reference-free and `children`
        # behaved exactly like `none`.
        if isinstance(value, Iterable):
            for item in value:
                visit(item, depth + 1)
            return
        cls = type(value)
        if attrs.has(cls):
            for field in attrs.fields(cls):
                visit(getattr(value, field.name, None), depth + 1)

    artefact_cls = type(artefact)
    for field in attrs.fields(artefact_cls) if attrs.has(artefact_cls) else ():
        visit(getattr(artefact, field.name, None))
    return urns


def _owner_urn(urn: str) -> str:
    """The artefact a urn belongs to — itself, or the scheme holding the item.

    A component references a *concept item*
    (`…conceptscheme.Concept=ESTAT:CS(1.0).REF_AREA`), and no service returns
    a bare item: it returns the ConceptScheme that contains it. Without this
    the fake serves `references=children` and still omits the scheme, so the
    executor looks short-fetched when it is the fake that is wrong — the
    failure this cost an hour to tell apart the first time.
    """
    try:
        parsed = SdmxUrn.parse(urn)
    except ValueError, TypeError:
        return urn
    if parsed.item_id is None:
        return urn
    package = parsed.package
    owner_class = {"conceptscheme": "ConceptScheme", "codelist": "Codelist", "categoryscheme": "CategoryScheme"}.get(
        package, parsed.artefact_class
    )
    return str(attrs.evolve(parsed, item_id=None, artefact_class=owner_class))


@attrs.define
class FakeRestService:
    """Answers structure requests from a fixed set of artefacts.

    Attributes:
        artefacts: Everything the service knows about.
        calls: Every request answered, as `(resource, agency, id, version,
            references)` — so a test can assert what was asked as well as what
            came back.
    """

    artefacts: Sequence[SupportsUrn]
    calls: list[tuple[str, str | None, str | None, str | None, str]] = attrs.field(factory=list)

    def _by_urn(self) -> dict[str, SupportsUrn]:
        return {str(artefact_urn(artefact)): artefact for artefact in self.artefacts}

    def _expand(self, seeds: Iterable[SupportsUrn], references: str) -> list[SupportsUrn]:
        """Apply `references=` the way the spec describes it.

        `none` returns the matched artefacts. `children` adds what they
        reference. `descendants` repeats until nothing new appears — which is
        also `all` minus the parents, and this service has no notion of
        parents, so `all` is not offered rather than being approximated.
        """
        by_urn = self._by_urn()
        found = {str(artefact_urn(a)): a for a in seeds}
        if references == "none":
            return list(found.values())
        rounds = 1 if references == "children" else len(by_urn)
        for _ in range(rounds):
            grew = False
            for artefact in list(found.values()):
                for urn in _refs_of(artefact):
                    owner = _owner_urn(urn)
                    if owner in by_urn and owner not in found:
                        found[owner] = by_urn[owner]
                        grew = True
            if not grew:
                break
        return list(found.values())

    def _message(self, artefacts: Iterable[object]) -> StructureMessage:
        grouped: dict[str, list[object]] = {}
        for artefact in artefacts:
            field = _FIELD_FOR.get(type(artefact).__name__)
            if field is None:
                # A kind this message cannot carry. Dropping it silently is
                # what a real service would never do, so say so loudly rather
                # than letting the executor look short-fetched.
                msg = f"{type(artefact).__name__} has no StructureMessage field; the fake cannot serve it"
                raise TypeError(msg)
            grouped.setdefault(field, []).append(artefact)
        # One `Any` local, on the line the dispatch happens — the pattern
        # `StructureMessage.containing` uses. The slot each bucket fills is
        # read off the annotations at runtime, so no static type describes
        # this splat: every keyword is checked against its own element type
        # and `list[object]` satisfies none of them.
        by_field: dict[str, Any] = grouped
        return StructureMessage(**by_field)

    def _matching(self, resource: str, agency: str | None, id: str | None) -> list[SupportsUrn]:
        found: list[SupportsUrn] = []
        for artefact in self.artefacts:
            if _resource_of(artefact) != resource:
                continue
            if agency is not None and getattr(artefact, "agency_id", None) != agency:
                continue
            if id is not None and getattr(artefact, "id", None) != id:
                continue
            found.append(artefact)
        return found

    def close(self) -> None:
        """No-op. A `RestRegistry` closes its client on the way out, and a
        service that cannot be closed would make the fake unusable there."""

    def get_artefact(
        self,
        resource: str,
        agency: str,
        id: str,
        version: str = "latest",
        *,
        detail: str = "full",
        references: str = "none",
    ) -> StructureMessage:
        del detail
        self.calls.append((resource, agency, id, version, references))
        return self._message(self._expand(self._matching(resource, agency, id), references))

    def get_all(
        self,
        resource: str,
        agency: str | None = None,
        *,
        detail: str = "full",
        references: str = "none",
    ) -> StructureMessage:
        del detail
        self.calls.append((resource, agency, None, None, references))
        return self._message(self._expand(self._matching(resource, agency, None), references))
