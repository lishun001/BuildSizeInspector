"""构建提交事务。不解压条目，不访问消息队列，不执行上传内容。"""

import uuid
from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from buildsize.analyzers.apk_zip import Limits, check_apk_container
from buildsize.auth.service import Principal, project_access
from buildsize.config import Config
from buildsize.db.models import (
    AnalysisRevision,
    ArtifactBlob,
    AuditEvent,
    Build,
    BuildArtifact,
    Job,
    Outbox,
    Project,
    RepositorySnapshot,
    UploadLease,
)
from buildsize.domain.contracts import (
    AssetImporters,
    BuildMeta,
    Contract,
    GitChanges,
    Submission,
    UnityReport,
)
from buildsize.domain.errors import BuildSizeError
from buildsize.domain.urls import validate_public_url
from buildsize.domain.validation import read_json, validate_identity
from buildsize.services.common import aware, fingerprint, now
from buildsize.storage.apk_download import download_target, protect_url, reveal_url
from buildsize.storage.local import BlobStorage, StagedUpload

PARSER_SET = "apk-zip-1:unity-1:git-1:rules-1:m5-1"
REPORTS: dict[str, type[Contract]] = {
    "unity_report": UnityReport,
    "asset_importers": AssetImporters,
    "git_changes": GitChanges,
}


def validate_link(config: Config, value: str) -> None:
    validate_public_url(value, config.trusted_link_hosts)


def audit(
    db: Session,
    actor: Principal,
    project_id: uuid.UUID,
    action: str,
    object_id: uuid.UUID,
    request_id: str | None = None,
) -> None:
    db.add(
        AuditEvent(
            project_id=project_id,
            actor=str(actor.user_id or f"ci:{actor.project_id}"),
            action=action,
            object_id=str(object_id),
            request_id=request_id,
        )
    )


def authorized_build(
    db: Session,
    actor: Principal,
    build_id: uuid.UUID,
    *,
    scope: str = "builds:read",
    admin: bool = False,
) -> Build:
    build = db.get(Build, build_id)
    if build is None:
        raise BuildSizeError("NOT_FOUND", "资源不存在或无权访问")
    project = db.get(Project, build.project_id)
    assert project is not None
    project_access(db, actor, project.key, scope=scope, admin=admin)
    return build


def initialize(
    db: Session,
    config: Config,
    actor: Principal,
    manifest: Submission,
    request_id: str | None = None,
) -> tuple[Build, bool]:
    identity = [
        manifest.ci.instance_id,
        manifest.ci.job_full_name,
        manifest.ci.build_number,
        manifest.variant,
    ]
    if any(
        len(value) > limit for value, limit in zip(identity, [200, 500, 128, 200], strict=True)
    ) or (sum(len(value.encode("utf-8")) for value in identity) > 1500):
        raise BuildSizeError("IDENTITY_FIELD_TOO_LONG", "CI 身份或变体超过可索引长度限制")
    project = project_access(
        db, actor, manifest.project_key, scope="builds:submit", admin=actor.user_id is not None
    )
    validate_link(config, manifest.ci.build_url)
    db.execute(select(Project).where(Project.id == project.id).with_for_update())
    candidates = db.scalars(
        select(Build)
        .where(
            or_(
                Build.run_id == uuid.UUID(manifest.run_id),
                (Build.project_id == project.id)
                & (Build.ci_instance_id == manifest.ci.instance_id)
                & (Build.ci_job_full_name == manifest.ci.job_full_name)
                & (Build.ci_build_number == manifest.ci.build_number)
                & (Build.variant == manifest.variant),
            )
        )
        .with_for_update()
    ).all()
    if candidates:
        build = candidates[0]
        if (
            len(candidates) != 1
            or build.project_id != project.id
            or (build.manifest_sha256 != manifest.manifest_sha256())
        ):
            raise BuildSizeError("BUILD_IDENTITY_CONFLICT", "构建身份或产物清单冲突")
        if build.upload_state != "SEALED" and (
            build.upload_state == "EXPIRED"
            or build.upload_expires_at is None
            or aware(build.upload_expires_at) <= now()
        ):
            build.upload_state = "UPLOADING"
            build.upload_expires_at = now() + timedelta(hours=24)
        return build, False
    if any(
        item.bytes > config.upload_max_bytes for item in manifest.artifacts if item.role == "apk"
    ):
        raise BuildSizeError("UPLOAD_TOO_LARGE", "APK 超过上传限制")
    build = Build(
        project_id=project.id,
        run_id=uuid.UUID(manifest.run_id),
        ci_instance_id=manifest.ci.instance_id,
        ci_job_full_name=manifest.ci.job_full_name,
        ci_build_number=manifest.ci.build_number,
        variant=manifest.variant,
        manifest_sha256=manifest.manifest_sha256(),
        manifest=manifest.model_dump(mode="json"),
        upload_expires_at=now() + timedelta(hours=24),
    )
    db.add(build)
    db.flush()
    audit(db, actor, project.id, "build.initialize", build.id, request_id)
    return build, True


