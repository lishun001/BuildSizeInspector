"""启动配置：生产仅允许 PostgreSQL，不提供默认凭据。"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    database_url: str = field(repr=False)
    public_base_url: str
    session_secret: str = field(repr=False)
    token_hash_pepper: str = field(repr=False)
    app_env: str = "development"
    session_lifetime_seconds: int = 8 * 60 * 60
    login_limit_per_minute: int = 10
    blob_root: Path = Path(".buildsize/blobs")
    redis_url: str = field(default="redis://127.0.0.1:6379/0", repr=False)
    upload_max_bytes: int = 2 * 1024**3
    upload_timeout_seconds: int = 900
    analysis_timeout_seconds: int = 1200
    redelivery_seconds: int = 30
    trusted_link_hosts: tuple[str, ...] = ()
    apk_download_hosts: tuple[str, ...] = ()
    apk_retention_days: int = 30
    report_retention_days: int = 180
    metrics_retention_days: int = 365

    def __post_init__(self) -> None:
        if len(self.session_secret) < 32 or len(self.token_hash_pepper) < 32:
            raise ValueError("SESSION_SECRET 和 TOKEN_HASH_PEPPER 必须分别配置至少 32 字符")
        if self.session_secret == self.token_hash_pepper:
            raise ValueError("会话密钥与令牌 pepper 必须不同")
        if self.app_env != "test" and not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("服务数据库必须使用 PostgreSQL / psycopg")
        url = urlsplit(self.public_base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("PUBLIC_BASE_URL 必须是无路径和凭据的 HTTP(S) origin")
        if self.app_env == "production" and url.scheme != "https":
            raise ValueError("生产 PUBLIC_BASE_URL 必须使用 HTTPS")
        if url.scheme == "http" and url.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
            "testserver",
        }:
            raise ValueError("HTTP 开发服务只允许本机 origin")
        if self.session_lifetime_seconds <= 0 or self.login_limit_per_minute <= 0:
            raise ValueError("会话期限和登录限流必须为正数")
        if (
            min(self.apk_retention_days, self.report_retention_days, self.metrics_retention_days)
            <= 0
        ):
            raise ValueError("保留天数必须为正整数")
        if (
            min(
                self.upload_max_bytes,
                self.upload_timeout_seconds,
                self.analysis_timeout_seconds,
                self.redelivery_seconds,
            )
            <= 0
        ):
            raise ValueError("文件与任务资源限制必须为正数")

    @property
    def origin(self) -> str:
        return self.public_base_url.rstrip("/")

    @property
    def secure_cookies(self) -> bool:
        return self.public_base_url.startswith("https://")

    @classmethod
    def from_env(cls) -> "Config":
        required = ["DATABASE_URL", "PUBLIC_BASE_URL", "SESSION_SECRET", "TOKEN_HASH_PEPPER"]
        if any(not os.environ.get(key) for key in required):
            raise ValueError(
                "缺少必填配置：DATABASE_URL/PUBLIC_BASE_URL/SESSION_SECRET/TOKEN_HASH_PEPPER"
            )
        return cls(
            database_url=os.environ["DATABASE_URL"],
            public_base_url=os.environ["PUBLIC_BASE_URL"],
            session_secret=os.environ["SESSION_SECRET"],
            token_hash_pepper=os.environ["TOKEN_HASH_PEPPER"],
            app_env=os.getenv("APP_ENV", "development"),
            blob_root=Path(os.getenv("BLOB_ROOT", ".buildsize/blobs")),
            redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
            upload_max_bytes=int(os.getenv("UPLOAD_MAX_BYTES", str(2 * 1024**3))),
            upload_timeout_seconds=int(os.getenv("UPLOAD_TIMEOUT_SECONDS", "900")),
            analysis_timeout_seconds=int(os.getenv("ANALYSIS_TIMEOUT_SECONDS", "1200")),
            trusted_link_hosts=tuple(
                host.strip().lower()
                for host in os.getenv("TRUSTED_LINK_HOSTS", "").split(",")
                if host.strip()
            ),
            apk_download_hosts=tuple(
                host.strip().lower()
                for host in os.getenv("APK_DOWNLOAD_HOSTS", "").split(",")
                if host.strip()
            ),
            apk_retention_days=int(os.getenv("APK_RETENTION_DAYS", "30")),
            report_retention_days=int(os.getenv("REPORT_RETENTION_DAYS", "180")),
            metrics_retention_days=int(os.getenv("METRICS_RETENTION_DAYS", "365")),
        )
