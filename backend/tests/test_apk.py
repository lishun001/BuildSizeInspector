import hashlib
import io
import stat
import struct
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from buildsize.analyzers.apk_zip import Limits, analyze_apk
from buildsize.comparison.apk import compare_apks
from buildsize.domain.errors import BuildSizeError


def archive(
    path: Path, entries: dict[str, bytes], compression: int = 0, comment: bytes = b""
) -> Path:
    with zipfile.ZipFile(path, "w", compression=compression) as output:
        for name, content in entries.items():
            output.writestr(name, content)
        output.comment = comment
    return path


def test_stored_independent_accounting_and_no_baseline(tmp_path: Path) -> None:
    path = archive(tmp_path / "sample.apk", {"a": b"123", "dir/": b"xy", "dir/b": b"1234"})
    snapshot = analyze_apk(path)
    # 三条本地头和中央目录：每条 30 + 46 + 2 * UTF-8 名称长度；EOCD 22。
    overhead = 3 * 76 + 2 * (1 + 4 + 5) + 22
    assert snapshot.apk_file_bytes == 9 + overhead
    assert snapshot.zip_payload_bytes == 9
    assert snapshot.apk_overhead_bytes == overhead
    assert snapshot.entries[1].uncompressed_bytes == 2
    assert snapshot.entries[1].content_sha256 == hashlib.sha256(b"xy").hexdigest()
    comparison = compare_apks(None, snapshot)
    assert comparison.status == "NO_BASELINE"
    assert comparison.delta_apk_bytes is None
    assert comparison.changes == ()


def test_add_remove_modify_and_reverse(tmp_path: Path) -> None:
    old = analyze_apk(archive(tmp_path / "old.apk", {"a": b"abc", "gone": b"123456"}))
    new = analyze_apk(archive(tmp_path / "new.apk", {"a": b"xyz", "added": b"123456789"}))
    result = compare_apks(old, new)
    changes = {item.path: item for item in result.changes}
    assert changes["a"].status == "MODIFIED"
    assert changes["a"].delta_compressed_bytes == 0
    assert changes["a"].content_changed
    assert changes["added"].status == "ADDED"
    assert changes["gone"].status == "REMOVED"
    assert result.positive_growth_bytes == 11  # 新增 9，加路径变长导致 overhead +2
    assert result.negative_shrink_bytes == -6
    assert result.delta_apk_bytes == 5
    assert compare_apks(new, old).delta_apk_bytes == -5


def test_compression_and_container_changes(tmp_path: Path) -> None:
    content = {"assets/中文 空格.bin": b"abcd" * 1000}
    old = analyze_apk(archive(tmp_path / "old.apk", content))
    new = analyze_apk(archive(tmp_path / "new.apk", content, zipfile.ZIP_DEFLATED))
    result = compare_apks(old, new)
    assert result.changes[0].status == "PACKAGING_CHANGED"
    assert not result.changes[0].content_changed
    same = analyze_apk(archive(tmp_path / "comment.apk", content, comment=b"changed"))
    result = compare_apks(old, same)
    assert result.changes[0].status == "UNCHANGED"
    assert result.delta_apk_bytes == 7
    assert result.delta_overhead_bytes == 7
    assert compare_apks(old, old).delta_apk_bytes == 0


@pytest.mark.parametrize("name", ["../bad", "/bad", "C:bad", "a/../b", "a\\b", "a\x00b"])
def test_invalid_paths(tmp_path: Path, name: str) -> None:
    if "\x00" in name:
        path = archive(tmp_path / "bad.apk", {"aXb": b"x"})
        path.write_bytes(path.read_bytes().replace(b"aXb", b"a\x00b"))
    else:
        path = archive(tmp_path / "bad.apk", {name: b"x"})
    with pytest.raises(BuildSizeError) as error:
        analyze_apk(path)
    assert error.value.code == "ZIP_UNSAFE_PATH"


def test_duplicates_and_normalized_conflicts(tmp_path: Path) -> None:
    for names in [("a", "a"), ("a//b", "a/b"), ("a", "./a"), ("a", "a/")]:
        path = tmp_path / "bad.apk"
        with pytest.warns(UserWarning) if names[0] == names[1] else io.StringIO():
            with zipfile.ZipFile(path, "w") as output:
                for name in names:
                    output.writestr(name, b"x")
        with pytest.raises(BuildSizeError) as error:
            analyze_apk(path)
        assert error.value.code == "ZIP_DUPLICATE_PATH"


