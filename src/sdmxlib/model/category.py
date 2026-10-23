"""CategoryScheme, Category, Categorisation — classification of artefacts."""

from datetime import datetime
from typing import ClassVar, override

from attrs import evolve, field, frozen

from sdmxlib._time import as_utc
from sdmxlib.model._invariants import enforce
from sdmxlib.model._items import item_ref
from sdmxlib.model._refshape import derived_view
from sdmxlib.model._rules import id_type, maintained_by, named, ncname_id_type, version_type
from sdmxlib.model._tree import PathIndex, PathNavigation
from sdmxlib.model.annotations import Annotations
from sdmxlib.model.base import MaintainableArtefact
from sdmxlib.model.collections import ItemList, to_itemlist
from sdmxlib.model.istring import InternationalString
from sdmxlib.model.link import Link, to_links
from sdmxlib.model.organisation import Agency, _to_agency, _to_annotations, _to_istring
from sdmxlib.model.ref import Ref, refuse_urn_text, to_optional_ref
from sdmxlib.model.urn import ParsesUrn


@frozen(unsafe_hash=False)
class Category(ParsesUrn):
    """A node in a classification hierarchy."""

    #: See `Code.sdmx_package` — the item's own URN pair, not its container's.
    #: A category URN is ``…categoryscheme.Category=AG:CS(1.0).ECON``, and the
    #: pair is what `validate_references` builds that from (#404).
    sdmx_package: ClassVar[str] = "categoryscheme"
    sdmx_class: ClassVar[str] = "Category"

    id: str = field(validator=id_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    categories: ItemList[Category] = field(factory=ItemList, converter=to_itemlist)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)


@frozen(unsafe_hash=False)
class CategoryScheme(MaintainableArtefact, PathNavigation[Category]):
    """A hierarchical classification scheme."""

    sdmx_package: ClassVar[str] = "categoryscheme"
    sdmx_class: ClassVar[str] = "CategoryScheme"
    msg_field: ClassVar[str] = "category_schemes"

    id: str = field(validator=ncname_id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    #: SDMX 2.1's `isFinal`, kept only where it disagrees with the version
    #: (see `MaintainableArtefact.is_final`). Only the 2.1 reader sets it.
    legacy_is_final: bool | None = None
    categories: ItemList[Category] = field(factory=ItemList, converter=to_itemlist)
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    valid_from: datetime | None = field(default=None, converter=as_utc)
    valid_to: datetime | None = field(default=None, converter=as_utc)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)
    #: SDMX ``@isExternalReference``, ``@serviceURL``, ``@structureURL`` — see
    #: `MaintainableArtefact` (#422).
    is_external_reference: bool = False
    service_url: str | None = None
    structure_url: str | None = None
    #: SDMX 3.1 ``@isPartialLanguage``: some text is missing a language the
    #: rest carries (#422).
    is_partial_language: bool = False
    #: SDMX ``@isPartial``: the scheme holds a subset of its items (#422).
    is_partial: bool = False
    # Set by `__attrs_post_init__`, so no default: the index is a function of
    # the tree field and has no meaning before it.
    _tree: PathIndex[Category] = field(init=False, repr=False, eq=False, metadata=derived_view())

    def ref_to(self, path: str) -> Ref[Category]:
        """Build a resolved `Ref` to a category within this scheme, by path.

        A nested category is named by its dotted path, which is also the item
        part of its URN: ``cs.ref_to("ECON.GDP")`` gives
        ``Category=AG:CS(1.0).ECON.GDP``.

        The URN is built from *this* scheme's agency, id and version, so it
        cannot name a parent that does not exist — which is what six hand-written
        keyword arguments to `SdmxUrn.make` invite, and what
        `validate_references` now correctly reports as broken.

        Raises:
            KeyError: If no category has this path in this scheme.

        Example:
            Categorisation(..., target=cs.ref_to("ECON.GDP"))
        """
        return item_ref(self, path, Category)

    def __attrs_post_init__(self) -> None:
        # Construction, not mutation: the one write a frozen class makes to itself (#492).
        object.__setattr__(self, "_tree", PathIndex(self.categories, lambda c: c.categories))
        # Its whole-artefact rules, once the index they read is built (#492).
        enforce(self)

    @property
    @override
    def _paths(self) -> PathIndex[Category]:
        return self._tree

    # ── SKOS/XKOS hierarchy navigation ────────────────────────────────────
    # Path-keyed, from `PathNavigation`; `roots` is this scheme's own field.

    def roots(self) -> ItemList[Category]:
        """Top-level categories (no parent)."""
        return self.categories

    def add(self, categories: Category | list[Category]) -> CategoryScheme:
        """Return a new CategoryScheme with *categories* added.

        Accepts a single Category or a list of Categories.
        """
        new = [categories] if isinstance(categories, Category) else list(categories)
        return evolve(self, categories=[*self.categories, *new])

    def drop(self, category_ids: str | list[str]) -> CategoryScheme:
        """Return a new CategoryScheme with the given category(ies) removed.

        Accepts a single id string or a list of id strings.
        Raises KeyError if any id is not present.
        """
        ids = [category_ids] if isinstance(category_ids, str) else list(category_ids)
        missing = [i for i in ids if i not in self.categories]
        if missing:
            raise KeyError(missing[0] if len(missing) == 1 else missing)
        id_set = set(ids)
        return evolve(self, categories=[c for c in self.categories if c.id not in id_set])


@frozen(unsafe_hash=False)
class Categorisation(MaintainableArtefact):
    """Links an artefact (source) to a Category (target)."""

    sdmx_package: ClassVar[str] = "categoryscheme"
    sdmx_class: ClassVar[str] = "Categorisation"
    msg_field: ClassVar[str] = "categorisations"

    id: str = field(validator=id_type)
    maintainer: Agency = field(converter=_to_agency, validator=maintained_by)
    version: str = field(default="1.0", validator=version_type)
    name: InternationalString = field(converter=_to_istring, validator=named, kw_only=True)
    description: InternationalString = field(factory=InternationalString, converter=_to_istring)
    #: SDMX 2.1's `isFinal`, kept only where it disagrees with the version
    #: (see `MaintainableArtefact.is_final`). Only the 2.1 reader sets it.
    legacy_is_final: bool | None = None
    annotations: Annotations = field(factory=Annotations, converter=_to_annotations)
    valid_from: datetime | None = field(default=None, converter=as_utc)
    valid_to: datetime | None = field(default=None, converter=as_utc)
    source: Ref[object] | None = field(converter=to_optional_ref, default=None, validator=refuse_urn_text)
    target: Ref[object] | None = field(converter=to_optional_ref, default=None, validator=refuse_urn_text)
    #: SDMX ``@uri`` — an external link describing this artefact, not an
    #: identifier. See `MaintainableArtefact.uri` (#238).
    uri: str | None = None
    #: SDMX 3.x's ``<com:Link>`` elements, in order (#422). See `Link`.
    links: tuple[Link, ...] = field(factory=tuple, converter=to_links)
    #: SDMX ``@isExternalReference``, ``@serviceURL``, ``@structureURL`` — see
    #: `MaintainableArtefact` (#422).
    is_external_reference: bool = False
    service_url: str | None = None
    structure_url: str | None = None
    #: SDMX 3.1 ``@isPartialLanguage``: some text is missing a language the
    #: rest carries (#422).
    is_partial_language: bool = False
