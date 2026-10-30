"""Building an edition from a registry and the observations a producer holds (GH-223).

`build_edition` is the one place a dataflow's structural closure is selected and
its codelists cut to partials. `fetch_edition` is a call to it; so is any
producer holding structures in a registry and observations in a lake.

**The closure is the fingerprint's.** It is what `References.closure` selects
from the dataflow and its data structure: what they refer to, every artefact
other than a dataflow referring to one of them, and what those refer to.
Another dataflow of the same data structure is left out with what only it
reaches. A `RestRegistry` is asked for the dataflow with
``references=all`` and for its data structure with ``references=parents``, so
the constraints and hierarchy associations that name either arrive.

**What a partial keeps.** A codelist is cut to the codes the observations hold
in the columns it enumerates, the codes the dataflow's allowed constraints
name, and the codes another kept artefact references, each with its ancestors
(`Codelist.partial`). It stays full when a hierarchy places its codes: SDMX has
no partial hierarchy, so the hierarchy stays whole and its codes with it. It
stays full too when it has extensions, when a component it enumerates has no
column in written observations, and when the cut would keep every code.
"""

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import msgspec

from sdmxlib.api.registry import RestRegistry
from sdmxlib.builders.contentconstraint import _distinct_values
from sdmxlib.data_store import WriteSource
from sdmxlib.edition._fingerprint import References, artefact_fingerprint
from sdmxlib.edition._write import Lifecycle, seal
from sdmxlib.local.registry import EditionRegistry, LocalRegistry
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.codelist import CascadeSelection, Codelist
from sdmxlib.model.constraint import ComponentValueSet, ConstraintType, DataConstraint, KeyValue
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.ref import Ref
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.storage._columns import table_columns
from sdmxlib.storage._manifest import Manifest, Producer, Provenance

#: The lake schema a dataflow's observations are written to.
SCHEMA = "obs"


def build_edition(
    source: RestRegistry | EditionRegistry,
    dataflows: Sequence[SdmxUrn[Dataflow]],
    path: str | Path,
    *,
    product_id: str,
    producer: Producer,
    observations: Mapping[str, WriteSource] | None = None,
    partials: bool = True,
    provenance: Provenance | None = None,
    lifecycle: Lifecycle = "revisable",
) -> Manifest:
    """Build a new edition at `path` from each dataflow's closure at `source` and its observations.

    The product is `path`, which must not exist or be empty, and this is its
    first edition, ``<path>/<edition_id>/``. ``metadata.duckdb`` holds the union of
    the dataflows' structural closures and a `DataflowBinding` for each
    dataflow `observations` gives rows for; ``tables.ducklake`` holds those rows,
    one table per dataflow in schema ``obs``. The manifest is written last.
    Run `check_edition` on the edition to verify it, with ``structures=``
    to check each partial against its full artefact.

    Args:
        source: Where the structures are: a `RestRegistry`, asked for each
            dataflow's closure, or a sealed edition's `EditionRegistry`.
        dataflows: The dataflows, by URN. A `latest` URN is built at the
            version it resolves to.
        path: The product directory to create.
        product_id: The producer's stable name for the product.
        producer: What built it.
        observations: The rows of each dataflow that has any, by URN string,
            as `LocalRegistry.write_observations` reads them: a Polars frame,
            an Arrow reader, a DuckDB relation over a DuckLake table. A
            dataflow with none is bound to nothing.
        partials: Whether to cut codelists to partials, recording each one's
            `partial_of`.
        provenance: Where the structures came from. Its ``artefacts`` is set
            to every artefact the edition holds.
        lifecycle: ``final`` or ``live``, as `seal` takes it.

    Returns:
        The manifest written; the edition is ``<path>/<manifest.edition_id>/``.

    Raises:
        FileExistsError: If `path` exists and is not empty.
        TypeError: If a URN names no dataflow at `source`.
        ValueError: If a dataflow has no data structure at `source`, if
            `observations` names a dataflow not in `dataflows`, or if a coded
            column holds a value outside its codelist, naming the dataflow,
            the component and the values.
    """
    rows = dict(observations or {})
    root = Path(path)
    if root.exists() and any(root.iterdir()):
        msg = f"{root} exists and is not empty; build_edition creates a new edition"
        raise FileExistsError(msg)
    root.mkdir(parents=True, exist_ok=True)
    with LocalRegistry.build(root) as builder:
        flows = _structures(source, dataflows, builder)
        # Rows are found by the URN given or the one it resolved to.
        keys = [{str(given), str(flow.urn)} for given, (flow, _) in zip(dataflows, flows, strict=True)]
        unknown = sorted(set(rows) - set().union(*keys))
        if unknown:
            msg = f"observations name {', '.join(unknown)}, which is not among the dataflows built"
            raise ValueError(msg)
        found = [next((rows[key] for key in sorted(names) if key in rows), None) for names in keys]
        bound = _write(builder, flows, found)
        cut = _cut(builder, flows, bound) if partials else {}
        stored = builder.connection.execute(
            "SELECT urn FROM sdmx.artefact WHERE artefact_type <> 'DataflowBinding' ORDER BY urn"
        ).fetchall()
        edition_dir = builder.edition_dir
    recorded = (
        None if provenance is None else msgspec.structs.replace(provenance, artefacts=tuple(u for (u,) in stored))
    )
    return seal(
        edition_dir, product_id=product_id, producer=producer, provenance=recorded, lifecycle=lifecycle, partial_of=cut
    )


