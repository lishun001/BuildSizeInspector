import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from buildsize.auth.security import digest, hash_password, opaque_token
from buildsize.config import Config
from buildsize.db.models import ApiToken, LoginBucket, Membership, Project, User, WebSession
from buildsize.domain.errors import BuildSizeError

ALLOWED_SCOPES = {"builds:read", "builds:submit", "distribution:write"}


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    scopes: frozenset[str] = frozenset()
    session_token: str | None = field(default=None, repr=False)


def create_user(db: Session, username: str, password: str) -> User:
    if not username or len(username) > 128:
        raise ValueError("用户名长度必须为 1–128 字符")
    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    db.flush()
    return user


def issue_token(
    db: Session, config: Config, project: Project, scopes: list[str], lifetime_seconds: int
) -> tuple[ApiToken, str]:
    if not scopes or not set(scopes) <= ALLOWED_SCOPES or lifetime_seconds <= 0:
        raise ValueError("令牌 scope 或有效期无效")
    raw = "bsi_" + opaque_token()
    token = ApiToken(
        project_id=project.id,
        token_hash=digest(config.token_hash_pepper, "ci", raw),
        prefix=raw[:12],
        scopes=sorted(set(scopes)),
        expires_at=int(time.time()) + lifetime_seconds,
    )
    db.add(token)
    db.flush()
    return token, raw


def authenticate_bearer(db: Session, config: Config, raw: str) -> Principal:
    token = db.scalar(
        select(ApiToken).where(
            ApiToken.token_hash == digest(config.token_hash_pepper, "ci", raw),
            ApiToken.revoked_at.is_(None),
            ApiToken.expires_at > int(time.time()),
        )
    )
    if token is None:
        raise BuildSizeError("UNAUTHENTICATED", "未登录或令牌无效")
    return Principal(project_id=token.project_id, scopes=frozenset(token.scopes))


def authenticate_session(db: Session, config: Config, raw: str) -> Principal:
    session = db.scalar(
        select(WebSession).where(
            WebSession.token_hash == digest(config.session_secret, "session", raw),
            WebSession.expires_at > int(time.time()),
        )
    )
    if session is None:
        raise BuildSizeError("UNAUTHENTICATED", "未登录或会话已失效")
    return Principal(user_id=session.user_id, session_token=raw)


def project_access(
    db: Session, principal: Principal, key: str, *, scope: str = "builds:read", admin: bool = False
) -> Project:
    query = select(Project).where(Project.key == key)
    if principal.user_id:
        query = query.join(Membership, Membership.project_id == Project.id).where(
            Membership.user_id == principal.user_id,
        )
        if admin:
            query = query.where(Membership.role == "admin")
    elif not admin and scope in principal.scopes:
        query = query.where(Project.id == principal.project_id)
    else:
        raise BuildSizeError("NOT_FOUND", "资源不存在或无权访问")
    project = db.scalar(query)
    if project is None:
        raise BuildSizeError("NOT_FOUND", "资源不存在或无权访问")
    return project


def count_login(db: Session, config: Config, peer: str) -> None:
    now = int(time.time())
    window = now // 60 * 60
    peer_hash = digest(config.session_secret, "login-peer", peer)
    dialect = db.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    statement = insert(LoginBucket).values(
        peer_hash=peer_hash,
        window_start=window,
        attempts=1,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[LoginBucket.peer_hash],
        set_={
            "attempts": LoginBucket.attempts + 1,
        },
        where=LoginBucket.window_start == window,
    )
    db.execute(statement)
    # 旧窗口只在其尚未被别的请求更新时重置；该事务持有同一行锁。
    bucket = db.scalar(
        select(LoginBucket).where(LoginBucket.peer_hash == peer_hash).with_for_update()
    )
    assert bucket is not None
    if bucket.window_start != window:
        bucket.window_start, bucket.attempts = window, 1
    attempts = bucket.attempts
    db.commit()
    if attempts > config.login_limit_per_minute:
        raise BuildSizeError("LOGIN_RATE_LIMIT", "登录请求过于频繁，请稍后重试")
