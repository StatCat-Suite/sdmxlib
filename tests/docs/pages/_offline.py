"""Serve a documentation page's REST calls from local artefacts.

The provider-facing pages construct their own registry — `with
sl.RestRegistry(sl.Provider.ECB) as reg:` is the first line of most of them —
so a `reg` supplied in the namespace is shadowed and the block goes to the
network anyway.

Patching the *client* instead leaves the page exactly as written and takes the
network out from under it. What a reader sees is what runs; only the socket is
different.

Deliberately not VCR cassettes. A cassette would tie a page to one recorded
provider response, so editing an example to fetch a different codelist would
fail with a cassette miss rather than a wrong answer — and re-recording needs
a network the gate does not have. These fixtures are small, local, and say in
one place what every page's examples assume exists.
"""

from __future__ import annotations

from collections.abc import Generator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from os import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol, TypeIs
from unittest.mock import patch

import attrs

from sdmxlib.api.client import SdmxClient
from sdmxlib.model.message import StructureMessage
from sdmxlib.model.ref import SupportsUrn


class _Stampable(SupportsUrn, attrs.AttrsInstance, Protocol):
    """An artefact `attrs.evolve` can re-stamp — every maintainable the pages use."""


def _is_stampable(artefact: SupportsUrn) -> TypeIs[_Stampable]:
    return attrs.has(type(artefact))


def _restamped(artefact: SupportsUrn, stamp: Mapping[str, object]) -> SupportsUrn:
    """`artefact` carrying the identity in `stamp`."""
    if not _is_stampable(artefact):
        msg = f"cannot re-stamp {type(artefact).__name__}: not an attrs class"
        raise TypeError(msg)
    return _evolved(artefact, stamp)


def _evolved[T: attrs.AttrsInstance](artefact: T, stamp: Mapping[str, object]) -> T:
    return attrs.evolve(artefact, **stamp)


def _message(artefacts: Sequence[SupportsUrn]) -> StructureMessage:
    """Sort `artefacts` into the message collection each belongs to.

    By `msg_field`, which every maintainable declares — so a new artefact type
    needs nothing here. Built with constructor keywords rather than by
    appending, because a message's `ArtefactList`s are immutable.
    """
    grouped: dict[str, list[SupportsUrn]] = {}
    for artefact in artefacts:
        field = getattr(type(artefact), "msg_field", None)
        if field is None:  # pragma: no cover - every maintainable has one
            continue
        grouped.setdefault(field, []).append(artefact)
    # One `Any` local, on the line the dispatch actually happens (#347). The
    # slot name comes from `msg_field` at runtime, so no static type describes
    # this splat: every keyword is checked against its own element type and
    # `list[SupportsUrn]` satisfies none of the 23. CLAUDE.md's rule for the
    # genuinely dynamic step — let the `Any` die here rather than widening
    # `_message`'s signature or annotating the buckets away.
    by_field: dict[str, Any] = grouped
    return StructureMessage(**by_field)


def _structural_stubs(artefacts: Sequence[SupportsUrn], *, answer_to_any_name: bool):
    """The `/structure` half of the stub."""

    def _get_artefact(self, resource, agency, id, version="latest", **kwargs):
        if not answer_to_any_name:
            return _message(artefacts)
        # Re-stamp the fixture with the identity that was asked for. The pages
        # name real providers' artefacts — BIS:WS_CBPOL, ESTAT:NAMAIN_10_GDP —
        # and the identity is not what they demonstrate; the shape is. Without
        # this each page needs its own fixture differing in two strings, or the
        # examples get rewritten to fetch `AG:DSD_X`, which no reader wants.
        wanted = resource.lower()
        # The version too, when one was named: pages compare `version="1.0"`
        # against `version="2.0"` to show what changed between them, and an
        # artefact stamped `1.0` cannot answer for `2.0`. `latest` is left
        # alone — it is a pointer, not a version (#338).
        stamp: dict[str, object] = {"maintainer": agency, "id": id}
        if version not in {"latest", "~"}:
            stamp["version"] = version
        # The first of the wanted type only: re-stamping every one put two
        # artefacts with one URN in a message, which no provider sends, and a
        # registry keeps the last of them, not the one the page shows (#492).
        first = next((a for a in artefacts if type(a).__name__.lower() == wanted), None)
        return _message([_restamped(artefact, stamp) if artefact is first else artefact for artefact in artefacts])

    def _get_all(self, resource, agency=None, **kwargs):
        return _message(artefacts)

    return {"get_artefact_if_changed": _get_artefact, "get_artefact": _get_artefact, "get_all": _get_all}


