"""项目隔离的内容寻址存储；临时文件验证后独占提交，绝不覆盖正式 Blob。"""

import hashlib
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Protocol

from buildsize.domain.errors import BuildSizeError

CHUNK = 1024 * 1024


class StagedUpload(Protocol):
    def write(self, data: bytes) -> None: ...
    def commit(self) -> str: ...
    def close(self) -> None: ...


class BlobStorage(Protocol):
    def stage(self, project_id: uuid.UUID, expected_bytes: int, sha256: str) -> StagedUpload: ...
    def read_path(self, key: str) -> AbstractContextManager[Path]: ...
    def delete(self, key: str) -> None: ...


class LocalBlobStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}/[a-f0-9]{2}/[a-f0-9]{64}", key):
            raise BuildSizeError("INVALID_STORAGE_KEY", "存储键无效")
        path = self.root / key
        if (
            not path.resolve().is_relative_to(self.root)
            or path.is_symlink()
            or any(
                parent.is_symlink()
                for parent in path.parents
                if parent != self.root and parent.is_relative_to(self.root)
            )
        ):
            raise BuildSizeError("INVALID_STORAGE_KEY", "存储路径不安全")
        return path

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists() and not stat.S_ISREG(path.stat().st_mode):
            raise BuildSizeError("INVALID_STORAGE_KEY", "只能清理规范普通 Blob 文件")
        path.unlink(missing_ok=True)

    def stage(self, project_id: uuid.UUID, expected_bytes: int, sha256: str) -> "LocalUpload":
        if expected_bytes < 0 or not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise ValueError("产物长度或 SHA-256 无效")
        directory = self.root / ".tmp"
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink():
            raise BuildSizeError("INVALID_STORAGE_KEY", "临时目录不可为符号链接")
        return LocalUpload(self, project_id, expected_bytes, sha256, directory)

    @contextmanager
    def read_path(self, key: str) -> Iterator[Path]:
        path = self._path(key)
        try:
            if not stat.S_ISREG(path.stat().st_mode):
                raise FileNotFoundError
        except FileNotFoundError as exc:
            raise BuildSizeError("BLOB_UNAVAILABLE", "原始产物不可用或已过期") from exc
        yield path


class LocalUpload:
    def __init__(
        self,
        storage: LocalBlobStorage,
        project_id: uuid.UUID,
        expected_bytes: int,
        sha256: str,
        directory: Path,
    ) -> None:
        self.storage = storage
        self.expected_bytes = expected_bytes
        self.expected_sha256 = sha256
        self.key = f"{project_id.hex}/{sha256[:2]}/{sha256}"
        self.stream = tempfile.NamedTemporaryFile(dir=directory, suffix=".part", delete=False)
        self.temporary = Path(self.stream.name)
        self.count = 0
        self.digest = hashlib.sha256()

    def write(self, data: bytes) -> None:
        self.count += len(data)
        if self.count > self.expected_bytes:
            raise BuildSizeError("ARTIFACT_LENGTH_MISMATCH", "上传实际长度大于清单声明")
        self.stream.write(data)
        self.digest.update(data)

    def commit(self) -> str:
        if self.count != self.expected_bytes:
            raise BuildSizeError("ARTIFACT_LENGTH_MISMATCH", "上传实际长度与清单不一致")
        if self.digest.hexdigest() != self.expected_sha256:
            raise BuildSizeError("ARTIFACT_HASH_MISMATCH", "上传 SHA-256 与清单不一致")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()
        target = self.storage._path(self.key)
        target.parent.mkdir(parents=True, exist_ok=True)
        # 创建目录后重新检查，拒绝被配置为越界链接的卷路径。
        target = self.storage._path(self.key)
        try:
            os.link(self.temporary, target)
            os.chmod(target, 0o440)
            descriptor = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except FileExistsError:
            # 文件名相同仍核验真实内容，不能覆盖已封存文件或信任损坏的去重对象。
            digest = hashlib.sha256()
            count = 0
            with target.open("rb") as source:
                while chunk := source.read(CHUNK):
                    count += len(chunk)
                    if count > self.expected_bytes:
                        break
                    digest.update(chunk)
            if count != self.expected_bytes or digest.hexdigest() != self.expected_sha256:
                raise BuildSizeError("BLOB_CORRUPTED", "已存储产物校验失败") from None
        self.temporary.unlink(missing_ok=True)
        return self.key

    def close(self) -> None:
        try:
            self.stream.close()
        finally:
            self.temporary.unlink(missing_ok=True)
