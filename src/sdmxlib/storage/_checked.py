"""A stored artefact is checked as it is built, and refused alone (#492).

A store read used to trust its rows. The bundle was written straight into
DuckDB, bypassing `Registry.add`, so its rows never met `validate()`, and an
artefact no SDMX format can write reached the model and stayed there until a
write found it. Every rule now runs as an artefact is built (#492): a value
the model refuses as it is set, or an artefact that breaks a rule across its
fields, is refused with a `Refusal` naming it.

Raised rather than returned, so a single read fails loudly and the caller one
level up decides: `LocalRegistry.get` raises it on, and its bulk reads record
it in `LocalRegistry.refused` and read on.
"""

from collections.abc import Callable

from sdmxlib.model.errors import Refusal, ValidationError
from sdmxlib.model.validation import SdmxConformanceError


def checked[T](artefact_class: str, urn: str, build: Callable[[], T]) -> T:
    """`build()`, which refuses an artefact a rule refuses as it builds it (#492).

    Args:
        artefact_class: The class the stored row names, e.g. ``"Codelist"``.
        urn: The stored artefact's URN.
        build: Builds the artefact from its rows.

    Returns:
        The artefact `build` returned.

    Raises:
        SdmxConformanceError: Carrying one `Refusal`, if building it raised
            `ValidationError` — a field validator, or a whole-artefact rule.
    """
    try:
        return build()
    except ValidationError as exc:
        reason = "; ".join(f"{issue.path} — {issue.message}" for issue in exc.issues)
        refusal = Refusal(artefact_class=artefact_class, urn=urn, reason=reason)
        detail = f"{urn} is stored and refused: {reason}"
        raise SdmxConformanceError(artefact_class, detail, refused=(refusal,)) from exc
