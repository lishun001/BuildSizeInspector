"""Unity 层分页查询，明确独立 packed 指标和原始报告证据定位。"""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from buildsize.analyzers.unity_report import preview_settings
from buildsize.api.builds import comparison_access
from buildsize.api.dependencies import DB, Auth
from buildsize.db.models import (
    AnalysisRevision,
    AssetImporterRow,
    Build,
    UnityChangeRow,
    UnityContainerRow,
    UnityGroupRow,
    UnityRecordRow,
)
from buildsize.domain.errors import BuildSizeError
from buildsize.services.builds import authorized_build

router = APIRouter(prefix="/api/v1")


def revision_for(
    db: Session, actor: Any, build_id: uuid.UUID, revision_id: uuid.UUID | None
) -> tuple[Build, AnalysisRevision]:
    build = authorized_build(db, actor, build_id)
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
    if revision is None or revision.status not in {"READY", "PARTIAL"}:
        raise BuildSizeError("ANALYSIS_NOT_AVAILABLE", "指定分析修订不可用")
    return build, revision


def report_sha(build: Build, role: str) -> str | None:
    return next(
        (row["sha256"] for row in build.manifest.get("artifacts", []) if row["role"] == role), None
    )


def availability(revision: AnalysisRevision, role: str = "unity_report") -> str:
    value = (revision.summary or {}).get("coverage", {}).get(role, "unsupported")
    return value if isinstance(value, str) else "unsupported"


