"""GitHub Releases API client using the Python standard library."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import DownloadError

RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}
DOWNLOAD_PROXY = "https://ghfast.top/"
CHUNK_SIZE = 8 * 1024 * 1024
CHUNK_ATTEMPTS = 5


@dataclass(frozen=True)
class ReleaseAsset:
    release_tag: str
    name: str
    size: int
    download_url: str


class GitHubReleaseClient:
    def __init__(self, repo: str, timeout: int = 300, retries: int = 3):
        if repo.count("/") != 1 or any(not part for part in repo.split("/")):
            raise DownloadError(f"Invalid GitHub repository: {repo}")
        self.repo = repo
        self.timeout = timeout
        self.retries = retries

    def _headers(self, authenticated: bool = True, range_start: int | None = None) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "deep-alpha"}
        token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        if token and authenticated:
            headers["Authorization"] = f"Bearer {token}"
        if range_start is not None:
            headers["Range"] = f"bytes={range_start}-"
        return headers

    def _request(self, url: str, authenticated: bool = True, range_start: int | None = None):
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                return urllib.request.urlopen(
                    urllib.request.Request(url, headers=self._headers(authenticated, range_start)), timeout=self.timeout
                )
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in RETRYABLE:
                    break
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
            if attempt + 1 < self.retries:
                time.sleep(2**attempt)
        raise DownloadError(f"GitHub request failed: {last_error}") from last_error

    def get_asset(self, tag: str | None, asset_name: str) -> ReleaseAsset:
        repo = "/".join(urllib.parse.quote(part, safe="") for part in self.repo.split("/"))
        if tag:
            endpoint = f"releases/tags/{urllib.parse.quote(tag, safe='')}"
        else:
            endpoint = "releases/latest"
        try:
            with self._request(f"https://api.github.com/repos/{repo}/{endpoint}") as response:
                payload = json.load(response)
        except DownloadError:
            raise
        except (ValueError, OSError) as exc:
            raise DownloadError(f"Invalid GitHub release response: {exc}") from exc
        release_tag = payload.get("tag_name")
        if not isinstance(release_tag, str) or not release_tag:
            raise DownloadError("Invalid GitHub release response: missing tag_name")
        matches = [item for item in payload.get("assets", []) if item.get("name") == asset_name]
        if len(matches) != 1:
            if not matches:
                raise DownloadError(f"Asset {asset_name} not found in release {release_tag}")
            raise DownloadError(f"Multiple assets named {asset_name} in release {release_tag}")
        item = matches[0]
        try:
            size = int(item["size"])
            url = str(item["browser_download_url"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DownloadError("Invalid GitHub asset metadata") from exc
        return ReleaseAsset(release_tag, asset_name, size, url)

    def download(
        self,
        asset: ReleaseAsset,
        destination: Path,
        progress: Callable[[int], None] | None = None,
    ) -> None:
        written = 0
        download_url = f"{DOWNLOAD_PROXY}{asset.download_url}"
        try:
            with destination.open("wb") as output:
                # Download in ranges and resume mid-chunk on truncation: the
                # proxy often drops long-lived connections on large assets.
                while asset.size < 0 or written < asset.size:
                    end = written + CHUNK_SIZE - 1
                    if asset.size >= 0:
                        end = min(end, asset.size - 1)
                    written = self._download_range(download_url, written, end, output, progress)
        except DownloadError:
            destination.unlink(missing_ok=True)
            raise
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise DownloadError(f"Download failed: {exc}") from exc
        if written == 0 or (asset.size >= 0 and written != asset.size):
            destination.unlink(missing_ok=True)
            raise DownloadError(f"Downloaded asset size mismatch: expected {asset.size}, got {written}")

    def _download_range(
        self,
        url: str,
        start: int,
        end: int,
        output,
        progress: Callable[[int], None] | None,
    ) -> int:
        """Stream bytes [start, end] into output, resuming on short reads.

        Returns the new file offset (end + 1 on success)."""
        written = start
        for _ in range(CHUNK_ATTEMPTS):
            if written > end:
                break
            try:
                # The proxy is a third party: never forward the user's GitHub token.
                with self._request(url, authenticated=False, range_start=written) as response:
                    while written <= end:
                        chunk = response.read(min(1024 * 1024, end - written + 1))
                        if not chunk:
                            break  # truncated; resume from current offset
                        output.write(chunk)
                        written += len(chunk)
                        if progress is not None:
                            progress(written)
            except DownloadError:
                raise
            except (OSError, TimeoutError):
                continue  # connection dropped mid-chunk; resume from current offset
        if written <= end:
            raise DownloadError(
                f"Download stalled at {written} of {end + 1} bytes after {CHUNK_ATTEMPTS} attempts"
            )
        return written
