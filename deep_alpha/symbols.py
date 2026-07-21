"""Qlib symbol normalization."""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from .errors import ArgumentError, QueryError

_CANONICAL = re.compile(r"^(SH|SZ|BJ)(\d{6})$")
_SUFFIX = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$")
_BARE = re.compile(r"^\d{6}$")


def normalize_symbol(value: str, available: Iterable[str]) -> str:
    raw = value.strip().upper()
    symbols = {item.strip().upper() for item in available if item.strip()}

    canonical = _CANONICAL.fullmatch(raw)
    if canonical:
        result = raw
    else:
        suffix = _SUFFIX.fullmatch(raw)
        if suffix:
            result = f"{suffix.group(2)}{suffix.group(1)}"
        elif _BARE.fullmatch(raw):
            by_code: dict[str, list[str]] = defaultdict(list)
            for symbol in symbols:
                match = _CANONICAL.fullmatch(symbol)
                if match:
                    by_code[match.group(2)].append(symbol)
            matches = sorted(set(by_code.get(raw, [])))
            if len(matches) > 1:
                choices = " or ".join(matches)
                raise QueryError(f"Ambiguous symbol: {raw}. Please use {choices}")
            if not matches:
                raise QueryError(f"Symbol not found in local dataset: {raw}")
            return matches[0]
        else:
            raise ArgumentError(f"Invalid symbol: {value}")

    if result not in symbols:
        raise QueryError(f"Symbol not found in local dataset: {result}")
    return result
