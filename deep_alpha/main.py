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
from deep_alpha.config import (
    DAILY_BASIC_ASSET,
    DAILY_BASIC_DELTA_ASSET,
    DAILY_BASIC_FIELDS,
    DEFAULT_ASSET,
    DEFAULT_FIELDS,
    DEFAULT_REPO,
    MARKET_DELTA_ASSET,
    provider_uri,
)
from deep_alpha.errors import ArgumentError, DeepAlphaError, ProviderError
from deep_alpha.feature_increment import (
    install_feature_archive,
    read_feature_metadata,
)
from deep_alpha.github_release import GitHubReleaseClient
from deep_alpha.installer import (
    install_archive,
    install_paired_archives,
    read_metadata,
    recover_paired_install,
    validate_provider,
)
from deep_alpha.temporal_delta import (
    apply_paired_archives,
    recover,
    trusted_paired_release,
    upgrade_legacy_paired_metadata,
)
from deep_alpha.output import emit, render_frame
from deep_alpha.progress import DownloadProgress
from deep_alpha.services import (
    KlineQuery,
    MarketDataService,
    parse_fields,
    parse_indicator_fields,
    read_calendar,
    read_fields,
    read_symbols,
    read_universes,
    validate_date,
)

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
    parser.add_argument(
        "--dataset",
        choices=["market", "daily-basic"],
        default=None,
        help="Legacy single-dataset download/update mode",
    )
    parser.add_argument("--asset", help="Override the selected dataset's Release asset")
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
    strategy = update.add_mutually_exclusive_group()
    strategy.add_argument("--full", action="store_true", help="Force paired full update")
    strategy.add_argument("--delta-only", action="store_true", help="Fail instead of full fallback")

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

    indicator = subparsers.add_parser(
        "indicator", help="Query historical daily indicators"
    )
    add_location(indicator)
    indicator.add_argument("--symbol", required=True)
    indicator.add_argument(
        "--start",
        default=default_start_date(),
        help="Start date (YYYY-MM-DD; default: one month ago)",
    )
    indicator.add_argument("--end")
    indicator.add_argument(
        "--fields",
        default=",".join(DAILY_BASIC_FIELDS),
        help=(
            "Comma-separated indicator fields (default: all). Available: "
            + ", ".join(DAILY_BASIC_FIELDS)
        ),
    )
    indicator.add_argument(
        "--format", choices=["csv", "json", "table"], default="json"
    )
    indicator.add_argument("--output")

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


def selected_asset(args: argparse.Namespace) -> str:
    return args.asset or (
        DAILY_BASIC_ASSET if args.dataset == "daily-basic" else DEFAULT_ASSET
    )


def select_daily_basic_tag(args: argparse.Namespace, target: Path) -> None:
    if args.dataset != "daily-basic" or args.tag is not None:
        return
    market = read_metadata(target, required=False)
    if market.get("repo") == args.repo and market.get("release_tag"):
        args.tag = market["release_tag"]


def download_release(args: argparse.Namespace, asset=None) -> str:
    target = command_target(args)
    is_daily_basic = args.dataset == "daily-basic"
    installed = {}
    if is_daily_basic:
        validate_provider(target)
        installed = read_feature_metadata(target, required=False)
        if installed and not args.force:
            raise ProviderError(
                "Daily-basic data is already installed. "
                "Run 'deep-alpha update --dataset daily-basic' to update it."
            )
        select_daily_basic_tag(args, target)
    elif target.exists() and not args.force:
        raise ProviderError(
            f"Target directory already exists: {target}. "
            "Run 'deep-alpha update' to update it, or use --force to replace it."
        )
    client = GitHubReleaseClient(args.repo, args.timeout)
    LOGGER.info("Fetching release metadata from %s", args.repo)
    asset_name = selected_asset(args)
    asset = asset or client.get_asset(args.tag, asset_name)
    LOGGER.info("Release %s: %s (%s bytes)", asset.release_tag, asset.name, asset.size)
    archive_dir = Path(tempfile.mkdtemp(prefix="deep-alpha-"))
    archive = archive_dir / asset.name
    try:
        LOGGER.info("Downloading asset")
        progress = DownloadProgress(asset.size)
        client.download(asset, archive, progress.update)
        LOGGER.info("Validating and installing data into %s", target)
        if is_daily_basic:
            install_feature_archive(
                archive,
                target,
                args.repo,
                asset,
                replace=bool(installed),
            )
        else:
            install_archive(archive, target, args.repo, asset, args.force)
        if args.keep_archive:
            kept = target.parent / f"{asset.release_tag}-{asset.name}"
            shutil.copy2(archive, kept)
            LOGGER.info("Saved archive to %s", kept)
    finally:
        shutil.rmtree(archive_dir, ignore_errors=True)
    dataset = "daily-basic data" if is_daily_basic else "market data"
    return f"Installed {dataset} {asset.release_tag} to {target}"


