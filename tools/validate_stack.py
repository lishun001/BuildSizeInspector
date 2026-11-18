"""仅操作本次随机 Compose 项目：部署、HTTP、重启、离线备份与空卷恢复。"""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from buildsize.client.transport import ApiClient

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args()
    project = "bsi-stack-test-" + uuid.uuid4().hex
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    password = secrets.token_urlsafe(32)
    environment = {
        **os.environ,
        "POSTGRES_PASSWORD": password,
        "DATABASE_URL": f"postgresql+psycopg://buildsize:{password}@postgres:5432/buildsize",
        "SESSION_SECRET": secrets.token_urlsafe(48),
        "TOKEN_HASH_PEPPER": secrets.token_urlsafe(48),
        "PUBLIC_BASE_URL": origin,
        "APP_ENV": "development",
        "BUILDSIZE_WEB_PORT": str(port),
        "TRUSTED_LINK_HOSTS": "jenkins.example.invalid",
        "BUILDSIZE_API_IMAGE": "buildsize-api:local",
        "BUILDSIZE_WEB_IMAGE": "buildsize-web:local",
        "UPLOAD_MAX_BYTES": "2147483648",
        "UPLOAD_TIMEOUT_SECONDS": "900",
        "ANALYSIS_TIMEOUT_SECONDS": "1200",
    }
    compose = [
        "docker",
        "compose",
        "-p",
        project,
        "--env-file",
        str(ROOT / ".env.example"),
        "-f",
        str(ROOT / "deploy/compose.yaml"),
    ]
    directory = ROOT / ".buildsize/stack-validation" / project
    directory.mkdir(parents=True)

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            args,
            env=environment,
            check=True,
            capture_output=True,
            timeout=900,
            **kwargs,
        )

    def ready(client: ApiClient) -> None:
        end = time.monotonic() + 60
        while time.monotonic() < end:
            try:
                client.request("GET", "/health/ready")
                return
            except Exception:
                time.sleep(0.3)
        raise ValueError("ready timeout")

    try:
        run(compose + ["up", "-d", "--wait"])
        script = """import sys,json
from sqlalchemy.orm import Session
from buildsize.config import Config
from buildsize.db.session import make_engine
from buildsize.auth.service import create_user,issue_token
from buildsize.db.models import Project,Membership
v=json.load(sys.stdin);c=Config.from_env()
with Session(make_engine(c)) as db:
 p=Project(key='stack-game',name='隔离栈测试');db.add(p);db.flush()
 u=create_user(db,'stack-admin',v['password']);db.add(Membership(user_id=u.id,project_id=p.id,role='admin'))
 _,t=issue_token(db,c,p,['builds:read','builds:submit'],3600);db.commit();print(json.dumps({'token':t}))
"""
        login_password = secrets.token_urlsafe(32)
        secret = run(
            compose + ["exec", "-T", "api", "python", "-c", script],
            input=json.dumps({"password": login_password}).encode(),
        )
        token = json.loads(secret.stdout)["token"]
        client = ApiClient(origin, token, timeout=180)
        ready(client)
        from urllib.request import urlopen

        with urlopen(origin + "/builds/not-a-real-id") as response:
            assert b"root" in response.read() and response.status == 200
            assert "script-src 'self'" in response.headers["Content-Security-Policy"]
        ids = []
        initial = None
        for number in (1, 2):
            metadata = json.loads((ROOT / "examples/build-meta.json").read_text())
            metadata.update(
                run_id=str(uuid.uuid4()),
                project_key="stack-game",
                build_completed_at=(datetime.now(UTC) - timedelta(minutes=3 - number)).isoformat(),
            )
            metadata["ci"]["build_number"] = str(number)
            run_dir = directory / str(number)
            run_dir.mkdir()
            apk = run_dir / "game.apk"
            with zipfile.ZipFile(apk, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("assets/中文 空格.bin", b"a" * number)
            meta = run_dir / "build-meta.json"
            meta.write_text(json.dumps(metadata))
            manifest = {
                "schema_version": 1,
                "run_id": metadata["run_id"],
                "project_key": "stack-game",
                "variant": metadata["variant"],
                "ci": metadata["ci"],
                "artifacts": [
                    {
                        "role": role,
                        "filename": path.name,
                        "bytes": path.stat().st_size,
                        "sha256": hashlib.file_digest(path.open("rb"), "sha256").hexdigest(),
                    }
                    for role, path in (("apk", apk), ("build_meta", meta))
                ],
            }
            _, created = client.request(
                "POST",
                "/projects/stack-game/builds",
                body=manifest,
                headers={"Idempotency-Key": manifest["run_id"]},
            )
            bid = created["build_id"]
            ids.append(bid)
            for item in manifest["artifacts"]:
                client.request(
                    "PUT",
                    f"/builds/{bid}/artifacts/{item['role']}",
                    file=run_dir / item["filename"],
                    headers={"X-Content-SHA256": item["sha256"]},
                )
            status, _ = client.request("POST", f"/builds/{bid}/finalize", body={})
            assert status == 202
            end = time.monotonic() + 45
            while True:
                _, value = client.request("GET", f"/builds/{bid}")
                assert value["analysis_state"] != "FAILED"
                if value["analysis_state"] in {"READY", "PARTIAL"}:
                    break
                assert time.monotonic() < end, "worker timeout"
                time.sleep(0.2)
            initial = value
        assert initial is not None
        _, comparison = client.request("GET", f"/comparisons/{initial['result']['comparison_id']}")
        assert (
            comparison["base_build_id"] == ids[0] and comparison["result"]["delta_apk_bytes"] == 1
        )
        run(compose + ["restart", "postgres", "redis", "api", "worker", "scheduler", "web"])
        client = ApiClient(origin, token, timeout=180)
        ready(client)
        assert (
            client.request("GET", f"/builds/{ids[-1]}")[1]["analysis_revision"]
            == initial["analysis_revision"]
        )
        run(compose + ["stop", "web", "api", "worker", "scheduler"])
        backup = directory / "backup"
        operation = [sys.executable, str(ROOT / "tools/stack_backup.py")]
        options = [
            "--project",
            project,
            "--env-file",
            str(ROOT / ".env.example"),
            "--directory",
            str(backup),
        ]
        run(operation + ["backup", *options])
        run(compose + ["down", "--volumes", "--remove-orphans"])
        run(operation + ["restore", *options])
        run(compose + ["up", "-d", "--wait"])
        client = ApiClient(origin, token, timeout=180)
        ready(client)
        assert (
            client.request("GET", f"/builds/{ids[-1]}")[1]["analysis_revision"]
            == initial["analysis_revision"]
        )
        # 恢复后实际登录、下载和重新分析，不只检查文件存在。
        import http.cookiejar
        from urllib.request import HTTPCookieProcessor, Request, build_opener

        browser = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with browser.open(
            Request(
                origin + "/api/v1/auth/login",
                data=json.dumps({"username": "stack-admin", "password": login_password}).encode(),
                headers={"Content-Type": "application/json", "Origin": origin},
            )
        ) as response:
            csrf = json.load(response)["csrf_token"]
        with browser.open(origin + f"/api/v1/builds/{ids[-1]}/artifacts/apk/download") as response:
            assert hashlib.sha256(response.read()).hexdigest() == manifest["artifacts"][0]["sha256"]
        with browser.open(
            Request(
                origin + f"/api/v1/builds/{ids[-1]}/reanalyze",
                data=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Origin": origin,
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": str(uuid.uuid4()),
                },
            )
        ) as response:
            assert response.status == 202
        end = time.monotonic() + 45
        while True:
            value = client.request("GET", f"/builds/{ids[-1]}")[1]
            if value["analysis_revision"] != initial["analysis_revision"]:
                break
            assert value["analysis_state"] != "FAILED" and time.monotonic() < end
            time.sleep(0.2)
        executable = Path(
            os.getenv(
                "BUILDSIZE_BROWSER_EXECUTABLE",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            )
        )
        browser_status = "NOT_VERIFIED"
        if shutil.which("pnpm") and (
            executable.is_file() or os.getenv("BUILDSIZE_PLAYWRIGHT_INSTALLED")
        ):
            browser_env = {
                **environment,
                "BUILDSIZE_BROWSER_ORIGIN": origin,
                "BUILDSIZE_BROWSER_PASSWORD": login_password,
                "BUILDSIZE_STACK_BUILD_ID": ids[-1],
            }
            if executable.is_file():
                browser_env["BUILDSIZE_BROWSER_EXECUTABLE"] = str(executable)
            subprocess.run(
                [
                    "pnpm",
                    "--dir",
                    str(ROOT / "frontend"),
                    "exec",
                    "playwright",
                    "test",
                    "--config",
                    "playwright.stack.config.ts",
                ],
                env=browser_env,
                check=True,
                capture_output=True,
                timeout=90,
            )
            browser_status = "PASS"
        # 入口覆盖伪造转发头；改变 X-Forwarded-For 不能绕过真实连接地址限流。
        from urllib.error import HTTPError

        codes = []
        for i in range(12):
            try:
                browser.open(
                    Request(
                        origin + "/api/v1/auth/login",
                        data=b'{"username":"invalid","password":"invalid"}',
                        headers={
                            "Content-Type": "application/json",
                            "Origin": origin,
                            "X-Forwarded-For": f"198.51.100.{i}",
                        },
                    )
                )
            except HTTPError as response:
                codes.append(response.code)
        assert codes[-1] == 429
        if args.benchmark:
            from benchmark_stack import measure

            performance = measure(origin, token, directory)
            performance["worker_cgroup_peak_bytes"] = int(
                run(
                    compose
                    + [
                        "exec",
                        "-T",
                        "worker",
                        "python",
                        "-c",
                        "print(open('/sys/fs/cgroup/memory.peak').read())",
                    ]
                ).stdout
            )
            info = json.loads(run(["docker", "info", "--format", "{{json .}}"]).stdout)
            performance["measured_at_utc"] = datetime.now(UTC).isoformat()
            performance["backend_image_id"] = (
                run(["docker", "image", "inspect", "buildsize-api:local", "--format", "{{.Id}}"])
                .stdout.decode()
                .strip()
            )
            performance["target_checks"] = {
                "rss_below_1gib": performance["worker_cgroup_peak_bytes"] < 1024**3,
                "pagination_p95_below_1s": all(
                    row["p95_ms"] < 1000 for row in performance["pagination"].values()
                ),
            }
            performance["docker_vm"] = {
                key: info[key] for key in ("NCPU", "MemTotal", "Architecture", "ServerVersion")
            }
            (ROOT / ".buildsize/performance.json").write_text(
                json.dumps(performance, ensure_ascii=False, indent=2) + "\n"
            )
            assert all(performance["target_checks"].values()), "PERFORMANCE_TARGET_NOT_MET"
        report = {
            "status": "PASS",
            "compose_services": ["web", "api", "worker", "scheduler", "postgres", "redis"],
            "checks": [
                "empty_migration",
                "same_origin_spa",
                "stream_upload",
                "real_worker_comparison",
                "restart_persistence",
                "offline_backup",
                "empty_volume_restore",
                "restored_login",
                "restored_token",
                "restored_download",
                "restored_reanalysis",
            ],
            "apk_fixture": "synthetic ZIP, not installable APK",
            "external_tls": "NOT_VERIFIED",
            "production_browser": browser_status,
            "spoofed_forwarded_header_rate_limit": "PASS",
        }
        (ROOT / ".buildsize/stack-validation.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except Exception as error:
        import re

        log = subprocess.run(
            compose + ["logs", "--no-color", "--tail", "30"],
            env=environment,
            capture_output=True,
            timeout=15,
        )
        details = (getattr(error, "stderr", b"") or b"").decode(errors="replace")
        details += (log.stdout + log.stderr).decode(errors="replace")
        for key in ("POSTGRES_PASSWORD", "DATABASE_URL", "SESSION_SECRET", "TOKEN_HASH_PEPPER"):
            details = details.replace(environment[key], "[REDACTED]")
        details = re.sub(r"\bbsi_[A-Za-z0-9_-]{32,}\b", "[REDACTED]", details)
        failure = ROOT / ".buildsize/stack-failure.log"
        failure.write_text(details)
        failure.chmod(0o600)
        print("STACK_VALIDATION_FAILED: " + type(error).__name__, file=sys.stderr)
        return 4
    finally:
        subprocess.run(
            compose + ["down", "--volumes", "--remove-orphans"],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=90,
        )


if __name__ == "__main__":
    raise SystemExit(main())
