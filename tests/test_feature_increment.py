import hashlib
import io
import json
import struct
import tarfile
from pathlib import Path

import pytest

from deep_alpha.feature_increment import install_feature_archive, read_feature_metadata
from deep_alpha.github_release import ReleaseAsset


def make_provider(path: Path) -> None:
    (path / "calendars").mkdir(parents=True)
    (path / "features" / "sh600519").mkdir(parents=True)
    (path / "instruments").mkdir()
    (path / "calendars/day.txt").write_text("2024-01-02\n2024-01-03\n")
    (path / "instruments/all.txt").write_text(
        "SH600519\t2024-01-02\t2024-01-03\n"
    )
    (path / "features/sh600519/close.day.bin").write_bytes(b"market")


def make_archive(path: Path, provider: Path, value: float = 8.0) -> None:
    feature_name = "features/sh600519/pe.day.bin"
    feature = struct.pack("<3f", 0.0, value, value + 1)
    calendar = provider / "calendars/day.txt"
    manifest = {
        "schema_version": 1,
        "package_type": "qlib_feature_increment",
        "dataset": "daily_basic",
        "frequency": "day",
        "base_calendar_sha256": hashlib.sha256(calendar.read_bytes()).hexdigest(),
        "base_calendar_start": "2024-01-02",
        "base_calendar_end": "2024-01-03",
        "base_calendar_count": 2,
        "features": ["pe"],
        "instrument_count": 1,
        "file_count": 1,
        "files": [feature_name],
    }
    files = {
        "daily_basic/manifest.json": json.dumps(manifest).encode(),
        f"daily_basic/{feature_name}": feature,
        "daily_basic/reports/build_summary.json": b"{}",
    }
    checksums = "".join(
        f"{hashlib.sha256(content).hexdigest()}  {name.removeprefix('daily_basic/')}\n"
        for name, content in sorted(files.items())
    ).encode()
    files["daily_basic/checksums.sha256"] = checksums
    with tarfile.open(path, "w:gz") as tar:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            tar.addfile(member, io.BytesIO(content))


def asset(path: Path, tag: str = "2026-07-22") -> ReleaseAsset:
    return ReleaseAsset(
        tag, path.name, path.stat().st_size, "https://example.test/data"
    )


def test_install_and_update_daily_basic_without_touching_market_data(tmp_path):
    provider = tmp_path / "cn_data"
    make_provider(provider)
    archive = tmp_path / "daily_basic_qlib_features.tar.gz"
    make_archive(archive, provider)

    install_feature_archive(archive, provider, "owner/repo", asset(archive))
    assert (provider / "features/sh600519/pe.day.bin").is_file()
    assert (provider / "features/sh600519/close.day.bin").read_bytes() == b"market"
    assert read_feature_metadata(provider)["release_tag"] == "2026-07-22"

    make_archive(archive, provider, value=10.0)
    install_feature_archive(
        archive,
        provider,
        "owner/repo",
        asset(archive, "2026-07-23"),
        replace=True,
    )
    values = struct.unpack(
        "<3f", (provider / "features/sh600519/pe.day.bin").read_bytes()
    )
    assert values[1] == 10.0


def test_unknown_feature_collision_is_rejected_before_writes(tmp_path):
    provider = tmp_path / "cn_data"
    make_provider(provider)
    archive = tmp_path / "daily_basic_qlib_features.tar.gz"
    make_archive(archive, provider)
    conflict = provider / "features/sh600519/pe.day.bin"
    conflict.write_bytes(b"unknown")

    with pytest.raises(FileExistsError, match="conflict"):
        install_feature_archive(archive, provider, "owner/repo", asset(archive))
    assert conflict.read_bytes() == b"unknown"


def test_calendar_mismatch_is_rejected(tmp_path):
    provider = tmp_path / "cn_data"
    make_provider(provider)
    archive = tmp_path / "daily_basic_qlib_features.tar.gz"
    make_archive(archive, provider)
    (provider / "calendars/day.txt").write_text("2024-01-02\n")

    with pytest.raises(ValueError, match="calendar mismatch"):
        install_feature_archive(archive, provider, "owner/repo", asset(archive))