def artifact_rows(db: Session, build_id: uuid.UUID) -> dict[str, BuildArtifact]:
    return {
        row.role: row
        for row in db.scalars(select(BuildArtifact).where(BuildArtifact.build_id == build_id))
    }


def begin_upload(
    db: Session,
    config: Config,
    actor: Principal,
    build_id: uuid.UUID,
    role: str,
    length: int,
    sha256: str,
) -> tuple[uuid.UUID | None, uuid.UUID]:
    build = authorized_build(
        db, actor, build_id, scope="builds:submit", admin=actor.user_id is not None
    )
    db.execute(select(Project).where(Project.id == build.project_id).with_for_update())
    db.refresh(build, with_for_update=True)
    item = next(
        (item for item in Submission.model_validate(build.manifest).artifacts if item.role == role),
        None,
    )
    if item is None:
        raise BuildSizeError("ARTIFACT_ROLE_INVALID", "清单未声明该角色")
    limit = config.upload_max_bytes if role == "apk" else 64 * 1024**2
    if length > limit:
        raise BuildSizeError("UPLOAD_TOO_LARGE", "上传长度超过限制")
    if length != item.bytes or sha256 != item.sha256:
        raise BuildSizeError("ARTIFACT_DECLARATION_MISMATCH", "上传头与清单不一致")
    if role in artifact_rows(db, build_id):
        return None, build.project_id
    if role == "apk":
        job = initial_job(db, build.id)
        if job is not None and job.state in {"QUEUED", "RUNNING"} and job.apk_download_secret:
            raise BuildSizeError("APK_DOWNLOAD_CONFLICT", "后台已接管本次 APK 下载")
    if build.upload_state != "UPLOADING" or (
        build.upload_expires_at is not None and aware(build.upload_expires_at) <= now()
    ):
        raise BuildSizeError("UPLOAD_STATE_CONFLICT", "构建已封存或上传已过期")
    db.execute(
        delete(UploadLease).where(
            UploadLease.project_id == build.project_id, UploadLease.expires_at <= now()
        )
    )
    count = (
        db.scalar(
            select(func.count())
            .select_from(UploadLease)
            .where(UploadLease.project_id == build.project_id)
        )
        or 0
    )
    if count >= 2 or db.scalar(
        select(UploadLease.id).where(UploadLease.build_id == build.id, UploadLease.role == role)
    ):
        raise BuildSizeError("UPLOAD_QUOTA_EXCEEDED", "项目或角色已有上传进行中")
    lease = UploadLease(
        project_id=build.project_id,
        build_id=build.id,
        role=role,
        expires_at=now() + timedelta(seconds=config.upload_timeout_seconds + 30),
    )
    db.add(lease)
    db.flush()
    return lease.id, build.project_id


