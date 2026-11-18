"""APK 物理对账；Unity packed bytes 不进入此账。"""

from dataclasses import dataclass

from buildsize.domain.apk import ApkEntry, ApkSnapshot
from buildsize.domain.errors import BuildSizeError


@dataclass(frozen=True)
class EntryChange:
    path: str
    status: str
    category: str
    old_compressed_bytes: int
    new_compressed_bytes: int
    delta_compressed_bytes: int
    content_changed: bool
    compressed_size_changed: bool
    compression_method_changed: bool
    old_entry: ApkEntry | None
    new_entry: ApkEntry | None


@dataclass(frozen=True)
class CategoryChange:
    category: str
    old_bytes: int
    new_bytes: int
    delta_bytes: int


@dataclass(frozen=True)
class ApkComparison:
    status: str
    base_apk_sha256: str | None
    target_apk_sha256: str
    old_apk_bytes: int | None
    new_apk_bytes: int
    delta_apk_bytes: int | None
    delta_overhead_bytes: int | None
    growth_percent: float | None
    positive_growth_bytes: int | None
    negative_shrink_bytes: int | None
    changes: tuple[EntryChange, ...]
    categories: tuple[CategoryChange, ...]


def compare_apks(base: ApkSnapshot | None, target: ApkSnapshot) -> ApkComparison:
    if base is None:
        return ApkComparison(
            "NO_BASELINE",
            None,
            target.apk_sha256,
            None,
            target.apk_file_bytes,
            None,
            None,
            None,
            None,
            None,
            (),
            (),
        )
    if base.classifier_version != target.classifier_version:
        raise BuildSizeError("CLASSIFIER_VERSION_MISMATCH", "必须按同一分类器版本重新计算")
    old_by_path = {entry.normalized_path: entry for entry in base.entries}
    new_by_path = {entry.normalized_path: entry for entry in target.entries}
    changes: list[EntryChange] = []
    for path in sorted(old_by_path.keys() | new_by_path.keys()):
        old, new = old_by_path.get(path), new_by_path.get(path)
        old_bytes = old.compressed_bytes if old else 0
        new_bytes = new.compressed_bytes if new else 0
        content_changed = old is None or new is None or old.content_sha256 != new.content_sha256
        method_changed = bool(old and new and old.compression_method != new.compression_method)
        if old is None:
            status = "ADDED"
        elif new is None:
            status = "REMOVED"
        elif content_changed or old.is_directory != new.is_directory:
            status = "MODIFIED"
        elif old_bytes != new_bytes or method_changed:
            status = "PACKAGING_CHANGED"
        else:
            status = "UNCHANGED"
        entry = new if new is not None else old
        assert entry is not None
        changes.append(
            EntryChange(
                path,
                status,
                entry.category,
                old_bytes,
                new_bytes,
                new_bytes - old_bytes,
                content_changed,
                old_bytes != new_bytes,
                method_changed,
                old,
                new,
            )
        )
    changes.sort(key=lambda item: (-item.delta_compressed_bytes, item.path))
    overhead = target.apk_overhead_bytes - base.apk_overhead_bytes
    delta = target.apk_file_bytes - base.apk_file_bytes
    if sum(item.delta_compressed_bytes for item in changes) + overhead != delta:
        raise BuildSizeError("ACCOUNTING_MISMATCH", "APK 文件差异与总增量无法对账")
    old_categories = {item.category: item.compressed_bytes for item in base.categories}
    new_categories = {item.category: item.compressed_bytes for item in target.categories}
    categories = tuple(
        CategoryChange(
            key,
            old_categories.get(key, 0),
            new_categories.get(key, 0),
            new_categories.get(key, 0) - old_categories.get(key, 0),
        )
        for key in sorted(old_categories.keys() | new_categories.keys())
    )
    categories += (
        CategoryChange(
            "apk_overhead", base.apk_overhead_bytes, target.apk_overhead_bytes, overhead
        ),
    )
    values = [item.delta_compressed_bytes for item in changes] + [overhead]
    return ApkComparison(
        "COMPARED",
        base.apk_sha256,
        target.apk_sha256,
        base.apk_file_bytes,
        target.apk_file_bytes,
        delta,
        overhead,
        delta * 100 / base.apk_file_bytes if base.apk_file_bytes else None,
        sum(max(value, 0) for value in values),
        sum(min(value, 0) for value in values),
        tuple(changes),
        categories,
    )
