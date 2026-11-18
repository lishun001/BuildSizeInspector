"""规则、复核、证据和同组趋势；所有对象都经过项目授权。"""

import hashlib
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Header, Query, Request, Response
from sqlalchemy import func, or_, select

from buildsize.analyzers.unity_report import preview_settings
from buildsize.api.builds import comparison_access
from buildsize.api.dependencies import DB, Auth, mutation
from buildsize.auth.service import project_access
from buildsize.db.models import (
    AnalysisRevision,
    ApkEntryRow,
    AssetImporterRow,
    AuditEvent,
    Build,
    Comparison,
    FindingRow,
    Project,
    RuleConfigRow,
    UnityGroupRow,
)
from buildsize.domain.contracts import BuildMeta, Contract
from buildsize.domain.errors import BuildSizeError
from buildsize.rules.config import RuleSettings
from buildsize.services.builds import audit, authorized_build
from buildsize.services.common import aware
from buildsize.services.comparisons import compatibility
from buildsize.services.rules import current_settings

router = APIRouter(prefix="/api/v1")


def check_version(value: str | None, expected: int) -> None:
    if value != '"' + str(expected) + '"':
        raise BuildSizeError("VERSION_CONFLICT", "配置或复核已更新，请重新读取后再修改")


@router.get("/projects/{key}/rules")
def get_rules(key: str, actor: Auth, db: DB, response: Response) -> dict[str, Any]:
    project = project_access(db, actor, key)
    response.headers["ETag"] = f'"{project.rules_version}"'
    return {
        "version": project.rules_version,
        "settings": current_settings(db, project).model_dump(mode="json"),
    }


@router.put("/projects/{key}/rules")
def set_rules(
    key: str,
    body: RuleSettings,
    actor: Auth,
    db: DB,
    request: Request,
    response: Response,
    if_match: str | None = Header(None),
) -> dict[str, Any]:
    mutation(request, actor)
    project = project_access(db, actor, key, admin=True)
    db.refresh(project, with_for_update=True)
    check_version(if_match, project.rules_version)
    if body.fixed_baseline_id:
        build = authorized_build(db, actor, uuid.UUID(body.fixed_baseline_id))
        if build.project_id != project.id or build.current_analysis_id is None:
            raise BuildSizeError("INVALID_BASELINE", "固定基线需为本项目已有成功分析的构建")
    project.rules_version += 1
    project.fixed_baseline_id = (
        uuid.UUID(body.fixed_baseline_id) if body.fixed_baseline_id else None
    )
    db.add(
        RuleConfigRow(
            project_id=project.id,
            version=project.rules_version,
            payload=body.model_dump(mode="json"),
        )
    )
    audit(db, actor, project.id, "rules.update", project.id, request.state.request_id)
    db.commit()
    response.headers["ETag"] = f'"{project.rules_version}"'
    return {"version": project.rules_version, "settings": body.model_dump(mode="json")}


@router.get("/comparisons/{comparison_id}/findings")
def findings(
    comparison_id: uuid.UUID,
    actor: Auth,
    db: DB,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    confidence: Literal["observed", "correlated", "hypothesis"] | None = None,
    review_state: Literal["unreviewed", "confirmed", "rejected"] | None = None,
    rule_id: str | None = Query(None, max_length=64),
    q: str = Query("", max_length=200),
) -> dict[str, Any]:
    comparison = comparison_access(db, actor, comparison_id)
    query = select(FindingRow).where(FindingRow.comparison_id == comparison.id)
    for column, value in (
        (FindingRow.confidence, confidence),
        (FindingRow.review_state, review_state),
        (FindingRow.rule_id, rule_id),
    ):
        if value:
            query = query.where(column == value)
    if q:
        query = query.where(FindingRow.subject_key.contains(q, autoescape=True))
    rows = db.scalars(
        query.order_by(FindingRow.rule_id, FindingRow.finding_key).offset(offset).limit(limit + 1)
    ).all()
    return {
        "items": [finding_view(row) for row in rows[:limit]],
        "next_offset": offset + limit if len(rows) > limit else None,
        "rules_version": comparison.rules_version,
        "rules_snapshot": comparison.rules_snapshot,
        "truncated": (comparison.summary or {}).get("findings_truncated", False),
    }


