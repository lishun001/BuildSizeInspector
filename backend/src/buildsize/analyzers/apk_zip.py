"""只读、有界的 ZIP/APK 分析。不会解压落盘或执行输入文件。"""

import hashlib
import math
import os
import re
import stat
import struct
import time
import zipfile
import zlib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from buildsize.analyzers.classifier import Classifier
from buildsize.domain.apk import (
    ApkEntry,
    ApkSnapshot,
    CategorySize,
    DirectorySize,
    DuplicateContent,
)
from buildsize.domain.contracts import SAFE_INTEGER
from buildsize.domain.errors import BuildSizeError

PARSER_VERSION = "apk-zip-1"
CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Limits:
    max_apk_bytes: int = 2 * 1024**3
    max_entries: int = 100_000
    max_central_directory_bytes: int = 64 * 1024**2
    max_entry_bytes: int = 4 * 1024**3
    max_total_bytes: int = 16 * 1024**3
    timeout_seconds: float = 20 * 60

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout_seconds):
            raise ValueError("分析期限必须为有限数值")
        if (
            min(
                self.max_apk_bytes,
                self.max_entries,
                self.max_central_directory_bytes,
                self.max_entry_bytes,
                self.max_total_bytes,
                self.timeout_seconds,
            )
            < 0
        ):
            raise ValueError("资源限制不能为负数")


class _Budget:
    def __init__(self, limits: Limits) -> None:
        self.limits = limits
        self.started = time.monotonic()
        self.total = 0

    def check(self) -> None:
        if time.monotonic() - self.started >= self.limits.timeout_seconds:
            raise BuildSizeError("ANALYSIS_TIMEOUT", "APK 分析超过期限")

    def add(self, count: int, entry_total: int) -> None:
        self.check()
        self.total += count
        if entry_total > self.limits.max_entry_bytes or self.total > self.limits.max_total_bytes:
            raise BuildSizeError("ZIP_DECOMPRESSION_LIMIT", "实际解压数据超过预算")
        if self.total > SAFE_INTEGER:
            raise BuildSizeError("INTEGER_OVERFLOW", "字节计数超出安全整数范围")


def _invalid(message: str) -> BuildSizeError:
    return BuildSizeError("ZIP_INVALID_STRUCTURE", message)


def _read(stream: BinaryIO, count: int) -> bytes:
    data = stream.read(count)
    if len(data) != count:
        raise _invalid("ZIP 文件截断")
    return data


def _central_directory(stream: BinaryIO, size: int, budget: _Budget) -> tuple[int, int]:
    # 先限制中央目录，避免 ZipFile 构造时无界分配条目元数据。
    tail_size = min(size, 65535 + 22)
    stream.seek(size - tail_size)
    tail = _read(stream, tail_size)
    position = len(tail)
    while True:
        position = tail.rfind(b"PK\x05\x06", 0, position)
        if position < 0:
            raise _invalid("找不到完整的 ZIP 结束记录")
        if position + 22 <= len(tail):
            fields = struct.unpack_from("<4s4H2IH", tail, position)
            if position + 22 + fields[7] == len(tail):
                break
    eocd_position = size - tail_size + position
    _, disk, cd_disk, disk_count, count, cd_size, cd_offset, _ = fields
    if disk or cd_disk or disk_count != count:
        raise _invalid("不支持多卷 ZIP")
    cd_end = eocd_position
    stream.seek(max(0, eocd_position - 20))
    has_zip64 = stream.read(4) == b"PK\x06\x07"
    if has_zip64 or cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
        stream.seek(eocd_position - 20)
        locator = struct.unpack("<4sIQI", _read(stream, 20))
        if locator[0] != b"PK\x06\x07" or locator[1] != 0 or locator[3] != 1:
            raise _invalid("ZIP64 locator 无效")
        # P0 支持标准 56 字节 ZIP64 EOCD；扩展扇区必须显式拒绝。
        cd_end = eocd_position - 20 - 56
        if cd_end < 0:
            raise _invalid("ZIP64 结束记录越界")
        stream.seek(cd_end)
        record = struct.unpack("<4sQ2H2I4Q", _read(stream, 56))
        if record[0] != b"PK\x06\x06" or record[1] != 44 or record[4] or record[5]:
            raise _invalid("不支持此 ZIP64 结束记录")
        if record[6] != record[7]:
            raise _invalid("ZIP64 条目数量不一致")
        count, cd_size, cd_offset = record[7:10]
        if locator[2] > cd_end:
            raise _invalid("ZIP64 locator 偏移无效")
    if count > budget.limits.max_entries:
        raise BuildSizeError("ZIP_ENTRY_LIMIT", "ZIP 条目数超过限制")
    if cd_size > budget.limits.max_central_directory_bytes:
        raise BuildSizeError("ZIP_DIRECTORY_LIMIT", "ZIP 中央目录超过限制")
    start = cd_end - cd_size
    if start < 0 or cd_offset > start:
        raise _invalid("ZIP 中央目录越界")
    stream.seek(start)
    actual = 0
    while stream.tell() < cd_end:
        budget.check()
        header = _read(stream, 46)
        if header[:4] != b"PK\x01\x02":
            raise _invalid("ZIP 中央目录头无效")
        name_length, extra_length, comment_length = struct.unpack_from("<3H", header, 28)
        next_position = stream.tell() + name_length + extra_length + comment_length
        if next_position > cd_end:
            raise _invalid("ZIP 中央目录记录越界")
        stream.seek(next_position)
        actual += 1
        if actual > budget.limits.max_entries:
            raise BuildSizeError("ZIP_ENTRY_LIMIT", "ZIP 实际条目数超过限制")
    if stream.tell() != cd_end or actual != count:
        raise _invalid("ZIP 中央目录计数不一致")
    return start, count


