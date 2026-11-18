"""至少一次消费；以数据库 lease/fencing 校验后一次事务发布全部结果。"""

import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine, insert, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from buildsize.analyzers.apk_zip import Limits, analyze_apk
from buildsize.comparison.apk import compare_apks
from buildsize.config import Config
from buildsize.db.models import (
    AnalysisRevision,
    ApkEntryRow,
    ArtifactBlob,
    Build,
    BuildArtifact,
    ChangeRecord,
    Comparison,
    Job,
    Project,
)
from buildsize.domain.apk import (
    ApkEntry,
    ApkSnapshot,
    CategorySize,
    DirectorySize,
    DuplicateContent,
)
from buildsize.domain.contracts import BuildMeta, GitChanges, Submission
from buildsize.domain.errors import BuildSizeError
from buildsize.jobs.git_layers import evidence as git_evidence
from buildsize.jobs.git_layers import persist_git, read_git
from buildsize.jobs.unity_layers import (
    Layers,
    load_layers,
    persist_comparison,
    persist_layers,
    read_layers,
)
from buildsize.services.builds import PARSER_SET
from buildsize.services.common import aware, now
from buildsize.services.comparisons import (
    POLICY_VERSION,
    choose_baseline,
    comparison_summary,
    compatibility,
    eligible,
)
from buildsize.services.rules import freeze_rules, persist_findings
from buildsize.storage.local import BlobStorage, LocalBlobStorage

TERMINAL = {"SUCCEEDED", "FAILED", "SUPERSEDED"}
LOGGER = logging.getLogger("buildsize.jobs")


@dataclass(frozen=True)
class Lease:
    job_id: uuid.UUID
    revision_id: uuid.UUID
    build_id: uuid.UUID
    token: int
    kind: str
    comparison_id: uuid.UUID | None
    deadline: datetime
    input_sha256: str


def check_deadline(lease: Lease) -> None:
    if now() >= lease.deadline:
        raise BuildSizeError("ANALYSIS_TIMEOUT", "分析任务超过总期限")


def abi_consistent(snapshot: ApkSnapshot, metadata: BuildMeta) -> bool:
    observed = {row.detected_abi for row in snapshot.entries if row.category == "native_library"}
    return not observed or observed == set(metadata.settings.architectures or [])


def log_stage(
    lease: Lease, stage: str, event: str, started: float, error_code: str | None = None
) -> None:
    LOGGER.info(
        json.dumps(
            {
                "job_id": str(lease.job_id),
                "build_id": str(lease.build_id),
                "analysis_revision": str(lease.revision_id),
                "stage": stage,
                "event": event,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "parser_version": PARSER_SET,
                "timestamp": now().isoformat(),
                "input_manifest_sha256": lease.input_sha256,
                "error_code": error_code,
            },
            separators=(",", ":"),
        )
    )


def claim(engine: Engine, config: Config, job_id: uuid.UUID) -> Lease | None:
    with Session(engine) as db, db.begin():
        job = db.get(Job, job_id, with_for_update=True)
        if job is None or job.state in TERMINAL:
            return None
        if job.state == "RUNNING" and job.lease_expires_at and aware(job.lease_expires_at) > now():
            return None
        if job.attempt >= 3:
            _fail_locked(db, job, "WORKER_ATTEMPTS_EXHAUSTED")
            return None
        job.state, job.attempt, job.lease_token = "RUNNING", job.attempt + 1, job.lease_token + 1
        job.lease_owner = uuid.uuid4().hex
        job.lease_expires_at = now() + timedelta(seconds=config.analysis_timeout_seconds + 60)
        revision = db.get(AnalysisRevision, job.analysis_revision_id)
        assert revision is not None
        if job.kind == "analysis":
            revision.status = "RUNNING"
            build = db.get(Build, job.build_id, with_for_update=True)
            assert build is not None
            if build.requested_analysis_id == revision.id:
                build.analysis_state = "RUNNING"
        else:
            comparison = db.get(Comparison, job.comparison_id)
            assert comparison is not None
            comparison.status = "RUNNING"
        return Lease(
            job.id,
            job.analysis_revision_id,
            job.build_id,
            job.lease_token,
            job.kind,
            job.comparison_id,
            aware(job.lease_expires_at) - timedelta(seconds=60),
            revision.input_sha256,
        )


