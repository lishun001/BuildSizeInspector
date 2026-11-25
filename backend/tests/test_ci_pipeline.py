"""真实本机 HTTP/CLI/Git 及项目授权验证，不代表真实 Jenkins/蒲公英验收。"""

import copy
import hashlib
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_ci_client import captured_fixture, command, configuration, repository
from test_ci_client import clean_ci as clean_ci
from test_pipeline import ROOT, Harness, init, put, result, run, submission, submit
from test_pipeline import pipeline as pipeline

from buildsize.auth.service import issue_token
from buildsize.client.files import ClientError
from buildsize.client.submission import submit as submit_frozen
from buildsize.client.transport import ApiClient
from buildsize.db.models import Build, DistributionLink, Job, Project, RepositorySnapshot
from buildsize.domain.errors import BuildSizeError
from buildsize.jobs.worker import execute_job
from buildsize.storage import apk_download


@contextmanager
def live_api(h: Harness, public_origin: str | None = None, *, tls: tuple[Path, Path] | None = None):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        scheme = "https" if tls else "http"
        origin = f"{scheme}://127.0.0.1:{listener.getsockname()[1]}"
        env = {
            **os.environ,
            "DATABASE_URL": h.database_url,
            "PUBLIC_BASE_URL": public_origin or origin,
            "SESSION_SECRET": h.config.session_secret,
            "TOKEN_HASH_PEPPER": h.config.token_hash_pepper,
            "APP_ENV": "test",
            "BLOB_ROOT": str(h.config.blob_root),
            "TRUSTED_LINK_HOSTS": "www.pgyer.com,jenkins.example.invalid",
            "APK_DOWNLOAD_HOSTS": ",".join(h.config.apk_download_hosts),
        }
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "buildsize.api.app:create_app",
                "--factory",
                "--fd",
                str(listener.fileno()),
                "--log-level",
                "error",
                "--no-access-log",
                *(["--ssl-certfile", str(tls[0]), "--ssl-keyfile", str(tls[1])] if tls else []),
            ],
            env=env,
            pass_fds=(listener.fileno(),),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                try:
                    verify = ssl.create_default_context(cafile=str(tls[0])) if tls else None
                    with urlopen(
                        origin + "/api/v1/health/ready", timeout=0.2, context=verify
                    ) as response:
                        assert response.status == 200
                    break
                except (URLError, TimeoutError):
                    assert process.poll() is None, "API 进程提前退出"
                    assert time.monotonic() < deadline, "API 启动超时"
                    time.sleep(0.05)
            yield origin
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def cli(env, *args):
    process = subprocess.run(
        [sys.executable, "-m", "buildsize", *map(str, args)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert process.returncode == 0, process.stderr
    assert env["BUILDSIZE_API_TOKEN"] not in process.stdout + process.stderr
    return json.loads(process.stdout) if process.stdout else None


def token(h):
    with Session(h.engine) as db:
        project = db.get(Project, h.project_id)
        _, value = issue_token(
            db, h.config, project, ["builds:read", "builds:submit", "distribution:write"], 3600
        )
        db.commit()
        return value


@pytest.mark.parametrize(
    ("configured", "multibranch", "expected"),
    [
        (None, None, "origin/main"),
        ("pinned-branch", None, None),
        (None, "feature/main", "feature/main"),
    ],
)
def test_portable_prepare_captures_freestyle_git_branch(
    tmp_path, clean_ci, monkeypatch, configured, multibranch, expected
) -> None:
    repo = repository(tmp_path / "game")
    path = configuration(tmp_path / "ci.json", [repo])
    config = json.loads(path.read_text())
    config["metadata"]["branch"] = configured
    path.write_text(json.dumps(config))
    monkeypatch.setenv("GIT_BRANCH", "origin/main")
    if multibranch is not None:
        monkeypatch.setenv("BRANCH_NAME", multibranch)
    state = tmp_path / "capture"
    subprocess.run(
        [
            sys.executable,
            ROOT / "integrations/jenkins/unity-report-handoff.py",
            "prepare",
            "--directory",
            state,
            "--config",
            path,
            "--workspace",
            tmp_path,
            "--project",
            "demo-game",
            "--variant",
            "variant",
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    assert json.loads((state / "context.json").read_text())["branch"] == expected


def test_live_cli_two_git_repositories_and_pgyer(pipeline: Harness, clean_ci) -> None:
    h = pipeline
    repos = [repository(h.tmp_path / name) for name in ("源码 空格", "美术")]
    cfg = configuration(h.tmp_path / "config.json", repos)
    base_config = json.loads(cfg.read_text())
    old_build = None
    with live_api(h) as origin:
        env = {**os.environ, "BUILDSIZE_API_URL": origin, "BUILDSIZE_API_TOKEN": token(h)}
        for number in (1, 2):
            if number == 2:
                command(repos[0], "mv", "file.txt", "中文 new name.txt")
                command(repos[0], "commit", "-m", "rename")
                (repos[1] / "added.txt").write_text("art delta")
                command(repos[1], "add", ".")
                command(repos[1], "commit", "-m", "art")
            values = copy.deepcopy(base_config)
            values["ci"]["build_number"] = str(number)
            values["metadata"]["build_completed_at"] = (
                datetime.now(UTC) - timedelta(minutes=3 - number)
            ).isoformat()
            cfg.write_text(json.dumps(values))
            root = h.tmp_path / str(number)
            context, directory, receipt = (
                root / "context.json",
                root / "submission",
                root / "receipt.json",
            )
            cli(
                env,
                "prepare",
                "--project",
                "demo-game",
                "--variant",
                "android-release-arm64",
                "--config",
                cfg,
                "--workspace",
                h.tmp_path,
                "--output",
                context,
            )
            # 使用显式合成 ZIP，只验证 CI 数据流，不能冒充可安装 APK。
            _, payloads = submission("fixture", {"assets/test": b"x" * number})
            apk = root / "explicit final.apk"
            apk.write_bytes(payloads["apk"])
            cli(env, "collect", "--context", context, "--apk", apk, "--output", directory)
            first = cli(env, "submit", "--directory", directory, "--receipt", receipt)
            assert first["upload_state"] == "SEALED"
            assert first["analysis_state"] == "QUEUED"  # 202 后不等待 Worker。
            again = cli(env, "submit", "--directory", directory, "--receipt", receipt)
            assert first["build_id"] == again["build_id"]
            current = cli(env, "status", "--receipt", receipt)
            run(h, current["jobs"][0]["job_id"])
            analyzed = cli(env, "status", "--receipt", receipt)
            comparison_id = analyzed["result"]["comparison_id"]
            compared = h.client.get(
                f"/api/v1/comparisons/{comparison_id}", headers=h.headers
            ).json()
            if number == 1:
                assert compared["result"]["git_evidence_status"] == "NO_BASELINE"
                old_build = first["build_id"]
            else:
                assert compared["base_build_id"] == old_build
                assert compared["result"]["git_evidence_status"] == "complete"
                assert compared["result"]["git_change_count"] == 2
                page = h.client.get(
                    f"/api/v1/builds/{first['build_id']}/git-changes?limit=1", headers=h.headers
                ).json()
                assert page["next_offset"] == 1
                assert "author" not in json.dumps(page)
                assert (
                    json.loads((directory / "git-changes.json").read_text())[
                        "requested_base_build_id"
                    ]
                    == old_build
                )
        safe = root / "pgyer-result.json"
        safe.write_text(
            json.dumps(
                {
                    "publish_state": "failed",
                    "build_key": None,
                    "build_version": None,
                    "install_page_url": None,
                    "published_at": None,
                    "apiKey": "must-not-be-sent",
                }
            )
        )
        linked = cli(env, "link-pgyer", "--receipt", receipt, "--result", safe)
        assert linked["publish_state"] == "failed"
        assert "apiKey" not in linked
        safe.write_text(
            json.dumps(
                {
                    "publish_state": "published",
                    "build_key": "fixture-key",
                    "build_version": "2",
                    "install_page_url": "https://www.pgyer.com/fixture",
                    "published_at": datetime.now(UTC).isoformat(),
                }
            )
        )
        assert (
            cli(env, "link-pgyer", "--receipt", receipt, "--result", safe)["publish_state"]
            == "published"
        )
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(Build)) == 2
        assert db.scalar(select(func.count()).select_from(Job)) == 2
        assert db.scalar(select(func.count()).select_from(RepositorySnapshot)) == 4
        assert "must-not-be-sent" not in str(db.scalar(select(DistributionLink)).payload)


def test_submit_resumes_verified_roles(pipeline: Harness, clean_ci, monkeypatch) -> None:
    from buildsize.client.context import prepare
    from buildsize.client.submission import collect, validate_frozen

    h = pipeline
    repo = repository(h.tmp_path / "repo")
    cfg = configuration(h.tmp_path / "config.json", [repo])
    context, directory, receipt = (
        h.tmp_path / name for name in ("context.json", "submission", "receipt.json")
    )
    prepare("demo-game", "variant", context, cfg, h.tmp_path)
    _, payloads = submission("fixture")
    apk = h.tmp_path / "game.apk"
    apk.write_bytes(payloads["apk"])
    collect(context, apk, directory)
    manifest = validate_frozen(directory).model_dump(mode="json")
    initialized = init(h, manifest).json()
    assert put(h, initialized["build_id"], "apk", payloads["apk"]).status_code == 201
    requests = []
    original = ApiClient.request

    def observed(self, method, path, **kwargs):
        requests.append((method, path))
        return original(self, method, path, **kwargs)

    monkeypatch.setattr(ApiClient, "request", observed)
    with live_api(h) as origin:
        monkeypatch.setenv("BUILDSIZE_API_URL", origin)
        monkeypatch.setenv("BUILDSIZE_API_TOKEN", h.headers["authorization"].split()[1])
        value = submit_frozen(directory, receipt)
    assert value["build_id"] == initialized["build_id"]
    assert not any(path.endswith("/artifacts/apk") for _, path in requests)
    assert len([method for method, _ in requests if method == "PUT"]) == 2


def test_shell_reports_and_downloaded_apk_complete_one_build(pipeline: Harness, clean_ci) -> None:
    h = pipeline
    context, reports, original = captured_fixture(h.tmp_path)
    _, fixture = submission("apk-fixture")
    original.write_bytes(fixture["apk"])
    completed = h.tmp_path / "completed-at.txt"
    completed.write_text(
        json.loads((reports / "unity-settings.json").read_text())["build_completed_at"]
    )
    directory = h.tmp_path / "report upload"
    receipt = h.tmp_path / "receipt.json"
    shell = ROOT / "integrations/jenkins/submit-unity-reports.sh"
    with live_api(h) as origin:
        environment = {
            **os.environ,
            "BUILDSIZE_API_URL": origin,
            "BUILDSIZE_API_TOKEN": h.headers["authorization"].split()[1],
            "BUILDSIZE_BIN": str(Path(sys.executable).with_name("buildsize")),
        }
        sent = subprocess.run(
            [
                "/bin/sh",
                shell,
                context,
                original,
                "fixture-build-key",
                completed,
                directory,
                receipt,
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert sent.returncode == 0, sent.stderr
        assert (
            environment["BUILDSIZE_API_TOKEN"]
            not in sent.stdout + sent.stderr + receipt.read_text()
        )
        pending = json.loads(sent.stdout)
        build_id = pending["build_id"]
        assert pending["upload_state"] == "UPLOADING"
        assert pending["pgyer_build_key"] == "fixture-build-key"
        assert not (directory / "game.apk").exists()
        current = cli(environment, "status", "--receipt", receipt)
        states = {row["role"]: row["state"] for row in current["artifacts"]}
        assert states == {
            "apk": "pending",
            "build_meta": "verified",
            "git_changes": "verified",
            "unity_report": "verified",
        }
        assert current["jobs"] == []

        wrong = h.tmp_path / "wrong version.apk"
        wrong.write_bytes(bytes([fixture["apk"][0] ^ 1]) + fixture["apk"][1:])
        rejected = subprocess.run(
            [
                sys.executable,
                "-m",
                "buildsize",
                "complete-apk",
                "--receipt",
                receipt,
                "--apk",
                wrong,
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert rejected.returncode == 5 and "PGYER_APK_MISMATCH" in rejected.stderr
        assert cli(environment, "status", "--receipt", receipt)["jobs"] == []

        # 运维超过常规上传窗口后仍可用同一个回执续期，报告保持已登记状态。
        with Session(h.engine) as db:
            build = db.get(Build, uuid.UUID(build_id))
            build.upload_expires_at = datetime.now(UTC) - timedelta(minutes=1)
            db.commit()
        downloaded = h.tmp_path / "downloaded example.apk"
        downloaded.write_bytes(fixture["apk"])
        original.unlink()
        for _ in range(2):
            sealed = cli(environment, "complete-apk", "--receipt", receipt, "--apk", downloaded)
            assert sealed["build_id"] == build_id and sealed["upload_state"] == "SEALED"
        current = cli(environment, "status", "--receipt", receipt)
        assert len(current["jobs"]) == 1
        run(h, current["jobs"][0]["job_id"])
        analyzed = cli(environment, "status", "--receipt", receipt)
        assert analyzed["result"]["apk"]["apk_file_bytes"] == len(fixture["apk"])
        assert analyzed["result"]["unity"]["report_record_count"] == 2
        assert json.loads(receipt.read_text())["build_id"] == build_id


@pytest.mark.skipif(shutil.which("openssl") is None, reason="HTTPS 测试需要本机 openssl")
@pytest.mark.parametrize("automatic", [False, True])
def test_portable_mac_report_handoff_without_installed_cli(
    pipeline: Harness,
    clean_ci,
    automatic: bool,
    monkeypatch,
) -> None:
    h = pipeline
    source_context, reports, apk = captured_fixture(h.tmp_path)
    _, fixture = submission("portable-apk")
    apk.write_bytes(fixture["apk"])
    completed_at = h.tmp_path / "completed-at.txt"
    completed_at.write_text(
        json.loads((reports / "unity-settings.json").read_text())["build_completed_at"]
    )
    state = h.tmp_path / "portable capture"
    receipt = state / "receipt.json"
    helper = ROOT / "integrations/jenkins/unity-report-handoff.py"
    cert, key = h.tmp_path / "server.crt", h.tmp_path / "server.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1,DNS:apk.example.com",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        capture_output=True,
        check=True,
        timeout=15,
    )
    url_file = state / "apk-download-url.txt"
    url = "https://apk.example.com/final.apk?signature=fixture-signed-url"
    if automatic:
        h.config = replace(h.config, apk_download_hosts=("apk.example.com",))
    with live_api(h, tls=(cert, key)) as origin:
        environment = {
            **os.environ,
            "BUILDSIZE_API_URL": origin,
            "BUILDSIZE_API_TOKEN": h.headers["authorization"].split()[1],
            "SSL_CERT_FILE": str(cert),
        }
        # 模拟 Jenkins 上不从默认证书路径读取 SSL_CERT_FILE 的 Python。
        launcher = """
import runpy
import ssl
import sys

ssl.SSLContext.set_default_verify_paths = lambda self: None
helper = sys.argv.pop(1)
sys.argv[0] = helper
runpy.run_path(helper, run_name="__main__")
"""
        isolated_python = [sys.executable, "-I", "-S", "-c", launcher, helper]

        def portable(*args):
            proc = subprocess.run(
                [*isolated_python, *map(str, args)],
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert proc.returncode == 0, proc.stderr
            assert environment["BUILDSIZE_API_TOKEN"] not in proc.stdout + proc.stderr
            return json.loads(proc.stdout)

        prepared = portable(
            "prepare",
            "--directory",
            state,
            "--config",
            h.tmp_path / "config.json",
            "--workspace",
            h.tmp_path,
            "--project",
            "demo-game",
            "--variant",
            "variant",
        )
        assert prepared["run_id"] == json.loads(source_context.read_text())["run_id"]
        assert environment["BUILDSIZE_API_TOKEN"] not in (state / "context.json").read_text()
        shutil.copytree(reports, prepared["report_directory"])
        if automatic:
            url_file.write_text(url)
        arguments = (
            "submit",
            "--directory",
            state,
            "--apk",
            apk,
            "--pgyer-build-key",
            "portable-key",
            "--completed-at-file",
            completed_at,
            "--receipt",
            receipt,
            *(("--apk-url-file", url_file) if automatic else ()),
        )
        untrusted = subprocess.run(
            [*isolated_python, *map(str, arguments)],
            env={**environment, "SSL_CERT_FILE": ""},
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert untrusted.returncode == 2 and "HTTPS 证书校验失败" in untrusted.stderr
        assert not receipt.exists()
        first = portable(*arguments)
        (h.tmp_path / "repo" / "untracked-after-submit.txt").write_text("changed workspace")
        assert portable(*arguments)["build_id"] == first["build_id"]
        current = cli(environment, "status", "--receipt", receipt)
        assert {row["role"]: row["state"] for row in current["artifacts"]} == {
            "apk": "pending",
            "build_meta": "verified",
            "unity_report": "verified",
        }

        if automatic:
            assert current["analysis_state"] == "QUEUED"
            assert current["jobs"][0]["apk_download_pending"] is True
            assert (
                h.client.post(
                    f"/api/v1/builds/{first['build_id']}/finalize-from-url",
                    json={"download_url": url},
                    headers=h.other_headers,
                ).status_code
                == 404
            )
            assert "fixture-signed-url" not in json.dumps(first) + receipt.read_text() + json.dumps(
                current
            )
            job_id = uuid.UUID(current["jobs"][0]["job_id"])
            with Session(h.engine) as db:
                job = db.get(Job, job_id)
                assert (
                    job.apk_download_secret and "fixture-signed-url" not in job.apk_download_secret
                )
            # Worker 实际通过 TLS 流式读取。测试只替换已验证公网 IP 到本机夹具的路由。
            with apk_fixture(cert, key, fixture["apk"], monkeypatch) as requests:
                with pytest.raises(BuildSizeError, match="错误码") as error:
                    execute_job(h.engine, h.config, job_id)
                assert error.value.code == "ARTIFACT_HASH_MISMATCH"
                failed = cli(environment, "status", "--receipt", receipt)
                assert failed["analysis_state"] == "FAILED" and failed["analysis_revision"] is None
                url_file.write_text(url.replace("final.apk", "correct.apk"))
                assert portable(*arguments)["analysis_state"] == "QUEUED"
                assert execute_job(h.engine, h.config, job_id) == "PUBLISHED"
                assert requests == ["/final.apk", "/correct.apk"]
            with Session(h.engine) as db:
                assert db.get(Job, job_id).apk_download_secret is None
        else:
            assert current["jobs"] == []
            downloaded = h.tmp_path / "from-pgyer.apk"
            downloaded.write_bytes(fixture["apk"])
            sealed = cli(environment, "complete-apk", "--receipt", receipt, "--apk", downloaded)
            assert sealed["build_id"] == first["build_id"] and sealed["upload_state"] == "SEALED"
            current = cli(environment, "status", "--receipt", receipt)
            run(h, current["jobs"][0]["job_id"])
        current = cli(environment, "status", "--receipt", receipt)
        assert current["result"]["unity"]["report_record_count"] == 2
        assert len(current["jobs"]) == 1
        assert current["jobs"][0]["apk_download_pending"] is False


@contextmanager
def apk_fixture(cert, key, apk, monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            requests.append(path)
            data = apk if path == "/correct.apk" else bytes([apk[0] ^ 1]) + apk[1:]
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connect = socket.create_connection

        def route(address, *args, **kwargs):
            assert address == ("93.184.216.34", 443)
            return connect(server.server_address, *args, **kwargs)

        try:
            with monkeypatch.context() as patch:
                patch.setattr(apk_download, "_resolve", lambda host: "93.184.216.34")
                patch.setattr(socket, "create_connection", route)
                patch.setenv("SSL_CERT_FILE", str(cert))
                yield requests
        finally:
            server.shutdown()
            thread.join()


def test_pgyer_authorization_safety_and_idempotency(
    pipeline: Harness,
) -> None:
    h = pipeline
    build_id, _ = submit(h, *submission("distribution"))
    path = f"/api/v1/builds/{build_id}/distribution/pgyer"
    pending = dict(
        publish_state="publishing",
        build_key=None,
        build_version=None,
        install_page_url=None,
        published_at=None,
    )
    assert h.client.put(path, json=pending, headers=h.headers).status_code == 404
    allowed = {"authorization": "Bearer " + token(h)}
    assert h.client.put(path, json=pending, headers=h.other_headers).status_code == 404
    assert h.client.put(path, json=pending, headers=allowed).status_code == 200
    assert (
        h.client.put(path, json={**pending, "apiKey": "forbidden"}, headers=allowed).status_code
        == 422
    )
    for url in (
        "https://jenkins.example.invalid/x?token=private",
        "https://untrusted.invalid/app",
        "https://jenkins.example.invalid/x?X-Amz-Signature=private",
    ):
        body = {
            **pending,
            "publish_state": "published",
            "build_key": "key",
            "install_page_url": url,
            "published_at": datetime.now(UTC).isoformat(),
        }
        assert h.client.put(path, json=body, headers=allowed).status_code == 422
    assert (
        h.client.put(
            path,
            json={**pending, "install_page_url": "https://jenkins.example.invalid/app"},
            headers=allowed,
        ).status_code
        == 422
    )
    published = {
        **pending,
        "publish_state": "published",
        "build_key": "key",
        "install_page_url": "https://jenkins.example.invalid/app",
        "published_at": datetime.now(UTC).isoformat(),
    }
    assert h.client.put(path, json=published, headers=allowed).status_code == 200
    assert h.client.put(path, json=published, headers=allowed).status_code == 200
    assert h.client.put(path, json=pending, headers=allowed).status_code == 409
    assert result(h, build_id)["distribution"]["pgyer"] == published


def rewrite(manifest, payloads, role, value):
    payloads[role] = json.dumps(value).encode()
    row = next(row for row in manifest["artifacts"] if row["role"] == role)
    row.update(bytes=len(payloads[role]), sha256=hashlib.sha256(payloads[role]).hexdigest())


def test_candidate_and_manual_git_range_mismatch(pipeline: Harness) -> None:
    h = pipeline
    first, job = submit(h, *submission("first", age=120))
    run(h, job)
    manifest, payloads = submission("target", age=60, reports={"git_changes": {}})
    old = json.loads(payloads["build_meta"])
    meta = copy.deepcopy(old)
    for index, repo in enumerate(meta["repositories"]):
        repo["revision"] = str(index + 3) * 40
    rewrite(manifest, payloads, "build_meta", meta)
    report = {
        "schema_version": 1,
        "run_id": manifest["run_id"],
        "requested_base_build_id": first,
        "repositories": [
            {
                "repo_key": repo["repo_key"],
                "base_revision": before["revision"],
                "head_revision": repo["revision"],
                "range_status": "complete",
                "is_ancestor": True,
                "changes": [],
                "commit_ids": [],
            }
            for before, repo in zip(old["repositories"], meta["repositories"], strict=True)
        ],
        "warnings": [],
    }
    rewrite(manifest, payloads, "git_changes", report)
    query = {
        "application_id": meta["application_id"],
        "channel": meta["channel"],
        "variant": meta["variant"],
        "branch": meta["branch"],
        "ci_instance_id": meta["ci"]["instance_id"],
        "ci_job_full_name": meta["ci"]["job_full_name"],
        "build_completed_at": meta["build_completed_at"],
        "development_build": meta["settings"]["development_build"],
        "scripting_backend": meta["settings"]["scripting_backend"],
        "architectures": meta["settings"]["architectures"],
    }
    path = "/api/v1/projects/demo-game/baseline-candidate"
    assert h.client.get(path, params=query, headers=h.other_headers).status_code == 404
    assert h.client.get(path, params=query, headers=h.headers).json()["base_build_id"] == first
    assert (
        h.client.get(path, params={**query, "branch": "other"}, headers=h.headers).json()["status"]
        == "NO_BASELINE"
    )
    assert (
        h.client.get(
            path, params={**query, "build_completed_at": "2020-01-01T00:00:00"}, headers=h.headers
        ).status_code
        == 422
    )
    target, job = submit(h, manifest, payloads)
    run(h, job)
    selected = result(h, target)["result"]["comparison_id"]
    assert (
        h.client.get(f"/api/v1/comparisons/{selected}", headers=h.headers).json()["result"][
            "git_evidence_status"
        ]
        == "complete"
    )
    other_manifest, other_payloads = submission("middle", age=90)
    other_meta = json.loads(other_payloads["build_meta"])
    other_meta["repositories"][0]["revision"] = "f" * 40
    other_meta["repositories"][1]["revision"] = "e" * 40
    rewrite(other_manifest, other_payloads, "build_meta", other_meta)
    middle, job = submit(h, other_manifest, other_payloads)
    run(h, job)
    response = h.client.post(
        "/api/v1/comparisons",
        json={"base_build_id": middle, "target_build_id": target},
        headers={**h.headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    comparison_id = response.json()["comparison_id"]
    with Session(h.engine) as db:
        job = db.scalar(select(Job).where(Job.comparison_id == uuid.UUID(comparison_id)))
        run_id = str(job.id)
    run(h, run_id)
    value = h.client.get(f"/api/v1/comparisons/{comparison_id}", headers=h.headers).json()
    assert value["result"]["git_evidence_status"] == "mismatched_range"
    assert value["result"]["git_change_count"] is None
    assert (
        h.client.get(f"/api/v1/builds/{target}/git-changes", headers=h.other_headers).status_code
        == 404
    )


def test_missing_receipt_never_chooses_latest(tmp_path: Path, monkeypatch) -> None:
    from buildsize.client.submission import link_pgyer

    monkeypatch.setenv("BUILDSIZE_API_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("BUILDSIZE_API_TOKEN", "temporary-fixture-token")
    with pytest.raises(ClientError) as error:
        link_pgyer(tmp_path / "missing.json", tmp_path / "also-missing.json")
    assert error.value.code == "RECEIPT_MISSING" and error.value.exit_code == 2
