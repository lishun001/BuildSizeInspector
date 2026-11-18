"""可对外公开的稳定错误码；不保存原始输入或凭据。"""

from typing import Any


class BuildSizeError(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
