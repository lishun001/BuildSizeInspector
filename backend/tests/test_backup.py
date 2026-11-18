import io
import tarfile
from dataclasses import replace

import pytest
from test_pipeline import Harness, submission, submit
from test_pipeline import pipeline as pipeline

from buildsize.backup import transfer
from buildsize.domain.errors import BuildSizeError


def test_blob_backup_round_trip_and_no_overwrite(pipeline: Harness):
    h = pipeline
    submit(h, *submission("backup"))
    stream = io.BytesIO()
    transfer(h.config, "pack", stream)
    target = replace(h.config, blob_root=h.tmp_path / "restored")
    transfer(target, "restore", io.BytesIO(stream.getvalue()))
    old = {
        p.relative_to(h.config.blob_root): p.read_bytes()
        for p in h.config.blob_root.rglob("*")
        if p.is_file()
    }
    restored = {
        p.relative_to(target.blob_root): p.read_bytes()
        for p in target.blob_root.rglob("*")
        if p.is_file()
    }
    assert old == restored
    with pytest.raises(BuildSizeError, match="必须为空"):
        transfer(target, "restore", io.BytesIO(stream.getvalue()))


@pytest.mark.parametrize(
    "name,link", [("../../outside", False), ("blobs/not-a-key", False), ("blobs/link", True)]
)
def test_restore_rejects_traversal_and_links(pipeline: Harness, name, link):
    h = pipeline
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        entry = tarfile.TarInfo(name)
        if link:
            entry.type = tarfile.SYMTYPE
            entry.linkname = "../../outside"
        else:
            entry.size = 1
        archive.addfile(entry, io.BytesIO(b"x") if not link else None)
    with pytest.raises(BuildSizeError):
        transfer(
            replace(h.config, blob_root=h.tmp_path / "empty"),
            "restore",
            io.BytesIO(output.getvalue()),
        )
    assert not (h.tmp_path / "outside").exists()
