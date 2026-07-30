"""Safe installation of Qlib feature-increment archives."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .config import DAILY_BASIC_FIELDS, DAILY_BASIC_META_FILENAME
from .errors import DownloadError, ProviderError
from .github_release import ReleaseAsset
from .installer import validate_provider

_DATASET = "daily_basic"
_MARKET_FIELDS = frozenset(
    {"open", "high", "low", "close", "volume", "amount", "vwap", "factor"}
)
_MAX_MEMBERS = 100_000
_MAX_EXTRACTED_SIZE = 20 * 1024**3


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _feature_path(relative: str) -> tuple[str, str]:
    path = PurePosixPath(relative)
    if len(path.parts) != 3 or path.parts[0] != "features":
        raise DownloadError(f"Invalid feature path: {relative}")
    suffix = ".day.bin"
    if not path.name.endswith(suffix):
        raise DownloadError(f"Invalid feature filename: {relative}")
    field = path.name[: -len(suffix)]
    if not path.parts[1] or field not in DAILY_BASIC_FIELDS or field in _MARKET_FIELDS:
        raise DownloadError(f"Feature is not allowed: {field}")
    return path.parts[1], field


def _destination(provider: Path, relative: str) -> Path:
    _feature_path(relative)
    features = provider / "features"
    if features.is_symlink():
        raise ProviderError("Provider features directory must not be a symbolic link")
    destination = provider.joinpath(*PurePosixPath(relative).parts)
    current = destination.parent
    while current != provider:
        if current.is_symlink():
            raise ProviderError(f"Provider feature path contains a symbolic link: {current}")
        current = current.parent
    return destination


def _extract(archive: Path, destination: Path) -> Path:
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            if not members or len(members) > _MAX_MEMBERS:
                raise DownloadError("Feature archive is empty or contains too many entries")
            total = 0
            seen: set[PurePosixPath] = set()
            for member in members:
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or not path.parts
                    or path.parts[0] != _DATASET
                ):
                    raise DownloadError(f"Unsafe archive path: {member.name}")
                if path in seen:
                    raise DownloadError(f"Duplicate archive path: {member.name}")
                seen.add(path)
                if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                    raise DownloadError(f"Unsupported archive entry: {member.name}")
                if not (member.isfile() or member.isdir()):
                    raise DownloadError(f"Unsupported archive entry: {member.name}")
                total += member.size
                if total > _MAX_EXTRACTED_SIZE:
                    raise DownloadError("Feature archive extracted size exceeds safety limit")
            for member in members:
                output = destination.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    output.mkdir(parents=True, exist_ok=True)
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    raise DownloadError(f"Cannot read archive entry: {member.name}")
                with source, output.open("xb") as target:
                    shutil.copyfileobj(source, target)
    except DownloadError:
        raise
    except (tarfile.TarError, OSError) as exc:
        raise DownloadError(f"Cannot extract feature archive: {exc}") from exc
    root = destination / _DATASET
    if not root.is_dir():
        raise DownloadError("Feature archive does not contain daily_basic/")
    return root


def _read_checksums(root: Path) -> dict[str, str]:
    path = root / "checksums.sha256"
    if not path.is_file():
        raise DownloadError("Feature archive is missing checksums.sha256")
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if not separator or len(digest) != 64 or relative in result:
            raise DownloadError("Feature archive has an invalid checksum inventory")
        result[relative] = digest
    actual = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
    )
    if sorted(result) != actual:
        raise DownloadError("Feature archive checksum inventory does not match its contents")
    for relative, digest in result.items():
        if _sha256(root / relative) != digest:
            raise DownloadError(f"Feature archive checksum mismatch: {relative}")
    return result


def _load_manifest(root: Path) -> tuple[dict[str, Any], list[str]]:
    _read_checksums(root)
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DownloadError(f"Invalid feature manifest: {exc}") from exc
    expected = {
        "schema_version": 1,
        "package_type": "qlib_feature_increment",
        "dataset": _DATASET,
        "frequency": "day",
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise DownloadError("Unsupported feature manifest")
    fields = manifest.get("features")
    files = manifest.get("files")
    if (
        not isinstance(fields, list)
        or not fields
        or any(field not in DAILY_BASIC_FIELDS for field in fields)
        or not isinstance(files, list)
        or any(not isinstance(path, str) for path in files)
        or len(files) != len(set(files))
    ):
        raise DownloadError("Feature manifest contains invalid fields or files")
    for relative in files:
        _, field = _feature_path(relative)
        if field not in fields:
            raise DownloadError(f"Feature file is not declared by the manifest: {relative}")
    actual = sorted(
        path.relative_to(root).as_posix()
        for path in (root / "features").rglob("*.bin")
        if path.is_file()
    )
    if sorted(files) != actual or manifest.get("file_count") != len(files):
        raise DownloadError("Feature manifest does not match archive contents")
    package_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    allowed_files = {
        "manifest.json",
        "checksums.sha256",
        "reports/build_summary.json",
        *files,
    }
    if package_files != allowed_files:
        raise DownloadError("Feature archive contains unexpected files")
    instruments = {_feature_path(relative)[0] for relative in files}
    if manifest.get("instrument_count") != len(instruments):
        raise DownloadError("Feature manifest instrument count is invalid")
    return manifest, sorted(files)


def _calendar(provider: Path) -> tuple[Path, list[str]]:
    path = provider / "calendars/day.txt"
    values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not values or values != sorted(set(values)):
        raise ProviderError("Provider calendar must be non-empty and strictly increasing")
    return path, values


def _validate_calendar(manifest: dict[str, Any], provider: Path) -> int:
    path, values = _calendar(provider)
    expected = (
        manifest.get("base_calendar_sha256"),
        manifest.get("base_calendar_start"),
        manifest.get("base_calendar_end"),
        manifest.get("base_calendar_count"),
    )
    actual = (_sha256(path), values[0], values[-1], len(values))
    if expected != actual:
        raise ProviderError(
            "Base calendar mismatch: "
            f"package expects {expected[1]} to {expected[2]} ({expected[3]} days), "
            f"but the installed market data has {actual[1]} to {actual[2]} "
            f"({actual[3]} days). Update market data first with 'deep-alpha update', "
            "then retry the daily-basic installation."
        )
    return len(values)


def _validate_binary(path: Path, calendar_count: int) -> None:
    size = path.stat().st_size
    if size < 4 or size % 4:
        raise DownloadError(f"Invalid Qlib feature binary: {path}")
    with path.open("rb") as source:
        start = struct.unpack("<f", source.read(4))[0]
    value_count = size // 4 - 1
    if not math.isfinite(start) or start < 0 or not start.is_integer():
        raise DownloadError(f"Invalid Qlib feature start index: {path}")
    if int(start) + value_count > calendar_count:
        raise DownloadError(f"Qlib feature exceeds provider calendar: {path}")


def feature_metadata_path(provider: Path) -> Path:
    return provider / DAILY_BASIC_META_FILENAME


def read_feature_metadata(provider: Path, required: bool = True) -> dict[str, Any]:
    path = feature_metadata_path(provider)
    if not path.is_file():
        if required:
            raise ProviderError(f"Daily-basic metadata does not exist: {path}")
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProviderError(f"Invalid daily-basic metadata: {exc}") from exc
    if value.get("schema_version") != 1 or value.get("dataset") != _DATASET:
        raise ProviderError("Unsupported daily-basic metadata")
    return value


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def install_feature_archive(
    archive: Path,
    provider: Path,
    repo: str,
    asset: ReleaseAsset,
    replace: bool = False,
) -> dict[str, Any]:
    provider = validate_provider(provider)
    previous = read_feature_metadata(provider, required=False)
    if replace and not previous:
        raise ProviderError("Daily-basic data is not installed; run download without --force")
    if previous and not replace:
        raise ProviderError("Daily-basic data is already installed; run deep-alpha update --dataset daily-basic")

    with tempfile.TemporaryDirectory(prefix="deep-alpha-features-") as temporary:
        root = _extract(archive, Path(temporary))
        manifest, files = _load_manifest(root)
        calendar_count = _validate_calendar(manifest, provider)
        for relative in files:
            _validate_binary(root / relative, calendar_count)

        old_files = previous.get("files", []) if previous else []
        old_checksums = previous.get("file_sha256", {}) if previous else {}
        if not isinstance(old_files, list) or not isinstance(old_checksums, dict):
            raise ProviderError("Invalid installed daily-basic metadata")
        owned = set(old_files)
        for relative in old_files:
            destination = _destination(provider, relative)
            if destination.is_file() and _sha256(destination) != old_checksums.get(relative):
                raise ProviderError(f"Installed daily-basic feature was modified: {relative}")
        conflicts = [
            relative
            for relative in files
            if _destination(provider, relative).exists() and relative not in owned
        ]
        if conflicts:
            raise FileExistsError(f"Feature conflicts detected: {conflicts[:10]}")

        backup = Path(tempfile.mkdtemp(prefix=".daily-basic-backup-", dir=provider))
        metadata_backup = backup / "metadata.json"
        if previous:
            shutil.copy2(feature_metadata_path(provider), metadata_backup)
        try:
            for relative in owned:
                source = _destination(provider, relative)
                if source.is_file():
                    target = backup / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            for relative in files:
                source = root / relative
                destination = _destination(provider, relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary_file = destination.with_name(f".{destination.name}.install.tmp")
                shutil.copy2(source, temporary_file)
                os.replace(temporary_file, destination)
            for relative in owned - set(files):
                _destination(provider, relative).unlink(missing_ok=True)
            metadata = {
                **manifest,
                "repo": repo,
                "release_tag": asset.release_tag,
                "asset_name": asset.name,
                "asset_size": asset.size,
                "download_url": asset.download_url,
                "installed_at": datetime.now(timezone.utc).astimezone().isoformat(),
                "files": files,
                "file_sha256": {
                    relative: _sha256(_destination(provider, relative)) for relative in files
                },
            }
            _write_json_atomic(feature_metadata_path(provider), metadata)
        except BaseException:
            for relative in set(files) | owned:
                _destination(provider, relative).unlink(missing_ok=True)
            for relative in owned:
                source = backup / relative
                if source.is_file():
                    destination = _destination(provider, relative)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
            if metadata_backup.is_file():
                shutil.copy2(metadata_backup, feature_metadata_path(provider))
            else:
                feature_metadata_path(provider).unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(backup, ignore_errors=True)
    return metadata
