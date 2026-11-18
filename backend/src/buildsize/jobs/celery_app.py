"""Celery 仅传递 job UUID，任务事实来源始终是 PostgreSQL。"""

import argparse
import os
from typing import Any

from celery import Celery  # type: ignore[import-untyped]

from buildsize.config import Config
from buildsize.logging_config import configure_logging


def create_celery(config: Config, queue: str = "buildsize") -> Any:
    app = Celery("buildsize", broker=config.redis_url, include=["buildsize.jobs.tasks"])
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_default_queue=queue,
        task_ignore_result=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_publish_retry=False,
        broker_connection_timeout=3,
        broker_transport_options={
            "visibility_timeout": config.analysis_timeout_seconds + 120,
            "socket_connect_timeout": 3,
            "socket_timeout": 3,
        },
        task_soft_time_limit=config.analysis_timeout_seconds + 30,
        task_time_limit=config.analysis_timeout_seconds + 45,
    )

    return app


def main() -> None:
    argparse.ArgumentParser(description="BuildSize Celery Worker；P0 固定并发 1").parse_args()
    if os.getenv("WORKER_CONCURRENCY", "1") != "1":
        raise ValueError("P0 Worker 并发必须为 1")
    configure_logging()
    app = create_celery(Config.from_env())
    app.worker_main(["worker", "--loglevel=INFO", "--concurrency=1", "--pool=prefork"])
