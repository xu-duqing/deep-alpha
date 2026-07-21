"""Market data services independent of command-line parsing."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from .config import FIELD_MAP
from .errors import ArgumentError, ProviderError, QueryError
from .qlib_client import QlibClient
from .symbols import normalize_symbol


@dataclass(frozen=True)
class KlineQuery:
    symbol: str
    start: str | None
    end: str | None
    fields: list[str]
    freq: str = "day"


def validate_date(value: str | None) -> str | None:
    if value is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ArgumentError(f"Invalid date: {value}. Expected YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ArgumentError(f"Invalid date: {value}. Expected YYYY-MM-DD") from exc
    return value


def read_calendar(provider: Path) -> list[str]:
    path = provider / "calendars/day.txt"
    if not path.is_file():
        raise ProviderError("Invalid Qlib provider directory: missing calendars/day.txt")
    values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not values:
        raise ProviderError("Invalid Qlib provider directory: empty calendars/day.txt")
    return sorted(set(values))


def read_symbols(provider: Path) -> list[str]:
    directory = provider / "instruments"
    if not directory.is_dir():
        raise ProviderError("Invalid Qlib provider directory: missing instruments/")
    symbols: set[str] = set()
    for path in directory.glob("*.txt"):
        for line in path.read_text(encoding="utf-8").splitlines():
            columns = line.strip().split()
            if columns:
                symbols.add(columns[0].upper())
    if not symbols:
        raise ProviderError("No symbols found in local dataset")
    return sorted(symbols)


def read_fields(provider: Path) -> list[str]:
    fields: set[str] = set()
    for path in (provider / "features").glob("*/*"):
        if path.is_file():
            name = path.name.split(".", 1)[0].lower()
            if name in FIELD_MAP:
                fields.add(name)
    return [field for field in FIELD_MAP if field in fields]


def parse_fields(value: str) -> list[str]:
    fields = [item.strip().lower() for item in value.split(",")]
    if not fields or any(not item for item in fields):
        raise ArgumentError("Fields must be a comma-separated non-empty list")
    result: list[str] = []
    for field in fields:
        if field not in FIELD_MAP:
            available = ", ".join(FIELD_MAP)
            raise ArgumentError(f"Unsupported field: {field}. Available fields: {available}")
        if field not in result:
            result.append(field)
    return result


class MarketDataService:
    def __init__(self, provider: Path, region: str):
        self.provider = provider
        self.client = QlibClient(provider, region)

    def get_kline(self, query: KlineQuery) -> pd.DataFrame:
        start, end = validate_date(query.start), validate_date(query.end)
        if start and end and start > end:
            raise ArgumentError("Start date must not be after end date")
        calendars = read_calendar(self.provider)
        start = start or calendars[0]
        end = end or calendars[-1]
        symbol = normalize_symbol(query.symbol, read_symbols(self.provider))
        frame = self.client.features(
            instruments=[symbol],
            fields=[FIELD_MAP[field] for field in query.fields],
            start_time=start,
            end_time=end,
            freq=query.freq,
        )
        if not isinstance(frame, pd.DataFrame):
            raise QueryError("Qlib query returned an unexpected result")
        if frame.empty:
            columns = pd.Index(["datetime", "symbol", *query.fields])
            return pd.DataFrame(columns=columns)
        result = frame.reset_index()
        instrument_column = next(
            (name for name in ("instrument", "symbol") if name in result.columns), None
        )
        datetime_column = next(
            (name for name in ("datetime", "date") if name in result.columns), None
        )
        if instrument_column is None or datetime_column is None:
            raise QueryError("Qlib result has an unexpected index structure")
        renames = {instrument_column: "symbol", datetime_column: "datetime"}
        renames.update({FIELD_MAP[field]: field for field in query.fields})
        result = result.rename(columns=renames)
        missing = [name for name in query.fields if name not in result.columns]
        if missing:
            raise QueryError(f"Qlib result is missing fields: {', '.join(missing)}")
        result["symbol"] = symbol
        result = result.loc[:, ["datetime", "symbol", *query.fields]]
        return result.sort_values(by="datetime")
