"""Validate domain objects against SDMX structural rules.

Three places, by what a rule's answer depends on (#492):

- **The artefact alone** — unique item ids, parents that exist, a concept on
  every component, and the rest of `_DISPATCH` — runs as the artefact is
  built: each class registers its rules with `_invariants`, and its
  constructor and `attrs.evolve` raise ``ValidationError``. A value alone
  (an id, a version, a name) is `_rules`', a field validator.
- **The message** — ``validate(msg)``: its references resolve
  (``validate_references``) and no artefact is in it twice.
- **The target format** — ``check_serialisable``, which `serialize` runs.

Example:
    import sdmxlib as sl

    try:
        sl.validate(msg)
    except sl.ReferenceValidationError as e:
        for issue in e.issues:
            print(issue)
"""

import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any

import attrs
from attrs import frozen

from sdmxlib.model._container import inner_id
from sdmxlib.model._invariants import advise, register, register_advisory
from sdmxlib.model._items import spellings
from sdmxlib.model._referenceable import CLASSES_21, ENUMERATION_PAIR_JSON, PACKAGES_21, PAIRS_30, PAIRS_31, PAIRS_JSON
from sdmxlib.model._refshape import can_reach_ref, item_fields, link_fields, walk_fields
from sdmxlib.model._rules import (
    AGENCY_PATTERN,
    ID_PATTERN,
    ID_RE,
    NCNAME_PATTERN,
    NCNAME_RE,
    XML_FORBIDDEN,
    first_xml_incompatible,
    id_problem,
    name_problem,
)
from sdmxlib.model._tree import sibling_duplicates
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.category import CategoryScheme
from sdmxlib.model.codelist import CascadeSelection, Codelist, unresolved_extensions, wildcard_misfits
from sdmxlib.model.collections import ArtefactList, ItemList, SdmxItem
from sdmxlib.model.concept import ConceptScheme
from sdmxlib.model.constraint import (
    AttachmentConstraint,
    ComponentValueSet,
    ConstraintAttachment,
    ConstraintType,
    DataConstraint,
    DataKeySet,
    KeyValue,
)
from sdmxlib.model.datastructure import (
    REPORTING_YEAR_START_DAY,
    AttachmentLevel,
    Attribute,
    DataStructure,
    MeasureDimension,
    MetadataAttributeUsage,
)
from sdmxlib.model.errors import ReadAssumption, ReadGap, Refusal, SdmxParseError, ValidationError, ValidationIssue
from sdmxlib.model.hierarchy import HierarchicalCodelist, Hierarchy
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link
from sdmxlib.model.mapping import (
    CategorySchemeMap,
    ConceptSchemeMap,
    DatePatternMap,
    EpochMap,
    MapDataType,
    OrganisationSchemeMap,
    RepresentationMap,
    RepresentationMapping,
    StructureMap,
    StructureSet,
    component_location,
    is_built_value_map,
    mapped_structure,
)
from sdmxlib.model.message import MetadataMessage, StructureMessage
from sdmxlib.model.metadata_provision import MetadataProvisionAgreement
from sdmxlib.model.metadataflow import Metadataflow
from sdmxlib.model.metadataset import MetadataSet, ReportedAttribute
from sdmxlib.model.metadatastructure import MetadataAttribute, MetadataStructure
from sdmxlib.model.organisation import (
    AgencyScheme,
    DataConsumerScheme,
    DataProviderScheme,
    MetadataProviderScheme,
    OrganisationUnitScheme,
)
from sdmxlib.model.provision import ProvisionAgreement
from sdmxlib.model.ref import Ref, artefact_urn
from sdmxlib.model.representation import DataType, Facet
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.valuelist import ValueList
from sdmxlib.model.version import late_binds, sort_key, unreferenceable, unwritable

# Formal type patterns from SDMXCommonReferences.xsd. There are *three* id
# types and the distinction is load-bearing — applying IDType everywhere let
# `validate` accept ids the schema rejects, so `serialize(validate=True)` could
# emit non-schema-valid SDMX-ML (#221).
#
#   IDType              [A-Za-z0-9_@$-]+                  most maintainables;
#                                                          items: Code, Category
#   NCNameIDType        [A-Za-z][A-Za-z0-9_-]*            components, Concept,
#                                                          Agency, and exactly
#                                                          three maintainables
#   NestedNCNameIDType  NCName(\.NCName)*                 agencyID
#   VersionType         [0-9]+(\.[0-9]+)*
#
# NCNameIDType exists because those ids become XML element and attribute names
# in structure-specific schemas, so they must be valid XML NCNames: they start
# with a letter and cannot contain @ or $.
#
# The "three maintainables" is the correction that matters. This comment used
# to say NCNameIDType covered *maintainables* generally, and `validate`
# implemented that. It covers Codelist, ConceptScheme and CategoryScheme; every
# other maintainable inherits MaintainableBaseType/@id, which is IDType. The
# over-application made sdmxlib refuse the ECB's own dataflow document, whose
# categorisation ids are UUIDs. See the `id` fields' validators (`_rules`), which are
# checked against the schemas rather than trusted.
#
# The patterns live in `_rules`, which the model's own fields use to refuse a
# bad id as it is set (#492); these names are what `test_xsd_conformance` reads.
_ID_PATTERN = ID_PATTERN
_NCNAME_PATTERN = NCNAME_PATTERN
_AGENCY_PATTERN = AGENCY_PATTERN
_ID_RE = ID_RE
_NCNAME_RE = NCNAME_RE


@frozen
class ReferenceIssue:
    """A broken cross-artefact reference within a StructureMessage."""

    source: str
    target_urn: str
    message: str = "referenced artefact not found in message"

    def __str__(self) -> str:
        return f"BROKEN: {self.source} → {self.target_urn}"


class SdmxConformanceError(SdmxParseError):
    """Raised by the format readers when a message contradicts the SDMX schema.

    Distinct from `ReferenceValidationError`, which reports a reference that
    simply could not be resolved — that is a normal state for a partial
    message, and stays a non-fatal `Ref.unresolved`. This one means the input
    is *wrong*: a URN whose artefact class contradicts the element carrying
    it, or an element the schema restricts to a URN holding something that is
    not one. Neither can be produced by a schema-valid message, so the reader
    fails rather than reifying semantics the input does not support.

    A `SdmxParseError`, because it is still "this payload is not usable SDMX" —
    the narrower case where the document parsed but says something impossible,
    rather than not parsing at all. Catching the base handles both.

    Raised two ways. A reader raises it about one element, and the reader one
    level up catches it and refuses that artefact. `parse` then raises
    it about the whole document via `refusing` when anything was refused,
    carrying the refusals and the message of everything that *was* read — so
    a caller can recover the rest instead of re-parsing with
    ``strict=False``.

    Attributes:
        element: The XML element name that carried the bad value.
        detail: What was expected versus what was found.
        refused: Every artefact refused, when raised by `parse`; else empty.
        message: What was read despite the refusals, when raised by `parse`;
            else None.
    """

    element: str
    detail: str
    refused: tuple[Refusal, ...]
    message: StructureMessage | None

    def __init__(
        self,
        element: str,
        detail: str,
        *,
        refused: tuple[Refusal, ...] = (),
        message: StructureMessage | None = None,
    ) -> None:
        self.element = element
        self.detail = detail
        self.refused = refused
        self.message = message
        super().__init__(f"<{element}>: {detail}")

    @classmethod
    def refusing(cls, message: StructureMessage) -> SdmxConformanceError:
        """The whole-document error for a message that refused artefacts.

        Names the count and the first few, like `ValidationError` — a
        registry document can refuse dozens, and the message is for reading.

        Args:
            message: A read message whose `StructureMessage.refused` is set.

        Returns:
            The error, carrying ``message`` and its refusals.
        """
        refused = message.refused
        n = len(refused)
        head_count = 5
        head = "\n".join(f"  {r.artefact_class} {r}" for r in refused[:head_count])
        suffix = f"\n  ... and {n - head_count} more" if n > head_count else ""
        plural = "" if n == 1 else "s"
        detail = (
            f"{n} artefact{plural} refused; the rest were read (parse with strict=False to keep them):\n{head}{suffix}"
        )
        return cls("Structure", detail, refused=refused, message=message)


class SdmxErrorMessage(SdmxParseError):  # noqa: N818 - SDMX's own element name, `ErrorMessage`
    """Raised by `parse` when the document is an SDMX error message.

    A registry answers a query it cannot serve with an error message —
    ``<mes:Error>`` in SDMX-ML, ``{"errors": [...]}`` in SDMX-JSON — rather
    than a structure or metadata message. `parse` used to read one as an empty
    `StructureMessage`, so a saved "100 No Results Found" was indistinguishable
    from a query that matched nothing. The REST client never gets this far: it
    raises on the HTTP status first. This is for bytes read from a file.

    A `SdmxParseError`, because the payload is still not the message asked
    for; catching the base handles it with every other unusable document.

    Attributes:
        errors: One ``(code, text)`` pair per error the message reports, in
            document order. The code is text, as SDMX-ML types it; the text
            is the first one given, or empty when there is none.
    """

    errors: tuple[tuple[str, str], ...]

    def __init__(self, errors: tuple[tuple[str, str], ...]) -> None:
        self.errors = errors
        reported = "; ".join(f"{code} {text}".strip() for code, text in errors) or "no error given"
        super().__init__(f"the document is an SDMX error message, not a structure or metadata message: {reported}")


class LossyReadError(SdmxParseError):
    """Raised by ``parse(..., lossless=True)`` when the message is not exactly the document.

    The document is valid, but the model either has nowhere to put some of it
    (`StructureMessage.lost`, `MetadataMessage.lost`) or had to supply a value
    the document omitted (`StructureMessage.assumed`). By default both are recorded and the read
    succeeds. A caller that would rather stop than continue on something other
    than what the document said, a pipeline that round-trips documents, asks
    for this instead.

    Attributes:
        lost: Every loss, per artefact and construct.
        assumed: Every value the reader supplied, per artefact and construct.
        message: Everything that was read, losses and assumptions recorded.
    """

    lost: tuple[ReadGap, ...]
    assumed: tuple[ReadAssumption, ...]
    message: StructureMessage | MetadataMessage

    def __init__(self, message: StructureMessage | MetadataMessage) -> None:
        self.lost = message.lost
        self.assumed = message.assumed if isinstance(message, StructureMessage) else ()
        self.message = message
        found: list[object] = [*self.lost, *self.assumed]
        n = len(found)
        head_count = 5
        head = "\n".join(f"  {entry}" for entry in found[:head_count])
        suffix = f"\n  ... and {n - head_count} more" if n > head_count else ""
        parts = []
        if self.lost:
            parts.append(f"{len(self.lost)} loss{'' if len(self.lost) == 1 else 'es'}")
        if self.assumed:
            parts.append(f"{len(self.assumed)} assumption{'' if len(self.assumed) == 1 else 's'}")
        super().__init__(
            f"{' and '.join(parts)} reading a document the model cannot hold exactly "
            f"(parse without lossless=True to keep what was read):\n{head}{suffix}"
        )


class ReferenceValidationError(Exception):
    """Raised by ``validate_references()`` when any Ref fails to resolve.

    ``issues`` carries every broken reference in the message — not just
    the first — so a single catch surfaces the full set.
    """

    issues: list[ReferenceIssue]

    def __init__(self, issues: list[ReferenceIssue]) -> None:
        self.issues = issues
        super().__init__(self._format(issues))

    @staticmethod
    def _format(issues: list[ReferenceIssue]) -> str:
        n = len(issues)
        head_count = 5
        head = "\n".join(f"  {i}" for i in issues[:head_count])
        suffix = f"\n  ... and {n - head_count} more" if n > head_count else ""
        plural = "" if n == 1 else "s"
        return f"{n} broken reference{plural}:\n{head}{suffix}"


# ── Per-field checks ──────────────────────────────────────────────────────────


def _check_id(val: str, path: str, issues: list[ValidationIssue]) -> None:
    if (problem := id_problem(val)) is not None:
        issues.append(ValidationIssue(path=path, message=problem))


_NO_URN = "Ref has an invalid or missing URN"


def _has_urn(ref: Ref[object]) -> bool:
    try:
        _ = str(ref.urn)
    except ValueError, TypeError, AttributeError:
        return False
    return True


def _check_ref_urn(ref: Ref[object], path: str, issues: list[ValidationIssue]) -> None:
    if not _has_urn(ref):
        issues.append(ValidationIssue(path=path, message=_NO_URN))


# ── Vectorised item-id checks ────────────────────────────────────────────────


def _check_item_ids(
    ids: list[str],
    path_prefix: str,
    issues: list[ValidationIssue],
    *,
    label: str = "id",
) -> None:
    """Validate an agency scheme's agency ids against the IDType pattern.

    Every other item's id is refused as it is set (`_rules`, #492). An
    `Agency`'s cannot be: the same class is the maintainer an artefact names,
    whose id is a dotted ``NestedNCNameIDType``, so it is checked here, where
    it is known to be an item.

    Args:
        ids: Item ids in the order they appear in the parent collection.
        path_prefix: e.g. ``"AgencyScheme.agencies"``; bad ids are reported as
            ``f"{path_prefix}[{id}].id"``.
        issues: Accumulator for any problems found.
        label: Friendly noun for the message.
    """
    for bad in (v for v in ids if not v or not _ID_RE.match(v)):
        if not bad:
            issues.append(ValidationIssue(path=f"{path_prefix}[].{label}", message=f"{label} must not be empty"))
        else:
            issues.append(
                ValidationIssue(
                    path=f"{path_prefix}[{bad}].{label}",
                    message=f"{label} {bad!r} contains invalid characters (allowed: A-Za-z0-9_@$-)",
                )
            )


def _check_sibling_duplicates(
    paths: tuple[str, ...], path_prefix: str, issues: list[ValidationIssue], *, label: str
) -> None:
    """A nested scheme's repeated *sibling* ids (#439).

    A repeat in another branch is valid — the item is identified by its path —
    and only a repeat among siblings makes two items share one. Every SDMX
    version's identity constraints on nested items are sibling-scoped; the
    3.0/3.1 hierarchy-wide rule on node ids is a format's, checked at write
    (`_check_hierarchy_node_ids_in_3x`).
    """
    issues.extend(
        ValidationIssue(
            path=f"{path_prefix}[{path}]",
            message=f"duplicate {label} {path!r} — two siblings share the id {path.rsplit('.', 1)[-1]!r}",
        )
        for path in paths
    )


def _check_duplicate_ids(ids: list[str], path_prefix: str, issues: list[ValidationIssue], *, label: str = "id") -> None:
    seen: set[str] = set()
    for item_id in ids:
        if item_id in seen:
            issues.append(
                ValidationIssue(
                    path=f"{path_prefix}[{item_id}]",
                    message=f"duplicate {label} {item_id!r}",
                )
            )
        seen.add(item_id)


# ── Per-artefact validators ──────────────────────────────────────────────────


# Characters XML 1.0 cannot represent, so no SDMX artefact may carry them:
# SDMX string types *are* XSD types, which means a value containing one of
# these has no valid SDMX-ML form in any version. SDMX-JSON will happily carry
# it, which is exactly why this is checked here rather than left to the writer:
# without it, a message is writable as JSON and not as XML, and the only signal
# is a raw ``ValueError`` out of lxml with no field path.
#
# The set is deliberately *exactly* XML 1.0's, no stricter. DEL (U+007F) and
# NEL (U+0085) are legal and appear in real documents; a "not printable" test
# would reject valid data. `test_validation_xml_charset` asserts this set
# against what lxml actually accepts, so the two cannot drift.
#: In `_rules`, which the model's name fields use as they are set (#492); these
#: names are what the XML-charset and read-cost tests read.
_XML_FORBIDDEN = XML_FORBIDDEN
_first_xml_incompatible = first_xml_incompatible


def _check_name(name: InternationalString, path: str, issues: list[ValidationIssue]) -> None:
    """An agency's name, by the rule every other name meets as it is set (`_rules.name_problem`).

    An `Agency` is the one nameable item without the field validator: the same
    class is the maintainer an artefact names, which carries no name at all.
    """
    problem = name_problem(name)
    if problem is not None:
        where, message = problem
        issues.append(ValidationIssue(path=f"{path}{where.removeprefix('name')}", message=message))


def _check_item_names(
    items: Sequence[tuple[str, InternationalString]],
    path_prefix: str,
    issues: list[ValidationIssue],
) -> None:
    """Require a name on each (id, name) item pair: an agency scheme's, since #492."""
    for item_id, name in items:
        _check_name(name, f"{path_prefix}[{item_id}].name", issues)


#: Organisation scheme ids the XSD pins with a `fixed` attribute, in every
#: SDMX-ML version. An AgencyScheme called anything but AGENCIES serialises to
#: XML that fails validation — and nothing stopped you building one (#223).
_FIXED_SCHEME_IDS = {
    "AgencyScheme": "AGENCIES",
    "DataProviderScheme": "DATA_PROVIDERS",
    # 3.0-only, and pinned the same way its data twin is (#315).
    "MetadataProviderScheme": "METADATA_PROVIDERS",
    # Pinned in 2.1, 3.0 and 3.1 alike (#469). An OrganisationUnitScheme's id
    # is free.
    "DataConsumerScheme": "DATA_CONSUMERS",
}


#: The one version every format allows an organisation scheme (#416), pinned
#: by the same schema mechanism as its id: SDMX-ML 2.1 declares the attribute
#: `fixed="1.0"`; 3.0 and 3.1 declare it `use="prohibited"`, so the reader
#: supplies `1.0`; and SDMX-JSON 2.0 / 2.1 give `MaintainableWithoutVersionType`
#: `"version": {"const": "1.0"}`, from which every organisation-scheme type
#: derives.
#:
#: Format-independent, so checked here rather than per format. It used to be a
#: per-format check (#230) that covered SDMX-ML only, on the belief that
#: SDMX-JSON "round-trips the version untouched" — it did, because its writer
#: checked nothing, and the document was schema-invalid. That check also
#: predated `MetadataProviderScheme` and never covered it, so its 3.x writer
#: dropped a non-default version silently and the reader brought back `1.0`.
_FIXED_SCHEME_VERSION = "1.0"


def _check_fixed_scheme_version(artefact: MaintainableArtefact, prefix: str, issues: list[ValidationIssue]) -> None:
    """Check the version every format pins for an organisation scheme."""
    if type(artefact).__name__ in _FIXED_SCHEME_IDS and artefact.version != _FIXED_SCHEME_VERSION:
        issues.append(
            ValidationIssue(
                path=f"{prefix}.version",
                message=(
                    f"{type(artefact).__name__} version is fixed to {_FIXED_SCHEME_VERSION!r} in every SDMX "
                    f"format (2.1 fixes it, 3.x prohibits it, SDMX-JSON makes it a const), got {artefact.version!r}"
                ),
            )
        )


def _check_fixed_scheme_id(artefact: MaintainableArtefact, prefix: str, issues: list[ValidationIssue]) -> None:
    """Check ids the schema pins to one literal value."""
    expected = _FIXED_SCHEME_IDS.get(type(artefact).__name__)
    if expected is not None and artefact.id != expected:
        issues.append(
            ValidationIssue(
                path=f"{prefix}.id",
                message=(
                    f"{type(artefact).__name__} id is fixed to {expected!r} by the SDMX schema, got {artefact.id!r}"
                ),
            )
        )


def _validate_maintainable(artefact: MaintainableArtefact, prefix: str, issues: list[ValidationIssue]) -> None:
    # The id, agency id, version and name are refused as they are set (`_rules`, #492).
    _check_fixed_scheme_id(artefact, prefix, issues)
    _check_fixed_scheme_version(artefact, prefix, issues)


def _validate_codelist(cl: Codelist, prefix: str, issues: list[ValidationIssue]) -> None:
    _validate_maintainable(cl, prefix, issues)
    ids = [c.id for c in cl.codes]
    _check_duplicate_ids(ids, f"{prefix}.codes", issues, label="code id")
    # An extending codelist inherits the codes it extends, so a parent outside
    # its own may be one of those; only what its own codes say is checked then.
    _check_item_parents(
        [(c.id, c.parent_id) for c in cl.codes], f"{prefix}.codes", issues, kind="code", closed=not cl.extensions
    )


