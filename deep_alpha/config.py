"""Configuration defaults for supported datasets."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REPO = "xu-duqing/investment_data"
DEFAULT_ASSET = "qlib_bin.tar.gz"
DEFAULT_PROVIDER_URI = Path("~/.qlib/qlib_data/cn_data")
DEFAULT_FIELDS = ("open", "high", "low", "close", "volume", "amount", "vwap")
FIELD_MAP = {name: f"${name}" for name in DEFAULT_FIELDS}
META_FILENAME = ".investment_data_meta.json"


@dataclass(frozen=True)
class DatasetConfig:
    name: str
    provider_uri: Path
    region: str
    freq: str
    fields: tuple[str, ...]


def provider_uri(value: str | Path | None = None) -> Path:
    raw = value if value is not None else os.getenv("DEEP_ALPHA_PROVIDER_URI", DEFAULT_PROVIDER_URI)
    return Path(raw).expanduser().absolute()


def dataset_config(uri: str | Path | None = None, region: str = "cn") -> DatasetConfig:
    return DatasetConfig("cn_stock_1d", provider_uri(uri), region, "day", DEFAULT_FIELDS)
