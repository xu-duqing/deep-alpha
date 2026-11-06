"""Market-aware health checks for the deep-alpha A-share Qlib provider.

This intentionally differs from Qlib's generic check_data_health.py:

* placeholder Beijing symbols (BJ92xxxx) are ignored by default;
* leading/trailing rows outside a symbol's actual OHLCV history are not
  treated as missing observations;
* a short listing history is reported as information, not data corruption;
* price-jump checks account for the board's daily price-limit regime.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd

LOGGER = logging.getLogger("check_data_health")
OHLCV = ("open", "high", "low", "close", "volume")
REQUIRED_FIELDS = tuple(f"${field}" for field in (*OHLCV, "factor"))
PLACEHOLDER_PATTERN = re.compile(r"^BJ92\d{4}$", re.IGNORECASE)


@dataclass(frozen=True)
class CheckResults:
    missing_data: pd.DataFrame
    large_steps: pd.DataFrame
    missing_columns: pd.DataFrame
    missing_factor: pd.DataFrame
    skipped_placeholders: tuple[str, ...]
    short_histories: Mapping[str, int]

    @property
    def healthy(self) -> bool:
        return all(
            frame.empty
            for frame in (self.missing_data, self.large_steps, self.missing_columns, self.missing_factor)
        )


def is_placeholder_symbol(symbol: str) -> bool:
    """Return whether a symbol is a known source placeholder/test code."""
    return PLACEHOLDER_PATTERN.fullmatch(symbol) is not None


def price_limit_for(symbol: str) -> float:
    """Return the normal daily price limit for the A-share board.

    The value is used as an exemption floor, not as proof that a move is
    valid. A separately configured stricter threshold is never relaxed.
    """
    normalized = symbol.upper()
    code = normalized[2:] if normalized[:2] in {"SH", "SZ", "BJ"} else normalized
    if normalized.startswith("BJ"):
        return 0.30
    if normalized.startswith("SZ") and code.startswith(("300", "301")):
        return 0.20
    if normalized.startswith("SH") and code.startswith("688"):
        return 0.20
    return 0.10


def active_history(frame: pd.DataFrame) -> pd.DataFrame:
    """Remove calendar padding before listing and after the last observation."""
    available = [column for column in OHLCV if column in frame.columns]
    if not available:
        return frame.iloc[0:0]
    active = frame.loc[:, available].notna().any(axis=1)
    if not active.any():
        return frame.iloc[0:0]
    positions = active.to_numpy().nonzero()[0]
    return frame.iloc[positions[0] : positions[-1] + 1]


def _date_text(value: object) -> str:
    if isinstance(value, tuple):
        value = value[-1]
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)


class DataHealthChecker:
    def __init__(
        self,
        qlib_dir: str | os.PathLike[str] | None = None,
        *,
        data: Mapping[str, pd.DataFrame] | None = None,
        freq: str = "day",
        large_step_threshold_price: float = 0.5,
        large_step_threshold_volume: float = 3.0,
        missing_data_num: int = 0,
        min_history: int = 244,
        include_placeholders: bool = False,
        price_limit_tolerance: float = 0.02,
    ) -> None:
        if (qlib_dir is None) == (data is None):
            raise ValueError("provide exactly one of qlib_dir or data")
        self.qlib_dir = Path(qlib_dir).expanduser().resolve() if qlib_dir is not None else None
        self.freq = freq
        self.large_step_threshold_price = large_step_threshold_price
        self.large_step_threshold_volume = large_step_threshold_volume
        self.missing_data_num = missing_data_num
        self.min_history = min_history
        self.include_placeholders = include_placeholders
        self.price_limit_tolerance = price_limit_tolerance
        self.skipped_placeholders: list[str] = []
        self.data = dict(data) if data is not None else self._load_qlib_data()

    def _load_qlib_data(self) -> dict[str, pd.DataFrame]:
        try:
            import qlib
            from qlib.data import D
        except ImportError as exc:
            raise RuntimeError("pyqlib is required when --qlib_dir is used") from exc

        assert self.qlib_dir is not None
        qlib.init(provider_uri=str(self.qlib_dir))
        instruments = D.instruments(market="all")
        symbols = D.list_instruments(instruments=instruments, as_list=True, freq=self.freq)
        result: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            if not self.include_placeholders and is_placeholder_symbol(symbol):
                self.skipped_placeholders.append(symbol)
                continue
            frame = D.features([symbol], list(REQUIRED_FIELDS), freq=self.freq).rename(
                columns={field: field.removeprefix("$") for field in REQUIRED_FIELDS}
            )
            result[symbol] = frame
        return result

    def check(self) -> CheckResults:
        missing_rows: list[dict[str, object]] = []
        step_rows: list[dict[str, object]] = []
        column_rows: list[dict[str, object]] = []
        factor_rows: list[dict[str, object]] = []
        short_histories: dict[str, int] = {}

        for symbol, original in self.data.items():
            if not self.include_placeholders and is_placeholder_symbol(symbol):
                self.skipped_placeholders.append(symbol)
                continue

            absent = [column for column in OHLCV if column not in original.columns]
            if absent:
                column_rows.append({"instruments": symbol, "missing_col": ",".join(absent)})
                continue

            frame = active_history(original)
            if len(frame) < self.min_history:
                short_histories[symbol] = len(frame)

            # A row where every OHLCV field is absent is a legitimate
            # suspension/non-trading day.  Corruption is indicated by a
            # partially populated OHLCV row, not by synchronized NaNs.
            ohlcv = frame.loc[:, OHLCV]
            partial_rows = ohlcv.isna().any(axis=1) & ohlcv.notna().any(axis=1)
            missing = ohlcv.loc[partial_rows].isna().sum()
            if (missing > self.missing_data_num).any():
                missing_rows.append({"instruments": symbol, **missing.to_dict()})

            for column in OHLCV:
                changes = frame[column].pct_change(fill_method=None).abs()
                if column == "volume":
                    threshold = self.large_step_threshold_volume
                else:
                    threshold = max(
                        self.large_step_threshold_price,
                        price_limit_for(symbol) + self.price_limit_tolerance,
                    )
                violations = changes[changes > threshold]
                if not violations.empty:
                    position = violations.idxmax()
                    step_rows.append(
                        {
                            "instruments": symbol,
                            "col_name": column,
                            "date": _date_text(position),
                            "pct_change": float(violations.max()),
                            "threshold": threshold,
                        }
                    )

            if "factor" not in original.columns:
                factor_rows.append(
                    {"instruments": symbol, "missing_factor_col": True, "missing_factor_data": True}
                )
            elif frame.empty or frame["factor"].isna().all():
                factor_rows.append(
                    {"instruments": symbol, "missing_factor_col": False, "missing_factor_data": True}
                )

        def make_frame(rows: Iterable[dict[str, object]], columns: list[str]) -> pd.DataFrame:
            frame = pd.DataFrame(rows, columns=["instruments", *columns])
            return frame.set_index("instruments")

        return CheckResults(
            missing_data=make_frame(missing_rows, list(OHLCV)),
            large_steps=make_frame(step_rows, ["col_name", "date", "pct_change", "threshold"]),
            missing_columns=make_frame(column_rows, ["missing_col"]),
            missing_factor=make_frame(
                factor_rows, ["missing_factor_col", "missing_factor_data"]
            ),
            skipped_placeholders=tuple(sorted(set(self.skipped_placeholders))),
            short_histories=short_histories,
        )

    def check_data(self) -> CheckResults:
        results = self.check()
        print(f"Summary of data health check ({len(self.data)} instruments checked):")
        if results.skipped_placeholders:
            LOGGER.info("Skipped %d BJ92xxxx placeholder symbols.", len(results.skipped_placeholders))
        if results.short_histories:
            LOGGER.info(
                "%d instruments have fewer than %d active rows; short listing histories are not errors.",
                len(results.short_histories),
                self.min_history,
            )

        reports = (
            (results.missing_data, "There is missing data inside active listing periods."),
            (results.large_steps, "The OHLCV columns have unexplained large step changes."),
            (results.missing_columns, "Required OHLCV columns are missing."),
            (results.missing_factor, "The factor column does not exist or is empty."),
        )
        for frame, message in reports:
            if not frame.empty:
                LOGGER.warning(message)
                print(frame)
        if results.healthy:
            LOGGER.info("Data health check passed.")
        return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check_data"])
    parser.add_argument("--qlib_dir", required=True)
    parser.add_argument("--freq", default="day")
    parser.add_argument("--large_step_threshold_price", type=float, default=0.5)
    parser.add_argument("--large_step_threshold_volume", type=float, default=3.0)
    parser.add_argument("--missing_data_num", type=int, default=0)
    parser.add_argument("--min_history", type=int, default=244)
    parser.add_argument("--include_placeholders", action="store_true")
    parser.add_argument("--price_limit_tolerance", type=float, default=0.02)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    args = build_parser().parse_args(argv)
    checker = DataHealthChecker(
        qlib_dir=args.qlib_dir,
        freq=args.freq,
        large_step_threshold_price=args.large_step_threshold_price,
        large_step_threshold_volume=args.large_step_threshold_volume,
        missing_data_num=args.missing_data_num,
        min_history=args.min_history,
        include_placeholders=args.include_placeholders,
        price_limit_tolerance=args.price_limit_tolerance,
    )
    results = checker.check_data()
    return 0 if results.healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
