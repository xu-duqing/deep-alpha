from datetime import date
import json
from pathlib import Path

import pytest

from deep_alpha.config import (
    DAILY_BASIC_ASSET,
    DAILY_BASIC_DELTA_ASSET,
    DAILY_BASIC_FIELDS,
    DEFAULT_ASSET,
    MARKET_DELTA_ASSET,
)
from deep_alpha.errors import ArgumentError, ProviderError
from deep_alpha.github_release import ReleaseAsset
from deep_alpha.main import build_parser, default_start_date, download_release, run


def make_provider(path: Path):
    (path / "calendars").mkdir(parents=True)
    (path / "features").mkdir()
    (path / "instruments").mkdir()
    (path / "calendars/day.txt").write_text("2024-01-02\n2024-01-03\n")
    (path / "instruments/all.txt").write_text("SH600519\t2024-01-02\t2024-01-03\nSZ000001\t2024-01-02\t2024-01-03\n")
    feature = path / "features/SH600519"
    feature.mkdir()
    (feature / "close.day.bin").write_bytes(b"data")


def test_parser_kline_defaults():
    args = build_parser().parse_args(["kline", "--symbol", "600519"])
    assert args.format == "json"
    assert args.region == "cn"
    assert args.start == default_start_date()


def test_daily_basic_download_defaults():
    args = build_parser().parse_args(["download", "--dataset", "daily-basic"])
    assert args.dataset == "daily-basic"
    assert args.asset is None


def test_update_defaults_to_paired_and_flags_are_exclusive():
    parser = build_parser()
    args = parser.parse_args(["update"])
    assert args.dataset is None
    assert not args.full and not args.delta_only
    assert parser.parse_args(["update", "--full"]).full
    with pytest.raises(SystemExit):
        parser.parse_args(["update", "--full", "--delta-only"])


def test_paired_strategy_flags_reject_legacy_dataset():
    args = build_parser().parse_args(["update", "--dataset", "market", "--full"])
    with pytest.raises(ArgumentError, match="paired update mode"):
        run(args)


def test_default_start_date_handles_month_boundaries():
    assert default_start_date(date(2026, 7, 21)) == "2026-06-21"
    assert default_start_date(date(2024, 3, 31)) == "2024-02-29"
    assert default_start_date(date(2026, 1, 31)) == "2025-12-31"


def test_provider_uri_is_accepted_before_or_after_subcommand():
    parser = build_parser()
    before = parser.parse_args(["--provider-uri", "/before", "info"])
    after = parser.parse_args(["info", "--provider-uri", "/after"])
    assert before.provider_uri == "/before"
    assert after.provider_uri == "/after"


def test_symbols_and_calendar_commands(tmp_path):
    provider = tmp_path / "provider"
    make_provider(provider)
    symbols = build_parser().parse_args(["symbols", "--provider-uri", str(provider), "--contains", "600519"])
    assert run(symbols) == "SH600519"
    calendar = build_parser().parse_args(["calendar", "--provider-uri", str(provider), "--start", "2024-01-03"])
    assert run(calendar) == "2024-01-03"


def test_info_reports_discovered_fields(tmp_path):
    provider = tmp_path / "provider"
    make_provider(provider)
    args = build_parser().parse_args(["info", "--provider-uri", str(provider)])
    result = run(args)
    assert result is not None
    assert "fields: close" in result
    assert "data_sources:" in result
    assert "  market:" in result
    assert "    dataset: cn_stock_1d" in result
    assert "    status: installed (metadata unavailable)" in result
    assert "    fields: close" in result
    assert "universes:" in result
    assert "all: 2 instruments, 2024-01-02 to 2024-01-03" in result


def test_daily_basic_fields_are_queryable():
    args = build_parser().parse_args(
        ["kline", "--symbol", "600519", "--fields", "pe,pb,limit_status"]
    )
    assert args.fields == "pe,pb,limit_status"


def test_indicator_command_defaults_to_all_fields():
    args = build_parser().parse_args(["indicator", "--symbol", "000001.SZ"])
    assert args.symbol == "000001.SZ"
    assert args.fields == ",".join(DAILY_BASIC_FIELDS)
    assert args.format == "json"
    assert args.start == default_start_date()


def test_indicator_help_lists_available_fields(capsys):
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(["indicator", "--help"])
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    assert "--fields FIELDS" in help_text
    assert "turnover_rate" in help_text
    assert "limit_status" in help_text


