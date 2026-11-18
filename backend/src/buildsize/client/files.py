import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from buildsize.domain.errors import BuildSizeError


class ClientError(BuildSizeError):
    def __init__(self, code: str, message: str, exit_code: int = 2) -> None:
        super().__init__(code, message)
        self.exit_code = exit_code


def hash_file(path: Path, maximum: int = 2 * 1024**3) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as source:
        before = os.fstat(source.fileno())
        while chunk := source.read(1024 * 1024):
            count += len(chunk)
            if count > maximum:
                raise ClientError("LOCAL_FILE_LIMIT", "本地产物超过大小限制")
            digest.update(chunk)
        after = os.fstat(source.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ClientError("LOCAL_INPUT_CHANGED", "读取期间本地产物发生变化", 5)
    return count, digest.hexdigest()


def write_json(value: dict[str, Any], output: Path | None) -> None:
    if output is None:
        json.dump(value, sys.stdout, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        sys.stdout.write("\n")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=".buildsize-",
            suffix=".part",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def create_json(value: dict[str, Any], output: Path) -> bool:
    """一次性创建；竞争者只能看到完整文件，不覆盖已有上下文/回执。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".part", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        write_json(value, temporary)
        try:
            os.link(temporary, output)
            return True
        except FileExistsError:
            return False
    finally:
        temporary.unlink(missing_ok=True)