def table_for(urn: SdmxUrn[Dataflow]) -> str:
    """The lake table a dataflow's observations go in: its URN's parts, as an identifier."""
    parts = f"{urn.agency}_{urn.id}_{urn.version}"
    return "".join(c if c.isalnum() else "_" for c in parts).lower()


def _structures(
    source: RestRegistry | EditionRegistry, dataflows: Sequence[SdmxUrn[Dataflow]], edition: LocalRegistry
) -> list[tuple[Dataflow, DataStructure]]:
    """Each dataflow and its data structure, with the union of their closures added to `bundle`."""
    flows: list[tuple[Dataflow, DataStructure]] = []
    for urn in dataflows:
        if isinstance(source, RestRegistry):
            flow = source.get(urn, expect=Dataflow, resolve="all")
            pointed = None if flow is None else flow.structure
            structure = None if pointed is None else source.get(pointed.urn, expect=DataStructure, resolve="parents")
        else:
            flow = source.get(urn, expect=Dataflow)
            structure = None if flow is None else source.dsd(flow.urn)
        if flow is None:
            msg = f"{urn} is not a dataflow at {source}"
            raise TypeError(msg)
        if structure is None:
            msg = f"{flow.urn} has no data structure at {source}"
            raise ValueError(msg)
        flows.append((flow, structure))
    if isinstance(source, RestRegistry):
        # Everything fetched goes in, and what the closure does not reach comes out.
        edition.add_all(list(source.registry))
        for urn in _held(edition) - _closure(edition, flows):
            edition.drop(SdmxUrn.parse(urn))
    else:
        kept = sorted(_closure(source, flows))
        copied = (source.get(SdmxUrn.parse(urn), expect=MaintainableArtefact, eager=True) for urn in kept)
        edition.add_all([artefact for artefact in copied if artefact is not None])
    return flows


def _held(registry: EditionRegistry) -> set[str]:
    rows = registry.connection.execute(
        "SELECT urn FROM sdmx.artefact WHERE artefact_type <> 'DataflowBinding'"
    ).fetchall()
    return {urn for (urn,) in rows}


def _closure(registry: EditionRegistry, flows: Iterable[tuple[Dataflow, DataStructure]]) -> set[str]:
    """The union of each dataflow's structural closure in `registry`'s store."""
    references = References(registry.connection, _held(registry))
    return {urn for flow, dsd in flows for urn in references.closure(str(flow.urn), [str(dsd.urn)])}


def _write(
    edition: LocalRegistry, flows: list[tuple[Dataflow, DataStructure]], rows: list[WriteSource | None]
) -> list[tuple[DataflowBinding, DataStructure]]:
    """Write each dataflow's rows to its own table and bind it; the bindings, located, with their structures."""
    bound: list[tuple[DataflowBinding, DataStructure]] = []
    for (flow, dsd), found in zip(flows, rows, strict=True):
        if found is None:
            continue
        table = table_for(flow.urn)
        edition.write_observations(schema=SCHEMA, table=table, source=found)
        binding = DataflowBinding.from_dsd(dsd, dataflow=Ref[Dataflow].unresolved(flow.urn), schema=SCHEMA, table=table)
        edition.add(binding)
        bound.append((edition.locate(binding), dsd))
    return bound


def _cut(
    edition: LocalRegistry,
    flows: list[tuple[Dataflow, DataStructure]],
    bound: list[tuple[DataflowBinding, DataStructure]],
) -> dict[str, str]:
    """Replace each codelist that can be cut by its partial; the full fingerprint of each, by URN."""
    used: dict[str, set[str]] = {}
    full: set[str] = set()
    observed = {str(binding.dataflow.urn) for binding, _ in bound}
    for flow, dsd in flows:
        if str(flow.urn) not in observed:
            full |= set(_components(dsd).values())
    for binding, dsd in bound:
        _observed_codes(edition, binding, dsd, used, full)
    placed, referenced = _code_references(edition)
    full |= placed
    for urn, codes in _constraint_codes(edition, flows).items():
        used.setdefault(urn, set()).update(codes)
    cut: dict[str, str] = {}
    for urn in sorted(set(used) - full):
        codelist = edition.get(Codelist.parse_urn(urn), eager=True)
        if not isinstance(codelist, Codelist) or codelist.is_partial or codelist.extensions:
            continue
        ids = {code.id for code in codelist.codes}
        keep = (used[urn] | referenced.get(urn, set())) & ids
        partial = codelist.partial(keep)
        if len(partial.codes) == len(codelist.codes):
            continue
        cut[urn] = artefact_fingerprint(codelist)
        edition.replace(codelist.urn, partial)
    return cut


