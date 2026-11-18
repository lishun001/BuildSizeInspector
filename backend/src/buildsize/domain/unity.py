"""Unity 序列化指标，与 APK 压缩字节独立。"""

from dataclasses import dataclass
from typing import Any

from buildsize.domain.contracts import AssetImporters, UnityReport


@dataclass(frozen=True)
class AssetGroup:
    key: str
    identity_kind: str
    source_guid: str | None
    asset_type: str
    source_paths: tuple[str, ...]
    container_paths: tuple[str, ...]
    packed_bytes: int
    record_count: int
    matchable: bool


@dataclass(frozen=True)
class ContainerMapping:
    container_index: int
    short_path: str
    state: str
    apk_paths: tuple[str, ...]
    mapper_version: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class UnitySnapshot:
    report: UnityReport | None
    importers: AssetImporters | None
    groups: tuple[AssetGroup, ...]
    container_groups: tuple[AssetGroup, ...]
    mappings: tuple[ContainerMapping, ...]
    summary: dict[str, Any]
    importer_rows: tuple[dict[str, Any], ...]
