import copy
import hashlib
import io
import json
import os
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from buildsize.analyzers.apk_zip import analyze_apk
from buildsize.api.app import create_app
from buildsize.auth.service import create_user, issue_token
from buildsize.config import Config
from buildsize.db.models import (
    AnalysisRevision,
    ApkEntryRow,
    ArtifactBlob,
    Build,
    BuildArtifact,
    Job,
    Membership,
    Outbox,
    Project,
    UploadLease,
)
from buildsize.db.session import make_engine
from buildsize.domain.errors import BuildSizeError
from buildsize.jobs.dispatcher import dispatch_once
from buildsize.jobs.worker import claim, execute_job, publish_analysis
from buildsize.services.common import now

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Harness:
    config: Config
    engine: Engine
    client: TestClient
    headers: dict[str, str] = field(repr=False)
    other_headers: dict[str, str] = field(repr=False)
    project_id: uuid.UUID
    tmp_path: Path
    database_url: str = field(repr=False)


@pytest.fixture
def pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    postgres = os.getenv("BUILDSIZE_TEST_DATABASE_URL")
    administration = None
    schema = "bsi_m2_" + uuid.uuid4().hex
    if postgres:
        administration = create_engine(postgres, hide_parameters=True, pool_pre_ping=True)
        with administration.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = make_url(postgres).update_query_dict({"options": f"-csearch_path={schema}"})
        database_url = url.render_as_string(hide_password=False)
    else:
        database_url = f"sqlite+pysqlite:///{tmp_path / 'pipeline.sqlite3'}"
    config = Config(
        database_url,
        "http://testserver",
        "s" * 48,
        "p" * 48,
        app_env="test",
        blob_root=tmp_path / "blobs",
        redis_url=os.getenv("BUILDSIZE_TEST_REDIS_URL", "redis://127.0.0.1:1/0"),
        trusted_link_hosts=("jenkins.example.invalid",),
    )
    monkeypatch.setenv("DATABASE_URL", database_url)
    command.upgrade(AlembicConfig(str(ROOT / "backend/alembic.ini")), "head")
    engine = make_engine(config)
    with Session(engine) as db:
        first, second = (
            Project(key="demo-game", name="测试项目"),
            Project(key="other-game", name="其他项目"),
        )
        db.add_all([first, second])
        admin = create_user(db, "admin", "fixture-admin-only-password")
        viewer = create_user(db, "viewer", "fixture-viewer-only-password")
        db.flush()
        db.add_all(
            [
                Membership(user_id=admin.id, project_id=first.id, role="admin"),
                Membership(user_id=viewer.id, project_id=first.id, role="viewer"),
            ]
        )
        _, token = issue_token(db, config, first, ["builds:read", "builds:submit"], 3600)
        _, other = issue_token(db, config, second, ["builds:read", "builds:submit"], 3600)
        db.commit()
        project_id = first.id
    client = TestClient(create_app(config, engine))
    try:
        with client:
            yield Harness(
                config,
                engine,
                client,
                {"authorization": "Bearer " + token},
                {"authorization": "Bearer " + other},
                project_id,
                tmp_path,
                database_url,
            )
    finally:
        engine.dispose()
        if administration:
            with administration.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            administration.dispose()


def submission(
    number: str,
    entries: dict[str, bytes] | None = None,
    *,
    age: int = 60,
    settings: dict | None = None,
    reports: dict[str, dict] | None = None,
):
    metadata = json.loads((ROOT / "examples/build-meta.json").read_text())
    run_id = str(uuid.uuid4())
    metadata["run_id"] = run_id
    metadata["ci"]["build_number"] = number
    metadata["build_completed_at"] = (datetime.now(UTC) - timedelta(minutes=age)).isoformat()
    if settings:
        metadata["settings"].update(settings)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as output:
        for path, content in (entries or {"assets/中文 空格.bin": b"test-data"}).items():
            output.writestr(path, content)
    payloads = {
        "apk": buffer.getvalue(),
        "build_meta": json.dumps(metadata, ensure_ascii=False).encode(),
    }
    for role, value in (reports or {}).items():
        value = copy.deepcopy(value)
        value.setdefault("run_id", run_id)
        payloads[role] = json.dumps(value, ensure_ascii=False).encode()
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "project_key": "demo-game",
        "variant": metadata["variant"],
        "ci": metadata["ci"],
        "artifacts": [
            {
                "role": role,
                "filename": "game.apk" if role == "apk" else role + ".json",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for role, data in payloads.items()
        ],
    }
    return manifest, payloads