def test_symlink_encryption_method_and_crc(tmp_path: Path) -> None:
    path = tmp_path / "bad.apk"
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as output:
        output.writestr(link, b"target")
    with pytest.raises(BuildSizeError) as error:
        analyze_apk(path)
    assert error.value.code == "ZIP_SYMLINK"
    path = archive(path, {"a": b"abc"})
    data = bytearray(path.read_bytes())
    data[31] ^= 1
    path.write_bytes(data)
    with pytest.raises(BuildSizeError) as error:
        analyze_apk(path)
    assert error.value.code == "ZIP_CRC_MISMATCH"
    for field, value, code in [(6, 1, "ZIP_ENCRYPTED"), (8, 99, "ZIP_UNSUPPORTED_COMPRESSION")]:
        archive(path, {"a": b"abc"})
        data = bytearray(path.read_bytes())
        central = data.index(b"PK\x01\x02")
        struct.pack_into("<H", data, field, value)
        struct.pack_into("<H", data, central + field + 2, value)
        path.write_bytes(data)
        with pytest.raises(BuildSizeError) as error:
            analyze_apk(path)
        assert error.value.code == code


def test_limits(tmp_path: Path) -> None:
    path = archive(
        tmp_path / "bomb.apk", {"a": b"a" * 10000, "b": b"b" * 10000}, zipfile.ZIP_DEFLATED
    )
    for limits, code in [
        (Limits(max_entries=1), "ZIP_ENTRY_LIMIT"),
        (Limits(max_entry_bytes=100), "ZIP_DECOMPRESSION_LIMIT"),
        (Limits(max_total_bytes=15000), "ZIP_DECOMPRESSION_LIMIT"),
        (Limits(max_apk_bytes=10), "APK_SIZE_LIMIT"),
        (Limits(max_central_directory_bytes=10), "ZIP_DIRECTORY_LIMIT"),
        (Limits(timeout_seconds=0), "ANALYSIS_TIMEOUT"),
    ]:
        with pytest.raises(BuildSizeError) as error:
            analyze_apk(path, limits=limits)
        assert error.value.code == code
    assert sorted(item.name for item in tmp_path.iterdir()) == ["bomb.apk"]


def test_declared_size_cannot_hide_actual_inflation(tmp_path: Path) -> None:
    path = archive(tmp_path / "bad.apk", {"a": b"x" * 10000}, zipfile.ZIP_DEFLATED)
    data = bytearray(path.read_bytes())
    central = data.index(b"PK\x01\x02")
    # 声明只解压 1 byte，CRC 也伪装成这个前缀；仍必须检查真实解压流。
    crc = zipfile.crc32(b"x")
    struct.pack_into("<I", data, 14, crc)
    struct.pack_into("<I", data, 22, 1)
    struct.pack_into("<I", data, central + 16, crc)
    struct.pack_into("<I", data, central + 24, 1)
    path.write_bytes(data)
    with pytest.raises(BuildSizeError) as error:
        analyze_apk(path, limits=Limits(max_entry_bytes=100))
    assert error.value.code == "ZIP_DECOMPRESSION_LIMIT"


def test_category_duplicate_and_classifier_version(tmp_path: Path) -> None:
    snapshot = analyze_apk(
        archive(
            tmp_path / "a.apk",
            {
                "lib/arm64-v8a/libx.so": b"same",
                "classes2.dex": b"dex",
                "assets/bin/Data/x": b"same",
                "res/a": b"",
                "assets/other": b"",
                "resources.arsc": b"res",
            },
        )
    )
    assert snapshot.entries[0].detected_abi == "arm64-v8a"
    assert snapshot.entries[0].category == "native_library"
    assert sum(item.compressed_bytes for item in snapshot.categories) == snapshot.zip_payload_bytes
    assert snapshot.duplicate_content[0].paths == ("assets/bin/Data/x", "lib/arm64-v8a/libx.so")
    assert len(snapshot.duplicate_content) == 1
    with pytest.raises(BuildSizeError) as error:
        compare_apks(snapshot, replace(snapshot, classifier_version="different"))
    assert error.value.code == "CLASSIFIER_VERSION_MISMATCH"


payloads = st.dictionaries(
    st.sampled_from(["a", "assets/中 文", "lib/arm64-v8a/libx.so", "dir/", "dir/b"]),
    st.binary(max_size=128),
    max_size=5,
)


@given(payloads, payloads, st.sampled_from([0, 8]), st.sampled_from([0, 8]))
@settings(max_examples=100, deadline=None)
def test_random_accounting(
    old_entries: dict[str, bytes], new_entries: dict[str, bytes], old_method: int, new_method: int
) -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        old = analyze_apk(archive(root / "a.apk", old_entries, old_method))
        new = analyze_apk(archive(root / "b.apk", new_entries, new_method))
        result = compare_apks(old, new)
        assert result.delta_apk_bytes == new.apk_file_bytes - old.apk_file_bytes
        assert result.delta_apk_bytes == (
            sum(item.delta_compressed_bytes for item in result.changes)
            + result.delta_overhead_bytes
        )
        assert result.delta_apk_bytes == result.positive_growth_bytes + result.negative_shrink_bytes
        assert compare_apks(new, old).delta_apk_bytes == -result.delta_apk_bytes
        assert sum(item.delta_bytes for item in result.categories) == result.delta_apk_bytes