def _download_assets(client, assets, directory: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for name, asset in assets.items():
        path = directory / name
        client.download(asset, path, DownloadProgress(asset.size).update)
        paths[name] = path
    return paths


def _installed_paired_release(
    market_meta: dict,
    daily_meta: dict,
    repo: str,
) -> str | None:
    return trusted_paired_release(market_meta, daily_meta, repo)


def update_paired(args: argparse.Namespace) -> str:
    target = command_target(args)
    recover_paired_install(target)
    validate_provider(target)
    recover(target)
    client = GitHubReleaseClient(args.repo, args.timeout)
    names = (DEFAULT_ASSET, DAILY_BASIC_ASSET, MARKET_DELTA_ASSET, DAILY_BASIC_DELTA_ASSET)
    release_tag, assets = client.get_assets(args.tag, names)
    market_meta = read_metadata(target, required=False)
    daily_meta = read_feature_metadata(target, required=False)
    installed_release = _installed_paired_release(market_meta, daily_meta, args.repo)
    if installed_release is None:
        installed_release = upgrade_legacy_paired_metadata(target, args.repo)
        if installed_release is not None:
            LOGGER.info("Upgraded paired local metadata to trusted schema v2: %s", installed_release)
    if not args.full and installed_release == release_tag:
        return f"Already up to date: {release_tag}"
    temporary = Path(tempfile.mkdtemp(prefix="deep-alpha-paired-"))
    try:
        delta_names = (MARKET_DELTA_ASSET, DAILY_BASIC_DELTA_ASSET)
        use_delta = False
        if not args.full and all(name in assets for name in delta_names):
            if installed_release is not None:
                delta_base = client.get_previous_paired_full_release_tag(
                    release_tag, DEFAULT_ASSET, DAILY_BASIC_ASSET
                )
                use_delta = installed_release == delta_base
                if not use_delta:
                    LOGGER.info(
                        "Selecting paired full update before downloads: installed release %s, delta base %s",
                        installed_release,
                        delta_base or "unknown",
                    )
            else:
                LOGGER.info(
                    "Selecting paired full update before downloads: local market and daily-basic are not a paired release"
                )
        if use_delta:
            try:
                paths = _download_assets(client, {name: assets[name] for name in delta_names}, temporary)
                print("Applying trusted-version temporal delta...", flush=True)
                result = apply_paired_archives(paths[MARKET_DELTA_ASSET], paths[DAILY_BASIC_DELTA_ASSET], target, release_tag)
                stats = result.get("delta_stats", {})
                if not isinstance(stats, dict):
                    stats = {}
                timings = stats.get("timings", {})
                if not isinstance(timings, dict):
                    timings = {}
                timing_text = ", ".join(f"{name}={seconds:.3f}s" for name, seconds in timings.items())
                return (f"Updated paired data to {release_tag} using temporal delta; "
                        f"touched={stats.get('touched', 0)} append={stats.get('append', 0)} "
                        f"create={stats.get('create', 0)}; {timing_text}")
            except Exception as exc:
                if args.delta_only:
                    raise ProviderError(f"Paired temporal delta cannot be applied: {exc}") from exc
                LOGGER.warning("Temporal delta unavailable; using paired full fallback: %s", exc)
        elif args.delta_only:
            if not all(name in assets for name in delta_names):
                raise ProviderError(f"Paired temporal delta assets are missing from Release {release_tag}")
            raise ProviderError("Paired temporal delta cannot be applied: local paired metadata v2 is not trusted or does not match the delta base")
        full_names = (DEFAULT_ASSET, DAILY_BASIC_ASSET)
        missing = [name for name in full_names if name not in assets]
        if missing:
            raise ProviderError(f"Paired full assets are missing from Release {release_tag}: {', '.join(missing)}")
        paths = _download_assets(client, {name: assets[name] for name in full_names}, temporary)
        install_paired_archives(paths[DEFAULT_ASSET], paths[DAILY_BASIC_ASSET], target, args.repo, assets[DEFAULT_ASSET], assets[DAILY_BASIC_ASSET])
        return f"Updated paired data to {release_tag} using full archives"
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def run(args: argparse.Namespace) -> str | None:
    if args.command == "download":
        return download_release(args)
    if args.command == "update":
        if args.dataset is None:
            return update_paired(args)
        if args.full or args.delta_only:
            raise ArgumentError("--full and --delta-only require paired update mode without --dataset")
        target = command_target(args)
        if target.exists():
            validate_provider(target)
            select_daily_basic_tag(args, target)
        client = GitHubReleaseClient(args.repo, args.timeout)
        asset_name = selected_asset(args)
        asset = client.get_asset(args.tag, asset_name)
        meta = {}
        if target.exists():
            meta = (
                read_feature_metadata(target, required=False)
                if args.dataset == "daily-basic"
                else read_metadata(target, required=False)
            )
        if meta and not args.force:
            if meta.get("repo") == args.repo and meta.get("asset_name") == asset_name and meta.get("release_tag") == asset.release_tag:
                return f"Already up to date: {asset.release_tag}"
        args.force = (
            bool(meta) or args.force
            if args.dataset == "daily-basic"
            else target.exists() or args.force
        )
        return download_release(args, asset)

    provider = provider_uri(args.provider_uri)
    validate_provider(provider)
    if args.command == "info":
        metadata = read_metadata(provider, required=False)
        daily_basic_metadata = read_feature_metadata(provider, required=False)
        calendars = read_calendar(provider)
        symbols = read_symbols(provider)
        universes = read_universes(provider)
        fields = read_fields(provider)
        market_fields = [field for field in DEFAULT_FIELDS if field in fields]
        daily_basic_fields = [
            field for field in fields if field not in DEFAULT_FIELDS
        ]
        lines = [
            f"provider_uri: {provider}",
            f"calendar_start: {calendars[0]}",
            f"calendar_end: {calendars[-1]}",
            f"instrument_count: {len(symbols)}",
            f"fields: {', '.join(fields)}",
            "data_sources:",
            "  market:",
            f"    dataset: {metadata.get('dataset', 'cn_stock_1d')}",
            "    status: "
            + ("installed" if metadata else "installed (metadata unavailable)"),
            f"    release_tag: {metadata.get('release_tag', 'unknown')}",
            f"    asset_name: {metadata.get('asset_name', 'unknown')}",
            f"    installed_at: {metadata.get('installed_at', 'unknown')}",
            f"    date_range: {calendars[0]} to {calendars[-1]}",
            f"    instrument_count: {len(symbols)}",
            f"    fields: {', '.join(market_fields)}",
        ]
        if daily_basic_metadata:
            source_start = daily_basic_metadata.get("source_min_date", "unknown")
            source_end = daily_basic_metadata.get("source_max_date", "unknown")
            lines.extend(
                [
                    "  daily-basic:",
                    f"    dataset: {daily_basic_metadata.get('dataset', 'daily_basic')}",
                    "    status: installed",
                    f"    release_tag: {daily_basic_metadata.get('release_tag', 'unknown')}",
                    f"    asset_name: {daily_basic_metadata.get('asset_name', 'unknown')}",
                    f"    installed_at: {daily_basic_metadata.get('installed_at', 'unknown')}",
                    f"    date_range: {source_start} to {source_end}",
                    "    instrument_count: "
                    f"{daily_basic_metadata.get('instrument_count', 'unknown')}",
                    f"    fields: {', '.join(daily_basic_fields)}",
                ]
            )
        else:
            lines.extend(
                [
                    "  daily-basic:",
                    "    dataset: daily_basic",
                    "    status: not installed",
                    "    fields: ",
                ]
            )
        lines.append("universes:")
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
    if args.command == "indicator":
        fields = parse_indicator_fields(args.fields)
        service = MarketDataService(provider, args.region)
        frame = service.get_kline(
            KlineQuery(args.symbol, args.start, args.end, fields)
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
