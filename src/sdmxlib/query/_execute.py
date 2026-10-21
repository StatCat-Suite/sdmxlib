"""Running a program against a REST service: fetch, materialise, then ask locally.

§13. The backend computes nothing, so this issues the requests a program names,
writes what comes back into a store, and hands the *original* query to the
compiler that already exists. There is no local predicate evaluator here, no
sort, no projection — the point of the design is that none is needed.

**What is load-bearing is not the fetching but the closure.** A short fetch
does not fail: the query runs against a store missing one relation and returns
nothing, with no error anywhere. So the only thing this module really has to
get right is asking `_rest` for the requests and issuing all of them.
"""

from collections.abc import Callable
from typing import Protocol

from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import SupportsUrn, artefact_urn
from sdmxlib.query._expr import RowKind
from sdmxlib.query._plan import Plan
from sdmxlib.query._pushdown import SDMX_REST, Capability, plan_program
from sdmxlib.query._rest import ANY, Request, requests_for


class StructureSource(Protocol):
    """The two calls a structure service answers.

    A Protocol rather than `SdmxClient` so a test can supply a service without
    HTTP, and so this module depends on the shape of the endpoint rather than
    on the client that happens to speak to it.
    """

    def get_artefact(
        self,
        resource: str,
        agency: str,
        id: str,
        version: str = "latest",
        *,
        detail: str = "full",
        references: str = "none",
    ) -> StructureMessage: ...

    def get_all(
        self,
        resource: str,
        agency: str | None = None,
        *,
        detail: str = "full",
        references: str = "none",
    ) -> StructureMessage: ...


class Store(Protocol):
    """Somewhere to put what comes back. `Registry` satisfies this."""

    def add(self, artefact: SupportsUrn, *, source_registry: str | None = None) -> None: ...


def fetch(source: StructureSource, request: Request) -> StructureMessage:
    """Issue one request.

    The id segment decides which call: SDMX REST spells "every id" as a
    wildcard in the path, and the client models that as a separate collection
    endpoint rather than an `all` literal.
    """
    if request.resource_id == ANY:
        return source.get_all(
            request.resource,
            None if request.agency == ANY else request.agency,
            detail=request.detail,
            references=request.references,
        )
    return source.get_artefact(
        request.resource,
        request.agency,
        request.resource_id,
        request.version,
        detail=request.detail,
        references=request.references,
    )


def artefacts_in(message: StructureMessage) -> list[object]:
    """Everything a structure message carries, in no particular order.

    Reads the collections off `attrs` rather than naming them, so a message
    type that grows a collection does not silently stop being materialised —
    which would present as a query answering short, the failure mode with no
    symptom.
    """
    import attrs  # noqa: PLC0415  (import cost is paid once; the alternative is a module cycle)

    found: list[object] = []
    for field in attrs.fields(type(message)):
        # `_`-prefixed fields are not artefact slots — `StructureMessage._refused`
        # holds `Refusal` records, which are nothing to materialise (#435).
        if field.name.startswith("_"):
            continue
        collection = getattr(message, field.name, None)
        if collection is None:
            continue
        try:
            found.extend(list(collection))
        except TypeError:
            continue
    return found


def fetch_into[R: RowKind](
    store: Store,
    plan: Plan[R],
    source: StructureSource,
    capability: Capability = SDMX_REST,
    *,
    projection: frozenset[tuple[str, str]] | None = None,
    record: Callable[[str], None] | None = None,
) -> tuple[Request, ...]:
    """Fetch everything `plan` needs and write it into `store`.

    Returns the requests issued, which is what a caller inspects and what a
    test asserts on — the count is the round-trip cost and is a property of
    the plan rather than of the data.

    Artefacts that the store cannot hold are skipped rather than raising: a
    real service returns kinds this schema does not model, and refusing the
    whole fetch because one arrived would make the backend hostage to the
    registry's contents.

    `record`, when given, is called with the URN of every artefact this fetch
    put in the store — which is what lets a query answer from its own fetch
    rather than from everything the store has accumulated (§5.13). A callback
    rather than a widened `Store`, because "where a fetch's results are
    remembered" is the caller's business and only one caller has any.

    `projection` is the `(table, column)` pairs the caller will read, and is
    what lets a request ask for `detail=allstubs` (§5.14). `None` means the
    caller wants *artefacts*, so the fetch must be `full`: a stub carries no
    items, and resolving against one yields a hollow artefact and no error.

    Raises:
        PushdownError: If the plan cannot be served within the bound — raised
            by the planner before anything is fetched.
    """
    requests = requests_for(plan_program(plan, capability), projection)
    for request in requests:
        for artefact in artefacts_in(fetch(source, request)):
            # `SupportsUrn` narrows rather than a `try`/`except TypeError`
            # around `add`: the exception would also swallow a *store* error,
            # and a store that quietly refused every artefact would present as
            # a query answering nothing — this module's whole failure mode.
            if not isinstance(artefact, SupportsUrn):
                continue
            try:
                store.add(artefact)
            except ValueError:
                # Structurally invalid for this schema. A real service returns
                # artefacts this model validates more strictly than the wire
                # does, and one of them must not lose the whole fetch.
                continue
            if record is not None:
                # After the add, not before: an artefact the store refused is
                # not in this fetch's answer, and recording it would scope a
                # query to a row that is not there.
                record(str(artefact_urn(artefact)))
    return requests
