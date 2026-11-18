"""只使用上报的 Git 证据；服务端不执行 Git、不拉取任何仓库。"""

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from buildsize.db.models import (
    AnalysisRevision,
    ArtifactBlob,
    Build,
    BuildArtifact,
    Comparison,
    GitFileChangeRow,
    GitRangeRow,
)
from buildsize.domain.contracts import BuildMeta, GitChanges
from buildsize.domain.validation import read_json
from buildsize.storage.local import BlobStorage


def read_git(db: Session, storage: BlobStorage, build: Build) -> GitChanges | None:
    artifact = db.scalar(
        select(BuildArtifact).where(
            BuildArtifact.build_id == build.id, BuildArtifact.role == "git_changes"
        )
    )
    if artifact is None or artifact.schema_state != "complete":
        return None
    blob = db.get(ArtifactBlob, artifact.blob_id)
    assert blob is not None and blob.project_id == build.project_id
    with storage.read_path(blob.storage_key) as path:
        return GitChanges.model_validate(read_json(path))


def persist_git(db: Session, revision: AnalysisRevision, report: GitChanges | None) -> None:
    if report is None:
        return
    for repo in report.repositories:
        row = GitRangeRow(
            revision_id=revision.id,
            repo_key=repo.repo_key,
            base_revision=repo.base_revision,
            head_revision=repo.head_revision,
            range_status=repo.range_status,
            is_ancestor=repo.is_ancestor,
            commit_ids=repo.commit_ids,
        )
        db.add(row)
        db.flush()
        values = [
            {**change.model_dump(), "range_id": row.id, "change_index": index}
            for index, change in enumerate(repo.changes)
        ]
        for start in range(0, len(values), 1000):
            db.execute(insert(GitFileChangeRow), values[start : start + 1000])


def evidence(
    db: Session, comparison: Comparison, base: BuildMeta | None, target: BuildMeta
) -> None:
    summary = dict(comparison.summary or {})
    if base is None:
        summary.update(
            git_evidence_status="NO_BASELINE", git_change_count=None, git_matched_repositories=[]
        )
        comparison.summary = summary
        return
    ranges = db.scalars(
        select(GitRangeRow).where(GitRangeRow.revision_id == comparison.target_revision_id)
    ).all()
    if not ranges:
        summary.update(
            git_evidence_status="missing", git_change_count=None, git_matched_repositories=[]
        )
        comparison.summary = summary
        return
    old = {repo.repo_key: repo for repo in base.repositories}
    new = {repo.repo_key: repo for repo in target.repositories}
    matched = []
    count = 0
    partial = False
    for row in ranges:
        before, after = old.get(row.repo_key), new.get(row.repo_key)
        if (
            before is None
            or after is None
            or (row.base_revision, row.head_revision) != (before.revision, after.revision)
        ):
            partial = True
            continue
        if row.range_status not in {"complete", "unrelated_history"}:
            partial = True
            continue
        matched.append(row.repo_key)
        partial = partial or before.dirty or after.dirty or row.range_status != "complete"
        from sqlalchemy import func

        count += (
            db.scalar(
                select(func.count())
                .select_from(GitFileChangeRow)
                .where(GitFileChangeRow.range_id == row.id)
            )
            or 0
        )
    summary["git_evidence_status"] = (
        "partial"
        if matched and (partial or set(matched) != set(old) | set(new))
        else "complete"
        if matched
        else "mismatched_range"
    )
    summary["git_change_count"] = count if matched else None
    summary["git_matched_repositories"] = matched
    summary["git_evidence_warning"] = (
        "仅说明匹配提交范围的文件变化，不是根因或责任证明；dirty 工作树不在提交差异中。"
    )
    comparison.summary = summary
