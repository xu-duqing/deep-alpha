import hashlib
import json
import struct
import tarfile
from pathlib import Path

import pytest

from deep_alpha.temporal_delta import (
    apply_paired_archives,
    build_transaction_plan,
    journal_path,
    load_archive,
    recover,
    trusted_paired_release,
    upgrade_legacy_paired_metadata,
)
from deep_alpha.temporal_delta_manifest import write_checksums


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_delta(tmp_path: Path, dataset: str, old: bytes, added: bytes) -> Path:
    root_name = "market_delta" if dataset == "market" else "daily_basic_delta"
    root = tmp_path / root_name
    field = "close" if dataset == "market" else "pe"
    path = f"features/sh600000/{field}.day.bin"
    payload = f"features/sh600000/{field}.day.append.bin"
    (root / payload).parent.mkdir(parents=True)
    (root / payload).write_bytes(added)
    before = b"2026-11-02\n"
    after = b"2026-11-02\n2026-11-03\n"
    manifest = {
        "schema_version": 1,
        "package_type": "qlib_temporal_delta",
        "dataset": dataset,
        "frequency": "day",
        "release_tag": "v2",
        "base_release_tag": "v1",
        "calendar_before_sha256": sha(before),
        "calendar_after_sha256": sha(after),
        "calendar_before_count": 1,
        "calendar_after_count": 2,
        "calendar_append": ["2026-11-03"],
        "files": [{
            "path": path,
            "payload_path": payload,
            "operation": "append",
            "expected_old_size": len(old),
            "expected_old_sha256": sha(old),
            "append_value_count": 1,
            "new_size": len(old + added),
            "new_sha256": sha(old + added),
        }],
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    if dataset == "market":
        (root / "calendars").mkdir()
        (root / "calendars/day.append.txt").write_text("2026-11-03\n")
        (root / "instruments").mkdir()
        (root / "instruments/all.txt").write_text("SH600000\t2026-11-02\t2026-11-03\n")
    write_checksums(root)
    archive = tmp_path / f"{root_name}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(root, arcname=root_name)
    return archive


def make_v2_delta(tmp_path: Path, records: list[dict], payload: bytes, dataset: str = "daily_basic") -> Path:
    root_name = "market_delta" if dataset == "market" else "daily_basic_delta"
    root = tmp_path / root_name
    root.mkdir(parents=True)
    before = b"2026-11-02\n"
    after = before + b"2026-11-03\n"
    manifest = {
        "schema_version": 2, "package_type": "qlib_temporal_delta", "dataset": dataset,
        "frequency": "day", "release_tag": "v2", "base_release_tag": "v1",
        "calendar_before_sha256": sha(before), "calendar_after_sha256": sha(after),
        "calendar_before_count": 1, "calendar_after_count": 2,
        "calendar_append": ["2026-11-03"], "files": records,
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "payloads.bin").write_bytes(payload)
    if dataset == "market":
        (root / "calendars").mkdir()
        (root / "calendars/day.append.txt").write_bytes(b"2026-11-03\n")
        (root / "instruments").mkdir()
        (root / "instruments/all.txt").write_text("SH600000\t2026-11-02\t2026-11-03\n")
    write_checksums(root)
    archive = tmp_path / f"{root_name}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(root, arcname=root_name)
    return archive


def v2_create(path: str, offset: int, values: bytes) -> dict:
    payload = struct.pack("<f", 0) + values
    return {"path": path, "payload_path": "payloads.bin", "payload_offset": offset,
            "payload_size": len(payload), "operation": "create", "expected_old_size": None,
            "expected_old_sha256": None, "append_value_count": len(values) // 4,
            "new_size": len(payload), "new_sha256": sha(payload)}


def make_provider(tmp_path: Path):
    provider = tmp_path / "provider"
    (provider / "calendars").mkdir(parents=True)
    (provider / "instruments").mkdir()
    (provider / "features/sh600000").mkdir(parents=True)
    (provider / "calendars/day.txt").write_text("2026-11-02\n")
    (provider / "instruments/all.txt").write_text("SH600000\t2026-11-02\t2026-11-02\n")
    common = {"schema_version": 2, "repo": "o/r", "release_tag": "v1", "paired_release_tag": "v1",
              "transaction_id": "base-transaction", "state": "committed",
              "calendar_count": 1, "calendar_end": "2026-11-02"}
    (provider / ".investment_data_meta.json").write_text(json.dumps({
        **common, "dataset": "cn_stock_1d",
        "asset_name": "qlib_bin.tar.gz", "installed_at": "old-market-time",
    }))
    (provider / ".daily_basic_meta.json").write_text(json.dumps({
        **common, "dataset": "daily_basic",
        "asset_name": "daily_basic_qlib_features.tar.gz", "installed_at": "old-daily-time",
        "source_min_date": "2020-01-02", "source_max_date": "2026-11-02", "instrument_count": 1,
    }))
    return provider


def test_apply_paired_delta_updates_both_metadata(tmp_path):
    provider = make_provider(tmp_path)
    old_market = struct.pack("<ff", 0, 10)
    old_daily = struct.pack("<ff", 0, 20)
    (provider / "features/sh600000/close.day.bin").write_bytes(old_market)
    (provider / "features/sh600000/pe.day.bin").write_bytes(old_daily)
    market = make_delta(tmp_path / "m", "market", old_market, struct.pack("<f", 11))
    daily = make_delta(tmp_path / "d", "daily_basic", old_daily, struct.pack("<f", 21))

    result = apply_paired_archives(market, daily, provider, "v2")

    assert result["release_tag"] == "v2"
    assert (provider / "features/sh600000/close.day.bin").read_bytes() == old_market + struct.pack("<f", 11)
    assert (provider / "features/sh600000/pe.day.bin").read_bytes() == old_daily + struct.pack("<f", 21)
    market_metadata = json.loads((provider / ".investment_data_meta.json").read_text())
    daily_metadata = json.loads((provider / ".daily_basic_meta.json").read_text())
    assert market_metadata["paired_release_tag"] == "v2"
    assert market_metadata["asset_name"] == "qlib_bin.tar.gz"
    assert market_metadata["installed_at"] != "old-market-time"
    assert daily_metadata["paired_release_tag"] == "v2"
    assert daily_metadata["asset_name"] == "daily_basic_qlib_features.tar.gz"
    assert daily_metadata["installed_at"] != "old-daily-time"
    assert daily_metadata["source_min_date"] == "2020-01-02"
    assert daily_metadata["source_max_date"] == "2026-11-02"
    assert daily_metadata["instrument_count"] == 1
    assert market_metadata["schema_version"] == daily_metadata["schema_version"] == 2
    assert market_metadata["state"] == daily_metadata["state"] == "committed"
    assert market_metadata["transaction_id"] == daily_metadata["transaction_id"]
    assert market_metadata["calendar_count"] == daily_metadata["calendar_count"] == 2
    assert market_metadata["calendar_end"] == daily_metadata["calendar_end"] == "2026-11-03"
    assert not (provider / ".temporal_delta/install-journal.json").exists()


def test_manifest_tag_must_match_api_release_before_writes(tmp_path):
    provider = make_provider(tmp_path)
    old_market = struct.pack("<ff", 0, 10)
    old_daily = struct.pack("<ff", 0, 20)
    market_file = provider / "features/sh600000/close.day.bin"
    daily_file = provider / "features/sh600000/pe.day.bin"
    market_file.write_bytes(old_market)
    daily_file.write_bytes(old_daily)
    market = make_delta(tmp_path / "m", "market", old_market, struct.pack("<f", 11))
    daily = make_delta(tmp_path / "d", "daily_basic", old_daily, struct.pack("<f", 21))

    with pytest.raises(ValueError, match="API Release tag"):
        apply_paired_archives(market, daily, provider, "other")

    assert market_file.read_bytes() == old_market
    assert daily_file.read_bytes() == old_daily


def test_daily_basic_metadata_must_match_delta_base_before_writes(tmp_path):
    provider = make_provider(tmp_path)
    old_market = struct.pack("<ff", 0, 10)
    old_daily = struct.pack("<ff", 0, 20)
    market_file = provider / "features/sh600000/close.day.bin"
    daily_file = provider / "features/sh600000/pe.day.bin"
    market_file.write_bytes(old_market)
    daily_file.write_bytes(old_daily)
    daily_metadata = json.loads((provider / ".daily_basic_meta.json").read_text())
    daily_metadata["paired_release_tag"] = "wrong"
    (provider / ".daily_basic_meta.json").write_text(json.dumps(daily_metadata))
    market = make_delta(tmp_path / "m", "market", old_market, struct.pack("<f", 11))
    daily = make_delta(tmp_path / "d", "daily_basic", old_daily, struct.pack("<f", 21))

    with pytest.raises(ValueError, match="trusted committed"):
        apply_paired_archives(market, daily, provider, "v2")

    assert market_file.read_bytes() == old_market
    assert daily_file.read_bytes() == old_daily


def test_same_size_historical_mutation_is_trusted(tmp_path):
    provider = make_provider(tmp_path)
    original = struct.pack("<ff", 0, 10)
    mutated = struct.pack("<ff", 0, 99)
    daily_old = struct.pack("<ff", 0, 20)
    (provider / "features/sh600000/close.day.bin").write_bytes(mutated)
    (provider / "features/sh600000/pe.day.bin").write_bytes(daily_old)
    market = make_delta(tmp_path / "m", "market", original, struct.pack("<f", 11))
    daily = make_delta(tmp_path / "d", "daily_basic", daily_old, struct.pack("<f", 21))

    apply_paired_archives(market, daily, provider, "v2")
    assert (provider / "features/sh600000/close.day.bin").read_bytes() == mutated + struct.pack("<f", 11)


def test_old_size_mismatch_fails_before_any_write(tmp_path):
    provider = make_provider(tmp_path)
    market_file = provider / "features/sh600000/close.day.bin"
    daily_file = provider / "features/sh600000/pe.day.bin"
    market_file.write_bytes(b"short")
    daily_old = struct.pack("<ff", 0, 20)
    daily_file.write_bytes(daily_old)
    market = make_delta(tmp_path / "m", "market", struct.pack("<ff", 0, 10), struct.pack("<f", 11))
    daily = make_delta(tmp_path / "d", "daily_basic", daily_old, struct.pack("<f", 21))

    with pytest.raises(ValueError, match="old size mismatch"):
        apply_paired_archives(market, daily, provider, "v2")
    assert market_file.read_bytes() == b"short"
    assert daily_file.read_bytes() == daily_old


def test_recovery_truncates_appends_deletes_creates_and_restores_state(tmp_path):
    provider = make_provider(tmp_path)
    feature = provider / "features/sh600000/close.day.bin"
    feature.write_bytes(b"old-appended")
    created = provider / "features/sh600000/new.day.bin"
    created.write_bytes(b"new")
    calendar = provider / "calendars/day.txt"
    old_calendar_size = calendar.stat().st_size
    with calendar.open("a") as stream:
        stream.write("2026-11-03\n")
    old_market = json.loads((provider / ".investment_data_meta.json").read_text())
    old_daily = json.loads((provider / ".daily_basic_meta.json").read_text())
    (provider / ".investment_data_meta.json").write_text("{}")
    (provider / ".daily_basic_meta.json").write_text("{}")
    backup = provider / ".temporal_delta/instruments-tx"
    backup.mkdir(parents=True)
    (backup / "all.txt").write_text("old instruments")
    (provider / "instruments/all.txt").write_text("new instruments")
    journal = {
        "schema_version": 2, "transaction_id": "tx", "phase": "prepared",
        "append_files": {"features/sh600000/close.day.bin": 3},
        "created_files": ["features/sh600000/new.day.bin"],
        "calendar_old_size": old_calendar_size,
        "instruments_backup_dir": str(backup),
        "previous_market_metadata": old_market,
        "previous_daily_metadata": old_daily,
    }
    journal_path(provider).write_text(json.dumps(journal))

    assert recover(provider)
    assert feature.read_bytes() == b"old"
    assert not created.exists()
    assert calendar.read_text() == "2026-11-02\n"
    assert (provider / "instruments/all.txt").read_text() == "old instruments"
    assert json.loads((provider / ".investment_data_meta.json").read_text()) == old_market
    assert json.loads((provider / ".daily_basic_meta.json").read_text()) == old_daily
    assert not journal_path(provider).exists()


def test_upgrade_legacy_paired_metadata_marks_existing_install_trusted(tmp_path):
    provider = make_provider(tmp_path)
    for name in (".investment_data_meta.json", ".daily_basic_meta.json"):
        path = provider / name
        metadata = json.loads(path.read_text())
        for key in ("transaction_id", "state", "calendar_count", "calendar_end"):
            metadata.pop(key)
        metadata["schema_version"] = 1
        path.write_text(json.dumps(metadata))

    assert upgrade_legacy_paired_metadata(provider, "o/r") == "v1"
    market = json.loads((provider / ".investment_data_meta.json").read_text())
    daily = json.loads((provider / ".daily_basic_meta.json").read_text())
    assert trusted_paired_release(market, daily, "o/r") == "v1"
    assert market["transaction_id"] == daily["transaction_id"]
    assert market["state"] == daily["state"] == "committed"


def test_upgrade_legacy_paired_metadata_rejects_different_versions(tmp_path):
    provider = make_provider(tmp_path)
    for name in (".investment_data_meta.json", ".daily_basic_meta.json"):
        path = provider / name
        metadata = json.loads(path.read_text())
        metadata["schema_version"] = 1
        path.write_text(json.dumps(metadata))
    daily_path = provider / ".daily_basic_meta.json"
    daily = json.loads(daily_path.read_text())
    daily.update({"release_tag": "other", "paired_release_tag": "other"})
    daily_path.write_text(json.dumps(daily))

    assert upgrade_legacy_paired_metadata(provider, "o/r") is None


def test_stream_loader_rejects_malicious_tar_path(tmp_path):
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        info = tarfile.TarInfo("market_delta/../../escaped")
        info.size = 1
        stream.addfile(info, __import__("io").BytesIO(b"x"))

    with pytest.raises(ValueError, match="unsafe archive path"):
        load_archive(archive, "market")
    assert not (tmp_path / "escaped").exists()


def test_stream_loader_rejects_checksum_mismatch(tmp_path):
    archive = make_delta(tmp_path / "source", "daily_basic", struct.pack("<ff", 0, 20), struct.pack("<f", 21))
    corrupted = tmp_path / "corrupted.tar.gz"
    with tarfile.open(archive, "r:gz") as source, tarfile.open(corrupted, "w:gz") as destination:
        for member in source.getmembers():
            extracted = source.extractfile(member) if member.isfile() else None
            data = extracted.read() if extracted is not None else None
            if member.name.endswith("pe.day.append.bin"):
                data = b"bad!"
            destination.addfile(member, __import__("io").BytesIO(data) if data is not None else None)

    with pytest.raises(ValueError, match="checksum mismatch"):
        load_archive(corrupted, "daily_basic")


def test_schema_v2_aggregate_payload_uses_shared_memoryviews(tmp_path):
    first, second = struct.pack("<f", 11), struct.pack("<ff", 21, 22)
    first_segment, second_segment = struct.pack("<f", 0) + first, struct.pack("<f", 0) + second
    records = [v2_create("features/sh600001/x.day.bin", 0, first),
               v2_create("features/sh600002/y.day.bin", len(first_segment), second)]
    package = load_archive(make_v2_delta(tmp_path, records, first_segment + second_segment), "daily_basic")
    empty_market = type(package)({**package.manifest, "dataset": "market", "files": []}, {})
    plan = build_transaction_plan(tmp_path / "provider", empty_market, package)
    assert bytes(plan.daily[0].payload) == first_segment
    assert bytes(plan.daily[1].payload) == second_segment
    assert plan.daily[0].payload.obj is package.files["payloads.bin"]
    assert plan.daily[1].payload.obj is package.files["payloads.bin"]


@pytest.mark.parametrize("mutation,match", [
    (lambda records: records[1].update(payload_offset=2), "overlap"),
    (lambda records: records[1].update(payload_offset=100), "out of bounds"),
    (lambda records: records[1].update(payload_size=4), "payload size contract"),
])
def test_schema_v2_rejects_invalid_aggregate_ranges(tmp_path, mutation, match):
    first = struct.pack("<f", 1)
    second = struct.pack("<ff", 2, 3)
    first_segment, second_segment = struct.pack("<f", 0) + first, struct.pack("<f", 0) + second
    records = [v2_create("features/sh600001/x.day.bin", 0, first),
               v2_create("features/sh600002/y.day.bin", len(first_segment), second)]
    mutation(records)
    archive = make_v2_delta(tmp_path, records, first_segment + second_segment)
    with pytest.raises(ValueError, match=match):
        load_archive(archive, "daily_basic")


def test_parallel_apply_failure_recovers_all_files(tmp_path, monkeypatch):
    import deep_alpha.temporal_delta as temporal_delta

    provider = make_provider(tmp_path)
    old_market = struct.pack("<ff", 0, 10)
    old_daily = struct.pack("<ff", 0, 20)
    market_file = provider / "features/sh600000/close.day.bin"
    daily_file = provider / "features/sh600000/pe.day.bin"
    market_file.write_bytes(old_market)
    daily_file.write_bytes(old_daily)
    market = make_delta(tmp_path / "m", "market", old_market, struct.pack("<f", 11))
    daily = make_delta(tmp_path / "d", "daily_basic", old_daily, struct.pack("<f", 21))
    original = temporal_delta._apply_item

    def fail_daily(item):
        if item.record["path"].endswith("/pe.day.bin"):
            raise OSError("injected parallel write failure")
        original(item)

    monkeypatch.setattr(temporal_delta, "_apply_item", fail_daily)
    with pytest.raises(OSError, match="injected"):
        apply_paired_archives(market, daily, provider, "v2")

    assert market_file.read_bytes() == old_market
    assert daily_file.read_bytes() == old_daily
    assert not journal_path(provider).exists()
    assert json.loads((provider / ".investment_data_meta.json").read_text())["release_tag"] == "v1"
