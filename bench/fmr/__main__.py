"""``python -m bench.fmr record`` — see `record.py` and `README.md`."""

import argparse
import os

from bench.fmr.record import record


def main() -> None:
    """Parse the command line and record."""
    parser = argparse.ArgumentParser(prog="python -m bench.fmr")
    parser.add_argument("command", choices=["record"])
    parser.add_argument("--url", default=os.environ.get("FMR_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--user", default=os.environ.get("FMR_USER", "root"))
    parser.add_argument("--password", default=os.environ.get("FMR_PASSWORD", "password"))
    args = parser.parse_args()
    print(f"recorded into {record(args.url, (args.user, args.password))}")


if __name__ == "__main__":
    main()
