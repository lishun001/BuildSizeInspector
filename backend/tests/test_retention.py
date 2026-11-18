import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_pipeline import Harness, init, put, result, run, submission, submit
from test_pipeline import pipeline as pipeline

from buildsize.db.models import ArtifactBlob, AuditEvent, Build, BuildArtifact, Project
from buildsize.domain.errors import BuildSizeError
from buildsize.services.common import now
from buildsize.services.retention import maintenance


def approved(value):
    return {k: v for k, v in value.items() if k != "mode"}


def age(h, build_id, days=400):
    with Session(h.engine) as db:
        build = db.get(Build, uuid.UUID(build_id))
        build.created_at = now() - timedelta(days=days)
        for blob in db.scalars(select(ArtifactBlob)):
            blob.created_at = now() - timedelta(days=days)
        db.commit()


def test_dry_run_shared_blob_and_two_phase_cleanup(pipeline: Harness):
    h = pipeline
    manifest, payloads = submission("one")
    first = init(h, manifest).json()["build_id"]
    assert put(h, first, "apk", payloads["apk"]).status_code == 201
    other, _ = submission("two")
    other["artifacts"][0] = dict(manifest["artifacts"][0])
    second = init(h, other).json()["build_id"]
    assert put(h, second, "apk", payloads["apk"]).status_code == 201
    age(h, first)
    age(h, second)
    with Session(h.engine) as db:
        build = db.get(Build, uuid.UUID(second))
        build.pinned = True
        first_row = db.scalar(
            select(BuildArtifact).where(BuildArtifact.build_id == uuid.UUID(first))
        )
        blob = db.get(ArtifactBlob, first_row.blob_id)
        key = blob.storage_key
        db.get(Build, uuid.UUID(first)).upload_expires_at = now() - timedelta(days=1)
        db.commit()
    plan = maintenance(h.engine, h.config, "demo-game")
    assert plan["mode"] == "dry_run" and plan["expire_artifacts"] and not plan["delete_blobs"]
    assert (h.config.blob_root / key).exists()
    applied = maintenance(h.engine, h.config, "demo-game", approved(plan))
    assert not applied["mark_blobs"]
    with Session(h.engine) as db:
        build = db.get(Build, uuid.UUID(second))
        build.pinned = False
        build.upload_expires_at = now() - timedelta(days=1)
        db.commit()
    plan = maintenance(h.engine, h.config, "demo-game")
    assert plan["mark_blobs"] and not plan["delete_blobs"]
    maintenance(h.engine, h.config, "demo-game", approved(plan))
    assert (h.config.blob_root / key).exists()
    with Session(h.engine) as db:
        blob = db.scalar(select(ArtifactBlob))
        blob.gc_marked_at = now() - timedelta(days=2)
        db.commit()
    plan = maintenance(h.engine, h.config, "demo-game")
    assert plan["delete_blobs"]
    maintenance(h.engine, h.config, "demo-game", approved(plan))
    assert not (h.config.blob_root / key).exists()
    assert (
        h.client.get(
            f"/api/v1/builds/{first}/artifacts/apk/download", headers=h.headers
        ).status_code
        == 410
    )
    with Session(h.engine) as db:
        assert {"retention.plan", "retention.apply"} <= set(db.scalars(select(AuditEvent.action)))


