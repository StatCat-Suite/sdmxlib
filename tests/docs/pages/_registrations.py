"""Where each page's namespace is built.

Kept in one module so the vocabulary stays visible: every page uses the same
handful of names, and seeing them together is what stops a page inventing a
fifth spelling of "the registry".
"""

from __future__ import annotations

import functools
import json
import logging
import warnings
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import attrs
import polars as pl

import sdmxlib as sl
from sdmxlib.bundle import Producer, copy_bundle, write_manifest
from sdmxlib.local.data_store import DuckDBDataStore
from sdmxlib.model.binding import DataflowBinding
from sdmxlib.model.ref import Ref
from tests.docs.pages import EXECUTED, SETUPS, base
from tests.docs.pages._offline import in_a_directory_containing, serving
from tests.support.bundles import make_bundle, publish
from tests.support.factories import code_urn, concept_ref
from tests.support.narrow import present

#: Any fixed instant — bindings carry timestamps and none of the pages read them.
_STAMP = datetime(2026, 1, 1, tzinfo=UTC)


#: The tutorial's config names each component's concept, as a DSD must (#492).
_PRICES_CONCEPTS = "urn:sdmx:org.sdmx.infomodel.conceptscheme.Concept=MY_AGENCY:CS_PRICES(1.0)."


def _geo_codelist() -> sl.Codelist:
    return sl.Codelist(
        id="CL_GEO",
        maintainer="AG",
        version="1.0",
        name={"en": "Geography", "fr": "Géographie"},
        codes=[
            sl.Code(id="CAN", name={"en": "Canada", "fr": "Canada"}),
            sl.Code(
                id="ON",
                name={"en": "Ontario", "fr": "Ontario"},
                parent_id="CAN",
                annotations=[sl.Annotation(type="geo_level", title="City")],
            ),
            sl.Code(id="QC", name={"en": "Quebec", "fr": "Québec"}, parent_id="CAN"),
            # The store pages look these two up by id, and a codelist that
            # answered every id would make their examples pass whatever the
            # lookup did.
            sl.Code(id="CA", name={"en": "Canada", "fr": "Canada"}),
            sl.Code(id="US", name={"en": "United States", "fr": "États-Unis"}),
        ],
    )


#: The broadest level first: SDMX reads a hierarchy's top codes at its first
#: level, so the country is a level of its own, not an unlevelled root (#455).
_COUNTRY = sl.Level(id="CO", name={"en": "Country"})
_PR = sl.Level(id="PR", name={"en": "Province"})
_CD = sl.Level(id="CD", name={"en": "Census division"})


def _geo_hierarchy() -> sl.Hierarchy:
    """Two formal levels, so `group_by(hcode.level.id)` has more than one
    group — a hierarchy with a single level would make the page's aggregate
    look right whatever it did."""
    return sl.Hierarchy(
        id="H_GEO",
        maintainer="AG",
        version="1.0",
        name={"en": "Geography"},
        has_formal_levels=True,
        levels=[_COUNTRY, _PR, _CD],
        tree=[
            sl.HierarchicalCode(
                id="CAN",
                code=code_urn("CL_GEO", "CAN", agency="AG"),
                children=[
                    sl.HierarchicalCode(
                        id="ON",
                        code=code_urn("CL_GEO", "ON", agency="AG"),
                        level=_PR,
                        children=[
                            sl.HierarchicalCode(id="ON-01", code=code_urn("CL_GEO", "ON-01", agency="AG"), level=_CD)
                        ],
                    ),
                    sl.HierarchicalCode(id="QC", code=code_urn("CL_GEO", "QC", agency="AG"), level=_PR),
                ],
            )
        ],
    )


@contextmanager
def _structures_as_frames() -> Generator[dict[str, Any]]:
    """`cl` — a codelist with a two-level tree, two languages and one
    annotation, which is the minimum that makes every example on the page
    return something distinguishable."""
    with sl.LocalRegistry(":memory:") as registry:
        registry.add_all([_geo_codelist(), _geo_hierarchy()])
        namespace = base()
        namespace["cl"] = registry.get(sl.Codelist.urn_for(agency="AG", id="CL_GEO", version="1.0"))
        namespace["h"] = registry.get(sl.Hierarchy.urn_for(agency="AG", id="H_GEO", version="1.0"))
        # The page reaches for `reg` once, to show that a *dataflow* query
        # groups the same way a code query does. Two agencies, so the group
        # count is not 1.
        registry.add_all(
            [
                sl.Dataflow(id="A_FLOW", maintainer="AG", version="1.0", name={"en": "a"}),
                sl.Dataflow(id="B_FLOW", maintainer="BG", version="1.0", name={"en": "b"}),
            ]
        )
        namespace["reg"] = registry
        yield namespace


SETUPS["cookbook/structures_as_frames.md"] = _structures_as_frames
EXECUTED.add("cookbook/structures_as_frames.md")


