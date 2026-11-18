"""流式上传、总期限和有限重试；不跟随重定向，不输出服务器原始错误。"""

import http.client
import json
import math
import os
import random
import ssl
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlencode, urlsplit

from buildsize.client.files import ClientError
from buildsize.domain.errors import BuildSizeError
from buildsize.domain.validation import parse_json


class ApiClient:
    def __init__(
        self, api_url: str | None = None, token: str | None = None, timeout: float = 900
    ) -> None:
        value: str = str(api_url or os.environ.get("BUILDSIZE_API_URL", ""))
        self._token: str = str(
            token if token is not None else os.environ.get("BUILDSIZE_API_TOKEN", "")
        )
        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or any(ord(c) < 32 for c in value)
                or not self._token
                or any(ord(c) < 33 or ord(c) > 126 for c in self._token)
            ):
                raise ValueError
            if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError
            _ = parsed.port
        except ValueError:
            raise ClientError(
                "API_CONFIG_INVALID", "需要无凭据的 HTTPS API 地址和环境令牌；HTTP 仅允许本机", 3
            ) from None
        self.url = value.rstrip("/")
        self.parsed = parsed
        self.prefix = parsed.path.rstrip("/")
        if not self.prefix.endswith("/api/v1"):
            self.prefix += "/api/v1"
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 86400:
            raise ClientError("CLIENT_TIMEOUT_INVALID", "请求总期限必须在 0–86400 秒之间")
        self.deadline = time.monotonic() + timeout

    def remaining(self) -> float:
        value = self.deadline - time.monotonic()
        if value <= 0:
            raise ClientError("CLIENT_DEADLINE", "操作超过总期限", 4)
        return value

    def _sleep(self, retry_after: str | None, attempt: int) -> None:
        delay = min(5.0, 0.25 * 2**attempt + random.random() * 0.25)
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                try:
                    delay = parsedate_to_datetime(retry_after).timestamp() - time.time()
                except (ValueError, TypeError, OverflowError):
                    pass
        delay = min(max(0.0, delay), 30.0)
        if delay >= self.remaining():
            raise ClientError("CLIENT_DEADLINE", "服务重试等待超过剩余总期限", 4)
        until = time.monotonic() + delay
        while time.monotonic() < until:
            time.sleep(max(0, min(0.2, until - time.monotonic())))
            self.remaining()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        file: Path | None = None,
        headers: dict[str, str] | None = None,
        query: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        target = self.prefix + path
        if query:
            target += "?" + urlencode(query, doseq=True)
        payload = (
            json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
            if body is not None
            else None
        )
        for attempt in range(3):
            remaining = self.remaining()
            cls = (
                http.client.HTTPSConnection
                if self.parsed.scheme == "https"
                else http.client.HTTPConnection
            )
            connection = cls(
                self.parsed.hostname or "",
                self.parsed.port,
                timeout=min(30.0, remaining),
                blocksize=65536,
            )
            source: BinaryIO | None = None
            try:
                request_headers = {
                    "Authorization": "Bearer " + self._token,
                    "Accept": "application/json",
                    **(headers or {}),
                }
                if payload is not None:
                    request_headers["Content-Type"] = "application/json"
                    request_headers["Content-Length"] = str(len(payload))
                connection.putrequest(method, target)
                if file is not None:
                    source = file.open("rb")
                    request_headers.setdefault("Content-Type", "application/octet-stream")
                    request_headers.setdefault(
                        "Content-Length", str(os.fstat(source.fileno()).st_size)
                    )
                for key, value in request_headers.items():
                    connection.putheader(key, value)
                connection.endheaders()
                if source is not None:
                    expected = int(request_headers["Content-Length"])
                    if os.fstat(source.fileno()).st_size != expected:
                        raise ClientError("LOCAL_INPUT_CHANGED", "上传文件长度与冻结清单不同", 5)
                    unsent = expected
                    while unsent:
                        chunk = source.read(min(65536, unsent))
                        if not chunk:
                            raise ClientError("LOCAL_INPUT_CHANGED", "上传文件被截断", 5)
                        if connection.sock:
                            connection.sock.settimeout(min(30.0, self.remaining()))
                        connection.send(chunk)
                        unsent -= len(chunk)
                    if source.read(1):
                        raise ClientError("LOCAL_INPUT_CHANGED", "上传文件在传输期间增长", 5)
                elif payload is not None:
                    connection.send(payload)
                if connection.sock:
                    connection.sock.settimeout(min(30.0, self.remaining()))
                response = connection.getresponse()
                status, retry_after = response.status, response.getheader("Retry-After")
                raw = bytearray()
                while True:
                    if connection.sock:
                        connection.sock.settimeout(min(30.0, self.remaining()))
                    chunk = response.read1(65536)
                    if not chunk:
                        break
                    self.remaining()
                    raw.extend(chunk)
                    if len(raw) > 4 * 1024**2:
                        raise ClientError("RESPONSE_LIMIT", "API 响应超过预算", 5)
                if status in {408, 429} or status >= 500:
                    if attempt < 2:
                        self._sleep(retry_after, attempt)
                        continue
                    raise ClientError("SERVICE_UNAVAILABLE", "API 暂不可用，已达到重试上限", 4)
                if status not in {200, 201, 202}:
                    exit_code = (
                        3
                        if status in {301, 302, 303, 307, 308, 401, 403, 404}
                        else 5
                        if status in {409, 422}
                        else 2
                        if status == 413
                        else 4
                    )
                    raise ClientError(
                        f"HTTP_{status}", "API 请求未被接受；未跟随重定向或盲目重试", exit_code
                    )
                self.remaining()
                try:
                    return status, parse_json(bytes(raw)) if raw else {}
                except BuildSizeError:
                    raise ClientError("RESPONSE_INVALID", "API 响应不是有效协议 JSON", 5) from None
            except ssl.SSLError:
                raise ClientError(
                    "TLS_VALIDATION_FAILED", "TLS 验证失败；未关闭证书校验", 3
                ) from None
            except (OSError, http.client.HTTPException):
                if attempt >= 2:
                    raise ClientError("NETWORK_FAILED", "网络请求失败，已达到重试上限", 4) from None
                self._sleep(None, attempt)
            finally:
                if source:
                    source.close()
                connection.close()
        raise ClientError("NETWORK_FAILED", "网络请求未完成", 4)
