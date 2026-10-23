"""MaintainableArtefact mixin — provides the .urn_for() classmethod."""

from typing import TYPE_CHECKING, ClassVar, Self

from sdmxlib.model.istring import InternationalString
from sdmxlib.model.urn import ParsesUrn, SdmxUrn
from sdmxlib.model.version import is_final

if TYPE_CHECKING:
    # `organisation` subclasses `MaintainableArtefact`, so it cannot be imported
    # here at runtime; the one annotation it serves is a property getter's.
    from sdmxlib.model.organisation import Agency


class MaintainableArtefact(ParsesUrn):
    """Mixin for top-level SDMX artefacts that have a globally unique URN.

    Subclasses must declare ``sdmx_package`` and ``sdmx_class`` as
    class variables. The ``.urn_for()`` classmethod then constructs a typed
    ``SdmxUrn[Self]`` without repeating the logic in every class.

    NOTE: Subclasses must redeclare the standard fields (id, maintainer,
    version, name, description, annotations) because slotted
    dataclasses cannot inherit slots from a non-dataclass parent.
    Python requires that all ``__slots__`` are declared on the class that
    uses them.

    Example:
        Codelist.urn_for(agency="SDMX", id="CL_FREQ")  # SdmxUrn[Codelist]
        DataStructure.urn_for(agency="ESTAT", id="CPI", version="2.0")
    """

    sdmx_package: ClassVar[str]
    sdmx_class: ClassVar[str]
    msg_field: ClassVar[str]
    #: How many subclasses have been defined so far, so a cache of them can
    #: tell that an import has added one (`model._items._maintainable_classes`,
    #: #405, #464).
    _subclasses_defined: ClassVar[int] = 0

    def __init_subclass__(cls) -> None:
        """Count the subclass, for `_subclasses_defined`."""
        super().__init_subclass__()
        MaintainableArtefact._subclasses_defined += 1

    # Every maintainable's fields, as read-only properties the attrs subclasses
    # override with their own slotted fields. Annotations alone (the PEP 526
    # "abstract attribute" idiom used before) declare an attribute nothing in
    # the class sets, which pyrefly's `uninitialized-instance-variable` refuses
    # — and a protocol base, the other way to state them, puts `typing`'s
    # protocol metaclass on every artefact class and made an `isinstance` that
    # misses five times slower (20 ns to 101 ns, measured). The bodies never
    # run: an attrs subclass's field replaces each. Slots cannot be inherited
    # through a non-slotted parent, so the fields themselves stay declared on
    # each subclass.
    @property
    def id(self) -> str:
        """The artefact's id, unique for its agency and version."""
        raise NotImplementedError

    # Read type is strict ``Agency``: every concrete subclass declares
    # ``maintainer: Agency = field(converter=_to_agency)``, so the ``str``
    # convenience input is absorbed by the converter and reads are always
    # ``Agency``. Declaring the mixin loosely as ``Agency | str`` was an
    # inconsistent mutable-attribute override of the strict subclass fields.
    @property
    def maintainer(self) -> Agency:
        """The agency that maintains the artefact."""
        raise NotImplementedError

    @property
    def version(self) -> str:
        """The artefact's version."""
        raise NotImplementedError

    # SDMX types every maintainable as a ``NameableArtefact``, and the XSD's
    # ``<com:Name>`` carries no ``minOccurs`` — so a name is required, not
    # decorative. Declared here (rather than only on each subclass) so
    # the rules can check it generically; `_check_name` enforces it.
    @property
    def name(self) -> InternationalString:
        """The artefact's name, in each language it has."""
        raise NotImplementedError

    # ``@uri`` on ``IdentifiableType``: "a URI that contains a link to a
    # resource with additional information about the object, such as a web
    # page. This uri is not a SDMX message."
    #
    # So it is a *pointer*, not an identifier — the URN is the identity, and
    # two artefacts may share a uri or carry none. Declared on
    # `IdentifiableType`, which is why every item and component carries it too,
    # not just maintainables: the ECB's DSD links to its statistics vocabulary,
    # and nothing was reading or writing it, so every round-trip dropped it
    # (#238). Optional in all three SDMX-ML versions; ``prohibited`` in exactly
    # one place, ``GroupDimensionBaseType``, which sdmxlib never emits an id on.
    @property
    def uri(self) -> str | None:
        """``@uri``: a link to more about the artefact, not its identity."""
        raise NotImplementedError

    # ``@isExternalReference`` on ``MaintainableType``: this is a stub, and the
    # full artefact is retrieved from ``@structureURL`` (an SDMX-ML document
    # holding it) or ``@serviceURL`` (a registry or REST service). Every
    # version allows all three, and every reader dropped them unreported
    # until the attribute census (#422). sdmx1 models them the same way.
    @property
    def is_external_reference(self) -> bool:
        """``@isExternalReference``: this is a stub of an artefact held elsewhere."""
        raise NotImplementedError

    @property
    def service_url(self) -> str | None:
        """``@serviceURL``: a registry or REST service holding the full artefact."""
        raise NotImplementedError

    @property
    def structure_url(self) -> str | None:
        """``@structureURL``: an SDMX-ML document holding the full artefact."""
        raise NotImplementedError

    # SDMX 3.1's ``@isPartialLanguage``: the artefact's text is not in every
    # language it uses (#422). 3.0 and 2.1 have no such attribute.
    @property
    def is_partial_language(self) -> bool:
        """SDMX 3.1's ``@isPartialLanguage``: the text is not in every language it uses."""
        raise NotImplementedError

    @property
    def is_final(self) -> bool:
        """Whether this artefact is final, read from its version as SDMX 3.0 does.

        Derived rather than stored: 3.0 dropped `isFinal` and made
        finality a fact about the version, so `1.0.0` is final and `1.0` and
        `1.0.0-draft` are not. A 2.1 document's `isFinal`, where it says
        otherwise, is kept on `legacy_is_final` by the classes 2.1 lets carry
        it, and only the 2.1 writer reads it — the convention FMR follows.
        """
        return is_final(self.version)

    @property
    def agency_id(self) -> str:
        """Maintainer agency identifier. Derived from the ``maintainer`` field."""
        return self.maintainer.id

    @property
    def urn(self) -> SdmxUrn[Self]:
        """This artefact's canonical URN.

        A property because `Ref[T].urn` is one, and a reader holding either
        should not have to remember which. It used to be the *builder* — a
        classmethod — so `flow.urn` on an instance gave back a bound method,
        and `ref.py`'s own docstring offered `Ref.unresolved(cl_freq.urn)` as
        an example, which raised. The builder is `urn_for`.

        **Recomputed, not cached.** Artefacts are frozen, so a cache would be
        sound, but none is added until a profile names this line — see
        `docs/design/caching.md`.

        Typed `SdmxUrn[Self]`, which `artefact_urn()` cannot be: that takes a
        `SupportsUrn` and has no concrete type to return, so it hands back
        `SdmxUrn[object]`. This keeps the type, so
        `Ref.unresolved(flow.urn)` is a `Ref[Dataflow]`.
        """
        return SdmxUrn.make(
            type(self),
            package=self.sdmx_package,
            artefact_class=self.sdmx_class,
            agency=self.agency_id,
            id=self.id,
            version=self.version,
        )

    @classmethod
    def urn_for(
        cls,
        *,
        agency: str,
        id: str,
        version: str = "latest",
    ) -> SdmxUrn[Self]:
        """A typed `SdmxUrn` for this artefact class, from coordinates.

        The building half of what `urn` used to be both of. Named apart from
        the property because the two differ in more than receiver: this
        defaults `version` to `"latest"`, which is right for an identity you
        are looking *up*, while an artefact you hold has whatever version it
        has.
        """
        return SdmxUrn.make(
            cls,
            package=cls.sdmx_package,
            artefact_class=cls.sdmx_class,
            agency=agency,
            id=id,
            version=version,
        )

    @classmethod
    def parse_rest_ref(cls, ref: str) -> SdmxUrn[Self]:
        """Parse an SDMX REST comma-separated artefact reference.

        SDMX REST URLs identify a maintainable artefact as
        ``AGENCY,ID,VERSION``. Symmetric with `urn`.

        Example:
            Dataflow.parse_rest_ref("ESTAT,STS_INPR_M,1.0")

        Args:
            ref: Comma-separated ``AGENCY,ID,VERSION`` string.

        Returns:
            A typed `SdmxUrn` for this artefact class.

        Raises:
            ValueError: If ``ref`` does not have exactly three
                comma-separated parts.
        """
        parts = ref.split(",")
        if len(parts) != 3:
            msg = f"REST ref {ref!r} must be 'AGENCY,ID,VERSION' (got {len(parts)} parts)"
            raise ValueError(msg)
        agency, id_, version = parts
        return cls.urn_for(agency=agency, id=id_, version=version)