def test_info_reports_multiple_installed_data_sources(tmp_path):
    provider = tmp_path / "provider"
    make_provider(provider)
    (provider / ".investment_data_meta.json").write_text(
        '{"schema_version": 1, "dataset": "cn_stock_1d", '
        '"release_tag": "2026-07-22", "asset_name": "qlib_bin.tar.gz", '
        '"installed_at": "2026-07-22T10:00:00+08:00"}'
    )
    (provider / "features/SH600519/pe.day.bin").write_bytes(b"data")
    (provider / ".daily_basic_meta.json").write_text(
        '{"schema_version": 1, "dataset": "daily_basic", '
        '"release_tag": "2026-07-22", '
        '"asset_name": "daily_basic_qlib_features.tar.gz", '
        '"installed_at": "2026-07-22T10:05:00+08:00", '
        '"source_min_date": "2020-01-02", "source_max_date": "2026-07-21", '
        '"instrument_count": 5756}'
    )
    result = run(
        build_parser().parse_args(["info", "--provider-uri", str(provider)])
    )
    assert result is not None
    assert "  market:" in result
    assert "    release_tag: 2026-07-22" in result
    assert "    asset_name: qlib_bin.tar.gz" in result
    assert "  daily-basic:" in result
    assert "    dataset: daily_basic" in result
    assert "    asset_name: daily_basic_qlib_features.tar.gz" in result
    assert "    date_range: 2020-01-02 to 2026-07-21" in result
    assert "    instrument_count: 5756" in result
    assert "    fields: pe" in result


def test_download_existing_target_fails_before_network(tmp_path, monkeypatch):
    target = tmp_path / "provider"
    target.mkdir()
    args = build_parser().parse_args(["download", "--target-dir", str(target)])

    def unexpected_client(*args, **kwargs):
        raise AssertionError("network client must not be created")

    monkeypatch.setattr("deep_alpha.main.GitHubReleaseClient", unexpected_client)
    with pytest.raises(ProviderError, match="deep-alpha update"):
        download_release(args)


def test_daily_basic_download_uses_feature_asset_and_installer(tmp_path, monkeypatch):
    provider = tmp_path / "provider"
    make_provider(provider)
    args = build_parser().parse_args(
        ["download", "--dataset", "daily-basic", "--target-dir", str(provider)]
    )
    calls = []

    class Asset:
        release_tag = "2026-07-22"
        name = "daily_basic_qlib_features.tar.gz"
        size = 3

    class Client:
        def __init__(self, repo, timeout):
            pass

        def get_asset(self, tag, name):
            calls.append(("asset", name))
            return Asset()

        def download(self, asset, destination, progress):
            destination.write_bytes(b"abc")

    monkeypatch.setattr("deep_alpha.main.GitHubReleaseClient", Client)
    monkeypatch.setattr(
        "deep_alpha.main.install_feature_archive",
        lambda archive, target, repo, asset, replace: calls.append(
            ("install", target, replace)
        ),
    )
    result = download_release(args)
    assert calls == [
        ("asset", "daily_basic_qlib_features.tar.gz"),
        ("install", provider, False),
    ]
    assert result == f"Installed daily-basic data 2026-07-22 to {provider}"


def test_daily_basic_download_uses_installed_market_release(tmp_path, monkeypatch):
    provider = tmp_path / "provider"
    make_provider(provider)
    (provider / ".investment_data_meta.json").write_text(
        '{"schema_version": 1, "dataset": "cn_stock_1d", '
        '"repo": "owner/repo", "release_tag": "2026-07-28", '
        '"asset_name": "qlib_bin.tar.gz"}'
    )
    args = build_parser().parse_args(
        [
            "download",
            "--dataset",
            "daily-basic",
            "--repo",
            "owner/repo",
            "--target-dir",
            str(provider),
        ]
    )
    calls = []

    class Asset:
        release_tag = "2026-07-28"
        name = "daily_basic_qlib_features.tar.gz"
        size = 3

    class Client:
        def __init__(self, repo, timeout):
            pass

        def get_asset(self, tag, name):
            calls.append(("asset", tag, name))
            return Asset()

        def download(self, asset, destination, progress):
            destination.write_bytes(b"abc")

    monkeypatch.setattr("deep_alpha.main.GitHubReleaseClient", Client)
    monkeypatch.setattr(
        "deep_alpha.main.install_feature_archive",
        lambda archive, target, repo, asset, replace: calls.append(
            ("install", target, replace)
        ),
    )

    result = download_release(args)

    assert calls == [
        ("asset", "2026-07-28", "daily_basic_qlib_features.tar.gz"),
        ("install", provider, False),
    ]
    assert result == f"Installed daily-basic data 2026-07-28 to {provider}"


def test_daily_basic_update_checks_compatible_release_before_latest(
    tmp_path, monkeypatch
):
    provider = tmp_path / "provider"
    make_provider(provider)
    (provider / ".investment_data_meta.json").write_text(
        '{"schema_version": 1, "dataset": "cn_stock_1d", '
        '"repo": "owner/repo", "release_tag": "2026-07-28", '
        '"asset_name": "qlib_bin.tar.gz"}'
    )
    calls = []

    class Asset:
        release_tag = "2026-07-28"
        name = "daily_basic_qlib_features.tar.gz"
        size = 3

    class Client:
        def __init__(self, repo, timeout):
            pass

        def get_asset(self, tag, name):
            calls.append((tag, name))
            return Asset()

    monkeypatch.setattr("deep_alpha.main.GitHubReleaseClient", Client)
    monkeypatch.setattr(
        "deep_alpha.main.download_release",
        lambda args, asset: f"selected {asset.release_tag}",
    )
    args = build_parser().parse_args(
        [
            "update",
            "--dataset",
            "daily-basic",
            "--repo",
            "owner/repo",
            "--target-dir",
            str(provider),
        ]
    )

    assert run(args) == "selected 2026-07-28"
    assert calls == [("2026-07-28", "daily_basic_qlib_features.tar.gz")]


