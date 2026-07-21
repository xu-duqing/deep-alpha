"""Stable CSV, JSON and table rendering."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pandas as pd

from .errors import ArgumentError


def render_frame(frame: pd.DataFrame, fmt: str) -> str:
    if fmt == "csv":
        return frame.to_csv(index=False, na_rep="", date_format="%Y-%m-%d")
    if fmt == "json":
        serialized = frame.to_json(orient="records", date_format="iso")
        records = json.loads(serialized or "[]")
        return json.dumps(records, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    if fmt == "table":
        return frame.to_string(index=False, na_rep="") + "\n"
    raise ArgumentError(f"Unsupported output format: {fmt}")


def emit(content: str, output: str | None) -> None:
    if output is None:
        print(content, end="")
        return
    path = Path(output).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            stream.write(content)
            temporary = Path(stream.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
