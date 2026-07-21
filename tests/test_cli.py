from datetime import date
from pathlib import Path

import pytest

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
    assert "datasets:" in result
    assert "all: 2 instruments, 2024-01-02 to 2024-01-03" in result


def test_download_existing_target_fails_before_network(tmp_path, monkeypatch):
    target = tmp_path / "provider"
    target.mkdir()
    args = build_parser().parse_args(["download", "--target-dir", str(target)])

    def unexpected_client(*args, **kwargs):
        raise AssertionError("network client must not be created")

    monkeypatch.setattr("deep_alpha.main.GitHubReleaseClient", unexpected_client)
    with pytest.raises(ProviderError, match="deep-alpha update"):
        download_release(args)