def init(h: Harness, manifest: dict):
    return h.client.post(
        "/api/v1/projects/demo-game/builds",
        json=manifest,
        headers={**h.headers, "Idempotency-Key": manifest["run_id"]},
    )


def put(h: Harness, build_id: str, role: str, data: bytes):
    return h.client.put(
        f"/api/v1/builds/{build_id}/artifacts/{role}",
        content=data,
        headers={
            **h.headers,
            "Content-Type": "application/octet-stream",
            "X-Content-SHA256": hashlib.sha256(data).hexdigest(),
        },
    )


def submit(h: Harness, manifest: dict, payloads: dict[str, bytes]) -> tuple[str, str]:
    response = init(h, manifest)
    assert response.status_code == 201, response.text
    build_id = response.json()["build_id"]
    for role, data in payloads.items():
        response = put(h, build_id, role, data)
        assert response.status_code == 201, response.text
    response = h.client.post(f"/api/v1/builds/{build_id}/finalize", json={}, headers=h.headers)
    assert response.status_code == 202, response.text
    return build_id, response.json()["job"]["job_id"]


def result(h: Harness, build_id: str):
    response = h.client.get(f"/api/v1/builds/{build_id}", headers=h.headers)
    assert response.status_code == 200, response.text
    return response.json()


def run(h: Harness, job_id: str) -> None:
    assert execute_job(h.engine, h.config, uuid.UUID(job_id)) == "PUBLISHED"


def test_upload_idempotency_identity_and_frozen_seal(pipeline: Harness) -> None:
    h = pipeline
    manifest, payloads = submission("1")
    first = init(h, manifest)
    assert first.status_code == 201
    build_id = first.json()["build_id"]
    assert init(h, manifest).json()["build_id"] == build_id
    assert init(h, manifest).status_code == 200
    reordered = copy.deepcopy(manifest)
    reordered["artifacts"].reverse()
    assert init(h, reordered).status_code == 200
    conflict = copy.deepcopy(manifest)
    conflict["artifacts"][0]["sha256"] = "0" * 64
    assert init(h, conflict).status_code == 409
    assert (
        h.client.post(f"/api/v1/builds/{build_id}/finalize", json={}, headers=h.headers).status_code
        == 409
    )
    for role, data in payloads.items():
        assert put(h, build_id, role, data).status_code == 201
        assert put(h, build_id, role, data).status_code == 200
    responses = [
        h.client.post(f"/api/v1/builds/{build_id}/finalize", json={}, headers=h.headers)
        for _ in range(2)
    ]
    assert all(response.status_code == 202 for response in responses)
    assert responses[0].json()["job"]["job_id"] == responses[1].json()["job"]["job_id"]
    run(h, responses[0].json()["job"]["job_id"])
    assert (
        h.client.post(f"/api/v1/builds/{build_id}/finalize", json={}, headers=h.headers).status_code
        == 202
    )
    assert put(h, build_id, "apk", b"wrong").status_code == 422
    download = h.client.get(f"/api/v1/builds/{build_id}/artifacts/apk/download", headers=h.headers)
    assert download.content == payloads["apk"]
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(Build)) == 1
        assert db.scalar(select(func.count()).select_from(Job)) == 1
        assert db.scalar(select(func.count()).select_from(Outbox)) == 1
        assert db.scalar(select(func.count()).select_from(AnalysisRevision)) == 1
    value = result(h, build_id)
    assert value["analysis_state"] == "PARTIAL"
    comparison_id = value["result"]["comparison_id"]
    comparison = h.client.get(f"/api/v1/comparisons/{comparison_id}", headers=h.headers).json()
    assert comparison["status"] == "NO_BASELINE"
    assert comparison["result"]["delta_apk_bytes"] is None


