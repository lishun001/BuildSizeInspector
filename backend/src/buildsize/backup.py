"""离线 Blob 流备份；只接受数据库内的规范对象，绝不 extractall 或覆盖现有卷。"""

import argparse
import hashlib
import io
import json
import sys
import tarfile
import uuid
from typing import Any, BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from buildsize.config import Config
from buildsize.db.models import ArtifactBlob
from buildsize.db.session import make_engine
from buildsize.domain.errors import BuildSizeError
from buildsize.domain.validation import parse_json
from buildsize.storage.local import LocalBlobStorage


def transfer(config: Config, mode: str, stream: BinaryIO) -> None:
    storage = LocalBlobStorage(config.blob_root)
    if mode in {"restore", "check-empty"}:
        if storage.root.exists() and any(storage.root.iterdir()):
            raise BuildSizeError("RESTORE_NOT_EMPTY", "恢复目标 Blob 卷必须为空")
        if mode == "check-empty":
            return
    engine = make_engine(config)
    try:
        with Session(engine) as db:
            blobs = db.scalars(
                select(ArtifactBlob)
                .where(ArtifactBlob.deleted_at.is_(None))
                .order_by(ArtifactBlob.storage_key)
            ).all()
            if len(blobs) > 100000:
                raise BuildSizeError("BACKUP_LIMIT", "单次备份对象数超限")
            expected: dict[str, dict[str, Any]] = {
                blob.storage_key: {"bytes": blob.bytes, "sha256": blob.sha256} for blob in blobs
            }
        manifest = {"schema_version": 1, "objects": expected}
        if mode == "pack":
            with tarfile.open(fileobj=stream, mode="w|") as archive:
                for key, info in expected.items():
                    with storage.read_path(key) as path, path.open("rb") as source:
                        # 内容寻址不代替备份前实际校验；流式读取两遍避免整文件入内存。
                        digest = hashlib.file_digest(source, "sha256").hexdigest()
                        if digest != info["sha256"] or path.stat().st_size != info["bytes"]:
                            raise BuildSizeError("BACKUP_CORRUPTED", "Blob 与登记内容不符")
                        source.seek(0)
                        entry = tarfile.TarInfo("blobs/" + key)
                        entry.size = info["bytes"]
                        entry.mode = 0o440
                        archive.addfile(entry, source)
                raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
                item = tarfile.TarInfo("manifest.json")
                item.size = len(raw)
                item.mode = 0o600
                archive.addfile(item, io.BytesIO(raw))
        else:
            seen = set()
            actual_manifest = None
            with tarfile.open(fileobj=stream, mode="r|") as archive:
                for member in archive:
                    if not member.isfile():
                        raise BuildSizeError("BACKUP_INVALID", "备份不能包含链接或特殊文件")
                    member_source = archive.extractfile(member)
                    assert member_source is not None
                    if member.name == "manifest.json":
                        if actual_manifest is not None or member.size > 64 * 1024**2:
                            raise BuildSizeError("BACKUP_INVALID", "备份清单无效")
                        actual_manifest = parse_json(member_source.read(64 * 1024**2 + 1))
                        continue
                    if not member.name.startswith("blobs/"):
                        raise BuildSizeError("BACKUP_INVALID", "未知备份成员")
                    key = member.name.removeprefix("blobs/")
                    storage._path(key)
                    if key not in expected or key in seen or member.size != expected[key]["bytes"]:
                        raise BuildSizeError("BACKUP_INVALID", "备份重复、未登记或长度不符")
                    info = expected[key]
                    staged = storage.stage(
                        uuid.UUID(hex=key.split("/")[0]),
                        info["bytes"],
                        info["sha256"],
                    )
                    try:
                        while chunk := member_source.read(1024 * 1024):
                            staged.write(chunk)
                        staged.commit()
                    finally:
                        staged.close()
                    seen.add(key)
            if actual_manifest != manifest or seen != set(expected):
                raise BuildSizeError("BACKUP_INVALID", "备份缺失或清单不符")
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="离线 Blob 流打包/恢复，仅供受信任管理员使用")
    parser.add_argument("mode", choices=["pack", "restore", "check-empty"])
    args = parser.parse_args()
    try:
        transfer(
            Config.from_env(),
            args.mode,
            sys.stdout.buffer if args.mode == "pack" else sys.stdin.buffer,
        )
        return 0
    except Exception:
        print("BACKUP_FAILED：备份/恢复失败，未标记成功；不要复用半恢复卷", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