def _for_flow(data: bytes | Mapping[str, bytes] | None, flow_id: str) -> bytes | None:
    """The observation bytes this dataflow should answer with.

    A page that queries two providers needs two payloads: `data.md` documents
    ECB's `ds.dimensions` and OECD's in the same file, and one CSV cannot have
    both shapes. Passing a mapping keyed by dataflow id is how a setup says so;
    passing bytes keeps the single-payload case unchanged.
    """
    if data is None or isinstance(data, bytes):
        return data
    return data.get(flow_id)


def _flow_id_in(path: str) -> str:
    """The dataflow id inside a `/data/...` request path.

    The REST seam builds its own URL, so this is the only place its shape is
    read. `AGENCY,ID,VERSION` and `AGENCY/ID/VERSION` both appear across API
    versions; the id is the middle field of whichever separator is used.
    """
    for segment in reversed(path.split("/")):
        if "," in segment:
            parts = segment.split(",")
            if len(parts) >= 2:
                return parts[1]
    parts = [s for s in path.split("/") if s]
    return parts[-2] if len(parts) >= 2 else ""


def _data_stubs(data: bytes | Mapping[str, bytes] | None, keys: list[str]):
    """The `/data` half.

    Three methods, not one: the query language's `_rest` seam builds its own
    URL and streams it to a file or reads a body, and `download` takes parts.
    A page reaching an unpatched one waits out a real network timeout, which
    reads as a hang rather than as a gap in this stub — which is how each was
    found.
    """

    def _stream_data_at(self, path, params, *, to):
        keys.append(str(params.get("key", "")))
        to.write_bytes(_for_flow(data, _flow_id_in(path)) or b"")

    def _get_data_at(self, path, params):
        keys.append(str(params.get("key", "")))
        return _for_flow(data, _flow_id_in(path)) or b""

    def _download_data(self, agency, id, version="latest", *, fmt, to=None, **kwargs):
        # `download` is the unparsed path, so the bytes need not match `fmt` —
        # what the pages demonstrate is the destination and the refusal rules,
        # both decided before the body is read.
        keys.append(kwargs.get("key", ""))
        body = _for_flow(data, id) or b""
        if to is None:
            return body
        to.write_bytes(body)
        return None

    return {
        "stream_data_at": _stream_data_at,
        "get_data_at": _get_data_at,
        "download_data": _download_data,
    }


@contextmanager
def serving(
    artefacts: Sequence[SupportsUrn],
    *,
    data: bytes | Mapping[str, bytes] | None = None,
    answer_to_any_name: bool = False,
) -> Generator[list[str]]:
    """Answer every structural request with `artefacts`, and data with `data`.

    Every request gets the whole set rather than the slice it asked for. A
    documentation page is checked for *running*, not for the server's
    filtering — and the alternative, a stub that implements SDMX-REST's
    selection rules, would be a second implementation of the thing under test.

    Yields the list of keys requested, so a setup can assert on them if it
    wants to.
    """
    keys: list[str] = []
    stubs = {
        **_structural_stubs(artefacts, answer_to_any_name=answer_to_any_name),
        **_data_stubs(data, keys),
    }
    with ExitStack() as patches:
        for name, stub in stubs.items():
            patches.enter_context(patch.object(SdmxClient, name, stub))
        yield keys


@contextmanager
def in_a_directory_containing(files: dict[str, bytes]) -> Generator[Path]:
    """Run with `files` on disk, in a temporary working directory.

    Pages open files by bare name — `sl.read("dsd_cpi.xml")` — because that is
    what a reader will type. Rewriting those to a fixture path would make the
    page worse to read in order to make it testable, which is the wrong trade;
    supplying the file instead leaves the example alone.
    """
    previous = Path.cwd()
    with TemporaryDirectory(prefix="sdmxlib-docs-") as directory:
        root = Path(directory)
        for name, content in files.items():
            (root / name).write_bytes(content)
        chdir(root)
        try:
            yield root
        finally:
            chdir(previous)
