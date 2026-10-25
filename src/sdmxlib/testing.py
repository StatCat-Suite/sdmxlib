"""Synthetic bundles: conforming, published, any shape, from a seed.

Real bundles of interesting size are built from data that cannot be published
as fixtures. A synthetic one can: `synthetic_bundle` builds a bundle from a
`Shape` — how many codes each dimension has and how deep and wide its
hierarchy is, which `Hierarchy` artefacts sit over its codelist, how many
observations, in which languages — and publishes it
with `write_manifest`, so it passes `check_bundle`. `profile_bundle` reads a
`Shape` off a real bundle (counts and fan-outs, never values), so a stress
bundle can mimic one that cannot leave the building.

**Deterministic from a seed.** The same seed and shape give the same artefacts
and the same observation rows. Not the same files: a publication's token is
new each time and DuckDB's bytes vary between builds, as for any
rebuild.

Observations are generated in DuckDB, not Python, so millions are cheap: row
``i`` takes key ``(a·i + b) mod |key space|`` for a seeded ``a`` coprime to the
key space, a bijection, so no two rows share a key.
"""

import hashlib
import math
from pathlib import Path

import duckdb
from attrs import field, frozen

from sdmxlib.bundle import Manifest, Producer, write_manifest
from sdmxlib.local.registry import LocalRegistry
from sdmxlib.model.annotations import Annotation
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import AttachmentLevel, Attribute, DataStructure, Dimension, Measure, TimeDimension
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, HierarchyAssociation
from sdmxlib.model.ref import Ref, SupportsUrn
from sdmxlib.model.representation import Facet
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.session import completed
from sdmxlib.storage._synthetic import distinct_periods, observations_from, observations_sql

__all__ = ["Dim", "HierarchyShape", "Shape", "profile_bundle", "synthetic_bundle"]

_AGENCY = "SYNTHETIC"
_SCHEMA = "obs"
_TABLE = "synthetic"
#: The first `TIME_PERIOD`; periods are consecutive years from it.
_FIRST_YEAR = 2000
#: Each attribute's codelist size.
_ATTRIBUTE_CODES = 3
#: The largest `HierarchyShape.multi_parent` a profile reports: the field is
#: below 1, and a real hierarchy may place codes more than twice.
_MOST_MULTI_PARENT = 0.99


class _Draws:
    """Deterministic draws from a seed: blake2b over a counter.

    Not `random.Random`, which ruff's S311 refuses wholesale; nothing here is
    secret, but a hash-based stream is as reproducible and needs no exception.
    """

    def __init__(self, seed: int, stream: str = "") -> None:
        # A named stream draws independently of the unnamed one, so adding
        # hierarchies to a shape leaves its codelists and observations as they were.
        self._prefix = f"{seed}:{stream}:" if stream else f"{seed}:"
        self._count = 0

    def _next(self) -> int:
        self._count += 1
        digest = hashlib.blake2b(f"{self._prefix}{self._count}".encode(), digest_size=8).digest()
        return int.from_bytes(digest)

    def below(self, n: int) -> int:
        """A draw in ``[0, n)``."""
        return self._next() % n

    def between(self, low: int, high: int) -> int:
        """A draw in ``[low, high]``."""
        return low + self.below(high - low + 1)

    def fraction(self) -> float:
        """A draw in ``[0, 1)``."""
        return self._next() / 2**64


def _fanout(value: tuple[int, int] | list[int]) -> tuple[int, int]:
    low, high = value
    return low, high


