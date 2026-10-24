"""One thing a bundle check found wrong, shared by the local and the remote checks."""

from attrs import frozen


@frozen
class Finding:
    """One thing a check found wrong.

    Attributes:
        check: Which check: ``manifest``, ``files``, ``lake``, ``bindings``,
            ``references``, or an extension's name.
        subject: What it is about: a path from the bundle root, a URN, or the
            artefact a reference sits in.
        detail: What is wrong with it.
    """

    check: str
    subject: str
    detail: str

    def __str__(self) -> str:
        """``[check] subject: detail``."""
        return f"[{self.check}] {self.subject}: {self.detail}"