def normalize_path(raw: str) -> str:
    if (
        not raw
        or raw.startswith("/")
        or re.match(r"^[a-zA-Z]:", raw)
        or "\\" in raw
        or "\x00" in raw
        or ".." in raw.split("/")
    ):
        raise BuildSizeError("ZIP_UNSAFE_PATH", "ZIP 含非法路径")
    result = "/".join(part for part in raw.split("/") if part not in {"", "."})
    if not result:
        raise BuildSizeError("ZIP_UNSAFE_PATH", "ZIP 路径规范化后为空")
    return result


def _local_data(stream: BinaryIO, info: zipfile.ZipInfo, cd_start: int) -> tuple[int, int]:
    if info.header_offset < 0 or info.header_offset + 30 > cd_start:
        raise _invalid("ZIP 本地头越界")
    stream.seek(info.header_offset)
    header = struct.unpack("<4s5H3I2H", _read(stream, 30))
    if header[0] != b"PK\x03\x04" or header[2] != info.flag_bits or header[3] != info.compress_type:
        raise _invalid("ZIP 本地头与中央目录不一致")
    name = _read(stream, header[9])
    extra = _read(stream, header[10])
    encoding = "utf-8" if info.flag_bits & 0x800 else "cp437"
    if name.decode(encoding) != info.orig_filename:
        raise _invalid("ZIP 本地路径与中央目录不一致")
    start = stream.tell()
    end = start + info.compress_size
    if end > cd_start:
        raise _invalid("ZIP 条目数据越过中央目录")
    if not info.flag_bits & 8:
        uncompressed, compressed = header[8], header[7]
        if 0xFFFFFFFF in (uncompressed, compressed):
            cursor = 0
            found = False
            while cursor + 4 <= len(extra):
                tag, length = struct.unpack_from("<HH", extra, cursor)
                cursor += 4
                if cursor + length > len(extra):
                    raise _invalid("ZIP64 extra 越界")
                if tag == 1:
                    found = True
                    values = extra[cursor : cursor + length]
                    if uncompressed == 0xFFFFFFFF:
                        if len(values) < 8:
                            raise _invalid("ZIP64 size 缺失")
                        uncompressed = struct.unpack_from("<Q", values)[0]
                        values = values[8:]
                    if compressed == 0xFFFFFFFF:
                        if len(values) < 8:
                            raise _invalid("ZIP64 compressed size 缺失")
                        compressed = struct.unpack_from("<Q", values)[0]
                    break
                cursor += length
            if not found:
                raise _invalid("ZIP64 extra 缺失")
        if (header[6], compressed, uncompressed) != (info.CRC, info.compress_size, info.file_size):
            raise _invalid("ZIP 本地大小或 CRC 声明不一致")
    else:
        stream.seek(end)
        available = min(cd_start - end, 24)
        descriptor = _read(stream, available)
        width = (
            8
            if 0xFFFFFFFF in header[7:9] or max(info.file_size, info.compress_size) > 0xFFFFFFFF
            else 4
        )
        form = "<IQQ" if width == 8 else "<III"
        needed = struct.calcsize(form)
        match = False
        for offset in (0, 4):
            if offset and descriptor[:4] != b"PK\x07\x08":
                continue
            if len(descriptor) >= offset + needed:
                actual = struct.unpack_from(form, descriptor, offset)
                if actual == (info.CRC, info.compress_size, info.file_size):
                    end += offset + needed
                    match = True
                    break
        if not match:
            raise _invalid("ZIP data descriptor 无效")
    return start, end


