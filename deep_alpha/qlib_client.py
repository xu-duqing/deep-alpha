"""Lazy wrapper around Microsoft Qlib."""
from __future__ import annotations

from pathlib import Path

from .errors import QueryError
from .installer import validate_provider


class QlibClient:
    def __init__(self, provider_uri: Path, region: str = "cn"):
        self.provider_uri = provider_uri.expanduser().absolute()
        self.region = region
        self.data = None

    def init(self) -> None:
        validate_provider(self.provider_uri)
        if self.region != "cn":
            raise QueryError(f"Unsupported region: {self.region}")
        try:
            import qlib
            from qlib.config import REG_CN
            from qlib.data import D
        except ImportError as exc:
            raise QueryError(
                "Qlib is not installed. Please install Microsoft Qlib before querying data."
            ) from exc
        try:
            qlib.init(provider_uri=str(self.provider_uri), region=REG_CN)
        except Exception as exc:
            raise QueryError(f"Failed to initialize Qlib: {exc}") from exc
        self.data = D

    def features(self, **kwargs):
        if self.data is None:
            self.init()
        data = self.data
        if data is None:
            raise QueryError("Qlib is not initialized")
        try:
            return data.features(**kwargs)
        except Exception as exc:
            raise QueryError(f"Qlib query failed: {exc}") from exc
