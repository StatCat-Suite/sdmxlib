"""Hypothesis strategies for SDMX artefacts (#215).

**Derived from the SDMX specification, not from `sdmxlib.model.validation`.**

The strategies depend on that distinction. If `sdmx_ids()` were built from the same
regex `validation.py` enforces, then "every generated id validates" would be a
tautology — the strategy and the code under test would share a single source of
truth, and a wrong regex would produce a passing property. The alphabets below
are transcribed from the SDMX Information Model's ``IDType`` / ``VersionType``
/ ``NestedNCNameIDType``, so a disagreement between a generated value and
`validate` is a real question about which of the two is wrong.

Test-only; not packaged with sdmxlib.
"""

from hypothesis import strategies as st

from sdmxlib.model.annotations import Annotation, Annotations
from sdmxlib.model.category import Categorisation, Category, CategoryScheme
from sdmxlib.model.codelist import Code, Codelist
from sdmxlib.model.collections import ItemList
from sdmxlib.model.concept import Concept, ConceptScheme
from sdmxlib.model.constraint import (
    AttachmentConstraint,
    ConstraintAttachment,
    ConstraintType,
    CubeRegion,
    DataConstraint,
    DataKey,
    DataKeySet,
    KeyValue,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.datastructure import AttachmentLevel, Attribute, DataStructure, Dimension, Measure
from sdmxlib.model.hierarchy import HierarchicalCode, Hierarchy, HierarchyAssociation, Level
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.mapping import (
    CategorySchemeMap,
    ComponentMap,
    ConceptSchemeMap,
    ItemMap,
    OrganisationSchemeMap,
    RepresentationMap,
    RepresentationMapping,
    StructureMap,
)
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.metadatastructure import (
    MetadataAttribute,
    MetadataStructure,
    MetadataTarget,
    ReportStructure,
    TargetObject,
)
from sdmxlib.model.organisation import Agency, AgencyScheme, Contact, DataProvider, DataProviderScheme
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref
from sdmxlib.model.representation import DataType, Facet
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.validation import _XML_FORBIDDEN
from tests.support.factories import concept_ref

# The SDMX XSDs use two different id types, and the distinction is load-bearing.
#
#   IDType        [A-Za-z0-9_@$\-]+          items: Code, Concept, ...
#   NCNameIDType  [A-Za-z][A-Za-z0-9_\-]*    maintainables and components
#
# NCNameIDType exists because those ids become XML element and attribute names
# in structure-specific schemas, so they must be valid XML NCNames: they start
# with a letter and cannot contain @ or $.
#
# `sdmxlib.model.validation` applies the looser IDType everywhere, so it accepts
# maintainable ids the XSD rejects — found by the #216 round-trip properties and
# filed separately. These strategies follow the *schemas*, which is the whole
# reason #215 derived them from the spec rather than from validation.py.
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_ID_ALPHABET = _LETTERS + "0123456789_@$-"
_NCNAME_ALPHABET = _LETTERS + "0123456789_-"

# Language tags that appear in the checked-in fixtures. The awkward ones are
# deliberate: a two-letter assumption anywhere breaks on pt-BR and zh-Hans, and
# es-419 is a numeric region subtag.
_LANG_TAGS = ["en", "fr", "de", "es", "pt-BR", "zh-Hans", "es-419"]

# Every SDMX artefact must be representable in SDMX-ML, so the alphabet here is
# exactly the complement of `validation._XML_FORBIDDEN` — the same set the
# library refuses at the read boundary and in `validate` (#221).
#
# Deriving it from production rather than restating it is safe *because* that
# set is not itself an assumption: `test_validation_xml_charset` anchors it to
# what lxml actually accepts. Restating it here independently is how U+FFFE got
# through on the first attempt — it is category Cn, not Cc, so a
# category-based exclusion missed it and the round-trip property failed inside
# lxml rather than saying anything useful.
#
# Surrogates need no explicit exclusion: `codec="utf-8"` already drops them,
# and they are not encodable on any wire in any case.
_XML_UNSAFE = "".join(sorted(c for c in _XML_FORBIDDEN if not 0xD800 <= ord(c) <= 0xDFFF))

# #224 removed the four members that appeared in no schema enumeration, so
# every remaining DataType is schema-valid and no exclusion list is needed.
_SCHEMA_VALID_DATATYPES = list(DataType)

# SDMX-ML 2.1 restricts a *component's* TextFormat to `SimpleDataType`, 39 of
# the 44 values. `XHTML` is legal in 3.0/3.1 and at other 2.1 positions but not
# there, so a component facet carrying it is valid 3.0 and invalid 2.1 — a real
# downgrade hazard rather than a strategy artefact, also #224. These properties
# write one artefact to all four formats, so component facets draw from the
# intersection.
_COMPONENT_DATATYPES = [d for d in _SCHEMA_VALID_DATATYPES if d.value != "XHTML"]
_XML_SAFE_TEXT = st.text(
    alphabet=st.characters(codec="utf-8", exclude_characters=_XML_UNSAFE),
    min_size=1,
    max_size=20,
)


@st.composite
def sdmx_ids(draw: st.DrawFn, *, min_size: int = 1, max_size: int = 12) -> str:
    """An SDMX ``IDType`` value — the permissive form, valid for *items*.

    Args:
        draw: Hypothesis draw function.
        min_size: Minimum length.
        max_size: Maximum length.

    Returns:
        A candidate item id.
    """
    return draw(st.text(alphabet=_ID_ALPHABET, min_size=min_size, max_size=max_size))


@st.composite
def ncname_ids(draw: st.DrawFn, *, max_size: int = 12) -> str:
    """An SDMX ``NCNameIDType`` value — required for maintainables and components.

    Starts with a letter; no ``@`` or ``$``.

    Args:
        draw: Hypothesis draw function.
        max_size: Maximum length.

    Returns:
        A candidate maintainable id.
    """
    head = draw(st.sampled_from(_LETTERS))
    tail = draw(st.text(alphabet=_NCNAME_ALPHABET, max_size=max_size - 1))
    return head + tail


@st.composite
def agency_ids(draw: st.DrawFn) -> str:
    """An agency id — ``NestedNCNameIDType``, i.e. dot-separated NCNames."""
    parts = draw(st.lists(ncname_ids(max_size=6), min_size=1, max_size=3))
    return ".".join(parts)


@st.composite
def versions(draw: st.DrawFn) -> str:
    """An SDMX version — one or more dot-separated non-negative integers."""
    parts = draw(st.lists(st.integers(min_value=0, max_value=99), min_size=1, max_size=3))
    return ".".join(str(p) for p in parts)


@st.composite
def international_strings(draw: st.DrawFn, *, allow_empty: bool = True) -> InternationalString:
    """A localised string.

    Args:
        draw: Hypothesis draw function.
        allow_empty: Whether the empty (no-language) case may be generated. It
            is a distinct code path — `emit_pair` returns ``(None, None)`` for
            it — so it is on by default.

    Returns:
        The InternationalString.
    """
    langs = draw(
        st.dictionaries(
            st.sampled_from(_LANG_TAGS),
            _XML_SAFE_TEXT,
            min_size=0 if allow_empty else 1,
            max_size=3,
        )
    )
    return InternationalString(**langs)


#: A name's text in one language: something to read, never blank (#452).
_NAME_TEXT = _XML_SAFE_TEXT.filter(str.strip)


def names() -> st.SearchStrategy[InternationalString]:
    """A NameableArtefact's name: at least one language, and none of them blank (#452).

    `international_strings` may draw ``" "``, which `_check_name` refuses: a
    name must name something in every language it gives.
    """
    return st.dictionaries(st.sampled_from(_LANG_TAGS), _NAME_TEXT, min_size=1, max_size=3).map(
        lambda langs: InternationalString(**langs)
    )


@st.composite
def annotations(draw: st.DrawFn, *, max_size: int = 3) -> Annotations:
    """A list of annotations, any field of which may be absent."""
    items = draw(
        st.lists(
            st.builds(
                Annotation,
                id=st.one_of(st.none(), sdmx_ids()),
                title=st.one_of(st.none(), _XML_SAFE_TEXT),
                type=st.one_of(st.none(), sdmx_ids()),
                # One unlocalised URL at most, which every target carries; the
                # census covers several and localised ones (#422).
                urls=st.one_of(st.just(()), st.just(("https://example.org/a",))),
                text=international_strings(),
            ),
            max_size=max_size,
        )
    )
    return Annotations(items)


@st.composite
def facets(draw: st.DrawFn, *, for_component: bool = False) -> Facet:
    """A free-text representation with any subset of its eleven bounds set.

    All eleven are drawn independently because the SDMX-ML writer emits them
    from a single table; fixtures that happen to set three say nothing about
    the other eight.

    Bounds are drawn within what the XSDs actually accept, which is narrower
    than what `Facet` accepts:

    - ``decimals`` / ``max_length`` / ``min_length`` are ``xs:positiveInteger``,
      so 0 is invalid. `Facet(decimals=0)` is constructible and every writer
      emits it, producing non-schema-valid output (#224).
    - ``isMultiLingual`` is not permitted on a component's ``TextFormat`` in
      SDMX-ML 2.1, though it is elsewhere. ``for_component=True`` omits it.

    Args:
        draw: Hypothesis draw function.
        for_component: Restrict to bounds legal on a DSD component.

    Returns:
        The Facet.
    """
    opt_pos = st.one_of(st.none(), st.integers(min_value=1, max_value=1000))
    # No longer rounded: #224 fixed `_xsd_num` to format via Decimal, so
    # exponent notation no longer reaches the wire and the full float range is
    # representable again. The rounding was a workaround for that bug.
    opt_float = st.one_of(st.none(), st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False))
    opt_bool = st.one_of(st.none(), st.booleans())
    return Facet(
        type=draw(
            st.one_of(st.none(), st.sampled_from(_COMPONENT_DATATYPES if for_component else _SCHEMA_VALID_DATATYPES))
        ),
        max_length=draw(opt_pos),
        min_length=draw(opt_pos),
        max_value=draw(opt_float),
        min_value=draw(opt_float),
        decimals=draw(opt_pos),
        pattern=draw(st.one_of(st.none(), st.just("[A-Z]+"))),
        start_value=draw(opt_float),
        end_value=draw(opt_float),
        is_sequence=draw(opt_bool),
        is_multilingual=False if for_component else draw(st.booleans()),
    )


