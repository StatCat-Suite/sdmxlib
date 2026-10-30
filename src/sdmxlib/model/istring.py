"""InternationalString — dict-like multilingual string — and the language tags that key it."""

import functools
import os
import re
import sys
from collections.abc import Callable, ItemsView, Iterator, KeysView, Mapping, Sequence, ValuesView
from pathlib import Path
from typing import ClassVar, Self, overload, override

import msgspec

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


class InvalidTagError(ValueError):
    """A language tag that is not well-formed (RFC 5646).

    Raised by `normalize_tag`, so by every `lang` / `langs` parameter and by
    `LocalisedString`. A `ValueError` only: as a `KeyError` it would be read as
    an absent key by code that catches `KeyError`, such as `Mapping.get`.

    Attributes:
        lang: The refused tag, cut to its first 64 characters.
    """

    def __init__(self, lang: str) -> None:
        """Name the refused tag, and its hyphen form when it is well-formed."""
        self.lang = lang[:_QUOTED]
        msg = f"{self.lang!r} is not a well-formed language tag (RFC 5646), such as 'en' or 'fr-ca'"
        hyphenated = lang.replace("_", "-")
        if hyphenated != lang and _well_formed(hyphenated) is not None:
            msg = f"{msg}; did you mean {hyphenated.lower()!r}?"
        super().__init__(msg)