def _validate_concept_scheme(cs: ConceptScheme, prefix: str, issues: list[ValidationIssue]) -> None:
    _validate_maintainable(cs, prefix, issues)
    ids = [c.id for c in cs.concepts]
    _check_duplicate_ids(ids, f"{prefix}.concepts", issues, label="concept id")
    _check_item_parents([(c.id, c.parent_id) for c in cs.concepts], f"{prefix}.concepts", issues, kind="concept")
    for concept in cs.concepts:
        _check_enumeration_format(concept, f"{prefix}.concepts[{concept.id}]", issues)


def _check_item_parents(
    items: list[tuple[str, str | None]], path: str, issues: list[ValidationIssue], *, kind: str, closed: bool = True
) -> None:
    """Each item's ``parent_id`` names another item of its scheme, and no chain of them loops (#453).

    A dangling parent, one naming the item itself, or a cycle validated
    cleanly and serialised; the navigation over it (`roots`, `children_of`)
    then gave a forest with orphans or none at all, and a strict registry
    refused it. One issue per offending item, as every other per-item rule
    here reports, and one per cycle, naming its members.

    Args:
        items: Each item's id and parent id, in scheme order.
        path: The items' path, e.g. ``Codelist:CL.codes``.
        issues: Where the findings go.
        kind: What the items are, for the messages.
        closed: Whether every parent must be among `items`. False for an
            extending codelist, whose parents may be codes it inherits.
    """
    ids = {item_id for item_id, _ in items}
    parent_of: dict[str, str] = {}
    for item_id, parent in items:
        if parent is None:
            continue
        if parent == item_id:
            message = f"parent_id {parent!r} is the {kind} itself"
            issues.append(ValidationIssue(path=f"{path}[{item_id}].parent_id", message=message))
        elif parent in ids:
            parent_of[item_id] = parent
        elif closed:
            article = "an" if kind[0] in "aeiou" else "a"
            message = f"parent_id {parent!r} is not {article} {kind} in this scheme"
            issues.append(ValidationIssue(path=f"{path}[{item_id}].parent_id", message=message))
    # Each chain walked once: an item seen on an earlier walk is settled.
    settled: set[str] = set()
    for start in parent_of:
        chain: list[str] = []
        on_chain: set[str] = set()
        node: str | None = start
        while node is not None and node not in settled and node not in on_chain:
            chain.append(node)
            on_chain.add(node)
            node = parent_of.get(node)
        if node is not None and node in on_chain:
            loop = chain[chain.index(node) :]
            message = f"parent_id forms a cycle: {' -> '.join([*loop, node])}"
            issues.append(ValidationIssue(path=f"{path}[{node}].parent_id", message=message))
        settled.update(chain)


def _validate_category_scheme(cs: CategoryScheme, prefix: str, issues: list[ValidationIssue]) -> None:
    _validate_maintainable(cs, prefix, issues)
    # Each category's id and name are refused as they are set (#492); what is
    # left needs the whole tree.
    _check_sibling_duplicates(sibling_duplicates(cs), f"{prefix}.categories", issues, label="category path")


def _validate_agency_scheme(s: AgencyScheme, prefix: str, issues: list[ValidationIssue]) -> None:
    _validate_maintainable(s, prefix, issues)
    ids = [a.id for a in s.agencies]
    _check_item_ids(ids, f"{prefix}.agencies", issues, label="agency id")
    _check_item_names([(a.id, a.name) for a in s.agencies], f"{prefix}.agencies", issues)
    _check_duplicate_ids(ids, f"{prefix}.agencies", issues, label="agency id")


def _validate_data_provider_scheme(s: DataProviderScheme, prefix: str, issues: list[ValidationIssue]) -> None:
    _validate_maintainable(s, prefix, issues)
    ids = [p.id for p in s.providers]
    _check_duplicate_ids(ids, f"{prefix}.providers", issues, label="provider id")


def _validate_data_consumer_scheme(s: DataConsumerScheme, prefix: str, issues: list[ValidationIssue]) -> None:
    _validate_maintainable(s, prefix, issues)
    ids = [c.id for c in s.consumers]
    _check_duplicate_ids(ids, f"{prefix}.consumers", issues, label="consumer id")


def _validate_organisation_unit_scheme(s: OrganisationUnitScheme, prefix: str, issues: list[ValidationIssue]) -> None:
    _validate_maintainable(s, prefix, issues)
    ids = [u.id for u in s.organisation_units]
    _check_duplicate_ids(ids, f"{prefix}.organisation_units", issues, label="organisation unit id")
    _check_item_parents(
        [(u.id, u.parent_id) for u in s.organisation_units],
        f"{prefix}.organisation_units",
        issues,
        kind="organisation unit",
    )


def _check_component_concept(concept: object, path: str, issues: list[ValidationIssue]) -> None:
    """Require a ConceptIdentity on a component, as every format does.

    Applies to DSD components and to MSD metadata attributes alike; the path
    argument says which. The XSD component sequence is ``Annotations?,
    ConceptIdentity, LocalRepresentation?`` in every SDMX-ML version, and
    SDMX-JSON lists ``conceptIdentity`` in the required properties of a
    dimension, attribute, measure and metadata attribute.

    A writer-only rule until #419, so a DSD built without concepts stayed
    storable and failed on its first write (#224). `Registry.add` refuses it
    now: no format can write it, so the store should not hold it.
    """
    if concept is None:
        issues.append(
            ValidationIssue(
                path=path,
                message=(
                    "ConceptIdentity is required here — every SDMX format rejects a "
                    "component without one, so this artefact cannot be serialised"
                ),
            )
        )


#: The data types a coding format may declare: ``SimpleCodeDataType``, in
#: SDMX-ML 2.1, 3.0 and 3.1 and SDMX-JSON alike — three values, where
#: `DataType` has forty-four, because a coding format describes the *shape of
#: a code*, not a value domain. Emitting anything else produced a document
#: every schema rejects (#230).
_CODING_FORMAT_TYPES = frozenset({"Alpha", "AlphaNumeric", "Numeric"})

#: The facets an enumeration format cannot carry. It constrains the codes of
#: an enumerated representation, and codes are strings, so every target types
#: it without them: SDMX-ML's ``CodedTextFormatType`` prohibits ``decimals``
#: and leaves ``isMultiLingual`` out, and SDMX-JSON closes the object (#364).
_NOT_CODED = ("decimals", "is_multilingual")

#: The facets a Level's coding format cannot carry: those two, and the time
#: facets ``CodingTextFormatType`` does not declare in any format (#422).
_NOT_CODING = ("decimals", "is_multilingual", "time_interval", "start_time", "end_time")


def _check_absent(fmt: Facet, fields: tuple[str, ...], path: str, what: str, issues: list[ValidationIssue]) -> None:
    issues.extend(
        ValidationIssue(
            path=f"{path}.{field_name}",
            message=f"{what} constrains codes, which are strings; no SDMX format can carry {field_name} there",
        )
        for field_name in fields
        # `is_multilingual` is False when unstated (#503).
        if getattr(fmt, field_name) not in {None, False}
    )


def _check_enumeration_format(owner: object, path: str, issues: list[ValidationIssue]) -> None:
    """An enumeration format, as every format types it (#243, #419).

    `RepresentationType` is a choice in every SDMX-ML version and a ``oneOf``
    in SDMX-JSON: a text format on one side, an enumeration plus an optional
    enumeration format on the other. So the format needs an enumeration beside
    it, and says only what can be said of codes — no `decimals`, and value
    bounds that are integers.

    Args:
        owner: A component, metadata attribute or concept; its representation
            is ``representation``, or a concept's ``core_representation``.
        path: Where `owner` sits, for the issue.
        issues: Accumulates what is found.
    """
    # `getattr`: `MeasureDimension` is not a `Component` and carries no
    # `enumeration_format`. 2.1's `MeasureDimensionRepresentationType`
    # restricts it to `<Enumeration>` alone, so the field would be
    # meaningless on it (#305).
    fmt: Facet | None = getattr(owner, "enumeration_format", None)
    if fmt is None:
        return
    path = f"{path}.enumeration_format"
    representation: object = getattr(owner, "core_representation", None)
    if representation is None:
        representation = getattr(owner, "representation", None)
    if not isinstance(representation, Ref):
        issues.append(
            ValidationIssue(
                path=path,
                message=(
                    "an enumeration_format constrains the codes of an enumerated representation, "
                    "so it needs one; RepresentationType is a choice of TextFormat or "
                    "Enumeration+EnumerationFormat, never both"
                ),
            )
        )
    _check_absent(fmt, _NOT_CODED, path, "an enumeration format", issues)
    for field_name in ("min_value", "max_value", "start_value", "end_value"):
        bound: float | None = getattr(fmt, field_name)
        if bound is not None and bound != int(bound):
            issues.append(
                ValidationIssue(
                    path=f"{path}.{field_name}",
                    message=(
                        f"an enumeration format types {field_name} as an integer, got {bound!r}; "
                        "the decimal form is only valid on a text format"
                    ),
                )
            )


def _check_coding_format(fmt: Facet | None, path: str, issues: list[ValidationIssue]) -> None:
    """A Level's coding format, as every format types it (#230, #419)."""
    if fmt is None:
        return
    if fmt.type is not None and str(fmt.type) not in _CODING_FORMAT_TYPES:
        issues.append(
            ValidationIssue(
                path=f"{path}.type",
                message=(
                    f"a Level CodingFormat may only declare {sorted(_CODING_FORMAT_TYPES)}, "
                    f"got {fmt.type!s}; every SDMX format types it SimpleCodeDataType"
                ),
            )
        )
    _check_absent(fmt, _NOT_CODING, path, "a coding format", issues)


def _validate_dsd(dsd: DataStructure, prefix: str, issues: list[ValidationIssue]) -> None:
    _validate_maintainable(dsd, prefix, issues)

    for dim in dsd.dimensions:
        base = f"{prefix}.dimensions[{dim.id}]"
        if isinstance(dim.representation, Ref):
            _check_ref_urn(dim.representation, f"{base}.representation", issues)
        if isinstance(dim.concept, Ref):
            _check_ref_urn(dim.concept, f"{base}.concept", issues)

    for attr in dsd.attributes:
        base = f"{prefix}.attributes[{attr.id}]"
        if isinstance(attr.representation, Ref):
            _check_ref_urn(attr.representation, f"{base}.representation", issues)

    for kind, components in (
        ("dimensions", dsd.dimensions),
        ("attributes", dsd.attributes),
        ("measures", dsd.measures),
    ):
        for component in components:
            base = f"{prefix}.{kind}[{component.id}]"
            _check_component_concept(component.concept, f"{base}.concept", issues)
            _check_enumeration_format(component, base, issues)
    # The two single-valued components need one too. Checking only the lists
    # let a time or measure dimension without a concept be built, stored and
    # written as XML its XSD rejects (#506).
    for kind, single in (("time_dimension", dsd.time_dimension), ("measure_dimension", dsd.measure_dimension)):
        if single is not None:
            _check_component_concept(single.concept, f"{prefix}.{kind}.concept", issues)

    _check_time_text_type(dsd, prefix, issues)
    _check_dsd_sentinels(dsd, prefix, issues)
    _check_dimension_multilingual(dsd, prefix, issues)
    _check_component_ids_unique(dsd, prefix, issues)
    _check_3x_components(dsd, prefix, issues)
    _check_21_groupings(dsd, prefix, issues)
    _check_attachments(dsd, prefix, issues)


def _check_dsd_sentinels(dsd: DataStructure, prefix: str, issues: list[ValidationIssue]) -> None:
    """No component's enumeration format carries a sentinel.

    What every format refuses, refused as the DSD is built rather than at write
    time, so a store never holds a DSD it cannot write (#508, #419).
    """
    for kind in ("dimensions", "attributes", "measures"):
        for component in getattr(dsd, kind):
            _no_sentinels(
                getattr(component, "enumeration_format", None),
                f"{prefix}.{kind}[{component.id}].enumeration_format",
                issues,
            )


def _check_dimension_multilingual(dsd: DataStructure, prefix: str, issues: list[ValidationIssue]) -> None:
    """A dimension's value is never multilingual (#503).

    Every format gives a dimension the simple text format and a time
    dimension the time one, and both prohibit ``isMultiLingual``, so no
    document can say it.
    """
    dimensions = [(f"dimensions[{dim.id}]", dim.representation) for dim in dsd.dimensions]
    if dsd.time_dimension is not None:
        dimensions.append(("time_dimension", dsd.time_dimension.representation))
    issues.extend(
        ValidationIssue(
            path=f"{prefix}.{where}.representation.is_multilingual",
            message="a dimension's text format prohibits isMultiLingual in every SDMX format",
        )
        for where, representation in dimensions
        if isinstance(representation, Facet) and representation.is_multilingual
    )


def _check_attachments(dsd: DataStructure, prefix: str, issues: list[ValidationIssue]) -> None:
    """A series attachment names the DSD's dimensions, and a group attachment one of its groups (#454).

    `AttributeRelationshipType` is a required choice, so a series attribute
    with no dimensions, or a group one with no group, was written as an empty
    ``<str:AttributeRelationship/>`` that no schema accepts; and one naming a
    dimension or group the DSD lacks says nothing a reader can use. For a data
    attribute and a metadata attribute usage alike. The time dimension, and
    2.1's measure dimension, are dimensions here.
    """
    dimension_ids = {d.id for d in dsd.dimensions}
    for extra in (dsd.time_dimension, dsd.measure_dimension):
        if extra is not None:
            dimension_ids.add(extra.id)
    group_ids = {group.id for group in dsd.groups or ()}
    for kind, holders in (("attributes", dsd.attributes), ("metadata_attributes", dsd.metadata_attributes)):
        for holder in holders:
            path = f"{prefix}.{kind}[{holder.id}]"
            if holder.attachment is AttachmentLevel.SERIES:
                _check_series(holder, dimension_ids, f"{path}.related_dimensions", issues)
            elif holder.attachment is AttachmentLevel.GROUP:
                _check_group(holder, group_ids, f"{path}.related_group", issues)


def _check_series(
    holder: Attribute | MetadataAttributeUsage, dimension_ids: set[str], path: str, issues: list[ValidationIssue]
) -> None:
    if not holder.related_dimensions:
        message = "attached to a series, it names no dimension; a series attachment names at least one"
        issues.append(ValidationIssue(path=path, message=message))
    unknown = [d for d in holder.related_dimensions or () if d not in dimension_ids]
    if unknown:
        message = f"names {', '.join(unknown)}, which this DataStructure has no dimension for"
        issues.append(ValidationIssue(path=path, message=message))


def _check_group(
    holder: Attribute | MetadataAttributeUsage, group_ids: set[str], path: str, issues: list[ValidationIssue]
) -> None:
    if holder.related_group is None:
        issues.append(ValidationIssue(path=path, message="attached to a group, it names none"))
    elif holder.related_group not in group_ids:
        message = f"names group {holder.related_group!r}, which this DataStructure does not declare"
        issues.append(ValidationIssue(path=path, message=message))


def _check_21_groupings(dsd: DataStructure, prefix: str, issues: list[ValidationIssue]) -> None:
    """SDMX-ML 2.1's attachment groups and constraint-defined groups, as it allows them (#422).

    A group is its dimensions *or* an attachment constraint — `GroupType` is
    an ``xs:choice`` — and an attachment group sits beside an attribute's
    related dimensions, naming a group the DSD declares.
    """
    groups = dsd.groups or ()
    group_ids = {group.id for group in groups}
    issues.extend(
        ValidationIssue(
            path=f"{prefix}.groups[{group.id}]",
            message="a group is its dimensions or an attachment constraint, not both",
        )
        for group in groups
        if group.dimension_ids and group.attachment_constraint is not None
    )
    for attr in dsd.attributes:
        if not attr.attachment_groups:
            continue
        path = f"{prefix}.attributes[{attr.id}].attachment_groups"
        if not attr.related_dimensions:
            issues.append(
                ValidationIssue(path=path, message="attachment groups sit beside related dimensions; none is set")
            )
        issues.extend(
            ValidationIssue(path=path, message=f"names group {group_id!r}, which the DSD does not declare")
            for group_id in attr.attachment_groups
            if group_id not in group_ids
        )


def _check_3x_components(dsd: DataStructure, prefix: str, issues: list[ValidationIssue]) -> None:
    """SDMX 3.0's additions to a DSD's components say only what the DSD can mean (#422).

    Occurrence bounds as the schema types them (`minOccurs` non-negative,
    `maxOccurs` at least 1), and no lower than the minimum; a measure
    relationship naming this DSD's measures; an optional dimension among the
    related ones — for a data attribute and a metadata attribute usage alike.
    """
    measure_ids = {m.id for m in dsd.measures}
    for kind, components in (("attributes", dsd.attributes), ("measures", dsd.measures)):
        for component in components:
            low, high = component.min_occurs, component.max_occurs
            if low < 0 or (high is not None and (high < 1 or high < low)):
                issues.append(
                    ValidationIssue(
                        path=f"{prefix}.{kind}[{component.id}].max_occurs",
                        message=f"occurs {low}..{'unbounded' if high is None else high} is not a range: "
                        "minOccurs is at least 0, maxOccurs at least 1 and at least minOccurs",
                    )
                )
    for attr in dsd.attributes:
        unknown = sorted(set(attr.related_measures or ()) - measure_ids)
        if unknown:
            issues.append(
                ValidationIssue(
                    path=f"{prefix}.attributes[{attr.id}].related_measures",
                    message=f"names {', '.join(unknown)}, which this DataStructure has no measure for",
                )
            )
    for kind, holders in (("attributes", dsd.attributes), ("metadata_attributes", dsd.metadata_attributes)):
        for holder in holders:
            stray = sorted(holder.optional_dimensions - set(holder.related_dimensions or ()))
            if stray:
                issues.append(
                    ValidationIssue(
                        path=f"{prefix}.{kind}[{holder.id}].optional_dimensions",
                        message=f"{', '.join(stray)} optional but not related: an optional dimension is one "
                        "of related_dimensions",
                    )
                )


#: SDMX's `TimeDataType`: the `textType`s a time dimension may declare. Every
#: SDMX-ML version types the time dimension's format `TimeTextFormatType`,
#: whose `textType` is this enumeration, identical in 2.1, 3.0 and 3.1 —
#: `test_time_text_type.py` reads it out of all three schemas and compares.
_TIME_DATA_TYPES = frozenset(
    {
        "ObservationalTimePeriod",
        "StandardTimePeriod",
        "BasicTimePeriod",
        "GregorianTimePeriod",
        "GregorianYear",
        "GregorianYearMonth",
        "GregorianDay",
        "ReportingTimePeriod",
        "ReportingYear",
        "ReportingSemester",
        "ReportingTrimester",
        "ReportingQuarter",
        "ReportingMonth",
        "ReportingWeek",
        "ReportingDay",
        "DateTime",
        "TimeRange",
    }
)


def _check_time_text_type(dsd: DataStructure, prefix: str, issues: list[ValidationIssue]) -> None:
    """A time dimension's `textType` must be a time type (#431).

    `TimeDimension.representation` is an ordinary `Facet`, so any `DataType`
    was accepted and written — schema-invalid in every format, since all of
    them restrict it to `TimeDataType`. IMF publishes 54 DSDs declaring
    `String` there. Format-independent, so refused here rather than per
    writer; coercing it to `ObservationalTimePeriod` would invent data.
    """
    td = dsd.time_dimension
    if td is None or not isinstance(td.representation, Facet) or td.representation.type is None:
        return
    text_type = str(td.representation.type)
    if text_type not in _TIME_DATA_TYPES:
        issues.append(
            ValidationIssue(
                path=f"{prefix}.time_dimension.representation.type",
                message=(
                    f"a time dimension's textType must be a time type (TimeDataType: "
                    f"ObservationalTimePeriod, GregorianYear, ReportingQuarter, DateTime, ...), not {text_type!r}"
                ),
            )
        )