def test_hash_failure_and_partial_cleanup(pipeline: Harness) -> None:
    h = pipeline
    manifest, payloads = submission("1")
    build_id = init(h, manifest).json()["build_id"]
    raw = payloads["apk"]
    corrupted = raw[:-1] + bytes([raw[-1] ^ 1])
    response = h.client.put(
        f"/api/v1/builds/{build_id}/artifacts/apk",
        content=corrupted,
        headers={
            **h.headers,
            "Content-Type": "application/octet-stream",
            "X-Content-SHA256": hashlib.sha256(raw).hexdigest(),
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ARTIFACT_HASH_MISMATCH"
    assert list((h.config.blob_root / ".tmp").iterdir()) == []
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(BuildArtifact)) == 0
        assert db.scalar(select(func.count()).select_from(UploadLease)) == 0
    assert put(h, build_id, "apk", raw).status_code == 201
    assert result(h, build_id)["artifacts"][0]["state"] == "verified"


def test_authorization_pagination_and_persistence(pipeline: Harness) -> None:
    h = pipeline
    manifest, payloads = submission("1", {"assets/a": b"a", "assets/b": b"bbb"})
    build_id, job_id = submit(h, manifest, payloads)
    run(h, job_id)
    assert h.client.get(f"/api/v1/builds/{build_id}").status_code == 401
    for route in (
        f"builds/{build_id}",
        f"builds/{build_id}/entries",
        f"builds/{build_id}/artifacts/apk/download",
    ):
        assert h.client.get("/api/v1/" + route, headers=h.other_headers).status_code == 404
    page = h.client.get(f"/api/v1/builds/{build_id}/entries?limit=1", headers=h.headers).json()
    assert page["items"][0]["compressed_bytes"] == 3
    assert page["next_offset"] == 1
    assert (
        h.client.get(f"/api/v1/builds/{build_id}/entries?limit=201", headers=h.headers).status_code
        == 422
    )
    assert (
        h.client.get(f"/api/v1/builds/{build_id}/entries?sort=sql", headers=h.headers).status_code
        == 422
    )
    with TestClient(create_app(h.config, make_engine(h.config))) as restarted:
        response = restarted.get(f"/api/v1/builds/{build_id}", headers=h.headers)
        assert response.json()["analysis_revision"] == result(h, build_id)["analysis_revision"]
        assert (
            restarted.get(
                f"/api/v1/builds/{build_id}/artifacts/apk/download", headers=h.headers
            ).content
            == payloads["apk"]
        )


def test_comparison_exact_accounting_and_baseline_time(pipeline: Harness) -> None:
    h = pipeline
    first, first_job = submit(h, *submission("old", {"a": b"123", "classes.dex": b"dex"}, age=120))
    run(h, first_job)
    target, target_job = submit(
        h, *submission("target", {"a": b"12345", "b": b"x", "classes.dex": b"dex"}, age=60)
    )
    run(h, target_job)
    value = result(h, target)
    comparison_id = value["result"]["comparison_id"]
    comparison = h.client.get(f"/api/v1/comparisons/{comparison_id}", headers=h.headers).json()
    assert comparison["base_build_id"] == first
    summary = comparison["result"]
    assert summary["delta_apk_bytes"] == summary["new_apk_bytes"] - summary["old_apk_bytes"]
    assert summary["delta_apk_bytes"] == sum(row["delta_bytes"] for row in summary["categories"])
    changes_url = f"/api/v1/comparisons/{comparison_id}/changes"
    changes = h.client.get(changes_url, headers=h.headers).json()["items"]
    assert {row["path"] for row in changes} == {"a", "b"}
    assert (
        summary["delta_apk_bytes"]
        == sum(row["delta_bytes"] for row in changes) + summary["delta_overhead_bytes"]
    )
    dex_query = {"category": "dex", "include_unchanged": "true"}
    dex_rows = h.client.get(changes_url, headers=h.headers, params=dex_query).json()["items"]
    assert [(row["path"], row["status"]) for row in dex_rows] == [("classes.dex", "UNCHANGED")]
    assert (
        h.client.get(
            changes_url, headers=h.headers, params={**dex_query, "status": "ADDED"}
        ).json()["items"]
        == []
    )
    late, late_job = submit(h, *submission("late", {"a": b"1234"}, age=90))
    run(h, late_job)
    assert (
        h.client.get(f"/api/v1/comparisons/{comparison_id}", headers=h.headers).json()[
            "base_build_id"
        ]
        == first
    )
    newer, newer_job = submit(h, *submission("newer", {"a": b"new"}, age=30))
    run(h, newer_job)
    selected = h.client.get(
        f"/api/v1/comparisons/{result(h, newer)['result']['comparison_id']}", headers=h.headers
    ).json()
    assert selected["base_build_id"] == target  # 按构建时间，晚上传的 late 不是前版。


@pytest.mark.parametrize(
    "settings,compatible",
    [
        ({"unity_version": "6000.0.99f1", "player_compression": "LZ4"}, True),
        ({"architectures": ["armeabi-v7a"]}, False),
        ({"development_build": True}, False),
        ({"scripting_backend": "Mono"}, False),
        ({"architectures": None}, False),
    ],
)
def test_compatibility_and_manual_cross_group(
    pipeline: Harness, settings: dict, compatible: bool
) -> None:
    h = pipeline
    first, first_job = submit(h, *submission("old", age=120))
    run(h, first_job)
    target, target_job = submit(h, *submission("new", age=60, settings=settings))
    run(h, target_job)
    auto = h.client.get(
        f"/api/v1/comparisons/{result(h, target)['result']['comparison_id']}", headers=h.headers
    ).json()
    assert (auto["base_build_id"] == first) is compatible
    key = str(uuid.uuid4())
    body = {"base_build_id": first, "target_build_id": target}
    response = h.client.post(
        "/api/v1/comparisons", json=body, headers={**h.headers, "Idempotency-Key": key}
    )
    assert response.status_code == 202, response.text
    comparison_id = response.json()["comparison_id"]
    assert (
        h.client.post(
            "/api/v1/comparisons", json=body, headers={**h.headers, "Idempotency-Key": key}
        ).json()["comparison_id"]
        == comparison_id
    )
    with Session(h.engine) as db:
        job = db.scalar(select(Job).where(Job.comparison_id == uuid.UUID(comparison_id)))
        job_id = str(job.id)
    run(h, job_id)
    manual = h.client.get(f"/api/v1/comparisons/{comparison_id}", headers=h.headers).json()
    assert manual["comparable"] is compatible
    assert manual["result"]["quality_gate"] == ("PASS" if compatible else "SKIPPED")
    assert manual["result"]["git_evidence_status"] == "missing"


def test_optional_report_degradation_and_identity_conflict(pipeline: Harness) -> None:
    h = pipeline
    manifest, payloads = submission("future", reports={"unity_report": {"schema_version": 99}})
    build_id, job_id = submit(h, manifest, payloads)
    run(h, job_id)
    assert result(h, build_id)["result"]["coverage"]["unity_report"] == "unsupported"
    manifest, payloads = submission(
        "foreign", reports={"unity_report": {"schema_version": 99, "run_id": str(uuid.uuid4())}}
    )
    build_id = init(h, manifest).json()["build_id"]
    for role, data in payloads.items():
        assert put(h, build_id, role, data).status_code == 201
    response = h.client.post(f"/api/v1/builds/{build_id}/finalize", json={}, headers=h.headers)
    assert response.status_code == 409
    assert result(h, build_id)["upload_state"] == "UPLOADING"


def test_duplicate_consumption_and_fencing(pipeline: Harness) -> None:
    h = pipeline
    build_id, job_id = submit(h, *submission("one"))
    first = claim(h.engine, h.config, uuid.UUID(job_id))
    assert first is not None
    assert claim(h.engine, h.config, uuid.UUID(job_id)) is None
    with Session(h.engine) as db:
        job = db.get(Job, uuid.UUID(job_id))
        job.lease_expires_at = now() - timedelta(seconds=1)
        artifact = db.scalar(
            select(ArtifactBlob)
            .join(BuildArtifact, BuildArtifact.blob_id == ArtifactBlob.id)
            .where(BuildArtifact.build_id == uuid.UUID(build_id), BuildArtifact.role == "apk")
        )
        apk_path = h.config.blob_root / artifact.storage_key
        db.commit()
    second = claim(h.engine, h.config, uuid.UUID(job_id))
    assert second.token > first.token
    snapshot = analyze_apk(apk_path)
    assert not publish_analysis(h.engine, first, snapshot)
    assert publish_analysis(h.engine, second, snapshot)
    assert execute_job(h.engine, h.config, uuid.UUID(job_id)) == "IGNORED"
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(ApkEntryRow)) == len(snapshot.entries)


