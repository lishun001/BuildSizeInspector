"""本机管理员引导入口。密码不接受命令行参数，令牌仅在创建时显示一次。"""

import argparse
import getpass
import json
import sys
import time
import uuid

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from buildsize.auth.service import ALLOWED_SCOPES, create_user, issue_token
from buildsize.config import Config
from buildsize.db.models import ApiToken, AuditEvent, Membership, Project, User
from buildsize.db.session import make_engine
from buildsize.domain.contracts import ProjectKey


def main() -> int:
    parser = argparse.ArgumentParser(description="BuildSize 本地管理员工具")
    commands = parser.add_subparsers(dest="command", required=True)
    user = commands.add_parser("create-user", help="交互输入密码创建用户")
    user.add_argument("username")
    project = commands.add_parser("create-project", help="创建项目")
    project.add_argument("key")
    project.add_argument("--name", required=True)
    grant = commands.add_parser("grant", help="授予项目角色")
    grant.add_argument("username")
    grant.add_argument("project")
    grant.add_argument("--role", choices=["admin", "viewer"], required=True)
    token = commands.add_parser("create-token", help="创建项目 CI token，stdout 只显示一次")
    token.add_argument("project")
    token.add_argument("--scope", action="append", choices=sorted(ALLOWED_SCOPES), required=True)
    token.add_argument("--days", type=int, default=30)
    revoke = commands.add_parser("revoke-token", help="按 token UUID 撤销")
    revoke.add_argument("token_id", type=uuid.UUID)
    args = parser.parse_args()
    try:
        config = Config.from_env()
        engine = make_engine(config)
        with Session(engine) as db:
            raw_token = None
            if args.command == "create-user":
                password = getpass.getpass("密码（至少 12 字符）：")
                if password != getpass.getpass("再次输入密码："):
                    raise ValueError("两次密码不一致")
                created_user = create_user(db, args.username, password)
                object_id, project_id = str(created_user.id), None
            elif args.command == "create-project":
                key = TypeAdapter(ProjectKey).validate_python(args.key)
                if not 1 <= len(args.name) <= 200:
                    raise ValueError("项目名称长度必须为 1–200 字符")
                created_project = Project(key=key, name=args.name)
                db.add(created_project)
                db.flush()
                object_id, project_id = str(created_project.id), created_project.id
            elif args.command == "revoke-token":
                revoked_token = db.get(ApiToken, args.token_id)
                if revoked_token is None:
                    raise ValueError("令牌不存在")
                revoked_token.revoked_at = int(time.time())
                object_id, project_id = str(revoked_token.id), revoked_token.project_id
            else:
                project = db.scalar(select(Project).where(Project.key == args.project))
                if project is None:
                    raise ValueError("项目不存在")
                project_id = project.id
                if args.command == "grant":
                    user = db.scalar(select(User).where(User.username == args.username))
                    if user is None:
                        raise ValueError("用户不存在")
                    membership = db.scalar(
                        select(Membership).where(
                            Membership.user_id == user.id,
                            Membership.project_id == project.id,
                        )
                    )
                    if membership is None:
                        membership = Membership(
                            user_id=user.id, project_id=project.id, role=args.role
                        )
                        db.add(membership)
                    else:
                        membership.role = args.role
                    db.flush()
                    object_id = str(membership.id)
                else:
                    created_token, raw_token = issue_token(
                        db, config, project, args.scope, args.days * 86400
                    )
                    object_id = str(created_token.id)
            db.add(
                AuditEvent(
                    project_id=project_id,
                    actor="local-admin",
                    action=args.command,
                    object_id=object_id,
                )
            )
            db.commit()
            result = {"id": object_id, "status": "created" if raw_token else "ok"}
            if raw_token:
                result["token"] = raw_token
            print(json.dumps(result, ensure_ascii=False))
        engine.dispose()
        return 0
    except IntegrityError:
        print("对象已存在或违反唯一约束", file=sys.stderr)
        return 2
    except (ValueError, ValidationError) as exc:
        # 不回显 ValidationError：其中可能携带输入。
        print("输入或配置无效" if isinstance(exc, ValidationError) else str(exc), file=sys.stderr)
        return 2
    except SQLAlchemyError:
        print("数据库操作失败；请检查迁移与连接配置", file=sys.stderr)
        return 4