def _check_component_ids_unique(dsd: DataStructure, prefix: str, issues: list[ValidationIssue]) -> None:
    """A component id is unique across the whole DSD, not per list.

    Storage has always assumed it — `dsd_component` declares
    ``PRIMARY KEY (dsd_urn, component_id)``, so a DSD with a dimension and a
    measure sharing an id cannot round-trip through DuckDB — but nothing
    checked the model, so the collision surfaced as a constraint violation on
    write rather than as a validation issue on the artefact.

    #407 gave it a second consumer: `DataStructure.ref_to` dispatches on which
    list holds the id, which is only unambiguous because this holds.
    """
    seen: dict[str, str] = {}
    single = [("time_dimension", dsd.time_dimension), ("measure_dimension", dsd.measure_dimension)]
    listed = [("dimensions", dsd.dimensions), ("attributes", dsd.attributes), ("measures", dsd.measures)]
    components: list[tuple[str, str]] = [(field, c.id) for field, c in single if c is not None]
    components += [(field, c.id) for field, items in listed for c in items]
    components += [("groups", g.id) for g in (dsd.groups or ())]
    for field, component_id in components:
        if component_id in seen:
            issues.append(
                ValidationIssue(
                    path=f"{prefix}.{field}[{component_id}].id",
                    message=(
                        f"component id {component_id!r} is already used by {seen[component_id]} — "
                        "SDMX component ids are unique across the whole structure, and storage keys on "
                        "(dsd_urn, component_id)"
                    ),
                )
            )
        else:
            seen[component_id] = field


def _validate_hierarchy(h: Hierarchy, prefix: str, issues: list[ValidationIssue]) -> None:
    _validate_maintainable(h, prefix, issues)
    for level in h.levels:
        _no_sentinels(level.coding_format, f"{prefix}.levels[{level.id}].coding_format", issues)
    nodes = h.codes
    _check_sibling_duplicates(sibling_duplicates(h), f"{prefix}.codes", issues, label="node path")
    for node in nodes:
        # `HierarchicalCodeType` requires the code a node places in every
        # SDMX-ML version, and SDMX-JSON requires `code` (#434). OECD's
        # alias-form HCLs became such nodes when the reader lost their codes.
        if node.code is None:
            issues.append(
                ValidationIssue(
                    path=f"{prefix}.codes[{h.path_of(node)}].code",
                    message="a hierarchical code must reference the code it places; every SDMX format requires it",
                )
            )
        elif isinstance(node.code, Ref) and not _has_urn(node.code):
            # The path only when there is an issue to name: a store read runs
            # this over every node it builds, 499,138 for `H_CP_GEO_FED` (#492).
            issues.append(ValidationIssue(path=f"{prefix}.codes[{h.path_of(node)}].code", message=_NO_URN))
    for level in h.levels:
        _check_coding_format(level.coding_format, f"{prefix}.levels[{level.id}].coding_format", issues)
    _check_node_levels(h, prefix, issues)


def _check_node_levels(h: Hierarchy, prefix: str, issues: list[ValidationIssue]) -> None:
    """A node's level is one of the hierarchy's, at its depth or below it, never above (#455).

    Every SDMX-ML version writes a hierarchy's levels as a chain, the first the
    broadest, and reads a node at the level whose depth matches its own. A
    node naming its level "is only necessary if the nesting depth of the
    hierarchical code does not correspond to the nesting depth of the level"
    — to skip *down* a level, says the schema. A node naming one above its own
    depth says the chain cannot hold, and one naming no level of the
    hierarchy names nothing.
    """
    depth_of = {level.id: depth for depth, level in enumerate(h.levels)}
    stack = [(node, 0) for node in h.tree]
    while stack:
        node, depth = stack.pop()
        stack.extend((child, depth + 1) for child in node.children)
        if node.level is None:
            continue
        level_depth = depth_of.get(node.level.id)
        if level_depth is None:
            message = f"names level {node.level.id!r}, which this hierarchy does not define"
        elif level_depth < depth:
            message = (
                f"at depth {depth}, it names level {node.level.id!r} at depth {level_depth}: a code may sit at its "
                "level's depth or skip down to a deeper one, never above it"
            )
        else:
            continue
        # The path only for a node with an issue, as in `_validate_hierarchy`.
        issues.append(ValidationIssue(path=f"{prefix}.codes[{h.path_of(node)}].level", message=message))


def _check_metadata_attribute_occurs(attr: MetadataAttribute, path: str, issues: list[ValidationIssue]) -> None:
    """A metadata attribute occurs a range of times: `minOccurs` at least 0, `maxOccurs` at least 1 and `minOccurs`.

    IM §7.3.4.1: "mandatory (minOccurs=1 or more) or optional (minOccurs=0)";
    the bounds a DSD component's occurrences meet (`_check_3x_components`).
    """
    low, high = attr.min_occurs, attr.max_occurs
    if low < 0 or (high is not None and (high < 1 or high < low)):
        issues.append(
            ValidationIssue(
                path=f"{path}.max_occurs",
                message=f"occurs {low}..{'unbounded' if high is None else high} is not a range: "
                "minOccurs is at least 0, maxOccurs at least 1 and at least minOccurs (IM §7.3.4.1)",
            )
        )


def _validate_metadata_structure(msd: MetadataStructure, prefix: str, issues: list[ValidationIssue]) -> None:
    _validate_maintainable(msd, prefix, issues)

    def _walk(attr: MetadataAttribute, path: str) -> None:
        _check_component_concept(attr.concept, f"{path}.concept", issues)
        _check_metadata_attribute_occurs(attr, path, issues)
        _check_enumeration_format(attr, path, issues)
        _no_sentinels(attr.enumeration_format, f"{path}.enumeration_format", issues)
        for child in attr.children:
            _walk(child, f"{path}.children[{child.id}]")

    for attr in msd.attributes:
        _walk(attr, f"{prefix}.attributes[{attr.id}]")
    _check_sibling_duplicates(sibling_duplicates(msd), f"{prefix}.attributes", issues, label="attribute path")


def _validate_data_constraint(dc: DataConstraint, prefix: str, issues: list[ValidationIssue]) -> None:
    """A cube region's selections, in the shapes every format requires (#433).

    A key or component holds values or a time range: holding both is refused
    by `CubeRegionKeyType` in 2.1, 3.0, 3.1 and SDMX-JSON alike, and so is a
    time range with no bound. A key with *neither* is SDMX-JSON-legal — the
    availability builders use it for "no data" — so that one is the SDMX-ML
    writers' question (`_check_empty_keys_in_ml`).
    """
    _validate_maintainable(dc, prefix, issues)
    _check_attachment_choice(dc.attachment, f"{prefix}.attachment", issues)
    _check_key_sets(dc.data_key_sets, prefix, issues)
    for r, region in enumerate(dc.regions):
        path = f"{prefix}.regions[{r}]"
        selections = [(f"{path}.keys[{kv.dimension_id}]", kv) for kv in region.keys]
        selections += [(f"{path}.components[{c.component_id}]", c) for c in region.components]
        for here, selection in selections:
            if selection.values and selection.time_range is not None:
                issues.append(ValidationIssue(path=here, message="a selection holds values or a time range, not both"))
            _check_region_values(selection, here, issues)


def _validate_attachment_constraint(ac: AttachmentConstraint, prefix: str, issues: list[ValidationIssue]) -> None:
    _validate_maintainable(ac, prefix, issues)
    _check_attachment_choice(ac.attachment, f"{prefix}.attachment", issues)
    _check_key_sets(ac.data_key_sets, prefix, issues)


def _check_key_sets(key_sets: tuple[DataKeySet, ...], prefix: str, issues: list[ValidationIssue]) -> None:
    """A key set holds at least one key: `DataKeySetType` requires one in every SDMX format (#479)."""
    issues.extend(
        ValidationIssue(
            path=f"{prefix}.data_key_sets[{s}].keys",
            message="a data key set holds at least one key; every SDMX format requires one",
        )
        for s, key_set in enumerate(key_sets)
        if not key_set.keys
    )


#: The attachment alternatives a queryable data source may sit beside.
_QUERYABLE = frozenset({"data_structures", "dataflows", "provision_agreements"})


def _check_attachment_choice(attachment: ConstraintAttachment, path: str, issues: list[ValidationIssue]) -> None:
    """A constraint attaches to one alternative, in every format (#422).

    `ConstraintAttachmentType` is an ``xs:choice`` in 2.1, 3.0 and 3.1, and
    SDMX-JSON's a ``oneOf``: a provider, data sets, simple data sources, or
    artefacts of one kind — so a constraint on dataflows and DSDs at once has
    no form anywhere. Queryable data sources come only beside a kind.
    """
    chosen = attachment.alternatives
    if len(chosen) > 1:
        issues.append(
            ValidationIssue(
                path=path,
                message=(
                    "a constraint attaches to one of a data provider, data sets, simple data sources, "
                    f"or artefacts of one kind; this attachment sets {', '.join(chosen)}"
                ),
            )
        )
    if attachment.queryable_data_sources and not _QUERYABLE & set(chosen):
        issues.append(
            ValidationIssue(
                path=f"{path}.queryable_data_sources",
                message=(
                    "queryable data sources sit beside data structures, dataflows or provision agreements; none is set"
                ),
            )
        )


def _check_region_values(selection: KeyValue | ComponentValueSet, path: str, issues: list[ValidationIssue]) -> None:
    """A value set's qualified values, as SDMX 3.x permits them (#422).

    A dimension's value has no language — `CubeKeyValueType` prohibits
    `xml:lang` in 3.0 and 3.1 — and a component's value no validity
    (`DataComponentValueType`). A value is listed once: two entries for one
    code, qualified differently, would say two things about it.
    """
    seen: set[str] = set()
    for value in sorted(selection.values, key=lambda v: (v.value, v.lang or "")):
        if value.value in seen:
            issues.append(ValidationIssue(path=f"{path}.values", message=f"{value.value!r} is listed more than once"))
        seen.add(value.value)
        if isinstance(selection, KeyValue) and value.lang is not None:
            issues.append(
                ValidationIssue(
                    path=f"{path}.values[{value.value}].lang",
                    message="a dimension's value has no language; SDMX 3.x prohibits xml:lang on one",
                )
            )
        if isinstance(selection, ComponentValueSet) and (value.valid_from or value.valid_to):
            issues.append(
                ValidationIssue(
                    path=f"{path}.values[{value.value}].valid_from",
                    message="a component's value has no validity; SDMX 3.x prohibits validFrom/validTo on one",
                )
            )


def _check_cascade_in_2_1(dc: DataConstraint, version: str, issues: list[ValidationIssue]) -> None:
    """2.1's `cascadeValues` is a boolean, so `EXCLUDE_ROOT` has no 2.1 form (#422).

    Refused rather than written as either boolean: `true` would select the
    code itself, `false` none of its descendants — both change which codes
    the region names.
    """
    if version != "2.1":
        return
    issues.extend(
        ValidationIssue(
            path=f"DataConstraint:{dc.id}.regions[{r}].{kind}[{selection_id}].values[{value.value}].cascade",
            message="SDMX-ML 2.1's cascadeValues is true or false; excluderoot has no 2.1 form — write 3.x",
        )
        for r, region in enumerate(dc.regions)
        for kind, selection_id, selection in (
            *(("keys", kv.dimension_id, kv) for kv in region.keys),
            *(("components", c.component_id, c) for c in region.components),
        )
        for value in selection.values
        if value.cascade is CascadeSelection.EXCLUDE_ROOT
    )


def _check_empty_keys_in_ml(dc: DataConstraint, version: str, issues: list[ValidationIssue]) -> None:
    """SDMX-ML requires a key value's `Value+` or `TimeRange` (#433).

    `CubeRegionKeyType` makes the choice mandatory in 2.1, 3.0 and 3.1, so a
    key with no values — a lake with no rows for a component, or a time range
    some reader lost — has no SDMX-ML form; SDMX-JSON's `anyOf` allows it.
    Refused rather than omitted: dropping the key would widen the region to
    every value of that dimension.
    """
    if version == "json":
        return
    message = (
        f"a key value with no values and no time range has no SDMX-ML {version} form "
        "(KeyValue requires one); write SDMX-JSON, or give the key its values"
    )
    issues.extend(
        ValidationIssue(path=f"DataConstraint:{dc.id}.regions[{r}].keys[{kv.dimension_id}]", message=message)
        for r, region in enumerate(dc.regions)
        for kv in region.keys
        if not kv.values and kv.time_range is None
    )


def _validate_structure_map(sm: StructureMap, prefix: str, issues: list[ValidationIssue]) -> None:
    _validate_maintainable(sm, prefix, issues)
    _check_date_map_frequency(sm, prefix, issues)


def _check_date_map_frequency(sm: StructureMap, prefix: str, issues: list[ValidationIssue]) -> None:
    """A date map names a frequency dimension or a target frequency, one of them (IM §9.1.4.2).

    The IM calls `targetFrequencyId` "Mutually exclusive with freqDimension".
    The 3.0 and 3.1 XSDs make the two a required choice (`DateMapType`), with
    `MappedFrequencies` inside the frequency-dimension branch, and SDMX-JSON a
    `oneOf`: a map with both, or neither, has no valid document.
    """
    date_maps: list[tuple[str, EpochMap | DatePatternMap]] = [
        *((f"{prefix}.epoch_maps[{m.id}]", m) for m in sm.epoch_maps),
        *((f"{prefix}.date_pattern_maps[{m.id}]", m) for m in sm.date_pattern_maps),
    ]
    for path, date_map in date_maps:
        dimension, target = date_map.frequency_dimension, date_map.target_frequency_id
        if dimension is not None and target is not None:
            message = "names a frequency dimension and a target frequency, which the IM makes mutually exclusive"
            issues.append(ValidationIssue(path=path, message=message))
        elif dimension is None and target is None:
            message = "names neither a frequency dimension nor a target frequency, and every SDMX format requires one"
            issues.append(ValidationIssue(path=path, message=message))
        elif dimension is None and date_map.mapped_frequencies:
            message = "maps frequencies without a frequency dimension; mapped frequencies go with one"
            issues.append(ValidationIssue(path=f"{path}.mapped_frequencies", message=message))


# Order matters: subclass checks come before MaintainableArtefact fallback.
_DISPATCH: tuple[tuple[type, Any], ...] = (
    (Codelist, _validate_codelist),
    (ConceptScheme, _validate_concept_scheme),
    (CategoryScheme, _validate_category_scheme),
    (AgencyScheme, _validate_agency_scheme),
    (DataProviderScheme, _validate_data_provider_scheme),
    (DataStructure, _validate_dsd),
    (Hierarchy, _validate_hierarchy),
    (DataConstraint, _validate_data_constraint),
    (AttachmentConstraint, _validate_attachment_constraint),
    (MetadataStructure, _validate_metadata_structure),
    (MetadataProviderScheme, _validate_maintainable),
    (DataConsumerScheme, _validate_data_consumer_scheme),
    (OrganisationUnitScheme, _validate_organisation_unit_scheme),
    (StructureMap, _validate_structure_map),
)


for _cls, _check in _DISPATCH:
    register(_cls, _check)


# ── Advisories: rules the IM states and no wire schema enforces (#481) ──
#
# Breaking one builds the artefact anyway, with an `ImRuleWarning`: every SDMX
# format can carry it, and refusing would break documents and builders that
# work today. Each `_check_` below is cited in the IM rule census as warned.


def _check_facet_sequence(fmt: Facet, path: str, issues: list[ValidationIssue], *, time_format: bool = False) -> None:
    """The sequence facets go together, as IM §3.6.3.3 states (#482).

    A sequence needs a start and an interval: `start_value` with a numeric
    `interval`, `start_time` with a `time_interval`. An interval, a start or an
    end means nothing outside a sequence. The XSDs type each facet on its own,
    so every format carries any combination.

    A time dimension's format (`time_format`) is the exception the schemas
    make: `TimeTextFormatType` allows `startTime` and `endTime` and prohibits
    `isSequence` and `timeInterval`, so there the two are a range's bounds.
    """
    sequence = fmt.is_sequence is True
    numeric = ("interval", "start_value", "end_value")
    timed = ("time_interval", "start_time", "end_time")
    stated = [
        name
        for name in (*numeric, *timed)
        if getattr(fmt, name) is not None and not (time_format and name in {"start_time", "end_time"})
    ]
    if not sequence:
        if stated:
            issues.append(ValidationIssue(path=path, message=f"{', '.join(stated)} without is_sequence=True"))
        return
    if fmt.interval is None and fmt.time_interval is None:
        issues.append(ValidationIssue(path=path, message="is_sequence=True with no interval or time_interval"))
    for interval, start, bounds in (("interval", "start_value", numeric), ("time_interval", "start_time", timed)):
        has_interval = getattr(fmt, interval) is not None
        if has_interval and getattr(fmt, start) is None:
            issues.append(ValidationIssue(path=path, message=f"a sequence with {interval} needs {start}"))
        bounded = [name for name in bounds[1:] if getattr(fmt, name) is not None]
        if bounded and not has_interval:
            issues.append(ValidationIssue(path=path, message=f"{', '.join(bounded)} without {interval}"))


def _facet_sequences(owner: object, path: str, issues: list[ValidationIssue]) -> None:
    """`_check_facet_sequence` over a component's or concept's representation and enumeration format."""
    for field_name in ("representation", "core_representation", "enumeration_format"):
        fmt: object = getattr(owner, field_name, None)
        if isinstance(fmt, Facet):
            _check_facet_sequence(fmt, f"{path}.{field_name}", issues)


def _advise_dsd(dsd: DataStructure, prefix: str, issues: list[ValidationIssue]) -> None:
    for kind in ("dimensions", "attributes", "measures"):
        for component in getattr(dsd, kind):
            _facet_sequences(component, f"{prefix}.{kind}[{component.id}]", issues)
    if dsd.measure_dimension is not None:
        _facet_sequences(dsd.measure_dimension, f"{prefix}.measure_dimension", issues)
    if dsd.time_dimension is not None and dsd.time_dimension.representation is not None:
        path = f"{prefix}.time_dimension.representation"
        _check_facet_sequence(dsd.time_dimension.representation, path, issues, time_format=True)


def _advise_concept_scheme(cs: ConceptScheme, prefix: str, issues: list[ValidationIssue]) -> None:
    for concept in cs.concepts:
        _facet_sequences(concept, f"{prefix}.concepts[{concept.id}]", issues)


def _advise_metadata_structure(msd: MetadataStructure, prefix: str, issues: list[ValidationIssue]) -> None:
    def _walk(attr: MetadataAttribute, path: str) -> None:
        _facet_sequences(attr, path, issues)
        for child in attr.children:
            _walk(child, f"{path}.children[{child.id}]")

    for attr in msd.attributes:
        _walk(attr, f"{prefix}.attributes[{attr.id}]")
    for target in msd.targets:
        for obj in target.target_objects:
            _facet_sequences(obj, f"{prefix}.targets[{target.id}].target_objects[{obj.id}]", issues)


def _advise_hierarchy(h: Hierarchy, prefix: str, issues: list[ValidationIssue]) -> None:
    for level in h.levels:
        if level.coding_format is not None:
            _check_facet_sequence(level.coding_format, f"{prefix}.levels[{level.id}].coding_format", issues)


def _check_constraint_choice(dc: DataConstraint, prefix: str, issues: list[ValidationIssue]) -> None:
    """Cube regions or key sets, not both, as IM §12.3.3.1 states (#491).

    The XSDs put both in one sequence, so every format carries both. FMR 12.4.2
    refuses such a constraint "for maintenance purposes".
    """
    if dc.regions and dc.data_key_sets:
        message = "holds both cube regions and data key sets; the IM gives a data constraint one or the other"
        issues.append(ValidationIssue(path=prefix, message=message))


#: A structure map's source and target kinds: a structure, or a structure usage.
_STRUCTURE_KIND = {
    "DataStructure": "structure",
    "MetadataStructure": "structure",
    "Dataflow": "structure usage",
    "Metadataflow": "structure usage",
}


def _check_structure_map_kinds(sm: StructureMap, prefix: str, issues: list[ValidationIssue]) -> None:
    """A structure map maps two structures or two structure usages, not one of each (IM §9.1.2.1).

    The XSDs type both ends `StructureOrUsageReferenceType`, so every format
    carries a mixed pair.
    """
    if sm.source is None or sm.target is None:
        return
    source = _STRUCTURE_KIND.get(sm.source.urn.artefact_class)
    target = _STRUCTURE_KIND.get(sm.target.urn.artefact_class)
    if source is not None and target is not None and source != target:
        message = f"maps a {source} to a {target}; the IM maps two structures or two structure usages"
        issues.append(ValidationIssue(path=prefix, message=message))


def _check_component_map_representation(sm: StructureMap, prefix: str, issues: list[ValidationIssue]) -> None:
    """A component map with more than one source or target names a representation map (IM §9.1.2.1).

    `ComponentMapType` makes `RepresentationMap` optional in every case.
    """
    for i, component_map in enumerate(sm.maps):
        many = len(component_map.sources) > 1 or len(component_map.targets) > 1
        if many and component_map.representation_map is None:
            message = (
                f"maps {len(component_map.sources)} sources to {len(component_map.targets)} targets with no "
                "representation map; the IM requires one to say how the values map"
            )
            issues.append(ValidationIssue(path=f"{prefix}.maps[{i}]", message=message))


