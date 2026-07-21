"""Terminal-friendly download progress reporting."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TextIO


@dataclass
class DownloadProgress:
    total: int
    stream: TextIO = sys.stderr
    width: int = 30
    _last_percent: int = -1

    def update(self, downloaded: int) -> None:
        if self.total > 0:
            percent = min(100, int(downloaded * 100 / self.total))
            interactive = self.stream.isatty()
            if percent == self._last_percent or (
                not interactive and percent < 100 and percent // 10 == self._last_percent // 10
            ):
                return
            self._last_percent = percent
            filled = int(self.width * percent / 100)
            bar = "#" * filled + "-" * (self.width - filled)
            size = _format_bytes(downloaded)
            total = _format_bytes(self.total)
            ending = "\r" if interactive and percent < 100 else "\n"
            print(f"Downloading [{bar}] {percent:3d}% {size}/{total}", end=ending, file=self.stream, flush=True)
        elif downloaded:
            print(f"Downloaded {_format_bytes(downloaded)}", file=self.stream, flush=True)


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"
