"""M0 持久模型；分析明细表在 M2 随实际写入流程添加。"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Entity(Base):
    __abstract__ = True
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Project(Entity):
    __tablename__ = "projects"
    key: Mapped[str] = mapped_column(String(63), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    rules_version: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    expected_layers: Mapped[list[str]] = mapped_column(
        JSON_VALUE,
        default=lambda: ["unity_report", "asset_importers", "settings"],
        server_default='["unity_report","asset_importers","settings"]',
    )
    fixed_baseline_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("builds.id", name="fk_project_fixed_baseline", use_alter=True)
    )


class User(Entity):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(128), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))


class Membership(Entity):
    __tablename__ = "project_memberships"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="uq_membership_user_project"),
        CheckConstraint("role IN ('admin','viewer')", name="ck_membership_role"),
    )


class ApiToken(Entity):
    __tablename__ = "api_tokens"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    scopes: Mapped[list[str]] = mapped_column(JSON_VALUE)
    expires_at: Mapped[int] = mapped_column(BigInteger)
    revoked_at: Mapped[int | None] = mapped_column(BigInteger)


class WebSession(Entity):
    __tablename__ = "web_sessions"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[int] = mapped_column(BigInteger)


class LoginBucket(Entity):
    __tablename__ = "login_buckets"
    peer_hash: Mapped[str] = mapped_column(String(64), unique=True)
    window_start: Mapped[int] = mapped_column(BigInteger)
    attempts: Mapped[int] = mapped_column(BigInteger)


class Build(Entity):
    __tablename__ = "builds"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"))
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    ci_instance_id: Mapped[str] = mapped_column(String(200))
    ci_job_full_name: Mapped[str] = mapped_column(String(500))
    ci_build_number: Mapped[str] = mapped_column(String(128))
    variant: Mapped[str] = mapped_column(String(200))
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    build_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    upload_state: Mapped[str] = mapped_column(String(16), default="UPLOADING")
    analysis_state: Mapped[str] = mapped_column(String(16), default="PENDING")
    build_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON_VALUE)
    upload_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_revisions.id", name="fk_build_requested_analysis", use_alter=True)
    )
    current_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_revisions.id", name="fk_build_current_analysis", use_alter=True)
    )
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "ci_instance_id",
            "ci_job_full_name",
            "ci_build_number",
            "variant",
            name="uq_build_logical_key",
        ),
        CheckConstraint("upload_state IN ('UPLOADING','SEALED','EXPIRED')", name="ck_build_upload"),
        CheckConstraint(
            "analysis_state IN ('PENDING','QUEUED','RUNNING','READY','PARTIAL','FAILED')",
            name="ck_build_analysis",
        ),
        Index("ix_build_project_completed", "project_id", "build_completed_at"),
    )


class ArtifactBlob(Entity):
    __tablename__ = "artifact_blobs"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"))
    sha256: Mapped[str] = mapped_column(String(64))
    bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(500))
    gc_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("project_id", "sha256", name="uq_blob_project_sha"),
        CheckConstraint("bytes >= 0 AND bytes <= 9007199254740991", name="ck_blob_bytes"),
    )


class BuildArtifact(Entity):
    __tablename__ = "build_artifacts"
    build_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("builds.id"))
    role: Mapped[str] = mapped_column(String(32))
    blob_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artifact_blobs.id"))
    schema_state: Mapped[str] = mapped_column(String(32))
    data_error: Mapped[str | None] = mapped_column(String(128))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("build_id", "role", name="uq_build_artifact_role"),)


class AnalysisRevision(Entity):
    __tablename__ = "analysis_revisions"
    build_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("builds.id"))
    input_sha256: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    apk_file_bytes: Mapped[int | None] = mapped_column(BigInteger)
    zip_payload_bytes: Mapped[int | None] = mapped_column(BigInteger)
    apk_overhead_bytes: Mapped[int | None] = mapped_column(BigInteger)
    request_key: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON_VALUE)
    compatibility_key: Mapped[str | None] = mapped_column(String(64), index=True)
    error_code: Mapped[str | None] = mapped_column(String(128))
    __table_args__ = (
        UniqueConstraint("build_id", "request_key", name="uq_revision_request"),
        CheckConstraint(
            "apk_file_bytes IS NULL OR (apk_file_bytes >= 0 AND "
            "apk_file_bytes <= 9007199254740991)",
            name="ck_analysis_apk_bytes",
        ),
        CheckConstraint(
            "zip_payload_bytes IS NULL OR zip_payload_bytes >= 0", name="ck_analysis_payload"
        ),
        CheckConstraint(
            "apk_overhead_bytes IS NULL OR apk_overhead_bytes >= 0", name="ck_analysis_overhead"
        ),
        CheckConstraint(
            "apk_file_bytes IS NULL OR zip_payload_bytes IS NULL OR "
            "apk_overhead_bytes IS NULL OR apk_file_bytes = "
            "zip_payload_bytes + apk_overhead_bytes",
            name="ck_analysis_accounting",
        ),
    )


class Job(Entity):
    __tablename__ = "jobs"
    build_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("builds.id"))
    analysis_revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_revisions.id"))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    state: Mapped[str] = mapped_column(String(16))
    attempt: Mapped[int] = mapped_column(BigInteger, default=0)
    lease_token: Mapped[int] = mapped_column(BigInteger, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), default="analysis", server_default="analysis")
    comparison_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("comparisons.id", name="fk_job_comparison")
    )


class Outbox(Entity):
    __tablename__ = "outbox"
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(BigInteger, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Entity):
    __tablename__ = "audit_events"
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100))
    object_id: Mapped[str] = mapped_column(String(200))
    request_id: Mapped[str | None] = mapped_column(String(36))


class UploadLease(Entity):
    __tablename__ = "upload_leases"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    build_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("builds.id"))
    role: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("build_id", "role", name="uq_upload_lease_role"),)


class ApkEntryRow(Entity):
    __tablename__ = "apk_entries"
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_revisions.id"))
    entry_index: Mapped[int] = mapped_column(BigInteger)
    raw_path: Mapped[str] = mapped_column(Text)
    normalized_path: Mapped[str] = mapped_column(Text)
    path_hash: Mapped[str] = mapped_column(String(64))
    is_directory: Mapped[bool] = mapped_column(Boolean)
    compressed_bytes: Mapped[int] = mapped_column(BigInteger)
    uncompressed_bytes: Mapped[int] = mapped_column(BigInteger)
    compression_method: Mapped[int] = mapped_column(BigInteger)
    crc32: Mapped[int] = mapped_column(BigInteger)
    content_sha256: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32))
    detected_abi: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("revision_id", "path_hash", name="uq_entry_revision_path"),
        UniqueConstraint("revision_id", "entry_index", name="uq_entry_revision_index"),
        Index("ix_entry_revision_size", "revision_id", "compressed_bytes"),
        CheckConstraint("compressed_bytes >= 0 AND uncompressed_bytes >= 0", name="ck_entry_bytes"),
    )


class Comparison(Entity):
    __tablename__ = "comparisons"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"))
    base_build_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("builds.id"))
    target_build_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("builds.id"))
    base_revision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("analysis_revisions.id"))
    target_revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_revisions.id"))
    request_key: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(32))
    selection_reason: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    comparable: Mapped[bool] = mapped_column(Boolean)
    rules_version: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    rules_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON_VALUE)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON_VALUE)
    error_code: Mapped[str | None] = mapped_column(String(128))
    __table_args__ = (UniqueConstraint("project_id", "request_key", name="uq_comparison_request"),)


class ChangeRecord(Entity):
    __tablename__ = "change_records"
    comparison_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("comparisons.id"))
    layer: Mapped[str] = mapped_column(String(32))
    object_key: Mapped[str] = mapped_column(Text)
    path_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(32))
    old_bytes: Mapped[int] = mapped_column(BigInteger)
    new_bytes: Mapped[int] = mapped_column(BigInteger)
    delta_bytes: Mapped[int] = mapped_column(BigInteger)
    content_changed: Mapped[bool] = mapped_column(Boolean)
    compressed_size_changed: Mapped[bool] = mapped_column(Boolean)
    compression_method_changed: Mapped[bool] = mapped_column(Boolean)
    __table_args__ = (
        UniqueConstraint("comparison_id", "layer", "path_hash", name="uq_change_object"),
        Index("ix_change_comparison_delta", "comparison_id", "delta_bytes"),
        CheckConstraint("delta_bytes = new_bytes - old_bytes", name="ck_change_delta"),
    )


class UnityContainerRow(Entity):
    __tablename__ = "unity_containers"
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_revisions.id"))
    container_index: Mapped[int] = mapped_column(BigInteger)
    short_path: Mapped[str] = mapped_column(Text)
    overhead_bytes: Mapped[int] = mapped_column(BigInteger)
    mapping_state: Mapped[str] = mapped_column(String(16))
    apk_paths: Mapped[list[str]] = mapped_column(JSON_VALUE)
    mapper_version: Mapped[str] = mapped_column(String(64))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    __table_args__ = (
        UniqueConstraint("revision_id", "container_index", name="uq_unity_container"),
        CheckConstraint("overhead_bytes >= 0", name="ck_unity_overhead"),
    )


class UnityRecordRow(Entity):
    __tablename__ = "unity_asset_records"
    container_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("unity_containers.id"), index=True)
    record_index: Mapped[int] = mapped_column(BigInteger)
    source_guid: Mapped[str | None] = mapped_column(String(32), index=True)
    source_path: Mapped[str | None] = mapped_column(Text)
    asset_type: Mapped[str] = mapped_column(Text)
    packed_bytes: Mapped[int] = mapped_column(BigInteger)
    json_pointer: Mapped[str] = mapped_column(String(128))
    __table_args__ = (
        UniqueConstraint("container_id", "record_index", name="uq_unity_record"),
        CheckConstraint(
            "packed_bytes >= 0 AND packed_bytes <= 9007199254740991", name="ck_unity_record_bytes"
        ),
    )


class UnityGroupRow(Entity):
    __tablename__ = "unity_asset_groups"
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_revisions.id"))
    scope: Mapped[str] = mapped_column(String(16))
    group_key: Mapped[str] = mapped_column(String(64))
    identity_kind: Mapped[str] = mapped_column(String(32))
    source_guid: Mapped[str | None] = mapped_column(String(32))
    source_path: Mapped[str | None] = mapped_column(Text)
    asset_type: Mapped[str] = mapped_column(Text)
    source_paths: Mapped[list[str]] = mapped_column(JSON_VALUE)
    container_paths: Mapped[list[str]] = mapped_column(JSON_VALUE)
    packed_bytes: Mapped[int] = mapped_column(BigInteger)
    record_count: Mapped[int] = mapped_column(BigInteger)
    matchable: Mapped[bool] = mapped_column(Boolean)
    __table_args__ = (
        UniqueConstraint("revision_id", "scope", "group_key", name="uq_unity_group"),
        Index("ix_unity_group_size", "revision_id", "scope", "packed_bytes"),
        CheckConstraint("packed_bytes >= 0", name="ck_unity_group_bytes"),
    )


class AssetImporterRow(Entity):
    __tablename__ = "asset_importers"
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_revisions.id"))
    source_guid: Mapped[str] = mapped_column(String(32))
    source_path: Mapped[str] = mapped_column(Text)
    importer_type: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    json_pointer: Mapped[str] = mapped_column(String(128))
    __table_args__ = (UniqueConstraint("revision_id", "source_guid", name="uq_importer_guid"),)


class UnityChangeRow(Entity):
    __tablename__ = "unity_change_records"
    comparison_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("comparisons.id"))
    kind: Mapped[str] = mapped_column(String(16))
    group_key: Mapped[str] = mapped_column(String(64))
    source_guid: Mapped[str | None] = mapped_column(String(32))
    asset_type: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    old_packed_bytes: Mapped[int | None] = mapped_column(BigInteger)
    new_packed_bytes: Mapped[int | None] = mapped_column(BigInteger)
    delta_packed_bytes: Mapped[int | None] = mapped_column(BigInteger)
    details: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    __table_args__ = (
        UniqueConstraint("comparison_id", "kind", "group_key", name="uq_unity_change"),
        Index("ix_unity_change_delta", "comparison_id", "kind", "delta_packed_bytes"),
    )


class RepositorySnapshot(Entity):
    __tablename__ = "repository_snapshots"
    build_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("builds.id"))
    repo_key: Mapped[str] = mapped_column(Text)
    revision: Mapped[str] = mapped_column(String(64))
    dirty: Mapped[bool] = mapped_column(Boolean)
    relative_path: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))
    __table_args__ = (UniqueConstraint("build_id", "repo_key", name="uq_repository_snapshot"),)


class DistributionLink(Entity):
    __tablename__ = "distribution_links"
    build_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("builds.id"))
    provider: Mapped[str] = mapped_column(String(32))
    publish_state: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    __table_args__ = (UniqueConstraint("build_id", "provider", name="uq_distribution_provider"),)


class GitRangeRow(Entity):
    __tablename__ = "git_ranges"
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_revisions.id"))
    repo_key: Mapped[str] = mapped_column(Text)
    base_revision: Mapped[str | None] = mapped_column(String(64))
    head_revision: Mapped[str] = mapped_column(String(64))
    range_status: Mapped[str] = mapped_column(String(32))
    is_ancestor: Mapped[bool | None] = mapped_column(Boolean)
    commit_ids: Mapped[list[str]] = mapped_column(JSON_VALUE)
    __table_args__ = (UniqueConstraint("revision_id", "repo_key", name="uq_git_range"),)


class GitFileChangeRow(Entity):
    __tablename__ = "git_file_changes"
    range_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("git_ranges.id"), index=True)
    change_index: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(2))
    old_path: Mapped[str | None] = mapped_column(Text)
    new_path: Mapped[str | None] = mapped_column(Text)
    old_blob: Mapped[str | None] = mapped_column(String(64))
    new_blob: Mapped[str | None] = mapped_column(String(64))
    source_guid: Mapped[str | None] = mapped_column(String(32))
    __table_args__ = (UniqueConstraint("range_id", "change_index", name="uq_git_file_change"),)


class RuleConfigRow(Entity):
    __tablename__ = "rule_configs"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"))
    version: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_rule_config_version"),)


class FindingRow(Entity):
    __tablename__ = "findings"
    comparison_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("comparisons.id"))
    finding_key: Mapped[str] = mapped_column(String(64))
    rule_id: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16))
    subject_key: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE)
    review_state: Mapped[str] = mapped_column(String(16), default="unreviewed")
    review_version: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (
        UniqueConstraint("comparison_id", "finding_key", name="uq_comparison_finding"),
        Index("ix_finding_comparison_rule", "comparison_id", "rule_id"),
        CheckConstraint(
            "review_state IN ('unreviewed','confirmed','rejected')", name="ck_finding_review"
        ),
    )
