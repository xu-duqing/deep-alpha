from datetime import date
from pathlib import Path

import pytest

from deep_alpha.config import DAILY_BASIC_FIELDS
from deep_alpha.errors import ArgumentError, ProviderError
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
