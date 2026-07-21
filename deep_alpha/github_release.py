"""GitHub Releases API client using the Python standard library."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .errors import DownloadError

RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


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

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "deep-alpha"}
        token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _request(self, url: str):
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                return urllib.request.urlopen(
                    urllib.request.Request(url, headers=self._headers()), timeout=self.timeout
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

    def download(self, asset: ReleaseAsset, destination: Path) -> None:
        written = 0
        try:
            with self._request(asset.download_url) as response, destination.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    written += len(chunk)
        except DownloadError:
            destination.unlink(missing_ok=True)
            raise
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise DownloadError(f"Download failed: {exc}") from exc
        if written == 0 or (asset.size >= 0 and written != asset.size):
            destination.unlink(missing_ok=True)
            raise DownloadError(f"Downloaded asset size mismatch: expected {asset.size}, got {written}")
