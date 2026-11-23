"""分步上传、授权查询与比较路由。耗时内容分析仅由 Worker 执行。"""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any, Literal, cast
from urllib.parse import quote

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from buildsize.api.contracts import BuildDetail, BuildState, ErrorEnvelope, FinalizedBuild
from buildsize.api.dependencies import DB, Auth, mutation
from buildsize.auth.service import project_access
from buildsize.config import Config
from buildsize.db.models import (
    AnalysisRevision,
    ApkEntryRow,
    ArtifactBlob,
    Build,
    BuildArtifact,
    ChangeRecord,
    Comparison,
    DistributionLink,
    Job,
    UploadLease,
)
from buildsize.domain.contracts import Contract, RunId, Submission
from buildsize.domain.errors import BuildSizeError
from buildsize.services import builds as service
from buildsize.services.common import aware
from buildsize.services.comparisons import create_manual
from buildsize.storage.local import BlobStorage, LocalBlobStorage

router = APIRouter(
    prefix="/api/v1",
    responses={
        code: {"model": ErrorEnvelope}
        for code in (400, 401, 403, 404, 409, 410, 411, 413, 415, 422, 429, 503)
    },
)


class EmptyBody(Contract):
    pass


class ApkDownloadBody(Contract):
    download_url: Annotated[str, Field(max_length=8192, repr=False)]


class ComparisonBody(Contract):
    base_build_id: RunId
    target_build_id: RunId


def idempotency(value: str | None) -> str:
    try:
        return str(uuid.UUID(value or ""))
    except ValueError as exc:
        raise BuildSizeError(
            "IDEMPOTENCY_KEY_INVALID", "必须提供 UUID 格式的 Idempotency-Key"
        ) from exc


def storage_for(request: Request) -> BlobStorage:
    return LocalBlobStorage(request.app.state.config.blob_root)