def _well_formed(tag: str) -> str | None:
    """`tag` lowercased if it is a well-formed tag, else None."""
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
    return None


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
        InvalidTagError: If `tag` is not a well-formed language tag, or is
            longer than `MAX_TAG_LENGTH`. The message quotes at most 64
            characters of it, and names the hyphen form of a tag written with
            `_`.

    Example:
        normalize_tag("fr-CA")  # "fr-ca"
        normalize_tag("fr_CA")  # InvalidTagError: ... did you mean 'fr-ca'?
    """
    if not isinstance(tag, str):
        msg = f"a language tag is a str, not {type(tag).__name__}"
        raise TypeError(msg)
    lowered = _well_formed(tag)
    if lowered is None:
        raise InvalidTagError(tag)
    return lowered


class MissingTagError(KeyError):
    """No text is held for any of the tags asked for.

    A `KeyError`, raised by `InternationalString.__getitem__` for a
    well-formed tag it does not hold.

    Attributes:
        langs: The tags asked for, normalised.
        held: The tags the string holds.
    """

    def __init__(self, langs: Sequence[str], held: Sequence[str], /) -> None:
        """Record what was asked for and what is held."""
        self.langs = tuple(langs)
        self.held = tuple(held)
        super().__init__(self.langs, self.held)

    @override
    def __str__(self) -> str:
        # `KeyError.__str__` quotes its argument; the message reads plainly.
        asked = ", ".join(map(repr, self.langs))
        held = ", ".join(map(repr, self.held)) or "nothing"
        return f"no text for {asked}; held: {held}"


def tag_alias(lang: str) -> str:
    """`lang` as it is written inside a generated name: normalised, with ``-`` as ``_``.

    For every name that joins a base name and a tag, such as the column
    ``name_fr_ca``. A tag never contains ``_`` or ``--``, so the alias can be
    read back given the base name, and never contains ``__``, which separates
    a suffix that is not a tag (``title__lang``).

    Raises:
        TypeError: If `lang` is not a `str`.
        InvalidTagError: If `lang` is not a well-formed tag.

    Example:
        tag_alias("fr-CA")  # "fr_ca"
    """
    return normalize_tag(lang).replace("-", "_")


#: Tags `LocalisedString` has already checked, in normalised form. Cleared
#: when it holds `_SEEN_TAGS_MAX`, so an input with many distinct tags costs a
#: full check per tag while common ones return to the fast path. `in` and
#: `add` on a set are atomic, so no lock is needed.
_SEEN_TAGS: set[str] = set()
_SEEN_TAGS_MAX = 1024


class LocalisedString(msgspec.Struct, frozen=True, gc=False):
    """One text and its language tag: the SDMX Information Model's LocalisedString.

    `text` is the IM's `label`, and `lang` its `locale`. What
    `InternationalString.pick` returns, so a caller asking for
    ``("fr-ca", "fr", "en")`` learns which tag answered. Compares and hashes
    by both fields. The query expressions `Localised` and `SideLocalised` are
    unrelated: they select a localised field in the path language.

    However it is built (directly, by `msgspec.json.decode`, `msgspec.convert`
    or `msgspec.structs.replace`), `lang` holds the tag as `normalize_tag`
    returns it, so ``LocalisedString("Annuel", "FR") == LocalisedString("Annuel",
    "fr")``. It encodes as ``{"text": "Annuel", "lang": "fr"}``, keys in that
    order: the JSON form the StatCat Suite shares.

    It defines no `__str__`, `__format__` or `__bool__`: read the text as
    `.text`. `str(v)` gives the repr, and every value is truthy, even with
    empty text.

    Attributes:
        text: The text (IM: `label`).
        lang: Its language tag (IM: `locale`), lowercase and well-formed.
    """

    text: str
    lang: str

    def __post_init__(self) -> None:
        """Check `text` is a `str`, and normalise `lang`.

        Raises:
            TypeError: If `text` or `lang` is not a `str`.
            InvalidTagError: If `lang` is not a well-formed tag.
        """
        # msgspec checks no types on direct construction, and `gc=False` is
        # safe only while both fields are `str`.
        if type(self.text) is not str:
            msg = f"LocalisedString text is a str, not {type(self.text).__name__}"
            raise TypeError(msg)
        if self.lang in _SEEN_TAGS:
            return
        lang = normalize_tag(self.lang)
        if lang != self.lang:
            msgspec.structs.force_setattr(self, "lang", lang)
        if len(_SEEN_TAGS) >= _SEEN_TAGS_MAX:
            _SEEN_TAGS.clear()
        _SEEN_TAGS.add(lang)


def _normalized(pairs: Mapping[str, str]) -> dict[str, str]:
    """`pairs` keyed by normalised, interned tags.

    Raises:
        TypeError: If a text is not a `str`.
        ValueError: If a key is not a well-formed tag, or two keys normalise
            to one tag: two texts for one language, which sdmxlib never
            chooses between.
    """
    data: dict[str, str] = {}
    for tag, text in pairs.items():
        lang = normalize_tag(tag)
        if not isinstance(text, str):
            msg = f"the text for {lang!r} is a str, not {type(text).__name__}"
            raise TypeError(msg)
        if lang in data:
            msg = f"two texts for language {lang!r}"
            raise ValueError(msg)
        # Interned (#116): a small pool of tags repeats across thousands of
        # strings built during a parse, and one shared `str` per tag saved
        # 200-400 KiB on large XML messages.
        data[sys.intern(lang)] = text
    return data


class InternationalString(Mapping[str, LocalisedString]):
    """A multilingual string: a mapping from language tag to `LocalisedString`.

    Immutable after construction. Every tag is normalised by `normalize_tag`
    as it enters, so keys are lowercase and well-formed, and every lookup
    normalises the tag it is asked for: ``s["FR-CA"]`` finds the text stored
    under ``fr-ca``. The texts are held as `str`; a `LocalisedString` is built
    when a lookup returns one. `as_dict` gives the texts themselves.

    A malformed tag raises `InvalidTagError` from every lookup, `in`
    included, and a tag that is not a `str` raises `TypeError`, as an
    unhashable key does on a `dict`. A well-formed tag that is not held
    raises `MissingTagError` from ``s[tag]``, gives None (or the default)
    from `get` and `pick`, and is not `in` the string.

    Example:
        s = InternationalString(en="Annual", fr="Annuel")
        s["en"].text  # "Annual"
        s.get("de")  # None
        s.pick("de", "en")  # LocalisedString(text='Annual', lang='en')
        "fr" in s  # True
        s.as_dict()  # {"en": "Annual", "fr": "Annuel"}
    """

    __slots__ = ("_data",)

    #: The empty string, shared. Assigned below the class body, since it is an
    #: instance of the class being defined.
    #:
    #: Sharing one is safe because this class is genuinely immutable — no
    #: `__setitem__`, no mutating method, `_data` reachable only through
    #: `__slots__`. The two ways out of that are `_backing()` and
    #: `_emit_pair()`, private to sdmxlib, which hand back the backing map and
    #: treat it as read-only; a caller that mutated either would corrupt every
    #: empty istring in the process rather than just its own.
    #: `tests/unit/test_shared_empty_values.py` holds that contract.
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
    def of(cls, pairs: InternationalString | Mapping[str, str]) -> InternationalString:
        """Build from an `InternationalString`, which is copied, or a mapping from tag to text.

        Returns the shared `EMPTY` for an empty mapping, the common case on a
        real bundle, so it allocates nothing. Only for this
        exact class: a subclass gets its own fresh instance, since `EMPTY` is
        an `InternationalString` and returning it would be a type error the
        annotation cannot see.

        Raises:
            TypeError: If a text is not a `str`, such as a `LocalisedString`.
            ValueError: If a tag is not well-formed, or two normalise to one.
        """
        data = dict(pairs._data) if isinstance(pairs, InternationalString) else _normalized(pairs)
        if not data and cls is InternationalString:
            return EMPTY
        obj: Self = cls.__new__(cls)
        obj._data = data
        return obj

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

    # ── Lookup ────────────────────────────────────────────────────────────

    @override
    def __getitem__(self, lang: str, /) -> LocalisedString:
        """The text held for `lang`, with its tag.

        Raises:
            TypeError: If `lang` is not a `str`.
            InvalidTagError: If `lang` is not a well-formed tag.
            MissingTagError: If no text is held for `lang`.
        """
        key = normalize_tag(lang)
        text = self._data.get(key)
        if text is None:
            raise MissingTagError((key,), tuple(self._data))
        return LocalisedString(text, key)

    @overload
    def get(self, lang: str, /) -> LocalisedString | None: ...
    @overload
    def get(self, lang: str, default: LocalisedString, /) -> LocalisedString: ...
    @overload
    def get[T](self, lang: str, default: T, /) -> LocalisedString | T: ...
    @override
    def get[T](self, lang: str, default: T | None = None, /) -> LocalisedString | T | None:
        """The text held for `lang`, with its tag, or `default`.

        Raises:
            TypeError: If `lang` is not a `str`.
            InvalidTagError: If `lang` is not a well-formed tag.
        """
        key = normalize_tag(lang)
        text = self._data.get(key)
        return default if text is None else LocalisedString(text, key)

    def pick(self, *langs: str) -> LocalisedString | None:
        """The first of `langs` held, with the tag that answered, or None.

        Each tag is normalised, then matched exactly: asking for
        ``("fr-CA", "fr")`` returns the ``fr`` text with ``lang="fr"`` when
        only that is held. Every tag is checked before any is looked up, so
        a malformed one raises even after a held one.

        Raises:
            TypeError: If no tag is given, or one is not a `str`.
            InvalidTagError: If one of `langs` is not a well-formed tag.
        """
        if not langs:
            msg = "pick() needs at least one language tag"
            raise TypeError(msg)
        keys = [normalize_tag(lang) for lang in langs]
        for key in keys:
            text = self._data.get(key)
            if text is not None:
                return LocalisedString(text, key)
        return None

    def _text(self, lang: str) -> str | None:
        """The text held under `lang`, a tag already normalised; not checked.

        For loops that normalise one tag once and then look it up in each of
        many strings, such as `Codelist.code_map`.
        """
        return self._data.get(lang)

    @override
    def __contains__(self, lang: object, /) -> bool:
        """Whether a text is held for `lang`.

        Raises:
            TypeError: If `lang` is not a `str`.
            InvalidTagError: If `lang` is not a well-formed tag.
        """
        if not isinstance(lang, str):
            msg = f"a language tag is a str, not {type(lang).__name__}"
            raise TypeError(msg)
        return normalize_tag(lang) in self._data

    @override
    def __iter__(self) -> Iterator[str]:
        """The held tags, in insertion order."""
        return iter(self._data)

    @override
    def __len__(self) -> int:
        return len(self._data)

    def __bool__(self) -> bool:
        return bool(self._data)

    @property
    def langs(self) -> list[str]:
        """The held tags, in insertion order."""
        return list(self._data)

    # ── Views ─────────────────────────────────────────────────────────────

    @override
    def keys(self) -> KeysView[str]:
        """The held tags; ``tag in s.keys()`` checks `tag` as ``tag in s`` does."""
        return KeysView(self)

    @override
    def values(self) -> ValuesView[LocalisedString]:
        """A `LocalisedString` per held tag."""
        return _Values(self)

    @override
    def items(self) -> ItemsView[str, LocalisedString]:
        """``(tag, LocalisedString)`` per held tag."""
        return _Items(self)

    def as_dict(self) -> dict[str, str]:
        """A new ``dict`` from tag to text, for serialisers and columnar output."""
        return dict(self._data)

    def _backing(self) -> dict[str, str]:
        """The backing map itself, uncopied: read-only, for sdmxlib's serialise hot path."""
        return self._data

    def _emit_pair(self) -> tuple[str | None, dict[str, str] | None]:
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

    @override
    def __eq__(self, other: object) -> bool:
        """Equal to an `InternationalString` holding the same texts under the same tags; never to a ``dict``."""
        if isinstance(other, InternationalString):
            return self._data == other._data
        return NotImplemented

    @override
    def __hash__(self) -> int:
        return hash(tuple(sorted(self._data.items())))

    @override
    def __repr__(self) -> str:
        return f"InternationalString.of({self._data!r})"


