"""服务响应合同；原始报告仍使用 schemas/ 中的版本化协议。"""

from typing import Any, Literal

from buildsize.domain.contracts import (
    ByteCount,
    Contract,
    Role,
    RunId,
    Sha256,
    Text,
    Timestamp,
    Uri,
)


class ErrorDetail(Contract):
    code: Text
    message: Text
    request_id: RunId
    details: dict[str, Any]


class ErrorEnvelope(Contract):
    error: ErrorDetail


class ArtifactState(Contract):
    role: Role
    bytes: ByteCount
    sha256: Sha256
    state: Literal["verified", "pending"]
    schema_state: str | None
    data_error: str | None
    expired_at: Timestamp | None


class JobState(Contract):
    job_id: RunId
    analysis_revision: RunId
    state: str
    attempt: ByteCount
    error_code: str | None
    apk_download_pending: bool


class BuildState(Contract):
    pinned: bool
    build_id: RunId
    run_id: RunId
    variant: Text
    upload_state: Literal["UPLOADING", "SEALED", "EXPIRED"]
    analysis_state: Literal["PENDING", "QUEUED", "RUNNING", "READY", "PARTIAL", "FAILED"]
    analysis_revision: RunId | None
    requested_analysis_revision: RunId | None
    metadata: dict[str, Any] | None
    result: dict[str, Any] | None
    artifacts: list[ArtifactState]
    report_url: Uri
    distribution: dict[str, Any]


class BuildDetail(BuildState):
    jobs: list[JobState]
    jobs_next_offset: ByteCount | None


class FinalizedBuild(BuildState):
    job: JobState
