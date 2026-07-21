import pytest

from deep_alpha.errors import ArgumentError, QueryError
from deep_alpha.services import parse_fields, validate_date
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
    assert validate_date("2024-02-29") == "2024-02-29"
    with pytest.raises(ArgumentError):
        parse_fields("open,turnover")
    with pytest.raises(ArgumentError):
        validate_date("2024-02-30")
