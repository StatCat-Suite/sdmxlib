"""``python -m bench.pysdmx_oracle record`` — see `record.py` and `README.md`."""

import argparse

from bench.pysdmx_oracle.record import record


def main() -> None:
    """Parse the command line and record."""
    parser = argparse.ArgumentParser(prog="python -m bench.pysdmx_oracle")
    parser.add_argument("command", choices=["record"])
    parser.parse_args()
    print(f"recorded into {record()}")


if __name__ == "__main__":
    main()
