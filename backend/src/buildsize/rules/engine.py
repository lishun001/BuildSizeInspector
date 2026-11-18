"""规则只输出带证据的非相加 finding；不改写 APK 字节账或采集事实。"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

from buildsize.analyzers.unity_report import preview_settings
from buildsize.comparison.apk import compare_apks
from buildsize.domain.apk import ApkSnapshot
from buildsize.domain.contracts import BuildMeta, Evidence, Finding, Subject
from buildsize.rules.config import RuleSettings

RULE_VERSION = "1"


@dataclass(frozen=True)
class RuleInput:
    target_revision: str
    base_revision: str | None
    base: ApkSnapshot | None
    target: ApkSnapshot
    base_meta: BuildMeta | None
    target_meta: BuildMeta
    comparable: bool
    coverage: dict[str, str]
    unity_changes: list[dict[str, Any]]
    importer_changes: list[dict[str, Any]]
    unity_summary: dict[str, Any]


def known_changes(old: dict[str, Any], new: dict[str, Any], prefix: str = "") -> list[str]:
    result = []
    for key in sorted(old.keys() & new.keys()):
        a, b = old[key], new[key]
        path = prefix + "/" + key.replace("~", "~0").replace("/", "~1")
        if isinstance(a, dict) and isinstance(b, dict):
            result.extend(known_changes(a, b, path))
        elif a is not None and b is not None and a != b:
            result.append(path)
    return result


def evaluate(value: RuleInput, settings: RuleSettings) -> Iterator[Finding]:
    comparison = compare_apks(value.base, value.target)

    def evidence(summary: str, key: str, *, old: bool = False) -> Evidence:
        revision = value.base_revision if old else value.target_revision
        assert revision is not None
        return Evidence(
            artifact_role="derived_analysis",
            artifact_sha256=None,
            locator=f"analysis_revision:{revision}:{key}",
            summary=summary,
        )

    def finding(
        rule: str,
        title: str,
        text: str,
        key: str,
        *,
        kind: Literal["apk_entry", "unity_asset", "config", "build", "container"] = "build",
        confidence: Literal["observed", "correlated", "hypothesis"] = "observed",
        metric: Literal[
            "zip_compressed_bytes", "unity_packed_bytes", "apk_overhead_bytes", "none"
        ] = "none",
        delta: int | None = None,
        proof: list[Evidence] | None = None,
        checks: list[str] | None = None,
    ) -> Finding:
        return Finding(
            schema_version=1,
            rule_id=rule,
            rule_version=RULE_VERSION,
            confidence=confidence,
            severity="warning"
            if rule in {"APK_ABSOLUTE_BUDGET", "APK_GROWTH_THRESHOLD", "REPORT_DATA_INCOMPLETE"}
            else "info",
            subject=Subject(kind=kind, key=key),
            title=title,
            explanation=text,
            metric=metric,
            delta_bytes=delta,
            additive=False,
            evidence=proof
            or [evidence(text, "unity_asset:" + key if kind == "unity_asset" else key)],
            suggested_checks=checks or ["查看原始证据；需要确证原因时进行控制变量构建。"],
            review_state="unreviewed",
        )

    def apk_evidence(path: str) -> list[Evidence]:
        result = [
            Evidence(
                artifact_role="apk",
                artifact_sha256=value.target.apk_sha256,
                locator="zip_entry:" + path,
                summary="目标 APK 中该路径的压缩表示。",
            )
        ]
        if value.base:
            result.append(
                Evidence(
                    artifact_role="apk",
                    artifact_sha256=value.base.apk_sha256,
                    locator="zip_entry:" + path,
                    summary="基线 APK 的相同路径；新增时不存在。",
                )
            )
        return result

    if (
        settings.absolute_budget_bytes is not None
        and value.target.apk_file_bytes > settings.absolute_budget_bytes
    ):
        text = (
            f"APK 实测 {value.target.apk_file_bytes} bytes，"
            f"超过预算 {settings.absolute_budget_bytes} bytes；含 ZIP 容器开销。"
        )
        yield finding("APK_ABSOLUTE_BUDGET", "APK 超过绝对预算", text, "apk_file_bytes")
    delta = comparison.delta_apk_bytes
    if (
        value.comparable
        and value.base
        and value.base.apk_file_bytes > 0
        and delta is not None
        and delta > 0
        and delta >= settings.growth_bytes
        and delta * 10000 >= value.base.apk_file_bytes * settings.growth_basis_points
    ):
        text = f"APK 净增长 {delta} bytes，同时达到字节和增幅阈值；这是 advisory 建议。"
        yield finding(
            "APK_GROWTH_THRESHOLD",
            "APK 增长达到阈值",
            text,
            "apk_growth",
            proof=[evidence(text, "apk"), evidence("基线 APK 实测大小。", "apk", old=True)],
        )
    for row in comparison.changes:
        if row.category == "native_library" and row.status == "ADDED":
            yield finding(
                "NATIVE_LIBRARY_ADDED",
                "新增 native 库条目",
                "APK 中新增该 .so 条目；仅条目压缩字节是观测贡献，不能据此猜测 SDK 身份。",
                row.path,
                kind="apk_entry",
                metric="zip_compressed_bytes",
                delta=row.delta_compressed_bytes,
                proof=apk_evidence(row.path),
            )
        if row.status == "PACKAGING_CHANGED":
            yield finding(
                "PACKAGING_CHANGED",
                "内容相同，压缩表示改变",
                "解压内容 SHA-256 相同，压缩大小或方法改变；具体打包配置原因仍需核实。",
                row.path,
                kind="apk_entry",
                metric="zip_compressed_bytes",
                delta=row.delta_compressed_bytes,
                proof=apk_evidence(row.path),
            )
        if row.category == "native_library" and row.delta_compressed_bytes > 0:
            yield finding(
                "NATIVE_GROWTH_UNEXPLAINED",
                "native 增长缺少精确内部映射",
                "已观测 native 条目增长，但没有符号级映射。"
                "代码、裁剪、SDK 或工具链只是待验证方向，不作责任归因。",
                row.path,
                kind="apk_entry",
                confidence="hypothesis",
                metric="zip_compressed_bytes",
                delta=row.delta_compressed_bytes,
                proof=apk_evidence(row.path),
                checks=["核对 IL2CPP、裁剪与工具链变化；需要确证时控制变量构建或 bisect。"],
            )
    if value.base:
        old_abis = {entry.detected_abi for entry in value.base.entries if entry.detected_abi}
        new_abis = {entry.detected_abi for entry in value.target.entries if entry.detected_abi}
        if old_abis and new_abis and old_abis != new_abis:
            yield finding(
                "ABI_SET_CHANGED",
                "APK native 目录 ABI 集发生变化",
                f"目录观测值 {sorted(old_abis)} → {sorted(new_abis)}；尚未核验 ELF 内部架构。",
                "observed_abis",
                kind="config",
                proof=[
                    evidence("基线 native 目录 ABI。", "observed_abis", old=True),
                    evidence("目标 native 目录 ABI。", "observed_abis"),
                ],
            )
    if value.base_meta:
        for field, rule, title in (
            ("architectures", "ABI_SET_CHANGED", "声明 ABI 集发生变化"),
            ("unity_version", "UNITY_VERSION_CHANGED", "声明 Unity 版本发生变化"),
        ):
            a, b = (
                getattr(value.base_meta.settings, field),
                getattr(value.target_meta.settings, field),
            )
            if (
                a is not None
                and b is not None
                and (sorted(a) if isinstance(a, list) else a)
                != (sorted(b) if isinstance(b, list) else b)
            ):
                yield finding(
                    rule,
                    title,
                    f"build_meta.settings.{field}：{a} → {b}；"
                    "来源为构建声明，变化本身不等于定量因果。",
                    "settings/" + field,
                    kind="config",
                    proof=[
                        evidence("目标元数据字段。", "build_meta:/settings/" + field),
                        evidence("基线元数据字段。", "build_meta:/settings/" + field, old=True),
                    ],
                )
    importers = {row["source_guid"]: row for row in value.importer_changes}
    old_guids = {
        asset["source_guid"]
        for asset in value.unity_changes
        if asset.get("source_guid") and asset["status"] != "ADDED_TO_BUILD"
    }
    for asset in value.unity_changes:
        if asset.get("identity_kind") != "guid" or not asset.get("source_guid"):
            continue
        key, packed_delta = asset["key"], asset["delta_packed_bytes"]
        if asset["status"] == "ADDED_TO_BUILD" and asset["source_guid"] not in old_guids:
            yield finding(
                "UNITY_SOURCE_ENTERED_BUILD",
                "源资源新增入包",
                "旧报告没有该 GUID，目标报告出现该资源；可能是已有资源新增引用或首包策略变化，"
                "不能断言源文件新建。",
                key,
                kind="unity_asset",
                metric="unity_packed_bytes",
                delta=packed_delta,
            )
        importer = importers.get(asset["source_guid"])
        if packed_delta <= 0 or not importer or "settings" not in importer["fields"]:
            continue
        importer_type = {"Texture2D": "TextureImporter", "AudioClip": "AudioImporter"}.get(
            asset["asset_type"]
        )
        if not importer_type or any(
            importer[side].get("importer_type") != importer_type for side in ("old", "new")
        ):
            continue
        fields = known_changes(
            preview_settings(importer_type, importer["old"]["settings"]),
            preview_settings(importer_type, importer["new"]["settings"]),
        )
        if not fields:
            continue
        import_rule = {
            "Texture2D": "UNITY_TEXTURE_IMPORT_CHANGED",
            "AudioClip": "UNITY_AUDIO_IMPORT_CHANGED",
        }.get(asset["asset_type"])
        if import_rule:
            yield finding(
                import_rule,
                "资源 packed 增长伴随导入配置变化",
                "同 GUID 的 Unity packed bytes 增长，已知设置同时变化："
                + ", ".join(fields[:20])
                + "。这是相关证据，不代表精确 APK 压缩增量。",
                key,
                kind="unity_asset",
                confidence="correlated",
                metric="unity_packed_bytes",
                delta=packed_delta,
                proof=[
                    evidence("目标 GUID/类型 packed 聚合。", "unity_asset:" + key),
                    evidence("目标 Importer 已知设置。", "asset_importer:" + asset["source_guid"]),
                    evidence(
                        "基线 Importer 已知设置。",
                        "asset_importer:" + asset["source_guid"],
                        old=True,
                    ),
                ],
            )
    for duplicate in value.target.duplicate_content:
        yield finding(
            "DUPLICATE_ENTRY_CONTENT",
            "多路径存在相同非空内容",
            f"{len(duplicate.paths)} 个条目具有相同内容 SHA-256，"
            f"合计压缩 {duplicate.compressed_bytes} bytes；不保证可以删除。",
            "content_sha256:" + duplicate.content_sha256,
            kind="apk_entry",
            proof=[
                Evidence(
                    artifact_role="apk",
                    artifact_sha256=value.target.apk_sha256,
                    locator="content_sha256:" + duplicate.content_sha256,
                    summary="相同内容哈希的多个非空条目。",
                )
            ],
        )
    incomplete = {
        key: state
        for key, state in value.coverage.items()
        if state not in {"complete", "not_applicable"} and key != "android_metadata"
    }
    unity = value.unity_summary
    if (
        incomplete
        or unity.get("guid_coverage_ratio", 1) != 1
        or unity.get("mapped_container_count", 0) < unity.get("container_count", 0)
        or unity.get("unattributed_packed_bytes", 0)
    ):
        yield finding(
            "REPORT_DATA_INCOMPLETE",
            "数据层或来源证据不完整",
            "覆盖状态："
            + str(incomplete)
            + "；缺失、不支持或未归属不等于 0。元数据未经 Android 工具核验；检查覆盖率后使用结论。",
            "coverage",
            proof=[evidence("各数据层覆盖与 Unity 来源/容器映射统计。", "coverage")],
        )
