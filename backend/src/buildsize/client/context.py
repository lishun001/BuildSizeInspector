"""CI 上下文在 prepare 时持久化，不以每次重试创建新构建。"""

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from buildsize.client.files import ClientError, create_json
from buildsize.client.git import snapshot
from buildsize.domain.contracts import (
    CI,
    BuildMeta,
    ContentPolicy,
    Contract,
    ProjectKey,
    RepoKey,
    Settings,
    Text,
    Toolchain,
)
from buildsize.domain.urls import validate_public_url
from buildsize.domain.validation import read_json
from buildsize.services.common import fingerprint

NAMESPACE = uuid.UUID("c7d4d891-e279-44c6-b16f-8a81abb6ebdc")


class RepoSpec(Contract):
    repo_key: RepoKey
    path: Text
    kind: Literal["main", "submodule", "art", "config", "other"]


class CIConfig(Contract):
    schema_version: Literal[1]
    metadata: dict[str, Any]
    repositories: list[RepoSpec] = Field(min_length=1, max_length=200)
    ci: dict[str, str] = Field(default_factory=dict)


def config(path: Path) -> CIConfig:
    value = CIConfig.model_validate(read_json(path, 1024 * 1024))
    allowed = {
        "application_id",
        "version_name",
        "version_code",
        "channel",
        "branch",
        "settings",
        "toolchain",
        "content_policy",
        "warnings",
        "build_completed_at",
    }
    if set(value.metadata) - allowed or set(value.ci) - set(CI.model_fields):
        raise ClientError("CI_CONFIG_INVALID", "配置含未允许字段")
    for group, model in (
        ("settings", Settings),
        ("toolchain", Toolchain),
        ("content_policy", ContentPolicy),
    ):
        data = value.metadata.get(group, {})
        if not isinstance(data, dict) or set(data) - set(model.model_fields):
            raise ClientError("CI_CONFIG_INVALID", "元数据配置组含未允许字段")
    if len({row.repo_key for row in value.repositories}) != len(value.repositories):
        raise ClientError("CI_CONFIG_INVALID", "仓库 key 不得重复")
    return value


def merge(field: str, *values: Any) -> Any:
    known = [value for value in values if value is not None and value != ""]
    if known and any(value != known[0] for value in known[1:]):
        raise ClientError("METADATA_CONFLICT", "受控配置、环境或 Unity 证据冲突：" + field, 5)
    return known[0] if known else None


def prepare(
    project: str, variant: str, output: Path, config_file: Path, workspace: Path
) -> dict[str, Any]:
    cfg = config(config_file)
    from pydantic import TypeAdapter

    TypeAdapter(ProjectKey).validate_python(project)
    ci_values = {}
    for field, variable in (
        ("instance_id", "BUILDSIZE_CI_INSTANCE_ID"),
        ("job_full_name", "JOB_NAME"),
        ("build_number", "BUILD_NUMBER"),
        ("build_url", "BUILD_URL"),
    ):
        ci_values[field] = merge("ci." + field, cfg.ci.get(field), os.getenv(variable))
    ci = CI.model_validate(ci_values)
    validate_public_url(ci.build_url)
    workspace = workspace.resolve()
    logical = [project, ci.instance_id, ci.job_full_name, ci.build_number, variant]
    config_sha = fingerprint({"config": cfg.model_dump(mode="json"), "workspace": str(workspace)})
    if output.exists():
        existing = read_json(output, 1024 * 1024)
        if existing.get("logical_key") != logical or existing.get("config_sha256") != config_sha:
            raise ClientError("CONTEXT_IDENTITY_CONFLICT", "已有上下文属于其他构建或配置", 5)
        return existing
    repos = []
    for spec in cfg.repositories:
        path = (workspace / spec.path).resolve()
        relative = os.path.relpath(path, workspace)
        repos.append(
            {"path": str(path), "snapshot": snapshot(path, spec.repo_key, spec.kind, relative)}
        )
    run_id = str(uuid.uuid5(NAMESPACE, fingerprint(logical)))
    directory = (output.parent / "reports" / run_id).resolve()
    value = {
        "schema_version": 1,
        "run_id": run_id,
        "project_key": project,
        "variant": variant,
        "ci": ci.model_dump(mode="json"),
        "logical_key": logical,
        "config_sha256": config_sha,
        "metadata_config": cfg.metadata,
        "workspace": str(workspace),
        "repositories": repos,
        "prepared_at": datetime.now(UTC).isoformat(),
        "report_directory": str(directory),
        "environment": {
            "BUILDSIZE_RUN_ID": run_id,
            "BUILDSIZE_REPORT_DIR": str(directory),
            "BUILDSIZE_EXPORT_MODE": "callback",
            "BUILDSIZE_IMPORTER_COLLECTION": "true",
        },
    }
    if not create_json(value, output):
        existing = read_json(output, 1024 * 1024)
        if existing.get("logical_key") != logical or existing.get("config_sha256") != config_sha:
            raise ClientError("CONTEXT_IDENTITY_CONFLICT", "上下文并发创建冲突", 5)
        return existing
    return value