def finish_upload(
    db: Session,
    build_id: uuid.UUID,
    role: str,
    lease_id: uuid.UUID,
    upload: StagedUpload,
    length: int,
    sha256: str,
) -> None:
    lease = db.get(UploadLease, lease_id)
    if lease is None or aware(lease.expires_at) <= now():
        raise BuildSizeError("UPLOAD_STATE_CONFLICT", "上传 lease 已失效")
    db.execute(select(Project).where(Project.id == lease.project_id).with_for_update())
    build = db.get(Build, build_id, with_for_update=True)
    if build is None or build.upload_state != "UPLOADING":
        raise BuildSizeError("UPLOAD_STATE_CONFLICT", "构建不允许继续上传")
    record_artifact(db, build, role, upload, length, sha256)
    db.delete(lease)


def record_artifact(
    db: Session, build: Build, role: str, upload: StagedUpload, length: int, sha256: str
) -> None:
    """调用方持有对应项目和构建锁。"""
    key = upload.commit()
    blob = db.scalar(
        select(ArtifactBlob).where(
            ArtifactBlob.project_id == build.project_id, ArtifactBlob.sha256 == sha256
        )
    )
    if blob is None:
        blob = ArtifactBlob(
            project_id=build.project_id, sha256=sha256, bytes=length, storage_key=key
        )
        db.add(blob)
        db.flush()
    elif blob.bytes != length:
        raise BuildSizeError("BLOB_CORRUPTED", "已登记 Blob 长度冲突")
    blob.gc_marked_at, blob.deleted_at = None, None
    db.add(BuildArtifact(build_id=build.id, role=role, blob_id=blob.id, schema_state="not_checked"))


def validate_artifacts(
    db: Session, storage: BlobStorage, build: Build, config: Config, *, require_apk: bool = True
) -> tuple[BuildMeta, dict[str, str]]:
    manifest = Submission.model_validate(build.manifest)
    rows = artifact_rows(db, build.id)
    roles = {item.role for item in manifest.artifacts}
    if not require_apk:
        roles.discard("apk")
    if set(rows) - (set() if require_apk else {"apk"}) != roles:
        raise BuildSizeError("ARTIFACTS_INCOMPLETE", "仍有清单产物未验证上传")
    blobs: dict[str, ArtifactBlob] = {}
    for role, row in rows.items():
        if row.expired_at is not None:
            raise BuildSizeError("BLOB_UNAVAILABLE", "原始产物已按保留策略过期")
        blob = db.get(ArtifactBlob, row.blob_id)
        if blob is None or blob.project_id != build.project_id:
            raise BuildSizeError("BLOB_CORRUPTED", "产物项目归属无效")
        blobs[role] = blob
        with storage.read_path(blob.storage_key):
            pass
    try:
        with storage.read_path(blobs["build_meta"].storage_key) as path:
            metadata = BuildMeta.model_validate(read_json(path))
    except ValidationError as exc:
        raise BuildSizeError("BUILD_META_INVALID", "构建元数据不符合协议") from exc
    validate_identity(manifest, metadata, [])
    if require_apk:
        with storage.read_path(blobs["apk"].storage_key) as path:
            check_apk_container(path, limits=Limits(max_apk_bytes=config.upload_max_bytes))
    coverage = {
        "apk": "complete" if require_apk else "pending",
        "build_meta": "complete",
        "android_metadata": "not_requested",
        "settings": "complete"
        if all(
            value is not None
            for value in (
                metadata.settings.unity_version,
                metadata.settings.scripting_backend,
                metadata.settings.architectures,
                metadata.settings.development_build,
            )
        )
        else "missing",
    }
    if require_apk:
        rows["apk"].schema_state = "not_applicable"
    rows["build_meta"].schema_state = "complete"
    for role in ("unity_report", "asset_importers", "git_changes", "packages_lock"):
        if role not in rows:
            coverage[role] = "missing" if role != "git_changes" else "not_requested"
            continue
        row = rows[role]
        try:
            with storage.read_path(blobs[role].storage_key) as path:
                value = read_json(path)
            # 身份冲突不能以旧 Schema 或可选报告错误为由降级。
            if role != "packages_lock" and "run_id" in value and value["run_id"] != manifest.run_id:
                raise BuildSizeError("BUILD_IDENTITY_CONFLICT", "报告 run_id 与构建不一致")
            if (
                role not in REPORTS
                or type(value.get("schema_version")) is not int
                or (value.get("schema_version") != 1)
            ):
                row.schema_state, coverage[role] = "unsupported_schema", "unsupported"
                continue
            report = REPORTS[role].model_validate(value)
            if isinstance(report, UnityReport | AssetImporters | GitChanges):
                validate_identity(manifest, metadata, [report])
            if (
                not require_apk
                and isinstance(report, UnityReport)
                and report.build_result in {"Failed", "Cancelled"}
            ):
                raise BuildSizeError("REPORT_DATA_INVALID", "Unity 构建失败或已取消")
            row.schema_state = "complete"
            coverage[role] = (
                "complete"
                if role in {"unity_report", "asset_importers", "git_changes"}
                else "unsupported"
            )
        except ValidationError:
            row.schema_state, row.data_error, coverage[role] = (
                "failed",
                "REPORT_DATA_INVALID",
                "failed",
            )
        except BuildSizeError as exc:
            if exc.code == "BUILD_IDENTITY_CONFLICT":
                raise
            row.schema_state, row.data_error, coverage[role] = "failed", exc.code, "failed"
    return metadata, coverage