def _catalogue() -> list[Any]:
    """The artefacts every catalogue-facing page's examples assume exist."""
    geo = _geo_codelist()
    # `AG:DSD` is what the pages' `.one()` examples name, so it has to exist
    # or the terminal raises for the wrong reason.
    dsd = sl.DataStructure(
        id="DSD",
        maintainer="AG",
        version="1.0",
        name={"en": "Example"},
        dimensions=[
            sl.Dimension(id="REF_AREA", concept=concept_ref("REF_AREA"), representation=Ref.to(geo)),
            sl.Dimension(id="FREQ", concept=concept_ref("FREQ")),
        ],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    return [
        geo,
        dsd,
        sl.Dataflow(
            id="PRC_HICP",
            maintainer="ESTAT",
            version="1.0",
            name={"en": "Consumer price index"},
            structure=Ref.to(dsd),
        ),
        # Every dataflow carries a structure: the pages loop over them calling
        # `df.structure()`, so one without would be a `NoneType` the examples
        # do not guard against and a reader would not hit either.
        sl.Dataflow(id="EXR", maintainer="ECB", version="1.0", name={"en": "Exchange rates"}, structure=Ref.to(dsd)),
        sl.Dataflow(
            id="ECB_EXR1", maintainer="ECB", version="1.0", name={"en": "Exchange rates"}, structure=Ref.to(dsd)
        ),
        sl.CategoryScheme(
            id="DATAFLOWS",
            maintainer="ESTAT",
            version="1.0",
            name={"en": "Dataflows"},
            categories=[
                sl.Category(
                    id="ECON",
                    name={"en": "Economy"},
                    categories=[sl.Category(id="PRICES", name={"en": "Prices"})],
                )
            ],
        ),
    ]


@contextmanager
def _catalogue_page() -> Generator[dict[str, Any]]:
    """For pages that open their own `RestRegistry` — the network is stubbed
    under them, and a local `reg` is bound too for the blocks that assume one
    from an earlier example."""
    artefacts = _catalogue()
    with serving(artefacts), sl.LocalRegistry(":memory:") as registry:
        registry.add_all(artefacts)
        namespace = base()
        namespace["reg"] = registry
        namespace["cl"] = registry.get(sl.Codelist.urn_for(agency="AG", id="CL_GEO", version="1.0"))
        # `ids` is the page's name for "a large set of code ids the caller
        # already has" — the input to `value_set`.
        namespace["ids"] = ["CAN", "ON", "QC"]
        #  is the page's name for the codelist's code query.
        namespace["codes"] = present(namespace["cl"]).codes
        yield namespace


SETUPS["cookbook/discovery.md"] = _catalogue_page
SETUPS["cookbook/catalog.md"] = _catalogue_page
EXECUTED.update({"cookbook/discovery.md", "cookbook/catalog.md"})


def _concept(id_: str) -> Ref[sl.Concept]:
    """Every component needs one: serialisation refuses a DSD without concept
    identities, because every SDMX format does — so a fixture missing them can
    be navigated but not written, and the serialisation page writes."""
    return Ref[sl.Concept].unresolved(sl.Concept.urn_for(agency="AG", scheme="CS", id=id_, version="1.0"))


def _inspectable_dsd() -> sl.DataStructure:
    """Every component shape the inspecting and polars pages talk about."""
    freq = sl.Codelist(
        id="CL_FREQ",
        maintainer="AG",
        version="1.0",
        name={"en": "Frequency", "fr": "Fréquence"},
        codes=[sl.Code(id="A", name={"en": "Annual", "fr": "Annuel"}), sl.Code(id="M", name={"en": "Monthly"})],
    )
    return sl.DataStructure(
        id="DSD_X",
        maintainer="AG",
        version="1.0",
        name={"en": "Example"},
        dimensions=[
            sl.Dimension(id="FREQ", concept=_concept("FREQ"), representation=Ref.to(freq)),
            sl.Dimension(id="REF_AREA", concept=_concept("REF_AREA"), representation=Ref.to(_geo_codelist())),
            sl.Dimension(id="TITLE", concept=_concept("TITLE"), representation=sl.Facet(type="String", max_length=70)),
        ],
        time_dimension=sl.TimeDimension(id="TIME_PERIOD", concept=_concept("TIME_PERIOD")),
        attributes=[
            sl.Attribute(
                id="OBS_STATUS",
                concept=_concept("OBS_STATUS"),
                representation=sl.Facet(type="String"),
                attachment=sl.AttachmentLevel.OBSERVATION,
            ),
            sl.Attribute(id="COMMENT", concept=_concept("COMMENT"), attachment=sl.AttachmentLevel.DATASET),
        ],
        measures=[sl.Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"), representation=sl.Facet(type="Double"))],
    )


@contextmanager
def _structure_page() -> Generator[dict[str, Any]]:
    """For pages that fetch one DSD and then inspect it."""
    dsd = _inspectable_dsd()
    artefacts = [dsd, *(d.representation() for d in dsd.dimensions if isinstance(d.representation, Ref))]
    with serving(artefacts, answer_to_any_name=True), sl.LocalRegistry(":memory:") as registry:
        registry.add_all(artefacts)
        namespace = base()
        namespace["reg"] = registry
        namespace["dsd"] = registry.get(sl.DataStructure.urn_for(agency="AG", id="DSD_X", version="1.0"))
        namespace["cl"] = registry.get(sl.Codelist.urn_for(agency="AG", id="CL_FREQ", version="1.0"))
        yield namespace


@contextmanager
def _serialization() -> Generator[dict[str, Any]]:
    """The page reads `dsd_cpi.xml` by name, so the file exists."""
    dsd = _inspectable_dsd()
    message = sl.StructureMessage(
        data_structures=[dsd],
        codelists=[d.codelist for d in dsd.dimensions if isinstance(d.representation, Ref)],
    )
    with in_a_directory_containing({"dsd_cpi.xml": sl.to_xml(message, version="2.1")}):
        yield base()


@contextmanager
def _polars_page() -> Generator[dict[str, Any]]:
    """The structure page, plus the CSVs the polars examples read by name.

    Rows use codes from the fixture's codelists, so `validation_report`
    returns an *empty* report — a page whose example reported errors on its
    own sample data would teach the wrong thing.
    """
    rows = b"FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE\nA,CAN,2020,1.0\nM,ON,2021,2.0\n"
    with (
        _structure_page() as namespace,
        in_a_directory_containing({"namain_gdp.csv": rows, "namain_gdp_extract.csv": rows}),
    ):
        yield namespace


@contextmanager
def _local_files() -> Generator[dict[str, Any]]:
    """The page reads SDMX-ML and SDMX-JSON files by the names it prints."""
    dsd = _inspectable_dsd()
    message = sl.StructureMessage(
        data_structures=[dsd],
        codelists=[d.codelist for d in dsd.dimensions if isinstance(d.representation, Ref)],
    )
    xml = sl.to_xml(message, version="2.1")
    codelists_only = sl.StructureMessage(
        codelists=[d.codelist for d in dsd.dimensions if isinstance(d.representation, Ref)]
    )
    files = {
        "datastructure.xml": xml,
        "dsd.xml": xml,
        "codelists.xml": sl.to_xml(codelists_only, version="2.1"),
        "local_codelists.xml": sl.to_xml(codelists_only, version="2.1"),
        "codelist.json": sl.to_json(message),
        "codelists.json": sl.to_json(codelists_only),
    }
    artefacts = [dsd, *(d.representation() for d in dsd.dimensions if isinstance(d.representation, Ref))]
    with serving(artefacts, answer_to_any_name=True), in_a_directory_containing(files) as root:
        (root / "path").mkdir(exist_ok=True)
        for name, content in files.items():
            (root / "path" / name).write_bytes(content)
        (root / "path" / "to").mkdir(exist_ok=True)
        for name, content in files.items():
            (root / "path" / "to" / name).write_bytes(content)
        yield base()


SETUPS["cookbook/local_files.md"] = _local_files
SETUPS["cookbook/inspecting.md"] = _structure_page
SETUPS["cookbook/polars.md"] = _polars_page
#: The stores these pages open by name, and the artefacts each already holds.
#:
#: The pages read before they write — "open the store you built last week and
#: look something up" is the point of a persistent registry — so the files
#: have to exist with the identities the examples name. Populated rather than
#: renamed, because a page that fetched `AG:DSD_X` from a store called
#: `bis_cbpol.duckdb` would read as a mistake.
_STORE_FILES = ("bis_cbpol.duckdb", "estat_gdp.duckdb", "my_artefacts.duckdb", "my_store.duckdb", "store.duckdb")


#: BIS's reference-area list is *reference areas*, and the tutorial prints its
#: ids: `# ["AE", "AR", "AU", ...]`. Cloning the geography template here would
#: have left that claim false, which is what checking printed output caught.
#: `US` is in the set because the same page asserts membership on it.
#: Named, not `{"en": id}` — the tutorial prints `cl.codes["US"].name["en"]`
#: and says `"United States"`. A codelist whose labels are its ids makes every
#: label example on the page vacuous.
_BIS_REF_AREAS = {
    "AE": "United Arab Emirates",
    "AR": "Argentina",
    "AU": "Australia",
    "BR": "Brazil",
    "CA": "Canada",
    "CN": "China",
    "US": "United States",
}


def _named_codelists() -> list[sl.Codelist]:
    """The codelists the store pages look up, under the identities they use."""
    template = _geo_codelist()
    areas = sl.Codelist(
        id="CL_BIS_IF_REF_AREA",
        maintainer="BIS",
        version="1.0",
        name={"en": "Reference area"},
        codes=[sl.Code(id=c, name={"en": label}) for c, label in _BIS_REF_AREAS.items()],
    )
    return [
        areas,
        *(
            attrs.evolve(template, maintainer=agency, id=cid)
            for agency, cid in (
                ("BIS", "CL_FREQ"),
                ("ESTAT", "CL_GEO"),
                ("SDMX", "CL_FREQ"),
                ("SDMX", "CL_OBSOLETE"),
            )
        ),
    ]


@contextmanager
def _store_page() -> Generator[dict[str, Any]]:
    """For the pages about a persistent store.

    Real files, not `:memory:` — they are about durability, and a store that
    vanished with the process would let a reopening bug through. The working
    directory is temporary, so each run starts from the same contents.
    """
    dsd = _inspectable_dsd()
    artefacts = [
        dsd,
        *(d.representation() for d in dsd.dimensions if isinstance(d.representation, Ref)),
        *_named_codelists(),
    ]
    named_dsds = [
        attrs.evolve(dsd, maintainer=agency, id=did)
        for agency, did in (("BIS", "WS_CBPOL"), ("ESTAT", "NAMAIN_10_GDP"))
    ]
    with serving(artefacts, answer_to_any_name=True), in_a_directory_containing({}):
        for filename in _STORE_FILES:
            with sl.LocalRegistry(filename) as prepared:
                prepared.add_all([*artefacts, *named_dsds])
        namespace = base()
        namespace["artefacts"] = artefacts
        # The conventional names these pages use for "an artefact you built
        # earlier". They are not defined on the page because the page is about
        # the *store*, not about construction — that is the building tutorial.
        namespace["my_codelist"] = next(a for a in artefacts if isinstance(a, sl.Codelist))
        namespace["my_dsd"] = dsd
        namespace["new_codelist_v2"] = attrs.evolve(namespace["my_codelist"], version="2.0")
        namespace["updated_codelist"] = attrs.evolve(
            next(a for a in artefacts if isinstance(a, sl.Codelist)), version="1.1"
        )
        namespace["my_dataflow"] = sl.Dataflow(
            id="DF", maintainer="AG", version="1.0", name={"en": "flow"}, structure=Ref.to(dsd)
        )
        bilingual = sl.Codelist(
            id="CL_LANG",
            maintainer="AG",
            version="1.0",
            name={"en": "languages", "fr": "langues"},
            codes=[sl.Code(id="A", name={"en": "Annual", "fr": "Annuel"})],
        )
        namespace["codelist_urn"] = namespace["my_codelist"].urn
        namespace["bilingual_codelist"] = bilingual
        namespace["new_cl_freq_v2"] = sl.Codelist(
            id="CL_FREQ",
            maintainer="SDMX",
            version="2.0",
            name={"en": "frequency"},
            codes=[sl.Code(id="A", name={"en": "Annual"}), sl.Code(id="Q", name={"en": "Quarterly"})],
        )
        namespace["urn"] = bilingual.urn
        yield namespace


SETUPS["local_registry_tutorial.md"] = _store_page
SETUPS["index.md"] = _structure_page
SETUPS["cookbook/registry.md"] = _structure_page
SETUPS["cookbook/serialization.md"] = _serialization
EXECUTED.update({"cookbook/inspecting.md", "cookbook/polars.md", "cookbook/serialization.md"})
EXECUTED.update({"index.md", "cookbook/registry.md", "cookbook/local_files.md"})
EXECUTED.add("local_registry_tutorial.md")


@contextmanager
def _resolution() -> Generator[dict[str, Any]]:
    """For the page about how deep a fetch goes.

    Every block opens its own `RestRegistry(sl.Provider.ECB)`, so the network
    is what has to be stubbed rather than the registry. `answer_to_any_name`
    lets the page keep naming `ECB:ECB_EXR1` — the identity is not what it
    demonstrates.
    """
    dsd = _inspectable_dsd()
    artefacts = [
        dsd,
        *(d.representation() for d in dsd.dimensions if isinstance(d.representation, Ref)),
        sl.Dataflow(id="EXR", maintainer="ECB", version="1.0", name={"en": "Exchange rates"}, structure=Ref.to(dsd)),
    ]
    with serving(artefacts, answer_to_any_name=True):
        yield base()


SETUPS["cookbook/resolution.md"] = _resolution
EXECUTED.add("cookbook/resolution.md")


@contextmanager
def _facade() -> Generator[dict[str, Any]]:
    """For the page written for someone *serving* SDMX-REST.

    The one page that needs a whole vertical: structures, a binding, and rows
    to stream. Built as a **real bundle** — `meta.duckdb` beside
    `lake.ducklake`, over a shared ``data/`` — because the page's first line is
    `LocalRegistry.bundle(...)`, and a fixture that handed it a plain store
    instead would leave the one constructor a facade actually deploys with
    unexecuted.

    `ON_CODE` has children because the page's headline example is a hierarchy
    traversal that resolves to a relation — over a flat codelist it would
    return nothing and the example would pass by returning an empty frame.
    """
    areas = sl.Codelist(
        id="CL_AREA",
        maintainer="ESTAT",
        version="1.0",
        name={"en": "Areas"},
        codes=[
            sl.Code(id="ON_CODE", name={"en": "Ontario"}),
            sl.Code(id="CA", name={"en": "Toronto"}, parent_id="ON_CODE"),
            sl.Code(id="US", name={"en": "Ottawa"}, parent_id="ON_CODE"),
        ],
    )
    dsd = sl.DataStructure(
        id="DSD_STS",
        maintainer="ESTAT",
        version="1.0",
        name={"en": "Short-term statistics"},
        dimensions=[
            sl.Dimension(id="REF_AREA", representation=Ref.to(areas), concept=_concept("REF_AREA")),
            sl.Dimension(id="FREQ", concept=_concept("FREQ")),
            sl.Dimension(id="TIME_PERIOD", concept=_concept("TIME_PERIOD")),
        ],
        attributes=[sl.Attribute(attachment=sl.AttachmentLevel.DATASET, id="GEO_LEVEL", concept=_concept("GEO_LEVEL"))],
        measures=[sl.Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"))],
    )
    flow = sl.Dataflow(
        id="STS_INPR_M", maintainer="ESTAT", version="1.0", name={"en": "Production"}, structure=Ref.to(dsd)
    )
    # The page's `parse_rest_ref` example names `SDMX,CL_FREQ,1.0` and the next
    # block resolves it, so it has to be here.
    freq = sl.Codelist(
        id="CL_FREQ",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Frequency"},
        codes=[sl.Code(id="M", name={"en": "Monthly"})],
    )
    with in_a_directory_containing({}):
        # Built as a bundle's first publication, then copied to `bundle/`, the
        # path the page opens; its data files land in `data/` beside it.
        with sl.LocalRegistry.build("built") as writer:
            writer.add_all([areas, freq, dsd, flow])
            writer._data_plane().write_observations(
                schema="data",
                table="obs",
                source=pl.DataFrame(
                    {
                        "REF_AREA": ["CA", "US"],
                        "FREQ": ["M", "M"],
                        "TIME_PERIOD": ["2021", "2021"],
                        "GEO_LEVEL": ["FED", "FED"],
                        "OBS_VALUE": [1.0, 2.0],
                    }
                ),
            )
            writer.add(DataflowBinding.from_dsd(dsd, dataflow=Ref.to(flow), schema="data", table="obs"))
            publication = writer.publication

        write_manifest(publication, bundle_id="tutorial", producer=Producer(name="docs-tests", version="0"))
        bundle = Path("bundle")
        copy_bundle(publication, bundle)
        registry = sl.LocalRegistry.bundle(bundle)
        try:
            namespace = base()
            namespace["reg"] = registry
            namespace["flow_urn"] = flow.urn
            namespace["dsd"] = registry.dsd(flow.urn)
            namespace["geo_codelist_urn"] = areas.urn
            namespace["key"] = "all"
            query_components: dict[str, object] = {}
            namespace["query_components"] = query_components
            # The page's stand-in for whatever the caller does with a batch.
            namespace["process"] = lambda batch: None
            yield namespace
        finally:
            registry.close()


SETUPS["cookbook/rest_facade.md"] = _facade
EXECUTED.add("cookbook/rest_facade.md")


_PROVINCE = sl.Level(id="PR", name={"en": "Province"})
_DIVISION = sl.Level(id="CD", name={"en": "Census division"})


#: `CL_GEO_STAT`'s codes and their names: what a node shows, since a
#: hierarchical code has no name of its own (#419).
_GEO_NAMES = {
    "A": "Agriculture",
    "01": "Crop production",
    "C": "Manufacturing",
    "29": "Motor vehicles",
    "CA": "Canada",
    "ON": "Ontario",
    "ON-01": "Eastern Ontario",
    "OTTAWA": "Ottawa",
    "QC": "Quebec",
    "BC": "British Columbia",
    "VAN": "Vancouver",
    "NS": "Nova Scotia",
}


@functools.cache
def _geo_stat_codelist() -> sl.Codelist:
    """The codelist every geography node places a code of — stored beside the hierarchies, so a query reaches names."""
    return sl.Codelist(
        id="CL_GEO_STAT",
        maintainer="STATCAN",
        version="1.0",
        name={"en": "Statistical geography codes"},
        codes=[sl.Code(id=code_id, name={"en": name}) for code_id, name in _GEO_NAMES.items()],
    )


def _geo(code_id: str) -> Ref[sl.Code]:
    """A node's code in `CL_GEO_STAT`, resolved, so ``node.code()`` gives the name the node shows.

    Every node needs one: `HierarchicalCodeType` requires the code in every
    format, and `classifications.xml` below is written from this hierarchy —
    without them it was schema-invalid, and #434 refuses the write.
    """
    return _geo_stat_codelist().ref_to(code_id)


def _classification() -> sl.Hierarchy:
    """One hierarchy carrying every shape the hierarchies page names.

    Two unrelated trees on purpose: a NACE-shaped one (`A`/`01`, `C`/`29`) for
    the inheritance example, and a geography one with formal levels for the
    rollup and level-filter examples. A single-tree fixture would let
    `ancestor_at("PR")` look right while mapping everything to one answer.
    """
    ottawa_code = _geo("OTTAWA")
    return sl.Hierarchy(
        id="H_GEO_STAT",
        maintainer="STATCAN",
        version="1.0",
        name={"en": "Statistical geography"},
        has_formal_levels=True,
        levels=[_COUNTRY, _PROVINCE, _DIVISION],
        tree=[
            sl.HierarchicalCode(
                id="A",
                code=_geo("A"),
                children=[sl.HierarchicalCode(id="01", code=_geo("01"))],
            ),
            sl.HierarchicalCode(
                id="C",
                code=_geo("C"),
                children=[sl.HierarchicalCode(id="29", code=_geo("29"))],
            ),
            sl.HierarchicalCode(
                id="CA",
                code=_geo("CA"),
                children=[
                    sl.HierarchicalCode(
                        id="ON",
                        code=_geo("ON"),
                        level=_PROVINCE,
                        children=[
                            sl.HierarchicalCode(id="ON-01", code=_geo("ON-01"), level=_DIVISION),
                            sl.HierarchicalCode(
                                id="OTTAWA",
                                level=_DIVISION,
                                code=ottawa_code,
                            ),
                        ],
                    ),
                    sl.HierarchicalCode(id="QC", code=_geo("QC"), level=_PROVINCE),
                    sl.HierarchicalCode(
                        id="BC",
                        code=_geo("BC"),
                        level=_PROVINCE,
                        children=[sl.HierarchicalCode(id="VAN", code=_geo("VAN"), level=_DIVISION)],
                    ),
                ],
            ),
        ],
    )


def _chain_codelist() -> sl.Codelist:
    """`A -> B -> C`, which is what the page's `include_self` examples assert:
    `narrower_transitive("A") == ["B", "C"]` needs two levels below A."""
    return sl.Codelist(
        id="CL_CHAIN",
        maintainer="AG",
        version="1.0",
        name={"en": "Chain"},
        codes=[
            sl.Code(id="A", name={"en": "A"}),
            sl.Code(id="B", name={"en": "B"}, parent_id="A"),
            sl.Code(id="C", name={"en": "C"}, parent_id="B"),
        ],
    )


def _second_classification() -> sl.Hierarchy:
    """A second tree over the same node ids, overlapping the first in part.

    The subtree-intersection example needs two hierarchies and a **non-empty**
    answer: the country-scale pair in #375's own measurement intersects to
    zero, and an example built on that would read as working while proving
    nothing. `ON` and `QC` are in both; `NS` is only here.
    """
    return sl.Hierarchy(
        id="H_GEO_POP",
        maintainer="STATCAN",
        version="1.0",
        name={"en": "By population centre"},
        tree=[
            sl.HierarchicalCode(
                id="CA",
                code=_geo("CA"),
                children=[
                    sl.HierarchicalCode(id="ON", code=_geo("ON")),
                    sl.HierarchicalCode(id="QC", code=_geo("QC")),
                    sl.HierarchicalCode(id="NS", code=_geo("NS")),
                ],
            ),
        ],
    )


@contextmanager
def _hierarchies() -> Generator[dict[str, Any]]:
    hierarchy = _classification()
    other = _second_classification()
    codelist = _chain_codelist()
    geo = _geo_stat_codelist()
    message = sl.StructureMessage(hierarchies=[hierarchy], codelists=[codelist, geo])
    files = {"classifications.xml": sl.to_xml(message, version="3.0")}
    with serving([hierarchy, codelist, geo], answer_to_any_name=True), in_a_directory_containing(files):
        with sl.LocalRegistry("classifications.duckdb") as prepared:
            prepared.add_all([hierarchy, other, codelist, geo])
        namespace = base()
        namespace["h"] = hierarchy
        namespace["cl"] = codelist
        namespace["h_urn"] = hierarchy.urn
        namespace["other_urn"] = other.urn
        yield namespace


SETUPS["cookbook/hierarchies.md"] = _hierarchies
EXECUTED.add("cookbook/hierarchies.md")


@contextmanager
def _constraints() -> Generator[dict[str, Any]]:
    """A published constraint to read, and a lake to synthesise one from.

    The page's availability section states its expected answers in comments —
    `REF_AREA -> {"CA"}` for `exact` against `{"CA", "US"}` for `available` —
    so the rows are the ones that make those two differ. A lake where every
    key were present would let a builder ignoring `mode` look correct.
    """
    freq = sl.Codelist(
        id="CL_FREQ",
        maintainer="ECB",
        version="1.0",
        name={"en": "Frequency"},
        codes=[sl.Code(id=c, name={"en": c}) for c in ("A", "M", "Q")],
    )
    area = sl.Codelist(
        id="CL_AREA",
        maintainer="ECB",
        version="1.0",
        name={"en": "Area"},
        codes=[sl.Code(id=c, name={"en": c}) for c in ("CA", "US")],
    )
    dsd = sl.DataStructure(
        id="ECB_EXR1",
        maintainer="ECB",
        version="1.0",
        name={"en": "Exchange rates"},
        dimensions=[
            sl.Dimension(id="FREQ", representation=Ref.to(freq), concept=_concept("FREQ")),
            sl.Dimension(id="REF_AREA", representation=Ref.to(area), concept=_concept("REF_AREA")),
        ],
        measures=[sl.Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"))],
    )
    flow = sl.Dataflow(
        id="ECB_EXR1", maintainer="ECB", version="1.0", name={"en": "Exchange rates"}, structure=Ref.to(dsd)
    )
    constraint = sl.DataConstraint(
        id="ECB_EXR1_CONSTRAINTS",
        maintainer="ECB",
        version="1.0",
        name={"en": "Exchange rate constraints"},
        constraint_type=sl.ConstraintType.ALLOWED,
        # Attached to the flow, as ECB's are: the page finds a flow's
        # constraints through `dataflows`, which an unattached one is not in.
        attachment=sl.ConstraintAttachment(dataflows=(Ref.to(flow),)),
        regions=(
            sl.CubeRegion(
                include=True,
                keys=(
                    sl.KeyValue(dimension_id="FREQ", values=frozenset({"D"})),
                    sl.KeyValue(dimension_id="CURRENCY", values=frozenset({"USD", "GBP"})),
                    sl.KeyValue(dimension_id="CURRENCY_DENOM", values=frozenset({"EUR"})),
                ),
            ),
        ),
    )
    artefacts = [freq, area, dsd, flow, constraint]
    message = sl.StructureMessage(
        codelists=[freq, area], data_structures=[dsd], dataflows=[flow], constraints=[constraint]
    )
    files = {"ecb_exr1_with_constraints.xml": sl.to_xml(message, version="2.1")}
    with serving(artefacts), in_a_directory_containing(files):
        with sl.LocalRegistry("lake.duckdb") as lake:
            lake.add_all(artefacts)
            cursor = lake.connection.cursor()
            cursor.execute("CREATE SCHEMA IF NOT EXISTS data")
            cursor.execute("CREATE TABLE data.obs (FREQ TEXT, REF_AREA TEXT, OBS_VALUE DOUBLE)")
            cursor.execute(
                "INSERT INTO data.obs VALUES ('A', 'CA', 1.0), ('A', 'US', 2.0), ('Q', 'CA', 3.0), ('M', 'US', 4.0)"
            )
            lake.add(
                DataflowBinding(
                    dataflow=Ref.to(flow),
                    database_role="lake",
                    table_location="data.obs",
                    dimension_column_map={"FREQ": "FREQ", "REF_AREA": "REF_AREA"},
                    measure_column_map={"OBS_VALUE": "OBS_VALUE"},
                    attribute_column_map={},
                    structural_version="ECB:ECB_EXR1(1.0)",
                    source_registry="local",
                    materialized_at=_STAMP,
                    bound_at=_STAMP,
                )
            )
        namespace = base()
        namespace["flow_urn"] = flow.urn
        yield namespace


SETUPS["cookbook/constraints.md"] = _constraints
EXECUTED.add("cookbook/constraints.md")


@contextmanager
def _federation() -> Generator[dict[str, Any]]:
    """Routing, drift and the local lake, with every provider stubbed.

    `urns` and `log` are the page's names for a caller's own loop variables —
    the loop is what it demonstrates, not where the ids came from.

    **The one page that has to silence a warning, and it is the stub's.** The
    page opens an ECB registry and then an ESTAT one over a shared
    `cache.duckdb`, the case cache-mode federation exists for. Really
    those two publish disjoint agencies, so the store never sees one URN from
    both. `serving` answers every provider with the *same* fixture, so it does
    — and `storage.writers` correctly warns that a spine row is being
    overwritten by a second source.

    Matched on the message rather than the category so any *other* warning a
    doc page raises still fails under `filterwarnings = ["error"]`. The fix if
    this ever gets in the way is a stub that partitions its fixture by agency,
    which is more machinery than the pages have earned.
    """
    dsd = _inspectable_dsd()
    artefacts = [
        dsd,
        *(d.representation() for d in dsd.dimensions if isinstance(d.representation, Ref)),
        *_named_codelists(),
    ]
    areas = sl.Codelist(
        id="CL_AREA",
        maintainer="STATCAN",
        version="2021",
        name={"en": "Areas"},
        codes=[sl.Code(id="CA", name={"en": "Canada"})],
    )
    census_dsd = sl.DataStructure(
        id="DSD_CP",
        maintainer="STATCAN",
        version="2021",
        name={"en": "Census profile"},
        dimensions=[
            sl.Dimension(id="REF_AREA", representation=Ref.to(areas), concept=_concept("REF_AREA")),
            sl.Dimension(id="TIME_PERIOD", concept=_concept("TIME_PERIOD")),
        ],
        attributes=[sl.Attribute(attachment=sl.AttachmentLevel.DATASET, id="GEO_NAME", concept=_concept("GEO_NAME"))],
        measures=[sl.Measure(id="OBS_COUNT", concept=_concept("OBS_COUNT"))],
    )
    census_flow = sl.Dataflow(
        id="CENSUS_PROFILE",
        maintainer="STATCAN",
        version="2021",
        name={"en": "Census profile"},
        structure=Ref.to(census_dsd),
    )
    with (
        warnings.catch_warnings(),
        serving(artefacts, answer_to_any_name=True),
        in_a_directory_containing({}),
    ):
        warnings.filterwarnings("ignore", message="Spine row .* is being overwritten", category=RuntimeWarning)
        # The lake the page's binding points at. Pre-created because the page
        # *adds the binding* and then queries through it — a binding naming a
        # table that does not exist would fail at the query, not at the add,
        # which is a confusing place for a fixture gap to surface.
        with sl.LocalRegistry("lake.duckdb", source_registry="local") as prepared:
            prepared.add_all([areas, census_dsd, census_flow])
            cursor = prepared.connection.cursor()
            cursor.execute("CREATE SCHEMA IF NOT EXISTS census_2021")
            cursor.execute(
                "CREATE TABLE census_2021.observations "
                "(REF_AREA TEXT, TIME_PERIOD TEXT, OBS_COUNT BIGINT, GEO_NAME TEXT, GEO_LEVEL TEXT)"
            )
            cursor.execute("INSERT INTO census_2021.observations VALUES ('CA', '2021', 5, 'Canada', 'CTRY')")
        namespace = base()
        namespace["urns"] = [sl.Codelist.urn_for(agency="ESTAT", id="CL_GEO", version="1.0")]
        namespace["log"] = logging.getLogger("docs")
        yield namespace


SETUPS["cookbook/federation.md"] = _federation
EXECUTED.add("cookbook/federation.md")


_KEI_DIMENSIONS = ("REF_AREA", "FREQ", "MEASURE", "UNIT_MEASURE", "ACTIVITY")


def _kei_dsd() -> sl.DataStructure:
    """OECD's KEI shape, cut down to what the page's examples touch.

    `MEASURE` is enumerated because the page pivots on it and `validate()`
    checks values against the codelist — an un-enumerated measure dimension
    would make both examples pass without checking anything.
    """
    codelists = {
        "REF_AREA": ("USA", "DEU", "FRA", "GBR", "ITA", "JPN", "CAN"),
        "FREQ": ("Q", "A"),
        "MEASURE": ("GDP", "GFCF", "EMP"),
        "UNIT_MEASURE": ("XDC",),
        "ACTIVITY": ("_T",),
    }
    lists = {
        dim: sl.Codelist(
            id=f"CL_{dim}",
            maintainer="OECD.SDD.STES",
            version="1.0",
            name={"en": dim},
            codes=[sl.Code(id=c, name={"en": "United States" if c == "USA" else c}) for c in codes],
        )
        for dim, codes in codelists.items()
    }
    return sl.DataStructure(
        id="DSD_KEI",
        maintainer="OECD.SDD.STES",
        version="1.0",
        name={"en": "Key economic indicators"},
        dimensions=[
            *(
                sl.Dimension(id=dim, representation=Ref.to(lists[dim]), concept=_concept(dim))
                for dim in _KEI_DIMENSIONS
            ),
            sl.Dimension(id="TIME_PERIOD", concept=_concept("TIME_PERIOD")),
        ],
        measures=[sl.Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"))],
    )


def _kei_csv() -> bytes:
    """Two periods x seven countries x three measures, in SDMX-CSV 2.0.

    Every country the page's G7 example names, so its final pivot has seven
    columns rather than one — a single-country fixture would let a broken
    pivot produce a plausible-looking frame.
    """
    header = "STRUCTURE,STRUCTURE_ID,ACTION,{},TIME_PERIOD,OBS_VALUE".format(",".join(_KEI_DIMENSIONS))
    rows: list[str] = [header]
    value = 100.0
    for area in ("USA", "DEU", "FRA", "GBR", "ITA", "JPN", "CAN"):
        for measure in ("GDP", "GFCF", "EMP"):
            for period in ("2015-Q1", "2015-Q2"):
                value += 1.5
                rows.append(f"dataflow,OECD.SDD.STES:DSD_KEI@DF_KEI(1.0),I,{area},Q,{measure},XDC,_T,{period},{value}")
    return ("\n".join(rows) + "\n").encode()


@contextmanager
def _indicator_flows() -> Generator[dict[str, Any]]:
    dsd = _kei_dsd()
    flow = sl.Dataflow(
        id="DSD_KEI@DF_KEI",
        maintainer="OECD.SDD.STES",
        version="1.0",
        name={"en": "Key economic indicators"},
        structure=Ref.to(dsd),
    )
    artefacts = [dsd, *(d.representation() for d in dsd.dimensions if isinstance(d.representation, Ref)), flow]
    with serving(artefacts, data=_kei_csv(), answer_to_any_name=True), in_a_directory_containing({}):
        namespace = base()
        namespace["flow"] = flow
        namespace["reg"] = sl.RestRegistry(sl.Provider.OECD)
        yield namespace


SETUPS["cookbook/indicator_flows.md"] = _indicator_flows
EXECUTED.add("cookbook/indicator_flows.md")


@contextmanager
def _observation_data() -> Generator[dict[str, Any]]:
    """The observation-data page, with each provider answering for itself.

    **Three real structures, not one union.** An earlier version served one
    DSD carrying every dimension the page mentions, re-stamped with whatever
    identity was asked for. That let the page run and made its printed claims
    false: `print(ds.dimensions)` says ECB EXR has six, and the union had nine.
    Checking printed output is what surfaced it — running never would have.

    So `serving` takes a payload per dataflow id, the identities are the real
    ones, and `answer_to_any_name` is off: the fixture is answerable for what
    the page says about it.
    """
    exr_dsd = _exr_dsd()
    exr_flow = sl.Dataflow(
        id="EXR", maintainer="ECB", version="1.0", name={"en": "Exchange rates"}, structure=Ref.to(exr_dsd)
    )
    kei_dsd = _kei_dsd()
    kei_flows = [
        sl.Dataflow(
            id=flow_id,
            maintainer="OECD.SDD.STES",
            version="1.0",
            name={"en": "Key economic indicators"},
            structure=Ref.to(kei_dsd),
        )
        for flow_id in ("DF_KEI", "DSD_KEI@DF_KEI")
    ]
    lfs_freq = sl.Codelist(
        id="CL_FREQ_ESTAT",
        maintainer="ESTAT",
        version="1.0",
        name={"en": "Frequency"},
        codes=[sl.Code(id="A", name={"en": "Annual"})],
    )
    lfs_dsd = sl.DataStructure(
        id="DSD_LFSI",
        maintainer="ESTAT",
        version="1.0",
        name={"en": "Labour force"},
        dimensions=[
            sl.Dimension(id="FREQ", representation=Ref.to(lfs_freq), concept=_concept("FREQ")),
            sl.Dimension(id="TIME_PERIOD", concept=_concept("TIME_PERIOD")),
        ],
        measures=[sl.Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"))],
    )
    lfs_flow = sl.Dataflow(
        id="LFSI_EMP_A", maintainer="ESTAT", version="1.0", name={"en": "Employment"}, structure=Ref.to(lfs_dsd)
    )
    lfs_csv = b"STRUCTURE,STRUCTURE_ID,ACTION,FREQ,TIME_PERIOD,OBS_VALUE\ndataflow,ESTAT:DSD_LFSI(1.0),I,A,2021,1.0\n"
    artefacts = [
        exr_dsd,
        *(d.representation() for d in exr_dsd.dimensions if isinstance(d.representation, Ref)),
        exr_flow,
        kei_dsd,
        *(d.representation() for d in kei_dsd.dimensions if isinstance(d.representation, Ref)),
        *kei_flows,
        lfs_freq,
        lfs_dsd,
        lfs_flow,
    ]
    exr_csv, kei_csv = _exr_csv(), _kei_csv()
    payloads = {
        "EXR": exr_csv,
        "DF_KEI": kei_csv,
        "DSD_KEI@DF_KEI": kei_csv,
        "LFSI_EMP_A": lfs_csv,
    }
    with serving(artefacts, data=payloads), in_a_directory_containing({"ecb_exr.csv": exr_csv}):
        with sl.LocalRegistry("lake.duckdb") as prepared:
            prepared.add_all(artefacts)
            cursor = prepared.connection.cursor()
            cursor.execute("CREATE SCHEMA IF NOT EXISTS data")
            cursor.execute("CREATE TABLE data.obs (REF_AREA TEXT, TIME_PERIOD TEXT, OBS_VALUE DOUBLE)")
            cursor.execute("INSERT INTO data.obs VALUES ('CAN', '2020-01', 1.0)")
            prepared.add(
                DataflowBinding(
                    dataflow=Ref.to(kei_flows[0]),
                    database_role="lake",
                    table_location="data.obs",
                    dimension_column_map={"REF_AREA": "REF_AREA", "TIME_PERIOD": "TIME_PERIOD"},
                    measure_column_map={"OBS_VALUE": "OBS_VALUE"},
                    attribute_column_map={},
                    structural_version="OECD.SDD.STES:DSD_KEI(1.0)",
                    source_registry="local",
                    materialized_at=_STAMP,
                    bound_at=_STAMP,
                )
            )
        namespace = base()
        namespace["flow_urn"] = kei_flows[0].urn
        namespace["process"] = lambda batch: None
        namespace["reg"] = sl.RestRegistry(sl.Provider.ECB)
        namespace["dsd"] = exr_dsd
        yield namespace


SETUPS["cookbook/data.md"] = _observation_data
EXECUTED.add("cookbook/data.md")


@contextmanager
def _reference_metadata() -> Generator[dict[str, Any]]:
    """The names the page builds *from* rather than builds.

    `reg` is a store the page's later query examples read, populated with the
    metadata sets its earlier blocks construct — so the queries return rows
    rather than passing over an empty table, which is the failure mode a
    doc-block runner is worst at noticing.
    """
    concept_scheme = sl.ConceptScheme(
        id="CS_CENSUS",
        maintainer="STATCAN",
        version="1.0",
        name={"en": "Census concepts"},
        concepts=[sl.Concept(id="FOOTNOTE_TEXT", name={"en": "Footnote text"})],
    )
    footnote_concept = concept_scheme.concepts["FOOTNOTE_TEXT"]
    dsd = sl.DataStructure(
        id="DSD_CENSUS",
        maintainer="STATCAN",
        version="1.0",
        name={"en": "Census"},
        dimensions=[sl.Dimension(id="REF_AREA", concept=_concept("REF_AREA"))],
        measures=[sl.Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"))],
    )
    flow = sl.Dataflow(
        id="DF_CENSUS", maintainer="STATCAN", version="1.0", name={"en": "Census"}, structure=Ref.to(dsd)
    )
    with in_a_directory_containing({}):
        with sl.LocalRegistry("census_footnotes.duckdb") as prepared:
            prepared.add_all([concept_scheme, dsd, flow])
        registry = sl.LocalRegistry("census_footnotes.duckdb")
        registry.open()
        try:
            namespace = base()
            namespace["footnote_concept"] = footnote_concept
            namespace["message"] = sl.StructureMessage(data_structures=[dsd], dataflows=[flow])
            # A str: `metadata_set.targets` stores the target URN as text, so
            # the comparison on this page is against a string.
            namespace["flow_urn"] = str(flow.urn)
            namespace["reg"] = registry
            yield namespace
        finally:
            registry.close()


SETUPS["cookbook/reference_metadata.md"] = _reference_metadata
EXECUTED.add("cookbook/reference_metadata.md")


@contextmanager
def _building() -> Generator[dict[str, Any]]:
    """The tutorial builds everything it uses; it needs only its inputs.

    Two files it reads, and a stubbed ECB for the "evolve what you fetched"
    section.
    """
    dsd = _inspectable_dsd()
    config = {
        "id": "DSD_PRICES",
        "agency_id": "MY_AGENCY",
        "version": "1.0",
        "name": {"en": "Prices"},
        "dimensions": [
            {
                "id": "FREQ",
                "concept": f"{_PRICES_CONCEPTS}FREQ",
                "representation": {
                    "kind": "Enumeration",
                    "urn": "urn:sdmx:org.sdmx.infomodel.codelist.Codelist=MY_AGENCY:CL_FREQ(1.0)",
                },
            },
            {"id": "REF_AREA", "concept": f"{_PRICES_CONCEPTS}REF_AREA"},
        ],
        "measures": [{"id": "OBS_VALUE", "concept": f"{_PRICES_CONCEPTS}OBS_VALUE"}],
    }
    countries = b"id,label,parent_id\nNA,North America,\nCA,Canada,NA\nUS,United States,NA\n"
    files = {"dsd_config.json": json.dumps(config).encode(), "iso_countries.csv": countries}
    artefacts = [dsd, *(d.representation() for d in dsd.dimensions if isinstance(d.representation, Ref))]
    with serving(artefacts, answer_to_any_name=True), in_a_directory_containing(files):
        namespace = base()
        namespace["Path"] = Path
        yield namespace


SETUPS["building_tutorial.md"] = _building
EXECUTED.add("building_tutorial.md")


#: Annotated rather than inferred: the tuples differ in length, so without it
#: the element type is `Unknown` and every lookup keyed on one degrades.
_EXR_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "FREQ": ("A", "D", "M", "Q"),
    "CURRENCY": ("USD", "GBP", "JPY"),
    "CURRENCY_DENOM": ("EUR",),
    "EXR_TYPE": ("SP00",),
    "EXR_SUFFIX": ("A",),
}

#: Labels that differ from their code, because the page filters by label —
#: `label("CURRENCY") == "US dollar"` matches nothing if the label *is* "USD".
_EXR_LABELS = {
    ("CURRENCY", "USD"): "US dollar",
    ("CURRENCY", "GBP"): "Pound sterling",
    ("CURRENCY", "JPY"): "Japanese yen",
    ("FREQ", "M"): "Monthly",
    ("FREQ", "A"): "Annual",
    ("FREQ", "Q"): "Quarterly",
    ("FREQ", "D"): "Daily",
}


def _exr_dsd() -> sl.DataStructure:
    lists = {
        dim: sl.Codelist(
            id=f"CL_{dim}",
            maintainer="ECB",
            version="1.0",
            name={"en": dim},
            codes=[sl.Code(id=c, name={"en": _EXR_LABELS.get((dim, c), c)}) for c in codes],
        )
        for dim, codes in _EXR_DIMENSIONS.items()
    }
    return sl.DataStructure(
        id="ECB_EXR1",
        maintainer="ECB",
        version="1.0",
        name={"en": "Exchange rates"},
        dimensions=[
            *(
                sl.Dimension(id=dim, representation=Ref.to(lists[dim]), concept=_concept(dim))
                for dim in _EXR_DIMENSIONS
            ),
            sl.Dimension(id="TIME_PERIOD", concept=_concept("TIME_PERIOD")),
        ],
        measures=[sl.Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"))],
    )


def _exr_csv() -> bytes:
    """One series per currency, four months each.

    (CURRENCY, TIME_PERIOD) is unique, which is what the tutorial's `pivot`
    example needs — a fixture repeating a pair makes polars raise there rather
    than showing the wide frame the page describes.
    """
    columns = [*_EXR_DIMENSIONS, "TIME_PERIOD"]
    rows = ["STRUCTURE,STRUCTURE_ID,ACTION," + ",".join(columns) + ",OBS_VALUE"]
    value = 1.0
    for currency in ("USD", "GBP", "JPY"):
        for period in ("2020-01", "2020-02", "2020-03", "2020-04"):
            value += 0.25
            cells = {
                "FREQ": "M",
                "CURRENCY": currency,
                "CURRENCY_DENOM": "EUR",
                "EXR_TYPE": "SP00",
                "EXR_SUFFIX": "A",
                "TIME_PERIOD": period,
            }
            rows.append("dataflow,ECB:EXR(1.0),I," + ",".join(cells[c] for c in columns) + f",{value}")
    return ("\n".join(rows) + "\n").encode()


@contextmanager
def _data_tutorial() -> Generator[dict[str, Any]]:
    dsd = _exr_dsd()
    flow = sl.Dataflow(id="EXR", maintainer="ECB", version="1.0", name={"en": "Exchange rates"}, structure=Ref.to(dsd))
    artefacts = [dsd, *(d.representation() for d in dsd.dimensions if isinstance(d.representation, Ref)), flow]
    csv = _exr_csv()
    with serving(artefacts, data=csv, answer_to_any_name=True), in_a_directory_containing({"ecb_exr.csv": csv}):
        namespace = base()
        namespace["dsd"] = dsd
        yield namespace


SETUPS["data_tutorial.md"] = _data_tutorial
EXECUTED.add("data_tutorial.md")


@contextmanager
def _upgrading() -> Generator[dict[str, Any]]:
    """The guide's *current* spellings, executed.

    Its "0.48" halves are `text` fences rather than skipped Python: they no
    longer parse against this API at all, and `tests/docs/test_upgrading.py`
    is what proves each removed name is really gone. What this runs is the
    other half — the line a reader will copy.

    `cl` is deliberately a `LazyCodelist`: the "a query is a plan" section is
    about exactly the codelist a `LocalRegistry` hands back, and an eager one
    would make `len(cl.codes)` succeed and the example read as wrong.
    """
    geo = _geo_codelist()
    dsd = sl.DataStructure(
        id="ECB_EXR1",
        maintainer="ECB",
        version="1.0",
        name={"en": "Exchange rates"},
        dimensions=[sl.Dimension(id="REF_AREA", representation=Ref.to(geo), concept=_concept("REF_AREA"))],
        measures=[sl.Measure(id="OBS_VALUE", concept=_concept("OBS_VALUE"))],
    )
    flow = sl.Dataflow(id="EXR", maintainer="ECB", version="1.0", name={"en": "Exchange rates"}, structure=Ref.to(dsd))
    artefacts = [geo, dsd, flow]
    csv = b"STRUCTURE,STRUCTURE_ID,ACTION,REF_AREA,OBS_VALUE\ndataflow,ECB:ECB_EXR1(1.0),I,ON,1.0\n"
    with serving(artefacts, data=csv, answer_to_any_name=True), in_a_directory_containing({}):
        with sl.LocalRegistry("store.duckdb") as prepared:
            prepared.add_all(artefacts)
            cursor = prepared.connection.cursor()
            cursor.execute("CREATE SCHEMA IF NOT EXISTS data")
            cursor.execute("CREATE TABLE data.obs (REF_AREA TEXT, OBS_VALUE DOUBLE)")
            cursor.execute("INSERT INTO data.obs VALUES ('ON', 1.0)")
            prepared.add(
                DataflowBinding(
                    dataflow=Ref.to(flow),
                    database_role="store",
                    table_location="data.obs",
                    dimension_column_map={"REF_AREA": "REF_AREA"},
                    measure_column_map={"OBS_VALUE": "OBS_VALUE"},
                    attribute_column_map={},
                    structural_version="ECB:ECB_EXR1(1.0)",
                    source_registry="local",
                    materialized_at=_STAMP,
                    bound_at=_STAMP,
                )
            )
        local = sl.LocalRegistry("store.duckdb")
        local.open()
        registry = sl.RestRegistry(sl.Provider.ECB, store=local)
        try:
            namespace = base()
            namespace["reg"] = registry
            namespace["fed"] = sl.FederatedRegistry(routing={"ECB": registry}, default=local)
            namespace["cl"] = local.get(geo.urn)
            namespace["urn"] = geo.urn
            namespace["flow_urn"] = flow.urn
            namespace["dsd"] = dsd
            namespace["key"] = "ON"
            namespace["pinned_urn"] = dsd.urn
            namespace["latest_urn"] = sl.DataStructure.urn_for(agency="ECB", id="ECB_EXR1")
            yield namespace
        finally:
            registry.close()
            local.close()


SETUPS["upgrading.md"] = _upgrading
EXECUTED.add("upgrading.md")


def _query_catalogue() -> list[Any]:
    """The catalogue the query tutorial documents, in the order the page lists it.

    Small enough that a reader can hold it in their head while reading, and
    shaped so each section has something real to find: a tree three levels
    deep, annotations on some codes and not others, two structures sharing
    codelists, and `CL_RETIRED` which nothing references — several sections
    ask "what is orphaned", and that question needs an answer that is not the
    empty set.
    """
    freq = sl.Codelist(
        id="CL_FREQ",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Frequency"},
        codes=[
            sl.Code(id="A", name={"en": "Annual"}, annotations=[sl.Annotation(type="ORDER", text={"en": "1"})]),
            sl.Code(id="Q", name={"en": "Quarterly"}, annotations=[sl.Annotation(type="ORDER", text={"en": "2"})]),
            sl.Code(id="M", name={"en": "Monthly"}),
        ],
    )
    area = sl.Codelist(
        id="CL_AREA",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Reference area", "fr": "Zone de référence"},
        codes=[
            sl.Code(id="W", name={"en": "World", "fr": "Monde"}),
            sl.Code(id="EU", name={"en": "Europe", "fr": "Europe"}, parent_id="W"),
            sl.Code(id="FR", name={"en": "France", "fr": "France"}, parent_id="EU"),
            sl.Code(id="DE", name={"en": "Germany", "fr": "Allemagne"}, parent_id="EU"),
            sl.Code(id="AS", name={"en": "Asia", "fr": "Asie"}, parent_id="W"),
            sl.Code(id="JP", name={"en": "Japan", "fr": "Japon"}, parent_id="AS"),
        ],
    )
    unit = sl.Codelist(
        id="CL_UNIT",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Unit of measure"},
        codes=[sl.Code(id="EUR", name={"en": "Euro"}), sl.Code(id="USD", name={"en": "US dollar"})],
    )
    retired = sl.Codelist(
        id="CL_RETIRED",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Retired codes"},
        codes=[sl.Code(id="X", name={"en": "Obsolete"})],
    )
    prices = sl.DataStructure(
        id="DSD_PRICES",
        maintainer="ECB",
        version="1.0",
        name={"en": "Consumer prices"},
        dimensions=[
            sl.Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=Ref.to(freq)),
            sl.Dimension(id="REF_AREA", concept=concept_ref("REF_AREA"), representation=Ref.to(area)),
        ],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    trade = sl.DataStructure(
        id="DSD_TRADE",
        maintainer="ECB",
        version="1.0",
        name={"en": "Trade balance"},
        dimensions=[
            sl.Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=Ref.to(freq)),
            sl.Dimension(id="REF_AREA", concept=concept_ref("REF_AREA"), representation=Ref.to(area)),
            sl.Dimension(id="UNIT", concept=concept_ref("UNIT"), representation=Ref.to(unit)),
        ],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    return [
        freq,
        area,
        unit,
        retired,
        prices,
        trade,
        sl.Dataflow(
            id="CPI_M",
            maintainer="ECB",
            version="1.0",
            name={"en": "Consumer prices, monthly"},
            structure=Ref.to(prices),
        ),
        sl.Dataflow(
            id="CPI_A",
            maintainer="ECB",
            version="1.0",
            name={"en": "Consumer prices, annual"},
            structure=Ref.to(prices),
        ),
        sl.Dataflow(
            id="TRADE_Q",
            maintainer="ESTAT",
            version="1.0",
            name={"en": "Trade balance, quarterly"},
            structure=Ref.to(trade),
        ),
    ]


@contextmanager
def _query_tutorial() -> Generator[dict[str, Any]]:
    """`reg`, holding the catalogue the page's table describes.

    Also serves the same artefacts over the patched client, so the page's one
    `RestRegistry` example runs for real rather than being skipped — a skipped
    block on a tutorial is a claim nobody checked.
    """
    artefacts = _query_catalogue()
    with serving(artefacts, answer_to_any_name=True), sl.LocalRegistry(":memory:") as reg:
        reg.add_all(artefacts)
        namespace = base()
        namespace["reg"] = reg
        yield namespace


SETUPS["query_tutorial.md"] = _query_tutorial
EXECUTED.add("query_tutorial.md")


@contextmanager
def _data_query_tutorial() -> Generator[dict[str, Any]]:
    """One dataflow with nine observations, and the binding that locates them.

    Real values rather than round ones: the page shows `mean` being rounded in
    polars, and a fixture whose means came out exact would make that section
    look like ceremony instead of the boundary it is teaching.
    """
    area = sl.Codelist(
        id="CL_AREA",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Reference area"},
        codes=[
            sl.Code(id="CA", name={"en": "Canada", "fr": "Canada"}),
            sl.Code(id="MX", name={"en": "Mexico", "fr": "Mexique"}),
            sl.Code(id="US", name={"en": "United States", "fr": "États-Unis"}),
        ],
    )
    freq = sl.Codelist(
        id="CL_FREQ",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Frequency"},
        codes=[sl.Code(id="A", name={"en": "Annual", "fr": "Annuel"})],
    )
    status = sl.Codelist(
        id="CL_STATUS",
        maintainer="SDMX",
        version="1.0",
        name={"en": "Observation status"},
        codes=[sl.Code(id="A", name={"en": "Normal"}), sl.Code(id="P", name={"en": "Provisional"})],
    )
    dsd = sl.DataStructure(
        id="DSD_CPI",
        maintainer="OECD",
        version="1.0",
        name={"en": "Consumer prices"},
        dimensions=[
            sl.Dimension(id="FREQ", concept=concept_ref("FREQ"), representation=Ref.to(freq)),
            sl.Dimension(id="REF_AREA", concept=concept_ref("REF_AREA"), representation=Ref.to(area)),
        ],
        time_dimension=sl.TimeDimension(id="TIME_PERIOD", concept=concept_ref("TIME_PERIOD")),
        attributes=[
            sl.Attribute(
                id="OBS_STATUS",
                concept=concept_ref("OBS_STATUS"),
                representation=Ref.to(status),
                attachment=sl.AttachmentLevel.OBSERVATION,
            )
        ],
        measures=[sl.Measure(id="OBS_VALUE", concept=concept_ref("OBS_VALUE"))],
    )
    flow = sl.Dataflow(
        id="CPI",
        maintainer="OECD",
        version="1.0",
        name={"en": "Consumer price inflation"},
        structure=Ref.to(dsd),
    )
    rows = [
        ("A", "CA", "2021", 3.4, "A"),
        ("A", "CA", "2022", 6.8, "A"),
        ("A", "CA", "2023", 3.9, "P"),
        ("A", "US", "2021", 4.7, "A"),
        ("A", "US", "2022", 8.0, "A"),
        ("A", "US", "2023", 5.3, "P"),
        ("A", "MX", "2021", 5.7, "A"),
        ("A", "MX", "2022", 7.9, "A"),
        ("A", "MX", "2023", 5.6, "P"),
    ]
    with sl.LocalRegistry(":memory:") as reg:
        reg.add_all([area, freq, status, dsd, flow])
        cursor = reg.connection.cursor()
        cursor.execute("CREATE SCHEMA IF NOT EXISTS data")
        cursor.execute(
            "CREATE TABLE data.cpi (FREQ TEXT, REF_AREA TEXT, TIME_PERIOD TEXT, OBS_VALUE DOUBLE, OBS_STATUS TEXT)"
        )
        cursor.executemany("INSERT INTO data.cpi VALUES (?, ?, ?, ?, ?)", rows)
        binding = DataflowBinding(
            dataflow=Ref.to(flow),
            database_role="memory",
            table_location="data.cpi",
            dimension_column_map={"FREQ": "FREQ", "REF_AREA": "REF_AREA", "TIME_PERIOD": "TIME_PERIOD"},
            measure_column_map={"OBS_VALUE": "OBS_VALUE"},
            attribute_column_map={"OBS_STATUS": "OBS_STATUS"},
            structural_version="OECD:DSD_CPI(1.0)",
            source_registry="local",
            materialized_at=_STAMP,
            bound_at=_STAMP,
        )
        reg.add(binding)
        store = DuckDBDataStore.from_registry(reg)
        namespace = base()
        namespace["store"] = store
        namespace["binding"] = binding
        namespace["dsd"] = dsd
        yield namespace


SETUPS["data_query_tutorial.md"] = _data_query_tutorial
EXECUTED.add("data_query_tutorial.md")


@contextmanager
def _llms() -> Generator[dict[str, Any]]:
    """The two files an LLM agent reads in place of the site.

    ECB's exchange-rate structure and data behind the stubbed client, as the
    data tutorial has them, because the files open with ECB and say so in
    their printed output. Nothing else is bound: an agent copies a block out
    of these files on its own, so each block names what it uses.
    """
    dsd = _exr_dsd()
    flow = sl.Dataflow(id="EXR", maintainer="ECB", version="1.0", name={"en": "Exchange rates"}, structure=Ref.to(dsd))
    artefacts = [dsd, *(d.representation() for d in dsd.dimensions if isinstance(d.representation, Ref)), flow]
    csv = _exr_csv()
    with serving(artefacts, data=csv, answer_to_any_name=True), in_a_directory_containing({"ecb_exr.csv": csv}):
        yield base()


SETUPS["llms.txt"] = _llms
SETUPS["llms-dsl.txt"] = _llms
EXECUTED.update({"llms.txt", "llms-dsl.txt"})


@contextmanager
def _readme() -> Generator[dict[str, Any]]:
    """The front page's BIS example, with the DSD it fetches served locally.

    `answer_to_any_name` re-stamps the fixture as `BIS:WS_CBPOL`: the README
    names a real provider's structure, and the identity is not what it
    demonstrates.
    """
    dsd = _inspectable_dsd()
    artefacts = [dsd, *(d.representation() for d in dsd.dimensions if isinstance(d.representation, Ref))]
    with serving(artefacts, answer_to_any_name=True):
        yield base()


SETUPS["README.md"] = _readme
EXECUTED.add("README.md")


@contextmanager
def _bundle_page() -> Generator[dict[str, Any]]:
    """`docs/bundle.md`: two published bundles, and a server's copy of one reading its data files remotely.

    The object-store example's store is a ``file://`` URI over the published
    census bundle, which DuckDB reads as it reads ``abfss://``; the server's
    copy holds tiers 1 and 2 only.
    """
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        census = publish(make_bundle(root / "census", 10))
        pod = root / "pod" / "census" / census.name
        copy_bundle(census, pod, tiers=(1, 2))
        yield base() | {
            "census_path": census,
            "trade_path": publish(make_bundle(root / "trade", 3)),
            "pod_path": pod,
            "store_url": f"file://{census}/",
        }


SETUPS["bundle.md"] = _bundle_page
EXECUTED.add("bundle.md")
