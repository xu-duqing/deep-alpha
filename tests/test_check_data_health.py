from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pandas as pd
import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "check_data_health.py"
SPEC = importlib.util.spec_from_file_location("check_data_health", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
health = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = health
SPEC.loader.exec_module(health)


def frame(values: list[float | None]) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=len(values), freq="D")
    data = {column: values for column in health.OHLCV}
    data["factor"] = [1.0 if value is not None else None for value in values]
    return pd.DataFrame(data, index=dates)


def test_placeholder_symbols_are_excluded() -> None:
    checker = health.DataHealthChecker(
        data={"BJ920001": frame([None, None]), "SH600000": frame([10.0, 10.1])},
        min_history=0,
    )

    results = checker.check()

    assert results.healthy
    assert results.skipped_placeholders == ("BJ920001",)


def test_calendar_padding_and_short_listing_history_are_not_missing_data() -> None:
    checker = health.DataHealthChecker(
        data={"SZ301505": frame([None, 10.0, 10.1, None])},
        min_history=244,
    )

    results = checker.check()

    assert results.missing_data.empty
    assert results.short_histories == {"SZ301505": 2}


def test_missing_value_inside_active_listing_period_is_reported() -> None:
    data = frame([10.0, 10.0, 10.1])
    data.loc[data.index[1], "close"] = None
    checker = health.DataHealthChecker(data={"SH600000": data}, min_history=0)

    results = checker.check()

    assert results.missing_data.loc["SH600000", "close"] == 1


def test_suspension_row_with_all_ohlcv_missing_is_not_corruption() -> None:
    checker = health.DataHealthChecker(
        data={"SH600000": frame([10.0, None, 10.1])},
        min_history=0,
    )

    results = checker.check()

    assert results.missing_data.empty


@pytest.mark.parametrize(
    ("symbol", "limit"),
    [("BJ832317", 0.30), ("SZ301505", 0.20), ("SH688001", 0.20), ("SH600000", 0.10)],
)
def test_board_price_limits(symbol: str, limit: float) -> None:
    assert health.price_limit_for(symbol) == limit


def test_board_limit_move_is_not_a_large_step_when_stricter_threshold_requested() -> None:
    checker = health.DataHealthChecker(
        data={"BJ832317": frame([10.0, 13.0]), "SZ301505": frame([10.0, 12.0])},
        min_history=0,
        large_step_threshold_price=0.10,
    )

    results = checker.check()

    price_steps = results.large_steps[results.large_steps["col_name"] != "volume"]
    assert price_steps.empty


def test_price_move_above_board_limit_and_tolerance_is_reported() -> None:
    checker = health.DataHealthChecker(
        data={"SZ301505": frame([10.0, 12.3])},
        min_history=0,
        large_step_threshold_price=0.10,
        price_limit_tolerance=0.02,
    )

    results = checker.check()

    assert set(results.large_steps["col_name"]) == {"open", "high", "low", "close"}
    assert results.large_steps["threshold"].eq(0.22).all()
