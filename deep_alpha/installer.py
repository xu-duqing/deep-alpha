"""Safe Qlib provider installation and metadata handling."""
from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import META_FILENAME
from .errors import DownloadError, ProviderError
from .github_release import ReleaseAsset

_REQUIRED = ("calendars/day.txt", "features", "instruments")
_MAX_MEMBERS = 1_000_000
_MAX_EXTRACTED_SIZE = 100 * 1024**3


def validate_provider(path: Path) -> Path:
    path = path.expanduser().absolute()
    if not path.is_dir():
        raise ProviderError(f"Provider directory does not exist: {path}. Run: deep-alpha download")
    calendar = path / "calendars/day.txt"
    if not calendar.is_file() or calendar.stat().st_size == 0:
        raise ProviderError("Invalid Qlib provider directory: missing calendars/day.txt")
    for name in ("features", "instruments"):
        if not (path / name).is_dir():
            raise ProviderError(f"Invalid Qlib provider directory: missing {name}/")
    return path


def _safe_extract(archive: Path, destination: Path) -> None:
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            if len(members) > _MAX_MEMBERS:
                raise DownloadError("Archive contains too many entries")
            total = 0
            seen: set[Path] = set()
            for member in members:
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise DownloadError(f"Unsafe archive path: {member.name}")
                if member_path in seen:
                    raise DownloadError(f"Duplicate archive path: {member.name}")
                seen.add(member_path)
                if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                    raise DownloadError(f"Unsupported archive entry: {member.name}")
                if not (member.isfile() or member.isdir()):
                    raise DownloadError(f"Unsupported archive entry: {member.name}")
                total += member.size
                if total > _MAX_EXTRACTED_SIZE:
                    raise DownloadError("Archive extracted size exceeds safety limit")
            # Members were validated above. Avoid the Python 3.12-only
            # extraction filter so Python 3.10 and 3.11 remain supported.
            for member in members:
                tar.extract(member, destination)
    except DownloadError:
        raise
    except (tarfile.TarError, OSError) as exc:
        raise DownloadError(f"Cannot extract archive: {exc}") from exc


def _provider_candidates(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for calendar in root.rglob("calendars/day.txt"):
        candidate = calendar.parent.parent
        if all((candidate / item).exists() for item in _REQUIRED):
            candidates.append(candidate)
    return sorted(set(candidates))


def metadata_path(provider: Path) -> Path:
    return provider / META_FILENAME


def read_metadata(provider: Path, required: bool = True) -> dict[str, Any]:
    path = metadata_path(provider)
    if not path.is_file():
        if required:
            raise ProviderError(f"Dataset metadata does not exist: {path}")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProviderError(f"Invalid dataset metadata: {exc}") from exc
    if data.get("schema_version") != 1:
        raise ProviderError("Unsupported dataset metadata schema_version")
    return data


def _write_metadata(provider: Path, repo: str, asset: ReleaseAsset) -> None:
    payload = {
        "repo": repo,
        "release_tag": asset.release_tag,
        "asset_name": asset.name,
        "asset_size": asset.size,
        "download_url": asset.download_url,
        "installed_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "dataset": "cn_stock_1d",
        "provider_uri": str(provider),
        "schema_version": 1,
    }
    metadata_path(provider).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def install_archive(archive: Path, target: Path, repo: str, asset: ReleaseAsset, force: bool) -> None:
    target = target.expanduser().absolute()
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise ProviderError(f"Refusing to replace symbolic link: {target}")
    if target.exists() and not force:
        raise ProviderError(f"Target directory already exists: {target}. Use --force to replace it.")
    if target.exists() and not target.is_dir():
        raise ProviderError(f"Target path is not a directory: {target}")

    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp.", dir=parent))
    backup: Path | None = None
    try:
        extracted = staging / "extracted"
        extracted.mkdir()
        _safe_extract(archive, extracted)
        candidates = _provider_candidates(extracted)
        if len(candidates) != 1:
            raise DownloadError(f"Expected exactly one Qlib provider root, found {len(candidates)}")
        provider = staging / "provider"
        os.replace(candidates[0], provider)
        validate_provider(provider)
        _write_metadata(provider, repo, asset)
        if target.exists():
            backup = Path(tempfile.mkdtemp(prefix=f".{target.name}.backup.", dir=parent))
            backup.rmdir()
            os.replace(target, backup)
        try:
            os.replace(provider, target)
        except BaseException:
            if backup is not None and not target.exists():
                os.replace(backup, target)
            raise
        if backup is not None:
            shutil.rmtree(backup)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
