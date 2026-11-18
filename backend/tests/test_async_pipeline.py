"""真实 PostgreSQL/Redis/Celery 流程，缺少专用连接时明确跳过。"""

import os
import signal
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import timedelta

import pytest
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_pipeline import Harness, result, submission, submit
from test_pipeline import pipeline as pipeline

from buildsize.db.models import Build, Job, Outbox
from buildsize.jobs.celery_app import create_celery
from buildsize.jobs.dispatcher import dispatch_once
from buildsize.services.common import now


@contextmanager
def worker(h: Harness, queue: str, *, delay: bool = False, pool: str = "solo"):
    environment = {
        **os.environ,
        "DATABASE_URL": h.database_url,
        "REDIS_URL": h.config.redis_url,
        "BLOB_ROOT": str(h.config.blob_root),
        "SESSION_SECRET": h.config.session_secret,
        "TOKEN_HASH_PEPPER": h.config.token_hash_pepper,
        "PUBLIC_BASE_URL": h.config.origin,
        "APP_ENV": "test",
        "PYTHONUNBUFFERED": "1",
    }
    script = """
import sys, time
from buildsize.config import Config
from buildsize.jobs import worker
from buildsize.jobs.celery_app import create_celery
if sys.argv[2] == 'delay':
    original = worker.analyze_apk
    worker.analyze_apk = lambda *args, **kwargs: (time.sleep(60), original(*args, **kwargs))[1]
app = create_celery(Config.from_env(), sys.argv[1])
app.worker_main(['worker', '--pool=' + sys.argv[3], '--concurrency=1', '--loglevel=ERROR',
                 '--without-gossip', '--without-mingle', '--without-heartbeat'])
"""
    log = h.tmp_path / f"{uuid.uuid4().hex}.worker.log"
    with log.open("wb") as output:
        process = subprocess.Popen(
            [sys.executable, "-c", script, queue, "delay" if delay else "normal", pool],
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            yield process
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def wait_state(h: Harness, job_id: str, states: set[str], process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        with Session(h.engine) as db:
            job = db.get(Job, uuid.UUID(job_id))
            state, code = job.state, job.error_code
        if state in states:
            return
        assert state != "FAILED", f"异步任务失败：{code}"
        assert process.poll() is None, "Celery Worker 进程提前退出"
        time.sleep(0.05)
    raise AssertionError(f"异步任务未在期限内进入 {states}")


def publisher(h: Harness, queue: str):
    app = create_celery(h.config, queue)

    def publish(job_id: str) -> None:
        app.send_task("buildsize.execute", args=[job_id], queue=queue, retry=False)

    return publish


def require_services() -> None:
    if not os.getenv("BUILDSIZE_TEST_DATABASE_URL") or not os.getenv("BUILDSIZE_TEST_REDIS_URL"):
        pytest.skip("NOT_VERIFIED：真实 Celery 验证需要专用 PostgreSQL 与 Redis 连接")


def test_real_broker_two_builds_and_duplicate_delivery(pipeline: Harness) -> None:
    require_services()
    h = pipeline
    queue = "bsi_test_" + uuid.uuid4().hex
    publish = publisher(h, queue)
    with worker(h, queue, pool="prefork") as process:
        old, old_job = submit(h, *submission("old", {"a": b"123"}, age=120))
        assert dispatch_once(h.engine, h.config, publish) == 1
        wait_state(h, old_job, {"SUCCEEDED"}, process)
        target, target_job = submit(h, *submission("new", {"a": b"12345", "b": b"x"}, age=60))
        assert dispatch_once(h.engine, h.config, publish) == 1
        wait_state(h, target_job, {"SUCCEEDED"}, process)
        current = result(h, target)
        comparison_id = current["result"]["comparison_id"]
        comparison = h.client.get(f"/api/v1/comparisons/{comparison_id}", headers=h.headers).json()
        assert comparison["base_build_id"] == old
        assert (
            comparison["result"]["delta_apk_bytes"] == 81
        )  # 独立 Stored 字节账：内容 +3，新增头/目录 +78。
        publish(target_job)
        assert result(h, target)["analysis_revision"] == current["analysis_revision"]


def test_worker_kill_and_lost_queue_recover_from_database(pipeline: Harness) -> None:
    require_services()
    h = pipeline
    queue = "bsi_test_" + uuid.uuid4().hex
    publish = publisher(h, queue)
    build_id, job_id = submit(h, *submission("crash"))
    with worker(h, queue, delay=True) as process:
        assert dispatch_once(h.engine, h.config, publish) == 1
        wait_state(h, job_id, {"RUNNING"}, process)
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    assert result(h, build_id)["analysis_revision"] is None
    with Session(h.engine) as db:
        db.get(Job, uuid.UUID(job_id)).lease_expires_at = now() - timedelta(seconds=1)
        row = db.scalar(select(Outbox).where(Outbox.job_id == uuid.UUID(job_id)))
        row.sent_at = now() - timedelta(seconds=h.config.redelivery_seconds + 1)
        db.commit()
    assert dispatch_once(h.engine, h.config, publish) == 1
    redis = Redis.from_url(h.config.redis_url)
    try:
        assert redis.llen(queue) >= 1
        redis.delete(queue)  # 只删除本测试随机 UUID 队列，模拟消息丢失。
        with Session(h.engine) as db:
            row = db.scalar(select(Outbox).where(Outbox.job_id == uuid.UUID(job_id)))
            row.sent_at = now() - timedelta(seconds=h.config.redelivery_seconds + 1)
            db.commit()
        with worker(h, queue) as restarted:
            assert dispatch_once(h.engine, h.config, publish) == 1
            wait_state(h, job_id, {"SUCCEEDED"}, restarted)
    finally:
        redis.close()
    with Session(h.engine) as db:
        job = db.get(Job, uuid.UUID(job_id))
        assert job.attempt == 2
        assert job.lease_token == 2
        assert db.get(Build, uuid.UUID(build_id)).current_analysis_id == job.analysis_revision_id