@st.composite
def codes(draw: st.DrawFn, *, max_size: int = 6) -> list[Code]:
    """A list of codes with unique ids and no parent links.

    Names are non-empty: `Code` is an SDMX ``Item``, hence a
    ``NameableArtefact``, so a Name is required. The first run of
    `test_generated_codelists_validate` caught this strategy generating
    unnamed codes — the property doing exactly its job, and the answer being
    that the strategy was wrong rather than the validator.
    """
    ids = draw(st.lists(sdmx_ids(), min_size=0, max_size=max_size, unique=True))
    return [
        Code(
            id=code_id,
            name=draw(names()),
            description=draw(international_strings()),
        )
        for code_id in ids
    ]


@st.composite
def codelists(draw: st.DrawFn) -> Codelist:
    """A Codelist with unique code ids."""
    return Codelist(
        id=draw(ncname_ids()),
        maintainer=draw(agency_ids()),
        version=draw(versions()),
        name=draw(names()),
        description=draw(international_strings()),
        codes=ItemList(draw(codes())),
        annotations=draw(annotations()),
    )


@st.composite
def concept_schemes(draw: st.DrawFn) -> ConceptScheme:
    """A ConceptScheme with unique concept ids.

    Concept ids are ``NCNameIDType``, not the looser ``IDType`` that Code ids
    use — verified against the vendored XSDs: a Concept id of ``0LEAD`` or
    ``A@B`` fails schema validation while the same Code id passes. Concept ids
    become component names in structure-specific schemas, hence the restriction.
    """
    ids = draw(st.lists(ncname_ids(), max_size=5, unique=True))
    # Concepts are Items too — same Name requirement as Code.
    concepts = [Concept(id=cid, name=draw(names())) for cid in ids]
    return ConceptScheme(
        id=draw(ncname_ids()),
        maintainer=draw(agency_ids()),
        version=draw(versions()),
        name=draw(names()),
        concepts=ItemList(concepts),
    )


#: Item ``uri``s the forest strategies draw from: two that collide often enough
#: for an ``==`` filter to hit a row, and None, so ``is_null`` has rows on both
#: sides (#444; CLAUDE.md's rule 4 for query strategies).
_URIS = [None, "https://example.org/a", "https://example.org/b/c"]


@st.composite
def code_forests(draw: st.DrawFn, *, max_size: int = 8) -> list[Code]:
    """Codes linked by ``parent_id`` into a forest — never a cycle.

    Built by only ever pointing a code at one drawn *earlier* in the list, so
    acyclicity is structural rather than something to filter for. The storage
    writer bakes ``depth`` and ``ancestor_ids`` from these links, which is what
    the depth and ancestry predicates read.

    Args:
        draw: Hypothesis draw function.
        max_size: Maximum number of codes.

    Returns:
        Codes in an order where every parent precedes its children.
    """
    ids = draw(st.lists(sdmx_ids(min_size=1, max_size=4), min_size=1, max_size=max_size, unique=True))
    codes: list[Code] = []
    for position, code_id in enumerate(ids):
        parent = draw(st.sampled_from([None, *ids[:position]])) if position else None
        codes.append(
            Code(
                id=code_id,
                name=draw(names()),
                parent_id=parent,
                annotations=draw(annotations(max_size=1)),
                uri=draw(st.sampled_from(_URIS)),
            )
        )
    return codes


@st.composite
def forest_codelists(draw: st.DrawFn) -> Codelist:
    """A Codelist whose codes form a parent/child forest."""
    return Codelist(
        id=draw(ncname_ids()),
        maintainer=draw(agency_ids()),
        version=draw(versions()),
        name=draw(names()),
        codes=ItemList(draw(code_forests())),
    )


#: Annotation types `annotated_forest_codelists` and `forest_hierarchies` draw
#: from. A tiny closed vocabulary so a generated filter on
#: ``annotations[type]`` has a real chance of hitting a row — a type drawn from
#: `sdmx_ids` would almost never collide with the one the filter asks for, and
#: the property would spend every example confirming that the empty set equals
#: the empty set.
_ANNOTATION_TYPES = ["geo_level", "note"]

