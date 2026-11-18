import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_pipeline import Harness, result, run, submission, submit
from test_pipeline import pipeline as pipeline

from buildsize.db.models import UnityRecordRow

ROOT = Path(__file__).resolve().parents[2]
GUID = "a" * 32


def reports(path="Assets/A.png", size=100, format="ASTC_6x6"):
    report = json.loads((ROOT / "examples/unity-report.json").read_text())
    report.pop("run_id")
    report["containers"][0]["container_index"] = 17
    report["containers"][0]["entries"] = [
        {
            "record_index": 9,
            "source_guid": GUID,
            "source_path": path,
            "asset_type": "Texture2D",
            "packed_bytes": size,
        },
        {
            "record_index": 20,
            "source_guid": GUID,
            "source_path": path,
            "asset_type": "Texture2D",
            "packed_bytes": size,
        },
        {
            "record_index": 100,
            "source_guid": "0" * 32,
            "source_path": None,
            "asset_type": "Generated",
            "packed_bytes": 5,
        },
    ]
    importers = json.loads((ROOT / "examples/asset-importers.json").read_text())
    importers.pop("run_id")
    importers["assets"][0]["source_path"] = path
    importers["assets"][0]["settings"]["android"]["format"] = format
    return {"unity_report": report, "asset_importers": importers}


def test_unity_ready_and_evidence_preserve_all_rows(pipeline: Harness) -> None:
    h = pipeline
    build_id, job = submit(h, *submission("one", reports=reports()))
    run(h, job)
    value = result(h, build_id)
    assert value["analysis_state"] == "READY"
    assert value["result"]["unity"]["unity_packed_bytes"] == 205
    assert value["result"]["unity"]["valid_guid_record_count"] == 2
    records = h.client.get(f"/api/v1/builds/{build_id}/unity-records", headers=h.headers).json()
    assert len(records["items"]) == 3
    assert records["items"][0]["container_index"] == 17
    assert records["items"][0]["record_index"] == 9
    assert records["items"][0]["json_pointer"] == "/containers/0/entries/0"
    assert records["items"][2]["source_guid"] == "0" * 32
    assets = h.client.get(
        f"/api/v1/builds/{build_id}/unity-assets?source_guid={GUID}", headers=h.headers
    ).json()
    assert assets["items"][0]["record_count"] == 2
    assert assets["items"][0]["packed_bytes"] == 200
    assert h.client.get(
        f"/api/v1/builds/{build_id}/unity-assets?q=Assets/A", headers=h.headers
    ).json()["items"]
    settings = h.client.get(f"/api/v1/builds/{build_id}/asset-importers", headers=h.headers).json()
    assert settings["items"][0]["settings"]["android"]["format"] == "ASTC_6x6"
    for route in ("unity-assets", "unity-containers", "unity-records", "asset-importers"):
        assert (
            h.client.get(f"/api/v1/builds/{build_id}/{route}", headers=h.other_headers).status_code
            == 404
        )
        assert (
            h.client.get(
                f"/api/v1/builds/{build_id}/{route}?limit=201", headers=h.headers
            ).status_code
            == 422
        )
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(UnityRecordRow)) == 3


def test_unity_compare_rename_and_importers_stay_separate_from_apk(pipeline: Harness) -> None:
    h = pipeline
    old, job = submit(h, *submission("old", age=120, reports=reports()))
    run(h, job)
    new, job = submit(
        h,
        *submission(
            "new", age=60, reports=reports(path="Assets/Renamed.png", size=150, format="Automatic")
        ),
    )
    run(h, job)
    comparison_id = result(h, new)["result"]["comparison_id"]
    value = h.client.get(f"/api/v1/comparisons/{comparison_id}", headers=h.headers).json()
    assert value["result"]["unity"]["delta_packed_bytes"] == 100
    assert value["result"]["unity"]["additive_to_apk"] is False
    changes = h.client.get(
        f"/api/v1/comparisons/{comparison_id}/changes?layer=unity_asset", headers=h.headers
    ).json()
    assert changes["metric"] == "unity_packed_bytes"
    assert len(changes["items"]) == 1
    assert changes["items"][0]["renamed"]
    assert changes["items"][0]["delta_packed_bytes"] == 100
    settings = h.client.get(
        f"/api/v1/comparisons/{comparison_id}/unity-changes?kind=importer", headers=h.headers
    ).json()
    assert settings["metric"] == "none"
    assert settings["items"][0]["new_settings"]["android"]["format"] == "Automatic"
    assert settings["items"][0]["delta_packed_bytes"] is None
    physical = h.client.get(
        f"/api/v1/comparisons/{comparison_id}/changes", headers=h.headers
    ).json()
    delta = (
        sum(row["delta_bytes"] for row in physical["items"])
        + value["result"]["delta_overhead_bytes"]
    )
    assert delta == value["result"]["delta_apk_bytes"]
    assert (
        h.client.get(
            f"/api/v1/comparisons/{comparison_id}/unity-changes", headers=h.other_headers
        ).status_code
        == 404
    )


def test_bad_importer_and_truncated_report_only_degrade_unity_layer(pipeline: Harness) -> None:
    h = pipeline
    raw = reports()
    raw["asset_importers"]["assets"][0]["settings"]["mipmap_enabled"] = "false"
    raw["unity_report"]["warnings"] = ["BUILDSIZE_TRUNCATED: records"]
    build_id, job = submit(h, *submission("bad", reports=raw))
    run(h, job)
    value = result(h, build_id)
    assert value["analysis_state"] == "PARTIAL"
    assert value["result"]["coverage"]["unity_report"] == "failed"
    assert value["result"]["coverage"]["asset_importers"] == "failed"
    assert h.client.get(f"/api/v1/builds/{build_id}/entries", headers=h.headers).status_code == 200
    assert (
        len(
            h.client.get(f"/api/v1/builds/{build_id}/unity-records", headers=h.headers).json()[
                "items"
            ]
        )
        == 3
    )


