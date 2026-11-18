"""保留原始记录，按 GUID/类型或低置信路径聚合，不虚构对象 ID。"""

import hashlib
import json
from collections import Counter
from dataclasses import replace
from typing import Any

from buildsize.domain.apk import ApkSnapshot
from buildsize.domain.contracts import SAFE_INTEGER, AssetImporters, UnityReport
from buildsize.domain.errors import BuildSizeError
from buildsize.domain.unity import AssetGroup, ContainerMapping, UnitySnapshot

PARSER_VERSION = "unity-report-1"
MAPPER_VERSION = "unity-direct-path-1"
# 直接前缀规则须经真实构建核验后才可登记版本；不按 basename 搜索。
VERIFIED_DIRECT_VERSIONS: frozenset[str] = frozenset({"6000.0.76f1"})


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def valid_guid(value: str | None) -> bool:
    return bool(value and value != "0" * 32)


def truncated(warnings: list[str]) -> bool:
    return any(warning.startswith("BUILDSIZE_TRUNCATED:") for warning in warnings)


def mapping(report: UnityReport, apk: ApkSnapshot) -> tuple[ContainerMapping, ...]:
    paths = {row.normalized_path for row in apk.entries if not row.is_directory}
    counts = Counter(row.short_path for row in report.containers)
    result = []
    for container in report.containers:
        path = container.short_path
        candidates = set()
        # 严格保留完整 short_path；拒绝 traversal，不做大小写/Unicode 合并。
        safe = (
            not path.startswith("/")
            and "\\" not in path
            and "\x00" not in path
            and ".." not in path.split("/")
        )
        if safe:
            if path in paths:
                candidates.add(path)
            prefixed = "assets/bin/Data/" + path
            if prefixed in paths:
                candidates.add(prefixed)
        state = "unmapped"
        if counts[path] > 1 or len(candidates) > 1:
            state = "ambiguous"
        elif candidates:
            state = "exact" if report.unity_version in VERIFIED_DIRECT_VERSIONS else "candidate"
        result.append(
            ContainerMapping(
                container.container_index,
                path,
                state,
                tuple(sorted(candidates)),
                MAPPER_VERSION,
                {
                    "rule": "full_short_path_or_data_prefix",
                    "unity_version": report.unity_version,
                    "apk_sha256": apk.apk_sha256,
                    "validated": state == "exact",
                    "validation_reference": "unity-6000.0.76f1-android-mono-2026-11-17"
                    if state == "exact"
                    else None,
                },
            )
        )
    return tuple(result)