def _advise_structure_map(sm: StructureMap, prefix: str, issues: list[ValidationIssue]) -> None:
    _check_structure_map_kinds(sm, prefix, issues)
    _check_component_map_representation(sm, prefix, issues)


def _check_mapping_arity(rm: RepresentationMap, prefix: str, issues: list[ValidationIssue]) -> None:
    """A mapping gives one source value per source, and one target value per target when it gives any (IM §10.1.2.1).

    "The RepresentationMapping sourceValues and targetValues must match the
    order" of the map's sources and targets. `ValueMappingType` bounds neither
    count, and its `TargetValue` is optional.
    """
    for i, mapping in enumerate(rm.mappings):
        wrong_sources = rm.sources and len(mapping.sources) != len(rm.sources)
        wrong_targets = rm.targets and mapping.targets and len(mapping.targets) != len(rm.targets)
        if wrong_sources or wrong_targets:
            message = (
                f"maps {len(mapping.sources)} source and {len(mapping.targets)} target values, and the map has "
                f"{len(rm.sources)} sources and {len(rm.targets)} targets; the values match them in order"
            )
            issues.append(ValidationIssue(path=f"{prefix}.mappings[{i}]", message=message))


register_advisory(StructureMap, _advise_structure_map)
register_advisory(RepresentationMap, _check_mapping_arity)
register_advisory(DataStructure, _advise_dsd)
register_advisory(ConceptScheme, _advise_concept_scheme)
register_advisory(MetadataStructure, _advise_metadata_structure)
register_advisory(Hierarchy, _advise_hierarchy)
register_advisory(DataConstraint, _check_constraint_choice)


def validate(msg: StructureMessage | MetadataMessage) -> None:
    """Check what only a message can know: its references resolve, and no artefact is in it twice.

    An artefact is checked as it is built: every rule that depends on
    it alone runs in its constructor and in `attrs.evolve`, so none can fail
    here, and passing one says so rather than appear to check it. What the
    target format can carry is `serialize`'s question (`check_serialisable`).

    Closed-world, as `validate_references` is: a reference must name an
    artefact in the message, so a partial bundle — a dataflow without its DSD —
    fails, and is resolved against a richer registry instead.

    It warns with one `ImRuleWarning` on an IM rule between artefacts that no
    wire schema can enforce: a dataflow bound to the latest minor version of
    an evolving DSD with no dimension constraint (IM §5.3.2.1), and a data
    constraint value its component's codelist does not hold (§12.3.3.1).

    Args:
        msg: A `StructureMessage`; a `MetadataMessage` holds no references to check.

    Raises:
        ValidationError: If a maintainable is in the message twice.
        ReferenceValidationError: If any reference fails to resolve.
        TypeError: If *msg* is an artefact or anything else.

    Example:
        validate(msg)   # no return; raises on any issue
    """
    if isinstance(msg, MetadataMessage):
        return
    if not isinstance(msg, StructureMessage):
        msg_text = (
            f"validate() takes a message, not {type(msg).__name__}; an artefact is checked as it is "
            "built (#492), so there is nothing left to validate on its own"
        )
        raise TypeError(msg_text)
    issues: list[ValidationIssue] = []
    _check_duplicate_maintainables(msg, issues)
    if issues:
        raise ValidationError(issues)
    validate_references(msg)
    advisories: list[ValidationIssue] = []
    _check_evolving_structure_constraints(msg, advisories)
    _check_constraint_values(msg, advisories)
    advise(advisories)


def metadata_sets_advisories(
    artefacts: Iterable[object], lookup: Callable[[SdmxUrn[object]], object]
) -> list[ValidationIssue]:
    """`metadata_set_advisories` over the metadata sets among `artefacts`, each structure looked up once.

    Args:
        artefacts: What a registry just added.
        lookup: The registry's `get`.

    Returns:
        Every rule the sets break, for one `_invariants.advise`.
    """
    found: dict[str, object] = {}

    def cached(urn: SdmxUrn[object]) -> object:
        key = str(urn)
        if key not in found:
            found[key] = lookup(urn)
        return found[key]

    return [
        issue
        for artefact in artefacts
        if isinstance(artefact, MetadataSet)
        for issue in metadata_set_advisories(artefact, cached)
    ]


def metadata_set_advisories(ms: MetadataSet, lookup: Callable[[SdmxUrn[object]], object]) -> list[ValidationIssue]:
    """What a metadata set breaks of IM §7.4.2.1, against the structures `lookup` finds.

    For the registries, which hold a set beside its metadataflow and MSD: a
    message carries one or the other. Nothing is checked of a structure
    `lookup` does not find, so a set added before its metadataflow is not
    checked.

    Args:
        ms: The metadata set.
        lookup: A registry's `get`: an artefact by URN, or None.

    Returns:
        The rules the set breaks, for `_invariants.advise`.
    """
    structure = lookup(ms.structure.urn)
    if isinstance(structure, MetadataProvisionAgreement):
        structure = None if structure.metadataflow is None else lookup(structure.metadataflow.urn)
    if not isinstance(structure, Metadataflow):
        return []
    issues: list[ValidationIssue] = []
    _check_metadata_set_targets(ms, structure, issues)
    msd = None if structure.structure is None else lookup(structure.structure.urn)
    if isinstance(msd, MetadataStructure):
        _check_metadata_set_attributes(ms, msd, issues)
    return issues


def _wildcard_urn(pattern: str) -> re.Pattern[str]:
    """A `WildcardUrnType` as a regular expression: `*` stands for any run of characters."""
    return re.compile(".*".join(re.escape(part) for part in pattern.split("*")))


def _check_metadata_set_targets(ms: MetadataSet, flow: Metadataflow, issues: list[ValidationIssue]) -> None:
    """A metadata set's targets are among its metadataflow's (IM §7.4.2.1).

    "The identifiable objects to which the metadata is to be attached must be
    part of those specified in the relevant Metadataflow or
    MetadataProvisionAgreement." A metadataflow names its targets as
    wildcard URNs; one that names none restricts nothing here.
    """
    if not flow.targets:
        return
    patterns = [_wildcard_urn(target) for target in flow.targets]
    for i, target in enumerate(ms.targets):
        urn = str(target.urn)
        if not any(pattern.fullmatch(urn) for pattern in patterns):
            message = f"targets {urn}, which none of {flow.urn}'s targets ({', '.join(flow.targets)}) covers"
            issues.append(ValidationIssue(path=f"MetadataSet:{ms.id}.targets[{i}]", message=message))


def _check_metadata_set_attributes(ms: MetadataSet, msd: MetadataStructure, issues: list[ValidationIssue]) -> None:
    """A metadata set reports the attributes its MSD defines, as often as it allows (IM §7.4.2.1).

    "The MetadataSet must conform to the MetadataStructureDefinition": each
    reported attribute is one the MSD defines at that place in its tree,
    reported between its `min_occurs` and `max_occurs` times, and a
    presentational one carries no value. An uncoded value against its
    attribute's text format is not checked (GH-297).
    """

    def walk(reported: Sequence[ReportedAttribute], slots: Sequence[MetadataAttribute], path: str) -> None:
        by_id = {slot.id: slot for slot in slots}
        for attribute in reported:
            if attribute.id not in by_id:
                message = f"reports {attribute.id}, which {msd.urn} does not define here"
                issues.append(ValidationIssue(path=f"{path}[{attribute.id}]", message=message))
        for slot in slots:
            found = [attribute for attribute in reported if attribute.id == slot.id]
            if len(found) < slot.min_occurs or (slot.max_occurs is not None and len(found) > slot.max_occurs):
                bound = "unbounded" if slot.max_occurs is None else slot.max_occurs
                message = f"reports {slot.id} {len(found)} times, and {msd.urn} allows {slot.min_occurs}..{bound}"
                issues.append(ValidationIssue(path=f"{path}[{slot.id}]", message=message))
            for attribute in found:
                if slot.is_presentational and attribute.value is not None:
                    message = f"gives {slot.id} a value, and {msd.urn} makes it presentational, holding none"
                    issues.append(ValidationIssue(path=f"{path}[{slot.id}].value", message=message))
                walk(list(attribute.children), list(slot.children), f"{path}[{slot.id}].children")

    walk(list(ms.attributes), list(msd.attributes), f"MetadataSet:{ms.id}.attributes")


def _held_as[T](urn: SdmxUrn[object], held: Sequence[T]) -> list[T]:
    """The artefacts of `held` that `urn` names.

    Its version; the newest for *latest*; or each version a late-bound one takes.
    """
    same = [a for a in held if isinstance(a, MaintainableArtefact) and (a.agency_id, a.id) == (urn.agency, urn.id)]
    if urn.is_latest:
        newest = max(same, key=lambda a: sort_key(a.version), default=None)
        return [] if newest is None else [newest]
    return [a for a in same if a.version == urn.version or late_binds(urn.version, a.version)]


def _constrained_structures(dc: DataConstraint, msg: StructureMessage) -> list[DataStructure]:
    """The DSDs of the message `dc` applies to: named, or reached through a dataflow or provision agreement."""
    attachment = dc.attachment
    if attachment is None:
        return []
    flows = [f for ref in attachment.dataflows for f in _held_as(ref.urn, msg.dataflows)]
    for ref in attachment.provision_agreements:
        for agreement in _held_as(ref.urn, msg.provision_agreements):
            if agreement.dataflow is not None:
                flows.extend(_held_as(agreement.dataflow.urn, msg.dataflows))
    structure_urns = [ref.urn for ref in attachment.data_structures]
    structure_urns += [flow.structure.urn for flow in flows if flow.structure is not None]
    found: dict[str, DataStructure] = {}
    for urn in structure_urns:
        for dsd in _held_as(urn, msg.data_structures):
            found[str(dsd.urn)] = dsd
    return list(found.values())


def _enumerations(dsd: DataStructure, msg: StructureMessage) -> dict[str, tuple[str, frozenset[str]]]:
    """Component id → (enumeration URN, its item ids), for each component the message holds the enumeration of.

    A codelist with extensions is left out: the codes it inherits are its
    extensions' to say.
    """
    held: Sequence[Codelist | ValueList] = [*msg.codelists, *msg.value_lists]
    out: dict[str, tuple[str, frozenset[str]]] = {}
    components = [*dsd.dimensions, *dsd.attributes, *dsd.measures]
    for component in components:
        representation = component.representation
        if not isinstance(representation, Ref):
            continue
        for enumeration in _held_as(representation.urn, held):
            if isinstance(enumeration, Codelist) and enumeration.extensions:
                continue
            out[component.id] = (str(enumeration.urn), frozenset(enumeration.code_ids()))
    return out


def _check_constraint_values(msg: StructureMessage, issues: list[ValidationIssue]) -> None:
    """A cube region's values are items of the enumeration the DSD gives the component (IM §12.3.3.1).

    "The value in ComponentValue.value and MemberValue.value must be
    consistent with the Representation declared for the Component." A value
    no schema can check: the representation is another artefact's. Checked
    where the message holds the constraint's DSD and the component's codelist
    or value list; a value set with `remove_prefix` holds values with the
    codes' prefix removed and is not compared. A value against a text format
    is not checked (GH-297).
    """
    for dc in msg.constraints:
        for dsd in _constrained_structures(dc, msg):
            enumerations = _enumerations(dsd, msg)
            for r, region in enumerate(dc.regions):
                selections: list[tuple[str, str, KeyValue | ComponentValueSet]] = [
                    (f"keys[{kv.dimension_id}]", kv.dimension_id, kv) for kv in region.keys
                ]
                selections += [(f"components[{c.component_id}]", c.component_id, c) for c in region.components]
                for where, component_id, selection in selections:
                    if selection.remove_prefix or component_id not in enumerations:
                        continue
                    urn, items = enumerations[component_id]
                    unknown = sorted(v.value for v in selection.values if v.value not in items)
                    if unknown:
                        message = (
                            f"selects {', '.join(map(repr, unknown))}, which {urn} does not hold; {dsd.urn} "
                            f"enumerates {component_id} with it"
                        )
                        issues.append(
                            ValidationIssue(path=f"DataConstraint:{dc.id}.regions[{r}].{where}", message=message)
                        )


def _late_binds_minor(version: str) -> bool:
    """Whether a reference version takes later minor versions: a `+` on its major or minor part.

    `2.0+.0` and `2+.0.0` do; `2.0.0+`, which takes later patches of one
    minor, and a plain version do not.
    """
    return any(part.endswith("+") for part in version.split(".")[:2])


def _check_evolving_structure_constraints(msg: StructureMessage, issues: list[ValidationIssue]) -> None:
    """A dataflow late-bound to an evolving DSD's minor versions names its dimensions (IM §5.3.2.1).

    "A Dataflow may optionally define which Dimensions it uses, by defining a
    DimensionConstraint (this is a mandatory requirement if the
    DataStructureDefinition sets its' evolvingStructure property to 'true' and
    is sematically referenced by the Dataflow)" — referenced "on the latest
    minor version", by a `+` on the minor part (§5.3.2.2 and its footnote; the
    3.1 XSD's `DataflowType` says the same). A wildcard names no one DSD, so
    every DSD of the message it can bind to is asked whether it evolves.
    """
    for flow in msg.dataflows:
        if flow.structure is None or flow.dimension_constraint:
            continue
        ref = flow.structure.urn
        if not _late_binds_minor(ref.version):
            continue
        evolving = [
            dsd
            for dsd in msg.data_structures
            if dsd.evolving_structure
            and (dsd.maintainer.id, dsd.id) == (ref.agency, ref.id)
            and late_binds(ref.version, dsd.version)
        ]
        if evolving:
            message = (
                f"is bound to {ref} and {evolving[0].urn} is an evolving structure, so the IM requires a "
                "dimension constraint naming the dimensions it uses"
            )
            issues.append(ValidationIssue(path=f"Dataflow:{flow.id}.dimension_constraint", message=message))


# ── Cross-artefact reference validation ──────────────────────────────────────


def _collect_known_urns(artefacts: Iterable[MaintainableArtefact]) -> set[str]:
    """Every artefact URN the message carries, scheme-level and item-level.

    **Item URNs are what this adds.** This used to expand items for
    `ConceptScheme` and nothing else, so every `Code`, `Category`, `Dimension`
    and `MetadataAttribute` reference failed to resolve against a message
    plainly containing its target — and `_collect_refs`, on the other side of
    the compare, walks into items correctly (#383, #392), so the two halves
    disagreed about what an item is. On a real SDMX 3.0 message the check
    reported 1,388 broken references of which **zero** were broken: a caller
    could not filter the noise, because there was no signal in it (#404).

    Which fields hold items comes from `item_fields`, which reads the
    `sdmx_package` / `sdmx_class` pair the item classes declare. A table of
    (scheme, accessor) rows here is what this had in effect, and what it cost:
    a row is a thing to forget, and the one row present was written when
    concepts were the only item anyone had needed.

    One `SdmxUrn.make` per (scheme, item class) and a string concatenation per
    item, which is the same shape — and the same reasoning —
    `storage._item_urn_prefix` uses: every item URN of one scheme shares
    everything but its `item_id`, and `SdmxUrn.__str__` builds an item URN as
    exactly ``base + "." + item_id``. `tests/unit/test_validation.py`'s
    `test_item_urns_cost_one_make_per_scheme_and_item_class` asserts that count
    rather than a duration.

    What it does cost is one *string* per item, and at bundle scale that is
    worth stating: a 500,000-code codelist takes **215 to 229 ms** over three
    interleaved arms and holds 69 MB for the length of the call. That is the
    price of the fix rather than a regression — the arm without item expansion
    is 0.1 ms because it registers one URN and answers nothing. If a caller ever
    needs it cheaper, the lever is the key: a ``(package, agency, id, version,
    item_id)`` tuple, or an id set per scheme, neither of which renders a
    string. Both were considered and the URN string won on being the thing a
    reference actually carries.
    """
    known: set[str] = set()
    for artefact in artefacts:
        if not isinstance(artefact, MaintainableArtefact):
            continue
        try:
            urn: SdmxUrn[object] = SdmxUrn.make(
                type(artefact),
                package=artefact.sdmx_package,
                artefact_class=artefact.sdmx_class,
                agency=artefact.agency_id,
                id=artefact.id,
                version=artefact.version,
            )
        except ValueError:
            # A malformed agency or id — refused as it is built (#492), so only
            # reachable unchecked. Skipping the artefact skips its items with it.
            continue
        known.add(str(urn))
        _add_item_urns(artefact, urn, known, {})
    return known


def _item_prefix(scheme_urn: SdmxUrn[object], item_cls: type[SdmxItem]) -> str:
    """The prefix every item URN of one scheme and item class shares.

    The package comes from the *item* class, which is where SDMX puts it: an
    organisation is ``base.Agency`` whatever scheme holds it. It agrees with
    the scheme's package everywhere in the model — an `AgencyScheme` is also
    ``base`` — and reading it off the item is the declaration rather than the
    coincidence.

    Cannot raise: `SdmxUrn.make` validates only the agency and the id, and both
    are the ones the scheme URN was already built from.
    """
    base: SdmxUrn[object] = SdmxUrn.make(
        item_cls,
        package=item_cls.sdmx_package,
        artefact_class=item_cls.sdmx_class,
        agency=scheme_urn.agency,
        id=scheme_urn.id,
        version=scheme_urn.version,
    )
    return f"{base}."


def _add_item_urns(
    owner: object,
    scheme_urn: SdmxUrn[object],
    known: set[str],
    prefixes: dict[str, str],
    path: str = "",
) -> None:
    """Register the item URNs of *owner*, recursing into nested items.

    `prefixes` is keyed by item class and shared down the recursion, so a
    9,000-node category scheme costs one `SdmxUrn.make`, not 9,000.

    Two shapes, and the difference is not cosmetic. A **container** field holds
    the scheme's items and nests — `Category.categories`,
    `MetadataAttribute.children` — so its elements accumulate a dotted path. A
    **single** item field is either a component the scheme contains
    (`DataStructure.time_dimension`, which SDMX gives its own item URN and
    which no accessor column in a table could have named) or a same-scheme
    sibling pointer (`Agency.parent`, `HierarchicalCode.level`). Both belong at
    the scheme, flat, and neither nests through that field: the parent agency
    and the level are also in `agencies` and `levels`.

    A nested item is registered by its path alone — the item part SDMX gives
    its URN (``Category=AG:CS(1.0).PARENT.CHILD``). Its bare leaf id is not
    registered: it may repeat in another branch and so names no single item
    (#439), and SDMX's 2.1 ``<Ref>`` carries the path too (``@id`` is a
    ``NestedIDType``). Every real registry swept writes the path.
    """
    for name, item_cls in item_fields(type(owner)):
        value: object = getattr(owner, name, None)
        if value is None:
            continue
        prefix = prefixes.get(item_cls.sdmx_class)
        if prefix is None:
            prefix = _item_prefix(scheme_urn, item_cls)
            prefixes[item_cls.sdmx_class] = prefix
        if isinstance(value, (ItemList, ArtefactList, list, tuple)):
            for item in value:
                _add_nested_item(item, prefix, scheme_urn, known, prefixes, path)
        elif (single_id := _item_id(value)) is not None:
            known.add(prefix + single_id)


def _add_nested_item(
    item: object,
    prefix: str,
    scheme_urn: SdmxUrn[object],
    known: set[str],
    prefixes: dict[str, str],
    path: str,
) -> None:
    """One element of an item container, by path, then its children."""
    item_id = _item_id(item)
    if item_id is None:
        return
    nested = f"{path}.{item_id}" if path else item_id
    known.add(prefix + nested)
    _add_item_urns(item, scheme_urn, known, prefixes, nested)


def _item_id(item: object) -> str | None:
    """An item's id, or None when it has none to register."""
    item_id: object = getattr(item, "id", None)
    return item_id if isinstance(item_id, str) and item_id else None