@frozen
class HierarchyShape:
    """One `Hierarchy` over a dimension's codelist: how much of it, how deep and how wide.

    Each becomes a `Hierarchy` of the codelist's codes and a
    `HierarchyAssociation` linking it to the dimension. A dimension's
    hierarchies take their codes from one seeded ordering of the codelist, so a
    smaller one holds a subset of every larger one, as alternative
    hierarchies over one geography do.

    Attributes:
        coverage: The share of the codelist's codes the hierarchy places, in
            ``(0, 1]``; at least one code.
        depth: Levels; 1 is flat.
        fanout: The least and most children a parent has, drawn per parent.
        multi_parent: The share of non-root nodes whose code is placed again,
            as a leaf, under a second parent at the same level, in ``[0, 1)``.

    Raises:
        ValueError: If a share is out of its range, or `depth` is below 1.
    """

    coverage: float = 1.0
    depth: int = 1
    fanout: tuple[int, int] = field(default=(2, 10), converter=_fanout)
    multi_parent: float = 0.0

    def __attrs_post_init__(self) -> None:
        if not 0 < self.coverage <= 1:
            msg = f"coverage must be in (0, 1], got {self.coverage}"
            raise ValueError(msg)
        if not 0 <= self.multi_parent < 1:
            msg = f"multi_parent must be in [0, 1), got {self.multi_parent}"
            raise ValueError(msg)
        if self.depth < 1:
            msg = f"depth must be at least 1, got {self.depth}"
            raise ValueError(msg)


@frozen
class Dim:
    """One enumerated key dimension: its codelist's size and hierarchy.

    Attributes:
        codes: How many codes its codelist has.
        depth: Levels in the hierarchy; 1 is flat.
        fanout: The least and most children a parent has, drawn per parent,
            so a hierarchy is ragged rather than regular.
        hierarchies: `Hierarchy` artefacts over the codelist, in the order
            their ids number them (``H_DIM_<n>_1``, ``H_DIM_<n>_2``, …).
    """

    codes: int
    depth: int = 1
    fanout: tuple[int, int] = field(default=(2, 10), converter=_fanout)
    hierarchies: tuple[HierarchyShape, ...] = field(default=(), converter=tuple)


@frozen
class Shape:
    """What a synthetic bundle looks like, and what `profile_bundle` reads off a real one.

    Attributes:
        dimensions: The key dimensions, in key order.
        observations: How many observation rows; at most the key space,
            every dimension's codes times `periods`.
        periods: How many distinct `TIME_PERIOD` values (consecutive years).
        languages: The languages every name is given in.
        attributes: Observation-level coded attributes, each with its own
            small codelist.
        annotated: The fraction of codes carrying an annotation.
    """

    dimensions: tuple[Dim, ...] = field(converter=tuple)
    observations: int
    periods: int = 10
    languages: tuple[str, ...] = field(default=("en",), converter=tuple)
    attributes: int = 0
    annotated: float = 0.0


def synthetic_bundle(path: str | Path, shape: Shape, *, seed: int = 0) -> Manifest:
    """Build and publish a bundle of `shape` at `path`, deterministically from `seed`.

    The bundle's first publication, ``<path>/<token>/``, beside its ``data/``.

    One dataflow, ``SYNTHETIC:DF_SYNTHETIC(1.0)``, on a DSD with a dimension
    per `Dim` (``DIM_1``, ``DIM_2``, …, each with its codelist), ``TIME_PERIOD``,
    a numeric ``OBS_VALUE`` and ``ATTR_1``…; its observations in the lake; the
    binding; and the manifest, whose ``product`` records the shape and seed.

    Args:
        path: The bundle directory to create; must not exist or be empty.
        shape: What to build.
        seed: Chooses the hierarchies, the annotations, and which keys the
            observations take.

    Returns:
        The manifest written; the publication is ``<path>/<manifest.token>/``.

    Raises:
        FileExistsError: If `path` exists and is not empty.
        ValueError: If `shape` asks for more observations than its key space
            holds, or for a key space too large to address.
    """
    root = Path(path)
    if root.exists() and any(root.iterdir()):
        msg = f"{root} exists and is not empty; synthetic_bundle creates a new bundle"
        raise FileExistsError(msg)
    radices = [d.codes for d in shape.dimensions] + [shape.periods]
    space = math.prod(radices)
    if shape.observations > space:
        msg = f"{shape.observations} observations do not fit a key space of {space} ({' x '.join(map(str, radices))})"
        raise ValueError(msg)
    if space >= 2**62:
        msg = f"a key space of {space} is too large to address; use fewer or smaller dimensions"
        raise ValueError(msg)
    root.mkdir(parents=True, exist_ok=True)
    draws = _Draws(seed)
    artefacts, dsd, flow = _structures(shape, draws, _Draws(seed, "hierarchies"))
    with LocalRegistry.build(root) as bundle:
        bundle.add_all(artefacts)
        rows = completed(bundle.connection).sql(_observations_sql(shape, space, draws))
        bundle._data_plane().write_observations(schema=_SCHEMA, table=_TABLE, source=rows)
        bundle.add(DataflowBinding.from_dsd(dsd, dataflow=Ref.to(flow), schema=_SCHEMA, table=_TABLE))
        publication = bundle.publication
    return write_manifest(
        publication,
        bundle_id=f"synthetic-{seed}",
        producer=Producer(name="sdmxlib.testing.synthetic_bundle", version="1"),
        product={
            "seed": seed,
            "dimensions": [[d.codes, d.depth, list(d.fanout)] for d in shape.dimensions],
            "observations": shape.observations,
            "periods": shape.periods,
            "languages": list(shape.languages),
            "attributes": shape.attributes,
            "annotated": shape.annotated,
        }
        | _hierarchy_product(shape),
    )


