"""InternationalString — dict-like multilingual string — and the language tags that key it."""

import re
import sys
from collections.abc import Iterator, Mapping
from typing import ClassVar, Self

from attrs import field, frozen

#: The RFC 5646 language-tag grammar, as the SDMX-JSON 2.1 structure schema
#: writes it: the `localisedText` `patternProperties` key in
#: `tests/schemas/sdmx_json/2.1/sdmx-json-structure-schema.json`, without its
#: `^` and `$`. `tests/unit/test_language_tags.py` asserts the two are equal.
TAG_PATTERN = (
    r"((?:(en-GB-oed|i-ami|i-bnn|i-default|i-enochian|i-hak|i-klingon|i-lux|i-mingo|i-navajo|i-pwn|"
    r"i-tao|i-tay|i-tsu|sgn-BE-FR|sgn-BE-NL|sgn-CH-DE)|(art-lojban|cel-gaulish|no-bok|no-nyn|zh-guoyu|"
    r"zh-hakka|zh-min|zh-min-nan|zh-xiang))|((?:([A-Za-z]{2,3}(-(?:[A-Za-z]{3}(-[A-Za-z]{3}){0,2})"
    r")?)|[A-Za-z]{4}|[A-Za-z]{5,8})(-(?:[A-Za-z]{4}))?(-(?:[A-Za-z]{2}|[0-9]{3}))?(-(?:[A-Za-z0-9]{5,8}|"
    r"[0-9][A-Za-z0-9]{3}))*(-(?:[0-9A-WY-Za-wy-z](-[A-Za-z0-9]{2,8})+))*(-(?:x(-[A-Za-z0-9]{1,8})"
    r"+))?)|(?:x(-[A-Za-z0-9]{1,8})+))"
)
_TAG_RE = re.compile(TAG_PATTERN)

#: The longest tag `normalize_tag` matches. The registries send two letters;
#: the cap bounds what an untrusted source can make the pattern read.
MAX_TAG_LENGTH = 256

#: How much of a refused tag an error message quotes.
_QUOTED = 64


def normalize_tag(tag: str) -> str:
    """The one stored spelling of a language tag: lowercase, and well-formed.

    `tag` is lowercased and accepted when it matches the RFC 5646 grammar
    (`TAG_PATTERN`). Nothing else is changed: whitespace, an underscore for a
    hyphen and an empty string are refused, as are the four grandfathered
    tags RFC 5646 spells in mixed case (`en-GB-oed`, `sgn-BE-FR`, `sgn-BE-NL`,
    `sgn-CH-DE`), whose lowercase forms do not match. The check is for
    well-formedness only: `qq-zz` passes, and a deprecated subtag is kept.

    Args:
        tag: A language tag, such as ``"en"`` or ``"fr-CA"``.

    Returns:
        `tag` in lowercase.

    Raises:
        TypeError: If `tag` is not a `str`.
        ValueError: If `tag` is not a well-formed language tag, or is longer
            than `MAX_TAG_LENGTH`. The message quotes at most 64 characters
            of it.

    Example:
        normalize_tag("fr-CA")  # "fr-ca"
        normalize_tag("fr_CA")  # ValueError
    """
    if not isinstance(tag, str):
        msg = f"a language tag is a str, not {type(tag).__name__}"
        raise TypeError(msg)
    # ASCII first: `str.lower` maps some non-ASCII letters to ASCII ones (the
    # Kelvin sign to "k"), which would turn a malformed tag into a valid one.
    # Two ASCII letters are always a well-formed tag, and are every tag the
    # surveyed registries send, so they skip the match: it halved what
    # checking costs a parse (`docs/design/language-tags.md`, "As built").
    # `tests/unit/test_language_tags.py` checks the two agree on every pair.
    if len(tag) == 2 and tag.isascii() and tag.isalpha():
        return tag.lower()
    if len(tag) <= MAX_TAG_LENGTH and tag.isascii():
        lowered = tag.lower()
        if _TAG_RE.fullmatch(lowered):
            return lowered
    msg = f"{tag[:_QUOTED]!r} is not a well-formed language tag (RFC 5646), such as 'en' or 'fr-ca'"
    raise ValueError(msg)


@frozen
class LocalizedText:
    """One text and the language tag it was found under.

    What `InternationalString.pick` returns, so a caller asking for
    ``("fr-ca", "fr", "en")`` learns which tag answered. Compares and hashes
    by both fields.

    Attributes:
        text: The text.
        lang: Its language tag, normalised by `normalize_tag`; a malformed one
            raises `ValueError`.
    """

    text: str
    lang: str = field(converter=normalize_tag)