class _Values(ValuesView[LocalisedString]):
    """`InternationalString.values`: builds each value from the stored map, without normalising a held tag again."""

    __slots__ = ("_data",)

    def __init__(self, mapping: InternationalString) -> None:
        super().__init__(mapping)
        self._data = mapping._data

    @override
    def __iter__(self) -> Iterator[LocalisedString]:
        for lang, text in self._data.items():
            yield LocalisedString(text, lang)

    @override
    def __contains__(self, value: object) -> bool:
        if not isinstance(value, LocalisedString):
            return False
        return self._data.get(value.lang) == value.text


class _Items(ItemsView[str, LocalisedString]):
    """`InternationalString.items`: as `_Values`; membership checks its tag as ``tag in s`` does."""

    __slots__ = ("_data", "_istring")

    def __init__(self, mapping: InternationalString) -> None:
        super().__init__(mapping)
        self._data = mapping._data
        self._istring = mapping

    @override
    def __iter__(self) -> Iterator[tuple[str, LocalisedString]]:
        for lang, text in self._data.items():
            yield lang, LocalisedString(text, lang)

    @override
    def __contains__(self, item: object) -> bool:
        match item:
            case (lang, value):
                pass
            case _:
                return False
        if not isinstance(lang, str):
            msg = f"a language tag is a str, not {type(lang).__name__}"
            raise TypeError(msg)
        found = self._istring.get(lang)
        return found is not None and found == value


