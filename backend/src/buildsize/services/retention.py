"""默认只生成审计计划；应用前在项目锁内重算。原始文件与派生索引分开保护。"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session, load_only

from buildsize.config import Config
from buildsize.db.models import (
    AnalysisRevision,
    ApkEntryRow,
    ArtifactBlob,
    AssetImporterRow,
    AuditEvent,
    Build,
    BuildArtifact,
    Comparison,
    GitFileChangeRow,
    GitRangeRow,
    Job,
    Project,
    UnityContainerRow,
    UnityGroupRow,
    UnityRecordRow,
    UploadLease,
)
from buildsize.domain.errors import BuildSizeError
from buildsize.services.common import aware, fingerprint, now
from buildsize.storage.local import BlobStorage, LocalBlobStorage

ACTIVE = ("QUEUED", "RUNNING")


def storage_identity(config: Config) -> str:
    root = config.blob_root.resolve()
    info = root.stat() if root.exists() else None
    return fingerprint([str(root), info.st_dev if info else None, info.st_ino if info else None])


def plan(db: Session, config: Config, project: Project, at: datetime) -> dict[str, Any]:
    if (
        db.scalar(select(func.count()).select_from(Build).where(Build.project_id == project.id))
        or 0
    ) > 10000:
        raise BuildSizeError("RETENTION_LIMIT", "项目构建数超出单次清理预算，未执行截断清理")
    builds = db.scalars(
        select(Build)
        .where(Build.project_id == project.id)
        .options(
            load_only(
                Build.id,
                Build.pinned,
                Build.current_analysis_id,
                Build.requested_analysis_id,
                Build.created_at,
                Build.upload_state,
                Build.upload_expires_at,
            )
        )
    ).all()
    protected = {row.id for row in builds if row.pinned or row.current_analysis_id is not None}
    if project.fixed_baseline_id:
        protected.add(project.fixed_baseline_id)
    protected.update(
        db.scalars(
            select(Job.build_id)
            .join(Build)
            .where(Build.project_id == project.id, Job.state.in_(ACTIVE))
        )
    )
    protected.update(
        db.scalars(
            select(UploadLease.build_id).where(
                UploadLease.project_id == project.id, UploadLease.expires_at > now()
            )
        )
    )
    artifacts = db.scalars(
        select(BuildArtifact).join(Build).where(Build.project_id == project.id)
    ).all()
    by_build = {row.id: row for row in builds}
    expire = []
    for artifact in artifacts:
        build = by_build[artifact.build_id]
        days = config.apk_retention_days if artifact.role == "apk" else config.report_retention_days
        if (
            artifact.expired_at is None
            and build.id not in protected
            and aware(build.created_at) < at - timedelta(days=days)
            and not (
                build.upload_state == "UPLOADING"
                and build.upload_expires_at
                and aware(build.upload_expires_at) > at
            )
        ):
            expire.append(str(artifact.id))
    expire_set = set(expire)
    referenced = {
        row.blob_id
        for row in artifacts
        if (row.expired_at is None and str(row.id) not in expire_set) or row.build_id in protected
    }
    blobs = db.scalars(
        select(ArtifactBlob).where(ArtifactBlob.project_id == project.id).limit(100001)
    ).all()
    if len(blobs) > 100000:
        raise BuildSizeError("RETENTION_LIMIT", "Blob 数超出预算，未执行截断清理")
    mark, remove, cancel = [], [], []
    for blob in blobs:
        if blob.id in referenced:
            if blob.gc_marked_at is not None:
                cancel.append(str(blob.id))
            continue
        if blob.deleted_at is not None or aware(blob.created_at) > at - timedelta(days=1):
            continue
        if blob.gc_marked_at is None:
            mark.append(str(blob.id))
        elif aware(blob.gc_marked_at) <= at - timedelta(days=1):
            remove.append(str(blob.id))
    # 当前/请求中与任意比较引用的修订全部保护，不能只看构建的年龄。
    revision_refs = {
        value for b in builds for value in (b.current_analysis_id, b.requested_analysis_id) if value
    }
    for before, after in db.execute(
        select(Comparison.base_revision_id, Comparison.target_revision_id).where(
            Comparison.project_id == project.id
        )
    ):
        revision_refs.update(value for value in (before, after) if value)
    revision_refs.update(
        db.scalars(
            select(Job.analysis_revision_id)
            .join(Build)
            .where(Build.project_id == project.id, Job.state.in_(ACTIVE))
        )
    )
    revisions = db.scalars(
        select(AnalysisRevision)
        .options(
            load_only(AnalysisRevision.id, AnalysisRevision.status, AnalysisRevision.created_at)
        )
        .limit(100001)
        .join(Build, Build.id == AnalysisRevision.build_id)
        .where(Build.project_id == project.id)
    ).all()
    if len(revisions) > 100000:
        raise BuildSizeError("RETENTION_LIMIT", "修订数超出预算，未执行截断清理")
    indices = [
        str(row.id)
        for row in revisions
        if row.id not in revision_refs
        and row.status != "EXPIRED"
        and aware(row.created_at) < at - timedelta(days=config.metrics_retention_days)
    ]
    uploads = [
        str(b.id)
        for b in builds
        if b.upload_state == "UPLOADING"
        and b.upload_expires_at
        and aware(b.upload_expires_at) <= at
        and b.id not in protected
    ]
    payload = {
        "schema_version": 1,
        "project_key": project.key,
        "as_of": at.isoformat(),
        "policy": {
            "storage_identity": storage_identity(config),
            "apk_days": config.apk_retention_days,
            "report_days": config.report_retention_days,
            "metrics_days": config.metrics_retention_days,
            "current_raw_protected": True,
            "gc_grace_hours": 24,
        },
        "protected_builds": sorted(str(i) for i in protected),
        "expire_artifacts": sorted(expire),
        "mark_blobs": sorted(mark),
        "delete_blobs": sorted(remove),
        "cancel_marks": sorted(cancel),
        "expire_indices": sorted(indices),
        "expire_uploads": sorted(uploads),
    }
    return {**payload, "plan_sha256": fingerprint(payload)}


def maintenance(
    engine: Engine,
    config: Config,
    project_key: str,
    approved: dict[str, Any] | None = None,
    storage: BlobStorage | None = None,
) -> dict[str, Any]:
    storage = storage or LocalBlobStorage(config.blob_root)
    with Session(engine) as db, db.begin():
        project = db.scalar(select(Project).where(Project.key == project_key).with_for_update())
        if project is None:
            raise BuildSizeError("NOT_FOUND", "项目不存在")
        at = datetime.fromisoformat(approved["as_of"]) if approved else now()
        if (
            at.tzinfo is None
            or at > now() + timedelta(seconds=5)
            or at < now() - timedelta(hours=24)
        ):
            raise BuildSizeError("RETENTION_PLAN_EXPIRED", "清理计划过期或时间无效")
        value = plan(db, config, project, at)
        if approved is not None and value != approved:
            raise BuildSizeError(
                "RETENTION_PLAN_CHANGED", "引用、保护状态或配置已变化，重新生成计划"
            )
        if approved is not None:
            for artifact_id in value["expire_artifacts"]:
                row = db.get(BuildArtifact, uuid.UUID(artifact_id))
                assert row is not None
                row.expired_at = now()
            for build_id in value["expire_uploads"]:
                build = db.get(Build, uuid.UUID(build_id))
                assert build is not None
                build.upload_state = "EXPIRED"
            for blob_id in value["mark_blobs"]:
                blob = db.get(ArtifactBlob, uuid.UUID(blob_id))
                assert blob is not None
                blob.gc_marked_at = now()
            for blob_id in value["cancel_marks"]:
                blob = db.get(ArtifactBlob, uuid.UUID(blob_id))
                assert blob is not None
                blob.gc_marked_at = None
            for blob_id in value["delete_blobs"]:
                blob = db.get(ArtifactBlob, uuid.UUID(blob_id))
                assert blob is not None
                # _path 只接受服务端规范键，禁止客户端路径、软链接和目录递归删除。
                storage.delete(blob.storage_key)
                blob.deleted_at = now()
            for revision_id in value["expire_indices"]:
                rid = uuid.UUID(revision_id)
                container_ids = select(UnityContainerRow.id).where(
                    UnityContainerRow.revision_id == rid
                )
                db.execute(
                    delete(UnityRecordRow).where(UnityRecordRow.container_id.in_(container_ids))
                )
                range_ids = select(GitRangeRow.id).where(GitRangeRow.revision_id == rid)
                db.execute(delete(GitFileChangeRow).where(GitFileChangeRow.range_id.in_(range_ids)))
                for model in (
                    ApkEntryRow,
                    AssetImporterRow,
                    UnityGroupRow,
                    UnityContainerRow,
                    GitRangeRow,
                ):
                    db.execute(delete(model).where(model.revision_id == rid))
                revision = db.get(AnalysisRevision, rid)
                assert revision is not None
                revision.status = "EXPIRED"
                revision.summary = {"stage": "expired", "reason": "retention"}
        db.add(
            AuditEvent(
                project_id=project.id,
                actor="local-ops",
                action="retention.apply" if approved else "retention.plan",
                object_id=value["plan_sha256"],
            )
        )
        return {**value, "mode": "applied" if approved else "dry_run"}


def temporary_maintenance(
    engine: Engine, config: Config, approved: dict[str, Any] | None = None
) -> dict[str, Any]:
    import re
    import stat

    with Session(engine) as db, db.begin():
        list(db.scalars(select(Project).order_by(Project.id).with_for_update()))
        active = db.scalar(select(UploadLease.id).where(UploadLease.expires_at > now()).limit(1))
        root = LocalBlobStorage(config.blob_root).root / ".tmp"
        if root.is_symlink():
            raise BuildSizeError("INVALID_STORAGE_KEY", "临时目录不允许软链接")
        files: list[dict[str, Any]] = []
        cutoff = now() - timedelta(seconds=max(86400, config.upload_timeout_seconds + 60))
        if root.exists() and active is None:
            for index, path in enumerate(root.iterdir()):
                if index >= 10000:
                    raise BuildSizeError("RETENTION_LIMIT", "临时文件预算超限")
                info = path.lstat()
                if (
                    re.fullmatch(r"tmp[a-z0-9_]{8}\.part", path.name)
                    and stat.S_ISREG(info.st_mode)
                    and info.st_mtime < cutoff.timestamp()
                ):
                    files.append(
                        {
                            "name": path.name,
                            "inode": info.st_ino,
                            "bytes": info.st_size,
                            "mtime_ns": info.st_mtime_ns,
                        }
                    )
        value: dict[str, Any] = {
            "schema_version": 1,
            "storage_identity": storage_identity(config),
            "files": sorted(files, key=lambda row: row["name"]),
            "active_uploads": active is not None,
        }
        value["plan_sha256"] = fingerprint(value)
        if approved is not None:
            if value != approved:
                raise BuildSizeError(
                    "RETENTION_PLAN_CHANGED", "临时文件或活跃租约变化，重新生成计划"
                )
            for row in files:
                (root / row["name"]).unlink()
        db.add(
            AuditEvent(
                actor="local-ops",
                action="temps.apply" if approved else "temps.plan",
                object_id=value["plan_sha256"],
            )
        )
        return {**value, "mode": "applied" if approved else "dry_run"}
