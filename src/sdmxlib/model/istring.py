"""InternationalString — dict-like multilingual string."""

import sys
from collections.abc import Iterator
from typing import ClassVar, Self


class InternationalString:
    """A multilingual string where language access is always explicit.

    Stores key-value pairs of language code → text. Immutable after
    construction (no ``__setitem__``).

    Example:
        s = InternationalString(en="Annual", fr="Annuel")
        s["en"]  # "Annual"
        s.get("de", "en")  # "Annual" (fallback)
        "fr" in s  # True
        s.langs  # ["en", "fr"]
    """

    __slots__ = ("_data",)

    #: The empty string, shared. Assigned below the class body, since it is an
    #: instance of the class being defined.
    #:
    #: Sharing one is safe because this class is genuinely immutable — no
    #: `__setitem__`, no mutating method, `_data` reachable only through
    #: `__slots__`. The two ways out of that are `as_dict(copy=False)` and
    #: `emit_pair()`, which hand back the backing map and document it as
    #: read-only; a caller that mutates either would now corrupt every empty
    #: istring in the process rather than just its own.
    #: `tests/unit/test_istring_empty_is_shared.py` holds that contract.
    #:
    #: Worth having because the empty case is not a corner: the 2021 Census Profile
    #: bundle carries 585,675 annotations and 10,064 annotation-text rows, so
    #: ~98% of annotation texts are empty, and a read of one codelist built
    #: 1,159,902 `InternationalString`s (#377).
    EMPTY: ClassVar[InternationalString]

    def __init__(self, **kwargs: str) -> None:
        # Intern lang codes (#116). High-cardinality keys repeated across
        # thousands of istring constructions during parse — interning
        # collapses them to a small finite pool of shared str objects,
        # saving 200-400 KiB on large XML messages.
        self._data: dict[str, str] = {sys.intern(k): v for k, v in kwargs.items()}

    # ── Construction helpers ───────────────────────────────────────────────

    @classmethod
    def of(cls, pairs: dict[str, str]) -> InternationalString:
        """Build from an existing dict.

        Returns the shared `EMPTY` for an empty mapping — the common case on a
        real bundle, and the one that used to allocate. Only for this
        exact class: a subclass gets its own fresh instance, since `EMPTY` is
        an `InternationalString` and returning it would be a type error the
        annotation cannot see.
        """
        if not pairs and cls is InternationalString:
            return EMPTY
        # `Self`-annotated so the local carries this class's fields; `cls.__new__`
        # alone gives no field information.
        obj: Self = cls.__new__(cls)
        obj._data = {sys.intern(k): v for k, v in pairs.items()}
        return obj

    # ── Access ────────────────────────────────────────────────────────────

    def __getitem__(self, lang: str) -> str:
        """Return the string for *lang*, raising KeyError if absent."""
        return self._data[lang]

    def get(self, *langs: str) -> str | None:
        """Return the first available language in priority order, or None."""
        for lang in langs:
            if lang in self._data:
                return self._data[lang]
        return None

    def __contains__(self, lang: object) -> bool:
        return lang in self._data

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __bool__(self) -> bool:
        return bool(self._data)

    @property
    def langs(self) -> list[str]:
        """List of available language codes."""
        return list(self._data)

    def items(self) -> Iterator[tuple[str, str]]:
        """Iterate over (lang, text) pairs in insertion order."""
        return iter(self._data.items())

    def as_dict(self, *, copy: bool = True) -> dict[str, str]:
        """The language→text mapping.

        Fast path for serialisers that need the whole map: a direct ``dict``
        copy avoids the ``items()`` iterator + ``dict(generator)`` overhead on
        hot per-item paths (annotation/name/description emit).

        Args:
            copy: When True (default) return a shallow copy that is safe to
                mutate. When False return the backing map directly — no copy —
                for the serialise hot path, where the result feeds a transient,
                immediately-discarded output structure and is never mutated.
                Callers passing ``copy=False`` must treat the result as
                read-only.
        """
        return dict(self._data) if copy else self._data

    def emit_pair(self) -> tuple[str | None, dict[str, str] | None]:
        """``(best-match string, full lang→text map)`` for the SDMX-JSON split.

        The serialise hot path's single-call primitive: collapses the
        empty-check + best-match pick + backing-map fetch into one method with
        direct field access, avoiding the separate ``__bool__`` / ``as_dict`` /
        ``iter``/``next`` calls that dominate `emit_istr` at scale. Returns
        ``(None, None)`` when empty so ``omit_defaults`` drops both wire fields.
        The map is the backing store (not copied) — read-only for the caller.
        """
        d = self._data
        if not d:
            return None, None
        return next(iter(d.values())), d

    # ── Equality & repr ───────────────────────────────────────────────────

    def __eq__(self, other: object) -> bool:
        if isinstance(other, InternationalString):
            return self._data == other._data
        return NotImplemented

    def __hash__(self) -> int:
        return hash(tuple(sorted(self._data.items())))

    def __repr__(self) -> str:
        pairs = ", ".join(f"{k}={v!r}" for k, v in self._data.items())
        return f"InternationalString({pairs})"


#: See `InternationalString.EMPTY`. Bound after the class body because it is an
#: instance of it; the class attribute is the public spelling.
EMPTY = InternationalString()
InternationalString.EMPTY = EMPTY


def to_istring(v: InternationalString | dict[str, str]) -> InternationalString:
    """Attrs converter: accept either an InternationalString or a plain dict."""
    return InternationalString.of(v) if isinstance(v, dict) else v