def finding_view(row: FindingRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "review_version": row.review_version,
        "finding": {**row.payload, "review_state": row.review_state},
    }


def finding_access(db: DB, actor: Auth, finding_id: uuid.UUID) -> tuple[FindingRow, Comparison]:
    row = db.get(FindingRow, finding_id)
    if row is None:
        raise BuildSizeError("NOT_FOUND", "资源不存在或无权访问")
    return row, comparison_access(db, actor, row.comparison_id)


@router.get("/findings/{finding_id}")
def finding_detail(finding_id: uuid.UUID, actor: Auth, db: DB) -> dict[str, Any]:
    row, comparison = finding_access(db, actor, finding_id)
    proofs = []
    for proof in row.payload["evidence"]:
        locator = proof["locator"]
        resolved = []
        for revision_id in (comparison.base_revision_id, comparison.target_revision_id):
            revision = db.get(AnalysisRevision, revision_id) if revision_id else None
            if revision is None:
                continue
            build = db.get(Build, revision.build_id)
            assert build is not None
            raw_role = next(
                (
                    item["role"]
                    for item in build.manifest["artifacts"]
                    if item["sha256"] == proof["artifact_sha256"]
                ),
                None,
            )
            if raw_role:
                detail: Any = None
                if locator.startswith("zip_entry:"):
                    path = locator.removeprefix("zip_entry:")
                    entry = db.scalar(
                        select(ApkEntryRow).where(
                            ApkEntryRow.revision_id == revision.id,
                            ApkEntryRow.path_hash == hashlib.sha256(path.encode()).hexdigest(),
                        )
                    )
                    if entry and entry.normalized_path == path:
                        detail = {
                            "path": path,
                            "compressed_bytes": entry.compressed_bytes,
                            "content_sha256": entry.content_sha256,
                        }
                elif locator.startswith("content_sha256:"):
                    digest = locator.removeprefix("content_sha256:")
                    query = select(ApkEntryRow).where(
                        ApkEntryRow.revision_id == revision.id, ApkEntryRow.content_sha256 == digest
                    )
                    entries = db.scalars(query.order_by(ApkEntryRow.entry_index).limit(51)).all()
                    detail = {
                        "paths": [entry.normalized_path for entry in entries[:50]],
                        "paths_truncated": len(entries) > 50,
                        "entry_count": db.scalar(
                            select(func.count()).select_from(query.subquery())
                        ),
                    }
                resolved.append(
                    {
                        "build_id": str(build.id),
                        "revision_id": str(revision.id),
                        "download_url": f"/api/v1/builds/{build.id}/artifacts/{raw_role}/download",
                        "value": detail,
                        "state": "observed" if detail else "see_artifact",
                    }
                )
            prefix = f"analysis_revision:{revision.id}:"
            if not locator.startswith(prefix):
                continue
            key = locator.removeprefix(prefix)
            data: Any = None
            if key in {"apk", "apk_file_bytes", "coverage"}:
                data = (revision.summary or {}).get("apk" if key != "coverage" else key)
                if key == "coverage":
                    data = {"coverage": data, "unity": (revision.summary or {}).get("unity")}
                if isinstance(data, dict):
                    data = {
                        k: v
                        for k, v in data.items()
                        if k not in {"directories", "duplicate_content"}
                    }
            elif key.startswith("build_meta:/settings/"):
                field = key.removeprefix("build_meta:/settings/")
                data = ((build.build_metadata or {}).get("settings", {})).get(field)
            elif key == "observed_abis":
                data = list(
                    db.scalars(
                        select(ApkEntryRow.detected_abi)
                        .where(
                            ApkEntryRow.revision_id == revision.id,
                            ApkEntryRow.detected_abi.is_not(None),
                        )
                        .distinct()
                    )
                )
            elif key.startswith("unity_asset:"):
                group = db.scalar(
                    select(UnityGroupRow).where(
                        UnityGroupRow.revision_id == revision.id,
                        UnityGroupRow.scope == "project",
                        UnityGroupRow.group_key == key.removeprefix("unity_asset:"),
                    )
                )
                if group:
                    data = {
                        "source_guid": group.source_guid,
                        "paths": group.source_paths[:50],
                        "asset_type": group.asset_type,
                        "packed_bytes": group.packed_bytes,
                    }
            elif key.startswith("asset_importer:"):
                importer = db.scalar(
                    select(AssetImporterRow).where(
                        AssetImporterRow.revision_id == revision.id,
                        AssetImporterRow.source_guid == key.removeprefix("asset_importer:"),
                    )
                )
                if importer:
                    data = {
                        "source_guid": importer.source_guid,
                        "state": importer.state,
                        "settings": preview_settings(
                            importer.importer_type, importer.payload.get("settings", {})
                        ),
                    }
            resolved.append(
                {
                    "build_id": str(build.id),
                    "revision_id": str(revision.id),
                    "state": "resolved" if data is not None else "unavailable",
                    "value": data,
                }
            )
        proofs.append({**proof, "sources": resolved})
    return {**finding_view(row), "comparison_id": str(comparison.id), "evidence": proofs}