def test_current_fixed_and_pin_protect_and_changed_plan_rejected(pipeline: Harness):
    h = pipeline
    first, job = submit(h, *submission("analyzed"))
    run(h, job)
    age(h, first)
    plan = maintenance(h.engine, h.config, "demo-game")
    assert first in plan["protected_builds"] and not plan["expire_artifacts"]
    manifest, payloads = submission("upload")
    second = init(h, manifest).json()["build_id"]
    put(h, second, "apk", payloads["apk"])
    age(h, second)
    with Session(h.engine) as db:
        build = db.get(Build, uuid.UUID(second))
        build.upload_expires_at = now() - timedelta(days=1)
        db.commit()
    plan = maintenance(h.engine, h.config, "demo-game")
    from dataclasses import replace

    with pytest.raises(BuildSizeError, match="保护状态"):
        maintenance(
            h.engine,
            replace(h.config, blob_root=h.tmp_path / "different-volume"),
            "demo-game",
            approved(plan),
        )
    with Session(h.engine) as db:
        db.get(Build, uuid.UUID(second)).pinned = True
        db.commit()
    with pytest.raises(BuildSizeError, match="保护状态"):
        maintenance(h.engine, h.config, "demo-game", approved(plan))
    with Session(h.engine) as db:
        db.get(Build, uuid.UUID(second)).pinned = False
        db.get(Project, h.project_id).fixed_baseline_id = uuid.UUID(second)
        db.commit()
    assert second in maintenance(h.engine, h.config, "demo-game")["protected_builds"]
    assert result(h, first)["result"]["apk"]["apk_file_bytes"] > 0


def test_stale_temps_keep_active_upload_and_reject_links(pipeline: Harness):
    import os

    from buildsize.services.retention import temporary_maintenance

    h = pipeline
    root = h.config.blob_root / ".tmp"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "tmpabcdefgh.part"
    path.write_bytes(b"partial")
    os.utime(path, (0, 0))
    external = h.tmp_path / "must-keep"
    external.write_bytes(b"keep")
    (root / "tmpabcd1234.part").symlink_to(external)
    fresh = root / "tmpabcdefgh.part2"
    fresh.write_bytes(b"not managed")
    plan = temporary_maintenance(h.engine, h.config)
    assert [row["name"] for row in plan["files"]] == [path.name]
    assert path.exists()
    temporary_maintenance(h.engine, h.config, approved(plan))
    assert not path.exists() and external.read_bytes() == b"keep" and fresh.exists()


def test_pin_api_scope_and_diagnostics(pipeline: Harness):
    from test_pipeline import admin_login

    h = pipeline
    bid, job = submit(h, *submission("pin"))
    run(h, job)
    path = f"/api/v1/builds/{bid}/pin"
    assert h.client.put(path, json={"pinned": True}, headers=h.headers).status_code == 404
    web = admin_login(h)
    assert h.client.put(path, json={"pinned": True}, headers=web).status_code == 200
    assert result(h, bid)["pinned"] is True
    assert h.client.get("/api/v1/projects/demo-game/diagnostics").json()["jobs"]["SUCCEEDED"] == 1
    assert h.client.put(path, json={"pinned": False}, headers={}).status_code == 403
    admin_login(h, "viewer")
    assert h.client.get("/api/v1/projects/demo-game/diagnostics").status_code == 404


def test_only_unreferenced_old_indices_expire(pipeline: Harness):
    import hashlib

    from buildsize.db.models import AnalysisRevision, ApkEntryRow

    h = pipeline
    bid, job = submit(h, *submission("index"))
    run(h, job)
    with Session(h.engine) as db:
        old = AnalysisRevision(
            build_id=uuid.UUID(bid),
            input_sha256="a" * 64,
            parser_version="old",
            status="READY",
            created_at=now() - timedelta(days=400),
            summary={},
        )
        db.add(old)
        db.flush()
        rid = str(old.id)
        db.add(
            ApkEntryRow(
                revision_id=old.id,
                entry_index=0,
                raw_path="old",
                normalized_path="old",
                path_hash=hashlib.sha256(b"old").hexdigest(),
                is_directory=False,
                compressed_bytes=1,
                uncompressed_bytes=1,
                compression_method=0,
                crc32=0,
                content_sha256="a" * 64,
                category="other",
            )
        )
        db.commit()
    plan = maintenance(h.engine, h.config, "demo-game")
    assert rid in plan["expire_indices"]
    maintenance(h.engine, h.config, "demo-game", approved(plan))
    assert (
        h.client.get(
            f"/api/v1/builds/{bid}/entries?revision_id={rid}", headers=h.headers
        ).status_code
        == 409
    )
    assert h.client.get(f"/api/v1/builds/{bid}/entries", headers=h.headers).json()["items"]
