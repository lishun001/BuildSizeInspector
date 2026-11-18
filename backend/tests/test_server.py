"""真实 Uvicorn 进程验证启动与降级；不冒充 PostgreSQL 就绪。"""

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def test_uvicorn_live_and_unavailable_database() -> None:
    environment = dict(os.environ)
    environment.update(
        {
            "DATABASE_URL": "postgresql+psycopg://127.0.0.1:1/buildsize?connect_timeout=1",
            "PUBLIC_BASE_URL": "http://localhost:8000",
            "SESSION_SECRET": secrets.token_urlsafe(48),
            "TOKEN_HASH_PEPPER": secrets.token_urlsafe(48),
            "APP_ENV": "development",
        }
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
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
            ],
            env=environment,
            pass_fds=(listener.fileno(),),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                try:
                    with urlopen(base_url + "/api/v1/health/live", timeout=0.25) as response:
                        assert json.load(response) == {"status": "live"}
                    break
                except (URLError, TimeoutError):
                    assert process.poll() is None, "Uvicorn 未能启动"
                    assert time.monotonic() < deadline, "Uvicorn 启动超时"
                    time.sleep(0.05)
            try:
                urlopen(base_url + "/api/v1/health/ready", timeout=2)
            except HTTPError as response:
                assert response.code == 503
                assert json.load(response)["error"]["code"] == "SERVICE_UNAVAILABLE"
            else:
                raise AssertionError("数据库不可用时不能返回 ready")
            try:
                urlopen(
                    Request(
                        base_url + "/api/v1/projects/demo/not-implemented",
                        data=b"{}",
                        headers={"Content-Type": "application/json"},
                    ),
                    timeout=2,
                )
            except HTTPError as response:
                assert response.code == 404
                assert json.load(response)["error"]["code"] == "NOT_FOUND"
            else:
                raise AssertionError("尚未实现上传接口，不能返回成功")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            assert process.stdout is not None
            process.stdout.close()