def test_directory_views_are_not_additive(tmp_path: Path) -> None:
    snapshot = analyze_apk(
        archive(tmp_path / "a.zip", {"a": b"123", "dir/": b"xy", "dir/b": b"1234"})
    )
    directories = {item.path: item for item in snapshot.directories}
    assert directories["/"].compressed_bytes == 9
    assert directories["dir/"].compressed_bytes == 6
    assert snapshot.zip_payload_bytes == 9  # 不把两个目录视图再次相加。


def test_overlapping_local_records(tmp_path: Path) -> None:
    nested = (
        struct.pack("<4s5H3I2H", b"PK\x03\x04", 20, 0, 0, 0, 0, zipfile.crc32(b"x"), 1, 1, 1, 0)
        + b"b"
        + b"x"
    )
    path = archive(tmp_path / "overlap.zip", {"a": nested, "b": b"x"})
    data = bytearray(path.read_bytes())
    first_central = data.index(b"PK\x01\x02")
    second_central = data.index(b"PK\x01\x02", first_central + 4)
    struct.pack_into("<I", data, second_central + 42, 31)
    path.write_bytes(data)
    with pytest.raises(BuildSizeError) as error:
        analyze_apk(path)
    assert error.value.code == "ZIP_OVERLAPPING_ENTRIES"


def test_streaming_descriptor_and_small_zip64(tmp_path: Path) -> None:
    class Unseekable(io.BytesIO):
        def seekable(self) -> bool:
            return False

        def seek(self, *args):
            raise OSError("unseekable")

    buffer = Unseekable()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as output:
        with output.open("a", "w", force_zip64=True) as entry:
            entry.write(b"content" * 1000)
    path = tmp_path / "descriptor.zip"
    path.write_bytes(buffer.getvalue())
    snapshot = analyze_apk(path)
    assert snapshot.zip_uncompressed_bytes == 7000
    assert snapshot.entries[0].content_sha256 == hashlib.sha256(b"content" * 1000).hexdigest()


def test_apk_like_gap_before_central_directory(tmp_path: Path) -> None:
    path = archive(tmp_path / "gap.zip", {"a": b"x"})
    data = bytearray(path.read_bytes())
    position = data.index(b"PK\x01\x02")
    data[position:position] = b"container gap" * 10
    eocd = data.index(b"PK\x05\x06")
    struct.pack_into("<I", data, eocd + 16, position + 130)
    path.write_bytes(data)
    snapshot = analyze_apk(path)
    assert snapshot.apk_overhead_bytes == 76 + 2 + 22 + 130
    assert snapshot.zip_payload_bytes == 1


@pytest.mark.parametrize("kind", ["multidisk", "count", "truncated", "header_name"])
def test_malformed_zip_structure(tmp_path: Path, kind: str) -> None:
    path = archive(tmp_path / "bad.zip", {"a": b"xyz"})
    data = bytearray(path.read_bytes())
    eocd = data.index(b"PK\x05\x06")
    if kind == "multidisk":
        struct.pack_into("<H", data, eocd + 4, 1)
    elif kind == "count":
        struct.pack_into("<HH", data, eocd + 8, 0, 0)
    elif kind == "truncated":
        data = data[:-1]
    else:
        data[30] = ord("b")
    path.write_bytes(data)
    with pytest.raises(BuildSizeError) as error:
        analyze_apk(path)
    assert error.value.code == "ZIP_INVALID_STRUCTURE"


@pytest.mark.parametrize("count", [65535, 65536])
def test_entry_count_at_zip64_boundary(tmp_path: Path, count: int) -> None:
    path = tmp_path / "many.zip"
    with zipfile.ZipFile(path, "w") as output:
        for number in range(count):
            output.writestr(str(number), b"")
    snapshot = analyze_apk(path)
    assert len(snapshot.entries) == count
    assert snapshot.zip_payload_bytes == 0
    assert snapshot.apk_overhead_bytes == path.stat().st_size


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -1])
def test_invalid_limits(timeout: float) -> None:
    with pytest.raises(ValueError):
        Limits(timeout_seconds=timeout)


def test_classifier_explicit_bundle_priority(tmp_path: Path) -> None:
    from buildsize.analyzers.classifier import Classifier

    path = archive(tmp_path / "bundle.zip", {"assets/bin/Data/bundles/a": b"test"})
    default = analyze_apk(path)
    custom = analyze_apk(path, classifier=Classifier(("assets/bin/Data/bundles/",), True))
    assert default.entries[0].category == "unity_data"
    assert custom.entries[0].category == "builtin_bundle"
    assert default.classifier_version != custom.classifier_version
