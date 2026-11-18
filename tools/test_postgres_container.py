"""在一次性 PostgreSQL 16 容器内运行合同测试；凭据只通过环境传递。"""

import argparse
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg
from redis import Redis

ROOT = Path(__file__).resolve().parents[1]
IMAGE = (
    "postgres:16.11-bookworm@"
    "sha256:a2420e9555e2224583fe84d0bb3f0b967e69354ae3a0be55a9c14e251388c4eb"
)
REDIS_IMAGE = (
    "redis:7.4.6-bookworm@sha256:a9cc41d6d01da2aa26c219e4f99ecbeead955a7b656c1c499cce8922311b2514"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="一次性 PostgreSQL/Redis 环境验证")
    parser.add_argument(
        "--e2e", action="store_true", help="只运行 API/异步恢复/CLI/HTTP 端到端场景"
    )
    parser.add_argument("--browser-only", action="store_true", help="仅运行真实浏览器闭环")
    args = parser.parse_args()
    name = "buildsize-test-" + uuid.uuid4().hex
    redis_name = "buildsize-redis-test-" + uuid.uuid4().hex
    volume_name = "buildsize-test-data-" + uuid.uuid4().hex
    password = secrets.token_urlsafe(32)
    environment = {**os.environ, "POSTGRES_PASSWORD": password}
    # Docker 自动分配的 HostPort 可能在 restart 后变化；为本轮选定并固定一个空闲端口。
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        postgres_port = reservation.getsockname()[1]
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--detach",
                "--name",
                name,
                "--memory",
                "1g",
                "--cpus",
                "2",
                "--mount",
                f"type=volume,source={volume_name},target=/var/lib/postgresql/data",
                "--publish",
                f"127.0.0.1:{postgres_port}:5432",
                "--env",
                "POSTGRES_PASSWORD",
                "--env",
                "POSTGRES_USER=buildsize",
                "--env",
                "POSTGRES_DB=buildsize_test",
                IMAGE,
            ],
            env=environment,
            check=True,
            stdout=subprocess.DEVNULL,
            timeout=300,
        )
        published = subprocess.run(
            ["docker", "port", name, "5432/tcp"],
            check=True,
            text=True,
            capture_output=True,
            timeout=10,
        ).stdout.strip()
        port = int(published.rsplit(":", 1)[1])
        database_url = f"postgresql://buildsize:{password}@127.0.0.1:{port}/buildsize_test"
        deadline = time.monotonic() + 45
        while True:
            try:
                with psycopg.connect(database_url, connect_timeout=1):
                    break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    print("测试数据库启动超时", file=sys.stderr)
                    return 4
                time.sleep(0.2)
        test_environment = {
            **os.environ,
            "BUILDSIZE_TEST_DATABASE_URL": database_url.replace(
                "postgresql://", "postgresql+psycopg://", 1
            ),
        }
        test_environment["BUILDSIZE_TEST_POSTGRES_CONTAINER"] = name
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--detach",
                "--name",
                redis_name,
                "--memory",
                "128m",
                "--cpus",
                "1",
                "--publish",
                "127.0.0.1::6379",
                REDIS_IMAGE,
                "redis-server",
                "--save",
                "",
                "--appendonly",
                "no",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            timeout=300,
        )
        published = subprocess.run(
            ["docker", "port", redis_name, "6379/tcp"],
            check=True,
            text=True,
            capture_output=True,
            timeout=10,
        ).stdout.strip()
        redis_url = f"redis://127.0.0.1:{int(published.rsplit(':', 1)[1])}/0"
        with Redis.from_url(redis_url) as client:
            client.ping()
        test_environment["BUILDSIZE_TEST_REDIS_URL"] = redis_url
        (ROOT / ".buildsize").mkdir(exist_ok=True)
        tests = (
            ["backend/tests/test_browser.py"]
            if args.browser_only
            else (
                ["backend/tests"]
                if not args.e2e
                else [
                    "backend/tests/test_pipeline.py",
                    "backend/tests/test_async_pipeline.py",
                    "backend/tests/test_unity_pipeline.py",
                    "backend/tests/test_ci_pipeline.py",
                    "backend/tests/test_ci_client.py",
                    "backend/tests/test_ci_transport.py",
                    "backend/tests/test_rules_pipeline.py",
                    "backend/tests/test_retention.py",
                    "backend/tests/test_backup.py",
                    "backend/tests/test_browser.py",
                    "backend/tests/test_cli.py",
                    "backend/tests/test_server.py",
                ]
            )
        )
        report = (
            ".buildsize/browser-harness.xml"
            if args.browser_only
            else ".buildsize/e2e-results.xml"
            if args.e2e
            else ".buildsize/postgres-test-results.xml"
        )
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *tests,
                "-q",
                "--junitxml=" + report,
            ],
            cwd=ROOT,
            env=test_environment,
            check=False,
        ).returncode
    except (OSError, subprocess.SubprocessError, ValueError):
        print("无法创建一次性 PostgreSQL 测试环境；请检查 Docker 服务与镜像可用性", file=sys.stderr)
        return 4
    finally:
        try:
            subprocess.run(
                ["docker", "rm", "--force", "--volumes", name, redis_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            subprocess.run(
                ["docker", "volume", "rm", volume_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            print("临时测试容器清理失败，请检查 buildsize-test- 容器", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
