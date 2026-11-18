"""APK 物理层快照；所有大小均为整数 bytes。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ApkEntry:
    entry_index: int
    raw_path: str
    normalized_path: str
    is_directory: bool
    compressed_bytes: int
    uncompressed_bytes: int
    compression_method: int
    crc32: int
    content_sha256: str
    category: str
    detected_abi: str | None


@dataclass(frozen=True)
class CategorySize:
    category: str
    compressed_bytes: int
    uncompressed_bytes: int
    entry_count: int


@dataclass(frozen=True)
class DuplicateContent:
    content_sha256: str
    paths: tuple[str, ...]
    compressed_bytes: int


@dataclass(frozen=True)
class DirectorySize:
    path: str
    compressed_bytes: int
    uncompressed_bytes: int
    entry_count: int


@dataclass(frozen=True)
class ApkSnapshot:
    parser_version: str
    classifier_version: str
    apk_sha256: str
    apk_file_bytes: int
    zip_payload_bytes: int
    zip_uncompressed_bytes: int
    apk_overhead_bytes: int
    entries: tuple[ApkEntry, ...]
    categories: tuple[CategorySize, ...]
    directories: tuple[DirectorySize, ...]
    duplicate_content: tuple[DuplicateContent, ...]
    warnings: tuple[str, ...]