def metadata(
    context: dict[str, Any],
    unity: dict[str, Any] | None,
    repos: list[dict[str, Any]],
    completed_at: str | None = None,
) -> BuildMeta:
    cfg = context["metadata_config"]
    unity = unity or {}
    if unity and (unity.get("run_id") != context["run_id"] or unity.get("platform") != "Android"):
        raise ClientError("CAPTURE_IDENTITY_CONFLICT", "Unity 设置快照身份/平台不匹配", 5)
    warnings = [*cfg.get("warnings", []), *unity.get("warnings", [])]
    if not unity:
        warnings.append("未采集 Unity 实际设置；元数据仅为声明或未知。")
    settings = {
        field: merge(
            "settings." + field,
            cfg.get("settings", {}).get(field),
            unity.get("settings", {}).get(field),
        )
        for field in Settings.model_fields
    }
    if settings["build_options"] is None:
        settings["build_options"] = []
        warnings.append("build_options 未采集，不代表实际没有选项。")
    content = {
        field: merge(
            "content_policy." + field,
            cfg.get("content_policy", {}).get(field),
            unity.get("content_policy", {}).get(field),
        )
        for field in ContentPolicy.model_fields
    }
    if content["scene_guids"] is None:
        content["scene_guids"] = []
        warnings.append("场景 GUID 未采集，空数组不代表没有场景。")
    toolchain = {field: cfg.get("toolchain", {}).get(field) for field in Toolchain.model_fields}
    toolchain["exporter_version"] = merge(
        "exporter_version", toolchain["exporter_version"], unity.get("exporter_version")
    )
    result = {
        "schema_version": 1,
        "run_id": context["run_id"],
        "project_key": context["project_key"],
        "variant": context["variant"],
        "platform": "Android",
        "artifact_type": "APK",
        "ci": context["ci"],
        "settings": settings,
        "repositories": repos,
        "toolchain": toolchain,
        "content_policy": content,
        "warnings": warnings,
    }
    for field in ("application_id", "version_name", "version_code"):
        result[field] = merge(field, cfg.get(field), unity.get(field))
    result["channel"] = cfg.get("channel")
    result["branch"] = merge("branch", cfg.get("branch"), os.getenv("BRANCH_NAME"))
    explicit_completion = merge(
        "build_completed_at",
        cfg.get("build_completed_at"),
        completed_at,
        os.getenv("BUILDSIZE_BUILD_COMPLETED_AT"),
    )
    result["build_completed_at"] = explicit_completion or unity.get("build_completed_at")
    if (
        explicit_completion
        and unity.get("build_completed_at")
        and explicit_completion != unity["build_completed_at"]
    ):
        warnings.append("采用显式最终 APK/CI 完成时间；Unity Player 结束时间保留在采集快照。")
    return BuildMeta.model_validate(result)
