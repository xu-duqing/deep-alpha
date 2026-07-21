from __future__ import annotations

import argparse
import calendar as calendar_module
import logging
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

from deep_alpha import __version__
from deep_alpha.config import DEFAULT_ASSET, DEFAULT_FIELDS, DEFAULT_REPO, provider_uri
from deep_alpha.errors import ArgumentError, DeepAlphaError, ProviderError
from deep_alpha.github_release import GitHubReleaseClient
from deep_alpha.installer import install_archive, read_metadata, validate_provider
from deep_alpha.output import emit, render_frame
from deep_alpha.progress import DownloadProgress
from deep_alpha.services import KlineQuery, MarketDataService, parse_fields, read_calendar, read_fields, read_symbols, read_universes, validate_date

LOGGER = logging.getLogger("deep_alpha")


def default_start_date(today: date | None = None) -> str:
    current = today or date.today()
    year = current.year if current.month > 1 else current.year - 1
    month = current.month - 1 if current.month > 1 else 12
    day = min(current.day, calendar_module.monthrange(year, month)[1])
    return date(year, month, day).isoformat()


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
    kline.add_argument(
        "--start",
        default=default_start_date(),
        help="Start date (YYYY-MM-DD; default: one month ago)",
    )
    kline.add_argument("--end")
    kline.add_argument("--fields", default=",".join(DEFAULT_FIELDS))
    kline.add_argument("--format", choices=["csv", "json", "table"], default="json")
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
    if target.exists() and not args.force:
        raise ProviderError(
            f"Target directory already exists: {target}. "
            "Run 'deep-alpha update' to update it, or use --force to replace it."
        )
    client = GitHubReleaseClient(args.repo, args.timeout)
    LOGGER.info("Fetching release metadata from %s", args.repo)
    asset = asset or client.get_asset(args.tag, args.asset)
    LOGGER.info("Release %s: %s (%s bytes)", asset.release_tag, asset.name, asset.size)
    archive_dir = Path(tempfile.mkdtemp(prefix="deep-alpha-"))
    archive = archive_dir / asset.name
    try:
        LOGGER.info("Downloading asset")
        progress = DownloadProgress(asset.size)
        client.download(asset, archive, progress.update)
        LOGGER.info("Validating and installing data into %s", target)
        install_archive(archive, target, args.repo, asset, args.force)
        if args.keep_archive:
            kept = target.parent / f"{asset.release_tag}-{asset.name}"
            shutil.copy2(archive, kept)
            LOGGER.info("Saved archive to %s", kept)
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
        universes = read_universes(provider)
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
        lines = [f"{key}: {value}" for key, value in values.items()]
        lines.append("datasets:")
        for universe in universes:
            period = (
                f"{universe.start} to {universe.end}"
                if universe.start and universe.end
                else f"{calendars[0]} to {calendars[-1]}"
            )
            lines.append(
                f"  {universe.name}: {universe.instrument_count} instruments, {period}"
            )
        return "\n".join(lines)
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
        fields = parse_fields(args.fields)
        service = MarketDataService(provider, args.region)
        frame = service.get_kline(
            KlineQuery(args.symbol, args.start, args.end, fields, adjust=args.adjust)
        )
        emit(render_frame(frame, args.format), args.output)
        return None
    raise ArgumentError(f"Unknown command: {args.command}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
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