class Review(Contract):
    review_state: Literal["unreviewed", "confirmed", "rejected"]


@router.put("/findings/{finding_id}/review")
def review(
    finding_id: uuid.UUID,
    body: Review,
    actor: Auth,
    db: DB,
    request: Request,
    response: Response,
    if_match: str | None = Header(None),
) -> dict[str, Any]:
    mutation(request, actor)
    row, comparison = finding_access(db, actor, finding_id)
    project = db.get(Project, comparison.project_id)
    assert project is not None
    project_access(db, actor, project.key, admin=True)
    db.refresh(row, with_for_update=True)
    check_version(if_match, row.review_version)
    row.review_state, row.review_version = body.review_state, row.review_version + 1
    audit(
        db,
        actor,
        project.id,
        "finding.review." + body.review_state,
        row.id,
        request.state.request_id,
    )
    db.commit()
    response.headers["ETag"] = f'"{row.review_version}"'
    return finding_view(row)


@router.get("/projects/{key}/trend")
def trend(
    key: str,
    actor: Auth,
    db: DB,
    reference_build_id: uuid.UUID | None = None,
    limit: int = Query(30, ge=1, le=100),
) -> dict[str, Any]:
    project = project_access(db, actor, key)
    reference = (
        authorized_build(db, actor, reference_build_id)
        if reference_build_id
        else db.scalar(
            select(Build)
            .join(AnalysisRevision, Build.current_analysis_id == AnalysisRevision.id)
            .where(Build.project_id == project.id, AnalysisRevision.compatibility_key.is_not(None))
            .order_by(Build.build_completed_at.desc(), Build.id.desc())
            .limit(1)
        )
    )
    if reference is None:
        return {"state": "NO_COMPATIBLE_DATA", "items": [], "compatibility_key": None}
    if reference.project_id != project.id:
        raise BuildSizeError("NOT_FOUND", "资源不存在或无权访问")
    revision = (
        db.get(AnalysisRevision, reference.current_analysis_id)
        if reference.current_analysis_id
        else None
    )
    key_hash = revision.compatibility_key if revision else None
    if key_hash is None:
        return {"state": "INCOMPLETE_PROFILE", "items": [], "compatibility_key": None}
    # 未分析项最多检查最近 200 个候选；每个占位都按声明配置再次核对。
    rows = db.execute(
        select(Build, AnalysisRevision)
        .outerjoin(AnalysisRevision, Build.current_analysis_id == AnalysisRevision.id)
        .where(
            Build.project_id == project.id,
            or_(
                AnalysisRevision.compatibility_key == key_hash, Build.current_analysis_id.is_(None)
            ),
        )
        .order_by(Build.build_completed_at.desc().nullslast(), Build.id.desc())
        .limit(200)
    ).all()
    items = []
    for build, analyzed in rows:
        if analyzed is None:
            if (
                not build.build_metadata
                or compatibility(BuildMeta.model_validate(build.build_metadata))[0] != key_hash
            ):
                continue
        items.append(
            {
                "build_id": str(build.id),
                "build_number": build.ci_build_number,
                "completed_at": aware(build.build_completed_at).isoformat()
                if build.build_completed_at
                else None,
                "analysis_state": build.analysis_state,
                "apk_file_bytes": analyzed.apk_file_bytes if analyzed else None,
            }
        )
        if len(items) == limit:
            break
    return {
        "state": "AVAILABLE",
        "compatibility_key": key_hash,
        "reference_build_id": str(reference.id),
        "items": list(reversed(items)),
        "candidate_window": 200,
    }