def _content(stream: BinaryIO, info: zipfile.ZipInfo, start: int, budget: _Budget) -> str:
    stream.seek(start)
    remaining = info.compress_size
    digest = hashlib.sha256()
    crc = 0
    actual = 0
    decoder = zlib.decompressobj(-15) if info.compress_type == zipfile.ZIP_DEFLATED else None

    def consume(data: bytes) -> None:
        nonlocal actual, crc
        actual += len(data)
        budget.add(len(data), actual)
        digest.update(data)
        crc = zlib.crc32(data, crc)

    while remaining:
        budget.check()
        data = _read(stream, min(remaining, CHUNK))
        remaining -= len(data)
        if decoder is None:
            consume(data)
        else:
            while data:
                consume(decoder.decompress(data, CHUNK))
                if decoder.unused_data:
                    raise _invalid("DEFLATE 数据后存在额外压缩内容")
                data = decoder.unconsumed_tail
    if decoder is not None:
        while True:
            output = decoder.decompress(b"", CHUNK)
            consume(output)
            if not output:
                break
        if not decoder.eof:
            raise _invalid("DEFLATE 数据截断")
    if actual != info.file_size:
        raise _invalid("实际解压长度与 ZIP 声明不一致")
    if crc != info.CRC:
        raise BuildSizeError("ZIP_CRC_MISMATCH", "ZIP 条目 CRC 校验失败")
    return digest.hexdigest()


