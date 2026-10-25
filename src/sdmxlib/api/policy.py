"""`RegistryUnavailable` — what a read raises when a source cannot be reached.

**`None` means absent; unreachable raises.** That is design §5.8, and it is one
rule rather than a policy because the two are different facts and a caller
acting on the wrong one is acting on a wrong answer: retrying is right for a
dead source and pointless for a missing artefact, and falling back to a default
is right for a missing artefact and dangerous for a dead source.

`FailurePolicy` used to live here with `HARD_FAIL` / `RETURN_NONE`, and
`RETURN_NONE` did exactly the conflation above — the cookbook described it as
*"source down or artefact missing — same outcome to the caller"*. It was
deleted rather than re-scoped. Its stated job was *"whether a federation
continues past a dead source"*, and `FederatedRegistry` routes each URN to
exactly one source by agency, so there is no next source to continue to: the
knob only ever converted an error into a silence.

A caller who wants to continue past a dead source now writes the loop that says
so, which cannot mislabel anything:

    for urn in urns:
        try:
            artefact = fed.get(urn)
        except sl.RegistryUnavailable as exc:
            log.warning("skipping %s: %s", urn, exc)
            continue
        if artefact is None:
            ...  # genuinely absent, and only that
"""


class RegistryUnavailable(Exception):  # noqa: N818  # name matches #46 vocabulary
    """A source could not be reached, so no read of it can be believed.

    Wraps the underlying httpx2 / connection error so callers catch one type
    without depending on the HTTP layer. Carries the *source* and *what was
    tried*, because §5.8 asks the error to name both — a bare
    ``ConnectError: connection refused`` names neither, and in a federation
    the first question is always "which one?".
    """

    def __init__(self, source_id: str | None, cause: Exception, *, tried: str | None = None) -> None:
        """Record which source failed, doing what.

        Args:
            source_id: The registry's `id`, or `None` when it has none.
            cause: The underlying transport error, kept as `__cause__` too.
            tried: What the read was after — a URN, or a description. Optional
                because a caller deep in the stack may not have it, and a
                message naming the source alone still beats naming neither.
        """
        self.source_id = source_id
        self.cause = cause
        self.tried = tried
        source = f" {source_id!r}" if source_id else ""
        target = f" while reading {tried}" if tried else ""
        super().__init__(f"Registry{source} is unavailable{target}: {cause}")