@router.get("/projects/{key}/audit")
def audit_page(
    key: str,
    actor: Auth,
    db: DB,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    project = project_access(db, actor, key, admin=True)
    rows = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.project_id == project.id)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .offset(offset)
        .limit(limit + 1)
    ).all()
    return {
        "items": [
            {
                "id": str(row.id),
                "actor": row.actor,
                "action": row.action,
                "object_id": row.object_id,
                "created_at": aware(row.created_at).isoformat(),
            }
            for row in rows[:limit]
        ],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


class Pin(Contract):
    pinned: bool


@router.put("/builds/{build_id}/pin")
def pin_build(
    build_id: uuid.UUID, body: Pin, actor: Auth, db: DB, request: Request
) -> dict[str, Any]:
    mutation(request, actor)
    build = authorized_build(db, actor, build_id, admin=True)
    db.execute(select(Project).where(Project.id == build.project_id).with_for_update())
    db.refresh(build, with_for_update=True)
    if build.pinned != body.pinned:
        build.pinned = body.pinned
        audit(
            db,
            actor,
            build.project_id,
            "build.pin" if body.pinned else "build.unpin",
            build.id,
            request.state.request_id,
        )
    db.commit()
    return {
        "build_id": str(build.id),
        "pinned": build.pinned,
        "notice": "pin 保护仍存在的数据，不能恢复已过期原始产物。",
    }


@router.get("/projects/{key}/diagnostics")
def diagnostics(key: str, actor: Auth, db: DB) -> dict[str, Any]:
    from buildsize.db.models import ArtifactBlob, BuildArtifact, Job
    from buildsize.services.common import now

    project = project_access(db, actor, key, admin=True)
    states: dict[str, int] = {
        state: count
        for state, count in db.execute(
            select(Job.state, func.count())
            .join(Build, Build.id == Job.build_id)
            .where(Build.project_id == project.id)
            .group_by(Job.state)
        ).all()
    }
    oldest = db.scalar(
        select(func.min(Job.created_at))
        .join(Build, Build.id == Job.build_id)
        .where(Build.project_id == project.id, Job.state == "QUEUED")
    )
    return {
        "jobs": states,
        "oldest_queued_seconds": max(0, int((now() - aware(oldest)).total_seconds()))
        if oldest
        else None,
        "registered_blob_bytes": int(
            db.scalar(
                select(func.sum(ArtifactBlob.bytes)).where(
                    ArtifactBlob.project_id == project.id, ArtifactBlob.deleted_at.is_(None)
                )
            )
            or 0
        ),
        "expired_artifacts": db.scalar(
            select(func.count())
            .select_from(BuildArtifact)
            .join(Build, Build.id == BuildArtifact.build_id)
            .where(Build.project_id == project.id, BuildArtifact.expired_at.is_not(None))
        )
        or 0,
        "automatic_cleanup": False,
        "android_metadata": "not_configured",
    }
