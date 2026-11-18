"""CI 候选基线和蒲公英安全关联，不访问私有 Git 或蒲公英凭据。"""

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from buildsize.api.dependencies import DB, Auth, mutation
from buildsize.auth.service import project_access
from buildsize.db.models import Build, DistributionLink
from buildsize.domain.distribution import PgyerResult
from buildsize.domain.errors import BuildSizeError
from buildsize.services.builds import audit, authorized_build, validate_link
from buildsize.services.common import aware, now
from buildsize.services.comparisons import profile_key, select_candidate

router = APIRouter(prefix="/api/v1")


@router.get("/projects/{project_key}/baseline-candidate")
def baseline(
    project_key: str,
    actor: Auth,
    db: DB,
    application_id: str,
    channel: str,
    variant: str,
    ci_instance_id: str,
    ci_job_full_name: str,
    build_completed_at: datetime,
    branch: str | None = None,
    development_build: bool | None = None,
    scripting_backend: Literal["IL2CPP", "Mono"] | None = None,
    architectures: Annotated[
        list[Literal["armeabi-v7a", "arm64-v8a", "x86", "x86_64"]] | None, Query()
    ] = None,
) -> dict[str, Any]:
    project = project_access(db, actor, project_key)
    if build_completed_at.tzinfo is None:
        raise BuildSizeError("INVALID_REQUEST", "构建完成时间必须带时区")
    if build_completed_at.year < 2000 or build_completed_at > now() + timedelta(minutes=5):
        return {
            "status": "NO_BASELINE",
            "reason": "invalid_clock",
            "base_build_id": None,
            "analysis_revision": None,
            "repositories": [],
        }
    key = profile_key(
        project_key,
        {
            "application_id": application_id,
            "channel": channel,
            "variant": variant,
            "branch": branch,
            "development_build": development_build,
            "scripting_backend": scripting_backend,
            "architectures": architectures,
            "ci_instance_id": ci_instance_id,
            "ci_job_full_name": ci_job_full_name,
        },
    )
    revision, reason = select_candidate(db, project, key, aware(build_completed_at))
    build = db.get(Build, revision.build_id) if revision else None
    repositories = (build.build_metadata or {}).get("repositories", []) if build else []
    return {
        "status": "CANDIDATE" if build else "NO_BASELINE",
        "reason": reason,
        "base_build_id": str(build.id) if build else None,
        "analysis_revision": str(revision.id) if revision else None,
        "repositories": repositories[:200],
        "repositories_truncated": len(repositories) > 200,
        "policy_version": "baseline-1",
        "warning": "候选不冻结最终基线；仅实际 base/head 匹配的 Git 证据可用。",
    }


@router.put("/builds/{build_id}/distribution/pgyer")
def distribution(
    build_id: uuid.UUID, body: PgyerResult, actor: Auth, db: DB, request: Request
) -> dict[str, Any]:
    mutation(request, actor)
    build = authorized_build(
        db, actor, build_id, scope="distribution:write", admin=actor.user_id is not None
    )
    db.refresh(build, with_for_update=True)
    if build.upload_state != "SEALED":
        raise BuildSizeError("UPLOAD_STATE_CONFLICT", "只允许关联同一封存构建")
    if body.install_page_url:
        validate_link(request.app.state.config, body.install_page_url)
    payload = body.model_dump(mode="json")
    row = db.scalar(
        select(DistributionLink).where(
            DistributionLink.build_id == build.id, DistributionLink.provider == "pgyer"
        )
    )
    if row and row.payload == payload:
        return row.payload
    if row and row.publish_state == "published":
        raise BuildSizeError("DISTRIBUTION_CONFLICT", "已有成功发布关联，不静默替换其他发布")
    if row is None:
        row = DistributionLink(
            build_id=build.id, provider="pgyer", publish_state=body.publish_state, payload=payload
        )
        db.add(row)
    else:
        row.publish_state, row.payload = body.publish_state, payload
    audit(db, actor, build.project_id, "distribution.pgyer", build.id, request.state.request_id)
    db.commit()
    return payload


@router.get("/builds/{build_id}/git-changes")
def git_changes(
    build_id: uuid.UUID,
    actor: Auth,
    db: DB,
    repo_key: str | None = None,
    revision_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    from buildsize.db.models import AnalysisRevision, GitFileChangeRow, GitRangeRow

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
    if revision is None:
        raise BuildSizeError("ANALYSIS_NOT_AVAILABLE", "分析修订尚不可用")
    query = (
        select(GitFileChangeRow, GitRangeRow)
        .join(GitRangeRow)
        .where(GitRangeRow.revision_id == revision.id)
    )
    if repo_key:
        query = query.where(GitRangeRow.repo_key == repo_key)
    rows = db.execute(
        query.order_by(GitRangeRow.repo_key, GitFileChangeRow.change_index)
        .offset(offset)
        .limit(limit + 1)
    ).all()
    return {
        "analysis_revision": str(revision.id),
        "state": (revision.summary or {}).get("coverage", {}).get("git_changes", "unsupported"),
        "items": [
            {
                "repo_key": repo.repo_key,
                "base_revision": repo.base_revision,
                "head_revision": repo.head_revision,
                "range_status": repo.range_status,
                "status": change.status,
                "old_path": change.old_path,
                "new_path": change.new_path,
                "old_blob": change.old_blob,
                "new_blob": change.new_blob,
                "source_guid": change.source_guid,
            }
            for change, repo in rows[:limit]
        ],
        "next_offset": offset + limit if len(rows) > limit else None,
    }