def test_outbox_recovery_and_broker_failure(pipeline: Harness) -> None:
    h = pipeline
    _, job_id = submit(h, *submission("one"))

    def unavailable(value: str) -> None:
        raise ConnectionError("not logged")

    with pytest.raises(BuildSizeError) as error:
        dispatch_once(h.engine, h.config, unavailable)
    assert error.value.code == "BROKER_UNAVAILABLE"
    with Session(h.engine) as db:
        row = db.scalar(select(Outbox))
        assert row.sent_at is None
        row.next_attempt_at = now() - timedelta(seconds=1)
        db.commit()
    delivered = []
    assert dispatch_once(h.engine, h.config, delivered.append) == 1
    assert delivered == [job_id]
    assert dispatch_once(h.engine, h.config, delivered.append) == 0
    with Session(h.engine) as db:
        row = db.scalar(select(Outbox))
        row.sent_at = now() - timedelta(seconds=h.config.redelivery_seconds + 1)
        db.commit()
    assert dispatch_once(h.engine, h.config, delivered.append) == 1
    assert delivered == [job_id, job_id]


def admin_login(h: Harness, username: str = "admin") -> dict[str, str]:
    password = (
        "fixture-admin-only-password" if username == "admin" else "fixture-viewer-only-password"
    )
    response = h.client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"origin": h.config.origin},
    )
    assert response.status_code == 200
    return {"origin": h.config.origin, "x-csrf-token": response.json()["csrf_token"]}