def test_market_update_adopts_valid_provider_without_metadata(tmp_path, monkeypatch):
    provider = tmp_path / "provider"
    make_provider(provider)
    calls = []

    class Asset:
        release_tag = "2026-07-28"
        name = "qlib_bin.tar.gz"
        size = 3

    class Client:
        def __init__(self, repo, timeout):
            pass

        def get_asset(self, tag, name):
            calls.append(("asset", tag, name))
            return Asset()

    def fake_download(args, asset):
        calls.append(("download", args.force, asset.release_tag))
        return f"selected {asset.release_tag}"

    monkeypatch.setattr("deep_alpha.main.GitHubReleaseClient", Client)
    monkeypatch.setattr("deep_alpha.main.download_release", fake_download)
    args = build_parser().parse_args(["update", "--dataset", "market", "--target-dir", str(provider)])

    assert run(args) == "selected 2026-07-28"
    assert calls == [
        ("asset", None, "qlib_bin.tar.gz"),
        ("download", True, "2026-07-28"),
    ]


def _write_paired_metadata(provider: Path, release_tag: str, repo: str = "owner/repo") -> None:
    calendar = (provider / "calendars/day.txt").read_text().splitlines()
    common = {"schema_version": 2, "repo": repo, "release_tag": release_tag,
              "paired_release_tag": release_tag, "transaction_id": "trusted-tx",
              "state": "committed", "calendar_count": len(calendar), "calendar_end": calendar[-1]}
    (provider / ".investment_data_meta.json").write_text(json.dumps({**common, "dataset": "cn_stock_1d"}))
    (provider / ".daily_basic_meta.json").write_text(json.dumps({**common, "dataset": "daily_basic"}))


def _release_assets(tag: str) -> dict[str, ReleaseAsset]:
    return {
        name: ReleaseAsset(tag, name, 3, f"https://example.test/{name}")
        for name in (DEFAULT_ASSET, DAILY_BASIC_ASSET, MARKET_DELTA_ASSET, DAILY_BASIC_DELTA_ASSET)
    }


def test_paired_update_skips_delta_download_when_installed_release_is_not_delta_base(tmp_path, monkeypatch):
    provider = tmp_path / "provider"
    make_provider(provider)
    _write_paired_metadata(provider, "2026-08-25")
    calls = []

    class Client:
        def __init__(self, repo, timeout):
            pass

        def get_assets(self, tag, names):
            return "2026-11-02", _release_assets("2026-11-02")

        def get_previous_paired_full_release_tag(self, target, market, daily):
            calls.append(("base", target))
            return "2026-10-30"

        def download(self, asset, destination, progress):
            calls.append(("download", asset.name))
            destination.write_bytes(b"abc")

    monkeypatch.setattr("deep_alpha.main.GitHubReleaseClient", Client)
    monkeypatch.setattr("deep_alpha.main.install_paired_archives", lambda *args: calls.append(("full",)))

    result = run(build_parser().parse_args(["update", "--repo", "owner/repo", "--target-dir", str(provider)]))

    assert result == "Updated paired data to 2026-11-02 using full archives"
    assert calls == [
        ("base", "2026-11-02"),
        ("download", DEFAULT_ASSET),
        ("download", DAILY_BASIC_ASSET),
        ("full",),
    ]


def test_paired_update_downloads_delta_when_installed_release_matches_base(tmp_path, monkeypatch):
    provider = tmp_path / "provider"
    make_provider(provider)
    _write_paired_metadata(provider, "2026-10-30")
    calls = []

    class Client:
        def __init__(self, repo, timeout):
            pass

        def get_assets(self, tag, names):
            return "2026-11-02", _release_assets("2026-11-02")

        def get_previous_paired_full_release_tag(self, target, market, daily):
            return "2026-10-30"

        def download(self, asset, destination, progress):
            calls.append(("download", asset.name))
            destination.write_bytes(b"abc")

    monkeypatch.setattr("deep_alpha.main.GitHubReleaseClient", Client)
    monkeypatch.setattr("deep_alpha.main.apply_paired_archives", lambda *args: (calls.append(("delta",)) or {
        "delta_stats": {"touched": 2, "append": 2, "create": 0, "timings": {"total": 0.1}}
    }))

    result = run(build_parser().parse_args(["update", "--repo", "owner/repo", "--target-dir", str(provider)]))

    assert result == "Updated paired data to 2026-11-02 using temporal delta; touched=2 append=2 create=0; total=0.100s"
    assert calls == [
        ("download", MARKET_DELTA_ASSET),
        ("download", DAILY_BASIC_DELTA_ASSET),
        ("delta",),
    ]
