"""500 MiB / 20,000 条目 / 50,000 Unity 记录，真实 HTTP、Worker 和分页查询测量。"""

import hashlib
import json
import statistics
import time
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from buildsize.client.transport import ApiClient


def measure(origin: str, token: str, directory: Path) -> dict[str, Any]:
    client = ApiClient(origin, token, timeout=900)
    root = Path(__file__).resolve().parents[1]
    apk = directory / "performance-500mib.apk"
    metadata = json.loads((root / "examples/build-meta.json").read_text())
    metadata.update(
        run_id=str(uuid.uuid4()),
        project_key="stack-game",
        build_completed_at=datetime.now(UTC).isoformat(),
    )
    metadata["ci"]["job_full_name"] += "/performance"
    metadata["ci"]["build_number"] = "1"
    metadata["warnings"].append("SYNTHETIC_PERFORMANCE_FIXTURE_NOT_INSTALLABLE_APK")
    names = [f"assets/bin/Data/perf-{i:05d}" for i in range(20000)]
    overhead = sum(76 + 2 * len(name.encode()) for name in names) + 22
    size = 500 * 1024**2
    length, remainder = divmod(size - overhead, len(names))
    with zipfile.ZipFile(apk, "w", compression=zipfile.ZIP_STORED) as archive:
        for i, name in enumerate(names):
            with archive.open(name, "w") as entry:
                entry.write(i.to_bytes(8, "little"))
                entry.write(b"\0" * (length + (i < remainder) - 8))
    assert apk.stat().st_size == size
    report = {
        "schema_version": 1,
        "run_id": metadata["run_id"],
        "exporter_version": "synthetic-performance-1",
        "unity_version": metadata["settings"]["unity_version"],
        "captured_at": metadata["build_completed_at"],
        "capture_mode": "buildplayer_return",
        "build_result": "Succeeded",
        "detailed_report_requested": True,
        "unity_reported_total_bytes": None,
        "containers": [
            {
                "container_index": c,
                "short_path": names[c],
                "overhead_bytes": 0,
                "entries": [
                    {
                        "record_index": r,
                        "source_guid": f"{c * 100 + r + 1:032x}",
                        "source_path": f"Assets/Synthetic/asset-{c * 100 + r}.asset",
                        "asset_type": "Texture2D",
                        "packed_bytes": 100,
                    }
                    for r in range(100)
                ],
            }
            for c in range(500)
        ],
        "output_files": [],
        "warnings": ["SYNTHETIC_REPORT_NOT_EXPORTED_FROM_UNITY"],
    }
    meta = directory / "performance-meta.json"
    meta.write_text(json.dumps(metadata))
    unity = directory / "performance-unity.json"
    unity.write_text(json.dumps(report))
    roles = {"apk": apk, "build_meta": meta, "unity_report": unity}
    artifacts: list[dict[str, Any]] = []
    for role, path in roles.items():
        with path.open("rb") as source:
            sha = hashlib.file_digest(source, "sha256").hexdigest()
        artifacts.append(
            {"role": role, "filename": path.name, "bytes": path.stat().st_size, "sha256": sha}
        )
    manifest = {
        "schema_version": 1,
        "run_id": metadata["run_id"],
        "project_key": "stack-game",
        "variant": metadata["variant"],
        "ci": metadata["ci"],
        "artifacts": artifacts,
    }
    started = time.monotonic()
    _, created = client.request(
        "POST",
        "/projects/stack-game/builds",
        body=manifest,
        headers={"Idempotency-Key": metadata["run_id"]},
    )
    bid = created["build_id"]
    for item in artifacts:
        client.request(
            "PUT",
            f"/builds/{bid}/artifacts/{item['role']}",
            file=roles[item["role"]],
            headers={"X-Content-SHA256": item["sha256"]},
        )
    upload_seconds = time.monotonic() - started
    finalize_started = time.monotonic()
    client.request("POST", f"/builds/{bid}/finalize", body={})
    finalize_seconds = time.monotonic() - finalize_started
    started = time.monotonic()
    while True:
        _, value = client.request("GET", f"/builds/{bid}")
        if value["analysis_state"] in {"READY", "PARTIAL"}:
            break
        assert value["analysis_state"] != "FAILED", "Performance worker failed"
        assert time.monotonic() - started < 600, "Performance worker timeout"
        time.sleep(0.5)
    seconds = time.monotonic() - started
    assert value["result"]["unity"]["report_record_count"] == 50000
    assert value["result"]["apk"]["apk_file_bytes"] == size
    measurements = {}
    for endpoint in ("entries", "unity-assets"):
        samples = []
        for i in range(50):
            start = time.monotonic()
            _, page = client.request(
                "GET", f"/builds/{bid}/{endpoint}", query={"limit": 50, "offset": i * 50}
            )
            assert len(page["items"]) == 50
            samples.append((time.monotonic() - start) * 1000)
        measurements[endpoint] = {
            "samples": 50,
            "p95_ms": sorted(samples)[47],
            "median_ms": statistics.median(samples),
        }
    result = {
        "fixture": "synthetic ZIP/Unity JSON, not a real Unity APK",
        "apk_bytes": size,
        "apk_entries": 20000,
        "unity_records": 50000,
        "upload_seconds": upload_seconds,
        "finalize_request_seconds": finalize_seconds,
        "accepted_202_to_ready_seconds": seconds,
        "worker_peak_rss_bytes": value["result"].get("runtime", {}).get("worker_peak_rss_bytes"),
        "pagination": measurements,
        "worker_cpu_quota": 4,
        "worker_memory_limit_bytes": 2 * 1024**3,
        "rss_target_bytes": 1024**3,
        "hardware_note": (
            "Docker Desktop VM on Apple Silicon; shared virtual disk, not a dedicated SSD test host"
        ),
    }
    for path in roles.values():
        path.unlink()
    return result
