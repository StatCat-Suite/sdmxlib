"""Every claim in `docs/upgrading.md` is checked against the API it describes.

An upgrade guide is the one page a reader cannot verify for themselves: they
are reading it precisely because their code does not run yet, so a wrong line
sends them to a name that never existed. Writing this page found one — it
documented `latest_ttl=` on `RestRegistry`, which reached the client only by
being smuggled through `**httpx_kwargs` and which basedpyright rejected at the
call site.

So the page's claims are assertions here rather than prose there: names that
must be gone, names that must exist, parameters that must be declared, and
behaviours the "before/after" blocks promise.
"""

from __future__ import annotations

import annotationlib
import inspect

import pytest

import sdmxlib as sl
from sdmxlib import bundle
from tests.support.narrow import present
from tests.support.repo import ROOT

pytestmark = pytest.mark.docs

_PAGE = ROOT / "docs" / "upgrading.md"

#: Named in the guide as removed from the public API.
_REMOVED = [
    "Registry",
    "FederatedCatalog",
    "FailurePolicy",
    "CodeQuery",
    "HierarchyNodeQuery",
    "Predicate",
    "CompileContext",
    "AnyRef",
    "code",
    "node",
    "Dataset",
    "DataQuery",
    "DimFilter",
    "dim",
]


def test_the_page_exists_and_is_linked() -> None:
    """A guide nobody can find is a guide nobody reads."""
    assert _PAGE.exists()
    nav = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "upgrading.md" in nav, "add it to the mkdocs nav"


@pytest.mark.parametrize("name", _REMOVED)
def test_a_name_the_guide_calls_removed_is_removed(name: str) -> None:
    """If one of these came back, the guide would be telling readers to stop
    using something that works."""
    assert not hasattr(sl, name), f"sl.{name} exists, but upgrading.md says it was removed"
    assert name in _PAGE.read_text(encoding="utf-8"), f"{name} is not actually named on the page"


@pytest.mark.parametrize(
    "name",
    [
        "LocalRegistry",
        "BundleRegistry",
        "RestRegistry",
        "FederatedRegistry",
        "DataFormat",
        "RegistryUnavailable",
        "widen_key",
        "q",
    ],
)
def test_a_replacement_the_guide_names_exists(name: str) -> None:
    assert hasattr(sl, name), f"upgrading.md points readers at sl.{name}"


@pytest.mark.parametrize(("parameter", "owner"), [("store", "__init__"), ("latest_ttl", "__init__")])
def test_a_constructor_parameter_the_guide_shows_is_declared(parameter: str, owner: str) -> None:
    """Declared, not merely accepted.

    `latest_ttl` reached the client through `**httpx_kwargs` and worked at
    runtime while basedpyright rejected the call — so it was documented,
    functional, and unusable in typed code at the same time. A guide showing a
    keyword argument has to mean a real parameter.
    """
    parameters = inspect.signature(
        getattr(sl.RestRegistry, owner), annotation_format=annotationlib.Format.FORWARDREF
    ).parameters
    assert parameter in parameters, f"RestRegistry.{owner} has no declared `{parameter}`"


def test_get_takes_identity_and_a_depth() -> None:
    """The guide's central before/after. `get(Type, agency=…)` is gone;
    `get(urn, resolve=…, partial=…)` is what replaced it."""
    parameters = inspect.signature(sl.RestRegistry.get, annotation_format=annotationlib.Format.FORWARDREF).parameters
    # `expect` joined the list in #406 — a keyword, not a change to the
    # identity-and-depth shape this test is about. The exact list rather than a
    # subset, so the next addition is also a deliberate edit here.
    assert list(parameters) == ["self", "target", "expect", "resolve", "partial"]
    assert "agency" not in parameters


def test_urn_for_builds_and_urn_reads() -> None:
    """Two operations, two names — the guide's `.urn` section."""
    assert isinstance(inspect.getattr_static(sl.Codelist, "urn"), property)
    urn = sl.Codelist.urn_for(agency="ECB", id="CL_FREQ")
    assert urn.agency == "ECB"