def _hierarchy_product(shape: Shape) -> dict[str, object]:
    """The hierarchies, by dimension number, for the manifest's ``product``; nothing when there are none."""
    recorded = [
        [n, h.coverage, h.depth, list(h.fanout), h.multi_parent]
        for n, dim in enumerate(shape.dimensions, start=1)
        for h in dim.hierarchies
    ]
    return {"hierarchies": recorded} if recorded else {}


def _width(count: int) -> int:
    return len(str(max(count - 1, 0)))


def _parents(count: int, depth: int, fanout: tuple[int, int], draws: _Draws) -> list[int | None]:
    """Each item's parent by index: trees of at most `depth` levels, ragged by `fanout`.

    A parent's index is below its children's.
    """
    parents: list[int | None] = []
    while len(parents) < count:
        parents.append(None)
        level = [len(parents) - 1]
        for _ in range(1, depth):
            below: list[int] = []
            for parent in level:
                for _ in range(draws.between(*fanout)):
                    if len(parents) >= count:
                        break
                    parents.append(parent)
                    below.append(len(parents) - 1)
            level = below
            if not level or len(parents) >= count:
                break
    return parents


def _codelist(n: int, dim: Dim, shape: Shape, draws: _Draws) -> Codelist:
    width = _width(dim.codes)
    ids = [f"D{n}_{i:0{width}d}" for i in range(dim.codes)]
    parents = _parents(dim.codes, dim.depth, dim.fanout, draws)
    codes = [
        Code(
            id=code_id,
            name={lang: f"Dimension {n} code {i} ({lang})" for lang in shape.languages},
            parent_id=None if parent is None else ids[parent],
            annotations=[Annotation(type="SYNTHETIC", title=f"note {i}")] if draws.fraction() < shape.annotated else [],
        )
        for i, (code_id, parent) in enumerate(zip(ids, parents, strict=True))
    ]
    return Codelist(
        id=f"CL_DIM_{n}",
        maintainer=_AGENCY,
        version="1.0",
        name={lang: f"Dimension {n} ({lang})" for lang in shape.languages},
        codes=codes,
    )