#: Level ids assigned by node depth in the hierarchy strategies, so
#: ``node.level`` correlates with tree position and the level predicates have
#: something to discriminate on. Nodes deeper than this get no level, which
#: keeps the ``level.is_null()`` path reachable.
_HIERARCHY_LEVEL_IDS = ["Region", "Country", "Subnational"]


@st.composite
def annotated_forest_codelists(draw: st.DrawFn) -> Codelist:
    """A forest codelist whose codes carry annotations from a closed vocabulary.

    `forest_codelists` draws annotation *types* from `sdmx_ids`, which is right
    for round-trip properties — an arbitrary id must survive serialisation —
    and useless for query properties, where the filter has to name a type that
    exists. This one narrows the vocabulary so the annotation predicates have
    something to match, and allows repeated types on one code because that is
    legal SDMX (and the case #254 gets wrong).

    Args:
        draw: Hypothesis draw function.

    Returns:
        A Codelist whose codes form a parent/child forest and carry annotations.
    """
    base = draw(code_forests())
    codes = [
        Code(
            id=c.id,
            name=c.name,
            parent_id=c.parent_id,
            annotations=Annotations(
                draw(
                    st.lists(
                        st.builds(
                            Annotation,
                            type=st.sampled_from(_ANNOTATION_TYPES),
                            title=st.one_of(st.none(), _XML_SAFE_TEXT),
                            text=international_strings(),
                        ),
                        max_size=2,
                    )
                )
            ),
        )
        for c in base
    ]
    return Codelist(
        id=draw(ncname_ids()),
        maintainer=draw(agency_ids()),
        version=draw(versions()),
        name=draw(names()),
        codes=ItemList(codes),
    )


@st.composite
def codelist_with_hierarchy(draw: st.DrawFn) -> tuple[Codelist, Hierarchy]:
    """A Codelist and a Hierarchy whose nodes reference its codes.

    `code_query`'s hierarchy-aware predicates — ``code.hierarchy(h)``,
    ``.level(...)``, ``code.descendants_of(id, hierarchy=h)`` — resolve
    membership by matching ``hierarchy_node.code_urn`` against ``code.code_urn``
    rather than assuming ``node_id == code_id``. Testing them needs both
    artefacts, consistently linked, in one draw.

    Two structural choices make the predicates discriminating:

    - **The hierarchy covers a subset of the codelist**, so ``code.hierarchy(h)``
      has codes to exclude. A hierarchy over every code would make the
      predicate indistinguishable from "everything".
    - **The hierarchy's tree is re-parented**, not a copy of the codelist's
      intrinsic ``parent_id`` forest. That is the whole reason the two graphs
      are separate in SDMX, and a test where they coincide cannot tell
      ``descendants_of(x)`` from ``descendants_of(x, hierarchy=h)``.
    - **Node ids differ from the code ids they reference.** SDMX permits them
      to coincide and in practice they usually do, which is why the compiler
      resolves membership through ``code_urn`` and says so in a comment. A
      strategy that let them coincide would make a compiler that *did* assume
      ``node_id == code_id`` pass.

    Args:
        draw: Hypothesis draw function.

    Returns:
        ``(codelist, hierarchy)``, the hierarchy's node code refs pointing into
        the codelist.
    """
    cl = draw(forest_codelists())
    ids = [c.id for c in cl.codes]
    covered = draw(st.lists(st.sampled_from(ids), min_size=1, max_size=len(ids), unique=True))
    levels = [Level(id=lid, name=InternationalString(en=lid)) for lid in _HIERARCHY_LEVEL_IDS]

    def code_ref(code_id: str) -> Ref[Code]:
        return Ref[Code].unresolved(
            SdmxUrn.make(
                Code,
                package="codelist",
                artefact_class="Code",
                agency=cl.agency_id,
                id=cl.id,
                version=cl.version,
                item_id=code_id,
            )
        )

    # A single spine: each covered code becomes a child of the previous one, so
    # the hierarchy's ancestry is deterministic and unrelated to the codelist's.
    def spine(remaining: list[str], depth: int) -> list[HierarchicalCode]:
        if not remaining:
            return []
        head, *tail = remaining
        return [
            HierarchicalCode(
                id=f"N{depth}",
                code=code_ref(head),
                level=levels[depth] if depth < len(levels) else None,
                children=ItemList(spine(tail, depth + 1)),
            )
        ]

    h = Hierarchy(
        id=draw(ncname_ids()),
        maintainer=draw(agency_ids()),
        version=draw(versions()),
        name=draw(names()),
        has_formal_levels=True,
        levels=ItemList(levels),
        tree=ItemList(spine(covered, 0)),
    )
    return cl, h


@st.composite
def enumeration_facets(draw: st.DrawFn) -> Facet:
    """A facet valid as an ``<EnumerationFormat>``.

    ``CodedTextFormatType`` is a *restriction* of the general text format: it
    prohibits ``decimals`` and narrows the value bounds from ``xs:decimal`` to
    ``xs:integer``. So a general `facets()` draw is not usable here — it
    produces documents every SDMX-ML version rejects, which is exactly what the
    round-trip properties reported when the pairing was first generated.
    """
    return Facet(
        type=draw(st.sampled_from(["Alpha", "AlphaNumeric", "Numeric", "String", "Integer"])),
        min_length=draw(st.one_of(st.none(), st.integers(min_value=1, max_value=8))),
        max_length=draw(st.one_of(st.none(), st.integers(min_value=1, max_value=64))),
        min_value=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=10))),
        max_value=draw(st.one_of(st.none(), st.integers(min_value=11, max_value=99))),
        pattern=draw(st.one_of(st.none(), st.just("[A-Z]+"))),
        is_sequence=draw(st.one_of(st.none(), st.booleans())),
    )


def _draw_dimension(draw: st.DrawFn, dimension_id: str) -> Dimension:
    """A dimension, sometimes enumerated and sometimes with a code format.

    The enumerated branch is what exercises `enumeration_format`: it is only
    legal beside an ``<Enumeration>``, never beside a ``<TextFormat>``.
    """
    if draw(st.booleans()):
        codelist = Ref[Codelist].unresolved(Codelist.urn_for(agency="TEST", id=f"CL_{dimension_id}", version="1.0"))
        return Dimension(
            id=dimension_id,
            concept=_concept_ref_for(dimension_id),
            representation=codelist,
            enumeration_format=draw(st.one_of(st.none(), enumeration_facets())),
            annotations=draw(annotations(max_size=1)),
        )
    return Dimension(
        id=dimension_id,
        concept=_concept_ref_for(dimension_id),
        representation=draw(facets(for_component=True)),
        annotations=draw(annotations(max_size=1)),
    )


def _concept_ref_for(component_id: str) -> Ref[Concept]:
    """A ConceptIdentity for a component.

    Every SDMX wire format *requires* `ConceptIdentity` on a component — 2.1,
    3.0, 3.1 and SDMX-JSON all reject a DSD without it. The model makes
    `concept` optional and no writer supplies one, so a DSD built without
    concepts cannot be validly serialised in any format (#224). These
    strategies therefore always supply one; the library gap is tracked
    separately rather than papered over by never generating the valid case.
    """
    return concept_ref(component_id, agency="TEST", scheme_id="CS")