def valid_lease(db: Session, lease: Lease) -> Job | None:
    job = db.get(Job, lease.job_id, with_for_update=True)
    if (
        job is None
        or job.state != "RUNNING"
        or job.lease_token != lease.token
        or job.lease_expires_at is None
        or aware(job.lease_expires_at) <= now()
    ):
        return None
    return job


def _fail_locked(db: Session, job: Job, code: str) -> None:
    job.state, job.error_code = "FAILED", code
    if job.kind == "analysis":
        revision = db.get(AnalysisRevision, job.analysis_revision_id)
        assert revision is not None
        revision.status, revision.error_code = "FAILED", code
        build = db.get(Build, job.build_id, with_for_update=True)
        assert build is not None
        if build.requested_analysis_id == revision.id:
            build.analysis_state = "FAILED"
    else:
        comparison = db.get(Comparison, job.comparison_id)
        assert comparison is not None
        comparison.status, comparison.error_code = "FAILED", code


def fail(engine: Engine, lease: Lease, code: str, *, transient: bool = False) -> None:
    with Session(engine) as db, db.begin():
        job = valid_lease(db, lease)
        if job is None:
            return
        if transient and job.attempt < 3:
            job.state, job.error_code, job.lease_expires_at = "QUEUED", code, None
            if job.kind == "analysis":
                revision = db.get(AnalysisRevision, lease.revision_id)
                assert revision is not None
                revision.status = "QUEUED"
                build = db.get(Build, lease.build_id, with_for_update=True)
                assert build is not None
                if build.requested_analysis_id == revision.id:
                    build.analysis_state = "QUEUED"
            else:
                comparison = db.get(Comparison, job.comparison_id)
                assert comparison is not None
                comparison.status = "QUEUED"
        else:
            _fail_locked(db, job, code)


def snapshot_metadata(snapshot: ApkSnapshot) -> dict[str, Any]:
    return {
        "parser_version": snapshot.parser_version,
        "classifier_version": snapshot.classifier_version,
        "apk_sha256": snapshot.apk_sha256,
        "apk_file_bytes": snapshot.apk_file_bytes,
        "zip_payload_bytes": snapshot.zip_payload_bytes,
        "zip_uncompressed_bytes": snapshot.zip_uncompressed_bytes,
        "apk_overhead_bytes": snapshot.apk_overhead_bytes,
        "categories": [asdict(row) for row in snapshot.categories],
        "directories": [asdict(row) for row in snapshot.directories],
        "duplicate_content": [asdict(row) for row in snapshot.duplicate_content],
        "warnings": list(snapshot.warnings),
    }


def load_snapshot(db: Session, revision: AnalysisRevision) -> ApkSnapshot:
    if revision.summary is None or "apk" not in revision.summary:
        raise BuildSizeError("ANALYSIS_NOT_AVAILABLE", "该修订没有完整 APK 索引")
    value = revision.summary["apk"]
    entries = tuple(
        ApkEntry(
            row.entry_index,
            row.raw_path,
            row.normalized_path,
            row.is_directory,
            row.compressed_bytes,
            row.uncompressed_bytes,
            row.compression_method,
            row.crc32,
            row.content_sha256,
            row.category,
            row.detected_abi,
        )
        for row in db.scalars(
            select(ApkEntryRow)
            .where(ApkEntryRow.revision_id == revision.id)
            .order_by(ApkEntryRow.entry_index)
        )
    )
    return ApkSnapshot(
        value["parser_version"],
        value["classifier_version"],
        value["apk_sha256"],
        value["apk_file_bytes"],
        value["zip_payload_bytes"],
        value["zip_uncompressed_bytes"],
        value["apk_overhead_bytes"],
        entries,
        tuple(CategorySize(**row) for row in value["categories"]),
        tuple(DirectorySize(**row) for row in value["directories"]),
        tuple(
            DuplicateContent(row["content_sha256"], tuple(row["paths"]), row["compressed_bytes"])
            for row in value["duplicate_content"]
        ),
        tuple(value["warnings"]),
    )