def test_reanalysis_new_revision_atomic_switch_and_expired_raw(pipeline: Harness) -> None:
    h = pipeline
    build_id, job_id = submit(h, *submission("one"))
    run(h, job_id)
    old_id = result(h, build_id)["analysis_revision"]
    assert (
        h.client.post(
            f"/api/v1/builds/{build_id}/reanalyze",
            json={},
            headers={**h.headers, "Idempotency-Key": str(uuid.uuid4())},
        ).status_code
        == 404
    )
    viewer = admin_login(h, "viewer")
    assert (
        h.client.post(
            f"/api/v1/builds/{build_id}/reanalyze",
            json={},
            headers={**viewer, "Idempotency-Key": str(uuid.uuid4())},
        ).status_code
        == 404
    )
    admin = admin_login(h)
    key = str(uuid.uuid4())
    headers = {**admin, "Idempotency-Key": key}
    response = h.client.post(f"/api/v1/builds/{build_id}/reanalyze", json={}, headers=headers)
    assert response.status_code == 202
    repeated = h.client.post(f"/api/v1/builds/{build_id}/reanalyze", json={}, headers=headers)
    assert repeated.json()["job"]["job_id"] == response.json()["job"]["job_id"]
    assert result(h, build_id)["analysis_revision"] == old_id
    run(h, response.json()["job"]["job_id"])
    assert result(h, build_id)["analysis_revision"] != old_id
    assert (
        h.client.get(
            f"/api/v1/builds/{build_id}/entries?revision_id={old_id}", headers=h.headers
        ).status_code
        == 200
    )
    with Session(h.engine) as db:
        apk = db.scalar(
            select(ArtifactBlob)
            .join(BuildArtifact, BuildArtifact.blob_id == ArtifactBlob.id)
            .where(BuildArtifact.build_id == uuid.UUID(build_id), BuildArtifact.role == "apk")
        )
        path = h.config.blob_root / apk.storage_key
    path.unlink()
    assert (
        h.client.post(
            f"/api/v1/builds/{build_id}/reanalyze",
            json={},
            headers={**admin, "Idempotency-Key": str(uuid.uuid4())},
        ).status_code
        == 410
    )
    assert h.client.get(f"/api/v1/builds/{build_id}/entries", headers=h.headers).status_code == 200
    assert (
        h.client.get(
            f"/api/v1/builds/{build_id}/artifacts/apk/download", headers=h.headers
        ).status_code
        == 410
    )


