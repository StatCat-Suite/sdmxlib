"""``python -m bench.pysdmx_oracle record`` — see `record.py` and `README.md`."""

import typer

from bench.pysdmx_oracle.record import record

app = typer.Typer()


@app.callback()
def main() -> None:
    """Record pysdmx's readings of our documents."""


@app.command("record")
def record_command() -> None:
    """Have pysdmx read each document and record what it returns."""
    print(f"recorded into {record()}")


if __name__ == "__main__":
    app()