def _bulk(
    db: Session, model: type[ApkEntryRow] | type[ChangeRecord], rows: list[dict[str, Any]]
) -> None:
    for start in range(0, len(rows), 1000):
        db.execute(insert(model), rows[start : start + 1000])


def _changes(
    db: Session,
    comparison: Comparison,
    old: ApkSnapshot | None,
    new: ApkSnapshot,
    base_meta: BuildMeta | None,
    target_meta: BuildMeta,
) -> None:
    result = compare_apks(old, new)
    comparison.status = result.status
    comparison.summary = comparison_summary(result, base_meta, target_meta, comparison.comparable)
    _bulk(
        db,
        ChangeRecord,
        [
            {
                "comparison_id": comparison.id,
                "layer": "apk_entry",
                "object_key": row.path,
                "path_hash": hashlib.sha256(row.path.encode()).hexdigest(),
                "status": row.status,
                "category": row.category,
                "old_bytes": row.old_compressed_bytes,
                "new_bytes": row.new_compressed_bytes,
                "delta_bytes": row.delta_compressed_bytes,
                "content_changed": row.content_changed,
                "compressed_size_changed": row.compressed_size_changed,
                "compression_method_changed": row.compression_method_changed,
            }
            for row in result.changes
        ],
    )


def publish_analysis(
    engine: Engine,
    lease: Lease,
    snapshot: ApkSnapshot,
    layers: Layers | None = None,
    git_report: GitChanges | None = None,
) -> bool:
    check_deadline(lease)
    with Session(engine) as db, db.begin():
        job = valid_lease(db, lease)
        if job is None:
            return False
        build = db.get(Build, lease.build_id)
        assert build is not None
        project = db.get(Project, build.project_id, with_for_update=True)
        assert project is not None
        db.refresh(build, with_for_update=True)
        revision = db.get(AnalysisRevision, lease.revision_id)
        assert build is not None and revision is not None
        if build.requested_analysis_id != revision.id:
            job.state, revision.status = "SUPERSEDED", "SUPERSEDED"
            return False
        metadata = BuildMeta.model_validate(build.build_metadata)
        _bulk(
            db,
            ApkEntryRow,
            [
                {
                    **asdict(row),
                    "revision_id": revision.id,
                    "path_hash": hashlib.sha256(row.normalized_path.encode()).hexdigest(),
                }
                for row in snapshot.entries
            ],
        )
        if abi_consistent(snapshot, metadata):
            selected_base, reason = choose_baseline(db, build, metadata)
            base_revision_id = selected_base.id if selected_base else None
        else:
            base_revision_id, reason = None, "apk_abi_mismatch"
        coverage = (revision.summary or {}).get("coverage", {})
        unity_summary: dict[str, Any] = (
            layers.summary if layers else {"state": "missing", "importers": {"state": "missing"}}
        )
        coverage = {
            **coverage,
            "unity_report": unity_summary["state"],
            "asset_importers": unity_summary["importers"]["state"],
        }
        if git_report is not None:
            coverage["git_changes"] = (
                "complete"
                if {repo.repo_key for repo in git_report.repositories}
                == {repo.repo_key for repo in metadata.repositories}
                and all(
                    repo.range_status in {"complete", "no_baseline"}
                    for repo in git_report.repositories
                )
                else "partial"
            )
        if layers:
            persist_layers(db, revision, layers)
        persist_git(db, revision, git_report)
        state = (
            "PARTIAL"
            if any(coverage.get(layer) != "complete" for layer in project.expected_layers)
            else "READY"
        )
        revision.apk_file_bytes = snapshot.apk_file_bytes
        revision.zip_payload_bytes = snapshot.zip_payload_bytes
        revision.apk_overhead_bytes = snapshot.apk_overhead_bytes
        key, warnings = compatibility(metadata)
        consistent = abi_consistent(snapshot, metadata)
        if not consistent:
            warnings.append(
                "APK 的 native 库目录 ABI 与声明配置不同；排除自动基线。目录 ABI 未核验 ELF 架构。"
            )
        revision.compatibility_key = (
            key if consistent and eligible(metadata, build.created_at) else None
        )
        revision.status = state
        base = db.get(AnalysisRevision, base_revision_id) if base_revision_id else None
        base_build = db.get(Build, base.build_id) if base else None
        comparison = Comparison(
            project_id=build.project_id,
            base_build_id=base.build_id if base else None,
            target_build_id=build.id,
            base_revision_id=base.id if base else None,
            target_revision_id=revision.id,
            request_key=f"auto:{revision.id}",
            policy_version=POLICY_VERSION,
            selection_reason=reason,
            status="RUNNING",
            comparable=base is not None,
        )
        db.add(comparison)
        freeze_rules(db, comparison, project)
        db.flush()
        base_snapshot = load_snapshot(db, base) if base else None
        _changes(
            db,
            comparison,
            base_snapshot,
            snapshot,
            BuildMeta.model_validate(base_build.build_metadata) if base_build else None,
            metadata,
        )
        persist_comparison(db, comparison, base, layers.snapshot if layers else None, revision)
        git_evidence(
            db,
            comparison,
            BuildMeta.model_validate(base_build.build_metadata) if base_build else None,
            metadata,
        )
        revision.summary = {
            "coverage": coverage,
            "stage": "complete",
            "apk": snapshot_metadata(snapshot),
            "comparison_id": str(comparison.id),
            "warnings": warnings,
            "metadata_verification": "declared",
            "unity": unity_summary,
        }
        persist_findings(
            db,
            comparison,
            base_snapshot,
            snapshot,
            BuildMeta.model_validate(base_build.build_metadata) if base_build else None,
            metadata,
            revision,
            coverage,
            unity_summary,
        )
        build.current_analysis_id, build.analysis_state, job.state = revision.id, state, "SUCCEEDED"
        job.error_code = None
        db.flush()
        import sys

        try:
            import resource

            peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            peak_bytes = peak if sys.platform == "darwin" else peak * 1024
        except ImportError:
            peak_bytes = None
        revision.summary = {
            **(revision.summary or {}),
            "runtime": {"worker_peak_rss_bytes": peak_bytes},
        }
        check_deadline(lease)
        if job.lease_expires_at is None or aware(job.lease_expires_at) <= now():
            raise BuildSizeError("LEASE_LOST", "发布时 lease 已过期")
        return True