def test_failed_reanalysis_keeps_old_result(pipeline: Harness) -> None:
    h = pipeline
    build_id, job_id = submit(h, *submission("one"))
    run(h, job_id)
    old_id = result(h, build_id)["analysis_revision"]
    headers = {**admin_login(h), "Idempotency-Key": str(uuid.uuid4())}
    response = h.client.post(f"/api/v1/builds/{build_id}/reanalyze", json={}, headers=headers)
    with Session(h.engine) as db:
        apk = db.scalar(
            select(ArtifactBlob)
            .join(BuildArtifact, BuildArtifact.blob_id == ArtifactBlob.id)
            .where(BuildArtifact.build_id == uuid.UUID(build_id), BuildArtifact.role == "apk")
        )
        path = h.config.blob_root / apk.storage_key
    path.chmod(0o640)
    data = bytearray(path.read_bytes())
    data[30 + len("assets/中文 空格.bin".encode())] ^= 1
    path.write_bytes(data)
    with pytest.raises(BuildSizeError):
        run(h, response.json()["job"]["job_id"])
    value = result(h, build_id)
    assert value["analysis_state"] == "FAILED"
    assert value["analysis_revision"] == old_id
    assert h.client.get(f"/api/v1/builds/{build_id}/entries", headers=h.headers).status_code == 200


def test_upload_headers_expiry_quota_and_web_csrf(pipeline: Harness) -> None:

    h = pipeline
    manifest, payloads = submission("one")
    build_id = init(h, manifest).json()["build_id"]
    assert (
        h.client.put(
            f"/api/v1/builds/{build_id}/artifacts/apk", content=payloads["apk"], headers=h.headers
        ).status_code
        == 415
    )
    assert (
        h.client.post(
            f"/api/v1/builds/{build_id}/finalize", json={}, headers=h.other_headers
        ).status_code
        == 404
    )
    with Session(h.engine) as db:
        build = db.get(Build, uuid.UUID(build_id))
        build.upload_expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert put(h, build_id, "apk", payloads["apk"]).status_code == 409
    assert init(h, manifest).status_code == 200
    admin_login(h)
    assert (
        h.client.post(
            "/api/v1/projects/demo-game/builds",
            json=manifest,
            headers={"Idempotency-Key": manifest["run_id"]},
        ).status_code
        == 403
    )
    with Session(h.engine) as db:
        for role in ("apk", "build_meta"):
            db.add(
                UploadLease(
                    project_id=h.project_id,
                    build_id=uuid.UUID(build_id),
                    role=role,
                    expires_at=now() + timedelta(minutes=1),
                )
            )
        db.commit()
    assert put(h, build_id, "apk", payloads["apk"]).status_code == 429


