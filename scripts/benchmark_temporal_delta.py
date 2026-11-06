#!/usr/bin/env python3
"""Benchmark temporal-delta loading, including the schema-v2 stress fixture."""
import argparse
import hashlib
import io
import json
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

from deep_alpha.temporal_delta import apply_paired_archives, load_archive


def _synthetic_archive(directory: Path, records_count: int, payload_mib: int) -> Path:
    """Build one v2 daily-basic tar with shared payload; generation is not timed."""
    payload_size = payload_mib * 1024 * 1024
    payload = bytes(range(256)) * (payload_size // 256) + bytes(range(payload_size % 256))
    base, remainder = divmod(payload_size // 4, records_count)
    records = []
    offset = 0
    zero_hash = "0" * 64
    for number in range(records_count):
        words = base + (number < remainder)
        size = words * 4
        # Create payload contains Qlib's start-index float plus values.
        records.append({
            "path": f"features/s{number:06d}/x.day.bin", "payload_path": "payloads.bin",
            "payload_offset": offset, "payload_size": size, "operation": "create",
            "expected_old_size": None, "expected_old_sha256": None,
            "append_value_count": words - 1, "new_size": size, "new_sha256": zero_hash,
        })
        offset += size
    manifest = json.dumps({
        "schema_version": 2, "package_type": "qlib_temporal_delta", "dataset": "daily_basic",
        "frequency": "day", "release_tag": "stress-v2", "base_release_tag": "stress-v1",
        "calendar_before_sha256": zero_hash, "calendar_after_sha256": zero_hash,
        "calendar_before_count": 1, "calendar_after_count": 2,
        "calendar_append": ["2026-11-05"], "files": records,
    }, separators=(",", ":")).encode()
    inventory = (f"{hashlib.sha256(manifest).hexdigest()}  manifest.json\n"
                 f"{hashlib.sha256(payload).hexdigest()}  payloads.bin\n").encode()
    archive_path = directory / "daily_basic_delta.tar.gz"
    with tarfile.open(archive_path, "w:gz", compresslevel=1) as archive:
        for name, data in (("manifest.json", manifest), ("payloads.bin", payload),
                           ("checksums.sha256", inventory)):
            info = tarfile.TarInfo(f"daily_basic_delta/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return archive_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-archive", type=Path)
    parser.add_argument("--daily-basic-archive", type=Path)
    parser.add_argument("--synthetic", action="store_true",
                        help="load synthetic schema v2: 139300 records and one 95 MiB payload")
    parser.add_argument("--records", type=int, default=139300)
    parser.add_argument("--payload-mib", type=int, default=95)
    parser.add_argument("--max-seconds", type=float, default=5.0)
    parser.add_argument("--provider", type=Path, help="isolated provider copy; enables full apply")
    parser.add_argument("--release-tag")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    temporary = tempfile.TemporaryDirectory() if args.synthetic else None
    try:
        if args.synthetic:
            assert temporary is not None
            archive = _synthetic_archive(Path(temporary.name), args.records, args.payload_mib)
            started = time.monotonic()
            package = load_archive(archive, "daily_basic")
            load_seconds = time.monotonic() - started
            report = {
                "schema_version": 2, "stream_load_seconds": load_seconds,
                "records": len(cast(list[object], package.manifest["files"])),
                "members_in_memory": len(package.files),
                "payload_bytes": len(package.files["payloads.bin"]),
                "target_seconds": args.max_seconds, "target_met": load_seconds < args.max_seconds,
            }
        else:
            if not args.market_archive or not args.daily_basic_archive:
                parser.error("archives are required unless --synthetic is used")
            started = time.monotonic()
            with ThreadPoolExecutor(max_workers=2) as executor:
                market_future = executor.submit(load_archive, args.market_archive, "market")
                daily_future = executor.submit(load_archive, args.daily_basic_archive, "daily_basic")
                market = market_future.result()
                daily = daily_future.result()
            load_seconds = time.monotonic() - started
            report = {
                "stream_load_seconds": load_seconds,
                "members_in_memory": len(market.files) + len(daily.files),
                "payload_bytes": sum(map(len, market.files.values())) + sum(map(len, daily.files.values())),
                "touched": len(cast(list[object], market.manifest["files"])) + len(cast(list[object], daily.manifest["files"])),
            }
            if args.provider:
                provider = args.provider.resolve()
                if provider == Path("~/.qlib/qlib_data/cn_data").expanduser().resolve():
                    raise SystemExit("refusing to benchmark against the default production Provider")
                result = apply_paired_archives(
                    args.market_archive, args.daily_basic_archive, provider, args.release_tag)
                report["apply"] = result["delta_stats"]
        encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        print(encoded)
        if args.json:
            args.json.write_text(encoded + "\n", encoding="utf-8")
        if args.synthetic and not report["target_met"]:
            raise SystemExit(1)
    finally:
        if temporary:
            temporary.cleanup()


if __name__ == "__main__":
    main()
