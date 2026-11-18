"""模块级任务注册，使 prefork/spawn 子进程都能重新导入任务。"""

import uuid

from celery import shared_task  # type: ignore[import-untyped]

from buildsize.config import Config
from buildsize.db.session import make_engine
from buildsize.jobs.worker import execute_job
from buildsize.logging_config import configure_logging


def execute(job_id: str) -> str:
    configure_logging()
    config = Config.from_env()
    engine = make_engine(config)
    try:
        return execute_job(engine, config, uuid.UUID(job_id))
    finally:
        engine.dispose()


shared_task(name="buildsize.execute", acks_late=True, reject_on_worker_lost=True)(execute)