def enqueue_analysis(db: Session, build: Build, request_key: str, coverage: dict[str, str]) -> Job:
    revision = AnalysisRevision(
        build_id=build.id,
        input_sha256=build.manifest_sha256,
        parser_version=PARSER_SET,
        status="QUEUED",
        request_key=request_key,
        summary={"coverage": coverage, "stage": "queued"},
    )
    db.add(revision)
    db.flush()
    job = Job(
        build_id=build.id,
        analysis_revision_id=revision.id,
        state="QUEUED",
        kind="analysis",
        idempotency_key=fingerprint(
            [str(build.id), str(revision.id), PARSER_SET, build.manifest_sha256]
        ),
    )
    db.add(job)
    db.flush()
    db.add(Outbox(job_id=job.id, payload={"job_id": str(job.id)}))
    build.requested_analysis_id, build.analysis_state = revision.id, "QUEUED"
    return job


def initial_job(db: Session, build_id: uuid.UUID, *, lock: bool = False) -> Job | None:
    query = (
        select(Job)
        .join(AnalysisRevision, AnalysisRevision.id == Job.analysis_revision_id)
        .where(
            Job.build_id == build_id,
            Job.kind == "analysis",
            AnalysisRevision.request_key == "initial",
        )
    )
    return db.scalar(query.with_for_update(of=Job) if lock else query)


def seal_build(db: Session, build: Build, metadata: BuildMeta) -> None:
    build.build_metadata = metadata.model_dump(mode="json")
    build.build_completed_at = aware(
        datetime.fromisoformat(metadata.build_completed_at.replace("Z", "+00:00"))
    )
    build.upload_state = "SEALED"
    for repository in metadata.repositories:
        db.add(RepositorySnapshot(build_id=build.id, **repository.model_dump()))


def finalize(
    db: Session,
    config: Config,
    storage: BlobStorage,
    actor: Principal,
    build_id: uuid.UUID,
    request_id: str | None = None,
) -> Job:
    build = authorized_build(
        db, actor, build_id, scope="builds:submit", admin=actor.user_id is not None
    )
    db.refresh(build, with_for_update=True)
    job = initial_job(db, build.id)
    if build.upload_state == "SEALED":
        assert job is not None
        return job
    if job is not None:
        raise BuildSizeError("APK_DOWNLOAD_CONFLICT", "本次构建已提交后台下载")
    if build.upload_state != "UPLOADING" or (
        build.upload_expires_at is not None and aware(build.upload_expires_at) <= now()
    ):
        raise BuildSizeError("UPLOAD_STATE_CONFLICT", "上传已过期")
    metadata, coverage = validate_artifacts(db, storage, build, config)
    seal_build(db, build, metadata)
    job = enqueue_analysis(db, build, "initial", coverage)
    audit(db, actor, build.project_id, "build.finalize", build.id, request_id)
    return job