def _aggregate(report: UnityReport, *, per_container: bool) -> tuple[AssetGroup, ...]:
    groups: dict[str, AssetGroup] = {}
    counts = Counter(row.short_path for row in report.containers)
    for container in report.containers:
        stable = counts[container.short_path] == 1
        container_key = (
            container.short_path if stable else f"report_container:{container.container_index}"
        )
        for record in container.entries:
            guid = record.source_guid if valid_guid(record.source_guid) else None
            path = record.source_path or None
            kind = "guid" if guid else "path_fallback" if path else "generated"
            identity: Any = guid or path or container_key
            key = hashlib.sha256(
                json.dumps(
                    [
                        container_key if per_container or kind == "generated" else None,
                        kind,
                        identity,
                        record.asset_type,
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            group = AssetGroup(
                key,
                kind,
                guid,
                record.asset_type,
                (path,) if path else (),
                (container.short_path,),
                record.packed_bytes,
                1,
                stable if per_container else kind != "generated" or stable,
            )
            if previous := groups.get(key):
                group = replace(
                    previous,
                    source_paths=tuple(
                        sorted(set(previous.source_paths) | set(group.source_paths))
                    ),
                    container_paths=tuple(
                        sorted(set(previous.container_paths) | {container.short_path})
                    ),
                    packed_bytes=previous.packed_bytes + record.packed_bytes,
                    record_count=previous.record_count + 1,
                )
            if group.packed_bytes > SAFE_INTEGER:
                raise BuildSizeError("INTEGER_OVERFLOW", "Unity 聚合大小超过安全整数范围")
            groups[key] = group
    return tuple(groups[key] for key in sorted(groups))


def _check_settings(value: Any, shape: dict[str, Any], prefix: str = "") -> list[str]:
    if not isinstance(value, dict):
        raise BuildSizeError("IMPORTER_SETTINGS_INVALID", "Importer 设置必须为对象")
    if any(key not in value for key in shape):
        raise BuildSizeError("IMPORTER_SETTINGS_INVALID", "Importer 已定义设置字段缺失")
    unsupported = []
    for key, item in value.items():
        field = prefix + key
        expected = shape.get(key)
        if expected is None:
            unsupported.append(field)
        elif isinstance(expected, dict):
            unsupported.extend(_check_settings(item, expected, field + "."))
        elif expected == "quality":
            if type(item) not in (int, float) or not 0 <= item <= 1:
                raise BuildSizeError("IMPORTER_SETTINGS_INVALID", "音频质量必须在 0–1 之间")
        elif expected == "percent":
            if type(item) is not int or not 0 <= item <= 100:
                raise BuildSizeError("IMPORTER_SETTINGS_INVALID", "纹理压缩质量必须在 0–100 之间")
        elif type(item) is not expected:
            raise BuildSizeError("IMPORTER_SETTINGS_INVALID", "Importer 设置类型不符")
        elif expected is int and not 0 <= item <= SAFE_INTEGER:
            raise BuildSizeError("IMPORTER_SETTINGS_INVALID", "Importer 整数设置无效")
    return unsupported


def parse_importers(
    report: AssetImporters | None,
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    if report is None:
        return (), {
            "state": "missing",
            "requested_count": None,
            "collected_count": None,
            "failed_count": None,
            "ratio": None,
        }
    texture = {
        "texture_type": str,
        "mipmap_enabled": bool,
        "is_readable": bool,
        "srgb": bool,
        "android": {
            "overridden": bool,
            "max_texture_size": int,
            "format": str,
            "compression_quality": "percent",
            "crunched_compression": bool,
        },
    }
    audio_sample = {
        "compression_format": str,
        "quality": "quality",
        "sample_rate_setting": str,
        "sample_rate_override": int,
        "load_type": str,
    }
    audio = {
        "force_to_mono": bool,
        "default": audio_sample,
        "android": {"overridden": bool, **audio_sample},
    }
    rows = []
    errors = 0
    for asset in report.assets:
        value = asset.model_dump(mode="json")
        try:
            shape = (
                texture
                if asset.importer_type == "TextureImporter"
                else audio
                if asset.importer_type == "AudioImporter"
                else {}
            )
            unsupported = _check_settings(asset.settings, shape)
            value["unsupported_fields"] = sorted(set(asset.unsupported_fields) | set(unsupported))
            value["state"] = "complete"
        except BuildSizeError:
            value["state"] = "failed"
            errors += 1
        rows.append(value)
    failed = report.coverage.failed_count + errors
    complete = (
        failed == 0
        and report.coverage.collected_count == report.coverage.requested_count
        and not truncated(report.warnings)
    )
    return tuple(rows), {
        "state": "complete" if complete else "failed",
        "requested_count": report.coverage.requested_count,
        "collected_count": len(rows) - errors,
        "failed_count": failed,
        "ratio": ratio(len(rows) - errors, report.coverage.requested_count),
    }


def parse_unity(
    report: UnityReport, apk: ApkSnapshot, importers: AssetImporters | None = None
) -> UnitySnapshot:
    if importers and (
        importers.run_id != report.run_id or importers.unity_version != report.unity_version
    ):
        raise BuildSizeError("REPORT_DATA_INVALID", "Unity 报告和 Importer 快照不一致")
    groups = _aggregate(report, per_container=False)
    container_groups = _aggregate(report, per_container=True)
    mappings = mapping(report, apk)
    importer_rows, importer_summary = parse_importers(importers)
    records = [record for container in report.containers for record in container.entries]
    total = sum(record.packed_bytes for record in records)
    overhead = sum(container.overhead_bytes for container in report.containers)
    if max(total, overhead) > SAFE_INTEGER:
        raise BuildSizeError("INTEGER_OVERFLOW", "Unity 汇总超过安全整数范围")
    guid_count = sum(valid_guid(record.source_guid) for record in records)
    exact = sum(row.state == "exact" for row in mappings)
    unknown = sum(
        record.packed_bytes
        for record in records
        if not valid_guid(record.source_guid) and not record.source_path
    )
    summary = {
        "parser_version": PARSER_VERSION,
        "state": "failed"
        if truncated(report.warnings) or report.detailed_report_requested is not True
        else "complete",
        "report_record_count": len(records),
        "valid_guid_record_count": guid_count,
        "guid_coverage_ratio": ratio(guid_count, len(records)),
        "container_count": len(mappings),
        "mapped_container_count": exact,
        "container_mapping_ratio": ratio(exact, len(mappings)),
        "unity_packed_bytes": total,
        "container_overhead_bytes": overhead,
        "unattributed_packed_bytes": unknown,
        "unity_reported_total_bytes": report.unity_reported_total_bytes,
        "importers": importer_summary,
        "build_result": report.build_result,
        "warnings": report.warnings,
        "mapper_version": MAPPER_VERSION,
    }
    return UnitySnapshot(
        report, importers, groups, container_groups, mappings, summary, importer_rows
    )


def preview_settings(importer_type: str, settings: dict[str, Any]) -> dict[str, Any]:
    """查询只返回已定义的设置键，完整未知扩展仍保留在原始产物。"""
    sample = {
        "compression_format",
        "quality",
        "sample_rate_setting",
        "sample_rate_override",
        "load_type",
    }
    if importer_type == "TextureImporter":
        roots = {"texture_type", "mipmap_enabled", "is_readable", "srgb"}
        nested = {
            "android": {
                "overridden",
                "max_texture_size",
                "format",
                "compression_quality",
                "crunched_compression",
            }
        }
    elif importer_type == "AudioImporter":
        roots = {"force_to_mono"}
        nested = {"default": sample, "android": sample | {"overridden"}}
    else:
        return {}
    result = {
        key: value
        for key, value in settings.items()
        if key in roots and type(value) in (str, int, float, bool, type(None))
    }
    for key, allowed in nested.items():
        value = settings.get(key)
        if isinstance(value, dict):
            result[key] = {
                field: item
                for field, item in value.items()
                if field in allowed and type(item) in (str, int, float, bool, type(None))
            }
    return result