def _versions_present(artefacts: Iterable[MaintainableArtefact]) -> dict[tuple[str, str, str], tuple[str, ...]]:
    """`(package, agency, id)` → the versions of it the message carries.

    What a version-less reference needs. SDMX writes "no version" for *latest*,
    and this check registers an artefact under the version it has — so a
    dataflow pointing at ``DataStructure=AG:DSD1`` read as broken while the DSD
    sat in the same message at 1.0.0. The readers' own resolution index has
    always registered both spellings (`_common.index_artefact`); this one
    registered neither alias, and the symptom was indistinguishable from #404's
    (a target plainly present, reported missing).

    Keyed by the *maintainable's* package, which is also an item URN's package:
    SDMX puts a scheme and its items in one package throughout — `Code` and
    `Codelist` are both ``codelist``, `Agency` and `AgencyScheme` both ``base``.
    """
    found: dict[tuple[str, str, str], list[str]] = {}
    for artefact in artefacts:
        if not isinstance(artefact, MaintainableArtefact):
            continue
        found.setdefault((artefact.sdmx_package, artefact.agency_id, artefact.id), []).append(artefact.version)
    return {key: tuple(versions) for key, versions in found.items()}


def _resolves(urn: SdmxUrn[object], known: set[str], versions: dict[tuple[str, str, str], tuple[str, ...]]) -> bool:
    """Whether *urn* names something the message contains, under any spelling.

    Exact match first, which is what nearly every reference gets and costs one
    set lookup — `SdmxUrn.__str__` is cached on the frozen URN, so even that
    string is already built. The alternates are computed only for a reference
    that just failed, so tolerating a spelling costs nothing per item and
    nothing per resolving reference.
    """
    if str(urn) in known:
        return True
    return any(str(alternate) in known for alternate in _alternate_spellings(urn, versions))


def _alternate_spellings(
    urn: SdmxUrn[object],
    versions: dict[tuple[str, str, str], tuple[str, ...]],
) -> Iterator[SdmxUrn[object]]:
    """The other URNs that would name the same artefact, excluding *urn* itself.

    Two axes, and they compose: the class spellings SDMX changed between
    versions (`_items.CLASS_ALIAS_GROUPS`) and, for a version-less reference,
    each version of that artefact the message actually carries; for a
    late-bound one (`1.0+.0`), each version it binds to (`version.late_binds`).
    A reference naming an explicit version is left alone — a different version
    is a different artefact, not a different spelling of this one.
    """
    present = versions.get((urn.package, urn.agency, urn.id), ())
    if urn.is_latest:
        resolved_versions = present
    else:
        resolved_versions = tuple(version for version in present if late_binds(urn.version, version))
    for artefact_class in spellings(urn.artefact_class):
        for version in (urn.version, *resolved_versions):
            if artefact_class == urn.artefact_class and version == urn.version:
                continue
            yield attrs.evolve(urn, artefact_class=artefact_class, version=version)


def _collect_refs(artefact: object, prefix: str) -> list[tuple[str, Ref[object]]]:
    """Every `(path, ref)` this artefact reaches, however deeply nested.

    **The descent comes from the class, not from a list here (#148).** This
    used to read the artefact's own `Ref`-typed fields and then hand-descend
    into `DataStructure` components and `Hierarchy` codes, which meant a `Ref`
    anywhere else was invisible — and `validate_references` documents itself as
    a *closed-world* check, so a ref it cannot see is a ref it silently passes.

    Measured before the change: a dangling concept ref on a
    `MetadataAttribute` was missed whether nested or top-level, and so was
    `Metadataflow.targets`, because a `Ref` in a plain list is in neither
    hand-listed type. Six model fields are bare `list`/`tuple` of `Ref` and
    every one of them was unreachable.

    `walk_fields` is the same per-class shape `local.registry._collect_refs`
    and `in_memory_registry._walk_refs` already resolve and intern by (#376),
    so this stops being the odd one out. It is conservative where it cannot
    read a declaration, which costs a wasted `getattr` and never a missed ref.

    The three walkers stay separate deliberately. They want different things
    at each ref — collect, intern, record a path — and two of them are the hot
    path #376 cut from 52% of an eager read, so a shared path-building
    generator would deoptimise them to spare this one its ten lines.
    """
    results: list[tuple[str, Ref[object]]] = []
    _walk_refs_into(artefact, prefix, results, set())
    return results


def _at(path: str, item: object, index: int) -> str:
    """One element's path — by id where it has one, by position otherwise.

    An error naming `attributes[REF_AREA]` is worth more than `attributes[3]`,
    and a bare `Ref` in a list has no id of its own — `Ref.id` is a property
    reading the URN, so this costs no resolution.
    """
    label = getattr(item, "id", None)
    return f"{path}[{label if isinstance(label, str) else index}]"


def _walk_refs_into(
    obj: object,
    prefix: str,
    results: list[tuple[str, Ref[object]]],
    seen: set[int],
) -> None:
    """Recursive half of `_collect_refs`, accumulating into `results`.

    `seen` is by `id()` and per call, mirroring the walkers this now matches:
    the model is a graph — a `Level` is shared by every node at that level —
    so without it a wide hierarchy reports the same ref once per referrer.
    """
    cls = type(obj)
    if isinstance(obj, type) or not can_reach_ref(cls):
        return
    oid = id(obj)
    if oid in seen:
        return
    seen.add(oid)

    for name in walk_fields(cls):
        value = getattr(obj, name, None)
        if value is None:
            continue
        path = f"{prefix}.{name}"
        if isinstance(value, Ref):
            results.append((path, value))
        elif isinstance(value, (ItemList, ArtefactList, list, tuple)):
            _walk_elements(value, path, results, seen)
        elif attrs.has(type(value)):
            _walk_refs_into(value, path, results, seen)


def _walk_elements(
    items: Iterable[object],
    path: str,
    results: list[tuple[str, Ref[object]]],
    seen: set[int],
) -> None:
    """One collection field's elements.

    **Both guards come before the path is built**, and that is the whole cost
    of this loop on a large scheme. A codelist's `codes` is walked now where
    the hand-listed descent skipped it entirely, and `can_reach_ref(Code)` is
    False — so each of 579,951 elements must cost a cached class lookup rather
    than an f-string and a `getattr`.

    Three arms measured back to back on an idle machine, since this project
    has been burned by wall-clock A/B under load before (#385):

    | | 10k codes | 100k codes | 50-dim DSD |
    | --- | --- | --- | --- |
    | the hand-listed walk | 0.0 ms | 0.0 ms | 26.3 us |
    | path built first | 1.0 ms | 10.7 ms | 36.2 us |
    | **guards first** | 0.4 ms | **4.3 ms** | 34.8 us |

    The old walk is 0 ms on a codelist because it never looked at one — that
    column is the price of the fix, not a regression against a working
    baseline. The DSD column is a real ~30% on a shape it did cover, which
    buys correctness on every shape it did not.
    """
    for index, item in enumerate(items):
        if isinstance(item, Ref):
            results.append((_at(path, item, index), item))
        elif can_reach_ref(type(item)) and not isinstance(item, type):
            _walk_refs_into(item, _at(path, item, index), results, seen)


def validate_references(msg: StructureMessage) -> None:
    """Check that all Refs within a message resolve to artefacts in that message.

    This is a closed-world check — every outgoing Ref must point to a URN
    of an artefact also present in the message. Partial bundles (e.g. a
    dataflow without its DSD) will fail and must use ref-resolution
    against a richer registry instead.

    An **item** reference resolves against the items of a scheme in the
    message: a `HierarchicalCode` pointing at a `Code`, a `Categorisation` at a
    `Category`, a `HierarchyAssociation` at the `Dimension` it organises. Three
    spellings of the same target are accepted, because all three are written in
    practice — a nested item by dotted path or by leaf id, a component under
    either the 2.1 or the 3.0 class name, and any artefact with its version
    omitted, which SDMX reads as *latest*.

    Raises:
        ReferenceValidationError: If any Ref fails to resolve.
            ``error.issues`` carries the full accumulated list.
    """
    issues = reference_issues(msg.all_artefacts())
    if issues:
        raise ReferenceValidationError(issues)


def reference_issues(
    artefacts: Sequence[MaintainableArtefact], also: Iterable[MaintainableArtefact] = ()
) -> list[ReferenceIssue]:
    """Every reference in `artefacts`, and in `also`, that resolves to nothing in `artefacts`.

    `validate_references` over a collection rather than a message, and without
    the raise. ``also`` holds artefacts whose references are checked without
    being targets themselves: a bundle's metadata sets, which a
    `StructureMessage` has no slot for, but whose metadataflows and targets
    must be in the store (GH-67).

    Args:
        artefacts: What references may resolve to, each checked too.
        also: More artefacts to check, which nothing resolves to.

    Returns:
        One issue per reference that resolves to nothing, in artefact order.
    """
    known = _collect_known_urns(artefacts)
    versions = _versions_present(artefacts)
    issues: list[ReferenceIssue] = []

    for artefact in [*artefacts, *also]:
        cls_name = type(artefact).__name__
        artefact_id = getattr(artefact, "id", "?")
        prefix = f"{cls_name}:{artefact_id}"
        for path, ref in _collect_refs(artefact, prefix):
            if not _resolves(ref.urn, known, versions):
                issues.append(ReferenceIssue(source=path, target_urn=str(ref.urn)))
    return issues


#: Values `DataType` permits that SDMX-ML 2.1 does not accept on a *component*
#: TextFormat. 2.1 restricts components to `SimpleDataType` (39 of 44 values);
#: `XHTML` is legal in 3.0/3.1 and at other 2.1 positions, so this is a
#: downgrade hazard rather than a bad value (#224).
_COMPONENT_DATATYPE_NOT_IN_21 = {"XHTML", "GeospatialInformation"}

#: SDMX-ML 2.1 pins PrimaryMeasure/@id to this exact value; 3.0 permits any id.
_PRIMARY_MEASURE_ID_21 = "OBS_VALUE"

#: `StructureMessage` fields a given target cannot carry, keyed by wire
#: version, with the reason. Until #225 these were dropped *silently*: a
#: message carrying a metadataflow written as 2.1 came back with none, no
#: error and no warning, and the emitted document was schema-valid so nothing
#: downstream noticed either.
#:
#: The 2.1 entries split two ways. ``metadataflows`` and ``metadata_structures``
#: are *unimplemented* rather than unrepresentable — 2.1's ``<Structures>`` has
#: first-class children for both, so they can be lifted by writing the
#: emitters. A ``HierarchyAssociation`` was here too, until #421 left it out
#: of 2.1 as a class the format lacks (`formats.gaps.dropped`), as 3.x and
#: SDMX-JSON now leave out a ``HierarchicalCodelist`` and a ``StructureSet``.
#:
#: ``attachment_constraints`` is the opposite direction: SDMX 3.0 *removed*
#: AttachmentConstraint, folding its role into DataConstraint. There is no
#: ``<AttachmentConstraints>`` child of 3.0's ``<Structures>`` and no
#: counterpart in the SDMX-JSON 2.1 schema, so a 2.1-sourced message carrying
#: one loses it on every upgrade path (#226).
#:
#: Kept honest by `test_unwritable_lists_exactly_what_each_writer_drops`, which
#: round-trips one artefact of each kind. An earlier version of this table was
#: assembled by grepping the writer for field accesses and wrongly named all
#: four map types — the 2.1 writer emits every one of them, so the table
#: refused four serialisations that had always worked. What a writer does is a
#: question for the writer, not for a regex over it.
_UNWRITABLE: dict[str, dict[str, str]] = {
    "2.1": {
        # `metadataflows` was here until #225 implemented the emitter. 2.1
        # carries a metadataflow fine; what it cannot carry is the *targets*,
        # which 3.0 added — recorded in KNOWN_GAPS rather than refused, since
        # refusing would block every 2.1 downgrade rather than an unusual one.
        # `metadata_provision_agreements` and `metadata_provider_schemes` were
        # here, as classes 2.1's <Structures> has no child for. Since #421 they
        # are written as 2.1 provisioned metadata itself: an agreement over a
        # metadataflow, naming a provider in the agency's data provider scheme
        # (`_check_metadata_provision_in_21`).
        # `structure_maps` was here until #242. The reason given was that 2.1's
        # ComponentMap <Ref> needs a class/package/containerID "that
        # ComponentMap does not carry" — true of the model, false of the
        # message: all three are recoverable from the structure the map names.
        # `_check_structure_map_in_21` now refuses only the maps whose
        # structure is genuinely out of reach.
    },
}


def _check_writer_coverage(msg: StructureMessage, version: str, issues: list[ValidationIssue]) -> None:
    """Refuse to write a message whose artefacts the target writer would drop.

    Silent loss is the worst failure mode a serialiser has: the call succeeds,
    the document looks fine, and the artefacts are simply gone. Raising here
    turns it into a question the caller can answer — write 3.0, or drop the
    artefacts deliberately.
    """
    for field_name, reason in _UNWRITABLE.get(version, {}).items():
        carried = getattr(msg, field_name, ())
        if carried:
            issues.append(
                ValidationIssue(
                    path=f"StructureMessage.{field_name}",
                    message=(
                        f"{version} cannot carry {len(carried)} {field_name} — {reason}. "
                        "Write a version that supports them, or remove them before serialising"
                    ),
                )
            )


#: The only text types a 2.1 target object's representation may declare.
#: ``TargetObjectTextFormatType`` restricts ``textType`` to four values, where
#: `DataType` has forty-four — a target names *what kind of thing* is being
#: described, not a value domain (#245). A ``ReportPeriodTarget`` is the
#: exception: its format is ``TimeTextFormatType``, so a `_TIME_DATA_TYPES`
#: one (#422).
_TARGET_REPRESENTATION_TYPES = frozenset(
    {"KeyValues", "IdentifiableReference", "DataSetReference", "AttachmentConstraintReference"}
)


def _check_msd_in_21(msd: MetadataStructure, version: str, issues: list[ValidationIssue]) -> None:
    """A 2.1 MSD needs the reporting layer SDMX 3.0 deleted.

    ``MetadataStructureComponents`` is required and must carry a
    ``<MetadataTarget>`` and a ``<ReportStructure>``. sdmxlib carries both on
    `MetadataStructure` so a 2.1 document round-trips, but an MSD built for
    3.0 — or read from it — has neither, and there is nothing to write.

    An MSD with neither is written with an invented layer, marked so the
    reader drops it and 3.0 -> 2.1 -> 3.0 returns its input (#421) — refused
    until then (#245). One with half a layer is still refused: the writer
    would have to invent the other half to match what it was given.
    """
    if version != "2.1":
        return
    for target in msd.targets:
        path = f"MetadataStructure:{msd.id}.targets[{target.id}]"
        if not target.target_objects:
            issues.append(
                ValidationIssue(
                    path=f"{path}.target_objects",
                    message=(
                        "SDMX-ML 2.1 requires a MetadataTarget to hold at least one target object; this one has none"
                    ),
                )
            )
        for obj in target.target_objects:
            fmt = obj.representation
            # A report period's format is `TimeTextFormatType`: a time type,
            # not one of the four references the other kinds take.
            allowed = _TIME_DATA_TYPES if obj.kind == "ReportPeriodTarget" else _TARGET_REPRESENTATION_TYPES
            if fmt is None or fmt.type is None or str(fmt.type) in allowed:
                continue
            issues.append(
                ValidationIssue(
                    path=f"{path}.target_objects[{obj.id}].representation.type",
                    message=f"a {obj.kind} representation may only declare {sorted(allowed)}, got {fmt.type!s}",
                )
            )
    # Neither is a 3.x MSD, written with a marked layer of its own (#421);
    # both is a 2.1 one. Only half a layer has no 2.1 form.
    if not msd.targets and not msd.report_structures and not msd.attributes:
        issues.append(
            ValidationIssue(
                path=f"MetadataStructure:{msd.id}.attributes",
                message=(
                    "SDMX-ML 2.1 writes an MSD's attributes in a ReportStructure, which requires at "
                    "least one MetadataAttribute; this MSD has none"
                ),
            )
        )
        return
    if bool(msd.targets) == bool(msd.report_structures):
        return
    missing = "ReportStructure" if msd.targets else "MetadataTarget"
    issues.append(
        ValidationIssue(
            path=f"MetadataStructure:{msd.id}",
            message=(
                f"SDMX-ML 2.1 requires a MetadataTarget and a ReportStructure on every MetadataStructure; "
                f"this MSD has the one and no {missing}. Populate both `targets` and `report_structures`, "
                "or neither, which writes one target of any object and one report of every attribute"
            ),
        )
    )


def _check_representation_map_in_21(msg: StructureMessage, version: str, issues: list[ValidationIssue]) -> None:
    """A 2.1 CodelistMap maps codes to codes, and nothing else.

    ``CodelistMapType`` requires a Source and a Target codelist and a body of
    ``<CodeMap>`` pairs, each side a local *code* reference. SDMX 3.0 widened
    all three: an endpoint may be a datatype rather than a codelist, and a
    mapping may be a regex or an index slice rather than a literal pair.

    None of that had a 2.1 form, and the writer emitted it anyway — a regex
    map went out as a ``<CodeMap>`` whose source id was the pattern text, so
    ``^M-[0-9]+$`` came back as a literal code with that name. The value
    survived and its meaning did not, which is worse than losing it: the
    document asserts a mapping the source never stated, and the id is not even
    a valid ``IDType``. A datatype endpoint simply vanished, leaving a
    ``<CodelistMap>`` with no Source at all, which the schema rejects.

    So both are refused here (#242). A literal codelist-to-codelist map — the
    2.1 shape, and the common one — still converts, and so does a value map a
    2.1 reader built, which goes back inline (#422).
    """
    if version != "2.1":
        return
    for rm in msg.representation_maps:
        if is_built_value_map(rm):
            continue
        path = f"RepresentationMap:{rm.id}"
        for end, endpoints in (("sources", rm.sources), ("targets", rm.targets)):
            if len(endpoints) != 1:
                issues.append(
                    ValidationIssue(
                        path=f"{path}.{end}",
                        message=(
                            f"SDMX-ML 2.1 maps one codelist to one other — CodelistMapType requires "
                            f"exactly one {end[:-1]} — but this map has {len(endpoints)}; serialise to 3.0"
                        ),
                    )
                )
            issues.extend(
                ValidationIssue(
                    path=f"{path}.{end}",
                    message=(
                        f"SDMX-ML 2.1 maps codelists to codelists — CodelistMapType requires a "
                        f"{end[:-1]} codelist — but this map's {end[:-1]} is the datatype "
                        f"{endpoint.data_type!r}, which has no 2.1 form; serialise to 3.0"
                    ),
                )
                for endpoint in endpoints
                if isinstance(endpoint, MapDataType)
            )
        issues.extend(
            ValidationIssue(path=f"{path}.mappings[{i}]", message=reason)
            for i, m in enumerate(rm.mappings)
            if (reason := _mapping_unwritable_in_21(m)) is not None
        )


def _mapping_unwritable_in_21(mapping: RepresentationMapping) -> str | None:
    """Why a 2.1 ``CodeMap`` cannot say what `mapping` says, or None if it can.

    ``CodeMapType`` is one code to one code. A regex or substring source
    written as one would restate its pattern as a literal code — asserting a
    mapping the source never made (#242) — and an N:M mapping cut to its
    first pair would do the same (#422).
    """
    match mapping.sources, mapping.targets:
        case (source,), (_,) if source.is_literal:
            return None
        case (source,), (_,):
            kind = "regex" if source.is_regex else "source-index"
            return (
                f"SDMX-ML 2.1 maps one code to another; a {kind} mapping has no 2.1 form. Writing it "
                f"as a plain pair would restate {source.value!r} as a literal code, asserting a "
                f"mapping the source never made; serialise to 3.0"
            )
        case sources, targets:
            return (
                f"SDMX-ML 2.1 maps one code to one code; this mapping has {len(sources)} source and "
                f"{len(targets)} target values, and cutting it to one pair would assert a mapping "
                f"the source never made; serialise to 3.0"
            )


def _check_text_format_mappings_in_21(msg: StructureMessage, version: str, issues: list[ValidationIssue]) -> None:
    """A 2.1 ``<ToTextFormat>`` goes with a ``<ToValueType>``, and instead of a map (#422).

    ``RepresentationMapType`` is a choice of a codelist map, a value map, or
    the sequence ToTextFormat-then-ToValueType: one without the other is not
    a document, and either beside a `representation_map` is two answers to a
    question 2.1 lets a component map answer once.
    """
    if version != "2.1":
        return
    for sm in msg.structure_maps:
        for i, cm in enumerate(sm.maps):
            path = f"StructureMap:{sm.id}.maps[{i}]"
            if (cm.to_text_format is None) != (cm.to_value_type is None):
                issues.append(
                    ValidationIssue(
                        path=f"{path}.to_value_type",
                        message=(
                            "SDMX-ML 2.1 writes a ToTextFormat and a ToValueType together; "
                            "this component map has one of them"
                        ),
                    )
                )
            elif cm.to_text_format is not None and cm.representation_map is not None:
                issues.append(
                    ValidationIssue(
                        path=f"{path}.to_text_format",
                        message=(
                            "SDMX-ML 2.1 maps a component's values one way — a codelist map, a value map or a "
                            "text format — and this component map has both a representation map and a text format"
                        ),
                    )
                )


