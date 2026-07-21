from pathlib import Path

import pytest

from deep_alpha.errors import ArgumentError
from deep_alpha.main import build_parser, run


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
    assert args.format == "csv"
    assert args.region == "cn"


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


def test_unsupported_adjust(tmp_path):
    provider = tmp_path / "provider"
    make_provider(provider)
    args = build_parser().parse_args(["kline", "--provider-uri", str(provider), "--symbol", "SH600519", "--adjust", "qfq"])
    with pytest.raises(ArgumentError, match="Only"):
        run(args)
