"""``sdmxlib``: the command line over the library's edition tools (GH-67).

A separate distribution, ``sdmxlib-cli``, so ``sdmxlib`` itself never depends on
typer. Each command is a thin wrapper: the work is the library's, and is
tested there.
"""

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

import sdmxlib as sl
from sdmxlib.api.providers import ProviderInfo
from sdmxlib.edition import (
    MANIFEST_NAME,
    Profile,
    Tier,
    edition_files,
    check_edition,
    copy_edition,
    fetch_edition,
    read_manifest,
)
from sdmxlib.model.dataflow import Dataflow
from sdmxlib.model.urn import SdmxUrn
from sdmxlib.model.version import compliance


class At(StrEnum):
    """`check --at`'s choices: the `Profile` each names."""

    PUBLISH = "publish"
    RECEIVE = "receive"
    OPEN = "open"


_PROFILES: dict[At, Profile] = {At.PUBLISH: "publish", At.RECEIVE: "receive", At.OPEN: "open"}

app = typer.Typer(help="sdmxlib from the command line.", no_args_is_help=True)
edition = typer.Typer(help="Sealed editions of SDMX products.", no_args_is_help=True)
app.add_typer(edition, name="edition")


@edition.command("check")
def check(
    path: Annotated[Path, typer.Argument(help="The edition directory.")],
    *,
    at: Annotated[
        At,
        typer.Option(
            "--at",
            help="The profile: publish (hashes everything), receive (data files by size) or open (sizes only).",
        ),
    ],
    expect: Annotated[
        Path | None,
        typer.Option(
            help="An earlier edition whose content this one must have: each fingerprint that differs is a finding.",
        ),
    ] = None,
) -> None:
    """Check a sealed edition against its manifest and against itself.

    Prints each finding and what was checked. Exits 1 if anything was found.
    ``--expect`` guards a republish that is to change no content, such as a
    rebuild at a new store schema.

    Raises:
        Exit: With status 1 when the edition has findings, 0 otherwise.
    """
    report = check_edition(path, at=_PROFILES[at], expect=expect)
    for finding in report.findings:
        typer.echo(str(finding))
    found = len(report.findings)
    typer.echo(f"{path}: {found} finding{'' if found == 1 else 's'}; checked {', '.join(report.checked)}")
    raise typer.Exit(code=0 if report.ok else 1)


def _source(text: str) -> ProviderInfo | str:
    """A `Provider` by name (``ECB``, case-insensitive), else the text as a REST endpoint URL."""
    if "://" in text:
        return text
    try:
        return sl.Provider[text.upper()]
    except (AttributeError, KeyError) as exc:
        msg = f"{text!r} is neither a known provider nor a URL"
        raise typer.BadParameter(msg) from exc


def _flow(text: str) -> SdmxUrn[Dataflow]:
    """A dataflow as ``AGENCY:ID(VERSION)``, ``AGENCY:ID`` (latest), or a full URN.

    Raises:
        typer.BadParameter: Naming the accepted forms, for text that is none
            of them: a malformed URN, agency or id, or a version that is
            neither ``latest`` nor an SDMX version.
    """
    forms = "AGENCY:ID(VERSION), AGENCY:ID or a dataflow URN"
    try:
        if text.startswith("urn:"):
            urn = Dataflow.parse_urn(text)
        else:
            agency, _, rest = text.partition(":")
            flow_id, _, version = rest.partition("(")
            if not agency or not flow_id:
                msg = f"{text!r} is not {forms}"
                raise typer.BadParameter(msg)
            version = version.removesuffix(")") or "latest"
            urn = Dataflow.urn_for(agency=agency, id=flow_id, version=version)
    except sl.SdmxParseError as exc:
        msg = f"{text!r} is not {forms}: {exc}"
        raise typer.BadParameter(msg) from exc
    if urn.version != "latest" and not compliance(urn.version):
        msg = f"{text!r} is not {forms}: {urn.version!r} is neither latest nor an SDMX version"
        raise typer.BadParameter(msg)
    return urn