def _check_structure_map_in_21(msg: StructureMessage, version: str, issues: list[ValidationIssue]) -> None:
    """A 2.1 StructureMap must name its components the way 2.1 references them.

    SDMX 2.1 references a mapped component *locally*: id, object class and the
    id of the ComponentList holding it, all three required. SDMX 3.0 carries
    only the id. The missing two are not information 3.0 dropped — they follow
    from the structure the map already names — so this resolves that structure
    and reads them off it, and refuses only when it cannot.

    Two cases genuinely cannot be written, and both are the format's rule
    rather than ours:

    - a map with no ComponentMaps, since ``StructureMapType`` requires at
      least one;
    - a component id the source or target structure does not define, which no
      amount of resolution can turn into a class.

    A third is ours only in the sense that the caller controls it: the map's
    structure is not in the message. sdmxlib does not fetch during
    serialisation, so there is nothing to read the class off — the message has
    to carry the structures the map refers to.
    """
    if version != "2.1":
        return
    for sm in msg.structure_maps:
        path = f"StructureMap:{sm.id}"
        if not sm.maps:
            issues.append(
                ValidationIssue(
                    path=f"{path}.maps",
                    message=(
                        "SDMX-ML 2.1 requires at least one ComponentMap on every StructureMap; "
                        "this one maps no components, so there is nothing to write"
                    ),
                )
            )
        issues.extend(
            ValidationIssue(
                path=f"{path}.maps[{i}]",
                message=(
                    f"SDMX-ML 2.1 maps one component to one component; this map has "
                    f"{len(cm.sources)} sources and {len(cm.targets)} targets, and cutting it to one "
                    f"pair would assert a mapping the source never made; serialise to 3.0"
                ),
            )
            for i, cm in enumerate(sm.maps)
            if len(cm.sources) != 1 or len(cm.targets) != 1
        )
        for end, ref, ids in (
            ("source", sm.source, [cid for cm in sm.maps for cid in cm.sources]),
            ("target", sm.target, [cid for cm in sm.maps for cid in cm.targets]),
        ):
            dsd = mapped_structure(ref, msg.data_structures, msg.dataflows)
            if dsd is None:
                if not sm.maps:
                    continue
                named = "nothing" if ref is None else str(ref.urn)
                issues.append(
                    ValidationIssue(
                        path=f"{path}.{end}",
                        message=(
                            f"SDMX-ML 2.1 references a mapped component by class and component-list "
                            f"id as well as by id, both read off the {end} structure — but this map's "
                            f"{end} is {named}, which the message does not carry. Include the "
                            f"structure (or its dataflow) in the message, or serialise to 3.0, which "
                            f"references components by id alone"
                        ),
                    )
                )
                continue
            unknown = [cid for cid in ids if component_location(dsd, cid) is None]
            issues.extend(
                ValidationIssue(
                    path=f"{path}.maps[{cid}]",
                    message=(
                        f"the {end} structure {dsd.id} defines no component {cid!r}, so SDMX-ML 2.1 "
                        f"has no class to reference it by; the components are "
                        f"{sorted(c.id for c in dsd.components)}"
                    ),
                )
                for cid in unknown
            )


def _check_metadataflow_targets(
    mdf: Metadataflow, version: str, msds: Iterable[MetadataStructure], issues: list[ValidationIssue]
) -> None:
    """A 3.0/3.1 metadataflow must name at least one target.

    ``MetadataflowType`` declares ``<Target>`` with ``minOccurs=1`` in both
    versions — the targets are what the reported metadata is *about*, so a
    metadataflow without them says nothing. The model defaults ``targets`` to
    ``()``, which serialises to a document the schema rejects (#225).

    A flow read from SDMX-ML 2.1 has none, because 2.1 declares them in the
    metadata structure. Since #421 the writer derives them from it
    (`Metadataflow.with_derived_targets`), so only a flow whose structure
    cannot give them is refused. SDMX-ML 2.1 has no Target element at all,
    so none of this applies there.
    """
    if version == "2.1" or mdf.targets:
        return
    misfits = mdf.target_misfits(msds)
    if not misfits:
        return
    issues.append(
        ValidationIssue(
            path=f"Metadataflow:{mdf.id}.targets",
            message=(
                f"SDMX-ML {version} requires at least one <str:Target> on a Metadataflow, but targets is empty "
                f"and cannot be derived from its metadata structure: {'; '.join(misfits)}"
            ),
        )
    )


def _check_metadata_provision_refs(
    mpa: MetadataProvisionAgreement, version: str, issues: list[ValidationIssue]
) -> None:
    """A metadata provision agreement must name its flow and its provider.

    `MetadataProvisionAgreementType` declares `<Metadataflow>` and
    `<MetadataProvider>` with the default `minOccurs=1`, so an agreement
    missing either serialises to a document the schema rejects — the same
    shape as `_check_metadataflow_targets` and found the same way, by the
    bare-artefact gate (#362).

    SDMX-ML 2.1 writes one as a ``ProvisionAgreement``, whose
    ``StructureUsage`` and ``DataProvider`` are required the same way (#421).
    """
    for field, value in (("metadataflow", mpa.metadataflow), ("metadata_provider", mpa.metadata_provider)):
        if value is None:
            element = "metadataflow" if field == "metadataflow" else "metadata provider"
            issues.append(
                ValidationIssue(
                    path=f"MetadataProvisionAgreement:{mpa.id}.{field}",
                    message=(
                        f"SDMX-ML {version} requires a {element} on a MetadataProvisionAgreement, but {field} is None"
                    ),
                )
            )


#: What SDMX-ML 2.1 writes of a provider scheme besides its name and
#: providers: a metadata provider scheme merged into its agency's data provider
#: scheme (#421) reads back with the data scheme's.
_PROVIDER_SCHEME_FIELDS_21 = (
    "description",
    "annotations",
    "valid_from",
    "valid_to",
    "uri",
    "is_external_reference",
    "service_url",
    "structure_url",
    "is_partial",
)


def _check_metadata_provision_in_21(msg: StructureMessage, version: str, issues: list[ValidationIssue]) -> None:
    """Metadata providers and their agreements must come back from SDMX-ML 2.1 as themselves (#421).

    2.1 has neither, and writes them as it provisioned metadata itself: an
    agency's metadata providers in its one ``DATA_PROVIDERS`` scheme, and each
    agreement as a ``ProvisionAgreement`` over its metadataflow. Beside data
    providers that shares an element, so a provider id both use, or a scheme
    property the two schemes disagree on, would come back wrong; and an
    agreement with a data agreement's URN would be written twice over.
    """
    if version != "2.1":
        return
    data = {scheme.maintainer.id: scheme for scheme in msg.data_provider_schemes}
    for meta in msg.metadata_provider_schemes:
        scheme = data.get(meta.maintainer.id)
        if scheme is None:
            continue
        path = f"MetadataProviderScheme:{meta.maintainer.id}"
        if shared := sorted(set(meta.providers.ids()) & set(scheme.providers.ids())):
            issues.append(
                ValidationIssue(
                    path=f"{path}.providers",
                    message=(
                        f"SDMX-ML 2.1 writes metadata providers into the agency's data provider scheme, which "
                        f"already has {', '.join(shared)}; give the metadata providers other ids, or write SDMX-ML 3.x"
                    ),
                )
            )
        issues.extend(
            ValidationIssue(
                path=f"{path}.{field}",
                message=(
                    f"SDMX-ML 2.1 writes metadata providers into the agency's data provider scheme, and its "
                    f"{field} differs from that scheme's, so it would read back as the data scheme's; make them "
                    "agree, or write SDMX-ML 3.x"
                ),
            )
            for field in _PROVIDER_SCHEME_FIELDS_21
            if getattr(meta, field) != getattr(scheme, field)
        )
    agreements = {(pa.maintainer.id, pa.id, pa.version) for pa in msg.provision_agreements}
    issues.extend(
        ValidationIssue(
            path=f"MetadataProvisionAgreement:{mpa.id}",
            message=(
                "SDMX-ML 2.1 writes a metadata provision agreement as a ProvisionAgreement, and the message "
                f"already has one with agency {mpa.maintainer.id}, id {mpa.id} and version {mpa.version}"
            ),
        )
        for mpa in msg.metadata_provision_agreements
        if (mpa.maintainer.id, mpa.id, mpa.version) in agreements
    )


def _check_constraint_in_31(dc: DataConstraint, version: str, issues: list[ValidationIssue]) -> None:
    """Refuse a constraint whose meaning SDMX-ML 3.1 cannot carry.

    3.1 removed **``@role``** from `DataConstraint`, which 3.0 declares
    *required* and which says whether the constraint describes Allowed or
    Actual content. The writer dropped it and the reader supplied the default —
    an `Actual` constraint came back asserting `Allowed`. That is not a lost
    field but a *false statement*, which is worse: the document is conformant
    and says something the input did not. So it is refused rather than dropped
    (#246); a caller who wants the downgrade anyway can pass ``validate=False``.

    Since #421 an Actual constraint that fits 3.1's `AvailabilityConstraint`
    is written as one, so only one that does not fit is refused.

    3.1 removed ``<ReleaseCalendar>`` too, and that was refused here as well
    until #421. A calendar says when data is released; leaving it out loses
    that and asserts nothing false, so it is a declared gap (`formats.gaps`),
    as it always was for SDMX-JSON.
    """
    if version != "3.1":
        return
    if dc.constraint_type is not ConstraintType.ALLOWED and (misfits := dc.availability_misfits()):
        issues.append(
            ValidationIssue(
                path=f"DataConstraint:{dc.id}.constraint_type",
                message=(
                    f"SDMX-ML 3.1 removed DataConstraint/@role, so a constraint cannot say "
                    f"{dc.constraint_type.value!r} and states Actual content as an AvailabilityConstraint, "
                    f"which this one does not fit: {'; '.join(misfits)}. Use 3.0, which requires the attribute"
                ),
            )
        )


def _check_availability_in_31(msg: StructureMessage, version: str, issues: list[ValidationIssue]) -> None:
    """An `AvailabilityConstraint` must be what SDMX-ML 3.1 can write (#421).

    One data structure, dataflow or provision agreement, and one included cube
    region: ``AvailabilityConstraintAttachmentType`` is a choice of exactly
    one, and its region states what *is* available. Only 3.1 writes one; the
    other targets leave it out (`formats.gaps.dropped`).
    """
    if version != "3.1":
        return
    for n, ac in enumerate(msg.availability_constraints, start=1):
        extra = set(ac.attachment.alternatives) - {"data_structures", "dataflows", "provision_agreements"}
        if ac.target is None or extra or ac.attachment.queryable_data_sources:
            issues.append(
                ValidationIssue(
                    path=f"AvailabilityConstraint[{n}].attachment",
                    message=(
                        "SDMX-ML 3.1 attaches an AvailabilityConstraint to exactly one data structure, "
                        "dataflow or provision agreement, and to nothing else"
                    ),
                )
            )
        if not ac.region.include:
            issues.append(
                ValidationIssue(
                    path=f"AvailabilityConstraint[{n}].region",
                    message="an AvailabilityConstraint's cube region states what is available, so it must include",
                )
            )


#: Where each attachment alternative can be written, when not everywhere —
#: `ConstraintAttachmentType` per version. 3.x dropped 2.1's data sets, and
#: SDMX-ML 3.1 and SDMX-JSON 2.1 the data sources. "json" output must validate
#: against SDMX-JSON 2.1 as well as 2.0, so it has none either.
_ATTACHMENT_VERSIONS = {
    "data_sets": frozenset({"2.1"}),
    "simple_data_sources": frozenset({"2.1", "3.0"}),
    "queryable_data_sources": frozenset({"2.1", "3.0"}),
}


def _check_attachment_in(
    attachment: ConstraintAttachment, path: str, version: str, issues: list[ValidationIssue], *, content: bool
) -> None:
    """Refuse what of an attachment *version* has no element for (#422).

    *content* is False for a 2.1 `AttachmentConstraint` written as 2.1:
    `AttachmentConstraintAttachmentType` has no provider and no queryable
    data sources, but any number of data sets and simple data sources, where
    the ContentConstraint takes one of each. Written as 3.x or SDMX-JSON an
    attachment constraint becomes a `DataConstraint`, and its rules are those.
    """
    for name, versions in _ATTACHMENT_VERSIONS.items():
        if getattr(attachment, name) and version not in versions:
            issues.append(
                ValidationIssue(
                    path=f"{path}.{name}",
                    message=f"{_wire_name(version)} attaches a constraint to no {name.replace('_', ' ')}",
                )
            )
    if version != "2.1":
        return
    if content:
        issues.extend(
            ValidationIssue(
                path=f"{path}.{name}",
                message=(f"SDMX-ML 2.1 attaches a ContentConstraint to at most one {one}, not {len(values)}"),
            )
            for name, one, values in (
                ("data_sets", "data set", attachment.data_sets),
                ("simple_data_sources", "simple data source", attachment.simple_data_sources),
            )
            if len(values) > 1
        )
        return
    issues.extend(
        ValidationIssue(
            path=f"{path}.{name}",
            message=(
                f"SDMX-ML 2.1 attaches an AttachmentConstraint to no {name.replace('_', ' ')}; write a DataConstraint"
            ),
        )
        for name in ("data_provider", "queryable_data_sources")
        if getattr(attachment, name)
    )


def _check_cube_region_inclusion(dc: DataConstraint, version: str, issues: list[ValidationIssue]) -> None:
    """At most one CubeRegion per ``include`` value, in 3.0 and 3.1.

    ``DataConstraint_CubeRegionInclusion`` is an ``xs:unique`` identity
    constraint over ``CubeRegion/@include``, so a constraint may carry one
    included region and one excluded region and no more — the regions of a
    kind are meant to be merged, not listed. SDMX-ML 2.1 states no such
    constraint, so this is version-specific (#226).

    An identity constraint is easy to miss reading a schema by eye: it sits
    beside the element declaration rather than in the type, and nothing in the
    model hints the field is limited.
    """
    if version not in {"3.0", "3.1"}:
        return
    seen: set[bool] = set()
    for region in dc.regions:
        if region.include in seen:
            issues.append(
                ValidationIssue(
                    path=f"DataConstraint:{dc.id}.regions",
                    message=(
                        f"SDMX-ML {version} allows at most one CubeRegion with include="
                        f"{str(region.include).lower()}; merge the regions of a kind into one"
                    ),
                )
            )
            return
        seen.add(region.include)


def _no_sentinels(facet: Facet | None, path: str, issues: list[ValidationIssue]) -> None:
    """A sentinel value belongs to a *text* format, never an enumeration or coding format (#422).

    SDMX 3.x puts ``SentinelValue`` on ``TextFormatType`` and its component
    restrictions; ``CodedTextFormatType`` (an ``EnumerationFormat``) and
    ``CodingTextFormatType`` (a level's ``CodingFormat``) have none. `Facet`
    serves all three, so the model could hold what no format can write — until
    #508 made it a rule of the artefacts that carry one, checked as built.
    """
    if facet is not None and facet.sentinel_values:
        issues.append(
            ValidationIssue(
                path=path, message="a sentinel value belongs to a text format; an enumeration or coding format has none"
            )
        )


def _check_provision_agreement(pa: ProvisionAgreement, issues: list[ValidationIssue]) -> None:
    """Both edges of a ProvisionAgreement are required by every SDMX-ML schema.

    The model makes them optional so a partially-assembled agreement stays
    storable, but ``ProvisionAgreementType`` requires a ``StructureUsage`` and
    a ``DataProvider`` child in 2.1, 3.0 and 3.1 alike — so an agreement
    missing either serialises to a document every consumer rejects. Same shape
    as the ``ConceptIdentity`` rule above, found the same way (#225).
    """
    prefix = f"ProvisionAgreement:{pa.id}"
    issues.extend(
        ValidationIssue(
            path=f"{prefix}.{field_name}",
            message=f"SDMX-ML requires a {element} child on a ProvisionAgreement, but {field_name} is None",
        )
        for field_name, element in (("dataflow", "<str:StructureUsage>"), ("data_provider", "<str:DataProvider>"))
        if getattr(pa, field_name) is None
    )


def _wire_name(version: str) -> str:
    """How to name a wire target in prose — "SDMX-JSON" reads badly as "SDMX json"."""
    return "SDMX-JSON" if version == "json" else f"SDMX-ML {version}"


#: SDMX-ML 2.1's ``ReportingYearStartDay`` representation: a text format fixed
#: to ``MonthDay``, with no facets (`NonFacetedTextFormatType`).
_REPORTING_YEAR_FORMAT = Facet(type=DataType.MonthDay)


def _check_dsd_21_only(dsd: DataStructure, prefix: str, version: str, issues: list[ValidationIssue]) -> None:
    """What of a DSD only SDMX-ML 2.1 has, and how 2.1 restricts it (#422).

    Constraint-defined groups have no 3.x or SDMX-JSON form, so they are
    refused there. Attachment groups were refused too until #421; leaving
    them out narrows where a value may be reported and asserts nothing false,
    so it is a declared gap (`formats.gaps`). In 2.1 an attribute by the reserved id
    `REPORTING_YEAR_START_DAY` is written as ``ReportingYearStartDay``, whose
    representation is fixed and which has no concept roles.
    """
    if version != "2.1":
        issues.extend(
            ValidationIssue(
                path=f"{prefix}.groups[{group.id}].attachment_constraint",
                message=f"{_wire_name(version)} defines a group by its dimensions only; SDMX-ML 2.1 alone "
                "by an attachment constraint",
            )
            for group in dsd.groups or ()
            if group.attachment_constraint is not None
        )
        return
    for attr in dsd.attributes:
        if attr.id != REPORTING_YEAR_START_DAY:
            continue
        path = f"{prefix}.attributes[{attr.id}]"
        if attr.representation != _REPORTING_YEAR_FORMAT or attr.enumeration_format is not None:
            issues.append(
                ValidationIssue(
                    path=f"{path}.representation",
                    message="SDMX-ML 2.1 writes this attribute as ReportingYearStartDay, whose representation "
                    "is a MonthDay text format and nothing else",
                )
            )
        if attr.concept_role:
            issues.append(
                ValidationIssue(
                    path=f"{path}.concept_role",
                    message="SDMX-ML 2.1 writes this attribute as ReportingYearStartDay, which has no concept roles",
                )
            )


def _check_measure_dimension_version(
    dsd: DataStructure,
    prefix: str,
    version: str,
    issues: list[ValidationIssue],
) -> None:
    """A MeasureDimension exists in SDMX-ML 2.1 and nowhere later (#305).

    2.1's cross-sectional measure dimension was removed in SDMX 3.0, which
    replaced it with the measure list — the 3.0 and 3.1 schemas contain no
    `MeasureDimension` element at all. So a DSD holding one has no 3.x or
    SDMX-JSON representation, and the 3.x writers would reach for an
    `enumeration_format` it does not carry.

    Before #305 the 3.x and JSON writers dropped it **silently**: the component
    vanished from the document and the round trip returned a DSD without it,
    with no error anywhere. Silent loss on downgrade is the failure this
    refusal replaces.

    The mirror image of `_check_version_specific`, which refuses 3.0 measure
    lists that 2.1 cannot express. Downgrade hazards run in both directions.

    Also refuses a MeasureDimension placed in `dimensions`, which is typed
    `ItemList[Dimension]` and has a dedicated `measure_dimension` slot. That
    misuse used to surface as `AttributeError: no attribute
    'enumeration_format'` from deep inside validation — the report that opened
    #305, written against the wrong field.
    """
    # Before the 2.1 early return: misplacement is wrong in *every* version.
    # Left below it, a 2.1 write skipped the check and crashed in the writer
    # instead, which is the AttributeError #305 reported.
    # Only MeasureDimension. `TimeDimension` is not a `Dimension` subclass
    # either and *does* sit in `dimensions` throughout the codebase — an
    # isinstance check against `Dimension` flags 74 tests' worth of legitimate
    # DSDs. What makes MeasureDimension different is that it has a dedicated
    # slot and no `enumeration_format`, so it is the one that crashes.
    issues.extend(
        ValidationIssue(
            path=f"{prefix}.dimensions[{dim.id}]",
            message=(
                "a MeasureDimension cannot live in `dimensions`; pass it as `measure_dimension=`. "
                "Placed here it used to raise AttributeError from inside validation (#305)"
            ),
        )
        for dim in dsd.dimensions
        if isinstance(dim, MeasureDimension)
    )
    if version == "2.1":
        return
    if dsd.measure_dimension is not None:
        issues.append(
            ValidationIssue(
                path=f"{prefix}.measure_dimension",
                message=(
                    f"a MeasureDimension has no {_wire_name(version)} representation — it is a 2.1 "
                    "cross-sectional construct that 3.0 replaced with the measure list, and the "
                    f"{_wire_name(version)} writer drops it silently. Write this data structure as "
                    "SDMX-ML 2.1, or model the component as a measure"
                ),
            )
        )


