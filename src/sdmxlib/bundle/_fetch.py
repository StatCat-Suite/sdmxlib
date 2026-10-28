"""Landing one dataflow from an SDMX REST source as a published bundle (GH-68).

A thin composition over `build_bundle`: a `RestRegistry` fetches the
dataflow's structural closure; its observation query fetches the rows, the key
and periods pushed into the request; and `build_bundle` writes them into a new
bundle and publishes it, provenance included, with every codelist full.

**Flow-scoped by design.** One dataflow, optionally narrowed, never a whole
registry: public endpoints rate-limit, and mirroring is a different tool.
Bundles are built locally; redistributing one is the user's responsibility
under the provider's terms.
"""

from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Unpack

from sdmxlib.api.client import HttpxOptions
from sdmxlib.api.providers import ProviderInfo
from sdmxlib.api.registry import RestRegistry
from sdmxlib.bundle._build import build_bundle
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import DataStructure
from sdmxlib.model.rest_api import ApiVersion
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.query.observations import ObsQuery
from sdmxlib.storage._manifest import Manifest, Producer, Provenance


def fetch_bundle(
    source: ProviderInfo | str,
    flow: SdmxUrn[Dataflow],
    path: str | Path,
    *,
    key: str | None = None,
    start: str | None = None,
    end: str | None = None,
    structures_only: bool = False,
    api: str = "2.1",
    source_id: str | None = None,
    **options: Unpack[HttpxOptions],
) -> Manifest:
    """Fetch one dataflow, narrowed by an SDMX key and periods, into a new published bundle.

    The bundle is `path`, which must not exist or be empty, and the fetch is
    its first publication, ``<path>/<token>/``: ``meta.duckdb`` holds the
    dataflow's structural closure and its `DataflowBinding`, ``lake.ducklake``
    its observations, whose data files are in ``<path>/data/``, and the
    manifest is written last, with `Provenance` naming the source, the request
    and every artefact fetched. It then passes `check_bundle`.

    The dataflow is fetched with ``references=all`` and its data structure
    with ``references=parents``, so the closure holds the constraints and
    hierarchy associations that refer to either as well as what the dataflow
    refers to: the structural fingerprint covers both. What else those
    requests bring, another dataflow of the same data structure say, is left
    out (`build_bundle`). Over SDMX 2.1 no hierarchy association arrives,
    since 2.1 has none.

    Args:
        source: A `Provider` entry, or a REST endpoint URL (then give ``api``
            and ``source_id``).
        flow: The dataflow. An unpinned version is fetched at the version it
            resolves to, which the manifest records.
        path: The bundle directory to create.
        key: The SDMX-REST data key, one segment per key dimension in the
            DSD's order, ``+`` between codes and empty for any:
            ``M.USD+GBP.EUR.SP00.A``. None fetches every series.
        start: Inclusive ``startPeriod``.
        end: Inclusive ``endPeriod``.
        structures_only: Fetch the structural closure and no observations.
        api: The REST API version of a URL ``source``.
        source_id: The registry id of a URL ``source``, recorded as provenance.
        **options: Passed to the `RestRegistry` (timeouts, a transport).

    Returns:
        The manifest written; the publication is ``<path>/<manifest.token>/``.

    Raises:
        FileExistsError: If `path` exists and is not empty.
        ValueError: If `key` has the wrong number of segments for the DSD,
            naming its key dimensions in order.
    """
    root = Path(path)
    if root.exists() and any(root.iterdir()):
        msg = f"{root} exists and is not empty; fetch_bundle creates a new bundle"
        raise FileExistsError(msg)
    endpoint, api = (source.url, source.api) if isinstance(source, ProviderInfo) else (source, api)
    retrieved_at = datetime.now(UTC)
    with RestRegistry(source, api=api, id=source_id, **options) as remote:
        resolved = remote.get(flow, resolve="all")
        if not isinstance(resolved, Dataflow):
            msg = f"{flow} is not a dataflow at {remote}"
            raise TypeError(msg)
        pointed = resolved.structure
        dsd = None if pointed is None else remote.get(pointed.urn, expect=DataStructure, resolve="parents")
        if dsd is None:
            msg = f"{resolved.urn} has no data structure at {remote}, so its observations have no key order"
            raise ValueError(msg)
        observations = None
        if not structures_only:
            # The client's own API generation: what `obs()` builds the request
            # for, so the key is checked against the same slots it fills.
            query = _narrowed(remote.obs(resolved.urn), dsd, remote._client.api, key=key, start=start, end=end)
            observations = {str(resolved.urn): query.stream()}
        provenance = Provenance(
            source=remote.id or "",
            endpoint=endpoint,
            api=api,
            retrieved_at=retrieved_at,
            dataflow=str(resolved.urn),
            key=key,
            start=start,
            end=end,
            structures_only=structures_only,
            artefacts=(),
        )
        return build_bundle(
            remote,
            [resolved.urn],
            path,
            bundle_id=str(resolved.urn),
            producer=Producer(name="sdmxlib.bundle.fetch_bundle", version=metadata.version("sdmxlib")),
            observations=observations,
            partials=False,
            provenance=provenance,
        )


def _narrowed(
    query: ObsQuery, dsd: DataStructure, api: ApiVersion, *, key: str | None, start: str | None, end: str | None
) -> ObsQuery:
    """`query` filtered by an SDMX key and periods, in the form the REST request carries."""
    if key is not None:
        slots = dsd.key_slot_ids(api)
        segments = key.split(".")
        if len(segments) != len(slots):
            msg = f"key {key!r} has {len(segments)} segments; {dsd.urn}'s key is {len(slots)}: {'.'.join(slots)}"
            raise ValueError(msg)
        for slot, segment in zip(slots, segments, strict=True):
            if segment:
                query = query.filter(query.col(slot).is_in(segment.split("+")))
    if (start is not None or end is not None) and dsd.time_dimension is not None:
        time = query.col(dsd.time_dimension.id)
        if start is not None:
            query = query.filter(time >= start)
        if end is not None:
            query = query.filter(time <= end)
    return query
