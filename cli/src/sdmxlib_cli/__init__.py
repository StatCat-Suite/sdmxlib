"""``sdmxlib``: the command line over the library's bundle tools (GH-67).

A separate distribution, ``sdmxlib-cli``, so ``sdmxlib`` itself never depends on
typer. Each command is a thin wrapper: the work is the library's, and is
tested there.
"""

from pathlib import Path
from typing import Annotated

import typer

from sdmxlib.bundle import check_bundle

app = typer.Typer(help="sdmxlib from the command line.", no_args_is_help=True)
bundle = typer.Typer(help="Published SDMX bundles.", no_args_is_help=True)
app.add_typer(bundle, name="bundle")


@bundle.command("check")
def check(
    path: Annotated[Path, typer.Argument(help="The bundle directory.")],
    *,
    references: Annotated[
        bool,
        typer.Option(
            "--references/--no-references",
            help="Check that every reference resolves within the bundle (loads every artefact; the slow part).",
        ),
    ] = True,
) -> None:
    """Check a published bundle against its manifest and against itself.

    Prints each finding and what was checked. Exits 1 if anything was found.

    Raises:
        Exit: With status 1 when the bundle has findings, 0 otherwise.
    """
    report = check_bundle(path, references=references)
    for finding in report.findings:
        typer.echo(str(finding))
    found = len(report.findings)
    typer.echo(f"{path}: {found} finding{'' if found == 1 else 's'}; checked {', '.join(report.checked)}")
    raise typer.Exit(code=0 if report.ok else 1)
