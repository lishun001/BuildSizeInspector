"""打包机报告上报，只使用 Python 3 标准库与 Git。APK 只读摘要。"""

import argparse
import hashlib
import http.client
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

NAMESPACE = uuid.UUID("c7d4d891-e279-44c6-b16f-8a81abb6ebdc")
JSON_LIMIT = 64 * 1024**2
ROLES = {"unity-report.json": "unity_report", "asset-importers.json": "asset_importers"}


class HandoffError(Exception):
    pass


def encode(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def fingerprint(value):
    return hashlib.sha256(encode(value)).hexdigest()


def read_json(path, limit=JSON_LIMIT):
    if path.is_symlink() or not path.is_file():
        raise HandoffError("缺少本次构建所需的普通 JSON 文件")
    with path.open("rb") as source:
        content = source.read(limit + 1)
    if len(content) > limit:
        raise HandoffError("JSON 文件超过大小限制")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise HandoffError("JSON 根节点必须为对象")
    return value


def save_once(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent) as file:
        file.write(encode(value) + b"\n")
        file.flush()
        try:
            os.link(file.name, path)
        except FileExistsError:
            if read_json(path, 1024**2) != value:
                raise HandoffError("已有回执或上下文属于不同构建，未覆盖") from None


def merge(old, new):
    if old is not None and new is not None and old != new:
        raise HandoffError("CI 配置与本次构建实际值冲突")
    return new if new is not None else old


def git(path, *args):
    git_env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", *args],
        cwd=path,
        env={**git_env, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"},
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout


def snapshot(spec, workspace):
    path = workspace / spec["path"]
    return {
        "repo_key": spec["repo_key"],
        "kind": spec["kind"],
        "relative_path": os.path.relpath(path.resolve(), workspace),
        "revision": git(path, "rev-parse", "--verify", "HEAD").decode().strip(),
        "dirty": bool(git(path, "status", "--porcelain=v1", "-z", "--untracked-files=normal")),
    }


def prepare(args, directory):
    cfg = read_json(args.config, 1024**2)
    if (
        cfg.get("schema_version") != 1
        or set(cfg) - {"schema_version", "ci", "metadata", "repositories"}
        or not 1 <= len(cfg.get("repositories", [])) <= 200
    ):
        raise HandoffError("CI 配置版本或仓库列表无效")
    if set(cfg["metadata"]) - {
        "channel",
        "branch",
        "application_id",
        "version_name",
        "version_code",
        "build_completed_at",
        "settings",
        "content_policy",
        "toolchain",
        "warnings",
    }:
        raise HandoffError("CI 元数据含未知字段，请使用现有配置模板")
    if set(cfg.get("ci", {})) - {"instance_id", "job_full_name", "build_number", "build_url"}:
        raise HandoffError("CI 身份配置含未知字段")
    if any(set(spec) != {"repo_key", "kind", "path"} for spec in cfg["repositories"]):
        raise HandoffError("仓库配置应包含 repo_key、kind 和 path")
    ci = {
        field: merge(cfg.get("ci", {}).get(field), os.getenv(env) or None)
        for field, env in (
            ("instance_id", "BUILDSIZE_CI_INSTANCE_ID"),
            ("job_full_name", "JOB_NAME"),
            ("build_number", "BUILD_NUMBER"),
            ("build_url", "BUILD_URL"),
        )
    }
    if not all(isinstance(value, str) and value for value in ci.values()):
        raise HandoffError(
            "缺少 Jenkins 构建身份，请检查 JOB_NAME、BUILD_NUMBER、BUILD_URL 和 CI 实例"
        )
    logical = [
        args.project,
        ci["instance_id"],
        ci["job_full_name"],
        ci["build_number"],
        args.variant,
    ]
    run_id = str(uuid.uuid5(NAMESPACE, fingerprint(logical)))
    report_directory = directory / "reports" / run_id
    context = {
        "project_key": args.project,
        "variant": args.variant,
        "ci": ci,
        "run_id": run_id,
        "workspace": str(args.workspace.resolve()),
        "config": cfg,
        "report_directory": str(report_directory),
        "branch": os.getenv("BRANCH_NAME") or None,
    }
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "context.json"
    if path.exists():
        previous = read_json(path, 1024**2)
        if any(previous.get(key) != value for key, value in context.items()):
            raise HandoffError("已有上下文与本次构建不一致")
    else:
        workspace = Path(context["workspace"])
        context["repositories"] = [snapshot(spec, workspace) for spec in cfg["repositories"]]
        save_once(path, context)
    return {"run_id": run_id, "report_directory": str(report_directory)}


def file_digest(path, limit):
    if path.is_symlink() or not path.is_file():
        raise HandoffError("产物必须为普通文件")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        before = os.fstat(source.fileno())
        while chunk := source.read(1024**2):
            size += len(chunk)
            if size > limit:
                raise HandoffError("产物超过大小限制")
            digest.update(chunk)
        after = os.fstat(source.fileno())
    if size != before.st_size or (before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise HandoffError("计算摘要期间产物发生变化")
    return {"bytes": size, "sha256": digest.hexdigest()}


def capture(context):
    root = Path(context["report_directory"])
    receipt = read_json(root / "capture-complete.json", 1024**2)
    run_id = context["run_id"]
    if receipt.get("schema_version") != 1 or receipt.get("run_id") != run_id:
        raise HandoffError("Unity 采集回执与本次构建不匹配")
    allowed = {*ROLES, "unity-settings.json"}
    files = {}
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list) or not 2 <= len(artifacts) <= len(allowed):
        raise HandoffError("Unity 采集回执文件列表无效")
    for item in artifacts:
        name = item["filename"]
        if name not in allowed or name in files:
            raise HandoffError("采集回执包含未知或重复文件")
        path = root / name
        if file_digest(path, JSON_LIMIT) != {key: item[key] for key in ("bytes", "sha256")}:
            raise HandoffError("Unity 报告大小或 SHA-256 与采集回执不符")
        files[name] = path, item
    if not {"unity-report.json", "unity-settings.json"} <= files.keys():
        raise HandoffError("采集回执缺少本次 Unity 构建报告或设置快照")
    report = read_json(files["unity-report.json"][0])
    settings = read_json(files["unity-settings.json"][0])
    if (
        report.get("run_id") != run_id
        or settings.get("run_id") != run_id
        or settings.get("platform") != "Android"
        or report.get("build_result") in {"Failed", "Cancelled"}
    ):
        raise HandoffError("Unity 报告构建身份、平台或结果无效")
    if report.get("unity_version") != settings["settings"]["unity_version"]:
        raise HandoffError("Unity 报告版本与实际设置快照不一致")
    if "asset-importers.json" in files:
        importers = read_json(files["asset-importers.json"][0])
        if (
            importers.get("run_id") != run_id
            or importers.get("unity_version") != report["unity_version"]
        ):
            raise HandoffError("Importer 快照与本次 Unity 构建不一致")
    return settings, files


def build_meta(context, unity, completed_at):
    cfg = context["config"]["metadata"]
    workspace = Path(context["workspace"])
    repos = []
    specs = context["config"]["repositories"]
    if len(specs) != len(context["repositories"]):
        raise HandoffError("CI 仓库快照不完整")
    for index, spec in enumerate(specs):
        before = context["repositories"][index]
        actual = snapshot(spec, workspace)
        if actual["revision"] != before["revision"]:
            raise HandoffError("源码 HEAD 在 Unity 构建前后发生变化")
        repos.append({**before, "dirty": before["dirty"] or actual["dirty"]})

    def settings_group(name):
        configured = cfg.get(name, {})
        actual = unity[name]
        return {
            key: merge(configured.get(key), actual.get(key))
            for key in configured.keys() | actual.keys()
        }

    return {
        "schema_version": 1,
        "platform": "Android",
        "artifact_type": "APK",
        **{key: context[key] for key in ("run_id", "project_key", "variant", "ci")},
        **{
            key: merge(cfg.get(key), unity.get(key))
            for key in ("application_id", "version_name", "version_code")
        },
        "channel": cfg["channel"],
        "branch": merge(cfg.get("branch"), context["branch"]),
        "build_completed_at": merge(cfg.get("build_completed_at"), completed_at),
        "settings": settings_group("settings"),
        "content_policy": settings_group("content_policy"),
        "repositories": repos,
        "toolchain": {
            **{
                key: cfg.get("toolchain", {}).get(key)
                for key in (
                    "java_version",
                    "gradle_version",
                    "agp_version",
                    "ndk_version",
                    "android_build_tools_version",
                )
            },
            "exporter_version": merge(
                cfg.get("toolchain", {}).get("exporter_version"), unity["exporter_version"]
            ),
        },
        "warnings": [*cfg.get("warnings", []), *unity.get("warnings", [])],
    }


class UploadApi:
    def __init__(self):
        self.url = os.environ.get("BUILDSIZE_API_URL", "").rstrip("/")
        self.address = urlsplit(self.url)
        self.token = os.environ.get("BUILDSIZE_API_TOKEN", "")
        if (
            not self.address.hostname
            or self.address.username is not None
            or self.address.query
            or self.address.fragment
            or not re.fullmatch(r"[A-Za-z0-9_-]+", self.token)
            or (
                self.address.scheme != "https"
                and not (
                    self.address.scheme == "http"
                    and self.address.hostname in {"localhost", "127.0.0.1", "::1"}
                )
            )
        ):
            raise HandoffError("需要 BuildSize HTTPS 地址和项目令牌，HTTP 只允许本机验证")
        prefix = self.address.path
        self.prefix = prefix if prefix.endswith("/api/v1") else prefix + "/api/v1"

    def request(self, method, path, body, headers):
        kind = (
            http.client.HTTPSConnection
            if self.address.scheme == "https"
            else http.client.HTTPConnection
        )
        conn = kind(self.address.hostname, self.address.port, timeout=120)
        try:
            conn.request(
                method,
                self.prefix + path,
                body,
                {"Authorization": "Bearer " + self.token, **headers},
            )
            with conn.getresponse() as response:
                payload = response.read(4 * 1024**2 + 1)
                if response.status not in {200, 201, 202} or len(payload) > 4 * 1024**2:
                    raise HandoffError(f"BuildSize 请求未完成，HTTP {response.status}")
                return json.loads(payload) if payload else None
        finally:
            conn.close()


def submit(args, directory):
    context = read_json(directory / "context.json", 1024**2)
    download_url = None
    if args.apk_url_file is not None:
        if args.apk_url_file.is_symlink() or not args.apk_url_file.is_file():
            raise HandoffError("缺少本次发布的 APK 下载链接文件")
        with args.apk_url_file.open() as file:
            raw_url = file.read(8194)
        download_url = raw_url.strip()
        if not download_url or len(download_url) > 8192 or len(raw_url) > 8193:
            raise HandoffError("APK 下载链接文件无效")
    if (args.pgyer_build_key is not None or download_url is None) and not re.fullmatch(
        r"[A-Za-z0-9_-]{1,128}", args.pgyer_build_key or ""
    ):
        raise HandoffError("请提供本次发布确认后的蒲公英 buildKey")
    unity, captured = capture(context)
    with args.completed_at_file.open() as file:
        time_text = file.read(129)
    completed_at = time_text.strip()
    if not completed_at or len(time_text) > 128:
        raise HandoffError("最终 APK 完成时间文件无效")
    uploads = {}
    for name, role in ROLES.items():
        if name in captured:
            path, info = captured[name]
            uploads[role] = name, info["bytes"], info["sha256"], path
    artifacts = [{"role": "apk", "filename": "game.apk", **file_digest(args.apk, 2 * 1024**3)}]
    for role, (name, size, sha, _) in uploads.items():
        artifacts.append({"role": role, "filename": name, "bytes": size, "sha256": sha})
    handoff = directory / "handoff.json"
    identity = {
        "context_sha256": fingerprint(context),
        "completed_at": completed_at,
        "pgyer_build_key": args.pgyer_build_key,
        "artifacts": artifacts,
    }
    if handoff.exists():
        frozen = read_json(handoff)
        if frozen["identity"] != identity:
            raise HandoffError("重试时发现报告、APK 或发布身份变化")
    else:
        frozen = {"identity": identity, "build_meta": build_meta(context, unity, completed_at)}
        save_once(handoff, frozen)
    meta = encode(frozen["build_meta"])
    meta_sha = hashlib.sha256(meta).hexdigest()
    artifacts.append(
        {
            "role": "build_meta",
            "filename": "build-meta.json",
            "bytes": len(meta),
            "sha256": meta_sha,
        }
    )
    uploads["build_meta"] = "build-meta.json", len(meta), meta_sha, meta
    manifest = {
        "schema_version": 1,
        **{key: context[key] for key in ("run_id", "project_key", "variant", "ci")},
        "artifacts": sorted(artifacts, key=lambda item: item["role"]),
    }
    api = UploadApi()
    receipt = {
        "schema_version": 1,
        "api_url": api.url,
        "run_id": manifest["run_id"],
        "project_key": manifest["project_key"],
        "manifest_sha256": fingerprint(manifest),
        "pgyer_build_key": args.pgyer_build_key,
        "manifest": manifest,
    }
    existing = read_json(args.receipt, 1024**2) if args.receipt.exists() else None
    if existing is not None and any(existing.get(key) != value for key, value in receipt.items()):
        raise HandoffError("已有回执属于不同构建，未提交")
    initialized = api.request(
        "POST",
        f"/projects/{quote(manifest['project_key'], safe='')}/builds",
        encode(manifest),
        {"Content-Type": "application/json", "Idempotency-Key": manifest["run_id"]},
    )
    build_id = str(uuid.UUID(initialized["build_id"]))
    if initialized["run_id"] != manifest["run_id"] or (
        existing is not None and existing["build_id"] != build_id
    ):
        raise HandoffError("服务端构建身份与回执不一致")
    states = {item["role"]: item for item in initialized["artifacts"]}
    for item in artifacts:
        role = item["role"]
        state = states[role]
        if any(state[key] != item[key] for key in ("bytes", "sha256")):
            raise HandoffError("服务端产物摘要与本地清单不一致")
        if role == "apk" or state["state"] == "verified":
            continue
        body = uploads[role][3]
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(item["bytes"]),
            "X-Content-SHA256": item["sha256"],
        }
        path = f"/builds/{build_id}/artifacts/{role}"
        if isinstance(body, Path):
            with body.open("rb") as source:
                api.request("PUT", path, source, headers)
        else:
            api.request("PUT", path, body, headers)
    receipt.update(build_id=build_id, report_url=initialized.get("report_url"))
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    save_once(args.receipt, receipt)
    status = initialized
    if download_url:
        status = api.request(
            "POST",
            f"/builds/{build_id}/finalize-from-url",
            encode({"download_url": download_url}),
            {"Content-Type": "application/json"},
        )
    return {
        **receipt,
        "upload_state": status["upload_state"],
        "analysis_state": status["analysis_state"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    before = commands.add_parser("prepare")
    before.add_argument("--directory", type=Path, required=True)
    before.add_argument("--project", required=True)
    before.add_argument("--variant", required=True)
    before.add_argument("--config", type=Path, required=True)
    before.add_argument("--workspace", type=Path, default=Path.cwd())
    after = commands.add_parser("submit")
    after.add_argument("--directory", type=Path, required=True)
    after.add_argument("--apk", type=Path, required=True)
    after.add_argument("--pgyer-build-key")
    after.add_argument("--apk-url-file", type=Path, help="本次 APK 的 HTTPS 直链文件")
    after.add_argument("--completed-at-file", type=Path, required=True)
    after.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        action = prepare if args.command == "prepare" else submit
        print(encode(action(args, args.directory.resolve())).decode())
        return 0
    except HandoffError as error:
        print(str(error), file=sys.stderr)
    except KeyboardInterrupt:
        return 130
    except ssl.SSLError:
        print("BuildSize HTTPS 证书校验失败", file=sys.stderr)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        http.client.HTTPException,
        subprocess.SubprocessError,
    ):
        print("报告上报失败，请检查配置、文件、Git 与 BuildSize 网络", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