def run_analysis(engine: Engine, config: Config, storage: BlobStorage, lease: Lease) -> bool:
    started = time.monotonic()
    log_stage(lease, "validate_inputs", "started", started)
    with Session(engine) as db:
        build = db.get(Build, lease.build_id)
        assert build is not None
        BuildMeta.model_validate(build.build_metadata)
        manifest = Submission.model_validate(build.manifest)
        blobs = {
            role: db.get(ArtifactBlob, blob_id)
            for role, blob_id in db.execute(
                select(BuildArtifact.role, BuildArtifact.blob_id).where(
                    BuildArtifact.build_id == build.id
                )
            )
        }
        for item in manifest.artifacts:
            check_deadline(lease)
            blob = blobs[item.role]
            if blob is None or blob.project_id != build.project_id:
                raise BuildSizeError("BLOB_CORRUPTED", "输入产物归属无效")
            if item.role != "apk":
                digest = hashlib.sha256()
                with storage.read_path(blob.storage_key) as path, path.open("rb") as source:
                    count = 0
                    while chunk := source.read(1024 * 1024):
                        check_deadline(lease)
                        count += len(chunk)
                        if count > item.bytes:
                            break
                        digest.update(chunk)
                if count != item.bytes or digest.hexdigest() != item.sha256:
                    raise BuildSizeError("BLOB_CORRUPTED", "封存报告校验失败")
        apk = blobs["apk"]
        assert apk is not None
        apk_key = apk.storage_key
        expected_sha = next(item.sha256 for item in manifest.artifacts if item.role == "apk")
    log_stage(lease, "validate_inputs", "completed", started)
    started = time.monotonic()
    log_stage(lease, "apk_zip", "started", started)
    with storage.read_path(apk_key) as path:
        snapshot = analyze_apk(
            path,
            limits=Limits(
                max_apk_bytes=config.upload_max_bytes,
                timeout_seconds=max(0.001, (lease.deadline - now()).total_seconds()),
            ),
        )
    if snapshot.apk_sha256 != expected_sha:
        raise BuildSizeError("BLOB_CORRUPTED", "封存 APK 校验失败")
    log_stage(lease, "apk_zip", "completed", started)
    started = time.monotonic()
    log_stage(lease, "unity_report", "started", started)
    with Session(engine) as db:
        build = db.get(Build, lease.build_id)
        assert build is not None
        layers = read_layers(db, storage, build, snapshot)
        git_report = read_git(db, storage, build)
    check_deadline(lease)
    log_stage(lease, "unity_report", "completed", started)
    return publish_analysis(engine, lease, snapshot, layers, git_report)


