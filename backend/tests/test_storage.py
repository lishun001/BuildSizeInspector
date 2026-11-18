import hashlib
import uuid
from pathlib import Path

import pytest

from buildsize.domain.errors import BuildSizeError
from buildsize.storage.local import LocalBlobStorage


def test_stream_commit_dedup_and_project_isolation(tmp_path: Path) -> None:
    storage = LocalBlobStorage(tmp_path)
    project = uuid.uuid4()
    content = b"example-content" * 100
    sha = hashlib.sha256(content).hexdigest()
    first = storage.stage(project, len(content), sha)
    first.write(content[:100])
    first.write(content[100:])
    key = first.commit()
    first.close()
    with storage.read_path(key) as path:
        inode = path.stat().st_ino
        assert path.read_bytes() == content
    second = storage.stage(project, len(content), sha)
    second.write(content)
    assert second.commit() == key
    second.close()
    with storage.read_path(key) as path:
        assert path.stat().st_ino == inode
    other = storage.stage(uuid.uuid4(), len(content), sha)
    other.write(content)
    assert other.commit() != key
    other.close()
    assert list((tmp_path / ".tmp").iterdir()) == []


@pytest.mark.parametrize("kind", ["short", "long", "hash", "interrupt"])
def test_failed_upload_never_publishes(tmp_path: Path, kind: str) -> None:
    storage = LocalBlobStorage(tmp_path)
    staged = storage.stage(uuid.uuid4(), 4, hashlib.sha256(b"test").hexdigest())
    try:
        if kind == "interrupt":
            staged.write(b"te")
        else:
            with pytest.raises(BuildSizeError):
                staged.write({"short": b"te", "long": b"too-long", "hash": b"oops"}[kind])
                staged.commit()
    finally:
        staged.close()
    assert not list(tmp_path.glob("*/*/*"))
    assert list((tmp_path / ".tmp").iterdir()) == []


def test_existing_corruption_is_not_overwritten(tmp_path: Path) -> None:
    storage = LocalBlobStorage(tmp_path)
    project = uuid.uuid4()
    sha = hashlib.sha256(b"test").hexdigest()
    key = f"{project.hex}/{sha[:2]}/{sha}"
    path = tmp_path / key
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fake")
    staged = storage.stage(project, 4, sha)
    staged.write(b"test")
    try:
        with pytest.raises(BuildSizeError) as error:
            staged.commit()
        assert error.value.code == "BLOB_CORRUPTED"
        assert path.read_bytes() == b"fake"
    finally:
        staged.close()


@pytest.mark.parametrize("key", ["../escape", "/root", "a/b/c", "f" * 32 + "/aa/" + "f" * 64])
def test_storage_keys_are_checked(tmp_path: Path, key: str) -> None:
    storage = LocalBlobStorage(tmp_path)
    if key.startswith("f" * 32):
        (tmp_path / ("f" * 32)).symlink_to(tmp_path.parent)
    with pytest.raises(BuildSizeError):
        with storage.read_path(key):
            pass