def test_bounded_and_duplicate_json_requests(pipeline: Harness) -> None:
    h = pipeline
    response = h.client.post(
        "/api/v1/projects/demo-game/builds",
        content=b'{"x":1,"x":2}',
        headers={**h.headers, "Content-Type": "application/json"},
    )
    assert response.status_code == 422
    response = h.client.post(
        "/api/v1/projects/demo-game/builds",
        content=b"x" * (1024 * 1024 + 1),
        headers={**h.headers, "Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert "request_id" in response.json()["error"]


def test_fixed_baseline_and_future_clock(pipeline: Harness) -> None:
    h = pipeline
    first, first_job = submit(h, *submission("old", age=180))
    run(h, first_job)
    second, second_job = submit(h, *submission("middle", age=120))
    run(h, second_job)
    with Session(h.engine) as db:
        db.get(Project, h.project_id).fixed_baseline_id = uuid.UUID(first)
        db.commit()
    target, target_job = submit(h, *submission("target", age=60))
    run(h, target_job)
    comparison = h.client.get(
        f"/api/v1/comparisons/{result(h, target)['result']['comparison_id']}", headers=h.headers
    ).json()
    assert comparison["base_build_id"] == first
    assert comparison["selection_reason"] == "fixed_baseline"
    future, future_job = submit(h, *submission("future", age=-60))
    run(h, future_job)
    comparison = h.client.get(
        f"/api/v1/comparisons/{result(h, future)['result']['comparison_id']}", headers=h.headers
    ).json()
    assert comparison["status"] == "NO_BASELINE"
    assert comparison["selection_reason"] == "incomplete_or_invalid_clock"


def test_superseded_job_and_transient_retry_limit(pipeline: Harness) -> None:
    from buildsize.jobs.worker import fail

    h = pipeline
    build_id, job_id = submit(h, *submission("one"))
    first = claim(h.engine, h.config, uuid.UUID(job_id))
    assert first is not None
    headers = {**admin_login(h), "Idempotency-Key": str(uuid.uuid4())}
    response = h.client.post(f"/api/v1/builds/{build_id}/reanalyze", json={}, headers=headers)
    with Session(h.engine) as db:
        blob = db.scalar(
            select(ArtifactBlob)
            .join(BuildArtifact, BuildArtifact.blob_id == ArtifactBlob.id)
            .where(BuildArtifact.build_id == uuid.UUID(build_id), BuildArtifact.role == "apk")
        )
        snapshot = analyze_apk(h.config.blob_root / blob.storage_key)
    assert not publish_analysis(h.engine, first, snapshot)
    assert result(h, build_id)["analysis_revision"] is None
    retry_id = uuid.UUID(response.json()["job"]["job_id"])
    for _ in range(3):
        lease = claim(h.engine, h.config, retry_id)
        assert lease is not None
        fail(h.engine, lease, "STORAGE_IO_ERROR", transient=True)
    assert claim(h.engine, h.config, retry_id) is None
    assert result(h, build_id)["analysis_state"] == "FAILED"


@pytest.mark.parametrize(
    "link",
    [
        "javascript:alert(1)",
        "https://untrusted.invalid/build",
        "https://u:password@jenkins.example.invalid/build",
        "https://jenkins.example.invalid/build?TOKEN=credential-sentinel",
    ],
)
def test_unsafe_ci_links_never_persist(pipeline: Harness, link: str, caplog) -> None:
    h = pipeline
    manifest, _ = submission("unsafe")
    manifest["ci"]["build_url"] = link
    response = init(h, manifest)
    assert response.status_code == 422
    assert link not in response.text
    assert "credential-sentinel" not in caplog.text
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(Build)) == 0


def test_summary_does_not_return_unbounded_directory_lists(pipeline: Harness) -> None:
    h = pipeline
    build_id, job_id = submit(
        h, *submission("many-dirs", {f"assets/dir{i}/a": b"x" for i in range(250)})
    )
    run(h, job_id)
    apk = result(h, build_id)["result"]["apk"]
    assert apk["directories_count"] > 200
    assert "directories" not in apk and "duplicate_content" not in apk
    assert (
        h.client.get(f"/api/v1/builds/{build_id}/entries?limit=200", headers=h.headers).json()[
            "next_offset"
        ]
        == 200
    )


def test_deadline_prevents_result_publish(pipeline: Harness) -> None:
    from dataclasses import replace

    h = pipeline
    build_id, job_id = submit(h, *submission("deadline"))
    lease = claim(h.engine, h.config, uuid.UUID(job_id))
    with Session(h.engine) as db:
        blob = db.scalar(
            select(ArtifactBlob)
            .join(BuildArtifact, BuildArtifact.blob_id == ArtifactBlob.id)
            .where(BuildArtifact.build_id == uuid.UUID(build_id), BuildArtifact.role == "apk")
        )
        snapshot = analyze_apk(h.config.blob_root / blob.storage_key)
    with pytest.raises(BuildSizeError) as error:
        publish_analysis(h.engine, replace(lease, deadline=now() - timedelta(seconds=1)), snapshot)
    assert error.value.code == "ANALYSIS_TIMEOUT"
    assert result(h, build_id)["analysis_revision"] is None


def test_concurrent_initialization_and_finalize(pipeline: Harness) -> None:
    from concurrent.futures import ThreadPoolExecutor

    if not os.getenv("BUILDSIZE_TEST_DATABASE_URL"):
        pytest.skip("NOT_VERIFIED：该竞态检查需要 PostgreSQL 行锁")
    h = pipeline
    manifest, payloads = submission("race")

    def initialize_request(_: int):
        with TestClient(h.client.app) as client:
            return client.post(
                "/api/v1/projects/demo-game/builds",
                json=manifest,
                headers={**h.headers, "Idempotency-Key": manifest["run_id"]},
            )

    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(initialize_request, range(4)))
    assert sorted(response.status_code for response in responses) == [200, 200, 200, 201]
    assert len({response.json()["build_id"] for response in responses}) == 1
    build_id = responses[0].json()["build_id"]
    for role, data in payloads.items():
        assert put(h, build_id, role, data).status_code == 201

    def seal(_: int):
        with TestClient(h.client.app) as client:
            return client.post(f"/api/v1/builds/{build_id}/finalize", json={}, headers=h.headers)

    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(seal, range(4)))
    assert all(response.status_code == 202 for response in responses)
    assert len({response.json()["job"]["job_id"] for response in responses}) == 1


