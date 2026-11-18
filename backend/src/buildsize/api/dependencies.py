"""各路由共享的会话、项目身份与 CSRF 检查。"""

import hmac
from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from buildsize.auth.security import digest
from buildsize.auth.service import Principal, authenticate_bearer, authenticate_session
from buildsize.config import Config
from buildsize.domain.errors import BuildSizeError


def database(request: Request) -> Generator[Session, None, None]:
    with Session(request.app.state.engine) as db:
        yield db


DB = Annotated[Session, Depends(database)]


def principal(request: Request, db: DB) -> Principal:
    config: Config = request.app.state.config
    authorization = request.headers.get("authorization")
    if authorization:
        if not authorization.startswith("Bearer ") or len(authorization) > 1024:
            raise BuildSizeError("UNAUTHENTICATED", "令牌无效")
        return authenticate_bearer(db, config, authorization[7:])
    raw = request.cookies.get("buildsize_session")
    if not raw or len(raw) > 256:
        raise BuildSizeError("UNAUTHENTICATED", "请先登录")
    return authenticate_session(db, config, raw)


Auth = Annotated[Principal, Depends(principal)]


def check_origin(request: Request, config: Config) -> None:
    if request.headers.get("origin") != config.origin:
        raise BuildSizeError("CSRF_REJECTED", "请求来源不符合配置")


def mutation(request: Request, actor: Principal) -> None:
    if actor.session_token is None:
        return
    config: Config = request.app.state.config
    check_origin(request, config)
    expected = digest(config.session_secret, "csrf", actor.session_token)
    if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), expected):
        raise BuildSizeError("CSRF_REJECTED", "CSRF 校验失败")
