"""Playwright + 真实 Vite/API/PostgreSQL/Redis/prefork；不 mock 业务响应。"""

import json
import os
import signal
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from test_async_pipeline import publisher, require_services, worker
from test_ci_pipeline import live_api
from test_pipeline import Harness, result, run, submission, submit
from test_pipeline import pipeline as pipeline

from buildsize.jobs.dispatcher import dispatch_once

ROOT = Path(__file__).resolve().parents[2]


def test_real_browser_flows(pipeline: Harness):
    require_services()
    h = pipeline
    executable = os.getenv("BUILDSIZE_BROWSER_EXECUTABLE")
    if not executable:
        candidate = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        executable = str(candidate) if candidate.is_file() else None
    if not executable and not os.getenv("BUILDSIZE_PLAYWRIGHT_INSTALLED"):
        pytest.skip("NOT_VERIFIED：设置浏览器路径或按前端说明安装 Chromium")
    first, job = submit(
        h, *submission("browser-base", {"assets/base": b"a", "classes.dex": b"dex"}, age=120)
    )
    run(h, job)
    # other-game 构建 ID 用于跨项目拒绝：直接初始化到另一个项目，不授予 viewer。
    foreign_manifest, _ = submission("browser-foreign")
    foreign_manifest["project_key"] = "other-game"
    foreign = h.client.post(
        "/api/v1/projects/other-game/builds",
        json=foreign_manifest,
        headers={**h.other_headers, "Idempotency-Key": foreign_manifest["run_id"]},
    )
    assert foreign.status_code == 201
    foreign_id = foreign.json()["build_id"]
    path = "assets/<img src=x onerror=globalThis.__xss=1>.bin"
    manifest, payloads = submission(
        "browser-target",
        {
            path: b"a" * 100,
            "classes.dex": b"dex",
            "lib/arm64-v8a/native.so": b"native",
            **{f"assets/page-{index:02}.bin": b"x" for index in range(30)},
        },
        age=60,
    )
    paths = {}
    for role, raw in payloads.items():
        target = h.tmp_path / (role + ".bin")
        target.write_bytes(raw)
        paths[role] = str(target)
    fixture = h.tmp_path / "browser.json"
    fixture.write_text(
        json.dumps(
            {
                "manifest": manifest,
                "paths": paths,
                "path": path,
                "foreign": foreign_id,
                "first_comparison": result(h, first)["result"]["comparison_id"],
            }
        )
    )
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    queue = "bsi_browser_" + uuid.uuid4().hex
    stop = threading.Event()
    errors = []
    publish = publisher(h, queue)

    def dispatch():
        while not stop.is_set():
            try:
                dispatch_once(h.engine, h.config, publish)
            except Exception:
                errors.append("DISPATCH_FAILED")
                return
            stop.wait(0.1)

    with live_api(h, public_origin=origin) as api_origin, worker(h, queue, pool="prefork"):
        environment = {
            **os.environ,
            "BUILDSIZE_DEV_API_ORIGIN": api_origin,
            "BUILDSIZE_BROWSER_ORIGIN": origin,
            "BUILDSIZE_BROWSER_FIXTURE": str(fixture),
            "BUILDSIZE_BROWSER_PASSWORD": "fixture-admin-only-password",
            "BUILDSIZE_BROWSER_VIEWER_PASSWORD": "fixture-viewer-only-password",
            "BUILDSIZE_BROWSER_SCREENSHOT": str(ROOT / ".buildsize/web-desktop.png"),
        }
        if executable:
            environment["BUILDSIZE_BROWSER_EXECUTABLE"] = executable
        process = subprocess.Popen(
            [
                "pnpm",
                "--dir",
                str(ROOT / "frontend"),
                "exec",
                "vite",
                "--port",
                str(port),
                "--strictPort",
            ],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        thread = threading.Thread(target=dispatch, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    with urlopen(origin, timeout=0.2) as response:
                        assert response.status == 200
                    break
                except (URLError, TimeoutError):
                    assert process.poll() is None and time.monotonic() < deadline, "Vite 未能启动"
                    time.sleep(0.05)
            (ROOT / ".buildsize/browser-preview.json").write_text(json.dumps({"url": origin}))
            completed = subprocess.run(
                ["pnpm", "--dir", str(ROOT / "frontend"), "test:e2e"],
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert completed.returncode == 0, completed.stdout + completed.stderr
            assert not errors
        finally:
            stop.set()
            thread.join(timeout=5)
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