def _structures(
    shape: Shape, draws: _Draws, hierarchy_draws: _Draws
) -> tuple[list[SupportsUrn], DataStructure, Dataflow]:
    dims = [f"DIM_{n}" for n in range(1, len(shape.dimensions) + 1)]
    attrs = [f"ATTR_{n}" for n in range(1, shape.attributes + 1)]
    concepts = ConceptScheme(
        id="CS_SYNTHETIC",
        maintainer=_AGENCY,
        version="1.0",
        name=dict.fromkeys(shape.languages, "Synthetic concepts"),
        concepts=[
            Concept(id=c, name=dict.fromkeys(shape.languages, c)) for c in (*dims, "TIME_PERIOD", "OBS_VALUE", *attrs)
        ],
    )
    codelists = [_codelist(n, dim, shape, draws) for n, dim in enumerate(shape.dimensions, start=1)]
    attribute_lists = [
        Codelist(
            id=f"CL_{a}",
            maintainer=_AGENCY,
            version="1.0",
            name=dict.fromkeys(shape.languages, a),
            codes=[
                Code(id=f"V{v}", name=dict.fromkeys(shape.languages, f"{a} value {v}")) for v in range(_ATTRIBUTE_CODES)
            ],
        )
        for a in attrs
    ]
    dsd = DataStructure(
        id="DSD_SYNTHETIC",
        maintainer=_AGENCY,
        version="1.0",
        name=dict.fromkeys(shape.languages, "Synthetic structure"),
        dimensions=[
            Dimension(id=d, concept=concepts.ref_to(d), representation=Ref.to(cl))
            for d, cl in zip(dims, codelists, strict=True)
        ],
        time_dimension=TimeDimension(id="TIME_PERIOD", concept=concepts.ref_to("TIME_PERIOD")),
        measures=[Measure(id="OBS_VALUE", concept=concepts.ref_to("OBS_VALUE"), representation=Facet(type="Double"))],
        attributes=[
            Attribute(
                id=a, concept=concepts.ref_to(a), representation=Ref.to(cl), attachment=AttachmentLevel.OBSERVATION
            )
            for a, cl in zip(attrs, attribute_lists, strict=True)
        ],
    )
    flow = Dataflow(
        id="DF_SYNTHETIC",
        maintainer=_AGENCY,
        version="1.0",
        name=dict.fromkeys(shape.languages, "Synthetic dataflow"),
        structure=Ref.to(dsd),
    )
    hierarchies = [
        artefact
        for n, (dim, codelist) in enumerate(zip(shape.dimensions, codelists, strict=True), start=1)
        for artefact in _hierarchies(n, dim, codelist, dsd, shape, hierarchy_draws)
    ]
    return [concepts, *codelists, *attribute_lists, dsd, flow, *hierarchies], dsd, flow


def _hierarchies(
    n: int, dim: Dim, codelist: Codelist, dsd: DataStructure, shape: Shape, draws: _Draws
) -> list[SupportsUrn]:
    """Dimension `n`'s hierarchies and the associations linking each to it."""
    if not dim.hierarchies:
        return []
    # One seeded ordering of the codes: hierarchy k takes its first codes, so
    # a smaller hierarchy is a subset of a larger one.
    order = list(range(dim.codes))
    for i in range(len(order) - 1, 0, -1):
        j = draws.below(i + 1)
        order[i], order[j] = order[j], order[i]
    ids = [code.id for code in codelist.codes]
    out: list[SupportsUrn] = []
    for k, spec in enumerate(dim.hierarchies, start=1):
        members = [ids[i] for i in order[: max(1, round(spec.coverage * dim.codes))]]
        hierarchy = Hierarchy(
            id=f"H_DIM_{n}_{k}",
            maintainer=_AGENCY,
            version="1.0",
            name={lang: f"Dimension {n} hierarchy {k} ({lang})" for lang in shape.languages},
            tree=_tree(members, spec, codelist, draws),
        )
        out.append(hierarchy)
        out.append(
            HierarchyAssociation(
                id=f"HA_DIM_{n}_{k}",
                maintainer=_AGENCY,
                version="1.0",
                name={lang: f"Dimension {n} hierarchy {k} association ({lang})" for lang in shape.languages},
                linked_hierarchy=Ref.to(hierarchy),
                linked_object=dsd.ref_to(f"DIM_{n}"),
            )
        )
    return out