def test_stream_disconnect_cleans_partial_file(pipeline: Harness) -> None:
    import asyncio

    h = pipeline
    manifest, payloads = submission("disconnect")
    build_id = init(h, manifest).json()["build_id"]
    raw = payloads["apk"]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "PUT",
        "scheme": "http",
        "path": f"/api/v1/builds/{build_id}/artifacts/apk",
        "raw_path": f"/api/v1/builds/{build_id}/artifacts/apk".encode(),
        "query_string": b"",
        "headers": [
            (b"authorization", h.headers["authorization"].encode()),
            (b"content-type", b"application/octet-stream"),
            (b"content-length", str(len(raw)).encode()),
            (b"x-content-sha256", hashlib.sha256(raw).hexdigest().encode()),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    sent = []
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": raw[:10], "more_body": True}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(h.client.app(scope, receive, send))
    assert list((h.config.blob_root / ".tmp").iterdir()) == []
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(BuildArtifact)) == 0
        assert db.scalar(select(func.count()).select_from(UploadLease)) == 0


def test_observed_abi_mismatch_excluded_from_baseline(pipeline: Harness) -> None:
    h = pipeline
    old, job = submit(h, *submission("old", age=120))
    run(h, job)
    target, job = submit(h, *submission("bad-abi", {"lib/x86/libx.so": b"native"}, age=60))
    run(h, job)
    value = result(h, target)
    comparison = h.client.get(
        f"/api/v1/comparisons/{value['result']['comparison_id']}", headers=h.headers
    ).json()
    assert comparison["status"] == "NO_BASELINE"
    assert comparison["selection_reason"] == "apk_abi_mismatch"
    assert any("ABI" in warning for warning in value["result"]["warnings"])


def test_close_error_still_releases_upload_lease(pipeline: Harness, monkeypatch) -> None:
    from buildsize.storage.local import LocalUpload

    h = pipeline
    manifest, payloads = submission("close-error")
    build_id = init(h, manifest).json()["build_id"]
    original = LocalUpload.close

    def bad_close(upload):
        original(upload)
        raise OSError("fixture-only I/O failure")

    monkeypatch.setattr(LocalUpload, "close", bad_close)
    response = put(h, build_id, "apk", payloads["apk"])
    assert response.status_code == 503
    assert list((h.config.blob_root / ".tmp").iterdir()) == []
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(UploadLease)) == 0
    # 已完整登记的产物仍可幂等重试，不会因响应中断而换文件。
    assert put(h, build_id, "apk", payloads["apk"]).status_code == 200


def test_openapi_has_strict_submission_and_binary_upload(pipeline: Harness) -> None:
    schema = pipeline.client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/projects/{project_key}/builds"]["post"]
    assert "201" in operation["responses"] and "200" in operation["responses"]
    assert schema["components"]["schemas"]["Submission"]["additionalProperties"] is False
    upload = schema["paths"]["/api/v1/builds/{build_id}/artifacts/{role}"]["put"]
    assert "application/octet-stream" in upload["requestBody"]["content"]
    assert operation["responses"]["409"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ErrorEnvelope"
    )


def test_database_restart_preserves_build_index_and_blob(pipeline: Harness) -> None:
    import re
    import subprocess
    import time

    name = os.getenv("BUILDSIZE_TEST_POSTGRES_CONTAINER", "")
    if not re.fullmatch(r"buildsize-test-[a-f0-9]{32}", name):
        pytest.skip("NOT_VERIFIED：数据库重启只能操作验证脚本创建的一次性容器")
    h = pipeline
    manifest, payloads = submission("restart")
    build_id, job_id = submit(h, manifest, payloads)
    run(h, job_id)
    before = result(h, build_id)
    h.engine.dispose()
    subprocess.run(
        ["docker", "restart", "--time", "5", name],
        check=True,
        stdout=subprocess.DEVNULL,
        timeout=30,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if h.client.get("/api/v1/health/ready").status_code == 200:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("测试数据库重启后未就绪")
    after = result(h, build_id)
    assert after["analysis_revision"] == before["analysis_revision"]
    assert after["result"] == before["result"]
    assert h.client.get(f"/api/v1/builds/{build_id}/entries", headers=h.headers).json()["items"]
    assert (
        h.client.get(f"/api/v1/builds/{build_id}/artifacts/apk/download", headers=h.headers).content
        == payloads["apk"]
    )
