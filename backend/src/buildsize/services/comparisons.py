"""基线策略和不可变比较。仅对 APK 物理层求和。"""

import uuid
from collections import Counter
from dataclasses import asdict, fields
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from buildsize.auth.service import Principal
from buildsize.comparison.apk import ApkComparison
from buildsize.db.models import AnalysisRevision, Build, Comparison, Job, Outbox, Project
from buildsize.domain.contracts import BuildMeta
from buildsize.domain.errors import BuildSizeError
from buildsize.services.builds import PARSER_SET, audit, authorized_build
from buildsize.services.common import aware, fingerprint
from buildsize.services.rules import freeze_rules

POLICY_VERSION = "baseline-1"


def profile_key(project: str, profile: dict[str, Any]) -> str | None:
    fields = [
        project,
        "Android",
        "APK",
        profile.get("application_id"),
        profile.get("channel"),
        profile.get("variant"),
        profile.get("branch"),
        profile.get("development_build"),
        profile.get("scripting_backend"),
        profile.get("architectures"),
    ]
    if any(value is None or value == "" for value in fields) or not fields[-1]:
        return None
    fields[-1] = sorted(profile["architectures"])
    fields += [profile.get("ci_instance_id"), profile.get("ci_job_full_name")]
    if any(value is None or value == "" for value in fields[-2:]):
        return None
    return fingerprint(fields)


def compatibility(metadata: BuildMeta) -> tuple[str | None, list[str]]:
    profile = {
        **metadata.settings.model_dump(),
        "application_id": metadata.application_id,
        "channel": metadata.channel,
        "variant": metadata.variant,
        "branch": metadata.branch,
        "ci_instance_id": metadata.ci.instance_id,
        "ci_job_full_name": metadata.ci.job_full_name,
    }
    key = profile_key(metadata.project_key, profile)
    warnings = ["包名与配置来自声明，未由 Android 工具核验；自动基线限定同 CI 实例和任务。"]
    if key is None:
        warnings.append("硬兼容字段缺失，不能自动选择基线。")
    return key, warnings


def eligible(metadata: BuildMeta, uploaded_at: datetime) -> bool:
    completed = datetime.fromisoformat(metadata.build_completed_at.replace("Z", "+00:00"))
    return (
        compatibility(metadata)[0] is not None
        and completed.year >= 2000
        and completed <= aware(uploaded_at) + timedelta(minutes=5)
    )


def choose_baseline(
    db: Session, build: Build, metadata: BuildMeta
) -> tuple[AnalysisRevision | None, str]:
    key, _ = compatibility(metadata)
    if not eligible(metadata, build.created_at):
        return None, "incomplete_or_invalid_clock"
    project = db.get(Project, build.project_id)
    assert project is not None and build.build_completed_at is not None
    return select_candidate(db, project, key, aware(build.build_completed_at), build.id)


def select_candidate(
    db: Session,
    project: Project,
    key: str | None,
    completed: datetime,
    exclude_id: uuid.UUID | None = None,
) -> tuple[AnalysisRevision | None, str]:
    if key is None:
        return None, "incomplete_profile"
    query = (
        select(AnalysisRevision)
        .join(Build, Build.current_analysis_id == AnalysisRevision.id)
        .where(
            Build.project_id == project.id,
            Build.build_completed_at < completed,
            AnalysisRevision.status.in_(["READY", "PARTIAL"]),
            AnalysisRevision.compatibility_key == key,
        )
    )
    if exclude_id:
        query = query.where(Build.id != exclude_id)
    if project.fixed_baseline_id:
        fixed = db.scalar(query.where(Build.id == project.fixed_baseline_id))
        return fixed, "fixed_baseline" if fixed else "fixed_baseline_unavailable_or_incompatible"
    row = db.scalar(query.order_by(Build.build_completed_at.desc(), Build.id.desc()).limit(1))
    return row, "nearest_earlier_build" if row else "no_earlier_compatible_build"


