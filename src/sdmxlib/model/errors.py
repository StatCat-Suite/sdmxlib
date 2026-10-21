"""The one exception a caller needs to handle a payload it cannot trust.

Also `Refusal`, the record of one artefact a reader declined to build (#435):
`StructureMessage` carries them and `SdmxConformanceError` raises with them, so
it lives below both.

Its own module, importing nothing from sdmxlib, because both ends of the read
path need it and they sit on opposite sides of an import edge: `sdmxlib.model.validation`
imports `sdmxlib.model.urn`, and a malformed URN inside a document is a
malformed document. Defining `SdmxParseError` in either one would make the
other import it backwards.

`sdmxlib.model.validation` re-exports it, which is where the rest of the
error hierarchy lives and where callers should expect to find it.

`ValidationIssue` and `ValidationError` live here for the same reason (#492):
the model's own fields refuse a value no SDMX id, version or name can hold as
the field is set (`sdmxlib.model._rules`), so the model raises them too, and
`sdmxlib.model.validation` imports the model. `SdmxWarning` and `ImRuleWarning`
sit beside them, for the rules an artefact is built under that warn rather
than refuse (#481).
"""

from attrs import frozen


class SdmxParseError(ValueError):
    """Raised when a payload cannot be read as SDMX.

    The one exception type a caller needs to catch to handle a bad payload.
    Before this existed, `parse` documented ``ValueError`` and actually raised
    five types, four of them from the untyped shell — ``lxml.etree
    .XMLSyntaxError``, ``msgspec.DecodeError``, ``msgspec.ValidationError``
    and ``UnicodeDecodeError``. Handling a malformed provider response
    therefore meant importing lxml and msgspec to name their exceptions, which
    is the opposite of the boundary this library is supposed to draw: parse,
    validate, hand typed objects inward.

    The underlying failure is preserved as ``__cause__``, so nothing is lost —
    a caller that genuinely wants the lxml line and column can still reach it.

    Subclasses `ValueError` because that is what `parse` already promised and
    what every wrapped exception except lxml's already was, so existing
    ``except ValueError`` handlers keep working and the docstring stays true.

    Example:
        try:
            msg = sl.parse(response.content)
        except sl.SdmxParseError as e:
            log.warning("provider sent unreadable SDMX: %s", e)
    """


@frozen
class Refusal:
    """One artefact a reader declined to build, and why (#435).

    A document is a batch of independent maintainable artefacts, and one of
    them contradicting the schema used to lose all of them: registry.sdmx.org's
    3.0 ``datastructure`` document carries 23 DSDs, three enumerate a
    dimension with a ConceptScheme, and the reader returned none. The unit of
    failure is now the artefact — the reader records a `Refusal` and reads on.

    Plain data, so it can be logged, tallied and compared. It carries no
    partial artefact: a refused artefact is never constructed.

    Attributes:
        artefact_class: The SDMX class the element would have built, e.g.
            ``"DataStructure"``.
        urn: Best-effort URN text, from what the element itself says — its
            agency, id and version. The element was not read, so nothing in it
            was checked; this names it, it does not vouch for it.
        reason: Why it was refused — the `SdmxConformanceError` message, or
            each rule it broke that `Registry.add` enforces (#419).
    """

    artefact_class: str
    urn: str
    reason: str

    def __str__(self) -> str:
        """``<urn>: <reason>`` — one refusal per line in an error message."""
        return f"{self.urn}: {self.reason}"


@frozen
class ReadGap:
    """Content a reader met and the model has nowhere to put (#422).

    The artefact is built without it and the loss is recorded here, rather
    than the artefact refused: a DataStructure carrying one `com:Link` is still
    the DataStructure, and refusing it would cost everything else it says. A
    `Refusal` is for an artefact that cannot be built at all.

    Attributes:
        urn: The artefact that lost it.
        construct: What was lost, as the format spells it — ``com:Link``,
            ``str:Dimension/@position``.
        count: How many occurrences in this artefact.
        reason: What the model holds instead, and why that is less.
    """

    urn: str
    construct: str
    count: int
    reason: str

    def __str__(self) -> str:
        """``<urn>: <construct> x<count> lost — <reason>``."""
        return f"{self.urn}: {self.construct} x{self.count} lost — {self.reason}"


@frozen
class ReadAssumption:
    """A value a reader filled in because the document omitted something the schema requires (#474).

    A `ReadGap` records what a reader dropped; this records what it supplied.
    The artefact holds the assumed value exactly as if the document had stated
    it, so without this a caller cannot tell the two apart. A value the schema
    itself defaults is the standard's own answer, and is never one of these.

    Attributes:
        urn: The artefact holding the assumed value.
        construct: What was missing, as the format spells it, e.g.
            ``str:Attribute/str:AttributeRelationship``.
        assumed: The value used in its place, e.g. ``attachment DATASET``.
        count: How many occurrences in this artefact.
        reason: Why that value: the tolerance it implements.
    """

    urn: str
    construct: str
    assumed: str
    count: int
    reason: str

    def __str__(self) -> str:
        """``<urn>: <construct> x<count> missing, assumed <value> — <reason>``."""
        return f"{self.urn}: {self.construct} x{self.count} missing, assumed {self.assumed} — {self.reason}"


@frozen
class ValidationIssue:
    """A single validation problem found on an artefact.

    Attributes:
        path: Dotted path to the offending field (e.g. ``"Codelist.codes[..].id"``).
        message: Human-readable description of the problem.
    """

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path} — {self.message}"


def _listed(issues: list[ValidationIssue], noun: str) -> str:
    """``<n> <noun>s:`` and the first five issues, one per line, with a count of the rest."""
    n = len(issues)
    head_count = 5
    head = "\n".join(f"  {i}" for i in issues[:head_count])
    suffix = f"\n  ... and {n - head_count} more" if n > head_count else ""
    plural = "" if n == 1 else "s"
    return f"{n} {noun}{plural}:\n{head}{suffix}"


class ValidationError(Exception):
    """Raised when an artefact breaks a rule as it is built, or a message or format check finds an issue.

    All issues are accumulated — ``issues`` holds the full list, not just
    the first problem. The exception message renders the first few inline
    with a count of the rest.

    Example:
        try:
            codelist = Codelist(id="CL", maintainer="AG", name={"en": "c"}, codes=codes)
        except ValidationError as e:
            for issue in e.issues:
                fix(issue)
    """

    issues: list[ValidationIssue]

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__(_listed(issues, "validation issue"))


class SdmxWarning(UserWarning):
    """The base of every warning sdmxlib gives.

    `warnings.simplefilter("error", sl.SdmxWarning)` turns them all into
    errors, and `"ignore"` silences them.
    """


class ImRuleWarning(SdmxWarning):
    """An artefact breaks a rule the information model states and no wire schema enforces (#481).

    The artefact is still built: every SDMX format can carry it, and refusing
    would break documents and builders that work today. One warning per
    artefact, carrying every rule it breaks.

    Example:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", sl.ImRuleWarning)
            msg = sl.parse(document)
        for warning in caught:
            if isinstance(warning.message, sl.ImRuleWarning):
                for issue in warning.message.issues:
                    print(issue.path, issue.message)
    """

    issues: list[ValidationIssue]

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__(_listed(issues, "information-model rule") + "\n  (built anyway; see sl.ImRuleWarning)")
