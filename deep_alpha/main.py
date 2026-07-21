from __future__ import annotations

import argparse

from deep_alpha import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deep-alpha",
        description="Download, update, and query offline Qlib market data.",
    )
    parser.add_argument("--version", action="version", version=f"deep-alpha {__version__}")

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("download", help="Download offline Qlib data")
    subparsers.add_parser("update", help="Update local offline Qlib data")
    subparsers.add_parser("info", help="Show local dataset information")
    subparsers.add_parser("kline", help="Query historical K-line data")
    subparsers.add_parser("symbols", help="List or search local symbols")
    subparsers.add_parser("calendar", help="Show trading calendar information")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return
    raise SystemExit(f"Command not implemented yet: {args.command}")


if __name__ == "__main__":
    main()
