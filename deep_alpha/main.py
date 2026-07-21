from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
from pathlib import Path

from deep_alpha import __version__
from deep_alpha.config import DEFAULT_ASSET, DEFAULT_FIELDS, DEFAULT_REPO, provider_uri
from deep_alpha.errors import ArgumentError, DeepAlphaError, ProviderError
from deep_alpha.github_release import GitHubReleaseClient
from deep_alpha.installer import install_archive, read_metadata, validate_provider
from deep_alpha.output import emit, render_frame
from deep_alpha.services import KlineQuery, MarketDataService, parse_fields, read_calendar, read_fields, read_symbols, validate_date


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def add_location(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider-uri", default=argparse.SUPPRESS, help="Qlib provider directory")
    parser.add_argument("--region", default=argparse.SUPPRESS, choices=["cn"])


def add_release_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--tag")
    parser.add_argument("--asset", default=DEFAULT_ASSET)
    parser.add_argument("--target-dir")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--keep-archive", action="store_true")
    parser.add_argument("--timeout", type=positive_int, default=300)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deep-alpha", description="Download, update, and query offline Qlib market data.")
    parser.add_argument("--version", action="version", version=f"deep-alpha {__version__}")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--provider-uri", default=None, help="Qlib provider directory")
    parser.add_argument("--region", default="cn", choices=["cn"])
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser("download", help="Download offline Qlib data")
    add_location(download)
    add_release_options(download)

    update = subparsers.add_parser("update", help="Update local offline Qlib data")
    add_location(update)
    add_release_options(update)

    info = subparsers.add_parser("info", help="Show local dataset information")
    add_location(info)

    kline = subparsers.add_parser("kline", help="Query historical K-line data")
    add_location(kline)
    kline.add_argument("--symbol", required=True)
    kline.add_argument("--start")
    kline.add_argument("--end")
    kline.add_argument("--fields", default=",".join(DEFAULT_FIELDS))
    kline.add_argument("--format", choices=["csv", "json", "table"], default="csv")
    kline.add_argument("--output")
    kline.add_argument("--adjust", choices=["none", "qfq", "hfq"], default="none")

    symbols = subparsers.add_parser("symbols", help="List or search local symbols")
    add_location(symbols)
    symbols.add_argument("--prefix")
    symbols.add_argument("--contains")

    calendar = subparsers.add_parser("calendar", help="Show trading calendar information")
    add_location(calendar)
    calendar.add_argument("--start")
    calendar.add_argument("--end")
    return parser


def command_target(args: argparse.Namespace) -> Path:
    return provider_uri(args.target_dir or args.provider_uri)


def download_release(args: argparse.Namespace, asset=None) -> str:
    target = command_target(args)
    client = GitHubReleaseClient(args.repo, args.timeout)
    asset = asset or client.get_asset(args.tag, args.asset)
    archive_dir = Path(tempfile.mkdtemp(prefix="deep-alpha-"))
    archive = archive_dir / asset.name
    try:
        client.download(asset, archive)
        install_archive(archive, target, args.repo, asset, args.force)
        if args.keep_archive:
            kept = target.parent / f"{asset.release_tag}-{asset.name}"
            shutil.copy2(archive, kept)
    finally:
        shutil.rmtree(archive_dir, ignore_errors=True)
    return f"Installed {asset.release_tag} to {target}"


def run(args: argparse.Namespace) -> str | None:
    if args.command == "download":
        return download_release(args)
    if args.command == "update":
        target = command_target(args)
        client = GitHubReleaseClient(args.repo, args.timeout)
        asset = client.get_asset(args.tag, args.asset)
        if target.exists() and not args.force:
            meta = read_metadata(target)
            if meta.get("repo") == args.repo and meta.get("asset_name") == args.asset and meta.get("release_tag") == asset.release_tag:
                return f"Already up to date: {asset.release_tag}"
        args.force = target.exists() or args.force
        return download_release(args, asset)

    provider = provider_uri(args.provider_uri)
    validate_provider(provider)
    if args.command == "info":
        metadata = read_metadata(provider, required=False)
        calendars = read_calendar(provider)
        symbols = read_symbols(provider)
        values = {
            "provider_uri": provider,
            "release_tag": metadata.get("release_tag", "unknown"),
            "asset_name": metadata.get("asset_name", "unknown"),
            "installed_at": metadata.get("installed_at", "unknown"),
            "calendar_start": calendars[0],
            "calendar_end": calendars[-1],
            "instrument_count": len(symbols),
            "fields": ", ".join(read_fields(provider)),
        }
        return "\n".join(f"{key}: {value}" for key, value in values.items())
    if args.command == "symbols":
        symbols = read_symbols(provider)
        if args.prefix:
            symbols = [item for item in symbols if item.startswith(args.prefix.upper())]
        if args.contains:
            symbols = [item for item in symbols if args.contains.upper() in item]
        return "\n".join(symbols)
    if args.command == "calendar":
        start, end = validate_date(args.start), validate_date(args.end)
        if start and end and start > end:
            raise ArgumentError("Start date must not be after end date")
        dates = [item for item in read_calendar(provider) if (not start or item >= start) and (not end or item <= end)]
        return "\n".join(dates)
    if args.command == "kline":
        if args.adjust != "none":
            raise ArgumentError("Only --adjust none is currently supported")
        fields = parse_fields(args.fields)
        service = MarketDataService(provider, args.region)
        frame = service.get_kline(KlineQuery(args.symbol, args.start, args.end, fields))
        emit(render_frame(frame, args.format), args.output)
        return None
    raise ArgumentError(f"Unknown command: {args.command}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    try:
        message = run(args)
        if message:
            print(message)
    except DeepAlphaError as exc:
        if args.verbose:
            logging.exception("Command failed")
        else:
            print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(exc.exit_code) from exc
    except BrokenPipeError:
        raise SystemExit(0)


if __name__ == "__main__":
    main()