def test_manual_comparison_uses_persisted_unity_indexes(pipeline: Harness) -> None:
    h = pipeline
    old, job = submit(h, *submission("old", reports=reports(), age=120))
    run(h, job)
    new, job = submit(h, *submission("new", reports=reports(size=200), age=60))
    run(h, job)
    response = h.client.post(
        "/api/v1/comparisons",
        json={"base_build_id": old, "target_build_id": new},
        headers={**h.headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 202
    comparison_id = response.json()["comparison_id"]
    from buildsize.db.models import Job

    with Session(h.engine) as db:
        queued = db.scalar(select(Job).where(Job.comparison_id == uuid.UUID(comparison_id)))
        job_id = str(queued.id)
    run(h, job_id)
    value = h.client.get(f"/api/v1/comparisons/{comparison_id}", headers=h.headers).json()
    assert value["result"]["unity"]["delta_packed_bytes"] == 200


def test_partial_importer_snapshot_keeps_valid_observations(pipeline: Harness) -> None:
    h = pipeline
    first = reports()
    second = reports(format="Automatic")
    for value in (first, second):
        value["asset_importers"]["coverage"] = {
            "requested_count": 2,
            "collected_count": 1,
            "failed_count": 1,
        }
    old, job = submit(h, *submission("old", reports=first, age=120))
    run(h, job)
    new, job = submit(h, *submission("new", reports=second, age=60))
    run(h, job)
    comparison_id = result(h, new)["result"]["comparison_id"]
    page = h.client.get(
        f"/api/v1/comparisons/{comparison_id}/unity-changes?kind=importer", headers=h.headers
    ).json()
    assert page["state"] == "partial"
    assert page["items"][0]["new_settings"]["android"]["format"] == "Automatic"
    assert result(h, new)["analysis_state"] == "PARTIAL"


def test_controlled_real_unity_apks_through_worker(pipeline: Harness) -> None:
    import hashlib
    import os

    pointer = ROOT / ".buildsize/unity/current-evidence.txt"
    selected = (
        pointer.read_text().strip()
        if pointer.exists()
        else str(ROOT / ".buildsize/unity/evidence-final")
    )
    root = Path(os.getenv("BUILDSIZE_UNITY_EVIDENCE", selected))
    if not (root / "a/fixture.apk").exists() or not (root / "b/fixture.apk").exists():
        pytest.skip("NOT_VERIFIED：缺少受控 Unity 实际 APK/报告")
    h = pipeline
    builds = []
    raw_apks = []
    guid = None
    for index, label in enumerate(("a", "b")):
        directory = root / label
        snapshot = json.loads((directory / "unity-settings.json").read_text())
        unity = json.loads((directory / "unity-report.json").read_text())
        manifest, payloads = submission("real-unity-" + label, age=120 - index * 60)
        manifest["run_id"] = unity["run_id"]
        metadata = json.loads(payloads["build_meta"])
        metadata.update(
            run_id=unity["run_id"],
            application_id=snapshot["application_id"],
            settings=snapshot["settings"],
            version_code=snapshot["version_code"],
            version_name=snapshot["version_name"],
            content_policy=snapshot["content_policy"],
            build_completed_at=snapshot["build_completed_at"],
        )
        metadata["warnings"] = [
            "APK/Unity 报告来自受控真实构建；CI/Git 为协议测试夹具，未验证真实 Jenkins。"
        ]
        payloads = {
            "apk": (directory / "fixture.apk").read_bytes(),
            "build_meta": json.dumps(metadata, ensure_ascii=False).encode(),
            "unity_report": (directory / "unity-report.json").read_bytes(),
            "asset_importers": (directory / "asset-importers.json").read_bytes(),
        }
        manifest["artifacts"] = [
            {
                "role": role,
                "filename": "fixture.apk" if role == "apk" else role + ".json",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for role, data in payloads.items()
        ]
        build_id, job = submit(h, manifest, payloads)
        run(h, job)
        builds.append(build_id)
        raw_apks.append(payloads["apk"])
        groups = h.client.get(
            f"/api/v1/builds/{build_id}/unity-assets?q=中文", headers=h.headers
        ).json()["items"]
        assert len(groups) == 1
        assert groups[0]["asset_type"] == "UnityEngine.Texture2D"
        if guid is not None:
            assert groups[0]["source_guid"] == guid
        guid = groups[0]["source_guid"]
        containers = h.client.get(
            f"/api/v1/builds/{build_id}/unity-containers", headers=h.headers
        ).json()["items"]
        assert any(row["mapping_state"] == "exact" for row in containers)
        assert any(row["mapping_state"] == "unmapped" for row in containers)
    comparison_id = result(h, builds[1])["result"]["comparison_id"]
    comparison = h.client.get(f"/api/v1/comparisons/{comparison_id}", headers=h.headers).json()[
        "result"
    ]
    assert comparison["delta_apk_bytes"] == len(raw_apks[1]) - len(raw_apks[0])
    assert comparison["unity"]["delta_packed_bytes"] == 61440
    changes = h.client.get(
        f"/api/v1/comparisons/{comparison_id}/unity-changes", headers=h.headers
    ).json()["items"]
    assert any(row["source_guid"] == guid and row["delta_packed_bytes"] == 61440 for row in changes)
