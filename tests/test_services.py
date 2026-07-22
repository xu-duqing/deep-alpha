import pandas as pd
import pytest

from deep_alpha.errors import ArgumentError, QueryError
from deep_alpha.services import (
    KlineQuery,
    MarketDataService,
    parse_fields,
    parse_indicator_fields,
    validate_date,
)
from deep_alpha.symbols import normalize_symbol

SYMBOLS = ["SH600519", "SZ000001", "BJ000001"]


@pytest.mark.parametrize("value,expected", [("SH600519", "SH600519"), ("sh600519", "SH600519"), ("600519.SH", "SH600519"), ("600519", "SH600519")])
def test_normalize_symbol(value, expected):
    assert normalize_symbol(value, SYMBOLS) == expected


def test_ambiguous_symbol():
    with pytest.raises(QueryError, match="Ambiguous"):
        normalize_symbol("000001", SYMBOLS)


def test_unknown_symbol():
    with pytest.raises(QueryError, match="not found"):
        normalize_symbol("SH000002", SYMBOLS)


def test_fields_and_dates():
    assert parse_fields("open, close,open") == ["open", "close"]
    assert parse_fields("pe, turnover_rate,limit_status") == [
        "pe",
        "turnover_rate",
        "limit_status",
    ]
    assert validate_date("2024-02-29") == "2024-02-29"
    with pytest.raises(ArgumentError):
        parse_fields("open,turnover")
    with pytest.raises(ArgumentError):
        validate_date("2024-02-30")


def test_indicator_fields_only_accept_daily_basic_fields():
    assert parse_indicator_fields("turnover_rate, pe") == ["turnover_rate", "pe"]
    with pytest.raises(ArgumentError, match="Unsupported indicator field: close"):
        parse_indicator_fields("turnover_rate,close")


class FakeClient:
    def features(self, **kwargs):
        dates = pd.to_datetime(["2024-01-02", "2024-01-03"])
        index = pd.MultiIndex.from_product(
            [["SH600519"], dates], names=["instrument", "datetime"]
        )
        if kwargs["fields"] == ["$factor"]:
            return pd.DataFrame({"$factor": [0.5, 1.0]}, index=index)
        return pd.DataFrame(
            {
                "$close": [50.0, 120.0],
                "$volume": [2000.0, 1000.0],
                "$amount": [10000.0, 12000.0],
                "$factor": [0.5, 1.0],
            },
            index=index,
        )


@pytest.mark.parametrize(
    "adjust,close,volume",
    [
        ("none", [100.0, 120.0], [1000.0, 1000.0]),
        ("qfq", [50.0, 120.0], [1000.0, 1000.0]),
        ("hfq", [100.0, 240.0], [1000.0, 1000.0]),
    ],
)
def test_kline_adjustment(tmp_path, adjust, close, volume):
    provider = tmp_path / "provider"
    (provider / "calendars").mkdir(parents=True)
    (provider / "instruments").mkdir()
    (provider / "calendars/day.txt").write_text("2024-01-02\n2024-01-03\n")
    (provider / "instruments/all.txt").write_text(
        "SH600519\t2024-01-02\t2024-01-03\n"
    )
    service = MarketDataService(provider, "cn")
    setattr(service, "client", FakeClient())
    result = service.get_kline(
        KlineQuery(
            "SH600519",
            "2024-01-02",
            "2024-01-03",
            ["close", "volume", "amount"],
            adjust=adjust,
        )
    )
    assert result["close"].tolist() == close
    assert result["volume"].tolist() == volume
    assert result["amount"].tolist() == [10000.0, 12000.0]