def enqueue_apk_download(
    db: Session,
    config: Config,
    storage: BlobStorage,
    actor: Principal,
    build_id: uuid.UUID,
    url: str,
    request_id: str | None = None,
) -> Job:
    build = authorized_build(
        db, actor, build_id, scope="builds:submit", admin=actor.user_id is not None
    )
    download_target(url, config.apk_download_hosts)
    # 与 Worker 一样先锁任务，再锁构建。首次入队由构建锁串行化。
    job = initial_job(db, build_id, lock=True)
    db.refresh(build, with_for_update=True)
    locked = job is not None
    job = job or initial_job(db, build_id)
    if job is not None:
        if build.upload_state == "SEALED":
            return job
        if job.state in {"QUEUED", "RUNNING"}:
            if job.apk_download_secret and reveal_url(config, job.apk_download_secret) == url:
                return job
            raise BuildSizeError("APK_DOWNLOAD_CONFLICT", "后台下载已在排队或执行")
        if not locked or job.state != "FAILED":
            raise BuildSizeError("APK_DOWNLOAD_CONFLICT", "构建任务状态已改变，请重新查询")
    manifest = Submission.model_validate(build.manifest)
    apk = next(item for item in manifest.artifacts if item.role == "apk")
    if not 0 < apk.bytes <= config.upload_max_bytes:
        raise BuildSizeError("UPLOAD_TOO_LARGE", "APK 长度超出限制")
    _, coverage = validate_artifacts(db, storage, build, config, require_apk=False)
    if coverage["unity_report"] != "complete":
        raise BuildSizeError("REPORT_DATA_INVALID", "后台下载要求本次 Unity 构建报告通过校验")
    active_upload = db.scalar(
        select(UploadLease.id).where(
            UploadLease.build_id == build.id,
            UploadLease.role == "apk",
            UploadLease.expires_at > now(),
        )
    )
    if active_upload:
        raise BuildSizeError("APK_DOWNLOAD_CONFLICT", "本次 APK 已有上传正在进行")
    build.upload_state = "UPLOADING"
    build.upload_expires_at = now() + timedelta(hours=24)
    if job is None:
        job = enqueue_analysis(db, build, "initial", coverage)
    else:
        revision = db.get(AnalysisRevision, job.analysis_revision_id)
        assert revision is not None
        outbox = db.scalar(select(Outbox).where(Outbox.job_id == job.id))
        assert outbox is not None
        outbox.sent_at = outbox.next_attempt_at = None
        job.attempt, job.state, job.error_code = 0, "QUEUED", None
        job.lease_expires_at = None
        revision.status, revision.error_code = "QUEUED", None
        revision.summary = {"coverage": coverage, "stage": "queued"}
        build.analysis_state = "QUEUED"
    job.apk_download_secret = protect_url(config, url)
    audit(db, actor, build.project_id, "build.apk_download_queued", build.id, request_id)
    return job


def reanalyze(
    db: Session,
    config: Config,
    storage: BlobStorage,
    actor: Principal,
    build_id: uuid.UUID,
    request_key: str,
    request_id: str | None = None,
) -> Job:
    build = authorized_build(db, actor, build_id, admin=True)
    db.refresh(build, with_for_update=True)
    revision = db.scalar(
        select(AnalysisRevision).where(
            AnalysisRevision.build_id == build.id,
            AnalysisRevision.request_key == "re:" + request_key,
        )
    )
    if revision:
        job = db.scalar(
            select(Job).where(Job.analysis_revision_id == revision.id, Job.kind == "analysis")
        )
        assert job is not None
        return job
    if build.upload_state != "SEALED":
        raise BuildSizeError("UPLOAD_STATE_CONFLICT", "只有封存构建可重新分析")
    _, coverage = validate_artifacts(db, storage, build, config)
    job = enqueue_analysis(db, build, "re:" + request_key, coverage)
    audit(db, actor, build.project_id, "build.reanalyze", build.id, request_id)
    return job
