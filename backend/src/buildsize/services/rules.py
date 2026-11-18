"""配置仅追加版本，比较冻结配置；findings 在 fencing 事务内写入。"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from buildsize.db.models import (
    AnalysisRevision,
    Comparison,
    FindingRow,
    Project,
    RuleConfigRow,
    UnityChangeRow,
)
from buildsize.domain.apk import ApkSnapshot
from buildsize.domain.contracts import BuildMeta
from buildsize.rules.config import RuleSettings
from buildsize.rules.engine import RuleInput, evaluate
from buildsize.services.common import fingerprint


def current_settings(db: Session, project: Project) -> RuleSettings:
    if project.rules_version == 0:
        return RuleSettings(
            fixed_baseline_id=str(project.fixed_baseline_id) if project.fixed_baseline_id else None
        )
    row = db.scalar(
        select(RuleConfigRow).where(
            RuleConfigRow.project_id == project.id, RuleConfigRow.version == project.rules_version
        )
    )
    assert row is not None
    return RuleSettings.model_validate(row.payload)


def freeze_rules(db: Session, comparison: Comparison, project: Project) -> None:
    comparison.rules_version = project.rules_version
    comparison.rules_snapshot = current_settings(db, project).model_dump(mode="json")


def persist_findings(
    db: Session,
    comparison: Comparison,
    base: ApkSnapshot | None,
    target: ApkSnapshot,
    old_meta: BuildMeta | None,
    new_meta: BuildMeta,
    revision: AnalysisRevision,
    coverage: dict[str, str],
    unity_summary: dict[str, Any],
) -> None:
    if comparison.rules_snapshot is None:
        # 旧比较没有历史配置证据，重算时使用并记录当前版本，不能假装存在旧配置。
        project = db.get(Project, comparison.project_id)
        assert project is not None
        freeze_rules(db, comparison, project)
    settings = RuleSettings.model_validate(comparison.rules_snapshot)
    if comparison.base_revision_id:
        old_revision = db.get(AnalysisRevision, comparison.base_revision_id)
        if old_revision:
            coverage = {
                **coverage,
                **{
                    "base." + key: state
                    for key, state in (old_revision.summary or {}).get("coverage", {}).items()
                    if key != "android_metadata"
                },
            }
    assets: list[dict[str, Any]] = []
    importers: list[dict[str, Any]] = []
    for row in db.scalars(
        select(UnityChangeRow).where(UnityChangeRow.comparison_id == comparison.id)
    ):
        (assets if row.kind == "asset" else importers).append(row.details)
    value = RuleInput(
        str(revision.id),
        str(comparison.base_revision_id) if base else None,
        base,
        target,
        old_meta,
        new_meta,
        comparison.comparable,
        coverage,
        assets,
        importers,
        unity_summary,
    )
    counts: dict[str, int] = {}
    total = 0
    for item in evaluate(value, settings):
        total += 1
        counts[item.rule_id] = counts.get(item.rule_id, 0) + 1
        if total <= 5000:
            db.add(
                FindingRow(
                    comparison_id=comparison.id,
                    finding_key=fingerprint([item.rule_id, item.subject.model_dump()]),
                    rule_id=item.rule_id,
                    confidence=item.confidence,
                    severity=item.severity,
                    subject_key=item.subject.key,
                    payload=item.model_dump(mode="json"),
                    review_state="unreviewed",
                    review_version=0,
                )
            )
    summary = dict(comparison.summary or {})
    summary.update(
        finding_count=total,
        stored_finding_count=min(total, 5000),
        findings_truncated=total > 5000,
        finding_counts=counts,
        rules_version=comparison.rules_version,
        quality_gate=("WARN" if counts.get("APK_GROWTH_THRESHOLD") else "PASS")
        if comparison.comparable and base
        else "SKIPPED",
        absolute_budget_gate="NOT_CONFIGURED"
        if settings.absolute_budget_bytes is None
        else "WARN"
        if counts.get("APK_ABSOLUTE_BUDGET")
        else "PASS",
    )
    comparison.summary = summary
