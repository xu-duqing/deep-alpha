import argparse
import hashlib
import json
import os
import shutil
import tarfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, cast

from .config import DAILY_BASIC_ASSET, DAILY_BASIC_META_FILENAME, DEFAULT_ASSET, META_FILENAME
from .temporal_delta_manifest import is_sha256, read_calendar, validate_manifest, write_json_atomic

ROOT_NAMES = {"market": "market_delta", "daily_basic": "daily_basic_delta"}
MAX_MEMBERS = 200000
MAX_MEMBER_SIZE = 1024 * 1024 * 1024
MAX_TOTAL_SIZE = 20 * 1024 * 1024 * 1024
IO_WORKERS = min(32, max(4, (os.cpu_count() or 1) * 4))


@dataclass(frozen=True)
class MemoryPackage:
    manifest: Mapping[str, object]
    files: Mapping[str, bytes]


@dataclass(frozen=True)
class PlanItem:
    record: Mapping[str, Any]
    destination: Path
    # A view into MemoryPackage's shared aggregate payload for schema v2.
    payload: memoryview


@dataclass(frozen=True)
class TransactionPlan:
    market: tuple[PlanItem, ...]
    daily: tuple[PlanItem, ...]
    append_files: Mapping[str, int]
    created_files: tuple[str, ...]

def safe_extract(archive_path: Path, destination: Path, dataset: str) -> Path:
    """Compatibility helper for callers which explicitly need an extracted tree.

    Delta apply itself uses :func:`load_archive`, and never materializes payloads.
    """
    package = load_archive(archive_path, dataset)
    root = destination / ROOT_NAMES[dataset]
    for relative, data in package.files.items():
        output = root.joinpath(*PurePosixPath(relative).parts)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
    return root


def _parse_checksums(data: bytes) -> dict[str, str]:
    checksums: dict[str, str] = {}
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("checksums.sha256 is not UTF-8") from exc
    for line in lines:
        digest, separator, relative_path = line.partition("  ")
        path = PurePosixPath(relative_path)
        if (not separator or not is_sha256(digest) or relative_path in checksums or
                path.is_absolute() or ".." in path.parts or "." in path.parts or not path.parts):
            raise ValueError(f"invalid checksum line: {line!r}")
        checksums[relative_path] = digest
    return checksums


