"""冻结本次产物并按已验证角色恢复上传。提交重试不重采集/不刷新时间。"""

import hashlib
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from buildsize.client.context import metadata
from buildsize.client.files import ClientError, create_json, hash_file, write_json
from buildsize.client.git import changes, snapshot
from buildsize.client.transport import ApiClient
from buildsize.domain.contracts import Artifact, Contract, GitChanges, Submission
from buildsize.domain.distribution import PgyerResult
from buildsize.domain.urls import validate_public_url
from buildsize.domain.validation import parse_json, read_json, validate_identity
from buildsize.services.common import fingerprint

ROLES = {
    "unity-report.json": "unity_report",
    "asset-importers.json": "asset_importers",
    "packages-lock.json": "packages_lock",
}


def _pgyer_key(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ClientError("PGYER_BUILD_KEY_INVALID", "请提供蒲公英发布结果里的具体 buildKey")
    return value


def capture(
    context: dict[str, Any],
) -> tuple[dict[str, Path], dict[str, Any] | None, dict[str, tuple[int, str]]]:
    root = Path(context["report_directory"])
    receipt = root / "capture-complete.json"
    if not receipt.exists():
        return {}, None, {}
    value = read_json(receipt, 1024 * 1024)
    if (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("run_id") != context["run_id"]
    ):
        raise ClientError("CAPTURE_IDENTITY_CONFLICT", "采集回执不是本次 run_id", 5)
    artifacts = value.get("artifacts")
    if (
        not isinstance(artifacts, list)
        or len(artifacts) > 3
        or any(not isinstance(item, dict) for item in artifacts)
    ):
        raise ClientError("CAPTURE_MANIFEST_INVALID", "采集回执文件清单无效", 5)
    files: dict[str, Path] = {}
    parsed = {}
    expected: dict[str, tuple[int, str]] = {}
    allowed = {"unity-report.json", "asset-importers.json", "unity-settings.json"}
    for item in artifacts:
        filename = item.get("filename")
        if not isinstance(filename, str) or filename not in allowed or filename in files:
            raise ClientError("CAPTURE_MANIFEST_INVALID", "采集文件名、角色或重复项无效", 5)
        path = root / filename
        if path.is_symlink() or not path.is_file():
            raise ClientError("CAPTURE_FILE_INVALID", "回执产物必须是普通文件", 5)
        with path.open("rb") as source:
            raw = source.read(64 * 1024**2 + 1)
        if len(raw) > 64 * 1024**2:
            raise ClientError("CAPTURE_LIMIT", "采集文件超过大小预算")
        size, sha = len(raw), hashlib.sha256(raw).hexdigest()
        if (size, sha) != (item.get("bytes"), item.get("sha256")):
            raise ClientError("CAPTURE_HASH_MISMATCH", "采集文件与完成回执不一致", 5)
        data = parse_json(raw)
        if data.get("run_id") != context["run_id"]:
            raise ClientError("CAPTURE_IDENTITY_CONFLICT", "采集文件 run_id 不一致", 5)
        files[filename] = path
        parsed[filename] = data
        expected[filename] = size, sha
    if "unity-report.json" not in files or "unity-settings.json" not in files:
        raise ClientError("CAPTURE_INCOMPLETE", "完成回执缺少报告或实际设置", 5)
    report = parsed["unity-report.json"]
    if report.get("build_result") in {"Failed", "Cancelled"}:
        raise ClientError("GAME_BUILD_NOT_SUCCESSFUL", "Unity 构建失败或取消，拒绝提交旧 APK")
    return files, parsed["unity-settings.json"], expected


def validate_frozen(directory: Path, *, reports_only: bool = False) -> Submission:
    manifest = Submission.model_validate(read_json(directory / "submission.json", 1024 * 1024))
    frozen = read_json(directory / "collection.json", 1024 * 1024)
    if frozen.get("manifest_sha256") != manifest.manifest_sha256():
        raise ClientError("FROZEN_MANIFEST_CHANGED", "冻结清单被修改", 5)
    key = frozen.get("pgyer_build_key")
    if reports_only != (key is not None):
        raise ClientError("FROZEN_APK_MODE_MISMATCH", "提交方式与冻结目录的 APK 来源不一致", 5)
    if reports_only:
        _pgyer_key(key)
        if not any(item.role == "unity_report" for item in manifest.artifacts):
            raise ClientError("UNITY_REPORT_REQUIRED", "分步提交需要本次 Unity 构建报告")
    from buildsize.domain.contracts import AssetImporters, BuildMeta, UnityReport

    reports: list[UnityReport | AssetImporters | GitChanges] = []
    meta = None
    for item in manifest.artifacts:
        if reports_only and item.role == "apk":
            continue
        path = directory / item.filename
        if path.is_symlink() or not path.is_file():
            raise ClientError("FROZEN_FILE_INVALID", "冻结目录存在无效产物", 5)
        size, sha = hash_file(path, 2 * 1024**3 if item.role == "apk" else 64 * 1024**2)
        if (size, sha) != (item.bytes, item.sha256):
            raise ClientError("FROZEN_CONTENT_CHANGED", "冻结产物哈希或大小被修改", 5)
        if item.role == "build_meta":
            meta = BuildMeta.model_validate(read_json(path))
        elif item.role in {"unity_report", "asset_importers", "git_changes"}:
            data = read_json(path)
            if data.get("run_id") != manifest.run_id:
                raise ClientError("BUILD_IDENTITY_CONFLICT", "冻结报告 run_id 不一致", 5)
            models: dict[str, type[Contract]] = {
                "unity_report": UnityReport,
                "asset_importers": AssetImporters,
                "git_changes": GitChanges,
            }
            if data.get("schema_version") == 1:
                parsed = models[item.role].model_validate(data)
                if isinstance(parsed, UnityReport | AssetImporters | GitChanges):
                    reports.append(parsed)
    assert meta is not None
    validate_identity(manifest, meta, reports)
    return manifest


def _copy(source: Path, destination: Path, maximum: int) -> None:
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        before = os.fstat(incoming.fileno())
        count = 0
        while chunk := incoming.read(1024 * 1024):
            count += len(chunk)
            if count > maximum:
                raise ClientError("LOCAL_FILE_LIMIT", "产物超过本地大小预算")
            outgoing.write(chunk)
        outgoing.flush()
        os.fsync(outgoing.fileno())
        after = os.fstat(incoming.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ClientError("LOCAL_INPUT_CHANGED", "冻结过程中源产物发生变化", 5)


def collect(
    context_file: Path,
    apk: Path,
    output: Path,
    completed_at: str | None = None,
    timeout: float = 900,
    *,
    pgyer_build_key: str | None = None,
) -> dict[str, Any]:
    reports_only = pgyer_build_key is not None
    if reports_only:
        _pgyer_key(pgyer_build_key)
    context = read_json(context_file, 1024 * 1024)
    if output.exists():
        manifest = validate_frozen(output, reports_only=reports_only)
        frozen = read_json(output / "collection.json", 1024 * 1024)
        size, sha = hash_file(apk)
        expected = next(item for item in manifest.artifacts if item.role == "apk")
        if (
            manifest.run_id != context.get("run_id")
            or frozen.get("context_sha256") != fingerprint(context)
            or frozen.get("pgyer_build_key") != pgyer_build_key
            or (size, sha) != (expected.bytes, expected.sha256)
        ):
            raise ClientError("FROZEN_IDENTITY_CONFLICT", "已冻结目录不属于本次上下文或 APK", 5)
        return {
            "run_id": manifest.run_id,
            "directory": str(output.resolve()),
            "state": "already_frozen",
        }
    captured, unity, expected_capture = capture(context)
    if reports_only and "unity-report.json" not in captured:
        raise ClientError("UNITY_REPORT_REQUIRED", "分步提交需要本次 Unity 构建报告和采集回执")
    repos = []
    for prepared in context["repositories"]:
        old = prepared["snapshot"]
        actual = snapshot(
            Path(prepared["path"]), old["repo_key"], old["kind"], old["relative_path"]
        )
        if actual["revision"] != old["revision"]:
            raise ClientError(
                "REPOSITORY_CHANGED_DURING_BUILD",
                "源码 HEAD 在 prepare 后变化，拒绝冒充实际构建版本",
                5,
            )
        actual["dirty"] = old["dirty"] or actual["dirty"]
        repos.append(actual)
    meta = metadata(context, unity, repos, completed_at)
    candidate: dict[str, Any] | None = None
    if os.getenv("BUILDSIZE_API_URL") or os.getenv("BUILDSIZE_API_TOKEN"):
        client = ApiClient(timeout=timeout)
        settings = meta.settings
        query: dict[str, Any] = {
            "application_id": meta.application_id,
            "channel": meta.channel,
            "variant": meta.variant,
            "ci_instance_id": meta.ci.instance_id,
            "ci_job_full_name": meta.ci.job_full_name,
            "build_completed_at": meta.build_completed_at,
        }
        for key, value in {
            "branch": meta.branch,
            "development_build": settings.development_build,
            "scripting_backend": settings.scripting_backend,
            "architectures": settings.architectures,
        }.items():
            if value is not None:
                query[key] = str(value).lower() if isinstance(value, bool) else value
        _, candidate = client.request(
            "GET", f"/projects/{quote(meta.project_key, safe='')}/baseline-candidate", query=query
        )
        if candidate.get("status") not in {"CANDIDATE", "NO_BASELINE"}:
            raise ClientError("BASELINE_RESPONSE_INVALID", "候选基线响应无效", 5)
        if candidate.get("repositories_truncated"):
            raise ClientError(
                "BASELINE_REPOSITORY_LIMIT", "候选仓库范围超过客户端预算，未伪造完整 Git 证据"
            )
    base_repos = {
        row["repo_key"]: row["revision"] for row in (candidate or {}).get("repositories", [])
    }
    ranges = []
    warnings = []
    if candidate is None:
        warnings.append("API 未配置，未查询基线；failed 范围不是没有修改。")
    for repo, prepared in zip(repos, context["repositories"], strict=True):
        ranges.append(
            changes(
                Path(prepared["path"]),
                repo,
                base_repos.get(repo["repo_key"]),
                no_baseline=candidate is not None and candidate.get("status") == "NO_BASELINE",
            )
        )
    git_report = GitChanges.model_validate(
        {
            "schema_version": 1,
            "run_id": context["run_id"],
            "requested_base_build_id": (candidate or {}).get("base_build_id"),
            "repositories": ranges,
            "warnings": warnings,
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".buildsize-collect-", dir=output.parent))
    try:
        if not reports_only:
            _copy(apk, staging / "game.apk", 2 * 1024**3)
        payloads: list[dict[str, Any]] = [{"role": "apk", "filename": "game.apk"}]
        write_json(meta.model_dump(mode="json"), staging / "build-meta.json")
        write_json(git_report.model_dump(mode="json"), staging / "git-changes.json")
        payloads += [
            {"role": "build_meta", "filename": "build-meta.json"},
            {"role": "git_changes", "filename": "git-changes.json"},
        ]
        for filename, path in captured.items():
            role = ROLES.get(filename)
            if role:
                _copy(path, staging / filename, 64 * 1024**2)
                if hash_file(staging / filename, 64 * 1024**2) != expected_capture[filename]:
                    raise ClientError("CAPTURE_CHANGED", "采集文件在冻结期间被替换", 5)
                payloads.append({"role": role, "filename": filename})
        if "unity-settings.json" in captured:
            _copy(captured["unity-settings.json"], staging / "unity-settings.json", 64 * 1024**2)
            if (
                hash_file(staging / "unity-settings.json", 64 * 1024**2)
                != expected_capture["unity-settings.json"]
            ):
                raise ClientError("CAPTURE_CHANGED", "实际设置在冻结期间被替换", 5)
        lock = Path(context["workspace"]) / "Packages/packages-lock.json"
        if lock.is_file():
            _copy(lock, staging / "packages-lock.json", 64 * 1024**2)
            payloads.append({"role": "packages_lock", "filename": "packages-lock.json"})
        for row in payloads:
            source = apk if reports_only and row["role"] == "apk" else staging / row["filename"]
            row["bytes"], row["sha256"] = hash_file(source)
        manifest = Submission.model_validate(
            {
                "schema_version": 1,
                "run_id": context["run_id"],
                "project_key": context["project_key"],
                "variant": context["variant"],
                "ci": context["ci"],
                "artifacts": payloads,
            }
        )
        write_json(manifest.model_dump(mode="json"), staging / "submission.json")
        write_json(
            {
                "schema_version": 1,
                "context_sha256": fingerprint(context),
                "manifest_sha256": manifest.manifest_sha256(),
                **({"pgyer_build_key": pgyer_build_key} if reports_only else {}),
            },
            staging / "collection.json",
        )
        validate_frozen(staging, reports_only=reports_only)
        os.rename(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {"run_id": context["run_id"], "directory": str(output.resolve()), "state": "frozen"}


def _receipt(path: Path, client: ApiClient) -> dict[str, Any]:
    if not path.is_file():
        raise ClientError("RECEIPT_MISSING", "缺少本次提交回执，未查询或关联最近构建")
    value = read_json(path, 1024 * 1024)
    if value.get("api_url") != client.url:
        raise ClientError("RECEIPT_API_CONFLICT", "回执 API 与当前环境不一致，未发送凭据", 3)
    try:
        for field in ("build_id", "run_id"):
            raw = value[field]
            if not isinstance(raw, str):
                raise ValueError
            uuid.UUID(raw)
    except (KeyError, ValueError, TypeError):
        raise ClientError("RECEIPT_INVALID", "回执身份无效", 5) from None
    return value


def _initialize(client: ApiClient, manifest: Submission) -> dict[str, Any]:
    _, initialized = client.request(
        "POST",
        f"/projects/{quote(manifest.project_key, safe='')}/builds",
        body=manifest.model_dump(mode="json"),
        headers={"Idempotency-Key": manifest.run_id},
    )
    return initialized


def _verify_remote(
    remote: dict[str, Any], manifest: Submission, expected_id: str | None = None
) -> tuple[str, dict[str, dict[str, Any]]]:
    try:
        build_id = str(uuid.UUID(str(remote["build_id"])))
        if str(uuid.UUID(str(remote["run_id"]))) != manifest.run_id or (
            expected_id is not None and expected_id != build_id
        ):
            raise ValueError
    except (KeyError, ValueError, TypeError):
        raise ClientError("BUILD_RESPONSE_INVALID", "服务端构建身份与清单不匹配", 5) from None
    rows = remote.get("artifacts", [])
    expected_roles = {item.role for item in manifest.artifacts}
    if (
        not isinstance(rows, list)
        or any(not isinstance(row, dict) for row in rows)
        or any(not isinstance(row.get("role"), str) for row in rows)
        or {row.get("role") for row in rows} != expected_roles
        or len(rows) != len(expected_roles)
    ):
        raise ClientError("BUILD_RESPONSE_INVALID", "服务端角色清单不一致", 5)
    states = {row["role"]: row for row in rows}
    for item in manifest.artifacts:
        state = states[item.role]
        if (state.get("bytes"), state.get("sha256")) != (item.bytes, item.sha256):
            raise ClientError("ARTIFACT_DECLARATION_CONFLICT", "服务端产物与冻结清单不符", 5)
    return build_id, states


def _upload(client: ApiClient, build_id: str, item: Artifact, path: Path) -> None:
    client.request(
        "PUT",
        f"/builds/{build_id}/artifacts/{item.role}",
        file=path,
        headers={"Content-Length": str(item.bytes), "X-Content-SHA256": item.sha256},
    )


def _finalize(client: ApiClient, build_id: str) -> dict[str, Any]:
    status, finalized = client.request("POST", f"/builds/{build_id}/finalize", body={})
    if status != 202 or finalized.get("build_id") != build_id:
        raise ClientError("FINALIZE_RESPONSE_INVALID", "封存没有返回有效的 202", 5)
    return finalized


def submit(
    directory: Path, receipt: Path, timeout: float = 900, *, reports_only: bool = False
) -> dict[str, Any]:
    client = ApiClient(timeout=timeout)
    manifest = validate_frozen(directory, reports_only=reports_only)
    extra: dict[str, Any] = {}
    if reports_only:
        frozen = read_json(directory / "collection.json", 1024 * 1024)
        extra = {
            "pgyer_build_key": _pgyer_key(frozen.get("pgyer_build_key")),
            "manifest": manifest.model_dump(mode="json"),
        }
    existing = _receipt(receipt, client) if receipt.exists() else None
    if existing is not None and (
        existing.get("run_id") != manifest.run_id
        or existing.get("manifest_sha256") != manifest.manifest_sha256()
        or existing.get("pgyer_build_key") != extra.get("pgyer_build_key")
    ):
        raise ClientError("RECEIPT_IDENTITY_CONFLICT", "已有回执属于其他冻结提交", 5)
    initialized = _initialize(client, manifest)
    build_id, states = _verify_remote(
        initialized, manifest, existing["build_id"] if existing else None
    )
    for item in manifest.artifacts:
        if reports_only and item.role == "apk":
            continue
        if states[item.role].get("state") != "verified":
            _upload(client, build_id, item, directory / item.filename)
    result = initialized if reports_only else _finalize(client, build_id)
    report_url = result.get("report_url")
    if report_url:
        validate_public_url(report_url)
    value = {
        "schema_version": 1,
        "api_url": client.url,
        "build_id": build_id,
        "run_id": manifest.run_id,
        "project_key": manifest.project_key,
        "manifest_sha256": manifest.manifest_sha256(),
        "report_url": report_url,
        **extra,
    }
    if not create_json(value, receipt):
        current = _receipt(receipt, client)
        if current != value:
            raise ClientError("RECEIPT_IDENTITY_CONFLICT", "并发回执冲突，未覆盖", 5)
    return {
        **value,
        "upload_state": result.get("upload_state"),
        "analysis_state": result.get("analysis_state"),
    }


def complete_apk(receipt: Path, apk: Path, timeout: float = 900) -> dict[str, Any]:
    client = ApiClient(timeout=timeout)
    value = _receipt(receipt, client)
    _pgyer_key(value.get("pgyer_build_key"))
    manifest = Submission.model_validate(value.get("manifest"))
    if (
        manifest.run_id != value["run_id"]
        or manifest.project_key != value.get("project_key")
        or manifest.manifest_sha256() != value.get("manifest_sha256")
    ):
        raise ClientError("RECEIPT_IDENTITY_CONFLICT", "回执清单身份或摘要不一致", 5)
    expected = next(item for item in manifest.artifacts if item.role == "apk")
    if hash_file(apk) != (expected.bytes, expected.sha256):
        raise ClientError("PGYER_APK_MISMATCH", "下载的 APK 与原始最终包的长度或 SHA-256 不符", 5)

    # 先确认回执指向已存在的构建，再续期。无效回执不能在补传时新建构建。
    _, current = client.request("GET", f"/builds/{value['build_id']}")
    build_id, states = _verify_remote(current, manifest, value["build_id"])
    if not all(
        state.get("state") == "verified" and not state.get("expired_at")
        for role, state in states.items()
        if role != "apk"
    ):
        raise ClientError("REPORTS_NOT_AVAILABLE", "报告还未上传齐全或已经过期，不能补交 APK", 5)
    if current.get("upload_state") != "SEALED":
        _, states = _verify_remote(_initialize(client, manifest), manifest, build_id)
    apk_state = states["apk"]
    if apk_state.get("expired_at"):
        raise ClientError("APK_EXPIRED", "已封存的 APK 已按保留策略过期，不能重新补交", 5)
    if apk_state.get("state") != "verified":
        _upload(client, build_id, expected, apk)
    finalized = _finalize(client, build_id)
    return {
        **value,
        "upload_state": finalized.get("upload_state"),
        "analysis_state": finalized.get("analysis_state"),
    }


def status(receipt: Path, timeout: float = 900) -> dict[str, Any]:
    client = ApiClient(timeout=timeout)
    value = _receipt(receipt, client)
    _, result = client.request("GET", f"/builds/{value['build_id']}")
    return result


def link_pgyer(receipt: Path, result_file: Path, timeout: float = 900) -> dict[str, Any]:
    client = ApiClient(timeout=timeout)
    value = _receipt(receipt, client)
    raw = read_json(result_file, 1024 * 1024)
    # 仅白名单字段，绝不把传输响应的 token/signature/apiKey 发给平台。
    payload = {key: raw.get(key) for key in PgyerResult.model_fields}
    result = PgyerResult.model_validate(payload)
    pgyer_key = value.get("pgyer_build_key")
    if pgyer_key and result.publish_state == "published" and result.build_key != pgyer_key:
        raise ClientError("PGYER_BUILD_KEY_MISMATCH", "发布结果的 buildKey 与上传回执不符", 5)
    _, linked = client.request(
        "PUT",
        f"/builds/{value['build_id']}/distribution/pgyer",
        body=result.model_dump(mode="json"),
    )
    return PgyerResult.model_validate(linked).model_dump(mode="json")