#: See `InternationalString.EMPTY`. Bound after the class body because it is an
#: instance of it; the class attribute is the public spelling.
EMPTY = InternationalString()
InternationalString.EMPTY = EMPTY


def to_istring(v: InternationalString | Mapping[str, str]) -> InternationalString:
    """Attrs converter: an `InternationalString` as it is, or one built from a mapping from tag to text."""
    return v if isinstance(v, InternationalString) else InternationalString.of(v)


def _audited[**P, R](method: Callable[P, R], name: str, out: str) -> Callable[P, R]:
    """`method`, writing each new ``file:line`` that calls it to `out` before it runs."""
    seen: set[str] = set()

    @functools.wraps(method)
    def audited(*args: P.args, **kwargs: P.kwargs) -> R:
        caller = sys._getframe(1)
        site = f"{caller.f_code.co_filename}:{caller.f_lineno}\t{name}"
        if site not in seen:
            seen.add(site)
            with Path(out).open("a", encoding="utf-8") as f:
                f.write(site + "\n")
        return method(*args, **kwargs)

    return audited


def _install_audit() -> None:
    """The migration audit: with ``SDMXLIB_ISTRING_AUDIT`` set, log each site reading an `InternationalString`.

    Read once, at import. Every call of ``[]``, `get`, `pick`, `values` and
    `items` appends its caller's ``file:line`` and the method to the file
    ``SDMXLIB_ISTRING_AUDIT_OUT`` names, once per site and process, and runs
    as before. A test run under it lists the call sites a change to what
    these return reaches (GH-309).

    Raises:
        RuntimeError: If ``SDMXLIB_ISTRING_AUDIT`` is set and
            ``SDMXLIB_ISTRING_AUDIT_OUT`` is not.
    """
    if not os.environ.get("SDMXLIB_ISTRING_AUDIT"):
        return
    out = os.environ.get("SDMXLIB_ISTRING_AUDIT_OUT")
    if not out:
        msg = "SDMXLIB_ISTRING_AUDIT is set: name the file to write in SDMXLIB_ISTRING_AUDIT_OUT"
        raise RuntimeError(msg)
    for name in ("__getitem__", "get", "pick", "values", "items"):
        setattr(InternationalString, name, _audited(getattr(InternationalString, name), name, out))


_install_audit()
