"""M0 服务：真实健康检查、会话与项目授权；业务上传在 M2 添加。"""

import hmac
import json
import logging
import time
import uuid
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, delete, false, select, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from buildsize.api.builds import router as build_router
from buildsize.api.ci import router as ci_router
from buildsize.api.dependencies import DB, Auth, check_origin
from buildsize.api.limits import BoundedJsonMiddleware
from buildsize.api.rules import router as rules_router
from buildsize.api.unity import router as unity_router
from buildsize.auth.security import PASSWORDS, digest, opaque_token, verify_password
from buildsize.auth.service import (
    count_login,
    project_access,
)
from buildsize.config import Config
from buildsize.db.models import AuditEvent, Membership, Project, User, WebSession
from buildsize.db.session import make_engine
from buildsize.domain.errors import BuildSizeError
from buildsize.logging_config import configure_logging
from buildsize.services.common import now

LOGGER = logging.getLogger("buildsize.api")
COOKIE = "buildsize_session"
ERROR_STATUS = {
    "UNAUTHENTICATED": 401,
    "NOT_FOUND": 404,
    "CSRF_REJECTED": 403,
    "LOGIN_RATE_LIMIT": 429,
    "SERVICE_UNAVAILABLE": 503,
    "BUILD_IDENTITY_CONFLICT": 409,
    "ARTIFACTS_INCOMPLETE": 409,
    "UPLOAD_STATE_CONFLICT": 409,
    "UPLOAD_QUOTA_EXCEEDED": 429,
    "UPLOAD_TOO_LARGE": 413,
    "APK_SIZE_LIMIT": 413,
    "CONTENT_LENGTH_REQUIRED": 411,
    "CONTENT_TYPE_INVALID": 415,
    "UPLOAD_TIMEOUT": 408,
    "UPLOAD_INTERRUPTED": 400,
    "BLOB_UNAVAILABLE": 410,
    "ANALYSIS_NOT_AVAILABLE": 409,
    "DISTRIBUTION_CONFLICT": 409,
    "VERSION_CONFLICT": 409,
}


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    username: Annotated[str, Field(min_length=1, max_length=128)]
    password: Annotated[str, Field(min_length=1, max_length=256)]


