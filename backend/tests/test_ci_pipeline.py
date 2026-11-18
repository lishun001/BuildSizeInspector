"""真实本机 HTTP/CLI/Git 及项目授权验证，不代表真实 Jenkins/蒲公英验收。"""

import copy
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_ci_client import clean_ci as clean_ci
from test_ci_client import command, configuration, repository
from test_pipeline import Harness, init, put, result, run, submission, submit
from test_pipeline import pipeline as pipeline

from buildsize.auth.service import issue_token
from buildsize.client.files import ClientError
from buildsize.client.submission import submit as submit_frozen
from buildsize.client.transport import ApiClient
from buildsize.db.models import Build, DistributionLink, Job, Project, RepositorySnapshot


@contextmanager
def live_api(h: Harness, public_origin: str | None = None):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        env = {
            **os.environ,
            "DATABASE_URL": h.database_url,
            "PUBLIC_BASE_URL": public_origin or origin,
            "SESSION_SECRET": h.config.session_secret,
            "TOKEN_HASH_PEPPER": h.config.token_hash_pepper,
            "APP_ENV": "test",
            "BLOB_ROOT": str(h.config.blob_root),
            "TRUSTED_LINK_HOSTS": "www.pgyer.com,jenkins.example.invalid",
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
                    with urlopen(origin + "/api/v1/health/ready", timeout=0.2) as response:
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


def test_pgyer_authorization_safety_and_idempotency(pipeline: Harness) -> None:
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