def _tree(members: list[str], spec: HierarchyShape, codelist: Codelist, draws: _Draws) -> list[HierarchicalCode]:
    """The nodes placing `members`: a forest by `spec`, and a second placement for `spec.multi_parent` of them."""
    parents = _parents(len(members), spec.depth, spec.fanout, draws)
    depth = [0] * len(members)
    children: list[list[int]] = [[] for _ in members]
    for i, parent in enumerate(parents):
        if parent is not None:
            depth[i] = depth[parent] + 1
            children[parent].append(i)
    also = _second_parents(parents, depth, children, spec.multi_parent, draws)
    built: list[HierarchicalCode | None] = [None] * len(members)
    for i in range(len(members) - 1, -1, -1):
        below = [present for c in children[i] if (present := built[c]) is not None]
        below += [HierarchicalCode(id=members[c], code=codelist.ref_to(members[c])) for c in also[i]]
        built[i] = HierarchicalCode(id=members[i], code=codelist.ref_to(members[i]), children=below)
    return [node for i, node in enumerate(built) if parents[i] is None and node is not None]


def _second_parents(
    parents: list[int | None], depth: list[int], children: list[list[int]], share: float, draws: _Draws
) -> list[list[int]]:
    """Per node, the items it also holds as leaves: `share` of the non-root items, each under a second parent.

    The second parent is drawn among the parents at the first one's level, so
    the item keeps its depth, and the extra node carries no subtree.
    """
    parents_at: dict[int, list[int]] = {}
    for i, below in enumerate(children):
        if below:
            parents_at.setdefault(depth[i], []).append(i)
    also: list[list[int]] = [[] for _ in parents]
    for i, parent in enumerate(parents):
        if parent is None or draws.fraction() >= share:
            continue
        candidates = parents_at[depth[parent]]
        other = candidates[draws.below(len(candidates))]
        if other != parent:
            also[other].append(i)
    return also


def _observations_sql(shape: Shape, space: int, draws: _Draws) -> str:
    multiplier = draws.below(space - 1) + 1 if space > 1 else 1
    while math.gcd(multiplier, space) != 1:
        multiplier += 1
    template = observations_sql(
        [d.codes for d in shape.dimensions],
        [_width(d.codes) for d in shape.dimensions],
        periods=shape.periods,
        first_year=_FIRST_YEAR,
        attributes=shape.attributes,
        attribute_codes=_ATTRIBUTE_CODES,
    )
    return observations_from(
        template, multiplier=multiplier, offset=draws.below(space), space=space, rows=shape.observations
    )


def profile_bundle(path: str | Path, flow: SdmxUrn[Dataflow]) -> Shape:
    """The `Shape` of one dataflow in a published bundle: counts and fan-outs, never values.

    Reads each enumerated key dimension's codelist size, hierarchy depth and
    fan-out, and the `Hierarchy` artefacts associated with it; the observation
    count, the number of distinct periods (approximate on a large table), the
    languages names are given in, the attribute count, and the fraction of
    codes annotated. A dimension without a codelist has nothing a `Dim` can
    describe and is left out.

    A hierarchy belongs to a dimension when a `HierarchyAssociation` links it
    to that dimension, with no context or this dataflow as context. They are
    listed by URN: SDMX gives associations no order. Each is read as a
    `HierarchyShape`: the share of the codelist's codes it places, its depth
    and fan-out, and the share of its non-root codes placed a second time
    (`multi_parent`, capped below 1).

    Args:
        path: The bundle directory.
        flow: The dataflow to profile.

    Returns:
        A shape `synthetic_bundle` can build a stand-in from.

    Raises:
        ValueError: If the dataflow or its structure is not in the bundle.
    """
    with LocalRegistry.bundle(path) as reg:
        dsd = reg.dsd(flow)
        binding = reg.binding_for(flow)
        if dsd is None or binding is None:
            msg = f"{flow} is not bound in {path}, or has no data structure there"
            raise ValueError(msg)
        conn = reg.connection
        dims: list[Dim] = []
        codelists: list[str] = []
        for dim in dsd.dimensions:
            representation = dim.representation
            if not isinstance(representation, Ref):
                continue
            urn = str(representation.urn)
            row = conn.execute(
                "SELECT count(*), coalesce(max(depth), 0) + 1 FROM sdmx.code WHERE codelist_urn = ?", [urn]
            ).fetchone()
            fan = conn.execute(
                """
                SELECT coalesce(min(n), 2), coalesce(max(n), 10) FROM (
                    SELECT count(*) AS n FROM sdmx.code
                    WHERE codelist_urn = ? AND parent_id IS NOT NULL GROUP BY parent_id
                )
                """,
                [urn],
            ).fetchone()
            if row is None or fan is None or row[0] == 0:
                continue
            codelists.append(urn)
            hierarchies = _profile_hierarchies(conn, dsd, dim.id, flow, urn, row[0])
            dims.append(Dim(codes=row[0], depth=row[1], fanout=(fan[0], fan[1]), hierarchies=hierarchies))
        languages = conn.execute(
            "SELECT DISTINCT unnest(map_keys(name)) FROM sdmx.code WHERE list_contains(?, codelist_urn) ORDER BY 1",
            [codelists],
        ).fetchall()
        annotated = conn.execute(
            """
            SELECT count(DISTINCT a.owner_urn) / greatest(count(DISTINCT c.code_urn), 1)
            FROM sdmx.code c LEFT JOIN sdmx.annotation a ON a.owner_urn = c.code_urn
            WHERE list_contains(?, c.codelist_urn)
            """,
            [codelists],
        ).fetchone()
        observations = reg.obs(flow).count()
        reg._data_plane()
        time_id = dsd.time_dimension.id if dsd.time_dimension is not None else None
        periods = 0
        if time_id is not None:
            periods = distinct_periods(conn, binding, binding.dimension_column_map.get(time_id, time_id))
    return Shape(
        dimensions=tuple(dims),
        observations=observations,
        periods=max(periods, 1),
        languages=tuple(lang for (lang,) in languages),
        attributes=len(dsd.attributes),
        annotated=float(annotated[0]) if annotated is not None else 0.0,
    )


