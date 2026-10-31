"""``python -m bench.fmr record`` — see `record.py` and `README.md`."""

from typing import Annotated

import typer

from bench.fmr.record import record

app = typer.Typer()


@app.callback()
def main() -> None:
    """Record FMR's readings of our documents."""


@app.command("record")
def record_command(
    *,
    url: Annotated[str, typer.Option(envvar="FMR_URL", help="The FMR server.")] = "http://127.0.0.1:8080",
    user: Annotated[str, typer.Option(envvar="FMR_USER", help="The FMR user.")] = "root",
    password: Annotated[
        str | None, typer.Option(envvar="FMR_PASSWORD", help="The FMR user's password; the local image's when omitted.")
    ] = None,
) -> None:
    """Have FMR read each document and record what it returns."""
    # The local image's admin login is root / password (README.md).
    print(f"recorded into {record(url, (user, password or 'password'))}")


if __name__ == "__main__":
    app()
