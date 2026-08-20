"""Minimal Retrace command-line entry point."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from retrace import __version__


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and return a process exit code."""
    parser = argparse.ArgumentParser(prog="retrace-logs")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
