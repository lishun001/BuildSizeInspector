"""有界 JSON 读取与跨文件身份校验；不依赖 HTTP 或数据库。"""

import json
from pathlib import Path
from typing import Any

from buildsize.domain.contracts import (
    AssetImporters,
    BuildMeta,
    GitChanges,
    Submission,
    UnityReport,
)
from buildsize.domain.errors import BuildSizeError

JSON_LIMIT = 64 * 1024**2


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("JSON 对象键重复")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("JSON 含非有限数字")


def read_json(path: Path, max_bytes: int = JSON_LIMIT) -> dict[str, Any]:
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise BuildSizeError("JSON_LIMIT_EXCEEDED", "JSON 文件超过大小限制")
    return parse_json(data)


def parse_json(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
        if not isinstance(value, dict):
            raise ValueError("JSON 根必须是对象")
        _bounded(value)
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise BuildSizeError("INVALID_JSON", "JSON 格式或结构无效") from exc
    return value


def _bounded(value: Any, depth: int = 0) -> None:
    if depth > 16:
        raise ValueError("JSON 嵌套过深")
    if isinstance(value, dict):
        for key, item in value.items():
            if len(key) > 4096:
                raise ValueError("JSON 键过长")
            _bounded(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _bounded(item, depth + 1)
    elif isinstance(value, str) and len(value) > 32768:
        raise ValueError("JSON 字符串过长")
    elif isinstance(value, float):
        import math

        if not math.isfinite(value):
            raise ValueError("JSON 含非有限数字")


def validate_identity(
    manifest: Submission,
    metadata: BuildMeta,
    reports: list[UnityReport | AssetImporters | GitChanges],
) -> None:
    for field in ("run_id", "project_key", "variant", "ci"):
        if getattr(manifest, field) != getattr(metadata, field):
            raise BuildSizeError("BUILD_IDENTITY_CONFLICT", "元数据与清单身份不一致", field=field)
    for report in reports:
        if report.run_id != manifest.run_id:
            raise BuildSizeError("BUILD_IDENTITY_CONFLICT", "报告 run_id 不一致", field="run_id")
        if isinstance(report, UnityReport | AssetImporters):
            if report.unity_version != metadata.settings.unity_version:
                raise BuildSizeError("REPORT_DATA_INVALID", "报告 Unity 版本不一致")
