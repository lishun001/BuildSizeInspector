"""v1 合同模型。可空字段仍必须提供，未知字段一律拒绝。"""

import hashlib
import json
import re
from typing import Annotated, Any, Literal, Self

from jsonschema import FormatChecker
from jsonschema.exceptions import FormatError
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

SAFE_INTEGER = 9_007_199_254_740_991
ByteCount = Annotated[int, Field(strict=True, ge=0, le=SAFE_INTEGER)]
ByteDelta = Annotated[int, Field(strict=True, ge=-SAFE_INTEGER, le=SAFE_INTEGER)]
Text = Annotated[str, Field(min_length=1)]
RepoKey = Annotated[str, Field(min_length=1, max_length=128)]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Guid = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
Revision = Annotated[str, Field(pattern=r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")]
ProjectKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,62}$")]


def _format(value: str, format_name: str) -> str:
    try:
        FormatChecker().check(value, format_name)
    except FormatError as exc:
        raise ValueError(f"不符合 {format_name} 格式") from exc
    return value


RunId = Annotated[str, AfterValidator(lambda value: _format(value, "uuid"))]
Timestamp = Annotated[str, AfterValidator(lambda value: _format(value, "date-time"))]
Uri = Annotated[str, AfterValidator(lambda value: _format(value, "uri"))]
Role = Literal[
    "apk", "build_meta", "unity_report", "asset_importers", "git_changes", "packages_lock"
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @model_validator(mode="before")
    @classmethod
    def literal_types(cls, value: Any) -> Any:
        if isinstance(value, dict):
            if "schema_version" in value and type(value["schema_version"]) is not int:
                raise ValueError("schema_version 必须为整数")
            if "additive" in value and type(value["additive"]) is not bool:
                raise ValueError("additive 必须为布尔值")
        return value


def unique(values: list[Any], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} 不得重复")


class CI(Contract):
    instance_id: Text
    job_full_name: Text
    build_number: Text
    build_url: Uri


class Artifact(Contract):
    role: Role
    filename: Text
    bytes: ByteCount
    sha256: Sha256

    @model_validator(mode="after")
    def safe_filename(self) -> Self:
        if self.filename in {".", ".."} or re.search(r"[/\\:\x00-\x1f]", self.filename):
            raise ValueError("产物文件名不得包含路径、盘符或控制字符")
        return self


class Submission(Contract):
    schema_version: Literal[1]
    run_id: RunId
    project_key: ProjectKey
    variant: Text
    ci: CI
    artifacts: Annotated[list[Artifact], Field(min_length=2, max_length=6)]

    @model_validator(mode="after")
    def roles_and_limits(self) -> Self:
        roles = [item.role for item in self.artifacts]
        unique(roles, "artifact role")
        unique([item.filename for item in self.artifacts], "artifact filename")
        if not {"apk", "build_meta"} <= set(roles):
            raise ValueError("必须包含一份 APK 和一份 build_meta")
        for item in self.artifacts:
            limit = 2 * 1024**3 if item.role == "apk" else 64 * 1024**2
            if item.bytes > limit:
                raise ValueError(f"{item.role} 超过单文件限制")
        if sum(item.bytes for item in self.artifacts) > 3 * 1024**3:
            raise ValueError("单构建文件总量超过限制")
        return self

    def canonical_bytes(self) -> bytes:
        value = self.model_dump(mode="json")
        value["artifacts"] = sorted(value["artifacts"], key=lambda item: item["role"])
        return json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")

    def manifest_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def logical_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.project_key,
            self.ci.instance_id,
            self.ci.job_full_name,
            self.ci.build_number,
            self.variant,
        )


class Settings(Contract):
    unity_version: Text | None
    scripting_backend: Literal["IL2CPP", "Mono"] | None
    architectures: (
        Annotated[list[Literal["arm64-v8a", "armeabi-v7a", "x86", "x86_64"]], Field(min_length=1)]
        | None
    )
    development_build: bool | None
    managed_stripping_level: str | None
    strip_engine_code: bool | None
    player_compression: str | None
    il2cpp_code_generation: str | None
    min_sdk: ByteCount | None
    target_sdk_requested: str | None
    build_options: list[Text]
    export_mode: Literal["direct_apk", "export_gradle"]

    @model_validator(mode="after")
    def sets(self) -> Self:
        unique(self.build_options, "build_options")
        unique(self.architectures or [], "architectures")
        return self


class Repository(Contract):
    repo_key: RepoKey
    revision: Revision
    dirty: bool
    relative_path: Text
    kind: Literal["main", "submodule", "art", "config", "other"]


class Toolchain(Contract):
    java_version: str | None
    gradle_version: str | None
    agp_version: str | None
    ndk_version: str | None
    android_build_tools_version: str | None
    exporter_version: str | None


class ContentPolicy(Contract):
    builtin_policy_hash: Sha256 | None
    scene_guids: list[Guid]

    @model_validator(mode="after")
    def scene_set(self) -> Self:
        unique(self.scene_guids, "scene_guids")
        return self


class BuildMeta(Contract):
    schema_version: Literal[1]
    run_id: RunId
    project_key: ProjectKey
    variant: Text
    platform: Literal["Android"]
    artifact_type: Literal["APK"]
    application_id: Text
    version_name: Text
    version_code: ByteCount
    channel: Text
    branch: str | None
    build_completed_at: Timestamp
    ci: CI
    settings: Settings
    repositories: Annotated[list[Repository], Field(min_length=1, max_length=200)]
    toolchain: Toolchain
    content_policy: ContentPolicy
    warnings: list[str]

    @model_validator(mode="after")
    def repository_keys(self) -> Self:
        unique([item.repo_key for item in self.repositories], "repo_key")
        return self


class UnityRecord(Contract):
    record_index: ByteCount
    source_guid: Guid | None
    source_path: str | None
    asset_type: Text
    packed_bytes: ByteCount


class UnityContainer(Contract):
    container_index: ByteCount
    short_path: Text
    overhead_bytes: ByteCount
    entries: list[UnityRecord]

    @model_validator(mode="after")
    def record_indices(self) -> Self:
        unique([item.record_index for item in self.entries], "record_index")
        return self


class OutputFile(Contract):
    path: Text
    role: Text
    bytes: ByteCount


class UnityReport(Contract):
    schema_version: Literal[1]
    run_id: RunId
    exporter_version: Text
    unity_version: Text
    captured_at: Timestamp
    capture_mode: Literal["buildplayer_return", "postprocess_callback"]
    build_result: Literal["Succeeded", "Failed", "Cancelled", "Unknown"]
    detailed_report_requested: bool | None
    unity_reported_total_bytes: ByteCount | None
    containers: list[UnityContainer]
    output_files: list[OutputFile]
    warnings: list[str]

    @model_validator(mode="before")
    @classmethod
    def resource_limits(cls, value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("containers"), list):
            containers = value["containers"]
            if (
                len(containers) > 10000
                or sum(
                    len(row.get("entries", []))
                    for row in containers
                    if isinstance(row, dict) and isinstance(row.get("entries", []), list)
                )
                > 100000
            ):
                raise ValueError("Unity 报告超过容器/记录预算")
        return value

    @model_validator(mode="after")
    def container_indices(self) -> Self:
        unique([item.container_index for item in self.containers], "container_index")
        return self


class Coverage(Contract):
    requested_count: ByteCount
    collected_count: ByteCount
    failed_count: ByteCount


class Importer(Contract):
    source_guid: Guid
    source_path: Text
    importer_type: Text
    dependency_hash: str | None
    source_sha256: Sha256 | None
    meta_sha256: Sha256 | None
    settings: dict[str, Any]
    unsupported_fields: list[Text]


class AssetImporters(Contract):
    schema_version: Literal[1]
    run_id: RunId
    unity_version: Text
    captured_at: Timestamp
    coverage: Coverage
    assets: list[Importer]
    warnings: list[str]

    @model_validator(mode="before")
    @classmethod
    def resource_limits(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and isinstance(value.get("assets"), list)
            and len(value["assets"]) > 50000
        ):
            raise ValueError("Importer 快照超过记录预算")
        return value

    @model_validator(mode="after")
    def importer_identity(self) -> Self:
        unique([item.source_guid for item in self.assets], "importer GUID")
        if any(item.source_guid == "0" * 32 for item in self.assets):
            raise ValueError("全零 GUID 不能作为 Importer 主键")
        if self.coverage.collected_count != len(self.assets):
            raise ValueError("collected_count 与 assets 数量不符")
        if (
            self.coverage.collected_count + self.coverage.failed_count
            > self.coverage.requested_count
        ):
            raise ValueError("Importer 覆盖数量不一致")
        return self


class GitChange(Contract):
    status: Literal["A", "M", "D", "R", "C", "T", "U", "X"]
    old_path: str | None
    new_path: str | None
    old_blob: Revision | None
    new_blob: Revision | None
    source_guid: Guid | None


class GitRepository(Contract):
    repo_key: RepoKey
    base_revision: Revision | None
    head_revision: Revision
    range_status: Literal["complete", "no_baseline", "missing_base", "unrelated_history", "failed"]
    is_ancestor: bool | None
    changes: Annotated[list[GitChange], Field(max_length=100000)]
    commit_ids: Annotated[list[Revision], Field(max_length=10000)]


class GitChanges(Contract):
    schema_version: Literal[1]
    run_id: RunId
    requested_base_build_id: str | None
    repositories: Annotated[list[GitRepository], Field(max_length=200)]
    warnings: list[str]

    @model_validator(mode="after")
    def repository_keys(self) -> Self:
        unique([item.repo_key for item in self.repositories], "Git repo_key")
        return self


class Subject(Contract):
    kind: Literal["apk_entry", "unity_asset", "config", "build", "container"]
    key: Text


class Evidence(Contract):
    artifact_role: Role | Literal["derived_analysis"]
    artifact_sha256: Sha256 | None
    locator: Text
    summary: Text

    @model_validator(mode="after")
    def immutable_locator(self) -> Self:
        if self.artifact_role != "derived_analysis" and self.artifact_sha256 is None:
            raise ValueError("原始证据必须携带产物 SHA-256")
        if self.artifact_role == "derived_analysis" and not self.locator.startswith(
            "analysis_revision:"
        ):
            raise ValueError("派生证据必须定位到 analysis_revision")
        return self


class Finding(Contract):
    schema_version: Literal[1]
    rule_id: Text
    rule_version: Text
    confidence: Literal["observed", "correlated", "hypothesis"]
    severity: Literal["info", "warning", "error"]
    subject: Subject
    title: Text
    explanation: Text
    metric: Literal["zip_compressed_bytes", "unity_packed_bytes", "apk_overhead_bytes", "none"]
    delta_bytes: ByteDelta | None
    additive: Literal[False]
    evidence: Annotated[list[Evidence], Field(min_length=1)]
    suggested_checks: list[Text]
    review_state: Literal["unreviewed", "confirmed", "rejected"]
