import io
import json
import tarfile
from pathlib import Path

import pytest

from deep_alpha.errors import DownloadError, ProviderError
from deep_alpha.github_release import ReleaseAsset
from deep_alpha.installer import install_archive, read_metadata, validate_provider


def make_archive(path: Path, prefix="qlib_bin"):
    with tarfile.open(path, "w:gz") as tar:
        files = {f"{prefix}/calendars/day.txt": b"2024-01-02\n", f"{prefix}/instruments/all.txt": b"SH600519\t2024-01-02\t2024-01-02\n", f"{prefix}/features/SH600519/close.day.bin": b"data"}
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))


def test_install_nested_provider(tmp_path):
    archive = tmp_path / "data.tar.gz"
    make_archive(archive)
    target = tmp_path / "cn_data"
    asset = ReleaseAsset("2026-07-20", "data.tar.gz", archive.stat().st_size, "https://example.test/data")
    install_archive(archive, target, "owner/repo", asset, False)
    validate_provider(target)
    assert read_metadata(target)["release_tag"] == "2026-07-20"


def test_existing_target_requires_force(tmp_path):
    archive = tmp_path / "data.tar.gz"
    make_archive(archive)
    target = tmp_path / "cn_data"
    target.mkdir()
    asset = ReleaseAsset("tag", "data", 1, "url")
    with pytest.raises(ProviderError, match="--force"):
        install_archive(archive, target, "owner/repo", asset, False)


def test_rejects_path_traversal(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("../evil")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    asset = ReleaseAsset("tag", "bad", 1, "url")
    with pytest.raises(DownloadError, match="Unsafe"):
        install_archive(archive, tmp_path / "target", "owner/repo", asset, False)
    assert not (tmp_path.parent / "evil").exists()