def test_a_lazy_codelist_refuses_len_and_iteration() -> None:
    """The guide warns that a query is a plan. It is worth asserting because
    the failure is a `TypeError` at a line that used to work."""
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            sl.Codelist(
                id="CL",
                maintainer="AG",
                version="1.0",
                name={"en": "c"},
                codes=[sl.Code(id="A", name={"en": "a"})],
            )
        )
        codelist = registry.get(sl.Codelist.urn_for(agency="AG", id="CL", version="1.0"))
        with pytest.raises(TypeError, match="no len"):
            len(present(codelist).codes)
        with pytest.raises(TypeError, match="not iterable"):
            iter(present(codelist).codes)
        # No narrowing needed since #397: `get` declares `LazyCodelist` for a
        # codelist URN, which is what it has always returned. This used to take
        # an `isinstance` *and* a `cast` — the guard alone could not do it,
        # because `LazyCodelist` duck-types as `Codelist` without subclassing
        # it, so narrowing yielded an intersection in which `Codelist.codes`
        # still won and `.count()` read as `Sequence.count(value)`.
        assert codelist is not None
        assert codelist.codes.count() == 1, "and the replacement the guide names works"
        assert codelist.codes.ids() == ["A"]


def test_a_str_is_refused_by_every_get() -> None:
    """The guide says a bare string raises and names both replacements (#370).

    Worth asserting rather than trusting: the old behaviour was *silent*, so a
    reader who does not believe the guide gets no signal from trying it.
    """
    urn = sl.Codelist.urn_for(agency="ECB", id="CL_FREQ", version="1.0")
    with sl.LocalRegistry(":memory:") as registry:
        registry.add(
            sl.Codelist(
                id="CL_FREQ",
                maintainer="ECB",
                version="1.0",
                name={"en": "Frequency"},
                codes=[sl.Code(id="A", name={"en": "Annual"})],
            )
        )
        with pytest.raises(TypeError, match=r"SdmxUrn\.parse"):
            # Two rules since #397 made `get` overloaded: the call fails
            # overload resolution *and* the generic fallback's parameter type.
            registry.get(str(urn))  # pyrefly: ignore[no-matching-overload]
        assert registry.get(sl.SdmxUrn.parse(str(urn))) is not None, "the first replacement the guide names"
        assert registry.get(urn) is not None, "and the second"


def test_a_ref_finds_what_its_urn_finds() -> None:
    """The other half of the same section: a `Ref` used to miss, silently."""
    with sl.LocalRegistry(":memory:") as registry:
        codelist = sl.Codelist(
            id="CL_FREQ",
            maintainer="ECB",
            version="1.0",
            name={"en": "Frequency"},
            codes=[sl.Code(id="A", name={"en": "Annual"})],
        )
        registry.add(codelist)
        by_ref = registry.get(sl.Ref[sl.Codelist].unresolved(codelist.urn))
        assert by_ref is not None, "a Ref returned None here before #370"
        assert by_ref.id == "CL_FREQ"


def test_key_slot_ids_replaced_key_dimension_ids() -> None:
    assert hasattr(sl.DataStructure, "key_slot_ids")
    assert not hasattr(sl.DataStructure, "key_dimension_ids")


def test_bundle_reads_and_build_writes() -> None:
    """The guide's read-only bundle section (GH-122): `bundle()` lost
    `require_manifest=` and returns a type with no writes; `build()` opens for
    writing."""
    parameters = inspect.signature(
        sl.LocalRegistry.bundle, annotation_format=annotationlib.Format.FORWARDREF
    ).parameters
    assert "require_manifest" not in parameters
    assert issubclass(sl.LocalRegistry, sl.BundleRegistry)
    assert [
        name for name in ("add", "add_all", "drop", "replace", "ingest", "sql") if hasattr(sl.BundleRegistry, name)
    ] == []
    assert callable(sl.LocalRegistry.build)


def test_build_starts_publications_over_a_shared_data_directory() -> None:
    """The guide's bundle-layout section (GH-123): `build()` takes `previous=`
    and `resume=`, the manifest is format 2 with the layout fields, and
    `copy_bundle` exists."""
    parameters = inspect.signature(sl.LocalRegistry.build, annotation_format=annotationlib.Format.FORWARDREF).parameters
    assert {"previous", "resume"} <= parameters.keys()
    assert bundle.FORMAT_VERSION.partition(".")[0] == "2"
    assert {"root", "carried_from"} <= set(bundle.BundleFile.__struct_fields__)
    assert {"previous_token", "data_path"} <= set(bundle.Manifest.__struct_fields__)
    assert callable(bundle.copy_bundle)
