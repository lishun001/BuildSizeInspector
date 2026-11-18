"""先限制 JSON 实际字节，再进入框架反序列化。大文件 PUT 由流式上传预算限制。"""

import uuid

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from buildsize.domain.errors import BuildSizeError
from buildsize.domain.validation import parse_json


class BoundedJsonMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        parts = scope["path"].strip("/").split("/")
        if (
            scope["method"] == "PUT"
            and len(parts) == 6
            and parts[:3] == ["api", "v1", "builds"]
            and parts[4] == "artifacts"
        ):
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > 1024 * 1024:
                await self.reject(scope, receive, send, "JSON_LIMIT_EXCEEDED", 413)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        if body:
            try:
                parse_json(bytes(body))
            except BuildSizeError:
                await self.reject(scope, receive, send, "INVALID_JSON", 422)
                return
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)

    @staticmethod
    async def reject(scope: Scope, receive: Receive, send: Send, code: str, status: int) -> None:
        request_id = str(uuid.uuid4())
        response = JSONResponse(
            {
                "error": {
                    "code": code,
                    "message": "JSON 请求无效或超过限制",
                    "request_id": request_id,
                    "details": {},
                }
            },
            status_code=status,
            headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
        )
        await response(scope, receive, send)