def config_changes(base: BuildMeta, target: BuildMeta) -> list[dict[str, Any]]:
    old, new = base.model_dump(mode="json"), target.model_dump(mode="json")
    changes = []
    for group in ("settings", "toolchain", "content_policy"):
        for field in sorted(old[group].keys() | new[group].keys()):
            if old[group].get(field) != new[group].get(field):
                changes.append(
                    {
                        "field": f"{group}.{field}",
                        "old": old[group].get(field),
                        "new": new[group].get(field),
                        "source": "build_meta",
                        "old_missing": old[group].get(field) is None,
                        "new_missing": new[group].get(field) is None,
                    }
                )
    return changes


def comparison_summary(
    result: ApkComparison, base: BuildMeta | None, target: BuildMeta, comparable: bool
) -> dict[str, Any]:
    summary = {
        field.name: getattr(result, field.name)
        for field in fields(result)
        if field.name != "changes"
    }
    summary["categories"] = [asdict(row) for row in result.categories]
    summary["change_counts"] = dict(Counter(change.status for change in result.changes))
    summary["config_changes"] = config_changes(base, target) if base else []
    summary["warnings"] = compatibility(target)[1]
    summary["quality_gate"] = "NOT_EVALUATED" if comparable and base else "SKIPPED"
    if base and not comparable:
        summary["warnings"].append("跨兼容组或身份/时钟数据不完整；自动增长门禁跳过。")
    # 发布事务随后根据实际两端提交验证 Git 范围，初始值不冒充完整证据。
    summary["git_evidence_status"] = "missing" if base else "NO_BASELINE"
    summary["unity_analysis_status"] = "unsupported"
    return summary


def create_manual(
    db: Session,
    actor: Principal,
    base_id: uuid.UUID,
    target_id: uuid.UUID,
    request_key: str,
    request_id: str | None = None,
) -> tuple[Comparison, bool]:
    base = authorized_build(db, actor, base_id)
    target = authorized_build(db, actor, target_id)
    if base.project_id != target.project_id:
        raise BuildSizeError("NOT_FOUND", "资源不存在或无权访问")
    db.execute(select(Project).where(Project.id == target.project_id).with_for_update())
    existing = db.scalar(
        select(Comparison).where(
            Comparison.project_id == target.project_id,
            Comparison.request_key == "manual:" + request_key,
        )
    )
    if existing:
        if (existing.base_build_id, existing.target_build_id) != (base_id, target_id):
            raise BuildSizeError("BUILD_IDENTITY_CONFLICT", "比较幂等键已用于其他版本")
        return existing, False
    db.refresh(base)
    db.refresh(target)
    if base.current_analysis_id is None or target.current_analysis_id is None:
        raise BuildSizeError("ANALYSIS_NOT_AVAILABLE", "两端均需已有成功的 APK 分析")
    old = db.get(AnalysisRevision, base.current_analysis_id)
    new = db.get(AnalysisRevision, target.current_analysis_id)
    assert old is not None and new is not None
    comparable = (
        old.compatibility_key is not None and old.compatibility_key == new.compatibility_key
    )
    comparison = Comparison(
        project_id=target.project_id,
        base_build_id=base.id,
        target_build_id=target.id,
        base_revision_id=old.id,
        target_revision_id=new.id,
        request_key="manual:" + request_key,
        policy_version=POLICY_VERSION,
        selection_reason="manual",
        status="QUEUED",
        comparable=comparable,
    )
    db.add(comparison)
    project = db.get(Project, target.project_id)
    assert project is not None
    db.refresh(project, with_for_update=True)
    freeze_rules(db, comparison, project)
    db.flush()
    job = Job(
        build_id=target.id,
        analysis_revision_id=new.id,
        comparison_id=comparison.id,
        kind="comparison",
        state="QUEUED",
        idempotency_key=fingerprint([str(comparison.id), str(old.id), str(new.id), PARSER_SET]),
    )
    db.add(job)
    db.flush()
    db.add(Outbox(job_id=job.id, payload={"job_id": str(job.id)}))
    audit(db, actor, target.project_id, "comparison.create", comparison.id, request_id)
    return comparison, True