def _check_usage_links(dsd: DataStructure, prefix: str, version: str, issues: list[ValidationIssue]) -> None:
    """A metadata attribute usage has at most one link in SDMX-ML 3.x (#422).

    `MetadataAttributeUsageBaseType` restricts `ComponentType` and, alone among
    the identifiables, leaves `<com:Link>` at its default `maxOccurs` of one.
    """
    if version not in {"3.0", "3.1"}:
        return
    issues.extend(
        ValidationIssue(
            path=f"{prefix}.metadata_attributes[{usage.id}].links",
            message=f"SDMX-ML {version} allows a metadata attribute usage one link; this one has {len(usage.links)}",
        )
        for usage in dsd.metadata_attributes
        if len(usage.links) > 1
    )


def _check_version_specific(
    dsd: DataStructure,
    prefix: str,
    version: str,
    issues: list[ValidationIssue],
) -> None:
    """Flag artefacts that are valid SDMX but not valid in *this* version.

    These are downgrade hazards: perfectly good SDMX 3.0 with no 2.1
    representation. Without the check the writer emits a document that fails
    schema validation, and the first sign is a consumer rejecting the file.
    """
    if version != "2.1":
        return
    # SDMX-ML 2.1 has exactly one PrimaryMeasure, with its id pinned to
    # OBS_VALUE. SDMX 3.0 introduced a measure *list* with free ids, so a 3.0
    # DSD with several measures has no 2.1 form, and this refuses it rather
    # than emit one <PrimaryMeasure> per measure, which is schema-invalid. A
    # *single* measure named anything else converts: the 2.1 writer names it
    # OBS_VALUE, which `formats.gaps` declares (#421).
    # MeasureListType requires a <PrimaryMeasure> ("Only a primary may be
    # defined"), and <MeasureList> itself is mandatory in
    # DataStructureComponentsType — so a measure-less DSD has no valid 2.1
    # encoding at all. The 2.1 writer already refused this, but with a
    # ValueError raised mid-write; stating it here makes it a refusal like
    # every other, ahead of any bytes being produced (#227).
    if not dsd.measures:
        issues.append(
            ValidationIssue(
                path=f"{prefix}.measures",
                message=(
                    "SDMX-ML 2.1 requires a <MeasureList> containing a <PrimaryMeasure>, "
                    "so a DataStructure with no measures has no 2.1 representation; "
                    "add a measure, or serialise to 3.0"
                ),
            )
        )
    if len(dsd.measures) > 1:
        ids = ", ".join(m.id for m in dsd.measures)
        issues.append(
            ValidationIssue(
                path=f"{prefix}.measures",
                message=(
                    f"SDMX-ML 2.1 allows exactly one PrimaryMeasure, but this DSD has "
                    f"{len(dsd.measures)} measures ({ids}); valid SDMX 3.0 with no 2.1 representation"
                ),
            )
        )
    components = [
        *((d, "dimensions") for d in dsd.dimensions),
        *((a, "attributes") for a in dsd.attributes),
        *((m, "measures") for m in dsd.measures),
    ]
    for component, kind in components:
        rep = component.representation
        if not isinstance(rep, Facet):
            continue
        path = f"{prefix}.{kind}[{component.id}].representation"
        if rep.type is not None and str(rep.type) in _COMPONENT_DATATYPE_NOT_IN_21:
            issues.append(
                ValidationIssue(
                    path=f"{path}.type",
                    message=(
                        f"SDMX-ML 2.1 does not allow {rep.type!s} on a component TextFormat "
                        "(it restricts components to SimpleDataType); valid in 3.0 and 3.1"
                    ),
                )
            )


#: Content a wire format requires an artefact to carry, beyond its identity.
#:
#: Each entry says: on this `StructureMessage` field, this model attribute must
#: be non-empty when writing these versions, because the schema declares the
#: element it becomes with ``minOccurs="1"`` (or offers it only through a
#: required ``xs:choice``).
#:
#: Stated here rather than read from the XSDs because the vendored schemas are
#: test-only — they are not shipped in the wheel, and `sdmxlib.model` may not
#: depend on them. The direction of trust runs the other way:
#: `tests/unit/test_xsd_conformance` checks this table *against*
#: `xsd_facts.required_children` and `required_choices`, so a rule that does
#: not match the schema fails there, and a schema refresh that changes a
#: cardinality reports the difference.
#:
#: Version-specific because the requirements genuinely differ — SDMX-JSON
#: accepts a hierarchy with no codes where all three SDMX-ML versions do not,
#: and 2.1 requires a map to carry its item maps where 3.0 does not.
_ALL_ML = frozenset({"2.1", "3.0", "3.1"})

_REQUIRED_CONTENT: tuple[tuple[str, str, str, frozenset[str]], ...] = (
    # (message field, model attribute, XSD element it becomes, versions)
    ("hierarchies", "tree", "HierarchicalCode", _ALL_ML),
    ("hierarchy_associations", "linked_hierarchy", "LinkedHierarchy", frozenset({"3.0", "3.1"})),
    ("hierarchy_associations", "linked_object", "LinkedObject", frozenset({"3.0", "3.1"})),
    ("data_structures", "dimensions", "Dimension", _ALL_ML),
    ("structure_maps", "source", "Source", _ALL_ML),
    ("structure_maps", "target", "Target", _ALL_ML),
    ("concept_scheme_maps", "source", "Source", _ALL_ML),
    ("concept_scheme_maps", "target", "Target", _ALL_ML),
    ("organisation_scheme_maps", "source", "Source", _ALL_ML),
    ("organisation_scheme_maps", "target", "Target", _ALL_ML),
    ("category_scheme_maps", "source", "Source", _ALL_ML),
    ("category_scheme_maps", "target", "Target", _ALL_ML),
    # A RepresentationMap is written as a 2.1 <CodelistMap>, whose ends are
    # <Source>/<Target>, and as a 3.x <RepresentationMap>, whose ends are a
    # choice of <SourceCodelist>|<SourceDataType> and the target equivalent.
    # Same model field, different element per version — so two entries rather
    # than one, because the element name is what the rule cites.
    ("representation_maps", "sources", "Source", frozenset({"2.1"})),
    ("representation_maps", "targets", "Target", frozenset({"2.1"})),
    ("representation_maps", "sources", "SourceCodelist", frozenset({"3.0", "3.1"})),
    ("representation_maps", "targets", "TargetCodelist", frozenset({"3.0", "3.1"})),
)


def _check_required_content(msg: StructureMessage, version: str, issues: list[ValidationIssue]) -> None:
    """Refuse artefacts missing content the target format requires.

    The write-boundary half of the model's deliberate split: a field is
    optional so a partially-assembled artefact stays *storable*, and this
    re-asks the question when the artefact is about to become a document.
    Before #229 the answer was simply not asked, and eighteen (artefact,
    format) pairs serialised to documents the official schemas reject.
    """
    for message_field, attribute, element, versions in _REQUIRED_CONTENT:
        if version not in versions:
            continue
        for artefact in getattr(msg, message_field, ()):
            if getattr(artefact, attribute, None):
                continue
            issues.append(
                ValidationIssue(
                    path=f"{type(artefact).__name__}:{artefact.id}.{attribute}",
                    message=(
                        f"SDMX-ML {version} requires a <{element}> here, so {attribute} must not be empty; "
                        f"the artefact has no {element} to write"
                    ),
                )
            )


def check_serialisable(msg: StructureMessage, *, version: str | None = None) -> None:
    """Raise unless every artefact in *msg* can be written to an SDMX format.

    Distinct from what *every* format requires of an artefact alone, which is
    refused as the artefact is built. This asks what the message and the
    target format require, a question that only matters at serialise time, so
    `serialize` calls it and `Registry.add` does not: a DSD with no dimensions
    is valid SDMX-JSON, and storable, and refused here when the target is
    SDMX-ML.

    A builder calls it before publishing, once per version its bundle will be
    served in, so a structure that cannot be written fails the build and not a
    server's first ``/structure`` request:

        for version in ("2.1", "3.0"):
            sl.check_serialisable(sl.StructureMessage(...), version=version)

    Args:
        msg: The message about to be serialised.
        version: Target wire version. When given, also checks constraints that
            hold in one SDMX-ML version and not another — 2.1 is markedly
            narrower than 3.0, so a downgrade can fail where the artefact
            itself is perfectly valid.

    Raises:
        ValidationError: If any artefact cannot be represented on the wire.
    """
    issues: list[ValidationIssue] = []
    _check_duplicate_maintainables(msg, issues)
    for pa in msg.provision_agreements:
        _check_provision_agreement(pa, issues)
    if version is not None:
        _check_message_version(msg, version, issues)
    if issues:
        raise ValidationError(issues)


def _check_duplicate_maintainables(msg: StructureMessage, issues: list[ValidationIssue]) -> None:
    """No two maintainables of a class may share (id, agency, version) (#231).

    Every container element in every SDMX-ML version carries an ``xs:unique``
    over ``@id``, ``@agencyID`` and ``@version`` — ``UniqueCodelist`` on
    ``<Codelists>``, ``UniqueDataflow`` on ``<Dataflows>``, and so on for 36 of
    them in 3.0. Together they say the obvious thing: a message is a set of
    distinct artefacts, not a list that may repeat one.

    Unenforced until #231, and invisible for the usual reason — an identity
    constraint sits beside the element declaration rather than inside the type,
    so reading `CodelistType` tells you nothing about it. Two codelists with
    the same id serialised happily and every schema rejected the result.

    Version-independent: 2.1, 3.0 and 3.1 all state it. The item-level
    ``xs:unique`` over ``@id`` alone — ``<Codelist>``'s own constraint on its
    codes — is a different rule, already enforced by `_check_duplicate_ids`.
    """
    seen: dict[tuple[str, str, str, str], int] = {}
    for artefact in msg.all_artefacts():
        cls = type(artefact).__name__
        key = (
            cls,
            getattr(artefact, "id", "?"),
            getattr(artefact, "agency_id", "?"),
            getattr(artefact, "version", "?"),
        )
        seen[key] = seen.get(key, 0) + 1
    for (cls, artefact_id, agency, version), count in seen.items():
        if count > 1:
            issues.append(
                ValidationIssue(
                    path=f"{cls}:{artefact_id}",
                    message=(
                        f"the message carries {count} {cls} artefacts with id={artefact_id!r}, "
                        f"agency={agency!r} and version={version!r}; every SDMX-ML version requires "
                        "them to be unique within the message"
                    ),
                )
            )


def _check_latest_refs(msg: StructureMessage, version: str, issues: list[ValidationIssue]) -> None:
    """Outgoing references must carry a concrete version, in every format (#233).

    `SdmxUrn.version` defaults to the sentinel ``"latest"``, which stringifies
    with no ``(version)`` part at all. 3.0 references are URN strings whose
    pattern facets *require* the parenthesised version, so there the document
    is schema-invalid. 2.1's ``<Ref>`` makes the version optional — but its
    XSD documents an omitted ``version`` and ``maintainableParentVersion`` as
    *defaulting to 1.0*, so the same reference is valid and names ``1.0`` to
    any reader that follows the schema. 2.1 was exempt until that was read.

    Refusing here rather than substituting the referenced artefact's real
    version: a write that quietly changes which artefact a reference names is
    worse than one that stops. "latest" is a question ("whatever is newest at
    read time"), and a serialised reference has to be an answer.
    """
    why = {
        "2.1": "SDMX-ML 2.1 reads a reference with no version as version 1.0",
        "json": "SDMX-JSON requires one on every URN reference",
    }.get(version, f"SDMX-ML {version} requires one on every URN reference")
    for artefact in msg.all_artefacts():
        prefix = f"{type(artefact).__name__}:{getattr(artefact, 'id', '?')}"
        for path, ref in _collect_refs(artefact, prefix):
            if ref.urn.is_latest:
                issues.append(
                    ValidationIssue(
                        path=path,
                        message=(
                            f"reference to {ref.urn.artefact_class}={ref.urn.agency}:{ref.urn.id} has no "
                            f"version, and {why}. "
                            'Build the reference with an explicit version instead of the "latest" default.'
                        ),
                    )
                )


def check_writable(version: str, target: str, *, artefact: str) -> None:
    """Refuse one maintainable's version that `target` cannot express.

    For the writers, which ask again for a caller that bypasses `serialize`;
    `check_serialisable` asks it of the whole message first, accumulated with
    everything else.

    Args:
        version: The artefact's version.
        target: `"2.1"`, `"3.0"`, `"3.1"` or `"json"`.
        artefact: The issue path naming the artefact, as `Codelist:CL_FREQ`.

    Raises:
        ValidationError: If `target` does not accept `version`.
    """
    reason = unwritable(version, target)
    if reason is not None:
        raise ValidationError([ValidationIssue(path=f"{artefact}.version", message=reason)])


def _check_artefact_versions(msg: StructureMessage, version: str, issues: list[ValidationIssue]) -> None:
    """Every maintainable's version must be one the target's grammar accepts (#319).

    The 2.1 and 3.0 grammars are not nested (`model/version.py`), so a version
    the store holds can be unwritable in the format chosen here. A refusal like
    any other: a `ValidationIssue`, accumulated, before a byte is written.
    """
    for artefact in msg.all_artefacts():
        if isinstance(artefact, MaintainableArtefact):
            reason = unwritable(artefact.version, version)
            if reason is not None:
                issues.append(ValidationIssue(path=f"{type(artefact).__name__}:{artefact.id}.version", message=reason))


def _check_ref_versions(msg: StructureMessage, version: str, issues: list[ValidationIssue]) -> None:
    """Every outgoing reference's version must be one the target can name (#429).

    `_check_artefact_versions` asks it of an artefact's own version; nothing
    asked it of the versions artefacts *reference*, and every writer copied
    them through. A reference can also late-bind (`1.0+.0`), which no artefact
    version can, so the grammar is `version.unreferenceable`'s rather than
    `unwritable`'s. Refused rather than rewritten: resolving a wildcard to a
    concrete version changes which artefact the reference names, the same
    objection `_check_latest_refs` makes.
    """
    for artefact in msg.all_artefacts():
        prefix = f"{type(artefact).__name__}:{getattr(artefact, 'id', '?')}"
        for path, ref in _collect_refs(artefact, prefix):
            if ref.urn.is_latest:
                continue
            reason = unreferenceable(ref.urn.version, version)
            if reason is not None:
                issues.append(ValidationIssue(path=path, message=f"{ref.urn}: {reason}"))


def _referenceable(package: str, artefact_class: str, version: str) -> bool:
    if version == "2.1":
        return artefact_class in CLASSES_21 and package in PACKAGES_21
    pairs = {"3.0": PAIRS_30, "3.1": PAIRS_31, "json": PAIRS_JSON}[version]
    return (package, artefact_class) in pairs


#: A 2.1 container's members are references in the model and *nested* on the
#: wire — a StructureSet writes its maps inside itself, a HierarchicalCodelist
#: its hierarchies — so no `Ref` is written for them, and their classes are not
#: a reference's to check. (3.x refuses both containers outright.)
_NESTED_ON_WRITE = {"StructureSet": "maps", "HierarchicalCodelist": "hierarchies"}


def _local_in_21(path: str, version: str) -> bool:
    """A 2.1 component map's value map: named by a local id or written inline, never as a `Ref` (#422)."""
    return version == "2.1" and path.startswith("StructureMap:") and path.endswith(".representation_map")


def _converted_in_21(path: str, version: str) -> bool:
    """A metadata provision agreement's provider, which 2.1 names as the data provider it writes it as (#421)."""
    return version == "2.1" and path.startswith("MetadataProvisionAgreement:") and path.endswith(".metadata_provider")


def _enumerates(path: str) -> bool:
    """True when a reference's path is an enumeration: a representation, or a representation map's end."""
    field = path.rsplit(".", 1)[-1].split("[", 1)[0]
    return field in {"representation", "core_representation"} or (
        path.startswith("RepresentationMap:") and field in {"sources", "targets"}
    )


def _check_ref_classes(msg: StructureMessage, version: str, issues: list[ValidationIssue]) -> None:
    """Every outgoing reference must name a class the target defines (#430).

    A reference is written as its URN's package and class, unchecked. BIS
    categorises Fusion Metadata Registry artefacts SDMX does not define —
    `publicationtable.PublicationTable`, `pdq.PreDefinedQuery` — and the
    reader keeps them faithfully, as it should; writing them produced 209
    categorisations every format rejects. Refused rather than dropped: there
    is no valid form to write, and dropping the reference would change what
    the artefact says.
    """
    for artefact in msg.all_artefacts():
        prefix = f"{type(artefact).__name__}:{getattr(artefact, 'id', '?')}"
        nested = _NESTED_ON_WRITE.get(type(artefact).__name__)
        for path, ref in _collect_refs(artefact, prefix):
            urn = ref.urn
            if (
                (nested is not None and path.startswith(f"{prefix}.{nested}["))
                or _local_in_21(path, version)
                or _converted_in_21(path, version)
            ):
                continue
            if version == "json" and _enumerates(path) and (urn.package, urn.artefact_class) == ENUMERATION_PAIR_JSON:
                continue
            if not _referenceable(urn.package, urn.artefact_class, version):
                label = "SDMX-JSON" if version == "json" else f"SDMX-ML {version}"
                issues.append(
                    ValidationIssue(
                        path=path,
                        message=(
                            f"{urn}: {urn.package}.{urn.artefact_class} is not a class {label} can reference "
                            "— a registry's own artefact type has no SDMX form; remove the reference"
                        ),
                    )
                )


def _check_message_version(msg: StructureMessage, version: str, issues: list[ValidationIssue]) -> None:
    """Constraints that hold in one SDMX-ML version and not another."""
    _check_artefact_versions(msg, version, issues)
    _check_ref_classes(msg, version, issues)
    _check_ref_versions(msg, version, issues)
    _check_latest_refs(msg, version, issues)
    _check_writer_coverage(msg, version, issues)
    _check_required_content(msg, version, issues)
    for mdf in msg.metadataflows:
        _check_metadataflow_targets(mdf, version, msg.metadata_structures, issues)
    for mpa in msg.metadata_provision_agreements:
        _check_metadata_provision_refs(mpa, version, issues)
    _check_metadata_provision_in_21(msg, version, issues)
    for msd in msg.metadata_structures:
        _check_msd_in_21(msd, version, issues)
    _check_representation_map_in_21(msg, version, issues)
    _check_structure_map_in_21(msg, version, issues)
    _check_text_format_mappings_in_21(msg, version, issues)
    for dsd in msg.data_structures:
        _check_measure_dimension_version(dsd, f"DataStructure:{dsd.id}", version, issues)
        _check_dsd_21_only(dsd, f"DataStructure:{dsd.id}", version, issues)
        _check_version_specific(dsd, f"DataStructure:{dsd.id}", version, issues)
        _check_usage_links(dsd, f"DataStructure:{dsd.id}", version, issues)
    _check_constraints_in(msg, version, issues)
    if version == "json":
        _check_links_in_json(msg.all_artefacts(), issues)
    if version == "3.0":
        for codelist in msg.codelists:
            _check_code_parents_in_30(codelist, issues)
    _check_extensions_in_21(msg, version, issues)
    _check_wildcards_in_json(msg, version, issues)
    _check_containers_in_21(msg, version, issues)
    _check_availability_in_31(msg, version, issues)
    if version in {"3.0", "3.1"}:
        for hierarchy in msg.hierarchies:
            _check_hierarchy_node_ids_in_3x(hierarchy, version, issues)
    _check_unit_scheme_version(msg, version, issues)