def load_archive(archive_path: Path, dataset: str) -> MemoryPackage:
    """Validate and load a gzip tar in one streaming pass.

    Payload bytes remain in memory; no per-payload temporary filesystem entries are
    created.  Hashes are computed while each tar member is read.
    """
    expected_root = ROOT_NAMES[dataset]
    files: dict[str, bytes] = {}
    hashes: dict[str, str] = {}
    seen: set[str] = set()
    total_size = 0
    count = 0
    try:
        archive = tarfile.open(archive_path, "r|gz")
    except (tarfile.TarError, OSError) as exc:
        raise ValueError(f"unable to open delta archive: {archive_path}") from exc
    with archive:
        for member in archive:
            count += 1
            if count > MAX_MEMBERS:
                raise ValueError("delta archive is empty or has too many members")
            path = PurePosixPath(member.name)
            normalized = path.as_posix().rstrip("/")
            if normalized in seen:
                raise ValueError(f"duplicate archive member: {normalized}")
            seen.add(normalized)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != expected_root:
                raise ValueError(f"unsafe archive path: {member.name}")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ValueError(f"unsupported archive member: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"unsupported archive member: {member.name}")
            if member.isdir():
                continue
            if member.size > MAX_MEMBER_SIZE:
                raise ValueError(f"archive member too large: {member.name}")
            total_size += member.size
            if total_size > MAX_TOTAL_SIZE:
                raise ValueError("delta archive expands beyond 20 GiB")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"unable to read archive member: {member.name}")
            relative = PurePosixPath(*path.parts[1:]).as_posix()
            if not relative:
                raise ValueError(f"archive root must be a directory: {member.name}")
            # Hash every member in the strict inventory. Retain only supported
            # package members; v2 therefore keeps one aggregate payload.
            retain = (relative in {"manifest.json", "checksums.sha256", "payloads.bin",
                                   "calendars/day.append.txt"} or
                      relative.startswith("instruments/") or relative.startswith("features/"))
            digest = hashlib.sha256()
            retained = bytearray() if retain else None
            read_size = 0
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                read_size += len(chunk)
                digest.update(chunk)
                if retained is not None:
                    retained.extend(chunk)
            if read_size != member.size:
                raise ValueError(f"truncated archive member: {member.name}")
            if retained is not None:
                files[relative] = bytes(retained)
            hashes[relative] = digest.hexdigest()
    if not count or not files:
        raise ValueError("delta archive is empty or has too many members")
    checksum_data = files.get("checksums.sha256")
    if checksum_data is None:
        raise ValueError("checksums.sha256 is missing")
    checksums = _parse_checksums(checksum_data)
    actual = set(hashes) - {"checksums.sha256"}
    if set(checksums) != actual:
        raise ValueError("checksum inventory does not match package contents")
    for relative, expected in checksums.items():
        if hashes[relative] != expected:
            raise ValueError(f"checksum mismatch: {relative}")
    manifest_data = files.get("manifest.json")
    if manifest_data is None:
        raise ValueError("delta manifest is missing")
    try:
        manifest = json.loads(manifest_data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("delta manifest is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("delta manifest must be an object")
    validate_manifest(manifest, dataset)
    allowed = {"manifest.json", "checksums.sha256"}
    allowed.update(record["payload_path"] for record in manifest["files"])
    if dataset == "market":
        allowed.add("calendars/day.append.txt")
        allowed.update(path for path in files if path.startswith("instruments/"))
        if "calendars/day.append.txt" not in files or not any(path.startswith("instruments/") for path in files):
            raise ValueError("market delta calendar or instruments are missing")
        expected_append = "".join(f"{value}\n" for value in manifest["calendar_append"]).encode("utf-8")
        if files["calendars/day.append.txt"] != expected_append:
            raise ValueError("calendar append payload does not match manifest")
    if set(files) != allowed:
        raise ValueError(f"unexpected package paths: {sorted(set(files) - allowed)[:5]}")
    if set(hashes) != allowed:
        raise ValueError(f"unexpected package paths: {sorted(set(hashes) - allowed)[:5]}")
    if manifest["schema_version"] == 2:
        payload_size = len(files["payloads.bin"])
        for record in manifest["files"]:
            if record["payload_offset"] + record["payload_size"] > payload_size:
                raise ValueError(f"aggregate payload range out of bounds: {record['path']}")
    return MemoryPackage(manifest=manifest, files=files)


def load_package(root: Path, dataset: str) -> Mapping[str, object]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("delta manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(manifest, dataset)
    allowed = {"manifest.json", "checksums.sha256"}
    allowed.update(record["payload_path"] for record in manifest["files"])
    if dataset == "market":
        allowed.add("calendars/day.append.txt")
        allowed.update(path.relative_to(root).as_posix() for path in (root / "instruments").rglob("*") if path.is_file())
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual != allowed:
        raise ValueError(f"unexpected package paths: {sorted(actual - allowed)[:5]}")
    if manifest["schema_version"] == 2:
        payload_size = (root / "payloads.bin").stat().st_size
        for record in manifest["files"]:
            if record["payload_offset"] + record["payload_size"] > payload_size:
                raise ValueError(f"aggregate payload range out of bounds: {record['path']}")
    return manifest


def safe_target_path(target: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f"unsafe target path: {relative_path}")
    if not (relative.parts[0] == "features" or relative_path == "calendars/day.txt" or relative.parts[0] == "instruments"):
        raise ValueError(f"target path is not allowlisted: {relative_path}")
    target = target.resolve()
    destination = target.joinpath(*relative.parts)
    current = destination.parent
    while current != target:
        if current.is_symlink():
            raise ValueError(f"target path contains symlink: {current}")
        current = current.parent
    resolved_parent = destination.parent.resolve()
    if resolved_parent != target and target not in resolved_parent.parents:
        raise ValueError(f"target path escapes provider: {relative_path}")
    return destination


def paired_contract(market: Mapping[str, object], daily: Mapping[str, object]) -> None:
    keys = ("base_release_tag", "release_tag", "calendar_before_sha256", "calendar_after_sha256",
            "calendar_before_count", "calendar_after_count", "calendar_append")
    for key in keys:
        if market.get(key) != daily.get(key):
            raise ValueError(f"paired delta {key} mismatch")


def metadata_path(target: Path) -> Path:
    return target / META_FILENAME


def daily_metadata_path(target: Path) -> Path:
    return target / DAILY_BASIC_META_FILENAME


def journal_path(target: Path) -> Path:
    return target / ".temporal_delta" / "install-journal.json"


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"metadata is not an object: {path}")
    return value


def trusted_paired_release(market: Mapping[str, object], daily: Mapping[str, object], repo: str) -> str | None:
    """Return the trusted local release represented by committed paired metadata v2."""
    if market.get("schema_version") != 2 or daily.get("schema_version") != 2:
        return None
    tag = market.get("paired_release_tag")
    transaction_id = market.get("transaction_id")
    if not isinstance(tag, str) or not tag or market.get("release_tag") != tag:
        return None
    if not isinstance(transaction_id, str) or not transaction_id:
        return None
    if any(meta.get("repo") != repo or meta.get("state") != "committed" for meta in (market, daily)):
        return None
    if daily.get("paired_release_tag") != tag or daily.get("release_tag") != tag:
        return None
    if daily.get("transaction_id") != transaction_id:
        return None
    count = market.get("calendar_count")
    end = market.get("calendar_end")
    if not isinstance(count, int) or count < 1 or not isinstance(end, str):
        return None
    if daily.get("calendar_count") != count or daily.get("calendar_end") != end:
        return None
    return tag


def upgrade_legacy_paired_metadata(target: Path, repo: str) -> str | None:
    """Trust and mark an already paired v1 install without touching Provider data."""
    market = _read_optional_json(metadata_path(target)) or {}
    daily = _read_optional_json(daily_metadata_path(target)) or {}
    trusted = trusted_paired_release(market, daily, repo)
    if trusted is not None:
        return trusted
    if market.get("schema_version") != 1 or daily.get("schema_version") != 1:
        return None
    if market.get("repo") != repo or daily.get("repo") != repo:
        return None
    market_tag = market.get("paired_release_tag", market.get("release_tag"))
    daily_tag = daily.get("paired_release_tag", daily.get("release_tag"))
    if not isinstance(market_tag, str) or not market_tag or market_tag != daily_tag:
        return None
    if market.get("release_tag") != market_tag or daily.get("release_tag") != market_tag:
        return None
    calendar = read_calendar(target / "calendars/day.txt")
    if not calendar:
        return None
    committed = {
        "schema_version": 2,
        "paired_release_tag": market_tag,
        "transaction_id": str(uuid.uuid4()),
        "state": "committed",
        "calendar_count": len(calendar),
        "calendar_end": calendar[-1],
    }
    write_json_atomic(daily_metadata_path(target), {**daily, **committed})
    write_json_atomic(metadata_path(target), {**market, **committed})
    return market_tag


def _restore_metadata(path: Path, value: object) -> None:
    if value is None:
        path.unlink(missing_ok=True)
    elif isinstance(value, Mapping):
        write_json_atomic(path, value)
    else:
        raise ValueError("invalid previous metadata in journal")


def recover(target: Path) -> bool:
    target = target.resolve()
    journal_file = journal_path(target)
    if not journal_file.is_file():
        return False
    journal = json.loads(journal_file.read_text(encoding="utf-8"))
    if journal.get("schema_version") != 2:
        raise ValueError("unsupported temporal delta journal")
    append_files = journal.get("append_files")
    created_files = journal.get("created_files")
    if not isinstance(append_files, dict) or not isinstance(created_files, list):
        raise ValueError("invalid temporal delta journal")
    for relative_path, old_size in append_files.items():
        if not isinstance(relative_path, str) or not isinstance(old_size, int) or old_size < 0:
            raise ValueError("invalid append entry in journal")
        destination = safe_target_path(target, relative_path)
        if destination.is_file():
            os.truncate(destination, old_size)
    for relative_path in created_files:
        if not isinstance(relative_path, str):
            raise ValueError("invalid create entry in journal")
        safe_target_path(target, relative_path).unlink(missing_ok=True)
    calendar_old_size = journal.get("calendar_old_size")
    if not isinstance(calendar_old_size, int) or calendar_old_size < 0:
        raise ValueError("invalid calendar size in journal")
    calendar = safe_target_path(target, "calendars/day.txt")
    if calendar.is_file():
        os.truncate(calendar, calendar_old_size)
    backup_value = journal.get("instruments_backup_dir")
    backup = Path(backup_value).resolve() if isinstance(backup_value, str) else None
    temporal_root = (target / ".temporal_delta").resolve()
    if backup is None or temporal_root not in backup.parents:
        raise ValueError("unsafe instruments backup path")
    if backup.is_dir():
        instruments = target / "instruments"
        replacement = target / f".instruments-recovery-{journal.get('transaction_id', os.getpid())}"
        shutil.rmtree(replacement, ignore_errors=True)
        shutil.copytree(backup, replacement)
        displaced = target / f".instruments-displaced-{journal.get('transaction_id', os.getpid())}"
        shutil.rmtree(displaced, ignore_errors=True)
        if instruments.exists():
            os.replace(instruments, displaced)
        os.replace(replacement, instruments)
        shutil.rmtree(displaced, ignore_errors=True)
    _restore_metadata(metadata_path(target), journal.get("previous_market_metadata"))
    _restore_metadata(daily_metadata_path(target), journal.get("previous_daily_metadata"))
    journal_file.unlink()
    shutil.rmtree(backup, ignore_errors=True)
    return True


def _cached_destination(target: Path, relative_path: str, parents: dict[str, Path]) -> Path:
    relative = PurePosixPath(relative_path)
    if (relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 3 or
            relative.parts[0] != "features"):
        raise ValueError(f"unsafe target path: {relative_path}")
    parent_key = "/".join(relative.parts[:-1])
    parent = parents.get(parent_key)
    if parent is None:
        # The expensive resolve/symlink walk happens once per symbol, not per field.
        probe = safe_target_path(target, f"{parent_key}/.__delta_path_probe__")
        parent = probe.parent
        parents[parent_key] = parent
    return parent / relative.parts[-1]


def _check_plan_item(item: PlanItem) -> tuple[str, int | None]:
    record = item.record
    relative_path = cast(str, record["path"])
    if record["operation"] == "append":
        try:
            if item.destination.is_symlink():
                raise ValueError(f"target file is a symlink: {relative_path}")
            size = item.destination.stat().st_size
        except (FileNotFoundError, NotADirectoryError):
            size = -1
        old_size = cast(int, record["expected_old_size"])
        if not item.destination.is_file() or size != old_size:
            raise ValueError(f"old size mismatch: {relative_path}")
        return relative_path, old_size
    if item.destination.exists():
        raise ValueError(f"create target already exists: {relative_path}")
    return relative_path, None


def build_transaction_plan(target: Path, market_package: MemoryPackage,
                           daily_package: MemoryPackage) -> TransactionPlan:
    parents: dict[str, Path] = {}
    seen: set[str] = set()

    def build(package: MemoryPackage) -> tuple[PlanItem, ...]:
        result: list[PlanItem] = []
        for record in cast(list[dict[str, Any]], package.manifest["files"]):
            relative_path = cast(str, record["path"])
            if relative_path in seen:
                raise ValueError(f"paired manifests target the same file: {relative_path}")
            seen.add(relative_path)
            payload_path = cast(str, record["payload_path"])
            payload_data = package.files.get(payload_path)
            if payload_data is None:
                raise ValueError(f"delta payload is missing: {payload_path}")
            expected_payload_size = (cast(int, record["new_size"]) -
                                     cast(int, record.get("expected_old_size") or 0))
            if package.manifest["schema_version"] == 2:
                offset = cast(int, record["payload_offset"])
                size = cast(int, record["payload_size"])
                payload = memoryview(payload_data)[offset:offset + size]
            else:
                payload = memoryview(payload_data)
            if len(payload) != expected_payload_size:
                raise ValueError(f"payload size mismatch: {payload_path}")
            result.append(PlanItem(record, _cached_destination(target, relative_path, parents), payload))
        return tuple(result)

    market_items = build(market_package)
    daily_items = build(daily_package)
    with ThreadPoolExecutor(max_workers=IO_WORKERS) as executor:
        checked = list(executor.map(_check_plan_item, market_items + daily_items))
    append_files = {path: size for path, size in checked if size is not None}
    created_files = tuple(path for path, size in checked if size is None)
    return TransactionPlan(market_items, daily_items, append_files, created_files)


def assert_preconditions(target: Path, market: Mapping[str, object], daily: Mapping[str, object],
                         plan: TransactionPlan | None = None) -> tuple[dict[str, int], list[str], dict[str, Any], dict[str, Any]]:
    metadata = _read_optional_json(metadata_path(target)) or {}
    daily_metadata = _read_optional_json(daily_metadata_path(target)) or {}
    repo = metadata.get("repo")
    if not isinstance(repo, str) or trusted_paired_release(metadata, daily_metadata, repo) != market["base_release_tag"]:
        raise ValueError("installed paired metadata is not a trusted committed delta base")
    calendar_path = target / "calendars/day.txt"
    calendar = read_calendar(calendar_path)
    if (len(calendar) != metadata["calendar_count"] or calendar[-1] != metadata["calendar_end"] or
            len(calendar) != market["calendar_before_count"] or
            hashlib.sha256(calendar_path.read_bytes()).hexdigest() != market["calendar_before_sha256"]):
        raise ValueError("installed calendar does not match trusted metadata/delta base")
    appended = cast(list[str], market["calendar_append"])
    if appended and appended[0] <= calendar[-1]:
        raise ValueError("calendar append does not follow installed calendar")
    if plan is None:
        # Backwards-compatible slow path for direct callers.
        append_files: dict[str, int] = {}
        created_files: list[str] = []
        seen: set[str] = set()
        for manifest in (market, daily):
            for record in cast(list[dict[str, Any]], manifest["files"]):
                relative_path = record["path"]
                if relative_path in seen:
                    raise ValueError(f"paired manifests target the same file: {relative_path}")
                seen.add(relative_path)
                path = safe_target_path(target, relative_path)
                if record["operation"] == "append":
                    old_size = record["expected_old_size"]
                    if not path.is_file() or path.stat().st_size != old_size:
                        raise ValueError(f"old size mismatch: {relative_path}")
                    append_files[relative_path] = old_size
                elif path.exists():
                    raise ValueError(f"create target already exists: {relative_path}")
                else:
                    created_files.append(relative_path)
    else:
        append_files = dict(plan.append_files)
        created_files = list(plan.created_files)
    return append_files, created_files, metadata, daily_metadata


def _write_journal(path: Path, value: Mapping[str, object]) -> None:
    write_json_atomic(path, value)
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _apply_item(item: PlanItem) -> None:
    record, destination, payload = item.record, item.destination, item.payload
    destination.parent.mkdir(parents=True, exist_ok=True)
    if record["operation"] == "append":
        with destination.open("ab", buffering=0) as stream:
            stream.write(payload)
    else:
        temporary_file = destination.with_name(f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary_file.open("xb", buffering=0) as stream:
                stream.write(payload)
            os.replace(temporary_file, destination)
        finally:
            temporary_file.unlink(missing_ok=True)
    if destination.stat().st_size != record["new_size"]:
        raise ValueError(f"new file size verification failed: {record['path']}")


def _apply_files(items: tuple[PlanItem, ...]) -> None:
    with ThreadPoolExecutor(max_workers=IO_WORKERS) as executor:
        # list is intentional: propagate worker exceptions before commit.
        list(executor.map(_apply_item, items))


def _write_instruments(files: Mapping[str, bytes], destination: Path) -> None:
    destination.mkdir(parents=True)
    for relative, data in files.items():
        if not relative.startswith("instruments/"):
            continue
        path = destination.joinpath(*PurePosixPath(relative).parts[1:])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def apply_paired_archives(market_archive: Path, daily_archive: Path, target: Path,
                          expected_release_tag: str | None = None) -> Mapping[str, object]:
    started = time.monotonic()
    timings: dict[str, float] = {}
    target = target.resolve()
    if journal_path(target).exists():
        raise RuntimeError("unfinished temporal delta journal exists; recover first")
    phase = time.monotonic()
    # Archives are independent, and gzip decompression releases the GIL.
    with ThreadPoolExecutor(max_workers=2) as executor:
        market_future = executor.submit(load_archive, market_archive, "market")
        daily_future = executor.submit(load_archive, daily_archive, "daily_basic")
        market_package = market_future.result()
        daily_package = daily_future.result()
    market = market_package.manifest
    daily = daily_package.manifest
    timings["extract_check"] = time.monotonic() - phase
    phase = time.monotonic()
    paired_contract(market, daily)
    if expected_release_tag is not None and market["release_tag"] != expected_release_tag:
        raise ValueError("delta manifest release_tag does not match API Release tag")
    plan = build_transaction_plan(target, market_package, daily_package)
    append_files, created_files, previous_metadata, previous_daily_metadata = assert_preconditions(
        target, market, daily, plan)
    timings["preflight"] = time.monotonic() - phase

    transaction_id = str(uuid.uuid4())
    metadata_dir = target / ".temporal_delta"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    backup = metadata_dir / f"instruments-{transaction_id}"
    phase = time.monotonic()
    shutil.copytree(target / "instruments", backup)
    calendar_path = target / "calendars/day.txt"
    journal: dict[str, object] = {
        "schema_version": 2, "transaction_id": transaction_id,
        "base_release_tag": market["base_release_tag"], "target_release_tag": market["release_tag"],
        "phase": "prepared", "append_files": append_files, "created_files": created_files,
        "calendar_old_size": calendar_path.stat().st_size,
        "instruments_backup_dir": str(backup),
        "previous_market_metadata": previous_metadata,
        "previous_daily_metadata": previous_daily_metadata,
    }
    _write_journal(journal_path(target), journal)
    timings["journal"] = time.monotonic() - phase
    try:
        phase = time.monotonic()
        _apply_files(plan.market)
        timings["market_append"] = time.monotonic() - phase
        phase = time.monotonic()
        _apply_files(plan.daily)
        timings["daily_append"] = time.monotonic() - phase
        phase = time.monotonic()
        appended = cast(list[str], market["calendar_append"])
        with calendar_path.open("ab") as stream:
            stream.write(("".join(f"{value}\n" for value in appended)).encode("utf-8"))
        calendar = read_calendar(calendar_path)
        if (len(calendar) != market["calendar_after_count"] or calendar[-1] != appended[-1] or
                hashlib.sha256(calendar_path.read_bytes()).hexdigest() != market["calendar_after_sha256"]):
            raise ValueError("delta target calendar count/end mismatch")
        replacement = target / f".instruments-{transaction_id}.tmp"
        _write_instruments(market_package.files, replacement)
        displaced = target / f".instruments-{transaction_id}.old"
        os.replace(target / "instruments", displaced)
        os.replace(replacement, target / "instruments")
        shutil.rmtree(displaced)
        timings["calendar_instruments"] = time.monotonic() - phase

        phase = time.monotonic()
        installed_at = datetime.now(timezone.utc).astimezone().isoformat()
        common = {
            "schema_version": 2, "repo": previous_metadata["repo"],
            "installed_at": installed_at, "release_tag": market["release_tag"],
            "paired_release_tag": market["release_tag"], "transaction_id": transaction_id,
            "state": "committed", "calendar_count": market["calendar_after_count"],
            "calendar_end": calendar[-1], "update_mode": "temporal_delta",
        }
        result = {**previous_metadata, **common, "dataset": "cn_stock_1d", "asset_name": DEFAULT_ASSET,
                  "market_release_tag": market["release_tag"], "daily_basic_release_tag": daily["release_tag"]}
        daily_result = {**previous_daily_metadata, **common, "dataset": "daily_basic", "asset_name": DAILY_BASIC_ASSET}
        write_json_atomic(metadata_path(target), result)
        write_json_atomic(daily_metadata_path(target), daily_result)
        journal["phase"] = "metadata_written"
        _write_journal(journal_path(target), journal)
        timings["metadata_commit"] = time.monotonic() - phase
    except BaseException:
        recover(target)
        raise
    journal_path(target).unlink()
    shutil.rmtree(backup, ignore_errors=True)
    timings["total"] = time.monotonic() - started
    result["delta_stats"] = {
        "touched": len(append_files) + len(created_files),
        "append": len(append_files), "create": len(created_files), "timings": timings,
    }
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply paired market and daily_basic temporal deltas")
    parser.add_argument("--market-archive", type=Path)
    parser.add_argument("--daily-basic-archive", type=Path)
    parser.add_argument("--target-dir", type=Path, required=True)
    parser.add_argument("--recover", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.recover:
        print("recovered" if recover(args.target_dir) else "no recovery needed")
        return
    if not args.market_archive or not args.daily_basic_archive:
        raise SystemExit("both --market-archive and --daily-basic-archive are required")
    result = apply_paired_archives(args.market_archive, args.daily_basic_archive, args.target_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