@router.get("/builds/{build_id}/unity-assets")
def assets(
    build_id: uuid.UUID,
    actor: Auth,
    db: DB,
    revision_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=200),
    source_guid: str | None = Query(None, pattern=r"^[a-f0-9]{32}$"),
    scope: Literal["project", "container"] = "project",
) -> dict[str, Any]:
    build, revision = revision_for(db, actor, build_id, revision_id)
    query = select(UnityGroupRow).where(
        UnityGroupRow.revision_id == revision.id, UnityGroupRow.scope == scope
    )
    if source_guid:
        query = query.where(UnityGroupRow.source_guid == source_guid)
    if q:
        # 在原始记录中按路径搜索，返回匹配 GUID/类型组，不依赖 JSON 编码的文字形式。
        guids = (
            select(UnityRecordRow.source_guid)
            .join(UnityContainerRow)
            .where(
                UnityContainerRow.revision_id == revision.id,
                UnityRecordRow.source_path.contains(q, autoescape=True),
            )
        )
        query = query.where(
            or_(
                UnityGroupRow.asset_type.contains(q, autoescape=True),
                UnityGroupRow.source_path.contains(q, autoescape=True),
                UnityGroupRow.source_guid.in_(guids),
            )
        )
    rows = db.scalars(
        query.order_by(UnityGroupRow.packed_bytes.desc(), UnityGroupRow.group_key)
        .offset(offset)
        .limit(limit + 1)
    ).all()
    return {
        "analysis_revision": str(revision.id),
        "state": availability(revision),
        "metric": "unity_packed_bytes",
        "artifact_sha256": report_sha(build, "unity_report"),
        "items": [
            {
                "group_key": row.group_key,
                "identity_kind": row.identity_kind,
                "source_guid": row.source_guid,
                "asset_type": row.asset_type,
                "source_paths": row.source_paths[:20],
                "source_path_count": len(row.source_paths),
                "container_paths": row.container_paths[:20],
                "container_count": len(row.container_paths),
                "packed_bytes": row.packed_bytes,
                "record_count": row.record_count,
                "matchable": row.matchable,
            }
            for row in rows[:limit]
        ],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


@router.get("/builds/{build_id}/unity-containers")
def containers(
    build_id: uuid.UUID,
    actor: Auth,
    db: DB,
    revision_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    _, revision = revision_for(db, actor, build_id, revision_id)
    rows = db.scalars(
        select(UnityContainerRow)
        .where(UnityContainerRow.revision_id == revision.id)
        .order_by(UnityContainerRow.container_index)
        .offset(offset)
        .limit(limit + 1)
    ).all()
    return {
        "state": availability(revision),
        "analysis_revision": str(revision.id),
        "items": [
            {
                "container_index": row.container_index,
                "short_path": row.short_path,
                "overhead_bytes": row.overhead_bytes,
                "mapping_state": row.mapping_state,
                "apk_paths": row.apk_paths,
                "mapper_version": row.mapper_version,
                "evidence": row.evidence,
            }
            for row in rows[:limit]
        ],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


@router.get("/builds/{build_id}/unity-records")
def records(
    build_id: uuid.UUID,
    actor: Auth,
    db: DB,
    revision_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    source_guid: str | None = Query(None, pattern=r"^[a-f0-9]{32}$"),
    container_index: int | None = Query(None, ge=0),
) -> dict[str, Any]:
    build, revision = revision_for(db, actor, build_id, revision_id)
    query = (
        select(UnityRecordRow, UnityContainerRow.container_index)
        .join(UnityContainerRow)
        .where(UnityContainerRow.revision_id == revision.id)
    )
    if source_guid:
        query = query.where(UnityRecordRow.source_guid == source_guid)
    if container_index is not None:
        query = query.where(UnityContainerRow.container_index == container_index)
    rows = db.execute(
        query.order_by(UnityContainerRow.container_index, UnityRecordRow.record_index)
        .offset(offset)
        .limit(limit + 1)
    ).all()
    return {
        "state": availability(revision),
        "metric": "unity_packed_bytes",
        "analysis_revision": str(revision.id),
        "artifact_sha256": report_sha(build, "unity_report"),
        "items": [
            {
                "container_index": index,
                "record_index": row.record_index,
                "source_guid": row.source_guid,
                "source_path": row.source_path,
                "asset_type": row.asset_type,
                "packed_bytes": row.packed_bytes,
                "json_pointer": row.json_pointer,
            }
            for row, index in rows[:limit]
        ],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


@router.get("/builds/{build_id}/asset-importers")
def importers(
    build_id: uuid.UUID,
    actor: Auth,
    db: DB,
    revision_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    source_guid: str | None = Query(None, pattern=r"^[a-f0-9]{32}$"),
) -> dict[str, Any]:
    build, revision = revision_for(db, actor, build_id, revision_id)
    query = select(AssetImporterRow).where(AssetImporterRow.revision_id == revision.id)
    if source_guid:
        query = query.where(AssetImporterRow.source_guid == source_guid)
    rows = db.scalars(
        query.order_by(AssetImporterRow.source_guid).offset(offset).limit(limit + 1)
    ).all()
    return {
        "state": availability(revision, "asset_importers"),
        "analysis_revision": str(revision.id),
        "artifact_sha256": report_sha(build, "asset_importers"),
        "items": [
            {
                "source_guid": row.source_guid,
                "source_path": row.source_path,
                "importer_type": row.importer_type,
                "state": row.state,
                "settings": preview_settings(row.importer_type, row.payload.get("settings", {})),
                "dependency_hash": row.payload.get("dependency_hash"),
                "source_sha256": row.payload.get("source_sha256"),
                "meta_sha256": row.payload.get("meta_sha256"),
                "unsupported_fields": row.payload.get("unsupported_fields", [])[:50],
                "json_pointer": row.json_pointer,
            }
            for row in rows[:limit]
        ],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


def change_page(
    db: Session,
    comparison: Any,
    layer: str,
    limit: int,
    offset: int,
    q: str,
    status: str | None,
    sort: str,
) -> dict[str, Any]:
    kind = "importer" if layer == "asset_importer" else "asset"
    summary = (comparison.summary or {}).get("unity", {})
    state = summary.get(
        "importer_comparison_status" if kind == "importer" else "status", "unavailable"
    )
    if state not in {"COMPARED", "NO_BASELINE", "complete", "partial"}:
        raise BuildSizeError("ANALYSIS_NOT_AVAILABLE", "Unity 比较数据不完整或尚未可用")
    query = select(UnityChangeRow).where(
        UnityChangeRow.comparison_id == comparison.id, UnityChangeRow.kind == kind
    )
    query = query.where(
        UnityChangeRow.status == status if status else UnityChangeRow.status != "UNCHANGED"
    )
    if q:
        query = query.where(
            or_(
                UnityChangeRow.source_guid.contains(q, autoescape=True),
                UnityChangeRow.asset_type.contains(q, autoescape=True),
            )
        )
    order = {
        "delta": UnityChangeRow.delta_packed_bytes,
        "absolute_delta": func.abs(UnityChangeRow.delta_packed_bytes),
        "old_bytes": UnityChangeRow.old_packed_bytes,
        "new_bytes": UnityChangeRow.new_packed_bytes,
    }[sort]
    rows = db.scalars(
        query.order_by(order.desc().nullslast(), UnityChangeRow.group_key)
        .offset(offset)
        .limit(limit + 1)
    ).all()
    items = []
    for row in rows[:limit]:
        details = row.details
        items.append(
            {
                "group_key": row.group_key,
                "source_guid": row.source_guid,
                "asset_type": row.asset_type,
                "status": row.status,
                "old_packed_bytes": row.old_packed_bytes,
                "new_packed_bytes": row.new_packed_bytes,
                "delta_packed_bytes": row.delta_packed_bytes,
                "renamed": details.get("renamed"),
                "identity_kind": details.get("identity_kind"),
                "old_paths": details.get("old_paths", [])[:20],
                "new_paths": details.get("new_paths", [])[:20],
                "fields": details.get("fields", []),
            }
        )
        if kind == "importer":
            for side in ("old", "new"):
                payload = details.get(side, {})
                items[-1][side + "_settings"] = preview_settings(
                    payload.get("importer_type", ""), payload.get("settings", {})
                )
    return {
        "state": state,
        "metric": "none" if kind == "importer" else "unity_packed_bytes",
        "additive_to_apk": False,
        "items": items,
        "next_offset": offset + limit if len(rows) > limit else None,
    }


@router.get("/comparisons/{comparison_id}/unity-changes")
def unity_changes(
    comparison_id: uuid.UUID,
    actor: Auth,
    db: DB,
    kind: Literal["asset", "importer"] = "asset",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    row = comparison_access(db, actor, comparison_id)
    return change_page(
        db,
        row,
        "asset_importer" if kind == "importer" else "unity_asset",
        limit,
        offset,
        "",
        None,
        "delta",
    )