def _check_unit_scheme_version(msg: StructureMessage, version: str, issues: list[ValidationIssue]) -> None:
    """An organisation unit scheme at a version other than 1.0 has no 3.x or SDMX-JSON form (#469).

    SDMX-ML 2.1 lets an organisation unit scheme carry any version, unlike the
    three fixed-id schemes, which it pins to 1.0 (#416). 3.0 and 3.1 prohibit
    ``version`` on every organisation scheme (`OrganisationSchemeBaseType`), and
    SDMX-JSON makes it the const ``1.0``. So a valid 2.1 scheme at 2.0 reads
    whole, and is refused here rather than written without its version and read
    back as 1.0.
    """
    if version not in {"3.0", "3.1", "json"}:
        return
    issues.extend(
        ValidationIssue(
            path=f"OrganisationUnitScheme:{scheme.id}.version",
            message=(
                f"SDMX-ML 3.x prohibits a version on an organisation scheme and SDMX-JSON fixes it to "
                f"{_FIXED_SCHEME_VERSION!r}, so this scheme at {scheme.version!r} has no {version} form; "
                "write SDMX-ML 2.1, which carries it"
            ),
        )
        for scheme in msg.organisation_unit_schemes
        if scheme.version != _FIXED_SCHEME_VERSION
    )


#: What SDMX-JSON's ``links`` already spells, so a `Link` with the relation
#: would come back as something else (#422): ``self`` is the artefact's own
#: URN, and the first ``describedby`` its ``@uri``.
_JSON_SELF_REL = "self"
_JSON_URI_REL = "describedby"


def _check_links_in_json(artefacts: Iterable[object], issues: list[ValidationIssue]) -> None:
    """Links SDMX-JSON cannot carry as links (#422).

    Its ``links`` array holds the artefact's identity and ``@uri`` too, so a
    `Link` spelt as either would be read back as that instead: a ``self`` link
    always, a ``describedby`` one when there is no uri ahead of it. And its
    schema types a link's ``urn`` as an SDMX URN, where SDMX-ML takes any URI.
    """
    for artefact in artefacts:
        label = getattr(artefact, "id", "")
        for path, holder, field_name in _link_fields(artefact, f"{type(artefact).__name__}:{label}", set()):
            links: tuple[Link, ...] = getattr(holder, field_name)
            uri: object = getattr(holder, field_name.removesuffix("links") + "uri", None)
            for i, link in enumerate(links):
                at = f"{path}[{i}]"
                if link.rel == _JSON_SELF_REL or (link.rel == _JSON_URI_REL and uri is None):
                    issues.append(
                        ValidationIssue(
                            path=f"{at}.rel",
                            message=(
                                f"SDMX-JSON reads a {link.rel!r} link as the artefact's "
                                f"{'URN' if link.rel == _JSON_SELF_REL else 'uri'}, not as a link"
                            ),
                        )
                    )
                if link.urn is not None and not _is_sdmx_urn(link.urn):
                    issues.append(
                        ValidationIssue(
                            path=f"{at}.urn",
                            message=f"SDMX-JSON types a link's urn as an SDMX URN, and {link.urn!r} is not one",
                        )
                    )


def _is_sdmx_urn(text: str) -> bool:
    try:
        SdmxUrn.parse(text)
    except ValueError:
        return False
    return True


def _link_fields(obj: object, prefix: str, seen: set[int]) -> Iterator[tuple[str, object, str]]:
    """Every non-empty `links` field `obj` reaches: its path, its holder and its name (#422).

    Descends by `link_fields`, so a field that cannot hold a link — every
    field of a `Code` but its links — is never read.
    """
    oid = id(obj)
    if oid in seen:
        return
    seen.add(oid)
    for name in link_fields(type(obj)):
        value: object = getattr(obj, name, None)
        if value is None or isinstance(value, Ref):
            continue
        path = f"{prefix}.{name}"
        if name == "links" or name.endswith("_links"):
            if value:
                yield path, obj, name
        elif isinstance(value, (ItemList, ArtefactList, list, tuple)):
            for index, item in enumerate(value):
                if _may_hold_links(item):
                    yield from _link_fields(item, _at(path, item, index), seen)
        elif attrs.has(type(value)):
            yield from _link_fields(value, path, seen)


def _may_hold_links(item: object) -> bool:
    """Whether walking `item` could find a link.

    It could when it is an artefact rather than a `Ref`, and one of its
    link-bearing fields holds something. Checked before descending, so an item with nothing to find, nearly every
    code of a codelist, costs no path string or generator (#462).
    """
    if not attrs.has(type(item)) or isinstance(item, Ref):
        return False
    return any(getattr(item, field, None) for field in link_fields(type(item)))


def _check_constraints_in(msg: StructureMessage, version: str, issues: list[ValidationIssue]) -> None:
    """Both constraint artefacts, as *version* can write them."""
    for dc in msg.constraints:
        _check_attachment_in(dc.attachment, f"DataConstraint:{dc.id}.attachment", version, issues, content=True)
        _check_cube_region_inclusion(dc, version, issues)
        _check_constraint_in_31(dc, version, issues)
        _check_empty_keys_in_ml(dc, version, issues)
        _check_cascade_in_2_1(dc, version, issues)
    for ac in msg.attachment_constraints:
        path = f"AttachmentConstraint:{ac.id}.attachment"
        _check_attachment_in(ac.attachment, path, version, issues, content=version != "2.1")


def _check_extensions_in_21(msg: StructureMessage, version: str, issues: list[ValidationIssue]) -> None:
    """An extending codelist reaches SDMX-ML 2.1 only with what it inherits (#421).

    2.1 has no codelist extension, so the writer inlines the inherited codes
    (`Codelist.apply_extensions`). That needs each extended codelist: a
    resolved reference, or one in the same message. Without it the 2.1
    codelist would silently hold only its own codes, which is what it did
    before #421 — so it is refused instead.
    """
    if version != "2.1":
        return
    for codelist in msg.codelists:
        missing = unresolved_extensions(codelist, msg.codelists)
        if not missing:
            continue
        issues.append(
            ValidationIssue(
                path=f"Codelist:{codelist.id}.extensions",
                message=(
                    f"SDMX-ML 2.1 has no codelist extension, so the inherited codes are written inline, but "
                    f"{len(missing)} extended codelist(s) are neither resolved nor in the message: "
                    f"{', '.join(missing)}. Resolve them, add them to the message, or write SDMX-ML 3.x"
                ),
            )
        )


def _check_wildcards_in_json(msg: StructureMessage, version: str, issues: list[ValidationIssue]) -> None:
    """A wildcarded member value reaches SDMX-JSON only spelled out (#451).

    SDMX-JSON's member value is a plain id, so the writer writes a `%`
    pattern as the codes it matches (`Codelist.expand_wildcards`). That needs
    the extended codelist, resolved or in the message, and a match: written
    as it stood, the pattern made the document schema-invalid.
    """
    if version != "json":
        return
    issues.extend(
        ValidationIssue(
            path=f"Codelist:{codelist.id}.extensions",
            message=(
                "SDMX-JSON has no wildcarded member value, so each is written as the codes it matches, "
                f"but {'; '.join(misfits)}. Resolve the extended codelist, add it to the message, "
                "or write SDMX-ML 3.x"
            ),
        )
        for codelist in msg.codelists
        if (misfits := wildcard_misfits(codelist, msg.codelists))
    )


#: What SDMX-ML 2.1 nests in a container: a hierarchy, or one of the maps.
type _Nested = (
    Hierarchy | RepresentationMap | StructureMap | ConceptSchemeMap | CategorySchemeMap | OrganisationSchemeMap
)


def _check_containers_in_21(msg: StructureMessage, version: str, issues: list[ValidationIssue]) -> None:
    """A hierarchy or map in its 2.1 container must come back from SDMX-ML 2.1 as itself (#421).

    2.1 nests a hierarchy in a HierarchicalCodelist and a map in a
    StructureSet, so what 3.0 made maintainable about each is its container's:
    a reader gives it the container's agency, version, validity and
    external-reference attributes, and the id `member_id` composes. One read
    from 2.1 always agrees; one built by hand may not, and would silently come
    back as a different artefact.
    """
    if version != "2.1":
        return
    nested: list[_Nested] = [
        *msg.hierarchies,
        *msg.representation_maps,
        *msg.structure_maps,
        *msg.concept_scheme_maps,
        *msg.organisation_scheme_maps,
        *msg.category_scheme_maps,
    ]
    by_urn = {str(artefact_urn(member)): member for member in nested}
    containers: list[tuple[HierarchicalCodelist | StructureSet, str, tuple[Ref[object], ...]]] = [
        *((hcl, "hierarchies", hcl.hierarchies) for hcl in msg.hierarchical_codelists),
        *((structure_set, "maps", structure_set.maps) for structure_set in msg.structure_sets),
    ]
    for container, field_name, refs in containers:
        for ref in refs:
            member = by_urn.get(str(ref.urn))
            if member is None:
                continue
            differ = [name for name, agrees in _agrees_with_container(member, container) if not agrees]
            if differ:
                issues.append(
                    ValidationIssue(
                        path=f"{type(container).__name__}:{container.id}.{field_name}",
                        message=(
                            f"SDMX-ML 2.1 reads {type(member).__name__} {member.id} back with its container's "
                            f"{', '.join(differ)}, not its own. Make them agree — the id as "
                            f"'<id>@{container.id}' — or write it without the container"
                        ),
                    )
                )


def _agrees_with_container(member: _Nested, container: HierarchicalCodelist | StructureSet) -> list[tuple[str, bool]]:
    """Each field a 2.1 reader takes from the container, and whether `member` already has it."""
    return [
        ("id", inner_id(member.id, container.id) is not None),
        ("agency", member.agency_id == container.agency_id),
        ("version", member.version == container.version),
        ("valid_from", member.valid_from == container.valid_from),
        ("valid_to", member.valid_to == container.valid_to),
        ("is_external_reference", member.is_external_reference == container.is_external_reference),
        ("service_url", member.service_url == container.service_url),
        ("structure_url", member.structure_url == container.structure_url),
    ]


def _check_code_parents_in_30(codelist: Codelist, issues: list[ValidationIssue]) -> None:
    """A code's parent must be an NCName id to be written as SDMX-ML 3.0.0 (#432).

    A schema erratum, not a rule: 3.0.0 types `<Parent>` as
    `SingleNCNameIDType` (`_NCNAME_RE`) (`[A-Za-z][A-Za-z0-9_-]*`) while a code's own `@id`
    is any `IDType` (`[A-Za-z0-9_@$-]+`), so `<Code id="_T">` is valid 3.0.0
    and a child of it cannot be written. 3.1.0 types `<Parent>` as `IDType`;
    2.1 never narrowed it. `_T` — SDMX's "total" — is the usual case, at every
    registry with code hierarchies (OECD alone has 18,562).

    Refused rather than dropped: losing `<Parent>` flattens the hierarchy, and
    3.1 carries it. One issue per codelist, since a registry's codelist can
    hold thousands.
    """
    bad = [code for code in codelist.codes if code.parent_id is not None and not _NCNAME_RE.match(code.parent_id)]
    if not bad:
        return
    shown = ", ".join(f"{code.id} (parent {code.parent_id!r})" for code in bad[:3])
    more = f" and {len(bad) - 3} more" if len(bad) > 3 else ""
    issues.append(
        ValidationIssue(
            path=f"Codelist:{codelist.id}.codes[].parent_id",
            message=(
                f"{len(bad)} code(s) have a parent SDMX-ML 3.0 cannot name: {shown}{more}. 3.0.0 types "
                "<Parent> as an NCName id ([A-Za-z][A-Za-z0-9_-]*) though a code's own id may start with any "
                "id character — a schema erratum fixed in 3.1. Write SDMX-ML 3.1 or 2.1 instead"
            ),
        )
    )


def _check_hierarchy_node_ids_in_3x(hierarchy: Hierarchy, version: str, issues: list[ValidationIssue]) -> None:
    """A node id may appear once in a whole hierarchy to be written as SDMX-ML 3.x (#439).

    `Hierarchiy_UniqueHierarchicalCode` selects ``.//structure:HierarchicalCode``
    — every descendant, not the siblings — in both 3.0 and 3.1, while 2.1 and
    the model identify a node by its path, so a repeat in another branch is
    valid to hold. BIS numbers its nodes per level (``1``, ``1/1``), OECD puts
    ``MR`` at a root and under ``TYPO_METRO``; neither has a 3.x form.

    Refused rather than renamed: the node id is what a reader matches on, and
    there is no id the writer could invent that would still mean the same
    node. One issue per hierarchy.
    """
    paths_of: dict[str, list[str]] = {}
    for node in hierarchy.codes:
        paths_of.setdefault(node.id, []).append(hierarchy.path_of(node))
    repeated = {node_id: paths for node_id, paths in paths_of.items() if len(paths) > 1}
    if not repeated:
        return
    shown = "; ".join(f"{node_id} at {', '.join(paths[:2])}" for node_id, paths in list(repeated.items())[:3])
    more = f" and {len(repeated) - 3} more" if len(repeated) > 3 else ""
    issues.append(
        ValidationIssue(
            path=f"Hierarchy:{hierarchy.id}.codes[].id",
            message=(
                f"{len(repeated)} node id(s) repeat in another branch: {shown}{more}. SDMX-ML {version} requires a "
                "node id to be unique across the whole hierarchy, where 2.1 asks it only of siblings — write "
                "SDMX-ML 2.1 instead"
            ),
        )
    )


#: SDMX-JSON's *metadata* schema pins ``VersionType`` to strict semantic
#: versioning; its *structure* schema uses SDMX-ML 3.0's two patterns (this
#: comment said "no pattern at all" until #417, which is how the structure
#: writer came to check nothing). So ``"1.0"`` —
#: the model default, and perfectly valid in SDMX-ML — cannot be written to
#: SDMX-JSON on a MetadataSet, and a default-constructed set had no valid JSON
#: form (#234).
_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$")

#: Artefact classes the 3.0 message header can carry, from the choice in
#: ``GenericMetadataStructureType``. A MetadataProvisionAgreement URN matches
#: none of the three pattern facets — ``ProvisionAgreementReferenceType`` is
#: the *data* agreement — so an unresolved MPA-structured set cannot state its
#: structure in the header.
#: What a `MetadataSet.structure` may reference. `MetadataSetType` sequences a
#: choice of ``MetadataProvisionAgreement`` or ``Metadataflow`` and offers no
#: third option, and SDMX-JSON's metadata schema agrees — both reject a
#: MetadataStructure reference in this position (#232).
#:
#: Not to be confused with the message *header*, whose ``<mes:Structure>`` does
#: carry the MSD. The writer derives that from this reference; conflating the
#: two is what let an MSD reference through to be written as a
#: ``<md:Metadataflow>`` holding an MSD URN, which every schema rejects.
_SET_STRUCTURE_CLASSES = {"Metadataflow", "MetadataProvisionAgreement"}


def _unresolvable_mpa(ms: MetadataSet) -> bool:
    """True if *ms* names a MetadataProvisionAgreement with no reachable MSD.

    The 3.0 writer puts the *MSD* URN in the header, walking
    MPA -> Metadataflow -> MetadataStructure when the in-memory graph allows,
    and otherwise falling back to the reference's own URN. That fallback has no
    valid slot for a MetadataProvisionAgreement: ``GenericMetadataStructureType``
    offers ProvisionAgreement (``.registry.ProvisionAgreement=``), StructureUsage
    (Metadataflow) or Structure (MetadataStructure), and an MPA URN matches none
    of those pattern facets.

    Mirrors the writer's own walk rather than importing it — validation must not
    depend on a format module. Found by #232 extending the MetadataSet strategy
    to draw both arms of the schema's choice; it had only ever drawn
    Metadataflow, so this branch was never generated.
    """
    if ms.structure.urn.artefact_class != "MetadataProvisionAgreement":
        return False
    if not ms.structure.resolved:
        return True
    mpa = ms.structure()
    flow = getattr(mpa, "metadataflow", None)
    if flow is None or not flow.resolved:
        return True
    return getattr(flow(), "structure", None) is None


def _check_metadata_set_in_json(ms: MetadataSet, prefix: str, issues: list[ValidationIssue]) -> None:
    """What SDMX-JSON's metadata schema asks of a set beyond SDMX-ML's: a semantic version, and links (#422)."""
    if not _SEMVER_RE.match(ms.version):
        issues.append(
            ValidationIssue(
                path=f"{prefix}.version",
                message=(
                    f"SDMX-JSON metadata requires a semantic version (x.y.z), got {ms.version!r}; "
                    "valid in SDMX-ML, which states no such pattern"
                ),
            )
        )
    _check_links_in_json([ms], issues)


def check_metadata_serialisable(msg: MetadataMessage, *, version: str | None = None) -> None:
    """Raise unless every metadata set in *msg* can be written to *version*.

    The `check_serialisable` of the reference-metadata side. `serialize`
    previously returned early for a `MetadataMessage`, so metadata payloads
    reached the wire with no check of any kind — neither well-formedness nor
    writability (#234).

    Args:
        msg: The message about to be serialised.
        version: Target wire form — ``"3.0"`` or ``"json"``. When given, also
            checks constraints that hold in one and not the other.

    Raises:
        ValidationError: If any set cannot be represented on the wire.
    """
    issues: list[ValidationIssue] = []
    for ms in msg.metadata_sets:
        prefix = f"MetadataSet:{ms.id}"
        # MetadataSetType sequences choice(flow|provision), Target (1..n),
        # Attribute (1..n) — so a set that names a structure must also name
        # what the metadata is *about*. SDMX-JSON agrees.
        if not ms.targets:
            issues.append(
                ValidationIssue(
                    path=f"{prefix}.targets",
                    message=(
                        "a MetadataSet must reference at least one target; SDMX-ML 3.0 and SDMX-JSON both require it"
                    ),
                )
            )
        # Same rule as `_check_latest_refs` on the structure side (#233), and
        # for the same reason: a target is a URN string on the wire, and
        # `MetadataTargetUrnType`'s facet requires the parenthesised version.
        # There is no 2.1 escape hatch here — SDMX 3.0 dropped the v2.1
        # MetadataReport layer, so every form this message has is a 3.x one.
        for index, target in enumerate(ms.targets):
            if target.urn.is_latest:
                issues.append(
                    ValidationIssue(
                        path=f"{prefix}.targets[{index}]",
                        message=(
                            f"target {target.urn.artefact_class}={target.urn.agency}:{target.urn.id} has no "
                            "version, and both SDMX-ML 3.0 and SDMX-JSON require one on a target URN. "
                            'Build the reference with an explicit version instead of the "latest" default.'
                        ),
                    )
                )
        if ms.structure.urn.is_latest:
            issues.append(
                ValidationIssue(
                    path=f"{prefix}.structure",
                    message=(
                        f"the structure reference {ms.structure.urn.artefact_class}="
                        f"{ms.structure.urn.agency}:{ms.structure.urn.id} has no version, "
                        "which SDMX-ML 3.0 and SDMX-JSON both reject."
                    ),
                )
            )
        if version == "json":
            _check_metadata_set_in_json(ms, prefix, issues)
        if version == "3.0" and _unresolvable_mpa(ms):
            issues.append(
                ValidationIssue(
                    path=f"{prefix}.structure",
                    message=(
                        "a MetadataSet bound to a MetadataProvisionAgreement can only be written to "
                        "SDMX-ML 3.0 when the agreement resolves through to its MetadataStructure: the "
                        "message header's choice is ProvisionAgreement, StructureUsage or Structure, and "
                        "a MetadataProvisionAgreement URN matches none of their patterns. Resolve the "
                        "reference, or bind the set to its Metadataflow instead."
                    ),
                )
            )
        if ms.structure.urn.artefact_class not in _SET_STRUCTURE_CLASSES:
            issues.append(
                ValidationIssue(
                    path=f"{prefix}.structure",
                    message=(
                        f"a MetadataSet may reference a Metadataflow or a MetadataProvisionAgreement, "
                        f"not a {ms.structure.urn.artefact_class}; SDMX-ML 3.0 and SDMX-JSON both "
                        "state that choice and neither has an element for anything else"
                    ),
                )
            )
    if issues:
        raise ValidationError(issues)