def _profile_hierarchies(
    conn: duckdb.DuckDBPyConnection,
    dsd: DataStructure,
    dimension: str,
    flow: SdmxUrn[Dataflow],
    codelist: str,
    codes: int,
) -> tuple[HierarchyShape, ...]:
    """The shapes of the hierarchies associated with `dimension`, by URN; one over another codelist is left out."""
    linked = conn.execute(
        """
        SELECT DISTINCT linked_hierarchy_urn FROM sdmx.hierarchy_association
        WHERE linked_object_owner_urn = ? AND linked_object_item_id = ?
          AND (context_object_urn IS NULL OR context_object_urn = ?)
        ORDER BY linked_hierarchy_urn
        """,
        [str(dsd.urn), dimension, str(flow)],
    ).fetchall()
    shapes: list[HierarchyShape] = []
    for (hierarchy,) in linked:
        row = conn.execute(
            """
            WITH nodes AS (SELECT code_urn, parent_path, depth FROM sdmx.hierarchy_node WHERE hierarchy_urn = $h),
            children AS (SELECT count(*) AS n FROM nodes WHERE parent_path IS NOT NULL GROUP BY parent_path)
            SELECT
                (SELECT count(DISTINCT code_urn) FROM nodes
                 WHERE code_urn IN (SELECT code_urn FROM sdmx.code WHERE codelist_urn = $cl)),
                (SELECT count(*) FROM nodes),
                (SELECT count(DISTINCT code_urn) FROM nodes),
                (SELECT count(*) FROM nodes WHERE parent_path IS NOT NULL),
                (SELECT coalesce(max(depth), 0) + 1 FROM nodes),
                (SELECT coalesce(min(n), 2) FROM children),
                (SELECT coalesce(max(n), 10) FROM children)
            """,
            {"h": hierarchy, "cl": codelist},
        ).fetchone()
        if row is None or row[0] == 0:
            continue
        placed, nodes, distinct, non_root, depth, least, most = row
        again = nodes - distinct
        share = again / (non_root - again) if non_root > again else 0.0
        shapes.append(
            HierarchyShape(
                coverage=min(placed / codes, 1.0),
                depth=depth,
                fanout=(least, max(least, most)),
                multi_parent=min(share, _MOST_MULTI_PARENT),
            )
        )
    return tuple(shapes)
