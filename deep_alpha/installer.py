"""Safe Qlib provider installation and metadata handling."""
from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
import uuid
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
    if data.get("schema_version") not in (1, 2):
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
    _write_json_atomic(metadata_path(provider), payload)


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, path)


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


def _paired_journal_path(target: Path) -> Path:
    return target.parent / f".{target.name}.paired-install-journal.json"


def recover_paired_install(target: Path) -> bool:
    target = target.expanduser().absolute()
    journal_path = _paired_journal_path(target)
    if not journal_path.is_file():
        return False
    try:
        backup = Path(json.loads(journal_path.read_text(encoding="utf-8"))["backup"]).absolute()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ProviderError(f"Invalid paired install journal: {exc}") from exc
    if backup.parent != target.parent or not backup.name.startswith(f".{target.name}.backup."):
        raise ProviderError("Unsafe paired install journal backup path")
    if backup.exists():
        if target.exists():
            shutil.rmtree(target)
        os.replace(backup, target)
    elif not target.exists():
        raise ProviderError("Paired install recovery backup is missing")
    journal_path.unlink()
    return True


def install_paired_archives(
    market_archive: Path, daily_archive: Path, target: Path, repo: str,
    market_asset: ReleaseAsset, daily_asset: ReleaseAsset,
) -> None:
    """Stage market, merge daily-basic, then replace provider as one unit."""
    if market_asset.release_tag != daily_asset.release_tag:
        raise DownloadError("Paired full assets have different Release tags")
    target = target.expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    recover_paired_install(target)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.paired-stage.", dir=target.parent))
    backup: Path | None = None
    journal_path = _paired_journal_path(target)
    try:
        extracted = staging / "extracted"
        extracted.mkdir()
        _safe_extract(market_archive, extracted)
        candidates = _provider_candidates(extracted)
        if len(candidates) != 1:
            raise DownloadError(f"Expected exactly one Qlib provider root, found {len(candidates)}")
        provider = staging / "provider"
        os.replace(candidates[0], provider)
        validate_provider(provider)
        from .feature_increment import install_feature_archive
        install_feature_archive(daily_archive, provider, repo, daily_asset, replace=False)
        _write_metadata(provider, repo, market_asset)
        market_meta = read_metadata(provider)
        daily_meta_path = provider / ".daily_basic_meta.json"
        daily_meta = json.loads(daily_meta_path.read_text(encoding="utf-8"))
        calendar = [line.strip() for line in (provider / "calendars/day.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
        committed = {
            "schema_version": 2,
            "paired_release_tag": market_asset.release_tag,
            "transaction_id": str(uuid.uuid4()),
            "state": "committed",
            "calendar_count": len(calendar),
            "calendar_end": calendar[-1],
            "update_mode": "full",
        }
        market_meta.update(committed)
        _write_json_atomic(metadata_path(provider), market_meta)
        daily_meta.update(committed)
        _write_json_atomic(daily_meta_path, daily_meta)
        if target.exists():
            backup = Path(tempfile.mkdtemp(prefix=f".{target.name}.backup.", dir=target.parent))
            backup.rmdir()
            _write_json_atomic(journal_path, {"backup": str(backup)})
            os.replace(target, backup)
        try:
            os.replace(provider, target)
        except BaseException:
            if backup is not None and backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
        journal_path.unlink(missing_ok=True)
        if backup is not None:
            shutil.rmtree(backup)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
