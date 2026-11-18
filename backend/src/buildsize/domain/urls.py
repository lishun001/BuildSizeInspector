"""链接安全语义；不访问 URL，不保存凭据参数。"""

from urllib.parse import parse_qsl, urlsplit

from buildsize.domain.errors import BuildSizeError


def validate_public_url(value: str, allowed_hosts: tuple[str, ...] | None = None) -> None:
    try:
        url = urlsplit(value)
        forbidden = (
            "token",
            "signature",
            "password",
            "secret",
            "apikey",
            "api_key",
            "authorization",
            "credential",
        )
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or (allowed_hosts is not None and url.hostname.lower() not in allowed_hosts)
            or any(
                word in key.lower().replace("-", "_")
                for key, _ in parse_qsl(url.query)
                for word in forbidden
            )
            or any(word in url.fragment.lower() for word in forbidden)
        ):
            raise ValueError
        _ = url.port
    except ValueError:
        raise BuildSizeError(
            "EXTERNAL_LINK_INVALID", "外部链接必须使用可信 HTTP(S) 域名且不含凭据"
        ) from None