def create_app(config: Config | None = None, engine: Engine | None = None) -> FastAPI:
    configure_logging()
    config = config or Config.from_env()
    engine = engine or make_engine(config)
    app = FastAPI(title="BuildSize Inspector", version="0.1.0")
    app.state.engine = engine
    app.state.config = config
    # 未知用户名也执行相同成本的密码校验。
    dummy_hash = PASSWORDS.hash(opaque_token())

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.monotonic()
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        LOGGER.info(
            json.dumps(
                {
                    "request_id": request_id,
                    "timestamp": now().isoformat(),
                    "stage": "http",
                    "method": request.method,
                    "status": response.status_code,
                    "duration_ms": round((time.monotonic() - start) * 1000),
                },
                separators=(",", ":"),
            )
        )
        return response

    def error_response(request: Request, code: str, message: str, status: int) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": code,
                    "message": message,
                    "request_id": request.state.request_id,
                    "details": {},
                }
            },
            status_code=status,
        )

    @app.exception_handler(BuildSizeError)
    async def domain_error(request: Request, error: BuildSizeError) -> JSONResponse:
        return error_response(request, error.code, error.message, ERROR_STATUS.get(error.code, 422))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        # FastAPI 默认错误中的 input 可能包含密码，不能返回或记录。
        return error_response(request, "INVALID_REQUEST", "请求字段不符合协议", 422)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        code = "NOT_FOUND" if error.status_code == 404 else "HTTP_ERROR"
        return error_response(request, code, "请求的接口或方法不可用", error.status_code)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        return error_response(request, "SERVICE_UNAVAILABLE", "数据库暂不可用", 503)

    @app.exception_handler(OSError)
    async def storage_error(request: Request, error: OSError) -> JSONResponse:
        return error_response(request, "STORAGE_IO_ERROR", "文件存储暂不可用", 503)

    @app.get("/api/v1/health/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/api/v1/health/ready")
    def ready(db: DB) -> dict[str, str]:
        version = db.scalar(text("SELECT version_num FROM alembic_version"))
        if version != "0006_lifecycle":
            raise BuildSizeError("SERVICE_UNAVAILABLE", "数据库迁移未就绪")
        db.execute(select(Project.id).limit(1))
        return {"status": "ready"}

    @app.post("/api/v1/auth/login")
    def login(body: LoginBody, request: Request, response: Response, db: DB) -> dict[str, Any]:
        check_origin(request, config)
        count_login(db, config, request.client.host if request.client else "unknown")
        user = db.scalar(select(User).where(User.username == body.username))
        valid = verify_password(user.password_hash if user else dummy_hash, body.password)
        if not valid or user is None:
            raise BuildSizeError("UNAUTHENTICATED", "用户名或密码错误")
        if PASSWORDS.check_needs_rehash(user.password_hash):
            user.password_hash = PASSWORDS.hash(body.password)
        raw = opaque_token()
        db.add(
            WebSession(
                user_id=user.id,
                token_hash=digest(config.session_secret, "session", raw),
                expires_at=int(time.time()) + config.session_lifetime_seconds,
            )
        )
        db.add(
            AuditEvent(
                actor=str(user.id),
                action="auth.login",
                object_id=str(user.id),
                request_id=request.state.request_id,
            )
        )
        db.commit()
        response.set_cookie(
            COOKIE,
            raw,
            httponly=True,
            secure=config.secure_cookies,
            samesite="strict",
            max_age=config.session_lifetime_seconds,
            path="/api",
        )
        return {"username": user.username, "csrf_token": digest(config.session_secret, "csrf", raw)}

    @app.get("/api/v1/auth/me")
    def me(actor: Auth, db: DB) -> dict[str, Any]:
        if actor.user_id is None:
            raise BuildSizeError("UNAUTHENTICATED", "此接口需要 Web 会话")
        user = db.get(User, actor.user_id)
        assert user is not None and actor.session_token is not None
        return {
            "username": user.username,
            "csrf_token": digest(config.session_secret, "csrf", actor.session_token),
        }

    @app.post("/api/v1/auth/logout")
    def logout(actor: Auth, request: Request, response: Response, db: DB) -> dict[str, str]:
        check_origin(request, config)
        if actor.session_token is None:
            raise BuildSizeError("UNAUTHENTICATED", "此接口需要 Web 会话")
        expected = digest(config.session_secret, "csrf", actor.session_token)
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), expected):
            raise BuildSizeError("CSRF_REJECTED", "CSRF 校验失败")
        db.execute(
            delete(WebSession).where(
                WebSession.token_hash
                == digest(config.session_secret, "session", actor.session_token),
            )
        )
        db.add(
            AuditEvent(
                actor=str(actor.user_id),
                action="auth.logout",
                object_id=str(actor.user_id),
                request_id=request.state.request_id,
            )
        )
        db.commit()
        response.delete_cookie(
            COOKIE, path="/api", secure=config.secure_cookies, httponly=True, samesite="strict"
        )
        return {"status": "logged_out"}

    @app.get("/api/v1/projects")
    def projects(
        actor: Auth, db: DB, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
    ) -> dict[str, Any]:
        query = select(Project)
        if actor.user_id:
            query = query.join(Membership, Membership.project_id == Project.id).where(
                Membership.user_id == actor.user_id,
            )
        elif "builds:read" in actor.scopes:
            query = query.where(Project.id == actor.project_id)
        else:
            query = query.where(false())
        rows = db.scalars(
            query.order_by(Project.key, Project.id).offset(offset).limit(limit + 1)
        ).all()
        return {
            "items": [{"key": row.key, "name": row.name} for row in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    @app.get("/api/v1/projects/{key}")
    def project(key: str, actor: Auth, db: DB) -> dict[str, Any]:
        row = project_access(db, actor, key)
        role = (
            db.scalar(
                select(Membership.role).where(
                    Membership.project_id == row.id, Membership.user_id == actor.user_id
                )
            )
            if actor.user_id
            else "ci"
        )
        return {
            "key": row.key,
            "name": row.name,
            "role": role or "viewer",
            "environment": config.app_env,
            "retention_days": {
                "apk": config.apk_retention_days,
                "reports": config.report_retention_days,
                "metrics": config.metrics_retention_days,
            },
            "raw_protection": (
                "所有 current 修订、固定基线、pin 和活跃任务/上传受保护；到期并不保证删除。"
            ),
        }

    app.include_router(build_router)
    app.include_router(unity_router)
    app.include_router(ci_router)
    app.include_router(rules_router)
    app.add_middleware(BoundedJsonMiddleware)
    return app