@st.composite
def data_structures(draw: st.DrawFn) -> DataStructure:
    """A schema-conformant DataStructure: dimensions, measures, attributes.

    Component ids are unique *across* dimensions, measures and attributes, not
    merely within each list — the XSD enforces a DSD-wide unique-component
    identity constraint, so drawing the three lists independently produced
    documents that failed validation for a reason unrelated to the property.

    The primary measure is ``OBS_VALUE``: SDMX-ML 2.1 fixes that value in the
    schema. SDMX 3.0 permits arbitrary measure ids, so a 3.0 DSD carrying one
    cannot be downgraded to 2.1 — also #224.
    """
    all_ids = draw(st.lists(ncname_ids(max_size=6), min_size=1, max_size=6, unique=True))
    dim_ids = all_ids[: max(1, len(all_ids) // 2)]
    attr_ids = [i for i in all_ids[len(dim_ids) :] if i != "OBS_VALUE"]

    # Some dimensions are *enumerated* with a format alongside — the pairing
    # `RepresentationType` allows, and which nothing generated before #243.
    # That gap is why a whole format's worth of dropped EnumerationFormats
    # went unnoticed by the generative properties.
    #
    # The format may only accompany an enumeration: RepresentationType is a
    # choice of TextFormat or Enumeration+EnumerationFormat, so pairing it
    # with a free-text facet produces a document every schema rejects.
    dims = [_draw_dimension(draw, d) for d in dim_ids]
    attrs = [
        Attribute(
            id=a,
            concept=_concept_ref_for(a),
            representation=draw(facets(for_component=True)),
            # Every format requires a relationship, and refuses an attribute
            # without one (#421); these two need no other component.
            attachment=draw(st.sampled_from([AttachmentLevel.DATASET, AttachmentLevel.OBSERVATION])),
        )
        for a in attr_ids
    ]
    meas = [
        Measure(
            id="OBS_VALUE",
            concept=_concept_ref_for("OBS_VALUE"),
            representation=draw(facets(for_component=True)),
        )
    ]

    return DataStructure(
        id=draw(ncname_ids()),
        maintainer=draw(agency_ids()),
        version=draw(versions()),
        name=draw(names()),
        description=draw(international_strings()),
        dimensions=ItemList(dims),
        measures=ItemList(meas),
        attributes=ItemList(attrs),
        annotations=draw(annotations()),
    )


@st.composite
def dataflows(draw: st.DrawFn, *, structure: DataStructure | None = None) -> Dataflow:
    """A Dataflow referencing a DataStructure.

    The ref is unresolved: a round-trip carries the URN, not the target, so
    resolving it here would compare something the wire never sees.
    """
    dsd = structure if structure is not None else draw(data_structures())
    return Dataflow(
        id=draw(ncname_ids()),
        maintainer=draw(agency_ids()),
        version=draw(versions()),
        name=draw(names()),
        description=draw(international_strings()),
        structure=Ref[DataStructure].unresolved(
            DataStructure.urn_for(agency=dsd.agency_id, id=dsd.id, version=dsd.version)
        ),
        annotations=draw(annotations()),
    )


@st.composite
def contacts(draw: st.DrawFn, *, max_size: int = 2) -> list[Contact]:
    """Contacts with their multi-valued sub-fields.

    Every one of ``emails`` / ``telephones`` / ``faxes`` / ``uris`` carries a
    ``minItems: 1`` constraint in the SDMX-JSON schema, so an empty tuple must
    be omitted rather than emitted — the same class of bug #216 found for
    `concepts` and `categories`. Drawing them non-empty-or-absent exercises
    both sides.
    """
    return [
        Contact(
            name=draw(names()),
            department=draw(international_strings()),
            role=draw(international_strings()),
            emails=tuple(draw(st.lists(st.just("a@example.org"), max_size=2))),
            phones=tuple(draw(st.lists(st.just("+1 555 0100"), max_size=2))),
            faxes=tuple(draw(st.lists(st.just("+1 555 0101"), max_size=2))),
            uris=tuple(draw(st.lists(st.just("https://example.org"), max_size=2))),
        )
        for _ in range(draw(st.integers(min_value=0, max_value=max_size)))
    ]


@st.composite
def agency_schemes(draw: st.DrawFn) -> AgencyScheme:
    """An AgencyScheme whose agencies carry contacts.

    Agency ids are ``NestedNCNameIDType`` like any other agency id.
    """
    ids = draw(st.lists(ncname_ids(max_size=6), min_size=1, max_size=3, unique=True))
    # The XSD pins both: `id` to AGENCIES in every version, and `version` to
    # "1.0" in 2.1. Drawing either freely produces documents that fail schema
    # validation for a reason unrelated to the property (#223).
    return AgencyScheme(
        id="AGENCIES",
        maintainer=draw(agency_ids()),
        version="1.0",
        name=draw(names()),
        agencies=ItemList([Agency(id=a, name=draw(names()), contacts=tuple(draw(contacts()))) for a in ids]),
    )


@st.composite
def hierarchies(draw: st.DrawFn) -> Hierarchy:
    """A Hierarchy over a generated codelist, with a nested tree.

    The tree is built from a `code_forests` draw so parent/child links are
    acyclic by construction, then reshaped into the nested `HierarchicalCode`
    form the artefact uses.
    """
    codelist_id = draw(ncname_ids())
    agency = draw(agency_ids())
    forest = draw(code_forests(max_size=5))
    by_parent: dict[str | None, list[str]] = {}
    for c in forest:
        by_parent.setdefault(c.parent_id, []).append(c.id)

    def build(code_id: str) -> HierarchicalCode:
        return HierarchicalCode(
            id=code_id,
            code=Ref[Code].unresolved(
                SdmxUrn.make(
                    Code,
                    package="codelist",
                    artefact_class="Code",
                    agency=agency,
                    id=codelist_id,
                    version="1.0",
                    item_id=code_id,
                )
            ),
            children=ItemList([build(child) for child in by_parent.get(code_id, [])]),
        )

    return Hierarchy(
        id=draw(ncname_ids()),
        maintainer=agency,
        version=draw(versions()),
        name=draw(names()),
        tree=ItemList([build(root) for root in by_parent.get(None, [])]),
    )


def _place_again(draw: st.DrawFn, ids: list[str], by_parent: dict[str | None, list[str]]) -> None:
    """Place up to two nodes a second time, under another parent (#439).

    A node id may repeat in another branch — OECD puts ``MR`` at a root and
    under ``TYPO_METRO``, BIS numbers its nodes per level — and a strategy
    whose ids are all distinct never asks a compiler or a store what a repeat
    means. The repeat carries its subtree with it, as a real one does.

    Never inside its own subtree, which would make the tree infinite, and never
    beside a sibling of the same id, which no SDMX version allows.
    """

    def subtree(node_id: str) -> set[str]:
        found, stack = set(), [node_id]
        while stack:
            current = stack.pop()
            if current not in found:
                found.add(current)
                stack.extend(by_parent.get(current, []))
        return found

    if len(ids) < 2:
        return
    for _ in range(draw(st.integers(min_value=0, max_value=2), label="repeats")):
        moved = draw(st.sampled_from(ids), label="repeated node")
        parent = draw(st.sampled_from(ids), label="second parent")
        if parent in subtree(moved) or moved in by_parent.get(parent, []):
            continue
        by_parent.setdefault(parent, []).append(moved)


@st.composite
def forest_hierarchies(draw: st.DrawFn) -> Hierarchy:
    """A Hierarchy whose nodes carry every field the query DSL can filter on.

    `hierarchies` generates the artefact shape — a nested tree of ids and code
    refs — which is what the round-trip properties need. The query DSL can
    additionally filter on ``name[lang]``, ``level``, ``code`` and
    ``annotations[type][lang]``, and a strategy that leaves those unset makes
    every predicate over them trivially empty. This one populates them.

    Two modelling choices worth stating, because they bound what the
    differential properties can find:

    - **Levels are assigned by depth**, from a closed three-id vocabulary.
      Nodes below the third level get ``None``, so ``level.is_null()`` is
      reachable. In a leveled SDMX hierarchy level and depth do correlate, so
      this is realistic rather than merely convenient.
    - **Annotation types come from a two-element vocabulary** and a node may
      carry more than one annotation of the same type. That is legal SDMX and
      it is the case an inner JOIN handles badly, so it is left in deliberately
      rather than generated away.

    Args:
        draw: Hypothesis draw function.

    Returns:
        A Hierarchy with a populated tree, formal levels, and node annotations.
    """
    codelist_id = draw(ncname_ids())
    agency = draw(agency_ids())
    forest = draw(code_forests(max_size=6))
    levels = [Level(id=lid, name=InternationalString(en=lid)) for lid in _HIERARCHY_LEVEL_IDS]

    by_parent: dict[str | None, list[str]] = {}
    for c in forest:
        by_parent.setdefault(c.parent_id, []).append(c.id)
    _place_again(draw, [c.id for c in forest], by_parent)

    def node_annotations() -> Annotations:
        items = draw(
            st.lists(
                st.builds(
                    Annotation,
                    type=st.sampled_from(_ANNOTATION_TYPES),
                    title=st.one_of(st.none(), _XML_SAFE_TEXT),
                    text=international_strings(),
                ),
                max_size=2,
            )
        )
        return Annotations(items)

    def build(code_id: str, depth: int) -> HierarchicalCode:
        return HierarchicalCode(
            id=code_id,
            code=Ref[Code].unresolved(
                SdmxUrn.make(
                    Code,
                    package="codelist",
                    artefact_class="Code",
                    agency=agency,
                    id=codelist_id,
                    version="1.0",
                    item_id=code_id,
                )
            ),
            level=levels[depth] if depth < len(levels) else None,
            annotations=node_annotations(),
            uri=draw(st.sampled_from(_URIS)),
            children=ItemList([build(child, depth + 1) for child in by_parent.get(code_id, [])]),
        )

    return Hierarchy(
        id=draw(ncname_ids()),
        maintainer=agency,
        version=draw(versions()),
        name=draw(names()),
        has_formal_levels=True,
        levels=ItemList(levels),
        tree=ItemList([build(root, 0) for root in by_parent.get(None, [])]),
    )


@st.composite
def category_schemes(draw: st.DrawFn) -> CategoryScheme:
    """A CategoryScheme with nested categories.

    Categories nest recursively, so this exercises the same shape as
    `hierarchies` on the category side. Category ids are `IDType` — the
    permissive form, like Code and unlike Concept.
    """
    ids = draw(st.lists(sdmx_ids(max_size=5), min_size=1, max_size=3, unique=True))

    def category(cid: str, depth: int) -> Category:
        children: list[str] = draw(st.lists(sdmx_ids(max_size=4), max_size=1, unique=True)) if depth else []
        return Category(
            id=cid,
            name=draw(names()),
            categories=ItemList([category(f"{cid}_{c}", 0) for c in children]),
        )

    return CategoryScheme(
        id=draw(ncname_ids()),
        maintainer=draw(agency_ids()),
        version=draw(versions()),
        name=draw(names()),
        categories=ItemList([category(i, 1) for i in ids]),
    )


@st.composite
def categorisations(draw: st.DrawFn) -> Categorisation:
    """A Categorisation linking a Dataflow to a Category.

    Both ends are unresolved refs: a round-trip carries URNs, not targets.
    """
    agency = draw(agency_ids())
    return Categorisation(
        id=draw(ncname_ids()),
        maintainer=agency,
        version=draw(versions()),
        name=draw(names()),
        source=Ref[Dataflow].unresolved(Dataflow.urn_for(agency=agency, id=draw(ncname_ids()), version="1.0")),
        target=Ref[Category].unresolved(
            SdmxUrn.make(
                Category,
                package="categoryscheme",
                artefact_class="Category",
                agency=agency,
                id=draw(ncname_ids()),
                version="1.0",
                item_id=draw(sdmx_ids(max_size=5)),
            )
        ),
    )


@st.composite
def provision_agreements(draw: st.DrawFn) -> ProvisionAgreement:
    """A ProvisionAgreement binding a data provider to a dataflow.

    Both edges are always drawn. The model makes them optional, but every
    SDMX-ML schema requires both children, so an agreement missing either is
    not serialisable — a rule `validation._check_provision_agreement` now
    enforces, discovered by this strategy generating one without a provider.
    """
    agency = draw(agency_ids())
    provider = SdmxUrn.make(
        DataProvider,
        package="base",
        artefact_class="DataProvider",
        agency=agency,
        id="DATA_PROVIDERS",
        version="1.0",
        item_id=draw(ncname_ids()),
    )
    return ProvisionAgreement(
        id=draw(ncname_ids()),
        maintainer=agency,
        version=draw(versions()),
        name=draw(names()),
        dataflow=Ref[Dataflow].unresolved(Dataflow.urn_for(agency=agency, id=draw(ncname_ids()), version="1.0")),
        data_provider=Ref[DataProvider].unresolved(provider),
    )


@st.composite
def metadata_structures(draw: st.DrawFn) -> MetadataStructure:
    """An MSD with a flat list of metadata attributes.

    ``min_occurs`` / ``max_occurs`` are drawn so the cardinality pair stays
    coherent — an attribute with min above max is not a thing the schema can
    express, and generating it would fail for a reason unrelated to the
    property.
    """
    ids = draw(st.lists(ncname_ids(max_size=6), min_size=1, max_size=3, unique=True))
    attributes = []
    for aid in ids:
        minimum = draw(st.integers(min_value=0, max_value=1))
        attributes.append(
            MetadataAttribute(
                id=aid,
                # Required by every format, exactly as for a DSD component —
                # see `_concept_ref_for`. SDMX-JSON's MetadataAttributeType
                # lists `conceptIdentity` in `required`.
                concept=_concept_ref_for(aid),
                representation=draw(facets(for_component=True)),
                min_occurs=minimum,
                max_occurs=draw(st.one_of(st.none(), st.integers(min_value=minimum or 1, max_value=3))),
            )
        )
    # The 2.1 reporting layer is always generated. SDMX 3.0 removed it and the
    # 3.x/JSON writers ignore it, but 2.1 requires it — an MSD without one has
    # no 2.1 form at all, so leaving it out would make every 2.1 case a
    # refusal and exercise none of the writing (#245).
    target = MetadataTarget(
        id=draw(ncname_ids(max_size=6)),
        target_objects=[
            TargetObject(
                id=draw(ncname_ids(max_size=6)),
                kind="IdentifiableObjectTarget",
                object_type="Dataflow",
                # TargetObjectTextFormatType restricts textType to four values,
                # and `IdentifiableReference` is the one an
                # IdentifiableObjectTarget takes.
                representation=Facet(type="IdentifiableReference"),
            )
        ],
    )
    return MetadataStructure(
        id=draw(ncname_ids()),
        maintainer=draw(agency_ids()),
        version=draw(versions()),
        name=draw(names()),
        attributes=ItemList(attributes),
        targets=(target,),
        report_structures=(
            ReportStructure(
                id=draw(ncname_ids(max_size=6)),
                attribute_ids=tuple(a.id for a in attributes),
                target_ids=(target.id,),
            ),
        ),
    )


@st.composite
def metadataflows(draw: st.DrawFn) -> Metadataflow:
    """A Metadataflow referencing an MSD, with at least one target.

    ``targets`` is always non-empty: `MetadataflowType` in both 3.0 and 3.1
    declares ``<Target>`` with ``minOccurs=1``, so a metadataflow with none has
    no valid serialisation. The model defaults the field to ``()``, which
    `validation` now rejects at write time (#225).
    """
    agency = draw(agency_ids())
    targets = draw(
        st.lists(
            ncname_ids(max_size=6).map(lambda i: str(Dataflow.urn_for(agency=agency, id=i, version="1.0"))),
            min_size=1,
            max_size=2,
            unique=True,
        )
    )
    return Metadataflow(
        targets=tuple(targets),
        id=draw(ncname_ids()),
        maintainer=agency,
        version=draw(versions()),
        name=draw(names()),
        structure=Ref[MetadataStructure].unresolved(
            MetadataStructure.urn_for(agency=agency, id=draw(ncname_ids()), version="1.0")
        ),
    )


# ── Constraints ──────────────────────────────────────────────────────────────


@st.composite
def cube_regions(draw: st.DrawFn, *, include: bool | None = None) -> CubeRegion:
    """A cube region selecting values on one or more dimensions.

    ``keys`` is non-empty: a region selecting nothing is degenerate, and the
    interesting wire structure is the ``<com:KeyValue>`` list.

    Args:
        draw: Hypothesis draw function.
        include: Pin the inclusion flag, so a caller can build a set of
            regions with distinct values — 3.0 and 3.1 require it.

    Returns:
        The CubeRegion.
    """
    dimension_ids = draw(st.lists(ncname_ids(max_size=6), min_size=1, max_size=3, unique=True))
    keys = [
        KeyValue(
            dimension_id=dimension_id,
            values=frozenset(draw(st.lists(sdmx_ids(max_size=6), min_size=1, max_size=3, unique=True))),
        )
        for dimension_id in dimension_ids
    ]
    inclusion = draw(st.booleans()) if include is None else include
    return CubeRegion(include=inclusion, keys=tuple(keys))


@st.composite
def data_constraints(draw: st.DrawFn) -> DataConstraint:
    """A DataConstraint attached to a dataflow, carrying cube regions.

    The attachment is always a dataflow. `ConstraintAttachment` models four
    mutually exclusive targets and picking among them here would test the
    strategy's dispatch rather than the writer's — the other three are worth a
    property of their own if one is wanted.
    """
    agency = draw(agency_ids())
    return DataConstraint(
        id=draw(ncname_ids()),
        maintainer=agency,
        version=draw(versions()),
        name=draw(names()),
        constraint_type=draw(st.sampled_from(list(ConstraintType))),
        attachment=ConstraintAttachment(
            dataflows=(Ref[Dataflow].unresolved(Dataflow.urn_for(agency=agency, id=draw(ncname_ids()), version="1.0")),)
        ),
        # At most one region per `include` value: 3.0/3.1 declare an
        # xs:unique identity constraint over CubeRegion/@include, so drawing
        # two independent booleans generated a schema-invalid artefact half
        # the time. Enforced by `validation._check_cube_region_inclusion`.
        regions=tuple(
            draw(cube_regions(include=inclusion))
            for inclusion in draw(st.sampled_from([(True,), (False,), (True, False)]))
        ),
        # A constraint may select by whole key as well as by region —
        # `DataConstraintType` is `DataKeySet | CubeRegion`. Modelling only the
        # region meant a document carrying a key set lost it (#241), and no
        # generated constraint had one.
        # Each set included or excluded: an excluded one read as allowed (#479).
        data_key_sets=tuple(draw(st.lists(_data_key_sets(), max_size=2))),
    )


@st.composite
def _data_key_sets(draw: st.DrawFn) -> DataKeySet:
    """A key set, included or excluded, of one or two keys: every format requires a key."""
    return DataKeySet(is_included=draw(st.booleans()), keys=tuple(draw(st.lists(_data_keys(), min_size=1, max_size=2))))


@st.composite
def _data_keys(draw: st.DrawFn) -> DataKey:
    """One explicit observation key: a value per named dimension."""
    dimension_ids = draw(st.lists(ncname_ids(max_size=6), min_size=1, max_size=3, unique=True))
    return DataKey(values=tuple((d, draw(sdmx_ids(max_size=6))) for d in dimension_ids))


@st.composite
def attachment_constraints(draw: st.DrawFn) -> AttachmentConstraint:
    """An AttachmentConstraint carrying explicit data keys.

    Distinct from `data_constraints` in what it selects: a key *set* naming
    whole observations, rather than per-dimension value regions.
    """
    agency = draw(agency_ids())
    dimension_ids = draw(st.lists(ncname_ids(max_size=6), min_size=1, max_size=2, unique=True))
    keys = draw(
        st.lists(
            st.builds(
                DataKey,
                values=st.tuples(*(st.tuples(st.just(d), sdmx_ids(max_size=6)) for d in dimension_ids)),
            ),
            min_size=1,
            max_size=2,
        )
    )
    return AttachmentConstraint(
        id=draw(ncname_ids()),
        maintainer=agency,
        version=draw(versions()),
        name=draw(names()),
        attachment=ConstraintAttachment(
            dataflows=(Ref[Dataflow].unresolved(Dataflow.urn_for(agency=agency, id=draw(ncname_ids()), version="1.0")),)
        ),
        data_key_sets=(DataKeySet(is_included=draw(st.booleans()), keys=tuple(keys)),),
    )


# ── Maps ─────────────────────────────────────────────────────────────────────


@st.composite
def representation_maps(draw: st.DrawFn) -> RepresentationMap:
    """A RepresentationMap between two codelists, with literal 1:1 mappings — the shape 2.1 can write."""
    agency = draw(agency_ids())
    maps = draw(
        st.lists(
            st.builds(
                RepresentationMapping,
                sources=st.tuples(sdmx_ids(max_size=6)),
                targets=st.tuples(sdmx_ids(max_size=6)),
            ),
            min_size=1,
            max_size=3,
        )
    )
    return RepresentationMap(
        id=draw(ncname_ids()),
        maintainer=agency,
        version=draw(versions()),
        name=draw(names()),
        sources=(Ref[Codelist].unresolved(Codelist.urn_for(agency=agency, id=draw(ncname_ids()), version="1.0")),),
        targets=(Ref[Codelist].unresolved(Codelist.urn_for(agency=agency, id=draw(ncname_ids()), version="1.0")),),
        mappings=tuple(maps),
    )


@st.composite
def structure_maps(draw: st.DrawFn) -> StructureMap:
    """A StructureMap pairing components across two DSDs."""
    agency = draw(agency_ids())
    pairs = draw(st.lists(st.tuples(ncname_ids(max_size=6), ncname_ids(max_size=6)), min_size=1, max_size=3))
    return StructureMap(
        id=draw(ncname_ids()),
        maintainer=agency,
        version=draw(versions()),
        name=draw(names()),
        source=Ref[DataStructure].unresolved(
            DataStructure.urn_for(agency=agency, id=draw(ncname_ids()), version="1.0")
        ),
        target=Ref[DataStructure].unresolved(
            DataStructure.urn_for(agency=agency, id=draw(ncname_ids()), version="1.0")
        ),
        maps=tuple(ComponentMap(sources=(s,), targets=(t,)) for s, t in pairs),
    )


@st.composite
def concept_scheme_maps(draw: st.DrawFn) -> ConceptSchemeMap:
    """A ConceptSchemeMap pairing concepts across two schemes."""
    agency = draw(agency_ids())
    pairs = draw(st.lists(st.tuples(ncname_ids(max_size=6), ncname_ids(max_size=6)), min_size=1, max_size=3))
    return ConceptSchemeMap(
        id=draw(ncname_ids()),
        maintainer=agency,
        version=draw(versions()),
        name=draw(names()),
        source=Ref[ConceptScheme].unresolved(
            ConceptScheme.urn_for(agency=agency, id=draw(ncname_ids()), version="1.0")
        ),
        target=Ref[ConceptScheme].unresolved(
            ConceptScheme.urn_for(agency=agency, id=draw(ncname_ids()), version="1.0")
        ),
        maps=tuple(ItemMap(source_id=s, target_id=t) for s, t in pairs),
    )


@st.composite
def organisation_scheme_maps(draw: st.DrawFn) -> OrganisationSchemeMap:
    """An OrganisationSchemeMap pairing agencies with data providers: ends of different kinds (#448)."""
    agency = draw(agency_ids())
    pairs = draw(st.lists(st.tuples(ncname_ids(max_size=6), ncname_ids(max_size=6)), min_size=1, max_size=3))
    return OrganisationSchemeMap(
        id=draw(ncname_ids()),
        maintainer=agency,
        version=draw(versions()),
        name=draw(names()),
        source=Ref[AgencyScheme].unresolved(AgencyScheme.urn_for(agency=agency, id="AGENCIES", version="1.0")),
        target=Ref[DataProviderScheme].unresolved(
            DataProviderScheme.urn_for(agency=agency, id="DATA_PROVIDERS", version="1.0")
        ),
        maps=tuple(ItemMap(source_id=s, target_id=t) for s, t in pairs),
    )


@st.composite
def category_scheme_maps(draw: st.DrawFn) -> CategorySchemeMap:
    """A CategorySchemeMap pairing categories across two schemes."""
    agency = draw(agency_ids())
    pairs = draw(st.lists(st.tuples(ncname_ids(max_size=6), ncname_ids(max_size=6)), min_size=1, max_size=3))
    return CategorySchemeMap(
        id=draw(ncname_ids()),
        maintainer=agency,
        version=draw(versions()),
        name=draw(names()),
        source=Ref[CategoryScheme].unresolved(
            CategoryScheme.urn_for(agency=agency, id=draw(ncname_ids()), version="1.0")
        ),
        target=Ref[CategoryScheme].unresolved(
            CategoryScheme.urn_for(agency=agency, id=draw(ncname_ids()), version="1.0")
        ),
        maps=tuple(ItemMap(source_id=s, target_id=t) for s, t in pairs),
    )


# ── Organisations & hierarchy association ────────────────────────────────────


@st.composite
def data_provider_schemes(draw: st.DrawFn) -> DataProviderScheme:
    """A DataProviderScheme, id pinned as the schema requires.

    ``DataProviderSchemeType/@id`` is fixed to ``DATA_PROVIDERS`` and 2.1 pins
    the version to ``1.0`` as well — the same pair of constraints that
    `agency_schemes` carries, both enforced by `validation._FIXED_SCHEME_IDS`.
    """
    providers = draw(
        st.lists(
            st.builds(DataProvider, id=ncname_ids(max_size=6), name=names()),
            min_size=1,
            max_size=3,
            unique_by=lambda p: p.id,
        )
    )
    return DataProviderScheme(
        id="DATA_PROVIDERS",
        maintainer=draw(agency_ids()),
        version="1.0",
        name=draw(names()),
        providers=ItemList(providers),
    )


@st.composite
def hierarchy_associations(draw: st.DrawFn) -> HierarchyAssociation:
    """A HierarchyAssociation linking a Hierarchy to a dataflow."""
    agency = draw(agency_ids())
    return HierarchyAssociation(
        id=draw(ncname_ids()),
        maintainer=agency,
        version=draw(versions()),
        name=draw(names()),
        linked_hierarchy=Ref[Hierarchy].unresolved(
            Hierarchy.urn_for(agency=agency, id=draw(ncname_ids()), version="1.0")
        ),
        linked_object=Ref[Dataflow].unresolved(Dataflow.urn_for(agency=agency, id=draw(ncname_ids()), version="1.0")),
    )


# ── Reference metadata ───────────────────────────────────────────────────────


@st.composite
def reported_attributes(draw: st.DrawFn, *, depth: int = 2) -> list[ReportedAttribute]:
    """A list of reported attributes, possibly nested and possibly repeated.

    Two shapes the model is explicit about and a naive strategy would miss:

    - **Repetition is sibling attributes sharing an id**, never a values list.
      So the ids drawn here are deliberately *not* unique.
    - **Children recurse**, so nesting depth is not always one.

    Args:
        draw: Hypothesis draw function.
        depth: Remaining levels of nesting to allow.

    Returns:
        One or more ReportedAttribute, in order.
    """
    ids = draw(st.lists(ncname_ids(max_size=6), min_size=1, max_size=3))
    out = []
    for aid in ids:
        children: list[ReportedAttribute] = (
            draw(reported_attributes(depth=depth - 1)) if depth > 0 and draw(st.booleans()) else []
        )
        # A presentational container carries children instead of a value; a
        # leaf carries a value. Both are legal, and mixing them is what makes
        # the tree interesting.
        value = (
            None
            if children
            else draw(
                st.one_of(
                    _XML_SAFE_TEXT,
                    st.integers(min_value=-1000, max_value=1000),
                    st.booleans(),
                    names(),
                )
            )
        )
        out.append(ReportedAttribute(id=aid, value=value, children=ItemList(children)))
    return out


@st.composite
def metadata_sets(draw: st.DrawFn) -> MetadataSet:
    """A MetadataSet reporting against one or more targets.

    ``targets`` is always non-empty and ``version`` is always semver, because
    neither is optional on the wire:

    - ``MetadataSetType`` sequences choice(flow|provision), ``Target`` (1..n),
      ``Attribute`` (1..n), so a set naming a structure must say what the
      metadata is *about*;
    - SDMX-JSON's *metadata* schema pins ``VersionType`` to strict semantic
      versioning, where its *structure* schema states no pattern at all — so
      the model default of ``"1.0"`` has no valid JSON form.

    Both are enforced by `validation.check_metadata_serialisable` (#234).
    """
    agency = draw(agency_ids())
    version = ".".join(str(draw(st.integers(min_value=0, max_value=20))) for _ in range(3))
    targets = draw(
        st.lists(
            # Versioned deliberately: an SdmxUrn defaults to version "latest",
            # which stringifies with no ``(version)`` at all, and both schemas
            # require one on a target. That sentinel has no 3.0 wire form on
            # any artefact — tracked separately, not worked around here.
            ncname_ids(max_size=6).map(
                lambda i: Ref[Codelist].unresolved(Codelist.urn_for(agency=agency, id=i, version="1.0"))
            ),
            min_size=1,
            max_size=2,
        )
    )
    return MetadataSet(
        id=draw(ncname_ids()),
        maintainer=agency,
        version=version,
        name=draw(names()),
        # Both arms of the schema's choice, not just one. `MetadataSetType`
        # sequences choice(MetadataProvisionAgreement | Metadataflow), and the
        # 3.0 writer picks its element by inspecting the referenced class — so
        # a strategy that only ever drew a Metadataflow left the other branch
        # of that `if` ungenerated. Drawing only Metadataflow is also what hid
        # #232: an MSD reference was silently written as a <md:Metadataflow>
        # holding an MSD URN, which every schema rejects.
        structure=draw(
            st.sampled_from(
                [
                    Ref[Metadataflow].unresolved(
                        Metadataflow.urn_for(agency=agency, id=draw(ncname_ids()), version="1.0")
                    ),
                    Ref[MetadataProvisionAgreement].unresolved(
                        MetadataProvisionAgreement.urn_for(agency=agency, id=draw(ncname_ids()), version="1.0")
                    ),
                ]
            )
        ),
        targets=targets,
        attributes=ItemList(draw(reported_attributes())),
    )


#: Characters and shapes that break a query compiler if a value reaches SQL
#: unescaped, or reaches it as text rather than as a bind (#250).
#:
#: Grouped by what each one probes, because a flat list invites deletion of
#: the awkward entries:
#:
#: - ``% _`` — LIKE wildcards. `_` is the confirmed #222 defect and is
#:   *ordinary* in SDMX ids, which is why that one shipped.
#: - ``\`` — the escape character itself; escaping it wrongly is the classic
#:   second-order bug.
#: - ``[ ]`` — LIKE character classes in some dialects, not DuckDB. Present so
#:   a dialect change is caught rather than assumed.
#: - quotes, backtick, ``;``, ``--`` — string-termination and comment shapes.
#:   These should be inert: values are binds, never spliced. That is the point.
#: - newline, tab, NUL — whitespace and driver-boundary handling.
#: - ``""`` and ``" "`` — the degenerate needles. `#255`'s fold guard turns a
#:   punctuation-only needle into the empty string, which matches everything.
#: - long, combining marks, RTL, astral — normalisation and collation.
_HOSTILE_VALUES = [
    "%",
    "_",
    "%_%",
    "\\",
    "\\%",
    "[",
    "]",
    "[a-z]",
    "'",
    '"',
    "`",
    ";",
    "--",
    "' OR 1=1 --",
    "/*",
    "*/",
    "\n",
    "\t",
    "\x00",
    "",
    " ",
    "   ",
    "a" * 300,
    # Escapes, not literals: a raw RTL override and an astral codepoint in
    # source are what ruff's PLE2502 / RUF001 exist to catch, and the intent
    # reads better spelled out.
    "\u00e9",  # precomposed e-acute
    "e\u0301",  # e + combining acute: same grapheme, different bytes
    "\u202eRTL",  # right-to-left override
    "\U0001d518",  # astral plane
    "\u00df",  # sharp s: uppercases to two characters
    "\u0130",  # dotted capital I: lowercases to two characters
    ":",
    "..",
    ". ",
    "-",
    "50%",
]


@st.composite
def hostile_strings(draw: st.DrawFn) -> str:
    """A filter value chosen to break naive SQL construction (#250).

    Draws from a curated list rather than `st.text()`: the failures here are
    *specific characters in specific positions*, and random text finds them
    only by luck. The one confirmed defect in this class (#222, an unescaped
    ``_`` behaving as a wildcard) needed exactly one character to reproduce and
    would have taken a very long random search to hit.

    Occasionally embeds the hostile fragment in ordinary text, because a value
    that is *only* a metacharacter exercises a different path from one that
    merely contains it — the escaping is per-character, but the surrounding
    pattern is not.
    """
    value = draw(st.sampled_from(_HOSTILE_VALUES))
    shape = draw(st.sampled_from(["bare", "prefixed", "suffixed", "wrapped"]))
    if shape == "prefixed":
        return f"A{value}"
    if shape == "suffixed":
        return f"{value}B"
    if shape == "wrapped":
        return f"A{value}B"
    return value


#: Labels that *contain* the hostile shapes, so a wildcard leak changes which
#: rows match rather than merely which SQL is emitted.
#:
#: The data side matters as much as the needle side, and differently: SDMX ids
#: are validated to ``A-Za-z0-9_@$-``, so the only LIKE metacharacter an id can
#: hold is ``_`` — which is precisely why #222 was a real defect and not a
#: theoretical one. Labels carry no such restriction, so they are where the
#: rest of the class can actually appear in stored data.
_HOSTILE_LABELS = [
    "L_x",
    "L%x",
    "L[x",
    "L\\x",
    "L'x",
    'L"x',
    "L;x",
    "L--x",
    "Léx",
    "Lex",
    "L x",
    "L-x",
    "L.x",
    "50%",
    "50 cents",
    "Trois-Rivières",
    "Trois Rivieres",
    "St. John's",
    "",
]


@st.composite
def hostile_labelled_codelists(draw: st.DrawFn, *, max_codes: int = 8) -> Codelist:
    """A flat codelist whose *labels* hold the hostile shapes.

    Ids stay id-legal, because the validator rejects anything else and a
    fixture the library refuses to store tests nothing. The hostility lives in
    the labels, which are unconstrained on both sides.
    """
    n = draw(st.integers(min_value=1, max_value=max_codes), label="n_codes")
    labels = draw(
        st.lists(st.sampled_from(_HOSTILE_LABELS), min_size=n, max_size=n),
        label="labels",
    )
    return Codelist(
        id="CL_HOSTILE",
        maintainer="AG",
        version="1.0",
        name={"en": "hostile"},
        codes=ItemList(
            [Code(id=f"C{i}_x", name=InternationalString.of({"en": label or "x"})) for i, label in enumerate(labels)]
        ),
    )