def _components(dsd: DataStructure) -> dict[str, str]:
    """The URN of the codelist each component of `dsd` is enumerated by, by component id."""
    found: dict[str, str] = {}
    for component in (*dsd.dimensions, *dsd.attributes, *dsd.measures):
        representation = component.representation
        if isinstance(representation, Ref) and representation.urn.artefact_class == "Codelist":
            found[component.id] = str(representation.urn)
    return found


def _observed_codes(
    edition: LocalRegistry, binding: DataflowBinding, dsd: DataStructure, used: dict[str, set[str]], full: set[str]
) -> None:
    """Add the codes each enumerated column of `binding` holds to `used`; a component without a column, to `full`.

    Raises:
        ValueError: If a column holds a value that is not a code of its codelist.
    """
    table = f"{binding.database_role}.{binding.table_location}"
    columns = table_columns(edition.connection, table)
    mapped = {**binding.dimension_column_map, **binding.attribute_column_map, **binding.measure_column_map}
    for component_id, urn in _components(dsd).items():
        column = mapped.get(component_id)
        codelist = edition.get(Codelist.parse_urn(urn), eager=True)
        if column is None or column not in columns or not isinstance(codelist, Codelist):
            full.add(urn)
            continue
        held = set(_distinct_values(edition.connection, table, column))
        outside = sorted(held - {code.id for code in codelist.codes})
        if outside:
            msg = f"{binding.dataflow.urn}: {component_id} holds {', '.join(outside)}, which are not codes of {urn}"
            raise ValueError(msg)
        used.setdefault(urn, set()).update(held)


def _code_references(edition: LocalRegistry) -> tuple[set[str], dict[str, set[str]]]:
    """The codelists a hierarchy places codes of, and the codes another artefact references, by codelist."""
    rows = edition.connection.execute(
        """
        SELECT a.artefact_type, r.source_urn, r.target_urn
        FROM sdmx.artefact_ref AS r JOIN sdmx.artefact AS a ON a.urn = r.source_urn
        WHERE r.target_urn LIKE 'urn:sdmx:org.sdmx.infomodel.codelist.Code=%'
        """
    ).fetchall()
    placed: set[str] = set()
    referenced: dict[str, set[str]] = {}
    for kind, source, target in rows:
        code = SdmxUrn.parse(target)
        codelist = str(Codelist.urn_for(agency=code.agency, id=code.id, version=code.version))
        if source == codelist or code.item_id is None:
            continue
        if kind == "Hierarchy":
            placed.add(codelist)
        else:
            referenced.setdefault(codelist, set()).add(code.item_id)
    return placed, referenced


def _constraint_codes(edition: LocalRegistry, flows: list[tuple[Dataflow, DataStructure]]) -> dict[str, set[str]]:
    """The codes the dataflows' allowed constraints name, by codelist; a cascade adds the descendants."""
    by_target = {str(urn): dsd for flow, dsd in flows for urn in (flow.urn, dsd.urn)}
    codes: dict[str, set[str]] = {}
    for constraint in edition.get_all(DataConstraint):
        if constraint.constraint_type is not ConstraintType.ALLOWED:
            continue
        attached = [*constraint.attachment.dataflows, *constraint.attachment.data_structures]
        targets = [by_target[str(ref.urn)] for ref in attached if str(ref.urn) in by_target]
        selections = [s for region in constraint.regions if region.include for s in (*region.keys, *region.components)]
        for dsd in targets:
            components = _components(dsd)
            for selection in selections:
                urn = components.get(_component_id(selection))
                codelist = None if urn is None else edition.get(Codelist.parse_urn(urn), eager=True)
                if urn is None or not selection.include or not isinstance(codelist, Codelist):
                    continue
                named = codes.setdefault(urn, set())
                for value in selection.values:
                    named |= _cascaded(codelist, value.value, value.cascade)
    return codes


def _component_id(selection: KeyValue | ComponentValueSet) -> str:
    return selection.dimension_id if isinstance(selection, KeyValue) else selection.component_id


def _cascaded(codelist: Codelist, code: str, cascade: CascadeSelection) -> set[str]:
    """`code`, its descendants, or both, as `cascade` selects them in `codelist`."""
    if cascade is CascadeSelection.SELF_ONLY:
        return {code}
    children: dict[str, list[str]] = {}
    for item in codelist.codes:
        if item.parent_id is not None:
            children.setdefault(item.parent_id, []).append(item.id)
    found: set[str] = set()
    pending = list(children.get(code, ()))
    while pending:
        current = pending.pop()
        if current not in found:
            found.add(current)
            pending.extend(children.get(current, ()))
    return found if cascade is CascadeSelection.EXCLUDE_ROOT else found | {code}