def analyze_apk(
    path: Path,
    *,
    limits: Limits | None = None,
    classifier: Classifier | None = None,
) -> ApkSnapshot:
    limits = limits or Limits()
    classifier = classifier or Classifier()
    budget = _Budget(limits)
    budget.check()
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise _invalid("APK 输入必须是普通文件")
            if before.st_size > limits.max_apk_bytes:
                raise BuildSizeError("APK_SIZE_LIMIT", "APK 文件超过大小限制")
            cd_start, count = _central_directory(stream, before.st_size, budget)
            stream.seek(0)
            file_digest = hashlib.sha256()
            while data := stream.read(CHUNK):
                budget.check()
                file_digest.update(data)
            stream.seek(0)
            with zipfile.ZipFile(stream) as archive:
                infos = archive.infolist()
                if len(infos) != count:
                    raise _invalid("ZIP 解析条目数不一致")
                seen: set[str] = set()
                prepared: list[tuple[zipfile.ZipInfo, str, int, int]] = []
                warnings: list[str] = []
                for info in infos:
                    budget.check()
                    raw = info.orig_filename
                    normalized = normalize_path(raw)
                    if normalized in seen:
                        raise BuildSizeError("ZIP_DUPLICATE_PATH", "ZIP 路径重复或规范化后冲突")
                    seen.add(normalized)
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(mode):
                        raise BuildSizeError("ZIP_SYMLINK", "ZIP 符号链接不被允许")
                    if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                        raise _invalid("ZIP 含不支持的特殊文件类型")
                    if info.flag_bits & (1 | 64):
                        raise BuildSizeError("ZIP_ENCRYPTED", "不支持加密 ZIP 条目")
                    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                        raise BuildSizeError("ZIP_UNSUPPORTED_COMPRESSION", "不支持此 ZIP 压缩方法")
                    if info.file_size > limits.max_entry_bytes:
                        raise BuildSizeError("ZIP_DECOMPRESSION_LIMIT", "声明的条目长度超过预算")
                    start, end = _local_data(stream, info, cd_start)
                    prepared.append((info, normalized, start, end))
                    if info.file_size > max(1, info.compress_size) * 1000:
                        warnings.append(f"高压缩比条目：{normalized}")
                previous_end = 0
                for info, _, _, end in sorted(prepared, key=lambda value: value[0].header_offset):
                    if info.header_offset < previous_end:
                        raise BuildSizeError("ZIP_OVERLAPPING_ENTRIES", "ZIP 条目范围重叠")
                    previous_end = end
                entries: list[ApkEntry] = []
                for index, (info, normalized, start, _) in enumerate(prepared):
                    content_hash = _content(stream, info, start, budget)
                    category, abi = classifier.classify(normalized, info.is_dir())
                    entries.append(
                        ApkEntry(
                            index,
                            info.orig_filename,
                            normalized,
                            info.is_dir(),
                            info.compress_size,
                            info.file_size,
                            info.compress_type,
                            info.CRC,
                            content_hash,
                            category,
                            abi,
                        )
                    )
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise BuildSizeError("INPUT_CHANGED", "分析过程中输入文件发生变化")
            payload = sum(entry.compressed_bytes for entry in entries)
            overhead = before.st_size - payload
            if overhead < 0:
                raise _invalid("APK overhead 为负数")
            if max(before.st_size, payload, budget.total) > SAFE_INTEGER:
                raise BuildSizeError("INTEGER_OVERFLOW", "指标超出安全整数范围")
            categories: dict[str, list[ApkEntry]] = defaultdict(list)
            directories: dict[str, list[ApkEntry]] = defaultdict(list)
            hashes: dict[str, list[ApkEntry]] = defaultdict(list)
            for entry in entries:
                categories[entry.category].append(entry)
                directories["/"].append(entry)
                parts = entry.normalized_path.split("/")
                depth = len(parts) if entry.is_directory else len(parts) - 1
                for level in range(1, depth + 1):
                    directories["/".join(parts[:level]) + "/"].append(entry)
                if entry.uncompressed_bytes and not entry.is_directory:
                    hashes[entry.content_sha256].append(entry)
            return ApkSnapshot(
                PARSER_VERSION,
                classifier.version,
                file_digest.hexdigest(),
                before.st_size,
                payload,
                budget.total,
                overhead,
                tuple(entries),
                tuple(
                    CategorySize(
                        key,
                        sum(e.compressed_bytes for e in group),
                        sum(e.uncompressed_bytes for e in group),
                        len(group),
                    )
                    for key, group in sorted(categories.items())
                ),
                tuple(
                    DirectorySize(
                        key,
                        sum(e.compressed_bytes for e in group),
                        sum(e.uncompressed_bytes for e in group),
                        len(group),
                    )
                    for key, group in sorted(directories.items())
                ),
                tuple(
                    DuplicateContent(
                        key,
                        tuple(sorted(e.normalized_path for e in group)),
                        sum(e.compressed_bytes for e in group),
                    )
                    for key, group in sorted(hashes.items())
                    if len(group) > 1
                ),
                tuple(warnings),
            )
    except BuildSizeError:
        raise
    except (
        zipfile.BadZipFile,
        zlib.error,
        UnicodeError,
        struct.error,
        EOFError,
        ValueError,
    ) as exc:
        raise _invalid("ZIP 数据格式无效") from exc
    except OSError as exc:
        raise BuildSizeError("INPUT_IO_ERROR", "无法读取 APK 输入") from exc


def check_apk_container(path: Path, *, limits: Limits | None = None) -> None:
    """封存前只检查有界 ZIP 目录；完整 CRC/内容校验由 Worker 执行。"""
    limits = limits or Limits()
    try:
        with path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            if size > limits.max_apk_bytes:
                raise BuildSizeError("APK_SIZE_LIMIT", "APK 文件超过大小限制")
            _central_directory(stream, size, _Budget(limits))
    except BuildSizeError:
        raise
    except (OSError, ValueError, struct.error) as exc:
        raise _invalid("APK ZIP 目录无效") from exc
