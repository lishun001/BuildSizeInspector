"""本机运维入口；所有凭据由环境获取，清理必须先生成再复核同一计划。"""

import argparse
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from buildsize.client.files import write_json
from buildsize.config import Config
from buildsize.db.models import ArtifactBlob, Build, Entity, Job, Project
from buildsize.db.session import make_engine
from buildsize.domain.errors import BuildSizeError
from buildsize.domain.validation import read_json
from buildsize.services.retention import maintenance, temporary_maintenance


def main() -> int:
    parser = argparse.ArgumentParser(description="BuildSize 运维：默认只读计划，不自动删除")
    commands = parser.add_subparsers(dest="command", required=True)
    cleanup = commands.add_parser("retention")
    cleanup.add_argument("--project", required=True)
    cleanup.add_argument("--plan", type=Path, help="应用此前 dry-run 输出文件，重新核对引用")
    cleanup.add_argument("--output", type=Path)
    temps = commands.add_parser("temps")
    temps.add_argument("--plan", type=Path)
    temps.add_argument("--output", type=Path)
    commands.add_parser("diagnostics")
    args = parser.parse_args()
    try:
        config = Config.from_env()
        engine = make_engine(config)
        try:
            if args.command in {"retention", "temps"}:
                approved = read_json(args.plan, 16 * 1024 * 1024) if args.plan else None
                if approved:
                    approved.pop("mode", None)
                value = (
                    maintenance(engine, config, args.project, approved)
                    if args.command == "retention"
                    else temporary_maintenance(engine, config, approved)
                )
                write_json(value, args.output)
            else:
                with Session(engine) as db:
                    from sqlalchemy import func

                    def count(model: type[Entity]) -> int:
                        return db.scalar(select(func.count()).select_from(model)) or 0

                    value = {
                        "projects": count(Project),
                        "builds": count(Build),
                        "jobs": count(Job),
                        "blob_count": count(ArtifactBlob),
                        "registered_blob_bytes": int(
                            db.scalar(
                                select(func.sum(ArtifactBlob.bytes)).where(
                                    ArtifactBlob.deleted_at.is_(None)
                                )
                            )
                            or 0
                        ),
                        "retention_days": {
                            "apk": config.apk_retention_days,
                            "reports": config.report_retention_days,
                            "metrics": config.metrics_retention_days,
                        },
                        "raw_protection": (
                            "fixed baseline + pin + all current revisions + active jobs/leases"
                        ),
                        "automatic_cleanup": False,
                        "android_tools": "not_configured",
                    }
                    write_json(value, None)
        finally:
            engine.dispose()
        return 0
    except (BuildSizeError, ValueError, KeyError, TypeError, OSError, SQLAlchemyError):
        print(
            '{"error":"OPS_FAILED","message":"运维操作失败；请检查配置、迁移或重新生成清理计划"}',
            file=sys.stderr,
        )
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