def _normalized(pairs: Mapping[str, str]) -> dict[str, str]:
    """`pairs` keyed by normalised, interned tags.

    Raises:
        ValueError: If a key is not a well-formed tag, or two keys normalise
            to one tag: two texts for one language, which sdmxlib never
            chooses between.
    """
    data: dict[str, str] = {}
    for tag, text in pairs.items():
        lang = normalize_tag(tag)
        if lang in data:
            msg = f"two texts for language {lang!r}"
            raise ValueError(msg)
        # Interned (#116): a small pool of tags repeats across thousands of
        # strings built during a parse, and one shared `str` per tag saved
        # 200-400 KiB on large XML messages.
        data[sys.intern(lang)] = text
    return data


class InternationalString:
    """A multilingual string where language access is always explicit.

    Maps language tags to texts. Immutable after construction (no
    ``__setitem__``). Every tag is normalised by `normalize_tag` as it enters,
    so keys are lowercase and well-formed, and every lookup normalises the
    tag it is asked for: ``s["FR-CA"]`` finds the text stored under
    ``fr-ca``.

    Example:
        s = InternationalString(en="Annual", fr="Annuel")
        s["en"]  # "Annual"
        s.get("de", "en")  # "Annual" (fallback)
        s.pick("de", "en")  # LocalizedText(text="Annual", lang="en")
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
        """Build from keyword arguments, one per language.

        Raises:
            ValueError: If a tag is not well-formed, or two normalise to one.
        """
        self._data: dict[str, str] = _normalized(kwargs)

    # ── Construction helpers ───────────────────────────────────────────────

    @classmethod
    def of(cls, pairs: Mapping[str, str]) -> InternationalString:
        """Build from an existing mapping.

        Returns the shared `EMPTY` for an empty mapping — the common case on a
        real bundle, and the one that used to allocate. Only for this
        exact class: a subclass gets its own fresh instance, since `EMPTY` is
        an `InternationalString` and returning it would be a type error the
        annotation cannot see.

        Raises:
            ValueError: If a tag is not well-formed, or two normalise to one.
        """
        return cls._from_normalized(_normalized(pairs))

    @classmethod
    def _from_normalized(cls, pairs: dict[str, str]) -> InternationalString:
        """Build from a map whose tags are already normalised, without checking them.

        For the store readers, whose rows were normalised as they were
        written, and for the format readers, which normalise each tag
        themselves to refuse a bad one as a contradiction. `check_bundle`
        reports a store holding a tag `normalize_tag` would change.

        One call deep, as the store path's call-count bounds hold it
        (`tests/bench/test_bundle_reads.py`).
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
        """Return the text for `lang`.

        Raises:
            KeyError: If no text is held for `lang`, or `lang` is not a
                well-formed tag (chained from the `ValueError`).
        """
        try:
            key = normalize_tag(lang)
        except ValueError as exc:
            raise KeyError(lang) from exc
        return self._data[key]

    def get(self, *langs: str) -> str | None:
        """The text of the first of `langs` held, or None.

        Raises:
            ValueError: If one of `langs` is not a well-formed tag.
        """
        for lang in langs:
            text = self._data.get(normalize_tag(lang))
            if text is not None:
                return text
        return None

    def pick(self, *langs: str) -> LocalizedText | None:
        """The first of `langs` held, with the tag that answered, or None.

        Each tag is normalised, then matched exactly: asking for
        ``("fr-CA", "fr")`` returns the ``fr`` text with ``lang="fr"`` when
        only that is held.

        Raises:
            ValueError: If one of `langs` is not a well-formed tag.
        """
        for lang in langs:
            key = normalize_tag(lang)
            text = self._data.get(key)
            if text is not None:
                return LocalizedText(text, key)
        return None

    def _text(self, lang: str) -> str | None:
        """The text held under `lang`, a tag already normalised; not checked.

        For loops that normalise one tag once and then look it up in each of
        many strings, such as `Codelist.code_map`.
        """
        return self._data.get(lang)

    def __contains__(self, lang: object) -> bool:
        """Whether a text is held for `lang`; False for a malformed tag or a non-`str`."""
        if not isinstance(lang, str):
            return False
        try:
            return normalize_tag(lang) in self._data
        except ValueError:
            return False

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
