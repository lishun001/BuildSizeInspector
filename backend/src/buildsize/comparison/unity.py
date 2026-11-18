"""仅比较 Unity packed bytes，结果不参与 APK 物理字节求和。"""

from typing import Any

from buildsize.domain.unity import UnitySnapshot


def compare_unity(base: UnitySnapshot | None, target: UnitySnapshot) -> dict[str, Any]:
    result: dict[str, Any] = {
        "metric": "unity_packed_bytes",
        "additive_to_apk": False,
        "status": "NO_BASELINE" if base is None else "unavailable",
        "changes": [],
        "importer_changes": [],
        "delta_packed_bytes": None,
        "importer_comparison_status": "unavailable",
    }
    if base is None:
        return result
    if base.summary["state"] != "complete" or target.summary["state"] != "complete":
        return result
    old, new = (
        {row.key: row for row in base.groups if row.matchable},
        {row.key: row for row in target.groups if row.matchable},
    )
    changes: list[dict[str, Any]] = []
    for key in sorted(old.keys() | new.keys()):
        previous, current = old.get(key), new.get(key)
        row = current or previous
        assert row is not None
        delta = (current.packed_bytes if current else 0) - (
            previous.packed_bytes if previous else 0
        )
        renamed = bool(
            previous
            and current
            and previous.source_paths != current.source_paths
            and row.identity_kind == "guid"
        )
        status = (
            "ADDED_TO_BUILD"
            if previous is None
            else "REMOVED_FROM_BUILD"
            if current is None
            else "CHANGED"
            if delta
            or renamed
            or previous.record_count != current.record_count
            or previous.container_paths != current.container_paths
            else "UNCHANGED"
        )
        changes.append(
            {
                "key": key,
                "identity_kind": row.identity_kind,
                "source_guid": row.source_guid,
                "asset_type": row.asset_type,
                "old_packed_bytes": previous.packed_bytes if previous else 0,
                "new_packed_bytes": current.packed_bytes if current else 0,
                "delta_packed_bytes": delta,
                "old_paths": list(previous.source_paths) if previous else [],
                "new_paths": list(current.source_paths) if current else [],
                "renamed": renamed,
                "status": status,
                "record_count_old": previous.record_count if previous else 0,
                "record_count_new": current.record_count if current else 0,
            }
        )
    old_importers = {
        row["source_guid"]: row for row in base.importer_rows if row["state"] == "complete"
    }
    new_importers = {
        row["source_guid"]: row for row in target.importer_rows if row["state"] == "complete"
    }
    importer_complete = (
        base.summary["importers"]["state"] == target.summary["importers"]["state"] == "complete"
    )
    comparable_importers = old_importers.keys() & new_importers.keys()
    result["importer_comparison_status"] = (
        "complete" if importer_complete else "partial" if comparable_importers else "unavailable"
    )
    for guid in sorted(comparable_importers):
        old_settings, new_settings = old_importers[guid], new_importers[guid]
        fields = [
            field
            for field in (
                "settings",
                "source_path",
                "dependency_hash",
                "source_sha256",
                "meta_sha256",
                "importer_type",
            )
            if old_settings[field] != new_settings[field]
        ]
        if fields:
            result["importer_changes"].append(
                {
                    "source_guid": guid,
                    "fields": fields,
                    "old": old_settings,
                    "new": new_settings,
                    "confidence": "observed",
                }
            )
    result.update(
        status="COMPARED",
        changes=sorted(changes, key=lambda row: (-row["delta_packed_bytes"], row["key"])),
        delta_packed_bytes=target.summary["unity_packed_bytes"]
        - base.summary["unity_packed_bytes"],
        unmatched_generated_old=sum(row.packed_bytes for row in base.groups if not row.matchable),
        unmatched_generated_new=sum(row.packed_bytes for row in target.groups if not row.matchable),
    )
    return result