@edition.command("fetch")
def fetch(
    source: Annotated[str, typer.Argument(help="A provider (ECB, ESTAT, BIS, OECD, ...) or a REST endpoint URL.")],
    flow: Annotated[str, typer.Argument(help="The dataflow: AGENCY:ID(VERSION), AGENCY:ID, or a URN.")],
    path: Annotated[Path, typer.Argument(help="The product directory; the edition is created inside it.")],
    *,
    key: Annotated[str | None, typer.Option(help="SDMX key, e.g. M.USD+GBP.EUR.SP00.A.")] = None,
    start: Annotated[str | None, typer.Option(help="Inclusive startPeriod.")] = None,
    end: Annotated[str | None, typer.Option(help="Inclusive endPeriod.")] = None,
    structures_only: Annotated[bool, typer.Option(help="Fetch the structures and no observations.")] = False,
    api: Annotated[str, typer.Option(help="REST API version of a URL source.")] = "2.1",
    source_id: Annotated[str | None, typer.Option(help="Registry id of a URL source, recorded as provenance.")] = None,
) -> None:
    """Fetch one dataflow, narrowed by key and period, into a new sealed edition.

    Prints the edition directory, its product and what it holds.
    """
    manifest = fetch_edition(
        _source(source),
        _flow(flow),
        path,
        key=key,
        start=start,
        end=end,
        structures_only=structures_only,
        api=api,
        source_id=source_id,
    )
    counts = ", ".join(f"{b.observations} observations" for b in manifest.bindings) or "structures only"
    typer.echo(f"{path / manifest.edition_id}: {manifest.product_id}, {counts}")


@edition.command("copy")
def copy(
    source: Annotated[Path, typer.Argument(help="The sealed edition directory.")],
    destination: Annotated[Path, typer.Argument(help="The edition directory to create.")],
    *,
    tiers: Annotated[
        str, typer.Option(help="Placement tiers to copy, comma-separated: required,lazy for what a server holds.")
    ] = "required,lazy,remote",
) -> None:
    """Copy an edition's listed files, verify them, and write its manifest last.

    Data files go to the shared data directory beside the destination, and one
    already there at the listed digest is kept. Prints the edition copied.

    Raises:
        BadParameter: If a tier is not required, lazy or remote.
    """
    manifest = copy_edition(source, destination, tiers=_tiers(tiers))
    typer.echo(f"{destination}: {manifest.product_id}, edition {manifest.edition_id}; {len(manifest.files)} files")


@edition.command("files")
def files(
    manifest: Annotated[Path, typer.Argument(help="An edition directory, or its manifest.json wherever it is staged.")],
    *,
    tiers: Annotated[
        str, typer.Option(help="Placement tiers to list, comma-separated: required,lazy for what a server holds.")
    ] = "required,lazy,remote",
    manifest_only: Annotated[
        bool, typer.Option("--manifest", help="Print only the manifest's path, for the copy's second step.")
    ] = False,
) -> None:
    r"""Print the files a copy of an edition needs, one path per line, for rclone or azcopy.

    Paths are under the product directory: ``<edition_id>/metadata.duckdb``,
    ``data/<schema>/<table>/ducklake-….parquet``. The manifest is left out,
    and ``--manifest`` prints its path alone: copy tools move several files at
    once, so the manifest is copied in a second step, after every other file.

        sdmxlib edition files /stage/census --tiers required,lazy \
          | rclone copy --files-from - store:products/census /var/products/census
        sdmxlib edition files /stage/census --manifest \
          | rclone copy --files-from - store:products/census /var/products/census

    Raises:
        BadParameter: If a tier is not required, lazy or remote.
    """
    root = manifest.parent if manifest.name == MANIFEST_NAME else manifest
    read = read_manifest(root)
    listed = [f"{read.edition_id}/{MANIFEST_NAME}"] if manifest_only else edition_files(read, tiers=_tiers(tiers))
    for path in listed:
        typer.echo(path)


def _tiers(text: str) -> list[Tier]:
    """The placement tiers `text` names, comma-separated.

    Raises:
        BadParameter: If one is not required, lazy or remote.
    """
    chosen: list[Tier] = []
    for part in text.split(","):
        match part.strip():
            case "required":
                chosen.append("required")
            case "lazy":
                chosen.append("lazy")
            case "remote":
                chosen.append("remote")
            case other:
                msg = f"{other!r} is not a tier; give required, lazy or remote, comma-separated"
                raise typer.BadParameter(msg)
    return chosen