def run_comparison(engine: Engine, lease: Lease) -> bool:
    check_deadline(lease)
    with Session(engine) as db, db.begin():
        job = valid_lease(db, lease)
        if job is None:
            return False
        comparison = db.get(Comparison, lease.comparison_id)
        assert comparison is not None
        base = db.get(AnalysisRevision, comparison.base_revision_id)
        target = db.get(AnalysisRevision, comparison.target_revision_id)
        old_build = db.get(Build, comparison.base_build_id)
        new_build = db.get(Build, comparison.target_build_id)
        assert base and target and old_build and new_build
        old_snapshot, new_snapshot = load_snapshot(db, base), load_snapshot(db, target)
        _changes(
            db,
            comparison,
            old_snapshot,
            new_snapshot,
            BuildMeta.model_validate(old_build.build_metadata),
            BuildMeta.model_validate(new_build.build_metadata),
        )
        persist_comparison(db, comparison, base, load_layers(db, target), target)
        git_evidence(
            db,
            comparison,
            BuildMeta.model_validate(old_build.build_metadata),
            BuildMeta.model_validate(new_build.build_metadata),
        )
        persist_findings(
            db,
            comparison,
            old_snapshot,
            new_snapshot,
            BuildMeta.model_validate(old_build.build_metadata),
            BuildMeta.model_validate(new_build.build_metadata),
            target,
            (target.summary or {}).get("coverage", {}),
            (target.summary or {}).get("unity", {}),
        )
        job.state, job.error_code = "SUCCEEDED", None
        db.flush()
        check_deadline(lease)
        if job.lease_expires_at is None or aware(job.lease_expires_at) <= now():
            raise BuildSizeError("LEASE_LOST", "发布时 lease 已过期")
        return True


def execute_job(
    engine: Engine, config: Config, job_id: uuid.UUID, storage: BlobStorage | None = None
) -> str:
    lease = claim(engine, config, job_id)
    if lease is None:
        return "IGNORED"
    started = time.monotonic()
    log_stage(lease, lease.kind, "started", started)
    try:
        if lease.kind == "comparison":
            published = run_comparison(engine, lease)
        else:
            published = run_analysis(
                engine, config, storage or LocalBlobStorage(config.blob_root), lease
            )
        state = "PUBLISHED" if published else "STALE"
        log_stage(lease, lease.kind, state, started)
        return state
    except BuildSizeError as exc:
        log_stage(lease, lease.kind, "failed", started, exc.code)
        fail(engine, lease, exc.code, transient=exc.code in {"INPUT_IO_ERROR", "STORAGE_IO_ERROR"})
        raise BuildSizeError(exc.code, "任务失败；错误码已保存") from None
    except OSError:
        log_stage(lease, lease.kind, "failed", started, "STORAGE_IO_ERROR")
        fail(engine, lease, "STORAGE_IO_ERROR", transient=True)
        raise BuildSizeError("STORAGE_IO_ERROR", "存储暂不可用，按任务策略重试") from None
    except OperationalError:
        fail(engine, lease, "DATABASE_IO_ERROR", transient=True)
        raise BuildSizeError("DATABASE_IO_ERROR", "数据库暂不可用，按任务策略重试") from None
    except Exception:
        log_stage(lease, lease.kind, "failed", started, "ANALYSIS_INTERNAL_ERROR")
        fail(engine, lease, "ANALYSIS_INTERNAL_ERROR")
        raise BuildSizeError("ANALYSIS_INTERNAL_ERROR", "任务异常，未发布结果") from None
