"""持久 outbox 投递与消息丢失恢复。同一个 dispatcher 可安全重复运行。"""

import argparse
import json
import logging
import time
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import Engine, and_, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from buildsize.config import Config
from buildsize.db.models import Job, Outbox
from buildsize.db.session import make_engine
from buildsize.domain.errors import BuildSizeError
from buildsize.jobs.celery_app import create_celery
from buildsize.logging_config import configure_logging
from buildsize.services.common import now


def dispatch_once(
    engine: Engine, config: Config, publish: Callable[[str], None], limit: int = 50
) -> int:
    count = 0
    failed = 0
    with Session(engine) as db, db.begin():
        records = db.scalars(
            select(Outbox)
            .join(Job, Job.id == Outbox.job_id)
            .where(
                or_(
                    Job.state == "QUEUED",
                    and_(Job.state == "RUNNING", Job.lease_expires_at <= now()),
                ),
                or_(
                    Outbox.sent_at.is_(None),
                    Outbox.sent_at < now() - timedelta(seconds=config.redelivery_seconds),
                ),
                or_(Outbox.next_attempt_at.is_(None), Outbox.next_attempt_at <= now()),
            )
            .order_by(Outbox.created_at, Outbox.id)
            .limit(limit)
            .with_for_update(skip_locked=True, of=Outbox)
        )
        for record in records:
            record.attempts += 1
            try:
                publish(str(record.job_id))
            except Exception:
                # broker 的异常文本可能含连接凭据；仅持久化重试时间。
                record.next_attempt_at = now() + timedelta(
                    seconds=min(60, 2 ** min(record.attempts, 6))
                )
                failed += 1
                continue
            record.sent_at, record.next_attempt_at = now(), None
            count += 1
    if failed:
        raise BuildSizeError(
            "BROKER_UNAVAILABLE",
            "消息投递失败，已保留 outbox 等待重试",
            published=count,
            failed=failed,
        )
    return count


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="数据库 outbox 投递和过期 lease 恢复")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = Config.from_env()
    engine = make_engine(config)
    app = create_celery(config)

    def publish(job_id: str) -> None:
        app.send_task("buildsize.execute", args=[job_id], retry=False)

    try:
        while True:
            try:
                count = dispatch_once(engine, config, publish)
            except (BuildSizeError, SQLAlchemyError):
                logging.getLogger("buildsize.jobs").warning(
                    json.dumps(
                        {
                            "stage": "outbox",
                            "error_code": "DISPATCH_FAILED",
                            "event": "retry_scheduled",
                        }
                    )
                )
                if args.once:
                    return 4
                time.sleep(2)
                continue
            if args.once:
                print(json.dumps({"published": count}))
                return 0
            time.sleep(2)
    finally:
        engine.dispose()