def summary_view(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    if summary is None:
        return None
    value = dict(summary)
    if "unity" in value:
        unity = dict(value["unity"])
        if "warnings" in unity:
            warnings = unity["warnings"]
            unity["warnings"], unity["warning_count"] = warnings[:50], len(warnings)
        value["unity"] = unity
    if "config_changes" in value:
        preview = []
        for change in value["config_changes"]:
            change = dict(change)
            for key in ("old", "new"):
                if isinstance(change.get(key), list):
                    change[key + "_count"] = len(change[key])
                    change[key] = change[key][:50]
            preview.append(change)
        value["config_changes"] = preview
    if "apk" in value:
        apk = dict(value["apk"])
        for key in ("directories", "duplicate_content"):
            rows = apk.pop(key, [])
            apk[key + "_count"] = len(rows)
        warnings = apk.get("warnings", [])
        apk["warnings"], apk["warning_count"] = warnings[:50], len(warnings)
        value["apk"] = apk
    return value


def metadata_view(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if metadata is None:
        return None
    value = dict(metadata)
    for key in ("repositories", "warnings"):
        rows = value.get(key, [])
        value[key], value[key + "_count"] = rows[:50], len(rows)
    for group, key in (("settings", "build_options"), ("content_policy", "scene_guids")):
        value[group] = dict(value[group])
        rows = value[group][key]
        value[group][key], value[group][key + "_count"] = rows[:50], len(rows)
    value["list_preview_limit"] = 50
    return value


def build_view(db: Session, config: Config, build: Build) -> dict[str, Any]:
    revision = (
        db.get(AnalysisRevision, build.current_analysis_id) if build.current_analysis_id else None
    )
    rows = service.artifact_rows(db, build.id)
    return {
        "build_id": str(build.id),
        "run_id": str(build.run_id),
        "variant": build.variant,
        "pinned": build.pinned,
        "upload_state": build.upload_state,
        "analysis_state": build.analysis_state,
        "analysis_revision": str(revision.id) if revision else None,
        "requested_analysis_revision": str(build.requested_analysis_id)
        if build.requested_analysis_id
        else None,
        "metadata": metadata_view(build.build_metadata),
        "result": summary_view(revision.summary) if revision else None,
        "artifacts": [
            {
                "role": item["role"],
                "bytes": item["bytes"],
                "sha256": item["sha256"],
                "state": "verified" if item["role"] in rows else "pending",
                "schema_state": rows[item["role"]].schema_state if item["role"] in rows else None,
                "data_error": rows[item["role"]].data_error if item["role"] in rows else None,
                "expired_at": aware(cast(datetime, rows[item["role"]].expired_at)).isoformat()
                if item["role"] in rows and rows[item["role"]].expired_at
                else None,
            }
            for item in build.manifest.get("artifacts", [])
        ],
        "report_url": f"{config.origin}/builds/{build.id}",
        "distribution": {
            row.provider: row.payload
            for row in db.scalars(
                select(DistributionLink).where(DistributionLink.build_id == build.id)
            )
        },
    }


def job_view(job: Job) -> dict[str, Any]:
    return {
        "job_id": str(job.id),
        "analysis_revision": str(job.analysis_revision_id),
        "state": job.state,
        "attempt": job.attempt,
        "error_code": job.error_code,
        "apk_download_pending": job.apk_download_secret is not None,
    }


@router.post(
    "/projects/{project_key}/builds",
    response_model=BuildState,
    responses={201: {"model": BuildState, "description": "新建构建"}},
)
def initialize(
    project_key: str,
    body: Submission,
    actor: Auth,
    db: DB,
    request: Request,
    response: Response,
    idempotency_key: str = Header(...),
) -> dict[str, Any]:
    mutation(request, actor)
    if (
        idempotency(idempotency_key) != str(uuid.UUID(body.run_id))
        or project_key != body.project_key
    ):
        raise BuildSizeError("BUILD_IDENTITY_CONFLICT", "路径、请求键与清单身份不一致")
    try:
        build, created = service.initialize(
            db, request.app.state.config, actor, body, request.state.request_id
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise BuildSizeError("BUILD_IDENTITY_CONFLICT", "构建身份已被其他提交使用") from None
    response.status_code = 201 if created else 200
    return build_view(db, request.app.state.config, build)


@router.put(
    "/builds/{build_id}/artifacts/{role}",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def upload(
    build_id: uuid.UUID,
    role: str,
    actor: Auth,
    db: DB,
    request: Request,
    content_length: str | None = Header(None),
    x_content_sha256: str | None = Header(None),
) -> Response:
    mutation(request, actor)
    if (
        request.headers.get("content-type", "").split(";")[0].strip().lower()
        != "application/octet-stream"
    ):
        raise BuildSizeError("CONTENT_TYPE_INVALID", "产物上传需要 application/octet-stream")
    if content_length is None or not content_length.isascii() or not content_length.isdigit():
        raise BuildSizeError("CONTENT_LENGTH_REQUIRED", "必须提供有效 Content-Length")
    if len(content_length) > 20:
        raise BuildSizeError("UPLOAD_TOO_LARGE", "上传声明长度超过限制")
    length = int(content_length)
    config: Config = request.app.state.config

    def begin() -> tuple[uuid.UUID | None, uuid.UUID]:
        result = service.begin_upload(
            db, config, actor, build_id, role, length, x_content_sha256 or ""
        )
        db.commit()
        return result

    lease_id, project_id = await run_in_threadpool(begin)
    if lease_id is None:
        return Response(status_code=200)
    staged = None
    try:
        staged = await run_in_threadpool(
            storage_for(request).stage, project_id, length, x_content_sha256 or ""
        )
        async with asyncio.timeout(config.upload_timeout_seconds):
            async for chunk in request.stream():
                await run_in_threadpool(staged.write, chunk)

            def finish() -> None:
                assert staged is not None
                service.finish_upload(
                    db, build_id, role, lease_id, staged, length, x_content_sha256 or ""
                )
                db.commit()

            await run_in_threadpool(finish)
        return Response(status_code=201)
    except TimeoutError:
        raise BuildSizeError("UPLOAD_TIMEOUT", "上传超过总期限") from None
    except ClientDisconnect:
        raise BuildSizeError("UPLOAD_INTERRUPTED", "上传连接中断") from None
    finally:
        # 使用独立事务；上传失败的请求 Session 可能已处于回滚状态。
        def release() -> None:
            db.rollback()
            with Session(request.app.state.engine) as cleanup, cleanup.begin():
                cleanup.execute(delete(UploadLease).where(UploadLease.id == lease_id))

        try:
            if staged is not None:
                await run_in_threadpool(staged.close)
        finally:
            await run_in_threadpool(release)


@router.post("/builds/{build_id}/finalize", status_code=202, response_model=FinalizedBuild)
def finalize(
    build_id: uuid.UUID, body: EmptyBody, actor: Auth, db: DB, request: Request
) -> dict[str, Any]:
    mutation(request, actor)
    job = service.finalize(
        db,
        request.app.state.config,
        storage_for(request),
        actor,
        build_id,
        request.state.request_id,
    )
    db.commit()
    build = db.get(Build, build_id)
    assert build is not None
    return {**build_view(db, request.app.state.config, build), "job": job_view(job)}


@router.post("/builds/{build_id}/finalize-from-url", status_code=202, response_model=FinalizedBuild)
def finalize_from_url(
    build_id: uuid.UUID, body: ApkDownloadBody, actor: Auth, db: DB, request: Request
) -> dict[str, Any]:
    mutation(request, actor)
    config: Config = request.app.state.config
    job = service.enqueue_apk_download(
        db,
        config,
        storage_for(request),
        actor,
        build_id,
        body.download_url,
        request.state.request_id,
    )
    db.commit()
    build = db.get(Build, build_id)
    assert build is not None
    return {**build_view(db, config, build), "job": job_view(job)}


@router.get("/builds/{build_id}", response_model=BuildDetail)
def detail(
    build_id: uuid.UUID,
    actor: Auth,
    db: DB,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    build = service.authorized_build(db, actor, build_id)
    value = build_view(db, request.app.state.config, build)
    jobs = db.scalars(
        select(Job)
        .where(Job.build_id == build.id)
        .order_by(Job.created_at.desc(), Job.id)
        .offset(offset)
        .limit(limit + 1)
    ).all()
    value["jobs"] = [job_view(job) for job in jobs[:limit]]
    value["jobs_next_offset"] = offset + limit if len(jobs) > limit else None
    return value


@router.get("/projects/{project_key}/builds")
def history(
    project_key: str,
    actor: Auth,
    db: DB,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    project = project_access(db, actor, project_key)
    rows = db.scalars(
        select(Build)
        .where(Build.project_id == project.id)
        .order_by(Build.build_completed_at.desc().nullslast(), Build.id.desc())
        .offset(offset)
        .limit(limit + 1)
    ).all()
    # 列表不返回每个构建的目录、重复组或全部条目。
    items = []
    for row in rows[:limit]:
        revision = (
            db.get(AnalysisRevision, row.current_analysis_id) if row.current_analysis_id else None
        )
        comparison_id = (revision.summary or {}).get("comparison_id") if revision else None
        comparison_row = db.get(Comparison, uuid.UUID(comparison_id)) if comparison_id else None
        comparison_summary = (comparison_row.summary or {}) if comparison_row else {}
        items.append(
            {
                "build_id": str(row.id),
                "variant": row.variant,
                "upload_state": row.upload_state,
                "analysis_state": row.analysis_state,
                "build_completed_at": aware(row.build_completed_at).isoformat()
                if row.build_completed_at
                else None,
                "apk_file_bytes": revision.apk_file_bytes if revision else None,
                "analysis_revision": str(revision.id) if revision else None,
                "compatibility_key": revision.compatibility_key if revision else None,
                "build_number": row.ci_build_number,
                "delta_apk_bytes": comparison_summary.get("delta_apk_bytes"),
                "finding_count": comparison_summary.get("finding_count"),
                "comparison_id": (revision.summary or {}).get("comparison_id")
                if revision
                else None,
            }
        )
    return {"items": items, "next_offset": offset + limit if len(rows) > limit else None}


@router.get("/builds/{build_id}/entries")
def entries(
    build_id: uuid.UUID,
    actor: Auth,
    db: DB,
    revision_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=200),
    sort: Literal["compressed_bytes", "path"] = "compressed_bytes",
) -> dict[str, Any]:
    build = service.authorized_build(db, actor, build_id)
    selected = revision_id or build.current_analysis_id
    revision = (
        db.scalar(
            select(AnalysisRevision).where(
                AnalysisRevision.id == selected, AnalysisRevision.build_id == build.id
            )
        )
        if selected
        else None
    )
    if (
        revision is None
        or revision.build_id != build.id
        or revision.status not in {"READY", "PARTIAL"}
    ):
        raise BuildSizeError("ANALYSIS_NOT_AVAILABLE", "指定 APK 分析索引不可用")
    query = select(ApkEntryRow).where(ApkEntryRow.revision_id == revision.id)
    if q:
        query = query.where(ApkEntryRow.normalized_path.contains(q, autoescape=True))
    order = (
        ApkEntryRow.compressed_bytes.desc()
        if sort == "compressed_bytes"
        else ApkEntryRow.normalized_path.asc()
    )
    rows = db.scalars(
        query.order_by(order, ApkEntryRow.entry_index).offset(offset).limit(limit + 1)
    ).all()
    return {
        "analysis_revision": str(revision.id),
        "items": [
            {
                "entry_index": row.entry_index,
                "raw_path": row.raw_path,
                "normalized_path": row.normalized_path,
                "is_directory": row.is_directory,
                "compressed_bytes": row.compressed_bytes,
                "uncompressed_bytes": row.uncompressed_bytes,
                "compression_method": row.compression_method,
                "content_sha256": row.content_sha256,
                "crc32": row.crc32,
                "category": row.category,
                "detected_abi": row.detected_abi,
            }
            for row in rows[:limit]
        ],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


@router.get("/builds/{build_id}/artifacts/{role}/download")
def download(
    build_id: uuid.UUID, role: str, actor: Auth, db: DB, request: Request
) -> StreamingResponse:
    build = service.authorized_build(db, actor, build_id)
    artifact = db.scalar(
        select(BuildArtifact).where(BuildArtifact.build_id == build.id, BuildArtifact.role == role)
    )
    if artifact is None:
        raise BuildSizeError("NOT_FOUND", "资源不存在或无权访问")
    if artifact.expired_at is not None:
        raise BuildSizeError("BLOB_UNAVAILABLE", "该构建原始产物已过期，已保存索引仍可查询")
    blob = db.get(ArtifactBlob, artifact.blob_id)
    assert blob is not None
    storage = storage_for(request)
    key, size = blob.storage_key, blob.bytes
    with storage.read_path(key):
        pass
    filename = next(
        item["filename"] for item in build.manifest["artifacts"] if item["role"] == role
    )

    def data() -> Iterator[bytes]:
        with storage.read_path(key) as path, path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                yield chunk

    return StreamingResponse(
        data(),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(size),
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
        },
    )


@router.post("/builds/{build_id}/reanalyze", status_code=202)
def reanalyze(
    build_id: uuid.UUID,
    body: EmptyBody,
    actor: Auth,
    db: DB,
    request: Request,
    idempotency_key: str | None = Header(None),
) -> dict[str, Any]:
    mutation(request, actor)
    job = service.reanalyze(
        db,
        request.app.state.config,
        storage_for(request),
        actor,
        build_id,
        idempotency(idempotency_key),
        request.state.request_id,
    )
    db.commit()
    return {"job": job_view(job)}


def comparison_access(db: Session, actor: Any, comparison_id: uuid.UUID) -> Comparison:
    row = db.get(Comparison, comparison_id)
    if row is None:
        raise BuildSizeError("NOT_FOUND", "资源不存在或无权访问")
    service.authorized_build(db, actor, row.target_build_id)
    if row.base_build_id:
        service.authorized_build(db, actor, row.base_build_id)
    return row


@router.post("/comparisons", status_code=202)
def compare(
    body: ComparisonBody,
    actor: Auth,
    db: DB,
    request: Request,
    idempotency_key: str | None = Header(None),
) -> dict[str, Any]:
    mutation(request, actor)
    row, _ = create_manual(
        db,
        actor,
        uuid.UUID(body.base_build_id),
        uuid.UUID(body.target_build_id),
        idempotency(idempotency_key),
        request.state.request_id,
    )
    db.commit()
    return {"comparison_id": str(row.id), "status": row.status}


@router.get("/comparisons/{comparison_id}")
def comparison(comparison_id: uuid.UUID, actor: Auth, db: DB) -> dict[str, Any]:
    row = comparison_access(db, actor, comparison_id)
    return {
        "comparison_id": str(row.id),
        "status": row.status,
        "comparable": row.comparable,
        "base_build_id": str(row.base_build_id) if row.base_build_id else None,
        "target_build_id": str(row.target_build_id),
        "base_analysis_revision": str(row.base_revision_id) if row.base_revision_id else None,
        "target_analysis_revision": str(row.target_revision_id),
        "policy_version": row.policy_version,
        "selection_reason": row.selection_reason,
        "result": summary_view(row.summary),
        "error_code": row.error_code,
    }


@router.get("/comparisons/{comparison_id}/changes")
def changes(
    comparison_id: uuid.UUID,
    actor: Auth,
    db: DB,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=200),
    status: Literal[
        "ADDED",
        "REMOVED",
        "MODIFIED",
        "PACKAGING_CHANGED",
        "UNCHANGED",
        "ADDED_TO_BUILD",
        "REMOVED_FROM_BUILD",
        "CHANGED",
    ]
    | None = None,
    sort: Literal["delta", "absolute_delta", "old_bytes", "new_bytes"] = "delta",
    layer: Literal["apk_entry", "unity_asset", "asset_importer"] = "apk_entry",
) -> dict[str, Any]:
    row = comparison_access(db, actor, comparison_id)
    if row.status not in {"COMPARED", "NO_BASELINE"}:
        raise BuildSizeError("ANALYSIS_NOT_AVAILABLE", "比较尚未成功完成")
    if layer != "apk_entry":
        from buildsize.api.unity import change_page

        return change_page(db, row, layer, limit, offset, q, status, sort)
    query = select(ChangeRecord).where(ChangeRecord.comparison_id == row.id)
    query = query.where(
        ChangeRecord.status == status if status else ChangeRecord.status != "UNCHANGED"
    )
    if q:
        query = query.where(ChangeRecord.object_key.contains(q, autoescape=True))
    from sqlalchemy import func

    order = {
        "delta": ChangeRecord.delta_bytes,
        "absolute_delta": func.abs(ChangeRecord.delta_bytes),
        "old_bytes": ChangeRecord.old_bytes,
        "new_bytes": ChangeRecord.new_bytes,
    }[sort]
    rows = db.scalars(
        query.order_by(order.desc(), ChangeRecord.object_key).offset(offset).limit(limit + 1)
    ).all()
    return {
        "items": [
            {
                "layer": item.layer,
                "path": item.object_key,
                "status": item.status,
                "category": item.category,
                "old_bytes": item.old_bytes,
                "new_bytes": item.new_bytes,
                "delta_bytes": item.delta_bytes,
                "content_changed": item.content_changed,
                "compressed_size_changed": item.compressed_size_changed,
                "compression_method_changed": item.compression_method_changed,
            }
            for item in rows[:limit]
        ],
        "next_offset": offset + limit if len(rows) > limit else None,
    }
